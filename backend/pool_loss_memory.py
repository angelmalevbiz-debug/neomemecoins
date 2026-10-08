"""POOL_LOSS_MEMORY_V1: no new PAPER entry into a pool right after repeated losses there.

PAPER only, read-only. After 2 consecutive losing closes (net pnl_usd < 0) on
the same (mint, pool) in one account or one Lab book, new entries into that
pool are blocked for 6 h after the last loss (reason ``pool_loss_cooldown``).
The memory is derived from the account's or book's existing closed history on
every decision: nothing is written, no ledger schema changes, no history is
rewritten, and exits of open positions are unaffected.

Evidence (research 2026-10-08 forensics, PAPER): 21 of the 23 historical main
closes were one token (swordinu) re-bought every 30-40 minutes by three
accounts running the same rule while it fell 76%; the 20-minute same-token
cooldown had no memory of losses (8,774 cooldown rejections on that pool, then
a re-buy after each).

Streak rule: the pool's closes are ordered newest first by closed_at; losses
are counted until the first non-losing close (pnl_usd >= 0). A close whose
pnl_usd is unknown is neither a loss nor a win and does not break the streak
(conservative). Rows without a finite closed_at, mint or pool are ignored.
"""
import math

VERSION = 'POOL_LOSS_MEMORY_V1'
REASON = 'pool_loss_cooldown'
CONSECUTIVE_LOSSES = 2
COOLDOWN_MS = 6 * 60 * 60 * 1000


def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _pool(row):
    mint, pair = row.get('address'), row.get('pairAddress')
    if not isinstance(mint, str) or not isinstance(pair, str) or not mint.strip() or not pair.strip():
        return None
    return mint.strip(), pair.strip()


def index(history, now, *, consecutive_losses=CONSECUTIVE_LOSSES, cooldown_ms=COOLDOWN_MS) -> dict:
    """{(mint, pool): record} of every pool blocked at ``now``, from one ledger's closes."""
    current = _finite(now)
    if current is None:
        return {}
    closes = {}
    for row in history or ():
        if not isinstance(row, dict):
            continue
        key = _pool(row)
        closed = _finite(row.get('closed_at'))
        if key is None or closed is None or closed <= 0:
            continue
        closes.setdefault(key, []).append((closed, _finite(row.get('pnl_usd'))))
    blocked = {}
    for key, rows in closes.items():
        rows.sort(key=lambda item: item[0], reverse=True)
        streak, last_loss = 0, None
        for closed, pnl in rows:
            if pnl is None:
                continue
            if pnl >= 0:
                break
            streak += 1
            if last_loss is None:
                last_loss = closed
        if streak < consecutive_losses or last_loss is None:
            continue
        until = last_loss + cooldown_ms
        if current < until:
            blocked[key] = {'consecutive_losses': streak, 'last_loss_at': int(last_loss),
                            'blocked_until': int(until), 'remaining_ms': int(until - current)}
    return blocked


def check(coin, now, *, history=None, blocked=None) -> dict:
    """Decision for one candidate; pass a precomputed ``blocked`` index or a ``history``."""
    coin = coin if isinstance(coin, dict) else {}
    if blocked is None:
        blocked = index(history, now)
    key = _pool(coin)
    record = blocked.get(key) if key is not None else None
    result = {'version': VERSION, 'blocked': record is not None,
              'reasons': [REASON] if record is not None else [],
              'consecutive_losses': 0, 'last_loss_at': None, 'blocked_until': None, 'remaining_ms': 0}
    if record is not None:
        result.update(record)
    return result


def config() -> dict:
    return {'version': VERSION, 'reason': REASON, 'consecutive_losses': CONSECUTIVE_LOSSES,
            'cooldown_hours': COOLDOWN_MS / 3_600_000, 'scope': 'same (mint, pool) within one account or one Lab book',
            'cross_ledger_union': False,
            'loss_definition': 'net pnl_usd < 0 on a closed trade', 'unknown_pnl': 'skipped, does not break a streak',
            'source': 'existing closed history, read-only', 'ledger_schema_changed': False,
            'exits_changed': False, 'is_entry_authorization': False, 'profitability_proven': False}
