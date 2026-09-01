#!/usr/bin/env python3
"""
LAVA report generation — offline HTML + backup PNG graphs.
==========================================================

WHAT THIS FILE IS
-----------------
Everything that turns a finished session's CSVs into human-facing output:
* an interactive, fully-OFFLINE HTML report (Plotly is inlined into the file,
  so it opens with no network), with a hardware/session tab and a per-run tab
  showing each run's temperature profile and electron-bath energy; and
* backup PNG graphs via matplotlib, when matplotlib is installed.

The public entry point is generate_session_outputs(). Everything else in this
module is a private helper it calls.

PURE BY DESIGN
--------------
These functions take explicit arguments — paths, parsed rows, plain booleans
(want_html / want_png), and a `log` callable — and never see the App or any
tkinter variable. That is deliberate: it keeps report generation testable with
no display. When you add report code, keep it that way. `log` is any function
taking one string; in the app it is App.log_line, but in a test it can be
print or a list's .append.

WHO IMPORTS IT / WHAT IT IMPORTS
--------------------------------
Imported by run.py (its session worker calls generate_session_outputs at the
end of a session). It imports the stdlib, FOOTER from constants.py, and
fmt_hms + script_dir from helpers.py. It NEVER imports run.py, and run.py's GUI
never leaks in here:

    constants.py, helpers.py  --imported by-->  report.py  --imported by-->  run.py

WORKING ON THIS FILE (for humans and LLMs)
------------------------------------------
* You can understand and change this file with only itself, plus the few names
  it imports (FOOTER, fmt_hms, script_dir). You do not need run.py open.
* matplotlib/plotly are optional. Missing matplotlib -> no PNGs (that path is
  guarded). Plotly is inlined if a copy is found next to the app or inside the
  installed plotly package, else a one-time CDN download is cached, else a CDN
  <script> tag is used as a last resort.
* Do not reintroduce App/tkinter references. Report generation must stay pure.
"""

import os
import csv
import glob
import json
import urllib.request
from pathlib import Path

from constants import FOOTER
from helpers import fmt_hms, script_dir


def generate_session_outputs(session_dir, session_id, summary_csv, hw_csv,
                             want_html, want_png, log):
    """Generate the HTML report and/or PNG graphs per the Step 3 choices."""
    if not want_html and not want_png:
        return

    # read the summary rows
    summary = []
    try:
        with open(summary_csv) as fh:
            summary = list(csv.DictReader(fh))
    except Exception as e:
        log(f"Report: could not read summary CSV ({e})")
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

    # Order matters: build PNGs (if wanted) first so the dataset can include
    # them, then the dataset zip, then the HTML report last so it can detect and
    # link the zip. Each stage is best-effort and never blocks the others.
    if want_png:
        try:
            _build_png_graphs(session_dir, session_id, summary, hw_csv,
                              n_ok, log)
        except Exception as e:
            log(f"Report: PNG generation failed ({e})")

    # Always build the downloadable dataset package (per-run tables as CSV, plus
    # any PNGs that were produced), zipped with max compression. This is what
    # the HTML report's "Download dataset" button links to. Best-effort.
    try:
        _build_dataset_zip(session_dir, session_id, summary, hw_csv, log)
    except Exception as e:
        log(f"Report: dataset zip failed ({e})")

    if want_html:
        try:
            _build_html_report(session_dir, session_id, summary, hw_csv,
                               n_ok, n_fail, n_abort, session_info, log)
        except Exception as e:
            log(f"Report: HTML generation failed ({e})")

def _plotly_js(log) -> str:
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
        log("Report: downloading Plotly for offline reports "
                      "(one-time)...")
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read().decode("utf-8")
        cache.write_text(data, encoding="utf-8")
        return data
    except Exception as e:
        log(f"Report: could not fetch Plotly ({e}); report will "
                      f"reference the CDN and need internet to view.")
        return ""

def _run_color(i):
    """Deterministic color per run index for the HW-stats bands/legend."""
    palette = ["#e2510f", "#f0b429", "#3fbf6f", "#4aa3df", "#c1272d",
               "#9b59b6", "#1abc9c", "#e67e22", "#2ecc71", "#e74c3c"]
    return palette[i % len(palette)]

def _build_html_report(session_dir, session_id, summary, hw_csv,
                       n_ok, n_fail, n_abort, session_info=None,
                       log=lambda *a: None):
    report_path = session_dir / f"REPORT_{session_id}.html"
    session_info = session_info or {}

    # Insufficient-data case: no successful runs -> default template.
    if n_ok == 0:
        html = _html_no_data(session_id, n_fail, n_abort, session_info)
        report_path.write_text(html, encoding="utf-8")
        log(f"REPORT (no data) -> {report_path}")
        return

    # read HW stats
    hw_rows = []
    try:
        with open(hw_csv) as fh:
            hw_rows = list(csv.DictReader(fh))
    except Exception:
        pass

    plotly = _plotly_js(log)
    plotly_tag = (f"<script>{plotly}</script>" if plotly else
                  '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">'
                  '</script>')

    # Build HW-stats figures (two: temps, usage) with per-run color bands.
    hw_traces_temp, hw_traces_use, shapes, run_spans = _hw_figures(
        hw_rows, summary)

    # Build per-run data (tprof/ebath) for successful runs.
    runs_dir = session_dir / "RUNS"
    per_run = _per_run_payload(summary, runs_dir)

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

    # collect the session's own files (log, CSVs, JSON) to embed as text so the
    # report is self-contained and viewable from anywhere with no server.
    embedded = _collect_session_files(session_dir, session_id, log)
    dataset_name = f"DATASET_{session_id}.zip"
    has_dataset = (session_dir / dataset_name).exists()

    html = _html_template(session_id, summary, n_ok, n_fail, n_abort,
                          plotly_tag, data_json, session_info,
                          embedded, dataset_name, has_dataset)
    report_path.write_text(html, encoding="utf-8")
    log(f"REPORT -> {report_path}")

def _hw_figures(hw_rows, summary):
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
        color = _run_color(run_i)
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

def _per_run_payload(summary, runs_dir: Path):
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
            "tprof": None, "ebath": None, "log_text": None,
        }
        rd = runs_dir / run_id
        if r["status"] != "OK":
            # For a failed/aborted run there are no graphs; surface the LAMMPS
            # log (renamed LAMMPS.log; fall back to log.lammps / run.log) so the
            # user can see why it failed, right where the charts would be.
            for cand in ("LAMMPS.log", "log.lammps", "run.log"):
                lp = rd / cand
                if lp.exists():
                    try:
                        txt = lp.read_text(encoding="utf-8", errors="replace")
                        if len(txt) > _EMBED_MAX_CHARS:
                            txt = txt[-_EMBED_MAX_CHARS:]  # tail: errors are last
                        entry["log_text"] = txt
                    except Exception:
                        pass
                    break
        if r["status"] == "OK":
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

def _build_png_graphs(session_dir, session_id, summary, hw_csv, n_ok, log):
    """Backup PNG graphs via matplotlib (best-effort).

    Produces, when matplotlib is available:
      * session-level hardware USAGE and TEMPERATURE PNGs (GRAPHS/), and
      * per successful run, a temperature-profile PNG and an electron-bath
        PNG written into that run's own folder (RUNS/<run-id>/).
    Any individual graph that can't be built is skipped with a logged note;
    one failure never stops the others.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        log("Report: matplotlib not available - skipping PNGs.")
        return
    if n_ok == 0:
        log("Report: no successful runs - skipping PNGs.")
        return
    graphs_dir = session_dir / "GRAPHS"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    # ---- session hardware graphs (usage + temperature) --------------------
    try:
        with open(hw_csv) as fh:
            rows = list(csv.DictReader(fh))
    except Exception as e:
        rows = []
        log(f"Report: could not read HW stats for PNGs ({e})")

    def _col(rows, name):
        out = []
        for r in rows:
            try:
                out.append(float(r[name]))
            except (TypeError, ValueError, KeyError):
                out.append(float("nan"))
        return out

    if rows:
        xs = list(range(len(rows)))
        # usage
        try:
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(xs, _col(rows, "cpu_usage_pct"), label="CPU %")
            ax.plot(xs, _col(rows, "gpu_usage_pct"), label="GPU %")
            ax.plot(xs, _col(rows, "ram_usage_pct"), label="RAM %")
            ax.set_xlabel("probe #"); ax.set_ylabel("%")
            ax.set_ylim(0, 100); ax.legend()
            ax.set_title(f"Session {session_id} - resource usage")
            fig.tight_layout()
            fig.savefig(graphs_dir / f"HW-USAGE_{session_id}.png", dpi=110)
            plt.close(fig)
            log(f"PNG -> {graphs_dir / f'HW-USAGE_{session_id}.png'}")
        except Exception as e:
            log(f"Report: PNG HW usage graph failed ({e})")
        # temperature
        try:
            cpu_t = _col(rows, "cpu_temp_c"); gpu_t = _col(rows, "gpu_temp_c")
            if any(v == v for v in cpu_t) or any(v == v for v in gpu_t):
                fig, ax = plt.subplots(figsize=(10, 4))
                ax.plot(xs, cpu_t, label="CPU temp (C)")
                ax.plot(xs, gpu_t, label="GPU temp (C)")
                ax.set_xlabel("probe #"); ax.set_ylabel("\u00b0C"); ax.legend()
                ax.set_title(f"Session {session_id} - temperatures")
                fig.tight_layout()
                fig.savefig(graphs_dir / f"HW-TEMP_{session_id}.png", dpi=110)
                plt.close(fig)
                log(f"PNG -> {graphs_dir / f'HW-TEMP_{session_id}.png'}")
        except Exception as e:
            log(f"Report: PNG HW temperature graph failed ({e})")

    # ---- per-run temperature-profile and electron-bath graphs -------------
    runs_dir = session_dir / "RUNS"
    for r in summary:
        if r.get("status") != "OK":
            continue
        run_id = r.get("run_id", "")
        rd = runs_dir / run_id
        if not rd.is_dir():
            continue
        # temperature profile (last timestep)
        try:
            tp = rd / "TEMP-PROFILE.csv"
            if tp.exists():
                with open(tp) as fh:
                    trows = list(csv.DictReader(fh))
                if trows:
                    last_ts = max(int(x["timestep"]) for x in trows)
                    prof = [x for x in trows
                            if int(x["timestep"]) == last_ts
                            and x["temperature"] != ""]
                    if prof:
                        coord = [float(x["coord"]) for x in prof]
                        temp = [float(x["temperature"]) for x in prof]
                        fig, ax = plt.subplots(figsize=(8, 4))
                        ax.plot(coord, temp, marker="o", ms=4, color="#e2510f")
                        ax.set_xlabel("position (reduced)")
                        ax.set_ylabel("T (K)")
                        ax.set_title(f"{run_id} - temperature profile "
                                     f"(t={last_ts})")
                        fig.tight_layout()
                        fig.savefig(rd / "TEMP-PROFILE.png", dpi=110)
                        plt.close(fig)
        except Exception as e:
            log(f"Report: PNG tprof failed for {run_id} ({e})")
        # electron bath
        try:
            ep = rd / "ELECTRON-BATH.csv"
            if ep.exists():
                with open(ep) as fh:
                    erows = list(csv.DictReader(fh))
                if erows:
                    t = [float(x["t_ps"]) for x in erows]
                    hot = [float(x["E_hot_eV"]) for x in erows]
                    cold = [float(x["E_cold_eV"]) for x in erows]
                    fig, ax = plt.subplots(figsize=(8, 4))
                    ax.plot(t, hot, label="E_hot (eV)", color="#ff5a4d")
                    ax.plot(t, cold, label="E_cold (eV)", color="#4aa3df")
                    ax.set_xlabel("t (ps)"); ax.set_ylabel("E (eV)"); ax.legend()
                    ax.set_title(f"{run_id} - electron-bath energy")
                    fig.tight_layout()
                    fig.savefig(rd / "ELECTRON-BATH.png", dpi=110)
                    plt.close(fig)
        except Exception as e:
            log(f"Report: PNG ebath failed for {run_id} ({e})")
    log("PNG graphs complete.")


def _build_dataset_zip(session_dir, session_id, summary, hw_csv, log):
    """Bundle a portable data package next to the report: DATASET_<id>.zip.

    Contents (max-compression DEFLATE):
      * SESSION-SUMMARY / SESSION-HW-STATS CSVs and SESSION-INFO JSON,
      * each successful run's converted CSVs (TEMP-PROFILE / ELECTRON-BATH) and
        any PNGs produced for it, plus the session GRAPHS PNGs.
    The HTML report's "Download dataset" button links to this file. It is built
    even when PNGs are disabled (then it simply contains the CSVs/JSON).
    """
    import zipfile
    zip_path = session_dir / f"DATASET_{session_id}.zip"
    runs_dir = session_dir / "RUNS"
    added = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        # session-level files
        for name in (f"SESSION-SUMMARY_{session_id}.csv",
                     f"SESSION-HW-STATS_{session_id}.csv",
                     f"SESSION-INFO_{session_id}.json"):
            p = session_dir / name
            if p.exists():
                zf.write(p, arcname=name); added += 1
        # session graphs
        gdir = session_dir / "GRAPHS"
        if gdir.is_dir():
            for p in sorted(gdir.glob("*.png")):
                zf.write(p, arcname=f"GRAPHS/{p.name}"); added += 1
        # per-run tables + pngs
        for r in summary:
            if r.get("status") != "OK":
                continue
            run_id = r.get("run_id", "")
            rd = runs_dir / run_id
            if not rd.is_dir():
                continue
            for fname in ("TEMP-PROFILE.csv", "ELECTRON-BATH.csv",
                          "TEMP-PROFILE.png", "ELECTRON-BATH.png"):
                p = rd / fname
                if p.exists():
                    zf.write(p, arcname=f"RUNS/{run_id}/{fname}"); added += 1
    log(f"DATASET -> {zip_path} ({added} files)")
    return zip_path

# -- HTML templates ----------------------------------------------------
import html as _html_mod
import base64 as _base64

def _logo_data_uri():
    """Return an <img src=...> data URI for the app logo, embedded so the report
    stays self-contained. Prefers LOGO.webp, then LOGO.png/.jpg from
    AUTOMATION/assets/logos. If none is found returns None (caller falls back to
    a text mark)."""
    base = script_dir() / "assets" / "logos"
    for fname, mime in (("LOGO.webp", "image/webp"),
                        ("LOGO.png", "image/png"),
                        ("LOGO.jpg", "image/jpeg"),
                        ("LOGO.jpeg", "image/jpeg")):
        p = base / fname
        if p.exists():
            try:
                b = _base64.b64encode(p.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{b}"
            except Exception:
                continue
    return None

# Cap for any single embedded file's text, so a huge HW-stats CSV can't bloat
# the report to tens of MB. Larger files are truncated in the on-page viewer
# (the full copy is always in the dataset zip).
_EMBED_MAX_CHARS = 200_000

def _collect_session_files(session_dir, session_id, log):
    """Read the session's log/CSV/JSON files and return a list of dicts:
    {name, label, kind, text, truncated}. Text is HTML-escaped and size-capped
    so the report stays self-contained without ballooning. Missing files are
    simply skipped."""
    wanted = [
        (f"SESSION_{session_id}.log", "Run log", "log"),
        (f"ANALYSIS_{session_id}.log", "Analysis log", "log"),
        (f"SESSION-SUMMARY_{session_id}.csv", "Run summary (CSV)", "csv"),
        (f"SESSION-HW-STATS_{session_id}.csv", "Hardware stats (CSV)", "csv"),
        (f"SESSION-INFO_{session_id}.json", "Session info (JSON)", "json"),
    ]
    out = []
    for name, label, kind in wanted:
        p = session_dir / name
        if not p.exists():
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log(f"Report: could not embed {name} ({e})")
            continue
        truncated = len(raw) > _EMBED_MAX_CHARS
        if truncated:
            raw = raw[:_EMBED_MAX_CHARS]
        out.append({
            "name": name,
            "label": label,
            "kind": kind,
            "text": _html_mod.escape(raw),
            "truncated": truncated,
        })
    return out

def _fmt_duration(secs):
    try:
        return fmt_hms(float(secs))
    except Exception:
        return "unknown"

def _hw_summary_cards(session_info):
    """Return HTML for the hardware + timing overview cards."""
    hw = session_info.get("hardware", {}) or {}
    start = session_info.get("start_utc") or "-"
    end = session_info.get("end_utc") or "-"
    dur = _fmt_duration(session_info.get("duration_sec"))
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

def _html_no_data(session_id, n_fail, n_abort, session_info=None):
    session_info = session_info or {}
    cards = _hw_summary_cards(session_info)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LAVA Report {session_id}</title>
<style>
body{{font-family:'Roboto Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
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

def _html_template(session_id, summary, n_ok, n_fail, n_abort,
                   plotly_tag, data_json, session_info=None,
                   embedded=None, dataset_name=None, has_dataset=False):
    session_info = session_info or {}
    embedded = embedded or []
    cards = _hw_summary_cards(session_info)

    # Files tab: each embedded file rendered as a titled <pre> block. A simple
    # left-hand file switcher shows one at a time.
    file_btns = []
    file_blocks = []
    for i, f in enumerate(embedded):
        active = " active" if i == 0 else ""
        trunc = ("<div class='hint'>Showing the first part only - full file is "
                 "in the dataset download.</div>" if f["truncated"] else "")
        file_btns.append(
            f"<button class='fbtn{active}' onclick=\"showFile({i})\">"
            f"{f['label']}</button>")
        file_blocks.append(
            f"<div class='fileblock{active}' id='file{i}'>"
            f"<div class='section-title'>{f['label']} "
            f"<span class='hint'>({f['name']})</span></div>{trunc}"
            f"<pre class='filepre'>{f['text']}</pre></div>")
    files_switcher = "\n".join(file_btns) or "<span class='hint'>none</span>"
    files_content = "\n".join(file_blocks) or (
        "<div class='detail-empty'>No session files were found to embed.</div>")

    # dataset button (links to the pre-built zip sitting next to this report)
    if has_dataset and dataset_name:
        dataset_btn = (
            f"<a class='dlbtn' href='{dataset_name}' download>"
            f"&#x2B07; Download dataset (.zip)</a>"
            f"<span class='hint' style='margin-left:10px'>All run tables (CSV) "
            f"and graphs (PNG), zipped. Keep it next to this report.</span>")
    else:
        dataset_btn = ("<span class='hint'>Dataset package not available for "
                       "this session.</span>")

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

    # header logo (embedded data URI); fall back to a small text mark
    _logo = _logo_data_uri()
    logo_html = (f"<img class='logo' src='{_logo}' alt='LAVA'>" if _logo
                 else "<span class='logomark'>LAVA</span>")
    # reuse the same embedded logo as the favicon so the report stays offline
    favicon_html = (f"<link rel='icon' href='{_logo}'>" if _logo else "")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LAVA Report {session_id}</title>
{favicon_html}
{plotly_tag}
<style>
*{{box-sizing:border-box}}
body{{font-family:'Roboto Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:#1a1210;color:#f3e9e3;margin:0;padding:0}}
header{{background:linear-gradient(180deg,#2b1e19,#241a17);
padding:20px 32px;border-bottom:2px solid #e2510f}}
header h1{{color:#ff7a1a;margin:0;font-size:24px;letter-spacing:.5px}}
header .sub{{color:#b08a7a;font-size:13px;margin-top:4px}}
.hrow{{display:flex;align-items:center;gap:16px}}
.hrow .logo{{height:48px;width:auto;display:block}}
.hrow .logomark{{color:#ff7a1a;font-weight:bold;font-size:22px;
border:2px solid #e2510f;border-radius:8px;padding:4px 10px}}
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
.dlbtn{{display:inline-block;background:#e2510f;color:#1a1210;font-weight:bold;
padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px}}
.dlbtn:hover{{background:#ff7a1a}}
.dlbar{{margin:18px 0 4px}}
.fswitch{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 14px}}
.fbtn{{padding:8px 14px;background:#241a17;color:#b08a7a;border:1px solid #3a2a24;
border-radius:6px;cursor:pointer;font-size:13px}}
.fbtn:hover{{color:#f3e9e3}}.fbtn.active{{color:#ff7a1a;border-color:#e2510f}}
.fileblock{{display:none}}.fileblock.active{{display:block}}
.filepre{{background:#160f0d;border:1px solid #3a2a24;border-radius:8px;
padding:14px;overflow:auto;max-height:600px;font-size:12px;line-height:1.45;
white-space:pre;color:#e8ddd6}}
.rtable-wrap{{max-height:320px;overflow:auto;margin:6px 0 16px;
border:1px solid #3a2a24;border-radius:8px}}
.rtable-wrap table{{margin:0}}
</style></head><body>
<header>
<div class="hrow">{logo_html}
<div><h1>LAVA Report</h1>
<div class="sub">Session {session_id}</div></div></div>
<div class="status-pills" style="margin-top:10px">
<span style="background:#1e3a28;color:#3fbf6f">{n_ok} successful</span>
<span style="background:#3a1e1e;color:#ff5a4d">{n_fail} failed</span>
<span style="background:#3a331e;color:#f0b429">{n_abort} aborted</span>
</div></header>

<div class="wrap">
<div class="cards">{cards}</div>
<div class="dlbar">{dataset_btn}</div>

<div class="tabs">
<button class="tab active" data-tab="hw" onclick="showTab(this,'hw')">
Hardware &amp; Session</button>
<button class="tab" data-tab="runs" onclick="showTab(this,'runs')">
Per-run Analysis</button>
<button class="tab" data-tab="files" onclick="showTab(this,'files')">
Files &amp; Logs</button>
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

<div id="files" class="panel">
<div class="section-title">Session files &amp; logs</div>
<p class="hint">These are embedded in this report so it stays viewable on its
own. For the full raw files (including any truncated here), use the dataset
download at the top.</p>
<div class="fswitch">{files_switcher}</div>
{files_content}
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

function showFile(i){{
  document.querySelectorAll('.fileblock').forEach(function(x){{x.classList.remove('active')}});
  document.querySelectorAll('.fbtn').forEach(function(x){{x.classList.remove('active')}});
  var b=document.getElementById('file'+i); if(b) b.classList.add('active');
  var btns=document.querySelectorAll('.fbtn'); if(btns[i]) btns[i].classList.add('active');
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
el.innerHTML='<div class="section-title" style="color:'+c+'">'+r.run_id+
  ' &mdash; '+r.status+'</div>'+
  '<p class="hint">This run produced no graphs. Its LAMMPS log is shown below '+
  'so you can see what happened.</p>'+
  '<div class="section-title">LAMMPS.log</div><pre class="filepre" '+
  'id="faillog"></pre>';
// use textContent so log characters like < > & can't break the page
var pre=document.getElementById('faillog');
pre.textContent = r.log_text ? r.log_text :
  '(no LAMMPS.log was captured for this run)';
return;
  }}
  el.innerHTML='<div class="section-title">'+r.run_id+'</div>'+
'<div id="tprofC" class="chart small"></div>'+
'<div id="ebathC" class="chart small"></div>'+
'<div class="section-title">Temperature-profile data</div>'+
'<div id="tprofT"></div>'+
'<div class="section-title">Electron-bath data</div>'+
'<div id="ebathT"></div>';
  if(r.tprof && r.tprof.coord && r.tprof.coord.length){{
Plotly.newPlot('tprofC',[{{x:r.tprof.coord,y:r.tprof.temp,
  mode:'lines+markers',line:{{color:'#e2510f'}},marker:{{size:5}},
  name:'T (K)'}}],
  Object.assign({{}},DARK,{{title:'Temperature profile (t='+r.tprof.timestep+')',
  xaxis:{{title:'position (reduced)',gridcolor:'#3a2a24'}},
  yaxis:{{title:'T (K)',gridcolor:'#3a2a24'}}}}),
  {{responsive:true,displaylogo:false}});
document.getElementById('tprofT').innerHTML=
  tableHTML(['position','T (K)'],r.tprof.coord.map(function(c,i){{
    return [c, r.tprof.temp[i]];
  }}));
  }} else {{
document.getElementById('tprofC').className='detail-empty';
document.getElementById('tprofC').innerHTML=
  'No temperature-profile data &mdash; the run likely ended before the '+
  'ave/chunk output interval was reached.';
document.getElementById('tprofT').innerHTML='';
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
document.getElementById('ebathT').innerHTML=
  tableHTML(['t (ps)','E_hot (eV)','E_cold (eV)'],r.ebath.t.map(function(t,i){{
    return [t, r.ebath.hot[i], r.ebath.cold[i]];
  }}));
  }} else {{
document.getElementById('ebathC').className='detail-empty';
document.getElementById('ebathC').innerHTML='No electron-bath data.';
document.getElementById('ebathT').innerHTML='';
  }}
}}

function tableHTML(headers,rows){{
  var h='<div class="rtable-wrap"><table><thead><tr>';
  headers.forEach(function(x){{h+='<th>'+x+'</th>';}});
  h+='</tr></thead><tbody>';
  rows.forEach(function(row){{
    h+='<tr>';
    row.forEach(function(c){{h+='<td>'+c+'</td>';}});
    h+='</tr>';
  }});
  return h+'</tbody></table></div>';
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
