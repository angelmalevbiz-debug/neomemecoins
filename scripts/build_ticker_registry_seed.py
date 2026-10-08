#!/usr/bin/env python3
"""Build a ticker registry sidecar offline from a copy of the training journal (TICKER_REGISTRY_SEED_V2).

PAPER only. Read-only on its input; it never contacts a service, a provider or
a wallet. STRUCTURAL_RUG_GUARD_V1 blocks pools younger than 14 days with
rug_ticker_registry_warming until a registry has observed the market for 24 h
without a gap longer than 60 min. On the first deploy of the layer every
service registry starts empty, so without a seed every engine and Lab book
would wait 24 h before entering any pool younger than 14 days.

The main engine's training journal (``<runtime>/training/observations.jsonl``)
records every scan coin with its mint, pool, symbol and observation time,
which is what the research scan log (obs.sqlite3) was built from. This tool
replays it through ``structural_rug_guard.TickerRegistry`` (same
normalization, placeholder handling, 14-day pruning, 40,000-entry bound and
60-minute coverage gap rule as the live registry) and writes a
TICKER_REGISTRY_V2_COVERAGE sidecar. Coverage is the journal's continuous
span ending at its last row, so the services must start within 60 minutes of
that row for it to carry over (otherwise the sightings still count and
coverage starts again).

Usage (services stopped, as for every deploy):
    python scripts/build_ticker_registry_seed.py \
        --journal <runtime>/training/observations.jsonl \
        --out <runtime>/state.ticker_registry.json

The output must not exist (an existing sidecar is never replaced). The Lab and
the tape seed from main's sidecar at their start, and personal engines through
NEO_MAIN_MARKET_STATE_PATH.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import structural_rug_guard as guard  # noqa: E402

VERSION = guard.REGISTRY_SEED_VERSION
SOURCE_KIND = 'TRAINING_OBSERVATIONS_JOURNAL'


def _stamp(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def build(journal: Path, *, max_gap_ms=guard.REGISTRY_MAX_GAP_MS) -> tuple:
    """Replay one journal into an in-memory registry; returns (registry, summary). Never writes."""
    clock = {'now': 0}
    # Pruned at most once per journal hour (each prune scans every entry); the final
    # forced prune applies the 14-day retention and the entry cap exactly.
    registry = guard.TickerRegistry(None, clock=lambda: clock['now'], max_gap_ms=max_gap_ms,
                                    prune_interval_ms=guard.HOUR_MS)
    rows = invalid = registered = 0
    first = last = None
    with journal.open('r', encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            coin = row.get('coin') if isinstance(row, dict) else None
            observed = _stamp(row.get('observed_at')) if isinstance(row, dict) else None
            if not isinstance(coin, dict) or observed is None:
                invalid += 1
                continue
            stamp = guard.observation_time(coin, observed)
            clock['now'] = max(clock['now'], observed)
            registered += int(registry.observe_coin(coin, stamp))
            registry.mark_observed(stamp)
            registry.prune(observed)
            first = observed if first is None else min(first, observed)
            last = observed if last is None else max(last, observed)
    if last is not None:
        registry.prune(last, force=True)
    coverage = registry.coverage_status(last)
    summary = {'version': VERSION, 'registry_version': guard.REGISTRY_VERSION, 'source_kind': SOURCE_KIND,
               'journal': journal.name, 'journal_bytes': journal.stat().st_size, 'rows': rows,
               'invalid_rows': invalid, 'new_pools_registered': registered, 'entries': len(registry),
               'first_observed_at': first, 'last_observed_at': last, 'coverage': coverage,
               'paper_only': True, 'is_entry_authorization': False}
    return registry, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--journal', required=True, type=Path, help='copy of training observations.jsonl (read only)')
    parser.add_argument('--out', required=True, type=Path, help='new sidecar path; must not exist')
    args = parser.parse_args(argv)
    journal, out = args.journal, args.out
    if not journal.is_file():
        parser.error(f'journal not found: {journal}')
    if out.exists():
        parser.error(f'refusing to replace an existing file: {out}')
    if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(journal)):
        parser.error('the output cannot be the journal')
    registry, summary = build(journal)
    if summary['last_observed_at'] is None:
        print(json.dumps({**summary, 'written': False, 'reason': 'no valid journal rows'}, indent=2))
        return 1
    registry.path = out
    written = registry.save(summary['last_observed_at'])
    print(json.dumps({**summary, 'out': str(out), 'written': written,
                      'save_error': registry.status()['save_error']}, indent=2))
    return 0 if written else 1


if __name__ == '__main__':
    raise SystemExit(main())
