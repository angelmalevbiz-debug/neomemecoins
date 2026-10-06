"""Small read-only dashboard projection of Strategy Lab state."""

def compact_strategy_lab(data):
    if not isinstance(data, dict):
        return {'status': 'offline', 'books': {}, 'stats': {}}

    books = {}
    for key, raw in (data.get('books') or {}).items():
        if not isinstance(raw, dict):
            continue
        position = raw.get('position')
        if isinstance(position, dict):
            position = {
                field: position.get(field)
                for field in ('symbol', 'address', 'pairAddress', 'strategy_id', 'opened_at', 'pnl_pct',
                              'open_pnl_usd', 'notional_usd', 'entry_price', 'execution_entry_price',
                              'current_price', 'entry_dex_fee_usd', 'entry_network_fee_usd',
                              'estimated_exit_fee_usd', 'estimated_exit_impact_pct', 'execution_mode')
            }
        else:
            position = None
        history = []
        # Strategy Lab stores history newest first, so keep the first 30 rows.
        for trade in (raw.get('history') or [])[:30]:
            if not isinstance(trade, dict):
                continue
            history.append({
                field: trade.get(field)
                for field in ('trade_no', 'strategy_id', 'symbol', 'name', 'address', 'pairAddress',
                              'entry_price', 'execution_entry_price', 'exit_price', 'execution_exit_price',
                              'notional_usd', 'opened_at', 'closed_at', 'score', 'pnl_pct', 'pnl_usd',
                              'balance_before', 'balance_after', 'exit_reason', 'execution_mode',
                              'entry_dex_fee_usd', 'exit_dex_fee_usd', 'entry_network_fee_usd',
                              'exit_network_fee_usd', 'entry_price_impact_pct', 'exit_price_impact_pct',
                              'entry_slippage_pct', 'exit_slippage_pct')
            })
        books[key] = {
            'id': raw.get('id', key),
            'name': raw.get('name', key),
            'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0),
            'portfolio_group': raw.get('portfolio_group', 'TEST'),
            'allocation_usd': raw.get('allocation_usd', raw.get('starting_balance', 0)),
            'max_position_fraction': raw.get('max_position_fraction', 1.0),
            'position': position,
            'history': history,
        }

    raw_setup = data.get('portfolio_setup') or {}
    setup_fields = (
        'version', 'status', 'group', 'completed_at', 'draining_since',
        'total_allocated_capital_usd', 'allocation_per_strategy_usd', 'max_position_fraction',
        'strategies', 'accounts_are_independent', 'real_execution_enabled',
        'selection_thresholds', 'selection_evidence', 'historical_simulations',
        'unique_market_episodes_30m', 'evidence_note', 'losing_test_books_restarted',
        'cancelled_open_test_positions', 'legacy_open_positions', 'promotion_error',
    )
    portfolio_setup = {field: raw_setup[field] for field in setup_fields if field in raw_setup}
    legacy_books = {}
    for key, raw in (raw_setup.get('legacy_draining_books') or {}).items():
        if not isinstance(raw, dict):
            continue
        position = raw.get('position')
        if isinstance(position, dict):
            position = {
                field: position.get(field)
                for field in ('symbol', 'address', 'pairAddress', 'strategy_id', 'opened_at',
                              'pnl_pct', 'open_pnl_usd', 'notional_usd', 'entry_price',
                              'execution_entry_price', 'current_price', 'execution_mode')
            }
        else:
            position = None
        history = []
        for trade in (raw.get('history') or [])[-10:]:
            if not isinstance(trade, dict):
                continue
            history.append({
                field: trade.get(field)
                for field in ('trade_no', 'strategy_id', 'symbol', 'address', 'pairAddress',
                              'opened_at', 'closed_at', 'pnl_usd', 'pnl_pct', 'exit_reason',
                              'execution_mode')
            })
        legacy_books[key] = {
            'id': raw.get('id', key), 'strategy_id': raw.get('strategy_id', key),
            'name': raw.get('name', key), 'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0), 'position': position, 'history': history,
        }
    if legacy_books:
        portfolio_setup['legacy_draining_books'] = legacy_books
        portfolio_setup['legacy_open_position_count'] = sum(bool(book.get('position')) for book in legacy_books.values())

    result = {
        'started_at': data.get('started_at'),
        'updated_at': data.get('updated_at'),
        'status': data.get('status', 'offline'),
        'books': books,
        'stats': data.get('stats') or {},
        'data_integrity_note': data.get('data_integrity_note'),
        'activity_config': data.get('activity_config') or {},
        'portfolio_setup': portfolio_setup,
    }

    astra = data.get('astra')
    if isinstance(astra, dict):
        astra_view = {key: value for key, value in astra.items() if key != 'book'}
        book = astra.get('book')
        if isinstance(book, dict):
            astra_view['book'] = {
                'balance': book.get('balance', 0),
                'starting_balance': book.get('starting_balance', 0),
                'positions': book.get('positions') or [],
                'history': (book.get('history') or [])[:30],
            }
        result['astra'] = astra_view

    paired = data.get('paired')
    if isinstance(paired, dict):
        result['paired'] = paired

    return result
