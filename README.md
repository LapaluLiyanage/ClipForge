# ClipForge

Save video as **MP4**, or extract **MP3** / **WAV** audio, with real quality control.
A Chrome (Manifest V3) extension for the interface, and a small local Python helper
that does the actual work.

```
┌──────────────────┐   HTTP (loopback only)   ┌────────────────────────┐
│  Chrome extension │ ───────────────────────► │  ClipForge helper      │
│  popup + YouTube  │ ◄─────────────────────── │  FastAPI + yt-dlp      │
│  floating button  │      live progress       │  + ffmpeg              │
└──────────────────┘                          └────────────────────────┘
                                                        │
                                               writes to your Downloads
```

---

## Why there is a helper at all

A browser extension on its own **cannot** do this, and any tutorial claiming otherwise
is showing you something that breaks within weeks:

| What's needed | Can an extension do it? |
|---|---|
| Fetch the stream URLs | Partly — they're signature-ciphered and throttled |
| Merge separate video + audio tracks | **No** — needs ffmpeg |
| Encode to MP3 / WAV | **No** — needs ffmpeg |
| Write to an arbitrary folder | No — only the Downloads sandbox |

Above 360p, YouTube ships video and audio as **separate** streams. A file with both
has to be muxed. That is what the helper is for: the extension is the interface,
the helper is the muscle.

> **Note on distribution:** the Chrome Web Store's developer policies prohibit
> extensions that download from YouTube, so ClipForge is built to be loaded
> unpacked, for personal use. It is not publishable there.

---

## Requirements

| | Needed | Check with |
|---|---|---|
| **Python** | 3.9 or newer | `python --version` |
| **ffmpeg** | any recent build, on `PATH` | `ffmpeg -version` |
| **Chrome** | 116 or newer | `chrome://version` |
| yt-dlp, FastAPI, uvicorn | installed automatically | — |

The helper checks all of these on startup and the extension shows you exactly
which one is missing, so you don't have to guess.

### Installing ffmpeg

**Windows** — easiest is winget:
```powershell
winget install Gyan.FFmpeg
```
Then **close and reopen** your terminal so `PATH` refreshes. Manual alternative:
download a build from https://www.gyan.dev/ffmpeg/builds/, unzip to `C:\ffmpeg`,
and add `C:\ffmpeg\bin` to your PATH environment variable.

**macOS**: `brew install ffmpeg` · **Debian/Ubuntu**: `sudo apt install ffmpeg`

---

## Install

### 0. Get the code

Since ClipForge isn't on the Chrome Web Store (see above), grab it from GitHub:

```
git clone https://github.com/LapaluLiyanage/ClipForge.git
```

Or download the ZIP from the repo's **Code → Download ZIP** button and extract it.

### 1. Start the helper

**Windows** — double-click `core\run.bat`
**macOS / Linux** — `cd core && ./run.sh`

First run creates a virtual environment and installs dependencies (~30 seconds).
You'll see:

```
  ClipForge helper 1.0.0
  --------------------------------------------
  [ok] yt-dlp: 2024.12.13
  [ok] ffmpeg (merging + MP3/WAV encoding): ffmpeg version 6.1.1
  [ok] Output folder writable: C:\Users\you\Downloads\ClipForge

  Listening on http://127.0.0.1:8787
```

Leave that window open while you use ClipForge.

### 2. Load the extension

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the `extension` folder
5. Pin ClipForge to your toolbar

### 3. Use it

Open a video, click the ClipForge icon (or the floating pill on the page),
pick your format, hit download. Files land in `Downloads/ClipForge`.

**No extension?** The helper serves its own UI at **http://127.0.0.1:8787** —
same engine, works in any browser.

---

## What you can pick

**Video** — best available / 2160p / 1440p / 1080p / 720p / 480p / 360p / 240p / 144p,
in MP4, MKV or WebM. Only qualities the video actually has are offered, each with an
estimated file size. MP4 prefers H.264 + AAC so the result plays anywhere.

**MP3** — 320 / 256 / 192 / 160 / 128 kbps, with cover art and title/artist tags embedded.

**WAV** — 16-bit (CD), 24-bit (studio) or 32-bit float; 44.1 / 48 / 96 kHz;
stereo, mono or source. Uncompressed PCM, about 10 MB per minute at 16-bit/44.1.

**Other audio** — M4A, Opus or FLAC. M4A and Opus can often be extracted without
re-encoding, so nothing is lost.

> A note on WAV: the source is already lossy compressed audio. Converting it to
> 24-bit/96 kHz WAV gives you a bigger file, not a better one — the extra headroom
> has nothing to fill it. 16-bit/44.1 kHz is the honest choice unless a specific
> tool you're feeding demands otherwise.

---

## Project layout

```
ClipForge/
├── core/                       the local helper
│   ├── clipforge/
│   │   ├── config.py           settings, stored in ~/.clipforge/
│   │   ├── formats.py          quality ladders + yt-dlp format selectors
│   │   ├── downloader.py       probe, job engine, progress, cancel
│   │   └── server.py           FastAPI routes
│   ├── web/index.html          browser UI fallback
│   ├── requirements.txt
│   ├── run.bat                 Windows launcher
│   └── run.sh                  macOS / Linux launcher
├── extension/                  Chrome MV3 extension
│   ├── manifest.json
│   ├── popup.html/.css/.js     main interface
│   ├── background.js           context menu, badge, notifications
│   ├── content.js/.css         floating button on video pages
│   └── icons/
└── docs/REQUIREMENTS.md        full requirement + acceptance checklist
```

## API

Every route is on `http://127.0.0.1:8787`, bound to loopback — nothing off this
machine can reach it. CORS is restricted to extension origins and localhost.

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | version + preflight checks |
| GET | `/options` | every quality/format choice the UI renders |
| POST | `/probe` | metadata + genuinely available qualities |
| POST | `/download` | queue a job, returns `{id, ...}` |
| GET | `/jobs` | all jobs, newest first |
| GET | `/jobs/{id}/events` | server-sent events, live progress |
| POST | `/jobs/{id}/cancel` | stop a running job |
| GET/POST | `/settings` | read / update config |
| POST | `/reveal/{id}` | open the file's folder |
| POST | `/update-ytdlp` | self-update yt-dlp |

---

## Troubleshooting

**"Helper isn't running"** — the helper window is closed, or something else took
port 8787. Restart it, or run `python -m clipforge.server --port 9001` and change
`HOST` at the top of `extension/popup.js` plus `host_permissions` in `manifest.json`.

**"ffmpeg is missing"** — install it (above) and restart the helper so it picks up
the new PATH.

**Downloads suddenly fail** — YouTube changed something. This is normal and happens
every few weeks. Settings → **Update yt-dlp**, then restart the helper. Fixes it
almost every time.

**"Sign in to confirm"** — age-restricted. Settings → *Use cookies from browser* →
Chrome. yt-dlp then reuses your existing login.

**HTTP 429** — you've been rate-limited. Wait a few minutes.

**Very long filenames on Windows** — already handled (titles are trimmed to 150
characters), but you can change `filename_template` in Settings.

---

## Please read this part

YouTube's Terms of Service **do not permit** downloading videos except through
features YouTube itself provides. Independent of that, most videos are protected
by copyright.

ClipForge is built for the cases where you have the right to keep the file:

- **your own uploads** — the obvious one
- **Creative Commons licensed** videos, within the licence terms
- **public domain** material
- content where the rights holder has given you permission
- archival or research use that your jurisdiction's law actually allows

Using it on anything else is on you, and it is not a grey area with respect to
YouTube's terms. If you put this on a CV or in a portfolio, expect to be asked
about exactly this — having a straight answer about scope and licensing is a much
better look than not having thought about it.

---

MIT licensed. Built as a learning project — the interesting parts are the MV3
messaging model, the streaming progress protocol, and how yt-dlp's format
selectors and ffmpeg postprocessors fit together.
