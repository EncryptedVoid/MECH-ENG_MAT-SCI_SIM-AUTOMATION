#!/usr/bin/env python3
"""
LAVA constants — the single source of truth for every configuration value.
==========================================================================

WHAT THIS FILE IS
-----------------
Plain Python data: version/branding strings, the volcanic colour palette,
tkinter font tuples, build paths, the project-folder layout, accepted input
extensions, output-rename rules, and default limits. Nothing here does any
work — it only *declares* values. Because it is plain Python (not JSON/INI), it
can hold computed values directly: Path.home(), the FOOTER f-string, tuples for
fonts, and sets for extensions all just work with no loader or parse step.

WHO IMPORTS IT
--------------
Everything. helpers.py, report.py, and run.py all import names from here.
This module imports NOTHING from the project — only the stdlib pathlib. That
one-way rule is what keeps the import graph acyclic:

    constants.py  --imported by-->  helpers.py, report.py, run.py

WORKING ON THIS FILE (for humans and LLMs)
------------------------------------------
* To change a colour, font, default limit, path, or accepted extension, edit it
  HERE — there is no other config file and no runtime override.
* Keep this module pure data + stdlib only. Do NOT import helpers, report, or
  run into it (that would create an import cycle), and do not do heavy work at
  import time.
* You can understand and safely edit this file in complete isolation: no other
  project file needs to be open to change a value here. Downstream modules read
  these names but never write them.

DIVERGENCE NOTE
---------------
CUDA_MIN, the build root (~/.lammps-build), and the binary path (build/lmp) are
also hard-coded (in bash) inside the standalone build script named by
BUILD_SCRIPT. Nothing syncs the two. If you change them here, change them in
that script too. See ARCHITECTURE.md.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Version / branding
# ---------------------------------------------------------------------------
VERSION = "0.8.0"
APP_NAME = "LAVA"
APP_SUBTITLE = "LAMMPS AUTOMATION VALIDATION AID"
APP_CREDIT = ('Project developed in conjunction with BUET Mechanical Engineering '
              'student "Kazi Rubaiyat Mustafiz" and University of the People '
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

# Preferred UI font family, applied at startup by run.py if it is installed on
# the system (tkinter can only use OS-installed fonts, not a bundled .ttf). The
# fallback is a generic sans-serif via tkinter's built-in default fonts above.
# run.py resolves FONT / FONT_BOLD / FONT_MONO against the installed families
# once the Tk root exists and writes the resolved tuples back here, so every
# widget that reads constants.FONT* picks up the choice with no other changes.
# To try a different font, change PREFERRED_FONT (and install it on each
# machine, e.g. `apt install fonts-roboto` on Linux); if it is absent LAVA
# falls back to the sans-serif defaults above rather than breaking.
PREFERRED_FONT = "Roboto Mono"
FONT_SIZE = 10
FONT_SIZE_MONO = 9

# ---------------------------------------------------------------------------
# Build / paths
# ---------------------------------------------------------------------------
BUILD_SCRIPT = "build-lammps.sh"
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
# Community-contributed materials
# ---------------------------------------------------------------------------
# Community-contributed material folders are committed with this name prefix
# until a CI run validates them. Anything carrying it is shown as "untested".
UNTESTED_PREFIX = "[UNTESTED]-"
# Optional per-material metadata file (name, description, tested flag).
MATERIAL_INFO_NAME = "MATERIAL-INFO.json"

# The three asset rows shown in the material-profile editor, mapped to the
# MATERIAL_SUBDIRS keys used everywhere else.
EDITOR_ROWS = [
    ("potentials", "POTENTIAL FILE(S)"),
    ("structures", "STRUCTURE FILE(S)"),
    ("configs", "CONFIGURATION/INPUT FILE(S)"),
]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_PROBE_SEC = 60
DEFAULT_MAX_CPU = 90
DEFAULT_MAX_RAM = 90
DEFAULT_MAX_GPU = 90
