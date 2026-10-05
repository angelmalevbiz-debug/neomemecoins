#!/usr/bin/env python3
"""One isolated PAPER learner process consuming the main engine's shared stream."""
import argparse
import json
import time
from pathlib import Path
import compat_file_lock as fcntl
from engine_runtime import atomic_json as _atomic_json
from paper_training import PaperTrainingEngine
from paper_state_reset import archive_files


def atomic_json(path, data):
    # Snapshot readers can briefly prevent replacement on Windows. Retry only
    # in this independent process; primary exits never wait for publication.
    for attempt in range(20):
        try:
            return _atomic_json(path, data)
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.01)


def follow(root, config):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'training.process.lock').open('a+') as singleton:
        fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine = PaperTrainingEngine(root / 'training.json', config=config)
        offset = int(engine.state.get('recording_start_offset', 0))
        minimum_time = int(engine.state.get('recording_start_at', 0))
        source = root / 'observations.jsonl'
        atomic_json(root / 'training_snapshot.json', engine.snapshot())
        while True:
            request = root / 'reset.request.json'
            if request.exists():
                info = json.loads(request.read_text(encoding='utf-8'))
                archive_files(root, ['training.json', 'training_snapshot.json', 'observations.jsonl'])
                engine.reset(initial_cash=500)
                minimum_time = int(info['at'])
                engine.state['recording_start_at'] = minimum_time
                # The marker was written in producer queue order. Preserve
                # post-reset rows even if they arrived during archive creation,
                # and exclude the old prefix on every subsequent restart.
                offset = int(info.get('journal_offset', source.stat().st_size if source.exists() else 0))
                engine.state['recording_start_offset'] = offset
                engine.state['recording_drops_baseline'] = int(info.get('recording_drops_total', 0))
                engine.save()
                request.unlink()
            changed = False
            if source.exists():
                # Replay the durable journal after a crash; existing IDs are skipped.
                # Partial final lines are retained for the next pass, never discarded.
                with source.open('rb') as handle:
                    if handle.seek(0, 2) < offset:
                        offset = 0
                    handle.seek(offset)
                    for _ in range(100):
                        line = handle.readline()
                        if not line or not line.endswith(b'\n'):
                            break
                        offset += len(line)
                        row = json.loads(line)
                        if int(row.get('available_at', 0)) < minimum_time:
                            continue
                        if row.get('id') in engine.state['seen_ids']:
                            continue
                        engine.ingest(row)
                        changed = True
            if changed or not source.exists() or minimum_time:
                atomic_json(root / 'training_snapshot.json', engine.snapshot())
            time.sleep(.05)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    follow(args.root, json.loads(args.config.read_text(encoding='utf-8')))
