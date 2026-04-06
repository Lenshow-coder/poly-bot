import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER_DIR = Path(__file__).resolve().parent
STATE_DIR = RUNNER_DIR / "state"
STATUS_FILE = STATE_DIR / "runner_status.json"
LOG_FILE = STATE_DIR / "runner.log"
PID_FILE = STATE_DIR / "runner.pid"
STOP_FILE = STATE_DIR / "stop.signal"
SERVICE_FILE = RUNNER_DIR / "service.py"

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
    body { font-family: Arial, sans-serif; margin: 24px; background: #f7f7f9; color: #111; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; min-width: 360px; }
    button { padding: 8px 12px; margin-right: 8px; margin-top: 8px; cursor: pointer; }
    input, select { padding: 6px; margin-top: 6px; }
    label { display: inline-block; min-width: 180px; }
    pre { background: #111; color: #ddd; padding: 12px; border-radius: 8px; max-height: 480px; overflow: auto; }
    .ok { color: #0a7d2d; font-weight: bold; }
    .bad { color: #b00020; font-weight: bold; }
    .mono { font-family: Consolas, "Courier New", monospace; }
  </style>
</head>
<body>
  <h2>Poly-Bot Runner Control</h2>
  <div class="row">
    <div class="card">
      <h3>Controls</h3>
      <div>
        <label for="mode">Mode:</label>
        <select id="mode">
          <option value="engine">engine (default)</option>
          <option value="dry-run">dry-run (single cycle)</option>
        </select>
      </div>
      <div>
        <label for="restartOnFailure">Restart on failure:</label>
        <input id="restartOnFailure" type="checkbox" checked>
      </div>
      <div>
        <label for="restartDelaySeconds">Restart delay (sec):</label>
        <input id="restartDelaySeconds" type="number" step="0.5" min="0" value="5">
      </div>
      <div>
        <button onclick="startRunner()">Start</button>
        <button onclick="stopRunner()">Stop (graceful)</button>
        <button onclick="killRunner()">Kill now</button>
        <button onclick="clearLogs()">Clear logs</button>
        <button onclick="refreshAll()">Refresh</button>
      </div>
      <p id="message"></p>
    </div>

    <div class="card">
      <h3>Status</h3>
      <div id="status">Loading...</div>
    </div>
  </div>

  <div class="card" style="margin-top: 16px;">
    <h3>Recent Logs</h3>
    <pre id="logs">Loading logs...</pre>
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
      const data = await postJSON('/api/clear-logs', {});
      showMessage(data.message || 'Logs cleared', !!data.ok);
      refreshAll();
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
    setInterval(refreshAll, 30000);
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

        if self.path == "/api/clear-logs":
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

        self._json({"ok": False, "message": "Not found"}, code=HTTPStatus.NOT_FOUND)

    def log_message(self, _format: str, *_args) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local dashboard for poly-bot runner.")
    parser.add_argument("--host", default="127.0.0.1", help="Host bind address.")
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
