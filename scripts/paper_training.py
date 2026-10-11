#!/usr/bin/env python3
"""Isolated PAPER replay/record/import/compare, using saved observations only.

Examples:
  python scripts/paper_training.py import --input recording.jsonl --output dataset.jsonl
  python scripts/paper_training.py replay --input dataset.jsonl --state .runtime/replay/training.json
  python scripts/paper_training.py compare --state .runtime/replay/training.json
  python scripts/paper_training.py record --output recording.jsonl < canonical-observations.jsonl

No provider calls, real trades, artificial backtest samples, or sleep are used.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from paper_training import PaperTrainingEngine, atomic_json
import observation_journal


def source_hash(path):
    digest = hashlib.sha256()
    with observation_journal.Reader(path) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path):
    rows = []
    with observation_journal.Reader(path) as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict) or "available_at" not in value or "observed_at" not in value or "coin" not in value:
                    raise ValueError("canonical observation fields are missing")
                if not 0 < int(value["observed_at"]) <= int(value["available_at"]):
                    raise ValueError("future/invalid source timestamp")
                rows.append(value)
            except (ValueError, TypeError) as exc:
                raise ValueError("%s:%s: %s" % (path, line_no, exc)) from exc
    return rows


def import_dataset(args):
    rows = load_rows(args.input)
    rows.sort(key=lambda row: int(row["available_at"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".import-", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                # Preserve source, rejection, safety and raw quote evidence;
                # do not infer missing proofs or relabel historical timestamps.
                handle.write(json.dumps(row, allow_nan=False, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    result = {"source": str(args.input.resolve()), "source_sha256": source_hash(args.input),
              "dataset": str(args.output.resolve()), "observations": len(rows),
              "first_available_at": rows[0]["available_at"] if rows else None,
              "last_available_at": rows[-1]["available_at"] if rows else None,
              "provenance": "user-supplied saved observations; no generated market outcomes"}
    atomic_json(args.output.with_suffix(args.output.suffix + ".manifest.json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="Accelerated causal replay; no waiting or API calls")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--state", type=Path, required=True, help="Isolated training state, never the main account")
    replay.add_argument("--config", type=Path)
    replay.add_argument("--snapshot", type=Path)
    compare = commands.add_parser("compare", help="Measured control/learner and held-out version comparisons")
    compare.add_argument("--state", type=Path, required=True)
    record = commands.add_parser("record", help="Append canonical JSONL from stdin without provider requests")
    record.add_argument("--output", type=Path, required=True)
    importer = commands.add_parser("import", help="Validate and sort an existing recording, preserving evidence")
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "import":
        result = import_dataset(args)
    elif args.command == "record":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        recorded = 0
        for line in sys.stdin:
            value = json.loads(line)
            if not isinstance(value, dict) or "available_at" not in value or "coin" not in value:
                raise ValueError("canonical observation required")
            observation_journal.append(args.output, (json.dumps(
                value, allow_nan=False, ensure_ascii=False) + "\n").encode('utf-8'))
            recorded += 1
        result = {"recorded": recorded, "path": str(args.output.resolve()), "provider_requests": 0}
    else:
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.command == "replay" and args.config else None
        if args.command == "compare" and not args.state.exists():
            raise ValueError("no saved training state to compare")
        engine = PaperTrainingEngine(args.state, config=config)
        result = engine.replay(load_rows(args.input)) if args.command == "replay" else engine.snapshot()
        if args.command == "replay":
            result["dataset"] = {"path": str(args.input.resolve()),
                                 "sha256": source_hash(args.input),
                                 "provenance": "saved observations only; correctness fixtures do not establish financial performance"}
            if args.snapshot:
                atomic_json(args.snapshot, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
