from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import html
import io
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from geo import destination_point, nm_to_km


LOG = logging.getLogger(__name__)
_LOG_EVENTS_CACHE: dict[str, dict] = {}


INDEX_HTML = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aircraft Transit Hunter</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb; --surface: rgba(255,255,255,.88); --surface-solid: #fff;
      --surface-2: #edf2f8; --surface-3: #e4ebf4; --line: rgba(51,65,85,.13);
      --text: #101828; --muted: #667085; --faint: #98a2b3;
      --accent: #6558f5; --accent-2: #16b8a6; --accent-rgb: 101,88,245;
      --good: #079455; --warn: #dc6803; --bad: #d92d20;
      --shadow: 0 14px 40px rgba(36,48,73,.08), 0 2px 6px rgba(36,48,73,.04);
      --sidebar: rgba(248,250,253,.9);
    }
    body.dark {
      color-scheme: dark;
      --bg: #070b14; --surface: rgba(16,23,38,.82); --surface-solid: #101726;
      --surface-2: #172033; --surface-3: #202b42; --line: rgba(148,163,184,.14);
      --text: #f1f5fb; --muted: #94a3b8; --faint: #64748b;
      --accent: #8b7cff; --accent-2: #2dd4bf; --accent-rgb: 139,124,255;
      --good: #3ddc97; --warn: #fbbf55; --bad: #fb7185;
      --shadow: 0 18px 50px rgba(0,0,0,.28), inset 0 1px rgba(255,255,255,.025);
      --sidebar: rgba(8,13,24,.88);
    }
    * { box-sizing: border-box; }
    html { background: var(--bg); }
    body {
      margin: 0; min-width: 320px; background:
        radial-gradient(circle at 82% -10%, rgba(var(--accent-rgb),.14), transparent 32rem),
        radial-gradient(circle at 28% 110%, rgba(45,212,191,.07), transparent 34rem), var(--bg);
      color: var(--text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px; font-variant-numeric: tabular-nums; letter-spacing: -.005em;
    }
    a { color: var(--accent); text-decoration: none; font-weight: 650; }
    a:hover { text-decoration: underline; }
    .app { min-height: 100vh; display: grid; grid-template-columns: 264px minmax(0,1fr); }
    aside {
      border-right: 1px solid var(--line); background: var(--sidebar); padding: 22px 14px 18px;
      position: sticky; top: 0; height: 100vh; overflow: auto; z-index: 10;
      backdrop-filter: blur(22px) saturate(140%);
    }
    .brand { display:flex; align-items:center; gap:12px; padding: 2px 8px 22px; margin-bottom: 13px; border-bottom:1px solid var(--line); }
    .brand-mark { position:relative; width:40px; height:40px; flex:0 0 40px; border:1px solid rgba(var(--accent-rgb),.45); border-radius:13px; background:linear-gradient(145deg,rgba(var(--accent-rgb),.24),rgba(45,212,191,.08)); box-shadow:0 8px 24px rgba(var(--accent-rgb),.18); }
    .brand-mark:before,.brand-mark:after { content:""; position:absolute; inset:8px; border:1px solid var(--accent); border-radius:50%; opacity:.75; }
    .brand-mark:after { inset:18px; background:var(--accent-2); border:0; box-shadow:0 0 10px var(--accent-2); }
    .brand-copy { min-width:0; }
    .brand h1 { font-size:14px; margin:0 0 4px; font-weight:800; letter-spacing:.02em; white-space:nowrap; }
    .brand-sub { color:var(--muted); font-size:10px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
    .nav-label { margin:18px 11px 7px; color:var(--faint); font-size:9px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
    nav { display:grid; gap:3px; }
    .tab-btn { width:100%; display:flex; align-items:center; gap:11px; text-align:left; border:1px solid transparent; background:transparent; color:var(--muted); padding:10px 11px; border-radius:10px; cursor:pointer; font-weight:650; transition:.16s ease; }
    .tab-btn .nav-icon { width:20px; text-align:center; font-size:15px; opacity:.9; filter:saturate(.8); }
    .tab-btn:hover { color:var(--text); background:rgba(var(--accent-rgb),.08); transform:translateX(2px); }
    .tab-btn.active { color:var(--text); border-color:rgba(var(--accent-rgb),.24); background:linear-gradient(90deg,rgba(var(--accent-rgb),.20),rgba(var(--accent-rgb),.07)); box-shadow:inset 3px 0 var(--accent); }
    .sidebar-foot { margin:22px 8px 0; padding:12px; border:1px solid var(--line); border-radius:12px; color:var(--muted); font-size:11px; line-height:1.6; background:rgba(var(--accent-rgb),.04); }
    .live-dot { display:inline-block; width:7px; height:7px; margin-right:7px; border-radius:50%; background:var(--good); box-shadow:0 0 0 4px color-mix(in srgb,var(--good) 14%,transparent); animation:pulse 2s infinite; }
    @keyframes pulse { 50% { box-shadow:0 0 0 7px transparent; } }
    main { min-width:0; }
    header { min-height:76px; display:flex; align-items:center; justify-content:space-between; gap:18px; padding:12px 24px; border-bottom:1px solid var(--line); background:color-mix(in srgb,var(--bg) 72%,transparent); position:sticky; top:0; z-index:8; backdrop-filter:blur(22px) saturate(140%); }
    .header-left { display:flex; align-items:center; gap:14px; min-width:0; }
    .view-copy { min-width:0; }
    .eyebrow { color:var(--accent-2); font-size:9px; line-height:1; font-weight:850; letter-spacing:.16em; text-transform:uppercase; margin-bottom:5px; }
    .title { font-size:21px; font-weight:790; line-height:1.15; white-space:nowrap; letter-spacing:-.025em; }
    .update-badge { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:11px; padding:6px 9px; border:1px solid var(--line); border-radius:999px; background:var(--surface); white-space:nowrap; }
    .header-right { display:flex; align-items:center; gap:7px; flex-wrap:wrap; justify-content:flex-end; }
    input,select,button { font:inherit; color:var(--text); background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:8px 10px; min-height:38px; outline:none; transition:.15s ease; }
    input:hover,select:hover,button:hover { border-color:rgba(var(--accent-rgb),.45); }
    input:focus,select:focus,button:focus-visible { border-color:var(--accent); box-shadow:0 0 0 3px rgba(var(--accent-rgb),.13); }
    input { width:210px; padding-left:34px; background-image:linear-gradient(45deg,transparent 45%,var(--muted) 46% 54%,transparent 55%),radial-gradient(circle,transparent 45%,var(--muted) 48% 57%,transparent 59%); background-size:7px 7px,13px 13px; background-position:21px 22px,10px 11px; background-repeat:no-repeat; }
    button { cursor:pointer; font-weight:700; }
    button.primary { color:white; border-color:transparent; background:linear-gradient(135deg,var(--accent),#6d5dfc 55%,#4f46e5); box-shadow:0 8px 22px rgba(var(--accent-rgb),.24); }
    button.primary:hover { transform:translateY(-1px); box-shadow:0 11px 26px rgba(var(--accent-rgb),.32); }
    .icon-btn { width:38px; padding:0; display:grid; place-items:center; font-size:16px; }
    .content { padding:22px 24px 38px; max-width:1880px; margin:0 auto; }
    .tab { display:none; animation:fade-in .2s ease; }
    .tab.active { display:block; }
    @keyframes fade-in { from { opacity:0; transform:translateY(4px); } }
    .metrics { display:grid; grid-template-columns:repeat(6,minmax(145px,1fr)); gap:12px; }
    .metric,.panel { background:var(--surface); border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); backdrop-filter:blur(16px); }
    .metric { position:relative; padding:17px 17px 15px; min-width:0; overflow:hidden; }
    .metric:after { content:""; position:absolute; right:-18px; top:-26px; width:78px; height:78px; border-radius:50%; background:rgba(var(--accent-rgb),.08); }
    .metric .value { font-size:27px; font-weight:820; line-height:1.05; letter-spacing:-.035em; }
    .metric .label { color:var(--muted); margin-top:8px; font-size:10px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
    .metric.good .value{color:var(--good)} .metric.warn .value{color:var(--warn)} .metric.bad .value{color:var(--bad)}
    .grid-2 { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr); gap:14px; margin-top:14px; }
    .grid-3 { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:14px; }
    .panel { padding:16px; min-width:0; }
    .panel h2 { margin:0 0 13px; font-size:14px; font-weight:780; letter-spacing:-.01em; }
    .panel-head { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:13px; }
    .panel-head h2 { margin:0; }
    .scroll { overflow:auto; max-height:520px; border:1px solid var(--line); border-radius:12px; background:color-mix(in srgb,var(--surface-solid) 55%,transparent); }
    table { width:100%; border-collapse:separate; border-spacing:0; font-size:12px; }
    th,td { text-align:left; padding:10px 11px; border-bottom:1px solid var(--line); vertical-align:top; white-space:nowrap; }
    th { color:var(--muted); background:color-mix(in srgb,var(--surface-2) 90%,transparent); font-size:9px; letter-spacing:.08em; text-transform:uppercase; font-weight:800; position:sticky; top:0; z-index:1; backdrop-filter:blur(10px); }
    tbody tr:last-child td { border-bottom:0; }
    td.wrap { white-space:normal; min-width:260px; line-height:1.55; }
    tr:hover td { background:rgba(var(--accent-rgb),.055); }
    .pill { display:inline-flex; align-items:center; gap:5px; padding:4px 8px; border-radius:999px; border:1px solid var(--line); font-size:10px; font-weight:800; background:var(--surface-2); }
    .pill:before { content:""; width:5px; height:5px; border-radius:50%; background:currentColor; }
    .pill.good{color:var(--good)} .pill.warn{color:var(--warn)} .pill.bad{color:var(--bad)}
    .muted{color:var(--muted)} .mono{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:.94em}
    .scorebar { width:76px; height:6px; background:var(--surface-3); border-radius:999px; overflow:hidden; display:inline-block; vertical-align:middle; }
    .scorebar span { display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg,var(--accent),var(--accent-2)); box-shadow:0 0 8px rgba(var(--accent-rgb),.45); }
    .map-layout { display:grid; grid-template-columns:minmax(0,1fr) 360px; gap:14px; }
    .map-frame { width:100%; height:calc(100vh - 142px); min-height:560px; border:1px solid var(--line); border-radius:16px; background:var(--surface-2); box-shadow:var(--shadow); }
    .detail { max-height:calc(100vh - 142px); overflow:auto; }
    pre { margin:0; padding:17px; border:1px solid rgba(139,124,255,.16); border-radius:12px; background:#060a12; color:#c9d7ea; white-space:pre-wrap; word-break:break-word; font-size:11px; line-height:1.65; max-height:680px; overflow:auto; box-shadow:inset 0 0 30px rgba(0,0,0,.25); }
    .chart { display:flex; align-items:end; gap:3px; height:130px; padding:12px 4px 0; border-top:1px solid var(--line); background:linear-gradient(180deg,transparent,rgba(var(--accent-rgb),.025)); }
    .bar { flex:1; min-width:4px; background:linear-gradient(180deg,var(--accent-2),var(--accent)); border-radius:4px 4px 1px 1px; opacity:.85; box-shadow:0 0 10px rgba(var(--accent-rgb),.13); }
    .kv { display:grid; grid-template-columns:150px 1fr; gap:9px 12px; align-items:center; }
    .kv div:nth-child(odd) { color:var(--muted); font-size:11px; }
    @media(max-width:1280px){.metrics{grid-template-columns:repeat(3,1fr)} input{width:180px}}
    @media(max-width:900px){
      .app{grid-template-columns:1fr} aside{position:relative;height:auto;padding:12px} .brand{padding-bottom:12px;margin-bottom:8px}
      nav{display:flex;overflow:auto;padding-bottom:3px}.nav-label,.sidebar-foot{display:none}.tab-btn{min-width:max-content;padding:9px 12px}.tab-btn:hover{transform:none}.tab-btn.active{box-shadow:inset 0 -2px var(--accent)}
      header{position:relative;min-height:auto;padding:14px;align-items:stretch;flex-direction:column}.header-left,.header-right{width:100%}.header-right{justify-content:flex-start}input{width:min(100%,260px)}
      .content{padding:14px}.grid-2,.grid-3,.map-layout{grid-template-columns:1fr}.map-frame{height:520px;min-height:420px}.detail{max-height:none}
    }
    @media(max-width:560px){.metrics{grid-template-columns:repeat(2,1fr)}.metric .value{font-size:23px}.update-badge{display:none}input{width:100%}.header-right>*{flex:1 1 auto}.icon-btn{flex:0 0 38px}.content{padding:10px}.panel{padding:11px;border-radius:13px}}
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand"><div class="brand-mark"></div><div class="brand-copy"><h1>Transit Hunter</h1><div class="brand-sub">Prediction intelligence</div></div></div>
    <div class="nav-label">Analityka</div>
    <nav id="nav"></nav>
    <div class="sidebar-foot"><span class="live-dot"></span>System pracuje<br><span class="muted">Predykcja · geometria · walidacja</span></div>
  </aside>
  <main>
    <header>
      <div class="header-left"><div class="view-copy"><div class="eyebrow">Aircraft intelligence</div><div class="title" id="viewTitle">Przegląd</div></div><span class="update-badge"><span class="live-dot"></span><span id="updated">ładowanie...</span></span></div>
      <div class="header-right">
        <input id="search" placeholder="Szukaj ICAO / callsign">
        <select id="range"><option value="15m">15 min</option><option value="30m">30 min</option><option value="1h">1 h</option><option value="6h">6 h</option><option value="today" selected>dziś</option></select>
        <select id="refresh"><option value="2000">2 s</option><option value="5000" selected>5 s</option><option value="15000">15 s</option><option value="0">off</option></select>
        <button id="theme" class="icon-btn" title="Zmień motyw">◐</button>
        <button class="primary" onclick="refreshAll()">↻ Odśwież</button>
      </div>
    </header>
    <div class="content">
      <section id="overview" class="tab active"></section>
      <section id="maptab" class="tab"></section>
      <section id="candidates" class="tab"></section>
      <section id="runs" class="tab"></section>
      <section id="aircraft" class="tab"></section>
      <section id="geometry" class="tab"></section>
      <section id="filters" class="tab"></section>
      <section id="alerts" class="tab"></section>
      <section id="validations" class="tab"></section>
      <section id="feeder" class="tab"></section>
      <section id="logs" class="tab"></section>
      <section id="config" class="tab"></section>
      <section id="export" class="tab"></section>
    </div>
  </main>
</div>
<script>
const tabs = [
  ['overview','Analiza','◈'], ['validations','Walidacja HIT/MISS','◎'], ['alerts','Alerty','◉'],
  ['geometry','Geometria','⌁'], ['filters','Lejek i filtry','≋'], ['candidates','Kandydaci','◇'],
  ['maptab','Mapa live','⌖'], ['aircraft','Samoloty','✈'], ['runs','Cykle','↻'],
  ['feeder','Feeder / ADS-B','⌁'], ['logs','Logi','▤'], ['config','Konfiguracja','⚙'], ['export','Eksport','⇩']
];
let active = 'overview', timer = null, lastData = {}, refreshInFlight = false, pendingRefresh = false;
let operationalRange = 'today';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v ? new Date(v).toLocaleString('pl-PL') : '-';
const num = (v, d=0) => v === null || v === undefined || Number.isNaN(Number(v)) ? '-' : Number(v).toLocaleString('pl-PL', {maximumFractionDigits:d, minimumFractionDigits:d});
const clsStatus = s => s === 'ALERT_SENT' || s === 'ALERT_READY' || s === 'OBSERVATION_CANDIDATE' ? 'good' : s === 'REJECTED' ? 'bad' : 'warn';
const params = () => `range=${encodeURIComponent(range.value)}&q=${encodeURIComponent(search.value.trim())}`;
async function getJson(path) { const r = await fetch(path, {cache:'no-store'}); if (!r.ok) throw new Error(path + ' ' + r.status); return r.json(); }
async function getText(path) { const r = await fetch(path, {cache:'no-store'}); if (!r.ok) throw new Error(path + ' ' + r.status); return r.text(); }
function renderNav() {
  nav.innerHTML = tabs.map(([id,label,icon]) => `<button class="tab-btn ${id===active?'active':''}" onclick="showTab('${id}')"><span class="nav-icon">${esc(icon)}</span><span>${esc(label)}</span></button>`).join('');
}
function localDateValue(value) {
  const year=value.getFullYear(), month=String(value.getMonth()+1).padStart(2,'0'), day=String(value.getDate()).padStart(2,'0');
  return `${year}-${month}-${day}`;
}
function configureRangeForTab(id, previousTab) {
  if (id === 'validations') {
    const keep = previousTab === 'validations' ? range.value : 'today';
    const today = new Date(); today.setHours(12,0,0,0);
    const options = [`<option value="today">dzisiaj — ${today.toLocaleDateString('pl-PL',{day:'2-digit',month:'2-digit'})}</option>`];
    for (let offset=1; offset<=30; offset++) {
      const value = new Date(today); value.setDate(today.getDate()-offset);
      const prefix = offset === 1 ? 'wczoraj — ' : '';
      options.push(`<option value="date:${localDateValue(value)}">${prefix}${value.toLocaleDateString('pl-PL',{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'})}</option>`);
    }
    range.innerHTML = options.join('');
    range.value = [...range.options].some(option=>option.value===keep) ? keep : 'today';
    return;
  }
  if (previousTab !== 'validations') operationalRange = range.value;
  range.innerHTML = '<option value="15m">15 min</option><option value="30m">30 min</option><option value="1h">1 h</option><option value="6h">6 h</option><option value="today">dziś</option>';
  range.value = operationalRange;
}
function showTab(id) {
  const previousTab = active;
  if (previousTab !== 'validations') operationalRange = range.value;
  active = id; configureRangeForTab(id, previousTab); document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === id));
  viewTitle.textContent = tabs.find(t => t[0] === id)?.[1] || 'Panel'; renderNav(); refreshAll();
}
function table(headers, rows, opts={}) {
  const body = rows.length ? rows.map(r => `<tr>${headers.map(h => `<td class="${h.cls||''}">${h.fn(r)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}" class="muted">Brak danych</td></tr>`;
  return `<div class="scroll" style="max-height:${opts.h||520}px"><table><thead><tr>${headers.map(h => `<th>${esc(h.name)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function metric(label, value, kind='') { return `<div class="metric ${kind}"><div class="value">${esc(value)}</div><div class="label">${esc(label)}</div></div>`; }
function score(v) { const n = Number(v || 0); return `<span class="scorebar"><span style="width:${Math.max(0, Math.min(100, n*100))}%"></span></span> ${num(n,2)}`; }
function bars(rows, key) {
  const vals = rows.map(r => Number(r[key] || 0)); const max = Math.max(1, ...vals);
  return `<div class="chart">${vals.map(v => `<div class="bar" title="${v}" style="height:${Math.max(2, v/max*110)}px"></div>`).join('')}</div>`;
}
async function refreshAll() {
  if (refreshInFlight) { pendingRefresh = true; return; }
  refreshInFlight = true;
  pendingRefresh = false;
  const requestedActive = active;
  try {
    const q = params();
    const next = {...lastData};
    if (!next.overview || requestedActive === 'overview') next.overview = await getJson('/api/overview?' + q);
    if (requestedActive === 'overview') {
      // Keep the landing view cheap; detailed tabs load log-heavy data on demand.
    } else if (requestedActive === 'maptab') {
      next.mapData = await getJson('/api/map?' + q);
    } else if (requestedActive === 'candidates') next.candidates = await getJson('/api/candidates?' + q);
    else if (requestedActive === 'runs') next.runs = await getJson('/api/runs?' + q);
    else if (requestedActive === 'aircraft') next.aircraft = await getJson('/api/aircraft?' + q);
    else if (requestedActive === 'geometry') next.geometry = await getJson('/api/geometry?' + q);
    else if (requestedActive === 'filters') next.filters = await getJson('/api/filters?' + q);
    else if (requestedActive === 'alerts') next.alerts = await getJson('/api/alerts?' + q);
    else if (requestedActive === 'validations') next.validations = await getJson('/api/validations?' + q);
    else if (requestedActive === 'feeder') next.feeder = await getJson('/api/feeder?' + q);
    else if (requestedActive === 'logs') next.logs = await getText('/api/logs?lines=220&' + q);
    else if (requestedActive === 'config') next.config = await getJson('/api/config');
    if (requestedActive !== active) return;
    lastData = next;
    updated.textContent = 'Aktualizacja ' + new Date().toLocaleTimeString('pl-PL');
    renderActive();
  } catch (e) { updated.textContent = 'Błąd: ' + e.message; }
  finally {
    refreshInFlight = false;
    if (pendingRefresh) refreshAll();
  }
}
function renderActive() {
  if (!lastData.overview) return;
  ({overview: renderOverview, maptab: renderMapTab, candidates: renderCandidates, runs: renderRuns, aircraft: renderAircraft,
    geometry: renderGeometry, filters: renderFilters, alerts: renderAlerts, validations: renderValidations, feeder: renderFeeder, logs: renderLogs,
    config: renderConfig, export: renderExport}[active] || renderOverview)();
}
function renderOverview() {
  const o = lastData.overview;
  const v = o.validation_summary || {};
  const evaluated = Number(v.hit||0) + Number(v.miss||0);
  const hitRate = evaluated ? `${num(Number(v.hit||0)/evaluated*100,1)}%` : '—';
  overview.innerHTML = `<div class="metrics">
    ${metric('Trafienia HIT', num(v.hit), 'good')}${metric('Chybienia MISS', num(v.miss), v.miss ? 'bad' : '')}
    ${metric('Skuteczność ocenionych', hitRate, evaluated ? 'good' : '')}${metric('Wyniki niepewne', num(v.uncertain), v.uncertain ? 'warn' : '')}
    ${metric('Alerty', num(o.totals.alerts), '')}${metric('Kandydaci', num(o.totals.candidates), '')}
  </div><div class="grid-2">
    <div class="panel"><div class="panel-head"><h2>Ostatnie walidacje po tranzycie</h2><button onclick="showTab('validations')">pełna analiza</button></div>${table(validationHeaders(), o.latest_validations||[], {h:410})}</div>
    <div class="panel"><h2>Jakość predykcji</h2><div class="kv">
      <div>Ocenione zdarzenia</div><div>${num(evaluated)}</div><div>Pokrycie danymi</div><div>${v.data_coverage_pct==null?'—':num(v.data_coverage_pct,1)+'%'}</div>
      <div>Średni błąd czasu</div><div>${num(v.avg_abs_time_error_seconds,1)} s</div><div>Średni offset faktyczny</div><div>${num(v.avg_actual_offset,3)} średnicy</div>
      <div>Brak danych</div><div>${num(v.no_data)}</div><div>Łącznie walidacji</div><div>${num(v.total)}</div>
    </div><div class="panel-head" style="margin-top:20px"><h2>Aktywność predykcji</h2><button onclick="showTab('filters')">zobacz lejek</button></div>${bars(o.run_trend, 'candidate_count')}</div>
  </div>`;
}
function renderMapTab() {
  const mapData = lastData.mapData || {observer: {lat: 0, lon: 0}, items: []};
  const url = `/api/map-frame?${params()}`;
  const searchKm = Number(mapData.search_radius_nm || 0) * 1.852;
  const mapMetricRows = () => `<div>Analizowane samoloty</div><div>${mapData.items.length}</div><div>Obserwator</div><div>${num(mapData.observer.lat,5)}, ${num(mapData.observer.lon,5)}</div><div>Zakres</div><div>${esc(range.options[range.selectedIndex].text)}</div><div>Promień ADS-B</div><div>${num(mapData.search_radius_nm,0)} NM / ${num(searchKm,0)} km</div><div>Promień geometrii</div><div>${num(mapData.max_range_km,0)} km</div>`;
  let frame = document.getElementById('mapFrame');
  if (!frame) {
    maptab.innerHTML = `<div class="map-layout"><iframe id="mapFrame" class="map-frame" src="${url}"></iframe><div class="panel detail"><div class="panel-head"><h2>Szczegóły</h2><button onclick="refreshAll()">Odśwież</button></div><div id="mapDetail" class="kv">${mapMetricRows()}</div><h2 style="margin-top:16px">Lista analizowana</h2><div id="mapList"></div></div></div>`;
    frame = document.getElementById('mapFrame');
    frame.addEventListener('load', () => {
      if (frame.contentWindow.updateMarkers) frame.contentWindow.updateMarkers(mapData);
    });
  } else {
    const currentUrl = new URL(frame.src, window.location.href);
    const newUrl = new URL(url, window.location.href);
    if (currentUrl.searchParams.get('range') !== newUrl.searchParams.get('range') || currentUrl.searchParams.get('q') !== newUrl.searchParams.get('q')) {
       frame.src = url;
    } else if (frame.contentWindow.updateMarkers) {
       frame.contentWindow.updateMarkers(mapData);
    }
    mapDetail.innerHTML = mapMetricRows();
  }
  document.getElementById('mapList').innerHTML = table(aircraftHeaders(), mapData.items.slice(0,18), {h:420});
}
const candidateHeaders = () => [
  {name:'Czas', fn:r=>fmt(r.created_at)}, {name:'Tranzyt', fn:r=>fmt(r.transit_time_utc)}, {name:'Samolot', fn:r=>`${esc(r.callsign||'-')} <span class="muted mono">${esc(r.icao)}</span>`},
  {name:'Ciało', fn:r=>esc(r.body)}, {name:'Status', fn:r=>`<span class="pill ${clsStatus(r.status)}">${esc(r.status)}</span>`},
  {name:'Powód', fn:r=>esc(r.rejection_reason||'-')}, {name:'Score', fn:r=>score(r.score)}, {name:'Offset', fn:r=>num(r.offset_body_diameters,2)},
  {name:'Mapa', fn:r=>`<a href="${esc(r.google_maps_url)}" target="_blank">otwórz</a>`}
];
const runHeaders = () => [{name:'Start',fn:r=>fmt(r.started_at)},{name:'Koniec',fn:r=>fmt(r.finished_at)},{name:'Pobrane',fn:r=>num(r.aircraft_count_total)},{name:'Analiza',fn:r=>num(r.aircraft_count_analyzed)},{name:'Kand.',fn:r=>num(r.candidate_count)},{name:'Alerty',fn:r=>num(r.alert_count)}];
const aircraftHeaders = () => [{name:'Ostatnio',fn:r=>fmt(r.observed_at)},{name:'Samolot',fn:r=>`${esc(r.callsign||'-')} <span class="muted mono">${esc(r.icao)}</span>`},{name:'Status mapy',fn:r=>`${esc(r.map_event||'-')} ${r.map_reason ? '<span class="muted">'+esc(r.map_reason)+'</span>' : ''}`},{name:'Typ',fn:r=>esc(r.aircraft_type||'-')},{name:'Wys.',fn:r=>num(r.altitude_ft)+' ft'},{name:'GS',fn:r=>num(r.ground_speed_kt)+' kt'},{name:'Kurs',fn:r=>num(r.track_deg)+'°'},{name:'Pkt',fn:r=>num(r.points)}];
const geometryHeaders = () => [{name:'Czas',fn:r=>fmt(r.log_time)},{name:'Event',fn:r=>`<span class="pill ${r.event==='GEOMETRY_SELECTED'?'good':r.event==='GEOMETRY_NO_ALIGNMENT'?'warn':'bad'}">${esc(r.event)}</span>`},{name:'Samolot',fn:r=>`${esc(r.callsign||'-')} <span class="muted mono">${esc(r.aircraft||'-')}</span>`},{name:'Ciało',fn:r=>esc(r.closest_body||'-')},{name:'Offset',fn:r=>num(r.closest_offset_diameters,2)},{name:'Separacja',fn:r=>num(r.closest_separation_deg,3)+'°'},{name:'Elew.',fn:r=>num(r.body_elevation_deg,1)+'°'}];
const filterHeaders = () => [{name:'Status',fn:r=>esc(r.status||r.event||'-')},{name:'Powód',fn:r=>esc(r.rejection_reason||r.reason||'-')},{name:'Liczba',fn:r=>num(r.count)}];
const validationClass = r => r === 'HIT' ? 'good' : r === 'MISS' ? 'bad' : 'warn';
const direction = (v, positive, negative) => v == null ? '-' : `${Number(v)>=0 ? positive : negative} ${num(Math.abs(Number(v)),3)}`;
const validationHeaders = () => [
  {name:'Walidacja',fn:r=>fmt(r.validated_at)}, {name:'Samolot',fn:r=>`${esc(r.callsign||'-')} <span class="muted mono">${esc(r.icao)}</span>`},
  {name:'Obiekt',fn:r=>esc(r.body==='sun'?'Słońce':r.body==='moon'?'Księżyc':r.body)}, {name:'Wynik',fn:r=>`<span class="pill ${validationClass(r.result)}">${esc(r.result)}</span>`},
  {name:'Δ czasu',fn:r=>r.time_error_seconds==null?'-':`${Number(r.time_error_seconds)>=0?'+':''}${num(r.time_error_seconds,1)} s`},
  {name:'Offset prog.',fn:r=>num(r.predicted_offset_body_diameters,3)}, {name:'Offset ADS-B',fn:r=>num(r.actual_offset_body_diameters,3)},
  {name:'Pion',fn:r=>direction(r.vertical_offset_body_diameters,'↑ góra','↓ dół')}, {name:'Poziom',fn:r=>direction(r.horizontal_offset_body_diameters,'→ prawo','← lewo')}
];
function renderCandidates(){ candidates.innerHTML = `<div class="panel"><div class="panel-head"><h2>Kandydaci</h2><a href="/api/export?type=candidates&${params()}">CSV</a></div>${table(candidateHeaders(), lastData.candidates.items, {h:720})}</div>`; }
function renderRuns(){ runs.innerHTML = `<div class="grid-2"><div class="panel"><h2>Trend pobrań</h2>${bars(lastData.runs.items.slice().reverse(), 'aircraft_count_total')}</div><div class="panel"><h2>Trend analiz</h2>${bars(lastData.runs.items.slice().reverse(), 'aircraft_count_analyzed')}</div></div><div class="panel" style="margin-top:12px"><h2>Cykle predykcji</h2>${table(runHeaders(), lastData.runs.items, {h:720})}</div>`; }
function renderAircraft(){ aircraft.innerHTML = `<div class="panel"><div class="panel-head"><h2>Samoloty z ostatniego zakresu</h2><a href="/api/export?type=aircraft&${params()}">CSV</a></div>${table(aircraftHeaders(), lastData.aircraft.items, {h:760})}</div>`; }
function renderGeometry(){ geometry.innerHTML = `<div class="metrics">${metric('Zdarzenia geometrii', num(lastData.geometry.items.length))}${metric('Selected', num(lastData.geometry.summary.GEOMETRY_SELECTED||0), 'good')}${metric('No alignment', num(lastData.geometry.summary.GEOMETRY_NO_ALIGNMENT||0), 'warn')}${metric('Skipped', num(lastData.geometry.summary.GEOMETRY_SKIPPED||0), 'bad')}</div><div class="panel" style="margin-top:12px"><div class="panel-head"><h2>Geometria z logów</h2><a href="/api/export?type=geometry&${params()}">CSV</a></div>${table(geometryHeaders(), lastData.geometry.items, {h:720})}</div>`; }
function renderFilters(){ filters.innerHTML = `<div class="grid-2"><div class="panel"><h2>Statusy kandydatów</h2>${table(filterHeaders(), lastData.filters.rejections, {h:520})}</div><div class="panel"><h2>Filtry z logów</h2>${table(filterHeaders(), lastData.filters.log_rejections, {h:520})}</div></div>`; }
function renderAlerts(){ alerts.innerHTML = `<div class="panel"><h2>Alerty</h2>${table([{name:'Czas',fn:r=>fmt(r.printed_at)},{name:'Samolot',fn:r=>`${esc(r.callsign||'-')} <span class="muted mono">${esc(r.icao||'-')}</span>`},{name:'Ciało',fn:r=>esc(r.body||'-')},{name:'Score',fn:r=>r.score==null?'-':score(r.score)},{name:'Wiadomość',fn:r=>esc(r.message||'-'),cls:'wrap'}], lastData.alerts.items, {h:760})}</div>`; }
function renderValidations(){
  const d=lastData.validations||{summary:{},items:[]},v=d.summary||{};
  const evaluated=Number(v.hit||0)+Number(v.miss||0), hitRate=evaluated?`${num(Number(v.hit||0)/evaluated*100,1)}%`:'—';
  validations.innerHTML=`<div class="metrics">${metric('HIT',num(v.hit),'good')}${metric('MISS',num(v.miss),v.miss?'bad':'')}${metric('Skuteczność',hitRate,evaluated?'good':'')}${metric('Niepewne',num(v.uncertain),v.uncertain?'warn':'')}${metric('Brak danych',num(v.no_data),v.no_data?'warn':'')}${metric('Śr. błąd czasu',v.avg_abs_time_error_seconds==null?'—':num(v.avg_abs_time_error_seconds,1)+' s')}</div><div class="panel" style="margin-top:14px"><div class="panel-head"><h2>Wyniki rzeczywiste po tranzycie</h2><span class="muted">Interpolacja zapisanej ścieżki ADS-B</span></div>${table(validationHeaders(),d.items||[],{h:760})}</div>`;
}
function renderFeederBox(){ const s=lastData.feeder.stats||{}; return `<div class="kv"><div>Requesty</div><div>${num(s.request_count)}</div><div>Upstream fetch</div><div>${num(s.upstream_fetch_count)}</div><div>Błędy upstream</div><div>${num(s.upstream_error_count)}</div><div>Cache hit</div><div>${num(s.cache_hit_count)}</div><div>Stale hit</div><div>${num(s.stale_hit_count)}</div><div>429 w logach</div><div>${num(lastData.feeder.log_rate_limits)}</div></div>`; }
function renderFeeder(){ feeder.innerHTML = `<div class="grid-2"><div class="panel"><h2>Status feedera</h2>${renderFeederBox()}</div><div class="panel"><h2>Błędy ADS-B z logów</h2>${table([{name:'Czas',fn:r=>fmt(r.log_time)},{name:'Typ',fn:r=>esc(r.level)},{name:'Opis',fn:r=>esc(r.message),cls:'wrap'}], lastData.feeder.errors, {h:520})}</div></div>`; }
function renderLogs(){ logs.innerHTML = `<div class="panel"><h2>Logi</h2><pre>${esc(lastData.logs)}</pre></div>`; }
function renderConfig(){ const rows = Object.entries(lastData.config.items||{}).map(([k,v])=>({k,v})); config.innerHTML = `<div class="panel"><h2>Konfiguracja read-only</h2>${table([{name:'Klucz',fn:r=>`<span class="mono">${esc(r.k)}</span>`},{name:'Wartość',fn:r=>esc(r.v)}], rows, {h:760})}</div>`; }
function renderExport(){ document.getElementById('export').innerHTML = `<div class="panel"><h2>Eksport CSV</h2><div class="kv"><div>Walidacje HIT/MISS</div><div><a href="/api/export?type=validations&${params()}">pobierz CSV</a></div><div>Kandydaci</div><div><a href="/api/export?type=candidates&${params()}">pobierz CSV</a></div><div>Samoloty</div><div><a href="/api/export?type=aircraft&${params()}">pobierz CSV</a></div><div>Cykle</div><div><a href="/api/export?type=runs&${params()}">pobierz CSV</a></div><div>Geometria</div><div><a href="/api/export?type=geometry&${params()}">pobierz CSV</a></div></div></div>`; }
function setupRefresh(){ if (timer) clearInterval(timer); const ms = Number(refresh.value); if (ms) timer = setInterval(refreshAll, ms); }
nav.innerHTML=''; renderNav(); theme.onclick=()=>{ document.body.classList.toggle('dark'); localStorage.setItem('theme', document.body.classList.contains('dark')?'dark':'light'); };
if ((localStorage.getItem('theme') || 'dark') === 'dark') document.body.classList.add('dark');
range.onchange=refreshAll; search.oninput=()=>{ clearTimeout(window._s); window._s=setTimeout(refreshAll, 300); }; refresh.onchange=setupRefresh;
setupRefresh(); refreshAll();
</script>
</body>
</html>
"""


class JsonEncoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return super().default(value)


def start_ui_server(settings) -> ThreadingHTTPServer:
    server = _create_ui_server(settings)
    thread = Thread(target=server.serve_forever, name="ui-server", daemon=True)
    thread.start()
    LOG.info("UI server started host=%s port=%s", settings.ui_host, settings.ui_port)
    return server


def run_ui_server(settings) -> None:
    server = _create_ui_server(settings)
    LOG.info("UI server started host=%s port=%s", settings.ui_host, settings.ui_port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _create_ui_server(settings) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (settings.ui_host, settings.ui_port),
        _handler_factory(settings.database_url, settings.log_dir),
    )


def _handler_factory(database_url: str, log_dir: str):
    class UIHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            LOG.debug("UI " + fmt, *args)

        def do_HEAD(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
            else:
                self.send_error(404, "Not found")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    self._send_html(INDEX_HTML)
                elif parsed.path == "/api/overview":
                    self._send_json(_overview(database_url, log_dir, params))
                elif parsed.path == "/api/map":
                    self._send_json(_map_data(database_url, params))
                elif parsed.path == "/api/map-frame":
                    self._send_html(_map_frame_html(database_url, params))
                elif parsed.path == "/api/runs":
                    self._send_json({"items": _runs(database_url, params)})
                elif parsed.path == "/api/candidates":
                    self._send_json({"items": _candidates(database_url, params)})
                elif parsed.path == "/api/aircraft":
                    self._send_json({"items": _aircraft(database_url, params)})
                elif parsed.path == "/api/geometry":
                    self._send_json(_geometry(log_dir, params))
                elif parsed.path == "/api/filters":
                    self._send_json(_filters(database_url, log_dir, params))
                elif parsed.path == "/api/alerts":
                    self._send_json({"items": _alerts(database_url, params)})
                elif parsed.path == "/api/validations":
                    self._send_json(_validations(database_url, params))
                elif parsed.path == "/api/feeder":
                    self._send_json(_feeder(log_dir, params))
                elif parsed.path == "/api/config":
                    self._send_json({"items": _config_items()})
                elif parsed.path == "/api/logs":
                    lines = int(params.get("lines", ["180"])[0])
                    q = params.get("q", [""])[0]
                    self._send_text(_tail_log(log_dir, max(20, min(lines, 1200)), q))
                elif parsed.path == "/api/export":
                    self._send_csv(_export(database_url, log_dir, params), params.get("type", ["data"])[0])
                else:
                    self.send_error(404, "Not found")
            except Exception as exc:
                LOG.exception("UI request failed path=%s error=%s", parsed.path, exc)
                self._send_json({"error": str(exc)}, status=500)

        def _send_html(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _send_json(self, payload, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload, cls=JsonEncoder).encode("utf-8"))

        def _send_text(self, body: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8", errors="replace"))

        def _send_csv(self, rows: list[dict], name: str) -> None:
            out = io.StringIO()
            if rows:
                fieldnames: list[str] = []
                for row in rows:
                    for key in row.keys():
                        if key not in fieldnames:
                            fieldnames.append(key)
                writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename=aircraft-hunter-{name}.csv")
            self.end_headers()
            self.wfile.write(out.getvalue().encode("utf-8"))

    return UIHandler


def _query(database_url: str, sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _window(params: dict) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    value = params.get("range", ["30m"])[0]
    if value == "15m":
        return now - timedelta(minutes=15), now
    if value == "1h":
        return now - timedelta(hours=1), now
    if value == "6h":
        return now - timedelta(hours=6), now
    if value == "today":
        warsaw = ZoneInfo("Europe/Warsaw")
        local_now = now.astimezone(warsaw)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=warsaw)
        return local_start.astimezone(timezone.utc), now
    if value.startswith("date:"):
        try:
            selected_date = date.fromisoformat(value.removeprefix("date:"))
        except ValueError:
            return now - timedelta(minutes=30), now
        warsaw = ZoneInfo("Europe/Warsaw")
        local_start = datetime.combine(selected_date, time.min, tzinfo=warsaw)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
    return now - timedelta(minutes=30), now


def _search(params: dict) -> str:
    return (params.get("q", [""])[0] or "").strip().lower()


def _overview(database_url: str, log_dir: str, params: dict) -> dict:
    start, end = _window(params)
    range_value = params.get("range", ["30m"])[0]
    trend_bucket = "1 hour" if range_value == "today" else "15 minutes" if range_value == "6h" else "1 minute"
    totals = _query(database_url, """
        SELECT
          (SELECT count(*) FROM transit_candidates WHERE created_at >= %s AND created_at <= %s)::int AS candidates,
          (SELECT count(*) FROM alerts WHERE printed_at >= %s AND printed_at <= %s)::int AS alerts
    """, (start, end, start, end))[0]
    run_trend = _query(database_url, """
        SELECT date_bin(%s::interval, started_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,
               sum(aircraft_count_total)::int AS aircraft_count_total,
               sum(aircraft_count_analyzed)::int AS aircraft_count_analyzed,
               sum(candidate_count)::int AS candidate_count,
               sum(alert_count)::int AS alert_count
        FROM prediction_runs
        WHERE started_at >= %s AND started_at <= %s
        GROUP BY bucket
        ORDER BY bucket
    """, (trend_bucket, start, end))
    validation_data = _validations(database_url, params, limit=12)
    return {
        "totals": totals,
        "run_trend": run_trend,
        "validation_summary": validation_data["summary"],
        "latest_validations": validation_data["items"],
        "log_summary": {
            "errors": 0,
            "warnings": 0,
            "rate_limits": 0,
        },
    }


def _runs(database_url: str, params: dict, limit: int = 80) -> list[dict]:
    start, end = _window(params)
    return _query(database_url, """
        SELECT id, started_at, finished_at, aircraft_count_total, aircraft_count_analyzed,
               candidate_count, alert_count
        FROM prediction_runs
        WHERE started_at >= %s AND started_at <= %s
        ORDER BY started_at DESC
        LIMIT %s
    """, (start, end, limit))


def _candidates(database_url: str, params: dict, limit: int = 150) -> list[dict]:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(icao) LIKE %s OR lower(COALESCE(callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    args.append(limit)
    return _query(database_url, f"""
        SELECT id, created_at, transit_time_utc, icao, callsign, aircraft_type, body, status,
               rejection_reason, score, offset_body_diameters, observer_distance_km, google_maps_url,
               stability_score, alignment_score, altitude_score, body_elevation_score,
               aircraft_range_score, lead_time_score, observer_distance_score,
               aircraft_altitude_ft, aircraft_range_km, aircraft_track_deg, body_azimuth_deg, body_elevation_deg
        FROM transit_candidates
        WHERE created_at >= %s AND created_at <= %s {where_q}
        ORDER BY created_at DESC
        LIMIT %s
    """, tuple(args))


def _alerts(database_url: str, params: dict) -> list[dict]:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(c.icao) LIKE %s OR lower(COALESCE(c.callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    return _query(database_url, f"""
        SELECT a.printed_at, a.message, c.icao, c.callsign, c.body, c.score
        FROM alerts a
        LEFT JOIN transit_candidates c ON c.id = a.transit_candidate_id
        WHERE a.printed_at >= %s AND a.printed_at <= %s {where_q}
        ORDER BY a.printed_at DESC
        LIMIT 200
    """, tuple(args))


def _validations(database_url: str, params: dict, limit: int = 200) -> dict:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(icao) LIKE %s OR lower(COALESCE(callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    summary = _query(database_url, f"""
        SELECT
          count(*)::int AS total,
          count(*) FILTER (WHERE result='HIT')::int AS hit,
          count(*) FILTER (WHERE result='MISS')::int AS miss,
          count(*) FILTER (WHERE result='UNCERTAIN')::int AS uncertain,
          count(*) FILTER (WHERE result='NO_DATA')::int AS no_data,
          round(avg(abs(extract(epoch FROM actual_closest_time_utc - predicted_transit_time_utc)))
                FILTER (WHERE actual_closest_time_utc IS NOT NULL)::numeric, 2) AS avg_abs_time_error_seconds,
          round(avg(actual_offset_body_diameters)
                FILTER (WHERE actual_offset_body_diameters IS NOT NULL)::numeric, 4) AS avg_actual_offset,
          round(100.0 * count(*) FILTER (WHERE actual_closest_time_utc IS NOT NULL)
                / NULLIF(count(*), 0), 1) AS data_coverage_pct
        FROM transit_validations
        WHERE predicted_transit_time_utc >= %s AND predicted_transit_time_utc <= %s {where_q}
    """, tuple(args))[0]
    item_args = list(args)
    item_args.append(limit)
    items = _query(database_url, f"""
        SELECT id, validated_at, predicted_transit_time_utc, actual_closest_time_utc,
               extract(epoch FROM actual_closest_time_utc - predicted_transit_time_utc) AS time_error_seconds,
               icao, callsign, body, result,
               predicted_offset_body_diameters, actual_offset_body_diameters,
               actual_separation_deg, vertical_offset_body_diameters,
               horizontal_offset_body_diameters, message
        FROM transit_validations
        WHERE predicted_transit_time_utc >= %s AND predicted_transit_time_utc <= %s {where_q}
        ORDER BY validated_at DESC
        LIMIT %s
    """, tuple(item_args))
    return {"summary": summary, "items": items}


def _aircraft(database_url: str, params: dict) -> list[dict]:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(icao) LIKE %s OR lower(COALESCE(callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    return _query(database_url, f"""
        SELECT DISTINCT ON (icao)
          icao, callsign, aircraft_type, observed_at, lat, lon, altitude_ft, ground_speed_kt,
          track_deg, vertical_rate_fpm,
          count(*) OVER (PARTITION BY icao)::int AS points
        FROM aircraft_observations
        WHERE observed_at >= %s AND observed_at <= %s {where_q}
        ORDER BY icao, observed_at DESC
        LIMIT 120
    """, tuple(args))


def _map_data(database_url: str, params: dict) -> dict:
    start, end = _window(params)
    log_dir = os.getenv("LOG_DIR", "./logs")
    analyzed_aircraft = _latest_analyzed_aircraft_from_logs(log_dir, start, end, _search(params))
    analyzed_icaos = [item["icao"] for item in analyzed_aircraft]
    rows = _latest_observations_for_aircraft(database_url, analyzed_icaos) if analyzed_icaos else []
    status_by_icao = {item["icao"]: item for item in analyzed_aircraft}
    for row in rows:
        status = status_by_icao.get((row.get("icao") or "").lower(), {})
        row["map_event"] = status.get("event")
        row["map_reason"] = status.get("reason")
        row["map_body"] = status.get("body")
    observer = _query(database_url, """
        SELECT user_lat AS lat, user_lon AS lon
        FROM prediction_runs
        ORDER BY started_at DESC
        LIMIT 1
    """)
    obs = observer[0] if observer else {"lat": 52.0, "lon": 21.0}
    env = _read_env_file()
    search_radius_nm = float(os.getenv("SEARCH_RADIUS_NM", env.get("SEARCH_RADIUS_NM", "120")))
    max_range_km = float(os.getenv("MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY", env.get("MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY", "120")))
    return {
        "observer": obs,
        "items": rows,
        "analyzed_icaos": analyzed_icaos,
        "celestial": _celestial_for_map(log_dir, obs, start, end, max_range_km),
        "search_radius_nm": search_radius_nm,
        "max_range_km": max_range_km,
    }


def _latest_observations_for_aircraft(database_url: str, icaos: list[str]) -> list[dict]:
    if not icaos:
        return []
    return _query(database_url, """
        SELECT DISTINCT ON (icao)
          icao, callsign, aircraft_type, observed_at, lat, lon, altitude_ft, ground_speed_kt,
          track_deg, vertical_rate_fpm,
          count(*) OVER (PARTITION BY icao)::int AS points
        FROM aircraft_observations
        WHERE icao = ANY(%s)
        ORDER BY icao, observed_at DESC
        LIMIT 80
    """, (icaos,))


def _latest_analyzed_aircraft_from_logs(log_dir: str, start: datetime, end: datetime, query: str = "") -> list[dict]:
    events = [
        e for e in _parse_log_events(log_dir, start, end, query)
        if e.get("event") in {"VISIBILITY_SKIPPED", "GEOMETRY_SELECTED", "GEOMETRY_NO_ALIGNMENT", "GEOMETRY_SKIPPED"}
    ]
    if not events:
        return []
    latest_cycle = None
    for event in sorted(events, key=lambda e: e.get("log_time") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        if event.get("cycle"):
            latest_cycle = event.get("cycle")
            break
    selected = [e for e in events if e.get("cycle") == latest_cycle] if latest_cycle else events[-20:]
    seen: list[str] = []
    result: list[dict] = []
    for event in selected:
        icao = (event.get("aircraft") or "").lower()
        if icao and icao not in seen:
            seen.append(icao)
            result.append({
                "icao": icao,
                "event": event.get("event"),
                "reason": event.get("reason"),
                "body": event.get("closest_body") or event.get("body"),
            })
    return result[:80]


def _celestial_for_map(log_dir: str, observer: dict, start: datetime, end: datetime, max_range_km: float) -> list[dict]:
    try:
        lat = float(observer["lat"])
        lon = float(observer["lon"])
    except (KeyError, TypeError, ValueError):
        return []
    events = _parse_log_events(log_dir, start, end, "")
    result = []
    seen = set()
    for event in sorted(events, key=lambda item: item.get("log_time") or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        body = event.get("closest_body")
        if body not in {"Sun", "Moon"} or body in seen:
            continue
        try:
            azimuth = float(event["body_azimuth_deg"])
            elevation = float(event["body_elevation_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        marker_lat, marker_lon = destination_point(lat, lon, azimuth, max_range_km)
        result.append({
            "body": body,
            "azimuth_deg": azimuth,
            "elevation_deg": elevation,
            "illumination": None,
            "lat": marker_lat,
            "lon": marker_lon,
            "source_time": event.get("log_time"),
        })
        seen.add(body)
        if len(seen) == 2:
            break
    return result


def _map_frame_html(database_url: str, params: dict) -> str:
    data = _map_data(database_url, params)
    payload = json.dumps(data, cls=JsonEncoder)
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mapa live</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
    html, body, #map {{ width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; background: #070b14; }}
    .aircraft-label {{
      background: rgba(12, 19, 32, 0.9); border: 1px solid rgba(148,163,184,.3); border-radius: 7px;
      padding: 2px 6px; font-size: 10px; font-weight: 750; color: #eef4ff;
      white-space: nowrap; box-shadow: 0 4px 14px rgba(0,0,0,.3); margin-top: 3px; backdrop-filter:blur(8px);
    }}
    .aircraft-icon-inner {{
      width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
      background: #6558f5; color: #fff; border: 2px solid rgba(255,255,255,.9); box-shadow: 0 4px 16px rgba(0,0,0,.45);
      font-size: 15px; font-weight: bold; transition: transform 0.3s ease-out;
    }}
    .observer-icon {{
      width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
      background: #ef4444; color: #fff; border: 2px solid #fff; box-shadow: 0 0 12px rgba(239, 68, 68, 0.6);
      font-size: 16px; font-weight: 800;
    }}
    .legend {{
      position: absolute; right: 12px; bottom: 12px; z-index: 1000; background: rgba(10,16,28,.88);
      border: 1px solid rgba(148,163,184,.2); border-radius: 12px; padding: 10px 12px; font-size: 11px; line-height:1.55; color: #cbd5e1;
      box-shadow: 0 12px 34px rgba(0,0,0,.35); backdrop-filter: blur(12px); pointer-events: none;
    }}
    .circle-label {{
      background: rgba(10,16,28,.9); border: 1px solid rgba(148,163,184,.28); border-radius: 7px;
      padding: 3px 7px; color: #e5edf9; font-size: 10px; font-weight: 800;
      box-shadow: 0 4px 14px rgba(0,0,0,.25);
    }}
    .leaflet-tile-container img {{ box-shadow: 0 0 1px rgba(0,0,0,0.05); }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    let map, aircraftLayer, celestialLayer, observerMarker, searchCircle, geometryCircle, legend;
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}}[c]));
    const num = (v, d=0) => v === null || v === undefined || Number.isNaN(Number(v)) ? '-' : Number(v).toLocaleString('pl-PL', {{maximumFractionDigits:d, minimumFractionDigits:d}});
    const statusColor = a => {{
      if (a.map_event === 'GEOMETRY_SELECTED') return '#16a34a';
      if (a.map_event === 'GEOMETRY_NO_ALIGNMENT') return '#f59e0b';
      if (a.map_event === 'GEOMETRY_SKIPPED') return '#dc2626';
      if (a.map_event === 'VISIBILITY_SKIPPED') return '#64748b';
      return '#2563eb';
    }};
    const statusLabel = a => {{
      if (a.map_event === 'GEOMETRY_SELECTED') return 'wybrane do geometrii';
      if (a.map_event === 'GEOMETRY_NO_ALIGNMENT') return 'brak przecięcia';
      if (a.map_event === 'GEOMETRY_SKIPPED') return 'poza kierunkiem ciała';
      if (a.map_event === 'VISIBILITY_SKIPPED') return 'pominięte widocznością';
      return 'analizowane';
    }};

    function updateMarkers(data) {{
      if (!map) return;
      aircraftLayer.clearLayers();
      celestialLayer.clearLayers();
      const bounds = [[data.observer.lat, data.observer.lon]];
      
      observerMarker.setLatLng([data.observer.lat, data.observer.lon]);
      searchCircle.setLatLng([data.observer.lat, data.observer.lon]);
      if(data.search_radius_nm) searchCircle.setRadius(data.search_radius_nm * 1852);
      searchCircle.bindTooltip(`ADS-B ${{num(data.search_radius_nm, 0)}} NM`, {{ permanent: true, direction: 'right', className: 'circle-label' }});
      
      geometryCircle.setLatLng([data.observer.lat, data.observer.lon]);
      if(data.max_range_km) geometryCircle.setRadius(data.max_range_km * 1000);
      geometryCircle.bindTooltip(`Geometria ${{num(data.max_range_km, 0)}} km`, {{ permanent: true, direction: 'left', className: 'circle-label' }});

      (data.items || []).forEach(a => {{
        const rotation = (Number(a.track_deg || 0) % 360);
        const color = statusColor(a);
        const icon = L.divIcon({{
          className: '',
          html: `<div style="display:flex;flex-direction:column;align-items:center;">
                   <div class="aircraft-icon-inner" style="background:${{color}};transform:rotate(${{rotation}}deg)">▲</div>
                   <div class="aircraft-label">${{esc(a.callsign || a.icao)}}</div>
                 </div>`,
          iconSize: [60, 42],
          iconAnchor: [30, 12]
        }});
        L.marker([a.lat, a.lon], {{ icon, zIndexOffset: 1000 }}).addTo(aircraftLayer)
          .bindPopup(`<b>${{esc(a.callsign || a.icao)}}</b><br>Status: ${{esc(statusLabel(a))}}<br>Powód: ${{esc(a.map_reason || '-')}}<br>Typ: ${{esc(a.aircraft_type || '-')}}<br>Wys: ${{num(a.altitude_ft)}} ft<br>GS: ${{num(a.ground_speed_kt)}} kt`);
        bounds.push([a.lat, a.lon]);
      }});

      (data.celestial || []).forEach(b => {{
        const isSun = b.body === 'Sun';
        const glyph = isSun ? '☀' : '☾';
        const color = isSun ? '#f59e0b' : '#6366f1';
        const icon = L.divIcon({{
          className: '',
          html: `<div style="width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:${{color}};color:#fff;border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.2);font-size:18px;">${{glyph}}</div>`,
          iconSize: [32, 32], iconAnchor: [16, 16]
        }});
        L.marker([b.lat, b.lon], {{ icon }}).addTo(celestialLayer)
          .bindPopup(`<b>${{isSun ? 'Słońce' : 'Księżyc'}}</b><br>Az: ${{num(b.azimuth_deg, 1)}}° | El: ${{num(b.elevation_deg, 1)}}°`);
        L.polyline([[data.observer.lat, data.observer.lon], [b.lat, b.lon]], {{ color, weight: 2, opacity: 0.5, dashArray: '5, 10' }}).addTo(celestialLayer);
        bounds.push([b.lat, b.lon]);
      }});

      const viewBounds = L.latLngBounds(bounds);
      viewBounds.extend(searchCircle.getBounds());
      viewBounds.extend(geometryCircle.getBounds());
      map.fitBounds(viewBounds, {{ padding: [40, 40], maxZoom: 11 }});
      const sun = (data.celestial || []).find(b => b.body === 'Sun');
      const moon = (data.celestial || []).find(b => b.body === 'Moon');
      legend.innerHTML = `<b>Mapa Live</b><br>Samoloty: ${{data.items.length}}<br>
                          Promień Ads-b: ${{data.search_radius_nm}} NM<br>
                          Promień Geo: ${{data.max_range_km}} km<br>
                          <span style="color:#16a34a">●</span> geometria wybrana<br>
                          <span style="color:#f59e0b">●</span> brak przecięcia<br>
                          <span style="color:#dc2626">●</span> poza kierunkiem<br>
                          <span style="color:#64748b">●</span> widoczność/dystans<br>
                          Słońce: ${{sun ? num(sun.elevation_deg,1)+'°' : '-'}} | Księżyc: ${{moon ? num(moon.elevation_deg,1)+'°' : '-'}}`;
    }}

    function init() {{
      if (!window.L) return;
      const startData = {payload};
      map = L.map('map', {{ zoomControl: true, fadeAnimation: true, markerZoomAnimation: true }})
             .setView([startData.observer.lat, startData.observer.lon], 9);
      
      L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
        attribution: '&copy; CartoDB',
        subdomains: 'abcd',
        maxZoom: 20
      }}).addTo(map);

      aircraftLayer = L.layerGroup().addTo(map);
      celestialLayer = L.layerGroup().addTo(map);
      
      const observerIcon = L.divIcon({{
        className: '', html: '<div class="observer-icon">⌂</div>', iconSize: [28, 28], iconAnchor: [14, 14]
      }});
      observerMarker = L.marker([startData.observer.lat, startData.observer.lon], {{ icon: observerIcon }}).addTo(map);
      
      searchCircle = L.circle([startData.observer.lat, startData.observer.lon], {{ 
        radius: (startData.search_radius_nm || 120) * 1852, 
        color: '#10b981', weight: 1.5, fill: false, dashArray: '10, 10', opacity: 0.4 
      }}).addTo(map);

      geometryCircle = L.circle([startData.observer.lat, startData.observer.lon], {{ 
        radius: (startData.max_range_km || 120) * 1000, 
        color: '#6366f1', weight: 1.5, fill: false, dashArray: '5, 5', opacity: 0.6 
      }}).addTo(map);
      
      legend = document.createElement('div');
      legend.className = 'legend';
      document.body.appendChild(legend);
      
      updateMarkers(startData);
      
      let resizeInterval = setInterval(() => map.invalidateSize(), 400);
      setTimeout(() => clearInterval(resizeInterval), 6000);
    }}
    
    window.onload = () => setTimeout(init, 50);
  </script>
</body>
</html>"""


def _filters(database_url: str, log_dir: str, params: dict) -> dict:
    start, end = _window(params)
    db = _query(database_url, """
        SELECT status, COALESCE(rejection_reason, '-') AS rejection_reason, count(*)::int AS count
        FROM transit_candidates
        WHERE created_at >= %s AND created_at <= %s
        GROUP BY status, rejection_reason
        ORDER BY count DESC, status
        LIMIT 80
    """, (start, end))
    events = _parse_log_events(log_dir, start, end, _search(params))
    c = CounterKey()
    for e in events:
        if e.get("event") in {"FILTER_REJECTED", "VISIBILITY_SKIPPED", "GEOMETRY_SKIPPED"}:
            c.add((e.get("event", "-"), e.get("reason", "-")))
    return {"rejections": db, "log_rejections": [{"event": k[0], "reason": k[1], "count": v} for k, v in c.items()]}


def _geometry(log_dir: str, params: dict) -> dict:
    start, end = _window(params)
    events = [e for e in _parse_log_events(log_dir, start, end, _search(params)) if e.get("event") in {"GEOMETRY_SELECTED", "GEOMETRY_NO_ALIGNMENT", "GEOMETRY_SKIPPED"}]
    for e in events:
        e.setdefault("closest_offset_diameters", None)
    events.sort(key=lambda e: _float_or_big(e.get("closest_offset_diameters")))
    summary: dict[str, int] = {}
    for e in events:
        summary[e["event"]] = summary.get(e["event"], 0) + 1
    return {"summary": summary, "items": [_compact_geometry_event(e) for e in events[:300]]}


def _compact_geometry_event(event: dict) -> dict:
    keys = [
        "log_time", "level", "event", "cycle", "aircraft", "callsign", "closest_body",
        "closest_offset_diameters", "closest_separation_deg", "body_elevation_deg",
        "reason", "type", "track_deg", "altitude_ft", "stability",
        "min_aircraft_range_km", "max_aircraft_elevation_deg",
    ]
    return {key: event.get(key) for key in keys if key in event}


def _feeder(log_dir: str, params: dict) -> dict:
    start, end = _window(params)
    events = _parse_log_events(log_dir, start, end, _search(params))
    errors = [e for e in events if e.get("level") in {"ERROR", "WARNING"} and ("ADSB" in e.get("raw", "") or "adsb-feeder" in e.get("raw", "") or "429" in e.get("raw", ""))]
    stats = _fetch_feeder_stats()
    return {
        "stats": stats,
        "log_rate_limits": sum(1 for e in events if "429" in e.get("raw", "")),
        "errors": [{"log_time": e.get("log_time"), "level": e.get("level"), "message": e.get("message")} for e in errors[-120:]],
    }


def _fetch_feeder_stats() -> dict:
    try:
        import urllib.request
        with urllib.request.urlopen("http://adsb-feeder:9988/stats", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def _export(database_url: str, log_dir: str, params: dict) -> list[dict]:
    typ = params.get("type", ["candidates"])[0]
    if typ == "runs":
        return _runs(database_url, params, limit=2000)
    if typ == "aircraft":
        return _aircraft(database_url, params)
    if typ == "geometry":
        return _geometry(log_dir, params)["items"]
    if typ == "validations":
        return _validations(database_url, params, limit=2000)["items"]
    return _candidates(database_url, params, limit=2000)


def _config_items() -> dict[str, str]:
    keys = [
        "USER_LAT", "USER_LON", "ADSBFI_BASE", "SEARCH_RADIUS_NM", "POLL_INTERVAL_SECONDS",
        "PREDICTION_HORIZON_SECONDS", "PREDICTION_STEP_SECONDS", "PREDICTION_USE_HISTORY_FIT",
        "PREDICTION_FIT_WINDOW_SECONDS", "PREDICTION_FIT_MIN_POINTS", "MAX_OBSERVER_RELOCATION_KM",
        "TRAVEL_SPEED_KMH", "REACH_SAFETY", "MIN_LEAD_TIME_SECONDS", "PREFERRED_LEAD_TIME_SECONDS", "MIN_ALTITUDE_FT",
        "SOFT_GOOD_ALTITUDE_FT", "MAX_VERTICAL_RATE_STABLE_FPM", "ALLOW_STABLE_VERTICAL_TREND",
        "MAX_STABLE_VERTICAL_RATE_FPM", "MAX_VERTICAL_RATE_VARIATION_FPM",
        "STABLE_VERTICAL_TREND_MIN_POINTS", "MAX_TRACK_CHANGE_60S_DEG",
        "MAX_GS_CHANGE_60S_KT", "MIN_STABILITY_SCORE_FOR_GEOMETRY",
        "MIN_AIRCRAFT_ELEVATION_DEG_FOR_GEOMETRY", "MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY",
        "ALERT_MIN_SCORE", "MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT", "MIN_BODY_ELEVATION_DEG",
        "MIN_BODY_ELEVATION_DEG_FOR_CANDIDATE", "OBSERVATION_CANDIDATE_MAX_SEPARATION_DEG",
        "OBSERVATION_CANDIDATE_MIN_SCORE", "OBSERVATION_CANDIDATE_MAX_LEAD_SECONDS",
        "NOTIFICATION_REQUIRE_CONVERGENCE", "EARLY_NOTIFICATION_CONSECUTIVE_CYCLES",
        "NOTIFICATION_CONSECUTIVE_CYCLES",
        "NOTIFICATION_MAX_TIME_SHIFT_SECONDS", "NOTIFICATION_MAX_OBSERVER_SHIFT_KM",
        "NOTIFICATION_MAX_OFFSET_WORSENING_DIAMETERS", "STANDBY_BODY_ELEVATION_DEG", "RUN_MODE", "UI_PORT",
    ]
    env = _read_env_file()
    return {key: os.getenv(key, env.get(key, "")) for key in keys}


def _read_env_file() -> dict[str, str]:
    data = {}
    if not os.path.exists(".env"):
        return data
    with open(".env", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _tail_log(log_dir: str, lines: int, query: str = "") -> str:
    path = _latest_log_file(log_dir)
    if not path:
        return "Nie znaleziono pliku logu."
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace")
    rows = text.splitlines()
    if query:
        q = query.lower()
        rows = [line for line in rows if q in line.lower()]
    tail = "\n".join(rows[-lines:])
    return f"# {html.escape(path)}\n{tail}"


def _latest_log_file(log_dir: str) -> str | None:
    if not os.path.isdir(log_dir):
        return None
    files = [os.path.join(log_dir, name) for name in os.listdir(log_dir) if name.startswith("aircraft-transit-") and name.endswith(".log")]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _parse_log_events(log_dir: str, start: datetime, end: datetime, query: str = "") -> list[dict]:
    path = _latest_log_file(log_dir)
    if not path:
        return []
    stat = os.stat(path)
    cached = _LOG_EVENTS_CACHE.get(path)
    if (
        cached is None
        or cached.get("inode") != stat.st_ino
        or cached.get("size", 0) > stat.st_size
        or cached.get("start") > start
    ):
        events = _read_log_events(path, start)
        timestamps = [event["log_time"] for event in events]
        _LOG_EVENTS_CACHE.clear()
        _LOG_EVENTS_CACHE[path] = {
            "inode": stat.st_ino,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "start": start,
            "timestamps": timestamps,
            "events": events,
        }
    else:
        if cached.get("size", 0) < stat.st_size:
            _append_log_events(path, cached, stat)
        timestamps = cached["timestamps"]
        events = cached["events"]
    left = bisect_left(timestamps, start)
    right = bisect_right(timestamps, end)
    q = query.lower()
    return [
        event for event in events[left:right]
        if not q or q in event.get("raw", "").lower()
    ]


def _read_log_events(path: str, start: datetime | None = None) -> list[dict]:
    if start is None:
        with open(path, encoding="utf-8", errors="replace") as fh:
            events = _parse_log_lines(fh)
    else:
        events = _read_log_events_since(path, start)
    events.sort(key=lambda event: event["log_time"])
    return events


def _read_log_events_since(path: str, start: datetime) -> list[dict]:
    block_size = 256 * 1024
    chunks: list[bytes] = []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        while pos > 0:
            read_size = min(block_size, pos)
            pos -= read_size
            fh.seek(pos)
            chunks.insert(0, fh.read(read_size))
            if _first_log_time(chunks) and _first_log_time(chunks) <= start:
                break
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return _parse_log_lines(text.splitlines())


def _first_log_time(chunks: list[bytes]) -> datetime | None:
    text = b"".join(chunks[:1]).decode("utf-8", errors="replace")
    for line in text.splitlines()[:20]:
        try:
            return datetime.strptime(line[:28], "%Y-%m-%d %H:%M:%S.%f%z").astimezone(timezone.utc)
        except ValueError:
            continue
    if len(chunks) <= 1:
        return None
    text = b"".join(chunks).decode("utf-8", errors="replace")
    for line in text.splitlines()[:20]:
        try:
            return datetime.strptime(line[:28], "%Y-%m-%d %H:%M:%S.%f%z").astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _append_log_events(path: str, cached: dict, stat: os.stat_result) -> None:
    with open(path, "rb") as fh:
        fh.seek(cached["size"])
        text = fh.read().decode("utf-8", errors="replace")
    new_events = _parse_log_lines(text.splitlines())
    if not new_events:
        cached["size"] = stat.st_size
        cached["mtime_ns"] = stat.st_mtime_ns
        return
    events = cached["events"]
    timestamps = cached["timestamps"]
    if timestamps and new_events[0]["log_time"] < timestamps[-1]:
        events.extend(new_events)
        events.sort(key=lambda event: event["log_time"])
        cached["timestamps"] = [event["log_time"] for event in events]
    else:
        events.extend(new_events)
        timestamps.extend(event["log_time"] for event in new_events)
    cached["size"] = stat.st_size
    cached["mtime_ns"] = stat.st_mtime_ns


def _parse_log_lines(lines) -> list[dict]:
    events = []
    for line in lines:
        try:
            log_time = datetime.strptime(line[:28], "%Y-%m-%d %H:%M:%S.%f%z").astimezone(timezone.utc)
        except ValueError:
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        level = parts[1] if len(parts) > 1 else ""
        message = parts[2] if len(parts) > 2 else line[28:].strip()
        fields = _fields(message)
        event = fields.get("event") or _event_from_message(message)
        row = {"log_time": log_time, "level": level, "message": message, "raw": line.rstrip("\n"), "event": event}
        row.update(fields)
        events.append(row)
    return events


def _event_from_message(message: str) -> str:
    for event in [
        "ADSB_FETCH", "DB_STORE_OBSERVATIONS", "FILTER_INPUT", "FILTER_REJECTED", "VISIBILITY_SKIPPED",
        "GEOMETRY_SELECTED", "GEOMETRY_NO_ALIGNMENT", "GEOMETRY_SKIPPED", "CANDIDATE_SCORED",
        "DB_STORE_CANDIDATE", "ALERT_SENT", "CYCLE_COMPLETE", "STANDBY",
    ]:
        if event in message:
            return event
    return ""


def _fields(message: str) -> dict:
    result = dict(re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)=([^ |]+)", message))
    step_match = re.search(r"cycle=(\d+) step=([0-9]/5) ([A-Z_]+)", message)
    if step_match:
        result.setdefault("cycle", step_match.group(1))
        result.setdefault("step", step_match.group(2))
        result.setdefault("event", step_match.group(3))
    return result


def _float_or_big(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 999999.0


class CounterKey(dict):
    def add(self, key) -> None:
        self[key] = self.get(key, 0) + 1
