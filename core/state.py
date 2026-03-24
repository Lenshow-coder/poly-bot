import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from core.models import BankrollSnapshot, Position

logger = logging.getLogger("poly-bot.state")


def _datetime_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class StateManager:
    def __init__(self, state_dir: str = "data"):
        self.state_path = Path(state_dir) / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _default_state(self) -> dict:
        return {
            "bankroll": None,
            "positions": [],
            "cooldowns": {},
        }

    def load(self) -> dict:
        if not self.state_path.exists():
            logger.info("No state file found, returning defaults")
            return self._default_state()

        try:
            text = self.state_path.read_text(encoding="utf-8")
            data = json.loads(text)
            # Rehydrate positions
            if data.get("positions"):
                data["positions"] = [
                    Position.from_dict(p) for p in data["positions"]
                ]
            # Rehydrate bankroll
            if data.get("bankroll"):
                data["bankroll"] = BankrollSnapshot.from_dict(data["bankroll"])
            return data
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Corrupt state file, backing up: {e}")
            backup = self.state_path.with_suffix(".json.bak")
            shutil.copy2(self.state_path, backup)
            logger.info(f"Backed up corrupt state to {backup}")
            return self._default_state()

    def save(
        self,
        bankroll: BankrollSnapshot | None = None,
        positions: list[Position] | None = None,
        cooldowns: dict | None = None,
    ) -> None:
        state = {
            "bankroll": bankroll.to_dict() if bankroll else None,
            "positions": [p.to_dict() for p in (positions or [])],
            "cooldowns": cooldowns or {},
        }

        # Atomic write: write to temp file then replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.state_path.parent), suffix=".tmp"
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=_datetime_serializer)
            Path(tmp_path).replace(self.state_path)
            logger.debug("State saved successfully")
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
