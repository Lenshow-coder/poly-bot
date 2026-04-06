from core.models import RiskDecision, Signal
from core.position_tracker import PositionTracker
from markets.base import TradeParams


class RiskManager:
    def __init__(self, risk_config: dict):
        self.max_event_exposure = float(risk_config.get("max_event_exposure", 0.0))
        self.max_portfolio_exposure = float(risk_config.get("max_portfolio_exposure", 0.0))
        self.min_balance = float(risk_config.get("min_balance", 0.0))
        self.min_bankroll = float(risk_config.get("min_bankroll", 0.0))
        self.size_clip_enabled = bool(risk_config.get("size_clip_enabled", True))

    def approve(
        self,
        signal: Signal,
        tracker: PositionTracker,
        trade_params: TradeParams,
        exchange_balance: float | None = None,
        event_token_ids: set[str] | None = None,
    ) -> RiskDecision:
        # Emergency cash gate.
        if exchange_balance is not None and exchange_balance < self.min_balance:
            return RiskDecision(
                approved=False,
                adjusted_size_usd=0.0,
                reason=(
                    f"min_balance_blocked balance={exchange_balance:.2f} "
                    f"threshold={self.min_balance:.2f}"
                ),
            )

        # Bankroll gate from latest snapshot if available.
        if tracker.bankroll is not None and tracker.bankroll.total_bankroll < self.min_bankroll:
            return RiskDecision(
                approved=False,
                adjusted_size_usd=0.0,
                reason=(
                    "min_bankroll_blocked "
                    f"bankroll={tracker.bankroll.total_bankroll:.2f} "
                    f"threshold={self.min_bankroll:.2f}"
                ),
            )

        if tracker.is_on_cooldown(signal.token_id):
            return RiskDecision(False, 0.0, "cooldown_active")

        if signal.side == "SELL":
            pos = tracker.get_position(signal.token_id)
            if pos is None or pos.size <= 0:
                return RiskDecision(False, 0.0, "no_position_to_sell")

            full_notional = pos.size * signal.market_price
            requested = signal.size_usd if signal.size_usd > 0 else full_notional
            approved_size = min(requested, full_notional)
            if approved_size <= 0:
                return RiskDecision(False, 0.0, "sell_size_zero")
            return RiskDecision(True, approved_size, "approved_sell")

        requested = signal.size_usd
        if requested <= 0:
            return RiskDecision(False, 0.0, "buy_size_zero")

        outcome_remaining = max(
            0.0,
            trade_params.max_outcome_exposure - tracker.get_outcome_exposure(signal.token_id),
        )
        current_event_exposure = (
            tracker.get_event_exposure_by_tokens(event_token_ids)
            if event_token_ids
            else tracker.get_event_exposure(signal.event_name)
        )
        event_remaining = max(0.0, self.max_event_exposure - current_event_exposure)
        portfolio_remaining = max(
            0.0,
            self.max_portfolio_exposure - tracker.get_total_exposure(),
        )
        spendable_balance = float("inf")
        if exchange_balance is not None:
            spendable_balance = max(0.0, exchange_balance - self.min_balance)

        allowed = min(
            requested,
            outcome_remaining,
            event_remaining,
            portfolio_remaining,
            spendable_balance,
        )

        if spendable_balance <= 0:
            return RiskDecision(False, 0.0, "insufficient_spendable_balance")
        if allowed <= 0:
            return RiskDecision(False, 0.0, "exposure_cap_reached")
        if allowed < trade_params.min_bet_size:
            return RiskDecision(False, 0.0, "below_min_bet_after_caps")
        if allowed < requested and not self.size_clip_enabled:
            return RiskDecision(False, 0.0, "size_clipping_disabled")

        reason = "approved" if allowed == requested else "approved_clipped"
        return RiskDecision(True, allowed, reason)
