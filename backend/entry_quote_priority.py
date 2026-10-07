"""Order scarce PAPER quote work; estimates never authorize a trade.

The input market feed and held-position order remain unchanged. Every coin is
returned so the caller still evaluates its existing signal, evidence, safety,
price, risk and executable-quote gates. A model that overestimates actual route
fees can only move a candidate later in the queue, never remove it.
"""
from collections.abc import Callable, Iterable
from collections import Counter
from typing import Any

from paper_market_feasibility import execution_feasibility


def prioritize_entry_candidates(
    coins: Iterable[dict[str, Any]],
    max_cost_pct: float,
    *,
    is_candidate: Callable[[dict[str, Any]], bool],
    feasibility: Callable[..., dict[str, Any]] = execution_feasibility,
) -> list[dict[str, Any]]:
    """Stable market-candidate buckets: plausible cost, unknown cost, high cost.

    The caller supplies its current market-candidate predicate. Noncandidates
    follow the three quote-work buckets, with their original relative order.
    Unknown estimates stay ahead of modeled expensive candidates: missing or
    failed planning data must not silently discard a possible executable route.
    This function neither performs HTTP requests nor returns entry permission.
    """
    buckets: list[list[dict[str, Any]]] = [[], [], [], []]
    for coin in coins:
        if not is_candidate(coin):
            buckets[3].append(coin)
            continue
        try:
            # Main uses actual route quotes and explicit execution buffers.
            # Fee-only planning must not penalize it with the Lab's separate
            # slippage/latency assumptions before those quotes are evaluated.
            estimate = feasibility(coin, max_cost_pct,
                                   base_slippage_bps=0, latency_buffer_bps=0)
            feasible = estimate.get('model_cost_feasible')
        except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
            feasible = None
        bucket = 0 if feasible is True else 2 if feasible is False else 1
        buckets[bucket].append(coin)
    return [coin for bucket in buckets for coin in bucket]


def bounded_feed(
    coins: Iterable[dict[str, Any]],
    max_coins: int,
    max_cost_pct: float,
    *,
    is_candidate: Callable[[dict[str, Any]], bool],
    feasibility: Callable[..., dict[str, Any]] = execution_feasibility,
) -> list[dict[str, Any]]:
    """Retain plausible candidates at the feed cap, in original display order.

    Selection uses quote-work priority only when the feed exceeds its cap.
    Display order still follows the original feed. Occurrence counts preserve
    duplicates correctly without changing or annotating any coin dictionary.
    Held-position refresh is independent of this market-feed selection.
    """
    original = list(coins)
    if max_coins <= 0:
        return []
    if len(original) <= max_coins:
        return original
    prioritized = prioritize_entry_candidates(
        original, max_cost_pct, is_candidate=is_candidate, feasibility=feasibility)
    remaining = Counter(id(coin) for coin in prioritized[:max_coins])
    selected = []
    for coin in original:
        identity = id(coin)
        if remaining[identity] > 0:
            selected.append(coin)
            remaining[identity] -= 1
    return selected
