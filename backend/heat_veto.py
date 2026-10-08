"""HEAT_VETO_STACK_V1: veto new PAPER entries into pools that are running hot or crashing.

PAPER only. A veto never admits an entry; it only removes candidates before any
quote, flow promotion or RugCheck call. Exits of open positions are unchanged.

Evidence (research 2026-10-08, 22.8 h of the engine's scan log; train,
holdout and a pre-registered 1.92 h forward test, calibrated net50 basis):
- Momentum family: the top quintiles of 5-min return, 5-min buy share and
  volume acceleration had the worst forward returns in every universe.
- Attention family: paid-profile ('token-profiles/latest') samples in
  100-125 bps pools lost -22.9% (train) and -24.7% (holdout) at 15 min.
- Crashes kept going (-14.6% over 15 min, train). Turnover had an IC of
  -0.13 to -0.19 with the same sign in both train halves.
- Forward test, full stack: vetoed samples -8.73% vs kept -3.66% at 30 min,
  difference -5.07 pp, pair-CI95 [-11.0, -0.95] (CONFIRMED); momentum part
  alone -5.73 pp CI [-12.1, -1.4]. Kept samples still lost -3.1% to -3.7%:
  the stack removes losers, it does not make the remaining entries profitable.

Rules (any one vetoes; every rule that fires is recorded, in this order):
  heat_history_warming       a rule's window is not covered by this process's
                             history, so the rule cannot be evaluated
                             (conservative; metrics['warming_windows'] names it):
                             'return_5m'  < 300 s of contiguous history, or no
                                          price at or before now - 300 s in it;
                             'crash_15m'  the pair was first observed here
                                          < 900 s ago and (f) does not already
                                          fire on the shorter window;
                             'paid_profile_60m'  fee tier >= 100 bps, the pair
                                          is not already known paid, and this
                                          process has observed the feed for
                                          < 3600 s (a restart forgets 'latest')
  heat_input_unknown         the current price is missing or not positive
  heat_return_5m_surge       (a) 5-min return >= +3% vs the price 300 s earlier
  heat_buy_share_5m          (b) txns.m5 buys / (buys + sells) >= 0.70
  heat_volume_acceleration   (c) volume.m5 / (volume.h1 / 12) >= 1.3
  heat_extended_move         (d) priceChange.h6 >= +200 or priceChange.h24 >= +150
  heat_paid_profile_high_fee (e) fee tier >= 100 bps and the pair carried the
                             paid-profile 'latest' source in the last 60 min
  heat_crash_in_progress     (f) price <= 75% of its 15-min high, or 5-min
                             return <= -20% (the 15-min high spans feed
                             gaps, as the research window; the 5-min return
                             needs the current contiguous segment)
  heat_turnover_5m           (g) volume.m5 / liquidityUsd >= 0.095 (the 80th
                             percentile on pre-cutoff data)
A missing m5 txn count, a zero h1 volume or unknown liquidity leave (b), (c)
or (g) unevaluated, exactly as in the research; unknown liquidity is already
a fail-closed structural rug input.

The history lives in memory only. The research windows read a continuous
scan log, so after a restart the 15-min high and the 60-min paid-profile
lookback would otherwise see only the samples since the restart; each window
therefore has its own coverage requirement above (fail closed), and an engine
or the Lab waits 15 min after a restart before any entry, and 60 min before
entering a >= 100 bps pool that it has not seen with a paid profile.

``log_only`` books (a pre-registered surge or dip hypothesis arm) receive the
same flags with ``vetoed`` False, so the hypothesis is measured, not filtered.
"""
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
import math
import threading

import paper_market_feasibility as feasibility

VERSION = 'HEAT_VETO_STACK_V1'
HISTORY_VERSION = 'PAIR_HISTORY_V1'
REASONS = ('heat_history_warming', 'heat_input_unknown', 'heat_return_5m_surge', 'heat_buy_share_5m',
           'heat_volume_acceleration', 'heat_extended_move', 'heat_paid_profile_high_fee',
           'heat_crash_in_progress', 'heat_turnover_5m')
PAID_PROFILE_SOURCE = 'latest'
# Reserved ids of the pre-registered surge (LAB_A) and dip (LAB_B) hypothesis
# arms of the research. They are not registered Lab books yet; a book listed
# here records the heat flags without being blocked. Every registered book,
# the engine accounts and the training probe enforce. The tape scheduler's
# seats (shared by every ledger) record the flags log-only; see
# tape_pool_scheduler.TapePoolScheduler.defensive_entry_decision.
LOG_ONLY_BOOK_IDS = frozenset({'LAB_A_SURGE_EST_GUARD', 'LAB_B_DIP_MKTDIP_GUARD'})


@dataclass(frozen=True)
class HeatParameters:
    """Frozen thresholds; no environment variable changes them."""
    min_history_seconds: int = 300
    return_window_seconds: int = 300
    return_5m_surge_pct: float = 3.0
    buy_share_5m: float = 0.70
    volume_acceleration: float = 1.3
    change_6h_pct: float = 200.0
    change_24h_pct: float = 150.0
    paid_profile_min_fee_bps: float = 100.0
    paid_profile_lookback_seconds: int = 3600
    crash_window_seconds: int = 900
    crash_fraction_of_high: float = 0.75
    crash_return_5m_pct: float = -20.0
    turnover_5m: float = 0.095


PARAMS = HeatParameters()


@dataclass(frozen=True)
class HistoryParameters:
    """Bounds of the rolling per-pair history (memory is O(max_pairs x max_samples))."""
    retention_ms: int = 3_660_000      # >= 60 min (paid-profile lookback) plus a minute
    max_gap_ms: int = 120_000          # a longer absence restarts the contiguous segment
    heartbeat_ms: int = 30_000         # an unchanged price is still stored this often
    max_samples_per_pair: int = 720
    max_pairs: int = 2048
    # Feed gaps kept per pair; each is > max_gap_ms long, so 64 cover the retention.
    max_gaps_per_pair: int = 64


HISTORY_PARAMS = HistoryParameters()


def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _identity(coin):
    mint, pair = coin.get('address'), coin.get('pairAddress')
    if not isinstance(mint, str) or not isinstance(pair, str) or not mint.strip() or not pair.strip():
        return None
    return mint.strip(), pair.strip()


def paid_profile(coin) -> bool:
    sources = coin.get('sources')
    return isinstance(sources, (list, tuple, set)) and PAID_PROFILE_SOURCE in sources


class PairHistory:
    """Rolling per-pair samples of (t, priceUsd, paid-profile flag), bounded in memory.

    ``observe(feed, now)`` is fed every scan. A sample is stored when the price
    or the paid flag changes, on a heartbeat, and at the start of a contiguous
    segment; an observation older than the pair's last one is ignored, and an
    absence longer than ``max_gap_ms`` restarts the segment (and the warm-up).
    Each such absence is kept as a (last seen before, first seen after) gap so
    the 15-minute crash window can span gaps like the research window does.
    The observation time is the coin's ``updatedAt`` when it is a valid past
    stamp, else ``now``. Thread-safe.

    Coverage: each pair keeps ``first_seen`` (its first observation in this
    instance; a pruned or evicted pair starts again), and the instance keeps
    ``observing_since`` (its first observation of any pair). Nothing is
    persisted, so both restart with the process.
    """

    def __init__(self, params: HistoryParameters = HISTORY_PARAMS):
        self.params = params
        self._pairs = OrderedDict()
        self._lock = threading.Lock()
        self._observing_since = None

    @property
    def observing_since(self):
        """First observation stamp (ms) of this instance, or None before any."""
        with self._lock:
            return self._observing_since

    def _observed_at(self, coin, now):
        stamp = _finite(coin.get('updatedAt'))
        current = _finite(now)
        if stamp is not None and stamp > 0 and (current is None or stamp <= current + 5_000):
            return stamp
        return current

    def observe_coin(self, coin, now) -> bool:
        if not isinstance(coin, dict):
            return False
        key = _identity(coin)
        price = _finite(coin.get('priceUsd'))
        stamp = self._observed_at(coin, now)
        if key is None or price is None or price <= 0 or stamp is None:
            return False
        # Coverage is counted on this process's clock: an old updatedAt never
        # claims a window the process did not observe.
        current = _finite(now)
        seen_at = stamp if current is None else max(stamp, current)
        paid = paid_profile(coin)
        params = self.params
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                record = {'samples': deque(maxlen=params.max_samples_per_pair), 'since': stamp,
                          'first_seen': seen_at, 'last_seen': None, 'last_paid_at': None,
                          'gaps': deque(maxlen=params.max_gaps_per_pair)}
                self._pairs[key] = record
            elif record['last_seen'] is not None and stamp <= record['last_seen']:
                return False
            if self._observing_since is None:
                self._observing_since = seen_at
            self._pairs.move_to_end(key)
            samples = record['samples']
            restart = record['last_seen'] is None or stamp - record['last_seen'] > params.max_gap_ms
            if restart:
                if record['last_seen'] is not None:
                    record['gaps'].append((record['last_seen'], stamp))
                record['since'] = stamp
            last = samples[-1] if samples else None
            if (restart or last is None or last[1] != price or last[2] != paid
                    or stamp - last[0] >= params.heartbeat_ms):
                samples.append((stamp, price, paid))
            record['last_seen'] = stamp
            if paid:
                record['last_paid_at'] = stamp
            horizon = stamp - params.retention_ms
            while samples and samples[0][0] < horizon:
                samples.popleft()
            gaps = record['gaps']
            while gaps and gaps[0][1] < horizon:
                gaps.popleft()
            while len(self._pairs) > params.max_pairs:
                self._pairs.popitem(last=False)
            return True

    def observe(self, feed, now) -> int:
        stored = sum(int(self.observe_coin(coin, now)) for coin in feed or ())
        self.prune(now)
        return stored

    def prune(self, now) -> int:
        current = _finite(now)
        if current is None:
            return 0
        horizon = current - self.params.retention_ms
        with self._lock:
            stale = [key for key, record in self._pairs.items()
                     if record['last_seen'] is None or record['last_seen'] < horizon]
            for key in stale:
                del self._pairs[key]
            return len(stale)

    def view(self, coin):
        """A copy of one pair's record (samples, segment start, first/last seen, last paid, gaps), or None."""
        key = _identity(coin) if isinstance(coin, dict) else None
        if key is None:
            return None
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                return None
            return {'samples': list(record['samples']), 'since': record['since'],
                    'first_seen': record.get('first_seen', record['since']),
                    'last_seen': record['last_seen'], 'last_paid_at': record['last_paid_at'],
                    'gaps': list(record.get('gaps') or ())}

    def __len__(self):
        with self._lock:
            return len(self._pairs)

    def status(self) -> dict:
        with self._lock:
            return {'version': HISTORY_VERSION, 'pairs': len(self._pairs),
                    'samples': sum(len(record['samples']) for record in self._pairs.values()),
                    'observing_since': self._observing_since, 'persistent': False,
                    **asdict(self.params)}


def _price_at_or_before(samples, since, target):
    """Price in effect at ``target`` inside the contiguous segment (None if before it)."""
    found = None
    for stamp, price, _paid in samples:
        if stamp < since:
            continue
        if stamp > target:
            break
        found = price
    return found


def window_prices(record, now, seconds):
    """Prices observed over (now - seconds, now], across feed gaps (the research P.window).

    Every retained sample inside the window counts, whatever its segment. The
    history stores a sample only on a change, a heartbeat or a segment start,
    so the price in effect at the window start is the last sample at or before
    it; it counts only when that sample's contiguous segment was still observed
    after the window start (a pair absent then contributed no observation).
    """
    current = _finite(now)
    if record is None or current is None:
        return []
    start = current - seconds * 1000
    samples = record.get('samples') or ()
    window = [price for stamp, price, _paid in samples if stamp > start]
    opening = None
    for stamp, price, _paid in samples:
        if stamp > start:
            break
        opening = (stamp, price)
    if opening is not None:
        # The opening sample's segment ends at the first gap after it (or is the current one).
        segment_end = record.get('last_seen')
        for gap_start, _gap_end in record.get('gaps') or ():
            if gap_start >= opening[0]:
                segment_end = gap_start
                break
        if segment_end is not None and segment_end > start:
            window.append(opening[1])
    return window


def evaluate(coin, now, history, *, log_only=False, params: HeatParameters = PARAMS) -> dict:
    """Heat veto of one candidate at ``now`` (ms). Returns {'version','vetoed','reasons','metrics','log_only'}."""
    coin = coin if isinstance(coin, dict) else {}
    current = _finite(now)
    price = _finite(coin.get('priceUsd'))
    record = history.view(coin) if history is not None else None
    fired = set()
    # Windows this history cannot cover yet; any one makes the result 'heat_history_warming'.
    warming = []
    metrics = {'history_span_s': None, 'return_5m_pct': None, 'buy_share_5m': None,
               'volume_acceleration': None, 'change_6h_pct': None, 'change_24h_pct': None,
               'fee_tier_bps': None, 'paid_profile_last_60m': False, 'fraction_of_15m_high': None,
               'turnover_5m': None, 'pair_coverage_s': None, 'process_coverage_s': None,
               'warming_windows': warming}
    samples, since, span_ms = [], None, None
    max_gap_ms = getattr(getattr(history, 'params', None), 'max_gap_ms', HISTORY_PARAMS.max_gap_ms)
    if record is not None and current is not None:
        samples, since = record['samples'], record['since']
        # A pair not re-observed within the gap limit has no current history.
        fresh = (record['last_seen'] is not None
                 and current - record['last_seen'] <= max_gap_ms)
        if fresh and since is not None:
            span_ms = max(0.0, current - since)
            # Rounded for publication only; every comparison uses span_ms.
            metrics['history_span_s'] = round(span_ms / 1000, 1)
        first_seen = _finite(record.get('first_seen'))
        if first_seen is not None:
            metrics['pair_coverage_s'] = round(max(0.0, current - first_seen) / 1000, 1)
    observing_since = _finite(getattr(history, 'observing_since', None)) if history is not None else None
    process_coverage_ms = (None if observing_since is None or current is None
                           else max(0.0, current - observing_since))
    if process_coverage_ms is not None:
        metrics['process_coverage_s'] = round(process_coverage_ms / 1000, 1)
    if price is None or price <= 0:
        fired.add('heat_input_unknown')
        price = None
    # (a) on the contiguous segment: >= 300 s of it, and a price at or before now - 300 s.
    segment_ready = span_ms is not None and span_ms >= params.min_history_seconds * 1000
    reference = None
    if price is not None and segment_ready:
        reference = _price_at_or_before(samples, since, current - params.return_window_seconds * 1000)
    if reference is not None and reference > 0:
        metrics['return_5m_pct'] = round((price / reference - 1) * 100, 4)
    elif not segment_ready or price is not None:
        # Warm-up, or (fail closed) no usable reference price inside the segment.
        warming.append('return_5m')
    ret5 = metrics['return_5m_pct']
    if ret5 is not None and ret5 >= params.return_5m_surge_pct:
        fired.add('heat_return_5m_surge')
    # A malformed txns or txns.m5 (not a dict) leaves (b) unevaluated, never raises.
    m5_txns = coin['txns'].get('m5') if isinstance(coin.get('txns'), dict) else None
    tx = m5_txns if isinstance(m5_txns, dict) else {}
    buys, sells = _finite(tx.get('buys')), _finite(tx.get('sells'))
    if buys is not None and sells is not None and buys >= 0 and sells >= 0 and buys + sells >= 1:
        metrics['buy_share_5m'] = round(buys / (buys + sells), 4)
        if metrics['buy_share_5m'] >= params.buy_share_5m:
            fired.add('heat_buy_share_5m')
    volume = coin.get('volume') if isinstance(coin.get('volume'), dict) else {}
    v5, v1h = _finite(volume.get('m5')), _finite(volume.get('h1'))
    if v5 is not None and v1h is not None and v5 >= 0 and v1h > 0:
        metrics['volume_acceleration'] = round(v5 / (v1h / 12), 4)
        if metrics['volume_acceleration'] >= params.volume_acceleration:
            fired.add('heat_volume_acceleration')
    changes = coin.get('priceChange') if isinstance(coin.get('priceChange'), dict) else {}
    pc6h, pc24 = _finite(changes.get('h6')), _finite(changes.get('h24'))
    metrics['change_6h_pct'], metrics['change_24h_pct'] = pc6h, pc24
    if (pc6h is not None and pc6h >= params.change_6h_pct) or (pc24 is not None and pc24 >= params.change_24h_pct):
        fired.add('heat_extended_move')
    fee = feasibility.pumpswap_fee_bps(coin)
    metrics['fee_tier_bps'] = fee
    lookback = params.paid_profile_lookback_seconds * 1000
    last_paid = record['last_paid_at'] if record is not None else None
    # The paid flag survives segment restarts; a stamp a few seconds ahead of
    # ``now`` (observation clock skew) still counts as within the lookback.
    paid = paid_profile(coin) or (last_paid is not None and current is not None
                                  and current - last_paid <= lookback)
    metrics['paid_profile_last_60m'] = bool(paid)
    if fee >= params.paid_profile_min_fee_bps:
        if paid:
            fired.add('heat_paid_profile_high_fee')
        elif process_coverage_ms is None or process_coverage_ms < lookback:
            # (e) needs 60 min of this process's observations: a pair seen with
            # 'latest' before a restart is not known to be unpaid.
            warming.append('paid_profile_60m')
    if price is not None:
        # The 15-minute high spans feed gaps (every retained sample in the
        # window, as the research P.window('price', 900)); the contiguous
        # segment only gates the warm-up and the 5-minute return.
        window = [p for p in window_prices(record, current, params.crash_window_seconds) if p > 0]
        high = max(window + [price])
        metrics['fraction_of_15m_high'] = round(price / high, 6)
        if price <= params.crash_fraction_of_high * high or (
                ret5 is not None and ret5 <= params.crash_return_5m_pct):
            fired.add('heat_crash_in_progress')
        else:
            first_seen = _finite(record.get('first_seen')) if record is not None else None
            if first_seen is None or current is None or current - first_seen < params.crash_window_seconds * 1000:
                # (f) needs the pair observed here for the whole 15-min window.
                warming.append('crash_15m')
    liquidity = _finite(coin.get('liquidityUsd'))
    if v5 is not None and v5 >= 0 and liquidity is not None and liquidity > 0:
        metrics['turnover_5m'] = round(v5 / liquidity, 6)
        if metrics['turnover_5m'] >= params.turnover_5m:
            fired.add('heat_turnover_5m')
    if warming:
        fired.add('heat_history_warming')
    reasons = [reason for reason in REASONS if reason in fired]
    return {'version': VERSION, 'vetoed': bool(reasons) and not log_only, 'reasons': reasons,
            'metrics': metrics, 'log_only': bool(log_only)}


def config(params: HeatParameters = PARAMS, history: HistoryParameters = HISTORY_PARAMS) -> dict:
    """Published definition; part of every effective config hash that applies it."""
    return {'version': VERSION, 'history_version': HISTORY_VERSION, 'reasons_in_order': list(REASONS),
            'parameters': asdict(params), 'history': asdict(history),
            'paid_profile_source': f"'{PAID_PROFILE_SOURCE}' (DexScreener token-profiles/latest)",
            'fee_tier_basis': 'paper_market_feasibility.pumpswap_fee_bps',
            'log_only_book_ids': sorted(LOG_ONLY_BOOK_IDS),
            'crash_high_window': 'every retained sample in the last 15 min, across feed gaps',
            'window_coverage': {
                'return_5m': ('>= 300 s of contiguous history (unrounded) and a price at or before '
                              'now - 300 s inside it; otherwise heat_history_warming'),
                'crash_15m': ('the pair first observed by this process >= 900 s ago, unless (f) already '
                              'fires on the shorter window; otherwise heat_history_warming'),
                'paid_profile_60m': ('at a fee tier >= 100 bps and no known paid profile: this process '
                                     'observing the feed for >= 3600 s; otherwise heat_history_warming'),
                'clock': 'coverage counts on the process clock (now), never on an older updatedAt',
                'persistence': 'none: a restart starts every window again'},
            'malformed_inputs': 'leave the affected rule unevaluated; never raise',
            'warming_is_a_veto': True, 'exits_changed': False,
            'is_entry_authorization': False, 'profitability_proven': False}
