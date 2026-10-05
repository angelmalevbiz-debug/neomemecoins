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
            'position': position,
            'history': history,
        }

    result = {
        'started_at': data.get('started_at'),
        'updated_at': data.get('updated_at'),
        'status': data.get('status', 'offline'),
        'books': books,
        'stats': data.get('stats') or {},
        'data_integrity_note': data.get('data_integrity_note'),
        'activity_config': data.get('activity_config') or {},
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
