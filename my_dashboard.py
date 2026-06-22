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
        return {"error": "No database found. Run: python3 cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Daily totals by model
    rows = conn.execute("""
        SELECT date(timestamp) as day, model,
               SUM(input_tokens) as input,
               SUM(output_tokens) as output,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_write,
               COUNT(*) as turns
        FROM turns
        GROUP BY day, model
        ORDER BY day
    """).fetchall()

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

    # Today's totals
    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = conn.execute("""
        SELECT SUM(input_tokens) as input,
               SUM(output_tokens) as output,
               SUM(cache_read_tokens) as cache_read,
               SUM(cache_creation_tokens) as cache_write,
               COUNT(*) as turns
        FROM turns WHERE date(timestamp) = ?
    """, (today,)).fetchone()

    today_data = dict(row) if row else {}

    conn.close()

    return {
        "daily_by_model": daily_by_model,
        "models_summary": models_summary,
        "sessions": sessions,
        "today": today_data,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Usage Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0d1117;
  color: #c9d1d9;
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}
h1 { color: #f0f6fc; font-size: 24px; margin-bottom: 4px; }
.subtitle { color: #8b949e; font-size: 13px; margin-bottom: 24px; }

/* ── Usage bar ─────────────────────────────────────────── */
.usage-bar-container {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 20px;
}
.usage-bar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.usage-bar-title { color: #f0f6fc; font-size: 14px; font-weight: 600; }
.usage-bar-pct { color: #f0f6fc; font-size: 22px; font-weight: 700; }
.usage-bar-track {
  width: 100%;
  height: 12px;
  background: #21262d;
  border-radius: 6px;
  overflow: hidden;
}
.usage-bar-fill {
  height: 100%;
  border-radius: 6px;
  transition: width 0.5s ease;
}
.usage-bar-details {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 12px;
  color: #8b949e;
}
.usage-bar-details .used { color: #c9d1d9; }

/* ── Cards ─────────────────────────────────────────────── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 28px; }
.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 16px;
}
.card .label { color: #8b949e; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { color: #f0f6fc; font-size: 26px; font-weight: 700; margin-top: 4px; }
.card .sub { color: #8b949e; font-size: 12px; margin-top: 2px; }

/* ── Chart ─────────────────────────────────────────────── */
.chart-box {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 28px;
}
.chart-box h2 { color: #f0f6fc; font-size: 16px; margin-bottom: 12px; }
canvas { width: 100% !important; }

/* ── Range buttons ─────────────────────────────────────── */
.range-bar { display: flex; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }
.range-bar button {
  background: #21262d;
  color: #8b949e;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.range-bar button.active { background: #d97757; color: #fff; border-color: #d97757; }

/* ── Tables ────────────────────────────────────────────── */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: #8b949e; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #30363d; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 8px 10px; border-bottom: 1px solid #21262d; }
tr:hover td { background: #1c2128; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.cost { color: #7ee787; }
.muted { color: #8b949e; }

/* ── Expandable sessions ───────────────────────────────── */
.group-row { cursor: pointer; }
.group-row:hover td { background: #1c2128; }
.group-row td:first-child { font-weight: 600; }
.toggle { display: inline-block; width: 16px; font-size: 11px; color: #8b949e; transition: transform 0.15s; }
.toggle.open { transform: rotate(90deg); }
.child-row td { padding-left: 30px; background: #13161d; }
.child-row td:first-child { font-weight: 400; color: #8b949e; }
.badge { display: inline-block; background: #21262d; color: #8b949e; font-size: 11px; padding: 1px 7px; border-radius: 10px; margin-left: 6px; font-weight: 400; }

/* ── Legend ─────────────────────────────────────────────── */
.legend { display: flex; gap: 16px; margin-bottom: 10px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #8b949e; }
.legend-dot { width: 10px; height: 10px; border-radius: 3px; }
</style>
</head>
<body>
<h1>Claude Usage Dashboard</h1>
<div class="subtitle" id="meta">Loading...</div>

<div class="usage-bar-container" id="usageBar"></div>

<div class="cards" id="cards"></div>

<div class="chart-box">
  <h2>Daily Output Tokens</h2>
  <div class="range-bar" id="rangeBar"></div>
  <div class="legend" id="legend"></div>
  <canvas id="chart" height="260"></canvas>
</div>

<div class="chart-box">
  <h2 id="modelTableTitle">Model Breakdown</h2>
  <table id="modelTable"></table>
</div>

<div class="chart-box">
  <h2 id="sessionTableTitle">Sessions</h2>
  <table id="sessionTable"></table>
</div>

<script>
const DAILY_LIMIT = 250_000; // output tokens per day

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
function drawChart(canvas, dailyData) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width - 40;
  const H = 260;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, W, H);

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
    ctx.fillStyle = '#8b949e';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No data for this range', W / 2, H / 2);
    return;
  }

  const modelList = Array.from(models).sort();

  // Stacked values
  const stacked = days.map(day => {
    let total = 0;
    const parts = modelList.map(m => {
      const v = dayMap[day][m] || 0;
      total += v;
      return { model: m, value: v };
    });
    return { day, parts, total };
  });

  const maxVal = Math.max(...stacked.map(s => s.total), 1);

  const padL = 58, padR = 16, padT = 10, padB = 44;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const barW = Math.max(2, Math.min(28, (chartW / days.length) - 2));
  const gap = (chartW - barW * days.length) / Math.max(days.length - 1, 1);

  // Y-axis grid
  const ySteps = 5;
  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#8b949e';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= ySteps; i++) {
    const y = padT + chartH - (chartH * i / ySteps);
    const val = maxVal * i / ySteps;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    ctx.fillText(fmt(val), padL - 6, y + 4);
  }

  // Bars (stacked)
  stacked.forEach((s, i) => {
    const x = padL + i * (barW + gap);
    let yOffset = 0;
    s.parts.forEach(p => {
      const barH = (p.value / maxVal) * chartH;
      ctx.fillStyle = colorFor(p.model);
      ctx.beginPath();
      const y = padT + chartH - yOffset - barH;
      // Rounded top corners for top segment
      const r = Math.min(3, barW / 2, barH);
      if (barH > 0) {
        ctx.moveTo(x, y + r);
        ctx.arcTo(x, y, x + barW, y, r);
        ctx.arcTo(x + barW, y, x + barW, y + barH, r);
        ctx.lineTo(x + barW, y + barH);
        ctx.lineTo(x, y + barH);
        ctx.closePath();
        ctx.fill();
      }
      yOffset += barH;
    });

    // X label (show subset)
    if (days.length <= 14 || i % Math.ceil(days.length / 14) === 0) {
      ctx.fillStyle = '#8b949e';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';
      const label = s.day.slice(5); // MM-DD
      ctx.save();
      ctx.translate(x + barW / 2, padT + chartH + 8);
      ctx.rotate(days.length > 20 ? -0.6 : 0);
      ctx.fillText(label, 0, 10);
      ctx.restore();
    }
  });

  // Tooltip on hover
  canvas.onmousemove = (e) => {
    const rect2 = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect2.left);
    const idx = Math.floor((mx - padL) / (barW + gap));
    if (idx >= 0 && idx < stacked.length) {
      const s = stacked[idx];
      canvas.title = s.day + ': ' + fmt(s.total) + ' output tokens';
    } else {
      canvas.title = '';
    }
  };
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
    `<button class="${r.key === selectedRange ? 'active' : ''}" onclick="setRange('${r.key}')">${r.label}</button>`
  ).join('');
}

function setRange(key) {
  selectedRange = key;
  buildRangeBar();
  render();
}

// ── Render ────────────────────────────────────────────────────────────────────
function render() {
  if (!DATA) return;

  // Usage bar
  const t = DATA.today || {};
  const todayOut = t.output || 0;
  const pct = Math.min(100, (todayOut / DAILY_LIMIT) * 100);
  const remaining = Math.max(0, DAILY_LIMIT - todayOut);
  let barColor = '#7ee787'; // green
  if (pct >= 80) barColor = '#f85149'; // red
  else if (pct >= 50) barColor = '#d29922'; // yellow

  // Estimate reset time (midnight local)
  const now = new Date();
  const midnight = new Date(now);
  midnight.setHours(24, 0, 0, 0);
  const msLeft = midnight - now;
  const hrsLeft = Math.floor(msLeft / 3600000);
  const minsLeft = Math.floor((msLeft % 3600000) / 60000);

  document.getElementById('usageBar').innerHTML = `
    <div class="usage-bar-header">
      <span class="usage-bar-title">Today's Usage</span>
      <span class="usage-bar-pct" style="color:${barColor}">${pct.toFixed(1)}%</span>
    </div>
    <div class="usage-bar-track">
      <div class="usage-bar-fill" style="width:${pct}%;background:${barColor}"></div>
    </div>
    <div class="usage-bar-details">
      <span><span class="used">${fmt(todayOut)}</span> / ${fmt(DAILY_LIMIT)} output tokens</span>
      <span>${fmt(remaining)} remaining · resets in ${hrsLeft}h ${minsLeft}m</span>
    </div>
  `;

  // Cards
  const totalOut = (DATA.models_summary || []).reduce((a, m) => a + m.output, 0);
  const totalCost = (DATA.models_summary || []).reduce((a, m) => a + m.cost, 0);
  const totalTurns = (DATA.models_summary || []).reduce((a, m) => a + m.turns, 0);

  document.getElementById('cards').innerHTML = `
    <div class="card">
      <div class="label">Today Output</div>
      <div class="value">${fmt(t.output || 0)}</div>
      <div class="sub">${fmt(t.turns || 0)} turns</div>
    </div>
    <div class="card">
      <div class="label">Today Input</div>
      <div class="value">${fmt(t.input || 0)}</div>
      <div class="sub">+ ${fmt(t.cache_read || 0)} cached</div>
    </div>
    <div class="card">
      <div class="label">All-Time Output</div>
      <div class="value">${fmt(totalOut)}</div>
      <div class="sub">${fmt(totalTurns)} turns</div>
    </div>
    <div class="card">
      <div class="label">Est. API Cost</div>
      <div class="value cost">${fmtCost(totalCost)}</div>
      <div class="sub">all time</div>
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
  mt.forEach(m => {
    const tier = m.model.toLowerCase();
    let p = null;
    if (tier.includes('opus'))   p = { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25 };
    if (tier.includes('sonnet')) p = { input: 3, output: 15, cache_read: 0.3, cache_write: 3.75 };
    if (tier.includes('haiku'))  p = { input: 1, output: 5, cache_read: 0.1, cache_write: 1.25 };
    m.cost = p ? (m.input * p.input + m.output * p.output + m.cache_read * p.cache_read + m.cache_write * p.cache_write) / 1_000_000 : 0;
  });

  const rangeLabel = RANGES.find(r => r.key === selectedRange)?.label || selectedRange;
  document.getElementById('modelTableTitle').textContent = 'Model Breakdown (' + rangeLabel + ')';

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
      sessionRows += `<tr class="group-row" onclick="toggleGroup('${gid}')">
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

  document.getElementById('meta').textContent = 'Updated: ' + DATA.generated_at + ' · Auto-refresh 30s';
}

function toggleGroup(gid) {
  const rows = document.querySelectorAll('.' + gid);
  const tgl = document.getElementById('tgl_' + gid);
  const visible = rows[0]?.style.display !== 'none';
  rows.forEach(r => r.style.display = visible ? 'none' : '');
  if (tgl) tgl.classList.toggle('open', !visible);
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function loadData() {
  try {
    const resp = await fetch('/api/data');
    DATA = await resp.json();
    if (DATA.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#f87171">' + esc(DATA.error) + '</div>';
      return;
    }
    render();
  } catch (e) {
    console.error('Failed to load data:', e);
    document.getElementById('meta').textContent = 'Error loading data. Is the server running?';
  }
}

window.addEventListener('resize', () => { if (DATA) render(); });
buildRangeBar();
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
