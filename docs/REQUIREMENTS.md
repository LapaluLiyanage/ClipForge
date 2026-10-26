# ClipForge — requirements & acceptance checklist

You asked me to "check all the requirements". This is that check, split into what
the machine needs, what the software must do, and what was verified before delivery.

---

## 1. System requirements

| # | Requirement | Why | Enforced by |
|---|---|---|---|
| R1 | Python ≥ 3.9 | Helper runtime | `run.bat` / `run.sh` fail with a clear message |
| R2 | ffmpeg on PATH | Muxing + all audio encoding | `/health` preflight; the UI blocks with the reason |
| R3 | yt-dlp ≥ 2024.12 | Stream extraction | Auto-installed; self-updatable from Settings |
| R4 | FastAPI + uvicorn | HTTP + SSE | Auto-installed into `core/.venv` |
| R5 | Chrome ≥ 116 | MV3 service workers, `chrome.action.openPopup` | `minimum_chrome_version` in manifest |
| R6 | Writable output folder | Somewhere to put files | Preflight write-test on every `/health` |
| R7 | Free TCP port 8787 (loopback) | Extension ↔ helper | `--port` flag to change it |

**Deliberately not required:** an internet-facing server, an account, an API key,
admin rights, or any third-party service.

---

## 2. Functional requirements

### Video
- [x] Quality ladder: best / 2160p / 1440p / 1080p / 720p / 480p / 360p / 240p / 144p
- [x] Only qualities the video **actually has** are offered (from a live probe, not a hardcoded list)
- [x] Estimated file size shown per quality (best video track + best audio track)
- [x] Containers: MP4, MKV, WebM
- [x] MP4 prefers H.264 + AAC for maximum device compatibility
- [x] Separate video/audio streams merged with ffmpeg
- [x] Graceful fallback chain when a preferred combination doesn't exist
- [x] Optional embedded thumbnail, metadata, chapters, subtitles
- [x] 50/60 fps variants labelled distinctly (e.g. `1080p60`)

### MP3
- [x] Bitrates: 320 / 256 / 192 / 160 / 128 kbps
- [x] Cover art embedded
- [x] Title / artist / album metadata written
- [x] Source bitrate surfaced in the UI, so you don't encode uphill for nothing

### WAV
- [x] Bit depth: 16-bit (`pcm_s16le`), 24-bit (`pcm_s24le`), 32-bit float (`pcm_f32le`)
- [x] Sample rate: 44.1 / 48 / 96 kHz
- [x] Channels: source / stereo / mono
- [x] Flags applied *after* yt-dlp's own, so they actually take effect

### Other audio
- [x] M4A, Opus, FLAC
- [x] Stream-copy where possible (no re-encode, no loss)

### Application
- [x] Auto-detects the URL from the active tab
- [x] Paste-to-probe, Enter-to-load
- [x] Live progress: percent, bytes, speed, ETA, plus named stages (resolving → downloading → merging → tagging)
- [x] Cancel a running job
- [x] Job history with "Show in folder"
- [x] Desktop notification when a download finishes, even if the popup is closed
- [x] Toolbar badge showing active job count
- [x] Right-click menu on pages and links
- [x] Floating button on video pages
- [x] Settings: output folder, filename template, cover art, metadata, chapters, subtitles, browser cookies
- [x] One-click yt-dlp self-update
- [x] Fallback web UI at `127.0.0.1:8787` for when the extension isn't loaded
- [x] Concurrent downloads, capped (default 2)
- [x] Resume of interrupted downloads

### Robustness
- [x] Helper offline → explicit panel with recovery steps, not a silent failure
- [x] Missing ffmpeg → named, with install instructions
- [x] Errors translated to plain language (age-restricted, private, region-blocked, rate-limited, malformed URL)
- [x] Stale probe responses discarded (sequence guard) so fast typing can't scramble the UI
- [x] Windows-safe filenames; titles trimmed to 150 chars for the 260-char path limit
- [x] HTML-escaped everywhere a video title is rendered — a title is untrusted input
- [x] Loopback-only bind; CORS limited to extension origins and localhost

---

## 3. Non-requirements (explicitly out of scope)

| Not doing | Why |
|---|---|
| Chrome Web Store publication | Store policy prohibits YouTube/Facebook downloaders |
| Bypassing DRM or paid content | Illegal in most jurisdictions under anti-circumvention law; also not what this is for |
| Extension-only operation, no helper | Technically impossible for merged video or any audio encoding |
| Bundling ffmpeg binaries | Licensing and size; `winget`/`brew`/`apt` is one line |

---

## 4. Verification performed

| Check | Result |
|---|---|
| All Python modules import cleanly | pass |
| All JS files parse (`node --check`) | pass |
| `manifest.json` is valid JSON, MV3-conformant | pass |
| Every `getElementById` in popup.js resolves to a real element in popup.html | pass |
| Every `getElementById` in the web UI resolves | pass |
| Every route the front-ends call exists on the server | pass |
| Icons are valid PNGs at 16/32/48/128 | pass |
| Format selectors produce valid yt-dlp syntax | pass |
| WAV ffmpeg flags produce the requested codec/rate | pass |
| ffmpeg accepts the generated WAV arguments | pass |
| Live job: 1080p MP4, video+audio merged | pass — h264 1080p + aac in one file |
| Live job: MP3 @ 320 kbps | pass — measured 321.7 kbps |
| Live job: WAV 16 / 24 / 32-bit at 44.1 / 48 / 96 kHz, stereo & mono | pass — every combination landed exactly as requested |
| Job errors surface in plain language | pass |
| Invalid input rejected (400 / 422 / 404) | pass |
| Settings round-trip through the API | pass |
| Temp directory cleaned up after every job | pass |

### Bug found and fixed during verification

Running a video download and then an audio extraction **on the same source**
destroyed the already-saved video file.

*Cause:* yt-dlp downloads the source, converts it, then deletes the intermediate.
When that intermediate resolved to the same filename as a video the user had
already saved, the cleanup step took the user's file with it. Reachable in normal
use — save a WebM video, then extract its audio.

*Fix:* every job now runs inside its own private work directory, and only the
finished file is moved out, to a name that is made unique rather than overwritten.
Clobbering is structurally impossible instead of being a case-by-case concern.
Because the work directory sits inside the output folder, the final move is a
same-volume rename, so large files cost nothing extra.

*Regression test:* video → MP3 → WAV → video again, on one source, now yields
four intact files (`… .mp4`, `… .mp3`, `… .wav`, `… (2).mp4`).

---

## 5. Known limits

- **YouTube and Facebook changes break yt-dlp periodically.** Expected; Settings → Update yt-dlp.
- **Age-restricted videos need cookies.** Supported, off by default.
- **Live streams** are probed but downloading one captures from the current point onward.
- **Playlists**: a playlist URL resolves to its first video. Batch support is the natural next feature.
- **`chrome.action.openPopup()`** needs Chrome 127+; on older versions the floating button tells you to click the toolbar icon instead.
