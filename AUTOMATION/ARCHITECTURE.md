# LAVA architecture

Orientation for anyone (human or LLM) maintaining LAVA. Read this before
editing; it explains what each module owns, how data and control flow, and the
few non-obvious constraints that will bite you if you don't know them.

## Module map

LAVA is four flat Python modules plus a bash build script — no package tree.

```
run.py        GUI + orchestration. The App wizard (tkinter), logging setup,
              the session worker that executes runs, and main(). Imports from
              helpers, report, and constants. This is the only module that
              touches tkinter.

helpers.py    Pure, GUI-free logic: time/stamp formatting, seed parsing,
              filesystem discovery, CSV I/O, LAMMPS output parsers (ebath,
              tprof), environment probes, and the background MetricsSampler.
              No tkinter, no App. Everything here is unit-testable in isolation.

report.py     Post-session output: the interactive offline HTML report and
              backup matplotlib PNGs. Plain functions that take paths and
              parsed rows; no tkinter, no App. Entry point:
              generate_session_outputs().

constants.py  Every configuration value (colors, fonts, project tree, accepted
              input extensions, build paths, defaults). Plain Python so it can
              hold Path.home(), f-strings, tuples and sets directly. Imported
              by all three modules above. Edit values here.

setup.sh      Standalone bash script that builds a machine-matched LAMMPS
              binary. Invoked by run.py (Step 2) but also runnable by hand.
```

Import direction (no cycles):

```
constants.py  ──imported by──▶  helpers.py, report.py, run.py
helpers.py    ──imported by──▶  run.py
report.py     ──imported by──▶  run.py
```

`constants.py` imports nothing from the project. `helpers.py` and `report.py`
never import each other or `run.py`. Keep it this way — pushing a shared value
"down" into constants.py is how you avoid a cycle if two modules start needing
the same thing.

## Control flow (a session)

The wizard is four steps, each a `show_stepN` method on `App` in `run.py`:

1. **Step 0** — pick a pipeline. Only the Selection Pipeline is implemented.
2. **Step 1** — build LAMMPS. `App._build_worker` authenticates sudo (password
   from the GUI, never stored or logged), then streams `setup.sh` output into
   the log. Produces `~/.lammps-build/build/lmp`.
3. **Step 2** — pick project root + materials source, choose files per material,
   set seeds. `App._build_project_worker` copies selected files into the
   project tree and writes `PROJECT/config.json`.
4. **Step 3** — configure limits + probe interval, preview planned runs, run.
   `App._session_worker` is the core loop:
   - `_plan_runs()` expands config into potential × config × structure × seed
     specs (a material with no potentials contributes one "None" potential).
   - For each run: copy inputs into an isolated temp dir, start a
     `MetricsSampler` (from helpers), launch `mpiexec ... lmp` via `_run_proc`,
     enforce skip/stop/resource-limit aborts, collect + rename outputs, convert
     raw profile files to CSVs (helpers' parsers), append a summary row and the
     probe rows to the session CSVs.
   - At the end: `report.generate_session_outputs(...)` writes the HTML report
     and PNGs per the user's Step 3 checkboxes.

### Threading and the GUI

Workers (`_build_worker`, `_build_project_worker`, `_session_worker`,
`_run_proc`'s output pump) run on **daemon threads** so the tkinter main loop
stays responsive. They must never touch tkinter widgets directly. Two safe
channels back to the GUI:

- **Logging** — any thread calls `log.info(...)`; the `QueueHandler` puts a
  formatted line on `App.log_q`, and `App._drain_log` (a `root.after` loop on
  the main thread) moves it into the on-screen log widget.
- **`self.root.after(0, callable)`** — schedules a widget update on the main
  thread. This is how workers flip button states, update the progress bar, etc.

Session control uses three `threading.Event`s: `pause_event`, `stop_event`,
`skip_event`. `_run_proc` polls them each second to abort a running process.

## Data on disk (a project)

```
<project root>/
  PROJECT/config.json            written in Step 2; drives Step 3
  SIMULATION/MATERIALS/<mat>/     selected input files, copied in
    POTENTIAL-FILES / CONFIGURATION-FILES / STRUCTURE-FILES
  ANALYSIS/
    HISTORICAL.csv                one row per session (appended)
    SESSIONS/SESSION-<id>/
      RUNS/<run-id>/              per-run outputs (renamed) + converted CSVs
      SESSION-SUMMARY_<id>.csv    one row per run
      SESSION-HW-STATS_<id>.csv   every probe, tagged with run_id
      REPORT_<id>.html            interactive offline report
      GRAPHS/                     backup PNGs (if matplotlib present)
      SESSION_<id>.log            run-phase log (see Logging)
      ANALYSIS_<id>.log           report-phase log (see Logging)
  TEMP/                           isolated per-run working dirs (cleaned up)
```

All persisted data is CSV except `config.json`.

## Logging

LAVA uses stdlib `logging`, configured once in `run.py` (`setup_logging`).
Every module does `log = logging.getLogger(__name__)` at the top and just calls
`log.info/.warning(...)`; no module except run.py configures handlers.

- **Formatter** (shared by GUI and all files):
  `%(asctime)s [%(name)s] %(levelname)s %(message)s`, timestamps to the second.
  The `[name]` is the logger name, which gives a per-module tag for free.
- **GUI stream** — a `QueueHandler` (a ~5-line `logging.Handler` subclass in
  run.py, the only GUI-coupled logging piece) is always attached to the root
  logger and feeds the live on-screen log.
- **Per-session files** — when a session starts, `App._open_session_logfile`
  attaches two `FileHandler`s and removes them on close:
  - `SESSION_<id>.log` — filtered to logger names `run`, `helpers`, `metrics`
    (the run phase).
  - `ANALYSIS_<id>.log` — filtered to logger name `report` (the analysis phase).
  Routing is by a `_NameFilter` on each handler.
- **setup.sh** — logs independently (it's bash, and runs before any project or
  session exists) to `~/.lammps-build/AUTOMATION/SETUP.log`, timestamped, append
  mode. Multiple builds accumulate there.

`MetricsSampler` logs under the name `metrics` (not `helpers`) so its probe
failures — previously swallowed silently — are visible and distinctly tagged.

## Gotchas / footguns

- **Values mirrored in `setup.sh`.** `CUDA_MIN`, the build root
  (`~/.lammps-build`), and the binary path (`build/lmp`) exist in BOTH
  `constants.py` (Python) and `setup.sh` (bash). Nothing syncs them. Change one,
  change the other. (Making bash read the Python constants would add more
  complexity than it removes, so they are deliberately kept as parallel
  definitions.)
- **`constants.py` must stay pure data + stdlib.** It's a plain module imported
  at load time by everything. Don't import project modules into it (instant
  cycle) and don't do heavy work at import.
- **Never touch tkinter from a worker thread.** Use `log.*` or
  `self.root.after(0, ...)`. Direct widget access off the main thread will crash
  or hang intermittently.
- **The sudo password** is handed to `_build_worker` as a local, used to
  authenticate via stdin, scrubbed, and never written to any log or file. Keep
  it that way — do not log it, even at debug level.
- **`config.json` schema drift.** `load_config_and_run` validates required keys
  and sends the user back to Step 2 on an old/incompatible config rather than
  crashing. If you add required keys, update that check.
- **Report functions are pure by design.** `report.py` takes explicit arguments
  and a plain `want_html`/`want_png` boolean, not the App or its tkinter vars.
  Keep new report code that way so it stays testable without a GUI.

## Testing without a display

`helpers.py` and `report.py` need no display and are directly testable. The App
and full logging can be exercised headless under `xvfb-run` (Linux) — construct
`tk.Tk()`, build a step, `root.update()`, `root.destroy()`. This is enough to
catch reference errors in the wizard methods.
