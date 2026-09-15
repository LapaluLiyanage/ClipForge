"""Quality ladders and yt-dlp format-selector construction.

Everything the UI offers as a choice is defined here once, so the extension,
the web UI and the backend can never drift apart.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------
VIDEO_QUALITIES: List[Dict[str, Any]] = [
    {"id": "best", "label": "Best available", "height": None},
    {"id": "2160", "label": "2160p · 4K", "height": 2160},
    {"id": "1440", "label": "1440p · 2K", "height": 1440},
    {"id": "1080", "label": "1080p · Full HD", "height": 1080},
    {"id": "720", "label": "720p · HD", "height": 720},
    {"id": "480", "label": "480p", "height": 480},
    {"id": "360", "label": "360p", "height": 360},
    {"id": "240", "label": "240p", "height": 240},
    {"id": "144", "label": "144p", "height": 144},
]

VIDEO_CONTAINERS = [
    {"id": "mp4", "label": "MP4 (H.264 · most compatible)"},
    {"id": "mkv", "label": "MKV (keeps the best streams as-is)"},
    {"id": "webm", "label": "WebM (VP9/AV1)"},
]

# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
MP3_BITRATES = [
    {"id": "320", "label": "320 kbps · highest"},
    {"id": "256", "label": "256 kbps"},
    {"id": "192", "label": "192 kbps · balanced"},
    {"id": "160", "label": "160 kbps"},
    {"id": "128", "label": "128 kbps · smallest"},
]

WAV_DEPTHS = [
    {"id": "16", "label": "16-bit · CD standard", "codec": "pcm_s16le"},
    {"id": "24", "label": "24-bit · studio", "codec": "pcm_s24le"},
    {"id": "32", "label": "32-bit float", "codec": "pcm_f32le"},
]

WAV_RATES = [
    {"id": "44100", "label": "44.1 kHz · CD"},
    {"id": "48000", "label": "48 kHz · video standard"},
    {"id": "96000", "label": "96 kHz · high-res"},
]

WAV_CHANNELS = [
    {"id": "source", "label": "Same as source"},
    {"id": "2", "label": "Stereo"},
    {"id": "1", "label": "Mono"},
]

OTHER_AUDIO_FORMATS = [
    {"id": "m4a", "label": "M4A / AAC (no re-encode when possible)"},
    {"id": "opus", "label": "Opus (best quality per byte)"},
    {"id": "flac", "label": "FLAC (lossless, compressed)"},
]

MODES = ["video", "mp3", "wav", "audio"]

_WAV_CODEC = {d["id"]: d["codec"] for d in WAV_DEPTHS}


def height_for(quality_id: str) -> Optional[int]:
    """Map a quality id from the UI to a pixel height (None = best available)."""
    for q in VIDEO_QUALITIES:
        if q["id"] == str(quality_id):
            return q["height"]
    try:
        return int(quality_id)
    except (TypeError, ValueError):
        return None


def video_selector(height: Optional[int], container: str = "mp4") -> str:
    """Build a yt-dlp format string.

    YouTube serves video and audio as separate streams above 360p, so we ask
    for the best video track at or below *height* plus the best audio track,
    and let ffmpeg mux them. Each '/' is a fallback for when the preferred
    combination does not exist on a given video.
    """
    cap = f"[height<={height}]" if height else ""

    if container == "mp4":
        # Prefer H.264 + AAC so the file plays everywhere without transcoding.
        return (
            f"bv*{cap}[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/"
            f"bv*{cap}[ext=mp4]+ba[ext=m4a]/"
            f"bv*{cap}+ba/"
            f"b{cap}[ext=mp4]/"
            f"b{cap}/"
            f"bv*+ba/b"
        )
    if container == "webm":
        return (
            f"bv*{cap}[ext=webm]+ba[ext=webm]/"
            f"bv*{cap}+ba/b{cap}/bv*+ba/b"
        )
    # MKV can hold anything, so just take the best.
    return f"bv*{cap}+ba/b{cap}/bv*+ba/b"


def audio_postprocessors(mode: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Postprocessor chain for the audio-only modes."""
    if mode == "mp3":
        return [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(params.get("bitrate", "192")),
        }]
    if mode == "wav":
        # Quality is irrelevant for PCM; exact codec/rate is applied through
        # postprocessor_args (see wav_ffmpeg_args).
        return [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }]
    codec = str(params.get("audio_format", "m4a"))
    return [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": codec,
        "preferredquality": str(params.get("bitrate", "192")),
    }]


def wav_ffmpeg_args(params: Dict[str, Any]) -> List[str]:
    """Extra ffmpeg flags that pin WAV bit depth, sample rate and channels.

    These are appended *after* yt-dlp's own flags, so they win.
    """
    depth = str(params.get("wav_depth", "16"))
    rate = str(params.get("wav_rate", "44100"))
    channels = str(params.get("wav_channels", "source"))

    args = ["-acodec", _WAV_CODEC.get(depth, "pcm_s16le")]
    if rate in {r["id"] for r in WAV_RATES}:
        args += ["-ar", rate]
    if channels in {"1", "2"}:
        args += ["-ac", channels]
    return args


def options_payload() -> Dict[str, Any]:
    """Everything the UI needs to render its pickers."""
    return {
        "video_qualities": VIDEO_QUALITIES,
        "video_containers": VIDEO_CONTAINERS,
        "mp3_bitrates": MP3_BITRATES,
        "wav_depths": WAV_DEPTHS,
        "wav_rates": WAV_RATES,
        "wav_channels": WAV_CHANNELS,
        "other_audio_formats": OTHER_AUDIO_FORMATS,
        "modes": MODES,
    }
