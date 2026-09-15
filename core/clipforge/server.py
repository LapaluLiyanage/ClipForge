"""ClipForge local helper — the HTTP API the extension and web UI talk to.

Binds to 127.0.0.1 only. Nothing outside this machine can reach it.
"""
from __future__ import annotations

import asyncio
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, config, downloader, formats

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ClipForge Helper", version=__version__)

# The extension's origin is chrome-extension://<id>, which changes per install,
# so we allow that scheme plus loopback and nothing else.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|moz-extension://.*|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cfg = config.load()
manager = downloader.JobManager(max_concurrent=int(_cfg["max_concurrent_jobs"]))


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class ProbeRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    mode: str = Field(default="video", pattern="^(video|mp3|wav|audio)$")
    quality: str = "best"
    container: str = "mp4"
    bitrate: str = "192"
    audio_format: str = "m4a"
    wav_depth: str = "16"
    wav_rate: str = "44100"
    wav_channels: str = "source"
    title: Optional[str] = None
    thumbnail: Optional[str] = None


class SettingsRequest(BaseModel):
    output_dir: Optional[str] = None
    filename_template: Optional[str] = None
    embed_thumbnail: Optional[bool] = None
    embed_metadata: Optional[bool] = None
    embed_chapters: Optional[bool] = None
    write_subtitles: Optional[bool] = None
    subtitle_langs: Optional[str] = None
    concurrent_fragments: Optional[int] = None
    cookies_from_browser: Optional[str] = None
    continue_downloads: Optional[bool] = None
    max_concurrent_jobs: Optional[int] = None


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> Dict[str, Any]:
    info = downloader.preflight()
    info["app"] = "ClipForge"
    info["version"] = __version__
    info["python"] = sys.version.split()[0]
    return info


@app.get("/options")
def options() -> Dict[str, Any]:
    return formats.options_payload()


@app.get("/settings")
def get_settings() -> Dict[str, Any]:
    return config.load()


@app.post("/settings")
def post_settings(req: SettingsRequest) -> Dict[str, Any]:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    return config.save(patch)


@app.post("/update-ytdlp")
def update_ytdlp() -> Dict[str, Any]:
    """Self-update yt-dlp. YouTube changes often; this is the usual fix."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=300,
        )
        return {
            "ok": proc.returncode == 0,
            "output": (proc.stdout + proc.stderr)[-2000:],
            "note": "Restart the helper for the new version to take effect.",
        }
    except subprocess.SubprocessError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Work
# --------------------------------------------------------------------------
@app.post("/probe")
async def probe(req: ProbeRequest) -> Dict[str, Any]:
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="No URL given.")
    try:
        return await asyncio.to_thread(downloader.probe, req.url.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=downloader._friendly_error(str(exc))) from exc


@app.post("/download")
def start_download(req: DownloadRequest) -> Dict[str, Any]:
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="No URL given.")
    params = req.model_dump()
    params.pop("url", None)
    job = manager.submit(url, params)
    return job.snapshot()


@app.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    return {"jobs": manager.all()}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    return job.snapshot()


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    if not manager.cancel(job_id):
        raise HTTPException(status_code=409, detail="That job is not running.")
    return {"ok": True}


@app.post("/jobs/clear")
def clear_jobs() -> Dict[str, Any]:
    return {"cleared": manager.clear_finished()}


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-sent events stream of live progress for one job."""
    job = manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")

    async def stream():
        q = job.subscribe()
        try:
            while True:
                try:
                    snap = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(snap)}\n\n"
                if snap["status"] in ("done", "error", "cancelled"):
                    break
        finally:
            job.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/reveal/{job_id}")
def reveal(job_id: str) -> Dict[str, Any]:
    """Open the finished file's folder in the OS file manager."""
    job = manager.get(job_id)
    if not job or not job.filepath:
        raise HTTPException(status_code=404, detail="No file for that job.")
    path = Path(job.filepath)
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/file/{job_id}")
def download_file(job_id: str) -> FileResponse:
    """Hand the finished file to the browser (used by the web UI's Save button)."""
    job = manager.get(job_id)
    if not job or not job.filepath or not Path(job.filepath).exists():
        raise HTTPException(status_code=404, detail="No file for that job.")
    return FileResponse(job.filepath, filename=Path(job.filepath).name)


# --------------------------------------------------------------------------
# Web UI
# --------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


def main() -> None:
    import argparse

    import uvicorn

    cfg = config.load()
    parser = argparse.ArgumentParser(prog="clipforge", description="ClipForge local helper")
    parser.add_argument("--port", type=int, default=int(cfg["port"]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the web UI in a browser")
    args = parser.parse_args()

    check = downloader.preflight()
    print("\n  ClipForge helper", __version__)
    print("  " + "-" * 44)
    for c in check["checks"]:
        print(f"  [{'ok' if c['ok'] else '!!'}] {c['label']}: {c['detail']}")
    if not check["ok"]:
        print("\n  One or more requirements are missing - see README.md\n")
    print(f"\n  Listening on http://{args.host}:{args.port}")
    print(f"  Saving to    {cfg['output_dir']}\n")

    if args.open:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
