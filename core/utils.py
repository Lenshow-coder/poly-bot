import logging
import logging.handlers
from pathlib import Path

import yaml
from dotenv import load_dotenv
import os


def setup_logging(level: str = "INFO", console: bool = True) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if console and not has_console:
        handler = logging.StreamHandler()
        handler.setFormatter(fmt)
        root.addHandler(handler)

    # File logging — one file per day in data/logs/
    logs_dir = Path("data/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = (logs_dir / "poly-bot.log").resolve()
    has_file = any(
        isinstance(h, logging.handlers.TimedRotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == log_file_path
        for h in root.handlers
    )
    if not has_file:
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_file_path,
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

    return logging.getLogger("poly-bot")


def load_config(path: str = "config.yaml") -> dict:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Expand ${ENV_VAR} placeholders in RPC URLs
    rpc_urls = config.get("polygon", {}).get("rpc_urls", [])
    config["polygon"]["rpc_urls"] = [os.path.expandvars(u) for u in rpc_urls]

    return config


def load_env_credentials() -> tuple[str, str]:
    pk = os.environ.get("PK")
    browser_address = os.environ.get("BROWSER_ADDRESS")
    if not pk:
        raise EnvironmentError("PK environment variable is required")
    if not browser_address:
        raise EnvironmentError("BROWSER_ADDRESS environment variable is required")
    return pk, browser_address


def ensure_data_dir() -> Path:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    return data_dir
