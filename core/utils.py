import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv
import os


def setup_logging(level: str = "INFO", console: bool = True) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if console and not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)

    return logging.getLogger("poly-bot")


def load_config(path: str = "config.yaml") -> dict:
    load_dotenv()
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(config_path) as f:
        return yaml.safe_load(f)


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
