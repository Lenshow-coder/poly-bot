# Runner UI

This folder contains a lightweight local dashboard and background runner for `main.py`.

## Files

- `service.py`: detached background service that supervises `python main.py`.
- `dashboard.py`: local web app for Start/Stop/Kill and live status/logs.
- `state/`: runtime files (created automatically).

## Quick start

From repo root:

```bash
.venv/Scripts/python.exe -m runner.dashboard --port 8766 --open-browser
```

Or on Windows, double-click:

- `runner.vbs`

Then use the controls in the browser:

- **Start**: launch runner in background
- **Stop (graceful)**: request clean shutdown (Ctrl+Break/SIGTERM)
- **Kill now**: immediate process kill
- **Quick settings**: edit key knobs in a form and save
- **Advanced YAML editor**: full `config.yaml` load/validate/save
- **Shut down dashboard**: stop local dashboard server (runner can keep running)

## Mode options

- **engine** (default): runs `main.py` in persistent bot mode.
- **dry-run**: runs `main.py --dry-run` once, then exits.

`Restart on failure` only applies when mode is `engine` and the bot exits with a non-zero code.
Default dashboard URL is `http://127.0.0.1:8766`.

## Notes

- The runner prefers `.venv/Scripts/python.exe` (fallback to current interpreter).
- `runner.vbs` uses `.venv/Scripts/pythonw.exe` when available so the launcher is windowless.
- Closing browser/dashboard does not stop a running background runner.
- Logs and status live in `runner/state/`.
- Saving config from the dashboard creates backups in `config.backups/`.
