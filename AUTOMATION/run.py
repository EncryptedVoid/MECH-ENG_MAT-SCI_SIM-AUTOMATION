#!/usr/bin/env python3
"""
LAVA — LAMMPS Automation Validation Aid (GUI + orchestration).
==============================================================

WHAT THIS FILE IS
-----------------
The tkinter wizard and the code that drives a run. This is the ONLY module that
touches the GUI. It owns:
* the App wizard — Step 0 (new session / view previous sessions), Step 1 (build
  LAMMPS), Step 2 (choose materials, seeds, and add/edit material profiles),
  Step 3 (configure limits, preview, and run a session);
* the whole-window + inner scrolling machinery, logging to the on-screen log,
  and worker threads (build, project build, session run) that must never touch
  tkinter directly;
* MaterialEditor, the add/edit window for a material profile set, including the
  "contribute" flow that commits a new [UNTESTED]- profile on its own branch and
  opens a pull request; and
* main().

WHERE THE OTHER LOGIC LIVES (so this file stays focused)
-------------------------------------------------------
* constants.py — every configuration value (colours, fonts, paths, defaults,
  the project tree, accepted extensions, markers). Imported wholesale below.
* helpers.py — all pure, GUI-free logic: parsers, seed parsing, filesystem and
  session discovery, CSV I/O, hardware probes, and MetricsSampler.
* report.py — offline HTML + PNG generation; the session worker calls
  report.generate_session_outputs() at the end of a run.

Import direction (no cycles):

    constants.py  --imported by-->  helpers.py, report.py, run.py
    helpers.py    --imported by-->  report.py, run.py
    report.py     --imported by-->  run.py

WORKING ON THIS FILE (for humans and LLMs)
------------------------------------------
* GUI and orchestration only. If you find yourself writing a pure parser, a
  filesystem scan, or HTML/PNG output here, it belongs in helpers.py or
  report.py — put it there and import it, so this file stays about the wizard.
* Threading rule: worker threads (build/project/session) run as daemons and
  must NEVER call tkinter directly. Talk back to the GUI only via self.log_line
  (queued) or self.root.after(0, ...). Direct widget access off the main thread
  crashes intermittently.
* The sudo password handed to the build worker is used to authenticate sudo via
  stdin and is never stored, logged, or written to disk. Keep it that way.
* Launch:  python3 run.py   (from inside AUTOMATION/, so the flat imports of
  constants / helpers / report resolve).

All output data is CSV except config.json.
"""

import os
import re
import sys
import csv
import json
import time
import queue
import shutil
import threading
import subprocess
import datetime as dt
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext

import report
import constants as _const
from constants import (
    VERSION, APP_NAME, APP_SUBTITLE, APP_CREDIT, FOOTER, LOGO_FILE,
    COL_BG, COL_PANEL, COL_TEXT, COL_MUTED, COL_LAVA, COL_LAVA_HOT,
    COL_EMBER, COL_OK, COL_WARN, COL_ERR, COL_ENTRY,
    FONT, FONT_BOLD, FONT_MONO,
    BUILD_SCRIPT, LAMMPS_BUILD_ROOT, LAMMPS_BIN, CONFIG_NAME,
    PROJECT_TREE, MATERIAL_SUBDIRS, OUTPUT_RENAME,
    UNTESTED_PREFIX, MATERIAL_INFO_NAME, EDITOR_ROWS,
    DEFAULT_PROBE_SEC, DEFAULT_MAX_CPU, DEFAULT_MAX_RAM, DEFAULT_MAX_GPU,
)
from helpers import (
    now_iso, script_dir, repo_root,
    is_untested_name, display_material_name, slugify, parse_seeds,
    have_nvidia_smi, have_psutil, looks_like_input, list_material_inputs,
    discover_materials, read_material_info, write_material_info,
    write_csv, append_csv, summarize_samples, now_utc_iso, detect_hardware,
    parse_ebath, parse_tprof, fmt_hms, MetricsSampler,
)


# ---------------------------------------------------------------------------
# The LAVA wizard application
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} - {APP_SUBTITLE}")
        root.geometry("900x760")
        root.minsize(820, 680)
        root.configure(bg=COL_BG)

        # Resolve the preferred UI font (Roboto Mono) against installed families
        # and apply it before any widgets are built; falls back to the default
        # sans-serif if it isn't installed. See _apply_font.
        self._apply_font()

        self.log_q = queue.Queue()
        self._logfile = None
        self._logo_img = None       # keep a reference so it isn't GC'd
        self._icon_img = None       # window/taskbar icon reference

        # wizard state
        self.pipeline = None
        self.project_root = tk.StringVar()
        self.materials_src = tk.StringVar()     # parent folder of material subfolders
        self.temp_dir = tk.StringVar()
        self.seed_spec = tk.StringVar(value="1")
        self.chk_check_old = tk.BooleanVar(value=True)
        self.chk_clean_old = tk.BooleanVar(value=False)
        self.sudo_pw = tk.StringVar()           # never logged, cleared after use

        self.config = None
        self.config_path = None

        # persisted user settings (currently: timestamp mode). Default to UTC
        # for privacy - local timestamps in filenames can reveal a user's
        # timezone in a shared report.
        self._settings = self._load_settings()
        self.use_utc = tk.BooleanVar(value=self._settings.get("use_utc", True))

        # session control
        self.session_thread = None
        self.pause_event = threading.Event()    # set() = paused
        self.stop_event = threading.Event()     # set() = stop the session
        self.skip_event = threading.Event()     # set() = skip current run
        self.duration_history = []              # completed run durations (for ETA)
        self.planned_runs = []                  # list of run-spec dicts for preview
        self.last_session_dir = None

        self._build_style()
        self._build_shell()
        self._drain_log()
        self.show_step0()

    # -- theming -----------------------------------------------------------
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        background=COL_PANEL, fieldbackground=COL_PANEL,
                        foreground=COL_TEXT, rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading",
                        background=COL_BG, foreground=COL_LAVA_HOT,
                        font=FONT_BOLD)
        style.map("Treeview", background=[("selected", COL_EMBER)])
        style.configure("Lava.Horizontal.TProgressbar",
                        troughcolor=COL_ENTRY, background=COL_LAVA)

    def _btn(self, parent, text, command, kind="normal", **kw):
        """A themed button. kind: normal|primary|danger|ghost."""
        colors = {
            "normal": (COL_PANEL, COL_TEXT),
            "primary": (COL_LAVA, "#1a1210"),
            "danger": (COL_EMBER, COL_TEXT),
            "ghost": (COL_BG, COL_MUTED),
        }
        bg, fg = colors.get(kind, colors["normal"])
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=COL_LAVA_HOT, activeforeground="#1a1210",
                      relief="flat", bd=0, padx=12, pady=6,
                      font=FONT_BOLD if kind == "primary" else FONT,
                      cursor="hand2", **kw)
        return b

    def _label(self, parent, text, fg=COL_TEXT, font=None, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg,
                        font=font or FONT, **kw)

    # -- settings + timestamps --------------------------------------------
    def _settings_path(self):
        return repo_root() / "PROJECT" / "lava-settings.json"

    def _load_settings(self):
        try:
            p = repo_root() / "PROJECT" / "lava-settings.json"
            if p.exists():
                return json.loads(p.read_text()) or {}
        except Exception:
            pass
        return {}

    def _save_settings(self):
        try:
            p = self._settings_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            self._settings["use_utc"] = bool(self.use_utc.get())
            p.write_text(json.dumps(self._settings, indent=2))
        except Exception as e:
            self.log_line(f"Could not save settings: {e}")

    def _now_dt(self):
        """Timezone-aware 'now' honoring the user's UTC/local preference."""
        if self.use_utc.get():
            return dt.datetime.now(dt.timezone.utc)
        return dt.datetime.now()

    def _stamp_suffix(self):
        """'Z' for UTC, 'L' for local - appended to every stamp so it's always
        clear which zone a filename/id was made in, even if the user switches."""
        return "Z" if self.use_utc.get() else "L"

    def _session_stamp(self):
        """YYYYmmddHHMMSS + Z/L suffix, honoring the timestamp preference."""
        return self._now_dt().strftime("%Y%m%d%H%M%S") + self._stamp_suffix()

    def _now_iso_pref(self):
        """ISO 'now' honoring the preference, with an explicit zone marker."""
        d = self._now_dt()
        return d.isoformat(timespec="seconds")

    def _apply_font(self):
        """Apply the preferred UI font (constants.PREFERRED_FONT, i.e. Roboto
        Mono) if available, else keep the sans-serif defaults.

        Self-enclosing: the TTFs are bundled in the repo at
        AUTOMATION/assets/fonts. tkinter can't load a .ttf by path, but on Linux
        we can make the OS font system see it by copying it into the user font
        dir and refreshing the fontconfig cache. So if the family isn't already
        installed we register the bundled copy, then apply it. All best-effort:
        if registration doesn't take (e.g. no fc-cache), we fall back cleanly.

        Implementation note: the FONT tuples reference Tk's *named* fonts
        ('TkDefaultFont', 'TkFixedFont'); reconfiguring those named fonts'
        family changes every widget that uses them (including ttk) with no other
        rebinding.
        """
        import tkinter.font as tkfont
        want = getattr(_const, "PREFERRED_FONT", "")
        if not want:
            return

        def families():
            try:
                return set(tkfont.families(self.root))
            except Exception:
                return set()

        if want not in families():
            # try to register the bundled TTFs, then re-check
            if self._register_bundled_font():
                # fontconfig was refreshed, but this already-running Tk may have
                # cached the old family list; families() usually still picks it
                # up. If not, we fall through to the graceful message.
                pass

        if want not in families():
            self.root.after(
                0, lambda: self.log_line(
                    f"Font '{want}' not available yet; using the default "
                    f"sans-serif. It should apply next launch (the bundled "
                    f"font was registered)."))
            return

        for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont",
                     "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(name).configure(family=want)
            except Exception:
                pass

        # Widgets pass explicit font tuples like ("TkDefaultFont", 10); Tk reads
        # the first element as a FAMILY name, so reconfiguring the named default
        # font is not enough - we must rebuild the FONT tuples with the real
        # resolved family and rebind them in this module's globals (and in
        # constants) so every `font=FONT` call picks up the change.
        size = getattr(_const, "FONT_SIZE", 10)
        size_mono = getattr(_const, "FONT_SIZE_MONO", 9)
        new_font = (want, size)
        new_bold = (want, size, "bold")
        new_mono = (want, size_mono)
        globals()["FONT"] = new_font
        globals()["FONT_BOLD"] = new_bold
        globals()["FONT_MONO"] = new_mono
        _const.FONT = new_font
        _const.FONT_BOLD = new_bold
        _const.FONT_MONO = new_mono

        self.root.after(0, lambda: self.log_line(f"UI font: {want}"))

    def _register_bundled_font(self):
        """Copy the bundled Roboto Mono TTFs into the user font dir and refresh
        the fontconfig cache so the OS (and thus tkinter) can see them. Linux/
        WSL only; returns True if it attempted registration. Best-effort."""
        src_dir = script_dir() / "assets" / "fonts"
        if not src_dir.is_dir():
            return False
        ttfs = sorted(src_dir.glob("*.ttf"))
        if not ttfs:
            return False
        try:
            dest = Path.home() / ".local" / "share" / "fonts" / "LAVA"
            dest.mkdir(parents=True, exist_ok=True)
            copied = False
            for f in ttfs:
                target = dest / f.name
                if not target.exists():
                    shutil.copy2(f, target)
                    copied = True
            # refresh fontconfig if available (harmless if it isn't)
            if shutil.which("fc-cache"):
                subprocess.run(["fc-cache", "-f", str(dest)],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
            if copied:
                self.root.after(
                    0, lambda: self.log_line(
                        "Registered bundled Roboto Mono into the user font "
                        "directory."))
            return True
        except Exception as e:
            msg = f"Could not register bundled font: {e}"
            self.root.after(0, lambda: self.log_line(msg))
            return False

    # -- shell (logo header, body container, footer, log) ------------------
    def _build_shell(self):
        # window/taskbar icon
        self._set_window_icon()
        # header with logo + title
        header = tk.Frame(self.root, bg=COL_BG)
        header.pack(fill="x", side="top")
        self._load_logo(header)
        titlebox = tk.Frame(header, bg=COL_BG)
        titlebox.pack(side="left", padx=(6, 0), pady=8)
        tk.Label(titlebox, text=APP_NAME, bg=COL_BG, fg=COL_LAVA_HOT,
                 font=("TkDefaultFont", 26, "bold")).pack(anchor="w")
        tk.Label(titlebox, text=APP_SUBTITLE, bg=COL_BG, fg=COL_MUTED,
                 font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        # breadcrumb bar - populated per step via _set_breadcrumb; lets the user
        # jump back to Home or an earlier step.
        self.crumb_bar = tk.Frame(self.root, bg=COL_PANEL)
        self.crumb_bar.pack(fill="x", side="top")

        # body: a scrollable viewport. self.container is the inner frame that
        # each step attaches to (unchanged from a step's point of view); the
        # canvas + scrollbar give the whole window its own scroll when a step's
        # content overflows. Inner boxes (material list, preview) keep their own
        # scroll regions - the wheel dispatcher routes to whichever region the
        # pointer is over.
        body = tk.Frame(self.root, bg=COL_BG)
        body.pack(fill="both", expand=True)
        self._body_canvas = tk.Canvas(body, bg=COL_BG, highlightthickness=0)
        self._body_sb = ttk.Scrollbar(body, orient="vertical",
                                       command=self._body_canvas.yview)
        self.container = tk.Frame(self._body_canvas, bg=COL_BG)
        self._body_win = self._body_canvas.create_window(
            (0, 0), window=self.container, anchor="nw")
        self._body_canvas.configure(yscrollcommand=self._on_body_scroll)
        self._body_canvas.pack(side="left", fill="both", expand=True)
        # scrollbar is packed/unpacked on demand by _on_body_scroll
        self.container.bind(
            "<Configure>",
            lambda e: self._body_canvas.configure(
                scrollregion=self._body_canvas.bbox("all")))
        self._body_canvas.bind(
            "<Configure>",
            lambda e: self._body_canvas.itemconfigure(self._body_win,
                                                      width=e.width))
        # one wheel dispatcher for the whole app (routes by pointer location)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._on_mousewheel, add="+")

        # log area
        logframe = tk.LabelFrame(self.root, text="Log", bg=COL_BG, fg=COL_MUTED,
                                 font=FONT)
        logframe.pack(fill="both", expand=False, padx=8, pady=(0, 2))
        self.log = scrolledtext.ScrolledText(logframe, height=8, state="disabled",
                                             wrap="word", font=FONT_MONO,
                                             bg="#140d0b", fg=COL_TEXT,
                                             insertbackground=COL_TEXT)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

        # footer
        footer = tk.Frame(self.root, bg=COL_BG)
        footer.pack(fill="x", side="bottom")
        tk.Label(footer, text=FOOTER, bg=COL_BG, fg=COL_MUTED,
                 font=("TkDefaultFont", 9)).pack(side="right", padx=10, pady=4)

    # -- scrolling ---------------------------------------------------------
    def _on_body_scroll(self, first, last):
        """yscrollcommand for the window viewport: show the scrollbar only when
        the content actually overflows, hide it when everything fits."""
        self._body_sb.set(first, last)
        try:
            fits = float(first) <= 0.0 and float(last) >= 1.0
        except ValueError:
            fits = True
        if fits:
            self._body_sb.pack_forget()
        elif not self._body_sb.winfo_ismapped():
            self._body_sb.pack(side="right", fill="y")

    def _register_scrollable(self, canvas):
        """Inner scroll regions register here so the wheel dispatcher can route
        to them when the pointer is over them."""
        if not hasattr(self, "_inner_scrollables"):
            self._inner_scrollables = []
        self._inner_scrollables.append(canvas)

    def _on_mousewheel(self, event):
        """Single wheel handler for the app. Scrolls the inner scroll region the
        pointer is over if it actually overflows; otherwise scrolls the window
        viewport, but only when the page itself overflows. Short pages that fit
        entirely do not scroll at all."""
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if getattr(event, "delta", 0) > 0 else 1

        def overflowing(canvas):
            # yview() == (0.0, 1.0) means everything is visible -> nothing to
            # scroll. Treat tiny float error tolerantly.
            try:
                first, last = canvas.yview()
                return not (first <= 0.0001 and last >= 0.9999)
            except Exception:
                return False

        # prune dead widgets, then prefer an inner region under the pointer
        live = []
        for c in getattr(self, "_inner_scrollables", []):
            try:
                if c.winfo_exists():
                    live.append(c)
            except Exception:
                pass
        self._inner_scrollables = live
        for c in live:
            if self._pointer_in(c):
                if overflowing(c):
                    c.yview_scroll(delta, "units")
                return   # pointer is over this region; don't fall through
        # otherwise scroll the window viewport only if the page overflows
        if overflowing(self._body_canvas):
            self._body_canvas.yview_scroll(delta, "units")

    def _load_logo(self, parent):
        """Load a logo next to this script. Tries LOGO.webp/.png/.jpg/.jpeg.
        PNG/GIF load natively in tk; JPEG/WEBP use Pillow if available.
        Falls back to a lava glyph if nothing loads."""
        base = script_dir() / "assets" / "logos"
        candidates = [LOGO_FILE, "LOGO.png", "LOGO.jpg", "LOGO.jpeg", "LOGO.gif"]
        for name in candidates:
            p = base / name
            if not p.exists():
                continue
            ext = p.suffix.lower()
            # try native tk first for formats it supports
            if ext in (".png", ".gif"):
                try:
                    img = tk.PhotoImage(file=str(p))
                    # downscale if very large (subsample is integer-only)
                    if img.width() > 80:
                        img = img.subsample(max(1, img.width() // 72))
                    self._logo_img = img
                    tk.Label(parent, image=self._logo_img, bg=COL_BG).pack(
                        side="left", padx=(12, 0), pady=8)
                    return
                except Exception:
                    pass
            # Pillow path for jpg/webp (and as fallback for png)
            try:
                from PIL import Image, ImageTk
                im = Image.open(p)
                im.thumbnail((72, 72))
                self._logo_img = ImageTk.PhotoImage(im)
                tk.Label(parent, image=self._logo_img, bg=COL_BG).pack(
                    side="left", padx=(12, 0), pady=8)
                return
            except Exception as e:
                self.log_line(f"(logo {name} present but could not load it: {e})")
        # fallback glyph
        tk.Label(parent, text="\U0001F30B", bg=COL_BG, fg=COL_LAVA_HOT,
                 font=("TkDefaultFont", 40)).pack(side="left", padx=(12, 0), pady=8)

    def _set_window_icon(self):
        """Set the taskbar / titlebar icon from the logo file (best-effort)."""
        base = script_dir() / "assets" / "logos"
        for name in (LOGO_FILE, "LOGO.png", "LOGO.jpg", "LOGO.jpeg", "LOGO.gif"):
            p = base / name
            if not p.exists():
                continue
            ext = p.suffix.lower()
            # native tk for png/gif
            if ext in (".png", ".gif"):
                try:
                    self._icon_img = tk.PhotoImage(file=str(p))
                    self.root.iconphoto(True, self._icon_img)
                    return
                except Exception:
                    pass
            # Pillow for jpg/webp (and png fallback)
            try:
                from PIL import Image, ImageTk
                im = Image.open(p).convert("RGBA")
                im.thumbnail((128, 128))
                self._icon_img = ImageTk.PhotoImage(im)
                self.root.iconphoto(True, self._icon_img)
                return
            except Exception:
                pass
        # no logo file: nothing to set (window keeps default icon)

    # -- logging -----------------------------------------------------------
    def _write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")
        fh = self._logfile
        if fh is not None:
            try:
                fh.write(text)
                fh.flush()
            except Exception:
                pass

    def _open_session_logfile(self, session_dir: Path, session_id: str):
        """Open the per-session log inside the session folder (no LOGS folder)."""
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            path = session_dir / f"SESSION_{session_id}.log"
            self._logfile = open(path, "a", buffering=1)
            self.log_line(f"Session log -> {path}")
        except Exception as e:
            self._logfile = None
            self.log_line(f"(could not open session log file: {e})")

    def _close_session_logfile(self):
        if self._logfile is not None:
            try:
                self._logfile.close()
            except Exception:
                pass
            self._logfile = None

    def log_line(self, text):
        self.log_q.put(text if text.endswith("\n") else text + "\n")

    def _drain_log(self):
        try:
            while True:
                self._write(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    # -- step scaffolding --------------------------------------------------
    def _clear_container(self):
        for w in self.container.winfo_children():
            w.destroy()
        # forget any inner scroll regions from the step we're leaving
        self._inner_scrollables = []
        # new steps start scrolled to the top
        try:
            self._body_canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _new_step(self):
        f = tk.Frame(self.container, bg=COL_BG)
        f.pack(fill="both", expand=True)
        return f

    def _set_breadcrumb(self, crumbs):
        """Render the top breadcrumb. crumbs is a list of (label, callback):
        a callback of None means the current (non-clickable) page. Always keeps
        a Home entry first unless the caller already provided one."""
        for w in self.crumb_bar.winfo_children():
            w.destroy()
        if not crumbs or crumbs[0][0] != "Home":
            crumbs = [("Home", self.show_step0)] + list(crumbs)
        row = tk.Frame(self.crumb_bar, bg=COL_PANEL)
        row.pack(anchor="w", padx=12, pady=5)
        for i, (label, cb) in enumerate(crumbs):
            if i:
                tk.Label(row, text="  \u203a  ", bg=COL_PANEL, fg=COL_MUTED,
                         font=FONT).pack(side="left")
            last = (i == len(crumbs) - 1)
            if cb is None or last:
                tk.Label(row, text=label, bg=COL_PANEL,
                         fg=(COL_LAVA_HOT if last else COL_MUTED),
                         font=FONT_BOLD if last else FONT).pack(side="left")
            else:
                lb = tk.Label(row, text=label, bg=COL_PANEL, fg=COL_LAVA,
                              font=FONT, cursor="hand2")
                lb.pack(side="left")
                lb.bind("<Button-1>", lambda e, c=cb: c())

    def _step_header(self, parent, step_no, title, subtitle=""):
        if step_no != "":
            tk.Label(parent, text=f"STEP {step_no}", bg=COL_BG, fg=COL_LAVA,
                     font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=14,
                                                             pady=(10, 0))
        tk.Label(parent, text=title, bg=COL_BG, fg=COL_TEXT,
                 font=("TkDefaultFont", 16, "bold")).pack(anchor="w", padx=14)
        if subtitle:
            tk.Label(parent, text=subtitle, bg=COL_BG, fg=COL_MUTED, font=FONT,
                     wraplength=840, justify="left").pack(anchor="w", padx=14,
                                                          pady=(2, 8))

    # ======================================================================
    # HOME - setup / start session / view past sessions
    # ======================================================================
    def _valid_build_exists(self):
        """True if a usable LAMMPS binary is present. We only check that the
        binary exists and is executable - confirming it has specific packages
        would require launching it, which we don't do on every home render."""
        try:
            return LAMMPS_BIN.exists() and os.access(LAMMPS_BIN, os.X_OK)
        except Exception:
            return False

    def show_step0(self):
        self._set_breadcrumb([("Home", None)])
        self._clear_container()
        f = self._new_step()
        self._step_header(f, "", "LAVA",
                          "Set up your LAMMPS build once, then run sessions and "
                          "browse past reports.")

        tk.Label(f, text=APP_CREDIT, bg=COL_BG, fg=COL_MUTED, font=FONT,
                 wraplength=840, justify="left").pack(anchor="w", padx=14,
                                                      pady=(0, 8))

        have_build = self._valid_build_exists()

        # build-status banner
        banner = tk.Frame(f, bg=COL_PANEL, highlightthickness=1,
                          highlightbackground=(COL_OK if have_build else COL_ERR))
        banner.pack(fill="x", padx=14, pady=(0, 10))
        if have_build:
            tk.Label(banner, text="APPROPRIATE BUILD FOUND - PLEASE WORK FREELY",
                     bg=COL_PANEL, fg=COL_OK, font=FONT_BOLD).pack(
                anchor="w", padx=10, pady=8)
        else:
            tk.Label(banner, text="NO APPROPRIATE BUILD OF LAMMPS FOUND - PLEASE "
                     "BUILD LAMMPS BEFORE WORKING", bg=COL_PANEL, fg=COL_ERR,
                     font=FONT_BOLD).pack(anchor="w", padx=10, pady=8)

        cards = tk.Frame(f, bg=COL_BG)
        cards.pack(anchor="w", padx=14, pady=6, fill="x")

        # (title, desc, command, enabled)
        choices = [
            ("SETUP LAMMPS BUILD",
             "Build a LAMMPS binary matched to this machine. This is a one-time "
             "step (re-run it only to rebuild or upgrade). Everything else stays "
             "locked until a valid build exists.",
             self.show_step1, True),
            ("START SESSION",
             "Choose the materials and files to run, then execute every "
             "potential x configuration x structure x seed combination per "
             "material.",
             self._start_new_session, have_build),
            ("VIEW PAST SESSIONS",
             "Browse every session already run in this project and open its "
             "report.",
             self.show_previous_sessions, have_build),
        ]
        for name, desc, cmd, enabled in choices:
            edge = COL_LAVA if enabled else "#3a2a24"
            card = tk.Frame(cards, bg=COL_PANEL, bd=0, highlightthickness=1,
                            highlightbackground=edge)
            card.pack(fill="x", pady=5)
            inner = tk.Frame(card, bg=COL_PANEL)
            inner.pack(fill="x", padx=12, pady=10)
            tk.Label(inner, text=name, bg=COL_PANEL,
                     fg=(COL_TEXT if enabled else COL_MUTED),
                     font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
            tk.Label(inner, text=desc, bg=COL_PANEL, fg=COL_MUTED, font=FONT,
                     wraplength=760, justify="left").pack(anchor="w", pady=(2, 6))
            if enabled:
                self._btn(inner, name, cmd, kind="primary").pack(anchor="w")
            else:
                self._btn(inner, name + "  (build required)", None,
                          kind="ghost", state="disabled").pack(anchor="w")

    def _start_new_session(self):
        if not self._valid_build_exists():
            messagebox.showwarning("Build required",
                                   "Set up a LAMMPS build first.")
            return
        self.pipeline = 1
        self.log_line("New session selected.")
        self.show_step2()

    # ======================================================================
    # VIEW PAST SESSIONS - render HISTORICAL.csv; double-click opens the folder
    # ======================================================================
    def show_previous_sessions(self):
        self._set_breadcrumb([("Past sessions", None)])
        self._clear_container()
        f = self._new_step()
        self._step_header(
            f, "", "Past sessions",
            "This is the session history from ANALYSIS/HISTORICAL.csv.")

        # help text about the double-click action
        tk.Label(f, text="\u2139  Double-click a row to open that session's "
                 "folder (report, CSVs, logs and graphs).",
                 bg=COL_BG, fg=COL_LAVA_HOT, font=FONT_BOLD).pack(
            anchor="w", padx=14, pady=(0, 6))

        hist = repo_root() / "ANALYSIS" / "HISTORICAL.csv"
        if not hist.exists():
            tk.Label(f, text="No HISTORICAL.csv yet - run a session first.",
                     bg=COL_BG, fg=COL_MUTED, font=FONT).pack(anchor="w",
                                                              padx=14, pady=10)
            return

        try:
            with open(hist, newline="") as fh:
                reader = csv.reader(fh)
                rows = list(reader)
        except Exception as e:
            tk.Label(f, text=f"Could not read HISTORICAL.csv: {e}", bg=COL_BG,
                     fg=COL_ERR, font=FONT).pack(anchor="w", padx=14, pady=10)
            return
        if not rows:
            tk.Label(f, text="HISTORICAL.csv is empty.", bg=COL_BG, fg=COL_MUTED,
                     font=FONT).pack(anchor="w", padx=14, pady=10)
            return

        header, data = rows[0], rows[1:]
        # find the session-id column so we can resolve each row's folder
        sid_idx = None
        for i, h in enumerate(header):
            if h.strip().lower() in ("session_id", "session", "id"):
                sid_idx = i
                break

        wrap = tk.Frame(f, bg=COL_PANEL)
        wrap.pack(fill="x", padx=14, pady=(0, 6))
        tree = ttk.Treeview(wrap, columns=[f"c{i}" for i in range(len(header))],
                            show="headings", height=14, selectmode="browse")
        for i, h in enumerate(header):
            tree.heading(f"c{i}", text=h)
            tree.column(f"c{i}", width=max(90, min(240, len(h) * 12)),
                        anchor="w")
        for r in data:
            # pad/truncate row to header length
            vals = (r + [""] * len(header))[:len(header)]
            tree.insert("", "end", values=vals)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def on_double(_ev):
            item = tree.focus()
            if not item:
                return
            vals = tree.item(item, "values")
            sid = None
            if sid_idx is not None and sid_idx < len(vals):
                sid = str(vals[sid_idx]).strip()
            if not sid:
                messagebox.showinfo(
                    "No session id",
                    "This row has no session id column, so its folder can't be "
                    "located automatically.")
                return
            sessions = repo_root() / "ANALYSIS" / "SESSIONS"
            folder = sessions / f"SESSION-{sid}"
            if not folder.is_dir():
                # stopped sessions have their folder tagged with -[ABORTED]
                aborted = sessions / f"SESSION-{sid}-[ABORTED]"
                if aborted.is_dir():
                    folder = aborted
                else:
                    messagebox.showwarning(
                        "Folder not found",
                        f"No folder for session {sid} at:\n{folder}")
                    return
            self.log_line(f"Opening session folder {folder}")
            self._open_in_file_manager(folder)

        tree.bind("<Double-1>", on_double)

    def _open_report(self, report_path: Path):
        p = Path(report_path)
        if not p.exists():
            messagebox.showwarning("No report",
                                   f"Report file not found:\n{p}")
            return
        self.log_line(f"Opening report {p}")
        self._open_in_file_manager(p)

    # ======================================================================
    # STEP 1 - set up LAMMPS (build via script, sudo password from GUI)
    # ======================================================================
    def show_step1(self):
        self._set_breadcrumb([("Setup LAMMPS build", None)])
        self._clear_container()
        f = self._new_step()
        self._step_header(
            f, 1, "Set up LAMMPS",
            "Build LAMMPS for this machine. The build installs packages, so it "
            "needs your sudo password. It is used only to authorize sudo for this "
            "build and is never stored, logged, or written to disk.")

        opts = tk.Frame(f, bg=COL_BG)
        opts.pack(anchor="w", padx=14, pady=4)
        tk.Checkbutton(opts, text="Check old builds first (compare an existing "
                       "build to this machine)", variable=self.chk_check_old,
                       bg=COL_BG, fg=COL_TEXT, selectcolor=COL_ENTRY,
                       activebackground=COL_BG, activeforeground=COL_TEXT,
                       font=FONT).pack(anchor="w")
        tk.Checkbutton(opts, text="Clean old builds (delete ALL existing builds "
                       "first, force fresh build)", variable=self.chk_clean_old,
                       bg=COL_BG, fg=COL_TEXT, selectcolor=COL_ENTRY,
                       activebackground=COL_BG, activeforeground=COL_TEXT,
                       font=FONT).pack(anchor="w")

        # one-time timestamp preference (persisted). UTC is the privacy-safe
        # default: local timestamps in filenames can reveal your timezone in a
        # shared report. Stamps are suffixed Z (UTC) or L (local) so it's always
        # clear which a given file used, even if you switch later.
        tsbox = tk.Frame(f, bg=COL_BG)
        tsbox.pack(anchor="w", padx=14, pady=(10, 0))
        tk.Checkbutton(
            tsbox, text="Use UTC timestamps in filenames and reports "
            "(recommended - avoids revealing your timezone; suffix Z=UTC, "
            "L=local)", variable=self.use_utc, command=self._save_settings,
            bg=COL_BG, fg=COL_TEXT, selectcolor=COL_ENTRY,
            activebackground=COL_BG, activeforeground=COL_TEXT, font=FONT,
            wraplength=800, justify="left", anchor="w").pack(anchor="w")

        # sudo password entry
        pwbox = tk.Frame(f, bg=COL_BG)
        pwbox.pack(anchor="w", padx=14, pady=(10, 4))
        tk.Label(pwbox, text="sudo password:", bg=COL_BG, fg=COL_TEXT,
                 font=FONT).pack(side="left")
        self.pw_entry = tk.Entry(pwbox, textvariable=self.sudo_pw, show="\u2022",
                                 width=28, bg=COL_ENTRY, fg=COL_TEXT,
                                 insertbackground=COL_TEXT, relief="flat")
        self.pw_entry.pack(side="left", padx=8)
        tk.Label(pwbox, text="(not stored or logged)", bg=COL_BG, fg=COL_MUTED,
                 font=FONT).pack(side="left")

        self.build_status = tk.Label(f, text="", bg=COL_BG, fg=COL_TEXT, font=FONT,
                                     wraplength=840, justify="left")
        self.build_status.pack(anchor="w", padx=14, pady=(8, 0))

        bar = tk.Frame(f, bg=COL_BG)
        bar.pack(anchor="w", padx=14, pady=10)
        self.build_btn = self._btn(bar, "Build LAMMPS", self._start_build,
                                   kind="primary")
        self.build_btn.grid(row=0, column=0, padx=(0, 8))
        self._btn(bar, "Verify build", self._verify_build).grid(row=0, column=1,
                                                                padx=8)
        self._btn(bar, "\u2190 Home", self.show_step0, kind="ghost").grid(
            row=0, column=2, padx=8)
        # once a build exists, offer to go straight to a new session
        self.step1_next = self._btn(bar, "Start a session  \u2192",
                                    self._start_new_session, kind="primary")
        self.step1_next.grid(row=0, column=3, padx=8)
        self.step1_next.configure(state="disabled")

        if LAMMPS_BIN.exists():
            self._verify_build()

    def _build_script_path(self) -> Path:
        return script_dir() / BUILD_SCRIPT

    def _check_old_builds(self):
        if not LAMMPS_BIN.exists():
            return (False, False, "no existing build")
        want_gpu = have_nvidia_smi()
        mode = "unknown"
        try:
            h = subprocess.run([str(LAMMPS_BIN), "-h"], capture_output=True,
                               text=True, timeout=20).stdout.lower()
            if "gpu package api:" in h and ("cuda" in h or "opencl" in h):
                mode = "gpu"
            elif h.strip():
                mode = "cpu"
        except Exception as e:
            return (True, False, f"exists but not runnable ({e})")
        matches = (mode == "gpu") if want_gpu else (mode == "cpu")
        return (True, matches, f"existing build is {mode}; machine wants "
                f"{'gpu' if want_gpu else 'cpu'}")

    def _verify_build(self):
        exists = LAMMPS_BIN.exists()
        if not exists:
            self.build_status.configure(
                text=f"No build found at {LAMMPS_BIN}. Build first.", fg=COL_ERR)
            self.step1_next.configure(state="disabled")
            return
        mode = "unknown"
        try:
            h = subprocess.run([str(LAMMPS_BIN), "-h"], capture_output=True,
                               text=True, timeout=20).stdout.lower()
            if "gpu package api:" in h and ("cuda" in h or "opencl" in h):
                mode = "GPU"
            elif h.strip():
                mode = "CPU"
        except Exception:
            pass
        self.build_status.configure(
            text=f"Build verified: {mode} binary at {LAMMPS_BIN}", fg=COL_OK)
        self.step1_next.configure(state="normal")
        self.log_line(f"Verify: {mode} build at {LAMMPS_BIN}")

    def _start_build(self):
        pw = self.sudo_pw.get()
        if not pw:
            messagebox.showwarning("Password needed",
                                   "Enter your sudo password to build.")
            return
        self.build_btn.configure(state="disabled")
        self.step1_next.configure(state="disabled")
        # hand the password to the worker as a local; clear the entry immediately
        threading.Thread(target=self._build_worker, args=(pw,), daemon=True).start()
        self.sudo_pw.set("")   # clear from the widget/var right away

    def _build_worker(self, pw):
        try:
            script = self._build_script_path()
            if not script.exists():
                self.log_line(f"ERROR: {BUILD_SCRIPT} not found at {script}")
                return

            # 1) authenticate sudo using the password via stdin (never logged).
            self.log_line("Authenticating sudo...")
            try:
                r = subprocess.run(["sudo", "-S", "-v"],
                                   input=pw + "\n", text=True,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, timeout=30)
            except Exception as e:
                self.log_line(f"ERROR: could not run sudo ({e})")
                return
            if r.returncode != 0:
                # do NOT echo stderr verbatim in case it contains the prompt+attempt
                self.log_line("ERROR: sudo authentication failed (wrong password?).")
                return
            self.log_line("sudo authenticated.")

            # 2) optional clean
            if self.chk_clean_old.get():
                self.log_line("Cleaning old builds (deleting ALL)...")
                shutil.rmtree(LAMMPS_BUILD_ROOT / "build", ignore_errors=True)

            # 3) optional check
            if self.chk_check_old.get():
                exists, matches, detail = self._check_old_builds()
                self.log_line(f"Check: {detail}")
                if exists and matches and not self.chk_clean_old.get():
                    self.log_line("Existing build already matches - skipping build.")
                    self.root.after(0, self._verify_build)
                    self.root.after(0, lambda: self.build_btn.configure(
                        state="normal"))
                    return

            # 4) run the build script. sudo credentials are cached (step 1), so
            #    the script's internal `sudo` calls won't re-prompt within the
            #    timeout window. We keep the cache warm with a background -v.
            self.log_line("--- Building LAMMPS (this can take a while) ---")
            keep_alive = threading.Event()
            def _sudo_keepalive():
                while not keep_alive.wait(50):
                    subprocess.run(["sudo", "-n", "-v"],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
            ka = threading.Thread(target=_sudo_keepalive, daemon=True)
            ka.start()
            try:
                rc = self._stream_subprocess(["bash", str(script)])
            finally:
                keep_alive.set()

            if rc == 0 and LAMMPS_BIN.exists():
                self.log_line(f"Build complete: {LAMMPS_BIN}")
                self.root.after(0, self._verify_build)
            else:
                self.log_line(f"Build did not complete (exit {rc}).")
        except Exception as e:
            self.log_line(f"ERROR during build: {e}")
        finally:
            # scrub the password from memory
            pw = "\x00" * len(pw)
            del pw
            self.root.after(0, lambda: self.build_btn.configure(state="normal"))

    def _stream_subprocess(self, cmd, cwd=None):
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    bufsize=1, cwd=cwd)
            for line in proc.stdout:
                self.log_line(line.rstrip("\n"))
            proc.wait()
            self.log_line(f"[exit {proc.returncode}]")
            return proc.returncode
        except Exception as e:
            self.log_line(f"ERROR running {' '.join(cmd)}: {e}")
            return -1

    # ======================================================================
    # STEP 2 - seeds + per-material file selection (root fixed to the repo)
    # ======================================================================
    def show_step2(self):
        self._set_breadcrumb([("New session", self._start_new_session), ("Choose materials", None)])
        self._clear_container()
        f = self._new_step()

        # Root and materials source are no longer chosen by the user: the
        # project root is the repo (one level above AUTOMATION/), and materials
        # always live in ROOT/SIMULATION/MATERIALS. Inter-material mixing is not
        # allowed - combinations are computed per material only.
        root = repo_root()
        self.project_root.set(str(root))
        materials_root = root / "SIMULATION" / "MATERIALS"
        materials_root.mkdir(parents=True, exist_ok=True)
        self.materials_src.set(str(materials_root))

        self._step_header(
            f, 2, "Choose materials & seeds",
            f"Project root: {root}\n"
            f"Materials: {materials_root}\n"
            "Set the seeds to run, tick the files to include per material, or add "
            "a new material profile. Each material's runs are its own "
            "potential x configuration x structure x seed combinations - "
            "materials are never mixed together.")

        # --- top: seed bar --------------------------------------------------
        top = tk.Frame(f, bg=COL_BG)
        top.pack(anchor="w", padx=14, fill="x", pady=(0, 4))
        tk.Label(top, text="Seeds", bg=COL_BG, fg=COL_TEXT, font=FONT_BOLD,
                 width=8, anchor="w").pack(side="left")
        tk.Entry(top, textvariable=self.seed_spec, width=40, bg=COL_ENTRY,
                 fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat").pack(
            side="left", padx=6)
        tk.Label(top, text="e.g. 1,3,5-8", bg=COL_BG, fg=COL_MUTED,
                 font=FONT).pack(side="left")

        tk.Label(f, text="Materials & files", bg=COL_BG, fg=COL_LAVA_HOT,
                 font=FONT_BOLD).pack(anchor="w", padx=14, pady=(10, 2))

        # --- per-material asset lists (this box scrolls internally; the whole
        #     window also scrolls if the page overflows) ---------------------
        self.mat_area = tk.Frame(f, bg=COL_PANEL)
        self.mat_area.pack(fill="x", padx=14, pady=(0, 6))

        # holds {material: {kind: {filename: BooleanVar}}}
        self.file_vars = {}
        self._populate_materials()

        # --- action bar at the natural end of the flow ----------------------
        bar = tk.Frame(f, bg=COL_BG)
        bar.pack(anchor="w", padx=14, pady=10, fill="x")
        self._btn(bar, "\u2190 Back", self.show_step1, kind="ghost").grid(
            row=0, column=0, padx=(0, 8))
        self._btn(bar, "+ Add material profile",
                  self._add_material_profile).grid(row=0, column=1, padx=8)
        self.build_project_btn = self._btn(
            bar, "Build project + save config", self._build_project, kind="primary")
        self.build_project_btn.grid(row=0, column=2, padx=8)

    def _populate_materials(self):
        for w in self.mat_area.winfo_children():
            w.destroy()
        self.file_vars = {}
        self.mat_select_vars = {}   # material -> master select BooleanVar
        src = Path(self.materials_src.get().strip())
        materials = discover_materials(src)
        if not materials:
            tk.Label(self.mat_area,
                     text="No materials found in SIMULATION/MATERIALS yet. Use "
                     "\u201c+ Add material profile\u201d below to create one.",
                     bg=COL_PANEL, fg=COL_MUTED, font=FONT,
                     wraplength=800, justify="left").pack(anchor="w", padx=10,
                                                          pady=10)
            return

        canvas = tk.Canvas(self.mat_area, bg=COL_PANEL, highlightthickness=0,
                           height=300)
        sb = ttk.Scrollbar(self.mat_area, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COL_PANEL)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # keep the inner frame the same width as the canvas so content wraps
        # within view instead of overflowing to the right
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # register so the app-wide wheel dispatcher scrolls this list when the
        # pointer is over it (window scrolls otherwise)
        self._register_scrollable(canvas)

        for mat, mdir in materials.items():
            self.file_vars[mat] = {}
            info = read_material_info(mdir)
            untested = is_untested_name(mat) or not info.get("tested", True)
            block = tk.Frame(inner, bg=COL_PANEL, highlightthickness=1,
                             highlightbackground="#3a2a24")
            block.pack(fill="x", anchor="w", pady=4)

            # title row: expander + master checkbox + name + tag + Edit
            titlerow = tk.Frame(block, bg=COL_PANEL)
            titlerow.pack(fill="x", anchor="w")

            # details frame (per-file checkboxes) starts collapsed
            details = tk.Frame(block, bg=COL_PANEL)

            exp_var = tk.BooleanVar(value=False)
            exp_btn = tk.Label(titlerow, text="\u25B6", bg=COL_PANEL,
                               fg=COL_LAVA_HOT, font=FONT_BOLD, cursor="hand2",
                               width=2)
            exp_btn.pack(side="left", padx=(6, 0))

            def _toggle_expand(m=mat, d=details, b=exp_btn, ev=exp_var):
                if ev.get():
                    d.pack_forget(); b.configure(text="\u25B6"); ev.set(False)
                else:
                    d.pack(fill="x", anchor="w", padx=(24, 6), pady=(0, 6))
                    b.configure(text="\u25BC"); ev.set(True)
            exp_btn.bind("<Button-1>", lambda e, fn=_toggle_expand: fn())

            # master checkbox: ticks/unticks every file in this material at once
            mat_var = tk.BooleanVar(value=True)
            mat_cb = tk.Checkbutton(
                titlerow, variable=mat_var, bg=COL_PANEL, selectcolor=COL_ENTRY,
                activebackground=COL_PANEL,
                command=lambda m=mat: self._toggle_material(m))
            mat_cb.pack(side="left")
            self.mat_select_vars[mat] = mat_var

            # clicking the name also expands/collapses (bigger hit target)
            name_lbl = tk.Label(titlerow, text=display_material_name(mat),
                                bg=COL_PANEL, fg=COL_LAVA_HOT, font=FONT_BOLD,
                                cursor="hand2")
            name_lbl.pack(side="left")
            name_lbl.bind("<Button-1>", lambda e, fn=_toggle_expand: fn())

            if untested:
                tk.Label(titlerow, text="  \u26A0 UNTESTED",
                         bg=COL_PANEL, fg=COL_WARN, font=FONT_BOLD).pack(
                    side="left")
            self._btn(titlerow, "Edit",
                      lambda m=mat, d=mdir: self._edit_material_profile(m, d),
                      kind="ghost").pack(side="right", padx=(0, 6))

            # a small hint of how many files, shown while collapsed
            counts = {k: len(list_material_inputs(mdir, k))
                      for k in ("potentials", "configs", "structures")}
            tk.Label(titlerow,
                     text=f"  {counts['potentials']}p \u00b7 {counts['configs']}c "
                          f"\u00b7 {counts['structures']}s",
                     bg=COL_PANEL, fg=COL_MUTED, font=FONT).pack(side="left")

            if untested:
                desc = info.get("description", "")
                msg = ("Contributed by a user and not validated. You may run it, "
                       "but some or all combinations may fail.")
                if desc:
                    msg += f" Note: {desc}"
                tk.Label(details, text=msg, bg=COL_PANEL, fg=COL_MUTED, font=FONT,
                         wraplength=780, justify="left").pack(anchor="w")

            for kind in ("potentials", "configs", "structures"):
                files = list_material_inputs(mdir, kind)
                self.file_vars[mat][kind] = {}
                if not files:
                    if kind == "potentials":
                        tk.Label(details, text="potentials: (none - runs execute "
                                 "without a potential file)", bg=COL_PANEL,
                                 fg=COL_MUTED, font=FONT).pack(anchor="w")
                    else:
                        tk.Label(details, text=f"{kind}: (none found - required)",
                                 bg=COL_PANEL, fg=COL_ERR, font=FONT).pack(
                            anchor="w")
                    continue
                row = tk.Frame(details, bg=COL_PANEL)
                row.pack(fill="x", anchor="w")
                tk.Label(row, text=f"{kind}:", bg=COL_PANEL, fg=COL_MUTED,
                         font=FONT, width=12, anchor="nw").pack(side="left",
                                                                anchor="n")
                cbwrap = tk.Frame(row, bg=COL_PANEL)
                cbwrap.pack(side="left", fill="x", expand=True)
                for fp in files:
                    v = tk.BooleanVar(value=True)   # preselected
                    self.file_vars[mat][kind][fp.name] = v
                    tk.Checkbutton(cbwrap, text=fp.name, variable=v, bg=COL_PANEL,
                                   fg=COL_TEXT, selectcolor=COL_ENTRY,
                                   activebackground=COL_PANEL,
                                   activeforeground=COL_TEXT, font=FONT,
                                   anchor="w",
                                   command=lambda m=mat:
                                       self._refresh_material_master(m)).pack(
                        side="top", anchor="w")

    def _iter_material_file_vars(self, mat):
        """Yield every file BooleanVar for a material, across all kinds."""
        for kind_vars in self.file_vars.get(mat, {}).values():
            for v in kind_vars.values():
                yield v

    def _toggle_material(self, mat):
        """Master checkbox handler: set every file in this material to match."""
        want = self.mat_select_vars[mat].get()
        for v in self._iter_material_file_vars(mat):
            v.set(want)

    def _refresh_material_master(self, mat):
        """Keep the master checkbox in sync when files are toggled by hand:
        ticked only if every file is ticked (unticked if any is off)."""
        vals = [v.get() for v in self._iter_material_file_vars(mat)]
        master = self.mat_select_vars.get(mat)
        if master is not None and vals:
            master.set(all(vals))

    def _add_material_profile(self):
        MaterialEditor(self, Path(self.materials_src.get().strip()))

    def _edit_material_profile(self, mat, mdir):
        MaterialEditor(self, Path(self.materials_src.get().strip()),
                       existing_name=mat, existing_dir=mdir)

    @staticmethod
    def _pointer_in(widget):
        """True if the mouse pointer is currently over the given widget."""
        try:
            x, y = widget.winfo_pointerxy()
            wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
            return (wx <= x <= wx + widget.winfo_width() and
                    wy <= y <= wy + widget.winfo_height())
        except Exception:
            return False

    def _selected_files(self, mat, kind):
        return [name for name, v in self.file_vars.get(mat, {}).get(kind, {}).items()
                if v.get()]

    def _build_project(self):
        root = self.project_root.get().strip()
        src = self.materials_src.get().strip()
        if not root:
            messagebox.showwarning("Missing", "Choose a project root.")
            return
        if not src:
            messagebox.showwarning("Missing", "Choose a materials source folder.")
            return
        try:
            seeds = parse_seeds(self.seed_spec.get())
        except ValueError as e:
            messagebox.showerror("Bad seeds", str(e))
            return
        if not seeds:
            messagebox.showwarning("Missing", "Enter at least one seed.")
            return
        self.build_project_btn.configure(state="disabled")
        threading.Thread(target=self._build_project_worker,
                         args=(root, src, seeds), daemon=True).start()

    def _ensure_tree(self, root: Path):
        for top, subs in PROJECT_TREE.items():
            (root / top).mkdir(parents=True, exist_ok=True)
            for s in subs:
                (root / top / s).mkdir(parents=True, exist_ok=True)

    def _build_project_worker(self, root_str, src_str, seeds):
        try:
            root = Path(root_str)
            src = Path(src_str)
            self.log_line(f"--- Building project tree at {root} ---")
            self._ensure_tree(root)
            materials_root = root / "SIMULATION" / "MATERIALS"
            materials_root.mkdir(parents=True, exist_ok=True)

            materials = discover_materials(src)
            selected = {}   # material -> {kind: [names]}
            untested = {}   # material -> contributor description (untested only)
            for mat, mdir in materials.items():
                sel = {k: self._selected_files(mat, k)
                       for k in ("potentials", "configs", "structures")}
                # config + structure are required; potentials are optional
                # (a material with no potentials runs config x structure x seed).
                if not sel["configs"] or not sel["structures"]:
                    self.log_line(f"  {mat}: missing configs or structures - "
                                  f"skipped")
                    continue
                if not sel["potentials"]:
                    self.log_line(f"  {mat}: no potential files - runs will execute "
                                  f"without a potential.")
                selected[mat] = sel
                info = read_material_info(mdir)
                if is_untested_name(mat) or not info.get("tested", True):
                    untested[mat] = info.get("description", "")
                for kind in ("potentials", "configs", "structures"):
                    dest = materials_root / mat / MATERIAL_SUBDIRS[kind]
                    dest.mkdir(parents=True, exist_ok=True)
                    for name in sel[kind]:
                        srcf = mdir / MATERIAL_SUBDIRS[kind] / name
                        dstf = dest / name
                        try:
                            if srcf.resolve() != dstf.resolve():
                                shutil.copy2(srcf, dstf)
                        except Exception as e:
                            self.log_line(f"    WARN copy {name}: {e}")
                # carry the material metadata alongside the copied files
                src_info = mdir / MATERIAL_INFO_NAME
                dst_info = materials_root / mat / MATERIAL_INFO_NAME
                try:
                    if src_info.exists() and src_info.resolve() != dst_info.resolve():
                        shutil.copy2(src_info, dst_info)
                except Exception:
                    pass
                self.log_line(f"  {mat}: "
                              f"{len(sel['potentials'])}p x "
                              f"{len(sel['configs'])}c x "
                              f"{len(sel['structures'])}s")

            if not selected:
                self.log_line("ERROR: no materials had a complete selection.")
                self.root.after(0, lambda: self.build_project_btn.configure(
                    state="normal"))
                return

            temp_dir = self.temp_dir.get().strip()
            temp_path = Path(temp_dir) if temp_dir else (root / "TEMP")
            temp_path.mkdir(parents=True, exist_ok=True)

            cfg = {
                "pipeline": self.pipeline,
                "app_version": VERSION,
                "created": now_iso(),
                "project_root": str(root),
                "lammps_bin": str(LAMMPS_BIN),
                "seeds": seeds,
                "materials_root": str(materials_root),
                "materials": selected,
                "untested_materials": untested,
                "paths": {
                    "sessions": str(root / "ANALYSIS" / "SESSIONS"),
                    "logs": str(root / "LOGS"),
                    "temp": str(temp_path),
                },
            }
            cfg_path = root / "PROJECT" / CONFIG_NAME
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cfg_path, "w") as fh:
                json.dump(cfg, fh, indent=2)
            self.log_line(f"Wrote {cfg_path}")
            self.log_line("Project setup complete.")
            self.root.after(0, lambda: self.load_config_and_run(cfg_path))
        except Exception as e:
            self.log_line(f"ERROR building project: {e}")
            self.root.after(0, lambda: self.build_project_btn.configure(
                state="normal"))

    # ======================================================================
    # STEP 3 - configure + run a session
    # ======================================================================
    def load_config_and_run(self, cfg_path):
        try:
            with open(cfg_path) as fh:
                self.config = json.load(fh)
        except Exception as e:
            messagebox.showerror("Config error", f"Could not read {cfg_path}: {e}")
            return

        # Validate the config against the current schema. Older versions used a
        # completely different layout (flat file lists, no materials), so an old
        # config is missing the keys Step 3 needs. Detect that and send the user
        # back to Step 2 rather than crashing.
        required = ("materials_root", "materials", "seeds", "paths")
        missing = [k for k in required if k not in (self.config or {})]
        if missing:
            ver = self.config.get("app_version", "unknown") if self.config else "?"
            if messagebox.askyesno(
                    "Incompatible project config",
                    f"This project's config.json is from an older version of LAVA "
                    f"(version: {ver}) and is missing: {', '.join(missing)}.\n\n"
                    f"The project needs to be set up again with the current "
                    f"version. Go to Step 2 to reconfigure now?\n\n"
                    f"(Your data folders are untouched; only config.json is "
                    f"rewritten.)"):
                # prefill the project root so the user doesn't re-pick it
                if isinstance(self.config, dict) and self.config.get("project_root"):
                    self.project_root.set(self.config["project_root"])
                self.config = None
                self.show_step2()
            return

        self.config_path = Path(cfg_path)
        self.show_step3()

    def _plan_runs(self):
        """Build the full list of run specs from config: for each material, the
        full potential x config x structure x seed combination within that
        material. If a material has no potentials, its runs are
        config x structure x seed with potential = None (no potential file)."""
        cfg = self.config
        mats_root = Path(cfg["materials_root"])
        seeds = cfg.get("seeds", [1])
        runs = []
        for mat, sel in cfg["materials"].items():
            mdir = mats_root / mat
            # a material with no potentials contributes one "None" potential so
            # the combination still produces config x structure x seed runs.
            pots = sel["potentials"] if sel["potentials"] else [None]
            for pot in pots:
                for conf in sel["configs"]:
                    for struct in sel["structures"]:
                        for seed in seeds:
                            runs.append({
                                "material": mat,
                                "material_dir": str(mdir),
                                "potential": pot,   # may be None
                                "configuration": conf,
                                "structure": struct,
                                "seed": seed,
                            })
        return runs

    def show_step3(self):
        self._set_breadcrumb([("New session", self._start_new_session), ("Choose materials", self.show_step2), ("Run session", None)])
        self._clear_container()
        f = self._new_step()
        self.planned_runs = self._plan_runs()
        total = len(self.planned_runs)
        mats = [display_material_name(m) for m in self.config["materials"].keys()]

        self._step_header(
            f, 3, "Run a session",
            f"Project: {self.config['project_root']}\n"
            f"Materials: {', '.join(mats)}   Seeds: "
            f"{','.join(map(str, self.config.get('seeds', [])))}\n"
            f"This session will run {total} combination(s).")

        # untested community-profile warning
        untested = self.config.get("untested_materials", {})
        if untested:
            names = ", ".join(display_material_name(m) for m in untested)
            warn = tk.Frame(f, bg=COL_PANEL, highlightthickness=1,
                            highlightbackground=COL_WARN)
            warn.pack(anchor="w", padx=14, pady=(0, 6), fill="x")
            tk.Label(warn, text=f"\u26A0 UNTESTED community profile(s): {names}",
                     bg=COL_PANEL, fg=COL_WARN, font=FONT_BOLD).pack(
                anchor="w", padx=8, pady=(6, 0))
            tk.Label(warn, text="These profiles were contributed by users and "
                     "have not been validated as fully functional. You may run "
                     "them, but some or all of their combinations may fail.",
                     bg=COL_PANEL, fg=COL_MUTED, font=FONT, wraplength=800,
                     justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # metrics prereq
        self.has_psutil = have_psutil()
        self.has_nvsmi = have_nvidia_smi()
        self.metrics_ok = self.has_psutil
        info = tk.Frame(f, bg=COL_BG)
        info.pack(anchor="w", padx=14)
        if not self.has_psutil:
            tk.Label(info, text="psutil is required (pip install psutil).",
                     bg=COL_BG, fg=COL_ERR, font=FONT).pack(anchor="w")
        elif self.has_nvsmi:
            tk.Label(info, text="Metrics: CPU/RAM via psutil, GPU via nvidia-smi.",
                     bg=COL_BG, fg=COL_OK, font=FONT).pack(anchor="w")
        else:
            tk.Label(info, text="Metrics: CPU/RAM via psutil. No NVIDIA GPU - GPU "
                     "metrics will be blank.", bg=COL_BG, fg=COL_MUTED,
                     font=FONT).pack(anchor="w")

        # resource limits + probe interval
        lim = tk.LabelFrame(f, text="Resource limits & probing", bg=COL_BG,
                            fg=COL_MUTED, font=FONT)
        lim.pack(anchor="w", padx=14, pady=8, fill="x")
        self.max_cpu = tk.StringVar(value=str(DEFAULT_MAX_CPU))
        self.max_ram = tk.StringVar(value=str(DEFAULT_MAX_RAM))
        self.max_gpu = tk.StringVar(value=str(DEFAULT_MAX_GPU))
        self.probe_sec = tk.StringVar(value=str(DEFAULT_PROBE_SEC))

        def _limit(col, label, var, show=True):
            if not show:
                return
            cell = tk.Frame(lim, bg=COL_BG)
            cell.grid(row=0, column=col, padx=8, pady=6, sticky="w")
            tk.Label(cell, text=label, bg=COL_BG, fg=COL_TEXT, font=FONT).pack(
                side="left")
            tk.Entry(cell, textvariable=var, width=6, bg=COL_ENTRY, fg=COL_TEXT,
                     insertbackground=COL_TEXT, relief="flat").pack(side="left",
                                                                    padx=4)

        _limit(0, "Max CPU %", self.max_cpu)
        _limit(1, "Max RAM %", self.max_ram)
        _limit(2, "Max GPU %", self.max_gpu, show=self.has_nvsmi)
        _limit(3, "Probe every (s)", self.probe_sec)

        # output-generation choices
        self.gen_html = tk.BooleanVar(value=True)
        self.gen_png = tk.BooleanVar(value=True)
        outbox = tk.Frame(lim, bg=COL_BG)
        outbox.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))
        tk.Checkbutton(outbox, text="Generate HTML report at end",
                       variable=self.gen_html, bg=COL_BG, fg=COL_TEXT,
                       selectcolor=COL_ENTRY, activebackground=COL_BG,
                       activeforeground=COL_TEXT, font=FONT).pack(side="left")
        tk.Checkbutton(outbox, text="Generate PNG graphs (needs matplotlib+numpy)",
                       variable=self.gen_png, bg=COL_BG, fg=COL_TEXT,
                       selectcolor=COL_ENTRY, activebackground=COL_BG,
                       activeforeground=COL_TEXT, font=FONT).pack(side="left",
                                                                  padx=(16, 0))

        # read-only preview of planned runs
        prev = tk.LabelFrame(f, text="Runs to execute (preview)", bg=COL_BG,
                             fg=COL_MUTED, font=FONT)
        prev.pack(anchor="w", padx=14, pady=(4, 6), fill="x")
        cols = ("idx", "material", "potential", "configuration", "structure", "seed")
        self.preview = ttk.Treeview(prev, columns=cols, show="headings", height=7)
        headings = {"idx": "#", "material": "Material", "potential": "Potential",
                    "configuration": "Config", "structure": "Structure",
                    "seed": "Seed"}
        widths = {"idx": 40, "material": 120, "potential": 130,
                  "configuration": 130, "structure": 150, "seed": 50}
        for c in cols:
            self.preview.heading(c, text=headings[c])
            self.preview.column(c, width=widths[c], anchor="w")
        # row states used to show progress while the session runs
        self.preview.tag_configure("active", background=COL_LAVA,
                                   foreground=COL_TEXT)
        self.preview.tag_configure("done", foreground=COL_MUTED)
        self.preview.tag_configure("pending", foreground=COL_TEXT)
        self._preview_items = []
        for i, r in enumerate(self.planned_runs, 1):
            iid = self.preview.insert("", "end", tags=("pending",), values=(
                i, r["material"], r["potential"] if r["potential"] else "none",
                r["configuration"], r["structure"], r["seed"]))
            self._preview_items.append(iid)
        self.preview.pack(fill="both", expand=True, side="left")
        psb = ttk.Scrollbar(prev, orient="vertical", command=self.preview.yview)
        psb.pack(side="right", fill="y")
        self.preview.configure(yscrollcommand=psb.set)

        # live status
        self.status_lbl = tk.Label(f, text="", bg=COL_BG, fg=COL_TEXT, font=FONT,
                                   justify="left", wraplength=840)
        self.status_lbl.pack(anchor="w", padx=14)
        self.eta_lbl = tk.Label(f, text="", bg=COL_BG, fg=COL_LAVA_HOT, font=FONT)
        self.eta_lbl.pack(anchor="w", padx=14)
        self.progress = ttk.Progressbar(f, length=680, maximum=max(total, 1),
                                        style="Lava.Horizontal.TProgressbar")
        self.progress.pack(anchor="w", padx=14, pady=(2, 6))

        # controls
        bar = tk.Frame(f, bg=COL_BG)
        bar.pack(anchor="w", padx=14, pady=(0, 8))
        self.run_btn = self._btn(bar, "Start session", self._start_session,
                                 kind="primary")
        self.run_btn.grid(row=0, column=0, padx=(0, 8))
        if not self.metrics_ok or total == 0:
            self.run_btn.configure(state="disabled")
        self.pause_btn = self._btn(bar, "Pause", self._toggle_pause)
        self.pause_btn.grid(row=0, column=1, padx=8)
        self.pause_btn.configure(state="disabled")
        self.skip_btn = self._btn(bar, "Skip next run", self._skip_run)
        self.skip_btn.grid(row=0, column=2, padx=8)
        self.skip_btn.configure(state="disabled")
        self.stop_btn = self._btn(bar, "Stop", self._stop_session, kind="danger")
        self.stop_btn.grid(row=0, column=3, padx=8)
        self.stop_btn.configure(state="disabled")
        self._btn(bar, "Reconfigure", self.show_step2, kind="ghost").grid(
            row=0, column=4, padx=8)
        self.open_session_btn = None

    # -- session controls --------------------------------------------------
    def _start_session(self):
        if not self.metrics_ok:
            messagebox.showerror("psutil required",
                                 "psutil is required (pip install psutil).")
            return
        # validate limits
        try:
            limits = {
                "cpu": float(self.max_cpu.get()),
                "ram": float(self.max_ram.get()),
                "gpu": float(self.max_gpu.get()) if self.has_nvsmi else None,
            }
            probe = int(self.probe_sec.get())
            if probe < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Bad values",
                                 "Limits must be numbers and probe interval a "
                                 "positive integer.")
            return

        self.stop_event.clear()
        self.pause_event.clear()
        self.skip_event.clear()
        self.duration_history = []
        self.run_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.skip_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self.session_thread = threading.Thread(
            target=self._session_worker, args=(limits, probe), daemon=True)
        self.session_thread.start()

    def _toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text="Pause")
            self.log_line("Resumed.")
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="Resume")
            self.log_line("Paused - will hold before the next run.")

    def _skip_run(self):
        self.skip_event.set()
        self.log_line("Skip requested - aborting current run early.")

    def _stop_session(self):
        if messagebox.askyesno("Stop session",
                               "Stop the entire session? The current run will be "
                               "aborted."):
            self.stop_event.set()
            self.skip_event.set()   # unblock a running proc
            self.log_line("Stop requested.")

    def _mark_preview_run(self, idx):
        """Mark run #idx (1-based) as active in the preview, everything before
        it as done, everything after as pending. Runs on the main thread."""
        def _apply():
            items = getattr(self, "_preview_items", [])
            for n, iid in enumerate(items, start=1):
                if n < idx:
                    tag = "done"
                elif n == idx:
                    tag = "active"
                else:
                    tag = "pending"
                self.preview.item(iid, tags=(tag,))
            if 0 < idx <= len(items):
                self.preview.see(items[idx - 1])
        self.root.after(0, _apply)

    def _clear_preview_highlight(self):
        """Drop the active highlight at session end; done rows stay marked."""
        def _apply():
            items = getattr(self, "_preview_items", [])
            for iid in items:
                if "active" in self.preview.item(iid, "tags"):
                    self.preview.item(iid, tags=("done",))
        self.root.after(0, _apply)

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_lbl.configure(text=text))

    def _set_eta(self, text):
        self.root.after(0, lambda: self.eta_lbl.configure(text=text))

    def _detect_run_flags(self):
        try:
            h = subprocess.run([self.config.get("lammps_bin", str(LAMMPS_BIN)), "-h"],
                               capture_output=True, text=True, timeout=20).stdout.lower()
            if "gpu package api:" in h and ("cuda" in h or "opencl" in h):
                return ["-sf", "gpu", "-pk", "gpu", "1"]
        except Exception:
            pass
        return []

    def _session_worker(self, limits, probe_sec):
        try:
            cfg = self.config
            root = Path(cfg["project_root"])
            runs = self.planned_runs
            total = len(runs)

            session_id = self._session_stamp()   # e.g. 20250101120000Z
            session_dir = root / "ANALYSIS" / "SESSIONS" / f"SESSION-{session_id}"
            runs_dir = session_dir / "RUNS"
            runs_dir.mkdir(parents=True, exist_ok=True)
            temp_root = Path(cfg["paths"].get("temp", root / "TEMP"))
            temp_root.mkdir(parents=True, exist_ok=True)
            self.last_session_dir = session_dir
            self.last_session_id = session_id
            # per-session log lives in the session folder
            self._open_session_logfile(session_dir, session_id)
            self.log_line(f"=== SESSION-{session_id}: {total} run(s) ===")

            lmp = cfg.get("lammps_bin") or str(LAMMPS_BIN)
            run_flags = self._detect_run_flags()

            # per-session summary CSV (one row per run)
            session_csv = session_dir / f"SESSION-SUMMARY_{session_id}.csv"
            session_fields = [
                "run_index", "run_id", "material", "status",
                "potential", "structure", "configuration", "seed",
                "start_time", "end_time", "duration_sec", "exit_code",
                "avg_gpu_usage_pct", "avg_cpu_usage_pct",
                "avg_gpu_temp_c", "avg_cpu_temp_c", "avg_ram_usage_pct",
            ]
            # per-session hardware-stats CSV (all probes, tagged with run_id)
            hw_csv = session_dir / f"SESSION-HW-STATS_{session_id}.csv"
            hw_fields = ["run_id", "timestamp", "gpu_usage_pct", "cpu_usage_pct",
                         "gpu_temp_c", "cpu_temp_c", "ram_usage_pct"]
            self._hw_csv_path = hw_csv
            self._hw_fields = hw_fields
            session_start = time.time()
            session_start_utc = now_utc_iso()

            # capture machine specs + start time into SESSION-INFO.json
            self._session_info = {
                "session_id": session_id,
                "app_version": VERSION,
                "start_utc": session_start_utc,
                "end_utc": None,
                "duration_sec": None,
                "num_runs_planned": total,
                "probe_interval_sec": probe_sec,
                "limits": limits,
                "hardware": detect_hardware(),
            }
            info_path = session_dir / f"SESSION-INFO_{session_id}.json"
            try:
                with open(info_path, "w") as fh:
                    json.dump(self._session_info, fh, indent=2)
                self.log_line(f"SESSION INFO -> {info_path}")
            except Exception as e:
                self.log_line(f"Could not write session info: {e}")
            self._session_info_path = info_path

            all_samples = []
            temp_dirs_made = []

            for idx, spec in enumerate(runs, start=1):
                if self.stop_event.is_set():
                    self.log_line("Session stopped by user.")
                    break

                # pause gate (before starting a run)
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    self._set_status("PAUSED")
                    time.sleep(0.3)
                if self.stop_event.is_set():
                    break

                self.skip_event.clear()
                mat = spec["material"]
                mdir = Path(spec["material_dir"])
                has_pot = spec["potential"] is not None
                pot = (mdir / MATERIAL_SUBDIRS["potentials"] / spec["potential"]
                       if has_pot else None)
                conf = mdir / MATERIAL_SUBDIRS["configs"] / spec["configuration"]
                struct = mdir / MATERIAL_SUBDIRS["structures"] / spec["structure"]
                seed = spec["seed"]
                pot_label = spec["potential"] if has_pot else "none"

                run_stamp = self._session_stamp()
                run_base = f"RUN-{idx:04d}-{run_stamp}"

                self.root.after(0, lambda i=idx, t=total: self._update_progress(i, t))
                self._mark_preview_run(idx)
                self._set_status(
                    f"Run {idx}/{total}  [{mat}]  pot={pot_label}  "
                    f"conf={spec['configuration']}  struct={spec['structure']}  "
                    f"seed={seed}")
                self.log_line(f"--- {run_base}  [{mat}] pot={pot_label} "
                              f"conf={spec['configuration']} "
                              f"struct={spec['structure']} seed={seed} ---")
                if not has_pot:
                    self.log_line("    No potential file for this combination - "
                                  "running without one.")

                # isolated temp working dir; copy inputs in (potential optional)
                work = temp_root / run_base
                work.mkdir(parents=True, exist_ok=True)
                temp_dirs_made.append(work)
                to_copy = [conf, struct] + ([pot] if has_pot else [])
                for srcf in to_copy:
                    try:
                        shutil.copy2(srcf, work / srcf.name)
                    except Exception as e:
                        self.log_line(f"    WARN copy {srcf.name}: {e}")

                sampler = MetricsSampler(interval=probe_sec)
                sampler.start()
                start_time = now_iso()
                t0 = time.time()

                cmd = ["mpiexec", "-n", "4", lmp] + run_flags + [
                    "-in", conf.name,
                    "-var", "infile", struct.name,
                    "-var", "seed", str(seed),
                ]
                if has_pot:
                    cmd += ["-var", "potfile", pot.name]
                rc, aborted = self._run_proc(cmd, work, limits, sampler)

                t1 = time.time()
                end_time = now_iso()
                sampler.stop()
                samples = sampler.collect()
                all_samples.extend(samples)
                duration = round(t1 - t0, 2)
                if not aborted:
                    self.duration_history.append(duration)

                # status + folder prefix
                if aborted:
                    status = "ABORTED_EARLY"
                    prefix = "[ABORTED]-"
                    exit_code = "ABORTED_EARLY"
                elif rc != 0:
                    status = "FAILED"
                    prefix = "[FAILED]-"
                    exit_code = rc
                else:
                    status = "OK"
                    prefix = ""
                    exit_code = rc

                run_id = f"{prefix}{run_base}"
                run_out = runs_dir / run_id
                run_out.mkdir(parents=True, exist_ok=True)

                # copy + rename output files (#13)
                self._collect_outputs(work, run_out, idx)

                # convert the raw profile files to clean CSVs in the RUN folder
                self._convert_profiles(run_out)

                # append this run's probes to the session HW-STATS CSV, tagged
                # with the run_id (one file for the whole session).
                for s in samples:
                    append_csv(hw_csv, hw_fields, {"run_id": run_id, **s})

                # session summary row
                srow = {
                    "run_index": idx, "run_id": run_id, "material": mat,
                    "status": status,
                    "potential": spec["potential"] if has_pot else "none",
                    "structure": spec["structure"],
                    "configuration": spec["configuration"], "seed": seed,
                    "start_time": start_time, "end_time": end_time,
                    "duration_sec": duration, "exit_code": exit_code,
                }
                srow.update(summarize_samples(samples))
                append_csv(session_csv, session_fields, srow)
                self.log_line(f"    {status} in {fmt_hms(duration)} -> {run_id}")

                # clean this run's temp dir
                shutil.rmtree(work, ignore_errors=True)

                # ETA update
                self._update_eta(idx, total)

            # session done
            self._clear_preview_highlight()
            session_dur = round(time.time() - session_start, 2)
            self.log_line(f"SESSION SUMMARY -> {session_csv}")
            self.log_line(f"SESSION HW-STATS -> {hw_csv}")
            self._update_historical(root, session_id, session_dur, all_samples,
                                    session_csv)

            # finalize SESSION-INFO.json with end time + duration
            try:
                self._session_info["end_utc"] = now_utc_iso()
                self._session_info["duration_sec"] = session_dur
                with open(self._session_info_path, "w") as fh:
                    json.dump(self._session_info, fh, indent=2)
            except Exception:
                pass

            # tidy: remove any leftover temp dirs
            for d in temp_dirs_made:
                shutil.rmtree(d, ignore_errors=True)

            # generate outputs (report / PNGs) per the user's Step 3 choices
            want_html = bool(getattr(self, "gen_html", None) and self.gen_html.get())
            want_png = bool(getattr(self, "gen_png", None) and self.gen_png.get())
            report.generate_session_outputs(
                session_dir, session_id, session_csv, hw_csv,
                want_html, want_png, self.log_line)

            if self.stop_event.is_set():
                self.log_line("=== SESSION stopped ===")
                self._set_status("Session stopped.")
            else:
                self.log_line("=== SESSION complete ===")
                self._set_status("Session complete.")
            self.root.after(0, self._show_open_session_button)
        except Exception as e:
            self.log_line(f"ERROR during session: {e}")
        finally:
            self._close_session_logfile()
            # If the user stopped the session, tag its folder so it is obvious
            # on disk and in the previous-sessions view. Done last, after the
            # log file inside the folder is closed, so the rename can't fail on
            # an open handle. Folder contents keep the plain <id>.
            if self.stop_event.is_set():
                cur = getattr(self, "last_session_dir", None)
                if cur is not None:
                    cur = Path(cur)
                    aborted = cur.with_name(cur.name + "-[ABORTED]")
                    try:
                        if cur.is_dir() and not aborted.exists():
                            cur.rename(aborted)
                            self.last_session_dir = aborted
                            self.log_line(f"Session folder renamed -> {aborted.name}")
                    except Exception as e:
                        self.log_line(f"(could not rename aborted session folder: {e})")
            self.root.after(0, self._reset_session_buttons)

    def _run_proc(self, cmd, work, limits, sampler):
        """Run one LAMMPS process. Returns (returncode, aborted_early: bool).

        Aborts (kills) the process if skip/stop is requested, or if a resource
        limit is exceeded for several consecutive probes."""
        self.log_line("    $ " + " ".join(cmd))
        over = 0
        try:
            with open(work / "run.log", "w") as lf:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        bufsize=1, cwd=str(work))
                # reader thread so we can poll for skip/stop/limits meanwhile
                def _pump():
                    for line in proc.stdout:
                        lf.write(line)
                threading.Thread(target=_pump, daemon=True).start()

                while proc.poll() is None:
                    if self.skip_event.is_set() or self.stop_event.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                        return (proc.returncode, True)
                    # resource-limit check on the latest probe
                    s = sampler.latest()
                    if s:
                        breached = (
                            (s["cpu_usage_pct"] or 0) > limits["cpu"] or
                            (s["ram_usage_pct"] or 0) > limits["ram"] or
                            (limits["gpu"] is not None and
                             (s["gpu_usage_pct"] or 0) > limits["gpu"]))
                        over = over + 1 if breached else 0
                        if over >= 3:
                            self.log_line("    Resource limit exceeded - aborting "
                                          "run.")
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except Exception:
                                proc.kill()
                            return (proc.returncode, True)
                    time.sleep(1)
                return (proc.returncode, False)
        except FileNotFoundError as e:
            self.log_line(f"    ERROR: command not found ({e}). Is mpiexec/lmp on "
                          f"PATH?")
            return (-1, False)
        except Exception as e:
            self.log_line(f"    ERROR: {e}")
            return (-1, False)

    def _collect_outputs(self, work: Path, run_out: Path, idx: int):
        """Copy+rename the run's output files into the RUN folder (#13)."""
        for srcname, dstname in OUTPUT_RENAME.items():
            srcf = work / srcname
            if srcf.exists():
                try:
                    shutil.copy2(srcf, run_out / dstname)
                except Exception as e:
                    self.log_line(f"    WARN output {srcname}: {e}")
        # run.log -> RUN_XXX.log
        rl = work / "run.log"
        if rl.exists():
            try:
                shutil.copy2(rl, run_out / f"RUN_{idx:03d}.log")
            except Exception:
                pass

    def _convert_profiles(self, run_out: Path):
        """Convert the raw profile files in the RUN folder to clean CSVs.

        TEMP-PROFILE.txt -> TEMP-PROFILE.csv (timestep,chunk,coord,ncount,temperature)
        ELECTRON-BATH.txt -> ELECTRON-BATH.csv (t_ps,E_hot_eV,E_cold_eV)
        Raw .txt files are kept alongside the CSVs."""
        tprof = run_out / "TEMP-PROFILE.txt"
        if tprof.exists():
            try:
                fields, rows = parse_tprof(tprof)
                write_csv(run_out / "TEMP-PROFILE.csv", fields, rows)
            except Exception as e:
                self.log_line(f"    WARN convert TEMP-PROFILE: {e}")
        ebath = run_out / "ELECTRON-BATH.txt"
        if ebath.exists():
            try:
                fields, rows = parse_ebath(ebath)
                write_csv(run_out / "ELECTRON-BATH.csv", fields, rows)
            except Exception as e:
                self.log_line(f"    WARN convert ELECTRON-BATH: {e}")

    def _update_progress(self, i, total):
        self.progress.configure(value=i, maximum=max(total, 1))

    def _update_eta(self, done, total):
        if not self.duration_history:
            return
        avg = sum(self.duration_history) / len(self.duration_history)
        remaining = total - done
        eta = avg * remaining
        self._set_eta(f"Avg {fmt_hms(avg)}/run  -  est. {fmt_hms(eta)} left "
                      f"({remaining} run(s) remaining)")

    def _update_historical(self, root, session_id, session_dur, all_samples,
                           session_csv):
        # count statuses from the session CSV
        num_runs = num_failed = num_aborted = 0
        try:
            with open(session_csv) as fh:
                for row in csv.DictReader(fh):
                    num_runs += 1
                    if row["status"] == "FAILED":
                        num_failed += 1
                    elif row["status"] == "ABORTED_EARLY":
                        num_aborted += 1
        except Exception:
            pass
        avgs = summarize_samples(all_samples)
        hist_path = root / "ANALYSIS" / "HISTORICAL.csv"
        fields = ["session_id", "start_timestamp", "total_duration_sec",
                  "num_runs", "num_failed", "num_aborted",
                  "avg_duration_per_run_sec", "avg_gpu_usage_pct",
                  "avg_cpu_usage_pct", "avg_gpu_temp_c", "avg_cpu_temp_c",
                  "avg_ram_usage_pct"]
        row = {
            "session_id": session_id, "start_timestamp": session_id,
            "total_duration_sec": session_dur, "num_runs": num_runs,
            "num_failed": num_failed, "num_aborted": num_aborted,
            "avg_duration_per_run_sec": (round(session_dur / num_runs, 2)
                                         if num_runs else ""),
        }
        row.update(avgs)
        append_csv(hist_path, fields, row)
        self.log_line(f"HISTORICAL CSV -> {hist_path}")

    def _reset_session_buttons(self):
        self.run_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause")
        self.skip_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")

    def _show_open_session_button(self):
        if getattr(self, "open_session_btn", None):
            try:
                self.open_session_btn.destroy()
            except Exception:
                pass
        self.open_session_btn = self._btn(
            self.status_lbl.master, "OPEN SESSION DATA",
            self._open_session_folder, kind="primary")
        self.open_session_btn.pack(anchor="w", padx=14, pady=4)

    def _open_session_folder(self):
        path = getattr(self, "last_session_dir", None)
        if not path or not Path(path).exists():
            self.log_line("No session folder to open yet.")
            return
        self._open_in_file_manager(Path(path))

    @staticmethod
    def _open_in_file_manager(path: Path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                if shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", str(path)])
                elif shutil.which("explorer.exe"):
                    winpath = str(path)
                    if shutil.which("wslpath"):
                        try:
                            winpath = subprocess.run(
                                ["wslpath", "-w", str(path)], capture_output=True,
                                text=True, check=True).stdout.strip()
                        except Exception:
                            pass
                    subprocess.Popen(["explorer.exe", winpath])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Material profile editor (add / edit a material profile set)
# EDITOR_ROWS is imported from constants (maps each editor row to a subdir kind).
# ---------------------------------------------------------------------------
class MaterialEditor:
    """Modal-ish window to create or edit one material profile set.

    Same view for both: when editing, the name/description and the three file
    lists are pre-filled from the existing material folder; when adding, they
    start empty. Files are gathered from anywhere on the filesystem via the
    ADD ASSETS dialog (repeatable, multi-select) and shown as removable chips.

    On save, the material folder is written under materials_root with the three
    POTENTIAL/STRUCTURE/CONFIGURATION subdirs and a MATERIAL-INFO.json. "Save +
    Contribute" additionally commits the folder (prefixed [UNTESTED]-) on its
    own branch and opens a pull request.
    """

    def __init__(self, app, materials_root: Path, existing_name=None,
                 existing_dir=None):
        self.app = app
        # The materials root is always ROOT/SIMULATION/MATERIALS. Derive it from
        # the repo rather than trusting a caller-supplied value, which can be an
        # empty StringVar if the editor is opened before Step 2 has populated it
        # (that previously wrote new materials into the current directory).
        mr = Path(materials_root) if materials_root else None
        if not mr or str(mr) in ("", "."):
            mr = repo_root() / "SIMULATION" / "MATERIALS"
        mr.mkdir(parents=True, exist_ok=True)
        self.materials_root = mr
        self.existing_name = existing_name
        self.existing_dir = Path(existing_dir) if existing_dir else None
        # kind -> list[str] of absolute source paths chosen for that row
        self.picked = {kind: [] for kind, _ in EDITOR_ROWS}
        self._chip_frames = {}

        self.win = tk.Toplevel(app.root)
        self.win.title("Edit material profile" if existing_name
                       else "Add material profile")
        self.win.configure(bg=COL_BG)
        self.win.geometry("720x640")
        self.win.transient(app.root)

        self.name_var = tk.StringVar()
        self.desc_var = tk.StringVar()
        self.contribute_var = tk.BooleanVar(value=False)

        self._build_ui()
        if existing_name and self.existing_dir:
            self._prefill_from_existing()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        # top: name + description
        top = tk.Frame(self.win, bg=COL_BG)
        top.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(top, text="Material name", bg=COL_BG, fg=COL_TEXT,
                 font=FONT_BOLD, width=16, anchor="w").grid(row=0, column=0,
                                                            sticky="w", pady=3)
        tk.Entry(top, textvariable=self.name_var, width=46, bg=COL_ENTRY,
                 fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat").grid(
            row=0, column=1, sticky="w", padx=6)
        tk.Label(top, text="(unique, descriptive)", bg=COL_BG, fg=COL_MUTED,
                 font=FONT).grid(row=0, column=2, sticky="w")
        tk.Label(top, text="Description", bg=COL_BG, fg=COL_TEXT, font=FONT,
                 width=16, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        tk.Entry(top, textvariable=self.desc_var, width=46, bg=COL_ENTRY,
                 fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat").grid(
            row=1, column=1, sticky="w", padx=6)
        tk.Label(top, text="(optional)", bg=COL_BG, fg=COL_MUTED,
                 font=FONT).grid(row=1, column=2, sticky="w")

        # three asset rows live in a scrollable middle area so the top fields
        # and the bottom actions stay put no matter how many chips are added.
        mid = tk.Frame(self.win, bg=COL_BG)
        mid.pack(fill="both", expand=True, padx=14, pady=4)
        ecanvas = tk.Canvas(mid, bg=COL_BG, highlightthickness=0)
        esb = ttk.Scrollbar(mid, orient="vertical", command=ecanvas.yview)
        body = tk.Frame(ecanvas, bg=COL_BG)
        ewin = ecanvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: ecanvas.configure(scrollregion=ecanvas.bbox("all")))
        ecanvas.bind("<Configure>",
                     lambda e: ecanvas.itemconfigure(ewin, width=e.width))
        ecanvas.configure(yscrollcommand=esb.set)
        ecanvas.pack(side="left", fill="both", expand=True)
        esb.pack(side="right", fill="y")
        # wheel scrolling while the pointer is over the editor's asset area
        def _ewheel(e):
            if getattr(e, "num", None) == 4:
                d = -1
            elif getattr(e, "num", None) == 5:
                d = 1
            else:
                d = -1 if getattr(e, "delta", 0) > 0 else 1
            if App._pointer_in(ecanvas):
                ecanvas.yview_scroll(d, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.win.bind(seq, _ewheel)

        for kind, label in EDITOR_ROWS:
            block = tk.LabelFrame(body, text=label, bg=COL_BG, fg=COL_LAVA_HOT,
                                  font=FONT_BOLD)
            block.pack(fill="x", pady=6)
            head = tk.Frame(block, bg=COL_BG)
            head.pack(fill="x", padx=6, pady=4)
            self.app._btn(head, "ADD ASSETS",
                          lambda k=kind: self._add_assets(k),
                          kind="primary").pack(side="left")
            tk.Label(head, text="  browse anywhere; select several at once, or "
                     "click again to add more", bg=COL_BG, fg=COL_MUTED,
                     font=FONT).pack(side="left")
            chips = tk.Frame(block, bg=COL_PANEL)
            chips.pack(fill="x", padx=6, pady=(0, 6))
            self._chip_frames[kind] = chips
            self._render_chips(kind)

        # contribute + actions (fixed at the bottom, never scrolls away)
        bottom = tk.Frame(self.win, bg=COL_BG)
        bottom.pack(fill="x", side="bottom", padx=14, pady=(4, 12))
        tk.Checkbutton(bottom, text="Also contribute this profile to the shared "
                       "repository (opens a pull request; marked UNTESTED until "
                       "CI validates it)", variable=self.contribute_var,
                       bg=COL_BG, fg=COL_TEXT, selectcolor=COL_ENTRY,
                       activebackground=COL_BG, activeforeground=COL_TEXT,
                       font=FONT, wraplength=660, justify="left",
                       anchor="w").pack(anchor="w", pady=(0, 6))
        actions = tk.Frame(bottom, bg=COL_BG)
        actions.pack(fill="x")
        self.app._btn(actions, "Cancel", self.win.destroy,
                      kind="ghost").pack(side="left")
        self.save_btn = self.app._btn(actions, "Save", self._save,
                                      kind="primary")
        self.save_btn.pack(side="right")

    def _render_chips(self, kind):
        frame = self._chip_frames[kind]
        for w in frame.winfo_children():
            w.destroy()
        if not self.picked[kind]:
            tk.Label(frame, text="(no files added yet)", bg=COL_PANEL,
                     fg=COL_MUTED, font=FONT).pack(anchor="w", padx=4, pady=2)
            return
        for path in list(self.picked[kind]):
            chip = tk.Frame(frame, bg=COL_ENTRY)
            chip.pack(side="top", anchor="w", fill="x", pady=1)
            bad = not looks_like_input(Path(path), kind)
            tk.Label(chip, text=Path(path).name, bg=COL_ENTRY,
                     fg=COL_WARN if bad else COL_TEXT, font=FONT).pack(
                side="left", padx=(6, 4))
            if bad:
                tk.Label(chip, text="(unexpected type)", bg=COL_ENTRY,
                         fg=COL_WARN, font=FONT).pack(side="left")
            tk.Button(chip, text="\u2715", command=lambda p=path, k=kind:
                      self._remove_asset(k, p), bg=COL_ENTRY, fg=COL_ERR,
                      relief="flat", bd=0, font=FONT_BOLD, cursor="hand2").pack(
                side="right", padx=6)

    # -- asset add/remove --------------------------------------------------
    def _add_assets(self, kind):
        paths = filedialog.askopenfilenames(
            parent=self.win,
            title=f"Add {dict(EDITOR_ROWS)[kind]}")
        if not paths:
            return
        for p in paths:
            if p not in self.picked[kind]:
                self.picked[kind].append(p)
        self._render_chips(kind)

    def _remove_asset(self, kind, path):
        try:
            self.picked[kind].remove(path)
        except ValueError:
            pass
        self._render_chips(kind)

    def _prefill_from_existing(self):
        info = read_material_info(self.existing_dir)
        self.name_var.set(display_material_name(self.existing_name))
        self.desc_var.set(info.get("description", ""))
        for kind, _ in EDITOR_ROWS:
            for fp in list_material_inputs(self.existing_dir, kind):
                self.picked[kind].append(str(fp))
            self._render_chips(kind)

    # -- validation + save -------------------------------------------------
    def _validate(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Missing name",
                                   "Give the material a unique name.",
                                   parent=self.win)
            return None
        if not self.picked["configs"] or not self.picked["structures"]:
            messagebox.showwarning(
                "Missing files",
                "A material needs at least one CONFIGURATION/INPUT file and one "
                "STRUCTURE file. Potentials are optional.", parent=self.win)
            return None
        # extension check across all rows
        bad = []
        for kind, _ in EDITOR_ROWS:
            for p in self.picked[kind]:
                if not looks_like_input(Path(p), kind):
                    bad.append(Path(p).name)
        if bad:
            listed = ", ".join(bad[:8]) + (" ..." if len(bad) > 8 else "")
            if not messagebox.askyesno(
                    "Unrecognized file types",
                    f"These files don't match the known extensions for their "
                    f"category:\n\n{listed}\n\nThey may not be valid LAMMPS "
                    f"inputs, and this session may not work. Continue anyway?",
                    parent=self.win):
                return None
        return name

    def _save(self):
        name = self._validate()
        if name is None:
            return
        contribute = self.contribute_var.get()
        try:
            dest_dir = self._write_material(name, tested=not contribute)
        except Exception as e:
            messagebox.showerror("Save failed", str(e), parent=self.win)
            return
        self.app.log_line(f"Saved material profile -> {dest_dir}")
        if contribute:
            # run the git flow off the UI thread; it may be slow or fail on a
            # bad connection, and must never block or crash the GUI.
            threading.Thread(target=self._contribute_worker,
                             args=(name, dest_dir), daemon=True).start()
        self.win.destroy()
        self.app._populate_materials()

    def _write_material(self, name, tested):
        """Create/refresh the material folder under materials_root.

        Contributed (untested) profiles get the [UNTESTED]- name prefix so the
        rest of the app and CI can recognize them. Returns the folder path."""
        folder = name if tested else f"{UNTESTED_PREFIX}{name}"
        dest = self.materials_root / folder

        # If editing an existing folder whose name is unchanged, write in place;
        # otherwise create the new folder fresh.
        dest.mkdir(parents=True, exist_ok=True)
        for kind, _ in EDITOR_ROWS:
            sub = dest / MATERIAL_SUBDIRS[kind]
            sub.mkdir(parents=True, exist_ok=True)
            wanted = {Path(p).name for p in self.picked[kind]}
            # remove files that were deleted in the editor
            for existing in list(sub.iterdir()) if sub.is_dir() else []:
                if existing.is_file() and existing.name not in wanted:
                    try:
                        existing.unlink()
                    except Exception:
                        pass
            # copy in the chosen files (skip self-copies)
            for p in self.picked[kind]:
                srcf = Path(p)
                dstf = sub / srcf.name
                try:
                    if srcf.resolve() != dstf.resolve():
                        shutil.copy2(srcf, dstf)
                except Exception as e:
                    self.app.log_line(f"    WARN copy {srcf.name}: {e}")
        write_material_info(dest, {
            "name": name,
            "description": self.desc_var.get().strip(),
            "tested": tested,
            "contributed_utc": now_utc_iso() if not tested else None,
        })
        return dest

    # -- contribute (git branch + push + PR) -------------------------------
    def _contribute_worker(self, name, dest_dir: Path):
        """Contribute the saved material as a new branch + PR WITHOUT disturbing
        the user's working directory or current branch.

        The material folder was already written into the user's working tree by
        _write_material and must stay there (visible and runnable locally). So
        instead of switching branches in place - which would make the folder's
        on-disk presence depend on the checked-out branch and can make it vanish
        - we create a temporary detached `git worktree`, copy the material into
        it, commit + push from there, then remove the worktree. The user's real
        working tree is never touched.
        """
        root = repo_root()
        slug = slugify(name)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        branch = f"material/{slug}-{stamp}"
        log = self.app.log_line

        rel = None
        try:
            if dest_dir.is_relative_to(root):
                rel = dest_dir.relative_to(root)
        except Exception:
            rel = None

        def git(args, cwd=root, check=True):
            return subprocess.run(["git", "-C", str(cwd), *args],
                                  capture_output=True, text=True, check=check)

        log(f"--- Contributing '{name}' as branch {branch} ---")
        if not (root / ".git").exists():
            log("    Not a git repository - the profile is saved locally only.")
            return
        if rel is None:
            log("    Material is outside the repo - saved locally, not "
                "contributed.")
            return

        wt_dir = root / ".git" / "lava-contrib" / slug
        created_wt = False
        try:
            # temporary worktree on a fresh branch off current HEAD
            wt_dir.parent.mkdir(parents=True, exist_ok=True)
            if wt_dir.exists():
                shutil.rmtree(wt_dir, ignore_errors=True)
            git(["worktree", "add", "-b", branch, str(wt_dir), "HEAD"])
            created_wt = True

            # copy the material folder into the worktree at the same rel path
            target = wt_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(dest_dir, target)

            git(["add", str(rel)], cwd=wt_dir)
            git(["commit", "-m", f"Add UNTESTED community material profile: {name}"],
                cwd=wt_dir)
            log("    Committed. Pushing (this may take a moment on a slow "
                "connection)...")
            push = git(["push", "-u", "origin", branch], cwd=wt_dir, check=False)
            if push.returncode != 0:
                log("    Push failed - the profile is saved locally and the "
                    "branch is committed; you may lack push access or network.")
                log(f"    git push: {push.stderr.strip()[:400]}")
                return
            log("    Pushed. Opening a pull request...")
            self._open_pull_request(root, branch, name)
        except subprocess.CalledProcessError as e:
            log(f"    git error: {(e.stderr or str(e)).strip()[:400]} "
                f"(profile is still saved locally)")
        except Exception as e:
            log(f"    Contribution failed: {e} (profile is still saved locally)")
        finally:
            # always remove the temporary worktree; never touches the user's tree
            if created_wt:
                try:
                    git(["worktree", "remove", "--force", str(wt_dir)], check=False)
                except Exception:
                    pass
            shutil.rmtree(wt_dir, ignore_errors=True)
            # remove the parent holder dir if it's now empty
            try:
                wt_dir.parent.rmdir()
            except Exception:
                pass

    def _open_pull_request(self, root: Path, branch: str, name: str):
        """Open a PR via the gh CLI if available/authenticated; otherwise open
        the GitHub 'compare' page in a browser for the user to click through."""
        log = self.app.log_line
        title = f"[UNTESTED] Community material profile: {name}"
        body = ("Automated contribution from LAVA. This material profile has "
                "not been validated. CI should run every combination and only "
                "allow merge if all runs succeed.")
        if shutil.which("gh"):
            pr = subprocess.run(
                ["gh", "pr", "create", "--head", branch, "--title", title,
                 "--body", body],
                cwd=str(root), capture_output=True, text=True)
            if pr.returncode == 0:
                log(f"    Pull request opened: {pr.stdout.strip()}")
                return
            log("    gh could not open the PR automatically; falling back to "
                "the browser.")
        # browser fallback: build a compare URL from origin
        url = self._compare_url(root, branch)
        if url:
            log(f"    Open this URL to finish the pull request:\n    {url}")
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        else:
            log("    Branch pushed. Open a pull request for it on GitHub "
                "manually.")

    @staticmethod
    def _compare_url(root: Path, branch: str):
        try:
            r = subprocess.run(["git", "-C", str(root), "remote", "get-url",
                                "origin"], capture_output=True, text=True,
                               check=True)
            remote = r.stdout.strip()
            # normalize git@github.com:owner/repo.git and https forms
            m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", remote)
            if not m:
                return None
            owner, repo = m.group(1), m.group(2)
            return (f"https://github.com/{owner}/{repo}/compare/"
                    f"{branch}?expand=1")
        except Exception:
            return None


def _register_bundled_font_early(log=lambda *a: None):
    """Copy the bundled Roboto Mono TTFs into the user font dir and refresh the
    fontconfig cache BEFORE any Tk root exists, so the font is visible on the
    very first launch (Tk reads the family list once at startup). Linux/WSL;
    best-effort and silent on failure. Returns True if it copied anything new."""
    try:
        src_dir = Path(__file__).resolve().parent / "assets" / "fonts"
        ttfs = sorted(src_dir.glob("*.ttf")) if src_dir.is_dir() else []
        if not ttfs:
            return False
        dest = Path.home() / ".local" / "share" / "fonts" / "LAVA"
        dest.mkdir(parents=True, exist_ok=True)
        copied = False
        for f in ttfs:
            target = dest / f.name
            if not target.exists():
                shutil.copy2(f, target)
                copied = True
        if copied and shutil.which("fc-cache"):
            subprocess.run(["fc-cache", "-f", str(dest)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
        return copied
    except Exception:
        return False


def main():
    # Register the bundled font before the Tk root exists so it applies on the
    # first launch, not the second.
    _register_bundled_font_early()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
