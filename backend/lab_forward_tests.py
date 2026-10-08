"""LAB_FORWARD_TESTS_V1: PAPER Strategy Lab forward tests of two pre-registered research hypotheses.

PAPER only. Nothing here builds, signs or sends a transaction, and nothing here
is a profit claim. Four isolated $500 TEST books forward-test the hypotheses
frozen in research/edge_study_2026_10_08/synthesis/strategies.py
(pre-registration sha256 786e01fd..., synthesis/prereg.sha256) next to their
random controls, exactly as specified in synthesis_specs.txt
(LAB_A_SURGE_EST_GUARD, LAB_B_DIP_MKTDIP_GUARD, PROMOTION GATE):

- LAB_A_SURGE_EST_GUARD: a fresh DexScreener buy-count surge (txns.m5.buys
  >= 30, txns.h1.buys > 0, m5 buys >= 3 x the 1-hour pace, and the same test
  false at the pair's previous observation) in PumpSwap/SOL pools with a fee
  tier <= 95 bps and liquidity >= $50k. Exits -5% / +10% net, 60 min, 300 s
  pool cooldown, $200.
- RND_LAB_A: random entries in the same universe (deterministic hashed coin,
  p = 0.0005 per observation, salt 'synA'), same exits.
- LAB_B_DIP_MKTDIP_GUARD: a coin dip (price <= 90% of 15 min ago, liquidity
  >= 85% of 15 min ago, priceChange.h24 > -50%) bought only while the market
  regime med15 < -0.2% (median 15-min change of PumpSwap/SOL pools with a
  print in the last 3 min and liquidity >= $20k, reference at most 10 min
  stale, at least 8 pools), in PumpSwap/SOL pools with liquidity >= $50k.
  Exits -15% / +20% net, 60 min, 300 s pool cooldown, $200.
- RND_LAB_B: random entries in the LAB_B universe without the dip or regime
  filter (p = 0.0007, salt 'synB'), same exits.

Research evidence (calibrated net50, 22.8 h scan log): LAB_A -3.21%/trade on
holdout (n=32, 16 pairs) against -6.14% for its random control; LAB_B -1.27%
(n=12, 6 pairs) against -4.48%. Both are hypotheses with a negative absolute
result; the books measure whether the relative edge survives forward.

Shared definitions are reused, never restated: STRUCTURAL_RUG_GUARD_V1 (the
universes' rug screen, applied by DEFENSIVE_ENTRY_LAYER_V1 in strategy_lab
before every entry; it subsumes the research's interim screen and its
age >= 60 min), POOL_LOSS_MEMORY_V1 (each book's own history) and the
defensive layer's PairHistory (prices, contiguous segments and feed gaps).
HEAT_VETO_STACK_V1 runs log-only for these four books: its flags are recorded
on each position and close, never applied (the books test surge and dip
hypotheses the veto would remove).

This module adds only what PairHistory does not carry: the LAB_A surge state
of each pair's last two distinct observations and per-pair liquidity samples
(LAB_B's own 15-minute reference and the regime's $20k activity floor).

Costs: the Lab's shared spot model (strategy_lab entry/exit_execution) plus
CALIB_V1 extra cost per leg (research calib/calibration.py, basis 'engine',
conservative): fee tier <= 50 bps +22 bps, 55-125 bps +10 bps, +25 bps when
liquidity < $50k or the trade is > 0.3% of liquidity, plus the engine's fixed
rent and network costs (+5.5 bps per leg at $200). Booked P&L includes it.
Exit triggers use the uncalibrated model net, as the research's exit
decisions did. Each close also records net50 = booked net minus 50 bps per
leg, minus 200 bps more on stop exits and 100 bps on trailing exits
(research calibrate_trade form).

Kill rule (LAB_FORWARD_KILL_RULE_V1), per book: after >= 50 closes of the
book's frozen config, retire it (new entries only; balance, history and open
exits are never touched) when mean net50 < 0 and the upper bound of the
pair-bootstrap 95% CI of mean net50 $/trade (2,000 resamples, fixed seed;
per-trade normal approximation under 3 pairs) is < 0. Promotion is never
automatic; the promotion gate is computed for the owner's review only.
"""
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import random
import threading

import heat_veto
import lab_activity as activity
import paper_market_feasibility as feasibility
import pool_loss_memory
import structural_rug_guard

VERSION = 'LAB_FORWARD_TESTS_V1'
ENTRY_POLICY_VERSION = VERSION
KILL_RULE_VERSION = 'LAB_FORWARD_KILL_RULE_V1'
PROMOTION_GATE_VERSION = 'LAB_FORWARD_PROMOTION_GATE_V1'
MEMORY_VERSION = 'LAB_FORWARD_FEED_MEMORY_V1'
REGIME_VERSION = 'LAB_B_MARKET_REGIME_MED15_V1'
CALIB_VERSION = 'CALIB_V1_2026-10-08'
NET50_VERSION = 'NET50_CALIB_V1'
# strategy_lab.EXECUTION_MODEL_VERSION (restated here to avoid a circular import; a test pins equality).
EXECUTION_MODEL = 'DEX_SPOT_MODELED_COSTS_V3_VERIFIED_SOL_DENOMINATION'
RESEARCH_SOURCE = 'research/edge_study_2026_10_08/synthesis/strategies.py'
RESEARCH_PREREG_SHA256 = '786e01fd1a07ab421d78ffe88a6067cf92ccabcd7719ab908e444db18b630189'
SOL_QUOTE_MINT = feasibility.SOL_QUOTE_MINT

LAB_A_ID = 'LAB_A_SURGE_EST_GUARD'
RND_A_ID = 'RND_LAB_A'
LAB_B_ID = 'LAB_B_DIP_MKTDIP_GUARD'
RND_B_ID = 'RND_LAB_B'
BOOK_IDS = (LAB_A_ID, RND_A_ID, LAB_B_ID, RND_B_ID)
HYPOTHESIS_IDS = (LAB_A_ID, LAB_B_ID)
CONTROL_OF = {LAB_A_ID: RND_A_ID, LAB_B_ID: RND_B_ID}
HYPOTHESIS_OF = {RND_A_ID: LAB_A_ID, RND_B_ID: LAB_B_ID}
BOOK_NAMES = {
    LAB_A_ID: 'Lab A: Surge Established (forward test)',
    RND_A_ID: 'Lab A: Random Control',
    LAB_B_ID: 'Lab B: Dip in Market Dip (forward test)',
    RND_B_ID: 'Lab B: Random Control',
}
# HEAT_VETO_STACK_V1 records its flags for these books without applying them:
# heat_veto.LOG_ONLY_BOOK_IDS reserves the two hypothesis arms, and their random
# controls must face the same (unfiltered) universe.
HEAT_LOG_ONLY_BOOK_IDS = frozenset(BOOK_IDS)
HEAT_MODE = 'log_only'
START_BALANCE_USD = 500.0
NOTIONAL_USD = 200.0
PORTFOLIO_GROUP = 'TEST'
AUTOMATIC_PROMOTION = False


# ------------------------------------------------------------------ frozen parameters

@dataclass(frozen=True)
class UniverseParameters:
    """Physical universe of one book; STRUCTURAL_RUG_GUARD_V1 is applied by the defensive layer."""
    dex_id: str = 'pumpswap'
    quote_token_address: str = SOL_QUOTE_MINT
    min_liquidity_usd: float = 50_000.0
    # None: no fee-tier limit (LAB_B). Fee tier as paper_market_feasibility.pumpswap_fee_bps.
    max_fee_tier_bps: float | None = None


@dataclass(frozen=True)
class SurgeParameters:
    """LAB_A: txns.m5.buys >= 30, txns.h1.buys > 0, m5 >= 3 x h1 / 12, false at the previous observation."""
    min_buys_5m: float = 30.0
    min_buys_1h_exclusive: float = 0.0
    pace_multiple: float = 3.0
    hourly_pace_divisor: float = 12.0
    fresh_crossing: bool = True


@dataclass(frozen=True)
class DipParameters:
    """LAB_B coin dip at the latest observation >= 15 min earlier (research P.ago, no staleness limit)."""
    lookback_seconds: int = 900
    max_return_pct: float = -10.0          # price / price_15m - 1 <= -10%
    min_liquidity_ratio: float = 0.85      # liquidity / liquidity_15m >= 0.85
    min_change_24h_pct_exclusive: float = -50.0
    max_regime_med15_pct_exclusive: float = -0.2


@dataclass(frozen=True)
class RegimeParameters:
    """LAB_B market regime med15 on a whole-minute grid (research f9_regime construction)."""
    grid_ms: int = 60_000
    active_within_ms: int = 180_000
    min_liquidity_usd: float = 20_000.0
    lookback_ms: int = 900_000
    max_reference_staleness_ms: int = 600_000
    min_pools: int = 8
    dex_id: str = 'pumpswap'
    quote_token_address: str = SOL_QUOTE_MINT


@dataclass(frozen=True)
class RandomParameters:
    """Deterministic hashed coin: blake2b-64('salt|pair|int(t_ms)') / 2**64 < probability, per observation."""
    probability: float
    salt: str
    hash: str = 'blake2b(digest_size=8) of "salt|pairAddress|int(observation_ms)", big-endian / 2**64'


@dataclass(frozen=True)
class ExitParameters:
    """Net exits; triggers on the uncalibrated Lab model net (research exits used the model)."""
    label: str
    stop_loss_net_pct: float
    take_profit_net_pct: float
    max_hold_minutes: float
    pool_cooldown_seconds: int = 300
    trigger_basis: str = 'LAB_SPOT_MODEL_NET_UNCALIBRATED'

    @property
    def stop_reason(self) -> str:
        return f'STOP_LOSS_{_whole(self.stop_loss_net_pct)}_NET'

    @property
    def take_profit_reason(self) -> str:
        return f'TAKE_PROFIT_{_whole(self.take_profit_net_pct)}_NET'

    @property
    def max_hold_reason(self) -> str:
        return f'ABSOLUTE_MAX_HOLD_{_whole(self.max_hold_minutes)}'


@dataclass(frozen=True)
class CalibParameters:
    """CALIB_V1 extra cost per leg (research calib/calibration.py, basis 'engine', conservative=True)."""
    version: str = CALIB_VERSION
    fee_le_50_bps: float = 22.0
    fee_55_95_central_bps: float = -5.0
    fee_100_125_central_bps: float = -55.0
    floor_bps: float = 10.0
    unmeasured_margin_bps: float = 25.0
    measured_min_liquidity_usd: float = 50_000.0
    measured_max_size_pct_of_liquidity: float = 0.30
    engine_rent_usd_per_trade: float = 0.185
    engine_network_usd_per_leg: float = 0.03
    model_network_usd_per_leg: float = 0.012


@dataclass(frozen=True)
class Net50Parameters:
    stress_bps_per_leg: float = 50.0
    stop_exit_extra_bps: float = 200.0
    trailing_exit_extra_bps: float = 100.0
    form: str = 'net50 = (1 + booked_pct) x (1 - 50 bps) x (1 - (50 bps + reason extra)) - 1 (research calibrate_trade)'


@dataclass(frozen=True)
class KillRuleParameters:
    min_closes: int = 50
    max_mean_net50_usd_exclusive: float = 0.0
    max_ci95_upper_usd_exclusive: float = 0.0
    ci_method: str = 'pair_bootstrap'
    bootstrap_resamples: int = 2000
    bootstrap_seed: int = 20261008
    fallback_under_pairs: int = 3
    fallback_method: str = 'normal_per_trade'
    action: str = 'retire_new_entries_only'


@dataclass(frozen=True)
class GateParameters:
    """PROMOTION GATE of synthesis_specs.txt; evaluated for owner review, never applied automatically."""
    min_closes: int = 150
    min_pairs: int = 25
    min_days: float = 3.0
    utc_hours_required: int = 24
    max_top_pair_share: float = 0.20
    max_best_day_share: float = 0.50
    max_vanished_or_unpriced_share: float = 0.10


@dataclass(frozen=True)
class MemoryParameters:
    retention_ms: int = heat_veto.HISTORY_PARAMS.retention_ms
    max_pairs: int = heat_veto.HISTORY_PARAMS.max_pairs
    max_liquidity_samples_per_pair: int = 720
    regime_cache_minutes: int = 30


def _whole(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


UNIVERSE_A = UniverseParameters(max_fee_tier_bps=95.0)
UNIVERSE_B = UniverseParameters()
SURGE = SurgeParameters()
DIP = DipParameters()
REGIME = RegimeParameters()
RANDOM_A = RandomParameters(probability=0.0005, salt='synA')
RANDOM_B = RandomParameters(probability=0.0007, salt='synB')
EXITS_A = ExitParameters('LAB_A_5_10_60', 5.0, 10.0, 60.0)
EXITS_B = ExitParameters('LAB_B_15_20_60', 15.0, 20.0, 60.0)
CALIB = CalibParameters()
NET50 = Net50Parameters()
KILL_RULE = KillRuleParameters()
GATE = GateParameters()
MEMORY_PARAMS = MemoryParameters()

UNIVERSES = {LAB_A_ID: UNIVERSE_A, RND_A_ID: UNIVERSE_A, LAB_B_ID: UNIVERSE_B, RND_B_ID: UNIVERSE_B}
EXITS = {LAB_A_ID: EXITS_A, RND_A_ID: EXITS_A, LAB_B_ID: EXITS_B, RND_B_ID: EXITS_B}
RANDOM = {RND_A_ID: RANDOM_A, RND_B_ID: RANDOM_B}
SIGNAL_KIND = {LAB_A_ID: 'fresh_buy_surge', RND_A_ID: 'random', LAB_B_ID: 'dip_in_market_dip', RND_B_ID: 'random'}
RESEARCH_ENTRIES = {LAB_A_ID: 'CONFIGS LAB_A_SURGE_EST_GUARD', RND_A_ID: 'BASELINES RND_LAB_A',
                    LAB_B_ID: 'CONFIGS LAB_B_DIP_MKTDIP_GUARD', RND_B_ID: 'BASELINES RND_LAB_B'}

UNIVERSE_REASONS = ('lab_dex_not_pumpswap', 'lab_quote_not_sol', 'lab_liquidity_below_minimum',
                    'lab_fee_tier_above_maximum')
SIGNAL_REASONS = ('lab_a_no_surge', 'lab_a_no_previous_observation', 'lab_a_surge_not_fresh',
                  'lab_b_no_reference', 'lab_b_no_dip', 'lab_b_liquidity_fell', 'lab_b_change_24h',
                  'lab_b_regime_unavailable', 'lab_b_market_not_dipping', 'rnd_coin_not_drawn')
RETIRED_REASON = 'lab_forward_kill_rule_retired'


def admission_cost_cap_pct(book_id) -> float:
    """The Lab's one admission rule (LAB_ACTIVE_V6) on this book's own net stop: 0.5 x stop, <= 2.75%."""
    return activity.admission_cost_cap_pct(EXITS[book_id].stop_loss_net_pct)


def book_parameters(book_id) -> dict:
    """Canonical frozen parameters of one book (the basis of its config hash)."""
    if book_id not in BOOK_IDS:
        raise KeyError(book_id)
    hypothesis = HYPOTHESIS_OF.get(book_id, book_id)
    signal = {'kind': SIGNAL_KIND[book_id]}
    if book_id == LAB_A_ID:
        signal['surge'] = asdict(SURGE)
    elif book_id == LAB_B_ID:
        signal.update({'dip': asdict(DIP), 'regime': asdict(REGIME), 'regime_version': REGIME_VERSION})
    else:
        signal['random'] = asdict(RANDOM[book_id])
    return {
        'version': VERSION, 'book_id': book_id,
        'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
        'hypothesis': hypothesis,
        'research': {'source': RESEARCH_SOURCE, 'prereg_sha256': RESEARCH_PREREG_SHA256,
                     'entry': RESEARCH_ENTRIES[book_id]},
        'universe': {**asdict(UNIVERSES[book_id]),
                     'rug_screen': f'{structural_rug_guard.VERSION} via the defensive entry layer'},
        'signal': signal,
        'exits': asdict(EXITS[book_id]),
        'size': {'notional_usd': NOTIONAL_USD, 'rule': 'FIXED_NOTIONAL_NO_BACKOFF',
                 'start_balance_usd': START_BALANCE_USD, 'max_open_positions': 1},
        'admission_cost_cap_pct': admission_cost_cap_pct(book_id),
        'costs': {'execution_model': EXECUTION_MODEL, 'calibration': asdict(CALIB)},
        'net50': asdict(NET50),
        'heat_veto': {'version': heat_veto.VERSION, 'mode': HEAT_MODE},
        'structural_rug_guard': structural_rug_guard.VERSION,
        'pool_loss_memory': pool_loss_memory.VERSION,
        'kill_rule': asdict(KILL_RULE),
        'automatic_promotion': AUTOMATIC_PROMOTION,
    }


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def config_hash(book_id) -> str:
    """sha256 of the book's canonical parameter JSON; recorded on every position and close."""
    return hashlib.sha256(canonical_json(book_parameters(book_id)).encode('utf-8')).hexdigest()


CONFIG_HASHES = {book_id: config_hash(book_id) for book_id in BOOK_IDS}


def is_forward_book(book_id) -> bool:
    return book_id in BOOK_IDS


def is_forward_position(position) -> bool:
    return (isinstance(position, dict) and position.get('lab_forward_version') == VERSION
            and position.get('strategy_id') in BOOK_IDS)


# ------------------------------------------------------------------ small helpers

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


def _txn(coin, window, side):
    txns = coin.get('txns') if isinstance(coin.get('txns'), dict) else {}
    bucket = txns.get(window) if isinstance(txns.get(window), dict) else {}
    return _finite(bucket.get(side))


def observation_ms(coin, now):
    """Observation stamp of a feed coin, on heat_veto.PairHistory's basis (updatedAt, <= now + 5 s, else now)."""
    stamp = _finite(coin.get('updatedAt')) if isinstance(coin, dict) else None
    current = _finite(now)
    if stamp is not None and stamp > 0 and (current is None or stamp <= current + 5_000):
        return stamp
    return current


def liquidity_usd(coin):
    value = _finite(coin.get('liquidityUsd'))
    if value is None and isinstance(coin.get('liquidity'), dict):
        value = _finite(coin['liquidity'].get('usd'))
    return value


def in_regime_population(coin) -> bool:
    """PumpSwap pools quoted in SOL: the forward-test universes and the regime population."""
    return (isinstance(coin, dict) and str(coin.get('dexId') or '').lower() == REGIME.dex_id
            and feasibility.quote_token_address(coin) == REGIME.quote_token_address)


def surge_state(coin, params: SurgeParameters = SURGE) -> bool:
    """The research _surge test on one observation; a missing count is False (research NaN semantics)."""
    buys_5m, buys_1h = _txn(coin, 'm5', 'buys'), _txn(coin, 'h1', 'buys')
    if buys_5m is None or buys_1h is None:
        return False
    return (buys_5m >= params.min_buys_5m and buys_1h > params.min_buys_1h_exclusive
            and buys_5m >= params.pace_multiple * buys_1h / params.hourly_pace_divisor)


def hashed_coin(pair, t_ms, probability, salt) -> bool:
    """Research harness hashed_coin, verbatim: deterministic per (salt, pool, observation time)."""
    digest = hashlib.blake2b(('%s|%s|%d' % (salt, pair, int(t_ms))).encode(), digest_size=8).digest()
    return int.from_bytes(digest, 'big') / 2 ** 64 < probability


def median(values):
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return None
    middle = count // 2
    return ordered[middle] if count % 2 else 0.5 * (ordered[middle - 1] + ordered[middle])


def last_observation(record, at_ms, max_gap_ms=heat_veto.HISTORY_PARAMS.max_gap_ms):
    """The pair's last observation at or before ``at_ms`` from a PairHistory view.

    Returns {'price', 'age_ms', 'exact'} or None (no observation at or before it in
    the retained history). The price is exact: PairHistory stores a sample on every
    price change and at every segment start. The age is exact after the last
    observation or inside a recorded feed gap; inside a contiguous segment
    observations are at most ``max_gap_ms`` apart, so that bound is returned.
    """
    if not isinstance(record, dict):
        return None
    samples = record.get('samples') or ()
    current = _finite(at_ms)
    if not samples or current is None or current < samples[0][0]:
        return None
    price = None
    for stamp, value, _paid in samples:
        if stamp > current:
            break
        price = value
    if price is None:
        return None
    last_seen = _finite(record.get('last_seen'))
    if last_seen is not None and last_seen <= current:
        return {'price': price, 'age_ms': current - last_seen, 'exact': True}
    for gap_start, gap_end in record.get('gaps') or ():
        if gap_start <= current < gap_end:
            return {'price': price, 'age_ms': current - gap_start, 'exact': True}
    return {'price': price, 'age_ms': float(max_gap_ms), 'exact': False}


# ------------------------------------------------------------------ feed memory

class ForwardFeedMemory:
    """What the four books need beyond PairHistory, for PumpSwap/SOL pools only (bounded, in memory).

    Per pair: the LAB_A surge state of the last two distinct observations and the
    liquidity at each observation (stored on change, so the value at the last
    observation at or before any time is exact). Observations are deduplicated by
    their stamp exactly like PairHistory. ``regime`` caches the LAB_B market
    regime per whole minute. Nothing is persisted: after a restart LAB_A needs a
    second observation of a pair and LAB_B 15 minutes of history. Thread-safe.
    """

    def __init__(self, params: MemoryParameters = MEMORY_PARAMS):
        self.params = params
        self._pairs = OrderedDict()
        self._regimes = OrderedDict()
        self._lock = threading.Lock()

    def observe_coin(self, coin, now) -> bool:
        if not in_regime_population(coin):
            return False
        key = _identity(coin)
        stamp = observation_ms(coin, now)
        price = _finite(coin.get('priceUsd'))
        # The same observation set as PairHistory (which skips a missing or non-positive
        # price), so the liquidity and the price at "the last observation" are one point.
        if key is None or stamp is None or price is None or price <= 0:
            return False
        liquidity = liquidity_usd(coin)
        liquidity = liquidity if liquidity is not None and liquidity > 0 else None
        surge = surge_state(coin)
        params = self.params
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                record = {'prev': None, 'cur': None, 'last_seen': None,
                          'liquidity': deque(maxlen=params.max_liquidity_samples_per_pair)}
                self._pairs[key] = record
            elif record['last_seen'] is not None and stamp <= record['last_seen']:
                return False
            self._pairs.move_to_end(key)
            record['prev'], record['cur'] = record['cur'], (stamp, surge)
            record['last_seen'] = stamp
            samples = record['liquidity']
            if not samples or samples[-1][1] != liquidity:
                samples.append((stamp, liquidity))
            horizon = stamp - params.retention_ms
            # Keep the newest sample before the horizon: it is the value in effect there.
            while len(samples) > 1 and samples[1][0] <= horizon:
                samples.popleft()
            while len(self._pairs) > params.max_pairs:
                self._pairs.popitem(last=False)
            return True

    def observe(self, feed, now) -> int:
        stored = 0
        for coin in feed or ():
            if isinstance(coin, dict):
                stored += int(self.observe_coin(coin, now))
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

    def previous_surge(self, coin, stamp):
        """(known, surge) of the pair's last distinct observation before ``stamp``."""
        key = _identity(coin) if isinstance(coin, dict) else None
        if key is None:
            return False, None
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                return False, None
            for entry in (record['cur'], record['prev']):
                if entry is not None and entry[0] < stamp:
                    return True, entry[1]
            return False, None

    def liquidity_at(self, key, at_ms):
        """(known, liquidity) at the pair's last observation at or before ``at_ms``."""
        with self._lock:
            record = self._pairs.get(key)
            if record is None:
                return False, None
            value, known = None, False
            for stamp, liquidity in record['liquidity']:
                if stamp > at_ms:
                    break
                value, known = liquidity, True
            return known, value

    def keys(self):
        with self._lock:
            return list(self._pairs)

    def regime(self, minute_ms, history, *, params: RegimeParameters = REGIME) -> dict:
        """LAB_B med15 at the whole minute ``minute_ms`` from observations at or before it (cached).

        Pools: PumpSwap/SOL pairs whose last observation at or before T is at most
        3 min old with liquidity >= $20k; each one's change against its last
        observation at or before T - 15 min, which must be at most 10 min stale.
        med15 (%) is their median when at least 8 pools qualify, else None.
        """
        stamp = _finite(minute_ms)
        if stamp is None:
            return {'version': REGIME_VERSION, 'minute': None, 'pools': 0, 'min_pools': params.min_pools,
                    'med15_pct': None, 'dipping': False}
        minute = int(stamp // params.grid_ms * params.grid_ms)
        with self._lock:
            cached = self._regimes.get(minute)
        if cached is not None:
            return cached
        reference_at = minute - params.lookback_ms
        max_gap = getattr(getattr(history, 'params', None), 'max_gap_ms', heat_veto.HISTORY_PARAMS.max_gap_ms)
        changes = []
        for key in self.keys():
            record = history.view({'address': key[0], 'pairAddress': key[1]}) if history is not None else None
            last = last_observation(record, minute, max_gap)
            if last is None or last['age_ms'] > params.active_within_ms or not last['price'] > 0:
                continue
            known, liquidity = self.liquidity_at(key, minute)
            if not known or liquidity is None or liquidity < params.min_liquidity_usd:
                continue
            reference = last_observation(record, reference_at, max_gap)
            if (reference is None or reference['age_ms'] > params.max_reference_staleness_ms
                    or not reference['price'] > 0):
                continue
            changes.append(last['price'] / reference['price'] - 1)
        value = median(changes) if len(changes) >= params.min_pools else None
        result = {'version': REGIME_VERSION, 'minute': minute, 'pools': len(changes),
                  'min_pools': params.min_pools,
                  'med15_pct': None if value is None else value * 100,
                  'dipping': bool(value is not None and value * 100 < DIP.max_regime_med15_pct_exclusive)}
        with self._lock:
            self._regimes[minute] = result
            while len(self._regimes) > self.params.regime_cache_minutes:
                self._regimes.popitem(last=False)
        return result

    def status(self) -> dict:
        with self._lock:
            latest = next(reversed(self._regimes.values()), None) if self._regimes else None
            return {'version': MEMORY_VERSION, 'pairs': len(self._pairs),
                    'liquidity_samples': sum(len(r['liquidity']) for r in self._pairs.values()),
                    'latest_regime': dict(latest) if latest else None, 'persistent': False,
                    **asdict(self.params)}


# ------------------------------------------------------------------ universe and signals

def universe_rejections(book_id, coin) -> list:
    """Physical universe of the book (dex, SOL quote, liquidity, fee tier); the rug screen runs after."""
    universe = UNIVERSES[book_id]
    coin = coin if isinstance(coin, dict) else {}
    if str(coin.get('dexId') or '').lower() != universe.dex_id:
        return ['lab_dex_not_pumpswap']
    if feasibility.quote_token_address(coin) != universe.quote_token_address:
        return ['lab_quote_not_sol']
    reasons = []
    liquidity = liquidity_usd(coin)
    if liquidity is None or liquidity < universe.min_liquidity_usd:
        reasons.append('lab_liquidity_below_minimum')
    if universe.max_fee_tier_bps is not None and feasibility.pumpswap_fee_bps(coin) > universe.max_fee_tier_bps:
        reasons.append('lab_fee_tier_above_maximum')
    return reasons


def evaluate(book_id, coin, now, memory, history) -> dict:
    """Universe and signal of one book on one feed coin at its observation stamp.

    Returns {'universe_rejections', 'signal_rejections', 'matched', 'observed_at', 'signal'}.
    Only the current and earlier observations are read (no look-ahead).
    """
    coin = coin if isinstance(coin, dict) else {}
    stamp = observation_ms(coin, now)
    result = {'book_id': book_id, 'observed_at': None if stamp is None else int(stamp),
              'universe_rejections': universe_rejections(book_id, coin), 'signal_rejections': [],
              'matched': False, 'signal': {'kind': SIGNAL_KIND[book_id]}}
    if result['universe_rejections'] or stamp is None:
        return result
    signal = result['signal']
    reasons = result['signal_rejections']
    if book_id == LAB_A_ID:
        buys_5m, buys_1h = _txn(coin, 'm5', 'buys'), _txn(coin, 'h1', 'buys')
        surge = surge_state(coin)
        known, previous = memory.previous_surge(coin, stamp) if memory is not None else (False, None)
        signal.update({'buys_5m': buys_5m, 'buys_1h': buys_1h, 'surge': surge,
                       'previous_observation_known': known, 'previous_surge': previous})
        if not surge:
            reasons.append('lab_a_no_surge')
        elif not known:
            reasons.append('lab_a_no_previous_observation')
        elif previous:
            reasons.append('lab_a_surge_not_fresh')
    elif book_id == LAB_B_ID:
        _dip_signal(coin, stamp, memory, history, signal, reasons)
    else:
        params = RANDOM[book_id]
        drawn = hashed_coin(_identity(coin)[1] if _identity(coin) else '', stamp, params.probability, params.salt)
        signal.update({'probability': params.probability, 'salt': params.salt, 'drawn': drawn})
        if not drawn:
            reasons.append('rnd_coin_not_drawn')
    result['matched'] = not reasons
    return result


def _dip_signal(coin, stamp, memory, history, signal, reasons):
    key = _identity(coin)
    price, liquidity = _finite(coin.get('priceUsd')), liquidity_usd(coin)
    changes = coin.get('priceChange') if isinstance(coin.get('priceChange'), dict) else {}
    change_24h = _finite(changes.get('h24'))
    reference_at = stamp - DIP.lookback_seconds * 1000
    record = history.view(coin) if history is not None else None
    max_gap = getattr(getattr(history, 'params', None), 'max_gap_ms', heat_veto.HISTORY_PARAMS.max_gap_ms)
    reference = last_observation(record, reference_at, max_gap)
    known, reference_liquidity = (memory.liquidity_at(key, reference_at)
                                  if memory is not None and key is not None else (False, None))
    signal.update({'price': price, 'liquidity_usd': liquidity, 'change_24h_pct': change_24h,
                   'reference_price': reference['price'] if reference else None,
                   'reference_liquidity_usd': reference_liquidity if known else None,
                   'reference_age_ms': reference['age_ms'] if reference else None})
    if reference is None or not known:
        reasons.append('lab_b_no_reference')
        return
    # Compared as a fraction, exactly as the research (p / p15 - 1 <= -0.10).
    change = price / reference['price'] - 1 if price is not None and price > 0 and reference['price'] > 0 else None
    liquidity_ratio = (liquidity / reference_liquidity
                       if liquidity is not None and reference_liquidity is not None and reference_liquidity > 0
                       else None)
    signal.update({'return_15m_pct': None if change is None else change * 100,
                   'liquidity_ratio_15m': liquidity_ratio})
    if change is None or not change <= DIP.max_return_pct / 100:
        reasons.append('lab_b_no_dip')
    if liquidity_ratio is None or not liquidity_ratio >= DIP.min_liquidity_ratio:
        reasons.append('lab_b_liquidity_fell')
    if change_24h is None or not change_24h > DIP.min_change_24h_pct_exclusive:
        reasons.append('lab_b_change_24h')
    regime = memory.regime(stamp, history) if memory is not None else None
    signal['regime'] = regime
    if regime is None or regime['med15_pct'] is None:
        reasons.append('lab_b_regime_unavailable')
    elif not regime['med15_pct'] < DIP.max_regime_med15_pct_exclusive:
        reasons.append('lab_b_market_not_dipping')


# ------------------------------------------------------------------ cooldown, costs and net50

def pool_cooldown_remaining_ms(book, coin, now) -> int:
    """300 s after the book's latest close on the same (mint, pool)."""
    key = _identity(coin) if isinstance(coin, dict) else None
    exits = EXITS.get((book or {}).get('id'))
    if key is None or exits is None:
        return 0
    latest = None
    for row in (book.get('history') or ()):
        if isinstance(row, dict) and _identity(row) == key:
            closed = _finite(row.get('closed_at'))
            if closed is not None and (latest is None or closed > latest):
                latest = closed
    if latest is None:
        return 0
    return max(0, int(latest + exits.pool_cooldown_seconds * 1000 - _finite(now)))


def _calib_fee_bucket(fee_bps):
    fee = float(fee_bps)
    return 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')


def calib_extra_bps_per_leg(fee_bps, liquidity_usd_value, notional_usd, params: CalibParameters = CALIB) -> dict:
    """CALIB_V1 calib_extra_bps_per_leg(basis='engine', conservative=True), with its components."""
    bucket = _calib_fee_bucket(fee_bps)
    central = {'fee<=50': params.fee_le_50_bps, 'fee55-95': params.fee_55_95_central_bps,
               'fee100-125': params.fee_100_125_central_bps}[bucket]
    fee_bucket_bps = max(central, params.floor_bps)
    liquidity, notional = _finite(liquidity_usd_value), _finite(notional_usd)
    in_range = bool(liquidity is not None and notional is not None and liquidity >= params.measured_min_liquidity_usd
                    and notional > 0 and 100.0 * notional / liquidity <= params.measured_max_size_pct_of_liquidity)
    margin = 0.0 if in_range else params.unmeasured_margin_bps
    fixed = 0.0
    if notional is not None and notional > 0:
        fixed = 1e4 * (params.engine_rent_usd_per_trade / 2
                       + (params.engine_network_usd_per_leg - params.model_network_usd_per_leg)) / notional
    return {'version': params.version, 'fee_bucket': bucket, 'fee_bucket_bps': fee_bucket_bps,
            'unmeasured_margin_bps': margin, 'engine_fixed_bps': fixed, 'in_measured_range': in_range,
            'total_bps': fee_bucket_bps + margin + fixed}


def exit_reason_extra_bps(reason, params: Net50Parameters = NET50) -> float:
    label = str(reason or '').upper()
    if label.startswith('STOP'):
        return params.stop_exit_extra_bps
    if 'TRAIL' in label:
        return params.trailing_exit_extra_bps
    return 0.0


def net50(booked_pnl_usd, notional_usd, reason, params: Net50Parameters = NET50) -> dict:
    """net50 of one close: booked net minus 50 bps per leg, minus the stop/trailing exit extra."""
    pnl, notional = _finite(booked_pnl_usd), _finite(notional_usd)
    if pnl is None or notional is None or notional <= 0:
        return {'net50_usd': None, 'net50_pct': None}
    extra = exit_reason_extra_bps(reason, params)
    keep = (1 - params.stress_bps_per_leg / 1e4) * (1 - (params.stress_bps_per_leg + extra) / 1e4)
    pct = 100 * ((1 + pnl / notional) * keep - 1)
    return {'net50_usd': round(notional * pct / 100, 6), 'net50_pct': round(pct, 6),
            'net50_stress_bps_per_leg': params.stress_bps_per_leg, 'net50_exit_extra_bps': extra,
            'net50_version': NET50_VERSION}


def exit_reason(book_id, model_net_pct, hold_minutes):
    """Research exit order on the uncalibrated model net: stop, take-profit, max hold."""
    exits = EXITS[book_id]
    if model_net_pct <= -exits.stop_loss_net_pct:
        return exits.stop_reason
    if model_net_pct >= exits.take_profit_net_pct:
        return exits.take_profit_reason
    if hold_minutes >= exits.max_hold_minutes:
        return exits.max_hold_reason
    return None


def position_calib_bps(position) -> float:
    """Calibrated extra bps per leg of a forward-test position; 0 for every other position."""
    if not is_forward_position(position):
        return 0.0
    value = _finite(position.get('calib_bps_per_leg'))
    return value if value is not None and value > 0 else 0.0


def position_record(book_id, *, evaluation, calib, model_entry, model_mark, defensive_flags) -> dict:
    """Fields every forward-test position carries (and every close inherits)."""
    return {
        'lab_forward_version': VERSION, 'lab_config_hash': CONFIG_HASHES[book_id],
        'lab_forward': {'version': VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
                        'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
                        'hypothesis': HYPOTHESIS_OF.get(book_id, book_id),
                        'observed_at': evaluation.get('observed_at'), 'signal': evaluation.get('signal')},
        'heat_veto_mode': HEAT_MODE, 'heat_log_only_flags': list(defensive_flags or ()),
        'calib_bps_per_leg': round(calib['total_bps'], 6), 'cost_calibration': calib,
        'model_quantity': model_entry['quantity'],
        'model_entry_roundtrip_pnl_pct': round(model_mark, 6),
        'exit_policy_label': EXITS[book_id].label, 'exit_parameters': asdict(EXITS[book_id]),
        'promotion_eligible': False,
    }


def close_record(position, trade, *, model_net_proceeds_usd, reason) -> dict:
    """Fields added to a forward-test close: model P&L, calibration cost and net50."""
    notional = _finite(position.get('notional_usd'))
    # The model leg's cost basis equals the booked one: calibration changes prices, not capital.
    basis = (notional or 0.0) + (_finite(position.get('entry_network_fee_usd')) or 0.0)
    model_proceeds = _finite(model_net_proceeds_usd)
    model_pnl = None if model_proceeds is None else model_proceeds - basis
    booked = _finite(trade.get('pnl_usd'))
    record = {'model_pnl_usd': None if model_pnl is None else round(model_pnl, 6),
              'model_pnl_pct': (None if model_pnl is None or not notional
                                else round(model_pnl / notional * 100, 6)),
              'calibration_cost_usd': (None if model_pnl is None or booked is None
                                       else round(model_pnl - booked, 6)),
              'lab_forward_version': VERSION,
              'lab_config_hash': position.get('lab_config_hash'),
              'heat_log_only_flags': list(position.get('heat_log_only_flags') or ())}
    record.update(net50(booked, notional, reason))
    return record


# ------------------------------------------------------------------ statistics, kill rule, promotion gate

def _evaluated_closes(book, book_id, now):
    """Unique closes of the book's frozen config with a finite net50 (chronological)."""
    rows = {}
    expected = CONFIG_HASHES[book_id]
    for row in (book.get('history') or ()):
        if not isinstance(row, dict):
            continue
        closed, value = _finite(row.get('closed_at')), _finite(row.get('net50_usd'))
        trade_no = row.get('trade_no')
        if (row.get('strategy_id') != book_id or row.get('lab_forward_version') != VERSION
                or row.get('lab_config_hash') != expected or closed is None or value is None
                or (now is not None and closed > now) or _identity(row) is None
                or isinstance(trade_no, bool) or not isinstance(trade_no, int)):
            continue
        rows.setdefault(trade_no, row)
    return sorted(rows.values(), key=lambda row: (_finite(row.get('closed_at')), row.get('trade_no')))


def bootstrap_ci(rows, *, key='net50_usd', params: KillRuleParameters = KILL_RULE):
    """95% CI of the mean per-trade value: pair (mint, pool) bootstrap with a fixed seed.

    Groups follow the order of their first close, so the result is deterministic.
    Under ``fallback_under_pairs`` distinct pairs: per-trade normal approximation.
    """
    values = [_finite(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {'low': None, 'high': None, 'method': None, 'groups': 0}
    groups = OrderedDict()
    for row in rows:
        value = _finite(row.get(key))
        if value is not None:
            groups.setdefault(_identity(row), []).append(value)
    if len(groups) >= params.fallback_under_pairs:
        rnd = random.Random(params.bootstrap_seed)
        members = list(groups.values())
        totals = [(math.fsum(group), len(group)) for group in members]
        means = []
        for _ in range(params.bootstrap_resamples):
            total = count = 0
            for _ in range(len(members)):
                group_total, group_count = totals[rnd.randrange(len(members))]
                total += group_total
                count += group_count
            means.append(total / count)
        means.sort()
        reps = params.bootstrap_resamples
        return {'low': means[int(reps * .025)], 'high': means[min(reps - 1, int(reps * .975))],
                'method': params.ci_method, 'groups': len(groups)}
    count = len(values)
    mean = math.fsum(values) / count
    if count < 2:
        return {'low': None, 'high': None, 'method': params.fallback_method, 'groups': len(groups)}
    variance = math.fsum((value - mean) ** 2 for value in values) / (count - 1)
    half = 1.959963984540054 * math.sqrt(variance / count)
    return {'low': mean - half, 'high': mean + half, 'method': params.fallback_method, 'groups': len(groups)}


_CI_CACHE = OrderedDict()
_CI_LOCK = threading.Lock()


def _cached_ci(book_id, rows):
    fingerprint = (book_id, len(rows), tuple((row.get('trade_no'), row.get('net50_usd')) for row in rows[-3:]),
                   round(math.fsum(_finite(row.get('net50_usd')) for row in rows), 9))
    with _CI_LOCK:
        cached = _CI_CACHE.get(fingerprint)
    if cached is None:
        cached = bootstrap_ci(rows)
        with _CI_LOCK:
            _CI_CACHE[fingerprint] = cached
            while len(_CI_CACHE) > 32:
                _CI_CACHE.popitem(last=False)
    return dict(cached)


def _round(value, digits=6):
    value = _finite(value)
    return None if value is None else round(value, digits)


def evidence(book, book_id, now) -> dict:
    """Kill-rule evidence of one book on its own frozen-config closes."""
    rows = _evaluated_closes(book, book_id, now)
    count = len(rows)
    values = [_finite(row['net50_usd']) for row in rows]
    mean = math.fsum(values) / count if count else None
    ci = _cached_ci(book_id, rows) if count else {'low': None, 'high': None, 'method': None, 'groups': 0}
    pairs = {_identity(row) for row in rows}
    retire = bool(count >= KILL_RULE.min_closes and mean is not None
                  and mean < KILL_RULE.max_mean_net50_usd_exclusive
                  and ci['high'] is not None and ci['high'] < KILL_RULE.max_ci95_upper_usd_exclusive)
    pct = [_finite(row.get('net50_pct')) for row in rows]
    pct = [value for value in pct if value is not None]
    return {'version': KILL_RULE_VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
            'closed_trades': count, 'pairs': len(pairs),
            'wins': sum(value > 0 for value in values),
            'net50_total_usd': _round(math.fsum(values)) if count else None,
            'mean_net50_usd': _round(mean), 'mean_net50_pct': _round(math.fsum(pct) / len(pct)) if pct else None,
            'ci95_mean_net50_usd': [_round(ci['low']), _round(ci['high'])], 'ci_method': ci['method'],
            'ci_groups': ci['groups'],
            'booked_total_usd': _round(math.fsum(_finite(row.get('pnl_usd')) or 0.0 for row in rows)) if count else None,
            'first_closed_at': int(_finite(rows[0]['closed_at'])) if rows else None,
            'last_closed_at': int(_finite(rows[-1]['closed_at'])) if rows else None,
            'min_closes': KILL_RULE.min_closes, 'kill_rule_met': retire,
            'note': 'PAPER forward test; net50 is booked net minus the research stress. Not a profit claim.'}


def _max_drawdown_pct(rows, start):
    balance = peak = start
    worst = 0.0
    for row in rows:
        balance += _finite(row.get('pnl_usd')) or 0.0
        peak = max(peak, balance)
        if peak > 0:
            worst = max(worst, (peak - balance) / peak * 100)
    return worst


def promotion_gate(book, book_id, control_book, now) -> dict:
    """The research PROMOTION GATE on the hypothesis book's closes (owner review only, never automatic)."""
    rows = _evaluated_closes(book, book_id, now)
    values = [_finite(row['net50_usd']) for row in rows]
    count = len(values)
    criteria = {}

    def put(name, value, threshold, passed):
        criteria[name] = {'value': value, 'threshold': threshold, 'pass': passed}

    put('closed_trades', count, f'>= {GATE.min_closes}', count >= GATE.min_closes)
    pair_totals = OrderedDict()
    for row, value in zip(rows, values):
        pair_totals.setdefault(_identity(row), []).append(value)
    put('pairs', len(pair_totals), f'>= {GATE.min_pairs}', len(pair_totals) >= GATE.min_pairs)
    opened = [_finite(row.get('opened_at')) for row in rows]
    opened = [value for value in opened if value is not None]
    days = (max(opened) - min(opened)) / 86_400_000 if len(opened) >= 2 else 0.0
    put('days', _round(days, 3), f'>= {GATE.min_days}', days >= GATE.min_days)
    hours = {int(value // 3_600_000) % 24 for value in opened}
    put('utc_hours_covered', len(hours), f'= {GATE.utc_hours_required}', len(hours) >= GATE.utc_hours_required)
    mean = math.fsum(values) / count if count else None
    put('mean_net50_usd', _round(mean), '> 0', bool(mean is not None and mean > 0))
    ci = _cached_ci(book_id, rows) if count else {'low': None, 'high': None}
    put('ci95_low_net50_usd', _round(ci['low']), '> 0', bool(ci['low'] is not None and ci['low'] > 0))
    control_rows = []
    if control_book is not None and opened:
        control_id = CONTROL_OF[book_id]
        start = min(opened)
        end = max(_finite(row.get('closed_at')) for row in rows)
        control_rows = [row for row in _evaluated_closes(control_book, control_id, now)
                        if start <= (_finite(row.get('opened_at')) or -1) <= end]
    control_values = [_finite(row['net50_usd']) for row in control_rows]
    control_mean = math.fsum(control_values) / len(control_values) if control_values else None
    control_ci = (_cached_ci(f'{CONTROL_OF[book_id]}@{book_id}', control_rows) if control_rows
                  else {'low': None, 'high': None})
    half_width = (None if control_ci['low'] is None or control_ci['high'] is None
                  else (control_ci['high'] - control_ci['low']) / 2)
    margin = None if mean is None or control_mean is None else mean - control_mean
    put('beats_same_period_control_usd', _round(margin),
        f'> control CI half-width ({_round(half_width)})',
        bool(margin is not None and half_width is not None and margin > half_width))
    top_share = (max(len(group) for group in pair_totals.values()) / count) if count else None
    put('top_pair_share', _round(top_share), f'<= {GATE.max_top_pair_share}',
        bool(top_share is not None and top_share <= GATE.max_top_pair_share))
    if len(pair_totals) >= 2:
        best = max(pair_totals, key=lambda key: math.fsum(pair_totals[key]))
        rest = [value for key, group in pair_totals.items() if key != best for value in group]
        without_best = math.fsum(rest) / len(rest)
    else:
        without_best = None
    put('mean_without_best_pair_usd', _round(without_best), '> 0',
        bool(without_best is not None and without_best > 0))
    by_day = {}
    for row, value in zip(rows, values):
        day = int(_finite(row.get('closed_at')) // 86_400_000)
        by_day[day] = by_day.get(day, 0.0) + value
    total = math.fsum(values)
    best_day_share = (max(by_day.values()) / total) if by_day and total > 0 else None
    put('best_day_share_of_pnl', _round(best_day_share), f'<= {GATE.max_best_day_share}',
        bool(best_day_share is not None and best_day_share <= GATE.max_best_day_share))
    put('max_drawdown_pct_3slot_1000', None, '<= 20 (research: 3-slot $1000 replay)', None)
    rug_entries = sum(1 for row in rows
                      if ((row.get('defensive_entry') or {}).get('structural_rug_guard') or {}).get('blocked'))
    put('entries_on_rug_flagged_pools', rug_entries, '= 0', rug_entries == 0)
    unpriced = sum(1 for row in rows if row.get('quote_status') in {'stale', 'unavailable'})
    share = unpriced / count if count else None
    put('vanished_or_unpriced_share', _round(share), f'< {GATE.max_vanished_or_unpriced_share}',
        bool(share is not None and share < GATE.max_vanished_or_unpriced_share))
    evaluable = [item['pass'] for item in criteria.values() if item['pass'] is not None]
    return {'version': PROMOTION_GATE_VERSION, 'book_id': book_id, 'control_book_id': CONTROL_OF[book_id],
            'control_closed_trades_same_period': len(control_rows), 'control_mean_net50_usd': _round(control_mean),
            'book_max_drawdown_pct_booked': _round(_max_drawdown_pct(rows, START_BALANCE_USD), 4),
            'criteria': criteria,
            'all_evaluable_pass': bool(evaluable) and all(evaluable),
            'not_evaluated': [name for name, item in criteria.items() if item['pass'] is None],
            'automatic_promotion': AUTOMATIC_PROMOTION,
            'note': 'Owner review only. Passing never promotes a book; the 3-slot drawdown needs an offline replay.'}


def apply_kill_rules(books, review, *, registered_ids, now) -> dict:
    """LAB_FORWARD_KILL_RULE_V1 for the four books; merged into the shared lifecycle review.

    A retirement only stops new entries: balance, history and the exits of an
    open position are untouched. It persists like the shared lifecycle marker
    (never re-enabled automatically, even if later closes improve).
    """
    review = dict(review or {})
    retired = list(review.get('retired_strategy_ids') or [])
    draining = list(review.get('retired_open_position_ids') or [])
    active = list(review.get('active_registered_strategy_ids') or [])
    for book_id in BOOK_IDS:
        book = books.get(book_id) if isinstance(books, dict) else None
        if book_id not in registered_ids or not isinstance(book, dict):
            continue
        current = evidence(book, book_id, now)
        previous = book.get('strategy_lifecycle') or {}
        if isinstance(previous, dict) and previous.get('status') == 'retired':
            # Any retirement persists with its original evidence; only a reviewed change re-enables.
            marker = {**previous, 'entry_enabled': False, 'position_management_enabled': True,
                      'current_evidence': current}
        else:
            met = current['kill_rule_met']
            marker = {'version': KILL_RULE_VERSION, 'status': 'retired' if met else 'active',
                      'entry_enabled': not met, 'position_management_enabled': True,
                      'reason': ('pre_registered_kill_rule' if met
                                 else 'kill_rule_min_closes_not_reached'
                                 if current['closed_trades'] < KILL_RULE.min_closes
                                 else 'kill_rule_threshold_not_met'),
                      'evidence': current}
            if met:
                marker['retired_at'] = now
        if book_id in CONTROL_OF:
            marker['promotion_gate'] = promotion_gate(book, book_id, books.get(CONTROL_OF[book_id]), now)
        book['strategy_lifecycle'] = marker
        if marker['status'] == 'retired':
            retired.append(book_id)
            if book.get('position'):
                draining.append(book_id)
        else:
            active.append(book_id)
    review.update({'retired_strategy_ids': sorted(set(retired)),
                   'retired_open_position_ids': sorted(set(draining)),
                   'active_registered_strategy_ids': sorted(set(active)),
                   'lab_forward_kill_rule': {'version': KILL_RULE_VERSION, 'book_ids': list(BOOK_IDS),
                                             'replaces_shared_lifecycle_heuristic': True,
                                             **asdict(KILL_RULE)}})
    return review


# ------------------------------------------------------------------ diagnostics and published config

def new_diagnostics(book_id) -> dict:
    return {'version': VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
            'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
            'universe_candidates': 0, 'universe_rejections': {}, 'signal_rejections': {},
            'signals': 0, 'heat_mode': HEAT_MODE, 'admission_cost_cap_pct': admission_cost_cap_pct(book_id),
            'notional_usd': NOTIONAL_USD, 'exits': asdict(EXITS[book_id]),
            'automatic_promotion': AUTOMATIC_PROMOTION, 'profitability_proven': False}


def record_evaluation(diagnostics, evaluation):
    for reason in evaluation['universe_rejections']:
        diagnostics['universe_rejections'][reason] = diagnostics['universe_rejections'].get(reason, 0) + 1
    if evaluation['universe_rejections']:
        return
    diagnostics['universe_candidates'] += 1
    for reason in evaluation['signal_rejections']:
        diagnostics['signal_rejections'][reason] = diagnostics['signal_rejections'].get(reason, 0) + 1
    diagnostics['signals'] += int(evaluation['matched'])


def summary(book_id) -> dict:
    """Compact published form of one book (the full canonical parameters are book_parameters)."""
    universe, exits = UNIVERSES[book_id], EXITS[book_id]
    out = {'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
           'hypothesis': HYPOTHESIS_OF.get(book_id, book_id), 'signal': SIGNAL_KIND[book_id],
           'min_liquidity_usd': universe.min_liquidity_usd, 'max_fee_tier_bps': universe.max_fee_tier_bps,
           'exits': exits.label, 'stop_loss_net_pct': exits.stop_loss_net_pct,
           'take_profit_net_pct': exits.take_profit_net_pct, 'max_hold_minutes': exits.max_hold_minutes,
           'pool_cooldown_seconds': exits.pool_cooldown_seconds, 'notional_usd': NOTIONAL_USD,
           'admission_cost_cap_pct': admission_cost_cap_pct(book_id), 'config_hash': CONFIG_HASHES[book_id]}
    if book_id in RANDOM:
        out.update({'probability': RANDOM[book_id].probability, 'salt': RANDOM[book_id].salt})
    return out


def config() -> dict:
    """Published definition of the four books; part of the Lab's activity_config."""
    return {'version': VERSION, 'book_ids': list(BOOK_IDS), 'book_names': dict(BOOK_NAMES),
            'controls': dict(CONTROL_OF), 'config_hashes': dict(CONFIG_HASHES),
            'books': {book_id: summary(book_id) for book_id in BOOK_IDS},
            'research_source': RESEARCH_SOURCE, 'research_prereg_sha256': RESEARCH_PREREG_SHA256,
            'calibration': CALIB_VERSION, 'net50': NET50_VERSION, 'heat_mode': HEAT_MODE,
            'heat_log_only_book_ids': sorted(HEAT_LOG_ONLY_BOOK_IDS),
            'kill_rule_version': KILL_RULE_VERSION, 'promotion_gate_version': PROMOTION_GATE_VERSION,
            'promotion_gate': asdict(GATE), 'memory_version': MEMORY_VERSION, 'regime_version': REGIME_VERSION,
            'universe_reasons': list(UNIVERSE_REASONS), 'signal_reasons': list(SIGNAL_REASONS),
            'automatic_promotion': AUTOMATIC_PROMOTION, 'profitability_proven': False,
            'evidence_status': 'PRE_REGISTERED_HYPOTHESES_NEGATIVE_ABSOLUTE_RESEARCH_RESULT'}
