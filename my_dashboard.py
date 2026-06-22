#!/usr/bin/env python3
"""
my_dashboard.py — Self-contained Claude Code usage dashboard.
Zero external dependencies. Pure Python + inline HTML/CSS/JS with Canvas charts.

Usage:
    python3 my_dashboard.py          # default port 8080
    PORT=9000 python3 my_dashboard.py
"""

import json
import os
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".claude" / "usage.db"

# ── Pricing (per million tokens) ──────────────────────────────────────────────
PRICING = {
    "opus":   {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "sonnet": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "haiku":  {"input": 1.00, "output":  5.00, "cache_write": 1.25, "cache_read": 0.10},
}


def model_tier(model_name):
    m = model_name.lower()
    for tier in PRICING:
        if tier in m:
            return tier
    return None


def cost_for(model, input_tok, output_tok, cache_read, cache_write):
    tier = model_tier(model)
    if not tier:
        return 0.0
    p = PRICING[tier]
    return (
        input_tok * p["input"] / 1_000_000
        + output_tok * p["output"] / 1_000_000
        + cache_read * p["cache_read"] / 1_000_000
        + cache_write * p["cache_write"] / 1_000_000
    )


def get_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "No usage data found yet. Use Claude Code, then refresh — logs live in ~/.claude/projects."}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Group by the server's LOCAL calendar day. Timestamps are stored as UTC; we
    # treat them as naive and apply a fixed minute offset so day buckets match the
    # user's wall clock (and the JS "resets at local midnight" countdown). Using a
    # literal offset rather than 'localtime' keeps this correct across SQLite versions.
    off = datetime.now().astimezone().utcoffset()
    off_min = int(off.total_seconds() // 60) if off else 0
    tz = f"{off_min} minutes"

    # Daily totals by model
    rows = conn.execute("""
        SELECT date(timestamp, :tz) as day, model,
               SUM(input_tokens) as input,
               SUM(output_tokens) as output,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_write,
               COUNT(*) as turns
        FROM turns
        GROUP BY day, model
        ORDER BY day
    """, {"tz": tz}).fetchall()

    daily_by_model = [dict(r) for r in rows]

    # All-time by model
    rows = conn.execute("""
        SELECT model,
               SUM(input_tokens) as input,
               SUM(output_tokens) as output,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_write,
               COUNT(*) as turns
        FROM turns
        GROUP BY model
        ORDER BY SUM(output_tokens) DESC
    """).fetchall()

    models_summary = []
    for r in rows:
        d = dict(r)
        d["cost"] = round(cost_for(d["model"], d["input"], d["output"],
                                    d["cache_read"], d["cache_write"]), 2)
        models_summary.append(d)

    # Sessions
    rows = conn.execute("""
        SELECT session_id, project_name, model, turn_count,
               total_input_tokens as input, total_output_tokens as output,
               total_cache_read as cache_read, total_cache_creation as cache_write,
               first_timestamp, last_timestamp
        FROM sessions
        ORDER BY last_timestamp DESC
    """).fetchall()

    sessions = [dict(r) for r in rows]

    # Today's totals (local calendar day, matching the daily grouping above)
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute("""
        SELECT SUM(input_tokens) as input,
               SUM(output_tokens) as output,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_write,
               COUNT(*) as turns
        FROM turns WHERE date(timestamp, :tz) = :today
    """, {"tz": tz, "today": today}).fetchone()

    today_data = dict(row) if row else {}

    conn.close()

    return {
        "daily_by_model": daily_by_model,
        "models_summary": models_summary,
        "sessions": sessions,
        "today": today_data,
        "pricing": PRICING,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Usage Dashboard</title>
<style>
:root {
  --bg: #0a0c10;
  --surface: #14181f;
  --surface-2: #181d26;
  --surface-hover: #1b212b;
  --border: #232a35;
  --border-strong: #2d3542;
  --text: #e8eef5;
  --text-2: #b3bdc9;
  --text-3: #97a1b0;
  --accent: #e0855f;
  --accent-strong: #d97757;
  --green: #4ade80;
  --yellow: #e3b341;
  --red: #f0625b;
  --blue: #5b9bf7;
  --radius: 14px;
  --radius-sm: 9px;
  --shadow: 0 8px 24px -14px rgba(0,0,0,0.7), 0 1px 0 rgba(255,255,255,0.03) inset;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--sans);
  background:
    radial-gradient(1100px 520px at 78% -12%, rgba(217,119,87,0.10), transparent 62%),
    radial-gradient(900px 500px at 0% 0%, rgba(91,155,247,0.05), transparent 55%),
    var(--bg);
  background-attachment: fixed;
  color: var(--text);
  padding: clamp(16px, 3vw, 32px);
  max-width: 1320px;
  margin: 0 auto;
  -webkit-font-smoothing: antialiased;
  line-height: 1.5;
}
.num-mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }

/* ── Top bar ───────────────────────────────────────────── */
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 13px; }
.brand-mark {
  width: 40px; height: 40px; border-radius: 11px;
  display: grid; place-items: center;
  background: linear-gradient(145deg, var(--accent), var(--accent-strong));
  box-shadow: 0 6px 18px -8px rgba(217,119,87,0.7);
  flex: none;
}
.brand-mark svg { width: 22px; height: 22px; display: block; }
h1 { color: #fff; font-size: 20px; font-weight: 650; letter-spacing: -0.01em; }
.subtitle { color: var(--text-3); font-size: 12.5px; margin-top: 1px; }
.live {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 12px; color: var(--text-2);
  background: var(--surface); border: 1px solid var(--border);
  padding: 6px 12px; border-radius: 999px;
}
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 0 rgba(74,222,128,0.5); animation: pulse 2.4s ease-out infinite; transition: background 0.2s ease; }
.live-dot.ok { background: var(--green); }
.live-dot.refreshing { background: var(--yellow); animation: none; box-shadow: 0 0 6px 1px rgba(227,179,65,0.5); }
.live-dot.error { background: var(--red); animation: none; box-shadow: 0 0 6px 1px rgba(240,98,91,0.5); }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(74,222,128,0.5); } 70% { box-shadow: 0 0 0 7px rgba(74,222,128,0); } 100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); } }
.group-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

/* ── Panels (shared surface) ───────────────────────────── */
.panel {
  background: linear-gradient(180deg, var(--surface-2), var(--surface));
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.panel-pad { padding: 20px 22px; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
h2 { color: #fff; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }

/* ── Usage bar ─────────────────────────────────────────── */
.usage-bar-container { padding: 18px 22px; margin-bottom: 14px; }
.usage-bar-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.usage-bar-title { color: var(--text); font-size: 13px; font-weight: 600; letter-spacing: 0.01em; }
.usage-bar-pct { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 24px; font-weight: 700; letter-spacing: -0.02em; }
.usage-bar-track { width: 100%; height: 10px; background: #0d1118; border: 1px solid var(--border); border-radius: 999px; overflow: hidden; }
.usage-bar-fill { height: 100%; border-radius: 999px; transition: width 0.6s cubic-bezier(0.22,1,0.36,1); position: relative; background: linear-gradient(90deg, var(--accent-strong), var(--accent)); }
.usage-bar-fill::after { content: ""; position: absolute; inset: 0; border-radius: 999px; background: linear-gradient(180deg, rgba(255,255,255,0.25), transparent 55%); }
.usage-bar-details { display: flex; justify-content: space-between; margin-top: 10px; font-size: 12px; color: var(--text-3); flex-wrap: wrap; gap: 6px; }
.usage-bar-details .used { color: var(--text); font-family: var(--mono); }
.goal-edit { background: none; border: none; color: var(--accent); font: inherit; font-family: var(--mono); cursor: pointer; padding: 0; border-bottom: 1px dotted var(--accent); }
.goal-edit:hover { color: #fff; }
.goal-edit:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.usage-note { margin-top: 9px; font-size: 11.5px; color: var(--text-3); }
.empty-state { padding: 6px 2px; color: var(--text-2); font-size: 13px; }
.empty-hint { margin: -4px 0 18px; padding: 13px 16px; font-size: 13px; color: var(--text-2); background: rgba(217,119,87,0.07); border: 1px solid rgba(217,119,87,0.25); border-radius: var(--radius-sm); }
.empty-hint[hidden] { display: none; }

/* ── Cards ─────────────────────────────────────────────── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-bottom: 24px; }
.card { padding: 16px 18px; position: relative; overflow: hidden; transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease; }
.card::before { content: ""; position: absolute; left: 0; top: 0; height: 100%; width: 3px; background: linear-gradient(180deg, var(--accent), transparent); opacity: 0.55; }
.card:hover { transform: translateY(-2px); border-color: var(--border-strong); box-shadow: 0 14px 30px -18px rgba(0,0,0,0.8); }
.card-top { display: flex; align-items: center; justify-content: space-between; }
.card .label { color: var(--text-3); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.card-ic { color: var(--text-3); width: 16px; height: 16px; opacity: 0.85; }
.card .value { color: #fff; font-family: var(--mono); font-size: 27px; font-weight: 700; letter-spacing: -0.02em; margin-top: 8px; }
.card .sub { color: var(--text-3); font-size: 12px; margin-top: 3px; }

/* ── Chart ─────────────────────────────────────────────── */
.chart-box { margin-bottom: 24px; position: relative; }
canvas { width: 100% !important; display: block; }
.chart-tip {
  position: absolute; pointer-events: none; z-index: 20; opacity: 0;
  transform: translate(-50%, -8px); transition: opacity 0.12s ease;
  background: rgba(13,17,24,0.96); border: 1px solid var(--border-strong);
  border-radius: 10px; padding: 9px 11px; font-size: 12px; min-width: 130px;
  box-shadow: 0 10px 30px -8px rgba(0,0,0,0.8); backdrop-filter: blur(6px);
}
.chart-tip.show { opacity: 1; }
.chart-tip .tip-day { color: var(--text-2); font-size: 11px; margin-bottom: 6px; font-family: var(--mono); }
.chart-tip .tip-row { display: flex; align-items: center; gap: 7px; margin-top: 3px; }
.chart-tip .tip-row .nm { color: var(--text-2); flex: 1; }
.chart-tip .tip-row .vl { color: var(--text); font-family: var(--mono); font-variant-numeric: tabular-nums; }
.chart-tip .tip-total { margin-top: 7px; padding-top: 6px; border-top: 1px solid var(--border); display: flex; justify-content: space-between; }
.chart-tip .tip-total .nm { color: var(--text-3); } .chart-tip .tip-total .vl { color: #fff; font-family: var(--mono); font-weight: 600; }

/* ── Segmented range control ───────────────────────────── */
.range-bar { display: inline-flex; gap: 3px; margin-bottom: 14px; flex-wrap: wrap; background: #0d1118; border: 1px solid var(--border); border-radius: 11px; padding: 4px; }
.range-bar button {
  background: transparent; color: var(--text-3); border: none; border-radius: 8px;
  padding: 6px 13px; font-size: 12.5px; font-weight: 550; cursor: pointer; font-family: var(--sans);
  transition: color 0.15s ease, background 0.15s ease;
}
.range-bar button:hover { color: var(--text); }
.range-bar button.active { background: linear-gradient(180deg, var(--accent), var(--accent-strong)); color: #fff; box-shadow: 0 4px 12px -4px rgba(217,119,87,0.6); }
.range-bar button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* ── Tables ────────────────────────────────────────────── */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--text-3); font-weight: 600; padding: 9px 12px; border-bottom: 1px solid var(--border); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; }
td { padding: 10px 12px; border-bottom: 1px solid rgba(35,42,53,0.6); color: var(--text-2); }
tbody tr { transition: background 0.12s ease; }
tbody tr:hover td { background: var(--surface-hover); }
tbody tr:last-child td { border-bottom: none; }
td:first-child { color: var(--text); font-weight: 500; }
.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.cost { color: var(--green); }
.muted { color: var(--text-3); }

/* ── Expandable sessions ───────────────────────────────── */
.group-row { cursor: pointer; }
.group-row td:first-child { font-weight: 600; }
.toggle { display: inline-block; width: 16px; font-size: 10px; color: var(--text-3); transition: transform 0.18s ease; }
.toggle.open { transform: rotate(90deg); }
.child-row td { padding-left: 34px; background: rgba(10,12,16,0.5); }
.child-row td:first-child { font-weight: 400; color: var(--text-3); font-family: var(--mono); font-size: 12px; }
.badge { display: inline-block; background: var(--surface-hover); color: var(--text-2); font-size: 11px; padding: 1px 8px; border-radius: 999px; margin-left: 7px; font-weight: 500; border: 1px solid var(--border); }

/* ── Legend ─────────────────────────────────────────────── */
.legend { display: flex; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-2); }
.legend-dot { width: 9px; height: 9px; border-radius: 3px; }

/* ── Modal (native <dialog>) ───────────────────────────── */
.modal {
  position: fixed; inset: 0; margin: auto;            /* center both axes (reset zeroed the UA margin:auto) */
  height: fit-content; max-height: calc(100vh - 32px);
  border: 1px solid var(--border-strong); padding: 0; color: var(--text);
  background: linear-gradient(180deg, var(--surface-2), var(--surface));
  border-radius: var(--radius); width: min(92vw, 400px);
  box-shadow: 0 30px 80px -20px rgba(0,0,0,0.85);
}
.modal::backdrop { background: rgba(5,7,10,0.62); backdrop-filter: blur(3px); }
.modal form { padding: 22px; }
.modal h3 { font-size: 16px; color: #fff; margin-bottom: 8px; letter-spacing: -0.01em; }
.modal-desc { font-size: 12.5px; color: var(--text-3); line-height: 1.55; margin-bottom: 18px; }
.modal-label { display: block; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-3); font-weight: 600; margin-bottom: 7px; }
.modal-input { width: 100%; background: #0d1118; border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-size: 15px; padding: 11px 12px; transition: border-color 0.15s ease, box-shadow 0.15s ease; }
.modal-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(217,119,87,0.22); }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.btn { font: inherit; font-size: 13px; font-weight: 600; padding: 9px 16px; border-radius: 9px; cursor: pointer; border: 1px solid transparent; transition: background 0.15s ease, border-color 0.15s ease, filter 0.15s ease, transform 0.08s ease; }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn:active { transform: translateY(1px); }
.btn-ghost { background: transparent; color: var(--text-2); border-color: var(--border-strong); }
.btn-ghost:hover { background: var(--surface-hover); color: var(--text); }
.btn-primary { background: linear-gradient(180deg, var(--accent), var(--accent-strong)); color: #fff; }
.btn-primary:hover { filter: brightness(1.08); }
.modal[open] { animation: modalIn 0.18s cubic-bezier(0.22,1,0.36,1); }
.modal[open]::backdrop { animation: backdropIn 0.18s ease; }
@keyframes modalIn { from { opacity: 0; transform: translateY(6px) scale(0.97); } to { opacity: 1; transform: none; } }
@keyframes backdropIn { from { opacity: 0; } to { opacity: 1; } }

@media (max-width: 640px) {
  .panel-pad, .usage-bar-container { padding: 15px 16px; }
  .card .value { font-size: 23px; }
  table { font-size: 12px; }
  th, td { padding: 8px 9px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2.5v19M2.5 12h19M5 5l14 14M19 5L5 19" stroke="#fff" stroke-width="2.1" stroke-linecap="round" opacity="0.95"/>
      </svg>
    </span>
    <div>
      <h1>Claude Usage</h1>
      <div class="subtitle" id="meta" aria-live="polite">Loading…</div>
    </div>
  </div>
  <div class="live"><span class="live-dot" id="liveDot"></span> <span>Auto-refresh 30s</span></div>
</header>

<div class="panel usage-bar-container" id="usageBar"></div>

<div class="empty-hint" id="emptyHint" hidden></div>

<div class="cards" id="cards"></div>

<div class="chart-box panel panel-pad">
  <div class="section-head"><h2>Daily Output Tokens</h2></div>
  <div class="range-bar" id="rangeBar" role="tablist" aria-label="Time range"></div>
  <div class="legend" id="legend"></div>
  <canvas id="chart" height="280"></canvas>
  <div class="chart-tip" id="chartTip"></div>
</div>

<div class="panel panel-pad chart-box">
  <div class="section-head"><h2 id="modelTableTitle">Model Breakdown</h2></div>
  <table id="modelTable"></table>
</div>

<div class="panel panel-pad chart-box">
  <div class="section-head"><h2 id="sessionTableTitle">Sessions</h2></div>
  <table id="sessionTable"></table>
</div>

<dialog id="goalModal" class="modal" aria-labelledby="goalModalTitle">
  <form method="dialog" id="goalForm">
    <h3 id="goalModalTitle">Set Daily Output Goal</h3>
    <p class="modal-desc">A personal target for output tokens per day — it drives the progress bar. This is not a Claude plan limit, just your own benchmark.</p>
    <label class="modal-label" for="goalInput">Output tokens per day</label>
    <input id="goalInput" class="modal-input num-mono" type="number" min="1" step="1000" inputmode="numeric" required>
    <div class="modal-actions">
      <button type="button" class="btn btn-ghost" id="goalCancel">Cancel</button>
      <button type="submit" class="btn btn-primary">Save goal</button>
    </div>
  </form>
</dialog>

<script>
// Personal daily output-token GOAL (not a real Claude plan limit). User-settable, persisted.
let DAILY_LIMIT = parseInt(localStorage.getItem('claudeDailyGoal') || '250000', 10) || 250000;

const MODEL_COLORS = {
  'opus':   '#d97757',
  'sonnet': '#4f8ef7',
  'haiku':  '#7ee787',
};

function colorFor(model) {
  const m = model.toLowerCase();
  if (m.includes('opus'))   return MODEL_COLORS.opus;
  if (m.includes('sonnet')) return MODEL_COLORS.sonnet;
  if (m.includes('haiku'))  return MODEL_COLORS.haiku;
  return '#8b949e';
}

function fmt(n) {
  if (n == null) return '0';
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + 'B';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toString();
}

function fmtCost(n) { return '$' + n.toFixed(2); }

let DATA = null;
let selectedRange = '1d';

const RANGES = [
  { key: '1d', label: 'Today' },
  { key: '7d', label: '7 Days' },
  { key: '14d', label: '14 Days' },
  { key: '30d', label: '30 Days' },
  { key: '90d', label: '90 Days' },
  { key: 'all', label: 'All Time' },
];

function getCutoff(rangeKey) {
  if (rangeKey === 'all') return null;
  const days = parseInt(rangeKey);
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return cutoff.toISOString().slice(0, 10);
}

function filterDays(dailyData, rangeKey) {
  const cutStr = getCutoff(rangeKey);
  if (!cutStr) return dailyData;
  return dailyData.filter(d => d.day >= cutStr);
}

// ── Canvas chart drawing ─────────────────────────────────────────────────────
function shade(hex, pct) {
  const n = parseInt(hex.slice(1), 16);
  let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const t = pct < 0 ? 0 : 255, p = Math.abs(pct);
  r = Math.round((t - r) * p + r); g = Math.round((t - g) * p + g); b = Math.round((t - b) * p + b);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}
function niceCeil(v) {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v)), base = Math.pow(10, exp), f = v / base;
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nf * base;
}

function drawChart(canvas, dailyData) {
  const ctx = canvas.getContext('2d');
  const tip = document.getElementById('chartTip');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width - 44;
  const H = 280;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';

  // Group by day
  const dayMap = {};
  const models = new Set();
  dailyData.forEach(d => {
    if (!dayMap[d.day]) dayMap[d.day] = {};
    dayMap[d.day][d.model] = (dayMap[d.day][d.model] || 0) + d.output;
    models.add(d.model);
  });

  const days = Object.keys(dayMap).sort();
  if (days.length === 0) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#788393';
    ctx.font = '13px ' + getComputedStyle(document.body).fontFamily;
    ctx.textAlign = 'center';
    ctx.fillText('No data for this range yet', W / 2, H / 2);
    canvas.onmousemove = null; canvas.onmouseleave = null;
    return;
  }

  const modelList = Array.from(models).sort();
  const stacked = days.map(day => {
    let total = 0;
    const parts = modelList.map(m => {
      const v = dayMap[day][m] || 0;
      total += v;
      return { model: m, value: v };
    });
    return { day, parts, total };
  });

  const maxVal = niceCeil(Math.max(...stacked.map(s => s.total), 1));
  const padL = 56, padR = 14, padT = 12, padB = 42;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const barW = Math.max(3, Math.min(34, (chartW / days.length) - 6));
  const gap = (chartW - barW * days.length) / Math.max(days.length - 1, 1);
  const ySteps = 4;

  function barX(i) { return padL + i * (barW + gap); }

  function paint(hl) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // Hover column highlight (behind everything)
    if (hl >= 0 && hl < stacked.length) {
      const cx = barX(hl) - gap / 2;
      ctx.fillStyle = 'rgba(255,255,255,0.035)';
      ctx.fillRect(cx, padT - 4, barW + gap, chartH + 4);
    }

    // Gridlines + y labels
    ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= ySteps; i++) {
      const y = padT + chartH - (chartH * i / ySteps);
      ctx.strokeStyle = i === 0 ? '#2d3542' : 'rgba(45,53,66,0.55)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y + 0.5);
      ctx.lineTo(W - padR, y + 0.5);
      ctx.stroke();
      ctx.fillStyle = '#788393';
      ctx.fillText(fmt(maxVal * i / ySteps), padL - 10, y);
    }

    // Bars (stacked, gradient fill, rounded top)
    stacked.forEach((s, i) => {
      const x = barX(i);
      const dim = (hl >= 0 && hl !== i);
      let yOffset = 0;
      s.parts.forEach(p => {
        if (p.value <= 0) return;
        const barH = (p.value / maxVal) * chartH;
        const y = padT + chartH - yOffset - barH;
        const base = colorFor(p.model);
        const grad = ctx.createLinearGradient(0, y, 0, y + barH);
        grad.addColorStop(0, shade(base, 0.18));
        grad.addColorStop(1, base);
        ctx.fillStyle = grad;
        ctx.globalAlpha = dim ? 0.42 : 1;
        const r = Math.min(4, barW / 2, barH);
        ctx.beginPath();
        ctx.moveTo(x, y + r);
        ctx.arcTo(x, y, x + barW, y, r);
        ctx.arcTo(x + barW, y, x + barW, y + barH, r);
        ctx.lineTo(x + barW, y + barH);
        ctx.lineTo(x, y + barH);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
        yOffset += barH;
      });

      if (days.length <= 16 || i % Math.ceil(days.length / 16) === 0) {
        ctx.fillStyle = '#788393';
        ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.save();
        ctx.translate(x + barW / 2, padT + chartH + 9);
        ctx.rotate(days.length > 22 ? -0.6 : 0);
        ctx.fillText(s.day.slice(5), 0, 0);
        ctx.restore();
      }
    });
  }

  paint(-1);

  canvas.onmousemove = (e) => {
    const r2 = canvas.getBoundingClientRect();
    const mx = e.clientX - r2.left;
    const idx = Math.round((mx - padL - barW / 2) / (barW + gap));
    if (idx >= 0 && idx < stacked.length) {
      paint(idx);
      const s = stacked[idx];
      const rows = s.parts.filter(p => p.value > 0).map(p =>
        `<div class="tip-row"><span class="legend-dot" style="background:${colorFor(p.model)}"></span><span class="nm">${p.model}</span><span class="vl">${fmt(p.value)}</span></div>`
      ).join('');
      tip.innerHTML = `<div class="tip-day">${s.day}</div>${rows}<div class="tip-total"><span class="nm">Total</span><span class="vl">${fmt(s.total)}</span></div>`;
      tip.classList.add('show');
      const cx = barX(idx) + barW / 2 + 22;        // +22: canvas left padding within panel
      tip.style.left = Math.min(cx, r2.width - 70) + 'px';
      tip.style.top = (padT + 8) + 'px';
    } else {
      paint(-1);
      tip.classList.remove('show');
    }
  };
  canvas.onmouseleave = () => { paint(-1); tip.classList.remove('show'); };
}

// ── Build legend ─────────────────────────────────────────────────────────────
function buildLegend(models) {
  const el = document.getElementById('legend');
  el.innerHTML = models.map(m =>
    `<div class="legend-item"><div class="legend-dot" style="background:${colorFor(m)}"></div>${m}</div>`
  ).join('');
}

// ── Build range bar ──────────────────────────────────────────────────────────
function buildRangeBar() {
  const el = document.getElementById('rangeBar');
  el.innerHTML = RANGES.map(r =>
    `<button role="tab" id="tab_${r.key}" aria-selected="${r.key === selectedRange}" tabindex="${r.key === selectedRange ? '0' : '-1'}" class="${r.key === selectedRange ? 'active' : ''}" onclick="setRange('${r.key}')">${r.label}</button>`
  ).join('');
}

function setRange(key) {
  selectedRange = key;
  localStorage.setItem('claudeRange', key);
  buildRangeBar();
  render();
}

// Keyboard support for the range tablist (arrow keys + roving focus)
function initTabKeys() {
  document.getElementById('rangeBar').addEventListener('keydown', e => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const i = RANGES.findIndex(r => r.key === selectedRange);
    const ni = e.key === 'ArrowRight' ? (i + 1) % RANGES.length : (i - 1 + RANGES.length) % RANGES.length;
    setRange(RANGES[ni].key);
    document.getElementById('tab_' + RANGES[ni].key)?.focus();
  });
}

let goalTrigger = null;

function setGoal() {
  const dlg = document.getElementById('goalModal');
  const input = document.getElementById('goalInput');
  input.value = DAILY_LIMIT;
  goalTrigger = document.activeElement;  // to restore focus on close
  if (typeof dlg.showModal === 'function') {
    dlg.showModal();
    input.focus(); input.select();
  } else {
    // Fallback for browsers without <dialog>
    applyGoal(prompt('Daily output-token goal:', DAILY_LIMIT));
  }
}

function applyGoal(v) {
  if (v == null) return;
  const n = parseInt(String(v).replace(/[^0-9]/g, ''), 10);
  if (n > 0) { DAILY_LIMIT = n; localStorage.setItem('claudeDailyGoal', String(n)); render(); }
}

function initGoalModal() {
  const dlg = document.getElementById('goalModal');
  document.getElementById('goalForm').addEventListener('submit', () => {
    applyGoal(document.getElementById('goalInput').value);  // method="dialog" closes it
  });
  document.getElementById('goalCancel').addEventListener('click', () => dlg.close());
  // Click on the backdrop (outside the form) closes
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => { if (goalTrigger && goalTrigger.focus) goalTrigger.focus(); });
}

// ── Render ────────────────────────────────────────────────────────────────────
function render() {
  if (!DATA) return;

  // First-run / empty-state hint
  const hasData = (DATA.models_summary || []).length > 0;
  const hint = document.getElementById('emptyHint');
  if (!hasData) {
    hint.hidden = false;
    hint.textContent = 'No usage logged yet. Use Claude Code, then refresh — this reads logs from ~/.claude/projects.';
  } else {
    hint.hidden = true;
  }

  // Usage bar — personal daily output GOAL (not a Claude plan limit)
  const t = DATA.today || {};
  const todayOut = t.output || 0;
  const pct = Math.min(100, (todayOut / DAILY_LIMIT) * 100);
  const remaining = Math.max(0, DAILY_LIMIT - todayOut);

  // Reset countdown to local midnight (matches the local 'today' grouping server-side)
  const now = new Date();
  const midnight = new Date(now);
  midnight.setHours(24, 0, 0, 0);
  const msLeft = midnight - now;
  const hrsLeft = Math.floor(msLeft / 3600000);
  const minsLeft = Math.floor((msLeft % 3600000) / 60000);

  document.getElementById('usageBar').innerHTML = `
    <div class="usage-bar-header">
      <span class="usage-bar-title">Daily Output Goal</span>
      <span class="usage-bar-pct">${pct.toFixed(1)}%</span>
    </div>
    <div class="usage-bar-track">
      <div class="usage-bar-fill" style="width:${pct}%"></div>
    </div>
    <div class="usage-bar-details">
      <span><span class="used">${fmt(todayOut)}</span> / <button class="goal-edit" onclick="setGoal()" title="Click to set your daily goal">${fmt(DAILY_LIMIT)}</button> output tokens today</span>
      <span>${pct >= 100 ? 'Goal reached' : fmt(remaining) + ' to goal'} · new day in ${hrsLeft}h ${minsLeft}m</span>
    </div>
    <div class="usage-note">A personal target you set — not a Claude plan limit. Click the number to change it.</div>
  `;

  // Cards
  const totalOut = (DATA.models_summary || []).reduce((a, m) => a + m.output, 0);
  const totalCost = (DATA.models_summary || []).reduce((a, m) => a + m.cost, 0);
  const totalTurns = (DATA.models_summary || []).reduce((a, m) => a + m.turns, 0);

  const IC = {
    up: '<svg class="card-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7M9 7h8v8"/></svg>',
    down: '<svg class="card-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 7 7 17M15 17H7V9"/></svg>',
    stack: '<svg class="card-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></svg>',
    cost: '<svg class="card-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
  };
  document.getElementById('cards').innerHTML = `
    <div class="card panel">
      <div class="card-top"><span class="label">Today Output</span>${IC.up}</div>
      <div class="value">${fmt(t.output || 0)}</div>
      <div class="sub">${fmt(t.turns || 0)} turns</div>
    </div>
    <div class="card panel">
      <div class="card-top"><span class="label">Today Input</span>${IC.down}</div>
      <div class="value">${fmt(t.input || 0)}</div>
      <div class="sub">+ ${fmt(t.cache_read || 0)} cached</div>
    </div>
    <div class="card panel">
      <div class="card-top"><span class="label">All-Time Output</span>${IC.stack}</div>
      <div class="value">${fmt(totalOut)}</div>
      <div class="sub">${fmt(totalTurns)} turns</div>
    </div>
    <div class="card panel">
      <div class="card-top"><span class="label">Est. API Cost</span>${IC.cost}</div>
      <div class="value" title="What this usage would cost at Anthropic API rates. NOT your actual bill if you're on a Pro or Max subscription.">${fmtCost(totalCost)}</div>
      <div class="sub">if billed at API rates</div>
    </div>
  `;

  // Chart
  const filtered = filterDays(DATA.daily_by_model, selectedRange);
  const models = [...new Set(filtered.map(d => d.model))].sort();
  buildLegend(models);
  drawChart(document.getElementById('chart'), filtered);

  // Model table — aggregate from filtered daily data
  const modelMap = {};
  filtered.forEach(d => {
    if (!modelMap[d.model]) modelMap[d.model] = { model: d.model, input: 0, output: 0, cache_read: 0, cache_write: 0, turns: 0 };
    const m = modelMap[d.model];
    m.input += d.input || 0;
    m.output += d.output || 0;
    m.cache_read += d.cache_read || 0;
    m.cache_write += d.cache_write || 0;
    m.turns += d.turns || 0;
  });
  const mt = Object.values(modelMap).sort((a, b) => b.output - a.output);
  const PRICING = DATA.pricing || {};   // single source of truth, shipped from the server
  let unpriced = 0;
  mt.forEach(m => {
    const tier = m.model.toLowerCase();
    const key = tier.includes('opus') ? 'opus' : tier.includes('sonnet') ? 'sonnet' : tier.includes('haiku') ? 'haiku' : null;
    const p = key ? PRICING[key] : null;
    m.cost = p ? (m.input * p.input + m.output * p.output + m.cache_read * p.cache_read + m.cache_write * p.cache_write) / 1_000_000 : 0;
    if (!p && (m.output || m.input)) unpriced++;
  });

  const rangeLabel = RANGES.find(r => r.key === selectedRange)?.label || selectedRange;
  document.getElementById('modelTableTitle').textContent = 'Model Breakdown (' + rangeLabel + ')'
    + (unpriced ? ' · ' + unpriced + ' model' + (unpriced > 1 ? 's' : '') + ' not priced' : '');

  // Accessible chart summary (canvas is otherwise invisible to screen readers)
  const chartTotal = filtered.reduce((a, d) => a + (d.output || 0), 0);
  const cv = document.getElementById('chart');
  cv.setAttribute('role', 'img');
  cv.setAttribute('aria-label', `Daily output tokens, ${rangeLabel}: ${fmt(chartTotal)} across ${models.length} model${models.length === 1 ? '' : 's'}. Exact values in the Model Breakdown table below.`);

  document.getElementById('modelTable').innerHTML = `
    <thead><tr>
      <th>Model</th><th class="num">Output</th><th class="num">Input</th>
      <th class="num">Cache Read</th><th class="num">Cache Write</th>
      <th class="num">Turns</th><th class="num">Est. Cost</th>
    </tr></thead>
    <tbody>${mt.map(m => `<tr>
      <td><span class="legend-dot" style="background:${colorFor(m.model)};display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px"></span>${m.model}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_write)}</td>
      <td class="num">${m.turns.toLocaleString()}</td>
      <td class="num cost">${m.cost > 0 ? fmtCost(m.cost) : '<span class="muted">n/a</span>'}</td>
    </tr>`).join('')}</tbody>
  `;

  // Sessions table — filtered by range, grouped by project
  const cutoff = getCutoff(selectedRange);
  const ss = (DATA.sessions || []).filter(s => {
    if (!cutoff) return true;
    return (s.last_timestamp || '').slice(0, 10) >= cutoff;
  });

  // Group by project_name
  const groups = {};
  const groupOrder = [];
  ss.forEach(s => {
    const key = s.project_name || 'unknown';
    if (!groups[key]) { groups[key] = []; groupOrder.push(key); }
    groups[key].push(s);
  });
  // Sort groups by most recent session
  groupOrder.sort((a, b) => {
    const la = groups[a][0].last_timestamp || '';
    const lb = groups[b][0].last_timestamp || '';
    return lb.localeCompare(la);
  });

  const projectCount = groupOrder.length;
  document.getElementById('sessionTableTitle').textContent = 'Sessions (' + rangeLabel + ' · ' + projectCount + ' projects, ' + ss.length + ' sessions)';

  let sessionRows = '';
  groupOrder.forEach((proj, gi) => {
    const items = groups[proj];
    const totalTurns = items.reduce((a, s) => a + s.turn_count, 0);
    const totalOut = items.reduce((a, s) => a + (s.output || 0), 0);
    const lastActive = items[0].last_timestamp || '';
    // Most used model in group
    const modelCounts = {};
    items.forEach(s => { if (s.model) modelCounts[s.model] = (modelCounts[s.model] || 0) + s.turn_count; });
    const topModel = Object.entries(modelCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || '';

    const gid = 'sg' + gi;
    if (items.length === 1) {
      // Single session — no expand
      sessionRows += `<tr>
        <td><span class="toggle" style="visibility:hidden">\u25B6</span>${esc(proj)}</td>
        <td>${esc(topModel)}</td>
        <td class="num">${totalTurns}</td>
        <td class="num">${fmt(totalOut)}</td>
        <td class="muted">${lastActive.slice(0, 16)}</td>
      </tr>`;
    } else {
      // Group header
      sessionRows += `<tr class="group-row" role="button" tabindex="0" aria-expanded="false" onclick="toggleGroup('${gid}', this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleGroup('${gid}', this);}">
        <td><span class="toggle" id="tgl_${gid}">\u25B6</span>${esc(proj)}<span class="badge">${items.length}</span></td>
        <td>${esc(topModel)}</td>
        <td class="num">${totalTurns}</td>
        <td class="num">${fmt(totalOut)}</td>
        <td class="muted">${lastActive.slice(0, 16)}</td>
      </tr>`;
      // Child rows (hidden by default)
      items.forEach(s => {
        sessionRows += `<tr class="child-row ${gid}" style="display:none">
          <td>${esc(s.session_id.slice(0, 8))}</td>
          <td>${esc(s.model || '')}</td>
          <td class="num">${s.turn_count}</td>
          <td class="num">${fmt(s.output || 0)}</td>
          <td class="muted">${(s.last_timestamp || '').slice(0, 16)}</td>
        </tr>`;
      });
    }
  });

  document.getElementById('sessionTable').innerHTML = `
    <thead><tr>
      <th>Project</th><th>Model</th><th class="num">Turns</th>
      <th class="num">Output</th><th>Last Active</th>
    </tr></thead>
    <tbody>${sessionRows}</tbody>
  `;

  document.getElementById('meta').textContent = 'Updated ' + DATA.generated_at;
}

function toggleGroup(gid, rowEl) {
  const rows = document.querySelectorAll('.' + gid);
  const tgl = document.getElementById('tgl_' + gid);
  const visible = rows[0]?.style.display !== 'none';
  rows.forEach(r => r.style.display = visible ? 'none' : '');
  if (tgl) tgl.classList.toggle('open', !visible);
  if (rowEl) rowEl.setAttribute('aria-expanded', String(!visible));
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function setDot(state) {
  const dot = document.getElementById('liveDot');
  if (dot) dot.className = 'live-dot ' + state;
}

async function loadData() {
  setDot('refreshing');
  try {
    const resp = await fetch('/api/data');
    DATA = await resp.json();
    if (DATA.error) {
      document.getElementById('usageBar').innerHTML = '<div class="empty-state">' + esc(DATA.error) + '</div>';
      document.getElementById('cards').innerHTML = '';
      document.getElementById('meta').textContent = 'No data yet';
      setDot('error');
      return;
    }
    render();
    setDot('ok');
  } catch (e) {
    console.error('Failed to load data:', e);
    document.getElementById('meta').textContent = 'Error loading data — is the server running?';
    setDot('error');
  }
}

window.addEventListener('resize', () => { if (DATA) render(); });
selectedRange = localStorage.getItem('claudeRange') || selectedRange;
buildRangeBar();
initTabKeys();
initGoalModal();
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/data":
            try:
                import scanner
                scanner.scan(db_path=DB_PATH, verbose=False)
            except Exception:
                pass
            data = get_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


def main():
    host = os.environ.get("HOST", "localhost")
    port = int(os.environ.get("PORT", "8080"))

    # Run scan first
    try:
        import scanner
        print("Scanning logs...")
        scanner.scan(db_path=DB_PATH, verbose=True)
    except Exception as e:
        print(f"Scan warning: {e}")

    server = HTTPServer((host, port), Handler)
    print(f"\nDashboard running at http://{host}:{port}")
    print("Press Ctrl+C to stop.\n")

    import webbrowser
    webbrowser.open(f"http://{host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
