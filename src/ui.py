from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import html
import io
import json
import logging
import math
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from ephemeris import get_body_state, get_body_states
from geo import angular_separation_deg, destination_point, haversine_distance_km, nm_to_km, topocentric_aircraft_position
from observer_solver import observer_search_grid


LOG = logging.getLogger(__name__)
_LOG_EVENTS_CACHE: dict[str, dict] = {}


INDEX_HTML = """<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aircraft Transit Hunter</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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
    nav { display:grid; gap:14px; }
    .nav-section { display:grid; gap:3px; }
    .nav-section-title { margin:0 11px 5px; color:var(--faint); font-size:9px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
    .nav-details summary { margin:0 3px; padding:8px; color:var(--faint); font-size:9px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; cursor:pointer; list-style:none; }
    .nav-details summary::-webkit-details-marker { display:none; }
    .nav-details summary:after { content:'+'; float:right; font-size:13px; line-height:9px; }
    .nav-details[open] summary:after { content:'−'; }
    .nav-details .nav-section { margin-top:3px; }
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
    .outcome { font-size:12px; padding:7px 10px; letter-spacing:.03em; }
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
    .decision { display:flex; align-items:flex-start; gap:14px; padding:18px; margin-bottom:14px; border:1px solid color-mix(in srgb,var(--accent) 34%,var(--line)); border-radius:16px; background:linear-gradient(120deg,rgba(var(--accent-rgb),.14),rgba(45,212,191,.05)); box-shadow:var(--shadow); }
    .decision-icon { display:grid; place-items:center; flex:0 0 38px; width:38px; height:38px; border-radius:12px; color:var(--accent-2); background:rgba(var(--accent-rgb),.15); font-size:20px; }
    .decision h2 { margin:0 0 5px; font-size:16px; }
    .decision p { margin:0; color:var(--muted); line-height:1.55; }
    .event-name { font-weight:780; }
    .reason { color:var(--muted); white-space:normal; min-width:190px; }
    .linkish { padding:0; min-height:0; border:0; background:transparent; color:inherit; font:inherit; font-weight:750; cursor:pointer; }
    .linkish:hover { color:var(--accent); text-decoration:underline; transform:none; box-shadow:none; }
    .threshold { display:inline-block; margin-left:5px; color:var(--faint); font-size:10px; }
    dialog { width:min(1480px,calc(100vw - 32px)); max-height:calc(100vh - 32px); padding:0; color:var(--text); background:var(--bg); border:1px solid var(--line); border-radius:18px; box-shadow:0 28px 90px rgba(0,0,0,.55); overflow:auto; }
    dialog::backdrop { background:rgba(2,6,15,.76); backdrop-filter:blur(7px); }
    .dialog-head { position:sticky; top:0; z-index:1001; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 18px; border-bottom:1px solid var(--line); background:color-mix(in srgb,var(--bg) 88%,transparent); backdrop-filter:blur(18px); }
    .dialog-head h2 { margin:0; font-size:17px; }
    .dialog-body { padding:18px; }
    .event-visuals { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(420px,.85fr); gap:14px; margin-top:14px; }
    .event-map { height:470px; border:1px solid var(--line); border-radius:12px; overflow:hidden; background:var(--surface-2); }
    .sky-chart { width:100%; height:390px; display:block; border:1px solid var(--line); border-radius:12px; background:radial-gradient(circle,rgba(var(--accent-rgb),.09),transparent 55%),var(--surface-2); }
    .chart-legend { display:flex; align-items:center; gap:16px; margin-top:10px; color:var(--muted); font-size:11px; }
    .legend-line { display:inline-block; width:22px; height:3px; margin-right:5px; vertical-align:middle; border-radius:2px; }
    .notice { padding:11px 13px; margin-bottom:12px; color:var(--warn); border:1px solid color-mix(in srgb,var(--warn) 35%,var(--line)); border-radius:11px; background:color-mix(in srgb,var(--warn) 8%,transparent); }
    .score-timeline { width:100%; height:180px; display:block; }
.transit-readout { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px; }
.readout-card { padding:12px; border:1px solid var(--line); border-radius:11px; background:var(--surface-2); }
.readout-card .readout-label { color:var(--muted); font-size:9px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; }
.readout-card .readout-value { margin-top:5px; font-size:15px; font-weight:800; }
.readout-card .readout-description { margin-top:4px; color:var(--muted); font-size:11px; line-height:1.45; }
.life-cycle { padding:14px; border:1px solid var(--line); border-radius:14px; background:var(--surface-2); }
.life-cycle-rail { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }
.life-step { position:relative; padding:11px 11px 10px; border:1px solid var(--line); border-radius:12px; background:var(--surface-solid); min-height:92px; }
.life-step.reached { background:color-mix(in srgb,var(--accent) 8%,var(--surface-solid)); }
.life-step.current { border-color:rgba(var(--accent-rgb),.55); box-shadow:0 0 0 3px rgba(var(--accent-rgb),.08); }
.life-step .step-index { color:var(--faint); font-size:9px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.life-step .step-label { margin-top:6px; font-size:13px; font-weight:820; line-height:1.15; }
.life-step .step-detail { margin-top:6px; color:var(--muted); font-size:10px; line-height:1.45; }
.life-cycle-note { margin-top:10px; color:var(--muted); font-size:11px; line-height:1.5; }
.body-direction-icon { display:flex; flex-direction:column; align-items:center; filter:drop-shadow(0 3px 8px rgba(0,0,0,.6)); }
.body-direction-icon .glyph { display:grid; place-items:center; width:42px; height:42px; border:2px solid white; border-radius:50%; background:#f59e0b; color:#fff7d6; font-size:27px; }
.body-direction-icon.moon .glyph { color:#172033; background:#dbeafe; }
    .body-direction-icon .caption { margin-top:3px; padding:3px 6px; color:white; background:rgba(7,11,20,.9); border-radius:6px; font-size:10px; font-weight:800; white-space:nowrap; }
    .map-key { padding:8px 10px; color:#e5edf9; background:rgba(7,11,20,.88); border:1px solid rgba(255,255,255,.2); border-radius:9px; font-size:10px; line-height:1.55; box-shadow:0 5px 18px rgba(0,0,0,.3); }
    @media(max-width:1280px){.metrics{grid-template-columns:repeat(3,minmax(0,1fr))} input{width:180px}}
    @media(max-width:900px){
      .app{grid-template-columns:1fr} aside{position:sticky;top:0;height:auto;padding:10px 12px;z-index:30;border-right:0;border-bottom:1px solid var(--line)} .brand{padding-bottom:10px;margin-bottom:7px}
      nav{display:flex;overflow:auto;padding-bottom:3px;gap:3px}.nav-label,.nav-section-title,.nav-details>summary,.sidebar-foot{display:none}.nav-section,.nav-details .nav-section{display:flex;margin:0}.nav-details{display:contents}.tab-btn{min-width:max-content;padding:9px 12px}.tab-btn:hover{transform:none}.tab-btn.active{box-shadow:inset 0 -2px var(--accent)}
      header{position:relative;min-height:auto;padding:14px;align-items:stretch;flex-direction:column}.header-left,.header-right{width:100%}.header-right{justify-content:flex-start}input{width:min(100%,260px)}
      .content{padding:14px}.grid-2,.grid-3,.map-layout,.event-visuals{grid-template-columns:minmax(0,1fr)}.map-frame{height:58dvh;min-height:380px}.detail{max-height:none}.event-map{height:390px}.life-cycle-rail{grid-template-columns:repeat(2,minmax(0,1fr))}.panel-head{align-items:flex-start;flex-wrap:wrap}.chart-legend{flex-wrap:wrap}
    }
    @media(max-width:600px){
      body{font-size:12px}.brand{display:none}aside{padding:7px 8px}nav{padding-bottom:1px}.tab-btn{padding:8px 10px}
      header{padding:10px;gap:10px}.title{font-size:18px}.update-badge{display:none}.header-right{gap:6px}#search{width:100%;flex:1 1 100%}#range,#refresh{flex:1 1 calc(50% - 4px);width:auto}.header-right>.primary{flex:1 1 auto}.icon-btn{flex:0 0 38px}
      .content{padding:9px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:13px 12px 12px}.metric .value{font-size:22px}.panel{padding:10px;border-radius:13px}.decision{padding:13px;gap:10px}.decision-icon{flex-basis:34px;width:34px;height:34px}.decision h2{font-size:14px}
      .scroll{overflow:visible;max-height:none!important;border:0;background:transparent}table,tbody,tr,td{display:block;width:100%}thead{display:none}tbody{display:grid;gap:9px}tbody tr{display:block;padding:5px 9px;border:1px solid var(--line);border-radius:11px;background:color-mix(in srgb,var(--surface-solid) 60%,transparent)}td{display:grid;grid-template-columns:minmax(92px,34%) minmax(0,1fr);gap:9px;padding:8px 2px;border-bottom:1px solid var(--line);white-space:normal;overflow-wrap:anywhere;line-height:1.45}td:last-child{border-bottom:0}td:before{content:attr(data-label);color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}td[data-label=""]:before{display:none}td[data-label=""]{display:block;text-align:right}.reason{min-width:0}.scorebar{width:64px}
      dialog{inset:0;width:100vw;height:100dvh;max-width:none;max-height:none;margin:0;border:0;border-radius:0}.dialog-head{padding:11px 12px}.dialog-head h2{font-size:15px}.dialog-body{padding:9px}.event-map{height:330px}.sky-chart{height:300px}.map-frame{height:52dvh;min-height:340px}.life-cycle{padding:10px}.life-cycle-rail{grid-template-columns:1fr;gap:7px}.life-step{min-height:0}.transit-readout{grid-template-columns:1fr}.kv{grid-template-columns:105px minmax(0,1fr)}
    }
    @media(max-width:380px){.metrics{grid-template-columns:1fr}.kv{grid-template-columns:1fr}.kv div:nth-child(even){margin-bottom:5px}td{grid-template-columns:82px minmax(0,1fr)}}
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand"><div class="brand-mark"></div><div class="brand-copy"><h1>Transit Hunter</h1><div class="brand-sub">Prediction intelligence</div></div></div>
    <div class="nav-label">Centrum analizy</div>
    <nav id="nav"></nav>
    <div class="sidebar-foot"><span class="live-dot"></span>System pracuje<br><span class="muted">Predykcja · geometria · walidacja</span></div>
  </aside>
  <main>
    <header>
      <div class="header-left"><div class="view-copy"><div class="eyebrow">Analiza tranzytów</div><div class="title" id="viewTitle">Dzisiaj</div></div><span class="update-badge"><span class="live-dot"></span><span id="updated">ładowanie...</span></span></div>
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
      <section id="radar" class="tab"></section>
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
<dialog id="eventDialog">
  <div class="dialog-head"><h2 id="eventDialogTitle">Analiza zdarzenia</h2><button class="icon-btn" onclick="closeEvent()" title="Zamknij">×</button></div>
  <div class="dialog-body" id="eventDialogBody"><div class="muted">Ładowanie analizy…</div></div>
</dialog>
<script>
const tabGroups = [
  {label:'Codzienna analiza', items:[['overview','Dzisiaj','◈'],['radar','Radar','◌'],['candidates','Zdarzenia','◇'],['alerts','Alerty','◉'],['validations','Wyniki HIT/MISS','◎']]},
  {label:'Analiza szczegółowa', items:[['filters','Lejek i filtry','≋'],['geometry','Geometria','⌁'],['maptab','Mapa','⌖']]},
  {label:'Diagnostyka', collapsible:true, items:[['aircraft','Samoloty ADS-B','✈'],['runs','Cykle','↻'],['feeder','Feeder','⌁'],['logs','Logi','▤'],['config','Konfiguracja','⚙'],['export','Eksport','⇩']]}
];
const tabs = tabGroups.flatMap(group => group.items);
let active = 'overview', timer = null, lastData = {}, refreshInFlight = false, pendingRefresh = false, eventFilter = 'all', funnelFocus = null;
let operationalRange = 'today';
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v ? new Date(v).toLocaleString('pl-PL') : '-';
const num = (v, d=0) => v === null || v === undefined || Number.isNaN(Number(v)) ? '-' : Number(v).toLocaleString('pl-PL', {maximumFractionDigits:d, minimumFractionDigits:d});
const clsStatus = s => s === 'ALERT_SENT' || s === 'ALERT_READY' || s === 'OBSERVATION_CANDIDATE' ? 'good' : s === 'REJECTED' ? 'bad' : 'warn';
const params = () => `range=${encodeURIComponent(range.value)}&q=${encodeURIComponent(search.value.trim())}`;
async function getJson(path) { const r = await fetch(path, {cache:'no-store'}); if (!r.ok) throw new Error(path + ' ' + r.status); return r.json(); }
async function getText(path) { const r = await fetch(path, {cache:'no-store'}); if (!r.ok) throw new Error(path + ' ' + r.status); return r.text(); }
function renderNav() {
  const buttons = items => `<div class="nav-section">${items.map(([id,label,icon]) => `<button class="tab-btn ${id===active?'active':''}" onclick="showTab('${id}')"><span class="nav-icon">${esc(icon)}</span><span>${esc(label)}</span></button>`).join('')}</div>`;
  nav.innerHTML = tabGroups.map(group => group.collapsible
    ? `<details class="nav-details" ${group.items.some(item=>item[0]===active)?'open':''}><summary>${esc(group.label)}</summary>${buttons(group.items)}</details>`
    : `<div><div class="nav-section-title">${esc(group.label)}</div>${buttons(group.items)}</div>`).join('');
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
  const body = rows.length ? rows.map(r => `<tr>${headers.map(h => `<td data-label="${esc(h.name)}" class="${h.cls||''}">${h.fn(r)}</td>`).join('')}</tr>`).join('') : `<tr><td data-label="" colspan="${headers.length}" class="muted">Brak danych</td></tr>`;
  return `<div class="scroll" style="max-height:${opts.h||520}px"><table><thead><tr>${headers.map(h => `<th>${esc(h.name)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function metric(label, value, kind='') { return `<div class="metric ${kind}"><div class="value">${esc(value)}</div><div class="label">${esc(label)}</div></div>`; }
function score(v) { const n = Number(v || 0); return `<span class="scorebar"><span style="width:${Math.max(0, Math.min(100, n*100))}%"></span></span> ${num(n,2)}`; }
function bars(rows, key) {
  const vals = rows.map(r => Number(r[key] || 0)); const max = Math.max(1, ...vals);
  return `<div class="chart">${vals.map(v => `<div class="bar" title="${v}" style="height:${Math.max(2, v/max*110)}px"></div>`).join('')}</div>`;
}
const bodyLabel = value => String(value||'').toLowerCase()==='sun' ? 'Słońce' : String(value||'').toLowerCase()==='moon' ? 'Księżyc' : (value||'-');
const statusLabel = value => ({ALERT_SENT:'Alert wysłany',ALERT_READY:'Gotowy do alertu',OBSERVATION_CANDIDATE:'Obserwowany',REJECTED:'Odrzucony',CANDIDATE_STORED:'Zapisany'}[value]||value||'-');
const reasonLabel = value => ({LOW_SCORE:'Za niski score',TOO_EARLY_FOR_ALERT:'Za wcześnie lub brak potwierdzenia w kolejnym cyklu',TOO_LATE:'Za mało czasu na reakcję',AIRPORT_TRAFFIC:'Ruch lotniskowy',AIRPORT_STRICT:'Ruch lotniskowy',LOW_ALTITUDE:'Za mała wysokość',UNSTABLE_FLIGHT:'Niestabilny tor lotu',FIRST_OBSERVATION:'Pierwszy cykl kwalifikujący — brak potwierdzenia',ONLY_1_CONVERGED_CYCLE:'Tylko 1 stabilny cykl',CYCLE_GAP:'Przerwa między cyklami przerwała potwierdzenie',TRANSIT_TIME_MOVED:'Czas tranzytu przesunął się za mocno',OBSERVER_POINT_MOVED:'Punkt obserwacji przesunął się za mocno',OFFSET_WORSENED:'Offset pogorszył się za mocno',SAME_CYCLE_DUPLICATE:'Duplikat w tym samym cyklu','-':'Brak dodatkowej przyczyny'}[value]||String(value||'-').replaceAll('_',' ').toLowerCase());
const notificationReason = row => row.notification_block_reason ? reasonLabel(row.notification_block_reason) : reasonLabel(row.rejection_reason);
const alertPhaseLabel = value => ({EARLY:'Wczesny',CONFIRMED:'Potwierdzony',LAST_CHANCE:'Ostatni moment',BETTER:'Lepszy punkt',CONSOLE:'Alert'}[String(value||'').toUpperCase()]||value||'Alert');
const durationLabel = value => {if(value===null||value===undefined||Number.isNaN(Number(value)))return '—';const raw=Math.round(Number(value)),sign=raw<0?'-':'',seconds=Math.abs(raw),minutes=Math.floor(seconds/60),rest=seconds%60;return minutes?`${sign}${minutes} min ${rest?rest+' s':''}`.trim():`${sign}${rest} s`;};
function relativeTime(value) { const seconds=Math.max(0,Math.round((Date.now()-new Date(value).getTime())/1000)); return seconds<60?`${seconds} s temu`:seconds<3600?`${Math.round(seconds/60)} min temu`:`${Math.round(seconds/3600)} godz. temu`; }
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
    } else if (requestedActive === 'radar') next.radar = await getJson('/api/radar?' + q);
    else if (requestedActive === 'candidates') next.events = await getJson('/api/events?' + q);
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
    radar: renderRadar, geometry: renderGeometry, filters: renderFilters, alerts: renderAlerts, validations: renderValidations, feeder: renderFeeder, logs: renderLogs,
    config: renderConfig, export: renderExport}[active] || renderOverview)();
}
function renderRadar() {
  const data = lastData.radar || {summary: {}, items: []};
  const s = data.summary || {};
  const headers = [
    {name:'Czas', fn:r=>fmt(r.created_at)},
    {name:'Lot', fn:r=>`${esc(r.callsign || '-')} <span class="muted mono">${esc(r.icao)}</span><br><span class="muted">${bodyLabel(r.body)} · ${fmt(r.transit_time_utc)}</span>`},
    {name:'Radar', fn:r=>`<span class="pill ${r.reachable_now ? 'good' : 'warn'}">${r.reachable_now ? 'osiągalne' : 'poza zasięgiem'}</span><div class="reason">${r.selected_from_home ? 'start z domu' : 'siatka poza domem'}</div>`},
    {name:'Score', fn:r=>`${score(r.score)}<span class="threshold">offset ${num(r.offset_body_diameters,3)}</span>`},
    {name:'Siatka', fn:r=>`${num(r.grid_points_checked)} punktów<br><span class="muted">${r.best_grid_offset_body_diameters==null?'brak':`best ${num(r.best_grid_offset_body_diameters,3)}`}</span>`},
    {name:'Decyzja alertowa', fn:r=>`<span class="pill ${clsStatus(r.alert_status)}">${esc(statusLabel(r.alert_status))}</span><div class="reason">${esc(r.alert_rejection_reason || r.rejection_reason || '-')}</div>`},
    {name:'', fn:r=>r.candidate_id ? `<button onclick="openEvent(${Number(r.candidate_id)})">Analiza alertu</button>` : '<span class="muted">-</span>'}
  ];
  radar.innerHTML = `<div class="metrics">
    ${metric('Zd. RADAR', num(s.events), s.events ? 'good' : '')}
    ${metric('Osiągalne', num(s.reachable), s.reachable ? 'warn' : '')}
    ${metric('Powiązane', num(s.alerted), s.alerted ? 'good' : '')}
    ${metric('Alert sent', num(s.hit), s.hit ? 'good' : '')}
    ${metric('Odrzucone', num(s.miss), s.miss ? 'bad' : '')}
    ${metric('Najlepszy score', s.best_score == null ? '—' : num(s.best_score, 2), Number(s.best_score || 0) >= Number(lastData.overview?.alert_min_score || 0.7) ? 'warn' : '')}
  </div>
  <div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Trend RADAR</h2><span class="muted">liczba zdarzeń geometrycznych w czasie</span></div><span class="muted">okno: ${esc(range.options[range.selectedIndex]?.text || range.value)}</span></div>${bars((s.trend || []), 'count')}</div>
  <div class="grid-2">
    <div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Radar events</h2><span class="muted">osobna warstwa geometryczna, niezależna od alertów</span></div><a href="/api/export?type=radar&${params()}">CSV</a></div>${table(headers, data.items || [], {h:720})}</div>
    <div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Interpretacja RADAR</h2><span class="muted">co widzi geometria, zanim alert przejdzie lejek</span></div></div>
      <div class="kv">
        <div>Eventy</div><div>${num(s.events)}</div>
        <div>Osiągalne</div><div>${num(s.reachable)}</div>
        <div>Wybrane z domu</div><div>${num(s.home_selected)}</div>
        <div>Najlepszy offset siatki</div><div>${s.best_grid_offset == null ? '-' : num(s.best_grid_offset, 3)}</div>
        <div>Alertowano</div><div>${num(s.alerted)}</div>
        <div>Średni score</div><div>${s.avg_score == null ? '-' : num(s.avg_score, 2)}</div>
      </div>
    </div>
  </div>`;
}
function renderOverview() {
  const o = lastData.overview;
  const t=o.totals||{}, events=o.top_events||[], latest=o.latest_run||{};
  const runAge=latest.finished_at ? (Date.now()-new Date(latest.finished_at).getTime())/1000 : null;
  const systemOk=runAge!=null && runAge<180;
  const decision=t.alerts>0
    ? `Wysłano ${num(t.alerts)} ${Number(t.alerts)===1?'alert':'alertów'} w wybranym okresie.`
    : events.length
      ? `Brak alertu. Najlepsze zdarzenie osiągnęło score ${num(events[0].score,2)}, ale nie przeszło wszystkich warunków powiadomienia.`
      : 'Brak alertu, ponieważ żaden lot nie utworzył zdarzenia o odpowiedniej geometrii.';
  const eventHeaders=[
    {name:'Zdarzenie',fn:r=>`<span class="event-name">${esc(r.callsign||r.icao)}</span> <span class="muted mono">${esc(r.icao)}</span><br><span class="muted">${bodyLabel(r.body)} · ${fmt(r.transit_time_utc)}</span>`},
    {name:'Najlepszy score',fn:r=>`${score(r.score)}<span class="threshold">próg ${num(o.alert_min_score,2)}</span>`},
    {name:'Offset',fn:r=>num(r.offset_body_diameters,3)},
    {name:'Cykle',fn:r=>`${num(r.qualifying_cycles)} spełniających / ${num(r.cycle_count)} wszystkich`},
    {name:'Decyzja',fn:r=>`<span class="pill ${r.notification_block_reason?'warn':clsStatus(r.status)}">${r.notification_block_reason?'Czeka na potwierdzenie':statusLabel(r.status)}</span><div class="reason">${notificationReason(r)}</div>`},
    {name:'',fn:r=>`<button onclick="openEvent(${Number(r.id)})">Pełna analiza</button>`}
  ];
  overview.innerHTML = `<div class="decision"><div class="decision-icon">${t.alerts?'✓':'i'}</div><div><h2>${systemOk?'Analiza działa':'Sprawdź aktualność analizy'}</h2><p>${decision} ${systemOk?`Ostatni cykl zakończył się ${relativeTime(latest.finished_at)}.`:'Brak świeżo zakończonego cyklu.'}</p></div></div>
  <div class="metrics">
    ${metric('Stan systemu',systemOk?'AKTYWNY':'NIEAKTUALNY',systemOk?'good':'bad')}
    ${metric('Najlepszy score',events.length?num(events[0].score,2):'—',events.length&&Number(events[0].score)>=Number(o.alert_min_score)?'warn':'')}
    ${metric('Blisko alertu',num(t.near_alert_events),t.near_alert_events?'warn':'')}
    ${metric('Radar eventy',num(t.radar_events),t.radar_events?'good':'')}
    ${metric('Alerty',num(t.alerts),t.alerts?'good':'')}
    ${metric('Cykle z geometrią',num(t.geometry_cycles))}
    ${metric('Przeanalizowane loty',num(t.aircraft_analyzed))}
  </div>
  <div class="grid-2">
    <div class="panel"><div class="panel-head"><h2>Najlepsze zdarzenia</h2><button onclick="showTab('candidates')">wszystkie zdarzenia</button></div>${table(eventHeaders,events,{h:480})}</div>
    <div class="panel"><div class="panel-head"><h2>Gdzie odpadają kandydaci</h2><button onclick="showTab('filters')">pełny lejek</button></div>${table([{name:'Powód',fn:r=>reasonLabel(r.rejection_reason)},{name:'Liczba',fn:r=>num(r.count)}],o.rejection_summary||[],{h:300})}<div class="panel-head" style="margin-top:20px"><h2>Aktywność analizy</h2><span class="muted">kandydaci w czasie</span></div>${bars(o.run_trend||[],'candidate_count')}<div class="panel-head" style="margin-top:20px"><h2>Trend RADAR</h2><span class="muted">radar_events w czasie</span></div>${bars(o.radar_trend||[],'count')}</div>
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
let eventLeafletMap=null;
function closeEvent(){ if(eventLeafletMap){eventLeafletMap.remove();eventLeafletMap=null;} eventDialog.close(); }
async function openEvent(candidateId){
  eventDialogTitle.textContent='Analiza zdarzenia';
  eventDialogBody.innerHTML='<div class="muted">Ładowanie trajektorii, pomiarów ADS-B i położenia tarczy…</div>';
  eventDialog.showModal();
  try { const data=await getJson(`/api/event-detail?candidate_id=${candidateId}`); renderEventDetail(data); }
  catch(error){ eventDialogBody.innerHTML=`<div class="notice">Nie udało się załadować analizy: ${esc(error.message)}</div>`; }
}
function renderEventDetail(data){
  const c=data.candidate||{}, actual=data.actual_result;
  eventDialogTitle.textContent=`${c.callsign||c.icao} · ${bodyLabel(c.body)} · ${fmt(c.transit_time_utc)}`;
  const snapshotNotice=c.snapshot_id?'':`<div class="notice">To zdarzenie powstało przed uruchomieniem zapisu migawek. Tor przewidywany nie jest dostępny; pokazano zachowane pomiary ADS-B.</div>`;
  const actualLabel=actual?actual.result:(new Date(c.transit_time_utc)>new Date()?'OCZEKUJE':'BRAK DANYCH');
  eventDialogBody.innerHTML=`${snapshotNotice}${eventLifecycle(data)}<div class="metrics" style="margin-top:14px">
    ${metric('Score',num(c.score,2),Number(c.score)>=.7?'warn':'')}${metric('Offset prognozy',num(c.offset_body_diameters,3))}
    ${metric('Wynik ADS-B',actualLabel,actual?.result==='HIT'?'good':actual?.result==='MISS'?'bad':'warn')}
    ${metric('Offset rzeczywisty',actual?num(actual.offset_body_diameters,3):'—')}
    ${metric('Odległość obserwatora',num(c.observer_distance_km,2)+' km')}${metric('Cykle zdarzenia',num((data.event_series||[]).length))}
  </div><div class="event-visuals">
    <div class="panel"><div class="panel-head"><h2>Tor lotu i możliwe pola obserwacji</h2><span class="muted">punkty siatki można ukryć w prawym górnym rogu mapy</span></div>${observerGridSummary(data.observer_grid)}<div id="eventGroundMap" class="event-map"></div></div>
    <div class="panel"><div class="panel-head"><h2>Czy samolot przeciął tarczę?</h2><span class="muted">odległość od środka w średnicach tarczy</span></div>${transitReadout(data)}${skyChart(data.predicted_sky||[],data.actual_sky||[],c.body)}<div class="chart-legend"><span><i class="legend-line" style="background:#8b7cff"></i>prognoza</span><span><i class="legend-line" style="background:#22c55e"></i>pomiary ADS-B</span><span>duży okrąg: widoczna tarcza</span></div><div class="muted" style="margin-top:8px;line-height:1.5">Wykres pokazuje widok przez aparat: lewo/prawo i góra/dół. Krawędź tarczy leży 0,5 średnicy od jej środka. Znacznik przy brzegu wykresu oznacza, że samolot minął ją o więcej niż 2 średnice.</div></div>
  </div><div class="grid-2">
    <div class="panel"><div class="panel-head"><h2>Score w kolejnych cyklach</h2><span class="muted">linia przerywana: próg 0,70</span></div>${scoreTimeline(data.event_series||[])}</div>
    <div class="panel"><h2>Składniki najlepszej oceny</h2><div class="kv"><div>Stabilność</div><div>${num(c.stability_score,2)}</div><div>Wyrównanie</div><div>${num(c.alignment_score,2)}</div><div>Wysokość</div><div>${num(c.altitude_score,2)}</div><div>Elewacja obiektu</div><div>${num(c.body_elevation_score,2)}</div><div>Zasięg samolotu</div><div>${num(c.aircraft_range_score,2)}</div><div>Czas na reakcję</div><div>${num(c.lead_time_score,2)}</div><div>Pozycja obserwatora</div><div>${num(c.observer_distance_score,2)}</div><div>Offset w domu</div><div>${c.observer_home_offset_body_diameters==null?'-':num(c.observer_home_offset_body_diameters,3)}</div><div>Najlepszy offset siatki</div><div>${c.observer_best_grid_offset_body_diameters==null?'-':num(c.observer_best_grid_offset_body_diameters,3)}</div><div>Punkty siatki</div><div>${num(c.observer_grid_points_checked||0)}</div><div>Wybrano dom</div><div>${c.observer_selected_from_home?'tak':'nie'}</div><div>Decyzja</div><div>${statusLabel(c.status)} — ${notificationReason(c)}</div></div></div>
  </div>`;
  setTimeout(()=>renderEventGroundMap(data),0);
}
function eventLifecycle(data){
  const c=data.candidate||{}, actual=data.actual_result, series=data.event_series||[];
  const requiredEarly=Number(data.required_early_cycles||2);
  const isRejected=c.status==='REJECTED';
  const observed=series.length>0;
  const alerted=!isRejected && c.status==='ALERT_SENT';
  const blocked=!!c.notification_block_reason && !alerted && (c.status==='ALERT_READY'||c.status==='OBSERVATION_CANDIDATE');
  const confirmed=!isRejected && !blocked && (Number(series.length)>=requiredEarly || c.status==='ALERT_SENT');
  const resolved=!!actual;
  const stopIndex=resolved ? 4 : alerted ? 3 : confirmed ? 2 : observed ? 1 : 0;
  const stopLabel=resolved
    ? `Zatrzymane na etapie wyniku: ${actual.result}.`
    : isRejected
      ? `Zatrzymane wcześniej: ${reasonLabel(c.rejection_reason)}.`
      : blocked
        ? `Gotowe geometrycznie, ale bez alertu: ${notificationReason(c)}.`
      : alerted
        ? 'Alert został wysłany.'
        : confirmed
          ? `Potwierdzone po ${num(series.length)} cyklach.`
          : observed
            ? `Obserwowane, ale nie przeszło do potwierdzenia (${num(series.length)} cykli, wymagane ${num(requiredEarly)}).`
            : 'Zatrzymane na etapie wykrycia.';
  const steps = [
    {index:'01', label:'Wykryty', detail:'Pojawił się pierwszy kandydat dla lotu i obiektu.'},
    {index:'02', label:'Obserwowany', detail:`Seria miała ${num(series.length)} cykli.`},
    {index:'03', label:'Potwierdzony', detail:`Próg stabilności: ${num(requiredEarly)} cykli.`},
    {index:'04', label:'Alert', detail:'Zdarzenie spełniło warunki powiadomienia.'},
    {index:'05', label:'Wynik', detail:'Po tranzycie porównano zapis ADS-B z prognozą.'},
  ];
  return `<div class="life-cycle"><div class="life-cycle-rail">${steps.map((step, index)=>`<div class="life-step ${index<=stopIndex?'reached':''} ${index===stopIndex?'current':''}"><div class="step-index">${step.index}</div><div class="step-label">${step.label}</div><div class="step-detail">${step.detail}</div></div>`).join('')}</div><div class="life-cycle-note">${stopLabel}</div></div>`;
}
function closestTrackPoint(rows){
  if(!rows.length)return null;let best=null;
  const take=(x,y)=>{const offset=Math.hypot(x,y);if(!best||offset<best.offset)best={x,y,offset};};
  rows.forEach(p=>take(Number(p.horizontal),Number(p.vertical)));
  for(let i=0;i<rows.length-1;i++){const x1=Number(rows[i].horizontal),y1=Number(rows[i].vertical),dx=Number(rows[i+1].horizontal)-x1,dy=Number(rows[i+1].vertical)-y1,l2=dx*dx+dy*dy;if(l2<=1e-12)continue;const f=Math.max(0,Math.min(1,-(x1*dx+y1*dy)/l2));take(x1+dx*f,y1+dy*f);}
  return best;
}
function directionDescription(horizontal,vertical){const parts=[];if(Math.abs(vertical)>.05)parts.push(`${num(Math.abs(vertical),2)} ${vertical>0?'nad tarczą':'pod tarczą'}`);if(Math.abs(horizontal)>.05)parts.push(`${num(Math.abs(horizontal),2)} ${horizontal>0?'w prawo':'w lewo'}`);return parts.join(', ')||'przez środek tarczy';}
function transitReadout(data){
  const c=data.candidate||{},actual=data.actual_result,predOffset=Number(c.offset_body_diameters),predHit=predOffset<=.5;
  const predicted=`<div class="readout-card"><div class="readout-label">Prognoza</div><div class="readout-value" style="color:${predHit?'var(--good)':'var(--bad)'}">${predHit?'PRZECIĘCIE TARCZY':'CHYBIENIE'} · ${num(predOffset,3)}</div><div class="readout-description">${predHit?'Przewidywany środek samolotu znalazł się wewnątrz obrysu tarczy.':'Prognozowany tor przebiegał poza tarczą.'}</div></div>`;
  let actualCard='<div class="readout-card"><div class="readout-label">Wynik po ADS-B</div><div class="readout-value">OCZEKUJE</div><div class="readout-description">Wynik będzie dostępny po zdarzeniu.</div></div>';
  if(actual){const hit=actual.result==='HIT',uncertain=actual.result==='UNCERTAIN';actualCard=`<div class="readout-card"><div class="readout-label">Wynik po ADS-B</div><div class="readout-value" style="color:${hit?'var(--good)':uncertain?'var(--warn)':'var(--bad)'}">${hit?'TRAFIONY':uncertain?'NIEPEWNY':'CHYBIONY'} · ${num(actual.offset_body_diameters,3)}</div><div class="readout-description">Najbliżej: ${directionDescription(Number(actual.horizontal_offset_body_diameters),Number(actual.vertical_offset_body_diameters))}.</div></div>`;}
  return `<div class="transit-readout">${predicted}${actualCard}</div>`;
}
function skyChart(predicted,actual,body){
  const extent=2, w=620,h=390,cx=w/2,cy=h/2,scale=Math.min(w,h)/(2*extent), pad=20;
  const path=rows=>rows.length?rows.map((p,i)=>`${i?'L':'M'} ${(cx+Number(p.horizontal)*scale).toFixed(1)} ${(cy-Number(p.vertical)*scale).toFixed(1)}`).join(' '):'';
  const marker=(rows,color,label)=>{const closest=closestTrackPoint(rows);if(!closest)return'';const rawX=cx+closest.x*scale,rawY=cy-closest.y*scale,x=Math.max(pad,Math.min(w-pad,rawX)),y=Math.max(pad,Math.min(h-pad,rawY)),clipped=rawX!==x||rawY!==y;return `<g><circle cx="${x}" cy="${y}" r="7" fill="${color}" stroke="white" stroke-width="2"/><text x="${x+(x>cx?-10:10)}" y="${y-11}" text-anchor="${x>cx?'end':'start'}" fill="${color}" font-size="11" font-weight="800">${label}: ${num(closest.offset,2)}${clipped?' →':''}</text></g>`;};
  return `<svg class="sky-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Symulacja przejścia względem tarczy"><defs><clipPath id="skyClip"><rect width="${w}" height="${h}" rx="12"/></clipPath></defs><g clip-path="url(#skyClip)"><line x1="0" y1="${cy}" x2="${w}" y2="${cy}" stroke="currentColor" opacity=".12"/><line x1="${cx}" y1="0" x2="${cx}" y2="${h}" stroke="currentColor" opacity=".12"/><circle cx="${cx}" cy="${cy}" r="${1*scale}" fill="none" stroke="currentColor" stroke-dasharray="3 5" opacity=".12"/><circle cx="${cx}" cy="${cy}" r="${.5*scale}" fill="${String(body).toLowerCase()==='sun'?'#fbbf24':'#dbeafe'}" fill-opacity=".35" stroke="${String(body).toLowerCase()==='sun'?'#fbbf24':'#bfdbfe'}" stroke-width="3"/><text x="${cx}" y="${cy+4}" text-anchor="middle" fill="currentColor" font-size="12" font-weight="800">${bodyLabel(body)}</text><path d="${path(predicted)}" fill="none" stroke="#8b7cff" stroke-width="3" stroke-linecap="round"/><path d="${path(actual)}" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-dasharray="7 5"/>${marker(predicted,'#8b7cff','Prognoza')}${marker(actual,'#22c55e','ADS-B')}<text x="${cx}" y="18" text-anchor="middle" fill="currentColor" opacity=".55" font-size="11">GÓRA ↑</text><text x="${cx}" y="${h-10}" text-anchor="middle" fill="currentColor" opacity=".55" font-size="11">DÓŁ ↓</text><text x="12" y="${cy}" fill="currentColor" opacity=".55" font-size="11">← LEWO</text><text x="${w-12}" y="${cy}" text-anchor="end" fill="currentColor" opacity=".55" font-size="11">PRAWO →</text></g></svg>`;
}
function scoreTimeline(rows){
  if(!rows.length)return '<div class="muted">Brak historii cykli.</div>';
  const w=720,h=180,p=24, scores=rows.map(r=>Number(r.score)||0), x=i=>p+(rows.length===1?(w-2*p)/2:i*(w-2*p)/(rows.length-1)), y=v=>h-p-v*(h-2*p);
  const points=scores.map((v,i)=>`${x(i)},${y(v)}`).join(' '), threshold=y(.7);
  return `<svg class="score-timeline" viewBox="0 0 ${w} ${h}"><line x1="${p}" y1="${threshold}" x2="${w-p}" y2="${threshold}" stroke="#f59e0b" stroke-dasharray="6 5" opacity=".8"/><polyline points="${points}" fill="none" stroke="#8b7cff" stroke-width="3" stroke-linejoin="round"/><text x="${p}" y="${threshold-6}" fill="#f59e0b" font-size="11">0,70</text><text x="${p}" y="${h-5}" fill="currentColor" opacity=".5" font-size="10">${fmt(rows[0].created_at)}</text><text x="${w-p}" y="${h-5}" text-anchor="end" fill="currentColor" opacity=".5" font-size="10">${fmt(rows[rows.length-1].created_at)}</text></svg>`;
}
function destinationJs(lat,lon,bearing,km){const r=6371,br=bearing*Math.PI/180,p1=lat*Math.PI/180,l1=lon*Math.PI/180,d=km/r,p2=Math.asin(Math.sin(p1)*Math.cos(d)+Math.cos(p1)*Math.sin(d)*Math.cos(br)),l2=l1+Math.atan2(Math.sin(br)*Math.sin(d)*Math.cos(p1),Math.cos(d)-Math.sin(p1)*Math.sin(p2));return [p2*180/Math.PI,l2*180/Math.PI];}
function observerGridSummary(grid){
  grid=grid||{points:[]};const count=(grid.points||[]).length;
  return `<div class="muted" style="margin:-3px 0 11px;line-height:1.5">Algorytm sprawdził ${num(count)} punktów na koncentrycznych pierścieniach co 15°. Zasięg możliwego dojazdu dla tego zdarzenia: <b>${num(grid.max_relocation_km,2)} km</b>. ${grid.has_alignment?'Kolor punktu pokazuje przewidywany offset.':'Dla tego starszego zdarzenia nie zapisano offsetu każdego pola, ale pokazano dokładny układ przeszukiwanej siatki.'}</div>`;
}
function renderEventGroundMap(data){
  if(typeof L==='undefined'){document.getElementById('eventGroundMap').innerHTML='<div class="notice">Biblioteka mapy nie została załadowana.</div>';return;}
  const c=data.candidate,pred=(data.predicted_ground||[]).map(p=>[p.lat,p.lon]),actual=(data.actual_ground||[]).map(p=>[p.lat,p.lon]),observer=[c.observer_lat,c.observer_lon],grid=data.observer_grid||{points:[]};
  eventLeafletMap=L.map('eventGroundMap',{zoomControl:true}).setView(observer,9);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(eventLeafletMap);
  const gridLayer=L.layerGroup().addTo(eventLeafletMap),canvas=L.canvas({padding:.5});
  if(c.home_lat!=null&&c.home_lon!=null&&Number(grid.max_relocation_km)>0)L.circle([c.home_lat,c.home_lon],{radius:Number(grid.max_relocation_km)*1000,color:'#64748b',weight:1,fillColor:'#64748b',fillOpacity:.035,dashArray:'5 6',renderer:canvas}).addTo(gridLayer).bindTooltip(`Maksymalny osiągalny zasięg: ${num(grid.max_relocation_km,2)} km`);
  (grid.points||[]).forEach((point,index)=>{const offset=point.offset_body_diameters,color=point.selected?'#ef4444':point.best?'#a855f7':offset==null?'#64748b':Number(offset)<=.25?'#22c55e':Number(offset)<=.5?'#facc15':Number(offset)<=1?'#38bdf8':'#64748b',radius=point.selected?7:point.best?6:point.home?5:3;L.circleMarker([point.lat,point.lon],{radius,color,weight:point.selected||point.best?2:1,fillColor:color,fillOpacity:(point.selected||point.best)?0.95:0.68,renderer:canvas}).addTo(gridLayer).bindTooltip(`<b>Pole ${index+1}</b>${point.selected?' · WYBRANE':''}${point.best?' · NAJLEPSZE W SIATCE':''}<br>Odległość: ${num(point.distance_km,2)} km${point.bearing_deg==null?'':` · kierunek ${num(point.bearing_deg,0)}°`}<br>Offset: ${offset==null?'brak zapisanej oceny':num(offset,3)+' średnicy'}`);});
  L.control.layers(null,{'Siatka pól obserwacji':gridLayer},{collapsed:false,position:'topright'}).addTo(eventLeafletMap);
  if(pred.length)L.polyline(pred,{color:'#6558f5',weight:4,opacity:.9}).addTo(eventLeafletMap).bindTooltip('Tor przewidywany');
  if(actual.length)L.polyline(actual,{color:'#16a34a',weight:4,opacity:.9,dashArray:'8 6'}).addTo(eventLeafletMap).bindTooltip('Pomiary ADS-B');
  L.circleMarker(observer,{radius:9,color:'#fff',weight:2,fillColor:'#ef4444',fillOpacity:1}).addTo(eventLeafletMap).bindPopup('Punkt obserwacji');
  const bounds=[...pred,...actual,observer];
  if(c.body_azimuth_deg!=null){const target=destinationJs(observer[0],observer[1],Number(c.body_azimuth_deg),20),isMoon=String(c.body).toLowerCase()==='moon',glyph=isMoon?'☾':'☀';L.polyline([observer,target],{color:isMoon?'#bfdbfe':'#f59e0b',weight:3,dashArray:'7 7'}).addTo(eventLeafletMap).bindTooltip(`Kierunek patrzenia: ${bodyLabel(c.body)} · azymut ${num(c.body_azimuth_deg,1)}°`);L.marker(target,{icon:L.divIcon({className:'',html:`<div class="body-direction-icon ${isMoon?'moon':''}"><div class="glyph">${glyph}</div><div class="caption">Kierunek na ${bodyLabel(c.body)}</div></div>`,iconSize:[110,64],iconAnchor:[55,32]})}).addTo(eventLeafletMap).bindPopup(`<b>${bodyLabel(c.body)}</b><br>To znacznik kierunku patrzenia, umieszczony umownie 20 km od obserwatora.<br>Azymut: ${num(c.body_azimuth_deg,1)}° · elewacja: ${num(c.body_elevation_deg,1)}°`);bounds.push(target);}
  const key=L.control({position:'bottomleft'});key.onAdd=()=>{const div=L.DomUtil.create('div','map-key');div.innerHTML='<b>Pola obserwacji</b><br><span style="color:#22c55e">●</span> offset ≤ 0,25 · najlepsze<br><span style="color:#facc15">●</span> przecina tarczę ≤ 0,50<br><span style="color:#38bdf8">●</span> blisko tarczy ≤ 1,00<br><span style="color:#64748b">●</span> poza tarczą / brak danych<br><span style="color:#a855f7">●</span> najlepsze pole siatki<br><span style="color:#ef4444">●</span> wybrany obserwator<br><span style="color:#f59e0b">☀/☾</span> kierunek patrzenia';return div;};key.addTo(eventLeafletMap);
  (grid.points||[]).forEach(point=>bounds.push([point.lat,point.lon]));if(bounds.length>1)eventLeafletMap.fitBounds(bounds,{padding:[45,45],maxZoom:11});
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
const validationLabel = r => ({HIT:'TRAFIONY',MISS:'CHYBIONY',UNCERTAIN:'NIEPEWNY',NO_DATA:'BRAK DANYCH'}[r]||r||'-');
function validationExplanation(row){
  if(row.result==='HIT')return 'Tor przeciął obszar tarczy.';
  if(row.result==='NO_DATA')return 'Za mało pomiarów ADS-B do oceny.';
  if(row.result==='UNCERTAIN')return 'Wynik leży w paśmie niepewności.';
  if(row.result!=='MISS')return '-';
  const vertical=Number(row.vertical_offset_body_diameters),horizontal=Number(row.horizontal_offset_body_diameters);
  if(!Number.isFinite(vertical)||!Number.isFinite(horizontal))return 'Najbliższy punkt pozostał poza tarczą.';
  const av=Math.abs(vertical),ah=Math.abs(horizontal),verticalText=vertical>=0?'powyżej':'poniżej',horizontalText=horizontal>=0?'po prawej':'po lewej';
  if(Math.min(av,ah)>=Math.max(av,ah)*0.7)return `Minięcie ukośne: ${verticalText} i ${horizontalText}.`;
  return av>ah?`Minięcie głównie w pionie: ${verticalText} tarczy.`:`Minięcie głównie w poziomie: ${horizontalText} tarczy.`;
}
const validationHeaders = () => [
  {name:'Wynik',fn:r=>`<span class="pill outcome ${validationClass(r.result)}">${esc(validationLabel(r.result))}</span><div class="reason">${esc(validationExplanation(r))}</div>`},
  {name:'Zdarzenie',fn:r=>`<span class="event-name">${esc(r.callsign||r.icao||'-')}</span> <span class="muted mono">${esc(r.icao)}</span><br><span class="muted">${bodyLabel(r.body)} · ${fmt(r.predicted_transit_time_utc)}</span>${r.alert_type?`<br><span class="pill ${r.alert_type==='EARLY'?'warn':'good'}">${esc(alertPhaseLabel(r.alert_type))}</span>`:''}`},
  {name:'Prognoza → ADS-B',fn:r=>{const delta=r.actual_offset_body_diameters==null?null:Number(r.actual_offset_body_diameters)-Number(r.predicted_offset_body_diameters);return `<b>${num(r.predicted_offset_body_diameters,3)} → ${num(r.actual_offset_body_diameters,3)}</b><br><span class="muted">zmiana ${delta==null?'—':`${delta>=0?'+':''}${num(delta,3)} średnicy`}</span>`;}},
  {name:'Położenie względem tarczy',fn:r=>`${direction(r.vertical_offset_body_diameters,'↑ powyżej','↓ poniżej')}<br>${direction(r.horizontal_offset_body_diameters,'→ po prawej','← po lewej')}`},
  {name:'Czas',fn:r=>`${fmt(r.actual_closest_time_utc)}<br><span class="muted">Δ ${r.time_error_seconds==null?'—':`${Number(r.time_error_seconds)>=0?'+':''}${num(r.time_error_seconds,1)} s`}</span>`},
  {name:'',fn:r=>r.candidate_id?`<button onclick="openEvent(${Number(r.candidate_id)})">Pełna analiza</button>`:'-'}
];
function eventStage(row){
  if(row.validation_result)return {label:`Wynik ${row.validation_result}`,kind:row.validation_result==='HIT'?'good':row.validation_result==='MISS'?'bad':'warn'};
  if(Number(row.alert_count)>0)return {label:'Alert wysłany',kind:'good'};
  if(row.status==='OBSERVATION_CANDIDATE')return {label:'Obserwowany',kind:'warn'};
  if(row.status==='ALERT_READY')return {label:'Czeka na potwierdzenie',kind:'warn'};
  return {label:'Odrzucony',kind:'bad'};
}
function eventFilterMatch(row){if(eventFilter==='near')return Number(row.score)>=Number(lastData.events.alert_min_score);if(eventFilter==='alerted')return Number(row.alert_count)>0;if(eventFilter==='hit')return row.validation_result==='HIT';if(eventFilter==='miss')return row.validation_result==='MISS';return true;}
function funnelMatch(row){
  if(!funnelFocus)return true;
  if(funnelFocus.kind==='status')return String(row.status||'')===String(funnelFocus.value||'');
  if(funnelFocus.kind==='reason')return String(row.rejection_reason||'-')===String(funnelFocus.value||'-');
  if(funnelFocus.kind==='log_event')return String(row.event||'')===String(funnelFocus.value||'');
  if(funnelFocus.kind==='log_reason')return String(row.reason||'-')===String(funnelFocus.value||'-');
  return true;
}
function funnelLabel(){
  if(!funnelFocus)return '';
  if(funnelFocus.kind==='status')return `status: ${statusLabel(funnelFocus.value)}`;
  if(funnelFocus.kind==='reason')return `powód: ${reasonLabel(funnelFocus.value)}`;
  if(funnelFocus.kind==='log_event')return `log: ${funnelFocus.value}`;
  if(funnelFocus.kind==='log_reason')return `log powód: ${reasonLabel(funnelFocus.value)}`;
  return '';
}
function setFunnelFocus(kind, value){
  funnelFocus = {kind, value};
  eventFilter = 'all';
  showTab('candidates');
}
function clearFunnelFocus(){
  funnelFocus = null;
  renderCandidates();
}
function renderCandidates(){
  const data=lastData.events||{items:[],summary:{}},summary=data.summary||{},items=(data.items||[]).filter(eventFilterMatch).filter(funnelMatch);
  const headers=[
    {name:'Zdarzenie',fn:r=>`<span class="event-name">${esc(r.callsign||r.icao)}</span> <span class="muted mono">${esc(r.icao)}</span><br><span class="muted">${bodyLabel(r.body)} · ${fmt(r.transit_time_utc)}</span>`},
    {name:'Wykryte',fn:r=>`${fmt(r.first_seen_at)}<br><span class="muted">ostatni cykl: ${fmt(r.last_seen_at)}</span>`},
    {name:'Najlepszy wynik',fn:r=>`${score(r.score)}<br><span class="muted">offset ${num(r.offset_body_diameters,3)} · obserwator ${num(r.observer_distance_km,2)} km</span>`},
    {name:'Stabilność',fn:r=>`<b>${num(r.qualifying_cycles)}</b> cykli ≥ ${num(data.alert_min_score,2)}<br><span class="muted">${num(r.cycle_count)} cykli zdarzenia · wymagane min. ${num(data.required_early_cycles)}</span>`},
    {name:'Etap',fn:r=>{const stage=eventStage(r);return `<span class="pill ${stage.kind}">${esc(stage.label)}</span><div class="reason">${r.validation_result?`Offset ADS-B: ${num(r.actual_offset_body_diameters,3)}`:notificationReason(r)}</div>`;}},
    {name:'Dane',fn:r=>r.has_snapshot?'<span class="pill good">Pełna migawka</span>':'<span class="muted">bez trajektorii</span>'},
    {name:'',fn:r=>`<button onclick="openEvent(${Number(r.candidate_id)})">Pełna analiza</button>`}
  ];
  candidates.innerHTML=`<div class="metrics">${metric('Zdarzenia',num(summary.events))}${metric('Osiągnęły próg',num(summary.near_alert),summary.near_alert?'warn':'')}${metric('Z alertem',num(summary.alerted),summary.alerted?'good':'')}${metric('HIT',num(summary.hit),summary.hit?'good':'')}${metric('MISS',num(summary.miss),summary.miss?'bad':'')}</div><div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Zdarzenia zamiast pojedynczych cykli</h2><span class="muted">Jeden lot, obiekt i okno tranzytu tworzą jeden rekord.</span>${funnelFocus?`<div style="margin-top:8px"><span class="pill good">Filtr aktywny: ${esc(funnelLabel())}</span> <button onclick="clearFunnelFocus()">Wyczyść filtr</button></div>`:''}</div><div><select onchange="eventFilter=this.value;renderCandidates()"><option value="all" ${eventFilter==='all'?'selected':''}>Wszystkie</option><option value="near" ${eventFilter==='near'?'selected':''}>Osiągnęły próg</option><option value="alerted" ${eventFilter==='alerted'?'selected':''}>Z alertem</option><option value="hit" ${eventFilter==='hit'?'selected':''}>HIT</option><option value="miss" ${eventFilter==='miss'?'selected':''}>MISS</option></select> <a href="/api/export?type=candidates&${params()}">surowe CSV</a></div></div>${table(headers,items,{h:720})}</div>`;
}
function renderRuns(){ runs.innerHTML = `<div class="grid-2"><div class="panel"><h2>Trend pobrań</h2>${bars(lastData.runs.items.slice().reverse(), 'aircraft_count_total')}</div><div class="panel"><h2>Trend analiz</h2>${bars(lastData.runs.items.slice().reverse(), 'aircraft_count_analyzed')}</div></div><div class="panel" style="margin-top:12px"><h2>Cykle predykcji</h2>${table(runHeaders(), lastData.runs.items, {h:720})}</div>`; }
function renderAircraft(){ aircraft.innerHTML = `<div class="panel"><div class="panel-head"><h2>Samoloty z ostatniego zakresu</h2><a href="/api/export?type=aircraft&${params()}">CSV</a></div>${table(aircraftHeaders(), lastData.aircraft.items, {h:760})}</div>`; }
function renderGeometry(){ geometry.innerHTML = `<div class="metrics">${metric('Zdarzenia geometrii', num(lastData.geometry.items.length))}${metric('Selected', num(lastData.geometry.summary.GEOMETRY_SELECTED||0), 'good')}${metric('No alignment', num(lastData.geometry.summary.GEOMETRY_NO_ALIGNMENT||0), 'warn')}${metric('Skipped', num(lastData.geometry.summary.GEOMETRY_SKIPPED||0), 'bad')}</div><div class="panel" style="margin-top:12px"><div class="panel-head"><h2>Geometria z logów</h2><a href="/api/export?type=geometry&${params()}">CSV</a></div>${table(geometryHeaders(), lastData.geometry.items, {h:720})}</div>`; }
function renderFilters(){
  const rejectionHeaders = [
    {name:'Status',fn:r=>`<button class="linkish" onclick="setFunnelFocus('status', ${JSON.stringify(r.status||'')})">${esc(r.status||'-')}</button>`},
    {name:'Powód',fn:r=>`<button class="linkish" onclick="setFunnelFocus('reason', ${JSON.stringify(r.rejection_reason||'-')})">${esc(r.rejection_reason||'-')}</button>`},
    {name:'Liczba',fn:r=>num(r.count)}
  ];
  const logHeaders = [
    {name:'Event',fn:r=>`<button class="linkish" onclick="search.value=${JSON.stringify(r.event||'')};eventFilter='all';funnelFocus=null;showTab('logs');refreshAll();">${esc(r.event||'-')}</button>`},
    {name:'Powód',fn:r=>`<button class="linkish" onclick="search.value=${JSON.stringify(r.reason||'')};eventFilter='all';funnelFocus=null;showTab('logs');refreshAll();">${esc(r.reason||'-')}</button>`},
    {name:'Liczba',fn:r=>num(r.count)}
  ];
  filters.innerHTML = `<div class="grid-2"><div class="panel"><h2>Statusy kandydatów</h2><div class="muted" style="margin:-6px 0 10px;line-height:1.5">Kliknij status lub powód, żeby zawęzić listę zdarzeń.</div>${table(rejectionHeaders, lastData.filters.rejections, {h:520})}</div><div class="panel"><h2>Filtry z logów</h2><div class="muted" style="margin:-6px 0 10px;line-height:1.5">Kliknij log, żeby przejść do odpowiadających mu zdarzeń.</div>${table(logHeaders, lastData.filters.log_rejections, {h:520})}</div></div>`;
}
function renderAlerts(){
  const data=lastData.alerts||{summary:{},items:[]},s=data.summary||{};
  const headers=[
    {name:'Powiadomienie',fn:r=>`<span class="pill ${r.alert_type==='EARLY'?'warn':'good'}">${esc(alertPhaseLabel(r.alert_type))}</span><div class="reason">${fmt(r.printed_at)}</div>`},
    {name:'Zdarzenie',fn:r=>`<span class="event-name">${esc(r.callsign||r.icao||'-')}</span> <span class="muted mono">${esc(r.icao||'-')}</span><br><span class="muted">${bodyLabel(r.body)} · tranzyt ${fmt(r.transit_time_utc)}</span>`},
    {name:'Czas na reakcję',fn:r=>{const margin=Number(r.preparation_margin_seconds),kind=margin>=60?'good':margin>=0?'warn':'bad';return `<b>${durationLabel(r.lead_seconds)}</b> do tranzytu<br><span class="muted">dojazd ok. ${durationLabel(r.travel_seconds)}</span><br><span class="pill ${kind}">po dojeździe ${durationLabel(r.preparation_margin_seconds)}</span>`;}},
    {name:'Prognoza',fn:r=>`${score(r.score)}<br><span class="muted">offset ${num(r.predicted_offset_body_diameters,3)} · ${num(r.observer_distance_km,2)} km</span>`},
    {name:'Wynik',fn:r=>r.validation_result?`<span class="pill ${validationClass(r.validation_result)}">${esc(r.validation_result)}</span><div class="reason">Offset ADS-B ${num(r.actual_offset_body_diameters,3)}<br>Δ czasu ${r.time_error_seconds==null?'—':`${Number(r.time_error_seconds)>=0?'+':''}${num(r.time_error_seconds,1)} s`}</div>`:'<span class="pill warn">Oczekuje</span><div class="reason">Walidacja po tranzycie</div>'},
    {name:'',fn:r=>`${r.google_maps_url?`<a href="${esc(r.google_maps_url)}" target="_blank">Mapa</a> · `:''}<button onclick="openEvent(${Number(r.candidate_id)})">Analiza</button>`}
  ];
  alerts.innerHTML=`<div class="metrics">${metric('Alerty',num(s.alerts),s.alerts?'good':'')}${metric('Zdarzenia',num(s.events))}${metric('Wczesne',num(s.early),s.early?'warn':'')}${metric('Potwierdzone',num(Number(s.confirmed||0)+Number(s.better||0)),s.confirmed||s.better?'good':'')}${metric('HIT',num(s.hit),s.hit?'good':'')}${metric('MISS',num(s.miss),s.miss?'bad':'')}</div><div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Historia powiadomień</h2><span class="muted">Etap, dostępny czas i wynik każdego alertu.</span></div><span class="muted">Średnie wyprzedzenie: ${durationLabel(s.avg_lead_seconds)}</span></div>${table(headers,data.items||[],{h:760})}</div>`;
}
function renderValidations(){
  const d=lastData.validations||{summary:{},items:[]},v=d.summary||{};
  const evaluated=Number(v.hit||0)+Number(v.miss||0), hitRate=evaluated?`${num(Number(v.hit||0)/evaluated*100,1)}%`:'—';
  const decision=evaluated?`Oceniono ${num(evaluated)} ${evaluated===1?'alert':'alertów'}: ${num(v.hit)} HIT i ${num(v.miss)} MISS. Skuteczność wynosi ${hitRate}.`:'Brak zakończonych zdarzeń z wynikiem HIT/MISS w wybranym dniu.';
  validations.innerHTML=`<div class="decision"><div class="decision-icon">${Number(v.miss||0)>0?'!':evaluated?'✓':'i'}</div><div><h2>${evaluated?'Wynik alertów po przelocie':'Brak wyników do oceny'}</h2><p>${decision} Offset ADS-B pokazuje najmniejszą odległość toru od środka tarczy.</p></div></div><div class="metrics">${metric('HIT',num(v.hit),'good')}${metric('MISS',num(v.miss),v.miss?'bad':'')}${metric('Skuteczność',hitRate,evaluated?'good':'')}${metric('Niepewne',num(v.uncertain),v.uncertain?'warn':'')}${metric('Brak danych',num(v.no_data),v.no_data?'warn':'')}${metric('Śr. błąd czasu',v.avg_abs_time_error_seconds==null?'—':num(v.avg_abs_time_error_seconds,1)+' s')}</div><div class="panel" style="margin-top:14px"><div class="panel-head"><div><h2>Wyniki rzeczywiste po tranzycie</h2><span class="muted">Prognoza zestawiona z zapisaną ścieżką ADS-B.</span></div><span class="muted">Przecięcie tarczy: offset około 0,5 średnicy lub mniej.</span></div>${table(validationHeaders(),d.items||[],{h:760})}</div>`;
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
                elif parsed.path == "/api/radar":
                    self._send_json(_radar(database_url, params))
                elif parsed.path == "/api/events":
                    self._send_json(_events(database_url, params))
                elif parsed.path == "/api/event-detail":
                    self._send_json(_event_detail(database_url, _candidate_id(params)))
                elif parsed.path == "/api/aircraft":
                    self._send_json({"items": _aircraft(database_url, params)})
                elif parsed.path == "/api/geometry":
                    self._send_json(_geometry(log_dir, params))
                elif parsed.path == "/api/filters":
                    self._send_json(_filters(database_url, log_dir, params))
                elif parsed.path == "/api/alerts":
                    self._send_json(_alerts(database_url, params))
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


def _candidate_id(params: dict) -> int:
    try:
        return max(1, int(params.get("candidate_id", [""])[0]))
    except (TypeError, ValueError):
        raise ValueError("Missing or invalid candidate_id") from None


def _overview(database_url: str, log_dir: str, params: dict) -> dict:
    start, end = _window(params)
    range_value = params.get("range", ["30m"])[0]
    trend_bucket = "1 hour" if range_value == "today" else "15 minutes" if range_value == "6h" else "1 minute"
    env = _read_env_file()
    alert_min_score = float(os.getenv("ALERT_MIN_SCORE", env.get("ALERT_MIN_SCORE", "0.70")))
    window = max(60, int(os.getenv("LOCKED_ALERT_WINDOW_SECONDS", env.get("LOCKED_ALERT_WINDOW_SECONDS", "600"))))
    totals = _query(database_url, """
        SELECT
          (SELECT count(*) FROM transit_candidates WHERE created_at >= %s AND created_at <= %s)::int AS candidates,
          (SELECT count(*) FROM radar_events WHERE created_at >= %s AND created_at <= %s)::int AS radar_events,
          (SELECT count(*) FROM alerts WHERE printed_at >= %s AND printed_at <= %s)::int AS alerts,
          COALESCE((SELECT sum(aircraft_count_analyzed) FROM prediction_runs WHERE started_at >= %s AND started_at <= %s), 0)::int AS aircraft_analyzed,
          (SELECT count(DISTINCT prediction_run_id)::int FROM transit_candidates WHERE created_at >= %s AND created_at <= %s) AS geometry_cycles,
          (SELECT count(*)::int FROM (
             SELECT lower(icao), lower(body)
             FROM transit_candidates
             WHERE created_at >= %s AND created_at <= %s AND score >= %s
             GROUP BY lower(icao), lower(body)
          ) near_alerts) AS near_alert_events
    """, (start, end, start, end, start, end, start, end, start, end, start, end, alert_min_score))[0]
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
    radar_trend = _query(database_url, """
        SELECT date_bin(%s::interval, created_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,
               count(*)::int AS count
        FROM radar_events
        WHERE created_at >= %s AND created_at <= %s
        GROUP BY bucket
        ORDER BY bucket
    """, (trend_bucket, start, end))
    validation_data = _validations(database_url, params, limit=12)
    top_events = _query(database_url, """
        WITH event_stats AS (
          SELECT lower(icao) AS event_icao, lower(body) AS event_body,
                 count(DISTINCT prediction_run_id)::int AS cycle_count,
                 count(DISTINCT prediction_run_id) FILTER (WHERE score >= %s)::int AS qualifying_cycles
          FROM transit_candidates
          WHERE created_at >= %s AND created_at <= %s
          GROUP BY lower(icao), lower(body)
        ), ranked AS (
          SELECT c.*,
                 row_number() OVER (
                   PARTITION BY lower(c.icao), lower(c.body)
                   ORDER BY c.score DESC, c.created_at DESC
                 ) AS rank
          FROM transit_candidates c
          WHERE c.created_at >= %s AND c.created_at <= %s
        )
        SELECT r.id, r.icao, r.callsign, r.body, r.transit_time_utc, r.created_at,
               floor(extract(epoch FROM r.transit_time_utc) / %s)::bigint AS event_slot,
               r.score, r.offset_body_diameters, r.status, r.rejection_reason,
               r.observer_distance_km, stats.cycle_count, stats.qualifying_cycles
        FROM ranked r
        JOIN event_stats stats
          ON stats.event_icao=lower(r.icao) AND stats.event_body=lower(r.body)
        WHERE r.rank=1
        ORDER BY r.score DESC, r.created_at DESC
        LIMIT 8
    """, (alert_min_score, start, end, start, end, window))
    for item in top_events:
        if item.get("status") not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
            continue
        series = _query(database_url, """
            WITH ranked AS (
              SELECT created_at, transit_time_utc, score, offset_body_diameters,
                     observer_lat, observer_lon, status, rejection_reason,
                     row_number() OVER (
                       PARTITION BY prediction_run_id
                       ORDER BY score DESC, offset_body_diameters ASC
                     ) AS rank
              FROM transit_candidates
              WHERE lower(icao)=lower(%s) AND lower(body)=lower(%s)
                AND floor(extract(epoch FROM transit_time_utc) / %s)=%s
            )
            SELECT created_at, transit_time_utc, score, offset_body_diameters,
                   observer_lat, observer_lon, status, rejection_reason
            FROM ranked WHERE rank=1
            ORDER BY created_at
        """, (item["icao"], item["body"], window, item["event_slot"]))
        item.update(_notification_block_analysis(item, series))
    rejection_summary = _query(database_url, """
        SELECT COALESCE(rejection_reason, '-') AS rejection_reason, count(*)::int AS count
        FROM transit_candidates
        WHERE created_at >= %s AND created_at <= %s
        GROUP BY rejection_reason
        ORDER BY count DESC
        LIMIT 6
    """, (start, end))
    latest_run = _query(database_url, """
        SELECT started_at, finished_at, aircraft_count_total, aircraft_count_analyzed,
               candidate_count, alert_count
        FROM prediction_runs
        ORDER BY started_at DESC
        LIMIT 1
    """)
    return {
        "totals": totals,
        "run_trend": run_trend,
        "radar_trend": radar_trend,
        "top_events": top_events,
        "rejection_summary": rejection_summary,
        "latest_run": latest_run[0] if latest_run else None,
        "alert_min_score": alert_min_score,
        "validation_summary": validation_data["summary"],
        "latest_validations": validation_data["items"],
        "log_summary": {
            "errors": 0,
            "warnings": 0,
            "rate_limits": 0,
        },
    }


def _notification_limits() -> dict:
    env = _read_env_file()
    return {
        "alert_min_score": float(os.getenv("ALERT_MIN_SCORE", env.get("ALERT_MIN_SCORE", "0.70"))),
        "max_offset": float(os.getenv("MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT", env.get("MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT", "0.25"))),
        "required_early": int(os.getenv("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", env.get("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", "2"))),
        "required_confirmed": int(os.getenv("NOTIFICATION_CONSECUTIVE_CYCLES", env.get("NOTIFICATION_CONSECUTIVE_CYCLES", "3"))),
        "max_time_shift": float(os.getenv("NOTIFICATION_MAX_TIME_SHIFT_SECONDS", env.get("NOTIFICATION_MAX_TIME_SHIFT_SECONDS", "5"))),
        "max_observer_shift": float(os.getenv("NOTIFICATION_MAX_OBSERVER_SHIFT_KM", env.get("NOTIFICATION_MAX_OBSERVER_SHIFT_KM", "0.5"))),
        "max_offset_worsening": float(os.getenv("NOTIFICATION_MAX_OFFSET_WORSENING_DIAMETERS", env.get("NOTIFICATION_MAX_OFFSET_WORSENING_DIAMETERS", "0.05"))),
    }


def _notification_block_analysis(candidate: dict, series: list[dict]) -> dict:
    if candidate.get("status") not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
        return {}

    limits = _notification_limits()
    early_quality = (
        float(candidate.get("score") or 0) >= limits["alert_min_score"]
        and float(candidate.get("offset_body_diameters") or 999) <= limits["max_offset"]
    )
    required = limits["required_early"] if early_quality else limits["required_confirmed"]
    candidate_created_at = candidate.get("created_at")
    consecutive = 0
    reason = "FIRST_OBSERVATION"
    previous = None

    for row in sorted(series, key=lambda item: item.get("created_at") or datetime.min.replace(tzinfo=timezone.utc)):
        if row.get("status") not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
            continue
        if candidate_created_at and row.get("created_at") and row["created_at"] > candidate_created_at:
            continue
        if previous is None:
            consecutive = 1
            reason = "FIRST_OBSERVATION"
        else:
            time_shift = abs((row["transit_time_utc"] - previous["transit_time_utc"]).total_seconds())
            observer_shift = haversine_distance_km(
                float(previous["observer_lat"]),
                float(previous["observer_lon"]),
                float(row["observer_lat"]),
                float(row["observer_lon"]),
            )
            offset_worsening = float(row["offset_body_diameters"]) - float(previous["offset_body_diameters"])
            if time_shift > limits["max_time_shift"]:
                consecutive = 1
                reason = "TRANSIT_TIME_MOVED"
            elif observer_shift > limits["max_observer_shift"]:
                consecutive = 1
                reason = "OBSERVER_POINT_MOVED"
            elif offset_worsening > limits["max_offset_worsening"]:
                consecutive = 1
                reason = "OFFSET_WORSENED"
            else:
                consecutive += 1
                reason = "CONVERGED"
        previous = row

    if consecutive >= required:
        return {
            "notification_consecutive_cycles": consecutive,
            "notification_required_cycles": required,
            "notification_block_reason": None,
        }

    block_reason = reason
    if reason in {"FIRST_OBSERVATION", "CONVERGED"}:
        block_reason = f"ONLY_{max(0, consecutive)}_CONVERGED_CYCLE"
    return {
        "notification_consecutive_cycles": consecutive,
        "notification_required_cycles": required,
        "notification_block_reason": block_reason,
    }


def _event_detail(database_url: str, candidate_id: int) -> dict:
    rows = _query(database_url, """
        SELECT c.id, c.prediction_run_id, c.icao, c.callsign, c.aircraft_type, c.body,
               c.transit_time_utc, c.created_at, c.observer_lat, c.observer_lon,
               c.observer_distance_km, c.score, c.confidence, c.offset_body_diameters,
               c.angular_separation_deg, c.body_radius_deg, c.body_azimuth_deg,
               c.body_elevation_deg, c.status, c.rejection_reason,
               c.stability_score, c.alignment_score, c.altitude_score,
               c.body_elevation_score, c.aircraft_range_score, c.lead_time_score,
               c.observer_distance_score,
               c.observer_home_offset_body_diameters,
               c.observer_best_grid_offset_body_diameters,
               c.observer_grid_points_checked,
               c.observer_selected_from_home,
               snapshot.id AS snapshot_id, snapshot.source_observed_at,
               snapshot.path_start_utc, snapshot.path_end_utc,
               snapshot.sample_interval_seconds, snapshot.point_count, snapshot.points,
               run.user_lat AS home_lat, run.user_lon AS home_lon
        FROM transit_candidates c
        LEFT JOIN event_trajectory_snapshots snapshot ON snapshot.candidate_id=c.id
        LEFT JOIN prediction_runs run ON run.id=c.prediction_run_id
        WHERE c.id=%s
    """, (candidate_id,))
    if not rows:
        raise ValueError(f"Candidate {candidate_id} not found")
    candidate = rows[0]
    env = _read_env_file()
    window = max(60, int(os.getenv("LOCKED_ALERT_WINDOW_SECONDS", env.get("LOCKED_ALERT_WINDOW_SECONDS", "600"))))
    required_early_cycles = int(os.getenv("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", env.get("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", "2")))
    event_slot = int(candidate["transit_time_utc"].timestamp()) // window
    series = _query(database_url, """
        WITH ranked AS (
          SELECT created_at, transit_time_utc, score, offset_body_diameters,
                 observer_lat, observer_lon, status, rejection_reason,
                 row_number() OVER (
                   PARTITION BY prediction_run_id
                   ORDER BY score DESC, offset_body_diameters ASC
                 ) AS rank
          FROM transit_candidates
          WHERE lower(icao)=lower(%s) AND lower(body)=lower(%s)
            AND floor(extract(epoch FROM transit_time_utc) / %s)=%s
        )
        SELECT created_at, transit_time_utc, score, offset_body_diameters,
               observer_lat, observer_lon, status, rejection_reason
        FROM ranked WHERE rank=1
        ORDER BY created_at
    """, (candidate["icao"], candidate["body"], window, event_slot))

    source_time = candidate.get("source_observed_at") or candidate["created_at"]
    transit_time = candidate["transit_time_utc"]
    observation_start = min(source_time - timedelta(seconds=60), transit_time - timedelta(seconds=300))
    observation_end = transit_time + timedelta(seconds=300)
    observations = _query(database_url, """
        SELECT observed_at, lat, lon, altitude_ft, ground_speed_kt, track_deg
        FROM aircraft_observations
        WHERE lower(icao)=lower(%s) AND observed_at BETWEEN %s AND %s
        ORDER BY observed_at
    """, (candidate["icao"], observation_start, observation_end))

    payload = candidate.get("points") or {"version": 1, "points": []}
    predicted_points = [
        {
            "timestamp": datetime.fromtimestamp(float(point[0]), tz=timezone.utc),
            "lat": float(point[1]),
            "lon": float(point[2]),
            "altitude_ft": float(point[3]) if point[3] is not None else None,
        }
        for point in payload.get("points", [])
        if len(point) >= 4
    ]
    predicted_sky = _sky_track(
        predicted_points,
        candidate["body"],
        candidate["observer_lat"],
        candidate["observer_lon"],
        transit_time,
        window_seconds=180,
    )
    actual_sky = _sky_track(
        observations,
        candidate["body"],
        candidate["observer_lat"],
        candidate["observer_lon"],
        transit_time,
        window_seconds=180,
    )
    actual_result = None
    if datetime.now(timezone.utc) >= transit_time + timedelta(seconds=60) and len(observations) >= 2:
        actual_result = _closest_sky_result(actual_sky)
    observer_grid = _event_observer_grid(candidate, predicted_points)
    candidate.update(_notification_block_analysis(candidate, series))
    candidate.pop("points", None)
    return {
        "candidate": candidate,
        "event_series": series,
        "predicted_ground": predicted_points,
        "actual_ground": observations,
        "predicted_sky": predicted_sky,
        "actual_sky": actual_sky,
        "actual_result": actual_result,
        "observer_grid": observer_grid,
        "required_early_cycles": required_early_cycles,
    }


def _event_observer_grid(candidate: dict, predicted_points: list[dict]) -> dict:
    home_lat = candidate.get("home_lat")
    home_lon = candidate.get("home_lon")
    if home_lat is None or home_lon is None:
        return {"points": [], "max_relocation_km": 0.0, "has_alignment": False}
    env = _read_env_file()
    configured_max = float(os.getenv("MAX_OBSERVER_RELOCATION_KM", env.get("MAX_OBSERVER_RELOCATION_KM", "12")))
    travel_speed = float(os.getenv("TRAVEL_SPEED_KMH", env.get("TRAVEL_SPEED_KMH", "50")))
    reach_safety = float(os.getenv("REACH_SAFETY", env.get("REACH_SAFETY", "0.8")))
    source_time = candidate.get("source_observed_at") or candidate["created_at"]
    lead_seconds = max(0.0, (candidate["transit_time_utc"] - source_time).total_seconds())
    reachable_km = min(configured_max, travel_speed * lead_seconds / 3600.0 * reach_safety)
    raw_grid = observer_search_grid(float(home_lat), float(home_lon), reachable_km)

    aircraft_point = None
    body = None
    if predicted_points:
        aircraft_point = min(
            predicted_points,
            key=lambda point: abs((point["timestamp"] - candidate["transit_time_utc"]).total_seconds()),
        )
        body = get_body_state(
            float(home_lat),
            float(home_lon),
            aircraft_point["timestamp"],
            candidate["body"],
        )
    points = []
    best_index = None
    best_offset = None
    for index, (lat, lon, radius, bearing) in enumerate(raw_grid):
        offset = None
        if aircraft_point is not None and body is not None and aircraft_point.get("altitude_ft") is not None:
            aircraft_azimuth, aircraft_elevation, _ = topocentric_aircraft_position(
                lat,
                lon,
                aircraft_point["lat"],
                aircraft_point["lon"],
                aircraft_point["altitude_ft"],
            )
            separation = angular_separation_deg(
                aircraft_azimuth,
                aircraft_elevation,
                body.azimuth_deg,
                body.elevation_deg,
            )
            offset = separation / max(1e-9, body.angular_radius_deg * 2)
            if best_offset is None or offset < best_offset:
                best_offset = offset
                best_index = index
        points.append({
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "distance_km": round(radius, 2),
            "bearing_deg": bearing,
            "offset_body_diameters": round(offset, 4) if offset is not None else None,
            "selected": haversine_distance_km(lat, lon, candidate["observer_lat"], candidate["observer_lon"]) <= 0.08,
            "home": index == 0,
            "best": False,
        })
    if best_index is not None:
        points[best_index]["best"] = True
    return {
        "points": points,
        "max_relocation_km": reachable_km,
        "configured_max_relocation_km": configured_max,
        "lead_time_seconds": lead_seconds,
        "has_alignment": aircraft_point is not None and body is not None,
    }


def _sky_track(
    points: list[dict],
    body_name: str,
    observer_lat: float,
    observer_lon: float,
    transit_time: datetime,
    *,
    window_seconds: int,
) -> list[dict]:
    eligible = []
    for point in points:
        timestamp = point.get("timestamp") or point.get("observed_at")
        if timestamp is not None and abs((timestamp - transit_time).total_seconds()) <= window_seconds and point.get("altitude_ft") is not None:
            eligible.append((point, timestamp))
    if not eligible:
        return []
    start_time = eligible[0][1]
    end_time = eligible[-1][1]
    start_body = get_body_state(observer_lat, observer_lon, start_time, body_name)
    end_body = get_body_state(observer_lat, observer_lon, end_time, body_name)
    if start_body is None or end_body is None:
        return []
    total_seconds = max(1e-9, (end_time - start_time).total_seconds())
    result = []
    for point, timestamp in eligible:
        altitude_ft = point.get("altitude_ft")
        fraction = max(0.0, min(1.0, (timestamp - start_time).total_seconds() / total_seconds))
        azimuth_delta_body = (end_body.azimuth_deg - start_body.azimuth_deg + 180.0) % 360.0 - 180.0
        body_azimuth = (start_body.azimuth_deg + azimuth_delta_body * fraction) % 360.0
        body_elevation = start_body.elevation_deg + (end_body.elevation_deg - start_body.elevation_deg) * fraction
        body_radius = start_body.angular_radius_deg + (end_body.angular_radius_deg - start_body.angular_radius_deg) * fraction
        aircraft_azimuth, aircraft_elevation, _ = topocentric_aircraft_position(
            observer_lat,
            observer_lon,
            point["lat"],
            point["lon"],
            altitude_ft,
        )
        diameter = max(1e-9, body_radius * 2)
        azimuth_delta = (aircraft_azimuth - body_azimuth + 180.0) % 360.0 - 180.0
        result.append({
            "timestamp": timestamp,
            "horizontal": azimuth_delta * math.cos(math.radians(body_elevation)) / diameter,
            "vertical": (aircraft_elevation - body_elevation) / diameter,
        })
    return result


def _closest_sky_result(points: list[dict], uncertainty_diameters: float = 0.10) -> dict | None:
    if not points:
        return None
    best: tuple[float, datetime, float, float] | None = None
    for point in points:
        horizontal = float(point["horizontal"])
        vertical = float(point["vertical"])
        offset = math.hypot(horizontal, vertical)
        if best is None or offset < best[0]:
            best = (offset, point["timestamp"], horizontal, vertical)
    for before, after in zip(points, points[1:]):
        x1, y1 = float(before["horizontal"]), float(before["vertical"])
        dx = float(after["horizontal"]) - x1
        dy = float(after["vertical"]) - y1
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-12:
            continue
        fraction = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_squared))
        horizontal = x1 + dx * fraction
        vertical = y1 + dy * fraction
        offset = math.hypot(horizontal, vertical)
        if best is None or offset < best[0]:
            span = (after["timestamp"] - before["timestamp"]).total_seconds()
            timestamp = before["timestamp"] + timedelta(seconds=span * fraction)
            best = (offset, timestamp, horizontal, vertical)
    if best is None:
        return None
    offset, timestamp, horizontal, vertical = best
    uncertainty = max(0.0, min(0.49, uncertainty_diameters))
    result = "HIT" if offset <= 0.5 - uncertainty else "MISS" if offset >= 0.5 + uncertainty else "UNCERTAIN"
    return {
        "result": result,
        "closest_time_utc": timestamp,
        "offset_body_diameters": offset,
        "vertical_offset_body_diameters": vertical,
        "horizontal_offset_body_diameters": horizontal,
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
               aircraft_altitude_ft, aircraft_range_km, aircraft_track_deg, body_azimuth_deg, body_elevation_deg,
               observer_home_offset_body_diameters, observer_best_grid_offset_body_diameters,
               observer_grid_points_checked, observer_selected_from_home
        FROM transit_candidates
        WHERE created_at >= %s AND created_at <= %s {where_q}
        ORDER BY created_at DESC
        LIMIT %s
    """, tuple(args))


def _radar(database_url: str, params: dict, limit: int = 200) -> dict:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(icao) LIKE %s OR lower(COALESCE(callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    args.append(limit)
    items = _query(database_url, f"""
        SELECT id, created_at, transit_time_utc, icao, callsign, aircraft_type, body,
               score, confidence, offset_body_diameters, observer_distance_km,
               reachable_now, home_offset_body_diameters, best_grid_offset_body_diameters,
               grid_points_checked, selected_from_home, alert_status, alert_rejection_reason,
               transit_candidate_id, observer_lat, observer_lon,
               aircraft_altitude_ft, aircraft_range_km, aircraft_track_deg,
               aircraft_ground_speed_kt, aircraft_vertical_rate_fpm,
               body_azimuth_deg, body_elevation_deg
        FROM radar_events
        WHERE created_at >= %s AND created_at <= %s {where_q}
        ORDER BY created_at DESC
        LIMIT %s
    """, tuple(args))
    avg_score = sum(float(item.get("score") or 0.0) for item in items) / len(items) if items else None
    summary = {
        "events": len(items),
        "reachable": sum(1 for item in items if item.get("reachable_now")),
        "home_selected": sum(1 for item in items if item.get("selected_from_home")),
        "alerted": sum(1 for item in items if item.get("transit_candidate_id")),
        "hit": sum(1 for item in items if item.get("alert_status") == "ALERT_SENT"),
        "miss": sum(1 for item in items if item.get("alert_status") == "REJECTED"),
        "best_score": max((float(item.get("score") or 0.0) for item in items), default=None),
        "avg_score": avg_score,
        "best_grid_offset": min(
            (float(item["best_grid_offset_body_diameters"]) for item in items if item.get("best_grid_offset_body_diameters") is not None),
            default=None,
        ),
    }
    return {"items": items, "summary": summary}


def _events(database_url: str, params: dict, limit: int = 300) -> dict:
    start, end = _window(params)
    q = _search(params)
    env = _read_env_file()
    window = max(60, int(os.getenv("LOCKED_ALERT_WINDOW_SECONDS", env.get("LOCKED_ALERT_WINDOW_SECONDS", "600"))))
    alert_min_score = float(os.getenv("ALERT_MIN_SCORE", env.get("ALERT_MIN_SCORE", "0.70")))
    required_early_cycles = int(os.getenv("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", env.get("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", "2")))
    where_q = "AND (lower(c.icao) LIKE %s OR lower(COALESCE(c.callsign,'')) LIKE %s)" if q else ""
    args: list = [window, start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    args.extend([alert_min_score, window, limit])
    items = _query(database_url, f"""
        WITH source AS (
          SELECT c.*,
                 floor(extract(epoch FROM c.transit_time_utc) / %s)::bigint AS event_slot
          FROM transit_candidates c
          WHERE c.created_at >= %s AND c.created_at <= %s {where_q}
        ), stats AS (
          SELECT lower(icao) AS normalized_icao, lower(body) AS normalized_body, event_slot,
                 min(created_at) AS first_seen_at, max(created_at) AS last_seen_at,
                 count(DISTINCT prediction_run_id)::int AS cycle_count,
                 count(DISTINCT prediction_run_id) FILTER (WHERE score >= %s)::int AS qualifying_cycles,
                 max(score) AS best_score
          FROM source
          GROUP BY lower(icao), lower(body), event_slot
        ), ranked AS (
          SELECT source.*,
                 row_number() OVER (
                   PARTITION BY lower(icao), lower(body), event_slot
                   ORDER BY score DESC, offset_body_diameters ASC, created_at DESC
                 ) AS rank
          FROM source
        ), alert_stats AS (
          SELECT lower(c.icao) AS normalized_icao, lower(c.body) AS normalized_body,
                 floor(extract(epoch FROM c.transit_time_utc) / %s)::bigint AS event_slot,
                 count(*)::int AS alert_count,
                 string_agg(DISTINCT a.alert_type, ', ' ORDER BY a.alert_type) AS alert_types
          FROM alerts a
          JOIN transit_candidates c ON c.id=a.transit_candidate_id
          GROUP BY normalized_icao, normalized_body, event_slot
        )
        SELECT ranked.id AS candidate_id, ranked.icao, ranked.callsign, ranked.aircraft_type,
               ranked.body, ranked.event_slot, ranked.transit_time_utc, ranked.created_at,
               stats.first_seen_at, stats.last_seen_at, stats.cycle_count,
               stats.qualifying_cycles, stats.best_score AS score,
               ranked.offset_body_diameters, ranked.observer_distance_km,
               ranked.status, ranked.rejection_reason,
               COALESCE(alert_stats.alert_count, 0)::int AS alert_count,
               alert_stats.alert_types,
               validation.result AS validation_result,
               validation.actual_offset_body_diameters,
               EXISTS (
                 SELECT 1 FROM event_trajectory_snapshots snapshot
                 WHERE snapshot.candidate_id=ranked.id
               ) AS has_snapshot
        FROM ranked
        JOIN stats ON stats.normalized_icao=lower(ranked.icao)
                  AND stats.normalized_body=lower(ranked.body)
                  AND stats.event_slot=ranked.event_slot
        LEFT JOIN alert_stats ON alert_stats.normalized_icao=lower(ranked.icao)
                             AND alert_stats.normalized_body=lower(ranked.body)
                             AND alert_stats.event_slot=ranked.event_slot
        LEFT JOIN transit_validations validation
               ON validation.icao=lower(ranked.icao)
              AND validation.body=lower(ranked.body)
              AND validation.event_slot=ranked.event_slot
        WHERE ranked.rank=1
        ORDER BY stats.best_score DESC, stats.last_seen_at DESC
        LIMIT %s
    """, tuple(args))
    for item in items:
        if item.get("alert_count", 0) > 0 or item.get("status") not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
            continue
        series = _query(database_url, """
            WITH ranked AS (
              SELECT created_at, transit_time_utc, score, offset_body_diameters,
                     observer_lat, observer_lon, status, rejection_reason,
                     row_number() OVER (
                       PARTITION BY prediction_run_id
                       ORDER BY score DESC, offset_body_diameters ASC
                     ) AS rank
              FROM transit_candidates
              WHERE lower(icao)=lower(%s) AND lower(body)=lower(%s)
                AND floor(extract(epoch FROM transit_time_utc) / %s)=%s
            )
            SELECT created_at, transit_time_utc, score, offset_body_diameters,
                   observer_lat, observer_lon, status, rejection_reason
            FROM ranked WHERE rank=1
            ORDER BY created_at
        """, (item["icao"], item["body"], window, item["event_slot"]))
        item.update(_notification_block_analysis(item, series))
    summary = {
        "events": len(items),
        "near_alert": sum(1 for item in items if float(item["score"] or 0) >= alert_min_score),
        "alerted": sum(1 for item in items if item["alert_count"] > 0),
        "hit": sum(1 for item in items if item["validation_result"] == "HIT"),
        "miss": sum(1 for item in items if item["validation_result"] == "MISS"),
    }
    return {
        "items": items,
        "summary": summary,
        "alert_min_score": alert_min_score,
        "required_early_cycles": required_early_cycles,
        "event_window_seconds": window,
    }


def _alerts(database_url: str, params: dict) -> dict:
    start, end = _window(params)
    q = _search(params)
    env = _read_env_file()
    event_window = max(60, int(os.getenv("LOCKED_ALERT_WINDOW_SECONDS", env.get("LOCKED_ALERT_WINDOW_SECONDS", "600"))))
    where_q = "AND (lower(c.icao) LIKE %s OR lower(COALESCE(c.callsign,'')) LIKE %s)" if q else ""
    args: list = [event_window, event_window, start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    items = _query(database_url, f"""
        SELECT a.id AS alert_id, a.alert_type, a.printed_at, a.message,
               c.id AS candidate_id, c.icao, c.callsign, c.body, c.score,
               c.transit_time_utc,
               extract(epoch FROM c.transit_time_utc - a.printed_at) AS lead_seconds,
               c.offset_body_diameters AS predicted_offset_body_diameters,
               c.observer_distance_km, c.google_maps_url,
               floor(extract(epoch FROM c.transit_time_utc) / %s)::bigint AS event_slot,
               validation.result AS validation_result,
               validation.actual_offset_body_diameters,
               extract(epoch FROM validation.actual_closest_time_utc - validation.predicted_transit_time_utc) AS time_error_seconds,
               validation.validated_at
        FROM alerts a
        LEFT JOIN transit_candidates c ON c.id = a.transit_candidate_id
        LEFT JOIN transit_validations validation
          ON validation.icao = lower(c.icao)
         AND validation.body = lower(c.body)
         AND validation.event_slot = floor(extract(epoch FROM c.transit_time_utc) / %s)
        WHERE a.printed_at >= %s AND a.printed_at <= %s {where_q}
        ORDER BY a.printed_at DESC
        LIMIT 200
    """, tuple(args))
    travel_speed = float(os.getenv("TRAVEL_SPEED_KMH", env.get("TRAVEL_SPEED_KMH", "50")))
    reach_safety = float(os.getenv("REACH_SAFETY", env.get("REACH_SAFETY", "0.8")))
    effective_speed = max(0.1, travel_speed * reach_safety)
    for item in items:
        distance_km = float(item.get("observer_distance_km") or 0.0)
        lead_seconds = float(item.get("lead_seconds") or 0.0)
        travel_seconds = distance_km / effective_speed * 3600.0
        item["travel_seconds"] = round(travel_seconds, 1)
        item["preparation_margin_seconds"] = round(lead_seconds - travel_seconds, 1)

    event_keys = {
        (str(item.get("icao") or "").lower(), str(item.get("body") or "").lower(), item.get("event_slot"))
        for item in items
    }
    validated_events = {
        (str(item.get("icao") or "").lower(), str(item.get("body") or "").lower(), item.get("event_slot")): item.get("validation_result")
        for item in items
        if item.get("validation_result")
    }
    lead_values = [float(item["lead_seconds"]) for item in items if item.get("lead_seconds") is not None]
    summary = {
        "alerts": len(items),
        "events": len(event_keys),
        "early": sum(1 for item in items if item.get("alert_type") == "EARLY"),
        "confirmed": sum(1 for item in items if item.get("alert_type") == "CONFIRMED"),
        "last_chance": sum(1 for item in items if item.get("alert_type") == "LAST_CHANCE"),
        "better": sum(1 for item in items if item.get("alert_type") == "BETTER"),
        "hit": sum(1 for result in validated_events.values() if result == "HIT"),
        "miss": sum(1 for result in validated_events.values() if result == "MISS"),
        "avg_lead_seconds": round(sum(lead_values) / len(lead_values), 1) if lead_values else None,
    }
    return {"items": items, "summary": summary}


def _validations(database_url: str, params: dict, limit: int = 200) -> dict:
    start, end = _window(params)
    q = _search(params)
    where_q = "AND (lower(v.icao) LIKE %s OR lower(COALESCE(v.callsign,'')) LIKE %s)" if q else ""
    args: list = [start, end]
    if q:
        args.extend([f"%{q}%", f"%{q}%"])
    summary = _query(database_url, f"""
        SELECT
          count(*)::int AS total,
          count(*) FILTER (WHERE v.result='HIT')::int AS hit,
          count(*) FILTER (WHERE v.result='MISS')::int AS miss,
          count(*) FILTER (WHERE v.result='UNCERTAIN')::int AS uncertain,
          count(*) FILTER (WHERE v.result='NO_DATA')::int AS no_data,
          round(avg(abs(extract(epoch FROM v.actual_closest_time_utc - v.predicted_transit_time_utc)))
                FILTER (WHERE v.actual_closest_time_utc IS NOT NULL)::numeric, 2) AS avg_abs_time_error_seconds,
          round(avg(v.actual_offset_body_diameters)
                FILTER (WHERE v.actual_offset_body_diameters IS NOT NULL)::numeric, 4) AS avg_actual_offset,
          round(100.0 * count(*) FILTER (WHERE v.actual_closest_time_utc IS NOT NULL)
                / NULLIF(count(*), 0), 1) AS data_coverage_pct
        FROM transit_validations v
        WHERE v.predicted_transit_time_utc >= %s AND v.predicted_transit_time_utc <= %s {where_q}
    """, tuple(args))[0]
    item_args = list(args)
    item_args.append(limit)
    items = _query(database_url, f"""
        SELECT v.id, v.validated_at, v.predicted_transit_time_utc, v.actual_closest_time_utc,
               extract(epoch FROM v.actual_closest_time_utc - v.predicted_transit_time_utc) AS time_error_seconds,
               v.icao, v.callsign, v.body, v.result,
               v.predicted_offset_body_diameters, v.actual_offset_body_diameters,
               v.actual_separation_deg, v.vertical_offset_body_diameters,
               v.horizontal_offset_body_diameters, v.message,
               a.alert_type, a.printed_at AS alert_sent_at,
               c.id AS candidate_id
        FROM transit_validations v
        LEFT JOIN alerts a ON a.id = v.source_alert_id
        LEFT JOIN transit_candidates c ON c.id = a.transit_candidate_id
        WHERE v.predicted_transit_time_utc >= %s AND v.predicted_transit_time_utc <= %s {where_q}
        ORDER BY v.validated_at DESC
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
    if typ == "radar":
        return _radar(database_url, params, limit=2000)["items"]
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
