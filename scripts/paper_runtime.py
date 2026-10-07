#!/usr/bin/env python3
"""Explicit local PAPER archive/reset/restore; read-only deployed snapshot audit."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from engine_runtime import atomic_json
from paper_state_reset import reset_offline, restore_archive, reset_all_offline
import order_flow_adaptive_oct4 as oct4
import winner_ensemble

SUPPORTED_SIGNAL_STRATEGIES = (winner_ensemble.VERSION, oct4.STRATEGY_ID)


def set_account_strategy(registry, user, strategy):
    """Record one account's engine strategy in the gateway registry.

    Touches only that account's signal_strategy field; balances, history,
    positions and engine ports are never modified. The gateway applies the
    choice when it next spawns the account's engine.
    """
    registry = Path(registry)
    data = json.loads(registry.read_text(encoding='utf-8'))
    accounts = data.get('accounts')
    if not isinstance(data, dict) or not isinstance(accounts, dict):
        raise SystemExit('Account registry has an unknown schema; nothing changed.')
    user = str(user or '').strip()
    if len(user) < 8:
        raise SystemExit('Give at least 8 characters of the account id; nothing changed.')
    matches = [uid for uid in accounts if uid == user or uid.startswith(user)]
    if len(matches) != 1:
        raise SystemExit(f'{len(matches)} accounts match {user!r}; nothing changed.')
    chosen = None if strategy == 'default' else strategy
    if chosen is not None and chosen not in SUPPORTED_SIGNAL_STRATEGIES:
        raise SystemExit(f'Unsupported strategy {strategy!r}; supported: {SUPPORTED_SIGNAL_STRATEGIES}')
    record = accounts[matches[0]]
    before = record.get('signal_strategy')
    if chosen is None:
        record.pop('signal_strategy', None)
    else:
        record['signal_strategy'] = chosen
    record['signal_strategy_set_at'] = int(time.time() * 1000)
    record['updated_at'] = record['signal_strategy_set_at']
    atomic_json(registry, data)
    return {'account': matches[0][:8] + '…', 'signal_strategy_before': before,
            'signal_strategy_after': record.get('signal_strategy'),
            'engine_port': record.get('engine_port'), 'ledger_files_touched': 0,
            'applies': 'when the gateway next starts this account engine'}


def audit(state):
    groups = {}
    for trade in state.get('history', []):
        key = '|'.join(str(trade.get(k) or 'UNKNOWN') for k in
                       ['session_id', 'strategy_id', 'entry_policy_version', 'exit_policy_version'])
        group = groups.setdefault(key, {'records': 0, 'wins': 0, 'losses': 0,
                                        'breakeven': 0, 'net_pnl_usd': 0., 'reconstructible': 0})
        pnl = float(trade.get('pnl_usd') or 0)
        group['records'] += 1
        group['wins'] += int(pnl > 0)
        group['losses'] += int(pnl < 0)
        group['breakeven'] += int(pnl == 0)
        group['net_pnl_usd'] += pnl
        group['reconstructible'] += int(bool(trade.get('entry_execution') and trade.get('exit_execution')))
    return {'scope': 'available closed-history sample, not audited lifetime performance',
            'config': state.get('config'), 'last_scan_at': state.get('last_scan_at'),
            'positions': len(state.get('positions', [])), 'groups': groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    reset = sub.add_parser('reset')
    reset.add_argument('--root', type=Path, required=True)
    reset.add_argument('--offline', action='store_true', required=True,
                       help='Assert all account/training writers have been stopped')
    reset.add_argument('--balance', type=float, default=1000)
    reset.add_argument('--training-balance', type=float, default=500)
    reset_all = sub.add_parser('reset-all')
    reset_all.add_argument('--root',type=Path,required=True)
    reset_all.add_argument('--paired-root',type=Path)
    reset_all.add_argument('--offline',action='store_true',required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--root', type=Path, required=True)
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--offline', action='store_true', required=True)
    strategy = sub.add_parser('set-account-strategy',
                              help='choose one account engine strategy in the gateway registry')
    strategy.add_argument('--registry', type=Path, required=True, help='gateway user_accounts.json')
    strategy.add_argument('--user', required=True, help='account UUID or a unique prefix')
    strategy.add_argument('--strategy', required=True,
                          help="one of %s or 'default'" % ', '.join(SUPPORTED_SIGNAL_STRATEGIES))
    check = sub.add_parser('audit')
    check.add_argument('--state', type=Path, required=True)
    check.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'reset':
        result = reset_offline(args.root, starting_balance=args.balance, training_balance=args.training_balance)
        result.pop('training', None)
    elif args.command == 'reset-all':
        result = reset_all_offline(args.root,paired_root=args.paired_root)
    elif args.command == 'set-account-strategy':
        result = set_account_strategy(args.registry, args.user, args.strategy)
    elif args.command == 'restore':
        result = restore_archive(args.archive, args.root)
    else:
        result = audit(json.loads(args.state.read_text(encoding='utf-8')))
    if getattr(args, 'output', None):
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
