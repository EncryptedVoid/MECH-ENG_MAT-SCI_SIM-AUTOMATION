#!/usr/bin/env python3
"""
LAVA helpers - pure, GUI-free logic.
====================================
Everything in this module is independent of tkinter and of the App class:
time/stamp formatting, seed parsing, filesystem discovery, CSV I/O, LAMMPS
output parsers, environment probes, and the background MetricsSampler.

Design rules for this file (keep it this way):
  * NO tkinter imports. Nothing here may touch the GUI.
  * Functions take plain arguments and return plain data, so they are unit
    testable in isolation.
  * One module-level logger, shared by every function in this file
    (`log = logging.getLogger(__name__)`). MetricsSampler uses its own
    "metrics" logger so its lines are tagged distinctly in the log files.

Logging destinations (file handlers, GUI queue) are configured centrally in
run.py; this module only emits records and never configures handlers.
"""

import csv
import shutil
import logging
import threading
import subprocess
import datetime as dt
from pathlib import Path

from constants import MATERIAL_SUBDIRS, INPUT_EXT, DEFAULT_PROBE_SEC

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time / stamp helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")

def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d%H%M%S")

def stamp_of(ts) -> str:
    return ts.strftime("%Y%m%d%H%M%S")

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
# Seed parsing
# ---------------------------------------------------------------------------
def parse_seeds(spec: str):
    """Parse 'X,Y,A-B,Z' -> sorted unique ints. Raises ValueError on bad tokens."""
    import re
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


# ---------------------------------------------------------------------------
# Environment probes
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Filesystem discovery
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Sample summarisation
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# LAMMPS output parsers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Metrics sampler (configurable probe interval)
# ---------------------------------------------------------------------------
class MetricsSampler:
    """Samples CPU/RAM (psutil) and GPU (nvidia-smi) on a background thread.

    Uses its own "metrics" logger so probe failures (previously swallowed
    silently) surface in the session log tagged distinctly. Pass nothing to
    keep the old silent-by-default behaviour aside from logging: if no handler
    is attached for the "metrics" logger, records simply go nowhere.
    """

    _log = logging.getLogger("metrics")

    def __init__(self, interval=None):
        import psutil  # noqa: F401 (stored on self._psutil, used across methods)
        self._psutil = psutil
        if interval is None:
            interval = DEFAULT_PROBE_SEC
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
        except Exception as e:
            self._log.debug("gpu probe failed: %s", e)
        return (None, None)

    def _cpu_temp(self):
        try:
            temps = self._psutil.sensors_temperatures()
        except Exception as e:
            self._log.debug("cpu temp probe failed: %s", e)
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
