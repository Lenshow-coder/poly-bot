import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from core.executor import Executor
from core.polymarket_client import PolymarketClient
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager
from core.signal import evaluate_signals

logger = logging.getLogger("poly-bot.engine")


class Engine:
    def __init__(self, config: dict, client: PolymarketClient, plugins: list, scrapers: list):
        self.config = config
        self.client = client
        self.plugins = plugins
        self.scrapers = scrapers

        self.engine_cfg = config.get("engine", {})
        self.risk_cfg = config.get("risk", {})

        self.dry_run = bool(self.engine_cfg.get("dry_run", False))
        self.position_sync_interval = int(self.engine_cfg.get("position_sync_interval", 60))
        self.loop_error_backoff_seconds = int(self.engine_cfg.get("loop_error_backoff_seconds", 5))
        self.reconcile_tolerance_pct = float(self.risk_cfg.get("reconcile_tolerance_pct", 0.05))
        self.kelly_bankroll = float(self.risk_cfg.get("kelly_bankroll", 1000))
        self.reconcile_local_fill_grace_seconds = int(
            self.risk_cfg.get("reconcile_local_fill_grace_seconds", 30)
        )
        self.reconcile_empty_sync_threshold = int(
            self.risk_cfg.get("reconcile_empty_sync_threshold", 2)
        )
        self.reconcile_missing_sync_threshold = int(
            self.risk_cfg.get("reconcile_missing_sync_threshold", 2)
        )
        self.trade_default_order_type = str(
            self.config.get("trade_defaults", {}).get("order_type", "FOK")
        ).upper()
        self.mark_cooldown_on_reject = bool(
            self.engine_cfg.get("mark_cooldown_on_reject", False)
        )
        self.reject_cooldown_minutes = int(
            self.engine_cfg.get("reject_cooldown_minutes", 5)
        )
        self.max_scrape_age_seconds = int(
            self.engine_cfg.get("max_scrape_age_seconds", 0)
        )

        self.tracker = PositionTracker()
        self.risk_manager = RiskManager(self.risk_cfg)
        self.executor = Executor(
            client=self.client,
            trade_log_path=self.engine_cfg.get("trade_log_path", "data/trades.csv"),
        )

        self.last_exchange_balance: float | None = None
        self._stop_event = asyncio.Event()
        self._tracker_lock = asyncio.Lock()

    async def run_forever(self) -> None:
        logger.info(
            "Engine starting dry_run=%s scrapers=%d plugins=%d",
            self.dry_run,
            len(self.scrapers),
            len(self.plugins),
        )
        if self.dry_run:
            logger.info(
                "Engine dry-run enabled: execution attempts are logged to CSV; "
                "orders/fills/cooldowns are not applied"
            )
        await self._refresh_bankroll_snapshot()
        self._register_signal_handlers()

        try:
            async with asyncio.TaskGroup() as tg:
                for scraper in self.scrapers:
                    tg.create_task(self._scraper_loop(scraper))
                tg.create_task(self._reconcile_loop())
                await self._stop_event.wait()
        except* Exception as eg:
            logger.exception("Engine task group failed: %s", eg)
            raise
        finally:
            self.tracker.save_state()
            logger.info("Engine stopped and state persisted")

    def stop(self) -> None:
        self._stop_event.set()

    async def _scraper_loop(self, scraper) -> None:
        logger.info(
            "Starting scraper loop name=%s interval=%ss",
            scraper.get_name(),
            scraper.interval,
        )
        while not self._stop_event.is_set():
            try:
                scraped = await scraper.scrape()
                await self.process_scraper_result(scraped, scraper.get_name())
                await asyncio.sleep(scraper.interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Scraper loop error name=%s error=%s",
                    scraper.get_name(),
                    str(exc),
                )
                await asyncio.sleep(self.loop_error_backoff_seconds)

    async def process_scraper_result(self, scraped_odds, scraper_name: str) -> None:
        logger.info(
            "Processing scraper result scraper=%s events=%d",
            scraper_name,
            len(scraped_odds.events),
        )

        if self.max_scrape_age_seconds > 0:
            age = (datetime.now(timezone.utc) - scraped_odds.timestamp).total_seconds()
            if age > self.max_scrape_age_seconds:
                logger.warning(
                    "Stale scrape data skipped scraper=%s age=%.0fs max=%ds",
                    scraper_name,
                    age,
                    self.max_scrape_age_seconds,
                )
                return

        for plugin in self.plugins:
            mapped_odds = plugin.extract_odds(scraped_odds)
            if not mapped_odds:
                continue

            scrape_timestamp = scraped_odds.timestamp
            event_key = getattr(plugin, "event_key", None)
            if event_key:
                event_odds = scraped_odds.events.get(event_key)
                if event_odds and event_odds.timestamp:
                    scrape_timestamp = event_odds.timestamp

            fair_values = plugin.compute_fair_values(mapped_odds)
            if not fair_values:
                continue

            async def _fetch_price(fv):
                try:
                    price = await asyncio.to_thread(self.client.get_prices, fv.token_id)
                    return fv.token_id, price
                except Exception as exc:
                    logger.warning(
                        "Price fetch failed token=%s outcome=%s error=%s",
                        fv.token_id,
                        fv.outcome_name,
                        str(exc),
                    )
                    return fv.token_id, None

            results = await asyncio.gather(*[_fetch_price(fv) for fv in fair_values])
            prices = {tid: price for tid, price in results if price is not None}

            signals = evaluate_signals(
                fair_values=fair_values,
                polymarket_prices=prices,
                trade_params=plugin.get_trade_params(),
                kelly_bankroll=self.kelly_bankroll,
                event_name=plugin.get_name(),
            )
            if not signals:
                continue

            fair_by_token = {fv.token_id: fv for fv in fair_values}
            event_token_ids = set(plugin.get_token_ids())
            trade_params = plugin.get_trade_params()
            for signal in signals:
                held_shares = None
                async with self._tracker_lock:
                    decision = self.risk_manager.approve(
                        signal=signal,
                        tracker=self.tracker,
                        trade_params=trade_params,
                        exchange_balance=self.last_exchange_balance,
                        event_token_ids=event_token_ids,
                    )
                    if signal.side.upper() == "SELL":
                        pos = self.tracker.get_position(signal.token_id)
                        held_shares = pos.size if pos else 0.0
                if not decision.approved:
                    logger.info(
                        "risk_rejected token=%s side=%s reason=%s",
                        signal.token_id,
                        signal.side,
                        decision.reason,
                    )
                    continue

                fair_ctx = fair_by_token.get(signal.token_id)
                sportsbook_odds = self._sportsbook_odds_for_signal(
                    signal.outcome_name, mapped_odds
                )
                sources_books = list((fair_ctx.book_devigged or {}).keys()) if fair_ctx else []
                result = await self.executor.execute(
                    signal=signal,
                    trade_params=trade_params,
                    adjusted_size_usd=decision.adjusted_size_usd,
                    dry_run=self.dry_run,
                    scrape_timestamp=scrape_timestamp,
                    sportsbook_odds=sportsbook_odds,
                    sources_books=sources_books,
                    order_type_override=(trade_params.order_type or self.trade_default_order_type),
                    held_shares=held_shares,
                )

                if self.dry_run or result is None:
                    continue

                async with self._tracker_lock:
                    if result.filled_shares > 0:
                        self.tracker.apply_fill(
                            token_id=signal.token_id,
                            outcome_name=signal.outcome_name,
                            event_name=signal.event_name,
                            side=signal.side.upper(),
                            shares=result.filled_shares,
                            price=result.avg_fill_price,
                        )
                        self.tracker.mark_traded(signal.token_id, trade_params.cooldown_minutes)
                    elif (
                        self.mark_cooldown_on_reject
                        and result.status not in {"SKIPPED", "DRY_RUN"}
                    ):
                        self.tracker.mark_traded(
                            signal.token_id,
                            self.reject_cooldown_minutes or trade_params.cooldown_minutes,
                        )

    async def _reconcile_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with self._tracker_lock:
                    await self._refresh_bankroll_snapshot()
                api_positions = await asyncio.to_thread(self.client.get_positions)
                async with self._tracker_lock:
                    self.tracker.sync_from_api(
                        api_positions=api_positions,
                        reconcile_tolerance_pct=self.reconcile_tolerance_pct,
                        local_fill_grace_seconds=self.reconcile_local_fill_grace_seconds,
                        empty_sync_threshold=self.reconcile_empty_sync_threshold,
                        missing_sync_threshold=self.reconcile_missing_sync_threshold,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Reconcile loop error: %s", str(exc))
            await asyncio.sleep(self.position_sync_interval)

    async def _refresh_bankroll_snapshot(self) -> None:
        self.last_exchange_balance = await asyncio.to_thread(self.client.get_exchange_balance)
        self.tracker.snapshot_bankroll(exchange_balance=self.last_exchange_balance)

    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):
                pass

        if sys.platform == "win32":
            def _win_handler(signum, frame):
                loop.call_soon_threadsafe(self.stop)

            signal.signal(signal.SIGINT, _win_handler)

    @staticmethod
    def _sportsbook_odds_for_signal(
        outcome_name: str,
        mapped_odds: dict[str, list],
    ) -> dict[str, float]:
        books = {}
        for bo in mapped_odds.get(outcome_name, []):
            books[bo.sportsbook] = bo.decimal_odds
        return books
