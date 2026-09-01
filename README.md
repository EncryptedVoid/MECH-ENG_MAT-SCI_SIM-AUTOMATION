# LAVA — LAMMPS Automation Validation Aid

LAVA is a friendly, click-through app for running batches of LAMMPS molecular-dynamics
simulations and turning the results into a clean, interactive report. You add your
materials, tick which files to run, press start, and it runs every combination for you
while recording how your computer performed. When it finishes, it builds an HTML report
you open in any web browser.

You do **not** need to be a programmer to use it. This guide walks you through
everything, one step at a time.

*Project developed by University of the People Computer Science student "Ashiq Arib
Gazi" and BUET Mechanical Engineering student "Kazi Rubaiyat Mustafiz".*

---

## See it in action

https://github.com/user-attachments/assets/06245dcf-2dc0-4990-9824-5278c23d4e81

---

## What LAVA does, in plain terms

- Your simulations are grouped by **material**. Each material has three kinds of input
  files: **potentials**, **configurations**, and **structures**.
- LAVA runs LAMMPS once for **every combination** of a material's files (and every random
  seed you ask for). One combination = one "run". All the runs together = one "session".
  Materials are never mixed — each material's runs stay within that material.
- While each run executes, LAVA measures your CPU, RAM, and (if you have an NVIDIA card)
  GPU usage and temperature.
- At the end it saves spreadsheets (CSV files) of everything and builds an interactive
  **HTML report** with graphs you can hover over and zoom into.

---

## Getting LAVA running

Everything below this section is just advice — *when* to do things, not more steps to
memorize. Setup itself is a handful of double-clicks on Windows, or one pasted line on
Linux. After the first time, opening LAVA is a single double-click (Windows) or the same
one line (Linux).

### Windows 11 — no terminal, no Git

You never open a terminal or type a command. Here's the whole thing, start to finish:

1. **Download the project.** On the GitHub page, click the green **Code** button →
   **Download ZIP**. Then **extract** the ZIP (right-click the downloaded file →
   **Extract All**). No Git required.
2. **Double-click `START-LAVA.bat`** inside the extracted folder. When Windows asks
   *"Do you want to allow this app to make changes?"*, click **Yes**.
3. **Restart when it tells you to.** The first run installs WSL + Ubuntu, which needs one
   reboot. Restart your computer.
4. **Double-click `Start LAVA` on your Desktop.** The launcher copies itself (and the
   script) to your Desktop, so from here on you start it from there.
5. **Create your Ubuntu account.** A black Ubuntu window appears asking for a **username**
   and **password** — type them in (pick something simple and **write the password
   down**; it won't show on screen as you type — that's normal). Then close that window
   and **double-click `Start LAVA`** once more.
6. **From then on: just double-click `Start LAVA`.** Every launch opens WSL, freshens
   packages, and starts LAVA automatically.

> **Just want to reopen LAVA later?** Double-click **Start LAVA on your Desktop**. That's
> it — one double-click, forever.

**Why the reboot and the Ubuntu prompt can't be skipped:** the restart (step 3) and the
Ubuntu username/password (step 5) are required by **Windows and WSL themselves**, not by
LAVA. Installing WSL has to enable a Windows feature that only takes effect after a
reboot, and Ubuntu forces an account-creation prompt on first boot that Microsoft gives no
supported way to skip. No launcher can remove those. So the honest floor is about three
double-clicks and one reboot — after that, it's permanently one double-click. (Later,
LAVA will also ask for your password once when it builds LAMMPS; that's the same Ubuntu
password, used only to install what the build needs.)

### Linux

1. Open a terminal.
2. Paste this and press Enter:

   ```bash
   git clone https://github.com/EncryptedVoid/LAVA_LAMMPS-Automation-Validation-Aid.git && cd LAVA_LAMMPS-Automation-Validation-Aid && chmod +x entrypoint.sh && ./entrypoint.sh
   ```

`entrypoint.sh` updates your packages, installs everything LAVA needs (`python3`, `pip`,
`tk`, `git`, and the Python helpers `psutil`, `plotly`, `matplotlib`, `numpy`), then
launches LAVA. Re-run `./entrypoint.sh` any time to open it again — it always freshens
packages first, and it auto-detects `apt`, `dnf`, or `pacman`.

---

## Using LAVA — the home screen

When LAVA opens you'll see a banner telling you whether a usable LAMMPS build exists yet,
and three big buttons:

- **Setup LAMMPS Build** — build the simulation engine for this computer. You do this
  once.
- **Start Session** — choose materials and files, then run them.
- **View Past Sessions** — browse and open reports from earlier runs.

**Start Session** and **View Past Sessions** stay locked (greyed out) until a valid build
exists. So the very first thing to do on a new computer is set up the build.

---

## When to set up your LAMMPS build

Do this **once per computer**, the first time you use LAVA. After that, LAVA detects the
existing build and the button just says it's ready — you can skip straight to running.

Rebuild only if you want to upgrade LAMMPS, or if you've changed your hardware (for
example, added an NVIDIA graphics card).

**To build:**

1. Press **Setup LAMMPS Build** on the home screen.
2. Type your computer password into the **sudo password** box. (This is the same Linux
   password from setup. LAVA uses it only to install the pieces LAMMPS needs — it is
   **never saved, shown, or written to any file**.)
3. Press **Build LAMMPS** and wait. The first build can take a while (10–30 minutes) as
   it downloads and compiles. You'll see progress in the Log box at the bottom.
4. When it's done, press **Verify build**. Once it verifies, you can go straight to a
   session.

If you have an NVIDIA graphics card, LAVA builds a GPU-capable version automatically. If
not, it builds a CPU version — that's completely fine and expected.

---

## When and how to add a new material profile

LAVA keeps all your materials **inside the app itself**, so you don't have to hand-build
folders anywhere. Add a material whenever you have a new set of input files you want to
run.

1. Press **Start Session** on the home screen.
2. On the **Choose materials & seeds** screen, press **+ Add material profile**.
3. In the window that opens, give the material a **name**, then add its files. You can
   pick files from anywhere on your computer — they show up as little removable chips, and
   LAVA warns you if a file has an unexpected extension. A material needs:
   - **configuration** file(s) — the LAMMPS input decks (required),
   - **structure** file(s) — the structure data (required),
   - **potential** file(s) — optional. If a material has none, LAVA still runs it, just
     without a potential file.
4. Save. The new material appears in the checklist on the same screen, ready to run.

To change a material later, press **Edit** next to it on that same screen.

> **Contributing (optional):** if you tick "contribute" while adding a material, LAVA can
> submit it to the shared project as an **[UNTESTED]** profile (it opens a pull request
> for you). Untested profiles are marked with a ⚠ warning tag so everyone knows they
> haven't been validated yet — you can still run them, but some combinations may fail.

---

## Running a session

1. Press **Start Session**, then set your **seeds** — the random seeds to run. You can
   list several:
   - `1` → just seed 1
   - `1,2,3` → seeds 1, 2, and 3
   - `1-5` → seeds 1 through 5
   - `1,3,7-9` → seeds 1, 3, 7, 8, 9
   More seeds means more runs.
2. Every material shows a checkbox for each of its files, **all ticked by default**.
   Untick anything you don't want this time (or use a material's master checkbox to toggle
   all its files at once).
3. Press **Build project + save config**. LAVA copies your chosen files into the project
   and remembers your settings.
4. On the run screen, optionally set **resource limits** (max CPU %, max RAM %, and max
   GPU % if you have a GPU) and **Probe every (s)** — how often to record hardware stats
   (60 seconds by default). The defaults are sensible; you can leave them. If a run pushes
   past a limit, LAVA stops that run to protect your machine.
5. Leave **Generate HTML report** and **Generate PNG graphs** ticked to get the report at
   the end. The **preview list** shows every run that's about to happen.
6. Press **Start session**.

While it runs you can:
- **Pause / Resume** — hold before the next run (handy if you need to step away).
- **Skip next run** — stop the current run immediately and move on (marked "aborted
  early", not failed).
- **Stop** — end the whole session.
- **Reconfigure** — go back to the materials screen to change files or seeds.

You'll see a live estimate of how much time is left, updating as it learns how long your
runs take. When it finishes, press **OPEN SESSION DATA** to jump to the results folder.

---

## How to find old sessions

Press **View Past Sessions** on the home screen. LAVA shows a table of every session it
has ever run (this comes from `ANALYSIS/HISTORICAL.csv`), one row per session.

**Double-click any row** to open that session's folder — the report, spreadsheets, logs,
and graphs are all inside. Just double-click the `REPORT_...html` file to view it.

---

## What you get at the end (the results)

Everything lands inside the project, in the **ANALYSIS** folder:

```
<project>/
└── ANALYSIS/
    ├── HISTORICAL.csv                  ← one row per session, all-time summary
    └── SESSIONS/
        └── SESSION-20260828143000Z/    ← one folder per session (named by date/time)
            ├── REPORT_...html          ← ★ open this in a web browser
            ├── SESSION-SUMMARY_...csv   ← one row per run: files, seed, status, timing
            ├── SESSION-HW-STATS_...csv  ← hardware readings over the whole session
            ├── SESSION-INFO_...json     ← your machine's specs + start/end times
            ├── SESSION_...log           ← a plain text log of what happened
            ├── GRAPHS/                  ← backup PNG images of the graphs
            └── RUNS/
                ├── RUN-0001-.../         ← one folder per run
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

> Session filenames end in a **Z** (UTC time) or **L** (your local time). LAVA defaults to
> UTC so a shared report doesn't reveal your timezone; you can switch this on the build
> screen.

### The report (`REPORT_...html`)

Double-click it to open in Chrome, Edge, Firefox, or any browser. It works completely
offline — you can email it, and it opens anywhere. It has two tabs:

- **Hardware & Session** — your machine's specs (CPU, RAM, GPU, and when it ran), a table
  of every run color-coded by result (green = success, red = failed, amber = aborted), and
  two timeline graphs: temperatures over the session and resource usage over the session.
  Colored bands on the timelines mark which run was happening when.
- **Per-run Analysis** — a searchable list of runs. Click any successful run to see its
  temperature profile and electron-bath energy graphs. You can hover for exact values and
  drag to zoom.

If **every** run in a session fails or is aborted, LAVA skips the graphs and writes a
short report saying there wasn't enough data — that's expected, not a bug.

---

## Common questions

**Do I have to rebuild LAMMPS every time?**
No. Only the first time on a given computer. After that, LAVA detects the existing build
and the home screen unlocks the session buttons.

**Do I have to reinstall WSL / Ubuntu every time on Windows?**
No. The launcher detects that WSL and Ubuntu are already there and skips straight to
launching. The reboot and Ubuntu account setup only happen on the very first run — after
that it's a single double-click of **Start LAVA** on your Desktop.

**I don't see `START-LAVA.bat` — I only see `START-LAVA`.**
Same file. Windows often hides the `.bat` at the end. Double-click it anyway. (The copy on
your Desktop shows as **Start LAVA**.)

**Where do my materials live? Do I organize folders myself?**
No — LAVA manages them for you. Add materials in the app with **+ Add material profile**;
they're stored inside the project at `SIMULATION/MATERIALS`. You never build those folders
by hand.

**My report shows no GPU / blank GPU graphs. Is it broken?**
No. That just means the computer has no NVIDIA graphics card, so there's nothing to
measure. CPU and RAM still record normally.

**A run says it "failed." How do I find out why?**
Open that run's folder (the one starting with `[FAILED]-`) and read `LAMMPS.log` or
`RUN_XXX.log` — the error from LAMMPS is in there.

**Can I change my file selection later?**
Yes. On the run screen, press **Reconfigure** to go back and pick different files or seeds.

**It says my project config is out of date.**
That's fine — it means the settings file is from an older version. Say yes when it offers
to reconfigure; your data folders are untouched, only the settings are rewritten.

---

## Getting help

If something goes wrong, the **Log** box at the bottom of the LAVA window and the
`SESSION_...log` file in your session folder usually explain what happened. Copy that text
when asking for help.

---

*Made by ASHIQ GAZI · Ashiq.live · LAVA version 0.8.0*
