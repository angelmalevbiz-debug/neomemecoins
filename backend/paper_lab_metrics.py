"""Read-only measurements of complete Strategy Lab PAPER ledgers.

Reported rates describe the supplied window, never future throughput. The
binomial interval is descriptive: shared tokens and market days can correlate.
"""
from datetime import datetime, timezone
import math

VERSION = 'PAPER_LAB_MEASUREMENTS_V1'
MIN_TRADES = 100
MIN_DAYS = 5
EPISODE_MS = 30 * 60 * 1000


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def wilson_interval(wins, total):
    if total <= 0:
        return None
    z = 1.959963984540054
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0, center - radius) * 100, 3),
            round(min(1, center + radius) * 100, 3)]


def _trade_key(row, opened, closed):
    # A Lab book has at most one position. A full mint/pool/time identity is
    # safer than trade_no, which can restart in a fresh PAPER session.
    return (row.get('address'), row.get('pairAddress'),
            opened, closed)


def measure_book(book, *, started_at, as_of, advertised_count=None, target=80.0):
    reset = book.get('last_reset') or {}
    starts = [finite(started_at), finite(book.get('created_at')), finite(reset.get('at'))]
    start = max([value for value in starts if value is not None and value > 0], default=as_of)
    history = book.get('history') or []
    if not isinstance(history, list):
        raise ValueError('Book history must be an array')
    rejected = {'malformed': 0, 'outside_session': 0, 'future': 0, 'duplicate': 0}
    seen = set()
    trades = []
    for row in history:
        if not isinstance(row, dict):
            rejected['malformed'] += 1
            continue
        opened = finite(row.get('opened_at'))
        closed = finite(row.get('closed_at'))
        pnl = finite(row.get('pnl_usd'))
        if (opened is None or closed is None or pnl is None or opened <= 0
                or closed < opened or not isinstance(row.get('address'), str) or not row['address']
                or not isinstance(row.get('pairAddress'), str) or not row['pairAddress']):
            rejected['malformed'] += 1
            continue
        if closed > as_of or opened > as_of:
            rejected['future'] += 1
            continue
        if opened < start:
            rejected['outside_session'] += 1
            continue
        key = _trade_key(row, opened, closed)
        if key in seen:
            rejected['duplicate'] += 1
            continue
        seen.add(key)
        trades.append((closed, opened, pnl, row))
    trades.sort(key=lambda item: (item[0], item[1]))
    pnls = [item[2] for item in trades]
    wins = sum(pnl > 0 for pnl in pnls)
    losses = sum(pnl < 0 for pnl in pnls)
    gross_profit = sum(max(0, pnl) for pnl in pnls)
    gross_loss = -sum(min(0, pnl) for pnl in pnls)
    count = len(trades)
    interval = wilson_interval(wins, count)
    as_of_date = datetime.fromtimestamp(as_of / 1000, timezone.utc).date()
    days = {datetime.fromtimestamp(item[0] / 1000, timezone.utc).date() for item in trades}
    completed_days = {day for day in days if day < as_of_date}
    episodes = {}
    for _, opened, _, row in sorted(trades, key=lambda item: item[1]):
        key = (row['address'], row['pairAddress'])
        episodes.setdefault(key, [])
        if not episodes[key] or opened - episodes[key][-1] > EPISODE_MS:
            episodes[key].append(opened)
    elapsed_hours = max(0, as_of - start) / 3_600_000
    opened_count = count
    position = book.get('position')
    if isinstance(position, dict):
        opened = finite(position.get('opened_at'))
        if opened is not None and start <= opened <= as_of:
            opened_count += 1
    capital = finite(book.get('starting_balance'))
    equity = peak = capital if capital is not None and capital > 0 else 0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak * 100)
    supplied_count = finite(advertised_count)
    truncated = supplied_count is not None and supplied_count > len(history)
    complete = not truncated and not any(rejected.values())
    sufficient = complete and count >= MIN_TRADES and len(completed_days) >= MIN_DAYS
    win_rate = wins / count * 100 if count else None
    target_observed = bool(sufficient and win_rate >= target and sum(pnls) > 0
                           and (gross_loss == 0 or gross_profit > gross_loss))
    return {
        'name': book.get('name', book.get('id', 'unknown')),
        'portfolio_group': book.get('portfolio_group', 'TEST'),
        'window_start_ms': int(start), 'as_of_ms': int(as_of),
        'elapsed_hours': round(elapsed_hours, 6),
        'closed_trades': count, 'opened_trades': opened_count,
        'wins': wins, 'losses': losses, 'breakeven': count - wins - losses,
        'win_rate_pct': round(win_rate, 3) if win_rate is not None else None,
        'win_rate_wilson_95_pct': interval,
        'target_win_rate_pct': target,
        'target_observed_in_sample': target_observed,
        'win_rate_interval_lower_meets_target': bool(sufficient and interval and interval[0] >= target),
        'realized_net_pnl_usd': round(sum(pnls), 6),
        'net_expectancy_usd_per_close': round(sum(pnls) / count, 6) if count else None,
        'profit_factor': round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        'profit_factor_status': ('finite' if gross_loss > 0 else
                                 'no_losses' if gross_profit > 0 else 'no_results'),
        'opened_per_hour': round(opened_count / elapsed_hours, 6) if elapsed_hours > 0 else None,
        'closed_per_hour': round(count / elapsed_hours, 6) if elapsed_hours > 0 else None,
        'closed_per_24h_normalized': round(count / elapsed_hours * 24, 6) if elapsed_hours > 0 else None,
        'closed_ledger_drawdown_pct': round(drawdown, 6) if capital and capital > 0 else None,
        'completed_utc_days': len(completed_days),
        'unique_mint_pool_30m_episodes': sum(len(values) for values in episodes.values()),
        'history_truncated': truncated, 'rejected_rows': rejected,
        'evidence_status': ('INCOMPLETE_LEDGER' if not complete else
                            'INSUFFICIENT_SAMPLE' if not sufficient else
                            'TARGET_OBSERVED_IN_SAMPLE' if target_observed else 'TARGET_NOT_MET'),
    }


def evaluate_lab(state, *, target=80.0):
    if not isinstance(state, dict) or not isinstance(state.get('books'), dict):
        raise ValueError('Expected full Strategy Lab state with books')
    target = finite(target)
    as_of = finite(state.get('updated_at'))
    started = finite(state.get('started_at'))
    if target is None or not 0 < target < 100:
        raise ValueError('Target must be between 0 and 100 percent')
    if as_of is None or started is None or started <= 0 or as_of < started:
        raise ValueError('Expected valid started_at and updated_at timestamps')
    books = {}
    for key, book in state['books'].items():
        if not isinstance(book, dict):
            raise ValueError('Each book must be an object')
        stats = (state.get('stats') or {}).get(key) or {}
        books[key] = measure_book(book, started_at=started, as_of=as_of,
                                  advertised_count=stats.get('trades'), target=target)
    return {
        'version': VERSION, 'paper_only': True, 'read_only': True,
        'minimum_closed_trades': MIN_TRADES, 'minimum_completed_utc_days': MIN_DAYS,
        'books': books,
        'limitations': [
            'PAPER executions are modeled; this report does not certify real fills or profitability.',
            'Wilson intervals assume independent outcomes; shared tokens and market days can correlate.',
            'Closed-ledger drawdown excludes open-position and intratrade losses.',
            'Hourly and 24h normalized rates describe the supplied window, not expected future activity.',
            'An observed target is descriptive, not a promotion gate or a guarantee.',
            'Independent books are measured separately; their capital and trades are never pooled.',
        ],
    }
