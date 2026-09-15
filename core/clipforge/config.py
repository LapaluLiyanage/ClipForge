"""Persistent settings for the ClipForge helper.

Config lives in ~/.clipforge/config.json so it survives reinstalls of the
package and is never written inside the project folder.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

APP_NAME = "ClipForge"
CONFIG_DIR = Path.home() / ".clipforge"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"

DEFAULTS: Dict[str, Any] = {
    # Where finished files land.
    "output_dir": str(Path.home() / "Downloads" / "ClipForge"),
    # Port the helper listens on (loopback only).
    "port": 8787,
    # yt-dlp output template. .200B truncates the title to 200 *bytes* so
    # long titles cannot blow past Windows' 260-char path limit.
    "filename_template": "%(title).150B [%(id)s].%(ext)s",
    "embed_thumbnail": True,
    "embed_metadata": True,
    "embed_chapters": True,
    "write_subtitles": False,
    "subtitle_langs": "en",
    "concurrent_fragments": 4,
    # Optional: "chrome" | "edge" | "firefox" | "brave" | "" (off).
    # Lets yt-dlp reuse a browser login for content your account can already
    # see. Leave empty unless you need it.
    "cookies_from_browser": "",
    # Keep partial files so an interrupted download can resume.
    "continue_downloads": True,
    "max_concurrent_jobs": 2,
}


def _read_raw() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load() -> Dict[str, Any]:
    """Return the effective config, creating the output directory if needed."""
    data = dict(DEFAULTS)
    data.update({k: v for k, v in _read_raw().items() if k in DEFAULTS})
    try:
        Path(data["output_dir"]).mkdir(parents=True, exist_ok=True)
    except OSError:
        # Bad path saved earlier - fall back to the default rather than crash.
        data["output_dir"] = DEFAULTS["output_dir"]
        Path(data["output_dir"]).mkdir(parents=True, exist_ok=True)
    return data


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *patch* into the stored config and return the new effective config."""
    data = dict(DEFAULTS)
    data.update({k: v for k, v in _read_raw().items() if k in DEFAULTS})
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue
        # Keep types stable so the UI never poisons the config.
        expected = type(DEFAULTS[key])
        if expected is bool:
            data[key] = bool(value)
        elif expected is int:
            try:
                data[key] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            data[key] = str(value)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return load()
