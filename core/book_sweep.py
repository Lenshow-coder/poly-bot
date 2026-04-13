"""Walk the order book to find fillable size within edge threshold."""

from dataclasses import dataclass


@dataclass
class SweepResult:
    executable_shares: float  # total shares available with edge
    vwap: float               # volume-weighted average price
    worst_price: float        # highest ask (or lowest bid) price included
    levels_used: int          # how many book levels consumed
    edge_at_vwap: float       # relative edge at the VWAP

    @staticmethod
    def zero() -> "SweepResult":
        return SweepResult(
            executable_shares=0.0,
            vwap=0.0,
            worst_price=0.0,
            levels_used=0,
            edge_at_vwap=0.0,
        )


def sweep_asks(
    asks: list,
    fair_value: float,
    edge_threshold: float,
    max_levels: int = 10,
    max_price_cap: float = 0.85,
) -> SweepResult:
    """Walk ask levels ascending; accumulate size while edge holds.

    Each ask level is expected to have `.price` and `.size` attributes (floats or
    str-castable).  Returns a SweepResult with shares rounded to 4 decimals.
    """
    if not asks or fair_value <= 0:
        return SweepResult.zero()

    cumulative_cost = 0.0
    cumulative_shares = 0.0
    worst_price = 0.0
    levels_used = 0

    for level in asks:
        if levels_used >= max_levels:
            break

        level_price = float(level.price)
        level_size = float(level.size)

        if level_price > max_price_cap:
            break

        new_cost = cumulative_cost + level_price * level_size
        new_shares = cumulative_shares + level_size
        vwap = new_cost / new_shares
        edge = (fair_value - vwap) / fair_value

        if edge < edge_threshold:
            break

        cumulative_cost = new_cost
        cumulative_shares = new_shares
        worst_price = level_price
        levels_used += 1

    if levels_used == 0:
        return SweepResult.zero()

    executable_shares = round(cumulative_shares, 4)
    vwap = cumulative_cost / cumulative_shares
    edge_at_vwap = (fair_value - vwap) / fair_value

    # Sub-$1 notional after rounding → treat as zero (Polymarket minimum)
    if executable_shares * worst_price < 1.0:
        return SweepResult.zero()

    return SweepResult(
        executable_shares=executable_shares,
        vwap=round(vwap, 6),
        worst_price=worst_price,
        levels_used=levels_used,
        edge_at_vwap=round(edge_at_vwap, 6),
    )


def sweep_bids(
    bids: list,
    fair_value: float,
    edge_threshold: float,
    max_levels: int = 10,
    min_price_floor: float = 0.15,
) -> SweepResult:
    """Walk bid levels descending; accumulate size while edge holds.

    Mirror of sweep_asks for the SELL side. Edge = (vwap - fair_value) / fair_value.
    """
    if not bids or fair_value <= 0:
        return SweepResult.zero()

    cumulative_cost = 0.0
    cumulative_shares = 0.0
    worst_price = 0.0
    levels_used = 0

    for level in bids:
        if levels_used >= max_levels:
            break

        level_price = float(level.price)
        level_size = float(level.size)

        if level_price < min_price_floor:
            break

        new_cost = cumulative_cost + level_price * level_size
        new_shares = cumulative_shares + level_size
        vwap = new_cost / new_shares
        edge = (vwap - fair_value) / fair_value

        if edge < edge_threshold:
            break

        cumulative_cost = new_cost
        cumulative_shares = new_shares
        worst_price = level_price
        levels_used += 1

    if levels_used == 0:
        return SweepResult.zero()

    executable_shares = round(cumulative_shares, 4)
    vwap = cumulative_cost / cumulative_shares
    edge_at_vwap = (vwap - fair_value) / fair_value

    if executable_shares * worst_price < 1.0:
        return SweepResult.zero()

    return SweepResult(
        executable_shares=executable_shares,
        vwap=round(vwap, 6),
        worst_price=worst_price,
        levels_used=levels_used,
        edge_at_vwap=round(edge_at_vwap, 6),
    )
