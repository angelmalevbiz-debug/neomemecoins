"""One-time PAPER-only promotion of qualifying Strategy Lab books.

Historical performance is archived first. The promoted strategies start on a
clean, separate $1,000 paper cohort so the selection sample cannot leak into
the funded-cohort performance statistics.
"""
import copy
import json
import math
import time
from pathlib import Path

from engine_runtime import atomic_json
from lab_dashboard_projection import compact_strategy_lab
from paper_state_reset import archive_files


PROMOTED_STRATEGIES = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')
TOTAL_CAPITAL_USD = 1000.0
ALLOCATION_PER_STRATEGY_USD = 250.0
MAX_POSITION_FRACTION = 0.25
MIN_HISTORY_TRADES = 10
MIN_NET_PNL_USD = 25.0
MIN_PROFIT_FACTOR = 1.0
EPISODE_GAP_MS = 30 * 60 * 1000
MIGRATION_VERSION = 'PROMOTED_PAPER_COHORT_V1'


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _closed_trades(book):
    return [row for row in book.get('history', [])
            if isinstance(row, dict) and row.get('closed_at')]


def historical_metrics(book):
    trades = _closed_trades(book)
    pnls = [_number(row.get('pnl_usd')) for row in trades]
    gross_profit = sum(max(0.0, pnl) for pnl in pnls)
    gross_loss = -sum(min(0.0, pnl) for pnl in pnls)
    return {
        'closed_trades': len(trades),
        'wins': sum(pnl > 0 for pnl in pnls),
        'losses': sum(pnl < 0 for pnl in pnls),
        'realized_net_pnl_usd': round(sum(pnls), 2),
        'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
    }


def unique_market_episodes(books):
    rows = []
    for strategy_id in PROMOTED_STRATEGIES:
        for trade in _closed_trades(books[strategy_id]):
            address = str(trade.get('address') or '')
            opened_at = int(_number(trade.get('opened_at')))
            if address and opened_at > 0:
                rows.append((address, opened_at))
    rows.sort()
    episodes = []
    for address, opened_at in rows:
        if not episodes or episodes[-1]['address'] != address or opened_at - episodes[-1]['last_opened_at'] > EPISODE_GAP_MS:
            episodes.append({'address': address, 'last_opened_at': opened_at})
        else:
            episodes[-1]['last_opened_at'] = opened_at
    return len(episodes)


def _clear_book(book, *, starting_balance, stamp, group, allocation, max_fraction, note):
    had_open_position = bool(book.get('position') or book.get('positions'))
    book.update(
        starting_balance=starting_balance,
        balance=starting_balance,
        position=None,
        positions=[],
        history=[],
        trade_seq=0,
        last_entry_by_address={},
        entry_diagnostics={},
        portfolio_group=group,
        allocation_usd=allocation,
        max_position_fraction=max_fraction,
        promotion_pending=False,
        created_at=stamp,
        last_reset={
            'at': stamp,
            'reason': note,
            'cancelled_open_position': had_open_position,
        },
    )
    return had_open_position


def begin_promotion_drain(root):
    """Freeze new promoted-candidate entries and reset losing test books.

    Existing promoted-candidate positions keep normal PAPER exit management.
    Once all have closed, call promote_strategy_lab to seed the $1,000 cohort.
    """
    root = Path(root).resolve()
    source = root / 'strategy_lab.json'
    if not source.is_file():
        raise FileNotFoundError('Strategy Lab state is missing; promotion drain refused')
    data = json.loads(source.read_text(encoding='utf-8'))
    books = data.get('books')
    if not isinstance(books, dict) or any(key not in books for key in PROMOTED_STRATEGIES):
        raise ValueError('Strategy Lab book schema is incomplete; promotion drain refused')
    previous_setup = data.get('portfolio_setup') or {}
    if previous_setup.get('status') == 'ACTIVE' and previous_setup.get('version') == MIGRATION_VERSION:
        raise ValueError('The funded PAPER cohort is already active')
    if previous_setup.get('status') == 'DRAINING' and previous_setup.get('version') == MIGRATION_VERSION:
        return {
            'status': 'DRAINING', 'pending_positions': [key for key in PROMOTED_STRATEGIES if books[key].get('position')],
            'archive': previous_setup.get('archive'),
            'losing_test_books_restarted': previous_setup.get('losing_test_books_restarted', []),
        }

    evidence = {key: historical_metrics(books[key]) for key in PROMOTED_STRATEGIES}
    for key, metrics in evidence.items():
        if metrics['closed_trades'] < MIN_HISTORY_TRADES:
            raise ValueError(f'{key} has too few closed trades for promotion')
        if metrics['realized_net_pnl_usd'] < MIN_NET_PNL_USD:
            raise ValueError(f'{key} no longer meets the $25 net PnL threshold')
        if metrics['profit_factor'] is None or metrics['profit_factor'] <= MIN_PROFIT_FACTOR:
            raise ValueError(f'{key} no longer meets the positive profit-factor threshold')

    stamp = int(time.time() * 1000)
    archive = archive_files(root, ['strategy_lab.json', 'strategy_lab_compact.json'])
    losing_ids = []
    cancelled_positions = []
    for key, book in books.items():
        if key in PROMOTED_STRATEGIES:
            continue
        start = _number(book.get('starting_balance'), 500.0)
        if _number(book.get('balance'), start) - start < -1e-9:
            losing_ids.append(key)
            if _clear_book(
                book, starting_balance=start, stamp=stamp, group='TEST', allocation=start,
                max_fraction=1.0,
                note='Losing PAPER test restarted; previous history preserved in archive',
            ):
                cancelled_positions.append(key)
        else:
            book['portfolio_group'] = 'TEST'
            book['allocation_usd'] = start
            book['max_position_fraction'] = 1.0

    for key in PROMOTED_STRATEGIES:
        book = books[key]
        book['promotion_pending'] = True
        book['portfolio_group'] = 'PROMOTION_DRAINING'
        book['allocation_usd'] = _number(book.get('starting_balance'), 500.0)
        book['max_position_fraction'] = 1.0

    data['portfolio_setup'] = {
        'version': MIGRATION_VERSION,
        'status': 'DRAINING',
        'group': 'PROMOTED_PAPER',
        'draining_since': stamp,
        'total_allocated_capital_usd': TOTAL_CAPITAL_USD,
        'allocation_per_strategy_usd': ALLOCATION_PER_STRATEGY_USD,
        'max_position_fraction': MAX_POSITION_FRACTION,
        'strategies': list(PROMOTED_STRATEGIES),
        'accounts_are_independent': True,
        'real_execution_enabled': False,
        'selection_thresholds': {
            'minimum_realized_net_pnl_usd': MIN_NET_PNL_USD,
            'minimum_closed_trades': MIN_HISTORY_TRADES,
            'minimum_profit_factor_exclusive': MIN_PROFIT_FACTOR,
        },
        'selection_evidence': evidence,
        'historical_simulations': sum(item['closed_trades'] for item in evidence.values()),
        'unique_market_episodes_30m': unique_market_episodes(books),
        'evidence_note': (
            'Historical outcomes are simulated, correlated across strategies, and include a known '
            'market-data integrity warning. They are selection evidence only, not proof of future returns.'
        ),
        'archive': archive.name,
        'losing_test_books_restarted': sorted(losing_ids),
        'cancelled_open_test_positions': sorted(cancelled_positions),
    }
    data['updated_at'] = stamp
    data['status'] = 'restarting'
    data['stats'] = {}
    atomic_json(source, data)
    atomic_json(root / 'strategy_lab_compact.json', compact_strategy_lab(data))
    return {
        'status': 'DRAINING',
        'pending_positions': [key for key in PROMOTED_STRATEGIES if books[key].get('position')],
        'archive': str(archive),
        'losing_test_books_restarted': sorted(losing_ids),
        'cancelled_open_test_positions': sorted(cancelled_positions),
        'historical_simulations': data['portfolio_setup']['historical_simulations'],
        'unique_market_episodes_30m': data['portfolio_setup']['unique_market_episodes_30m'],
    }


def promote_strategy_lab(root):
    """Archive state, re-seed qualifying books, and restart losing test books.

    The caller must stop the Strategy Lab writer before invoking this function.
    It intentionally does not touch the user's separate $1,000 PAPER account.
    Any selected open position is moved into an exit-only legacy PAPER book so
    the fresh $1,000 cohort can start without erasing or resizing that position.
    """
    root = Path(root).resolve()
    source = root / 'strategy_lab.json'
    if not source.is_file():
        raise FileNotFoundError('Strategy Lab state is missing; promotion refused')
    data = json.loads(source.read_text(encoding='utf-8'))
    books = data.get('books')
    if not isinstance(books, dict) or any(key not in books for key in PROMOTED_STRATEGIES):
        raise ValueError('Strategy Lab book schema is incomplete; promotion refused')
    previous_setup = data.get('portfolio_setup') or {}
    if previous_setup.get('version') == MIGRATION_VERSION and previous_setup.get('completed_at'):
        raise ValueError('Promotion already applied; refusing to reset the funded cohort again')

    evidence = previous_setup.get('selection_evidence') if previous_setup.get('status') == 'DRAINING' else None
    evidence = evidence or {key: historical_metrics(books[key]) for key in PROMOTED_STRATEGIES}
    for key, metrics in evidence.items():
        if metrics['closed_trades'] < MIN_HISTORY_TRADES:
            raise ValueError(f'{key} has too few closed trades for promotion')
        if metrics['realized_net_pnl_usd'] < MIN_NET_PNL_USD:
            raise ValueError(f'{key} no longer meets the $25 net PnL threshold')
        if metrics['profit_factor'] is None or metrics['profit_factor'] <= MIN_PROFIT_FACTOR:
            raise ValueError(f'{key} no longer meets the positive profit-factor threshold')
    unique_episodes = int(previous_setup.get('unique_market_episodes_30m')
                          or unique_market_episodes(books))
    historical_simulations = int(previous_setup.get('historical_simulations')
                                 or sum(item['closed_trades'] for item in evidence.values()))

    # Derive the loss cohort from the live book balances, not stale dashboard stats.
    prior_reset_ids = set(previous_setup.get('losing_test_books_restarted') or [])
    losing_ids = sorted(prior_reset_ids)
    for key, book in books.items():
        if key in PROMOTED_STRATEGIES:
            continue
        start = _number(book.get('starting_balance'), 500.0)
        if key not in prior_reset_ids and _number(book.get('balance'), start) - start < -1e-9:
            losing_ids.append(key)

    stamp = int(time.time() * 1000)
    archive = archive_files(root, ['strategy_lab.json', 'strategy_lab_compact.json'])
    archive_path = str(archive)

    legacy_draining_books = copy.deepcopy(previous_setup.get('legacy_draining_books') or {})
    transferred_positions = []
    for key in PROMOTED_STRATEGIES:
        old_book = books[key]
        position = old_book.get('position')
        if not position:
            continue
        legacy_draining_books[key] = {
            'id': f'LEGACY_{key}',
            'strategy_id': key,
            'name': f"{old_book.get('name', key)} · legacy PAPER exit",
            'portfolio_group': 'LEGACY_DRAIN',
            'starting_balance': _number(old_book.get('starting_balance'), 500.0),
            'balance': _number(old_book.get('balance'), 500.0),
            'position': copy.deepcopy(position),
            'history': [],
            'trade_seq': int(old_book.get('trade_seq', 0)),
            'last_entry_by_address': {},
            'created_at': stamp,
        }
        transferred_positions.append(key)

    # Keep every strategy balance independent. Promoted books begin a clean
    # measurement period with equal allocations; their old sample stays archived.
    for key in PROMOTED_STRATEGIES:
        _clear_book(
            books[key], starting_balance=ALLOCATION_PER_STRATEGY_USD, stamp=stamp,
            group='PROMOTED_PAPER', allocation=ALLOCATION_PER_STRATEGY_USD,
            max_fraction=MAX_POSITION_FRACTION,
            note='Fresh promoted PAPER cohort; selection history preserved in archive',
        )
        if key in transferred_positions:
            books[key]['last_reset']['cancelled_open_position'] = False
            books[key]['last_reset']['open_position_moved_to_legacy_drain'] = True

    cancelled_losing_positions = list(previous_setup.get('cancelled_open_test_positions') or [])
    newly_reset_losing_ids = set(losing_ids) - prior_reset_ids
    for key, book in books.items():
        if key in PROMOTED_STRATEGIES:
            continue
        start = _number(book.get('starting_balance'), 500.0)
        book['portfolio_group'] = 'TEST'
        book['allocation_usd'] = start
        book['max_position_fraction'] = 1.0
        if key in newly_reset_losing_ids:
            if _clear_book(
                book, starting_balance=start, stamp=stamp, group='TEST', allocation=start,
                max_fraction=1.0,
                note='Losing PAPER test restarted; previous history preserved in archive',
            ):
                cancelled_losing_positions.append(key)

    data['portfolio_setup'] = {
        'version': MIGRATION_VERSION,
        'status': 'ACTIVE',
        'group': 'PROMOTED_PAPER',
        'completed_at': stamp,
        'total_allocated_capital_usd': TOTAL_CAPITAL_USD,
        'allocation_per_strategy_usd': ALLOCATION_PER_STRATEGY_USD,
        'max_position_fraction': MAX_POSITION_FRACTION,
        'strategies': list(PROMOTED_STRATEGIES),
        'accounts_are_independent': True,
        'real_execution_enabled': False,
        'selection_thresholds': {
            'minimum_realized_net_pnl_usd': MIN_NET_PNL_USD,
            'minimum_closed_trades': MIN_HISTORY_TRADES,
            'minimum_profit_factor_exclusive': MIN_PROFIT_FACTOR,
        },
        'selection_evidence': evidence,
        'historical_simulations': historical_simulations,
        'unique_market_episodes_30m': unique_episodes,
        'evidence_note': (
            'Historical outcomes are simulated, correlated across strategies, and include a known '
            'market-data integrity warning. They are selection evidence only, not proof of future returns.'
        ),
        'archive': archive.name,
        'losing_test_books_restarted': sorted(losing_ids),
        'cancelled_open_test_positions': sorted(cancelled_losing_positions),
        'legacy_draining_books': legacy_draining_books,
        'legacy_open_positions': sorted(transferred_positions),
    }
    data['updated_at'] = stamp
    data['status'] = 'restarting'
    data['stats'] = {}
    atomic_json(source, data)
    atomic_json(root / 'strategy_lab_compact.json', compact_strategy_lab(data))

    if abs(sum(_number(books[key].get('starting_balance')) for key in PROMOTED_STRATEGIES) - TOTAL_CAPITAL_USD) > 1e-8:
        raise AssertionError('promoted PAPER allocations do not sum to $1,000')
    return {
        'archive': archive_path,
        'promoted_strategies': list(PROMOTED_STRATEGIES),
        'promoted_capital_usd': TOTAL_CAPITAL_USD,
        'allocation_per_strategy_usd': ALLOCATION_PER_STRATEGY_USD,
        'losing_test_books_restarted': sorted(losing_ids),
        'cancelled_open_test_positions': sorted(cancelled_losing_positions),
        'selection_evidence': copy.deepcopy(evidence),
        'historical_simulations': data['portfolio_setup']['historical_simulations'],
        'unique_market_episodes_30m': data['portfolio_setup']['unique_market_episodes_30m'],
        'legacy_open_positions': sorted(transferred_positions),
    }
