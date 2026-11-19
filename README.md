# clip-watcher

One command that records a live Twitch **or YouTube** stream, watches its chat
anonymously, and auto-clips the moments where chat explodes — vertical 9:16,
with subtitles.

```
./watch.sh <twitch_channel>                    # Linux / macOS / WSL
./watch.sh @YouTubeHandle                      # YouTube channel
./watch.sh https://www.youtube.com/@xQc/live   # YouTube URL
watch.cmd <twitch_channel>                     # Windows
```

## Simple interface (Windows)

Double-click **`watch-gui.cmd`** (or run it from PowerShell):

```powershell
watch-gui.cmd
```

Enter the channel, set the options, press **Start watching**. The window shows
the clip folder, a live list of finished clips (double-click one to play it),
the log, and a **Stop** button that finishes the current clip before exiting.
Clips go to the folder shown in the window — by default `apk\apk\clips`.

**Clip layout** is selectable in the window:

- **Facecam + gameplay** (default) — the streamer's face on the top half,
  the full game/scene on the bottom half with a blurred fill. The camera is
  found automatically; if it grabs the wrong area, pick the camera corner
  (`top-left`, `top-right`, `bottom-left`, `bottom-right`).
- **9:16 center crop** — the original behaviour, a plain vertical crop of the
  middle of the stream.

## Install

Requires Python 3.9+, [ffmpeg](https://ffmpeg.org/) (which ships ffprobe) and
the `streamlink` + `openai-whisper` + `opencv-python-headless` pip packages.
For **YouTube** chat spike detection also install `pytchat`.

**Windows** (PowerShell, in this folder):

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install streamlink openai-whisper opencv-python-headless pytchat
winget install Gyan.FFmpeg        # if ffmpeg is not already on PATH
watch.cmd xqc
```

**Linux / macOS / WSL**:

```bash
python3 -m venv .venv
.venv/bin/pip install streamlink openai-whisper opencv-python-headless pytchat
sudo apt install ffmpeg           # macOS: brew install ffmpeg
chmod +x watch.sh
./watch.sh xqc
```

`watch.cmd` / `watch.sh` pick up `.venv` automatically. The first clip also
downloads the Whisper model (~150 MB for `base`). Face detection uses the
bundled YuNet model in `assets/` (OpenCV Zoo, Apache-2.0); `opencv-python-headless`
is only needed for the facecam layout.

## Usage

```bash
./watch.sh sodapoppin                       # bare Twitch channel name
./watch.sh https://www.twitch.tv/sodapoppin # or a twitch.tv URL
./watch.sh @MrBeast                         # YouTube handle
./watch.sh https://www.youtube.com/@xQc/live # YouTube URL
./watch.sh sodapoppin --min 5               # quiet chats need a lower bar
./watch.sh sodapoppin --max-clips 3         # grab 3 highlights and exit
./watch.sh sodapoppin --layout facecam      # face on top, gameplay below
./watch.sh sodapoppin --layout facecam --cam bl   # camera bottom-left
./watch.sh sodapoppin --quality 720p60 --model small --language en
```

| flag            | default | meaning                                             |
|-----------------|---------|-----------------------------------------------------|
| `--min N`       | 10      | messages per bucket needed to call it a spike       |
| `--mult X`      | 2.5     | spike must be this many times the recent median     |
| `--bucket N`    | 10      | bucket size in seconds                              |
| `--window N`    | 180     | median window in seconds (3 min)                    |
| `--warmup N`    | 60      | seconds of chat history required before clipping    |
| `--cooldown N`  | 60      | minimum seconds between two clips                   |
| `--pre N`       | 20      | seconds of video kept before the spike              |
| `--post N`      | 10      | seconds of video kept after the spike               |
| `--latency X`   | 4       | extra flush time before cutting (raise if clips are early) |
| `--aspect A:B`  | 9:16    | output aspect ratio                                 |
| `--layout L`    | crop    | `crop` = plain 9:16 crop, `facecam` = face + game  |
| `--cam C`       | auto    | facecam corner: `auto`, `tl`, `tr`, `bl`, `br`       |
| `--height N`    | 1920    | output height, `0` keeps the source size            |
| `--quality Q`   | best    | streamlink quality, e.g. `best`, `720p60`, `480p`   |
| `--model M`     | base    | whisper model: tiny / base / small / medium / large |
| `--language XX` | auto    | force whisper language, e.g. `en`                   |
| `--no-whisper`  | off     | skip subtitle generation                            |
| `--max-clips N` | 0       | stop after N clips, `0` = never stop                |
| `--verbose`     | off     | log every bucket (useful while tuning `--min`)      |
| `--stop-file P` | off     | exit gracefully when file `P` appears (GUI uses it) |

Directories (`--rec-dir`, `--clips-dir`, `--log-dir`) default to `rec/`,
`clips/` and `logs/`; paths to `ffmpeg`, `ffprobe`, `streamlink` and `whisper`
can be overridden with `--ffmpeg`, `--ffprobe`, `--streamlink`, `--whisper`.

## How it works

1. `streamlink` records the live stream to `rec/<channel>-<timestamp>.ts`
   (auto-restarts if the stream drops).
2. Chat is read anonymously over Twitch IRC (`irc.chat.twitch.tv:6667`,
   nick `justinfan#####`, no token). `PING` is answered, dropped connections
   reconnect with backoff.
3. Messages are counted in 10 s buckets. A bucket is a spike when it has at
   least `--min` messages **and** at least `2.5x` the median of the previous
   3 minutes (and it is outside the cooldown). The first minute is warmup.
4. For each spike the recorder waits for the post-spike footage to flush, then
   `ffmpeg` cuts `--pre` seconds before + `--post` seconds after from the tail
   of the recording. With `--layout facecam` it detects the streamer's face
   (YuNet) and stacks a face close-up over the full game frame; with
   `--layout crop` it center-crops to 9:16. Either way the output is 1080x1920.
5. `whisper` writes a matching `.srt` next to each clip.

## Output

```
rec/     raw recordings (kept — ~2-4 GB/hr at 1080p; delete old ones)
clips/   clip-<channel>-<time>.mp4 + .srt pairs
logs/    streamlink.log, ffmpeg.log, whisper.log
```

## Notes

- Only works while the channel is **live**; while offline it retries every 20 s.
- **YouTube** chat is read via `pytchat`. If `pytchat` is not installed the tool
  still records and clips, but spike detection counts zero messages (clips will
  not be triggered automatically). Install with `pip install pytchat`.
- **YouTube** video ID is resolved automatically from the channel `/live` URL
  via `streamlink --json`. If the stream is not yet live, the chat thread retries
  every few seconds.
- Twitch ads are stream content too. If `streamlink` supports
  `--twitch-disable-ads` it is used automatically; disable that with
  `--no-twitch-ads`.
- Clip timing is approximate by a second or two (stream buffering). If clips
  feel early, raise `--latency`; if late, lower it.
- Facecam auto-detection needs a visible face; if the streamer is off-camera
  (or the detector picks the wrong area) the clip falls back to the 9:16 crop
  and the log says so. Choose `--cam tl|tr|bl|br` for a fixed corner.
- Whisper transcription is CPU-friendly with `tiny`/`base`; `small` and up are
  noticeably slower without a GPU.
- Clip streamers who allow it. Respect Twitch's terms and each streamer's rules.
