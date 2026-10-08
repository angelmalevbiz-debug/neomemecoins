#!/usr/bin/env python3
"""Build a ticker registry sidecar offline from a copy of the training journal (TICKER_REGISTRY_SEED_V3).

PAPER only. Read-only on its input; it never contacts a service, a provider or
a wallet. STRUCTURAL_RUG_GUARD_V1 blocks pools younger than 14 days with
rug_ticker_registry_warming until a registry has observed the market for 24 h
without a gap longer than 60 min. On the first deploy of the layer every
service registry starts empty, so without a seed every engine and Lab book
would wait 24 h before entering any pool younger than 14 days.

The main engine's training journal (``.runtime/accounts/training/observations.jsonl``)
records the bounded scan feed (market_monitor.MAX_FEED = 90 coins after
entry_quote_priority.bounded_feed; unchanged repeat polls within 3 s are
coalesced) plus entry, probe and position rows, each with its mint, pool,
symbol and observation time. Gecko new pools cut by the bound are not
journalled, although main's live registry sees them in the untrimmed feed, so
the seed is a Lab/tape-grade memory: the same trimmed view the Lab, the tape
and the research scan log (obs.sqlite3, built from this journal) have.

This tool replays the journal through ``structural_rug_guard.TickerRegistry``
(same normalization, placeholder handling, 14-day pruning, 40,000-entry bound
and 60-minute coverage gap rule as the live registry) and writes a
TICKER_REGISTRY_V2_COVERAGE sidecar. Every valid row registers its sighting;
coverage comes only from market rows (REGISTRY_COVERAGE_BASIS): a row whose
coin sources name only a held position, or a position-mark row (rejection
reason 'exit_quote' or a 'mark' quote), observes no market and never extends
coverage. Coverage is the journal's continuous market span, so the services
must start within 60 minutes of the printed ``coverage.observed_until`` for it
to carry over (otherwise the sightings still count and coverage starts again).

Usage, from the live checkout root, only after ``.\\scripts\\start_local_paper.ps1
-Action Stop`` has completed (it prints "All owned PAPER services stopped and
flushed."); copy the journal after they stopped:
    .venv\\Scripts\\python.exe scripts\\build_ticker_registry_seed.py
        --journal .runtime\\accounts\\training\\observations.jsonl
        --out .runtime\\accounts\\state.ticker_registry.json

The services must be stopped for every run, the first one and
``--replace-stale`` alike: a running main keeps its own registry in memory and
rewrites its sidecar at its next periodic save (within 5 min), and the Lab,
the tape and the personal engines seed only when their registry starts, so a
seed written under running services is silently lost. The tool therefore
refuses to write into a runtime directory (the output's folder or any parent)
whose ``services/processes.json`` exists without ``services/stop.request``
(Stop writes that marker, Start removes it).

The output must not exist. An earlier attempt (services started before the
seed, a rolled-back deploy, a failed first try) can leave a sidecar that does
not vouch for anything yet; ``--replace-stale`` replaces an existing sidecar
only when it is corrupt (it reads but does not parse), carries no coverage,
or its coverage at the wall clock is not current (last observation more than
60 min ago) or shorter than 24 h. A sidecar with current coverage of 24 h or
more is never replaced, and neither is one that cannot be read (an I/O error:
something still holds it open). Before replacing, the old file is copied to
``<out>.replaced-<ms>`` (no service reads that name) and its readable
sightings are merged into the new sidecar (a ticker registry is market
memory, so a merge can only block more). The Lab and the tape seed from
main's sidecar at their start, and personal engines through
NEO_MAIN_MARKET_STATE_PATH.

After Start, verify that the seed took (docs/PAPER_RUNBOOK.md): main's
``entry_diagnostics.defensive_entry.layer.ticker_registry`` shows
``load_status`` LOADED and ``coverage.warming`` false, and the Lab's
(``activity_config.defensive_entry_state.ticker_registry``) and the tape's
(``live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry``)
show ``coverage.warming`` false.
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import structural_rug_guard as guard  # noqa: E402

VERSION = guard.REGISTRY_SEED_VERSION
SOURCE_KIND = 'TRAINING_OBSERVATIONS_JOURNAL'
MIN_COVERAGE_MS = guard.PARAMS.ticker_registry_min_coverage_minutes * 60_000
# Read-only checks on main's GET /state after Start (docs/PAPER_RUNBOOK.md, first deploy).
VERIFY_AFTER_START = [
    'entry_diagnostics.defensive_entry.layer.ticker_registry.load_status == LOADED',
    'entry_diagnostics.defensive_entry.layer.ticker_registry.coverage.warming == false',
    'strategy_lab.activity_config.defensive_entry_state.ticker_registry.coverage.warming == false '
    '(seed.sources SEEDED or coverage.adopted_from set)',
    'live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry.coverage.warming == false',
]


def _stamp(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def position_mark_row(row: dict) -> bool:
    """A held position's mark row (market_monitor.update_positions): no market observation."""
    reasons = row.get('rejection_reasons')
    if isinstance(reasons, (list, tuple)) and 'exit_quote' in reasons:
        return True
    evidence = (row.get('source') or {}).get('execution_evidence') if isinstance(row.get('source'), dict) else None
    return isinstance(evidence, dict) and 'mark' in evidence


def build(journal: Path, *, max_gap_ms=guard.REGISTRY_MAX_GAP_MS) -> tuple:
    """Replay one journal into an in-memory registry; returns (registry, summary). Never writes."""
    clock = {'now': 0}
    # Pruned at most once per journal hour (each prune scans every entry); the final
    # forced prune applies the 14-day retention and the entry cap exactly.
    registry = guard.TickerRegistry(None, clock=lambda: clock['now'], max_gap_ms=max_gap_ms,
                                    prune_interval_ms=guard.HOUR_MS)
    rows = invalid = registered = market_rows = held_rows = 0
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
            if guard.market_observation(coin) and not position_mark_row(row):
                registry.mark_observed(stamp)
                market_rows += 1
            else:
                held_rows += 1
            registry.prune(observed)
            first = observed if first is None else min(first, observed)
            last = observed if last is None else max(last, observed)
    if last is not None:
        registry.prune(last, force=True)
    coverage = registry.coverage_status(last)
    summary = {'version': VERSION, 'registry_version': guard.REGISTRY_VERSION, 'source_kind': SOURCE_KIND,
               'coverage_basis': guard.REGISTRY_COVERAGE_BASIS,
               'journal': journal.name, 'journal_bytes': journal.stat().st_size, 'rows': rows,
               'invalid_rows': invalid, 'market_rows': market_rows, 'held_position_rows': held_rows,
               'new_pools_registered': registered, 'entries': len(registry),
               'first_observed_at': first, 'last_observed_at': last, 'coverage': coverage,
               'paper_only': True, 'is_entry_authorization': False}
    return registry, summary


def running_services_root(out: Path):
    """The runtime directory (the output's folder or a parent) whose local PAPER services may run, else None.

    start_local_paper.ps1 keeps its manifest in ``.runtime/accounts/services/processes.json``;
    ``-Action Stop`` writes ``services/stop.request`` and ``-Action Start`` removes
    it. A manifest without the marker means services were started and not stopped.
    """
    folder = Path(os.path.abspath(out)).parent
    for candidate in (folder, *folder.parents):
        services = candidate / 'services'
        if (services / 'processes.json').exists() and not (services / 'stop.request').exists():
            return candidate
    return None


def existing_sidecar(path: Path, now) -> dict:
    """Read-only verdict on an existing sidecar at ``now``: may it be replaced, and its rows."""
    status, rows, coverage = guard.TickerRegistry.read_sidecar(path)
    since = until = None
    if coverage is not None:
        _version, since, until, _resets = coverage
    current = since is not None and until is not None and now - until <= guard.REGISTRY_MAX_GAP_MS
    covered_ms = max(0.0, now - since) if current else 0.0
    return {'status': status, 'rows': rows, 'covered_since': since, 'observed_until': until,
            'coverage_current': current, 'coverage_hours': round(covered_ms / guard.HOUR_MS, 2),
            # Only a sidecar that already vouches (current coverage of 24 h or more) is kept,
            # and one that cannot be read (I/O error) is never replaced: it cannot be copied
            # first, and something (a running service) may still hold it open.
            'replaceable': (status != 'UNREADABLE'
                            and not (status == 'OK' and current and covered_ms >= MIN_COVERAGE_MS))}


def main(argv=None, *, clock=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--journal', required=True, type=Path, help='copy of training observations.jsonl (read only)')
    parser.add_argument('--out', required=True, type=Path, help='new sidecar path; must not exist')
    parser.add_argument('--replace-stale', action='store_true',
                        help=('replace an existing --out only when its coverage at the wall clock is not '
                              'current or under 24 h (or it is corrupt); a copy is kept as '
                              '<out>.replaced-<ms> and its sightings are merged. Like the first run, only '
                              'after start_local_paper.ps1 -Action Stop has completed'))
    args = parser.parse_args(argv)
    journal, out = args.journal, args.out
    now = (clock or (lambda: int(time.time() * 1000)))()
    if not journal.is_file():
        parser.error(f'journal not found: {journal}')
    if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(journal)):
        parser.error('the output cannot be the journal')
    running = running_services_root(out)
    if running is not None:
        parser.error(f'refusing to write into {running}: its local PAPER services are not stopped '
                     '(services/processes.json exists without services/stop.request). A running main '
                     'rewrites its sidecar within 5 minutes and the other services seed only at start, so '
                     'the seed would be lost. Run .\\scripts\\start_local_paper.ps1 -Action Stop and wait for '
                     '"All owned PAPER services stopped and flushed." first (docs/PAPER_RUNBOOK.md)')
    previous = None
    if out.exists():
        if not args.replace_stale:
            parser.error(f'refusing to replace an existing file: {out} (an earlier attempt left it? '
                         'see --replace-stale and docs/PAPER_RUNBOOK.md)')
        if not out.is_file():
            parser.error(f'refusing to replace a non-file: {out}')
        previous = existing_sidecar(out, now)
        if previous['status'] == 'UNREADABLE':
            parser.error(f'refusing to replace {out}: it cannot be read (I/O error), so no copy can be kept; '
                         'something may still hold it open (are the services stopped?)')
        if not previous['replaceable']:
            parser.error(f'refusing to replace {out}: its coverage is current and '
                         f'{previous["coverage_hours"]} h long (it already vouches)')
    registry, summary = build(journal)
    if summary['last_observed_at'] is None:
        print(json.dumps({**summary, 'written': False, 'reason': 'no valid journal rows'}, indent=2))
        return 1
    replaced = None
    if previous is not None:
        backup = out.with_name(f'{out.name}.replaced-{int(now)}')
        if backup.exists():
            parser.error(f'refusing to overwrite an earlier backup: {backup}')
        shutil.copy2(out, backup)
        merged, skipped = registry.merge_rows(previous['rows'])
        registry.prune(summary['last_observed_at'], force=True)
        summary['entries'] = len(registry)
        summary['coverage'] = registry.coverage_status(summary['last_observed_at'])
        replaced = {'backup': str(backup), 'status': previous['status'],
                    'covered_since': previous['covered_since'], 'observed_until': previous['observed_until'],
                    'coverage_current': previous['coverage_current'],
                    'coverage_hours': previous['coverage_hours'],
                    'merged_new_pools': merged, 'skipped_rows': skipped}
    registry.path = out
    written = registry.save(summary['last_observed_at'])
    print(json.dumps({**summary, 'out': str(out), 'written': written, 'replaced': replaced,
                      'coverage_at_now': registry.coverage_status(now),
                      'save_error': registry.status()['save_error'],
                      'services_stopped_check': 'NO_UNSTOPPED_SERVICES_MANIFEST',
                      'verify_after_start': VERIFY_AFTER_START}, indent=2))
    return 0 if written else 1


if __name__ == '__main__':
    raise SystemExit(main())
