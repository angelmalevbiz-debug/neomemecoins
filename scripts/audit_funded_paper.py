"""Read-only PAPER audit: full ledgers, not the dashboard's last 30 receipts.

No provider probes, engine controls, orders, capital changes or replay profits.
The three inputs are sequential snapshots, NOT one atomic market observation.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import urllib.request

BOOK_IDS = ('EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def utc(value):
    value = number(value)
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat() if value and value > 0 else None
    except (OverflowError, ValueError, OSError):
        return None


def trade_row(trade):
    """Actual receipt values only. A sampled peak is NOT an executable exit."""
    opened, closed = number(trade.get('opened_at')), number(trade.get('closed_at'))
    size = number(trade.get('notional_usd'))
    protection = trade.get('profit_protection') or {}
    peak = number(protection.get('peak_net_usd'))
    if peak is None and size is not None and number(trade.get('peak_net_pct')) is not None:
        peak = size * number(trade['peak_net_pct']) / 100
    fee_fields = ('entry_dex_fee_usd', 'exit_dex_fee_usd', 'entry_network_fee_usd', 'exit_network_fee_usd')
    fees = {key: number(trade.get(key)) for key in fee_fields}
    exit_parameters = trade.get('exit_parameters') or {}
    flow = trade.get('verified_entry_flow') or {}
    return dict(trade_no=trade.get('trade_no'), symbol=trade.get('symbol'),
        address=trade.get('address'), pairAddress=trade.get('pairAddress'),
        opened_at=opened, closed_at=closed, opened_utc=utc(opened), closed_utc=utc(closed),
        hold_minutes=(closed-opened)/60000 if opened and closed and closed >= opened else None,
        policy=trade.get('entry_policy_version') or 'UNVERSIONED',
        exit_policy=exit_parameters.get('version') or trade.get('exit_policy_label') or 'UNVERSIONED',
        notional_usd=size, pnl_usd=number(trade.get('pnl_usd')),
        initial_roundtrip_pnl_pct=number(trade.get('entry_roundtrip_pnl_pct')),
        sampled_peak_net_usd=peak, exit_reason=trade.get('exit_reason'),
        explicit_fee_fields=fees, fees_complete=all(v is not None for v in fees.values()),
        explicit_recorded_fees_usd=sum(v for v in fees.values() if v is not None),
        exit_parameters=exit_parameters,
        entry_flow={key:flow.get(key) for key in ('source','coverage_status','trades','unique_wallets','buy_usd','sell_usd')},
        entry_features={key:(trade.get('entry_features') or {}).get(key) for key in ('m5','h1','bs','liq','vol_liq')})


def aggregate(rows):
    valid = [row for row in rows if row['pnl_usd'] is not None]
    wins = [row['pnl_usd'] for row in valid if row['pnl_usd'] > 0]
    losses = [row['pnl_usd'] for row in valid if row['pnl_usd'] < 0]
    holds = [row['hold_minutes'] for row in valid if row['hold_minutes'] is not None]
    return dict(receipts=len(rows), valid_pnl_receipts=len(valid), wins=len(wins), losses=len(losses),
        breakevens=len(valid)-len(wins)-len(losses),
        win_rate_pct=len(wins)/len(valid)*100 if valid else None,
        net_pnl_usd=sum(row['pnl_usd'] for row in valid),
        average_win_usd=sum(wins)/len(wins) if wins else None,
        average_loss_usd=sum(losses)/len(losses) if losses else None,
        profit_factor=sum(wins)/-sum(losses) if losses else None,
        average_hold_minutes=sum(holds)/len(holds) if holds else None,
        unique_exact_pools=len({(row['address'],row['pairAddress']) for row in valid}),
        sampled_positive_peak_but_loss=sum(row['pnl_usd'] < 0 and (row['sampled_peak_net_usd'] or 0) > 0 for row in valid),
        never_sampled_net_positive=sum(row['sampled_peak_net_usd'] is not None and row['sampled_peak_net_usd'] <= 0 for row in valid),
        exit_reasons=dict(Counter(row['exit_reason'] or 'UNRECORDED' for row in rows)),
        profitability_proven=False)


def audit_book(book, current_policy, now):
    rows = [trade_row(trade) for trade in book.get('history') or [] if isinstance(trade, dict)]
    cohorts = defaultdict(list)
    for row in rows:
        cohorts[(row['policy'], row['exit_policy'], row['notional_usd'])].append(row)
    current = [row for row in rows if row['policy'] == current_policy]
    opened = [row['opened_at'] for row in rows]
    positions = book.get('positions') or ([book['position']] if book.get('position') else [])
    opened += [number(pos.get('opened_at')) for pos in positions]
    return dict(balance=book.get('balance'), starting_balance=book.get('starting_balance'),
        open_positions=len(positions), all_policies=aggregate(rows), current_policy=current_policy,
        current_policy_performance=aggregate(current),
        entries_last_60m=sum(value is not None and 0 <= now-value < 3600000 for value in opened),
        cohorts=[dict(policy=key[0], exit_policy=key[1], notional_usd=key[2], **aggregate(values))
                 for key, values in sorted(cohorts.items(), key=lambda item:str(item[0]))],
        receipts=sorted(rows, key=lambda row:row['opened_at'] or 0))


def market_audit(feed, tape, strategy_id, active, usable_feed_coin, now, *, held_addresses=(), flows=None, flow_admission=None):
    counts, primary = Counter(), Counter()
    rows = []
    selected = set((tape.get('entry_scheduling') or {}).get('selected_pairs') or [])
    latest = {}
    usable = 0
    for index,coin in enumerate(feed):
        if usable_feed_coin(coin,now):
            usable += 1
            key=(coin['address'],coin['pairAddress'])
            if key not in latest or coin['updatedAt']>feed[latest[key]]['updatedAt']:
                latest[key]=index
    included={index for index in latest.values() if feed[index]['address'] not in held_addresses}
    for index,coin in enumerate(feed):
        failed = active.market_rejections(strategy_id, coin)
        if index in included:
            counts.update(failed)
        if failed and index in included:
            primary.update(failed[:1])
        coverage = (tape.get('pair_coverage') or {}).get(coin.get('pairAddress')) or {}
        flow_gate = flow_admission(coin,(flows or {}).get((coin.get('address'),coin.get('pairAddress'))) or {},now) if flow_admission else None
        rows.append(dict(symbol=coin.get('symbol'), address=coin.get('address'), pairAddress=coin.get('pairAddress'),
            updated_at=coin.get('updatedAt'), failed_market_constraints=failed,
            included_in_entry_screen=index in included,
            passed_market_screen=not failed, tape_selected=coin.get('pairAddress') in selected,
            tape_coverage_status=coverage.get('status'),
            flow_snapshot_diagnostic_only=flow_gate,
            age_minutes=coin.get('ageMinutes'), liquidity_usd=coin.get('liquidityUsd'),
            changes=coin.get('priceChange'), transactions_m5=(coin.get('txns') or {}).get('m5')))
    return dict(feed_rows=len(rows),unusable_feed_rows=len(rows)-usable,
        duplicate_usable_rows=usable-len(latest),already_open=len(latest)-len(included),evaluated=len(included),
        passed=sum(row['included_in_entry_screen'] and not row['failed_market_constraints'] for row in rows),
        primary_rejections=dict(primary), overlapping_failed_constraints=dict(counts),
        passes_if_only_one_failed_constraint_removed={reason:sum(row['included_in_entry_screen'] and row['failed_market_constraints']==[reason] for row in rows) for reason in counts},
        ablation_is_diagnostic_only_not_entry_admission=True, candidates=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--tape', type=Path, required=True)
    parser.add_argument('--state-url', default='http://127.0.0.1:8878/state')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='neo-readonly-audit-') as temporary:
        setup = runpy.run_path(str(root/'scripts/run_python_checks.py'))
        environment = setup['isolated_environment'](temporary)
        os.environ.clear()
        os.environ.update(environment)
        os.environ.update(NEO_LAB_FUNDED_ACTIVE_ENABLED='1', NEO_LAB_QUALITY_ENABLED='1',
            NEO_ENGINE_MODE='PAPER', NEO_EXECUTION_MODE='PAPER',
            NEO_LAB_PAPER_250_ENABLED='1', NEO_LAB_TICKER_WARNING_ENABLED='1',
            NEO_LAB_FLOW_SIZE_DECOUPLED_ENABLED='1', NEO_LAB_BASE_FLOW_RESTORED_ENABLED='1')
        sys.path.insert(0, str(root/'backend'))
        import funded_active_paper as active
        import lab_activity as activity
        import strategy_lab as lab_module
        with urllib.request.urlopen(args.state_url, timeout=15) as response:
            state = json.load(response)
        ledger = json.loads(args.ledger.read_text(encoding='utf-8'))
        tape = json.loads(args.tape.read_text(encoding='utf-8'))
        # Existing pure snapshot projection, not the Lab's trading loop. All
        # import-time cache/ledger paths above are isolated from the runtime.
        lab_module.LIVE_TAPE_PATH=args.tape
        flows=lab_module.flow_map()
        now = int(time.time()*1000)
        lab = state.get('strategy_lab') or {}
        report = dict(version='READ_ONLY_FUNDED_PAPER_AUDIT_V1', audit_at=now, audit_utc=utc(now),
            source_times=dict(main_last_scan=state.get('last_scan_at'), lab=ledger.get('updated_at'), tape=tape.get('updated_at')),
            snapshot_is_atomic=False, main_running=state.get('running'), lab_status=lab.get('status'),
            books={sid:dict(**audit_book(ledger['books'][sid], active.reporting_version(sid), now),
                market=market_audit(state.get('feed') or [], tape, sid, active, activity.usable_feed_coin, now,
                    held_addresses={pos.get('address') for pos in active.positions(ledger['books'][sid])},
                    flows=flows,flow_admission=lab_module.promoted_guard.flow_admission),
                last_live_diagnostics=(lab.get('books',{}).get(sid) or {}).get('entry_diagnostics')) for sid in BOOK_IDS},
            tape=dict(status=tape.get('status'), current_backlog=tape.get('current_backlog'),
                lag_ms=tape.get('lag_ms'), coverage=tape.get('coverage'),
                entry_scheduling=tape.get('entry_scheduling')),
            fast_scalp=lab.get('fast_scalp'), high_frequency=lab.get('high_frequency'),
            exit_research=lab.get('exit_research'),
            limitations=['PAPER modeled results, not real realized money',
                'Full available funded ledgers; other accounts and TEST books excluded',
                'Sampled peaks are not executable fills or valid alternative-policy backtests',
                'Explicit fees omit impact, slippage, and any unrecorded partial-leg fees',
                'A missing tape seat is unobserved, not evidence of zero buying',
                'Small heterogeneous cohorts do not establish a profitable edge'])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        print(json.dumps(dict(output=str(args.output.resolve()), audit_utc=report['audit_utc'],
            books={sid:dict(summary=book['current_policy_performance'],market_passed=book['market']['passed'],
                primary_market_rejections=book['market']['primary_rejections']) for sid,book in report['books'].items()}), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
