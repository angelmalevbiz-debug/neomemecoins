#!/usr/bin/env python3
"""Replay recorded observations through the actual primary PAPER engine.

Uses only recorded quote evidence; no provider calls. An --exit-variant run
applies an alternative exit rule set to the same recorded episodes and writes a
clearly labelled variant report. No run overwrites an existing --output unless
--overwrite is passed and the existing report has the same report_kind, so a
baseline is never replaced by a variant (or the reverse). Nothing here proves
profitability.
"""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from engine_runtime import atomic_json


def output_refusal(path,report_kind,overwrite):
    """Reason to refuse writing a report of report_kind to path, or None."""
    path=Path(path)
    if not path.exists():
        return None
    if not overwrite:
        return (f'--output {path} already exists; a replay report is never overwritten '
                '(pass --overwrite to replace a report of the same report_kind)')
    try:
        existing=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeDecodeError,ValueError):
        return f'--output {path} exists and is not a readable replay report; refusing to overwrite it'
    existing_kind=existing.get('report_kind') if isinstance(existing,dict) else None
    if existing_kind!=report_kind:
        return (f'--output {path} holds a {existing_kind or "unlabelled"} report; refusing to overwrite it '
                f'with a {report_kind} report')
    return None


def main():
    parser=argparse.ArgumentParser(description='Actual primary PAPER replay; never fetches historical quotes from live APIs')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--adaptive',action='store_true')
    parser.add_argument('--engine-backend',type=Path,
                        help='Explicit archived backend for frozen-source comparison; common controlled evidence adapters')
    parser.add_argument('--exit-variant',
                        help='Alternative exit rules as key=value pairs separated by commas: '
                             'stop_pct, take_profit_pct, disable_exit_impact_emergency, max_hold_minutes. '
                             'Exit decisions only; the report is labelled EXIT_VARIANT.')
    parser.add_argument('--variant-label',help='Label stored in the variant report (default derived from the rules)')
    parser.add_argument('--entry-defense',choices=('recorded_policy','apply','without_layer'),
                        help='DEFENSIVE_ENTRY_LAYER_V1 in the replay: recorded_policy (default; only journals '
                             'recorded before the layer, refuses rows recorded under it), apply (labelled '
                             'counterfactual on the replayed rows only) or without_layer (labelled counterfactual '
                             'that skips the layer)')
    parser.add_argument('--overwrite',action='store_true',
                        help='Replace an existing --output, only when it is a report of the same report_kind')
    args=parser.parse_args()
    root=args.state_dir.resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit('Fresh isolated replay state directory required')
    # Set all paths before import: module import must never read a running account.
    os.environ.update(NEO_MARKET_STATE_PATH=str(root/'state.json'),NEO_MARKET_AUDIT_PATH=str(root/'audit.jsonl'),
        NEO_LIVE_TAPE_PATH=str(root/'tape.json'),NEO_EXECUTION_MODE='REPLAY',NEO_ENGINE_MODE='REPLAY',
        NEO_RISK_CACHE_DIR=str(root/'risk-cache'),NEO_JUPITER_LOCK_PATH=str(root/'quotes.lock'))
    os.environ.update(NEO_STRATEGY_LAB_PATH=str(root/'strategy_lab.json'),
        NEO_STRATEGY_LAB_COMPACT_PATH=str(root/'strategy_lab_compact.json'),
        NEO_PRICE_CHECK_DIR=str(root/'price-check'),NEO_ASTRA_DATA_DIR=str(root/'astra'))
    rows=[json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
    if args.engine_backend:
        sys.path.insert(0,str(args.engine_backend.resolve()))
    import main_replay
    from main_replay import MainReplay, parse_exit_variant
    # Archived harnesses (--engine-backend) predate the recorded_policy cutover check.
    refused=getattr(main_replay,'RecordedPolicyRefused',None)
    exit_variant=None
    if args.exit_variant:
        try:
            exit_variant=parse_exit_variant(args.exit_variant)
        except ValueError as exc:
            raise SystemExit(f'Invalid --exit-variant: {exc}')
    elif args.variant_label:
        raise SystemExit('--variant-label requires --exit-variant')
    refusal=output_refusal(args.output,'EXIT_VARIANT' if exit_variant else 'BASELINE',args.overwrite)
    if refusal:
        raise SystemExit(refusal)
    options={'entry_defense':args.entry_defense} if args.entry_defense else {}
    with MainReplay(root,adaptive=args.adaptive,exit_variant=exit_variant,label=args.variant_label,**options) as replay:
        try:
            result=replay.replay(rows)
        except Exception as exc:
            if refused is not None and isinstance(exc,refused):
                raise SystemExit(str(exc))
            raise
    # Re-check after the run: the path may have been written in the meantime.
    refusal=output_refusal(args.output,result['report_kind'],args.overwrite)
    if refusal:
        raise SystemExit(refusal)
    atomic_json(args.output,result)
    print(json.dumps({'report_kind':result['report_kind'],'exit_variant':result['exit_variant'].get('label'),
                      'records':result['records'],'invalid_records':result['invalid_records'],
                      'closed_trades':result['stats']['closed_trades'],
                      'open_positions':len(result['positions']),
                      'decision_summary':result['decision_summary'],'output':str(args.output)},indent=2))


if __name__=='__main__':
    main()
