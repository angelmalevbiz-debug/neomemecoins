#!/usr/bin/env python3
"""Run both regression suites with temporary account/cache/audit paths."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='neo-checks-') as tmp:
        directory=Path(tmp)
        env=os.environ.copy()
        env.update(PYTHONUTF8='1',PYTHONPATH=str(ROOT/'backend'),NEO_ENGINE_MODE='PAPER')
        paths={'NEO_MARKET_STATE_PATH':'state.json','NEO_MARKET_AUDIT_PATH':'audit.jsonl',
               'NEO_LIVE_TAPE_PATH':'live_tape.json','NEO_STRATEGY_LAB_PATH':'strategy_lab.json',
               'NEO_STRATEGY_LAB_COMPACT_PATH':'strategy_lab_compact.json',
               'NEO_STRATEGY_LAB_RESET_FLAG':'strategy_lab.reset','NEO_USER_STATE_PATH':'user_accounts.json',
               'NEO_USER_ENGINE_ROOT':'users','NEO_ASTRA_DATA_DIR':'astra','NEO_PAIRED_DIR':'paired',
               'NEO_RISK_CACHE_DIR':'risk','NEO_PRICE_CHECK_DIR':'price-check',
               'NEO_JUPITER_LOCK_PATH':'quote.lock','NEO_JUPITER_STAMP_PATH':'quote-stamp.txt',
               'NEO_TRAINING_ROOT':'training','NEO_TAPE_DB_PATH':'tape.sqlite'}
        env.update({key:str(directory/value) for key,value in paths.items()})
        # A shell's main-engine path (personal engines' ticker registry seed) never reaches the tests.
        env.pop('NEO_MAIN_MARKET_STATE_PATH',None)
        results=[]
        for suite in ['tests','backend/tests']:
            command=[sys.executable,'-m','unittest','discover','-s',suite,'-v']
            print('RUN '+ ' '.join(command),flush=True)
            result=subprocess.run(command,cwd=ROOT,env=env)
            results.append({'suite':suite,'exit_code':result.returncode})
        print(json.dumps({'isolated_paths':True,'results':results},indent=2))
        return int(any(r['exit_code'] for r in results))


if __name__=='__main__':
    raise SystemExit(main())
