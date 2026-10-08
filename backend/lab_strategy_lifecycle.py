"""Retire repeatedly losing PAPER research entries while retaining their ledgers.

This is a conservative operational filter on observed modeled results. Repeated
trades can share market episodes; the score is not a profitability prediction.
"""
import math


VERSION = 'LAB_STRATEGY_LIFECYCLE_V1'
MIN_CLOSED_TRADES = 12
MIN_ALL_LOSS_TRADES = 9
MAX_WIN_RATE_UPPER_BOUND = 0.5
Z_95 = 1.959963984540054


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _wilson_upper(wins, count):
    if count <= 0:
        return None
    fraction = wins / count
    z2 = Z_95 * Z_95
    return ((fraction + z2 / (2 * count)
             + Z_95 * math.sqrt(fraction * (1 - fraction) / count + z2 / (4 * count * count)))
            / (1 + z2 / count))


def _sum_finite(values):
    try:
        result = math.fsum(values)
        return result if math.isfinite(result) else None
    except (OverflowError, ValueError):
        return None


def observed_evidence(book, *, activity_version, execution_version, now):
    """Count unique, valid closes in one known price/execution/entry regime."""
    unique = {}
    conflicts = set()
    excluded = 0
    rows = book.get('history') or []
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            excluded += 1
            continue
        trade_no = _finite(row.get('trade_no'))
        opened = _finite(row.get('opened_at'))
        closed = _finite(row.get('closed_at'))
        pnl = _finite(row.get('pnl_usd'))
        price = row.get('price_crosscheck') or {}
        mint = row.get('address')
        pair = row.get('pairAddress')
        quote_status = row.get('quote_status')
        if (trade_no is None or trade_no <= 0 or not trade_no.is_integer()
                or opened is None or closed is None or not 0 < opened <= closed <= now
                or pnl is None or row.get('strategy_id') != book.get('id')
                or not isinstance(mint, str) or not mint.strip()
                or not isinstance(pair, str) or not pair.strip()
                or row.get('entry_policy_version') != activity_version
                or row.get('execution_mode') != execution_version
                or not isinstance(price, dict) or price.get('status') != 'pass'
                or price.get('mint') != mint or price.get('pair') != pair
                or row.get('audit_pending') or row.get('quality_flags')
                or (quote_status is not None and not isinstance(quote_status, str))
                or quote_status in {'stale', 'unavailable'}
                or row.get('quote_unavailable_reason')):
            excluded += 1
            continue
        identity = int(trade_no)
        value = (closed, opened, pnl, mint, pair)
        if identity in conflicts:
            excluded += 1
        elif identity in unique:
            excluded += 1
            if unique[identity] != value:
                del unique[identity]
                conflicts.add(identity)
                excluded += 1
        else:
            unique[identity] = value
    trades = sorted(unique.values())
    pnls = [trade[2] for trade in trades]
    count = len(pnls)
    split = count // 2
    wins = sum(pnl > 0 for pnl in pnls)
    total = _sum_finite(pnls)
    first = _sum_finite(pnls[:split])
    second = _sum_finite(pnls[split:])
    upper = _wilson_upper(wins, count)
    all_losses = bool(count >= MIN_ALL_LOSS_TRADES and all(pnl < 0 for pnl in pnls))
    repeated_losses = ((count >= MIN_CLOSED_TRADES or all_losses)
                       and all(value is not None and value < 0 for value in (total, first, second))
                       and upper is not None and upper <= MAX_WIN_RATE_UPPER_BOUND)
    return {
        'closed_trades': count, 'wins': wins, 'losses': sum(pnl < 0 for pnl in pnls),
        'breakeven': sum(pnl == 0 for pnl in pnls), 'excluded_rows': excluded,
        'net_pnl_usd': round(total, 4) if total is not None else None,
        'first_half_net_pnl_usd': round(first, 4) if first is not None else None,
        'second_half_net_pnl_usd': round(second, 4) if second is not None else None,
        'win_rate_upper_bound_95': round(upper, 6) if upper is not None else None,
        'activity_version': activity_version, 'execution_version': execution_version,
        'first_closed_at': int(trades[0][0]) if trades else None,
        'last_closed_at': int(trades[-1][0]) if trades else None,
        'repeated_losses': repeated_losses,
        'all_loss_sample': all_losses,
        'evidence_note': 'Observed PAPER results may share market episodes. This retirement heuristic does not prove future losses or profitability.',
    }


def entry_enabled(book):
    marker = book.get('strategy_lifecycle') or {}
    return not (isinstance(marker, dict) and marker.get('status') == 'retired')


def accepted_activity_version(key, activity_version, activity_versions=None):
    """Entry-policy version whose closes count as evidence for one book.

    Books that stamp their own entry policy (for example the COST_FIRST pair)
    are compared against that version; every other book uses the shared one.
    """
    if isinstance(activity_versions, dict):
        version = activity_versions.get(key)
        if isinstance(version, str) and version:
            return version
    return activity_version


def apply_lifecycle(books, *, registered_ids, promoted_ids, activity_version,
                    execution_version, now, activity_versions=None):
    """Annotate entries; never alter money, trades, positions or registration.

    A retirement remains in place after restart and does not expire on a timer.
    Re-enabling it requires an explicit reviewed policy change. activity_versions
    maps a book id to the entry-policy version its own closes carry, so a book
    with its own policy is judged by exactly the same rules on its own rows.
    """
    retired = []
    active = []
    draining = []
    for key in sorted(registered_ids):
        book = books.get(key)
        if not isinstance(book, dict):
            continue
        previous = book.get('strategy_lifecycle') or {}
        if isinstance(previous, dict) and previous.get('status') == 'retired':
            # Preserve the original retirement evidence, even after a final exit.
            marker = {**previous, 'entry_enabled': False, 'position_management_enabled': True}
        elif key in promoted_ids or book.get('portfolio_group', 'TEST') != 'TEST':
            active.append(key)
            continue
        else:
            evidence = observed_evidence(
                book, activity_version=accepted_activity_version(key, activity_version, activity_versions),
                execution_version=execution_version, now=now)
            rejected = evidence['repeated_losses']
            marker = {'version': VERSION, 'status': 'retired' if rejected else 'active',
                      'entry_enabled': not rejected, 'position_management_enabled': True,
                      'reason': ('repeated_observed_paper_losses' if rejected
                                 else 'retirement_threshold_not_met'),
                      'evidence': evidence}
            if rejected:
                marker['retired_at'] = now
        book['strategy_lifecycle'] = marker
        if marker['status'] == 'retired':
            retired.append(key)
            if book.get('position'):
                draining.append(key)
        else:
            active.append(key)
    return {
        'version': VERSION, 'retired_strategy_ids': retired,
        'retired_open_position_ids': draining, 'active_registered_strategy_ids': active,
        'policy': {'minimum_unique_closed_trades': MIN_CLOSED_TRADES,
                   'minimum_unique_closed_trades_if_all_losses': MIN_ALL_LOSS_TRADES,
                   'maximum_win_rate_upper_bound_95': MAX_WIN_RATE_UPPER_BOUND,
                   'negative_total_and_both_chronological_halves_required': True,
                   'activity_version': activity_version,
                   'activity_versions_by_strategy': {
                       key: version for key, version in sorted((activity_versions or {}).items())
                       if key in registered_ids and isinstance(version, str) and version},
                   'execution_version': execution_version,
                   'promoted_cohort_exempt': True, 'automatic_reactivation': False},
    }
