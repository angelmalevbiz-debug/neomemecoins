#!/usr/bin/env python3
"""Measure net PAPER results and throughput from a complete local Lab ledger."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from paper_lab_metrics import evaluate_lab


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path,
                        help='Full strategy_lab.json, not the compact dashboard projection')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--target', type=float, default=80.0)
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        report = evaluate_lab(json.loads(raw.decode('utf-8-sig')), target=args.target)
        report['source_sha256'] = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        if args.output.resolve() == args.input.resolve():
            parser.error('Report output cannot overwrite the source ledger')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding='utf-8')
    print(serialized, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
