# LAVA — LAMMPS Automation Validation Aid

LAVA is a step-by-step desktop wizard (tkinter) for setting up and running
batches of [LAMMPS](https://www.lammps.org/) molecular-dynamics simulations. It
builds LAMMPS for your machine, lays out a project tree, plans every
potential × configuration × structure × seed combination for your materials,
runs them while sampling hardware usage, and produces an interactive offline
HTML report plus backup PNG graphs.

Project developed in conjunction with BUET Mechanical Engineering student *Kazi
Rubaiyat Mustafix* and University of the People Computer Science student *Ashiq
Arib Gazi*.

## Quick start (automated)

Paste one line; the entrypoint scripts install everything and launch LAVA.
Re-run the same command any time to reopen it — setup steps are skipped once done.

**Windows 11** (PowerShell as administrator):

```powershell
git clone https://github.com/EncryptedVoid/LAVA_LAMMPS-Automation-Validation-Aid.git; cd LAVA_LAMMPS-Automation-Validation-Aid; .\entrypoint.ps1
```

The first run installs WSL + Ubuntu 22.04 and asks you to reboot, then re-run
(a copy of `entrypoint.ps1` is left on your Desktop). It moves the repo into
`~/LAVA` inside WSL and launches automatically from then on.

**Linux**:

```bash
git clone https://github.com/EncryptedVoid/LAVA_LAMMPS-Automation-Validation-Aid.git && cd LAVA_LAMMPS-Automation-Validation-Aid && chmod +x entrypoint.sh && ./entrypoint.sh
```

`entrypoint.sh` updates packages, installs dependencies, then runs
`AUTOMATION/run.py`. It auto-detects apt, dnf, or pacman.

## Requirements

- **Python 3.11+**
- **tkinter** — ships with CPython, but on Linux is a system package: `sudo apt install python3-tk`
- **psutil** — required for a session to run (CPU/RAM sampling)
- **Pillow**, **matplotlib**, **plotly** — optional; each degrades gracefully if absent (see `requirements.txt`)
- A working **LAMMPS build toolchain** for the build step (handled by `build_lammps.sh`: build-essential, cmake, git, OpenMPI; CUDA/OpenCL added automatically if a GPU is present)

Install the Python dependencies with:

```bash
pip install -r requirements.txt
```

## Running (manual)

If you'd rather not use the entrypoint scripts, run it directly. The Python
lives in `AUTOMATION/`:

```bash
cd AUTOMATION
python3 run.py
```

The wizard walks through:

1. **Pick a pipeline** — the Selection Pipeline runs specific chosen files in
   combination. (Auto-Generate and Headless pipelines are stubbed for later.)
2. **Set up LAMMPS** — builds a machine-matched `lmp` binary via `build_lammps.sh`
   (asks for your sudo password to install build packages; it is never stored
   or logged). A GPU build also runs CPU-only, so one binary serves both.
3. **Set up the project** — choose a project root and a materials source folder,
   tick the files to include per material, set seeds (e.g. `1,3,5-8`). Selected
   files are copied into the project tree and a `config.json` is written.
4. **Run a session** — set resource limits and probe interval, preview the
   planned runs, then start. Runs execute in isolated temp dirs with live
   pause / skip / stop control. Outputs, per-run CSVs, a session summary, and a
   hardware-stats CSV are written under `ANALYSIS/SESSIONS/`.

At session end LAVA writes an interactive `REPORT_<id>.html` (fully offline —
Plotly is inlined) and, if matplotlib is available, backup PNG graphs.

## Editing configuration

All tunable values — colors, fonts, the project tree, accepted input
extensions, default limits, the build paths — live in **`constants.py`**. Edit
them there; there is no separate config file to keep in sync.

One exception: `CUDA_MIN` and the build paths are mirrored in **`build_lammps.sh`**
(bash) because the build script runs standalone. If you change them in
`constants.py`, change them in `build_lammps.sh` too. See `ARCHITECTURE.md`.

## Files

| File | What it is |
|------|-----------|
| `entrypoint.ps1` | Windows bootstrap: installs WSL + Ubuntu, moves repo to `~/LAVA`, launches |
| `entrypoint.sh` | Linux/WSL bootstrap: updates packages, installs deps, runs `AUTOMATION/run.py` |
| `AUTOMATION/run.py` | Entry point: the App wizard, logging setup, `main()` |
| `AUTOMATION/helpers.py` | Pure, GUI-free logic + the background `MetricsSampler` |
| `AUTOMATION/report.py` | Post-session HTML / PNG generation |
| `AUTOMATION/constants.py` | All configuration values (edit in place) |
| `AUTOMATION/build_lammps.sh` | Standalone LAMMPS build script (invoked from Step 2) |

See `ARCHITECTURE.md` for how they fit together, and `CLAUDE.md` for the coding
guidelines used when working on this project.
