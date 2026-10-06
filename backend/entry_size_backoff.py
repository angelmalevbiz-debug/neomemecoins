"""Bounded PAPER quote-size retries after a valid signal fails cost gates."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

MIN_ENTRY_NOTIONAL_USD = 25.0
MAX_SIZE_ATTEMPTS = 3
QUOTE_RETRY_COOLDOWN_MS = 15_000
VERSION = "QUOTE_COST_SIZE_BACKOFF_V1"
RETRYABLE_COST_REJECTIONS = frozenset({"impact", "roundtrip_cost", "worst_case_cost"})


def quote_notional_steps(requested: float, *, minimum: float = MIN_ENTRY_NOTIONAL_USD,
                         max_attempts: int = MAX_SIZE_ATTEMPTS) -> list[float]:
    """Return a short descending size ladder, never below the current risk-sized amount.

    Each amount must receive a new exact-pool buy/sell/buy quote sequence. This
    helper only proposes sizes; it cannot waive the execution or safety gates.
    """
    try:
        requested = float(requested)
        minimum = float(minimum)
        max_attempts = int(max_attempts)
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(requested) or not math.isfinite(minimum):
        return []
    if requested <= 0 or minimum <= 0 or max_attempts <= 0:
        return []

    floor = min(requested, minimum)
    current = math.floor(requested * 100) / 100
    if current <= 0:
        return []
    sizes = [current]
    while len(sizes) < max_attempts and current > floor:
        smaller = max(floor, math.floor(current * 0.5 * 100) / 100)
        if smaller >= current:
            break
        sizes.append(smaller)
        current = smaller
    return sizes


def may_retry_at_smaller_size(rejections: list[str] | tuple[str, ...]) -> bool:
    """Only cost/impact failures may trigger a smaller quote; data/risk failures stop."""
    reasons = set(rejections)
    return bool(reasons) and reasons.issubset(RETRYABLE_COST_REJECTIONS)


def first_quote_passing_costs(requested: float,
                              check: Callable[[float, int], tuple[Any | None, list[str]]]
                              ) -> tuple[float, Any, int] | None:
    """Retry fresh quote bundles only when the previous valid quote failed cost gates."""
    for attempt_index, notional in enumerate(quote_notional_steps(requested)):
        candidate, rejections = check(notional, attempt_index)
        if candidate is not None and not rejections:
            return notional, candidate, attempt_index + 1
        if not may_retry_at_smaller_size(rejections):
            break
    return None
