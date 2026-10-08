#!/usr/bin/env python3
"""Read-only forensics for STALE_MARKET_EXIT closes in a PAPER ledger (state.json).

For every closed trade the report shows the hold time, the recorded signal age at
entry, the age of the stored candidate snapshot at close, the previous closed trade
in the same mint/pool with the gap since its close, and whether the booked exit
price equals a price recorded by an earlier trade in the same pool. Monitor restarts
recorded in the ledger events are listed so closes can be related to deployments.
The ledger is only read; nothing is modified, reset or written.
"""
import argparse
import json
from datetime import datetime, timezone


def stamp(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime('%m-%d %H:%M:%S') if ms else '-'


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rows_for(history):
    ordered = sorted((t for t in history if isinstance(t, dict)), key=lambda t: int(t.get('opened_at') or 0))
    rows = []
    for index, trade in enumerate(ordered):
        key = (trade.get('address'), trade.get('pairAddress'))
        earlier = [u for u in ordered[:index] if (u.get('address'), u.get('pairAddress')) == key]
        previous = earlier[-1] if earlier else None
        snapshot = trade.get('coin_snapshot') or {}
        opened, closed = int(trade.get('opened_at') or 0), int(trade.get('closed_at') or 0)
        seen = {number(u.get(name)) for u in earlier for name in ('exit_price', 'current_price', 'peak_price')} - {None}
        snapshot_at = int(snapshot.get('updatedAt') or 0)
        rows.append({
            'trade_no': trade.get('trade_no'), 'symbol': str(trade.get('symbol') or '')[:8],
            'exit_reason': str(trade.get('exit_reason') or ''), 'opened': stamp(opened),
            'hold_s': (closed - opened) / 1000, 'signal_age_ms': number(trade.get('signal_age_at_entry_ms')),
            'snapshot_age_s': (closed - snapshot_at) / 1000 if snapshot_at else None,
            'previous_trade': previous.get('trade_no') if previous else None,
            'gap_min': (opened - int(previous.get('closed_at') or 0)) / 60000 if previous else None,
            'exit_price': number(trade.get('exit_price')),
            'price_seen_earlier': number(trade.get('exit_price')) in seen if earlier else False,
            'pnl_usd': number(trade.get('pnl_usd')) or 0.0,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description='Read-only STALE_MARKET_EXIT forensics for a PAPER state.json.')
    parser.add_argument('state', help='path to a PAPER ledger state.json (live or archived)')
    parser.add_argument('--all', action='store_true', help='list every closed trade, not only STALE_MARKET_EXIT')
    args = parser.parse_args()
    with open(args.state, encoding='utf-8') as handle:
        data = json.load(handle)
    rows = rows_for(data.get('history', []))
    shown = rows if args.all else [r for r in rows if r['exit_reason'] == 'STALE_MARKET_EXIT']
    print(f"session {data.get('demo_session_id')} closed_trades {len(rows)}")
    print('trade symbol   exit_reason            opened_utc      hold_s sig_age_ms snap_age_s prev gap_min exit_price   seen_earlier pnl_usd')
    for r in shown:
        cell = lambda value, spec: format(value, spec) if value is not None else '-'
        print(f"#{r['trade_no']!s:>3} {r['symbol']:<8} {r['exit_reason']:<22} {r['opened']:<15} {r['hold_s']:>6.1f} "
              f"{cell(r['signal_age_ms'], '.0f'):>10} {cell(r['snapshot_age_s'], '.1f'):>10} "
              f"{('#%s' % r['previous_trade']) if r['previous_trade'] is not None else '-':>4} "
              f"{cell(r['gap_min'], '.1f'):>7} {r['exit_price']!s:<12} "
              f"{str(r['price_seen_earlier']):<12} {r['pnl_usd']:>8.2f}")
    stale = [r for r in rows if r['exit_reason'] == 'STALE_MARKET_EXIT']
    reentries = [r for r in rows if r['previous_trade'] is not None]
    print(f"stale_closes {len(stale)} stale_pnl_usd {sum(r['pnl_usd'] for r in stale):.2f} "
          f"stale_reentries {sum(1 for r in stale if r['previous_trade'] is not None)} "
          f"stale_price_seen_earlier {sum(1 for r in stale if r['price_seen_earlier'])} "
          f"same_pool_reentries {len(reentries)}")
    if stale:
        print(f"stale_hold_s {min(r['hold_s'] for r in stale):.1f}-{max(r['hold_s'] for r in stale):.1f} "
              f"stale_snapshot_age_s {min(r['snapshot_age_s'] or 0 for r in stale):.1f}-{max(r['snapshot_age_s'] or 0 for r in stale):.1f} "
              f"stale_gap_min {min(r['gap_min'] or 0 for r in stale):.1f}-{max(r['gap_min'] or 0 for r in stale):.1f}")
    restarts = [stamp(e.get('ts')) for e in data.get('events', []) if isinstance(e, dict) and 'monitor started' in str(e.get('text', ''))]
    print('monitor_restarts', ', '.join(sorted(restarts)) or '-')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
