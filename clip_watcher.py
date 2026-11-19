#!/usr/bin/env python3
"""clip-watcher: record a live Twitch or YouTube stream, watch its chat
anonymously, auto-clip the moments where chat explodes (vertical 9:16)
and subtitle them."""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import math
import os
import queue
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667
PLATFORM_TWITCH = "twitch"
PLATFORM_YOUTUBE = "youtube"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
CAM_MODEL = os.path.join(ASSETS, "face_detection_yunet_2023mar.onnx")
CAM_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
CAM_ASPECT = 1.125


def no_window():
    return {"creationflags": CREATE_NO_WINDOW} if CREATE_NO_WINDOW else {}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def parse_channel(target):
    """Return (platform, login, url) for a Twitch or YouTube target."""
    t = target.strip()

    # ---- YouTube ----
    yt_patterns = ("youtube.com/", "youtu.be/", "youtube.com/@")
    if any(p in t for p in yt_patterns) or t.startswith("@"):
        # direct video URL: youtube.com/watch?v=ID or youtube.com/live/ID
        vid = re.search(r"(?:watch\?v=|/live/)([A-Za-z0-9_-]{11})", t)
        if vid:
            login = vid.group(1)
            url = f"https://www.youtube.com/watch?v={login}"
            return PLATFORM_YOUTUBE, login, url
        # channel handle: /@handle or bare @handle
        handle = re.search(r"/@([A-Za-z0-9_.-]+)", t)
        if handle:
            login = handle.group(1)
        elif t.startswith("@"):
            login = t.lstrip("@")
        else:
            # youtu.be/ID
            m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", t)
            if m:
                login = m.group(1)
                url = f"https://www.youtube.com/watch?v={login}"
                return PLATFORM_YOUTUBE, login, url
            login = re.split(r"[/?#]", t.split("://", 1)[-1].lstrip("/"))[-1]
        url = f"https://www.youtube.com/@{login}/live"
        return PLATFORM_YOUTUBE, login, url

    # ---- Twitch ----
    if t.lower().startswith("twitch:"):
        t = t.split(":", 1)[1]
    if "twitch.tv/" in t:
        t = t.split("twitch.tv/", 1)[1]
    elif "://" in t:
        rest = t.split("://", 1)[1]
        t = rest.split("/", 1)[1] if "/" in rest else ""
    t = re.split(r"[/?#]", t.lstrip("#"), maxsplit=1)[0].lower()
    if not re.fullmatch(r"[a-z0-9_]{2,25}", t):
        raise SystemExit(f"error: cannot parse a Twitch channel or YouTube URL from {target!r}")
    return PLATFORM_TWITCH, t, f"https://twitch.tv/{t}"


def build_vf(aspect="9:16", height=1920):
    aw, ah = (int(part) for part in aspect.split(":"))
    vf = (
        f"crop=w='min(iw,ih*{aw}/{ah})':h='min(ih,iw*{ah}/{aw})'"
        f":x='(iw-ow)/2':y='(ih-oh)/2'"
    )
    if height:
        width = int(round(height * aw / ah / 2)) * 2
        vf += f",scale={width}:{height}:flags=lanczos"
    else:
        vf += ",scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return vf + ",setsar=1"


class SpikeDetector:
    def __init__(self, bucket=10, window=180, multiplier=2.5, min_msgs=10,
                 warmup=60, cooldown=60):
        self.bucket = bucket
        self.multiplier = multiplier
        self.min_msgs = min_msgs
        self.cooldown = cooldown
        capacity = max(1, window // bucket)
        self.warmup_buckets = min(max(1, math.ceil(warmup / bucket)), capacity)
        self.history = collections.deque(maxlen=capacity)
        self.last_spike = 0.0
        self.count = 0
        self.last = (0, 0.0)

    def add(self, n=1):
        self.count += n

    def close(self, bucket_start):
        count, self.count = self.count, 0
        med = statistics.median(self.history) if self.history else 0.0
        ready = len(self.history) >= self.warmup_buckets
        thin = (bucket_start - self.last_spike) < self.cooldown
        spike = (
            ready
            and not thin
            and count >= self.min_msgs
            and count >= self.multiplier * med
        )
        self.history.append(count)
        self.last = (count, float(med))
        if spike:
            self.last_spike = bucket_start
            return count, float(med)
        return None


class Recorder(threading.Thread):
    def __init__(self, args, stop):
        super().__init__(daemon=True, name="recorder")
        self.args = args
        self.stop = stop
        self.proc = None
        self.path = None
        self.lock = threading.Lock()
        self.ended = threading.Event()

    def segment(self):
        with self.lock:
            path = self.path
        if not path or not os.path.exists(path):
            return None
        try:
            return path, os.path.getsize(path)
        except OSError:
            return None

    def _command(self, path):
        cmd = [self.args.streamlink]
        if self.args.platform == PLATFORM_TWITCH and self.args.streamlink_disable_ads:
            cmd.append("--twitch-disable-ads")
        cmd += ["-o", path, self.args.url, self.args.quality]
        return cmd

    def _terminate(self):
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=5)
        self.proc = None

    def run(self):
        log_path = os.path.join(self.args.log_dir, "streamlink.log")
        while not self.stop.is_set():
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(self.args.rec_dir, f"{self.args.login}-{stamp}.ts")
            log(f"[rec] recording -> {path}")
            with self.lock:
                self.path = path
            with open(log_path, "ab") as logf:
                try:
                    self.proc = subprocess.Popen(
                        self._command(path),
                        stdin=subprocess.DEVNULL,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        **no_window(),
                    )
                except OSError as exc:
                    log(f"[rec] could not start streamlink: {exc}")
                    return
                while not self.stop.is_set() and self.proc.poll() is None:
                    self.stop.wait(1)
                rc = self.proc.poll()
            if self.stop.is_set():
                self._terminate()
                return
            if rc == 0:
                log("[rec] stream ended")
                self.ended.set()
                return
            with self.lock:
                self.path = None
            with contextlib.suppress(OSError):
                if os.path.getsize(path) < 1024:
                    os.remove(path)
            log(f"[rec] streamlink exited (code {rc}) -- retrying in 20 s "
                f"(see {log_path})")
            self.stop.wait(20)


class _ChatBase(threading.Thread):
    """Shared drain/pending logic for all chat backends."""
    def __init__(self):
        super().__init__(daemon=True, name="chat")
        self.pending = 0
        self.lock = threading.Lock()
        self.connected = threading.Event()

    def drain(self):
        with self.lock:
            n, self.pending = self.pending, 0
        return n

    def _inc(self):
        with self.lock:
            self.pending += 1


class TwitchChat(_ChatBase):
    def __init__(self, args, stop):
        super().__init__()
        self.args = args
        self.stop = stop

    def run(self):
        nick = f"justinfan{random.randint(10000, 99999)}"
        backoff = 3
        while not self.stop.is_set():
            sock = None
            buf = b""  # reset buffer on every (re)connect
            try:
                sock = socket.create_connection(
                    (self.args.irc_host, self.args.irc_port), timeout=15)
                sock.settimeout(1.0)
                sock.sendall(
                    f"PASS SCHMOOPIIE\r\nNICK {nick}\r\n"
                    f"JOIN #{self.args.login}\r\n".encode())
                log(f"[chat] connected to #{self.args.login} as {nick}")
                self.connected.set()
                backoff = 3
                while not self.stop.is_set():
                    try:
                        data = sock.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError("server closed the connection")
                    buf += data
                    while b"\r\n" in buf:
                        raw, buf = buf.split(b"\r\n", 1)
                        if raw.startswith(b"PING"):
                            sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG " in raw:
                            self._inc()
            except Exception as exc:
                if not self.stop.is_set():
                    log(f"[chat] {exc} -- reconnecting in {backoff}s")
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 30)
            finally:
                self.connected.clear()
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.close()


class YouTubeChat(_ChatBase):
    """Polls YouTube live chat via pytchat (pip install pytchat)."""
    def __init__(self, args, stop):
        super().__init__()
        self.args = args
        self.stop = stop

    def run(self):
        try:
            import pytchat
        except ImportError:
            log("[chat] pytchat not installed -- pip install pytchat")
            log("[chat] spike detection will use zero chat counts")
            self.stop.wait()
            return

        backoff = 5
        while not self.stop.is_set():
            chat = None
            try:
                # pytchat needs a video ID; if we have a channel /live URL
                # streamlink resolves it, but pytchat needs the actual video id.
                # We resolve it once via yt-dlp / streamlink --json.
                video_id = self._resolve_video_id()
                if not video_id:
                    log(f"[chat] YouTube: stream not live yet, retrying in {backoff}s")
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                log(f"[chat] YouTube: reading chat for video {video_id}")
                chat = pytchat.create(video_id=video_id, interruptable=False)
                self.connected.set()
                backoff = 5
                while not self.stop.is_set() and chat.is_alive():
                    for item in chat.get().sync_items():
                        self._inc()
                    self.stop.wait(1)
            except Exception as exc:
                if not self.stop.is_set():
                    log(f"[chat] YouTube error: {exc} -- retrying in {backoff}s")
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 60)
            finally:
                self.connected.clear()
                if chat is not None:
                    with contextlib.suppress(Exception):
                        chat.terminate()

    def _resolve_video_id(self):
        """Return the live video ID. If the URL already contains one, use it directly."""
        # Direct video URL already has the ID (watch?v= or /live/ID)
        m = re.search(r"(?:watch\?v=|/live/)([A-Za-z0-9_-]{11})", self.args.url)
        if m:
            return m.group(1)
        # Channel /live URL — ask streamlink to resolve the actual stream URL
        try:
            import json
            out = subprocess.check_output(
                [self.args.streamlink, "--json", self.args.url, "best"],
                stderr=subprocess.DEVNULL, timeout=30, text=True, **no_window())
            data = json.loads(out)
            url = data.get("url", "")
            m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
            if m:
                return m.group(1)
            return data.get("metadata", {}).get("id", "")
        except Exception:
            return ""


def srt_to_ass(srt_path, ass_path, video_width=1080, cam_h=768):
    """Convert Whisper's word-highlighted SRT to ASS with timed word emphasis."""
    import re as _re

    def ts_to_cs(ts):
        """SRT timestamp -> centiseconds."""
        h, m, s_ms = ts.strip().split(":")
        s, ms = s_ms.replace(",", ".").split(".")
        return (int(h)*3600 + int(m)*60 + int(s)) * 100 + int(ms) // 10

    def cs_to_ass(cs):
        h = cs // 360000; cs %= 360000
        m = cs // 6000;   cs %= 6000
        s = cs // 100;    cs %= 100
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    with open(srt_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()

    # parse SRT blocks
    blocks = []
    for block in _re.split(r"\n{2,}", raw.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        m = _re.match(r"(\S+)\s*-->\s*(\S+)", lines[1] if lines[0].isdigit() else lines[0])
        if not m:
            continue
        start_cs = ts_to_cs(m.group(1))
        end_cs   = ts_to_cs(m.group(2))
        text = " ".join(lines[2:] if lines[0].isdigit() else lines[1:]).strip()
        if text:
            blocks.append((start_cs, end_cs, text))

    # ASS header — captions centered at the cam/game boundary
    margin_v = cam_h  # vertical position = bottom of cam strip
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: 1920
WrapStyle: 1

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,Strikeout,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,20,20,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    lines_out = []
    for start_cs, end_cs, text in blocks:
        active_word = _re.search(r"<u>(.*?)</u>", text, flags=_re.IGNORECASE)
        if active_word:
            text = _re.sub(
                r"<u>(.*?)</u>",
                r"{\\1c&H00FFFFFF&\\3c&HFF0080&\\bord4\\shad0}\1{\\rDefault}",
                text,
                flags=_re.IGNORECASE,
            )
        text = _re.sub(r"<[^>]+>", "", text).strip()
        if not text:
            continue
        lines_out.append(
            "Dialogue: 0," + cs_to_ass(start_cs) + "," + cs_to_ass(end_cs) +
            ",Default,,0,0,0,," + text
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(lines_out) + "\n")


def burn_captions(ffmpeg, clip, srt, out, log_path, cam_h=768, crf=23, preset="veryfast"):
    """Burn word-by-word ASS captions into the clip."""
    import tempfile as _tmp
    ass = _tmp.mktemp(suffix=".ass")
    try:
        srt_to_ass(srt, ass, video_width=1080, cam_h=cam_h)
        ass_escaped = ass.replace("\\", "/").replace(":", "\\:")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", clip,
            "-vf", f"subtitles='{ass_escaped}'",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-c:a", "copy",
            "-movflags", "+faststart",
            out,
        ]
        with open(log_path, "ab") as logf:
            rc = subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                 **no_window())
        return rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    finally:
        with contextlib.suppress(OSError):
            os.remove(ass)



def probe_duration(ffprobe, path):
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            stderr=subprocess.DEVNULL, timeout=20, text=True, **no_window())
        return float(out.strip())
    except Exception:
        return 0.0


def build_facecam_filter(rect, aspect="9:16", height=1920):
    """Cam top 40%, gameplay bottom 60% — both crop-fill, no black bars.
    Game gets a sharpening pass. Captions are burned in separately via burn_captions().
    """
    aw, ah = (int(part) for part in aspect.split(":"))
    width = int(round(height * aw / ah / 2)) * 2
    cam_h  = int(height * 0.40) // 2 * 2   # 768 px at 1920
    game_h = height - cam_h                 # 1152 px at 1920
    x, y, cw, ch = rect
    return (
        "[0:v]split=2[f][g];"
        # facecam: crop detected region → scale+crop to fill top 40%
        f"[f]crop={cw}:{ch}:{x}:{y},"
        f"scale={width}:{cam_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{cam_h},setsar=1[fc];"
        # gameplay: scale+crop to fill bottom 60%, then sharpen
        f"[g]scale={width}:{game_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{game_h},"
        f"unsharp=lx=3:ly=3:la=0.8:cx=3:cy=3:ca=0.4,setsar=1[gp];"
        "[fc][gp]vstack=inputs=2,setsar=1,format=yuv420p[out]"
    )


def probe_size(ffprobe, path):
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            stderr=subprocess.DEVNULL, timeout=20, text=True, **no_window())
        line = next((ln for ln in out.splitlines() if ln.strip()), "")
        width, height = line.split(",")[:2]
        return int(width), int(height)
    except Exception:
        return 0, 0


def manual_cam_rect(corner, width, height):
    if corner == "auto" or not width or not height:
        return None
    cw = int(width * 0.38) // 2 * 2
    ch = int(cw / CAM_ASPECT) // 2 * 2
    if ch > height:
        ch = height // 2 * 2
    x = 0 if corner in ("tl", "bl") else width - cw
    y = 0 if corner in ("tl", "tr") else height - ch
    return x, y, cw, ch


def _ensure_cam_model():
    """Download the YuNet face-detection model if it is not present."""
    if os.path.exists(CAM_MODEL):
        return True
    try:
        import urllib.request
        os.makedirs(ASSETS, exist_ok=True)
        log(f"[clip] downloading face-detection model from opencv_zoo ...")
        urllib.request.urlretrieve(CAM_MODEL_URL, CAM_MODEL)
        log(f"[clip] model saved to {CAM_MODEL}")
        return True
    except Exception as exc:
        log(f"[clip] could not download model: {exc} -- using 9:16 crop")
        return False


def detect_cam(ffmpeg, ffprobe, src, duration, log_path):
    if not _ensure_cam_model():
        return None
    try:
        os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
        import cv2
    except ImportError:
        log("[clip] facecam: opencv missing -- pip install opencv-python-headless")
        return None

    width, height = probe_size(ffprobe, src)
    if not width or not height:
        return None
    detector = cv2.FaceDetectorYN_create(CAM_MODEL, "", (640, 360), 0.6, 0.3, 5000)
    best = None
    with tempfile.TemporaryDirectory(prefix="clipcam-") as tmp:
        pattern = os.path.join(tmp, "f%02d.jpg")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-sseof", f"-{duration}", "-i", src, "-t", f"{duration}",
               "-vf", "fps=1/5,scale=640:-2", "-frames:v", "8", pattern]
        with open(log_path, "ab") as logf:
            subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT,
                            **no_window())
        for name in sorted(os.listdir(tmp)):
            img = cv2.imread(os.path.join(tmp, name))
            if img is None:
                continue
            sh, sw = img.shape[:2]
            detector.setInputSize((sw, sh))
            _, faces = detector.detect(img)
            if faces is None:
                continue
            for face in faces:
                fx, fy, fw, fh = face[:4]
                area = fw * fh
                if best is None or area > best[0]:
                    best = (area, fx, fy, fw, fh, sw, sh)

    if best is None:
        log("[clip] facecam: no face found, using 9:16 crop "
            "(set the camera corner manually if this keeps happening)")
        return None
    _, fx, fy, fw, fh, sw, sh = best
    sx, sy = width / sw, height / sh
    fx, fy, fw, fh = fx * sx, fy * sy, fw * sx, fh * sy
    cx, cy = fx + fw / 2, fy + fh / 2
    box_h = min(float(height), fh * 3.0)
    box_w = min(float(width), box_h * CAM_ASPECT)
    box_h = min(float(height), box_w / CAM_ASPECT)
    bw = int(box_w) // 2 * 2
    bh = int(box_h) // 2 * 2
    x = int(min(max(0.0, cx - box_w / 2), width - bw))
    y = int(min(max(0.0, cy - fh * 0.9), height - bh))
    log(f"[clip] facecam: face at {x},{y} {bw}x{bh} (source {width}x{height})")
    return x, y, bw, bh


def ffmpeg_cut(ffmpeg, seek, src, out, duration, vf, log_path,
               filter_complex=None, crf=23, preset="veryfast"):
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        *seek, "-i", src, "-t", f"{duration:.3f}",
    ]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "[out]", "-map", "0:a:0?"]
    else:
        cmd += ["-vf", vf]
    cmd += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out,
    ]
    with open(log_path, "ab") as logf:
        rc = subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             **no_window())
    return rc == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def _detect_device():
    """Return 'cuda' if a CUDA GPU is available, else 'cpu'."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _cleanup_rec(rec_dir, keep_bytes=2 * 1024 ** 3, exclude=None):
    """Delete oldest completed .ts files while preserving the active recording."""
    excluded = (
        os.path.normcase(os.path.abspath(exclude))
        if exclude is not None else None
    )
    try:
        names = os.listdir(rec_dir)
    except OSError as exc:
        log(f"[rec] could not list recordings in {rec_dir}: {exc}")
        return

    files = []
    for name in names:
        path = os.path.join(rec_dir, name)
        if not name.lower().endswith(".ts") or os.path.normcase(os.path.abspath(path)) == excluded:
            continue
        try:
            stat = os.stat(path)
        except OSError as exc:
            log(f"[rec] could not inspect {path}: {exc}")
            continue
        if os.path.isfile(path):
            files.append((path, stat.st_mtime, stat.st_size))

    files.sort(key=lambda item: item[1])
    total = sum(size for _, _, size in files)
    for path, _, size in files:
        if total <= keep_bytes:
            break
        try:
            os.remove(path)
        except OSError as exc:
            log(f"[rec] could not delete old recording {path}: {exc}")
            continue
        total -= size
        log(f"[rec] deleted old recording {os.path.basename(path)} "
            f"(freed {size // 1024 // 1024} MB)")


def transcribe(whisper, clip, args):
    device = _detect_device()
    cmd = [whisper, clip, "--model", args.model,
           "--device", device,
           "--word_timestamps", "True",
           "--highlight_words", "True",
           "--output_format", "srt", "--output_dir", args.clips_dir]
    if args.language:
        cmd += ["--language", args.language]
    if device == "cuda":
        log(f"[whisper] using GPU (CUDA)")
    else:
        log(f"[whisper] using CPU")
    log_path = os.path.join(args.log_dir, "whisper.log")
    with open(log_path, "ab") as logf:
        rc = subprocess.call(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             **no_window())
    srt = os.path.join(args.clips_dir, os.path.splitext(os.path.basename(clip))[0] + ".srt")
    if rc != 0 or not os.path.exists(srt):
        log(f"[whisper] failed for {os.path.basename(clip)} (see {log_path})")
        return
    log(f"[whisper] subtitles -> {srt}")
    # burn word-by-word captions into the clip
    if args.layout == "facecam":
        aw, ah = (int(p) for p in args.aspect.split(":"))
        width = int(round(args.height * aw / ah / 2)) * 2
        cam_h = int(args.height * 0.40) // 2 * 2
        burned = clip.replace(".mp4", "-cap.mp4")
        ffmpeg_log = os.path.join(args.log_dir, "ffmpeg.log")
        ok = burn_captions(args.ffmpeg, clip, srt, burned, ffmpeg_log,
                            cam_h=cam_h, crf=args.crf, preset=args.preset)
        if ok:
            with contextlib.suppress(OSError):
                os.replace(burned, clip)
            log(f"[whisper] captions burned into {os.path.basename(clip)}")
        else:
            with contextlib.suppress(OSError):
                os.remove(burned)
            log(f"[whisper] caption burn failed, keeping plain clip")


class Transcriber(threading.Thread):
    def __init__(self, args):
        super().__init__(daemon=True, name="whisper")
        self.args = args
        self.jobs = queue.Queue()

    def submit(self, clip):
        self.jobs.put(clip)

    def finish(self, timeout=None):
        self.jobs.put(None)
        self.join(timeout)

    def run(self):
        while True:
            clip = self.jobs.get()
            try:
                if clip is None:
                    return
                transcribe(self.args.whisper, clip, self.args)
            except Exception as exc:
                log(f"[whisper] error: {exc}")
            finally:
                self.jobs.task_done()


class Clipper(threading.Thread):
    def __init__(self, args):
        super().__init__(daemon=True, name="clipper")
        self.args = args
        self.abort = threading.Event()
        self.jobs = queue.Queue()
        self.transcriber = Transcriber(args)
        self.cam_cache = {}
        self.done = 0
        self.made = 0
        self.submitted = 0
        self.lock = threading.Lock()

    def submit(self, path, spike_end, count, median):
        with self.lock:
            self.submitted += 1
        self.jobs.put(dict(path=path, spike_end=spike_end,
                           count=count, median=median))

    def finish(self, timeout=None):
        self.jobs.put(None)
        self.join(timeout)
        if self.transcriber.is_alive():
            self.transcriber.finish(timeout)

    def run(self):
        self.transcriber.start()
        while True:
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                return
            try:
                self._process(job)
            except Exception as exc:
                log(f"[clip] error: {exc}")
            finally:
                self.jobs.task_done()
                with self.lock:
                    self.done += 1

    def _process(self, job):
        args = self.args
        due = job["spike_end"] + args.post + args.latency
        while not self.abort.is_set():
            remaining = due - time.time()
            if remaining <= 0:
                break
            self.abort.wait(min(remaining, 1.0))
        if self.abort.is_set():
            return
        late = time.time() - due
        if late > 5:
            log(f"[clip] warning: cutting {late:.0f}s late, clip may be shifted")

        try:
            file_size = os.path.getsize(job["path"])
        except OSError:
            file_size = 0
        if file_size < 200_000:
            log(f"[clip] skipped {job['count']}-message spike -- recording vanished or too small")
            return

        stamp = dt.datetime.fromtimestamp(job["spike_end"]).strftime("%Y%m%d-%H%M%S")
        base = f"clip-{args.login}-{stamp}"
        out = os.path.join(args.clips_dir, base + ".mp4")
        n = 2
        while os.path.exists(out):
            out = os.path.join(args.clips_dir, f"{base}-{n}.mp4")
            n += 1

        duration = args.pre + args.post
        log(f"[clip] cutting {duration}s ({args.pre}s before + {args.post}s after) "
            f"-> {os.path.basename(out)}")
        ffmpeg_log = os.path.join(args.log_dir, "ffmpeg.log")
        vf = build_vf(args.aspect, args.height)
        layout_filter = None
        if args.layout == "facecam":
            rect = self._cam_rect(job["path"], duration)
            if rect:
                layout_filter = build_facecam_filter(rect, args.aspect, args.height)
        ok = ffmpeg_cut(args.ffmpeg, ["-sseof", f"-{duration}"],
                        job["path"], out, duration, vf, ffmpeg_log,
                        filter_complex=layout_filter,
                        crf=args.crf, preset=args.preset)
        if not ok:
            total = probe_duration(args.ffprobe, job["path"])
            start = max(0.0, total - duration)
            if total > 0:
                ok = ffmpeg_cut(args.ffmpeg, ["-ss", f"{start:.3f}"],
                                job["path"], out, duration, vf, ffmpeg_log,
                                filter_complex=layout_filter,
                                crf=args.crf, preset=args.preset)
        if not ok:
            log(f"[clip] ffmpeg failed (see {ffmpeg_log})")
            with contextlib.suppress(OSError):
                os.remove(out)
            return

        log(f"[clip] saved {out}  ({job['count']} msgs in {args.bucket} s, "
            f"median {job['median']:.1f})")
        with self.lock:
            self.made += 1
        _cleanup_rec(args.rec_dir, keep_bytes=args.rec_keep_gb * 1024 ** 3,
                     exclude=job["path"])
        if args.whisper and not args.no_whisper:
            self.transcriber.submit(out)
    def _cam_rect(self, path, duration):
        args = self.args
        if args.cam != "auto":
            width, height = probe_size(args.ffprobe, path)
            return manual_cam_rect(args.cam, width, height)
        if path in self.cam_cache:
            return self.cam_cache[path]
        rect = detect_cam(args.ffmpeg, args.ffprobe, path, duration,
                          os.path.join(args.log_dir, "ffmpeg.log"))
        if rect:
            self.cam_cache[path] = rect
        return rect


def streamlink_supports(streamlink, flag):
    try:
        out = subprocess.run([streamlink, "--help"], capture_output=True,
                             text=True, timeout=20, **no_window()).stdout
        return flag in out
    except Exception:
        return False


def resolve_binary(explicit, name, siblings=()):
    if explicit:
        if not os.path.exists(explicit):
            raise SystemExit(f"error: --{name} path not found: {explicit!r}")
        return explicit
    found = shutil.which(name)
    if found:
        return found
    here = os.path.dirname(os.path.abspath(sys.executable))
    for suffix in (".exe", ""):
        candidate = os.path.join(here, name + suffix)
        if os.path.exists(candidate):
            return candidate
    for candidate in siblings:
        if os.path.exists(candidate):
            return candidate
    return None


def make_chat(args, stop):
    if args.platform == PLATFORM_YOUTUBE:
        return YouTubeChat(args, stop)
    return TwitchChat(args, stop)


def prepare(args):
    if not re.fullmatch(r"\d+:\d+", args.aspect):
        raise SystemExit(f"error: --aspect must look like 9:16, got {args.aspect!r}")
    aw, ah = (int(part) for part in args.aspect.split(":"))
    if aw < 1 or ah < 1:
        raise SystemExit(f"error: --aspect parts must be positive, got {args.aspect!r}")
    if args.pre + args.post < 1:
        raise SystemExit("error: --pre + --post must be at least 1 second")
    checks = {
        "--bucket": (args.bucket, 1),
        "--window": (args.window, 1),
        "--warmup": (args.warmup, 0),
        "--min": (args.min_msgs, 1),
        "--mult": (args.multiplier, 0),
        "--cooldown": (args.cooldown, 0),
        "--pre": (args.pre, 0),
        "--post": (args.post, 0),
        "--latency": (args.latency, 0),
        "--height": (args.height, 0),
        "--max-clips": (args.max_clips, 0),
    }
    for flag, (value, low) in checks.items():
        if value < low:
            raise SystemExit(f"error: {flag} must be >= {low}, got {value}")
    args.platform, args.login, args.url = parse_channel(args.channel)
    for d in (args.rec_dir, args.clips_dir, args.log_dir):
        os.makedirs(d, exist_ok=True)

    args.ffmpeg = resolve_binary(args.ffmpeg_bin, "ffmpeg")
    if not args.ffmpeg:
        raise SystemExit("error: ffmpeg not found -- install it or pass --ffmpeg PATH")
    ffmpeg_dir = os.path.dirname(args.ffmpeg)
    args.ffprobe = resolve_binary(
        args.ffprobe_bin, "ffprobe",
        (os.path.join(ffmpeg_dir, "ffprobe.exe"),
         os.path.join(ffmpeg_dir, "ffprobe")))
    if not args.ffprobe:
        raise SystemExit("error: ffprobe not found -- install ffmpeg (it ships together)")
    args.streamlink = resolve_binary(args.streamlink_bin, "streamlink")
    if not args.streamlink:
        raise SystemExit("error: streamlink not found -- pip install streamlink")
    args.streamlink_disable_ads = (
        args.platform == PLATFORM_TWITCH
        and not args.no_twitch_ads
        and streamlink_supports(args.streamlink, "--twitch-disable-ads"))

    args.whisper = None if args.no_whisper else resolve_binary(
        args.whisper_bin, "whisper")
    if not args.no_whisper and not args.whisper:
        log("[watch] whisper not found -- clips will be made without subtitles "
            "(pip install openai-whisper)")
        args.no_whisper = True
    return args


def run(args):
    log(f"[watch] watching {args.url} [{args.platform}]")
    log(f"[watch] spike = >= {args.min_msgs} msgs/{args.bucket}s and "
        f"{args.multiplier}x the {args.window}s median "
        f"(cooldown {args.cooldown}s, warmup {args.warmup}s)")
    log(f"[watch] clips: {args.pre}s before + {args.post}s after, "
        f"{args.aspect}, whisper model {args.model}")
    if args.layout == "facecam":
        log("[watch] layout: facecam on top + gameplay below"
            + (" (auto-detecting camera)" if args.cam == "auto" else
               f" (camera {args.cam})"))
    log("[watch] Ctrl-C to stop")

    stop = threading.Event()
    detector = SpikeDetector(
        bucket=args.bucket, window=args.window, multiplier=args.multiplier,
        min_msgs=args.min_msgs, warmup=args.warmup, cooldown=args.cooldown)
    chat = make_chat(args, stop)
    recorder = Recorder(args, stop)
    clipper = Clipper(args)
    chat.start()
    recorder.start()
    clipper.start()

    current = int(time.time() // args.bucket)
    interrupted = False
    limit_hit = False
    try:
        while not stop.is_set():
            time.sleep(0.25)
            if args.stop_file and os.path.exists(args.stop_file):
                log("[watch] stop requested")
                break
            idx = int(time.time() // args.bucket)
            while current < idx:
                bucket_start = current * args.bucket
                detector.add(chat.drain() if current == idx - 1 else 0)
                result = detector.close(bucket_start)
                if args.verbose:
                    count, med = detector.last
                    log(f"[chat] {count:>3} msgs in bucket "
                        f"{dt.datetime.fromtimestamp(bucket_start):%H:%M:%S} "
                        f"(median {med:.1f})")
                elif not result and detector.last[0] > 0:
                    count, med = detector.last
                    log(f"[chat] {count:>3} msgs (median {med:.1f}, need "
                        f">={args.min_msgs} and >={args.multiplier}x median)")
                if result:
                    count, med = result
                    seg = recorder.segment()
                    if not seg or seg[1] < 200_000:
                        log(f"[clip] {count}-message spike ignored -- "
                            f"recording not ready yet")
                    else:
                        clipper.submit(seg[0], bucket_start + args.bucket, count, med)
                        log(f"[clip] SPIKE {count} msgs/{args.bucket}s "
                            f"(median {med:.1f}) -- clip queued")
                        if args.max_clips and clipper.submitted >= args.max_clips:
                            log(f"[watch] reached --max-clips {args.max_clips}")
                            limit_hit = True
                            break
                current += 1
            if limit_hit:
                break
            if recorder.ended.is_set():
                log("[watch] stream ended")
                break
    except KeyboardInterrupt:
        interrupted = True
        log("[watch] interrupted")
    finally:
        stop.set()
        recorder.join(10)
        chat.join(2)
        if args.stop_file:
            with contextlib.suppress(OSError):
                os.remove(args.stop_file)
        try:
            if interrupted:
                clipper.abort.set()
                clipper.finish(10)
            else:
                log("[watch] finishing queued clips (Ctrl-C to abandon)...")
                clipper.finish()
        except KeyboardInterrupt:
            log("[watch] forced stop -- some clips may still be pending")
        with clipper.lock:
            made = clipper.made
        log(f"[watch] done -- {made} clip(s) in {args.clips_dir}{os.sep}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="watch.sh",
        description="Record a live Twitch or YouTube stream, watch chat anonymously and "
                    "auto-clip chat spikes to vertical video with subtitles.")
    p.add_argument("channel", help="twitch channel, twitch.tv URL, @YouTubeHandle, or youtube.com URL")
    p.add_argument("--min", dest="min_msgs", type=int, default=10,
                   help="messages per bucket required for a spike (default 10; "
                        "quiet chats: --min 5)")
    p.add_argument("--mult", dest="multiplier", type=float, default=2.5,
                   help="spike multiplier over the median (default 2.5)")
    p.add_argument("--bucket", type=int, default=10,
                   help="bucket size in seconds (default 10)")
    p.add_argument("--window", type=int, default=180,
                   help="median window in seconds (default 180)")
    p.add_argument("--warmup", type=int, default=60,
                   help="seconds of chat history required before clipping (default 60)")
    p.add_argument("--cooldown", type=int, default=60,
                   help="minimum seconds between two clips (default 60)")
    p.add_argument("--max-clips", type=int, default=0,
                   help="stop after this many clips, 0 = never stop (default 0)")
    p.add_argument("--pre", type=int, default=20,
                   help="seconds of video before the spike (default 20)")
    p.add_argument("--post", type=int, default=10,
                   help="seconds of video after the spike (default 10)")
    p.add_argument("--latency", type=float, default=4.0,
                   help="extra seconds to let the recorder flush before cutting "
                        "(default 4)")
    p.add_argument("--aspect", default="9:16", help="output aspect ratio (default 9:16)")
    p.add_argument("--layout", choices=["crop", "facecam"], default="crop",
                   help="crop = plain 9:16 center crop; facecam = facecam on top, "
                        "gameplay below (default crop)")
    p.add_argument("--cam", choices=["auto", "tl", "tr", "bl", "br"],
                   default="auto",
                   help="facecam position: auto-detect or top/bottom left/right "
                        "(default auto)")
    p.add_argument("--height", type=int, default=1920,
                   help="output height in pixels, 0 to keep the source size (default 1920)")
    p.add_argument("--quality", default="best",
                   help="streamlink quality, e.g. best, 1080p60, 720p (default best)")
    p.add_argument("--model", default="base",
                   help="whisper model: tiny/base/small/medium/large (default base)")
    p.add_argument("--language", default="",
                   help="force whisper language, e.g. en (default auto-detect)")
    p.add_argument("--crf", type=int, default=23,
                   help="ffmpeg CRF quality (lower = better, default 23)")
    p.add_argument("--preset", default="veryfast",
                   choices=["ultrafast","superfast","veryfast","faster",
                             "fast","medium","slow","slower","veryslow"],
                   help="ffmpeg encoding preset (default veryfast)")
    p.add_argument("--rec-keep-gb", type=float, default=2.0,
                   help="max GB to keep in rec/ before deleting oldest files (default 2)")
    p.add_argument("--no-whisper", action="store_true",
                   help="skip subtitle generation")
    p.add_argument("--rec-dir", default="rec", help="recording directory (default rec)")
    p.add_argument("--clips-dir", default="clips", help="clip directory (default clips)")
    p.add_argument("--log-dir", default="logs", help="log directory (default logs)")
    p.add_argument("--ffmpeg", dest="ffmpeg_bin", help="path to ffmpeg")
    p.add_argument("--ffprobe", dest="ffprobe_bin", help="path to ffprobe")
    p.add_argument("--streamlink", dest="streamlink_bin", help="path to streamlink")
    p.add_argument("--whisper", dest="whisper_bin", help="path to whisper")
    p.add_argument("--no-twitch-ads", action="store_true",
                   help="do not pass --twitch-disable-ads to streamlink")
    p.add_argument("--irc-host", default=IRC_HOST,
                   help=f"twitch chat IRC host (default {IRC_HOST})")
    p.add_argument("--irc-port", type=int, default=IRC_PORT,
                   help=f"twitch chat IRC port (default {IRC_PORT})")
    p.add_argument("--verbose", action="store_true", help="log every chat bucket")
    p.add_argument("--stop-file", default=None,
                   help="exit gracefully when this file appears (used by the GUI)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args = prepare(args)
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(130)
