import logging
from datetime import datetime, timedelta, timezone

from core.models import BankrollSnapshot, Position
from core.state import StateManager

logger = logging.getLogger("poly-bot.position-tracker")


class PositionTracker:
    def __init__(self, state_manager: StateManager | None = None):
        self.state_manager = state_manager or StateManager()
        loaded = self.state_manager.load()

        self.positions: dict[str, Position] = {
            p.token_id: p for p in loaded.get("positions", [])
        }
        self.cooldowns: dict[str, datetime] = {}
        for token_id, raw_ts in (loaded.get("cooldowns", {}) or {}).items():
            try:
                self.cooldowns[token_id] = datetime.fromisoformat(raw_ts)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring invalid cooldown timestamp for {token_id}: {raw_ts}")

        bankroll = loaded.get("bankroll")
        self.bankroll: BankrollSnapshot | None = bankroll if isinstance(
            bankroll, BankrollSnapshot
        ) else None
        self.last_local_trade_at: dict[str, datetime] = {}
        self.consecutive_empty_syncs = 0
        self.missing_seen_counts: dict[str, int] = {}
        reconcile_meta = loaded.get("reconcile_meta", {}) or {}
        for token_id, raw_ts in (reconcile_meta.get("last_local_trade_at", {}) or {}).items():
            try:
                self.last_local_trade_at[token_id] = datetime.fromisoformat(raw_ts)
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignoring invalid last_local_trade_at for {token_id}: {raw_ts}"
                )
        try:
            self.consecutive_empty_syncs = int(
                reconcile_meta.get("consecutive_empty_syncs", 0)
            )
        except (TypeError, ValueError):
            self.consecutive_empty_syncs = 0
        for token_id, count in (reconcile_meta.get("missing_seen_counts", {}) or {}).items():
            try:
                self.missing_seen_counts[token_id] = int(count)
            except (TypeError, ValueError):
                continue

    def get_position(self, token_id: str) -> Position | None:
        return self.positions.get(token_id)

    def get_positions(self) -> list[Position]:
        return list(self.positions.values())

    def get_total_exposure(self) -> float:
        return sum(p.size * p.avg_cost for p in self.positions.values())

    def get_event_exposure(self, event_name: str) -> float:
        return sum(
            p.size * p.avg_cost for p in self.positions.values() if p.market_name == event_name
        )

    def get_event_exposure_by_tokens(self, token_ids: set[str]) -> float:
        return sum(
            p.size * p.avg_cost for token_id, p in self.positions.items() if token_id in token_ids
        )

    def get_outcome_exposure(self, token_id: str) -> float:
        pos = self.positions.get(token_id)
        if not pos:
            return 0.0
        return pos.size * pos.avg_cost

    def is_on_cooldown(self, token_id: str) -> bool:
        cooldown_until = self.cooldowns.get(token_id)
        if not cooldown_until:
            return False
        if self._utcnow() >= cooldown_until:
            self.cooldowns.pop(token_id, None)
            self.save_state()
            return False
        return True

    def mark_traded(self, token_id: str, cooldown_minutes: int) -> None:
        self.cooldowns[token_id] = self._utcnow() + timedelta(minutes=cooldown_minutes)
        self.save_state()

    def apply_fill(
        self,
        token_id: str,
        outcome_name: str,
        event_name: str,
        side: str,
        shares: float,
        price: float,
    ) -> None:
        if shares <= 0:
            return

        side = side.upper()
        existing = self.positions.get(token_id)

        now = self._utcnow()
        if side == "BUY":
            if existing is None:
                self.positions[token_id] = Position(
                    token_id=token_id,
                    outcome_name=outcome_name,
                    market_name=event_name,
                    side="BUY",
                    size=shares,
                    avg_cost=price,
                )
            else:
                total_cost = (existing.size * existing.avg_cost) + (shares * price)
                new_size = existing.size + shares
                existing.size = new_size
                existing.avg_cost = total_cost / new_size if new_size > 0 else price
                existing.outcome_name = outcome_name or existing.outcome_name
                existing.market_name = event_name or existing.market_name
        elif side == "SELL":
            if existing is None:
                logger.warning(f"SELL fill ignored: no local position for token {token_id}")
                return
            existing.size = max(0.0, existing.size - shares)
            if existing.size <= 1e-9:
                self.positions.pop(token_id, None)
        else:
            raise ValueError(f"Unsupported side '{side}'")

        self.last_local_trade_at[token_id] = now
        self.save_state()

    def sync_from_api(
        self,
        api_positions: list[dict],
        reconcile_tolerance_pct: float = 0.05,
        local_fill_grace_seconds: int = 30,
        empty_sync_threshold: int = 2,
        missing_sync_threshold: int = 2,
    ) -> None:
        if not isinstance(api_positions, list):
            logger.warning("Position sync skipped: API payload is not a list")
            return

        api_map: dict[str, Position] = {}
        for raw in api_positions:
            parsed = self._parse_api_position(raw)
            if parsed is not None:
                api_map[parsed.token_id] = parsed

        if self.positions and not api_map:
            self.consecutive_empty_syncs += 1
            if self.consecutive_empty_syncs < max(1, empty_sync_threshold):
                logger.warning(
                    "Position sync returned empty set (%d/%d); keeping local positions",
                    self.consecutive_empty_syncs,
                    max(1, empty_sync_threshold),
                )
                return
            logger.warning(
                "Position sync empty threshold reached (%d); applying deletion policy",
                self.consecutive_empty_syncs,
            )
        else:
            self.consecutive_empty_syncs = 0

        missing_tokens = set(self.positions.keys()) - set(api_map.keys())
        for token_id in set(self.positions.keys()) & set(api_map.keys()):
            self.missing_seen_counts.pop(token_id, None)

        for token_id, remote in api_map.items():
            local = self.positions.get(token_id)
            if local is None:
                self.positions[token_id] = remote
                self.missing_seen_counts.pop(token_id, None)
                continue

            if self._is_recent_local_trade(token_id, local_fill_grace_seconds):
                size_delta = abs(local.size - remote.size)
                base = max(local.size, 1e-9)
                size_delta_pct = size_delta / base
                if remote.size < local.size or size_delta_pct > reconcile_tolerance_pct:
                    logger.warning(
                        "Skipping reconcile overwrite for recently traded token=%s "
                        "local=%.6f remote=%.6f",
                        token_id,
                        local.size,
                        remote.size,
                    )
                    continue

            size_delta = abs(local.size - remote.size)
            base = max(local.size, 1e-9)
            size_delta_pct = size_delta / base
            if size_delta_pct > reconcile_tolerance_pct:
                logger.warning(
                    "Position reconciliation mismatch token=%s local=%.6f remote=%.6f "
                    "delta_pct=%.2f%%",
                    token_id,
                    local.size,
                    remote.size,
                    size_delta_pct * 100,
                )

            local.size = remote.size
            local.avg_cost = remote.avg_cost
            if remote.outcome_name:
                local.outcome_name = remote.outcome_name
            # Keep local event identity stable when already known.
            if remote.market_name and not local.market_name:
                local.market_name = remote.market_name

        # Remove local positions only after repeated missing observations.
        for token_id in missing_tokens:
            if self._is_recent_local_trade(token_id, local_fill_grace_seconds):
                continue
            self.missing_seen_counts[token_id] = self.missing_seen_counts.get(token_id, 0) + 1
            if self.missing_seen_counts[token_id] < max(1, missing_sync_threshold):
                continue
            self.missing_seen_counts.pop(token_id, None)
            if token_id in self.positions:
                self.positions.pop(token_id, None)

        self.save_state()

    def snapshot_bankroll(
        self,
        exchange_balance: float,
        prices_by_token: dict[str, float] | None = None,
    ) -> BankrollSnapshot:
        prices_by_token = prices_by_token or {}
        positions_value = 0.0
        for pos in self.positions.values():
            mark_price = prices_by_token.get(pos.token_id, pos.avg_cost)
            positions_value += pos.size * mark_price

        snapshot = BankrollSnapshot(
            usdc_balance=exchange_balance,
            positions_value=positions_value,
            total_bankroll=exchange_balance + positions_value,
            timestamp=self._utcnow().isoformat(),
        )
        self.bankroll = snapshot
        self.save_state()
        return snapshot

    def save_state(self) -> None:
        cooldowns = {
            token_id: ts.isoformat()
            for token_id, ts in self.cooldowns.items()
        }
        reconcile_meta = {
            "last_local_trade_at": {
                token_id: ts.isoformat()
                for token_id, ts in self.last_local_trade_at.items()
            },
            "consecutive_empty_syncs": self.consecutive_empty_syncs,
            "missing_seen_counts": self.missing_seen_counts,
        }
        self.state_manager.save(
            bankroll=self.bankroll,
            positions=list(self.positions.values()),
            cooldowns=cooldowns,
            reconcile_meta=reconcile_meta,
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _is_recent_local_trade(self, token_id: str, grace_seconds: int) -> bool:
        last_trade = self.last_local_trade_at.get(token_id)
        if not last_trade:
            return False
        return (self._utcnow() - last_trade).total_seconds() < max(0, grace_seconds)

    def _parse_api_position(self, raw: dict) -> Position | None:
        token_id = raw.get("asset") or raw.get("tokenID") or raw.get("token_id")
        if token_id is None:
            return None

        try:
            size = float(
                raw.get("size")
                or raw.get("amount")
                or raw.get("position")
                or raw.get("shares")
                or 0.0
            )
        except (TypeError, ValueError):
            return None

        if size <= 0:
            return None

        try:
            avg_cost = float(
                raw.get("avgPrice")
                or raw.get("avg_price")
                or raw.get("averagePrice")
                or raw.get("initialPrice")
                or 0.0
            )
        except (TypeError, ValueError):
            avg_cost = 0.0

        existing = self.positions.get(str(token_id))
        return Position(
            token_id=str(token_id),
            outcome_name=str(
                raw.get("outcome")
                or raw.get("outcome_name")
                or (existing.outcome_name if existing else "")
            ),
            market_name=str(
                raw.get("title")
                or raw.get("event")
                or raw.get("market_name")
                or (existing.market_name if existing else "")
            ),
            side="BUY",
            size=size,
            avg_cost=avg_cost if avg_cost > 0 else (existing.avg_cost if existing else 0.0),
        )
