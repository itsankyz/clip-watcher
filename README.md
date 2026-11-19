<div align="center">

```
 ██████╗██╗     ██╗██████╗       ██╗    ██╗ █████╗ ████████╗ ██████╗██╗  ██╗███████╗██████╗
██╔════╝██║     ██║██╔══██╗      ██║    ██║██╔══██╗╚══██╔══╝██╔════╝██║  ██║██╔════╝██╔══██╗
██║     ██║     ██║██████╔╝      ██║ █╗ ██║███████║   ██║   ██║     ███████║█████╗  ██████╔╝
██║     ██║     ██║██╔═══╝       ██║███╗██║██╔══██║   ██║   ██║     ██╔══██║██╔══╝  ██╔══██╗
╚██████╗███████╗██║██║           ╚███╔███╔╝██║  ██║   ██║   ╚██████╗██║  ██║███████╗██║  ██║
 ╚═════╝╚══════╝╚═╝╚═╝            ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

### 🎬 Auto-clip Twitch & YouTube live streams when chat explodes
### Vertical 9:16 · Facecam layout · Word-by-word captions · Zero tokens needed

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-purple.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()
[![Twitch](https://img.shields.io/badge/Twitch-supported-9146FF?logo=twitch&logoColor=white)](https://twitch.tv)
[![YouTube](https://img.shields.io/badge/YouTube-supported-FF0000?logo=youtube&logoColor=white)](https://youtube.com)

</div>

---

## ⚡ Quick Start

```bash
./watch.sh <twitch_channel>                     # Linux / macOS / WSL
./watch.sh @YouTubeHandle                       # YouTube channel
./watch.sh https://www.youtube.com/@xQc/live    # YouTube URL
watch.cmd <twitch_channel>                      # Windows CLI
watch-gui.cmd                                   # Windows GUI (double-click)
```

---

## 🖥️ Windows GUI

Double-click **`watch-gui.cmd`** — no terminal needed.

- Enter a Twitch channel name, `@YouTubeHandle`, or full stream URL
- Pick your layout, quality, and Whisper model
- Press **Start watching** — clips appear in the list as they are made
- Select a clip to preview a frame; double-click it to play
- The status indicator shows recording/chat connection state and flags clip or subtitle failures; details stay in the log
- Press **Stop** to finish the current clip and exit cleanly

**Clip layouts:**

| Layout | Description |
|--------|-------------|
| 🎭 **Facecam + gameplay** | Enlarged facecam top 40%, full game bottom 60%, subtle divider |
| ✂️ **9:16 center crop** | Plain vertical crop of the middle of the stream |

---

## 📦 Install

Requires **Python 3.9+** and **ffmpeg**.

**Windows**:
```powershell
.\setup.cmd
.\watch-gui.cmd
# Or run the CLI:
.\watch.cmd xqc
```

`setup.cmd` creates the local `.venv`, installs Python requirements, and offers
to install Python and FFmpeg with Winget if they are not already available.

The Inno Setup source for the per-user Windows installer is
[`installer/ClipWatcher.iss`](installer/ClipWatcher.iss). It creates Start Menu
shortcuts, optionally a desktop shortcut, and asks whether to download Whisper's
base model during setup or wait until captions are first generated. Build it
from the project root with `ISCC.exe installer\ClipWatcher.iss`; the output is
written under `dist\installer`. The installer packages only the listed source
files and does not include recordings, clips, logs, local configuration, or
downloaded models.

Pushing a `v*` tag runs the Windows release workflow, which builds and signs
the installer before uploading it to GitHub Releases. Configure the repository
environment `windows-release` with the Actions secrets
`WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` from a trusted
code-signing certificate first. Protect the `v*` tags and require approval for
the release environment.
The certificate's private key is used only by the release runner and is never
included in the installer or source repository.

**Linux / macOS / WSL**:
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo apt install ffmpeg        # macOS: brew install ffmpeg
chmod +x watch.sh
./watch.sh xqc
```

> The first clip downloads the Whisper model (~150 MB for `base`).
> The YuNet face-detection model is downloaded automatically on first facecam use.

### Optional settings

Create a local `config.toml` for reusable settings. Do not commit this file if
it contains private values; command-line options override its values.
`requirements.txt` installs `tomli` automatically on Python 3.9 and 3.10.

---

## 🎯 Usage Examples

```bash
./watch.sh sodapoppin                            # Twitch channel
./watch.sh https://www.twitch.tv/sodapoppin      # Twitch URL
./watch.sh @MrBeast                              # YouTube handle
./watch.sh https://www.youtube.com/@xQc/live     # YouTube URL
./watch.sh sodapoppin --min 5                    # lower bar for quiet chats
./watch.sh sodapoppin --max-clips 3              # grab 3 highlights and exit
./watch.sh sodapoppin --layout facecam           # face on top, gameplay below
./watch.sh sodapoppin --layout facecam --cam bl  # camera fixed bottom-left
./watch.sh sodapoppin --quality 720p60 --model small --language en
./watch.sh sodapoppin --crf 20 --preset fast      # higher-quality encoding
```

---

## ⚙️ All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | `config.toml` if present | load reusable settings from a TOML file |
| `--min N` | `10` | messages per bucket needed to call it a spike |
| `--mult X` | `2.5` | spike must be this many × the recent median |
| `--bucket N` | `10` | bucket size in seconds |
| `--window N` | `180` | median window in seconds (3 min) |
| `--warmup N` | `60` | seconds of chat history required before clipping |
| `--cooldown N` | `60` | minimum seconds between two clips |
| `--pre N` | `20` | seconds of video kept before the spike |
| `--post N` | `10` | seconds of video kept after the spike |
| `--latency X` | `4` | extra recorder flush time before cutting; requested clip window stays aligned |
| `--aspect A:B` | `9:16` | output aspect ratio |
| `--layout L` | `crop` | `crop` = plain 9:16, `facecam` = face + game |
| `--cam C` | `auto` | facecam corner: `auto` `tl` `tr` `bl` `br` |
| `--cam-zoom X` | `1.0` | facecam zoom: `0.7` = wider, `1.5` = closer |
| `--height N` | `1920` | output height px, `0` = keep source size |
| `--quality Q` | `best` | streamlink quality: `best` `720p60` `480p` |
| `--model M` | `base` | whisper model: `tiny` `base` `small` `medium` `large` |
| `--language XX` | auto | force whisper language e.g. `en` |
| `--crf N` | `23` | video quality (0–51): lower values give larger, higher-quality files |
| `--preset P` | `veryfast` | x264 speed/size tradeoff: `ultrafast` through `veryslow` |
| `--rec-keep-gb N` | `2` | maximum space for completed recordings; oldest files are removed first |
| `--chat-max-retries N` | `10` | stop after N consecutive chat connection errors; `0` = unlimited |
| `--discord-webhook-url URL` | off | send the clip filename to a Discord webhook |
| `--no-whisper` | off | skip subtitle generation |
| `--max-clips N` | `0` | stop after N clips, `0` = never stop |
| `--verbose` | off | log every chat bucket |
| `--stop-file P` | off | exit gracefully when file P appears |

Directories `--rec-dir` `--clips-dir` `--log-dir` default to `rec/` `clips/` `logs/`.
Binary paths can be overridden with `--ffmpeg` `--ffprobe` `--streamlink` `--whisper`.

---

## 🔧 How It Works

```
Stream ──► streamlink ──► rec/<channel>-<timestamp>.ts
                                    │
Chat IRC / pytchat                  │
     │                              │
     ▼                              ▼
SpikeDetector              ffmpeg cuts clip
(10s buckets,              (--pre s before +
 2.5x median)               --post s after)
     │                              │
     └──── spike? ─────────────────►│
                                    ▼
                           YuNet face detect
                           build_facecam_filter
                           (40% cam / 60% game)
                                    │
                                    ▼
                           whisper → .srt
                           srt_to_ass → word karaoke
                           burn_captions → final .mp4
```

1. **streamlink** records the stream to `rec/` — auto-restarts on drop
2. **Chat** is read anonymously (Twitch IRC / YouTube pytchat) — no token needed
3. A **spike** = bucket has ≥ `--min` messages AND ≥ `2.5×` the 3-min median
4. **ffmpeg** cuts the clip, YuNet detects the face, stacks cam (top 40%) + game (bottom 60%)
5. **Whisper** transcribes → word-by-word captions burned in with purple highlight

---

## 📁 Output

```
rec/     raw recordings  (~2–4 GB/hr at 1080p — oldest completed files are cleaned up)
clips/   clip-<channel>-<timestamp>.mp4  +  .srt
logs/    streamlink.log  ffmpeg.log  whisper.log
```

---

## 📝 Notes

- Only works while the channel is **live** — retries every 20 s while offline
- **YouTube** chat needs `pip install pytchat` — without it the tool still clips but won't detect spikes automatically
- YouTube channel live IDs are resolved with Streamlink, with optional `yt-dlp` fallback when installed
- Twitch ads are skipped automatically via `--twitch-disable-ads` if streamlink supports it
- `--latency` gives the recorder extra flush time; the cutter compensates so the requested pre/post window stays aligned
- Old recordings are pruned every minute and after clips are saved; the active recording is never deleted
- Chat retry limits apply to errors; YouTube offline/not-live checks continue retrying
- Set `CLIP_WATCHER_DISCORD_WEBHOOK` instead of storing a webhook URL in a config file
- Facecam falls back to 9:16 crop if no face is detected — use `--cam tl|tr|bl|br` to fix a corner
- Facecam auto-detection prefers corner faces so it is less likely to crop a character in the gameplay
- Adjust **Face zoom** in the GUI; higher values make the face larger, and the Clips panel previews the resulting output
- Whisper automatically uses CUDA when available, otherwise CPU; word-level timestamps drive caption highlighting

---

## 📄 License

[CC BY-NC 4.0](LICENSE) © 2025 Ankur Yadav — free to use and modify, **no commercial use**.
