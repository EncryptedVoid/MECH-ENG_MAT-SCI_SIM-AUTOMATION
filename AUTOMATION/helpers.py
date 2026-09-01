#!/usr/bin/env python3
"""
LAVA helpers — pure, GUI-free logic.
====================================

WHAT THIS FILE IS
-----------------
Every piece of LAVA's logic that does NOT touch the GUI: time/stamp
formatting, seed parsing, filesystem discovery of materials and their input
files, the community-material helpers (slugify, untested-name handling,
per-material MATERIAL-INFO.json read/write), past-session scanning, CSV
read/write, LAMMPS output parsers (ebath and temperature-profile), environment
probes (psutil / nvidia-smi / hardware detection), and the background
MetricsSampler that samples CPU/RAM/GPU on its own thread.

Every function takes plain arguments and returns plain data, so all of it is
unit-testable with no display and no App instance. The one class here,
MetricsSampler, only samples and stores numbers; it never touches tkinter.

WHO IMPORTS IT / WHAT IT IMPORTS
--------------------------------
Imported by run.py (and usable by report.py). It imports only the stdlib and
values from constants.py. It NEVER imports report.py or run.py — that keeps the
graph acyclic:

    constants.py  --imported by-->  helpers.py  --imported by-->  run.py

WORKING ON THIS FILE (for humans and LLMs)
------------------------------------------
* This module is self-contained: to understand or change a function here you
  need only this file and the handful of names it reads from constants.py
  (MATERIAL_SUBDIRS, INPUT_EXT, DEFAULT_PROBE_SEC, UNTESTED_PREFIX,
  MATERIAL_INFO_NAME). You do not need run.py or report.py open.
* Keep the no-tkinter, no-App rule. If you are tempted to import the GUI or
  reach for App state, the logic belongs in run.py instead.
* Logging: functions here are silent by default. MetricsSampler swallows probe
  failures (a missing sensor must never crash a run). If you add logging, do it
  via a passed-in callable, not by importing GUI code.
* Because these functions are pure, the right way to verify a change is a small
  unit test (see ARCHITECTURE.md, "Testing without a display").
"""

import os
import re
import csv
import json
import shutil
import platform
import threading
import subprocess
import datetime as dt
from pathlib import Path

from constants import (
    MATERIAL_SUBDIRS,
    INPUT_EXT,
    DEFAULT_PROBE_SEC,
    UNTESTED_PREFIX,
    MATERIAL_INFO_NAME,
)


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")

def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d%H%M%S")

def stamp_of(ts) -> str:
    return ts.strftime("%Y%m%d%H%M%S")

def script_dir() -> Path:
    return Path(__file__).resolve().parent

def repo_root() -> Path:
    """The project root: one level above AUTOMATION/ (where run.py lives)."""
    return script_dir().parent

def is_untested_name(name: str) -> bool:
    return name.startswith(UNTESTED_PREFIX)

def display_material_name(name: str) -> str:
    """Strip the untested prefix for display; leave tested names untouched."""
    return name[len(UNTESTED_PREFIX):] if is_untested_name(name) else name

def slugify(text: str) -> str:
    """Filesystem/git-ref-safe slug: lowercase, non-alnum -> hyphen, trimmed."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").lower()
    return s or "material"

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

def read_material_info(material_dir: Path) -> dict:
    """Optional per-material metadata (description, contributor, tested flag).

    Absent file -> empty dict. Never raises."""
    try:
        p = material_dir / MATERIAL_INFO_NAME
        if p.exists():
            with open(p) as fh:
                return json.load(fh) or {}
    except Exception:
        pass
    return {}

def write_material_info(material_dir: Path, info: dict):
    material_dir.mkdir(parents=True, exist_ok=True)
    with open(material_dir / MATERIAL_INFO_NAME, "w") as fh:
        json.dump(info, fh, indent=2)

def scan_sessions(root: Path):
    """List past sessions under <root>/ANALYSIS/SESSIONS, newest first.

    Each entry: {id, date, materials, report (Path or None), dir}. Reads the
    already-written SESSION-INFO_<id>.json / SESSION-SUMMARY_<id>.csv for the
    date and material list; falls back to the folder name when they're absent."""
    sessions = []
    sroot = root / "ANALYSIS" / "SESSIONS"
    if not sroot.is_dir():
        return sessions
    for d in sorted(sroot.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith("SESSION-"):
            continue
        sid = d.name[len("SESSION-"):]
        # A stopped session's folder is tagged with -[ABORTED]; its inner files
        # keep the plain id, so strip the tag when resolving them.
        aborted = sid.endswith("-[ABORTED]")
        if aborted:
            sid = sid[:-len("-[ABORTED]")]
        date = _session_date(sid)
        materials = []
        # materials come from the per-run summary CSV if present
        summary = d / f"SESSION-SUMMARY_{sid}.csv"
        if summary.exists():
            try:
                with open(summary) as fh:
                    seen = []
                    for row in csv.DictReader(fh):
                        m = display_material_name(row.get("material", ""))
                        if m and m not in seen:
                            seen.append(m)
                    materials = seen
            except Exception:
                pass
        report = d / f"REPORT_{sid}.html"
        sessions.append({
            "id": sid,
            "date": date,
            "materials": materials,
            "report": report if report.exists() else None,
            "dir": d,
        })
    return sessions

def _session_date(session_id: str) -> str:
    """Session ids are YYYYMMDDHHMMSS stamps; format if parseable."""
    try:
        d = dt.datetime.strptime(session_id[:14], "%Y%m%d%H%M%S")
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return session_id

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


