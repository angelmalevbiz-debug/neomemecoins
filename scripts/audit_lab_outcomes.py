#!/usr/bin/env python3
"""Read-only PAPER ledger forensics; no replay, hypothetical fills or tuning.

Closed net results, open marks and admission/exit cohorts stay separate.
An observed peak is not an executable alternative exit or a future forecast.
"""
import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

BOOKS = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def median(values):
    rows = [n for v in values if (n := number(v)) is not None]
    return round(statistics.median(rows), 6) if rows else None


def summarize(rows):
    priced = [r for r in rows if number(r.get('pnl_usd')) is not None]
    wins = [r for r in priced if number(r['pnl_usd']) > 0]
    losses = [r for r in priced if number(r['pnl_usd']) < 0]
    gross_win = sum(number(r['pnl_usd']) for r in wins)
    gross_loss = -sum(number(r['pnl_usd']) for r in losses)
    result = dict(closes=len(rows), unpriced=len(rows)-len(priced), wins=len(wins), losses=len(losses),
                  net_usd=round(gross_win-gross_loss, 4),
                  win_rate_pct=round(100*len(wins)/len(priced), 2) if priced else None,
                  profit_factor=round(gross_win/gross_loss, 6) if gross_loss else None,
                  mean_win_usd=round(gross_win/len(wins), 6) if wins else None,
                  mean_loss_usd=round(gross_loss/len(losses), 6) if losses else None,
                  unique_pools=len({r.get('pairAddress') for r in rows if r.get('pairAddress')}),
                  reasons=dict(Counter(r.get('exit_reason', 'UNKNOWN') for r in rows)))
    for label, subset in [('winners', wins), ('losers', losses)]:
        result[label] = dict(
            median_entry_roundtrip_pct=median(r.get('entry_roundtrip_pnl_pct') for r in subset),
            median_net_pct=median(r.get('pnl_pct') for r in subset),
            median_liquidity_usd=median((r.get('entry_features') or {}).get('liq') for r in subset),
            median_hold_minutes=median((number(r.get('closed_at'))-number(r.get('opened_at')))/60_000
                for r in subset if number(r.get('closed_at')) is not None and number(r.get('opened_at')) is not None),
            recorded_peak_net_ge_2=sum((number(r.get('peak_net_pct')) or 0) >= 2 for r in subset),
            recorded_peak_net_ge_30=sum((number(r.get('peak_net_pct')) or 0) >= 30 for r in subset),
            peak_net_recorded=sum(number(r.get('peak_net_pct')) is not None for r in subset),
            confirmed_entry_flow_recorded=sum(isinstance(r.get('verified_entry_flow'), dict) for r in subset))
    return result


def audit(ledger):
    books = ledger.get('books') or {}
    rows = [r for sid in BOOKS for r in (books.get(sid) or {}).get('history', [])]
    policy = defaultdict(list)
    for row in rows:
        policy[row.get('entry_policy_version', 'LEGACY_UNKNOWN')].append(row)
    test = [r for r in rows if r.get('capacity_test') is True]
    def shadow_pass(row):
        s = row.get('capacity_test_shadow') or {}
        return bool(s.get('strategy_market_signal') is True and
                    (s.get('flow_admission') or {}).get('allow') is True and
                    (s.get('defensive_entry') or {}).get('allowed') is True and
                    s.get('strategy_roundtrip_cost_admitted') is True and
                    s.get('recent_loss_pause_ms') == 0 and s.get('address_cooldown_ms') == 0 and
                    s.get('capacity_risk_block') is None)
    shadow = dict(strategy_signal_pass=0, flow_pass=0, defense_pass=0, cost_pass=0, all_gates_pass=0)
    defense_reasons = Counter()
    for row in test:
        s = row.get('capacity_test_shadow') or {}
        shadow['strategy_signal_pass'] += s.get('strategy_market_signal') is True
        shadow['flow_pass'] += (s.get('flow_admission') or {}).get('allow') is True
        shadow['defense_pass'] += (s.get('defensive_entry') or {}).get('allowed') is True
        shadow['cost_pass'] += s.get('strategy_roundtrip_cost_admitted') is True
        shadow['all_gates_pass'] += shadow_pass(row)
        defense_reasons.update((s.get('defensive_entry') or {}).get('reasons') or [])
    opened = []
    for sid in BOOKS:
        book = books.get(sid) or {}
        positions = list(book.get('positions') or [])
        alias = book.get('position')
        key = lambda p: (p.get('trade_no'), p.get('opened_at'), p.get('address'), p.get('pairAddress'))
        if isinstance(alias, dict) and not any(key(alias) == key(p) for p in positions):
            positions.append(alias)
        opened += [dict(strategy=sid, trade_no=p.get('trade_no'), symbol=p.get('symbol'),
                        pool=p.get('pairAddress'), entry_policy=p.get('entry_policy_version'),
                        exit_policy=(p.get('exit_parameters') or {}).get('version'),
                        net_mark_usd=p.get('open_pnl_usd'), quote_status=p.get('quote_status'),
                        updated_at=p.get('updated_at')) for p in positions]
    pools = Counter(p['pool'] for p in opened if p['pool'])
    return dict(kind='READ_ONLY_RECORDED_PAPER_OUTCOMES_NOT_A_BACKTEST',
        updated_at=ledger.get('updated_at'), primary_total=summarize(rows),
        by_book={sid:summarize((books.get(sid) or {}).get('history', [])) for sid in BOOKS},
        by_entry_policy={name:summarize(group) for name, group in sorted(policy.items())},
        capacity_test={**summarize(test), 'shadow_admission_counts':shadow,
                       'defense_rejections':dict(defense_reasons)},
        open_positions=opened, open_position_count=len(opened), unique_open_pools=len(pools),
        duplicated_open_pools={p:n for p,n in pools.items() if n>1},
        best_closed=[{k:r.get(k) for k in ('strategy_id','trade_no','symbol','pnl_usd','pnl_pct',
            'entry_policy_version','exit_reason','notional_usd')} for r in sorted(rows,
             key=lambda r:number(r.get('pnl_usd')) or 0, reverse=True)[:5]],
        caveats=['Open marks are not closed wins; stale marks are not executable exits.',
                 'Multiple books holding the same pool are correlated, not independent samples.',
                 'Older policy cohorts used different notionals/exits; no pooled profitability claim.',
                 'Recorded peaks do not establish that an alternative exit would fill.',
                 'Too few historical winners to fit and validate a profitable strategy.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    args = parser.parse_args()
    raw = args.ledger.read_bytes()
    result = audit(json.loads(raw.decode('utf-8-sig')))
    result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
