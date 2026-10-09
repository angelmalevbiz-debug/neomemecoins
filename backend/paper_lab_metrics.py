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
# LAB_FORWARD_CONTROL_CONTINUITY_V1: a random control's close past its funding.
# It is a per-trade measurement whose P&L never moved the book's balance.
ZERO_CAPITAL_MODE = 'zero_capital_control'


def balance_effect(row, pnl):
    """What a close did to the book's balance: 0 for a zero-capital control close, else its P&L."""
    if row.get('capital_mode') == ZERO_CAPITAL_MODE:
        return 0.0
    effect = finite(row.get('balance_effect_usd'))
    return pnl if effect is None else effect


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
    # The ledger's equity path moves only by what each close did to the balance: a
    # zero-capital control close (LAB_FORWARD_CONTROL_CONTINUITY_V1) never moved it.
    effects = [balance_effect(row, pnl) for _, _, pnl, row in trades]
    zero_capital = [pnl for _, _, pnl, row in trades if row.get('capital_mode') == ZERO_CAPITAL_MODE]
    for effect in effects:
        equity += effect
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
        # Balance-moving P&L only; zero-capital control closes are reported separately below.
        'realized_net_pnl_usd': round(sum(effects), 6),
        'realized_net_pnl_basis': 'balance effect of each close (zero-capital control closes excluded)',
        'funded_closed_trades': count - len(zero_capital),
        'zero_capital_closes': len(zero_capital),
        'zero_capital_pnl_usd': round(sum(zero_capital), 6),
        # Per-trade measurement: every close, including zero-capital control closes.
        'net_expectancy_usd_per_close': round(sum(pnls) / count, 6) if count else None,
        'net_expectancy_basis': 'every close, including zero-capital control measurements',
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


HF_VERSION = 'PAPER_LAB_HF_MEASUREMENTS_V1'
HF_ROW_KINDS = ('order', 'fill', 'cancel', 'close', 'cap', 'session', 'retire')


def read_hf_journal(directory):
    """Rows of every LAB_HIGH_FREQUENCY_V1 journal file under ``directory`` (read only).

    ``directory`` is strategy_lab_hf/ or its journal/ folder; files under an ``archive``
    folder (earlier reset sessions) are skipped. Unparseable lines are counted, not raised.
    """
    from pathlib import Path
    import json
    root = Path(directory)
    rows, malformed = [], 0
    for path in sorted(root.rglob('*.jsonl')):
        if 'archive' in path.relative_to(root).parts:
            continue
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                rows.append(row)
    return rows, malformed


def measure_hf_rows(rows, *, as_of=None, started_at=None):
    """Measure LAB_HIGH_FREQUENCY_V1 journal rows per (book, config hash); read only.

    A book holds up to three positions at once, so throughput counts every order
    (``opened_per_hour``) and close, deduplicated by (book, cfg, seq), and the
    most slots held at the same time is reported. Rates describe the supplied
    window only; nothing here is a profitability claim.
    """
    rejected = {'malformed': 0, 'duplicate': 0, 'future': 0}
    seen = set()
    groups = {}
    clean = []
    limit = finite(as_of)
    for row in rows or ():
        if (not isinstance(row, dict) or row.get('kind') not in HF_ROW_KINDS
                or not isinstance(row.get('seq'), int) or isinstance(row.get('seq'), bool)
                or not isinstance(row.get('book'), str) or not isinstance(row.get('cfg'), str)
                or finite(row.get('at')) is None):
            rejected['malformed'] += 1
            continue
        key = (row['book'], row['cfg'], row['seq'])
        if key in seen:
            rejected['duplicate'] += 1
            continue
        seen.add(key)
        if limit is not None and finite(row['at']) > limit:
            rejected['future'] += 1
            continue
        clean.append(row)
    clean.sort(key=lambda item: (finite(item['at']), item['seq']))
    for row in clean:
        group = groups.setdefault((row['book'], row['cfg']), {
            'book': row['book'], 'config_hash': row['cfg'], 'orders': 0, 'fills': 0, 'closes': 0, 'cancels': {},
            'booked_usd': 0.0, 'net50_usd': 0.0, 'net0_usd': 0.0, 'wins_booked': 0, 'wins_net50': 0,
            'first_at': None, 'last_at': None, 'open': set(), 'max_concurrent': 0, 'close_kinds': {},
            'cap_trips': 0, 'retired': None})
        at = finite(row['at'])
        group['first_at'] = at if group['first_at'] is None else group['first_at']
        group['last_at'] = at
        kind = row['kind']
        trade = row.get('trade_no')
        if kind == 'order' and row.get('side') == 'entry':
            group['orders'] += 1
            group['open'].add(trade)
            group['max_concurrent'] = max(group['max_concurrent'], len(group['open']))
        elif kind == 'fill':
            group['fills'] += 1
        elif kind == 'cancel':
            reason = str(row.get('reason'))
            group['cancels'][reason] = group['cancels'].get(reason, 0) + 1
            group['open'].discard(trade)
        elif kind == 'close':
            group['closes'] += 1
            group['open'].discard(trade)
            booked = finite(row.get('pnl_usd')) or 0.0
            net50 = finite(row.get('net50_usd')) or 0.0
            group['booked_usd'] += booked
            group['net50_usd'] += net50
            group['net0_usd'] += finite(row.get('net0_usd')) or 0.0
            group['wins_booked'] += int(booked > 0)
            group['wins_net50'] += int(net50 > 0)
            label = str(row.get('close_kind'))
            group['close_kinds'][label] = group['close_kinds'].get(label, 0) + 1
        elif kind == 'cap':
            group['cap_trips'] += 1
        elif kind == 'retire':
            group['retired'] = row.get('reason')
    books = {}
    for (book, cfg), group in sorted(groups.items()):
        start = finite(started_at) if finite(started_at) is not None else group['first_at']
        end = limit if limit is not None else group['last_at']
        hours = max(0.0, (end - start) / 3_600_000) if start is not None and end is not None else 0.0
        closes = group['closes']
        books[f'{book}:{cfg[:12]}'] = {
            'book': book, 'config_hash': cfg, 'orders': group['orders'], 'fills': group['fills'],
            'closes': closes, 'cancels': group['cancels'], 'close_kinds': group['close_kinds'],
            'open_at_end': len(group['open']), 'max_concurrent_slots': group['max_concurrent'],
            'window_hours': round(hours, 6),
            'opened_per_hour': round(group['orders'] / hours, 6) if hours > 0 else None,
            'closed_per_hour': round(closes / hours, 6) if hours > 0 else None,
            'booked_usd': round(group['booked_usd'], 6), 'net50_usd': round(group['net50_usd'], 6),
            'net0_usd': round(group['net0_usd'], 6),
            'booked_usd_per_close': round(group['booked_usd'] / closes, 6) if closes else None,
            'net50_usd_per_close': round(group['net50_usd'] / closes, 6) if closes else None,
            'win_rate_booked_pct': round(100 * group['wins_booked'] / closes, 3) if closes else None,
            'win_rate_net50_pct': round(100 * group['wins_net50'] / closes, 3) if closes else None,
            'cap_trips': group['cap_trips'], 'retired': group['retired'],
        }
    return {
        'version': HF_VERSION, 'paper_only': True, 'read_only': True, 'books': books, 'rejected_rows': rejected,
        'limitations': [
            'PAPER fills at the next DexScreener refresh with modeled costs; not executable quotes.',
            'Three slots per book: opened_per_hour counts every order, including cancelled ones.',
            'Rates describe the supplied window only; nothing here is a profitability claim.',
        ],
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
            ('Zero-capital control closes count in per-trade statistics (trades, wins, expectancy) but never '
             'in realized P&L or the equity path: they did not move the balance.'),
            'Hourly and 24h normalized rates describe the supplied window, not expected future activity.',
            'An observed target is descriptive, not a promotion gate or a guarantee.',
            'Independent books are measured separately; their capital and trades are never pooled.',
        ],
    }
