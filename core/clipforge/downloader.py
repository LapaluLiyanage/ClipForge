"""Job engine: probing URLs and running yt-dlp downloads in the background."""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from . import config, formats


class Cancelled(Exception):
    """Raised inside a progress hook to abort a running download."""


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------
def _ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def preflight() -> Dict[str, Any]:
    """Report whether every external requirement is satisfied."""
    import yt_dlp

    ffmpeg = _ffmpeg_path()
    ffmpeg_version = None
    if ffmpeg:
        try:
            out = subprocess.run(
                [ffmpeg, "-version"], capture_output=True, text=True, timeout=10
            ).stdout.splitlines()
            if out:
                ffmpeg_version = out[0].split(" Copyright")[0].strip()
        except (OSError, subprocess.SubprocessError):
            ffmpeg_version = "unknown"

    cfg = config.load()
    out_dir = Path(cfg["output_dir"])
    writable = False
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".clipforge-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False

    checks = [
        {
            "id": "ytdlp",
            "label": "yt-dlp",
            "ok": True,
            "detail": yt_dlp.version.__version__,
        },
        {
            "id": "ffmpeg",
            "label": "ffmpeg (merging + MP3/WAV encoding)",
            "ok": bool(ffmpeg),
            "detail": ffmpeg_version or "not found on PATH",
        },
        {
            "id": "output",
            "label": "Output folder writable",
            "ok": writable,
            "detail": str(out_dir),
        },
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "ffmpeg": bool(ffmpeg),
        "output_dir": str(out_dir),
    }


# --------------------------------------------------------------------------
# Probe
# --------------------------------------------------------------------------
def _base_opts(cfg: Dict[str, Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 10,
        "socket_timeout": 30,
    }
    browser = (cfg.get("cookies_from_browser") or "").strip()
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _fmt_size(n: Optional[float]) -> Optional[str]:
    if not n:
        return None
    units = ["B", "KB", "MB", "GB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return None


def probe(url: str) -> Dict[str, Any]:
    """Fetch metadata and the real list of qualities available for *url*."""
    cfg = config.load()
    opts = _base_opts(cfg)
    opts["skip_download"] = True

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("That playlist is empty or unavailable.")
        info = entries[0]

    raw_formats = info.get("formats") or []

    # Best audio size, used to estimate the size of a merged download.
    audio_sizes = [
        f.get("filesize") or f.get("filesize_approx") or 0
        for f in raw_formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    best_audio = max(audio_sizes) if audio_sizes else 0

    by_height: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
    for f in raw_formats:
        height = f.get("height")
        if not height or f.get("vcodec") in (None, "none"):
            continue
        size = f.get("filesize") or f.get("filesize_approx") or 0
        current = by_height.get(height)
        if current is None or size > current["_size"]:
            by_height[height] = {
                "height": height,
                "label": f"{height}p",
                "fps": f.get("fps"),
                "vcodec": (f.get("vcodec") or "").split(".")[0],
                "ext": f.get("ext"),
                "_size": size,
            }

    available = []
    for height in sorted(by_height, reverse=True):
        item = by_height[height]
        total = item.pop("_size") + best_audio
        item["approx_size"] = _fmt_size(total)
        if item.get("fps") and item["fps"] >= 50:
            item["label"] = f"{height}p{int(item['fps'])}"
        available.append(item)

    audio_only = [
        {
            "abr": f.get("abr"),
            "acodec": (f.get("acodec") or "").split(".")[0],
            "ext": f.get("ext"),
        }
        for f in raw_formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    best_abr = max((a["abr"] or 0 for a in audio_only), default=0)

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "view_count": info.get("view_count"),
        "is_live": bool(info.get("is_live")),
        "available_qualities": available,
        "best_audio_bitrate": round(best_abr) if best_abr else None,
        "subtitle_langs": sorted((info.get("subtitles") or {}).keys())[:40],
    }


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
class Job:
    """One download. Owns its own thread and a fan-out queue of progress events."""

    def __init__(self, url: str, params: Dict[str, Any]):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.params = params
        self.mode = params.get("mode", "video")
        self.status = "queued"          # queued|running|done|error|cancelled
        self.stage = "queued"           # queued|downloading|merging|converting|done
        self.progress = 0.0
        self.speed: Optional[float] = None
        self.eta: Optional[int] = None
        self.downloaded: int = 0
        self.total: int = 0
        self.title: Optional[str] = params.get("title")
        self.thumbnail: Optional[str] = params.get("thumbnail")
        self.filepath: Optional[str] = None
        self.error: Optional[str] = None
        self.created = time.time()
        self.finished: Optional[float] = None
        self._cancel = threading.Event()
        self._work_dir: Optional[Path] = None
        self._subscribers: List["queue.Queue[Dict[str, Any]]"] = []
        self._lock = threading.Lock()

    # -- events ------------------------------------------------------------
    def subscribe(self) -> "queue.Queue[Dict[str, Any]]":
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        q.put(self.snapshot())
        return q

    def unsubscribe(self, q: "queue.Queue[Dict[str, Any]]") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _emit(self) -> None:
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(snap)
            except queue.Full:
                pass

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "mode": self.mode,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "downloaded": self.downloaded,
            "total": self.total,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "filepath": self.filepath,
            "filename": Path(self.filepath).name if self.filepath else None,
            "error": self.error,
            "created": self.created,
            "finished": self.finished,
            "params": self.params,
        }

    def cancel(self) -> None:
        self._cancel.set()

    # -- hooks -------------------------------------------------------------
    def _progress_hook(self, d: Dict[str, Any]) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            self.total = int(total)
            self.downloaded = int(done)
            self.progress = (done / total * 100) if total else 0.0
            self.speed = d.get("speed")
            self.eta = d.get("eta")
            self.stage = "downloading"
            self._emit()
        elif status == "finished":
            self.progress = 100.0
            self.speed = None
            self.eta = None
            self.stage = "merging" if self.mode == "video" else "converting"
            self._emit()

    def _postprocessor_hook(self, d: Dict[str, Any]) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        name = d.get("postprocessor", "")
        if d.get("status") != "started":
            return
        if name == "Merger":
            self.stage = "merging"
        elif name in ("ExtractAudio", "FFmpegExtractAudio"):
            self.stage = "converting"
        elif "Thumbnail" in name:
            self.stage = "artwork"
        elif "Metadata" in name:
            self.stage = "tagging"
        self._emit()

    # -- run ---------------------------------------------------------------
    def build_opts(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        opts = _base_opts(cfg)
        # Every job runs inside its own private directory and only the finished
        # file is moved out. yt-dlp deletes its intermediate download after
        # converting it - and for an audio job that intermediate can carry the
        # same name as a video the user already saved, so letting yt-dlp write
        # directly into the output folder destroys that file. Working in
        # isolation makes clobbering structurally impossible rather than
        # something we have to get right case by case. The work directory sits
        # inside the output folder so the final move is a same-volume rename,
        # not a multi-gigabyte copy.
        work_dir = Path(cfg["output_dir"]) / ".clipforge-temp" / self.id
        work_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir = work_dir
        opts.update({
            "paths": {"home": str(work_dir), "temp": str(work_dir)},
            "outtmpl": cfg["filename_template"],
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "concurrent_fragment_downloads": max(1, int(cfg["concurrent_fragments"])),
            "continuedl": bool(cfg["continue_downloads"]),
            "windowsfilenames": True,
            "trim_file_name": 150,
            # If the exact target already exists, keep it and skip rather than
            # silently overwriting something the user chose to save.
            "overwrites": False,
        })

        postprocessors: List[Dict[str, Any]] = []

        if self.mode == "video":
            container = str(self.params.get("container", "mp4"))
            height = formats.height_for(self.params.get("quality", "best"))
            opts["format"] = formats.video_selector(height, container)
            opts["merge_output_format"] = container
            if cfg["embed_thumbnail"]:
                opts["writethumbnail"] = True
                postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
            if cfg["embed_chapters"] or cfg["embed_metadata"]:
                postprocessors.append({
                    "key": "FFmpegMetadata",
                    "add_chapters": bool(cfg["embed_chapters"]),
                    "add_metadata": bool(cfg["embed_metadata"]),
                })
            if cfg["write_subtitles"]:
                opts["writesubtitles"] = True
                opts["subtitleslangs"] = [s.strip() for s in str(cfg["subtitle_langs"]).split(",") if s.strip()]
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})
        else:
            # Audio-only: grab the best audio stream, then convert.
            opts["format"] = "ba[acodec^=opus]/ba/bestaudio/best"
            postprocessors.extend(formats.audio_postprocessors(self.mode, self.params))
            if self.mode == "wav":
                # Applied after yt-dlp's own flags, so these take precedence.
                opts["postprocessor_args"] = {
                    "extractaudio": formats.wav_ffmpeg_args(self.params),
                }
            else:
                if cfg["embed_thumbnail"] and self.mode in ("mp3", "audio"):
                    opts["writethumbnail"] = True
                    postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
                if cfg["embed_metadata"]:
                    postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})

        opts["postprocessors"] = postprocessors
        return opts

    def _locate_output(self, info: Dict[str, Any]) -> Optional[Path]:
        """Find the finished file inside the work directory.

        Postprocessors rename and change extensions, so the path yt-dlp
        reported at download time is only a hint. The work directory holds
        exactly one job, so the largest real media file in it is the output.
        """
        hint = None
        requested = info.get("requested_downloads") or []
        if requested:
            hint = requested[0].get("filepath")
        hint = hint or info.get("filepath") or info.get("_filename")
        if hint and Path(hint).exists():
            return Path(hint)

        if not self._work_dir or not self._work_dir.is_dir():
            return None

        sidecars = {".jpg", ".jpeg", ".png", ".webp", ".part", ".ytdl", ".temp", ".description"}
        candidates = [
            f for f in self._work_dir.iterdir()
            if f.is_file() and f.suffix.lower() not in sidecars
        ]
        if not candidates:
            return None
        if hint:
            # Prefer a file sharing the hinted stem (same title, new extension).
            stem = Path(hint).stem
            same = [f for f in candidates if f.stem == stem]
            if same:
                return max(same, key=lambda f: f.stat().st_size)
        return max(candidates, key=lambda f: f.stat().st_size)

    @staticmethod
    def _unique_path(target: Path) -> Path:
        """Return *target*, or target with ' (2)', ' (3)'... if it is taken."""
        if not target.exists():
            return target
        stem, suffix, parent = target.stem, target.suffix, target.parent
        for n in range(2, 1000):
            candidate = parent / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{stem} ({uuid.uuid4().hex[:6]}){suffix}"

    def _move_into_place(self, produced: Path, output_dir: Path) -> Path:
        """Move the finished file out of the work directory, never overwriting."""
        destination = self._unique_path(output_dir / produced.name)
        try:
            produced.replace(destination)          # same volume: atomic rename
        except OSError:
            shutil.move(str(produced), str(destination))
        return destination.resolve()

    def _sweep_temp(self, cfg: Dict[str, Any]) -> None:
        """Delete this job's work directory, and the shared parent if empty."""
        if self._work_dir:
            shutil.rmtree(self._work_dir, ignore_errors=True)
        parent = Path(cfg["output_dir"]) / ".clipforge-temp"
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    def run(self) -> None:
        cfg = config.load()
        self.status = "running"
        self.stage = "resolving"
        self._emit()
        try:
            opts = self.build_opts(cfg)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)

            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                info = entries[0] if entries else info

            self.title = info.get("title") or self.title
            self.thumbnail = info.get("thumbnail") or self.thumbnail

            produced = self._locate_output(info)
            if produced is None:
                raise RuntimeError("The download finished but no output file was found.")
            self.filepath = str(self._move_into_place(produced, Path(cfg["output_dir"])))

            self.status = "done"
            self.stage = "done"
            self.progress = 100.0
        except Cancelled:
            self.status = "cancelled"
            self.stage = "cancelled"
            self.error = "Cancelled."
        except DownloadError as exc:
            if self._cancel.is_set():
                self.status = "cancelled"
                self.stage = "cancelled"
                self.error = "Cancelled."
            else:
                self.status = "error"
                self.stage = "error"
                self.error = _friendly_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self.status = "error"
            self.stage = "error"
            self.error = _friendly_error(f"{type(exc).__name__}: {exc}")
        finally:
            self._sweep_temp(cfg)
            self.finished = time.time()
            self.speed = None
            self.eta = None
            self._emit()
            self._emit()  # second beat so late subscribers see the terminal state


def _friendly_error(raw: str) -> str:
    text = raw.replace("ERROR: ", "").strip()
    lowered = text.lower()
    if "ffmpeg" in lowered and ("not installed" in lowered or "not found" in lowered):
        return ("ffmpeg is missing. ClipForge needs it to merge video with audio "
                "and to write MP3/WAV. See the README for install steps.")
    if "sign in to confirm" in lowered or "age" in lowered and "restrict" in lowered:
        return ("This video is age-restricted. Set 'cookies_from_browser' in "
                "Settings to a browser where you are signed in.")
    if "private video" in lowered:
        return "This video is private."
    if "video unavailable" in lowered:
        return "This video is unavailable in your region or has been removed."
    if "http error 429" in lowered or "too many requests" in lowered:
        return "YouTube is rate-limiting this machine. Wait a few minutes and retry."
    if "http error 404" in lowered or "not found" in lowered:
        return "That URL returned 'not found' - check it is correct and still public."
    if "http error 403" in lowered:
        return "Access to that media was refused (403). It may be geo-blocked or require a login."
    if "unable to download webpage" in lowered or "unable to connect" in lowered:
        return "Couldn't reach that URL. Check your connection and that the link is valid."
    if "unsupported url" in lowered:
        return "That does not look like a supported video URL."
    if "is not a valid url" in lowered:
        return "That URL is malformed."
    return text[:400] or "Download failed."


class JobManager:
    """Keeps jobs, runs at most N at a time, and remembers recent history."""

    def __init__(self, max_concurrent: int = 2, history: int = 50):
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._history = history
        self._sem = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()

    def submit(self, url: str, params: Dict[str, Any]) -> Job:
        job = Job(url, params)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._history:
                oldest_id, oldest = next(iter(self._jobs.items()))
                if oldest.status in ("running", "queued"):
                    break
                self._jobs.pop(oldest_id)

        def worker() -> None:
            with self._sem:
                if job.status == "cancelled":
                    return
                job.run()

        threading.Thread(target=worker, name=f"clipforge-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def all(self) -> List[Dict[str, Any]]:
        return [j.snapshot() for j in reversed(list(self._jobs.values()))]

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in ("queued", "running"):
            job.cancel()
            if job.status == "queued":
                job.status = "cancelled"
                job.stage = "cancelled"
                job.error = "Cancelled."
                job.finished = time.time()
                job._emit()
            return True
        return False

    def clear_finished(self) -> int:
        with self._lock:
            done = [k for k, j in self._jobs.items() if j.status in ("done", "error", "cancelled")]
            for k in done:
                self._jobs.pop(k, None)
        return len(done)
