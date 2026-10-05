#!/usr/bin/env python3
"""Hidden local PAPER services with an explicit, graceful shared stop marker."""
import argparse
import _thread
import os
import sys
import threading
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / 'backend'))


def validate_environment():
    if os.environ.get('NEO_ENGINE_MODE') != 'PAPER' or os.environ.get('NEO_EXECUTION_MODE') != 'PAPER':
        raise ValueError('Local runner requires explicit PAPER mode')
    root = Path(os.environ['NEO_LOCAL_RUNTIME_ROOT']).resolve()
    if not root.is_relative_to(REPOSITORY / '.runtime'):
        raise ValueError('Local runtime must stay in the repository .runtime directory')
    for name in ('NEO_MARKET_STATE_PATH', 'NEO_MARKET_AUDIT_PATH', 'NEO_LIVE_TAPE_PATH',
                 'NEO_TAPE_DB_PATH', 'NEO_USER_STATE_PATH', 'NEO_USER_ENGINE_ROOT',
                 'NEO_STRATEGY_LAB_PATH', 'NEO_STRATEGY_LAB_COMPACT_PATH',
                 'NEO_RISK_CACHE_DIR', 'NEO_PRICE_CHECK_DIR', 'NEO_JUPITER_LOCK_PATH',
                 'NEO_JUPITER_STAMP_PATH', 'NEO_ENGINE_BLOCKLIST_PATH', 'NEO_TRAINING_ROOT'):
        if not Path(os.environ[name]).resolve().is_relative_to(root):
            raise ValueError(f'{name} escapes the isolated local runtime')
    marker = Path(os.environ['NEO_LOCAL_STOP_FILE']).resolve()
    if not marker.is_relative_to(root):
        raise ValueError('Stop marker escapes local runtime')
    return marker


def run(service):
    marker = validate_environment()
    if marker.exists():
        return
    if service == 'main':
        import market_monitor as module
    elif service == 'tape':
        import live_tape as module
    elif service == 'lab':
        import strategy_lab as module
    else:
        import user_gateway as module
        # Per-user engines inherit the same isolated environment and stop marker.
        module.ENGINE_SCRIPT = Path(__file__).resolve()

    stopping = threading.Event()
    prior_running = None

    def watch_stop():
        nonlocal prior_running
        while not stopping.wait(.2):
            if marker.exists():
                if service == 'main':
                    with module.STATE.lock:
                        prior_running = module.STATE.running
                        module.STATE.running = False
                    module.MONITOR.stop()
                _thread.interrupt_main()
                return

    watcher = threading.Thread(target=watch_stop, name='local-paper-stop', daemon=True)
    watcher.start()
    print(f'Local PAPER {service} started; pid={os.getpid()}', flush=True)
    try:
        module.main()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        if service == 'main':
            module.MONITOR.stop()
            # Serialize after any in-flight entry/exit finishes. Every trade is
            # already atomic; this also persists the latest session metadata.
            with module.MONITOR.position_lock, module.MONITOR.entry_lock, module.STATE.lock:
                if prior_running is not None:
                    module.STATE.running = prior_running
                module.STATE.save()
            module.training_bridge.stop()
        elif service == 'tape' and module._RECORDER is not None:
            module._RECORDER.db.commit()
            module._RECORDER.close()
        elif service == 'lab':
            module.persist('stopped')
        elif service == 'gateway':
            for process in list(module.ENGINE_PROCESSES.values()):
                try:
                    process.wait(timeout=30)
                except Exception:
                    print('A personal engine is still stopping; no forced termination performed', flush=True)
            module.save_store()
        print(f'Local PAPER {service} stopped with persistent state; pid={os.getpid()}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', choices=('main', 'tape', 'lab', 'gateway'), default='main')
    args = parser.parse_args()
    run(args.service)
