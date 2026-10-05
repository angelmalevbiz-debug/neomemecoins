#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from engine_runtime import atomic_json


def main():
    parser=argparse.ArgumentParser(description='Actual primary PAPER replay; never fetches historical quotes from live APIs')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--adaptive',action='store_true')
    parser.add_argument('--engine-backend',type=Path,
                        help='Explicit archived backend for frozen-source comparison; common controlled evidence adapters')
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
    from main_replay import MainReplay
    with MainReplay(root,adaptive=args.adaptive) as replay:
        result=replay.replay(rows)
    atomic_json(args.output,result)
    print(json.dumps({'records':result['records'],'invalid_records':result['invalid_records'],
                      'closed_trades':result['stats']['closed_trades'],'output':str(args.output)},indent=2))


if __name__=='__main__':
    main()
