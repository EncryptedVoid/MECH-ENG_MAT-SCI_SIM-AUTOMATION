#!/usr/bin/env python3
"""
LAVA - LAMMPS Automation Validation Aid
=======================================
Step-by-step wizard to set up and run LAMMPS simulation sessions.

Project developed in conjunction with BUET Mechanical Engineering student
"Kazi Rubaiyat Mustafix" and University of the People Computer Science student
"Ashiq Arib Gazi".

Pipelines:
  1. Selection Pipeline     - choose specific file(s) to run in combination
                              without generating new files.
  2. Auto-Generate Pipeline - (not built yet) using potential files,
                              auto-generate all combinations and validate sets.

All output data is CSV except config.json.
Launch:  python3 gui.py
"""

import os
import platform
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

# ---------------------------------------------------------------------------
# Version / branding
# ---------------------------------------------------------------------------
VERSION = "0.4.0"
APP_NAME = "LAVA"
APP_SUBTITLE = "LAMMPS AUTOMATION VALIDATION AID"
APP_CREDIT = ('Project developed in conjunction with BUET Mechanical Engineering '
              'student "Kazi Rubaiyat Mustafix" and University of the People '
              'Computer Science student "Ashiq Arib Gazi"')
FOOTER = f"Made by ASHIQ GAZI | Ashiq.live | VERSION {VERSION}"
LOGO_FILE = "LOGO.webp"

# Volcanic / lava palette
COL_BG = "#1a1210"
COL_PANEL = "#241a17"
COL_TEXT = "#f3e9e3"
COL_MUTED = "#b08a7a"
COL_LAVA = "#e2510f"
COL_LAVA_HOT = "#ff7a1a"
COL_EMBER = "#c1272d"
COL_OK = "#3fbf6f"
COL_WARN = "#f0b429"
COL_ERR = "#ff5a4d"
COL_ENTRY = "#31231f"

FONT = ("TkDefaultFont", 10)
FONT_BOLD = ("TkDefaultFont", 10, "bold")
FONT_MONO = ("TkFixedFont", 9)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BUILD_SCRIPT = "setup.sh"
LAMMPS_BUILD_ROOT = Path.home() / ".lammps-build"
LAMMPS_BIN = LAMMPS_BUILD_ROOT / "build" / "lmp"
CUDA_MIN = "12.8"
CONFIG_NAME = "config.json"

PROJECT_TREE = {
    "ANALYSIS": ["SESSIONS"],
    "AUTOMATION": [],
    "LOGS": [],
    "PROJECT": [],
    "SIMULATION": ["MATERIALS"],
    "TEMP": [],
}
MATERIAL_SUBDIRS = {
    "potentials": "POTENTIAL-FILES",
    "configs": "CONFIGURATION-FILES",
    "structures": "STRUCTURE-FILES",
}
INPUT_EXT = {
    "potentials": {".sw", ".txt", ".meam", ".eam", ".alloy", ".pot", ".tersoff"},
    "configs": {"", ".in", ".lmp", ".txt"},
    "structures": {".data", ".lmp", ".xyz", ".dat"},
}
# run output file -> renamed name in RUN folder (run.log handled separately)
OUTPUT_RENAME = {
    "T_profile.dat": "TEMP-PROFILE.txt",
    "ebath.dat": "ELECTRON-BATH.txt",
    "log.lammps": "LAMMPS.log",
}

DEFAULT_PROBE_SEC = 60
DEFAULT_MAX_CPU = 100
DEFAULT_MAX_RAM = 90
DEFAULT_MAX_GPU = 100

# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")

def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d%H%M%S")

def stamp_of(ts) -> str:
    return ts.strftime("%Y%m%d%H%M%S")

def script_dir() -> Path:
    return Path(__file__).resolve().parent

def parse_seeds(spec: str):
    """Parse 'X,Y,A-B,Z' -> sorted unique ints. Raises ValueError on bad tokens."""
    if not spec or not spec.strip():
        return []
    out = set()
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.update(range(a, b + 1))
        elif re.fullmatch(r"\d+", tok):
            out.add(int(tok))
        else:
            raise ValueError(f"invalid seed token: {tok!r}")
    return sorted(out)

def have_nvidia_smi() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    try:
        subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def have_psutil() -> bool:
    try:
        import psutil  # noqa
        return True
    except Exception:
        return False

def looks_like_input(path: Path, kind: str) -> bool:
    name = path.name
    if name.startswith(".") or name == "__init__.py":
        return False
    if not path.is_file():
        return False
    if kind == "configs" and name.startswith("in."):
        return True
    return path.suffix.lower() in INPUT_EXT.get(kind, set())

def list_material_inputs(material_dir: Path, kind: str):
    sub = material_dir / MATERIAL_SUBDIRS[kind]
    if not sub.is_dir():
        return []
    return [p for p in sorted(sub.iterdir()) if looks_like_input(p, kind)]

def discover_materials(root: Path):
    out = {}
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        # A material folder is one that has configs AND structures. Potentials
        # are optional (some materials run without a potential file).
        if ((d / MATERIAL_SUBDIRS["configs"]).is_dir() and
                (d / MATERIAL_SUBDIRS["structures"]).is_dir()):
            out[d.name] = d
    return out

def write_csv(path: Path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})

def append_csv(path: Path, fieldnames, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames})

def _avg(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None

def summarize_samples(samples):
    return {
        "avg_gpu_usage_pct": _avg([s["gpu_usage_pct"] for s in samples]),
        "avg_cpu_usage_pct": _avg([s["cpu_usage_pct"] for s in samples]),
        "avg_gpu_temp_c": _avg([s["gpu_temp_c"] for s in samples]),
        "avg_cpu_temp_c": _avg([s["cpu_temp_c"] for s in samples]),
        "avg_ram_usage_pct": _avg([s["ram_usage_pct"] for s in samples]),
    }


def now_utc_iso() -> str:
    """Current time as a UTC ISO-8601 string with a Z suffix."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_hardware() -> dict:
    """Best-effort machine specs via psutil / nvidia-smi / platform.

    Any field that can't be determined is left blank/None rather than raising.
    """
    info = {
        "cpu_model": "", "cpu_cores_physical": None, "cpu_cores_logical": None,
        "ram_total_gb": None, "gpu_model": "", "gpu_vram_mb": None,
        "platform": "", "python": "",
    }
    try:
        info["platform"] = platform.platform()
        info["python"] = platform.python_version()
    except Exception:
        pass
    # CPU model name
    try:
        if os.path.exists("/proc/cpuinfo"):
            for line in open("/proc/cpuinfo"):
                if line.lower().startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    if not info["cpu_model"]:
        try:
            info["cpu_model"] = platform.processor() or platform.machine()
        except Exception:
            pass
    # cores + RAM
    try:
        import psutil
        info["cpu_cores_physical"] = psutil.cpu_count(logical=False)
        info["cpu_cores_logical"] = psutil.cpu_count(logical=True)
        info["ram_total_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    except Exception:
        pass
    # GPU model + VRAM
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        if out:
            first = out.splitlines()[0].split(",")
            info["gpu_model"] = first[0].strip()
            try:
                info["gpu_vram_mb"] = int(float(first[1].strip()))
            except Exception:
                pass
    except Exception:
        pass
    return info


def parse_ebath(path: Path):
    """Parse ebath.dat / ELECTRON-BATH.txt.

    Format: header line 't_ps E_hot_eV E_cold_eV' then whitespace-separated
    numeric rows. Returns (fieldnames, list[dict])."""
    rows = []
    try:
        with open(path) as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except Exception:
        return (["t_ps", "E_hot_eV", "E_cold_eV"], [])
    if not lines:
        return (["t_ps", "E_hot_eV", "E_cold_eV"], [])
    header = lines[0].split()
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) != len(header):
            continue
        try:
            rows.append({header[i]: float(parts[i]) for i in range(len(header))})
        except ValueError:
            continue
    return (header, rows)


def parse_tprof(path: Path):
    """Parse T_profile.dat / TEMP-PROFILE.txt (LAMMPS ave/chunk).

    Repeating blocks: a '<timestep> <nchunks> <total>' line followed by
    <nchunks> rows of '<chunk> <coord> <ncount> <v_tatom>'. Comment lines
    start with '#'. Empty bins (ncount==0) get temperature '' (no data).
    Returns (fieldnames, list[dict])."""
    out = []
    try:
        with open(path) as fh:
            raw = [ln.rstrip("\r\n") for ln in fh]
    except Exception:
        return (["timestep", "chunk", "coord", "ncount", "temperature"], [])
    cur_ts = None
    expect = got = 0
    for ln in raw:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if cur_ts is None and len(parts) == 3:
            try:
                cur_ts = int(float(parts[0]))
                expect = int(parts[1])
                got = 0
            except ValueError:
                cur_ts = None
            continue
        if cur_ts is not None and len(parts) == 4:
            try:
                chunk = int(parts[0])
                coord = float(parts[1])
                ncount = float(parts[2])
                temp = float(parts[3])
            except ValueError:
                continue
            out.append({
                "timestep": cur_ts, "chunk": chunk, "coord": coord,
                "ncount": ncount,
                "temperature": ("" if ncount == 0 else temp),
            })
            got += 1
            if got >= expect:
                cur_ts = None
    return (["timestep", "chunk", "coord", "ncount", "temperature"], out)

def fmt_hms(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Metrics sampler (configurable probe interval)
# ---------------------------------------------------------------------------
class MetricsSampler:
    """Samples CPU/RAM (psutil) and GPU (nvidia-smi) on a background thread."""

    def __init__(self, interval=DEFAULT_PROBE_SEC):
        import psutil
        self._psutil = psutil
        self.interval = max(1, int(interval))
        self._samples = []
        self._stop = threading.Event()
        self._thread = None
        self._latest = None   # most recent sample, for live limit checks

    def _probe_gpu(self):
        try:
            out = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=True).stdout.strip()
            utils, temps = [], []
            for line in out.splitlines():
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 2:
                    try:
                        utils.append(float(parts[0]))
                        temps.append(float(parts[1]))
                    except ValueError:
                        pass
            if utils:
                return (sum(utils) / len(utils), sum(temps) / len(temps))
        except Exception:
            pass
        return (None, None)

    def _cpu_temp(self):
        try:
            temps = self._psutil.sensors_temperatures()
        except Exception:
            return None
        if not temps:
            return None
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
            if key in temps and temps[key]:
                vals = [t.current for t in temps[key] if t.current is not None]
                if vals:
                    return sum(vals) / len(vals)
        allv = [t.current for arr in temps.values() for t in arr if t.current is not None]
        return sum(allv) / len(allv) if allv else None

    def _sample_once(self):
        cpu = self._psutil.cpu_percent(interval=None)
        ram = self._psutil.virtual_memory().percent
        gpu_util, gpu_temp = self._probe_gpu()
        s = {
            "timestamp": now_iso(),
            "gpu_usage_pct": gpu_util,
            "cpu_usage_pct": cpu,
            "gpu_temp_c": gpu_temp,
            "cpu_temp_c": self._cpu_temp(),
            "ram_usage_pct": ram,
        }
        self._latest = s
        return s

    def _loop(self):
        self._psutil.cpu_percent(interval=None)
        self._samples.append(self._sample_once())
        while not self._stop.wait(self.interval):
            self._samples.append(self._sample_once())

    def start(self):
        self._stop.clear()
        self._samples = []
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def latest(self):
        return self._latest

    def collect(self):
        return list(self._samples)


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

    def _label(self, parent, text, fg=COL_TEXT, font=FONT, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=fg, font=font, **kw)

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

        # body container (each step frame packs in here)
        self.container = tk.Frame(self.root, bg=COL_BG)
        self.container.pack(fill="both", expand=True)

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

    def _load_logo(self, parent):
        """Load a logo next to this script. Tries LOGO.webp/.png/.jpg/.jpeg.
        PNG/GIF load natively in tk; JPEG/WEBP use Pillow if available.
        Falls back to a lava glyph if nothing loads."""
        base = script_dir()
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
        base = script_dir()
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

    def _new_step(self):
        f = tk.Frame(self.container, bg=COL_BG)
        f.pack(fill="both", expand=True)
        return f

    def _step_header(self, parent, step_no, title, subtitle=""):
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
    # STEP 0 - choose pipeline
    # ======================================================================
    def show_step0(self):
        self._clear_container()
        f = self._new_step()
        self._step_header(f, 0, "Choose a pipeline",
                          "Pick which pipeline to run.")

        # credit line
        tk.Label(f, text=APP_CREDIT, bg=COL_BG, fg=COL_MUTED, font=FONT,
                 wraplength=840, justify="left").pack(anchor="w", padx=14,
                                                      pady=(0, 10))

        cards = tk.Frame(f, bg=COL_BG)
        cards.pack(anchor="w", padx=14, pady=6, fill="x")

        pipelines = [
            (1, "Selection Pipeline",
             "Choose the specific file(s) that should be run in combination "
             "without generating new files.", True),
            (2, "Auto-Generate Pipeline",
             "Using potential files, auto-generate all combinations and "
             "validate sets.", False),
            (3, "Headless Run",
             "Run specifically chosen combinations headless for remote "
             "execution.", False),
        ]
        for num, name, desc, ready in pipelines:
            card = tk.Frame(cards, bg=COL_PANEL, bd=0, highlightthickness=1,
                            highlightbackground=COL_LAVA if ready else "#3a2a24")
            card.pack(fill="x", pady=5)
            inner = tk.Frame(card, bg=COL_PANEL)
            inner.pack(fill="x", padx=12, pady=10)
            top = tk.Frame(inner, bg=COL_PANEL)
            top.pack(fill="x")
            tk.Label(top, text=f"PIPELINE {num}", bg=COL_PANEL, fg=COL_LAVA,
                     font=("TkDefaultFont", 9, "bold")).pack(side="left")
            if not ready:
                tk.Label(top, text="  \u2014 coming soon", bg=COL_PANEL,
                         fg=COL_MUTED, font=FONT).pack(side="left")
            tk.Label(inner, text=name, bg=COL_PANEL, fg=COL_TEXT,
                     font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
            tk.Label(inner, text=desc, bg=COL_PANEL, fg=COL_MUTED, font=FONT,
                     wraplength=760, justify="left").pack(anchor="w", pady=(2, 6))
            btn = self._btn(inner, f"Use Pipeline {num}",
                            (lambda n=num: self._pick_pipeline(n)),
                            kind="primary" if ready else "ghost")
            btn.pack(anchor="w")

    def _pick_pipeline(self, num):
        if num == 1:
            self.pipeline = 1
            self.log_line("Selection Pipeline selected.")
            self.show_step1()
        elif num == 2:
            messagebox.showinfo("Coming soon",
                                "The Auto-Generate Pipeline is not built yet.\n\n"
                                "Please use the Selection Pipeline for now.")
        elif num == 3:
            messagebox.showinfo("Coming soon",
                                "Headless Run is not built yet.\n\n"
                                "It will support running pre-chosen combinations "
                                "headless via:\n"
                                "  python3 gui.py --pipeline=3 "
                                "--session-id=combination.headless.json\n\n"
                                "Pause / resume / skip / stop are not available in "
                                "headless mode by design.")

    # ======================================================================
    # STEP 1 - set up LAMMPS (build via script, sudo password from GUI)
    # ======================================================================
    def show_step1(self):
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
        self._btn(bar, "Back", self.show_step0, kind="ghost").grid(row=0, column=2,
                                                                   padx=8)
        self.step1_next = self._btn(bar, "Next  \u2192", self.show_step2,
                                    kind="primary")
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
    # STEP 2 - project root, material sources, seeds, per-material file select
    # ======================================================================
    def show_step2(self):
        self._clear_container()
        f = self._new_step()
        self._step_header(
            f, 2, "Set up the project",
            "Choose a project root and a materials source folder. Each material "
            "is its own subfolder (e.g. Zinc-Oxide/POTENTIAL-FILES, "
            "CONFIGURATION-FILES, STRUCTURE-FILES). Tick the files to include per "
            "material - untick any you don't want. Set seeds as X,Y,A-B,Z. "
            "Selected files are copied into the project tree and saved to "
            "config.json.")

        top = tk.Frame(f, bg=COL_BG)
        top.pack(anchor="w", padx=14, fill="x")

        def _row(r, label, var, cmd):
            tk.Label(top, text=label, bg=COL_BG, fg=COL_TEXT, font=FONT,
                     width=16, anchor="w").grid(row=r, column=0, sticky="w", pady=4)
            tk.Entry(top, textvariable=var, width=54, bg=COL_ENTRY, fg=COL_TEXT,
                     insertbackground=COL_TEXT, relief="flat").grid(
                row=r, column=1, padx=6)
            self._btn(top, "Browse", cmd).grid(row=r, column=2)

        _row(0, "Project root", self.project_root, self._pick_project_root)
        _row(1, "Materials source", self.materials_src, self._pick_materials_src)
        _row(2, "Temp folder", self.temp_dir, self._pick_temp_dir)
        tk.Label(top, text="(temp optional - defaults to <root>/TEMP)", bg=COL_BG,
                 fg=COL_MUTED, font=FONT).grid(row=3, column=1, sticky="w")

        tk.Label(top, text="Seeds", bg=COL_BG, fg=COL_TEXT, font=FONT, width=16,
                 anchor="w").grid(row=4, column=0, sticky="w", pady=4)
        tk.Entry(top, textvariable=self.seed_spec, width=54, bg=COL_ENTRY,
                 fg=COL_TEXT, insertbackground=COL_TEXT, relief="flat").grid(
            row=4, column=1, padx=6)
        tk.Label(top, text="e.g. 1,3,5-8", bg=COL_BG, fg=COL_MUTED,
                 font=FONT).grid(row=4, column=2, sticky="w")

        # scrollable area for per-material file checkboxes
        tk.Label(f, text="Materials & files", bg=COL_BG, fg=COL_LAVA_HOT,
                 font=FONT_BOLD).pack(anchor="w", padx=14, pady=(10, 2))

        # Pin the action bar to the BOTTOM first, so the expanding material
        # area below can never push it off-screen (this was the overflow bug).
        bar = tk.Frame(f, bg=COL_BG)
        bar.pack(side="bottom", anchor="w", padx=14, pady=10, fill="x")
        self._btn(bar, "Back", self.show_step1, kind="ghost").grid(
            row=0, column=0, padx=(0, 8))
        self.build_project_btn = self._btn(
            bar, "Build project + save config", self._build_project, kind="primary")
        self.build_project_btn.grid(row=0, column=1, padx=8)

        # The material area fills whatever space is left between the fields above
        # and the pinned bar below, and scrolls internally if it overflows.
        self.mat_area = tk.Frame(f, bg=COL_PANEL)
        self.mat_area.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self._mat_placeholder = tk.Label(
            self.mat_area, text="Choose a materials source folder to list "
            "materials and files.", bg=COL_PANEL, fg=COL_MUTED, font=FONT)
        self._mat_placeholder.pack(anchor="w", padx=10, pady=10)

        # holds {material: {kind: {filename: BooleanVar}}}
        self.file_vars = {}

        # if the materials source is already set, populate now
        if self.materials_src.get().strip():
            self._populate_materials()

    def _pick_project_root(self):
        d = filedialog.askdirectory(title="Choose PROJECT ROOT")
        if d:
            self.project_root.set(d)
            cfg = Path(d) / "PROJECT" / CONFIG_NAME
            if cfg.exists() and messagebox.askyesno(
                    "Project already set up",
                    "This project already has a config.json.\n\nSkip setup and go "
                    "straight to running?"):
                self.load_config_and_run(cfg)

    def _pick_materials_src(self):
        d = filedialog.askdirectory(title="Choose materials source folder")
        if d:
            self.materials_src.set(d)
            self._populate_materials()

    def _pick_temp_dir(self):
        d = filedialog.askdirectory(title="Choose TEMP folder")
        if d:
            self.temp_dir.set(d)

    def _populate_materials(self):
        for w in self.mat_area.winfo_children():
            w.destroy()
        self.file_vars = {}
        src = Path(self.materials_src.get().strip())
        materials = discover_materials(src)
        if not materials:
            tk.Label(self.mat_area,
                     text="No materials found. Expected subfolders each "
                     "containing POTENTIAL-FILES / CONFIGURATION-FILES / "
                     "STRUCTURE-FILES.", bg=COL_PANEL, fg=COL_ERR,
                     font=FONT).pack(anchor="w", padx=10, pady=10)
            return

        canvas = tk.Canvas(self.mat_area, bg=COL_PANEL, highlightthickness=0)
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

        # mousewheel scrolling while the pointer is over the list
        def _wheel(e):
            delta = -1 if getattr(e, "delta", 0) > 0 else 1
            if getattr(e, "num", None) == 4:
                delta = -1
            elif getattr(e, "num", None) == 5:
                delta = 1
            canvas.yview_scroll(delta, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, lambda e: _wheel(e)
                            if self._pointer_in(canvas) else None)

        for mat, mdir in materials.items():
            self.file_vars[mat] = {}
            block = tk.Frame(inner, bg=COL_PANEL)
            block.pack(fill="x", anchor="w", pady=(6, 2))
            tk.Label(block, text=mat, bg=COL_PANEL, fg=COL_LAVA_HOT,
                     font=FONT_BOLD).pack(anchor="w")
            for kind in ("potentials", "configs", "structures"):
                files = list_material_inputs(mdir, kind)
                self.file_vars[mat][kind] = {}
                if not files:
                    if kind == "potentials":
                        tk.Label(block, text="   potentials: (none - runs will "
                                 "execute without a potential file)",
                                 bg=COL_PANEL, fg=COL_MUTED, font=FONT).pack(
                            anchor="w")
                    else:
                        tk.Label(block, text=f"   {kind}: (none found - required)",
                                 bg=COL_PANEL, fg=COL_ERR, font=FONT).pack(
                            anchor="w")
                    continue
                row = tk.Frame(block, bg=COL_PANEL)
                row.pack(fill="x", anchor="w")
                tk.Label(row, text=f"{kind}:", bg=COL_PANEL, fg=COL_MUTED,
                         font=FONT, width=12, anchor="nw").pack(side="left",
                                                                anchor="n")
                # checkboxes wrap within the available width
                cbwrap = tk.Frame(row, bg=COL_PANEL)
                cbwrap.pack(side="left", fill="x", expand=True)
                for fp in files:
                    v = tk.BooleanVar(value=True)   # preselected
                    self.file_vars[mat][kind][fp.name] = v
                    tk.Checkbutton(cbwrap, text=fp.name, variable=v, bg=COL_PANEL,
                                   fg=COL_TEXT, selectcolor=COL_ENTRY,
                                   activebackground=COL_PANEL,
                                   activeforeground=COL_TEXT, font=FONT,
                                   anchor="w").pack(side="top", anchor="w")

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
        self._clear_container()
        f = self._new_step()
        self.planned_runs = self._plan_runs()
        total = len(self.planned_runs)
        mats = list(self.config["materials"].keys())

        self._step_header(
            f, 3, "Run a session",
            f"Project: {self.config['project_root']}\n"
            f"Materials: {', '.join(mats)}   Seeds: "
            f"{','.join(map(str, self.config.get('seeds', [])))}\n"
            f"This session will run {total} combination(s).")

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
        prev.pack(anchor="w", padx=14, pady=(4, 6), fill="both", expand=True)
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
        for i, r in enumerate(self.planned_runs, 1):
            self.preview.insert("", "end", values=(
                i, r["material"], r["potential"] if r["potential"] else "none",
                r["configuration"], r["structure"], r["seed"]))
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

            session_dt = dt.datetime.now()
            session_id = stamp_of(session_dt)
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

                run_dt = dt.datetime.now()
                run_stamp = stamp_of(run_dt)
                run_base = f"RUN-{idx:04d}-{run_stamp}"

                self.root.after(0, lambda i=idx, t=total: self._update_progress(i, t))
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
            self._generate_session_outputs(session_dir, session_id, session_csv,
                                           hw_csv)

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

    # ======================================================================
    # STEP 4 - post-processing / report generation (auto at session end)
    # ======================================================================
    def _generate_session_outputs(self, session_dir, session_id, summary_csv,
                                   hw_csv):
        """Generate the HTML report and/or PNG graphs per the Step 3 choices."""
        want_html = bool(getattr(self, "gen_html", None) and self.gen_html.get())
        want_png = bool(getattr(self, "gen_png", None) and self.gen_png.get())
        if not want_html and not want_png:
            return

        # read the summary rows
        summary = []
        try:
            with open(summary_csv) as fh:
                summary = list(csv.DictReader(fh))
        except Exception as e:
            self.log_line(f"Report: could not read summary CSV ({e})")
            return

        n_ok = sum(1 for r in summary if r["status"] == "OK")
        n_fail = sum(1 for r in summary if r["status"] == "FAILED")
        n_abort = sum(1 for r in summary if r["status"] == "ABORTED_EARLY")

        # load SESSION-INFO.json (hardware + timing) if present
        session_info = {}
        try:
            info_path = session_dir / f"SESSION-INFO_{session_id}.json"
            if info_path.exists():
                with open(info_path) as fh:
                    session_info = json.load(fh)
        except Exception:
            session_info = {}

        if want_html:
            try:
                self._build_html_report(session_dir, session_id, summary, hw_csv,
                                        n_ok, n_fail, n_abort, session_info)
            except Exception as e:
                self.log_line(f"Report: HTML generation failed ({e})")

        if want_png:
            try:
                self._build_png_graphs(session_dir, session_id, summary, hw_csv,
                                       n_ok)
            except Exception as e:
                self.log_line(f"Report: PNG generation failed ({e})")

    def _plotly_js(self) -> str:
        """Return the Plotly library JS to inline for fully-offline reports.

        Order of preference:
          1. plotly.min.js cached next to the app (fastest, offline).
          2. The copy bundled inside the `plotly` PyPI package, if installed.
          3. A one-time CDN download, cached for next time.
          4. Empty string -> caller falls back to a CDN <script> tag.
        """
        cache = script_dir() / "plotly.min.js"
        if cache.exists():
            try:
                return cache.read_text(encoding="utf-8")
            except Exception:
                pass
        # bundled inside the plotly package
        try:
            import plotly
            import glob
            base = os.path.dirname(plotly.__file__)
            hits = glob.glob(os.path.join(base, "**", "plotly.min.js"),
                             recursive=True)
            if hits:
                data = Path(hits[0]).read_text(encoding="utf-8")
                try:
                    cache.write_text(data, encoding="utf-8")   # cache for speed
                except Exception:
                    pass
                return data
        except Exception:
            pass
        # one-time CDN download
        url = "https://cdn.plot.ly/plotly-2.35.2.min.js"
        try:
            import urllib.request
            self.log_line("Report: downloading Plotly for offline reports "
                          "(one-time)...")
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read().decode("utf-8")
            cache.write_text(data, encoding="utf-8")
            return data
        except Exception as e:
            self.log_line(f"Report: could not fetch Plotly ({e}); report will "
                          f"reference the CDN and need internet to view.")
            return ""

    def _run_color(self, i):
        """Deterministic color per run index for the HW-stats bands/legend."""
        palette = ["#e2510f", "#f0b429", "#3fbf6f", "#4aa3df", "#c1272d",
                   "#9b59b6", "#1abc9c", "#e67e22", "#2ecc71", "#e74c3c"]
        return palette[i % len(palette)]

    def _build_html_report(self, session_dir, session_id, summary, hw_csv,
                           n_ok, n_fail, n_abort, session_info=None):
        report_path = session_dir / f"REPORT_{session_id}.html"
        session_info = session_info or {}

        # Insufficient-data case: no successful runs -> default template.
        if n_ok == 0:
            html = self._html_no_data(session_id, n_fail, n_abort, session_info)
            report_path.write_text(html, encoding="utf-8")
            self.log_line(f"REPORT (no data) -> {report_path}")
            return

        # read HW stats
        hw_rows = []
        try:
            with open(hw_csv) as fh:
                hw_rows = list(csv.DictReader(fh))
        except Exception:
            pass

        plotly = self._plotly_js()
        plotly_tag = (f"<script>{plotly}</script>" if plotly else
                      '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">'
                      '</script>')

        # Build HW-stats figures (two: temps, usage) with per-run color bands.
        hw_traces_temp, hw_traces_use, shapes, run_spans = self._hw_figures(
            hw_rows, summary)

        # Build per-run data (tprof/ebath) for successful runs.
        runs_dir = session_dir / "RUNS"
        per_run = self._per_run_payload(summary, runs_dir)

        import json as _json
        payload = {
            "session_id": session_id,
            "hw_temp": hw_traces_temp,
            "hw_use": hw_traces_use,
            "hw_shapes": shapes,
            "run_spans": run_spans,
            "per_run": per_run,
        }
        data_json = _json.dumps(payload)

        html = self._html_template(session_id, summary, n_ok, n_fail, n_abort,
                                   plotly_tag, data_json, session_info)
        report_path.write_text(html, encoding="utf-8")
        self.log_line(f"REPORT -> {report_path}")

    def _hw_figures(self, hw_rows, summary):
        """Turn HW-stats rows into Plotly trace dicts plus per-run color bands.

        Returns (temp_traces, usage_traces, shapes, run_spans)."""
        # group probe rows by run_id, preserving order
        from collections import OrderedDict
        by_run = OrderedDict()
        for r in hw_rows:
            by_run.setdefault(r["run_id"], []).append(r)

        # x axis = probe index across the whole session (simple, monotonic)
        temp_cpu_x, temp_cpu_y = [], []
        temp_gpu_x, temp_gpu_y = [], []
        use_cpu_x, use_cpu_y = [], []
        use_gpu_x, use_gpu_y = [], []
        use_ram_x, use_ram_y = [], []
        shapes = []
        run_spans = []
        idx = 0
        for run_i, (run_id, rows) in enumerate(by_run.items()):
            start = idx
            for r in rows:
                def num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None
                ct = num(r.get("cpu_temp_c")); gt = num(r.get("gpu_temp_c"))
                cu = num(r.get("cpu_usage_pct")); gu = num(r.get("gpu_usage_pct"))
                ru = num(r.get("ram_usage_pct"))
                if ct is not None:
                    temp_cpu_x.append(idx); temp_cpu_y.append(ct)
                if gt is not None:
                    temp_gpu_x.append(idx); temp_gpu_y.append(gt)
                if cu is not None:
                    use_cpu_x.append(idx); use_cpu_y.append(cu)
                if gu is not None:
                    use_gpu_x.append(idx); use_gpu_y.append(gu)
                if ru is not None:
                    use_ram_x.append(idx); use_ram_y.append(ru)
                idx += 1
            end = max(idx - 1, start)
            color = self._run_color(run_i)
            shapes.append({"type": "rect", "xref": "x", "yref": "paper",
                           "x0": start - 0.5, "x1": end + 0.5, "y0": 0, "y1": 1,
                           "fillcolor": color, "opacity": 0.12, "line": {"width": 0},
                           "layer": "below"})
            run_spans.append({"run_id": run_id, "x0": start, "x1": end,
                              "color": color})

        temp_traces = [
            {"x": temp_cpu_x, "y": temp_cpu_y, "name": "CPU temp (C)",
             "mode": "lines+markers", "line": {"color": "#e2510f"}},
            {"x": temp_gpu_x, "y": temp_gpu_y, "name": "GPU temp (C)",
             "mode": "lines+markers", "line": {"color": "#4aa3df"}},
        ]
        usage_traces = [
            {"x": use_cpu_x, "y": use_cpu_y, "name": "CPU %",
             "mode": "lines+markers", "line": {"color": "#e2510f"}},
            {"x": use_gpu_x, "y": use_gpu_y, "name": "GPU %",
             "mode": "lines+markers", "line": {"color": "#4aa3df"}},
            {"x": use_ram_x, "y": use_ram_y, "name": "RAM %",
             "mode": "lines+markers", "line": {"color": "#3fbf6f"}},
        ]
        return temp_traces, usage_traces, shapes, run_spans

    def _per_run_payload(self, summary, runs_dir: Path):
        """Build per-run tprof/ebath series for the per-run tab."""
        out = []
        for r in summary:
            run_id = r["run_id"]
            entry = {
                "run_id": run_id, "status": r["status"],
                "material": r.get("material", ""),
                "potential": r.get("potential", ""),
                "configuration": r.get("configuration", ""),
                "structure": r.get("structure", ""),
                "seed": r.get("seed", ""),
                "duration_sec": r.get("duration_sec", ""),
                "tprof": None, "ebath": None,
            }
            if r["status"] == "OK":
                rd = runs_dir / run_id
                # ebath series
                ep = rd / "ELECTRON-BATH.csv"
                if ep.exists():
                    try:
                        with open(ep) as fh:
                            rows = list(csv.DictReader(fh))
                        entry["ebath"] = {
                            "t": [float(x["t_ps"]) for x in rows],
                            "hot": [float(x["E_hot_eV"]) for x in rows],
                            "cold": [float(x["E_cold_eV"]) for x in rows],
                        }
                    except Exception:
                        pass
                # tprof: last timestep's profile (temp vs coord)
                tp = rd / "TEMP-PROFILE.csv"
                if tp.exists():
                    try:
                        with open(tp) as fh:
                            rows = list(csv.DictReader(fh))
                        if rows:
                            last_ts = max(int(x["timestep"]) for x in rows)
                            prof = [x for x in rows
                                    if int(x["timestep"]) == last_ts
                                    and x["temperature"] != ""]
                            entry["tprof"] = {
                                "coord": [float(x["coord"]) for x in prof],
                                "temp": [float(x["temperature"]) for x in prof],
                                "timestep": last_ts,
                            }
                    except Exception:
                        pass
            out.append(entry)
        return out

    def _build_png_graphs(self, session_dir, session_id, summary, hw_csv, n_ok):
        """Backup PNG graphs via matplotlib (best-effort)."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception:
            self.log_line("Report: matplotlib/numpy not available - skipping PNGs.")
            return
        if n_ok == 0:
            self.log_line("Report: no successful runs - skipping PNGs.")
            return
        graphs_dir = session_dir / "GRAPHS"
        graphs_dir.mkdir(parents=True, exist_ok=True)
        # HW usage over probe index
        try:
            with open(hw_csv) as fh:
                rows = list(csv.DictReader(fh))
            xs = list(range(len(rows)))
            def col(name):
                out = []
                for r in rows:
                    try:
                        out.append(float(r[name]))
                    except (TypeError, ValueError):
                        out.append(float("nan"))
                return out
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(xs, col("cpu_usage_pct"), label="CPU %")
            ax.plot(xs, col("gpu_usage_pct"), label="GPU %")
            ax.plot(xs, col("ram_usage_pct"), label="RAM %")
            ax.set_xlabel("probe #"); ax.set_ylabel("%"); ax.legend()
            ax.set_title(f"Session {session_id} - resource usage")
            fig.tight_layout()
            fig.savefig(graphs_dir / f"HW-USAGE_{session_id}.png", dpi=110)
            plt.close(fig)
            self.log_line(f"PNG -> {graphs_dir / f'HW-USAGE_{session_id}.png'}")
        except Exception as e:
            self.log_line(f"Report: PNG HW graph failed ({e})")

    # -- HTML templates ----------------------------------------------------
    def _fmt_duration(self, secs):
        try:
            return fmt_hms(float(secs))
        except Exception:
            return "unknown"

    def _hw_summary_cards(self, session_info):
        """Return HTML for the hardware + timing overview cards."""
        hw = session_info.get("hardware", {}) or {}
        start = session_info.get("start_utc") or "-"
        end = session_info.get("end_utc") or "-"
        dur = self._fmt_duration(session_info.get("duration_sec"))
        gpu = hw.get("gpu_model") or "none detected"
        vram = hw.get("gpu_vram_mb")
        gpu_line = gpu + (f" ({vram} MB VRAM)" if vram else "")
        cpu = hw.get("cpu_model") or "unknown"
        cores_p = hw.get("cpu_cores_physical")
        cores_l = hw.get("cpu_cores_logical")
        cores = (f"{cores_p} physical / {cores_l} logical"
                 if cores_p or cores_l else "unknown")
        ram = hw.get("ram_total_gb")
        ram_line = f"{ram} GB" if ram else "unknown"

        def card(label, value):
            return (f'<div class="card"><div class="card-label">{label}</div>'
                    f'<div class="card-value">{value}</div></div>')

        return "\n".join([
            card("Started (UTC)", start),
            card("Ended (UTC)", end),
            card("Duration", dur),
            card("CPU", cpu),
            card("CPU cores", cores),
            card("RAM", ram_line),
            card("GPU", gpu_line),
            card("Platform", hw.get("platform") or "unknown"),
        ])

    def _html_no_data(self, session_id, n_fail, n_abort, session_info=None):
        session_info = session_info or {}
        cards = self._hw_summary_cards(session_info)
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LAVA Report {session_id}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:#1a1210;color:#f3e9e3;margin:0;padding:0}}
.wrap{{max-width:900px;margin:0 auto;padding:32px}}
h1{{color:#ff7a1a;margin:0 0 4px}}
.box{{background:#241a17;padding:28px;border-radius:12px;
border:1px solid #e2510f;margin-top:20px}}
.red{{color:#ff5a4d;font-weight:bold}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
gap:12px;margin-top:20px}}
.card{{background:#241a17;border:1px solid #3a2a24;border-radius:10px;padding:14px}}
.card-label{{color:#b08a7a;font-size:11px;text-transform:uppercase;
letter-spacing:.5px}}
.card-value{{color:#f3e9e3;font-size:15px;margin-top:4px;word-break:break-word}}
.footer{{color:#b08a7a;font-size:12px;margin-top:24px}}
</style></head><body><div class="wrap">
<h1>LAVA &mdash; Report not generated</h1>
<div class="box"><p>This report was <span class="red">not generated</span>
because {n_fail} run(s) failed and {n_abort} run(s) were aborted early, leaving
insufficient successful data to visualize.</p>
<p style="color:#b08a7a">Session {session_id}</p></div>
<div class="cards">{cards}</div>
<div class="footer">{FOOTER}</div>
</div></body></html>"""

    def _html_template(self, session_id, summary, n_ok, n_fail, n_abort,
                       plotly_tag, data_json, session_info=None):
        session_info = session_info or {}
        cards = self._hw_summary_cards(session_info)

        # summary table rows, colored by status
        rows_html = []
        for r in summary:
            st = r["status"]
            color = ("#3fbf6f" if st == "OK" else
                     "#ff5a4d" if st == "FAILED" else "#f0b429")
            rows_html.append(
                f"<tr><td>{r['run_index']}</td>"
                f"<td>{r.get('material','')}</td>"
                f"<td>{r.get('potential','')}</td>"
                f"<td>{r.get('configuration','')}</td>"
                f"<td>{r.get('structure','')}</td>"
                f"<td>{r.get('seed','')}</td>"
                f"<td style='color:{color};font-weight:bold'>{st}</td>"
                f"<td>{r.get('duration_sec','')}</td></tr>")
        table = "\n".join(rows_html)

        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LAVA Report {session_id}</title>
{plotly_tag}
<style>
*{{box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:#1a1210;color:#f3e9e3;margin:0;padding:0}}
header{{background:linear-gradient(180deg,#2b1e19,#241a17);
padding:20px 32px;border-bottom:2px solid #e2510f}}
header h1{{color:#ff7a1a;margin:0;font-size:24px;letter-spacing:.5px}}
header .sub{{color:#b08a7a;font-size:13px;margin-top:4px}}
.status-pills span{{display:inline-block;padding:3px 12px;border-radius:12px;
font-size:12px;font-weight:bold;margin-right:8px}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 32px 48px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:12px;margin:20px 0}}
.card{{background:#241a17;border:1px solid #3a2a24;border-radius:10px;
padding:14px 16px}}
.card-label{{color:#b08a7a;font-size:11px;text-transform:uppercase;
letter-spacing:.5px}}
.card-value{{color:#f3e9e3;font-size:15px;margin-top:5px;word-break:break-word}}
.tabs{{display:flex;gap:6px;margin:24px 0 0;border-bottom:1px solid #3a2a24}}
.tab{{padding:11px 22px;background:transparent;color:#b08a7a;cursor:pointer;
border:none;font-size:14px;font-weight:600;border-bottom:3px solid transparent}}
.tab:hover{{color:#f3e9e3}}
.tab.active{{color:#ff7a1a;border-bottom-color:#e2510f}}
.panel{{display:none;padding-top:20px}}.panel.active{{display:block}}
.section-title{{color:#ff7a1a;font-size:16px;margin:22px 0 8px}}
table{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}}
th,td{{border:1px solid #3a2a24;padding:8px 12px;text-align:left}}
th{{background:#2b1e19;color:#ff7a1a;position:sticky;top:0}}
tr:nth-child(even) td{{background:#211814}}
.chart{{background:#241a17;border:1px solid #3a2a24;border-radius:10px;
margin:12px 0;padding:10px;height:380px}}
.chart.small{{height:320px}}
.hint{{color:#b08a7a;font-size:12px;margin:6px 0 0}}
#runsearch{{padding:10px 14px;width:min(420px,100%);background:#31231f;
color:#f3e9e3;border:1px solid #3a2a24;border-radius:6px;font-size:14px}}
.layout{{display:grid;grid-template-columns:320px 1fr;gap:18px;margin-top:14px}}
@media(max-width:820px){{.layout{{grid-template-columns:1fr}}}}
.runlist{{max-height:620px;overflow-y:auto;padding-right:4px}}
.runitem{{padding:10px 12px;margin:6px 0;background:#241a17;border-radius:8px;
cursor:pointer;border-left:4px solid #3fbf6f;transition:background .15s}}
.runitem:hover{{background:#2b1e19}}
.runitem.FAILED{{border-left-color:#ff5a4d}}
.runitem.ABORTED_EARLY{{border-left-color:#f0b429}}
.runitem .rid{{font-weight:bold;font-size:13px}}
.runitem .meta{{color:#b08a7a;font-size:12px;margin-top:3px}}
.badge{{padding:2px 9px;border-radius:10px;font-size:11px;font-weight:bold;
float:right}}
.detail-empty{{color:#b08a7a;padding:40px;text-align:center;
background:#241a17;border-radius:10px;border:1px dashed #3a2a24}}
.footer{{color:#b08a7a;padding:20px 32px;font-size:12px;
border-top:1px solid #3a2a24;margin-top:24px}}
</style></head><body>
<header>
<h1>&#x1F30B; LAVA Report</h1>
<div class="sub">Session {session_id}</div>
<div class="status-pills" style="margin-top:10px">
<span style="background:#1e3a28;color:#3fbf6f">{n_ok} successful</span>
<span style="background:#3a1e1e;color:#ff5a4d">{n_fail} failed</span>
<span style="background:#3a331e;color:#f0b429">{n_abort} aborted</span>
</div></header>

<div class="wrap">
<div class="cards">{cards}</div>

<div class="tabs">
<button class="tab active" data-tab="hw" onclick="showTab(this,'hw')">
Hardware &amp; Session</button>
<button class="tab" data-tab="runs" onclick="showTab(this,'runs')">
Per-run Analysis</button>
</div>

<div id="hw" class="panel active">
<div class="section-title">Run summary</div>
<table><thead><tr><th>#</th><th>Material</th><th>Potential</th><th>Config</th>
<th>Structure</th><th>Seed</th><th>Status</th><th>Duration (s)</th></tr></thead>
<tbody>{table}</tbody></table>
<div class="section-title">Temperatures over session</div>
<div id="tempChart" class="chart"></div>
<div class="section-title">Resource usage over session</div>
<div id="useChart" class="chart"></div>
<p class="hint">Colored bands mark each run's span along the timeline. Drag to
zoom, double-click to reset, hover for values.</p>
</div>

<div id="runs" class="panel">
<input id="runsearch" placeholder="Search runs by material, file, seed, or ID..."
 oninput="filterRuns()"/>
<div class="layout">
<div class="runlist" id="runlist"></div>
<div id="rundetail"><div class="detail-empty">Select a run on the left to see
its temperature profile and electron-bath energy.</div></div>
</div>
</div>
</div>

<div class="footer">{FOOTER}</div>

<script>
var DATA = {data_json};
var DARK={{paper_bgcolor:'#241a17',plot_bgcolor:'#241a17',
  font:{{color:'#f3e9e3'}},margin:{{t:30,r:20,b:45,l:55}},
  legend:{{orientation:'h',y:1.12}}}};

function showTab(btn,t){{
  document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('active')}});
  document.querySelectorAll('.panel').forEach(function(x){{x.classList.remove('active')}});
  btn.classList.add('active');
  document.getElementById(t).classList.add('active');
  if(t==='hw'){{drawHW();}}
  // charts drawn while hidden have 0 width; resize once visible
  setTimeout(function(){{
    ['tempChart','useChart','tprofC','ebathC'].forEach(function(id){{
      var el=document.getElementById(id);
      if(el && el.data){{Plotly.Plots.resize(el);}}
    }});
  }},50);
}}

var hwDrawn=false;
function drawHW(){{
  if(hwDrawn) return; hwDrawn=true;
  var t=Object.assign({{}},DARK,{{shapes:DATA.hw_shapes,
    xaxis:{{title:'probe #',gridcolor:'#3a2a24'}},
    yaxis:{{title:'\\u00B0C',gridcolor:'#3a2a24'}}}});
  Plotly.newPlot('tempChart',DATA.hw_temp,t,{{responsive:true,displaylogo:false}});
  var u=Object.assign({{}},DARK,{{shapes:DATA.hw_shapes,
    xaxis:{{title:'probe #',gridcolor:'#3a2a24'}},
    yaxis:{{title:'%',gridcolor:'#3a2a24',range:[0,100]}}}});
  Plotly.newPlot('useChart',DATA.hw_use,u,{{responsive:true,displaylogo:false}});
}}

function renderRunList(items){{
  var el=document.getElementById('runlist'); el.innerHTML='';
  if(!items.length){{el.innerHTML='<p class="hint">No runs match.</p>';return;}}
  items.forEach(function(r){{
    var c=(r.status==='OK'?'#3fbf6f':r.status==='FAILED'?'#ff5a4d':'#f0b429');
    var d=document.createElement('div');
    d.className='runitem '+r.status;
    d.innerHTML='<span class="badge" style="background:'+c+';color:#1a1210">'+
      r.status+'</span><div class="rid">'+r.run_id+'</div>'+
      '<div class="meta">'+r.material+' &middot; '+(r.potential||'no potential')+
      ' &middot; '+r.structure+' &middot; seed '+r.seed+'</div>';
    d.onclick=function(){{showRun(r);}};
    el.appendChild(d);
  }});
}}

function showRun(r){{
  var el=document.getElementById('rundetail');
  if(r.status!=='OK'){{
    var c=(r.status==='FAILED'?'#ff5a4d':'#f0b429');
    el.innerHTML='<div class="detail-empty" style="color:'+c+'"><b>'+r.run_id+
      '</b><br>'+r.status+' &mdash; no data available for this run.</div>';
    return;
  }}
  el.innerHTML='<div class="section-title">'+r.run_id+'</div>'+
    '<div id="tprofC" class="chart small"></div>'+
    '<div id="ebathC" class="chart small"></div>';
  if(r.tprof && r.tprof.coord && r.tprof.coord.length){{
    Plotly.newPlot('tprofC',[{{x:r.tprof.coord,y:r.tprof.temp,
      mode:'lines+markers',line:{{color:'#e2510f'}},marker:{{size:5}},
      name:'T (K)'}}],
      Object.assign({{}},DARK,{{title:'Temperature profile (t='+r.tprof.timestep+')',
      xaxis:{{title:'position (reduced)',gridcolor:'#3a2a24'}},
      yaxis:{{title:'T (K)',gridcolor:'#3a2a24'}}}}),
      {{responsive:true,displaylogo:false}});
  }} else {{
    document.getElementById('tprofC').className='detail-empty';
    document.getElementById('tprofC').innerHTML=
      'No temperature-profile data &mdash; the run likely ended before the '+
      'ave/chunk output interval was reached.';
  }}
  if(r.ebath && r.ebath.t && r.ebath.t.length){{
    Plotly.newPlot('ebathC',[
      {{x:r.ebath.t,y:r.ebath.hot,mode:'lines',name:'E_hot (eV)',
        line:{{color:'#ff5a4d'}}}},
      {{x:r.ebath.t,y:r.ebath.cold,mode:'lines',name:'E_cold (eV)',
        line:{{color:'#4aa3df'}}}}],
      Object.assign({{}},DARK,{{title:'Electron-bath energy',
      xaxis:{{title:'t (ps)',gridcolor:'#3a2a24'}},
      yaxis:{{title:'E (eV)',gridcolor:'#3a2a24'}}}}),
      {{responsive:true,displaylogo:false}});
  }} else {{
    document.getElementById('ebathC').className='detail-empty';
    document.getElementById('ebathC').innerHTML='No electron-bath data.';
  }}
}}

function filterRuns(){{
  var q=document.getElementById('runsearch').value.toLowerCase();
  var items=DATA.per_run.filter(function(r){{
    return (r.run_id+' '+r.material+' '+(r.potential||'')+' '+r.configuration+
      ' '+r.structure+' '+r.seed).toLowerCase().indexOf(q)>=0;
  }});
  renderRunList(items);
}}

window.addEventListener('resize',function(){{
  ['tempChart','useChart','tprofC','ebathC'].forEach(function(id){{
    var el=document.getElementById(id);
    if(el && el.data){{Plotly.Plots.resize(el);}}
  }});
}});

drawHW();
renderRunList(DATA.per_run);
</script>
</body></html>"""


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
