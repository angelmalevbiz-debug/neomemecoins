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
  heat_history_warming       the pair has < 300 s of contiguous history here
                             (conservative: a 5-min return cannot be measured)
  heat_input_unknown         the current price is missing or not positive
  heat_return_5m_surge       (a) 5-min return >= +3% vs the price 300 s earlier
  heat_buy_share_5m          (b) txns.m5 buys / (buys + sells) >= 0.70
  heat_volume_acceleration   (c) volume.m5 / (volume.h1 / 12) >= 1.3
  heat_extended_move         (d) priceChange.h6 >= +200 or priceChange.h24 >= +150
  heat_paid_profile_high_fee (e) fee tier >= 100 bps and the pair carried the
                             paid-profile 'latest' source in the last 60 min
  heat_crash_in_progress     (f) price <= 75% of its 15-min high, or 5-min
                             return <= -20%
  heat_turnover_5m           (g) volume.m5 / liquidityUsd >= 0.095 (the 80th
                             percentile on pre-cutoff data)
A missing m5 txn count, a zero h1 volume or unknown liquidity leave (b), (c)
or (g) unevaluated, exactly as in the research; unknown liquidity is already
a fail-closed structural rug input.

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
# the engine accounts, the training probe and the tape scheduler enforce.
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
    The observation time is the coin's ``updatedAt`` when it is a valid past
    stamp, else ``now``. Thread-safe.
    """

    def __init__(self, params: HistoryParameters = HISTORY_PARAMS):
        self.params = params
        self._pairs = OrderedDict()
        self._lock = threading.Lock()

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
        paid = paid_profile(coin)
        params = self.params
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                record = {'samples': deque(maxlen=params.max_samples_per_pair), 'since': stamp,
                          'last_seen': None, 'last_paid_at': None}
                self._pairs[key] = record
            elif record['last_seen'] is not None and stamp <= record['last_seen']:
                return False
            self._pairs.move_to_end(key)
            samples = record['samples']
            restart = record['last_seen'] is None or stamp - record['last_seen'] > params.max_gap_ms
            if restart:
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
        """A copy of one pair's record (samples, segment start, last seen, last paid), or None."""
        key = _identity(coin) if isinstance(coin, dict) else None
        if key is None:
            return None
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                return None
            return {'samples': list(record['samples']), 'since': record['since'],
                    'last_seen': record['last_seen'], 'last_paid_at': record['last_paid_at']}

    def __len__(self):
        with self._lock:
            return len(self._pairs)

    def status(self) -> dict:
        with self._lock:
            return {'version': HISTORY_VERSION, 'pairs': len(self._pairs),
                    'samples': sum(len(record['samples']) for record in self._pairs.values()),
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


def evaluate(coin, now, history, *, log_only=False, params: HeatParameters = PARAMS) -> dict:
    """Heat veto of one candidate at ``now`` (ms). Returns {'version','vetoed','reasons','metrics','log_only'}."""
    coin = coin if isinstance(coin, dict) else {}
    current = _finite(now)
    price = _finite(coin.get('priceUsd'))
    record = history.view(coin) if history is not None else None
    reasons = []
    metrics = {'history_span_s': None, 'return_5m_pct': None, 'buy_share_5m': None,
               'volume_acceleration': None, 'change_6h_pct': None, 'change_24h_pct': None,
               'fee_tier_bps': None, 'paid_profile_last_60m': False, 'fraction_of_15m_high': None,
               'turnover_5m': None}
    samples, since = [], None
    max_gap_ms = getattr(getattr(history, 'params', None), 'max_gap_ms', HISTORY_PARAMS.max_gap_ms)
    if record is not None and current is not None:
        samples, since = record['samples'], record['since']
        # A pair not re-observed within the gap limit has no current history.
        fresh = (record['last_seen'] is not None
                 and current - record['last_seen'] <= max_gap_ms)
        if fresh and since is not None:
            metrics['history_span_s'] = round(max(0.0, current - since) / 1000, 1)
    span = metrics['history_span_s']
    if span is None or span < params.min_history_seconds:
        reasons.append('heat_history_warming')
    if price is None or price <= 0:
        reasons.append('heat_input_unknown')
        price = None
    reference = None
    if price is not None and span is not None and span >= params.min_history_seconds:
        reference = _price_at_or_before(samples, since, current - params.return_window_seconds * 1000)
    if reference is not None and reference > 0:
        metrics['return_5m_pct'] = round((price / reference - 1) * 100, 4)
    ret5 = metrics['return_5m_pct']
    if ret5 is not None and ret5 >= params.return_5m_surge_pct:
        reasons.append('heat_return_5m_surge')
    tx = ((coin.get('txns') or {}).get('m5') or {}) if isinstance(coin.get('txns'), dict) else {}
    buys, sells = _finite(tx.get('buys')), _finite(tx.get('sells'))
    if buys is not None and sells is not None and buys >= 0 and sells >= 0 and buys + sells >= 1:
        metrics['buy_share_5m'] = round(buys / (buys + sells), 4)
        if metrics['buy_share_5m'] >= params.buy_share_5m:
            reasons.append('heat_buy_share_5m')
    volume = coin.get('volume') if isinstance(coin.get('volume'), dict) else {}
    v5, v1h = _finite(volume.get('m5')), _finite(volume.get('h1'))
    if v5 is not None and v1h is not None and v5 >= 0 and v1h > 0:
        metrics['volume_acceleration'] = round(v5 / (v1h / 12), 4)
        if metrics['volume_acceleration'] >= params.volume_acceleration:
            reasons.append('heat_volume_acceleration')
    changes = coin.get('priceChange') if isinstance(coin.get('priceChange'), dict) else {}
    pc6h, pc24 = _finite(changes.get('h6')), _finite(changes.get('h24'))
    metrics['change_6h_pct'], metrics['change_24h_pct'] = pc6h, pc24
    if (pc6h is not None and pc6h >= params.change_6h_pct) or (pc24 is not None and pc24 >= params.change_24h_pct):
        reasons.append('heat_extended_move')
    fee = feasibility.pumpswap_fee_bps(coin)
    metrics['fee_tier_bps'] = fee
    lookback = params.paid_profile_lookback_seconds * 1000
    last_paid = record['last_paid_at'] if record is not None else None
    # The paid flag survives segment restarts; a stamp a few seconds ahead of
    # ``now`` (observation clock skew) still counts as within the lookback.
    paid = paid_profile(coin) or (last_paid is not None and current is not None
                                  and current - last_paid <= lookback)
    metrics['paid_profile_last_60m'] = bool(paid)
    if paid and fee >= params.paid_profile_min_fee_bps:
        reasons.append('heat_paid_profile_high_fee')
    if price is not None:
        # The 15-minute high is read only from current (fresh) contiguous history.
        current_history = span is not None and since is not None and current is not None
        window = [p for stamp, p, _paid in samples
                  if current_history and stamp >= since
                  and stamp > current - params.crash_window_seconds * 1000]
        opening = (_price_at_or_before(samples, since, current - params.crash_window_seconds * 1000)
                   if current_history else None)
        high = max(window + ([opening] if opening else []) + [price])
        metrics['fraction_of_15m_high'] = round(price / high, 6)
        if price <= params.crash_fraction_of_high * high or (
                ret5 is not None and ret5 <= params.crash_return_5m_pct):
            reasons.append('heat_crash_in_progress')
    liquidity = _finite(coin.get('liquidityUsd'))
    if v5 is not None and v5 >= 0 and liquidity is not None and liquidity > 0:
        metrics['turnover_5m'] = round(v5 / liquidity, 6)
        if metrics['turnover_5m'] >= params.turnover_5m:
            reasons.append('heat_turnover_5m')
    return {'version': VERSION, 'vetoed': bool(reasons) and not log_only, 'reasons': reasons,
            'metrics': metrics, 'log_only': bool(log_only)}


def config(params: HeatParameters = PARAMS, history: HistoryParameters = HISTORY_PARAMS) -> dict:
    """Published definition; part of every effective config hash that applies it."""
    return {'version': VERSION, 'history_version': HISTORY_VERSION, 'reasons_in_order': list(REASONS),
            'parameters': asdict(params), 'history': asdict(history),
            'paid_profile_source': f"'{PAID_PROFILE_SOURCE}' (DexScreener token-profiles/latest)",
            'fee_tier_basis': 'paper_market_feasibility.pumpswap_fee_bps',
            'log_only_book_ids': sorted(LOG_ONLY_BOOK_IDS),
            'warming_is_a_veto': True, 'exits_changed': False,
            'is_entry_authorization': False, 'profitability_proven': False}
