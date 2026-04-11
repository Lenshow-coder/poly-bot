import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER_DIR = Path(__file__).resolve().parent
STATE_DIR = RUNNER_DIR / "state"
STATUS_FILE = STATE_DIR / "runner_status.json"
LOG_FILE = STATE_DIR / "runner.log"
PID_FILE = STATE_DIR / "runner.pid"
STOP_FILE = STATE_DIR / "stop.signal"
SERVICE_FILE = RUNNER_DIR / "service.py"
CONFIG_FILE = ROOT_DIR / "config.yaml"
CONFIG_BACKUP_DIR = ROOT_DIR / "config.backups"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import validate_config

STOP_GRACEFUL = "graceful"
STOP_KILL = "kill"


def get_project_python() -> str:
    venv_python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Poly-Bot Runner</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; margin: 0; padding: 24px 32px; background: #eef1f5; color: #1a1a2e; }
    h2 { margin: 0 0 20px; font-size: 22px; color: #1a1a2e; letter-spacing: -0.3px; }
    h3 { margin: 0 0 14px; font-size: 15px; font-weight: 600; color: #1a1a2e; text-transform: uppercase; letter-spacing: 0.5px; }
    .layout { display: flex; flex-direction: column; gap: 16px; max-width: 980px; }
    .card { background: #fff; border: 1px solid #dce0e6; border-radius: 10px; padding: 20px 24px; }
    .top-row { display: flex; gap: 16px; flex-wrap: wrap; }
    .top-row > .card { flex: 1 1 420px; }
    .controls-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .controls-bar label { font-size: 13px; font-weight: 500; color: #555; white-space: nowrap; min-width: 0; }
    .controls-bar input[type="number"] { width: 90px; }
    .btn-group { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
    .btn { padding: 7px 14px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; font-weight: 500; color: #333; transition: all 0.15s; }
    .btn:hover { background: #f5f5f5; border-color: #aaa; }
    .btn-primary { background: #2563eb; color: #fff; border-color: #2563eb; }
    .btn-primary:hover { background: #1d4ed8; }
    .btn-danger { color: #dc2626; border-color: #fca5a5; }
    .btn-danger:hover { background: #fef2f2; border-color: #dc2626; }
    .btn-sm { padding: 3px 10px; font-size: 11px; margin: 0; }
    #message, #configMessage, #quickConfigMessage { margin: 8px 0 0; font-size: 13px; min-height: 18px; }
    .ok { color: #16a34a; font-weight: 600; }
    .bad { color: #dc2626; font-weight: 600; }
    .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; font-size: 13px; }
    .status-grid div { padding: 2px 0; }
    .status-grid strong { color: #555; font-weight: 500; }
    .settings-sections { display: flex; flex-direction: column; gap: 12px; margin-bottom: 12px; }
    .settings-section { border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; background: #fafafa; }
    .settings-section h4 { margin: 0 0 10px; font-size: 12px; color: #334155; letter-spacing: 0.4px; text-transform: uppercase; }
    .settings-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px 16px; }
    .setting-item { display: flex; flex-direction: column; gap: 4px; }
    .setting-item label { font-size: 12px; color: #444; display: flex; align-items: center; gap: 6px; }
    .setting-item input, .setting-item select { padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
    .setting-item input[type="checkbox"] { width: 16px; height: 16px; align-self: flex-start; margin-top: 4px; }
    .checkbox-list { display: flex; flex-wrap: wrap; gap: 8px 14px; }
    .checkbox-list label { font-size: 12px; color: #334155; display: inline-flex; align-items: center; gap: 6px; }
    .checkbox-list input[type="checkbox"] { width: 14px; height: 14px; }
    .info-dot {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      border-radius: 999px;
      background: #e5e7eb;
      color: #334155;
      border: 1px solid #cbd5e1;
      font-size: 10px;
      font-weight: 700;
      cursor: help;
      user-select: none;
    }
    .info-wrap { position: relative; display: inline-flex; }
    .info-tip {
      position: absolute;
      left: 20px;
      top: -6px;
      min-width: 220px;
      max-width: 320px;
      padding: 8px 10px;
      background: #111827;
      color: #f9fafb;
      border-radius: 6px;
      font-size: 11px;
      line-height: 1.35;
      box-shadow: 0 6px 20px rgba(0,0,0,.2);
      visibility: hidden;
      opacity: 0;
      z-index: 30;
      pointer-events: none;
      white-space: normal;
      transition: opacity 0s;
    }
    .info-wrap:hover .info-tip { visibility: visible; opacity: 1; }
    pre { background: #181b20; color: #c8ccd4; padding: 14px 16px; border-radius: 8px; max-height: 400px; overflow: auto; font-size: 12px; line-height: 1.5; margin: 10px 0 0; }
    details summary { cursor: pointer; }
    textarea { width: 100%; min-height: 320px; font-family: Consolas, "Courier New", monospace; font-size: 12px; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px; }
    .mono { font-family: Consolas, "Courier New", monospace; }
  </style>
</head>
<body>
  <h2>Poly-Bot Runner</h2>
  <div class="layout">
    <div class="top-row">
      <div class="card">
        <h3>Controls</h3>
        <div class="controls-bar">
          <label for="mode">Mode:</label>
          <select id="mode">
            <option value="engine">engine (default)</option>
            <option value="dry-run">dry-run (single cycle)</option>
          </select>
          <label for="restartOnFailure">Restart on failure:</label>
          <input id="restartOnFailure" type="checkbox" checked>
          <label for="restartDelaySeconds">Restart delay (sec):</label>
          <input id="restartDelaySeconds" type="number" step="0.5" min="0" value="5">
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="startRunner()">Start</button>
          <button class="btn" onclick="stopRunner()">Stop</button>
          <button class="btn btn-danger" onclick="killRunner()">Kill</button>
          <button class="btn" onclick="refreshAll()">Refresh</button>
          <button class="btn" onclick="clearLogs()">Clear logs</button>
          <button class="btn" onclick="shutdownDashboard()">Shutdown</button>
        </div>
        <p id="message"></p>
      </div>
      <div class="card">
        <h3>Status</h3>
        <div id="status" class="status-grid">Loading...</div>
      </div>
    </div>

    <div class="card">
    <h3>Quick Settings</h3>
    <div class="settings-sections">
      <div class="settings-section">
        <h4>engine</h4>
        <div class="settings-grid">
          <div class="setting-item">
            <label for="qsEngineDryRun">dry_run <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">If true, run full pipeline but do not place live orders.</span></span></label>
            <input id="qsEngineDryRun" type="checkbox">
          </div>
        </div>
      </div>
      <div class="settings-section">
        <h4>trade_defaults</h4>
        <div class="settings-grid">
          <div class="setting-item">
            <label for="qsOrderType">order_type <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Default order type: FAK (Fill-And-Kill) or FOK (Fill-Or-Kill).</span></span></label>
            <select id="qsOrderType">
              <option value="FOK">FOK</option>
              <option value="FAK">FAK</option>
            </select>
          </div>
          <div class="setting-item">
            <label for="qsTradeEdgeThreshold">edge_threshold <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Minimum edge (our fair price vs market price) required to place a trade.</span></span></label>
            <input id="qsTradeEdgeThreshold" type="number" step="0.001" min="0">
          </div>
          <div class="setting-item">
            <label for="qsKellyFraction">kelly_fraction <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Fraction of full Kelly bet to use (0.25 = quarter-Kelly, more conservative).</span></span></label>
            <input id="qsKellyFraction" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsMinBetSize">min_bet_size <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Minimum trade size ($); trades smaller than this are skipped.</span></span></label>
            <input id="qsMinBetSize" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsMaxBetSize">max_bet_size <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Maximum trade size ($) regardless of Kelly recommendation.</span></span></label>
            <input id="qsMaxBetSize" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsMaxOutcomeExposure">max_outcome_exposure <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Max $ exposure on a single outcome (e.g. Team X wins).</span></span></label>
            <input id="qsMaxOutcomeExposure" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsCooldownMinutes">cooldown_minutes <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Minutes to wait after trading an outcome before trading it again.</span></span></label>
            <input id="qsCooldownMinutes" type="number" step="1" min="0">
          </div>
          <div class="setting-item">
            <label for="qsPriceRangeMin">price_range[0] <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Only trade markets with prices in this range (avoid extremes near 0 or 1).</span></span></label>
            <input id="qsPriceRangeMin" type="number" step="0.001" min="0" max="1">
          </div>
          <div class="setting-item">
            <label for="qsPriceRangeMax">price_range[1] <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Only trade markets with prices in this range (avoid extremes near 0 or 1).</span></span></label>
            <input id="qsPriceRangeMax" type="number" step="0.001" min="0" max="1">
          </div>
          <div class="setting-item">
            <label for="qsSportsbookBuffer">sportsbook_buffer <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Min relative gap between poly ask and best raw sportsbook implied prob.</span></span></label>
            <input id="qsSportsbookBuffer" type="number" step="0.001" min="0">
          </div>
          <div class="setting-item">
            <label for="qsTradeMinSources">min_sources <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Minimum number of sportsbook sources required before trusting a consensus line.</span></span></label>
            <input id="qsTradeMinSources" type="number" step="1" min="1">
          </div>
        </div>
      </div>
      <div class="settings-section">
        <h4>risk</h4>
        <div class="settings-grid">
          <div class="setting-item">
            <label for="qsKellyBankroll">kelly_bankroll <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Total bankroll ($) used for Kelly criterion bet sizing.</span></span></label>
            <input id="qsKellyBankroll" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsMaxEventExposure">max_event_exposure <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Max $ exposure allowed on any single event.</span></span></label>
            <input id="qsMaxEventExposure" type="number" step="0.01" min="0">
          </div>
          <div class="setting-item">
            <label for="qsMaxPortfolioExposure">max_portfolio_exposure <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Max $ exposure across all open positions combined.</span></span></label>
            <input id="qsMaxPortfolioExposure" type="number" step="0.01" min="0">
          </div>
        </div>
      </div>
      <div class="settings-section">
        <h4>sportsbook_signals</h4>
        <div class="settings-grid">
          <div class="setting-item">
            <label for="qsSbSignalsEnabled">enabled <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Enable outlier sportsbook signal generation/logging.</span></span></label>
            <input id="qsSbSignalsEnabled" type="checkbox">
          </div>
          <div class="setting-item">
            <label for="qsSbEdgeThreshold">edge_threshold <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Min relative deviation from consensus to flag a book as outlier.</span></span></label>
            <input id="qsSbEdgeThreshold" type="number" step="0.001" min="0">
          </div>
          <div class="setting-item">
            <label for="qsSbAbsEdgeThreshold">abs_edge_threshold <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Min absolute probability difference from consensus to flag.</span></span></label>
            <input id="qsSbAbsEdgeThreshold" type="number" step="0.001" min="0">
          </div>
          <div class="setting-item">
            <label for="qsSbMinSources">min_sources <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Skip outcomes with fewer contributing books.</span></span></label>
            <input id="qsSbMinSources" type="number" step="1" min="1">
          </div>
        </div>
      </div>
      <div class="settings-section">
        <h4>enabled_markets <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Market config slug to load from markets/configs.</span></span></h4>
        <div class="btn-group" style="margin-top:0; margin-bottom: 8px;">
          <button class="btn btn-sm" type="button" onclick="setAllEnabledMarkets(true)">All</button>
          <button class="btn btn-sm" type="button" onclick="setAllEnabledMarkets(false)">None</button>
        </div>
        <div id="qsEnabledMarketsOptions" class="checkbox-list"></div>
      </div>
      <div class="settings-section">
        <h4>scrapers[0]</h4>
        <div class="settings-grid">
          <div class="setting-item">
            <label for="qsCsvIntervalSeconds">interval <span class="info-wrap"><span class="info-dot">i</span><span class="info-tip">Seconds between runs for this scraper.</span></span></label>
            <input id="qsCsvIntervalSeconds" type="number" step="1" min="1">
          </div>
        </div>
      </div>
    </div>
    <div class="btn-group" style="margin-top: 0;">
      <button class="btn" onclick="loadCommonConfig()">Load quick settings</button>
      <button class="btn btn-primary" onclick="saveCommonConfig()">Save quick settings</button>
    </div>
    <p id="quickConfigMessage"></p>
    </div>

  <details class="card">
    <summary><strong>Advanced YAML Editor (`config.yaml`)</strong></summary>
    <div class="btn-group" style="margin-top: 12px;">
      <button class="btn" onclick="loadConfig()">Load from disk</button>
      <button class="btn" onclick="validateConfig()">Validate</button>
      <button class="btn btn-primary" onclick="saveConfig()">Save YAML</button>
      <button class="btn" onclick="resetConfigEditor()">Reset editor</button>
    </div>
    <p id="configMessage"></p>
    <textarea id="configEditor" spellcheck="false" class="mono" placeholder="Loading config..."></textarea>
  </details>

  <div class="card">
    <h3>Logs</h3>
    <div class="btn-group" style="margin-top:0">
      <button class="btn btn-sm" onclick="clearLogs()">Clear</button>
      <button class="btn btn-sm" onclick="refreshLogs()">Refresh</button>
    </div>
    <pre id="logs">Loading logs...</pre>
  </div>
  </div>

  <script>
    function showMessage(msg, ok=true) {
      const el = document.getElementById('message');
      el.className = ok ? 'ok' : 'bad';
      el.textContent = msg;
    }

    async function postJSON(url, payload = {}) {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      return await resp.json();
    }

    let loadedConfigText = '';
    let loadedCommonConfig = null;
    let enabledMarketOptions = [];

    function showConfigMessage(msg, ok=true) {
      const el = document.getElementById('configMessage');
      el.className = ok ? 'ok' : 'bad';
      el.textContent = msg;
    }

    function showQuickConfigMessage(msg, ok=true) {
      const el = document.getElementById('quickConfigMessage');
      el.className = ok ? 'ok' : 'bad';
      el.textContent = msg;
    }

    function setInputValue(id, value) {
      const el = document.getElementById(id);
      if (el) {
        el.value = value;
      }
    }

    function setInputChecked(id, value) {
      const el = document.getElementById(id);
      if (el) {
        el.checked = !!value;
      }
    }

    function renderEnabledMarkets(options, selectedValues) {
      enabledMarketOptions = Array.isArray(options) ? options.slice() : [];
      const selectedSet = new Set(Array.isArray(selectedValues) ? selectedValues : []);
      const container = document.getElementById('qsEnabledMarketsOptions');
      if (!container) {
        return;
      }
      if (enabledMarketOptions.length === 0) {
        container.innerHTML = '<em>No market configs found in markets/configs.</em>';
        return;
      }
      container.innerHTML = enabledMarketOptions.map((slug) => {
        const checked = selectedSet.has(slug) ? ' checked' : '';
        return `<label><input type="checkbox" value="${slug}"${checked}> ${slug}</label>`;
      }).join('');
    }

    function getSelectedEnabledMarkets() {
      const container = document.getElementById('qsEnabledMarketsOptions');
      if (!container) {
        return [];
      }
      return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map((cb) => cb.value);
    }

    function setAllEnabledMarkets(checked) {
      const container = document.getElementById('qsEnabledMarketsOptions');
      if (!container) {
        return;
      }
      for (const cb of container.querySelectorAll('input[type="checkbox"]')) {
        cb.checked = !!checked;
      }
    }

    function applyCommonConfigToForm(common) {
      setInputChecked('qsEngineDryRun', common.engine_dry_run);
      setInputValue('qsOrderType', common.trade_order_type);
      setInputValue('qsTradeEdgeThreshold', common.trade_edge_threshold);
      setInputValue('qsKellyFraction', common.trade_kelly_fraction);
      setInputValue('qsMinBetSize', common.trade_min_bet_size);
      setInputValue('qsMaxBetSize', common.trade_max_bet_size);
      setInputValue('qsMaxOutcomeExposure', common.trade_max_outcome_exposure);
      setInputValue('qsCooldownMinutes', common.trade_cooldown_minutes);
      setInputValue('qsPriceRangeMin', common.trade_price_range_min);
      setInputValue('qsPriceRangeMax', common.trade_price_range_max);
      setInputValue('qsSportsbookBuffer', common.trade_sportsbook_buffer);
      setInputValue('qsTradeMinSources', common.trade_min_sources);
      setInputValue('qsKellyBankroll', common.risk_kelly_bankroll);
      setInputValue('qsMaxEventExposure', common.risk_max_event_exposure);
      setInputValue('qsMaxPortfolioExposure', common.risk_max_portfolio_exposure);
      setInputChecked('qsSbSignalsEnabled', common.sportsbook_signals_enabled);
      setInputValue('qsSbEdgeThreshold', common.sportsbook_signals_edge_threshold);
      setInputValue('qsSbAbsEdgeThreshold', common.sportsbook_signals_abs_edge_threshold);
      setInputValue('qsSbMinSources', common.sportsbook_signals_min_sources);
      setInputValue('qsCsvIntervalSeconds', common.csv_scraper_interval_seconds);
      renderEnabledMarkets(common.enabled_market_options || [], common.enabled_markets || []);
    }

    function collectCommonConfigFromForm() {
      return {
        engine_dry_run: !!document.getElementById('qsEngineDryRun').checked,
        trade_order_type: String(document.getElementById('qsOrderType').value || '').toUpperCase(),
        trade_edge_threshold: Number(document.getElementById('qsTradeEdgeThreshold').value),
        trade_kelly_fraction: Number(document.getElementById('qsKellyFraction').value),
        trade_min_bet_size: Number(document.getElementById('qsMinBetSize').value),
        trade_max_bet_size: Number(document.getElementById('qsMaxBetSize').value),
        trade_max_outcome_exposure: Number(document.getElementById('qsMaxOutcomeExposure').value),
        trade_cooldown_minutes: Number(document.getElementById('qsCooldownMinutes').value),
        trade_price_range_min: Number(document.getElementById('qsPriceRangeMin').value),
        trade_price_range_max: Number(document.getElementById('qsPriceRangeMax').value),
        trade_sportsbook_buffer: Number(document.getElementById('qsSportsbookBuffer').value),
        trade_min_sources: Number(document.getElementById('qsTradeMinSources').value),
        risk_kelly_bankroll: Number(document.getElementById('qsKellyBankroll').value),
        risk_max_event_exposure: Number(document.getElementById('qsMaxEventExposure').value),
        risk_max_portfolio_exposure: Number(document.getElementById('qsMaxPortfolioExposure').value),
        sportsbook_signals_enabled: !!document.getElementById('qsSbSignalsEnabled').checked,
        sportsbook_signals_edge_threshold: Number(document.getElementById('qsSbEdgeThreshold').value),
        sportsbook_signals_abs_edge_threshold: Number(document.getElementById('qsSbAbsEdgeThreshold').value),
        sportsbook_signals_min_sources: Number(document.getElementById('qsSbMinSources').value),
        csv_scraper_interval_seconds: Number(document.getElementById('qsCsvIntervalSeconds').value),
        enabled_markets: getSelectedEnabledMarkets(),
      };
    }

    async function startRunner() {
      const mode = document.getElementById('mode').value;
      const restartOnFailure = !!document.getElementById('restartOnFailure').checked;
      const restartDelaySeconds = parseFloat(document.getElementById('restartDelaySeconds').value);
      const data = await postJSON('/api/start', {
        mode: mode,
        restart_on_failure: restartOnFailure,
        restart_delay_seconds: restartDelaySeconds,
      });
      showMessage(data.message || 'Start requested', !!data.ok);
      refreshAll();
    }

    async function stopRunner() {
      const data = await postJSON('/api/stop', {});
      showMessage(data.message || 'Stop requested', !!data.ok);
      refreshAll();
    }

    async function killRunner() {
      const data = await postJSON('/api/kill', {});
      showMessage(data.message || 'Kill requested', !!data.ok);
      refreshAll();
    }

    async function clearLogs() {
      const data = await postJSON('/api/log/clear', {});
      showMessage(data.message || 'Logs cleared', !!data.ok);
      refreshAll();
    }

    async function shutdownDashboard() {
      const data = await postJSON('/api/dashboard/shutdown', {});
      showMessage(data.message || 'Dashboard shutting down.', !!data.ok);
      if (!data.ok) {
        return;
      }
      setTimeout(() => {
        window.close();
        setTimeout(() => {
          document.body.innerHTML = '<h3>Dashboard stopped.</h3><p>You can close this tab.</p>';
        }, 250);
      }, 250);
    }

    async function loadConfig() {
      const resp = await fetch('/api/config');
      const data = await resp.json();
      if (!data.ok) {
        showConfigMessage(data.message || 'Failed to load config', false);
        return;
      }
      loadedConfigText = data.config || '';
      document.getElementById('configEditor').value = loadedConfigText;
      const runningMsg = data.runner_running ? ' Runner is active; restart it for changes to apply.' : '';
      showConfigMessage(`Loaded ${data.path}.${runningMsg}`, true);
      loadCommonConfig();
    }

    async function loadCommonConfig() {
      const resp = await fetch('/api/config/common');
      const data = await resp.json();
      if (!data.ok) {
        showQuickConfigMessage(data.message || 'Failed to load quick settings', false);
        return;
      }
      loadedCommonConfig = data.common;
      applyCommonConfigToForm(data.common);
      const runningMsg = data.runner_running ? ' Runner is active; restart it for changes to apply.' : '';
      showQuickConfigMessage(`Quick settings loaded.${runningMsg}`, true);
    }

    async function saveCommonConfig() {
      const common = collectCommonConfigFromForm();
      const data = await postJSON('/api/config/common/save', {common: common});
      showQuickConfigMessage(data.message || 'Quick settings save completed', !!data.ok);
      if (!data.ok) {
        return;
      }
      loadedCommonConfig = common;
      if (typeof data.config === 'string') {
        loadedConfigText = data.config;
        document.getElementById('configEditor').value = loadedConfigText;
      }
    }

    async function validateConfig() {
      const configText = document.getElementById('configEditor').value;
      const data = await postJSON('/api/config/validate', {config: configText});
      showConfigMessage(data.message || 'Validation complete', !!data.ok);
    }

    async function saveConfig() {
      const configText = document.getElementById('configEditor').value;
      const data = await postJSON('/api/config/save', {config: configText});
      showConfigMessage(data.message || 'Config saved', !!data.ok);
      if (data.ok) {
        loadedConfigText = configText;
        loadCommonConfig();
      }
    }

    function resetConfigEditor() {
      document.getElementById('configEditor').value = loadedConfigText;
      showConfigMessage('Editor reset to last loaded/saved config.', true);
    }

    async function refreshStatus() {
      const resp = await fetch('/api/status');
      const s = await resp.json();
      const runningText = s.running ? '<span class="ok">RUNNING</span>' : '<span class="bad">STOPPED</span>';
      document.getElementById('status').innerHTML = `
        <div><strong>State:</strong> ${runningText} (${s.state ?? '-'})</div>
        <div><strong>Runner PID:</strong> ${s.pid ?? '-'}</div>
        <div><strong>Bot PID:</strong> ${s.child_pid ?? '-'}</div>
        <div><strong>Mode:</strong> ${s.mode ?? '-'}</div>
        <div><strong>Restart on failure:</strong> ${s.restart_on_failure ?? '-'}</div>
        <div><strong>Restart delay:</strong> ${s.restart_delay_seconds ?? '-'} sec</div>
        <div><strong>Restart count:</strong> ${s.restart_count ?? 0}</div>
        <div><strong>Total starts:</strong> ${s.total_starts ?? 0}</div>
        <div><strong>Started:</strong> ${s.started_at ?? '-'}</div>
        <div><strong>Bot run start:</strong> ${s.last_bot_started_at ?? '-'}</div>
        <div><strong>Bot run finish:</strong> ${s.last_bot_finished_at ?? '-'}</div>
        <div><strong>Last bot duration:</strong> ${s.last_bot_duration_seconds ?? '-'} sec</div>
        <div><strong>Last exit code:</strong> ${s.last_exit_code ?? '-'}</div>
        <div><strong>Next restart:</strong> ${s.next_restart_at ?? '-'}</div>
        <div><strong>Last stop reason:</strong> ${s.last_stop_reason ?? '-'}</div>
        <div><strong>Last heartbeat:</strong> ${s.last_heartbeat_at ?? '-'}</div>
        <div><strong>Last error:</strong> ${s.last_error ?? '-'}</div>
      `;

      if (s.mode === 'engine' || s.mode === 'dry-run') {
        document.getElementById('mode').value = s.mode;
      }
      if (typeof s.restart_on_failure === 'boolean') {
        document.getElementById('restartOnFailure').checked = s.restart_on_failure;
      }
      if (!Number.isNaN(Number(s.restart_delay_seconds)) && Number(s.restart_delay_seconds) >= 0) {
        document.getElementById('restartDelaySeconds').value = s.restart_delay_seconds;
      }
    }

    async function refreshLogs() {
      const resp = await fetch('/api/log?lines=250');
      const data = await resp.json();
      document.getElementById('logs').textContent = data.log || '(No logs yet)';
    }

    async function refreshAll() {
      await Promise.all([refreshStatus(), refreshLogs()]);
    }

    refreshAll();
    loadConfig();
    loadCommonConfig();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
"""


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid() -> int | None:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (FileNotFoundError, ValueError):
        return None


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_stop_signal(signal_kind: str) -> None:
    STOP_FILE.write_text(signal_kind + "\n", encoding="utf-8")


def tail_log_lines(line_count: int) -> str:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    return "\n".join(lines[-line_count:])


def clear_log_file() -> None:
    LOG_FILE.write_text("", encoding="utf-8")


def _runner_running() -> bool:
    pid = read_pid()
    return bool(pid and is_pid_running(pid))


def read_config_text() -> str:
    return CONFIG_FILE.read_text(encoding="utf-8")


def _read_config_dict_from_text(config_text: str) -> dict:
    parsed = yaml.safe_load(config_text)
    if not isinstance(parsed, dict):
        raise ValueError("Config must parse to a YAML mapping/object.")
    return parsed


def read_config_dict() -> dict:
    return _read_config_dict_from_text(read_config_text())


def validate_config_text(config_text: str) -> tuple[bool, str]:
    try:
        parsed = _read_config_dict_from_text(config_text)
    except (yaml.YAMLError, ValueError) as exc:
        return False, f"YAML parse error: {exc}"
    try:
        validate_config(parsed)
    except Exception as exc:
        return False, f"Config validation failed: {exc}"
    return True, "Config is valid."


def backup_config_file() -> Path:
    CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = CONFIG_BACKUP_DIR / f"config-{ts}.yaml"
    suffix = 1
    while backup_path.exists():
        backup_path = CONFIG_BACKUP_DIR / f"config-{ts}-{suffix}.yaml"
        suffix += 1
    shutil.copy2(CONFIG_FILE, backup_path)
    return backup_path


def save_config_text(config_text: str) -> Path:
    backup_path = backup_config_file()
    tmp_path = CONFIG_FILE.with_suffix(".yaml.tmp")
    tmp_path.write_text(config_text, encoding="utf-8")
    tmp_path.replace(CONFIG_FILE)
    return backup_path


def _get_dict(cfg: dict, key: str) -> dict:
    value = cfg.get(key)
    if isinstance(value, dict):
        return value
    value = {}
    cfg[key] = value
    return value


def _to_float(value, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return number


def _to_int(value, name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer.") from None
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return number


def _to_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true/false.")


def _get_csv_scraper(config: dict) -> dict:
    scrapers = config.get("scrapers")
    if not isinstance(scrapers, list):
        scrapers = []
        config["scrapers"] = scrapers
    for scraper in scrapers:
        if isinstance(scraper, dict) and scraper.get("name") == "csv":
            return scraper
    scraper = {"name": "csv", "interval": 300, "path": "data/normalized_odds.csv"}
    scrapers.append(scraper)
    return scraper


def _market_config_slugs() -> list[str]:
    configs_dir = ROOT_DIR / "markets" / "configs"
    slugs: set[str] = set()
    for pattern in ("*.yaml", "*.yml"):
        for path in configs_dir.glob(pattern):
            slugs.add(path.stem)
    return sorted(slugs)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extract_common_config(config: dict) -> dict:
    engine = config.get("engine", {}) if isinstance(config.get("engine"), dict) else {}
    trade = config.get("trade_defaults", {}) if isinstance(config.get("trade_defaults"), dict) else {}
    risk = config.get("risk", {}) if isinstance(config.get("risk"), dict) else {}
    sb = config.get("sportsbook_signals", {}) if isinstance(config.get("sportsbook_signals"), dict) else {}
    csv_scraper = _get_csv_scraper(config)
    price_range = trade.get("price_range")
    if isinstance(price_range, list) and len(price_range) == 2:
        price_min, price_max = price_range
    else:
        price_min, price_max = 0.01, 0.99
    enabled_market_options = _market_config_slugs()
    configured_markets = config.get("enabled_markets")
    if isinstance(configured_markets, list):
        enabled_markets = [str(m) for m in configured_markets if str(m) in enabled_market_options]
    else:
        enabled_markets = []
    if not enabled_markets and enabled_market_options:
        enabled_markets = [enabled_market_options[0]]

    return {
        "engine_dry_run": bool(engine.get("dry_run", False)),
        "trade_order_type": str(trade.get("order_type", "FOK")).upper(),
        "trade_edge_threshold": float(trade.get("edge_threshold", 0.1)),
        "trade_kelly_fraction": float(trade.get("kelly_fraction", 0.25)),
        "trade_min_bet_size": float(trade.get("min_bet_size", 1.0)),
        "trade_max_bet_size": float(trade.get("max_bet_size", 100.0)),
        "trade_max_outcome_exposure": float(trade.get("max_outcome_exposure", 0.0)),
        "trade_cooldown_minutes": int(trade.get("cooldown_minutes", 30)),
        "trade_price_range_min": float(price_min),
        "trade_price_range_max": float(price_max),
        "trade_sportsbook_buffer": float(trade.get("sportsbook_buffer", 0.0)),
        "trade_min_sources": int(trade.get("min_sources", 2)),
        "risk_kelly_bankroll": float(risk.get("kelly_bankroll", 1000.0)),
        "risk_max_event_exposure": float(risk.get("max_event_exposure", 0.0)),
        "risk_max_portfolio_exposure": float(risk.get("max_portfolio_exposure", 0.0)),
        "sportsbook_signals_enabled": bool(sb.get("enabled", False)),
        "sportsbook_signals_edge_threshold": float(sb.get("edge_threshold", 0.1)),
        "sportsbook_signals_abs_edge_threshold": float(sb.get("abs_edge_threshold", 0.01)),
        "sportsbook_signals_min_sources": int(sb.get("min_sources", 2)),
        "csv_scraper_interval_seconds": int(csv_scraper.get("interval", 300)),
        "enabled_markets": enabled_markets,
        "enabled_market_options": enabled_market_options,
    }


def apply_common_config(config: dict, common: dict) -> dict:
    if not isinstance(common, dict):
        raise ValueError("common must be an object.")

    engine = _get_dict(config, "engine")
    trade = _get_dict(config, "trade_defaults")
    risk = _get_dict(config, "risk")
    sb = _get_dict(config, "sportsbook_signals")
    csv_scraper = _get_csv_scraper(config)

    order_type = str(common.get("trade_order_type", "")).upper()
    if order_type not in {"FOK", "FAK"}:
        raise ValueError("trade_order_type must be FOK or FAK.")

    price_min = _to_float(common.get("trade_price_range_min"), "trade_price_range_min", 0.0, 1.0)
    price_max = _to_float(common.get("trade_price_range_max"), "trade_price_range_max", 0.0, 1.0)
    if price_min > price_max:
        raise ValueError("trade_price_range_min must be <= trade_price_range_max.")

    engine["dry_run"] = _to_bool(common.get("engine_dry_run"), "engine_dry_run")
    trade["order_type"] = order_type
    trade["edge_threshold"] = _to_float(common.get("trade_edge_threshold"), "trade_edge_threshold", 0.0)
    trade["kelly_fraction"] = _to_float(common.get("trade_kelly_fraction"), "trade_kelly_fraction", 0.0)
    trade["min_bet_size"] = _to_float(common.get("trade_min_bet_size"), "trade_min_bet_size", 0.0)
    trade["max_bet_size"] = _to_float(common.get("trade_max_bet_size"), "trade_max_bet_size", 0.0)
    trade["max_outcome_exposure"] = _to_float(
        common.get("trade_max_outcome_exposure"),
        "trade_max_outcome_exposure",
        0.0,
    )
    trade["cooldown_minutes"] = _to_int(common.get("trade_cooldown_minutes"), "trade_cooldown_minutes", 0)
    trade["price_range"] = [price_min, price_max]
    trade["sportsbook_buffer"] = _to_float(common.get("trade_sportsbook_buffer"), "trade_sportsbook_buffer", 0.0)
    trade["min_sources"] = _to_int(common.get("trade_min_sources"), "trade_min_sources", 1)

    risk["kelly_bankroll"] = _to_float(common.get("risk_kelly_bankroll"), "risk_kelly_bankroll", 0.0)
    risk["max_event_exposure"] = _to_float(common.get("risk_max_event_exposure"), "risk_max_event_exposure", 0.0)
    risk["max_portfolio_exposure"] = _to_float(common.get("risk_max_portfolio_exposure"), "risk_max_portfolio_exposure", 0.0)

    sb["enabled"] = _to_bool(common.get("sportsbook_signals_enabled"), "sportsbook_signals_enabled")
    sb["edge_threshold"] = _to_float(common.get("sportsbook_signals_edge_threshold"), "sportsbook_signals_edge_threshold", 0.0)
    sb["abs_edge_threshold"] = _to_float(
        common.get("sportsbook_signals_abs_edge_threshold"),
        "sportsbook_signals_abs_edge_threshold",
        0.0,
    )
    sb["min_sources"] = _to_int(common.get("sportsbook_signals_min_sources"), "sportsbook_signals_min_sources", 1)

    csv_scraper["interval"] = _to_int(
        common.get("csv_scraper_interval_seconds"),
        "csv_scraper_interval_seconds",
        1,
    )
    if not isinstance(csv_scraper.get("path"), str):
        csv_scraper["path"] = "data/normalized_odds.csv"

    enabled_market_options = _market_config_slugs()
    enabled_markets_raw = common.get("enabled_markets")
    if not isinstance(enabled_markets_raw, list):
        raise ValueError("enabled_markets must be an array.")
    enabled_markets_clean = _dedupe_keep_order([str(m) for m in enabled_markets_raw])
    invalid_markets = [m for m in enabled_markets_clean if m not in enabled_market_options]
    if invalid_markets:
        raise ValueError(f"Unknown enabled_markets: {invalid_markets}")
    if not enabled_markets_clean:
        raise ValueError("enabled_markets must include at least one market.")
    config["enabled_markets"] = enabled_markets_clean

    try:
        validate_config(config)
    except Exception as exc:
        raise ValueError(f"Config validation failed: {exc}") from None
    return config


def dump_config_text(config: dict) -> str:
    return yaml.safe_dump(config, sort_keys=False)


def get_status_payload() -> dict:
    status = read_status()
    pid = read_pid()
    running = bool(pid and is_pid_running(pid))
    status["pid"] = pid
    status["running"] = running
    if not running and "state" not in status:
        status["state"] = "stopped"

    child_pid = status.get("child_pid")
    status["child_running"] = bool(isinstance(child_pid, int) and is_pid_running(child_pid))
    return status


def start_runner(mode: str, restart_on_failure: bool, restart_delay_seconds: float) -> tuple[bool, str]:
    ensure_state_dir()
    if mode not in {"engine", "dry-run"}:
        return False, "mode must be 'engine' or 'dry-run'."
    if restart_delay_seconds < 0:
        return False, "restart_delay_seconds must be >= 0."

    current_pid = read_pid()
    if current_pid and is_pid_running(current_pid):
        return False, f"Runner is already running (pid={current_pid})."

    cmd = [
        get_project_python(),
        str(SERVICE_FILE),
        "--mode",
        mode,
        "--restart-delay-seconds",
        str(restart_delay_seconds),
    ]
    cmd.append("--restart-on-failure" if restart_on_failure else "--no-restart-on-failure")

    kwargs = {
        "cwd": str(ROOT_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(cmd, **kwargs)
    return True, "Runner start requested."


def stop_runner(force: bool) -> tuple[bool, str]:
    pid = read_pid()
    if not pid:
        return False, "Runner is not running."
    if not is_pid_running(pid):
        return False, "Runner pid file exists, but process is not active."

    write_stop_signal(STOP_KILL if force else STOP_GRACEFUL)
    return True, "Kill requested." if force else "Graceful stop requested."


class RunnerHandler(BaseHTTPRequestHandler):
    def _json(self, payload: dict, code: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content: str, code: int = HTTPStatus.OK) -> None:
        body = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        ensure_state_dir()
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._html(HTML_PAGE)
            return
        if parsed.path == "/api/status":
            self._json(get_status_payload())
            return
        if parsed.path == "/api/log":
            params = parse_qs(parsed.query)
            lines = 250
            if "lines" in params:
                try:
                    lines = max(1, min(5000, int(params["lines"][0])))
                except ValueError:
                    lines = 250
            self._json({"log": tail_log_lines(lines)})
            return
        if parsed.path == "/api/config":
            try:
                config_text = read_config_text()
            except OSError as exc:
                self._json(
                    {"ok": False, "message": f"Failed to read config: {exc}"},
                    code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._json(
                {
                    "ok": True,
                    "path": str(CONFIG_FILE),
                    "config": config_text,
                    "runner_running": _runner_running(),
                }
            )
            return
        if parsed.path == "/api/config/common":
            try:
                config = read_config_dict()
                common = extract_common_config(config)
            except (OSError, yaml.YAMLError, ValueError) as exc:
                self._json(
                    {"ok": False, "message": f"Failed to load quick settings: {exc}"},
                    code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._json(
                {
                    "ok": True,
                    "common": common,
                    "runner_running": _runner_running(),
                }
            )
            return
        self._json({"ok": False, "message": "Not found"}, code=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        ensure_state_dir()
        if self.path == "/api/start":
            body = self._read_json_body()
            mode = body.get("mode", "engine")
            restart_on_failure = bool(body.get("restart_on_failure", True))
            restart_delay_seconds = body.get("restart_delay_seconds", 5.0)
            try:
                restart_delay_seconds = float(restart_delay_seconds)
            except (TypeError, ValueError):
                self._json(
                    {"ok": False, "message": "restart_delay_seconds must be a number"},
                    code=HTTPStatus.BAD_REQUEST,
                )
                return
            ok, message = start_runner(mode, restart_on_failure, restart_delay_seconds)
            self._json({"ok": ok, "message": message})
            return

        if self.path == "/api/stop":
            ok, message = stop_runner(force=False)
            self._json({"ok": ok, "message": message})
            return

        if self.path == "/api/kill":
            ok, message = stop_runner(force=True)
            self._json({"ok": ok, "message": message})
            return

        if self.path in {"/api/clear-logs", "/api/log/clear"}:
            try:
                clear_log_file()
            except OSError as exc:
                self._json(
                    {"ok": False, "message": f"Failed to clear logs: {exc}"},
                    code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._json({"ok": True, "message": "Logs cleared."})
            return

        if self.path == "/api/config/validate":
            body = self._read_json_body()
            config_text = body.get("config", "")
            if not isinstance(config_text, str):
                self._json(
                    {"ok": False, "message": "config must be a string"},
                    code=HTTPStatus.BAD_REQUEST,
                )
                return
            ok, message = validate_config_text(config_text)
            self._json({"ok": ok, "message": message})
            return

        if self.path == "/api/config/save":
            body = self._read_json_body()
            config_text = body.get("config", "")
            if not isinstance(config_text, str):
                self._json(
                    {"ok": False, "message": "config must be a string"},
                    code=HTTPStatus.BAD_REQUEST,
                )
                return
            ok, message = validate_config_text(config_text)
            if not ok:
                self._json({"ok": False, "message": message}, code=HTTPStatus.BAD_REQUEST)
                return
            try:
                backup_path = save_config_text(config_text)
            except OSError as exc:
                self._json(
                    {"ok": False, "message": f"Failed to save config: {exc}"},
                    code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            running_note = (
                " Runner is active; restart it for changes to apply."
                if _runner_running()
                else ""
            )
            self._json(
                {
                    "ok": True,
                    "message": f"Config saved. Backup: {backup_path}.{running_note}",
                    "backup_path": str(backup_path),
                }
            )
            return

        if self.path == "/api/config/common/save":
            body = self._read_json_body()
            common = body.get("common", {})
            try:
                config = read_config_dict()
                config = apply_common_config(config, common)
                config_text = dump_config_text(config)
                backup_path = save_config_text(config_text)
            except ValueError as exc:
                self._json({"ok": False, "message": str(exc)}, code=HTTPStatus.BAD_REQUEST)
                return
            except (OSError, yaml.YAMLError) as exc:
                self._json(
                    {"ok": False, "message": f"Failed to save quick settings: {exc}"},
                    code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            running_note = (
                " Runner is active; restart it for changes to apply."
                if _runner_running()
                else ""
            )
            self._json(
                {
                    "ok": True,
                    "message": f"Quick settings saved. Backup: {backup_path}.{running_note}",
                    "backup_path": str(backup_path),
                    "config": config_text,
                    "common": extract_common_config(config),
                }
            )
            return

        if self.path == "/api/dashboard/shutdown":
            self._json({"ok": True, "message": "Dashboard shutting down."})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._json({"ok": False, "message": "Not found"}, code=HTTPStatus.NOT_FOUND)

    def log_message(self, _format: str, *_args) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local dashboard for poly-bot runner.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host bind address. WARNING: binding to 0.0.0.0 exposes runner control APIs without authentication.",
    )
    parser.add_argument("--port", type=int, default=8766, help="Port number.")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open browser automatically on startup.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_state_dir()
    server = ThreadingHTTPServer((args.host, args.port), RunnerHandler)
    url = f"http://{args.host}:{args.port}"
    if sys.stdout:
        print(f"Poly-bot runner dashboard available at {url}")
        print("Press Ctrl+C to stop the dashboard server.")

    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
