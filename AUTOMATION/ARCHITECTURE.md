# LAVA architecture

Orientation for anyone — human or LLM — maintaining LAVA. Read this before
editing. It explains what each module owns, how data and control flow, the
import rules that prevent cycles, and the non-obvious constraints that will bite
you if you don't know them.

The guiding principle of the split is **single-file focus**: each module is
written so you can understand and safely change it with only that file open,
plus the short list of names it imports. Each file's own top-of-file docstring
says what it owns and what it must never do. This document is the map that ties
them together.

## Module map

LAVA's Python lives in `AUTOMATION/` — four flat modules plus a bash build
script, no package tree. Two entrypoint scripts sit at the repo root and handle
setup + launch.

```
entrypoint.ps1   Repo root. Windows bootstrap: installs WSL + Ubuntu, copies
                 the repo in, then calls entrypoint.sh. Idempotent. Not imported.

entrypoint.sh    Repo root. Linux/WSL bootstrap: updates packages, installs
                 dependencies, then `cd AUTOMATION/ && python3 run.py`.

AUTOMATION/
  constants.py   Every configuration value: version/branding, the colour
                 palette, fonts, build paths, the project tree, accepted input
                 extensions, output-rename rules, community-material markers
                 (UNTESTED_PREFIX, MATERIAL_INFO_NAME, EDITOR_ROWS), and default
                 limits. Plain data + stdlib only. ~135 lines.

  helpers.py     All pure, GUI-free logic: time/stamp formatting, seed parsing,
                 community-material helpers (slugify, untested-name handling,
                 MATERIAL-INFO.json read/write), filesystem + past-session
                 discovery, CSV I/O, LAMMPS output parsers (ebath, tprof),
                 environment/hardware probes, and the background MetricsSampler.
                 No tkinter, no App. Unit-testable in isolation. ~490 lines.

  report.py      Post-session output: the interactive offline HTML report and
                 backup matplotlib PNGs. Pure functions that take paths, parsed
                 rows, plain want_html/want_png booleans, and a `log` callable —
                 never the App or a tkinter var. Entry point:
                 generate_session_outputs(). ~660 lines.

  run.py         GUI + orchestration. The App wizard (tkinter), the scrolling
                 machinery, logging to the on-screen log, the worker threads
                 (build / project-build / session), MaterialEditor (the add/edit
                 window plus the git "contribute" flow), and main(). The ONLY
                 module that touches tkinter. ~2110 lines.

  setup.sh       Standalone bash script that builds a machine-matched LAMMPS
                 binary. Invoked by run.py (Step 1) but also runnable by hand.
                 Named by constants.BUILD_SCRIPT.
```

## Import direction (no cycles)

```
constants.py  ──imported by──▶  helpers.py, report.py, run.py
helpers.py    ──imported by──▶  report.py, run.py
report.py     ──imported by──▶  run.py
```

`constants.py` imports nothing from the project (only stdlib `pathlib`).
`helpers.py` imports only from `constants`. `report.py` imports from `constants`
and `helpers`. `run.py` imports from all three. Nothing imports `run.py`.

**Keep it this way.** If two modules start needing the same value, push it
*down* into `constants.py` (or a pure helper into `helpers.py`) rather than
importing "sideways" or "up". That is how the graph stays acyclic.

## Control flow (a session)

The wizard is a sequence of `show_*` methods on `App` in `run.py`:

- **Step 0** (`show_step0`) — two choices: **Execute New Session** (→ Step 1) or
  **View Previous Sessions** (`show_previous_sessions`, a grid built from
  `helpers.scan_sessions`, one row per session with a button that opens its
  `REPORT_<id>.html`).
- **Step 1** (`show_step1`) — build LAMMPS. `App._build_worker` authenticates
  sudo (password from the GUI, never stored or logged), then streams
  `setup.sh` output into the log. Produces `~/.lammps-build/build/lmp`.
- **Step 2** (`show_step2`) — the project root is fixed to the repo root
  (`helpers.repo_root()`, one level above `AUTOMATION/`) and materials always
  live in `ROOT/SIMULATION/MATERIALS`; the user does not pick them. A seed bar
  sits on top, per-material asset lists below, and an "Add material profile"
  button opens `MaterialEditor`. `App._build_project_worker` copies the ticked
  files into the project tree and writes `PROJECT/config.json`.
- **Step 3** (`show_step3`) — configure limits + probe interval, preview planned
  runs, run. `App._session_worker` is the core loop:
  - `_plan_runs()` expands config into **per-material** potential × config ×
    structure × seed specs. Materials are never mixed; a material with no
    potentials contributes one "None" potential.
  - For each run: copy inputs into an isolated temp dir, start a
    `helpers.MetricsSampler`, launch `mpiexec … lmp` via `_run_proc`, enforce
    skip/stop/resource-limit aborts, collect + rename outputs, convert raw
    profile files to CSVs (`helpers.parse_tprof` / `parse_ebath`), append a
    summary row and the probe rows to the session CSVs.
  - At the end: `report.generate_session_outputs(session_dir, session_id,
    summary_csv, hw_csv, want_html, want_png, self.log_line)` writes the HTML
    report and PNGs per the Step 3 checkboxes.

### Adding / contributing a material (MaterialEditor)

`MaterialEditor` (in `run.py`) is one window for both add and edit. It gathers
files from anywhere on the filesystem via a repeatable multi-select dialog,
shows them as removable chips, warns on unrecognised extensions, and writes the
material folder into `SIMULATION/MATERIALS` **first**. If "contribute" is ticked
it then runs a git flow on a daemon thread: the folder is committed with the
`[UNTESTED]-` prefix on a branch named `material/<slug>-<utc>`, pushed, and a PR
is opened (via `gh` if present, else the browser compare URL). Untested
materials are surfaced with a warning tag in Step 2 and a banner in Step 3.

### Threading and the GUI

Workers (`_build_worker`, `_build_project_worker`, `_session_worker`,
`MaterialEditor._contribute_worker`, `_run_proc`'s output pump) run on **daemon
threads** so the tkinter main loop stays responsive. They must never touch
tkinter widgets directly. Two safe channels back to the GUI:

- **`self.log_line(text)`** — puts a line on `App.log_q`; `App._drain_log` (a
  `root.after` loop on the main thread) moves it into the on-screen log widget.
- **`self.root.after(0, callable)`** — schedules a widget update on the main
  thread (flip button states, update the progress bar, etc.).

Session control uses three `threading.Event`s: `pause_event`, `stop_event`,
`skip_event`. `_run_proc` polls them each second to abort a running process.

### Scrolling

`run.py` gives the whole window a scrollable viewport (`_build_shell` +
`_on_body_scroll`, scrollbar auto-hidden when content fits) AND keeps inner
scroll regions (the material list, the previous-sessions grid, the editor's
asset area). A single wheel dispatcher (`_on_mousewheel`) routes the wheel to
whichever region the pointer is over, so nested scrolling never fights itself.
Inner scroll canvases call `self._register_scrollable(canvas)`.

## Data on disk (a project)

```
<repo root>/                     (this is the project root)
  PROJECT/config.json            written in Step 2; drives Step 3
  SIMULATION/MATERIALS/<mat>/     selected input files, copied in
    POTENTIAL-FILES / CONFIGURATION-FILES / STRUCTURE-FILES
    MATERIAL-INFO.json            optional metadata (name, description, tested)
  ANALYSIS/
    HISTORICAL.csv                one row per session (appended)
    SESSIONS/SESSION-<id>/
      RUNS/<run-id>/              per-run outputs (renamed) + converted CSVs
      SESSION-SUMMARY_<id>.csv    one row per run
      SESSION-HW-STATS_<id>.csv   every probe, tagged with run_id
      SESSION-INFO_<id>.json      machine specs + session timing
      REPORT_<id>.html            interactive offline report
      SESSION_<id>.log            run-phase log
  TEMP/                           isolated per-run working dirs (cleaned up)
```

All persisted tabular data is CSV; `config.json`, `SESSION-INFO`, and
`MATERIAL-INFO` are JSON.

## Logging

`run.py` writes to the on-screen log via `self.log_line` (thread-safe queue) and
mirrors the run phase to `SESSION_<id>.log` inside the session folder. Pure
modules do not configure logging: `helpers.py` is silent by default (a probe
failure in `MetricsSampler` must never crash a run), and `report.py` emits
through the `log` callable it is handed (in the app that is `App.log_line`; in a
test it can be `print` or `list.append`). `setup.sh` logs independently to
`~/.lammps-build/AUTOMATION/SETUP.log`.

## Gotchas / footguns

- **Values mirrored in `setup.sh`.** `CUDA_MIN`, the build root
  (`~/.lammps-build`), and the binary path (`build/lmp`) exist in BOTH
  `constants.py` (Python) and `setup.sh` (bash). Nothing syncs them. Change one,
  change the other.
- **`constants.py` must stay pure data + stdlib.** Don't import project modules
  into it (instant cycle) and don't do heavy work at import.
- **Never touch tkinter from a worker thread.** Use `self.log_line` or
  `self.root.after(0, ...)`.
- **The sudo password** is handed to `_build_worker` as a local, used to
  authenticate via stdin, scrubbed, and never written to any log or file.
- **`config.json` schema drift.** `load_config_and_run` validates required keys
  and sends the user back to Step 2 on an old/incompatible config rather than
  crashing. If you add required keys, update that check.
- **Report functions are pure by design.** `report.py` takes explicit arguments
  and plain `want_html`/`want_png` booleans plus a `log` callable, not the App
  or its tkinter vars. Keep new report code that way so it stays testable
  without a GUI.
- **`MaterialEditor` resolves its own materials root** from `repo_root()` rather
  than trusting a passed value, because it can open before Step 2 has populated
  the path. Don't reintroduce a dependency on that StringVar.

## Testing without a display

`constants.py`, `helpers.py`, and `report.py` need no display and are directly
testable — import them from inside `AUTOMATION/` and call functions with plain
arguments (e.g. `helpers.parse_seeds`, `report.generate_session_outputs` on
fixture CSVs with `log=print`). The App and full wizard can be exercised
headless under `xvfb-run` (Linux): construct `tk.Tk()`, `run.App(root)`, call a
`show_*` method, `root.update()`, `root.destroy()`. That is enough to catch
reference errors across the wizard methods and confirm the four modules import
and wire together.
