#!/usr/bin/env python3
"""Honest, read-only PAPER edge report from copies of engine and Strategy Lab ledgers.

Reads ``state.json`` engine ledgers (main or per-user accounts) and
``strategy_lab.json`` Lab ledgers, dedupes closed trades across copies, and
prints descriptive statistics per account, per Lab book, per strategy/policy
version and per exit reason: wins/losses/breakeven on a NET basis, average net
win and loss, expectancy, profit factor (null below ten losses), max drawdown
of the closed-trade equity path per session / Lab reset segment (never across
a reset), modeled costs versus gross mark move and an implied gross, hold
times, throughput, distinct mints and (mint, pool, hour) clusters, a
cluster-bootstrap confidence interval of expectancy and a +50 bps per leg cost
stress.

It never modifies its inputs and never claims profitability: every section
carries an evidence status that says ``insufficient data`` below the
documented thresholds, and a sufficient sample is only ever labelled
descriptive. Point it at copies of ledgers (archive or backup directories),
not at files an engine is still writing.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys
from typing import Any, Iterable

VERSION = 'PAPER_EDGE_REPORT_V1'
# Thresholds mirror docs/STRATEGY_VALIDATION.md acceptance defaults: 20 closed
# trades, 30 episode clusters and five calendar days before any statistic is
# more than a description of the supplied window.
MIN_CLOSED_TRADES = 20
MIN_CLUSTERS = 30
MIN_CALENDAR_DAYS = 5
MIN_LOSSES_FOR_PROFIT_FACTOR = 10
MIN_CLUSTERS_FOR_BOOTSTRAP = 10
COST_ONLY_GROSS_TOLERANCE_PCT = 0.5
STRESS_BPS_PER_LEG = 50
STRESS_LEGS = 2
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
DISCLAIMER = ('Descriptive PAPER statistics only. This report is not a profitability claim, '
              'not a forecast and not a promotion decision; modeled fills and costs are not real fills.')

_UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_UUID_ANY_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
# Only for explicit operator labels: a long hex id typed as a label is shortened too.
_HEX_RE = re.compile(r'^[0-9a-fA-F]{16,}$')
# Per-user engine roots are <runtime>/users/<account-uuid>/...; the directory name is case-sensitive.
USERS_DIR = 'users'
# A gateway-created per-user session id names its owner: USER-<first 8 of the account uuid>-<stamp>.
_USER_SESSION_RE = re.compile(r'^USER-([0-9a-fA-F]{8})-')
# Files that mark a live runtime directory; inputs and outputs under one are refused without --allow-runtime.
RUNTIME_MARKER_FILE = 'user_accounts.json'
RUNTIME_DIR_NAME = '.runtime'
LAB_BALANCE_TOLERANCE_USD = 0.01


def finite(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def iso(ms: float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_stamp(text: str | None) -> int | None:
    """Accept epoch milliseconds or ISO 8601 (naive values are UTC)."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if re.fullmatch(r'-?\d{11,}', raw):
        return int(raw)
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f'cannot parse timestamp {text!r}; use ISO 8601 or epoch milliseconds') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def short_account_label(name: str) -> str:
    """Never expose a full account UUID: keep eight characters only."""
    if _UUID_RE.match(name) or _HEX_RE.match(name):
        return f'user_{name[:8]}'
    return name


def scrub(text: Any) -> str:
    """Shorten every UUID inside free text (paths, messages) to its first eight characters."""
    return _UUID_ANY_RE.sub(lambda match: match.group(0)[:8] + '...', str(text))


def display_path(path: Path | str) -> str:
    return scrub(path)


def derive_account_label(path: Path) -> str | None:
    """Label from the directory layout only, never from an arbitrary ancestor.

    ``.../users/<account-uuid>/...`` is that user's ledger (the component
    directly under the last ``users`` directory must be a pure UUID); a ledger
    with no ``users`` directory above it is ``main``. A ledger under ``users``
    whose next component is not a UUID returns ``None`` so the caller can refuse
    and ask for an explicit label instead of guessing.
    """
    parents = path.parts[:-1]
    positions = [index for index, part in enumerate(parents) if part == USERS_DIR]
    if not positions:
        return 'main'
    index = positions[-1]
    if index + 1 < len(parents) and _UUID_RE.match(parents[index + 1]):
        return short_account_label(parents[index + 1])
    return None


def session_owner(session_id: str | None) -> str | None:
    """``user_<8>`` when a session id names its owner (gateway USER-<8>-... ids), else None."""
    match = _USER_SESSION_RE.match(session_id or '')
    return f'user_{match.group(1)}' if match else None


def runtime_marker(path: Path) -> Path | None:
    """The live-runtime directory an input/output path resolves under, if any.

    A directory is treated as live runtime when it is named ``.runtime`` or
    holds a ``user_accounts.json`` registry (the gateway's account store).
    """
    resolved = path.resolve()
    candidates = ([resolved] if resolved.is_dir() else []) + list(resolved.parents)
    for directory in candidates:
        if directory.name == RUNTIME_DIR_NAME:
            return directory
        try:
            if (directory / RUNTIME_MARKER_FILE).is_file():
                return directory
        except OSError:
            continue
    return None


def runtime_marker_below(root: Path) -> Path | None:
    """A live-runtime marker inside a directory that would be scanned recursively."""
    for found in root.rglob(RUNTIME_MARKER_FILE):
        return found.parent
    for found in root.rglob(RUNTIME_DIR_NAME):
        if found.is_dir():
            return found
    return None


def parse_labelled_path(argument: str) -> tuple[str | None, Path]:
    """``label=path`` or a bare path. A Windows drive letter is not a label."""
    label, separator, rest = argument.partition('=')
    if separator and rest and len(label) > 1 and not re.match(r'^[A-Za-z]:[\\/]', argument):
        return short_account_label(label.strip()), Path(rest)
    return None, Path(argument)


def load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'{display_path(path)}: cannot read ({exc.strerror or type(exc).__name__})') from None
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{display_path(path)}: not valid JSON ({exc})') from None
    return data, hashlib.sha256(raw).hexdigest(), len(raw)


# --------------------------------------------------------------------------
# Trade normalisation
# --------------------------------------------------------------------------

def _engine_trade(row: dict[str, Any], account: str, source: str) -> dict[str, Any] | None:
    opened = finite(row.get('opened_at'))
    closed = finite(row.get('closed_at'))
    pnl = finite(row.get('pnl_usd'))
    trade_id = row.get('id')
    if (not isinstance(trade_id, str) or not trade_id.strip() or opened is None or closed is None
            or pnl is None or opened <= 0 or closed < opened):
        return None
    if row.get('exit_state', 'CLOSED') != 'CLOSED':
        return None
    notional = finite(row.get('original_notional_usd')) or finite(row.get('notional_usd')) or 0.0
    pct = finite(row.get('pnl_pct'))
    if pct is None:
        pct = pnl / notional * 100 if notional else None
    market_entry = finite(row.get('market_entry_price')) or finite(row.get('entry_price'))
    exit_mark = finite(row.get('exit_price'))
    quantity = finite(row.get('quantity'), 0.0) or 0.0
    partial_legs = row.get('partial_fills') or []
    has_partials = isinstance(partial_legs, list) and len(partial_legs) > 1
    gross_mark_usd = None
    if market_entry and exit_mark and notional and not has_partials:
        gross_mark_usd = notional * (exit_mark / market_entry - 1)
    entry_rt_pct = finite(row.get('entry_roundtrip_pnl_pct'))
    net_proceeds = finite(row.get('exit_net_proceeds_usd'))
    exit_leg_usd = None
    if quantity and exit_mark and net_proceeds is not None and not has_partials:
        exit_leg_usd = quantity * exit_mark - net_proceeds
    exit_gross = finite(row.get('exit_gross_proceeds_usd'))
    exit_impact_pct = finite(row.get('exit_price_impact_pct'), 0.0) or 0.0
    exit_slip_pct = finite(row.get('exit_slippage_pct'), 0.0) or 0.0
    exit_impact_usd = None
    if exit_gross is not None:
        exit_impact_usd = exit_gross * (exit_impact_pct + exit_slip_pct) / 100
    return {
        'family': 'ENGINE', 'account': account, 'source': source, 'id': trade_id,
        'book': None, 'session': str(row.get('session_id') or 'UNKNOWN'),
        'policy': ' | '.join([str(row.get('strategy_id') or 'UNKNOWN'),
                              'entry=' + str(row.get('entry_policy_version') or 'legacy_unknown'),
                              'exit=' + str(row.get('exit_policy_version') or 'legacy_unknown')]),
        'exit_reason': str(row.get('exit_reason') or 'UNKNOWN'),
        'mint': str(row.get('address') or 'UNKNOWN'), 'pool': str(row.get('pairAddress') or 'UNKNOWN'),
        'opened_at': opened, 'closed_at': closed, 'hold_s': (closed - opened) / 1000,
        'notional_usd': notional, 'pnl_usd': pnl, 'pnl_pct': pct,
        'gross_mark_usd': gross_mark_usd,
        'entry_roundtrip_pct': entry_rt_pct,
        'entry_roundtrip_cost_usd': (-entry_rt_pct / 100 * notional) if entry_rt_pct is not None and notional else None,
        'entry_fixed_usd': ((finite(row.get('entry_dex_fee_usd'), 0.0) or 0.0)
                            + (finite(row.get('entry_network_fee_usd'), 0.0) or 0.0)
                            + (finite(row.get('entry_account_reserve_usd'), 0.0) or 0.0)),
        'exit_fees_usd': ((finite(row.get('exit_dex_fee_usd'), 0.0) or 0.0)
                          + (finite(row.get('exit_network_fee_usd'), 0.0) or 0.0)),
        'exit_impact_usd': exit_impact_usd,
        'exit_leg_usd': exit_leg_usd,
        'has_partials': has_partials,
    }


def _lab_trade(row: dict[str, Any], book_id: str, group: str, source: str) -> dict[str, Any] | None:
    opened = finite(row.get('opened_at'))
    closed = finite(row.get('closed_at'))
    pnl = finite(row.get('pnl_usd'))
    mint = row.get('address')
    pool = row.get('pairAddress')
    if (opened is None or closed is None or pnl is None or opened <= 0 or closed < opened
            or not isinstance(mint, str) or not mint or not isinstance(pool, str) or not pool):
        return None
    trade_no = row.get('trade_no')
    trade_id = f'{book_id}:{trade_no}:{mint}:{pool}:{int(opened)}'
    notional = finite(row.get('notional_usd')) or 0.0
    pct = finite(row.get('pnl_pct'))
    if pct is None:
        pct = pnl / notional * 100 if notional else None
    entry_mark = finite(row.get('entry_price'))
    exit_mark = finite(row.get('exit_price'))
    partials = row.get('partial_exits') or []
    partials = partials if isinstance(partials, list) else []
    gross_mark_usd = None
    if entry_mark and exit_mark and notional:
        remaining = finite(row.get('remaining_fraction'), 1.0)
        if remaining is None:
            remaining = 1.0
        move = remaining * (exit_mark / entry_mark - 1) * 100
        for leg in partials:
            if isinstance(leg, dict):
                move += (finite(leg.get('fraction_of_original'), 0.0) or 0.0) * (finite(leg.get('move_pct'), 0.0) or 0.0)
        gross_mark_usd = notional * move / 100
    entry_rt_pct = finite(row.get('entry_roundtrip_pnl_pct'))
    exit_fees = (finite(row.get('exit_dex_fee_usd'), 0.0) or 0.0) + (finite(row.get('exit_network_fee_usd'), 0.0) or 0.0)
    for leg in partials:
        if isinstance(leg, dict):
            exit_fees += (finite(leg.get('dex_fee_usd'), 0.0) or 0.0) + (finite(leg.get('network_fee_usd'), 0.0) or 0.0)
    quantity = finite(row.get('quantity'), 0.0) or 0.0
    exit_impact_usd = None
    if quantity and exit_mark:
        exit_impact_usd = quantity * exit_mark * ((finite(row.get('exit_price_impact_pct'), 0.0) or 0.0)
                                                  + (finite(row.get('exit_slippage_pct'), 0.0) or 0.0)) / 100
    basis = finite(row.get('remaining_cost_basis_usd'))
    final_leg = finite(row.get('final_leg_pnl_usd'))
    exit_leg_usd = None
    if quantity and exit_mark and basis is not None and final_leg is not None:
        exit_leg_usd = quantity * exit_mark - (basis + final_leg)
    return {
        'family': 'LAB', 'account': f'LAB:{group}', 'source': source, 'id': trade_id,
        'book': book_id, 'session': 'LAB',
        'trade_no': trade_no if isinstance(trade_no, int) and not isinstance(trade_no, bool) else None,
        'balance_after': finite(row.get('balance_after')),
        'policy': ' | '.join([f'LAB:{group}', 'entry=' + str(row.get('entry_policy_version') or 'legacy_unknown')]),
        'exit_reason': str(row.get('exit_reason') or 'UNKNOWN'),
        'mint': mint, 'pool': pool,
        'opened_at': opened, 'closed_at': closed, 'hold_s': (closed - opened) / 1000,
        'notional_usd': notional, 'pnl_usd': pnl, 'pnl_pct': pct,
        'gross_mark_usd': gross_mark_usd,
        'entry_roundtrip_pct': entry_rt_pct,
        'entry_roundtrip_cost_usd': (-entry_rt_pct / 100 * notional) if entry_rt_pct is not None and notional else None,
        'entry_fixed_usd': ((finite(row.get('entry_dex_fee_usd'), 0.0) or 0.0)
                            + (finite(row.get('entry_network_fee_usd'), 0.0) or 0.0)),
        'exit_fees_usd': exit_fees,
        'exit_impact_usd': exit_impact_usd,
        'exit_leg_usd': exit_leg_usd,
        'has_partials': bool(partials),
    }


def _peek_session(path: Path) -> str | None:
    data, _digest, _size = load_json(path)
    return str(data.get('demo_session_id') or '') if isinstance(data, dict) else None


class LedgerCollector:
    """Collect closed trades from ledger copies, counting every row once by id."""

    def __init__(self, since: int | None = None, until: int | None = None):
        self.since = since
        self.until = until
        self.trades: dict[str, dict[str, Any]] = {}
        self.conflicts: set[str] = set()
        self.sources: list[dict[str, Any]] = []
        self.rejected: dict[str, int] = defaultdict(int)
        self.duplicates = 0
        self.filtered_by_window = 0
        self.starting_balances: dict[str, float] = {}
        self.session_balances: dict[tuple[str, str], float] = {}
        # Path-derived 'main' is a fallback, not proof of ownership: remember which
        # session each unlabelled fallback ledger carried so pooling can be refused.
        self._fallback_sessions: dict[str, dict[str, str]] = defaultdict(dict)

    def _admit(self, trade: dict[str, Any]) -> None:
        if (self.since is not None and trade['closed_at'] < self.since) or \
                (self.until is not None and trade['closed_at'] > self.until):
            self.filtered_by_window += 1
            return
        existing = self.trades.get(trade['id'])
        if existing is None:
            self.trades[trade['id']] = trade
            return
        self.duplicates += 1
        same = all(existing.get(key) == trade.get(key) for key in ('pnl_usd', 'closed_at', 'opened_at', 'exit_reason', 'mint', 'pool'))
        if not same:
            self.conflicts.add(trade['id'])

    def add_engine_ledger(self, path: Path, label: str | None = None) -> None:
        data, digest, size = load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get('history'), list):
            raise ValueError(f'{display_path(path)}: not an engine ledger (expected object with history array)')
        session_id = str(data.get('demo_session_id') or 'UNKNOWN')
        account = label or self._derive_label(path, session_id)
        start = finite(data.get('demo_starting_balance_usd'))
        if start and start > 0:
            self.starting_balances.setdefault(account, start)
            self.session_balances.setdefault((account, session_id), start)
        admitted = 0
        for row in data['history']:
            if not isinstance(row, dict):
                self.rejected['malformed'] += 1
                continue
            if not row.get('closed_at'):
                self.rejected['not_closed'] += 1
                continue
            trade = _engine_trade(row, account, path.name)
            if trade is None:
                self.rejected['invalid_closed_record'] += 1
                continue
            self._admit(trade)
            admitted += 1
        self.sources.append({'kind': 'engine', 'account': account, 'file': scrub(path.name), 'sha256': digest,
                             'bytes': size, 'closed_rows': admitted, 'session': scrub(session_id),
                             'label_source': 'explicit' if label else 'path'})

    def _derive_label(self, path: Path, session_id: str) -> str:
        """Path-derived label for an unlabelled ledger, refusing anything ambiguous."""
        hint = 'pass an explicit label, e.g. --state main=PATH or --state user_3aa07b45=PATH (or --archive-dir LABEL=DIR)'
        derived = derive_account_label(path.resolve())
        if derived is None:
            raise ValueError(f'{display_path(path)}: ledger is under a "{USERS_DIR}" directory but the directory below it '
                             f'is not an account UUID, so its account is unknown; {hint}')
        owner = session_owner(session_id)
        if owner is not None and owner != derived:
            raise ValueError(f'{display_path(path)}: path implies account "{derived}" but its demo_session_id names '
                             f'"{owner}"; refusing to guess the account; {hint}')
        if derived == 'main':
            seen = self._fallback_sessions[derived]
            seen.setdefault(session_id, display_path(path))
            if len(seen) > 1:
                (first_session, first_path), (other_session, other_path) = list(seen.items())[:2]
                raise ValueError(
                    f'unlabelled ledgers {first_path} (session {scrub(first_session)}) and {other_path} '
                    f'(session {scrub(other_session)}) both resolve to account "main" only because neither is under '
                    f'{USERS_DIR}/<account-uuid>/; they may belong to different accounts and would be pooled. {hint}')
        return derived

    def add_lab_ledger(self, path: Path) -> None:
        data, digest, size = load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get('books'), dict):
            raise ValueError(f'{display_path(path)}: not a Strategy Lab ledger (expected object with books)')
        admitted = 0
        for book_id, book in data['books'].items():
            if not isinstance(book, dict):
                self.rejected['malformed_book'] += 1
                continue
            group = str(book.get('portfolio_group') or 'TEST')
            start = finite(book.get('starting_balance'))
            if start and start > 0:
                self.starting_balances.setdefault(f'LAB_BOOK:{book_id}', start)
            for row in book.get('history') or []:
                if not isinstance(row, dict):
                    self.rejected['malformed'] += 1
                    continue
                if not row.get('closed_at'):
                    self.rejected['not_closed'] += 1
                    continue
                trade = _lab_trade(row, str(book_id), group, path.name)
                if trade is None:
                    self.rejected['invalid_closed_record'] += 1
                    continue
                self._admit(trade)
                admitted += 1
        self.sources.append({'kind': 'lab', 'file': scrub(path.name), 'sha256': digest, 'bytes': size,
                             'closed_rows': admitted, 'books': len(data['books'])})

    def add_archive_dir(self, root: Path, label: str | None = None) -> int:
        """Scan for ledger copies. ``label`` applies to engine ledgers that are not under
        ``users/<account-uuid>/``; those keep the account named by their directory."""
        found = 0
        for path in sorted(root.rglob('*.json')):
            if path.name == 'state.json':
                derived = derive_account_label(path.resolve())
                explicit = label if label is not None and derived == 'main' else None
                if explicit is not None:
                    # A directory label is broad; still refuse a ledger whose session names another user.
                    owner = session_owner(_peek_session(path))
                    if owner is not None and owner != explicit:
                        raise ValueError(f'{display_path(path)}: --archive-dir label "{explicit}" conflicts with its '
                                         f'demo_session_id owner "{owner}"; give that file its own --state label')
                self.add_engine_ledger(path, explicit)
                found += 1
            elif path.name == 'strategy_lab.json':
                self.add_lab_ledger(path)
                found += 1
        return found

    def unique_trades(self) -> list[dict[str, Any]]:
        rows = [trade for trade_id, trade in self.trades.items() if trade_id not in self.conflicts]
        rows.sort(key=lambda trade: (trade['closed_at'], trade['id']))
        return rows


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def cluster_key(trade: dict[str, Any]) -> tuple[str, str, int]:
    return (trade['mint'], trade['pool'], int(trade['opened_at'] // HOUR_MS))


def cluster_bootstrap(trades: list[dict[str, Any]], field: str, *, iterations: int, seed: int) -> dict[str, Any]:
    clusters: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for trade in trades:
        value = trade.get(field)
        if value is not None:
            clusters[cluster_key(trade)].append(value)
    keys = list(clusters)
    if len(keys) < MIN_CLUSTERS_FOR_BOOTSTRAP:
        return {'status': 'insufficient data', 'clusters': len(keys),
                'minimum_clusters': MIN_CLUSTERS_FOR_BOOTSTRAP, 'ci95': None}
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        picked = [keys[rng.randrange(len(keys))] for _ in keys]
        total = 0.0
        count = 0
        for key in picked:
            total += sum(clusters[key])
            count += len(clusters[key])
        means.append(total / count)
    means.sort()
    low = means[max(0, int(math.floor(0.025 * (len(means) - 1))))]
    high = means[min(len(means) - 1, int(math.ceil(0.975 * (len(means) - 1))))]
    return {'status': 'computed', 'clusters': len(keys), 'iterations': iterations, 'seed': seed,
            'ci95': [round(low, 6), round(high, 6)],
            'note': 'Clusters are (mint, pool, UTC hour) resampled with replacement; clusters can still be correlated.'}


def segment_key(trade: dict[str, Any]) -> str:
    """One equity path per ledger session: engine (account, session) or Lab (book, reset segment)."""
    return trade.get('segment') or f"{trade['account']}|{trade['session']}"


def assign_segments(trades: list[dict[str, Any]], collector: 'LedgerCollector') -> None:
    """Tag every trade with the equity segment it belongs to and that segment's starting balance.

    Engine rows carry ``session_id``; a reset starts a new session, so each
    (account, session) is its own path starting at that session's recorded
    ``demo_starting_balance_usd``. A Lab book keeps one id across resets, so a
    reset is detected from the book itself: ``trade_no`` restarting, or the
    implied balance before a trade (``balance_after - pnl_usd``) jumping back
    to the book's starting balance instead of continuing from the previous
    ``balance_after``. A Lab segment starts at its first trade's implied
    balance when ``balance_after`` is recorded.
    """
    lab_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        if trade['family'] == 'ENGINE':
            account, session = trade['account'], trade['session']
            trade['segment'] = f'{account}|{session}'
            trade['segment_start'] = (collector.session_balances.get((account, session))
                                      or collector.starting_balances.get(account))
        else:
            lab_by_book[str(trade['book'])].append(trade)
    for book, rows in lab_by_book.items():
        rows.sort(key=lambda trade: (trade['closed_at'], trade['id']))
        book_start = collector.starting_balances.get(f'LAB_BOOK:{book}')
        segment = 0
        previous: dict[str, Any] | None = None
        start: float | None = None
        for trade in rows:
            implied_before = (trade['balance_after'] - trade['pnl_usd']) if trade.get('balance_after') is not None else None
            restart = previous is None
            if previous is not None:
                if trade.get('trade_no') is not None and previous.get('trade_no') is not None \
                        and trade['trade_no'] <= previous['trade_no']:
                    restart = True
                elif (implied_before is not None and previous.get('balance_after') is not None and book_start
                      and abs(implied_before - previous['balance_after']) > LAB_BALANCE_TOLERANCE_USD
                      and abs(implied_before - book_start) <= LAB_BALANCE_TOLERANCE_USD):
                    restart = True
            if restart:
                segment += 1
                start = implied_before if implied_before is not None and implied_before > 0 else book_start
            trade['segment'] = f'LAB:{book}|segment-{segment}'
            trade['segment_start'] = start
            previous = trade


def _segment_drawdown(rows: list[dict[str, Any]], start: float | None) -> tuple[float, float | None]:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    worst_pct = 0.0 if start and start > 0 else None
    for trade in rows:  # already sorted by closed_at
        equity += trade['pnl_usd']
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
        if worst_pct is not None and start + peak > 0:
            worst_pct = max(worst_pct, (peak - equity) / (start + peak) * 100)
    return worst, worst_pct


def drawdown(trades: list[dict[str, Any]], starting_balance: float | None) -> dict[str, Any]:
    """Worst drawdown over separate equity segments; never one path across reset boundaries."""
    segments: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        segments.setdefault(segment_key(trade), []).append(trade)
    worst: tuple[float, float | None, str] | None = None
    worst_pct_any: float | None = None
    for key, rows in segments.items():
        start = rows[0].get('segment_start')
        if not start or start <= 0:
            start = starting_balance if starting_balance and starting_balance > 0 else None
        usd, pct = _segment_drawdown(rows, start)
        if worst is None or usd > worst[0]:
            worst = (usd, pct, key)
        if pct is not None:
            worst_pct_any = pct if worst_pct_any is None else max(worst_pct_any, pct)
    return {'max_drawdown_usd': round(worst[0], 6) if worst else 0.0,
            'max_drawdown_pct_of_peak': _round(worst[1]) if worst else None,
            'worst_segment': scrub(worst[2]) if worst else None,
            'max_drawdown_pct_of_peak_any_segment': _round(worst_pct_any),
            'segments': len(segments),
            'basis': ('worst single segment (engine account+session, or Lab book between resets) of the closed-trade '
                      'equity path ordered by close time, % of that segment\'s peak (segment starting balance plus its '
                      'peak cumulative PnL); segments are never concatenated; open positions and intratrade excursions excluded')}


def measure(trades: list[dict[str, Any]], *, starting_balance: float | None, iterations: int, seed: int) -> dict[str, Any]:
    trades = sorted(trades, key=lambda trade: (trade['closed_at'], trade['id']))
    count = len(trades)
    pnls = [trade['pnl_usd'] for trade in trades]
    pcts = [trade['pnl_pct'] for trade in trades if trade['pnl_pct'] is not None]
    wins = [trade for trade in trades if trade['pnl_usd'] > 0]
    losses = [trade for trade in trades if trade['pnl_usd'] < 0]
    gross_profit = sum(trade['pnl_usd'] for trade in wins)
    gross_loss = -sum(trade['pnl_usd'] for trade in losses)
    if len(losses) >= MIN_LOSSES_FOR_PROFIT_FACTOR:
        profit_factor = round(gross_profit / gross_loss, 6)
        pf_status = 'defined'
    elif losses:
        profit_factor, pf_status = None, f'insufficient data: fewer than {MIN_LOSSES_FOR_PROFIT_FACTOR} losses'
    else:
        profit_factor, pf_status = None, 'insufficient data: no losses'

    # Cost decomposition versus the gross mark move; only trades with a usable
    # mark path and without partial legs enter the identity.
    decomposable = [trade for trade in trades if trade['gross_mark_usd'] is not None and not trade['has_partials']]
    gross_mark = sum(trade['gross_mark_usd'] for trade in decomposable)
    net_of_decomposable = sum(trade['pnl_usd'] for trade in decomposable)
    entry_rt = [trade['entry_roundtrip_cost_usd'] for trade in trades if trade['entry_roundtrip_cost_usd'] is not None]
    exit_impact = [trade['exit_impact_usd'] for trade in trades if trade['exit_impact_usd'] is not None]
    exit_leg = [trade['exit_leg_usd'] for trade in decomposable if trade['exit_leg_usd'] is not None]
    total_cost = gross_mark - net_of_decomposable if decomposable else None
    if total_cost is None:
        identity_status = 'n/a: no decomposable trades'
    elif total_cost < 0:
        identity_status = ('unreliable: negative total modeled cost; exit marks (e.g. STALE_MARKET_EXIT closes) '
                           'can lag the fill, use the implied gross and the components instead')
    else:
        identity_status = 'consistent (non-negative)'
    stale_in_identity = sum(1 for trade in decomposable if 'STALE' in trade['exit_reason'].upper())
    implied = [trade for trade in trades if trade['entry_roundtrip_cost_usd'] is not None]
    implied_gross = sum(trade['pnl_usd'] + trade['entry_roundtrip_cost_usd'] for trade in implied)

    cost_only = 0
    cost_only_basis = 0
    for trade in losses:
        if trade['entry_roundtrip_pct'] is not None and trade['pnl_pct'] is not None:
            reference = trade['pnl_pct'] - trade['entry_roundtrip_pct']
        elif trade['gross_mark_usd'] is not None and trade['notional_usd']:
            reference = trade['gross_mark_usd'] / trade['notional_usd'] * 100
        else:
            continue
        cost_only_basis += 1
        if abs(reference) <= COST_ONLY_GROSS_TOLERANCE_PCT:
            cost_only += 1

    first_open = min((trade['opened_at'] for trade in trades), default=None)
    last_close = max((trade['closed_at'] for trade in trades), default=None)
    span_hours = (last_close - first_open) / HOUR_MS if first_open is not None and last_close is not None else None
    rate_hours = max(span_hours, 1.0) if span_hours is not None else None
    days = {int(trade['closed_at'] // DAY_MS) for trade in trades}
    clusters = {cluster_key(trade) for trade in trades}
    mints = {trade['mint'] for trade in trades}
    accounts = sorted({scrub(trade['account']) for trade in trades})
    books = sorted({str(trade['book']) for trade in trades if trade.get('book') is not None})

    stress_usd = [trade['pnl_usd'] - trade['notional_usd'] * STRESS_BPS_PER_LEG * STRESS_LEGS / 10_000 for trade in trades]
    stress_pct = [pct - STRESS_BPS_PER_LEG * STRESS_LEGS / 100 for pct in pcts]

    missing = []
    if count < MIN_CLOSED_TRADES:
        missing.append(f'closed trades {count} < {MIN_CLOSED_TRADES}')
    if len(clusters) < MIN_CLUSTERS:
        missing.append(f'(mint,pool,hour) clusters {len(clusters)} < {MIN_CLUSTERS}')
    if len(days) < MIN_CALENDAR_DAYS:
        missing.append(f'calendar days {len(days)} < {MIN_CALENDAR_DAYS}')
    status = 'insufficient data' if missing else 'descriptive sample (no edge claim)'

    return {
        'closed_trades': count,
        'wins': len(wins), 'losses': len(losses), 'breakeven': count - len(wins) - len(losses),
        'basis': 'net after all modeled costs (pnl_usd / pnl_pct as recorded)',
        'win_rate_pct': _round(len(wins) / count * 100) if count else None,
        'net_pnl_usd': round(sum(pnls), 6),
        'avg_net_win_usd': _round(_mean(trade['pnl_usd'] for trade in wins)),
        'avg_net_win_pct': _round(_mean(trade['pnl_pct'] for trade in wins if trade['pnl_pct'] is not None)),
        'avg_net_loss_usd': _round(_mean(trade['pnl_usd'] for trade in losses)),
        'avg_net_loss_pct': _round(_mean(trade['pnl_pct'] for trade in losses if trade['pnl_pct'] is not None)),
        'expectancy_usd_per_trade': _round(_mean(pnls)),
        'expectancy_pct_per_trade': _round(_mean(pcts)),
        'expectancy_usd_ci95_cluster_bootstrap': cluster_bootstrap(trades, 'pnl_usd', iterations=iterations, seed=seed),
        'expectancy_pct_ci95_cluster_bootstrap': cluster_bootstrap(trades, 'pnl_pct', iterations=iterations, seed=seed),
        'cost_stress_plus_50bps_per_leg': {
            'expectancy_usd_per_trade': _round(_mean(stress_usd)),
            'expectancy_pct_per_trade': _round(_mean(stress_pct)),
            'net_pnl_usd': _round(sum(stress_usd)),
            'basis': f'{STRESS_BPS_PER_LEG} bps on entry notional per leg, {STRESS_LEGS} legs, subtracted from each recorded net result',
        },
        'profit_factor': profit_factor, 'profit_factor_status': pf_status,
        'gross_profit_usd': round(gross_profit, 6), 'gross_loss_usd': round(gross_loss, 6),
        'drawdown': drawdown(trades, starting_balance),
        'costs': {
            'decomposable_trades': len(decomposable),
            'excluded_partial_or_unmarked': count - len(decomposable),
            'gross_mark_move_usd': _round(gross_mark) if decomposable else None,
            'net_pnl_of_decomposable_usd': _round(net_of_decomposable) if decomposable else None,
            'total_modeled_cost_usd': _round(total_cost),
            'mark_identity_status': identity_status,
            'mark_identity_reliable': total_cost is not None and total_cost >= 0,
            'stale_exit_trades_in_identity': stale_in_identity,
            'implied_gross_usd': _round(implied_gross) if implied else None,
            'implied_gross_trades': len(implied),
            'implied_gross_definition': ('sum of net PnL minus the entry round-trip quote PnL (net + entry round-trip cost); '
                                         'the move after the entry quote, independent of exit marks'),
            'entry_roundtrip_quote_cost_usd': _round(sum(entry_rt)) if entry_rt else None,
            'entry_roundtrip_quote_cost_trades': len(entry_rt),
            'exit_impact_and_slippage_usd': _round(sum(exit_impact)) if exit_impact else None,
            'exit_fees_usd': _round(sum(trade['exit_fees_usd'] for trade in trades)),
            'entry_fixed_fees_usd': _round(sum(trade['entry_fixed_usd'] for trade in trades)),
            'exit_leg_mark_minus_net_proceeds_usd': _round(sum(exit_leg)) if exit_leg else None,
            'cost_only_losses': cost_only,
            'cost_only_loss_share_pct': _round(cost_only / cost_only_basis * 100) if cost_only_basis else None,
            'cost_only_definition': (f'loss whose gross reference (net minus entry round-trip cost, else mark move) is within '
                                     f'+/-{COST_ONLY_GROSS_TOLERANCE_PCT}% of zero'),
            'note': ('total modeled cost = gross mark move - net; components overlap and are not additive. '
                     'Marks at stale-exit closes can be stale, so the component view and the implied gross are more '
                     'reliable than the mark identity there; a negative total is flagged unreliable.'),
        },
        'median_hold_s': _round(statistics.median(trade['hold_s'] for trade in trades), 3) if trades else None,
        'period': {'first_open': iso(first_open), 'last_close': iso(last_close),
                   'span_hours': _round(span_hours, 3), 'calendar_days': len(days)},
        'trades_per_hour': _round(count / rate_hours) if rate_hours else None,
        'trades_per_day': _round(count / rate_hours * 24) if rate_hours else None,
        'rate_basis': 'closed trades over the first-open to last-close span (minimum one hour); describes this window only',
        'distinct_mints': len(mints),
        'distinct_mint_pool_hour_clusters': len(clusters),
        'sessions': len({trade['session'] for trade in trades}),
        'account_count': len(accounts),
        'accounts': accounts,
        'book_count': len(books),
        'books': books,
        'evidence_status': status,
        'evidence_missing': missing,
    }


def group_by(trades: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade[key])].append(trade)
    return dict(sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])))


def build_report(collector: LedgerCollector, *, iterations: int, seed: int) -> dict[str, Any]:
    trades = collector.unique_trades()
    assign_segments(trades, collector)
    engine = [trade for trade in trades if trade['family'] == 'ENGINE']
    lab = [trade for trade in trades if trade['family'] == 'LAB']

    def section(rows: dict[str, list[dict[str, Any]]], balance_prefix: str | None = None) -> dict[str, Any]:
        result = {}
        for name, items in rows.items():
            balance = collector.starting_balances.get(f'{balance_prefix}{name}') if balance_prefix is not None else None
            result[name] = measure(items, starting_balance=balance, iterations=iterations, seed=seed)
        return result

    engine_by_account_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in engine:
        engine_by_account_reason[f"{trade['account']} / {trade['exit_reason']}"].append(trade)
    engine_by_account_reason = dict(sorted(engine_by_account_reason.items(), key=lambda item: (-len(item[1]), item[0])))

    return {
        'version': VERSION, 'paper_only': True, 'read_only': True, 'profitability_claim': False,
        'disclaimer': DISCLAIMER,
        'generated_at': iso(datetime.now(timezone.utc).timestamp() * 1000),
        'thresholds': {'minimum_closed_trades': MIN_CLOSED_TRADES, 'minimum_clusters': MIN_CLUSTERS,
                       'minimum_calendar_days': MIN_CALENDAR_DAYS,
                       'minimum_losses_for_profit_factor': MIN_LOSSES_FOR_PROFIT_FACTOR,
                       'minimum_clusters_for_bootstrap': MIN_CLUSTERS_FOR_BOOTSTRAP,
                       'cost_stress_bps_per_leg': STRESS_BPS_PER_LEG},
        'window': {'since': iso(collector.since), 'until': iso(collector.until),
                   'filtered_out_by_window': collector.filtered_by_window},
        'sources': collector.sources,
        'dedupe': {'unique_closed_trades': len(trades), 'duplicate_copies_skipped': collector.duplicates,
                   'conflicting_ids_excluded': len(collector.conflicts),
                   'rejected_rows': dict(collector.rejected),
                   'engine_key': 'trade id', 'lab_key': 'book:trade_no:mint:pool:opened_at'},
        'engine': {
            'unique_closed_trades': len(engine),
            'by_account': section(group_by(engine, 'account'), balance_prefix=''),
            'by_policy_version': section(group_by(engine, 'policy')),
            'by_exit_reason': section(group_by(engine, 'exit_reason')),
            'by_account_and_exit_reason': section(engine_by_account_reason),
            'note': ('by_account (and by_account_and_exit_reason) rows are one PAPER account each and are never pooled. '
                     'by_policy_version and by_exit_reason rows are cross-account aggregates of every account listed in '
                     "the row's 'accounts' field: they describe the pooled trades, not any one account's result, and their "
                     'drawdown is the worst single (account, session) segment.'),
        },
        'lab': {
            'unique_closed_trades': len(lab),
            'by_book': section(group_by(lab, 'book'), balance_prefix='LAB_BOOK:'),
            'by_policy_version': section(group_by(lab, 'policy')),
            'by_exit_reason': section(group_by(lab, 'exit_reason')),
            'note': ('Each Lab book owns its capital; by_book rows are measured separately, with drawdown segmented at '
                     "book resets. by_policy_version and by_exit_reason rows pool the books listed in each row's 'books' "
                     'field and are cross-book aggregates, not the result of any one book.'),
        },
        'limitations': [
            DISCLAIMER,
            'Shared tokens, a shared market window and the same signal traded in several accounts correlate outcomes; n is not an independent sample size.',
            'Cluster bootstrap intervals are diagnostics; they do not correct for multiple strategy comparisons or regime change.',
            'Profit factor is null below ten losses; small groups are reported as insufficient data regardless of sign.',
            'Modeled costs are the PAPER execution model, not observed on-chain fees; the +50 bps per leg stress is a sensitivity check, not a fee forecast.',
            'Drawdown uses closed trades only, per segment (engine account+session, Lab book between resets); open positions and intratrade excursions are excluded.',
            'Pooled policy/exit-reason rows mix accounts or books (see their accounts/books fields); only by_account and by_book rows describe one ledger.',
        ],
    }


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return 'n/a'
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value)


def _ci(block: dict[str, Any]) -> str:
    if block.get('ci95'):
        low, high = block['ci95']
        return f'[{low:.2f}, {high:.2f}] (k={block["clusters"]})'
    return f"{block.get('status', 'n/a')} (k={block.get('clusters', 0)})"


TABLE_HEADER = ('| group | accounts | n | W/L/BE | win % | avg win $ | avg loss $ | exp $/trade | exp %/trade | '
                'exp $ CI95 (cluster bootstrap) | exp $ +50 bps/leg | PF | max DD $ (% of segment peak, segments) | '
                'modeled cost $ / gross mark $ | implied gross $ (net - entry RT) | '
                'cost-only loss % | median hold s | /hour | /day | mints | clusters | period | status |\n'
                '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n')


def _who(stats: dict[str, Any]) -> str:
    names = stats.get('books') or stats.get('accounts') or []
    shown = ', '.join(names[:4]) + (f', +{len(names) - 4}' if len(names) > 4 else '')
    return f'{len(names)}: {shown}' if names else '0'


def _row(name: str, stats: dict[str, Any]) -> str:
    costs = stats['costs']
    pf = _fmt(stats['profit_factor']) if stats['profit_factor'] is not None else f"null ({stats['profit_factor_status']})"
    period = f"{stats['period']['first_open']} .. {stats['period']['last_close']} ({stats['period']['calendar_days']} d)"
    dd = stats['drawdown']
    pct = f"{_fmt(dd['max_drawdown_pct_of_peak'])}%" if dd.get('max_drawdown_pct_of_peak') is not None else 'n/a %'
    cost = f"{_fmt(costs['total_modeled_cost_usd'])} / {_fmt(costs['gross_mark_move_usd'])}"
    if costs['total_modeled_cost_usd'] is not None and not costs.get('mark_identity_reliable', True):
        cost += ' UNRELIABLE (negative cost; stale exit marks)'
    return (f"| {scrub(name)} | {_who(stats)} | {stats['closed_trades']} | {stats['wins']}/{stats['losses']}/{stats['breakeven']} | "
            f"{_fmt(stats['win_rate_pct'], 1)} | {_fmt(stats['avg_net_win_usd'])} | {_fmt(stats['avg_net_loss_usd'])} | "
            f"{_fmt(stats['expectancy_usd_per_trade'], 3)} | {_fmt(stats['expectancy_pct_per_trade'], 3)} | "
            f"{_ci(stats['expectancy_usd_ci95_cluster_bootstrap'])} | "
            f"{_fmt(stats['cost_stress_plus_50bps_per_leg']['expectancy_usd_per_trade'], 3)} | {pf} | "
            f"{_fmt(dd['max_drawdown_usd'])} ({pct}, {dd.get('segments', 1)} seg) | "
            f"{cost} | {_fmt(costs.get('implied_gross_usd'))} | "
            f"{_fmt(costs['cost_only_loss_share_pct'], 1)} | {_fmt(stats['median_hold_s'], 1)} | "
            f"{_fmt(stats['trades_per_hour'], 3)} | {_fmt(stats['trades_per_day'], 2)} | {stats['distinct_mints']} | "
            f"{stats['distinct_mint_pool_hour_clusters']} | {period} | {stats['evidence_status']} |\n")


def _table(title: str, rows: dict[str, Any]) -> str:
    if not rows:
        return f'\n### {title}\n\ninsufficient data: no closed trades in this section.\n'
    return f'\n### {title}\n\n' + TABLE_HEADER + ''.join(_row(name, stats) for name, stats in rows.items())


def render_markdown(report: dict[str, Any]) -> str:
    out = [f"# PAPER edge report ({report['version']})\n\n", report['disclaimer'], '\n\n']
    dedupe = report['dedupe']
    out.append(f"Unique closed trades: {dedupe['unique_closed_trades']} "
               f"(engine {report['engine']['unique_closed_trades']}, Lab {report['lab']['unique_closed_trades']}); "
               f"duplicate copies skipped: {dedupe['duplicate_copies_skipped']}; conflicting ids excluded: "
               f"{dedupe['conflicting_ids_excluded']}; rejected rows: {dedupe['rejected_rows'] or 'none'}; "
               f"window {report['window']['since'] or 'open'} .. {report['window']['until'] or 'open'} "
               f"(filtered out {report['window']['filtered_out_by_window']}).\n\n")
    out.append('Thresholds for anything beyond a description: '
               f"{report['thresholds']['minimum_closed_trades']} closed trades, "
               f"{report['thresholds']['minimum_clusters']} (mint,pool,hour) clusters, "
               f"{report['thresholds']['minimum_calendar_days']} calendar days; profit factor needs "
               f"{report['thresholds']['minimum_losses_for_profit_factor']} losses. Rows below them say 'insufficient data'.\n")
    out.append('\n## Sources\n\n| kind | account/books | file | closed rows | sha256 |\n|---|---|---|---|---|\n')
    for source in report['sources']:
        who = source.get('account') if source['kind'] == 'engine' else f"{source.get('books')} books"
        out.append(f"| {source['kind']} | {scrub(who)} | {scrub(source['file'])} | {source['closed_rows']} | {source['sha256'][:16]}... |\n")
    out.append('\n## Engine accounts\n')
    out.append(_table('By account', report['engine']['by_account']))
    out.append('\n' + report['engine']['note'] + '\n')
    out.append(_table('By strategy / policy version (pooled across the listed accounts)', report['engine']['by_policy_version']))
    out.append(_table('By exit reason (pooled across the listed accounts)', report['engine']['by_exit_reason']))
    out.append(_table('By account and exit reason', report['engine']['by_account_and_exit_reason']))
    out.append('\n## Strategy Lab books\n')
    out.append(_table('By book', report['lab']['by_book']))
    out.append('\n' + report['lab']['note'] + '\n')
    out.append(_table('By entry policy version (pooled across the listed books)', report['lab']['by_policy_version']))
    out.append(_table('By exit reason (pooled across the listed books)', report['lab']['by_exit_reason']))
    out.append('\n## Limitations\n\n' + ''.join(f'- {line}\n' for line in report['limitations']))
    return ''.join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--state', action='append', default=[], metavar='[LABEL=]PATH',
                        help='engine state.json copy (repeatable); optional label, otherwise derived from the path '
                             '(users/<account-uuid>/... is that user, anything else is main; ambiguous inputs are refused)')
    parser.add_argument('--lab', action='append', default=[], metavar='PATH', type=Path,
                        help='full strategy_lab.json copy (repeatable)')
    parser.add_argument('--archive-dir', action='append', default=[], metavar='[LABEL=]DIR',
                        help='directory scanned recursively for state.json / strategy_lab.json copies; an optional '
                             'label applies to engine ledgers that are not under users/<account-uuid>/')
    parser.add_argument('--since', help='keep closes at or after this ISO 8601 / epoch-ms time (UTC)')
    parser.add_argument('--until', help='keep closes at or before this ISO 8601 / epoch-ms time (UTC)')
    parser.add_argument('--output', type=Path, help='write <output>.json and <output>.md (suffix .json/.md is stripped)')
    parser.add_argument('--bootstrap', type=int, default=2000, help='cluster bootstrap iterations (default 2000)')
    parser.add_argument('--seed', type=int, default=20261008, help='bootstrap seed for reproducible intervals')
    parser.add_argument('--allow-runtime', action='store_true',
                        help='allow inputs/outputs under a live runtime directory (one named .runtime or holding '
                             'user_accounts.json); refused by default, read a copy instead')
    parser.add_argument('--quiet', action='store_true', help='do not print the markdown to stdout')
    return parser


def output_paths(output: Path) -> tuple[Path, Path]:
    base = output.with_suffix('') if output.suffix.lower() in ('.json', '.md') else output
    return base.with_name(base.name + '.json'), base.with_name(base.name + '.md')


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    def fail(message: Any) -> None:
        parser.error(scrub(message))

    if not (args.state or args.lab or args.archive_dir):
        fail('give at least one --state, --lab or --archive-dir')
    if args.bootstrap < 1:
        fail('--bootstrap must be at least 1')
    try:
        since = parse_stamp(args.since)
        until = parse_stamp(args.until)
    except ValueError as exc:
        fail(exc)
    if since is not None and until is not None and since > until:
        fail('--since must not be after --until')

    states = [parse_labelled_path(argument) for argument in args.state]
    archives = [parse_labelled_path(argument) for argument in args.archive_dir]
    for _label, root in archives:
        if not root.is_dir():
            fail(f'{display_path(root)} is not a directory')
    inputs: list[Path] = ([path.resolve() for _label, path in states] + [path.resolve() for path in args.lab]
                          + [root.resolve() for _label, root in archives])
    outputs = list(output_paths(args.output)) if args.output else []

    # Every path check happens before any ledger is opened.
    if not args.allow_runtime:
        for path in inputs + outputs:
            marker = runtime_marker(path)
            if marker is not None:
                fail(f'refusing {display_path(path)}: it resolves under a live runtime directory '
                     f'({display_path(marker)} is named {RUNTIME_DIR_NAME} or holds {RUNTIME_MARKER_FILE}); '
                     'copy the ledgers elsewhere first and write the report outside the runtime, '
                     'or pass --allow-runtime deliberately')
        for _label, root in archives:
            marker = runtime_marker_below(root)
            if marker is not None:
                fail(f'refusing --archive-dir {display_path(root)}: it contains a live runtime directory '
                     f'({display_path(marker)}); scan a copy instead, or pass --allow-runtime deliberately')
    for target in outputs:
        resolved = target.resolve()
        for source in inputs:
            if resolved == source or (source.is_dir() and source in resolved.parents):
                fail(f'refusing to write {display_path(target)} inside or over an input ({display_path(source)})')

    collector = LedgerCollector(since=since, until=until)
    try:
        for label, path in states:
            collector.add_engine_ledger(path, label)
        for path in args.lab:
            collector.add_lab_ledger(path)
        for label, root in archives:
            if collector.add_archive_dir(root, label) == 0:
                print(f'warning: no state.json or strategy_lab.json under {display_path(root)}', file=sys.stderr)
    except (OSError, ValueError) as exc:
        fail(exc)

    report = build_report(collector, iterations=args.bootstrap, seed=args.seed)
    markdown = render_markdown(report)
    if outputs:
        json_path, md_path = outputs
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        md_path.write_text(markdown, encoding='utf-8')
        print(f'wrote {display_path(json_path)} and {display_path(md_path)}', file=sys.stderr)
    if not args.quiet:
        stream = sys.stdout
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(errors='replace')
            except (ValueError, OSError):
                pass
        stream.write(markdown)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
