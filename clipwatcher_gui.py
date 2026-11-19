#!/usr/bin/env python3
"""Simple desktop front-end for clip-watcher."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ROOT = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(ROOT, "clip_watcher.py")
STOP_FILE = os.path.join(ROOT, ".stop-request")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
QUALITIES = ["best", "1080p60", "720p60", "480p30", "360p30", "worst"]
MODELS = ["tiny", "base", "small", "medium", "large"]


def _find_python():
    """Return the best python executable: venv python.exe > sys.executable."""
    venv_py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")  # Windows
    if not os.path.exists(venv_py):
        venv_py = os.path.join(ROOT, ".venv", "bin", "python")      # Linux/macOS
    return venv_py if os.path.exists(venv_py) else sys.executable


PYTHON = _find_python()


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.reader = None
        self.closing = False
        self.messages = queue.Queue()
        self.clip_paths = []
        self.clip_sig = None

        root.title("clip-watcher")
        root.geometry("820x700")
        root.minsize(680, 540)
        for row, weight in ((5, 1), (6, 2)):
            root.rowconfigure(row, weight=weight)
        root.columnconfigure(0, weight=1)

        self.channel_var = tk.StringVar()
        self.min_var = tk.StringVar(value="5")
        self.mult_var = tk.StringVar(value="2.0")
        self.quality_var = tk.StringVar(value="best")
        self.model_var = tk.StringVar(value="small")
        self.subs_var = tk.BooleanVar(value=True)
        self.layout_var = tk.StringVar(value="Facecam + gameplay")
        self.cam_var = tk.StringVar(value="auto")
        self.clips_var = tk.StringVar(value=os.path.join(ROOT, "clips"))
        self.status_var = tk.StringVar(value="Idle - enter a Twitch channel or YouTube URL")

        self._build()
        self._refresh_clips()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._pump)

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self.root)
        top.grid(row=0, column=0, sticky="ew", **pad)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Channel / URL").grid(row=0, column=0, sticky="w")
        self.channel_entry = ttk.Entry(top, textvariable=self.channel_var)
        self.channel_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.channel_entry.bind("<Return>", lambda _e: self.start())
        ttk.Label(top, text="Twitch name, @YouTubeHandle, or stream URL",
                  foreground="#666").grid(row=0, column=2, sticky="w")

        opts = ttk.LabelFrame(self.root, text="Options")
        opts.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Label(opts, text="Min messages / 10s").grid(row=0, column=0, sticky="w",
                                                       padx=(8, 4), pady=8)
        ttk.Spinbox(opts, from_=1, to=999, width=6,
                    textvariable=self.min_var).grid(row=0, column=1, sticky="w")
        ttk.Label(opts, text="Spike mult").grid(row=0, column=2, sticky="w", padx=(12, 4))
        ttk.Spinbox(opts, from_=1.0, to=10.0, increment=0.5, width=5,
                    textvariable=self.mult_var).grid(row=0, column=3, sticky="w")
        ttk.Label(opts, text="Quality").grid(row=0, column=4, sticky="w", padx=(12, 4))
        ttk.Combobox(opts, values=QUALITIES, width=10, state="readonly",
                     textvariable=self.quality_var).grid(row=0, column=5, sticky="w")
        ttk.Label(opts, text="Whisper model").grid(row=0, column=6, sticky="w",
                                                   padx=(12, 4))
        ttk.Combobox(opts, values=MODELS, width=10, state="readonly",
                     textvariable=self.model_var).grid(row=0, column=7, sticky="w")
        ttk.Checkbutton(opts, text="Subtitles",
                        variable=self.subs_var).grid(row=0, column=8, padx=(12, 8))
        ttk.Label(opts, text="Clip layout").grid(row=1, column=0, sticky="w",
                                                 padx=(8, 4), pady=(0, 8))
        ttk.Combobox(opts, values=["Facecam + gameplay", "9:16 center crop"],
                     width=22, state="readonly",
                     textvariable=self.layout_var).grid(row=1, column=1, columnspan=2,
                                                        sticky="w", pady=(0, 8))
        ttk.Label(opts, text="Camera").grid(row=1, column=3, sticky="w",
                                            padx=(20, 4), pady=(0, 8))
        ttk.Combobox(opts, values=["auto", "top-left", "top-right",
                                   "bottom-left", "bottom-right"],
                     width=14, state="readonly",
                     textvariable=self.cam_var).grid(row=1, column=4, columnspan=2,
                                                     sticky="w", pady=(0, 8))
        opts.columnconfigure(9, weight=1)

        folder = ttk.Frame(self.root)
        folder.grid(row=2, column=0, sticky="ew", **pad)
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text="Clips folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(folder, textvariable=self.clips_var).grid(row=0, column=1,
                                                            sticky="ew", padx=8)
        ttk.Button(folder, text="Browse...", command=self._browse).grid(row=0, column=2)
        ttk.Button(folder, text="Open", command=self._open_folder).grid(row=0, column=3,
                                                                        padx=(6, 0))

        buttons = ttk.Frame(self.root)
        buttons.grid(row=3, column=0, sticky="ew", **pad)
        self.start_btn = ttk.Button(buttons, text="Start watching", command=self.start)
        self.start_btn.grid(row=0, column=0, ipadx=12)
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=8, ipadx=12)
        ttk.Label(buttons, textvariable=self.status_var, wraplength=420).grid(
            row=0, column=2, padx=12, sticky="w")

        ttk.Label(self.root, foreground="#666",
                  text="Clips + .srt files are saved to the folder above. "
                       "Double-click a clip to play it. "
                       "Facecam layout finds the streamer's face automatically; "
                       "if it gets the wrong area, pick a camera corner.").grid(
            row=4, column=0, sticky="w", padx=10)

        clips_box = ttk.LabelFrame(self.root, text="Clips")
        clips_box.grid(row=5, column=0, sticky="nsew", **pad)
        self.clips_box = clips_box
        clips_box.rowconfigure(0, weight=1)
        clips_box.columnconfigure(0, weight=1)
        self.clip_list = tk.Listbox(clips_box, height=6, activestyle="none")
        self.clip_list.grid(row=0, column=0, sticky="nsew")
        self.clip_list.bind("<Double-Button-1>", lambda _e: self._open_clip())
        scroll = ttk.Scrollbar(clips_box, orient="vertical",
                               command=self.clip_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.clip_list.configure(yscrollcommand=scroll.set)

        log_box = ttk.LabelFrame(self.root, text="Log")
        log_box.grid(row=6, column=0, sticky="nsew", **pad)
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(log_box, height=10, state="disabled",
                                     wrap="word", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def _log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clips_dir(self):
        return os.path.abspath(os.path.expanduser(self.clips_var.get().strip()
                                                  or os.path.join(ROOT, "clips")))

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
            for p in paths:
                self.clip_list.insert("end", os.path.basename(p))
            self.clips_box.configure(text=f"Clips ({len(paths)})")
        self.root.after(2000, self._refresh_clips)

    def _pump(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
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
            rc = proc.wait()
            self.messages.put(("exit", rc))

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        channel = self.channel_var.get().strip()
        if not channel:
            messagebox.showwarning("clip-watcher", "Enter a Twitch channel or YouTube URL first.")
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
            messagebox.showerror("clip-watcher", f"Cannot create clips folder:\n{exc}")
            return
        self.clips_var.set(clips)
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass

        cmd = [PYTHON, "-u", CORE, channel,
               "--min", str(min_msgs),
               "--mult", self.mult_var.get(),
               "--quality", self.quality_var.get(),
               "--model", self.model_var.get(),
               "--clips-dir", clips,
               "--stop-file", STOP_FILE]
        cams = {"auto": "auto", "top-left": "tl", "top-right": "tr",
                "bottom-left": "bl", "bottom-right": "br"}
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

        self.status_var.set(f"Watching {channel} - clips are in {clips}")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.reader = threading.Thread(target=self._read, args=(self.proc,),
                                       daemon=True)
        self.reader.start()

    def _request_stop(self, proc):
        if not proc or proc.poll() is not None:
            return
        try:
            with open(STOP_FILE, "w", encoding="utf-8") as fh:
                fh.write("stop")
        except OSError as exc:
            self._log(f"[gui] could not write stop file: {exc}")
        self._log("[gui] stop requested - finishing current work ...")
        self.status_var.set("Stopping...")
        self.stop_btn.configure(state="disabled")
        self.root.after(15000, lambda: self._force_kill(proc))

    def stop(self):
        self._request_stop(self.proc)

    def _force_kill(self, proc):
        if not proc or proc.poll() is not None:
            return
        self._log("[gui] watcher did not stop in time - killing process")
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
        self.status_var.set("Idle - enter a Twitch channel or YouTube URL")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

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
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
