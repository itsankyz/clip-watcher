#!/usr/bin/env python3
"""Desktop front-end for clip-watcher."""

import hashlib
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

ROOT = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(ROOT, "clip_watcher.py")
STOP_FILE = os.path.join(ROOT, ".stop-request")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

QUALITIES = ["best", "1080p60", "720p60", "480p30", "360p30", "worst"]
MODELS    = ["tiny", "base", "small", "medium", "large"]

# ── palette ────────────────────────────────────────────────────────────────
BG        = "#f7f7f7"
SURFACE   = "#ffffff"
BORDER    = "#e2e2e2"
ACCENT    = "#f97316"
ACCENT_HO = "#ea6c0a"
TXT       = "#1a1a1a"
TXT_MUTED = "#6b7280"
LOG_BG    = "#fafafa"
FONT      = ("Segoe UI", 10)
FONT_SM   = ("Segoe UI", 9)
FONT_LG   = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 9)

LOG_COLORS = {
    "[clip]":    "#f97316",
    "[whisper]": "#8b5cf6",
    "[chat]":    "#0ea5e9",
    "[rec]":     "#10b981",
    "[watch]":   "#6b7280",
    "[gui]":     "#9ca3af",
    "error":     "#ef4444",
    "warning":   "#f59e0b",
}


def _find_python():
    venv_py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        venv_py = os.path.join(ROOT, ".venv", "bin", "python")
    return venv_py if os.path.exists(venv_py) else sys.executable


PYTHON = _find_python()


# ── reusable widgets ────────────────────────────────────────────────────────

def _card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=SURFACE, border_color=BORDER,
                        border_width=1, corner_radius=8, **kw)


def _label(parent, text, font=FONT, color=TXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color,
                        fg_color="transparent", **kw)


def _entry(parent, textvariable, width=None):
    e = ctk.CTkEntry(parent, textvariable=textvariable, font=FONT,
                     text_color=TXT, fg_color=SURFACE, border_color=BORDER,
                     border_width=1, corner_radius=6)
    if width:
        e.configure(width=width * 10)
    return e


def _btn(parent, text, command, primary=False, width=None):
    bg = ACCENT if primary else SURFACE
    fg = "#ffffff" if primary else TXT
    b = ctk.CTkButton(
        parent, text=text, command=command, font=FONT,
        text_color=fg, fg_color=bg,
        hover_color=ACCENT_HO if primary else BORDER,
        corner_radius=6, cursor="hand2",
    )
    if width:
        b.configure(width=width * 10)
    return b


def _dropdown(parent, variable, values, width=12):
    return ctk.CTkOptionMenu(
        parent, variable=variable, values=values, width=width * 10,
        font=FONT, text_color=TXT, fg_color=SURFACE,
        button_color=ACCENT, button_hover_color=ACCENT_HO,
        dropdown_fg_color=SURFACE, dropdown_text_color=TXT,
        dropdown_hover_color=BORDER, corner_radius=6,
    )


def _spinbox(parent, variable, from_, to, increment=1, width=6):
    return ctk.CTkEntry(parent, textvariable=variable, font=FONT,
                        text_color=TXT, fg_color=SURFACE,
                        border_color=BORDER, border_width=1,
                        corner_radius=6, width=width * 10,
                        justify="center")


def _sep(parent):
    return ctk.CTkFrame(parent, fg_color=BORDER, height=1, corner_radius=0)


# ── main app ────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.closing = False
        self.messages = queue.Queue()
        self.clip_paths = []
        self.clip_sig = None
        self.preview_image = None

        root.title("clip-watcher")
        root.geometry("860x720")
        root.minsize(700, 560)
        root.configure(fg_color=BG)
        root.rowconfigure(3, weight=1)
        root.columnconfigure(0, weight=1)

        self.channel_var = tk.StringVar()
        self.min_var     = tk.StringVar(value="5")
        self.mult_var    = tk.StringVar(value="2.0")
        self.quality_var = tk.StringVar(value="best")
        self.model_var   = tk.StringVar(value="small")
        self.subs_var    = tk.BooleanVar(value=True)
        self.layout_var  = tk.StringVar(value="Facecam + gameplay")
        self.cam_var     = tk.StringVar(value="auto")
        self.clips_var   = tk.StringVar(value=os.path.join(ROOT, "clips"))
        self.status_var  = tk.StringVar(value="Idle")
        self.dot_color   = tk.StringVar(value="#d1d5db")

        self._build()
        self._refresh_clips()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._pump)

    # ── layout ──────────────────────────────────────────────────────────────

    def _build(self):
        P = dict(padx=12, pady=6)

        # ── header ──
        hdr = ctk.CTkFrame(self.root, fg_color=SURFACE,
                           border_color=BORDER, border_width=1, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(1, weight=1)

        _label(hdr, "clip-watcher", font=FONT_LG).grid(
            row=0, column=0, padx=(16, 8), pady=12, sticky="w")

        self.dot = tk.Canvas(hdr, width=10, height=10, bg=SURFACE,
                             highlightthickness=0)
        self.dot.grid(row=0, column=1, sticky="w", padx=(0, 4))
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill="#d1d5db", outline="")

        self.status_lbl = _label(hdr, "Idle — enter a channel or URL",
                                 color=TXT_MUTED, font=FONT_SM)
        self.status_lbl.grid(row=0, column=2, sticky="w")

        # ── channel input ──
        ch_card = _card(self.root)
        ch_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 4))
        ch_card.columnconfigure(1, weight=1)

        _label(ch_card, "Channel / URL", color=TXT_MUTED,
               font=FONT_SM).grid(row=0, column=0, columnspan=3,
                                  sticky="w", padx=12, pady=(10, 2))
        self.channel_entry = _entry(ch_card, self.channel_var)
        self.channel_entry.grid(row=1, column=0, columnspan=2,
                                sticky="ew", padx=12, pady=(0, 10), ipady=5)
        self.channel_entry.bind("<Return>", lambda _e: self.start())
        _label(ch_card, "Twitch name · @YouTubeHandle · stream URL",
               color=TXT_MUTED, font=FONT_SM).grid(
            row=1, column=2, sticky="w", padx=(0, 12))

        # ── options ──
        opt_card = _card(self.root)
        opt_card.grid(row=2, column=0, sticky="ew", padx=12, pady=4)

        def _opt_col(col, label, widget_fn):
            f = ctk.CTkFrame(opt_card, fg_color="transparent")
            f.grid(row=0, column=col, sticky="w", padx=(12, 16), pady=10)
            _label(f, label, color=TXT_MUTED, font=FONT_SM).pack(anchor="w")
            widget_fn(f).pack(anchor="w", pady=(3, 0))

        _opt_col(0, "Min msgs / 10 s",
                 lambda p: _spinbox(p, self.min_var, 1, 999, width=7))
        _opt_col(1, "Spike multiplier",
                 lambda p: _spinbox(p, self.mult_var, 1.0, 10.0,
                                    increment=0.5, width=7))
        _opt_col(2, "Quality",
                 lambda p: _dropdown(p, self.quality_var, QUALITIES, width=10))
        _opt_col(3, "Whisper model",
                 lambda p: _dropdown(p, self.model_var, MODELS, width=10))

        # subtitles toggle
        sub_f = ctk.CTkFrame(opt_card, fg_color="transparent")
        sub_f.grid(row=0, column=4, sticky="w", padx=(12, 16), pady=10)
        _label(sub_f, "Subtitles", color=TXT_MUTED, font=FONT_SM).pack(anchor="w")
        ctk.CTkCheckBox(sub_f, text="", variable=self.subs_var,
                        fg_color=ACCENT, hover_color=ACCENT_HO,
                        border_color=BORDER, checkbox_width=20,
                        checkbox_height=20).pack(anchor="w", pady=(3, 0))

        _sep(opt_card).grid(row=1, column=0, columnspan=6, sticky="ew",
                            padx=12, pady=0)

        _opt_col(0, "Clip layout",
                 lambda p: _dropdown(p, self.layout_var,
                                     ["Facecam + gameplay", "9:16 center crop"],
                                     width=18))
        _opt_col(1, "Camera corner",
                 lambda p: _dropdown(p, self.cam_var,
                                     ["auto", "top-left", "top-right",
                                      "bottom-left", "bottom-right"], width=12))

        # clips folder
        folder_f = ctk.CTkFrame(opt_card, fg_color="transparent")
        folder_f.grid(row=2, column=0, columnspan=6, sticky="ew",
                      padx=12, pady=(4, 10))
        folder_f.columnconfigure(1, weight=1)
        _label(folder_f, "Clips folder", color=TXT_MUTED,
               font=FONT_SM).grid(row=0, column=0, sticky="w", pady=(0, 2))
        _entry(folder_f, self.clips_var).grid(
            row=1, column=0, sticky="ew", ipady=4, padx=(0, 8))
        _btn(folder_f, "Browse", self._browse).grid(row=1, column=1, padx=(0, 6))
        _btn(folder_f, "Open folder", self._open_folder).grid(row=1, column=2)

        # ── action bar ──
        act = ctk.CTkFrame(self.root, fg_color="transparent")
        act.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 8))

        self.start_btn = _btn(act, "▶  Start watching", self.start,
                              primary=True, width=18)
        self.start_btn.pack(side="left")

        self.stop_btn = _btn(act, "■  Stop", self.stop, width=10)
        self.stop_btn.configure(state="disabled", text_color=TXT_MUTED)
        self.stop_btn.pack(side="left", padx=(8, 0))

        # ── bottom pane: clips + log ──
        pane = ctk.CTkFrame(self.root, fg_color="transparent")
        pane.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
        pane.rowconfigure(0, weight=1)
        pane.columnconfigure(0, weight=1)
        pane.columnconfigure(1, weight=2)
        self.root.rowconfigure(4, weight=1)

        # clips panel
        clips_card = _card(pane)
        clips_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        clips_card.rowconfigure(2, weight=1)
        clips_card.columnconfigure(0, weight=1)

        clips_hdr = ctk.CTkFrame(clips_card, fg_color="transparent")
        clips_hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                       padx=12, pady=(10, 4))
        self.clips_title = _label(clips_hdr, "Clips", font=("Segoe UI", 10, "bold"))
        self.clips_title.pack(side="left")
        _label(clips_hdr, "double-click to play",
               color=TXT_MUTED, font=FONT_SM).pack(side="left", padx=8)

        self.preview_label = tk.Label(
            clips_card, text="Select a clip to preview",
            font=FONT_SM, fg=TXT_MUTED, bg=LOG_BG,
            width=36, height=8, compound="center",
        )
        self.preview_label.grid(row=1, column=0, columnspan=2, sticky="ew",
                               padx=12, pady=(0, 8))

        self.clip_list = tk.Listbox(
            clips_card, font=FONT_SM, fg=TXT, bg=SURFACE,
            selectbackground=ACCENT, selectforeground="#fff",
            relief="flat", highlightthickness=0,
            activestyle="none", borderwidth=0)
        self.clip_list.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=(0, 12))
        self.clip_list.bind("<<ListboxSelect>>", lambda _e: self._preview_selected())
        self.clip_list.bind("<Double-Button-1>", lambda _e: self._open_clip())

        sb = tk.Scrollbar(clips_card, orient="vertical",
                          command=self.clip_list.yview,
                          bg=BG, troughcolor=BG, relief="flat")
        sb.grid(row=2, column=1, sticky="ns", pady=(0, 12), padx=(0, 4))
        self.clip_list.configure(yscrollcommand=sb.set)

        # log panel
        log_card = _card(pane)
        log_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)

        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        _label(log_hdr, "Log", font=("Segoe UI", 10, "bold")).pack(side="left")

        self.log_text = tk.Text(
            log_card, font=FONT_MONO, fg=TXT, bg=LOG_BG,
            relief="flat", highlightthickness=0,
            state="disabled", wrap="word", borderwidth=0)
        self.log_text.grid(row=1, column=0, sticky="nsew",
                           padx=(12, 0), pady=(0, 12))

        log_sb = tk.Scrollbar(log_card, orient="vertical",
                              command=self.log_text.yview,
                              bg=BG, troughcolor=BG, relief="flat")
        log_sb.grid(row=1, column=1, sticky="ns", pady=(0, 12), padx=(0, 4))
        self.log_text.configure(yscrollcommand=log_sb.set)

        # log color tags
        for tag, color in LOG_COLORS.items():
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.tag_configure("muted", foreground=TXT_MUTED)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _set_status(self, text, active=False):
        self.status_lbl.configure(text=text)
        color = ACCENT if active else "#d1d5db"
        self.dot.itemconfig(self._dot_id, fill=color)

    def _log(self, line):
        self.log_text.configure(state="normal")
        tag = "muted"
        low = line.lower()
        for key, _ in LOG_COLORS.items():
            if key in low:
                tag = key
                break
        self.log_text.insert("end", line + "\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clips_dir(self):
        return os.path.abspath(os.path.expanduser(
            self.clips_var.get().strip() or os.path.join(ROOT, "clips")))

    def _browse(self):
        chosen = filedialog.askdirectory(initialdir=self._clips_dir(),
                                         title="Where should clips go?")
        if chosen:
            self.clips_var.set(os.path.normpath(chosen))

    def _open_folder(self):
        folder = self._clips_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("clip-watcher", f"Cannot create folder:\n{exc}")
            return
        self._open_path(folder)

    def _open_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("clip-watcher", str(exc))

    def _open_clip(self):
        sel = self.clip_list.curselection()
        if sel and sel[0] < len(self.clip_paths):
            self._open_path(self.clip_paths[sel[0]])

    def _preview_selected(self):
        selection = self.clip_list.curselection()
        if not selection or selection[0] >= len(self.clip_paths):
            return
        path = self.clip_paths[selection[0]]
        self.preview_label.config(text="Loading preview...", image="")
        self.preview_image = None
        threading.Thread(target=self._make_preview, args=(path,),
                         daemon=True).start()

    def _make_preview(self, path):
        try:
            stat = os.stat(path)
            identity = (os.path.normcase(os.path.abspath(path)) +
                        f":{stat.st_size}:{stat.st_mtime_ns}")
            cache_name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".png"
            cache_dir = os.path.join(tempfile.gettempdir(), "clip-watcher-previews")
            os.makedirs(cache_dir, exist_ok=True)
            preview_path = os.path.join(cache_dir, cache_name)
            if not os.path.isfile(preview_path):
                last_error = None
                temp_preview = f"{preview_path}.{threading.get_ident()}.tmp.png"
                for seek in ("1", "0"):
                    try:
                        subprocess.run(
                            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                             "-ss", seek, "-i", path, "-frames:v", "1",
                             "-vf", "scale=320:180:force_original_aspect_ratio=decrease,"
                                    "pad=320:180:(ow-iw)/2:(oh-ih)/2",
                             "-f", "image2", temp_preview],
                            check=True, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=30,
                            creationflags=CREATE_NO_WINDOW,
                        )
                        if (not os.path.isfile(temp_preview) or
                                os.path.getsize(temp_preview) == 0):
                            last_error = "ffmpeg produced an empty preview"
                            if os.path.isfile(temp_preview):
                                os.remove(temp_preview)
                            continue
                        os.replace(temp_preview, preview_path)
                        break
                    except subprocess.CalledProcessError as exc:
                        last_error = (
                            exc.stderr.decode("utf-8", errors="replace").strip()
                            if exc.stderr else str(exc)
                        )
                        if os.path.isfile(temp_preview):
                            os.remove(temp_preview)
                if (not os.path.isfile(preview_path) or
                        os.path.getsize(preview_path) == 0):
                    raise RuntimeError(last_error or "ffmpeg could not create a preview")
            self.messages.put(("preview", (path, preview_path, None)))
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self.messages.put(("preview", (path, None, str(exc))))

    def _show_preview(self, path, preview_path, error):
        selection = self.clip_list.curselection()
        if not selection or selection[0] >= len(self.clip_paths):
            return
        if self.clip_paths[selection[0]] != path:
            return
        if error:
            self.preview_label.config(
                image="", text=f"Preview unavailable\n{error[:140]}")
            self.preview_image = None
            return
        try:
            image = tk.PhotoImage(file=preview_path)
        except tk.TclError as exc:
            self.preview_label.config(text=f"Preview unavailable\n{exc}")
            self.preview_image = None
            return
        self.preview_image = image
        self.preview_label.config(image=image, text="")

    def _refresh_clips(self):
        folder = self._clips_dir()
        try:
            paths = [os.path.join(folder, n) for n in os.listdir(folder)
                     if n.lower().endswith(".mp4")]
        except OSError:
            paths = []
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        sig = tuple((os.path.basename(p), os.path.getsize(p),
                     int(os.path.getmtime(p))) for p in paths)
        if sig != self.clip_sig:
            self.clip_sig = sig
            self.clip_paths = paths
            self.clip_list.delete(0, "end")
            self.preview_label.config(text="Select a clip to preview", image="")
            self.preview_image = None
            for i, p in enumerate(paths):
                self.clip_list.insert("end", f"  {os.path.basename(p)}")
                self.clip_list.itemconfig(i, bg=SURFACE if i % 2 == 0 else "#f9f9f9")
            self.clips_title.configure(
                text=f"Clips ({len(paths)})" if paths else "Clips")
        self.root.after(2000, self._refresh_clips)

    def _pump(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "preview":
                self._show_preview(*payload)
            else:
                self._exited(payload)
        self.root.after(100, self._pump)

    def _read(self, proc):
        try:
            for line in proc.stdout:
                self.messages.put(("log", line.rstrip()))
        except Exception as exc:
            self.messages.put(("log", f"[gui] read error: {exc}"))
        finally:
            self.messages.put(("exit", proc.wait()))

    # ── watcher control ─────────────────────────────────────────────────────

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        channel = self.channel_var.get().strip()
        if not channel:
            messagebox.showwarning("clip-watcher",
                                   "Enter a Twitch channel or YouTube URL first.")
            return
        try:
            min_msgs = max(1, int(self.min_var.get()))
        except ValueError:
            messagebox.showwarning("clip-watcher", "Min messages must be a number.")
            return
        clips = self._clips_dir()
        try:
            os.makedirs(clips, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("clip-watcher",
                                 f"Cannot create clips folder:\n{exc}")
            return
        self.clips_var.set(clips)
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass

        cams = {"auto": "auto", "top-left": "tl", "top-right": "tr",
                "bottom-left": "bl", "bottom-right": "br"}
        cmd = [PYTHON, "-u", CORE, channel,
               "--min", str(min_msgs),
               "--mult", self.mult_var.get(),
               "--quality", self.quality_var.get(),
               "--model", self.model_var.get(),
               "--clips-dir", clips,
               "--stop-file", STOP_FILE]
        if self.layout_var.get().startswith("Facecam"):
            cmd += ["--layout", "facecam", "--cam",
                    cams.get(self.cam_var.get(), "auto")]
        else:
            cmd += ["--layout", "crop"]
        if not self.subs_var.get():
            cmd.append("--no-whisper")

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log(f"[gui] starting watcher for {channel} ...")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
                creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            messagebox.showerror("clip-watcher", f"Cannot start watcher:\n{exc}")
            return

        self._set_status(f"Watching {channel}", active=True)
        self.start_btn.configure(state="disabled", fg_color="#e5e7eb",
                                 text_color=TXT_MUTED)
        self.stop_btn.configure(state="normal", text_color=TXT)
        threading.Thread(target=self._read, args=(self.proc,),
                         daemon=True).start()

    def _request_stop(self, proc):
        if not proc or proc.poll() is not None:
            return
        try:
            with open(STOP_FILE, "w", encoding="utf-8") as fh:
                fh.write("stop")
        except OSError as exc:
            self._log(f"[gui] could not write stop file: {exc}")
        self._log("[gui] stop requested — finishing current clip ...")
        self._set_status("Stopping...", active=False)
        self.stop_btn.configure(state="disabled")
        self.root.after(15000, lambda: self._force_kill(proc))

    def stop(self):
        self._request_stop(self.proc)

    def _force_kill(self, proc):
        if not proc or proc.poll() is not None:
            return
        self._log("[gui] watcher did not stop in time — killing process")
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                proc.kill()
        except Exception as exc:
            self._log(f"[gui] kill failed: {exc}")

    def _exited(self, rc):
        self._log(f"[gui] watcher exited (code {rc})")
        self.proc = None
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass
        if self.closing:
            self.root.destroy()
            return
        self._set_status("Idle — enter a channel or URL", active=False)
        self.start_btn.configure(state="normal", fg_color=ACCENT,
                                 text_color="#ffffff")
        self.stop_btn.configure(state="disabled", text_color=TXT_MUTED)

    def _on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("clip-watcher",
                                       "A recording is running. Stop it and exit?"):
                return
            self.closing = True
            self._request_stop(self.proc)
            self.root.after(16000, self._force_close)
        else:
            self.root.destroy()

    def _force_close(self):
        self._force_kill(self.proc)
        self.root.destroy()


def main():
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.configure(fg_color=BG)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
