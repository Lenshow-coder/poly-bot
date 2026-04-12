import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER_DIR = Path(__file__).resolve().parent
STATE_DIR = RUNNER_DIR / "state"
STATUS_FILE = STATE_DIR / "runner_status.json"
LOG_FILE = STATE_DIR / "runner.log"
PID_FILE = STATE_DIR / "runner.pid"
STOP_FILE = STATE_DIR / "stop.signal"

STOP_GRACEFUL = "graceful"
STOP_KILL = "kill"

HEARTBEAT_SECONDS = 2.0
GRACEFUL_TIMEOUT_SECONDS = 20.0

_log_handles: dict[int, object] = {}


def get_project_python() -> str:
    venv_python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid), encoding="utf-8")


def remove_pid_file() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_status(updates: dict) -> None:
    status = read_status()
    status.update(updates)
    tmp_file = STATUS_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
    tmp_file.replace(STATUS_FILE)


def log_line(message: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{now_utc_iso()}] {message}\n")


def read_stop_signal() -> str | None:
    try:
        raw = STOP_FILE.read_text(encoding="utf-8").strip().lower()
    except FileNotFoundError:
        return None
    if raw == STOP_KILL:
        return STOP_KILL
    return STOP_GRACEFUL


def clear_stop_signal() -> None:
    try:
        STOP_FILE.unlink()
    except FileNotFoundError:
        pass


def launch_bot(mode: str) -> subprocess.Popen:
    cmd = [get_project_python(), "main.py"]
    if mode == "dry-run":
        cmd.append("--dry-run")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write("----- BOT RUN START -----\n")
        f.write(f"Command: {' '.join(cmd)}\n")
        f.flush()

    log_handle = LOG_FILE.open("a", encoding="utf-8")
    kwargs = {
        "cwd": str(ROOT_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(cmd, **kwargs)
    _log_handles[process.pid] = log_handle
    return process


def _close_process_log_handle(process: subprocess.Popen) -> None:
    handle = _log_handles.pop(process.pid, None)
    if handle is not None:
        handle.close()


def request_graceful_child_stop(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except Exception:
            process.terminate()
    else:
        process.terminate()


def force_kill_child(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass


def run_service(mode: str, restart_on_failure: bool, restart_delay_seconds: float) -> None:
    ensure_state_dir()
    clear_stop_signal()

    existing_pid = read_pid()
    if existing_pid and is_pid_running(existing_pid):
        raise SystemExit(f"Runner already active (pid={existing_pid}).")
    if existing_pid and not is_pid_running(existing_pid):
        remove_pid_file()

    service_pid = os.getpid()
    write_pid(service_pid)
    write_status(
        {
            "is_running": True,
            "pid": service_pid,
            "state": "idle",
            "mode": mode,
            "restart_on_failure": restart_on_failure,
            "restart_delay_seconds": restart_delay_seconds,
            "started_at": now_utc_iso(),
            "stopped_at": None,
            "last_heartbeat_at": now_utc_iso(),
            "child_pid": None,
            "child_running": False,
            "total_starts": read_status().get("total_starts", 0),
            "restart_count": read_status().get("restart_count", 0),
            "last_error": None,
            "last_stop_reason": None,
            "next_restart_at": None,
        }
    )
    log_line(
        f"Runner started (pid={service_pid}, mode={mode}, restart_on_failure={restart_on_failure}, "
        f"restart_delay_seconds={restart_delay_seconds})"
    )

    try:
        while True:
            stop_request = read_stop_signal()
            if stop_request:
                log_line(f"Stop requested before start ({stop_request}).")
                write_status({"last_stop_reason": stop_request})
                break

            started_at = time.time()
            process = launch_bot(mode)
            total_starts = int(read_status().get("total_starts", 0)) + 1
            write_status(
                {
                    "state": "running",
                    "last_heartbeat_at": now_utc_iso(),
                    "child_pid": process.pid,
                    "child_running": True,
                    "last_bot_started_at": now_utc_iso(),
                    "next_restart_at": None,
                    "total_starts": total_starts,
                }
            )
            log_line(f"Bot process started (pid={process.pid}).")

            graceful_requested_at = None
            kill_requested = False
            last_heartbeat_at = 0.0

            while True:
                now = time.time()
                if now - last_heartbeat_at >= HEARTBEAT_SECONDS:
                    write_status({"last_heartbeat_at": now_utc_iso()})
                    last_heartbeat_at = now

                stop_request = read_stop_signal()
                if stop_request == STOP_KILL and not kill_requested:
                    kill_requested = True
                    write_status({"last_stop_reason": STOP_KILL, "state": "stopping"})
                    log_line("Kill requested. Terminating bot process.")
                    force_kill_child(process)
                elif stop_request == STOP_GRACEFUL and graceful_requested_at is None:
                    graceful_requested_at = now
                    write_status({"last_stop_reason": STOP_GRACEFUL, "state": "stopping"})
                    log_line("Graceful stop requested. Sending interrupt to bot process.")
                    request_graceful_child_stop(process)

                if graceful_requested_at is not None and process.poll() is None:
                    if (now - graceful_requested_at) >= GRACEFUL_TIMEOUT_SECONDS:
                        log_line("Graceful stop timed out; force-killing bot process.")
                        force_kill_child(process)

                exit_code = process.poll()
                if exit_code is not None:
                    duration = round(time.time() - started_at, 1)
                    _close_process_log_handle(process)
                    with LOG_FILE.open("a", encoding="utf-8") as f:
                        f.write("----- BOT RUN END -----\n")
                    log_line(f"Bot process exited with code {exit_code} after {duration}s.")
                    write_status(
                        {
                            "child_pid": None,
                            "child_running": False,
                            "last_bot_finished_at": now_utc_iso(),
                            "last_bot_duration_seconds": duration,
                            "last_exit_code": exit_code,
                            "last_heartbeat_at": now_utc_iso(),
                        }
                    )
                    break

                time.sleep(0.25)

            stop_request = read_stop_signal()
            if stop_request:
                break

            if mode == "dry-run":
                log_line("Dry-run mode completed one execution. Runner exiting.")
                break

            exit_code = read_status().get("last_exit_code")
            if exit_code == 0:
                log_line("Bot exited cleanly. Runner exiting.")
                break

            if not restart_on_failure:
                log_line("Bot exited with error and restart_on_failure=False. Runner exiting.")
                break

            restart_count = int(read_status().get("restart_count", 0)) + 1
            restart_at = datetime.now(timezone.utc) + timedelta(seconds=restart_delay_seconds)
            write_status(
                {
                    "state": "restarting",
                    "restart_count": restart_count,
                    "next_restart_at": restart_at.isoformat(timespec="seconds"),
                }
            )
            log_line(
                f"Bot exited with code {exit_code}. Restarting in {restart_delay_seconds:.1f}s "
                f"(restart #{restart_count})."
            )

            slept = 0.0
            while slept < restart_delay_seconds:
                if read_stop_signal():
                    break
                time.sleep(min(0.5, restart_delay_seconds - slept))
                slept += 0.5
    except KeyboardInterrupt:
        log_line("Runner interrupted by keyboard signal.")
        write_status({"last_stop_reason": "keyboard_interrupt"})
    except Exception as exc:
        write_status({"last_error": str(exc), "state": "error"})
        log_line(f"Runner error: {exc}")
        raise
    finally:
        clear_stop_signal()
        remove_pid_file()
        write_status(
            {
                "is_running": False,
                "pid": None,
                "state": "stopped",
                "child_pid": None,
                "child_running": False,
                "next_restart_at": None,
                "stopped_at": now_utc_iso(),
                "last_heartbeat_at": now_utc_iso(),
            }
        )
        log_line("Runner stopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Background runner service for poly-bot.")
    parser.add_argument(
        "--mode",
        choices=["engine", "dry-run"],
        default="engine",
        help="Bot mode: engine (run_forever) or dry-run (single cycle).",
    )
    parser.add_argument(
        "--restart-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically restart after non-zero exit code (default: true).",
    )
    parser.add_argument(
        "--restart-delay-seconds",
        type=float,
        default=5.0,
        help="Delay before restart on failure.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.restart_delay_seconds < 0:
        raise SystemExit("--restart-delay-seconds must be >= 0.")
    run_service(
        mode=args.mode,
        restart_on_failure=args.restart_on_failure,
        restart_delay_seconds=args.restart_delay_seconds,
    )


if __name__ == "__main__":
    main()
