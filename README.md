# LAVA — LAMMPS Automation Validation Aid

LAVA is a friendly, click-through app for running batches of LAMMPS molecular-dynamics
simulations and turning the results into a clean, interactive report. You point it at
your input files, tick which ones to run, press start, and it runs every combination
for you while recording how your computer performed. When it finishes, it builds an
HTML report you open in any web browser.

You do **not** need to be a programmer to use it. This guide walks you through
everything, one step at a time.

*Project developed in conjunction with BUET Mechanical Engineering student "Kazi
Rubaiyat Mustafix" and University of the People Computer Science student "Ashiq Arib
Gazi".*

---

## See it in action

**Watch the demo:**

https://github.com/user-attachments/assets/LAVA-DEMO.mp4

<!-- If the video above does not play inline, download it directly: [LAVA-DEMO.mp4](./LAVA-DEMO.mp4) -->

**Sample outputs**

Want to see what LAVA produces before installing anything? A complete example session
lives in [`DEMO-ANALYSIS/`](./DEMO-ANALYSIS/). The best-looking session to explore is
in [`DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/`](./DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/):

- 📊 **[REPORT_20260827214250.html](./DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/REPORT_20260827214250.html)**
  — the interactive HTML report. Download it and open in any browser to explore the graphs, hover for values, and zoom in.
- 🖼️ **[HW-USAGE_20260827214250.png](./DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/HW-USAGE_20260827214250.png)**
  — a hardware-usage graph showing CPU, RAM, and GPU over the session.
- 📈 **[SESSION-HW-STATS_20260827214250.csv](./DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/SESSION-HW-STATS_20260827214250.csv)**
  — the raw hardware readings behind the graphs.
- 📝 **[SESSION_20260827214250.log](./DEMO-ANALYSIS/SESSIONS/SESSION-20260827214250/SESSION_20260827214250.log)**
  — the plain-text log of everything that happened during the session.

> GitHub can't render an HTML report in the browser directly — download it (or use the
> "Download raw file" button) and open the file locally.

---

## What LAVA does, in plain terms

- You have three kinds of input files: **potentials**, **configurations**, and
  **structures**, grouped by material.
- LAVA runs LAMMPS once for **every combination** of those files (and every random
  seed you ask for). One combination = one "run". All the runs together = one
  "session".
- While each run executes, LAVA measures your CPU, RAM, and (if you have an NVIDIA
  card) GPU usage and temperature.
- At the end it saves spreadsheets (CSV files) of everything and builds an interactive
  **HTML report** with graphs you can hover over and zoom into.

---

## Before you start: what you need

- A computer running **Windows 11** or **Linux**.
- Your LAMMPS input files.
- About 30–60 minutes the first time, mostly waiting for things to install.

You will type a few commands into a black window called a **terminal**. Don't worry —
you can copy and paste every one of them from this guide. A line starting with `$` means
"type this into the terminal and press Enter" (don't type the `$` itself).

---

## Part 1 — Windows 11 setup

Windows can't run LAMMPS directly, so we use a free Microsoft feature called **WSL**
(Windows Subsystem for Linux). It gives you a small Linux system inside Windows. LAVA's
window still appears like a normal Windows app.

### Step 1.1 — Install WSL

1. Click the Start menu, type **PowerShell**, right-click **Windows PowerShell**, and
   choose **Run as administrator**.
2. In the blue window, type this and press Enter:

   ```
   wsl --install
   ```

3. Let it finish, then **restart your computer** when it asks.
4. After restarting, a window will pop up asking you to create a **username** and
   **password** for your Linux system. Pick something simple you'll remember. **Write
   the password down** — you'll need it later, and it won't show on screen as you type
   it (that's normal).

You now have Ubuntu (a kind of Linux) installed. From now on, open it by clicking Start
and typing **Ubuntu**.

### Step 1.2 — Install the basics

Open **Ubuntu** and paste these lines one at a time (press Enter after each). It will
ask for the password you just created:

```
$ sudo apt update
$ sudo apt install -y python3 python3-pip python3-tk git
```

### Step 1.3 — Install the Python helpers LAVA uses

```
$ pip3 install --break-system-packages psutil plotly matplotlib numpy
```

What these are for (you don't need to memorize this):
- **psutil** — measures CPU and RAM. Required.
- **plotly** — builds the interactive graphs in the report. Needed for the HTML report.
- **matplotlib** and **numpy** — make backup image (PNG) versions of the graphs.

### Step 1.4 — Get the LAVA files onto your computer

Put the project folder somewhere easy to find inside Ubuntu. If your files are already
on Windows (for example in your Documents), you can reach them from Ubuntu — your
Windows drive lives at `/mnt/c/`. For example, your Documents folder is usually:

```
/mnt/c/Users/YOUR-WINDOWS-NAME/Documents
```

Move into the folder that contains `run.py` and `build_lammps.sh`. For example:

```
$ cd /mnt/c/Users/YOUR-WINDOWS-NAME/Documents/LAVA
```

(Replace the path with wherever you actually put the files.)

### Step 1.5 — Start LAVA

```
$ python3 run.py
```

The LAVA window should appear. If it does, skip ahead to **Part 3 — Using LAVA**.

> **If nothing appears:** on Windows 11 the graphical part usually works automatically.
> If you get an error about a display, close Ubuntu, make sure Windows is fully updated
> (Start → Settings → Windows Update), restart, and try again.

---

## Part 2 — Linux setup

If you're already on Linux, this is shorter.

### Step 2.1 — Install the basics

Open a terminal and run (this example is for Ubuntu/Debian; if you use Fedora or Arch,
use `dnf` or `pacman` instead of `apt`):

```
$ sudo apt update
$ sudo apt install -y python3 python3-pip python3-tk git
```

### Step 2.2 — Install the Python helpers

```
$ pip3 install --break-system-packages psutil plotly matplotlib numpy
```

### Step 2.3 — Start LAVA

Move into the folder with `run.py` and launch it:

```
$ cd /path/to/LAVA
$ python3 run.py
```

The LAVA window appears.

---

## Part 3 — Using LAVA

LAVA walks you through four steps. Each step only appears when you're ready for it.

### Step 0 — Choose a pipeline

You'll see three choices. Pick **Selection Pipeline** — it runs the specific files you
choose. (The other two, Auto-Generate and Headless, aren't finished yet.)

### Step 1 — Set up LAMMPS (first time only)

LAMMPS is the simulation engine. LAVA can build it for you.

1. Type your computer password into the **sudo password** box. (This is the same Linux
   password from setup. LAVA uses it only to install the pieces LAMMPS needs — it is
   **never saved, shown, or written to any file**.)
2. Press **Build LAMMPS** and wait. The first build can take a while (10–30 minutes) as
   it downloads and compiles. You'll see progress in the Log box at the bottom.
3. When it's done, press **Verify build**, then **Next**.

If you have an NVIDIA graphics card, LAVA builds a GPU-capable version automatically. If
not, it builds a CPU version — that's completely fine and expected.

You only do this once per computer. Next time, LAVA sees the existing build and lets you
skip straight ahead.

### Step 2 — Set up your project

This is where you tell LAVA about your files.

1. **Project root** — pick (or make) an empty folder where LAVA will keep everything:
   your copied inputs, results, and reports.
2. **Materials source** — pick the folder that contains your material folders (see
   *"How to organize your material files"* below).
3. **Temp folder** — leave blank unless you want runs to execute somewhere specific.
4. **Seeds** — the random seeds to run. You can list several:
   - `1` → just seed 1
   - `1,2,3` → seeds 1, 2, and 3
   - `1-5` → seeds 1 through 5
   - `1,3,7-9` → seeds 1, 3, 7, 8, 9
   Every seed becomes part of the combinations, so more seeds means more runs.
5. A list of your materials appears with a checkbox for every file, **all ticked by
   default**. Untick anything you don't want to run this time.
6. Press **Build project + save config**. LAVA copies your chosen files into the project
   and remembers your settings, so next time it jumps straight to running.

### Step 3 — Run a session

1. Set **resource limits** if you like (max CPU %, max RAM %, and max GPU % if you have
   a GPU). If a run pushes past a limit, LAVA stops that run to protect your machine.
   The defaults are sensible — you can leave them.
2. Set **Probe every (s)** — how often to record hardware stats. 60 seconds is the
   default.
3. Leave **Generate HTML report** and **Generate PNG graphs** ticked to get the report
   at the end.
4. The **preview list** shows every run that's about to happen.
5. Press **Start session**.

While it runs you can:
- **Pause / Resume** — hold before the next run (handy if you need to step away).
- **Skip next run** — stop the current run immediately and move on (it's marked
  "aborted early", not failed).
- **Stop** — end the whole session.

You'll see a live estimate of how much time is left, updating as it learns how long your
runs take. When it finishes, press **OPEN SESSION DATA** to jump to the results folder.

---

## How to organize your material files

LAVA groups files by material. Each material is a folder, and inside it are three
folders with these **exact names**:

```
YOUR-MATERIALS-FOLDER/
├── Zinc-Oxide/
│   ├── POTENTIAL-FILES/        (potential files, e.g. bp.sw — optional)
│   ├── CONFIGURATION-FILES/    (LAMMPS input decks, e.g. in.zno_kappa)
│   └── STRUCTURE-FILES/        (structure data, e.g. zno_L30.data)
├── Black-Phosphorus/
│   ├── POTENTIAL-FILES/
│   ├── CONFIGURATION-FILES/
│   └── STRUCTURE-FILES/
└── ...add as many materials as you like...
```

**To add a new material:** make a new folder next to the others, give it the three
subfolders above, drop your files in, and pick this same materials source folder again
in Step 2. The new material will appear in the checklist automatically.

**A note on potentials:** the `POTENTIAL-FILES` folder is optional. If a material has no
potential files, LAVA still runs it — it just runs each configuration-and-structure
combination without one, and tells you so.

**Important:** configuration and structure files are required for every material. A
material missing either one is skipped.

---

## What you get at the end (the results)

Everything lands inside your project root. The important part is the **SESSIONS** folder:

```
YOUR-PROJECT/
└── ANALYSIS/
    ├── HISTORICAL.csv                  ← one row per session, all-time summary
    └── SESSIONS/
        └── SESSION-20260828143000/     ← one folder per session (named by date/time)
            ├── REPORT_...html          ← ★ open this in a web browser
            ├── SESSION-SUMMARY_...csv   ← one row per run: files, seed, status, timing
            ├── SESSION-HW-STATS_...csv  ← hardware readings over the whole session
            ├── SESSION-INFO_...json     ← your machine's specs + start/end times (UTC)
            ├── SESSION_...log           ← a plain text log of what happened
            ├── GRAPHS/                  ← backup PNG images of the graphs
            └── RUNS/
                ├── RUN-0001-.../        ← one folder per run
                │   ├── TEMP-PROFILE.txt & .csv    (temperature profile)
                │   ├── ELECTRON-BATH.txt & .csv   (electron-bath energy)
                │   ├── LAMMPS.log                 (LAMMPS' own output)
                │   └── RUN_001.log                (this run's console output)
                └── RUN-0002-.../ ...
```

Run folders are named so you can tell at a glance how each run went:
- `RUN-0003-...` — finished normally.
- `[FAILED]-RUN-0003-...` — LAMMPS reported an error.
- `[ABORTED]-RUN-0003-...` — you skipped it, or it hit a resource limit.

### The report (`REPORT_...html`)

Double-click it to open in Chrome, Edge, Firefox, or any browser. It works completely
offline — you can email it, and it opens anywhere. It has two tabs:

- **Hardware & Session** — your machine's specs (CPU, RAM, GPU, and when it ran), a
  table of every run color-coded by result (green = success, red = failed, amber =
  aborted), and two timeline graphs: temperatures over the session and resource usage
  over the session. Colored bands on the timelines mark which run was happening when.
- **Per-run Analysis** — a searchable list of runs. Click any successful run to see its
  temperature profile and electron-bath energy graphs. You can hover for exact values
  and drag to zoom.

If **every** run in a session fails or is aborted, LAVA skips the graphs and writes a
short report saying there wasn't enough data — that's expected, not a bug.

---

## Common questions

**Do I have to rebuild LAMMPS every time?**
No. Only the first time on a given computer. After that, Step 1 detects the existing
build and lets you continue.

**My report shows no GPU / blank GPU graphs. Is it broken?**
No. That just means the computer has no NVIDIA graphics card, so there's nothing to
measure. CPU and RAM still record normally.

**A run says it "failed." How do I find out why?**
Open that run's folder (the one starting with `[FAILED]-`) and read `LAMMPS.log` or
`RUN_XXX.log` — the error from LAMMPS is in there.

**Can I change my file selection later?**
Yes. On Step 3, press **Reconfigure** to go back to Step 2 and pick different files or
seeds.

**I updated LAVA and it says my project config is out of date.**
That's fine — it means the settings file is from an older version. Say yes when it
offers to reconfigure; your data folders are untouched, only the settings are rewritten.

---

## Getting help

If something goes wrong, the **Log** box at the bottom of the LAVA window and the
`SESSION_...log` file in your session folder usually explain what happened. Copy that
text when asking for help.

---

*Made by ASHIQ GAZI · Ashiq.live · LAVA version 0.4.0*
