#!/usr/bin/env python3
"""
LAVA constants - single source of truth for all configuration values.
=====================================================================
Edit values here directly; every other module imports from this file. This is
plain Python on purpose, so paths (Path.home()), computed strings (FOOTER), and
tkinter font tuples all just work with no loader or parsing step.

Used by run.py (all of it), helpers.py (MATERIAL_SUBDIRS, INPUT_EXT,
DEFAULT_PROBE_SEC) and report.py (FOOTER).

NOTE - values duplicated in setup.sh: CUDA_MIN, the build root, and the binary
path also appear (as bash) in setup.sh. If you change them here, change them
there too - nothing keeps the two in sync automatically. See ARCHITECTURE.md.
"""

from pathlib import Path

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

# ---------------------------------------------------------------------------
# Volcanic / lava palette
# ---------------------------------------------------------------------------
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
# Build / paths
# ---------------------------------------------------------------------------
BUILD_SCRIPT = "setup.sh"
LAMMPS_BUILD_ROOT = Path.home() / ".lammps-build"
LAMMPS_BIN = LAMMPS_BUILD_ROOT / "build" / "lmp"
CUDA_MIN = "12.8"
CONFIG_NAME = "config.json"

# ---------------------------------------------------------------------------
# Project structure
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_PROBE_SEC = 60
DEFAULT_MAX_CPU = 100
DEFAULT_MAX_RAM = 90
DEFAULT_MAX_GPU = 100
