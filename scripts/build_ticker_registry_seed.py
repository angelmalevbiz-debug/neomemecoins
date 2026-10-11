#!/usr/bin/env python3
"""Build a ticker registry sidecar offline from the training journal, read in place (TICKER_REGISTRY_SEED_V4).

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
must start within 60 minutes of the printed ``coverage.observed_until`` (the
printed ``start_services_before_utc``) for it to carry over (otherwise the
sightings still count and coverage starts again).

Run time (SEED_REPLAY_WINDOW_V1): the replay parses every row it reads (about
4 min per journal day on the reference PC: 54.6 MB/s, about 13.9 GB of journal
a day). The journal is reset only by a PAPER reset, so it is not replayed from
its first byte: the tool seeks (binary search on ``observed_at`` by byte
offset; rows are appended in observation order, with 1 h of slack) to the
first row of the last ``--window-days`` (default 15: the 14-day ticker
retention plus the 24 h coverage span) before the newest row and replays from
there; ``--full-journal`` replays everything. The window is a time span, and
15 journal days take about an hour, so the replay is also capped by bytes:
it starts no earlier than the last ``--max-replay-minutes`` (default 40) of
journal at the reference rate (about 131 GB, about 9 days at 13.9 GB a day;
``replay.limited_by_replay_budget`` and ``effective_window_days`` say when the
cap shortened the window). Both trade older ticker memory for time; the
replayed span must still reach 24 h of coverage. Coverage is judged at the
clock read AFTER the replay: when it has lapsed or is under 24 h the tool
still writes the sidecar (its sightings count) but exits 2, and every
registry then warms for 24 h unless a sibling vouches.

Usage (docs/PAPER_RUNBOOK.md, first deploy), from the live checkout root, on
the live journal in place; do not copy it: the tool opens the journal
read-only ('rb', with read/write sharing) and never writes it, and a copy (tens
of GB) only costs disk and minutes of the 60-minute coverage window. Two modes:

- Seed before Stop (first deploy only; recommended), while no
  ``.runtime\\accounts\\state.ticker_registry.json`` exists (the pre-release
  services have no ticker registry and never write it): with the services
  running (main keeps appending), run the release checkout's copy of this tool
  with ``--out`` in a scratch folder outside ``.runtime``, copy the sidecar
  into ``.runtime\\accounts`` after Stop and start within the printed window.
  The replay is then not part of the outage:
    .venv\\Scripts\\python.exe <release checkout>\\scripts\\build_ticker_registry_seed.py
        --journal .runtime\\accounts\\training\\observations.jsonl
        --out <scratch folder outside .runtime>\\state.ticker_registry.json
- Seed after Stop, in place: when that sidecar already exists and for every
  ``--replace-stale`` rerun, only after ``.\\scripts\\start_local_paper.ps1
  -Action Stop`` has completed (it prints "All owned PAPER services stopped
  and flushed."); nothing appends to the journal while the services are
  stopped, and the replay runs inside the outage:
    .venv\\Scripts\\python.exe scripts\\build_ticker_registry_seed.py
        --journal .runtime\\accounts\\training\\observations.jsonl
        --out .runtime\\accounts\\state.ticker_registry.json

A later deploy needs no seed, and after an outage over 60 minutes no seed can
help: when main scans again within 60 minutes of its last scan before Stop the
sidecars carry the coverage (the tool refuses a sidecar that still vouches);
after a longer outage the journal holds the same gap (nothing journals while
the services are stopped), so the replay's coverage ends at the last scan
before Stop, the tool exits 2 and every registry warms for 24 h. A seed helps
on a later deploy only when the sidecars themselves are lost and the outage
stays under 60 minutes.

The services must be stopped whenever a seed is written into the runtime
folder, the first one and ``--replace-stale`` alike: a running main keeps its
own registry in memory and rewrites its sidecar at its next periodic save
(within 5 min), and the Lab, the tape and the personal engines seed only when
their registry starts, so a seed written under running services is silently
lost. The tool therefore refuses to write into a runtime directory (the
output's folder or any parent) whose ``services/processes.json`` exists
without ``services/stop.request`` (Stop writes that marker, Start removes it).
The marker proves that Stop ran, not that every process exited: when Stop
reported that services are still flushing, wait until ``-Action Status`` shows
none running first. A scratch folder outside ``.runtime`` has no services
manifest, so the before-Stop seed of the first deploy is written there while
the services run and reaches ``.runtime\\accounts`` only by the copy after Stop.

The output must not exist. An earlier attempt (services started before the
seed, a rolled-back deploy, a failed first try) can leave a sidecar that does
not vouch for anything yet; ``--replace-stale`` replaces an existing sidecar
only when it is corrupt (it reads but does not parse), carries no coverage,
its coverage is stamped more than 5 min after the wall clock (never observed,
TICKER_COVERAGE_CLOCK_V1), or its coverage at the wall clock is not current
(last observation more than 60 min ago) or shorter than 24 h. A sidecar with
current coverage of 24 h or more is never replaced, and neither is one that
cannot be read (an I/O error: something still holds it open). Before
replacing, the old file is copied to ``<out>.replaced-<ms>`` (no service
reads that name) and its readable sightings are merged into the new sidecar
(a ticker registry is market memory, so a merge can only block more). The Lab
and the tape seed from main's sidecar at their start, and personal engines
through NEO_MAIN_MARKET_STATE_PATH.

After Start, verify that the seed took (docs/PAPER_RUNBOOK.md): main's
``defensive_entry_layer.ticker_registry`` (on /state after every scan, also
while main is paused) shows ``load_status`` LOADED and ``coverage.warming``
false, and the Lab's (``activity_config.defensive_entry_state.ticker_registry``)
and the tape's (``live_tape_status.entry_scheduling.defensive_entry.layer.ticker_registry``)
show ``coverage.warming`` false.
"""
import argparse
from datetime import datetime, timezone
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
import observation_journal  # noqa: E402

VERSION = guard.REGISTRY_SEED_VERSION
SOURCE_KIND = 'TRAINING_OBSERVATIONS_JOURNAL'
MIN_COVERAGE_MS = guard.PARAMS.ticker_registry_min_coverage_minutes * 60_000
# SEED_REPLAY_WINDOW_V1: replay only the last window of the journal (bounded run time).
REPLAY_WINDOW_VERSION = 'SEED_REPLAY_WINDOW_V1'
# Default: the 14-day ticker retention plus the 24 h coverage span (15 days).
DEFAULT_WINDOW_DAYS = guard.REGISTRY_RETENTION_DAYS + MIN_COVERAGE_MS / guard.DAY_MS
MIN_WINDOW_DAYS = 2.0
# Replay budget: measured 54.6 MB/s on 4.5 KB journal rows on the reference PC (review,
# 2026-10-08); the journal grows about 13.9 GB a day. 40 minutes leave room in the 60-minute
# coverage window for the stop, the file sync and the start.
REFERENCE_REPLAY_BYTES_PER_S = 54.6e6
DEFAULT_MAX_REPLAY_MINUTES = 40.0
MIN_REPLAY_MINUTES = 1.0
SEEK_SLACK_MS = guard.HOUR_MS
SEEK_GRANULARITY_BYTES = 1 << 20
TAIL_SCAN_BYTES = 8 << 20
# Exit codes: 0 written and vouching at the end, 1 not written, 2 written but not vouching.
EXIT_NOT_VOUCHING = 2
# Read-only checks on main's GET /state after Start (docs/PAPER_RUNBOOK.md, first deploy).
VERIFY_AFTER_START = [
    'defensive_entry_layer.ticker_registry.load_status == LOADED',
    'defensive_entry_layer.ticker_registry.coverage.warming == false',
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


def iso_utc(stamp_ms):
    """ISO 8601 UTC of a millisecond stamp (None when missing)."""
    value = _stamp(stamp_ms)
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def position_mark_row(row: dict) -> bool:
    """A held position's mark row (market_monitor.update_positions): no market observation."""
    reasons = row.get('rejection_reasons')
    if isinstance(reasons, (list, tuple)) and 'exit_quote' in reasons:
        return True
    evidence = (row.get('source') or {}).get('execution_evidence') if isinstance(row.get('source'), dict) else None
    return isinstance(evidence, dict) and 'mark' in evidence


def _row_observed_at(line: bytes):
    """observed_at of one journal line (None when the line is not a valid row)."""
    try:
        row = json.loads(line.decode('utf-8', errors='replace'))
    except ValueError:
        return None
    return _stamp(row.get('observed_at')) if isinstance(row, dict) else None


def _first_valid_after(handle, offset: int, size: int) -> tuple:
    """(observed_at, start offset) of the first valid row starting after the line at ``offset``."""
    handle.seek(offset)
    if offset > 0:
        handle.readline()   # the line ``offset`` falls into
    while True:
        start = handle.tell()
        line = handle.readline()
        if not line:
            return None, size
        stamp = _row_observed_at(line)
        if stamp is not None:
            return stamp, start


def _newest_observed_at(handle, size: int):
    """Newest observed_at among the valid rows in the journal's last TAIL_SCAN_BYTES (None when none)."""
    start = max(0, size - TAIL_SCAN_BYTES)
    handle.seek(start)
    if start:
        handle.readline()
    newest = None
    for line in handle:
        stamp = _row_observed_at(line)
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


def replay_window(journal: Path, window_days, *, max_bytes=None) -> dict:
    """Byte offset where the replay of the last ``window_days`` starts (0 for the whole journal). Read-only.

    Binary search on observed_at by byte offset: rows are appended in
    observation order, so every row before the returned offset is older than
    the target (the newest row minus the window minus 1 h of slack); the
    search stops within SEEK_GRANULARITY_BYTES, so a little more is replayed.
    ``max_bytes`` (the replay budget) moves the start later when the window
    holds more journal than that (``limited_by_replay_budget``).
    """
    size = observation_journal.total_size(journal)
    result = {'version': REPLAY_WINDOW_VERSION, 'journal_bytes': size, 'start_offset': 0,
              'skipped_bytes': 0, 'window_days': window_days, 'full_journal': window_days is None,
              'target_start_at': None, 'newest_row_at': None, 'max_replay_bytes': max_bytes,
              'limited_by_replay_budget': False, 'effective_start_at': None, 'effective_window_days': None,
              'estimated_replay_minutes': None}
    with observation_journal.Reader(journal) as handle:
        newest = _newest_observed_at(handle, size)
        result['newest_row_at'] = newest
        lo = 0
        if window_days is not None and newest is not None:
            target = newest - window_days * guard.DAY_MS - SEEK_SLACK_MS
            result['target_start_at'] = target
            hi = size
            while hi - lo > SEEK_GRANULARITY_BYTES:
                mid = (lo + hi) // 2
                stamp, _start = _first_valid_after(handle, mid, size)
                if stamp is None or stamp >= target:
                    hi = mid
                else:
                    lo = mid
            if lo > 0:
                handle.seek(lo)
                handle.readline()
                lo = handle.tell()
            if max_bytes is not None and size - lo > max_bytes:
                # The window holds more journal than the budget replays in time: start later.
                handle.seek(max(0, size - int(max_bytes)))
                handle.readline()
                lo = handle.tell()
                result['limited_by_replay_budget'] = True
        if newest is not None:
            # The first valid row at the line start ``lo`` (lo - 1 is the previous line's newline).
            stamp, _start = _first_valid_after(handle, lo - 1 if lo else 0, size)
            result['effective_start_at'] = stamp
            if stamp is not None:
                result['effective_window_days'] = round(max(0.0, newest - stamp) / guard.DAY_MS, 2)
    result['start_offset'] = result['skipped_bytes'] = lo
    result['estimated_replay_minutes'] = round((size - lo) / REFERENCE_REPLAY_BYTES_PER_S / 60, 1)
    return result


def build(journal: Path, *, max_gap_ms=guard.REGISTRY_MAX_GAP_MS, start_offset: int = 0) -> tuple:
    """Replay one journal from ``start_offset`` into an in-memory registry; returns (registry, summary). Never writes."""
    clock = {'now': 0}
    # Pruned at most once per journal hour (each prune scans every entry); the final
    # forced prune applies the 14-day retention and the entry cap exactly.
    registry = guard.TickerRegistry(None, clock=lambda: clock['now'], max_gap_ms=max_gap_ms,
                                    prune_interval_ms=guard.HOUR_MS)
    rows = invalid = registered = market_rows = held_rows = 0
    first = last = None
    with observation_journal.Reader(journal) as handle:
        handle.seek(max(0, int(start_offset)))
        for raw in handle:
            line = raw.decode('utf-8', errors='replace')
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
               'journal': journal.name, 'journal_bytes': observation_journal.total_size(journal), 'rows': rows,
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
    status, rows, coverage = guard.TickerRegistry.read_sidecar(path, reference=now)
    since = until = ahead = None
    if coverage is not None:
        _version, since, until, _resets, ahead = coverage
    current = since is not None and until is not None and now - until <= guard.REGISTRY_MAX_GAP_MS
    covered_ms = max(0.0, now - since) if current else 0.0
    return {'status': status, 'rows': rows, 'covered_since': since, 'observed_until': until,
            'coverage_current': current, 'coverage_hours': round(covered_ms / guard.HOUR_MS, 2),
            # Coverage stamped ahead of the wall clock was never observed (TICKER_COVERAGE_CLOCK_V1):
            # read as no coverage, so such a sidecar is replaceable.
            'coverage_ahead_minutes': None if not ahead else round(ahead / 60_000, 1),
            # Only a sidecar that already vouches (current coverage of 24 h or more) is kept,
            # and one that cannot be read (I/O error) is never replaced: it cannot be copied
            # first, and something (a running service) may still hold it open.
            'replaceable': (status != 'UNREADABLE'
                            and not (status == 'OK' and current and covered_ms >= MIN_COVERAGE_MS))}


def window_days_argument(value) -> float:
    days = float(value)
    if not math.isfinite(days) or days < MIN_WINDOW_DAYS:
        raise argparse.ArgumentTypeError(f'--window-days must be at least {MIN_WINDOW_DAYS:g}')
    return days


def replay_minutes_argument(value) -> float:
    minutes = float(value)
    if not math.isfinite(minutes) or minutes < MIN_REPLAY_MINUTES:
        raise argparse.ArgumentTypeError(f'--max-replay-minutes must be at least {MIN_REPLAY_MINUTES:g}')
    return minutes


def main(argv=None, *, clock=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--journal', required=True, type=Path,
                        help=('training observations.jsonl, read in place (read only); services stopped, or on '
                              'the first deploy running with --out outside .runtime'))
    parser.add_argument('--out', required=True, type=Path, help='new sidecar path; must not exist')
    parser.add_argument('--replace-stale', action='store_true',
                        help=('replace an existing --out only when its coverage at the wall clock is not '
                              'current or under 24 h, is stamped ahead of the wall clock, or it is corrupt; a '
                              'copy is kept as <out>.replaced-<ms> and its sightings are merged. Like the first '
                              'run, only after start_local_paper.ps1 -Action Stop has completed'))
    parser.add_argument('--window-days', type=window_days_argument, default=DEFAULT_WINDOW_DAYS,
                        help=(f'replay only the journal rows of the last N days before its newest row (default '
                              f'{DEFAULT_WINDOW_DAYS:g}: 14-day retention plus the 24 h coverage span; at least '
                              f'{MIN_WINDOW_DAYS:g}); about 4 min per replayed journal day'))
    parser.add_argument('--max-replay-minutes', type=replay_minutes_argument, default=DEFAULT_MAX_REPLAY_MINUTES,
                        help=(f'start the replay no earlier than this many minutes of journal at the reference '
                              f'{REFERENCE_REPLAY_BYTES_PER_S / 1e6:g} MB/s before its end (default '
                              f'{DEFAULT_MAX_REPLAY_MINUTES:g}, inside the 60-minute coverage window)'))
    parser.add_argument('--full-journal', action='store_true',
                        help='replay the journal from its first byte (slower; the window and the budget are ignored)')
    args = parser.parse_args(argv)
    journal, out = args.journal, args.out
    read_clock = clock or (lambda: int(time.time() * 1000))
    now = read_clock()
    started_monotonic = time.monotonic()
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
    window = replay_window(journal, None if args.full_journal else args.window_days,
                           max_bytes=None if args.full_journal
                           else int(args.max_replay_minutes * 60 * REFERENCE_REPLAY_BYTES_PER_S))
    registry, summary = build(journal, start_offset=window['start_offset'])
    summary['replay'] = window
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
                    'coverage_ahead_minutes': previous['coverage_ahead_minutes'],
                    'merged_new_pools': merged, 'skipped_rows': skipped}
    registry.path = out
    written = registry.save(summary['last_observed_at'])
    # Coverage is judged at the clock read after the replay and the write: the replay
    # itself can take long enough for the 60-minute coverage window to lapse.
    finished = read_clock()
    coverage_at_end = registry.coverage_status(finished)
    vouches = bool(written) and not coverage_at_end['warming']
    observed_until = coverage_at_end.get('observed_until') or summary['coverage'].get('observed_until')
    start_before = None if observed_until is None else observed_until + guard.REGISTRY_MAX_GAP_MS
    report = {**summary, 'out': str(out), 'written': written, 'replaced': replaced,
              'coverage_at_now': coverage_at_end,
              'clock': {'started_at': now, 'finished_at': finished,
                        'elapsed_seconds': round(max(0, finished - now) / 1000, 1),
                        'replay_seconds': round(time.monotonic() - started_monotonic, 1)},
              'start_services_before_utc': iso_utc(start_before),
              'vouches_at_end': vouches,
              'save_error': registry.status()['save_error'],
              'services_stopped_check': 'NO_UNSTOPPED_SERVICES_MANIFEST',
              'verify_after_start': VERIFY_AFTER_START}
    if written and not vouches:
        report['warning'] = ('the sidecar was written (its sightings count) but its coverage at the end of the '
                             'run is lapsed or under 24 h: every registry warms for 24 h unless a sibling '
                             'vouches; a rerun cannot add coverage the journal does not hold '
                             '(docs/PAPER_RUNBOOK.md, first deploy)')
    print(json.dumps(report, indent=2))
    if not written:
        return 1
    return 0 if vouches else EXIT_NOT_VOUCHING


if __name__ == '__main__':
    raise SystemExit(main())
