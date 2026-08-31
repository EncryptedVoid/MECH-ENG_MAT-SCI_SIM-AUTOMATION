#!/usr/bin/env python3
"""
LAVA report generation - post-processing / analysis phase.
==========================================================
Turns a finished session's CSVs (summary + hardware stats) and per-run output
files into an interactive offline HTML report and backup PNG graphs.

Design rules for this file (keep it this way):
  * NO tkinter imports and no reference to the App class. Every function takes
    explicit arguments (paths, parsed rows) and returns data or writes files.
  * One module-level logger (`log = logging.getLogger(__name__)`), shared by
    every function here. run.py routes this logger's records to ANALYSIS.log
    (and to the live GUI). Functions here never configure handlers.
  * The public entry point is generate_session_outputs(); App calls only that.

The Plotly library is inlined into the HTML so reports open fully offline.
"""

import os
import csv
import glob
import json
import logging
from pathlib import Path

from constants import FOOTER

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_session_outputs(session_dir, session_id, summary_csv, hw_csv,
                             want_html, want_png, script_dir):
    """Generate the HTML report and/or PNG graphs per the user's Step 3 choices.

    want_html / want_png are plain booleans (resolved by the caller from the
    tkinter vars). script_dir is the folder to cache plotly.min.js next to.
    """
    if not want_html and not want_png:
        return

    summary = []
    try:
        with open(summary_csv) as fh:
            summary = list(csv.DictReader(fh))
    except Exception as e:
        log.warning("could not read summary CSV (%s)", e)
        return

    n_ok = sum(1 for r in summary if r["status"] == "OK")
    n_fail = sum(1 for r in summary if r["status"] == "FAILED")
    n_abort = sum(1 for r in summary if r["status"] == "ABORTED_EARLY")

    if want_html:
        try:
            build_html_report(session_dir, session_id, summary, hw_csv,
                              n_ok, n_fail, n_abort, script_dir)
        except Exception as e:
            log.warning("HTML generation failed (%s)", e)

    if want_png:
        try:
            build_png_graphs(session_dir, session_id, summary, hw_csv, n_ok)
        except Exception as e:
            log.warning("PNG generation failed (%s)", e)


# ---------------------------------------------------------------------------
# Plotly offline bundle
# ---------------------------------------------------------------------------
def plotly_js(script_dir: Path) -> str:
    """Return the Plotly library JS to inline for fully-offline reports.

    Order of preference:
      1. plotly.min.js cached next to the app (fastest, offline).
      2. The copy bundled inside the `plotly` PyPI package, if installed.
      3. A one-time CDN download, cached for next time.
      4. Empty string -> caller falls back to a CDN <script> tag.
    """
    cache = script_dir / "plotly.min.js"
    if cache.exists():
        try:
            return cache.read_text(encoding="utf-8")
        except Exception:
            pass
    # bundled inside the plotly package
    try:
        import plotly
        base = os.path.dirname(plotly.__file__)
        hits = glob.glob(os.path.join(base, "**", "plotly.min.js"), recursive=True)
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
        import urllib.request
        log.info("downloading Plotly for offline reports (one-time)...")
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read().decode("utf-8")
        cache.write_text(data, encoding="utf-8")
        return data
    except Exception as e:
        log.warning("could not fetch Plotly (%s); report will reference the CDN "
                    "and need internet to view.", e)
        return ""


def run_color(i):
    """Deterministic color per run index for the HW-stats bands/legend."""
    palette = ["#e2510f", "#f0b429", "#3fbf6f", "#4aa3df", "#c1272d",
               "#9b59b6", "#1abc9c", "#e67e22", "#2ecc71", "#e74c3c"]
    return palette[i % len(palette)]


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def build_html_report(session_dir, session_id, summary, hw_csv,
                      n_ok, n_fail, n_abort, script_dir):
    report_path = session_dir / f"REPORT_{session_id}.html"

    # Insufficient-data case: no successful runs -> default template.
    if n_ok == 0:
        html = _html_no_data(session_id, n_fail, n_abort)
        report_path.write_text(html, encoding="utf-8")
        log.info("REPORT (no data) -> %s", report_path)
        return

    hw_rows = []
    try:
        with open(hw_csv) as fh:
            hw_rows = list(csv.DictReader(fh))
    except Exception:
        pass

    plotly = plotly_js(script_dir)
    plotly_tag = (f"<script>{plotly}</script>" if plotly else
                  '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">'
                  '</script>')

    hw_traces_temp, hw_traces_use, shapes, run_spans = hw_figures(hw_rows, summary)

    runs_dir = session_dir / "RUNS"
    per_run = per_run_payload(summary, runs_dir)

    payload = {
        "session_id": session_id,
        "hw_temp": hw_traces_temp,
        "hw_use": hw_traces_use,
        "hw_shapes": shapes,
        "run_spans": run_spans,
        "per_run": per_run,
    }
    data_json = json.dumps(payload)

    html = _html_template(session_id, summary, n_ok, n_fail, n_abort,
                          plotly_tag, data_json)
    report_path.write_text(html, encoding="utf-8")
    log.info("REPORT -> %s", report_path)


def hw_figures(hw_rows, summary):
    """Turn HW-stats rows into Plotly trace dicts plus per-run color bands.

    Returns (temp_traces, usage_traces, shapes, run_spans)."""
    from collections import OrderedDict
    by_run = OrderedDict()
    for r in hw_rows:
        by_run.setdefault(r["run_id"], []).append(r)

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
        color = run_color(run_i)
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


def per_run_payload(summary, runs_dir: Path):
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
            "tprof": None, "ebath": None,
        }
        if r["status"] == "OK":
            rd = runs_dir / run_id
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


# ---------------------------------------------------------------------------
# PNG backup graphs
# ---------------------------------------------------------------------------
def build_png_graphs(session_dir, session_id, summary, hw_csv, n_ok):
    """Backup PNG graphs via matplotlib (best-effort)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        log.info("matplotlib/numpy not available - skipping PNGs.")
        return
    if n_ok == 0:
        log.info("no successful runs - skipping PNGs.")
        return
    graphs_dir = session_dir / "GRAPHS"
    graphs_dir.mkdir(parents=True, exist_ok=True)
    # HW usage over probe index
    try:
        with open(hw_csv) as fh:
            rows = list(csv.DictReader(fh))
        xs = list(range(len(rows)))
        def col(name):
            out = []
            for r in rows:
                try:
                    out.append(float(r[name]))
                except (TypeError, ValueError):
                    out.append(float("nan"))
            return out
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(xs, col("cpu_usage_pct"), label="CPU %")
        ax.plot(xs, col("gpu_usage_pct"), label="GPU %")
        ax.plot(xs, col("ram_usage_pct"), label="RAM %")
        ax.set_xlabel("probe #"); ax.set_ylabel("%"); ax.legend()
        ax.set_title(f"Session {session_id} - resource usage")
        fig.tight_layout()
        fig.savefig(graphs_dir / f"HW-USAGE_{session_id}.png", dpi=110)
        plt.close(fig)
        log.info("PNG -> %s", graphs_dir / f"HW-USAGE_{session_id}.png")
    except Exception as e:
        log.warning("PNG HW graph failed (%s)", e)


# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------
def _html_no_data(session_id, n_fail, n_abort):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LAVA Report {session_id}</title>
<style>body{{font-family:sans-serif;background:#1a1210;color:#f3e9e3;
padding:40px}}.box{{max-width:680px;margin:60px auto;background:#241a17;
padding:30px;border-radius:8px;border:1px solid #e2510f}}
h1{{color:#ff7a1a}}.red{{color:#ff5a4d;font-weight:bold}}</style></head>
<body><div class="box"><h1>LAVA - Report not generated</h1>
<p>This report was <span class="red">not generated</span> because
{n_fail} run(s) failed and {n_abort} run(s) were aborted early, leaving
insufficient data.</p>
<p>Session: {session_id}</p>
<p style="color:#b08a7a">{FOOTER}</p></div></body></html>"""


def _html_template(session_id, summary, n_ok, n_fail, n_abort,
                   plotly_tag, data_json):
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

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LAVA Report {session_id}</title>
{plotly_tag}
<style>
body{{font-family:sans-serif;background:#1a1210;color:#f3e9e3;margin:0;padding:0}}
header{{background:#241a17;padding:16px 24px;border-bottom:2px solid #e2510f}}
h1{{color:#ff7a1a;margin:0}}.sub{{color:#b08a7a;font-size:13px}}
.tabs{{display:flex;gap:4px;padding:12px 24px 0}}
.tab{{padding:10px 18px;background:#241a17;color:#b08a7a;cursor:pointer;
border-radius:6px 6px 0 0}}.tab.active{{background:#e2510f;color:#1a1210;
font-weight:bold}}
.panel{{display:none;padding:24px}}.panel.active{{display:block}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #3a2a24;padding:6px 10px;text-align:left;font-size:13px}}
th{{background:#241a17;color:#ff7a1a}}
.chart{{background:#241a17;border-radius:8px;margin:14px 0;padding:8px}}
#runsearch{{padding:8px;width:320px;background:#31231f;color:#f3e9e3;
border:1px solid #3a2a24;border-radius:4px}}
.runitem{{padding:8px 12px;margin:4px 0;background:#241a17;border-radius:4px;
cursor:pointer;border-left:4px solid #3fbf6f}}
.runitem.FAILED{{border-left-color:#ff5a4d}}
.runitem.ABORTED_EARLY{{border-left-color:#f0b429}}
.footer{{color:#b08a7a;padding:16px 24px;font-size:12px}}
.badge{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold}}
</style></head><body>
<header><h1>LAVA Report</h1>
<div class="sub">Session {session_id} &mdash;
<span style="color:#3fbf6f">{n_ok} OK</span>,
<span style="color:#ff5a4d">{n_fail} failed</span>,
<span style="color:#f0b429">{n_abort} aborted</span></div></header>
<div class="tabs">
<div class="tab active" onclick="showTab('hw')">HW-STATS</div>
<div class="tab" onclick="showTab('runs')">Per-run</div></div>

<div id="hw" class="panel active">
<h3>Session summary</h3>
<table><tr><th>#</th><th>Material</th><th>Potential</th><th>Config</th>
<th>Structure</th><th>Seed</th><th>Status</th><th>Duration (s)</th></tr>
{table}</table>
<h3>Temperatures over session</h3>
<div id="tempChart" class="chart"></div>
<h3>Resource usage over session</h3>
<div id="useChart" class="chart"></div>
<div class="sub">Colored bands mark each run's span along the timeline.</div>
</div>

<div id="runs" class="panel">
<input id="runsearch" placeholder="Search runs (material, file, seed, id)..."
 oninput="filterRuns()"/>
<div id="runlist"></div>
<div id="rundetail"></div>
</div>

<div class="footer">{FOOTER}</div>

<script>
var DATA = {data_json};

function showTab(t){{
  document.querySelectorAll('.tab').forEach(function(x){{x.classList.remove('active')}});
  document.querySelectorAll('.panel').forEach(function(x){{x.classList.remove('active')}});
  document.getElementById(t).classList.add('active');
  event.target.classList.add('active');
  if(t==='hw'){{drawHW();}}
}}

var hwDrawn=false;
function drawHW(){{
  if(hwDrawn) return; hwDrawn=true;
  var dark={{paper_bgcolor:'#241a17',plot_bgcolor:'#241a17',
    font:{{color:'#f3e9e3'}},shapes:DATA.hw_shapes,margin:{{t:20}},
    xaxis:{{title:'probe #',gridcolor:'#3a2a24'}},
    yaxis:{{gridcolor:'#3a2a24'}}}};
  Plotly.newPlot('tempChart',DATA.hw_temp,
    Object.assign({{}},dark,{{yaxis:{{title:'°C',gridcolor:'#3a2a24'}}}}),
    {{responsive:true}});
  Plotly.newPlot('useChart',DATA.hw_use,
    Object.assign({{}},dark,{{yaxis:{{title:'%',gridcolor:'#3a2a24',range:[0,100]}}}}),
    {{responsive:true}});
}}

function renderRunList(items){{
  var el=document.getElementById('runlist'); el.innerHTML='';
  items.forEach(function(r){{
    var d=document.createElement('div');
    d.className='runitem '+r.status;
    d.innerHTML='<b>'+r.run_id+'</b> &mdash; '+r.material+' / '+r.potential+
      ' / '+r.configuration+' / '+r.structure+' (seed '+r.seed+') '+
      '<span class="badge" style="background:'+
      (r.status==='OK'?'#3fbf6f':r.status==='FAILED'?'#ff5a4d':'#f0b429')+
      ';color:#1a1210">'+r.status+'</span>';
    d.onclick=function(){{showRun(r);}};
    el.appendChild(d);
  }});
}}

function showRun(r){{
  var el=document.getElementById('rundetail');
  if(r.status!=='OK'){{
    el.innerHTML='<p style="color:'+
      (r.status==='FAILED'?'#ff5a4d':'#f0b429')+'"><b>'+r.run_id+
      '</b>: '+r.status+' &mdash; no data available for this run.</p>';
    return;
  }}
  el.innerHTML='<h3>'+r.run_id+'</h3>'+
    '<div id="tprofC" class="chart"></div><div id="ebathC" class="chart"></div>';
  var dark={{paper_bgcolor:'#241a17',plot_bgcolor:'#241a17',
    font:{{color:'#f3e9e3'}},margin:{{t:20}},
    xaxis:{{gridcolor:'#3a2a24'}},yaxis:{{gridcolor:'#3a2a24'}}}};
  if(r.tprof){{
    Plotly.newPlot('tprofC',[{{x:r.tprof.coord,y:r.tprof.temp,
      mode:'lines+markers',line:{{color:'#e2510f'}},name:'T (K)'}}],
      Object.assign({{}},dark,{{title:'Temperature profile (t='+r.tprof.timestep+')',
      xaxis:{{title:'position (reduced)',gridcolor:'#3a2a24'}},
      yaxis:{{title:'T (K)',gridcolor:'#3a2a24'}}}}),{{responsive:true}});
  }}
  if(r.ebath){{
    Plotly.newPlot('ebathC',[
      {{x:r.ebath.t,y:r.ebath.hot,mode:'lines',name:'E_hot (eV)',
        line:{{color:'#ff5a4d'}}}},
      {{x:r.ebath.t,y:r.ebath.cold,mode:'lines',name:'E_cold (eV)',
        line:{{color:'#4aa3df'}}}}],
      Object.assign({{}},dark,{{title:'Electron bath energy',
      xaxis:{{title:'t (ps)',gridcolor:'#3a2a24'}},
      yaxis:{{title:'E (eV)',gridcolor:'#3a2a24'}}}}),{{responsive:true}});
  }}
}}

function filterRuns(){{
  var q=document.getElementById('runsearch').value.toLowerCase();
  var items=DATA.per_run.filter(function(r){{
    return (r.run_id+' '+r.material+' '+r.potential+' '+r.configuration+' '+
      r.structure+' '+r.seed).toLowerCase().indexOf(q)>=0;
  }});
  renderRunList(items);
}}

drawHW();
renderRunList(DATA.per_run);
</script>
</body></html>"""
