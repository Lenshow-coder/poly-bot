import asyncio
import csv
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.models import ExecutionResult, OrderResult, Signal
from core.polymarket_client import PolymarketClient
from core.position_tracker import PositionTracker
from markets.base import TradeParams

logger = logging.getLogger("poly-bot.executor")

SPORTSBOOK_COLUMNS = [
    "odds_fanduel",
    "odds_draftkings",
    "odds_betmgm",
    "odds_betrivers",
    "odds_bet365",
    "odds_caesars",
    "odds_thescore",
    "odds_ozoon",
    "odds_bol",
    "odds_betano",
    "odds_pinnacle",
]

TRADE_LOG_COLUMNS = [
    "timestamp",
    "event",
    "outcome",
    "token_id",
    "side",
    "shares",
    "price",
    "usd",
    "edge_pct",
    "fair_value",
    "kelly_usd",
    "sources",
    "odds_scrape_ts",
    *SPORTSBOOK_COLUMNS,
    "order_type",
    "status",
    "order_id",
    "reason",
]


class TradeCsvLogger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def append(self, row: dict) -> None:
        self._ensure_header()
        with self.path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_LOG_COLUMNS, extrasaction="ignore")
            writer.writerow(row)

    def _ensure_header(self) -> None:
        if self.path.exists() and self.path.stat().st_size > 0:
            return
        with self.path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_LOG_COLUMNS)
            writer.writeheader()


class Executor:
    def __init__(
        self,
        client: PolymarketClient,
        trade_log_path: str = "data/trades.csv",
    ):
        self.client = client
        self.trade_logger = TradeCsvLogger(trade_log_path)

    async def execute(
        self,
        signal: Signal,
        trade_params: TradeParams,
        tracker: PositionTracker | None,
        adjusted_size_usd: float,
        dry_run: bool = False,
        scrape_timestamp: datetime | None = None,
        sportsbook_odds: dict[str, float] | None = None,
        sources_books: list[str] | None = None,
        order_type_override: str | None = None,
        mark_cooldown_on_reject: bool = False,
        reject_cooldown_minutes: int | None = None,
        apply_tracker_updates: bool = True,
        held_shares: float | None = None,
    ) -> ExecutionResult:
        resolved_order_type = (order_type_override or trade_params.order_type or "FOK").upper()
        side = signal.side.upper()
        price = self._select_limit_price(signal, side)
        if price is None or price <= 0:
            result = ExecutionResult(
                status="SKIPPED",
                token_id=signal.token_id,
                side=side,
                requested_shares=0.0,
                filled_shares=0.0,
                requested_price=0.0,
                avg_fill_price=0.0,
                reason="invalid_price",
            )
            self._log_trade_attempt(
                signal=signal,
                result=result,
                order_type=resolved_order_type,
                usd_size=0.0,
                scrape_timestamp=scrape_timestamp,
                sportsbook_odds=sportsbook_odds,
                sources_books=sources_books,
            )
            return result

        requested_shares = self._shares_for_signal(
            signal=signal,
            side=side,
            limit_price=price,
            tracker=tracker,
            adjusted_size_usd=adjusted_size_usd,
            held_shares=held_shares,
        )
        requested_notional = requested_shares * price
        if requested_shares <= 0:
            result = ExecutionResult(
                status="SKIPPED",
                token_id=signal.token_id,
                side=side,
                requested_shares=0.0,
                filled_shares=0.0,
                requested_price=price,
                avg_fill_price=0.0,
                reason="zero_shares",
            )
            self._log_trade_attempt(
                signal=signal,
                result=result,
                order_type=resolved_order_type,
                usd_size=0.0,
                scrape_timestamp=scrape_timestamp,
                sportsbook_odds=sportsbook_odds,
                sources_books=sources_books,
            )
            return result

        if requested_notional < 1:
            result = ExecutionResult(
                status="SKIPPED",
                token_id=signal.token_id,
                side=side,
                requested_shares=requested_shares,
                filled_shares=0.0,
                requested_price=price,
                avg_fill_price=0.0,
                reason="below_min_notional",
            )
            self._log_trade_attempt(
                signal=signal,
                result=result,
                order_type=resolved_order_type,
                usd_size=requested_notional,
                scrape_timestamp=scrape_timestamp,
                sportsbook_odds=sportsbook_odds,
                sources_books=sources_books,
            )
            return result

        if dry_run:
            result = ExecutionResult(
                status="DRY_RUN",
                token_id=signal.token_id,
                side=side,
                requested_shares=requested_shares,
                filled_shares=0.0,
                requested_price=price,
                avg_fill_price=0.0,
                reason="would_execute",
            )
            self._log_trade_attempt(
                signal=signal,
                result=result,
                order_type=resolved_order_type,
                usd_size=requested_notional,
                scrape_timestamp=scrape_timestamp,
                sportsbook_odds=sportsbook_odds,
                sources_books=sources_books,
            )
            return result

        placed_attempt = False
        try:
            placed_attempt = True
            order_result = await asyncio.to_thread(
                self.client.place_order,
                signal.token_id,
                side,
                round(requested_shares, 4),
                round(price, 4),
                resolved_order_type,
            )
            result = self._from_order_result(signal, side, requested_shares, price, order_result)
            if apply_tracker_updates and tracker is not None and result.filled_shares > 0:
                tracker.apply_fill(
                    token_id=signal.token_id,
                    outcome_name=signal.outcome_name,
                    event_name=signal.event_name,
                    side=side,
                    shares=result.filled_shares,
                    price=result.avg_fill_price,
                )
                tracker.mark_traded(signal.token_id, trade_params.cooldown_minutes)
            elif (
                apply_tracker_updates
                and tracker is not None
                and mark_cooldown_on_reject
                and placed_attempt
            ):
                tracker.mark_traded(
                    signal.token_id,
                    reject_cooldown_minutes or trade_params.cooldown_minutes,
                )
        except Exception as exc:
            logger.exception(
                "Order placement failed token=%s side=%s reason=%s",
                signal.token_id,
                side,
                str(exc),
            )
            result = ExecutionResult(
                status="ERROR",
                token_id=signal.token_id,
                side=side,
                requested_shares=requested_shares,
                filled_shares=0.0,
                requested_price=price,
                avg_fill_price=0.0,
                reason=f"exception:{exc}",
            )
            if (
                apply_tracker_updates
                and tracker is not None
                and mark_cooldown_on_reject
                and placed_attempt
            ):
                tracker.mark_traded(
                    signal.token_id,
                    reject_cooldown_minutes or trade_params.cooldown_minutes,
                )

        self._log_trade_attempt(
            signal=signal,
            result=result,
            order_type=resolved_order_type,
            usd_size=requested_notional,
            scrape_timestamp=scrape_timestamp,
            sportsbook_odds=sportsbook_odds,
            sources_books=sources_books,
        )
        return result

    @staticmethod
    def _select_limit_price(signal: Signal, side: str) -> float | None:
        if side == "BUY":
            return signal.max_price or signal.market_price
        return signal.market_price or signal.min_price

    @staticmethod
    def _shares_for_signal(
        signal: Signal,
        side: str,
        limit_price: float,
        tracker: PositionTracker | None,
        adjusted_size_usd: float,
        held_shares: float | None = None,
    ) -> float:
        if side == "BUY":
            return adjusted_size_usd / limit_price

        if held_shares is not None:
            position_size = held_shares
        elif tracker is not None:
            pos = tracker.get_position(signal.token_id)
            position_size = pos.size if pos is not None else 0.0
        else:
            position_size = 0.0

        if position_size <= 0:
            return 0.0

        if adjusted_size_usd <= 0:
            return position_size
        return min(position_size, adjusted_size_usd / limit_price)

    @staticmethod
    def _from_order_result(
        signal: Signal,
        side: str,
        requested_shares: float,
        requested_price: float,
        order_result: OrderResult,
    ) -> ExecutionResult:
        status = str(order_result.status).upper()
        filled_shares = float(order_result.filled_size or 0.0)
        avg_fill_price = float(order_result.filled_price or requested_price)
        return ExecutionResult(
            status=status,
            token_id=signal.token_id,
            side=side,
            requested_shares=requested_shares,
            filled_shares=filled_shares,
            requested_price=requested_price,
            avg_fill_price=avg_fill_price,
            order_id=order_result.order_id,
            reason="filled" if filled_shares > 0 else "not_filled",
        )

    def _log_trade_attempt(
        self,
        signal: Signal,
        result: ExecutionResult,
        order_type: str,
        usd_size: float,
        scrape_timestamp: datetime | None,
        sportsbook_odds: dict[str, float] | None,
        sources_books: list[str] | None,
    ) -> None:
        odds_values = {k: "" for k in SPORTSBOOK_COLUMNS}
        sportsbook_odds = sportsbook_odds or {}
        for col in SPORTSBOOK_COLUMNS:
            book = col.replace("odds_", "")
            if book in sportsbook_odds:
                odds_values[col] = sportsbook_odds[book]

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": signal.event_name,
            "outcome": signal.outcome_name,
            "token_id": signal.token_id,
            "side": signal.side,
            "shares": round(result.requested_shares, 6),
            "price": round(result.requested_price, 6),
            "usd": round(usd_size, 6),
            "edge_pct": round(signal.edge * 100, 4),
            "fair_value": round(signal.fair_value, 6),
            "kelly_usd": round(signal.size_usd, 6),
            "sources": ",".join(sorted(set(sources_books or []))),
            "odds_scrape_ts": scrape_timestamp.isoformat() if scrape_timestamp else "",
            "order_type": order_type.upper(),
            "status": result.status,
            "order_id": result.order_id,
            "reason": result.reason,
            **odds_values,
        }
        self.trade_logger.append(row)

        logger.info(
            "execution_result %s",
            asdict(result),
        )
