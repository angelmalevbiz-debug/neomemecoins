#!/usr/bin/env python3
"""Measure net PAPER results and throughput from a complete local Lab ledger.

``--hf-dir`` adds the LAB_HIGH_FREQUENCY_V1 books from their journal
(strategy_lab_hf/ or its journal/ folder; read only), measured per book and
config hash with multi-slot throughput (paper_lab_metrics.measure_hf_rows).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from paper_lab_metrics import evaluate_lab, measure_hf_rows, read_hf_journal


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', type=Path,
                        help='Full strategy_lab.json, not the compact dashboard projection')
    parser.add_argument('--hf-dir', type=Path,
                        help='strategy_lab_hf directory (or its journal folder) of the HF books, read only')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--target', type=float, default=80.0)
    args = parser.parse_args()
    if args.input is None and args.hf_dir is None:
        parser.error('give --input, --hf-dir or both')
    report = {}
    try:
        as_of = None
        if args.input is not None:
            raw = args.input.read_bytes()
            state = json.loads(raw.decode('utf-8-sig'))
            report = evaluate_lab(state, target=args.target)
            report['source_sha256'] = hashlib.sha256(raw).hexdigest()
            as_of = state.get('updated_at')
        if args.hf_dir is not None:
            if not args.hf_dir.is_dir():
                raise ValueError(f'{args.hf_dir} is not a directory')
            rows, malformed = read_hf_journal(args.hf_dir)
            report['high_frequency'] = measure_hf_rows(rows, as_of=as_of)
            report['high_frequency']['rejected_rows']['unparseable_lines'] = malformed
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        for source in (args.input, args.hf_dir):
            if source is not None and (args.output.resolve() == source.resolve()
                                       or source.resolve() in args.output.resolve().parents):
                parser.error('Report output cannot overwrite or sit inside a source')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding='utf-8')
    print(serialized, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
