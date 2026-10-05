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
                for field in ('symbol', 'address', 'strategy_id', 'opened_at', 'pnl_pct', 'notional_usd')
            }
        else:
            position = None
        books[key] = {
            'id': raw.get('id', key),
            'name': raw.get('name', key),
            'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0),
            'position': position,
            'history': [],
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
