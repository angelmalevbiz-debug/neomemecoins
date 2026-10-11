"""Persist observed PAPER heat windows, not signals, orders or inferred prices.

Each service owns a separate bounded sidecar. A short restart restores the exact
PairHistory samples/coverage/gaps; a stale, future, malformed or incompatible
snapshot starts cold. The ordinary heat veto and its per-pair gap checks remain
unchanged. A flush never advances the observation clock.
"""
from collections import OrderedDict, deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import heat_veto
from shared_snapshot_io import open_shared_text, replace_shared_snapshot

VERSION = 'PAPER_OBSERVED_HEAT_CONTINUITY_V1'
SAVE_INTERVAL_MS = 30_000
MAX_BYTES = 32 * 1024 * 1024


def config():
    return {'version': VERSION, 'save_interval_ms': SAVE_INTERVAL_MS,
            'maximum_restart_gap_ms': heat_veto.HISTORY_PARAMS.max_gap_ms,
            'maximum_sidecar_bytes': MAX_BYTES, 'scope': 'OWN_HISTORY_INCLUDING_VALIDATED_ACTUAL_SEEDS',
            'missing_or_invalid_history': 'NORMAL_FAIL_CLOSED_WARMUP',
            'flush_advances_coverage': False, 'thresholds_changed': False,
            'is_entry_authorization': False, 'profitability_proven': False}


def path_for_registry(registry_path):
    path = Path(registry_path)
    suffix = '.ticker_registry.json'
    stem = path.name[:-len(suffix)] if path.name.endswith(suffix) else path.stem
    return path.with_name(stem + '.pair_history.json')


def timestamp(value):
    number = heat_veto._finite(value)
    if number is None or number <= 0:
        raise ValueError('Invalid observation clock')
    return number


class PersistentPairHistory(heat_veto.PairHistory):
    def __init__(self, path, *, clock=None, params=heat_veto.HISTORY_PARAMS):
        super().__init__(params)
        self.path = Path(path)
        self.clock = clock or (lambda: int(time.time() * 1000))
        self._save_lock = threading.Lock()
        self._observed_until = None
        self._generation = 0
        self._saved_generation = 0
        self._last_attempt = None
        self._keep_original = False
        self.load_status = 'NO_SIDECAR'
        self.save_error = None
        self.saved_at = None
        self.saves = 0
        self._load()

    def _load(self):
        now = timestamp(self.clock())
        try:
            # Bound the actual read too: a changing file cannot bypass stat().
            with open_shared_text(self.path) as handle:
                text = handle.read(MAX_BYTES + 1)
            if len(text.encode('utf-8')) > MAX_BYTES:
                raise ValueError('Oversized heat history')
            payload = json.loads(text)
            if (not isinstance(payload, dict) or payload.get('version') != VERSION
                    or payload.get('history_version') != heat_veto.HISTORY_VERSION
                    or payload.get('parameters') != asdict(self.params)):
                raise ValueError('Incompatible heat history')
            saved_at = timestamp(payload.get('saved_at'))
            until = timestamp(payload.get('observed_until'))
            started = timestamp(payload.get('observing_since'))
            if not started <= until <= saved_at <= now:
                self.load_status = 'FUTURE_SIDECAR_IGNORED'
                return
            if now - until > self.params.max_gap_ms:
                self.load_status = 'STALE_SIDECAR_IGNORED'
                return
            rows = payload.get('pairs')
            if not isinstance(rows, list) or not 0 < len(rows) <= self.params.max_pairs:
                raise ValueError('Invalid pair count')
            restored = OrderedDict()
            for row in rows:
                key = heat_veto._identity(row)
                if key is None or key in restored or any(len(part) > 128 for part in key):
                    raise ValueError('Invalid pair identity')
                since, first, last = (timestamp(row.get(k)) for k in ('since', 'first_seen', 'last_seen'))
                paid = row.get('last_paid_at')
                if paid is not None:
                    paid = timestamp(paid)
                if (not started <= first <= until or not since <= last <= until
                        or (paid is not None and paid > last)
                        or last < now - self.params.retention_ms):
                    raise ValueError('Invalid pair coverage')
                samples, gaps = row.get('samples'), row.get('gaps')
                if (not isinstance(samples, list) or not 0 < len(samples) <= self.params.max_samples_per_pair
                        or not isinstance(gaps, list) or len(gaps) > self.params.max_gaps_per_pair):
                    raise ValueError('Invalid history bounds')
                previous = 0
                for sample in samples:
                    if not isinstance(sample, list) or len(sample) != 3:
                        raise ValueError('Invalid sample')
                    stamp, price, flag = sample
                    if (not previous < timestamp(stamp) <= last
                            or stamp < saved_at - self.params.retention_ms - self.params.max_gap_ms
                            or heat_veto._finite(price) is None or price <= 0 or type(flag) is not bool):
                        raise ValueError('Invalid sample evidence')
                    previous = stamp
                    if flag and (paid is None or paid < stamp):
                        raise ValueError('Lost paid-profile evidence')
                previous = 0
                for gap in gaps:
                    if not isinstance(gap, list) or len(gap) != 2:
                        raise ValueError('Invalid gap')
                    left, right = (timestamp(v) for v in gap)
                    if not previous <= left < right <= last or right - left <= self.params.max_gap_ms:
                        raise ValueError('Invalid gap order')
                    previous = right
                if ((gaps and since != gaps[-1][1]) or since > samples[-1][0]
                        or (since > samples[0][0] and since not in {s[0] for s in samples})):
                    raise ValueError('Invalid contiguous segment')
                restored[key] = {'samples': deque((tuple(s) for s in samples), maxlen=self.params.max_samples_per_pair),
                                 'gaps': deque((tuple(g) for g in gaps), maxlen=self.params.max_gaps_per_pair),
                                 'since': since, 'first_seen': first, 'last_seen': last, 'last_paid_at': paid}
            # Commit only after EVERY record validates. Never partially restore.
            with self._lock:
                self._pairs = restored
                self._observing_since = started
                self._observed_until = until
            self.saved_at = saved_at
            self.load_status = 'RESTORED_OBSERVATIONS'
            self.prune(now)
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            # Keep unreadable/corrupt evidence for diagnosis. Real new observations
            # still warm in memory; no disk error grants coverage or stops exits.
            self.load_status = 'INVALID_OR_UNREADABLE_SIDECAR'
            self._keep_original = True

    def observe_coin(self, coin, now):
        stored = super().observe_coin(coin, now)
        if stored:
            with self._lock:
                observed = heat_veto._finite(now)
                if observed is not None:
                    self._observed_until = max(self._observed_until or 0, observed)
                self._generation += 1
        return stored

    def observe(self, feed, now):
        count = super().observe(feed, now)
        self.flush(now=now, force=False)
        return count

    def flush(self, *, now=None, force=True):
        """Atomic bounded checkpoint; failure retains the previous complete file."""
        temporary = None
        with self._save_lock:
            try:
                current = timestamp(self.clock() if now is None else now)
                if self._keep_original:
                    return False
                if (not force and self._last_attempt is not None
                        and 0 <= current - self._last_attempt < SAVE_INTERVAL_MS):
                    return True
                self._last_attempt = current
                self.prune(current)
                with self._lock:
                    if not self._pairs or self._saved_generation == self._generation:
                        return True
                    generation = self._generation
                    horizon = current - self.params.retention_ms
                    pairs = [dict(address=key[0], pairAddress=key[1],
                                  samples=[s for s in row['samples'] if s[0] >= horizon],
                                  gaps=[g for g in row['gaps'] if g[1] >= horizon],
                                  **{k: row[k] for k in ('since', 'first_seen', 'last_seen', 'last_paid_at')})
                             for key, row in self._pairs.items()]
                    until, started = self._observed_until, self._observing_since
                if (until is None or started is None or not started <= until <= current
                        or any(row['last_seen'] > until or row['first_seen'] > until for row in pairs)):
                    raise ValueError('Future observation is not restart evidence')
                payload = dict(version=VERSION, history_version=heat_veto.HISTORY_VERSION,
                               parameters=asdict(self.params), saved_at=current, observed_until=until,
                               observing_since=started, pairs=pairs)
                encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode('utf-8')
                if len(encoded) > MAX_BYTES:
                    raise ValueError('Oversized checkpoint; refusing truncation')
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix='.' + self.path.name + '.', suffix='.tmp', dir=self.path.parent)
                temporary = Path(name)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                replace_shared_snapshot(temporary, self.path)
                self._saved_generation = generation
                self.saved_at = current
                self.saves += 1
                self.save_error = None
                return True
            except Exception as exc:
                self.save_error = type(exc).__name__
                return False
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def status(self):
        return {**super().status(), 'persistent': True,
                'continuity_version': VERSION, 'sidecar': self.path.name,
                'load_status': self.load_status, 'observed_until': self._observed_until,
                'saved_at': self.saved_at, 'saves': self.saves,
                'save_error': self.save_error, 'kept_not_overwritten': self._keep_original,
                'maximum_restart_gap_ms': self.params.max_gap_ms,
                'save_interval_ms': SAVE_INTERVAL_MS}
