import logging

from core.models import PriceInfo, Signal
from markets.base import OutcomeFairValue, TradeParams

logger = logging.getLogger(__name__)


def kelly_bet_size(
    fair_prob: float,
    market_price: float,
    bankroll: float,
    kelly_fraction: float = 0.25,
    min_bet: float = 5.0,
    max_bet: float = 50.0,
) -> float:
    """Compute fractional Kelly bet size in USDC."""
    if market_price <= 0 or market_price >= 1:
        return 0.0

    b = (1 / market_price) - 1       # net odds
    q = 1 - fair_prob
    kelly_pct = (fair_prob * b - q) / b

    if kelly_pct <= 0:
        return 0.0

    bet = bankroll * kelly_pct * kelly_fraction

    if bet < min_bet:
        return 0.0
    return min(bet, max_bet)


def evaluate_signals(
    fair_values: list[OutcomeFairValue],
    polymarket_prices: dict[str, PriceInfo],
    trade_params: TradeParams,
    kelly_bankroll: float,
    event_name: str,
) -> list[Signal]:
    """Compare fair values to Polymarket prices, emit trade signals."""
    signals = []
    for fv in fair_values:
        pm = polymarket_prices.get(fv.token_id)
        if not pm:
            continue

        # Source count filter
        if fv.sources_agreeing < trade_params.min_sources:
            continue

        # Price range filter (use best_ask for BUY, best_bid for SELL)
        lo, hi = trade_params.price_range
        ask_in_range = pm.best_ask is not None and lo <= pm.best_ask <= hi
        bid_in_range = pm.best_bid is not None and lo <= pm.best_bid <= hi
        if not ask_in_range and not bid_in_range:
            continue

        # BUY edge: fair value > best ask
        if fv.fair_value > 0 and pm.best_ask is not None:
            buy_edge = (fv.fair_value - pm.best_ask) / fv.fair_value
        else:
            buy_edge = 0

        if ask_in_range and buy_edge > trade_params.edge_threshold:
            # Sportsbook safety buffer: poly ask must exceed best book implied prob
            # by a relative margin. Uses raw (vigged) prob — conservative.
            if (
                trade_params.sportsbook_buffer > 0
                and fv.best_book_implied_prob > 0
            ):
                relative_gap = (
                    (pm.best_ask - fv.best_book_implied_prob)
                    / fv.best_book_implied_prob
                )
                if relative_gap < trade_params.sportsbook_buffer:
                    logger.debug(
                        f"Buffer skip {fv.outcome_name}: poly_ask={pm.best_ask:.3f} "
                        f"best_book={fv.best_book_implied_prob:.3f} "
                        f"gap={relative_gap:.1%} < buffer={trade_params.sportsbook_buffer:.1%}"
                    )
                    continue

            bet_size = kelly_bet_size(
                fair_prob=fv.fair_value,
                market_price=pm.best_ask,
                bankroll=kelly_bankroll,
                kelly_fraction=trade_params.kelly_fraction,
                min_bet=trade_params.min_bet_size,
                max_bet=trade_params.max_bet_size,
            )
            if bet_size > 0:
                signals.append(Signal(
                    token_id=fv.token_id,
                    outcome_name=fv.outcome_name,
                    event_name=event_name,
                    side="BUY",
                    edge=buy_edge,
                    fair_value=fv.fair_value,
                    market_price=pm.best_ask,
                    size_usd=bet_size,
                    max_price=pm.best_ask,
                ))

        # SELL edge: best bid > fair value
        if fv.fair_value > 0 and pm.best_bid is not None:
            sell_edge = (pm.best_bid - fv.fair_value) / fv.fair_value
        else:
            sell_edge = 0

        if bid_in_range and sell_edge > trade_params.edge_threshold:
            signals.append(Signal(
                token_id=fv.token_id,
                outcome_name=fv.outcome_name,
                event_name=event_name,
                side="SELL",
                edge=sell_edge,
                fair_value=fv.fair_value,
                market_price=pm.best_bid,
                size_usd=0,  # Phase 3's risk manager determines actual size
                min_price=fv.fair_value,
                reason="edge_disappeared",
            ))

    return signals


def check_exits(
    fair_values: list[OutcomeFairValue],
    polymarket_prices: dict[str, PriceInfo],
    trade_params: TradeParams,
    kelly_bankroll: float,
    event_name: str,
    held_token_ids: set[str],
) -> list[Signal]:
    """Filter evaluate_signals output to SELL signals for tokens we actually hold."""
    all_signals = evaluate_signals(
        fair_values, polymarket_prices, trade_params, kelly_bankroll, event_name
    )
    return [s for s in all_signals if s.side == "SELL" and s.token_id in held_token_ids]
