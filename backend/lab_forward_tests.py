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
per-trade normal approximation under 3 pairs, and below the 50 closes the
rule needs) is < 0, on the booked or on the research-fill basis. Promotion is
never automatic; the promotion gate is computed for the owner's review only and
is 'met' only when every criterion, including the offline 3-slot drawdown
replay, has been evaluated and passes.

Close policy (LAB_FORWARD_CLOSE_POLICY_V1), forward books only: the booked
exit leg values a drained pool as the research does (constant-product impact
x / (1 + x), x = 2 x value / liquidity, never below the shared model's capped
impact; a reported liquidity of 0 is worth 0), and a position whose exact pool
has given no usable mark beyond max hold plus 10 min closes as VANISHED at
its last mark minus the research's 10% haircut. Both kinds are counted in the
kill-rule evidence and the gate's vanished-or-unpriced share. A mark that
omits the pool liquidity is a reported liquidity of 0 on every path
(LAB_FORWARD_MARK_LIQUIDITY_V2): main's scan feed and the exact-pair refresh
both normalize the omitted field to liquidityUsd 0.0, and in the research
every scan-feed liquidity of 0 (omitted fields included) was terminal (F6).
Its price triggers the exits and its sale is booked as a drain, the same as a
reported 0.

Cash state (LAB_FORWARD_CASH_STATE_V1): a book whose balance cannot fund the
fixed $200 entry (plus a network-fee reserve) and holds no position cannot
trade again; its marker says 'cash_exhausted' instead of 'active', and the
gate's control comparison is limited to the period in which the control could
still enter (control_coverage).

Control continuity (LAB_FORWARD_CONTROL_CONTINUITY_V1): a random control exists
for the gate's same-period comparison, so while its hypothesis can still enter
the control keeps entering. Its kill rule is evaluated and published but only
retires it once the hypothesis has stopped, and a control that can no longer
fund its fixed entry keeps entering at the fixed $200 as a zero-capital
measurement (capital_mode 'zero_capital_control': same signal, gates, exits,
costs and records; the close never moves the balance).

Signal carry (LAB_FORWARD_SIGNAL_CARRY_V1): a matched signal whose only blocker
is the asynchronous price cross-check (price_crosscheck_pending) is retried on
later refreshes for up to 60 s after its observation (the research fill window)
against the pool's current observation, with every other gate re-run. Without
it a single-observation signal (a LAB_A fresh crossing, every random draw) was
lost whenever the price reference was cold, while LAB_B's persistent dip was
not. Episodes are counted per book and config hash: entered, lost to a still
pending price check, dropped by another gate, superseded.

Fill basis (LAB_FORWARD_FILL_BASIS_V2): the books book their entry at the
entry observation's DexScreener print and their exit at the triggering mark.
The research judged every book on fills at the next DexScreener refresh
(harness_final F1: the first later exact-pool observation within 60 s whose
price differs from the decision print, else the next observation). Each forward
position and close therefore carries a research-fill shadow ('research_fill',
one leg per side) that is completed once both legs are known, at most about
90 s after the close; booked fields never change. The entry leg is decided
where the research decided: at the matched signal observation, which for a
signal carried past a pending price check is earlier than the entry
observation (V1, never released, anchored it at the entry observation and so
filled such an entry one refresh after the research would have). The close gets
net50_research_fill_usd / _pct: the same booked model re-run at the research
fill observations, minus the same net50 stress. The kill rule is evaluated on
both bases (met on either retires) and the gate's expectancy, CI and control
criteria must pass on both. Once valued, a shadow drops its observation
snapshots (LAB_FORWARD_FILL_SHADOW_COMPACT_V1; the result keeps the valuation
inputs), because the Lab rewrites its whole ledger every loop.

Positions keep the exits they were opened with: exit triggers, the vanish clock
and the unpriced-past-max-hold count read the position's own exit_parameters,
and any released LAB_FORWARD_TESTS version (KNOWN_VERSIONS) is still booked as
a forward position.

The config hash includes the resolved Lab cost-model knobs (NEO_LAB_* env), so
a changed cost model starts a new evidence sample whose closes never pool with
the old ones. It does not start a new ledger: the same book keeps its balance,
its cash state and a kill-rule retirement. A new test with fresh funding needs
new, versioned book ids.
"""
import bisect
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import random
import threading

import heat_veto
import lab_activity as activity
import lab_paired_costs as lab_costs
import paper_market_feasibility as feasibility
import pool_loss_memory
import structural_rug_guard

VERSION = 'LAB_FORWARD_TESTS_V1'
ENTRY_POLICY_VERSION = VERSION
KILL_RULE_VERSION = 'LAB_FORWARD_KILL_RULE_V1'
PROMOTION_GATE_VERSION = 'LAB_FORWARD_PROMOTION_GATE_V1'
MEMORY_VERSION = 'LAB_FORWARD_FEED_MEMORY_V1'
REGIME_VERSION = 'LAB_B_MARKET_REGIME_MED15_V1'
CLOSE_POLICY_VERSION = 'LAB_FORWARD_CLOSE_POLICY_V1'
CASH_STATE_VERSION = 'LAB_FORWARD_CASH_STATE_V1'
CONTROL_CONTINUITY_VERSION = 'LAB_FORWARD_CONTROL_CONTINUITY_V1'
SIGNAL_CARRY_VERSION = 'LAB_FORWARD_SIGNAL_CARRY_V1'
# V2: a carried signal's entry leg is decided at its signal observation. V1 (never
# released, no close under it) decided it at the later entry observation.
FILL_BASIS_VERSION = 'LAB_FORWARD_FILL_BASIS_V2'
# Part of the close policy's parameters (and so of every config hash): a mark that omits
# the pool liquidity is a reported 0, on the exact-pair refresh as in the shared feed.
# V1 (never released, no close under it) ignored such a mark's price.
MARK_LIQUIDITY_VERSION = 'LAB_FORWARD_MARK_LIQUIDITY_V2'
# Storage form of a completed research-fill shadow (valuation and booking unchanged):
# the observation snapshots are dropped once the result is computed.
FILL_SHADOW_STORAGE_VERSION = 'LAB_FORWARD_FILL_SHADOW_COMPACT_V1'
# lab_position_marks.parse_pair_response's mark_source (restated: that module opens an
# HTTP session at import; a test pins equality).
EXACT_PAIR_MARK_SOURCE = 'DEXSCREENER_EXACT_POOL_API'
# Every released LAB_FORWARD_TESTS version whose open positions this module still
# books and exits (with their own stored exit parameters). Add, never remove.
KNOWN_VERSIONS = (VERSION,)
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
# The entry's network fee (0.0001 SOL) must be funded on top of the fixed $200;
# $0.10 covers it up to $1,000/SOL. Below this balance, with no open position,
# the book can never enter again (LAB_FORWARD_CASH_STATE_V1).
ENTRY_NETWORK_FEE_RESERVE_USD = 0.10
# LAB_FORWARD_CONTROL_CONTINUITY_V1 capital modes of a position (and its close).
FUNDED = 'funded'
ZERO_CAPITAL = 'zero_capital_control'
PORTFOLIO_GROUP = 'TEST'
AUTOMATIC_PROMOTION = False
# These books never read tape flow (no flow gate at entry, no flow exit; marks
# come from the shared feed or the DexScreener exact-pair refresh), so their
# positions do not take a tape exit pin (tape_pool_scheduler V6).
TAPE_PIN_REQUIRED = False


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
    # Below the closes the rule needs, the published CI is the per-trade normal
    # approximation (the bootstrap only runs where a decision can depend on it).
    bootstrap_min_closes: int = 50
    action: str = 'retire_new_entries_only'
    # LAB_FORWARD_FILL_BASIS_V2: the rule is evaluated on the booked net50 and on the
    # research-fill net50 (each on its own closes); met on either basis retires.
    bases: str = 'booked and research_fill (LAB_FORWARD_FILL_BASIS_V2); met on either retires'


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
    # The control must have been able to enter (not retired, not out of cash) for at
    # least this share of the hypothesis's evaluated trades and of its time window.
    min_control_coverage: float = 0.90
    max_drawdown_pct_3slot_1000: float = 20.0
    # LAB_FORWARD_FILL_BASIS_V2: expectancy, CI and control criteria pass on both bases,
    # and fewer than this share of the research-fill legs fell back to the decision print.
    max_research_fill_unobserved_leg_share: float = 0.10
    met_rule: str = ('gate_met only when every criterion was evaluated and passes; the 3-slot $1000 '
                     'drawdown needs an offline replay, so the Lab reports at most '
                     'evaluable_criteria_pass_replay_pending')


@dataclass(frozen=True)
class MemoryParameters:
    retention_ms: int = heat_veto.HISTORY_PARAMS.retention_ms
    max_pairs: int = heat_veto.HISTORY_PARAMS.max_pairs
    max_liquidity_samples_per_pair: int = 720
    regime_cache_minutes: int = 30


@dataclass(frozen=True)
class CostModelParameters:
    """The Lab spot model's resolved knobs (NEO_LAB_* env; strategy_lab and its frozen copy lab_paired_costs).

    Part of every book's config hash: closes booked under another cost model are
    another test. strategy_lab refuses forward entries when its own resolved values
    differ from these or from the pre-registered defaults (PREREGISTERED_COST_MODEL;
    fail closed, 'lab_forward_cost_model_mismatch'): both modules read the same
    NEO_LAB_* environment, so only the second comparison sees an override.
    """
    execution_model: str
    generic_dex_fee_bps: float
    base_slippage_bps: float
    latency_buffer_bps: float
    network_fee_sol: float
    max_price_impact_pct: float


@dataclass(frozen=True)
class ClosePolicyParameters:
    """LAB_FORWARD_CLOSE_POLICY_V1: drained and vanished pools, forward books only (research harness_final F3/F6)."""
    version: str = CLOSE_POLICY_VERSION
    exit_impact: str = ('booked exit impact = max(shared capped impact, x / (1 + x)), x = 2 x value / reported '
                        'liquidity; 100% when the reported liquidity is <= 0 (research rug/realistic_exit.py)')
    exit_impact_applies_to: str = 'booked marks and closes; exit triggers keep the uncalibrated shared model'
    vanish_grace_minutes: float = 10.0          # research FEED_GAP_MS
    vanish_haircut_pct: float = 10.0            # research VANISH_HAIRCUT_PCT
    vanish_confirm_seconds: int = 60            # continuous no-mark time in this Lab process
    feed_alive_max_age_ms: int = 60_000         # the shared feed must still be priced
    vanish_reason: str = 'VANISHED_NO_FRESH_MARK'
    # LAB_FORWARD_MARK_LIQUIDITY_V2. main's make_coin and parse_pair_response both turn an
    # omitted DexScreener liquidity into liquidityUsd 0.0, and every such scan-feed point in
    # the research was terminal (F6: 570 of 570; LP pulls drop liquidity at an unchanged
    # price). V1 (never released) ignored the mark instead, so its price could not stop out.
    omitted_liquidity: str = ('LAB_FORWARD_MARK_LIQUIDITY_V2: a mark without liquidity.usd (the shared feed or the '
                              'exact-pair refresh, DEXSCREENER_EXACT_POOL_API) is a reported liquidity of 0, as '
                              'both normalize it: its price is used, exits trigger on it as on any mark, and the '
                              'drain-aware exit books the sale at 0 (research F6: every scan-feed liquidity of 0, '
                              'omitted fields included, was terminal)')


@dataclass(frozen=True)
class CashParameters:
    """LAB_FORWARD_CASH_STATE_V1: a book that cannot fund its fixed entry and holds nothing is out of cash."""
    version: str = CASH_STATE_VERSION
    notional_usd: float = NOTIONAL_USD
    network_fee_reserve_usd: float = ENTRY_NETWORK_FEE_RESERVE_USD
    min_entry_balance_usd: float = NOTIONAL_USD + ENTRY_NETWORK_FEE_RESERVE_USD
    status: str = 'cash_exhausted'
    reason: str = 'balance_below_fixed_notional'


@dataclass(frozen=True)
class ControlContinuityParameters:
    """LAB_FORWARD_CONTROL_CONTINUITY_V1: a random control keeps entering while its hypothesis can.

    The gate compares the hypothesis with its control over the same period, so
    neither the control's own kill rule nor its cash may end that period early.
    """
    version: str = CONTROL_CONTINUITY_VERSION
    applies_to: str = 'random controls only'
    while_hypothesis: str = 'entry-enabled: not retired by its kill rule and not cash_exhausted'
    kill_rule: str = ('evaluated and published; retires the control only once its hypothesis can no '
                      'longer enter (status active, reason control_kill_rule_deferred, kill_rule_met true)')
    cash: str = ('below the cash minimum the control enters the fixed notional as a zero-capital '
                 'measurement: same signal, gates, exits, costs and close records; its closes never '
                 'move the balance (balance_effect_usd 0)')
    capital_mode: str = ZERO_CAPITAL
    evidence: str = 'kill-rule evidence and the gate count funded and zero-capital closes alike'


@dataclass(frozen=True)
class SignalCarryParameters:
    """LAB_FORWARD_SIGNAL_CARRY_V1: a signal held back only by the pending price cross-check is retried.

    The research filled an order decided at one observation at the next price
    refresh within 60 s (harness_final MAX_FILL_LAG_MS). The Lab's independent
    price cross-check is asynchronous, so a signal that exists at one observation
    only (a LAB_A fresh crossing, a random draw) was lost whenever the reference
    was cold. A carried signal re-runs every other gate on the pool's current
    observation and enters at that observation's price.
    """
    version: str = SIGNAL_CARRY_VERSION
    carried_blocker: str = 'price_crosscheck_pending'
    max_age_ms: int = 60_000
    age_basis: str = "the latest matched signal observation of the pool (observed_at)"
    retry_basis: str = ("the pool's current feed observation; universe, structural guard and loss memory "
                        '(decision and commit), network price, price cross-check with exact identity, '
                        'cooldowns, cost cap, cash and retirement are re-run')
    max_pending_per_book: int = 32
    applies_to: str = 'all four books (each hypothesis and its control see the same latency)'
    # strategy_lab never schedules its RugCheck + Jupiter price probe for these books.
    jupiter_probe: str = ('never requested by these books: the GeckoTerminal exact-pool reference, or a '
                          'Jupiter tie-break another book already cached, must resolve within the carry')


@dataclass(frozen=True)
class FillBasisParameters:
    """LAB_FORWARD_FILL_BASIS_V2: a research-fill shadow next to the booked fills (research harness_final F1).

    The books keep booking at the entry observation's print (entry) and at the
    triggering mark (exit). Each side also records where the research harness
    would have filled an order decided at its decision observation: the first
    later observation of the exact pool, within 60 s of the decision
    observation, whose priceUsd differs from the decision print (the next
    DexScreener refresh); with no differing price in the window, the first later
    observation in it (a quiet market, the harness's i + 1). With no later
    observation in the window at all the leg is 'no_next_observation' and is
    valued at the decision print (the harness would have skipped such an entry
    and taken a later point for such an exit); those legs are counted, not hidden.
    A VANISHED close has no exit fill (the harness values it at the last price
    minus the haircut, as booked).

    The entry's decision observation is the matched signal observation the entry
    acts on, as in the harness (fill_index(s, i) at the signal point i). For a
    signal carried past a pending price check (LAB_FORWARD_SIGNAL_CARRY_V1) that
    is the signal observation, not the later entry observation: the carry keeps
    the leg and advances it with each observation of the pool it retries on, and
    the entry observation advances it last. V1 (never released, no close under
    it) decided a carried entry's leg at the entry observation, so the shadow
    filled such an entry one refresh after the harness and counted the carry's
    latency twice on the research-fill basis.
    """
    version: str = FILL_BASIS_VERSION
    source: str = 'research harness_final F1 (fill_index, FILL_RULE next_refresh, MAX_FILL_LAG_MS)'
    entry_decision: str = ('the matched signal observation the entry acts on (evaluation observed_at): a carried '
                           "signal's own observation, its leg advanced by the carry's retries and then by the "
                           'entry observation; otherwise the entry observation itself')
    max_fill_lag_ms: int = 60_000
    # A leg with no observation beyond the window resolves on time this long after it.
    resolve_grace_ms: int = 30_000
    observations: str = ("the shared feed's exact-pool observations and the exact-pair refresh "
                         '(updatedAt, as PairHistory), each counted once, in stamp order; an observation '
                         'without liquidity.usd counts at a reported liquidity of 0 (LAB_FORWARD_MARK_LIQUIDITY_V2)')
    valuation: str = ('the booked model (Lab spot model + the position CALIB_V1 bps per leg, drain-aware exit) '
                      're-run at the entry and exit fill observations; net50 with the booked stress and '
                      'exit-reason extra')
    applies_to: str = 'every forward position and close (shadow only: booked P&L, balance and exits unchanged)'
    kill_rule: str = 'evaluated on both bases; met on either retires'
    gate: str = 'expectancy, CI and same-period control criteria must pass on both bases'


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
COST_MODEL = CostModelParameters(
    execution_model=EXECUTION_MODEL, generic_dex_fee_bps=lab_costs.GENERIC_DEX_FEE_BPS,
    base_slippage_bps=lab_costs.BASE_SLIPPAGE_BPS, latency_buffer_bps=lab_costs.LATENCY_BUFFER_BPS,
    network_fee_sol=lab_costs.NETWORK_FEE_SOL, max_price_impact_pct=lab_costs.MAX_PRICE_IMPACT_PCT)
# The cost model the books were pre-registered and pinned with (the Lab's defaults, no
# NEO_LAB_* override). Literal on purpose: it must not follow the environment.
PREREGISTERED_COST_MODEL = CostModelParameters(
    execution_model=EXECUTION_MODEL, generic_dex_fee_bps=30.0, base_slippage_bps=10.0,
    latency_buffer_bps=10.0, network_fee_sol=0.0001, max_price_impact_pct=20.0)
CLOSE_POLICY = ClosePolicyParameters()
CASH = CashParameters()
CONTROL_CONTINUITY = ControlContinuityParameters()
SIGNAL_CARRY = SignalCarryParameters()
FILL_BASIS = FillBasisParameters()

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
CASH_EXHAUSTED_REASON = 'lab_forward_cash_exhausted'
COST_MODEL_MISMATCH_REASON = 'lab_forward_cost_model_mismatch'
# LAB_FORWARD_SIGNAL_CARRY_V1: every remaining signal waits on the price cross-check (carried).
PRICE_CHECK_PENDING_REASON = 'lab_forward_price_check_pending'


def cost_model_overrides(model: CostModelParameters | None = None) -> list:
    """Names of the hashed cost-model values that differ from PREREGISTERED_COST_MODEL (a NEO_LAB_* override)."""
    current, registered = asdict(COST_MODEL if model is None else model), asdict(PREREGISTERED_COST_MODEL)
    return sorted(name for name, value in registered.items() if current.get(name) != value)


def cost_model_mismatches(**resolved) -> list:
    """Names of the Lab's resolved cost-model values that differ from the hashed COST_MODEL or the pre-registered one.

    The first comparison catches drift between strategy_lab and its frozen copy
    lab_paired_costs (and a non-finite knob); the second an environment override,
    which both modules read alike and which would otherwise only change the config
    hashes silently.
    """
    frozen, registered = asdict(COST_MODEL), asdict(PREREGISTERED_COST_MODEL)
    return sorted(name for name, value in resolved.items()
                  if name not in frozen or frozen[name] != value or registered.get(name) != value)


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
        'signal_carry': asdict(SIGNAL_CARRY),
        'exits': asdict(EXITS[book_id]),
        'close_policy': asdict(CLOSE_POLICY),
        'size': {'notional_usd': NOTIONAL_USD, 'rule': 'FIXED_NOTIONAL_NO_BACKOFF',
                 'start_balance_usd': START_BALANCE_USD, 'max_open_positions': 1,
                 'cash_state': asdict(CASH)},
        # The hypothesis-control protocol: both books of a pair carry it.
        'control_continuity': asdict(CONTROL_CONTINUITY),
        'admission_cost_cap_pct': admission_cost_cap_pct(book_id),
        'costs': {'execution_model': EXECUTION_MODEL, 'model': _hashable(asdict(COST_MODEL)),
                  'calibration': asdict(CALIB)},
        'net50': asdict(NET50),
        'fill_basis': asdict(FILL_BASIS),
        'heat_veto': {'version': heat_veto.VERSION, 'mode': HEAT_MODE},
        'structural_rug_guard': structural_rug_guard.VERSION,
        'pool_loss_memory': pool_loss_memory.VERSION,
        'kill_rule': asdict(KILL_RULE),
        'automatic_promotion': AUTOMATIC_PROMOTION,
    }


def _hashable(values) -> dict:
    """A non-finite env value is hashed by its repr (and then fails the Lab's cost-model check)."""
    return {key: (repr(value) if isinstance(value, float) and not math.isfinite(value) else value)
            for key, value in values.items()}


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def config_hash(book_id) -> str:
    """sha256 of the book's canonical parameter JSON; recorded on every position and close."""
    return hashlib.sha256(canonical_json(book_parameters(book_id)).encode('utf-8')).hexdigest()


CONFIG_HASHES = {book_id: config_hash(book_id) for book_id in BOOK_IDS}


def is_forward_book(book_id) -> bool:
    return book_id in BOOK_IDS


def is_forward_position(position) -> bool:
    """A position of one of these books stamped by any released version (KNOWN_VERSIONS).

    Not only the current VERSION: a later version must keep booking and exiting
    the open positions of an earlier one with their own recorded parameters.
    """
    return (isinstance(position, dict) and position.get('lab_forward_version') in KNOWN_VERSIONS
            and position.get('strategy_id') in BOOK_IDS)


def is_zero_capital_position(position) -> bool:
    """LAB_FORWARD_CONTROL_CONTINUITY_V1: a control position whose close never moves the balance."""
    return is_forward_position(position) and position.get('capital_mode') == ZERO_CAPITAL


def tape_pin_required(position) -> bool:
    """Whether a Lab position needs a tape exit pin: False for these books (they never read flow).

    Reads only fields the compact Lab projection keeps (strategy_id, lab_forward_version).
    """
    return TAPE_PIN_REQUIRED if is_forward_position(position) else True


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


def _pool_key(key):
    """A (mint, pair) key in _identity's form, or None."""
    if not isinstance(key, (tuple, list)) or len(key) != 2:
        return None
    return _identity({'address': key[0], 'pairAddress': key[1]})


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


def position_exits(position):
    """The exits a forward position was opened with: its stored ``exit_parameters``.

    The book's current EXITS only when the stored ones are missing or malformed,
    so a later parameter or version change never moves the exits of a position
    that is already open. None for a position of another book.
    """
    if not isinstance(position, dict) or position.get('strategy_id') not in BOOK_IDS:
        return None
    fallback = EXITS.get(position.get('strategy_id'))
    stored = position.get('exit_parameters')
    if not isinstance(stored, dict):
        return fallback
    stop, take, hold = (_finite(stored.get(name))
                        for name in ('stop_loss_net_pct', 'take_profit_net_pct', 'max_hold_minutes'))
    if stop is None or take is None or hold is None or min(stop, take, hold) <= 0:
        return fallback
    cooldown = _finite(stored.get('pool_cooldown_seconds'))
    label = stored.get('label')
    return ExitParameters(
        label=label if isinstance(label, str) and label else (fallback.label if fallback else 'STORED'),
        stop_loss_net_pct=stop, take_profit_net_pct=take, max_hold_minutes=hold,
        pool_cooldown_seconds=int(cooldown) if cooldown is not None and cooldown >= 0 else 300,
        trigger_basis=str(stored.get('trigger_basis') or ExitParameters.trigger_basis))


def exit_reason(position, model_net_pct, hold_minutes):
    """Research exit order on the uncalibrated model net: stop, take-profit, max hold.

    ``position`` is the open position (its own stored exits apply) or a book id
    (that book's current exits).
    """
    exits = position_exits(position) if isinstance(position, dict) else EXITS[position]
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


# ------------------------------------------------------------------ close policy (drained and vanished pools)

def reported_liquidity_usd(coin):
    """Pool liquidity as the mark reports it: a number (0 = drained) or None when it carries none.

    The DexScreener pair object's own ``liquidity.usd`` wins when it is a number.
    Otherwise the normalized ``liquidityUsd``: main's make_coin (the scan feed) and
    lab_position_marks.parse_pair_response (the exact-pair refresh) both turn an
    omitted ``liquidity`` or ``liquidity.usd`` into 0.0, and that is a drain on both
    paths (LAB_FORWARD_MARK_LIQUIDITY_V2): in the research every scan-feed PumpSwap
    liquidity of 0, omitted fields included, was terminal (F6), and an LP pull
    drops the liquidity at an unchanged price. None only for an object that
    carries neither field (no feed or refresh coin does).
    """
    if not isinstance(coin, dict):
        return None
    raw = coin.get('liquidity')
    if isinstance(raw, dict):
        reported = _finite(raw.get('usd'))
        if reported is not None:
            return reported
    return _finite(coin.get('liquidityUsd'))


def drain_aware_impact_pct(value_usd, liquidity, shared_impact_pct) -> float:
    """Booked exit impact (%): constant-product x / (1 + x) where it exceeds the shared capped impact.

    x = 2 x value / liquidity, as in the shared model; identical to it while
    x <= 0.25 (a sale of at most 12.5% of the reported liquidity). A reported
    liquidity <= 0 leaves nothing to sell into (100%); unknown liquidity keeps
    the shared impact. Research basis: rug/realistic_exit.py and harness F6.
    """
    shared = _finite(shared_impact_pct)
    shared = 0.0 if shared is None else shared
    liquidity = _finite(liquidity)
    if liquidity is None:
        return shared
    if liquidity <= 0:
        return 100.0
    x = 2.0 * max(0.0, _finite(value_usd) or 0.0) / liquidity
    return max(shared, 100.0 * x / (1.0 + x))


def mark_snapshot(coin, observed_at, previous=None) -> dict:
    """What a later VANISHED valuation needs from the last usable mark of the exact pool."""
    previous = previous if isinstance(previous, dict) else {}
    liquidity = reported_liquidity_usd(coin)
    return {'priceUsd': _finite(coin.get('priceUsd')), 'priceNative': _finite(coin.get('priceNative')),
            'marketCap': _finite(coin.get('marketCap')), 'fdv': _finite(coin.get('fdv')),
            'dexId': coin.get('dexId'), 'quoteTokenAddress': feasibility.quote_token_address(coin),
            'liquidityUsd': liquidity if liquidity is not None else previous.get('liquidityUsd'),
            'observed_at': _finite(observed_at)}


def feed_alive(feed, now, params: ClosePolicyParameters = CLOSE_POLICY) -> bool:
    """The shared feed still carries a priced observation at most ``feed_alive_max_age_ms`` old."""
    current = _finite(now)
    if current is None:
        return False
    for coin in feed or ():
        if not isinstance(coin, dict) or not (_finite(coin.get('priceUsd')) or 0) > 0:
            continue
        stamp = _finite(coin.get('updatedAt'))
        if stamp is not None and -5_000 <= current - stamp <= params.feed_alive_max_age_ms:
            return True
    return False


def vanish_due(position, now, unpriced_since, alive, params: ClosePolicyParameters = CLOSE_POLICY) -> bool:
    """A forward position whose exact pool gave no usable mark beyond max hold + grace closes as VANISHED.

    All of: held >= max hold + grace; the last usable mark is >= grace old; this
    Lab process has looked for a mark without success for >= the confirm window
    (so a restart first retries the exact-pair refresh); the shared feed is alive.
    """
    if not is_forward_position(position) or not alive:
        return False
    current, opened = _finite(now), _finite(position.get('opened_at'))
    last = _finite(position.get('mark_received_at'))
    last = opened if last is None else last
    since = _finite(unpriced_since)
    if current is None or opened is None or last is None or since is None:
        return False
    grace_ms = params.vanish_grace_minutes * 60_000
    return (current - opened >= position_exits(position).max_hold_minutes * 60_000 + grace_ms
            and current - last >= grace_ms
            and current - since >= params.vanish_confirm_seconds * 1000)


def vanished_coin(position, params: ClosePolicyParameters = CLOSE_POLICY):
    """The exact pool at its last usable mark minus the research VANISH haircut (None if unknown)."""
    snapshot = position.get('last_mark') if isinstance(position, dict) else None
    if not isinstance(snapshot, dict):
        return None
    price, native = _finite(snapshot.get('priceUsd')), _finite(snapshot.get('priceNative'))
    if price is None or price <= 0 or native is None or native <= 0:
        return None
    keep = 1 - params.vanish_haircut_pct / 100
    coin = {'address': position.get('address'), 'pairAddress': position.get('pairAddress'),
            'dexId': snapshot.get('dexId'), 'quoteTokenAddress': snapshot.get('quoteTokenAddress'),
            'priceUsd': price * keep, 'priceNative': native * keep,
            'marketCap': snapshot.get('marketCap'), 'fdv': snapshot.get('fdv'),
            'updatedAt': snapshot.get('observed_at'), 'mark_received_at': snapshot.get('observed_at'),
            'mark_source': 'LAB_FORWARD_VANISHED_LAST_MARK'}
    if snapshot.get('liquidityUsd') is not None:
        coin['liquidityUsd'] = snapshot['liquidityUsd']
    return coin


def close_kind(reason, exit_liquidity_usd, params: ClosePolicyParameters = CLOSE_POLICY) -> str:
    if reason == params.vanish_reason:
        return 'vanished'
    liquidity = _finite(exit_liquidity_usd)
    if liquidity is not None and liquidity <= 0:
        return 'drained'
    return 'marked'


def unpriced_past_max_hold(position, now) -> bool:
    """An open forward position past its max hold that has no usable mark (not yet closed as VANISHED)."""
    if not is_forward_position(position):
        return False
    current, opened = _finite(now), _finite(position.get('opened_at'))
    if current is None or opened is None:
        return False
    return (position.get('quote_status') in {'stale', 'unavailable'}
            and current - opened >= position_exits(position).max_hold_minutes * 60_000)


# ------------------------------------------------------------------ research-fill shadow (LAB_FORWARD_FILL_BASIS_V2)

FILL_PENDING = 'pending'
FILL_NEXT_REFRESH = 'next_refresh'
FILL_QUIET = 'quiet'
FILL_NO_NEXT = 'no_next_observation'
FILL_VANISHED = 'not_applicable_vanished'
FILL_LEG_STATUSES = (FILL_PENDING, FILL_NEXT_REFRESH, FILL_QUIET, FILL_NO_NEXT, FILL_VANISHED)
# LAB_FORWARD_FILL_BASIS_V2: the evaluation field through which a carried signal hands
# position_record the entry leg decided at its signal observation.
CARRIED_FILL_LEG = 'research_fill_entry'
# research_fill.entry_decision of a position: where its entry leg was decided.
ENTRY_DECISION_SIGNAL = 'carried_signal_observation'
ENTRY_DECISION_ENTRY = 'entry_observation'
# Bumped whenever a close's research-fill shadow is completed in place, so cached
# history digests (statistics, kill rule, gate) are rebuilt.
_SHADOW_REVISION = [0]


def fill_snapshot(coin, observed_at) -> dict:
    """What a research-fill valuation needs from one exact-pool observation (the booked model's inputs).

    ``liquidityUsd`` follows the Lab's pair_liquidity_usd (the feed's liquidityUsd,
    else the pair object's liquidity.usd) and ``liquidity.usd`` keeps the reported
    value the drain-aware exit reads, so a snapshot values exactly like its coin.
    """
    coin = coin if isinstance(coin, dict) else {}
    raw = coin.get('liquidity') if isinstance(coin.get('liquidity'), dict) else {}
    snapshot = {'priceUsd': _finite(coin.get('priceUsd')), 'priceNative': _finite(coin.get('priceNative')),
                'marketCap': _finite(coin.get('marketCap')), 'fdv': _finite(coin.get('fdv')),
                'dexId': coin.get('dexId'), 'quoteTokenAddress': feasibility.quote_token_address(coin),
                'liquidityUsd': _finite(coin.get('liquidityUsd') or raw.get('usd')),
                'observed_at': None if _finite(observed_at) is None else int(_finite(observed_at))}
    reported = reported_liquidity_usd(coin)
    if reported is not None:
        snapshot['liquidity'] = {'usd': reported}
    return snapshot


def new_fill_leg(coin, observed_at, *, vanished=False) -> dict:
    """A research-fill leg decided at one observation: pending until the next refresh (or 60 s) is known."""
    stamp = observation_ms(coin, observed_at) if isinstance(coin, dict) else None
    price = _finite(coin.get('priceUsd')) if isinstance(coin, dict) else None
    leg = {'status': FILL_PENDING, 'decision_at': None if stamp is None else int(stamp), 'decision_price': price,
           'decision': fill_snapshot(coin, stamp), 'first_later_at': None, 'first_later': None,
           'fill': None, 'fill_at': None, 'fill_price': None, 'fill_lag_ms': None, 'later_observations': 0,
           'last_observed_at': None, 'resolved_at': None}
    if vanished:
        # The harness values a vanished pool at its last price minus the haircut: no fill.
        _resolve_leg(leg, FILL_VANISHED, None, stamp)
    elif stamp is None or price is None or price <= 0:
        _resolve_leg(leg, FILL_NO_NEXT, None, stamp)
    return leg


def _resolve_leg(leg, status, snapshot, now):
    """Close a leg; ``snapshot`` None means it is valued at its decision observation."""
    leg['status'] = status
    leg['resolved_at'] = None if _finite(now) is None else int(_finite(now))
    leg['fill'] = snapshot
    source = snapshot if isinstance(snapshot, dict) else (leg.get('decision') or {})
    leg['fill_at'], leg['fill_price'] = source.get('observed_at'), source.get('priceUsd')
    start, end = _finite(leg.get('decision_at')), _finite(leg.get('fill_at'))
    leg['fill_lag_ms'] = None if start is None or end is None else int(end - start)
    return True


def _resolve_at_window_end(leg, now):
    first = leg.get('first_later')
    if isinstance(first, dict):
        return _resolve_leg(leg, FILL_QUIET, first, now)
    return _resolve_leg(leg, FILL_NO_NEXT, None, now)


def advance_fill_leg(leg, coin, now, *, key=None, params: FillBasisParameters = FILL_BASIS) -> bool:
    """Advance one pending leg with an observation of its exact pool (``coin`` None: time only).

    Observations at or before the decision, of another pool or not newer than the
    last one used are ignored. An observation without liquidity.usd counts at a
    reported liquidity of 0 (LAB_FORWARD_MARK_LIQUIDITY_V2). Returns True when the
    leg resolved now.
    """
    if not isinstance(leg, dict) or leg.get('status') != FILL_PENDING:
        return False
    start, price0 = _finite(leg.get('decision_at')), _finite(leg.get('decision_price'))
    current = _finite(now)
    if start is None or price0 is None:
        return _resolve_leg(leg, FILL_NO_NEXT, None, now)
    if isinstance(coin, dict) and (key is None or _identity(coin) == key):
        stamp, price = observation_ms(coin, now), _finite(coin.get('priceUsd'))
        last = _finite(leg.get('last_observed_at'))
        if (stamp is not None and price is not None and price > 0 and stamp > start
                and (last is None or stamp > last)):
            leg['last_observed_at'] = int(stamp)
            if stamp - start > params.max_fill_lag_ms:
                return _resolve_at_window_end(leg, now)
            leg['later_observations'] = int(leg.get('later_observations') or 0) + 1
            snapshot = fill_snapshot(coin, stamp)
            if leg.get('first_later') is None:
                leg['first_later_at'], leg['first_later'] = int(stamp), snapshot
            if price != price0:
                return _resolve_leg(leg, FILL_NEXT_REFRESH, snapshot, now)
    if current is not None and current - start > params.max_fill_lag_ms + params.resolve_grace_ms:
        return _resolve_at_window_end(leg, now)
    return False


def _snapshot_coin(row, snapshot):
    if not isinstance(snapshot, dict):
        return None
    coin = {name: value for name, value in snapshot.items() if name != 'observed_at' and value is not None}
    coin.update(address=row.get('address'), pairAddress=row.get('pairAddress'),
                updatedAt=snapshot.get('observed_at'))
    return coin


def fill_leg_coin(row, leg):
    """The exact pool as a coin at the leg's research fill (its decision observation when it has none)."""
    if not isinstance(leg, dict) or leg.get('status') == FILL_PENDING:
        return None
    return _snapshot_coin(row, leg.get('fill') if isinstance(leg.get('fill'), dict) else leg.get('decision'))


def fill_legs_resolved(row) -> bool:
    shadow = row.get('research_fill') if isinstance(row, dict) else None
    if not isinstance(shadow, dict):
        return False
    return all(isinstance(shadow.get(side), dict) and shadow[side].get('status') in FILL_LEG_STATUSES
               and shadow[side].get('status') != FILL_PENDING for side in ('entry', 'exit'))


def research_fill_value(row, entry_fn, exit_fn) -> dict:
    """The booked model of one forward close re-run at its research fills.

    ``entry_fn(coin, notional, calib_bps)`` and ``exit_fn(coin, quantity, calib_bps)``
    are the Lab's calibrated entry and drain-aware exit. A leg whose fill
    observation cannot be valued (no network price) falls back to its decision
    observation, and the result names it in ``valuation_fallbacks``.
    """
    shadow = row['research_fill']
    notional = _finite(row.get('notional_usd')) or 0.0
    calib = position_calib_bps(row)
    out = {'version': FILL_BASIS_VERSION, 'entry_status': shadow['entry']['status'],
           'exit_status': shadow['exit']['status'], 'valuation_fallbacks': []}

    def run(side, fn, *args):
        leg = shadow[side]
        for fallback, coin in ((False, fill_leg_coin(row, leg)), (True, _snapshot_coin(row, leg.get('decision')))):
            if coin is None:
                continue
            try:
                quote = fn(coin, *args)
            except (ValueError, TypeError, ZeroDivisionError):
                continue
            if fallback:
                out['valuation_fallbacks'].append(side)
            return quote, coin
        raise ValueError(f'research_fill_{side}_unpriced')

    try:
        entry, entry_coin = run('entry', entry_fn, notional, calib)
        exit_quote, exit_coin = run('exit', exit_fn, _finite(entry.get('quantity')) or 0.0, calib)
    except ValueError as error:
        out.update({'pnl_usd': None, 'pnl_pct': None, 'net50_usd': None, 'net50_pct': None, 'error': str(error)})
        return out
    pnl = ((_finite(exit_quote.get('net_proceeds_usd')) or 0.0)
           - (_finite(entry.get('capital_committed_usd')) or 0.0))
    stressed = net50(pnl, notional, row.get('exit_reason'))
    booked = _finite(row.get('pnl_usd'))
    out.update({'entry_fill_price': _finite(entry_coin.get('priceUsd')),
                'exit_fill_price': _finite(exit_coin.get('priceUsd')),
                'entry_fill_at': entry_coin.get('updatedAt'), 'exit_fill_at': exit_coin.get('updatedAt'),
                # The valuation's liquidity inputs (the leg snapshots are dropped once valued).
                'entry_fill_liquidity_usd': reported_liquidity_usd(entry_coin),
                'exit_fill_liquidity_usd': reported_liquidity_usd(exit_coin),
                'quantity': _finite(entry.get('quantity')),
                'pnl_usd': round(pnl, 6), 'pnl_pct': round(pnl / notional * 100, 6) if notional else None,
                'net50_usd': stressed['net50_usd'], 'net50_pct': stressed['net50_pct'],
                'minus_booked_usd': None if booked is None else round(pnl - booked, 6)})
    return out


FILL_SNAPSHOT_FIELDS = ('decision', 'first_later', 'fill')


def compact_fill_shadow(shadow) -> int:
    """LAB_FORWARD_FILL_SHADOW_COMPACT_V1: drop a valued shadow's observation snapshots.

    The Lab rewrites its whole ledger every loop, and the three snapshots of each
    leg were most of a forward close's size. Once ``result`` holds the valuation
    (prices, stamps, quantity and liquidity inputs) nothing reads them again; each
    leg keeps its status, decision and fill prices and stamps, lag and
    observation counts. Returns the number of snapshots dropped.
    """
    dropped = 0
    for side in ('entry', 'exit'):
        leg = shadow.get(side)
        if not isinstance(leg, dict):
            continue
        for name in FILL_SNAPSHOT_FIELDS:
            if name in leg:
                dropped += leg.pop(name) is not None
    shadow['storage'] = FILL_SHADOW_STORAGE_VERSION
    return dropped


def complete_research_fill(row, entry_fn, exit_fn) -> bool:
    """Value a close's research fills once both legs are known (completed once, in place).

    Only the shadow fields are written ('research_fill.result',
    'net50_research_fill_usd', 'net50_research_fill_pct', 'research_fill_pnl_usd')
    and the shadow's observation snapshots are then dropped
    (LAB_FORWARD_FILL_SHADOW_COMPACT_V1); the booked P&L, net50 and balance of
    the close never change.
    """
    shadow = row.get('research_fill') if isinstance(row, dict) else None
    if not isinstance(shadow, dict) or shadow.get('result') is not None or not fill_legs_resolved(row):
        return False
    result = research_fill_value(row, entry_fn, exit_fn)
    shadow['result'] = result
    row['net50_research_fill_usd'] = result.get('net50_usd')
    row['net50_research_fill_pct'] = result.get('net50_pct')
    row['research_fill_pnl_usd'] = result.get('pnl_usd')
    compact_fill_shadow(shadow)
    _SHADOW_REVISION[0] += 1
    return True


def pending_fill_rows(book, now, *, full_scan=False, horizon_ms=30 * 60_000):
    """Forward closes of the book whose research-fill shadow is not complete (newest first).

    Closes are stored newest first and a shadow completes within about 90 s, so
    only the recent rows are scanned unless ``full_scan`` (once per process, for
    shadows left pending by a restart).
    """
    current = _finite(now)
    out = []
    for row in (book.get('history') or ()) if isinstance(book, dict) else ():
        if not isinstance(row, dict):
            continue
        closed = _finite(row.get('closed_at'))
        if not full_scan and current is not None and closed is not None and closed < current - horizon_ms:
            break
        shadow = row.get('research_fill')
        if isinstance(shadow, dict) and shadow.get('result') is None and is_forward_position(row):
            out.append(row)
    return out


# ------------------------------------------------------------------ cash state

def cash_state(book, params: CashParameters = CASH) -> dict:
    """LAB_FORWARD_CASH_STATE_V1: whether the book can still fund its fixed entry."""
    book = book if isinstance(book, dict) else {}
    balance = _finite(book.get('balance'))
    holding = isinstance(book.get('position'), dict) and bool(book.get('position'))
    exhausted = bool(not holding and balance is not None and balance < params.min_entry_balance_usd)
    # One cached pass over the history (LAB_FORWARD review performance).
    ledger = _history_digest(book, book.get('id'))['cash']
    opened = [value for value in (ledger['opened_max'],) if value is not None]
    zero_opened = [value for value in (ledger['zero_opened_max'],) if value is not None]
    position = book.get('position') if holding else None
    if position and _finite(position.get('opened_at')) is not None:
        opened.append(_finite(position.get('opened_at')))
        if position.get('capital_mode') == ZERO_CAPITAL:
            zero_opened.append(_finite(position.get('opened_at')))
    funded_closed = ledger['funded_closed_max']
    return {'version': params.version, 'exhausted': exhausted,
            'balance_usd': None if balance is None else round(balance, 4),
            'min_entry_balance_usd': params.min_entry_balance_usd, 'notional_usd': params.notional_usd,
            # Balance only moves on a funded close (zero-capital control closes never do),
            # so the latest funded close is when the book ran out.
            'exhausted_at': int(funded_closed) if exhausted and funded_closed is not None else None,
            'last_entry_at': int(max(opened)) if opened else None,
            'closes_in_ledger': ledger['closes_in_ledger'],
            # LAB_FORWARD_CONTROL_CONTINUITY_V1 measurement beyond the funded balance.
            'zero_capital_closes': ledger['zero_capital_closes'],
            'zero_capital_pnl_usd': ledger['zero_capital_pnl_usd'],
            'last_zero_capital_entry_at': int(max(zero_opened)) if zero_opened else None}


def entry_window_end(book):
    """(reason, time) at which the book stopped being able to enter, or (None, None) while it still can.

    'retired' (kill rule, at ``retired_at``) or 'cash_exhausted' (at its last funded
    close, or its last zero-capital entry if later), whichever came first; a time of
    None means it never could in this ledger. A control whose marker allows
    zero-capital entries (LAB_FORWARD_CONTROL_CONTINUITY_V1) can still enter.
    """
    book = book if isinstance(book, dict) else {}
    ends = []
    marker = book.get('strategy_lifecycle') if isinstance(book.get('strategy_lifecycle'), dict) else {}
    if marker.get('status') == 'retired':
        at = _finite(marker.get('retired_at'))
        if at is None:
            at = _finite((marker.get('evidence') or {}).get('last_closed_at'))
        ends.append(('retired', at))
    cash = cash_state(book)
    if cash['exhausted'] and not (marker.get('status') == 'active' and marker.get('capital_mode') == ZERO_CAPITAL):
        at = _finite(cash['exhausted_at'])
        last_zero = _finite(cash['last_zero_capital_entry_at'])
        if last_zero is not None and (at is None or last_zero > at):
            at = last_zero
        ends.append(('cash_exhausted', at))
    if not ends:
        return None, None
    return min(ends, key=lambda item: -math.inf if item[1] is None else item[1])


# ------------------------------------------------------------------ control continuity

def hypothesis_can_enter(books, control_id, registered_ids=None) -> bool:
    """LAB_FORWARD_CONTROL_CONTINUITY_V1: whether the control's hypothesis is still entry-enabled.

    Registered (when ``registered_ids`` is given), not retired (its persisted
    marker) and not out of cash (its marker or its ledger now). False for a
    hypothesis book and when the hypothesis is missing.
    """
    hypothesis_id = HYPOTHESIS_OF.get(control_id)
    hypothesis = books.get(hypothesis_id) if hypothesis_id and isinstance(books, dict) else None
    if not isinstance(hypothesis, dict) or (registered_ids is not None and hypothesis_id not in registered_ids):
        return False
    marker = hypothesis.get('strategy_lifecycle')
    marker = marker if isinstance(marker, dict) else {}
    if marker.get('status') in {'retired', CASH.status}:
        return False
    return not cash_state(hypothesis)['exhausted']


def zero_capital_entry_allowed(book_id, books, registered_ids=None) -> bool:
    """A control below its cash minimum may enter as a zero-capital measurement while its hypothesis can."""
    return book_id in HYPOTHESIS_OF and hypothesis_can_enter(books, book_id, registered_ids)


# ------------------------------------------------------------------ signal carry (pending price cross-check)

CARRY_COUNTERS = ('pending_signals', 'entered', 'lost_price_pending', 'superseded', 'book_stopped')


def _new_carry_counters() -> dict:
    return {**{name: 0 for name in CARRY_COUNTERS}, 'dropped_by_gate': {}}


def signal_carry_counters(book, book_id=None) -> dict:
    """Cumulative LAB_FORWARD_SIGNAL_CARRY_V1 counters of the book's current config hash (a copy).

    pending_signals: episodes in which a matched signal of a pool waited only on the
    price cross-check; each ends as entered, lost_price_pending (the 60 s window
    passed with the check still pending), dropped_by_gate (another gate refused
    the retry), superseded (the book entered another pool) or book_stopped.
    """
    book = book if isinstance(book, dict) else {}
    book_id = book_id or book.get('id')
    store = book.get('lab_forward_signal_carry')
    by_hash = store.get('by_config_hash') if isinstance(store, dict) else None
    stored = by_hash.get(CONFIG_HASHES.get(book_id)) if isinstance(by_hash, dict) else None
    out = _new_carry_counters()
    if isinstance(stored, dict):
        for name in CARRY_COUNTERS:
            value = stored.get(name)
            out[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        dropped = stored.get('dropped_by_gate')
        if isinstance(dropped, dict):
            out['dropped_by_gate'] = {str(key): value for key, value in dropped.items()
                                      if isinstance(value, int) and not isinstance(value, bool) and value >= 0}
    ended = (out['entered'] + out['lost_price_pending'] + out['superseded'] + out['book_stopped']
             + sum(out['dropped_by_gate'].values()))
    out.update({'version': SIGNAL_CARRY_VERSION, 'config_hash': CONFIG_HASHES.get(book_id),
                'ended': ended,
                'lost_price_pending_share': (round(out['lost_price_pending'] / ended, 6) if ended else None)})
    return out


class SignalCarry:
    """LAB_FORWARD_SIGNAL_CARRY_V1 pending signals of this Lab process (in memory, bounded, thread-safe).

    One episode per (book, mint, pool): created when a matched signal's only
    blocker is price_crosscheck_pending, refreshed by a newer matched signal of
    the same pool, and retried on later refreshes until it enters, another gate
    refuses it, the book enters elsewhere or stops, or it is older than
    ``max_age_ms`` after its latest signal observation. Outcomes are counted in
    the book's ledger per config hash (``lab_forward_signal_carry``); pending
    episodes are not persisted (a restart drops them uncounted).

    LAB_FORWARD_FILL_BASIS_V2: an episode also keeps the research-fill entry leg
    decided at its latest signal observation (when ``hold`` is given that
    observation) and advances it with the pool's observation at every retry, so
    a carried entry is shadow-filled where the research harness would have
    filled the signal, not one refresh after its own entry.
    """

    def __init__(self, params: SignalCarryParameters = SIGNAL_CARRY):
        self.params = params
        self._pending = {}
        self._lock = threading.Lock()

    @staticmethod
    def _counters(book) -> dict:
        """The book's mutable counters for its current config hash (created on first use, never reset)."""
        store = book.get('lab_forward_signal_carry')
        if not isinstance(store, dict) or not isinstance(store.get('by_config_hash'), dict):
            store = {'version': SIGNAL_CARRY_VERSION, 'by_config_hash': {}}
            book['lab_forward_signal_carry'] = store
        counters = store['by_config_hash'].get(CONFIG_HASHES[book['id']])
        if not isinstance(counters, dict):
            counters = _new_carry_counters()
            store['by_config_hash'][CONFIG_HASHES[book['id']]] = counters
        for name in CARRY_COUNTERS:
            if not isinstance(counters.get(name), int) or isinstance(counters.get(name), bool):
                counters[name] = 0
        if not isinstance(counters.get('dropped_by_gate'), dict):
            counters['dropped_by_gate'] = {}
        return counters

    def _expired(self, entry, now) -> bool:
        current = _finite(now)
        return current is None or current - entry['observed_at'] > self.params.max_age_ms

    def expire(self, book, now) -> int:
        """End the book's episodes past the window; they were lost to a still-pending price check."""
        book_id = book.get('id')
        with self._lock:
            entries = self._pending.get(book_id) or {}
            stale = [key for key, entry in entries.items() if self._expired(entry, now)]
            for key in stale:
                del entries[key]
        if stale:
            self._counters(book)['lost_price_pending'] += len(stale)
        return len(stale)

    def carried_evaluation(self, book_id, key, now, coin=None):
        """The pool's live carried signal as an evaluation with its 'carry' record, or None.

        ``coin`` is the pool's current observation: it first advances the episode's
        research-fill entry leg (LAB_FORWARD_FILL_BASIS_V2), which the evaluation then
        carries as ``research_fill_entry`` for position_record.
        """
        with self._lock:
            entry = (self._pending.get(book_id) or {}).get(key)
            if entry is None or self._expired(entry, now):
                return None
            leg = entry.get('fill_leg')
            pool = _pool_key(key)
            if isinstance(leg, dict) and isinstance(coin, dict) and pool is not None:
                advance_fill_leg(leg, coin, now, key=pool)
            evaluation = copy.deepcopy(entry['evaluation'])
            carry = {'version': SIGNAL_CARRY_VERSION, 'signal_observed_at': int(entry['observed_at']),
                     'first_signal_observed_at': int(entry['first_observed_at']),
                     'first_pending_at': int(entry['first_pending_at']), 'attempts': entry['attempts'],
                     'signals': entry['signals'], 'max_age_ms': self.params.max_age_ms}
            if isinstance(leg, dict):
                evaluation[CARRIED_FILL_LEG] = copy.deepcopy(leg)
        evaluation['carry'] = carry
        return evaluation

    @staticmethod
    def _signal_fill_leg(evaluation, key, coin, now):
        """The research-fill entry leg decided at this signal's own observation, or None.

        A carried evaluation brings its leg; otherwise ``coin`` must be the signal
        observation itself (same exact pool, same stamp as observed_at).
        """
        leg = evaluation.get(CARRIED_FILL_LEG)
        if isinstance(leg, dict):
            return copy.deepcopy(leg)
        pool = _pool_key(key)
        if not isinstance(coin, dict) or pool is None or _identity(coin) != pool:
            return None
        stamp, signal = _finite(observation_ms(coin, now)), _finite(evaluation.get('observed_at'))
        if stamp is None or signal is None or int(stamp) != int(signal):
            return None
        return new_fill_leg(coin, now)

    def hold(self, book, key, evaluation, now, coin=None) -> bool:
        """A matched signal of this pool waits only on the price cross-check: start or refresh its episode.

        ``coin`` is the observation the signal matched on; with it the episode keeps
        the research-fill entry leg decided there (LAB_FORWARD_FILL_BASIS_V2).
        """
        book_id = book.get('id')
        stamp = _finite((evaluation or {}).get('observed_at'))
        current = _finite(now)
        if book_id not in BOOK_IDS or stamp is None or current is None:
            return False
        created = evicted = 0
        with self._lock:
            entries = self._pending.setdefault(book_id, OrderedDict())
            entry = entries.get(key)
            if entry is None:
                fresh = {name: value for name, value in evaluation.items()
                         if name not in ('carry', CARRIED_FILL_LEG)}
                entry = {'evaluation': copy.deepcopy(fresh), 'observed_at': stamp, 'first_observed_at': stamp,
                         'first_pending_at': current, 'attempts': 0, 'signals': 1,
                         'fill_leg': self._signal_fill_leg(evaluation, key, coin, now)}
                entries[key] = entry
                created = 1
                while len(entries) > self.params.max_pending_per_book:
                    entries.popitem(last=False)
                    evicted += 1
            elif 'carry' not in evaluation and stamp > entry['observed_at']:
                # A newer matched observation of the same pool: the window, and the research
                # fill of the entry it may become, restart from it.
                fresh = {name: value for name, value in evaluation.items() if name != CARRIED_FILL_LEG}
                entry.update(evaluation=copy.deepcopy(fresh), observed_at=stamp, signals=entry['signals'] + 1,
                             fill_leg=self._signal_fill_leg(evaluation, key, coin, now))
            entry['attempts'] += 1
        counters = self._counters(book)
        counters['pending_signals'] += created
        counters['lost_price_pending'] += evicted
        return bool(created)

    def resolve(self, book, key, outcome, reason=None) -> bool:
        """End the pool's episode: 'entered', 'dropped_by_gate' (with the gate), 'superseded' or 'book_stopped'."""
        with self._lock:
            entry = (self._pending.get(book.get('id')) or {}).pop(key, None)
        if entry is None:
            return False
        counters = self._counters(book)
        if outcome == 'dropped_by_gate':
            gate = str(reason or 'other_gate')
            counters['dropped_by_gate'][gate] = counters['dropped_by_gate'].get(gate, 0) + 1
        else:
            counters[outcome] = counters.get(outcome, 0) + 1
        return True

    def clear(self, book, outcome, *, keep=None) -> int:
        """End every episode of the book (except ``keep``) with one outcome."""
        with self._lock:
            keys = [key for key in (self._pending.get(book.get('id')) or {}) if key != keep]
        return sum(self.resolve(book, key, outcome) for key in keys)

    def pending_count(self, book_id) -> int:
        with self._lock:
            return len(self._pending.get(book_id) or {})

    def status(self) -> dict:
        with self._lock:
            pending = {book_id: len(entries) for book_id, entries in self._pending.items() if entries}
        return {'version': SIGNAL_CARRY_VERSION, 'pending': pending, 'persistent': False,
                **asdict(self.params)}


def entry_fill_leg(evaluation, entry_coin, entry_at):
    """LAB_FORWARD_FILL_BASIS_V2: (research-fill entry leg, where it was decided) of a new forward position.

    A carried signal (its 'carry' record and the leg SignalCarry decided at the
    signal observation, ``research_fill_entry``) keeps that leg, advanced by the
    entry observation like any later observation of the pool. Any other entry,
    or a carried one without a consistent leg, is decided at the entry
    observation (which is then the signal observation, or the fallback).
    """
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    carried = evaluation.get(CARRIED_FILL_LEG) if isinstance(evaluation.get('carry'), dict) else None
    signal_at = _finite(evaluation.get('observed_at'))
    if (isinstance(carried, dict) and carried.get('status') in FILL_LEG_STATUSES and signal_at is not None
            and _finite(carried.get('decision_at')) == signal_at):
        leg = copy.deepcopy(carried)
        key = _identity(entry_coin) if isinstance(entry_coin, dict) else None
        if key is not None:
            advance_fill_leg(leg, entry_coin, entry_at, key=key)
        return leg, ENTRY_DECISION_SIGNAL
    if isinstance(entry_coin, dict):
        return new_fill_leg(entry_coin, entry_at), ENTRY_DECISION_ENTRY
    return new_fill_leg(None, None), ENTRY_DECISION_ENTRY


def position_record(book_id, *, evaluation, calib, model_entry, model_mark, defensive_flags,
                    entry_coin=None, entry_at=None, capital_mode=FUNDED) -> dict:
    """Fields every forward-test position carries (and every close inherits).

    ``evaluation`` is the matched signal; a signal carried past a pending price
    cross-check (LAB_FORWARD_SIGNAL_CARRY_V1) also has its 'carry' record and the
    research-fill entry leg decided at its signal observation, and the entry
    observation is the pool's current one (``entry_coin``).
    """
    entry_leg, entry_decision = entry_fill_leg(evaluation, entry_coin, entry_at)
    lab_forward = {'version': VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
                   'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
                   'hypothesis': HYPOTHESIS_OF.get(book_id, book_id),
                   'observed_at': evaluation.get('observed_at'), 'signal': evaluation.get('signal'),
                   'entry_observed_at': (int(observation_ms(entry_coin, entry_at))
                                         if isinstance(entry_coin, dict) and observation_ms(entry_coin, entry_at)
                                         is not None else None),
                   'signal_carry_version': SIGNAL_CARRY_VERSION}
    if isinstance(evaluation.get('carry'), dict):
        lab_forward['carry'] = dict(evaluation['carry'])
    return {
        'lab_forward_version': VERSION, 'lab_config_hash': CONFIG_HASHES[book_id],
        'close_policy_version': CLOSE_POLICY_VERSION,
        # LAB_FORWARD_CONTROL_CONTINUITY_V1: 'funded', or a control's zero-capital measurement.
        'capital_mode': capital_mode,
        'last_mark': mark_snapshot(entry_coin, entry_at) if isinstance(entry_coin, dict) else None,
        'lab_forward': lab_forward,
        'heat_veto_mode': HEAT_MODE, 'heat_log_only_flags': list(defensive_flags or ()),
        'calib_bps_per_leg': round(calib['total_bps'], 6), 'cost_calibration': calib,
        'model_quantity': model_entry['quantity'],
        'model_entry_roundtrip_pnl_pct': round(model_mark, 6),
        'exit_policy_label': EXITS[book_id].label, 'exit_parameters': asdict(EXITS[book_id]),
        # LAB_FORWARD_FILL_BASIS_V2: where the research would have filled this entry's signal
        # (shadow only).
        'research_fill': {'version': FILL_BASIS_VERSION, 'entry_decision': entry_decision,
                          'entry': entry_leg, 'exit': None, 'result': None},
        'promotion_eligible': False,
    }


def close_record(position, trade, *, model_net_proceeds_usd, reason, capped_net_proceeds_usd=None,
                 booked_net_proceeds_usd=None, exit_liquidity_usd=None, exit_coin=None, exit_at=None) -> dict:
    """Fields added to a forward-test close: model P&L, calibration and drain costs, net50 and close kind.

    ``capped_net_proceeds_usd`` is the booked (calibrated) exit with the shared
    capped impact; the difference to the booked exit is the drain valuation of
    LAB_FORWARD_CLOSE_POLICY_V1 (0 unless the sale exceeds 12.5% of liquidity).
    ``exit_coin`` is the mark the close was booked at: the exit leg of the
    research-fill shadow (LAB_FORWARD_FILL_BASIS_V2) is decided there.
    """
    notional = _finite(position.get('notional_usd'))
    # The model leg's cost basis equals the booked one: calibration changes prices, not capital.
    basis = (notional or 0.0) + (_finite(position.get('entry_network_fee_usd')) or 0.0)
    model_proceeds = _finite(model_net_proceeds_usd)
    model_pnl = None if model_proceeds is None else model_proceeds - basis
    booked = _finite(trade.get('pnl_usd'))
    capped, booked_proceeds = _finite(capped_net_proceeds_usd), _finite(booked_net_proceeds_usd)
    drain_cost = None
    if capped is not None and booked_proceeds is not None:
        drain_cost = max(0.0, capped - booked_proceeds)
    calibration = None
    if model_pnl is not None and booked is not None:
        calibration = model_pnl - booked - (drain_cost or 0.0)
    kind = close_kind(reason, exit_liquidity_usd)
    record = {'model_pnl_usd': None if model_pnl is None else round(model_pnl, 6),
              'model_pnl_pct': (None if model_pnl is None or not notional
                                else round(model_pnl / notional * 100, 6)),
              'calibration_cost_usd': None if calibration is None else round(calibration, 6),
              'drain_valuation_cost_usd': None if drain_cost is None else round(drain_cost, 6),
              # The version and hash the position was opened under (not the current module's).
              'lab_forward_version': position.get('lab_forward_version') or VERSION,
              'lab_config_hash': position.get('lab_config_hash'),
              'capital_mode': ZERO_CAPITAL if position.get('capital_mode') == ZERO_CAPITAL else FUNDED,
              'heat_log_only_flags': list(position.get('heat_log_only_flags') or ()),
              'close_policy_version': CLOSE_POLICY_VERSION, 'close_kind': kind,
              'exit_liquidity_usd': _finite(exit_liquidity_usd)}
    if kind == 'vanished':
        record.update({'quote_status': 'vanished', 'vanish_haircut_pct': CLOSE_POLICY.vanish_haircut_pct,
                       'last_mark_at': _finite(position.get('mark_received_at'))})
    record.update(net50(booked, notional, reason))
    shadow = position.get('research_fill')
    if isinstance(shadow, dict) and isinstance(shadow.get('entry'), dict):
        # The close keeps its own copy: the entry leg may still be pending and resolves on the row.
        shadow = copy.deepcopy(shadow)
        shadow['exit'] = new_fill_leg(exit_coin if isinstance(exit_coin, dict) else None, exit_at,
                                      vanished=kind == 'vanished')
        shadow['result'] = None
        record.update({'research_fill': shadow, 'fill_basis_version': FILL_BASIS_VERSION,
                       'net50_research_fill_usd': None, 'net50_research_fill_pct': None,
                       'research_fill_pnl_usd': None})
    return record


# ------------------------------------------------------------------ statistics, kill rule, promotion gate

# The two net50 bases of every forward close: the booked fills and the research
# fills of LAB_FORWARD_FILL_BASIS_V2 (each on the closes that have it).
BASES = ('booked', 'research_fill')
BASIS_FIELDS = {'booked': ('net50_usd', 'net50_pct'),
                'research_fill': ('net50_research_fill_usd', 'net50_research_fill_pct')}

# History digests: one pass over a ledger's history per change, shared by the
# kill rule, the cash state and the gate of every review (the Lab reviews twice
# per loop). A digest is reused while the same list object has the same length,
# the same newest and oldest rows and no research-fill shadow was completed since
# (rows are otherwise append-only). The cache holds the list, so its id cannot be
# reused while cached.
_DIGESTS = OrderedDict()
_DIGEST_LOCK = threading.Lock()
_DIGEST_CACHE_SIZE = 32


def _row_key(row):
    return (id(row), row.get('trade_no'), row.get('closed_at')) if isinstance(row, dict) else (id(row),)


def _build_digest(history, book_id) -> dict:
    """Cash aggregates of the ledger and the frozen-config closes of ``book_id`` (chronological)."""
    funded_closed = opened_max = zero_opened = None
    closes_in_ledger = 0
    zero_pnl = []
    by_trade = {}
    expected = CONFIG_HASHES.get(book_id)
    for row in history:
        if not isinstance(row, dict):
            continue
        closed, opened = _finite(row.get('closed_at')), _finite(row.get('opened_at'))
        zero = row.get('capital_mode') == ZERO_CAPITAL
        if closed is not None:
            closes_in_ledger += 1
            if not zero and (funded_closed is None or closed > funded_closed):
                funded_closed = closed
        if opened is not None:
            opened_max = opened if opened_max is None else max(opened_max, opened)
            if zero:
                zero_opened = opened if zero_opened is None else max(zero_opened, opened)
        if zero:
            zero_pnl.append(_finite(row.get('pnl_usd')) or 0.0)
        trade_no = row.get('trade_no')
        if (expected is None or row.get('strategy_id') != book_id or row.get('lab_forward_version') != VERSION
                or row.get('lab_config_hash') != expected or closed is None
                or _finite(row.get('net50_usd')) is None or _identity(row) is None
                or isinstance(trade_no, bool) or not isinstance(trade_no, int)):
            continue
        by_trade.setdefault(trade_no, row)
    evaluated = sorted(by_trade.values(), key=lambda row: (_finite(row.get('closed_at')), row.get('trade_no')))
    return {'cash': {'funded_closed_max': funded_closed, 'opened_max': opened_max, 'zero_opened_max': zero_opened,
                     'closes_in_ledger': closes_in_ledger, 'zero_capital_closes': len(zero_pnl),
                     'zero_capital_pnl_usd': round(math.fsum(zero_pnl), 4)},
            'evaluated': evaluated, 'closed_at': [_finite(row.get('closed_at')) for row in evaluated],
            'cache': {}}


def _history_digest(book, book_id=None) -> dict:
    history = book.get('history') if isinstance(book, dict) else None
    if not isinstance(history, list):
        return _build_digest(list(history or ()), book_id)
    signature = (book_id, len(history), _row_key(history[0]) if history else None,
                 _row_key(history[-1]) if history else None, _SHADOW_REVISION[0])
    token = (id(history), book_id)
    with _DIGEST_LOCK:
        cached = _DIGESTS.get(token)
        if cached is not None and cached[0] is history and cached[1] == signature:
            _DIGESTS.move_to_end(token)
            return cached[2]
    digest = _build_digest(history, book_id)
    with _DIGEST_LOCK:
        _DIGESTS[token] = (history, signature, digest)
        _DIGESTS.move_to_end(token)
        while len(_DIGESTS) > _DIGEST_CACHE_SIZE:
            _DIGESTS.popitem(last=False)
    return digest


def _count_until(digest, now) -> int:
    """Number of the digest's chronological closes at or before ``now`` (a later close is not evidence yet)."""
    current = _finite(now)
    return len(digest['evaluated']) if current is None else bisect.bisect_right(digest['closed_at'], current)


def _memo(digest, key, build):
    cache = digest['cache']
    if key not in cache:
        cache[key] = build()
    return cache[key]


def _evaluated_closes(book, book_id, now):
    """Unique closes of the book's frozen config with a finite net50 (chronological)."""
    digest = _history_digest(book, book_id)
    return digest['evaluated'][:_count_until(digest, now)]


def _normal_ci(values, groups, params: KillRuleParameters = KILL_RULE, reason=None) -> dict:
    count = len(values)
    out = {'low': None, 'high': None, 'method': params.fallback_method, 'groups': groups, 'method_reason': reason}
    if count < 2:
        return out
    mean = math.fsum(values) / count
    variance = math.fsum((value - mean) ** 2 for value in values) / (count - 1)
    half = 1.959963984540054 * math.sqrt(variance / count)
    out.update(low=mean - half, high=mean + half)
    return out


def bootstrap_ci(rows, *, key='net50_usd', params: KillRuleParameters = KILL_RULE):
    """95% CI of the mean per-trade value: pair (mint, pool) bootstrap with a fixed seed.

    Groups follow the order of their first close, so the result is deterministic.
    Under ``fallback_under_pairs`` distinct pairs: per-trade normal approximation.
    """
    values = [_finite(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {'low': None, 'high': None, 'method': None, 'groups': 0, 'method_reason': None}
    groups = OrderedDict()
    for row in rows:
        value = _finite(row.get(key))
        if value is not None:
            groups.setdefault(_identity(row), []).append(value)
    if len(groups) < params.fallback_under_pairs:
        return _normal_ci(values, len(groups), params, reason='under_minimum_pairs')
    rnd = random.Random(params.bootstrap_seed)
    members = list(groups.values())
    totals = [math.fsum(group) for group in members]
    counts = [len(group) for group in members]
    size = len(members)
    draw, span = rnd.random, range(size)
    total_of, count_of = totals.__getitem__, counts.__getitem__
    means = []
    for _ in range(params.bootstrap_resamples):
        picks = [int(draw() * size) for _ in span]
        means.append(sum(map(total_of, picks)) / sum(map(count_of, picks)))
    means.sort()
    reps = params.bootstrap_resamples
    return {'low': means[int(reps * .025)], 'high': means[min(reps - 1, int(reps * .975))],
            'method': params.ci_method, 'groups': len(groups), 'method_reason': None}


# Bootstraps already computed, by the content of their sample (an unchanged sample is
# never resampled again, whichever review or digest asks for it). Bounded.
_CI_CACHE = OrderedDict()
_CI_LOCK = threading.Lock()
_CI_CACHE_SIZE = 64


def _ci_fingerprint(rows, key):
    def ident(row):
        return (row.get('trade_no'), row.get('address'), row.get('pairAddress'), row.get(key))
    values = [_finite(row.get(key)) for row in rows]
    return (key, len(rows), tuple(ident(row) for row in rows[:2]), tuple(ident(row) for row in rows[-3:]),
            round(math.fsum(value for value in values if value is not None), 9),
            round(math.fsum(index * (value or 0.0) for index, value in enumerate(values)), 6))


def decision_ci(rows, *, key='net50_usd', bootstrap=True, params: KillRuleParameters = KILL_RULE) -> dict:
    """The published CI of mean $/trade: the pair bootstrap where a decision can depend on it.

    That is from ``bootstrap_min_closes`` closes (the kill rule's minimum) and
    when the caller allows it; below, the per-trade normal approximation. A
    bootstrap is cached by the content of its sample.
    """
    rows = list(rows)
    if bootstrap and len(rows) >= params.bootstrap_min_closes:
        fingerprint = (params.bootstrap_seed, params.bootstrap_resamples, _ci_fingerprint(rows, key))
        with _CI_LOCK:
            cached = _CI_CACHE.get(fingerprint)
            if cached is not None:
                _CI_CACHE.move_to_end(fingerprint)
                return dict(cached)
        result = bootstrap_ci(rows, key=key, params=params)
        with _CI_LOCK:
            _CI_CACHE[fingerprint] = dict(result)
            while len(_CI_CACHE) > _CI_CACHE_SIZE:
                _CI_CACHE.popitem(last=False)
        return result
    values = [value for value in (_finite(row.get(key)) for row in rows) if value is not None]
    groups = len({_identity(row) for row in rows if _finite(row.get(key)) is not None})
    if not values:
        return {'low': None, 'high': None, 'method': None, 'groups': 0, 'method_reason': None}
    reason = 'below_bootstrap_min_closes' if len(rows) < params.bootstrap_min_closes else 'below_gate_min_closes'
    return _normal_ci(values, groups, params, reason=reason)


def _round(value, digits=6):
    value = _finite(value)
    return None if value is None else round(value, digits)


def _basis_stats(digest, basis, count) -> dict:
    """Per-basis statistics of the first ``count`` evaluated closes (cached in the digest)."""
    def build():
        usd_field, pct_field = BASIS_FIELDS[basis]
        rows = [row for row in digest['evaluated'][:count] if _finite(row.get(usd_field)) is not None]
        values = [_finite(row.get(usd_field)) for row in rows]
        pct = [value for value in (_finite(row.get(pct_field)) for row in rows) if value is not None]
        pairs = OrderedDict()
        for row, value in zip(rows, values):
            pairs.setdefault(_identity(row), []).append(value)
        number = len(values)
        mean = math.fsum(values) / number if number else None
        ci = (decision_ci(rows, key=usd_field) if number
              else {'low': None, 'high': None, 'method': None, 'groups': 0, 'method_reason': None})
        retire = bool(number >= KILL_RULE.min_closes and mean is not None
                      and mean < KILL_RULE.max_mean_net50_usd_exclusive
                      and ci['high'] is not None and ci['high'] < KILL_RULE.max_ci95_upper_usd_exclusive)
        return {'basis': basis, 'field': usd_field, 'rows': rows, 'values': values, 'count': number,
                'mean': mean, 'total': math.fsum(values) if number else None,
                'pct_mean': math.fsum(pct) / len(pct) if pct else None, 'pairs': pairs, 'ci': ci,
                'wins': sum(value > 0 for value in values), 'kill_rule_met': retire}
    return _memo(digest, ('stats', basis, count), build)


def _research_coverage(digest, count) -> dict:
    """LAB_FORWARD_FILL_BASIS_V2 coverage of the first ``count`` evaluated closes."""
    def build():
        legs = {'entry': {status: 0 for status in FILL_LEG_STATUSES},
                'exit': {status: 0 for status in FILL_LEG_STATUSES}}
        pending = missing = failed = observed = unobserved = 0
        differences = []
        for row in digest['evaluated'][:count]:
            shadow = row.get('research_fill')
            if not isinstance(shadow, dict) or not isinstance(shadow.get('entry'), dict):
                missing += 1
                unobserved += 2
                continue
            for side in ('entry', 'exit'):
                status = (shadow.get(side) or {}).get('status')
                if status in legs[side]:
                    legs[side][status] += 1
            if shadow.get('result') is None:
                pending += 1
                continue
            value = _finite(row.get('net50_research_fill_usd'))
            if value is None:
                failed += 1
                unobserved += 2
                continue
            differences.append(value - _finite(row.get('net50_usd')))
            for side in ('entry', 'exit'):
                status = (shadow.get(side) or {}).get('status')
                if status in {FILL_NEXT_REFRESH, FILL_QUIET}:
                    observed += 1
                elif status == FILL_NO_NEXT:
                    unobserved += 1
        applicable = observed + unobserved
        return {'pending': pending, 'missing': missing, 'valuation_failed': failed, 'legs': legs,
                'unobserved_leg_share': (unobserved / applicable) if applicable else None,
                'mean_minus_booked_net50_usd': (math.fsum(differences) / len(differences)) if differences else None}
    return _memo(digest, ('research_coverage', count), build)


def _shape(digest, count) -> dict:
    """Booked-basis sample shape of the gate (pairs, days, hours, concentration, pricing, rug entries)."""
    def build():
        stats = _basis_stats(digest, 'booked', count)
        rows, values, pairs = stats['rows'], stats['values'], stats['pairs']
        opened = [value for value in (_finite(row.get('opened_at')) for row in rows) if value is not None]
        days = (max(opened) - min(opened)) / 86_400_000 if len(opened) >= 2 else 0.0
        hours = {int(value // 3_600_000) % 24 for value in opened}
        top_share = (max(len(group) for group in pairs.values()) / len(values)) if values else None
        if len(pairs) >= 2:
            best = max(pairs, key=lambda key: math.fsum(pairs[key]))
            rest = [value for key, group in pairs.items() if key != best for value in group]
            without_best = math.fsum(rest) / len(rest)
        else:
            without_best = None
        by_day = {}
        for row, value in zip(rows, values):
            day = int(_finite(row.get('closed_at')) // 86_400_000)
            by_day[day] = by_day.get(day, 0.0) + value
        total = math.fsum(values)
        best_day_share = (max(by_day.values()) / total) if by_day and total > 0 else None
        rug_entries = sum(1 for row in rows
                          if ((row.get('defensive_entry') or {}).get('structural_rug_guard') or {}).get('blocked'))
        kinds = [row.get('close_kind') for row in rows]
        unpriced = sum(1 for row in rows if row.get('close_kind') in {'vanished', 'drained'}
                       or row.get('quote_status') in {'stale', 'unavailable', 'vanished'})
        return {'days': days, 'hours': len(hours), 'top_share': top_share, 'without_best': without_best,
                'best_day_share': best_day_share, 'rug_entries': rug_entries, 'unpriced': unpriced,
                'vanished': kinds.count('vanished'), 'drained': kinds.count('drained'),
                'zero_capital': sum(row.get('capital_mode') == ZERO_CAPITAL for row in rows),
                'booked_total': math.fsum(_finite(row.get('pnl_usd')) or 0.0 for row in rows) if rows else None,
                'max_drawdown_pct': _max_drawdown_pct(rows, START_BALANCE_USD)}
    return _memo(digest, ('shape', count), build)


def evidence(book, book_id, now) -> dict:
    """Kill-rule evidence of one book on its own frozen-config closes, on both net50 bases."""
    digest = _history_digest(book, book_id)
    count = _count_until(digest, now)
    booked = _basis_stats(digest, 'booked', count)
    research = _basis_stats(digest, 'research_fill', count)
    coverage = _research_coverage(digest, count)
    shape = _shape(digest, count)
    rows = booked['rows']
    ci = booked['ci']
    met_bases = [basis for basis, stats in (('booked', booked), ('research_fill', research)) if stats['kill_rule_met']]
    return {'version': KILL_RULE_VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
            'closed_trades': booked['count'], 'pairs': len(booked['pairs']),
            'wins': booked['wins'],
            # LAB_FORWARD_CLOSE_POLICY_V1: these closes are in the sample above, valued conservatively.
            'vanished_closes': shape['vanished'], 'drained_closes': shape['drained'],
            # LAB_FORWARD_CONTROL_CONTINUITY_V1: closes of a control past its funded balance.
            'zero_capital_closes': shape['zero_capital'],
            'open_unpriced_past_max_hold': unpriced_past_max_hold(book.get('position'), now),
            'net50_total_usd': _round(booked['total']),
            'mean_net50_usd': _round(booked['mean']), 'mean_net50_pct': _round(booked['pct_mean']),
            'ci95_mean_net50_usd': [_round(ci['low']), _round(ci['high'])], 'ci_method': ci['method'],
            'ci_method_reason': ci.get('method_reason'), 'ci_groups': ci['groups'],
            'booked_total_usd': _round(shape['booked_total']),
            'first_closed_at': int(_finite(rows[0]['closed_at'])) if rows else None,
            'last_closed_at': int(_finite(rows[-1]['closed_at'])) if rows else None,
            'min_closes': KILL_RULE.min_closes,
            # LAB_FORWARD_FILL_BASIS_V2: met on either basis retires (each on its own closes).
            'kill_rule_met': bool(met_bases), 'kill_rule_met_bases': met_bases,
            'kill_rule_met_booked': booked['kill_rule_met'],
            'kill_rule_met_research_fill': research['kill_rule_met'],
            'research_fill': {
                'version': FILL_BASIS_VERSION, 'closed_trades': research['count'],
                'pending': coverage['pending'], 'missing': coverage['missing'],
                'valuation_failed': coverage['valuation_failed'],
                'mean_net50_usd': _round(research['mean']), 'mean_net50_pct': _round(research['pct_mean']),
                'net50_total_usd': _round(research['total']),
                'ci95_mean_net50_usd': [_round(research['ci']['low']), _round(research['ci']['high'])],
                'ci_method': research['ci']['method'], 'ci_method_reason': research['ci'].get('method_reason'),
                'kill_rule_met': research['kill_rule_met'], 'legs': copy.deepcopy(coverage['legs']),
                'unobserved_leg_share': _round(coverage['unobserved_leg_share']),
                'mean_minus_booked_net50_usd': _round(coverage['mean_minus_booked_net50_usd'])},
            'note': ('PAPER forward test; net50 is booked net minus the research stress; net50_research_fill '
                     're-runs the booked model at the research fills (next refresh). Not a profit claim.')}


def _max_drawdown_pct(rows, start):
    """Closed-ledger drawdown of the balance: a zero-capital control close never moves it."""
    balance = peak = start
    worst = 0.0
    for row in rows:
        if row.get('capital_mode') == ZERO_CAPITAL:
            continue
        effect = _finite(row.get('balance_effect_usd'))
        balance += effect if effect is not None else (_finite(row.get('pnl_usd')) or 0.0)
        peak = max(peak, balance)
        if peak > 0:
            worst = max(worst, (peak - balance) / peak * 100)
    return worst


def control_window(book_id, rows, control_book) -> dict:
    """The period in which the hypothesis and its random control could both enter (the gate's 'same period').

    start: the hypothesis's first evaluated entry; end: the earlier of its last
    evaluated close and the moment the control stopped being able to enter
    (retired by its kill rule, or out of cash). coverage = the smaller of the
    share of the hypothesis's evaluated trades opened inside the window and the
    share of its time span inside it; None without trades or without a control.
    """
    opened = [value for value in (_finite(row.get('opened_at')) for row in rows) if value is not None]
    closed = [value for value in (_finite(row.get('closed_at')) for row in rows) if value is not None]
    out = {'start': None, 'end': None, 'hypothesis_end': None, 'control_book_id': CONTROL_OF[book_id],
           'control_status': None, 'control_entry_end_reason': None, 'control_entry_end_at': None,
           'trade_share': None, 'time_share': None, 'coverage': None}
    if not isinstance(control_book, dict):
        out['control_entry_end_reason'] = 'control_book_missing'
        return out
    reason, at = entry_window_end(control_book)
    marker = control_book.get('strategy_lifecycle') if isinstance(control_book.get('strategy_lifecycle'), dict) else {}
    out.update({'control_status': marker.get('status'), 'control_entry_end_reason': reason,
                'control_entry_end_at': None if at is None else int(at)})
    if not opened or not closed:
        return out
    start, hypothesis_end = min(opened), max(closed)
    end = hypothesis_end if reason is None else (None if at is None else min(hypothesis_end, at))
    if end is not None and end < start:
        end = None          # the control could no longer enter when the hypothesis began
    inside = sum(1 for value in opened if end is not None and start <= value <= end)
    span = hypothesis_end - start
    trade_share = inside / len(opened)
    time_share = 0.0 if end is None else (1.0 if span <= 0 else max(0.0, end - start) / span)
    out.update({'start': int(start), 'end': None if end is None else int(end), 'hypothesis_end': int(hypothesis_end),
                'trade_share': round(trade_share, 6), 'time_share': round(time_share, 6),
                'coverage': min(trade_share, time_share)})
    return out


def _window_rows(digest, basis, count, start, end):
    """The basis rows of the first ``count`` closes opened inside [start, end] and their mean (cached)."""
    def build():
        if start is None or end is None:
            return {'rows': [], 'mean': None}
        rows = [row for row in _basis_stats(digest, basis, count)['rows']
                if _finite(row.get('opened_at')) is not None and start <= _finite(row.get('opened_at')) <= end]
        field = BASIS_FIELDS[basis][0]
        values = [_finite(row.get(field)) for row in rows]
        return {'rows': rows, 'mean': math.fsum(values) / len(values) if values else None}
    return _memo(digest, ('window', basis, count, start, end), build)


def promotion_gate(book, book_id, control_book, now) -> dict:
    """The research PROMOTION GATE on the hypothesis book's closes (owner review only, never automatic).

    Expectancy, CI and the same-period control comparison are evaluated on both
    net50 bases (booked fills and the research fills of LAB_FORWARD_FILL_BASIS_V2)
    and must pass on both. ``gate_met`` needs every criterion evaluated: the
    3-slot $1000 drawdown needs an offline replay, so the Lab reports at most
    'evaluable_criteria_pass_replay_pending'.
    """
    digest = _history_digest(book, book_id)
    count = _count_until(digest, now)
    booked = _basis_stats(digest, 'booked', count)
    research = _basis_stats(digest, 'research_fill', count)
    coverage_rf = _research_coverage(digest, count)
    shape = _shape(digest, count)
    rows = booked['rows']
    number = booked['count']
    criteria = {}

    def put(name, value, threshold, passed):
        criteria[name] = {'value': value, 'threshold': threshold, 'pass': passed}

    put('closed_trades', number, f'>= {GATE.min_closes}', number >= GATE.min_closes)
    put('pairs', len(booked['pairs']), f'>= {GATE.min_pairs}', len(booked['pairs']) >= GATE.min_pairs)
    put('days', _round(shape['days'], 3), f'>= {GATE.min_days}', shape['days'] >= GATE.min_days)
    put('utc_hours_covered', shape['hours'], f'= {GATE.utc_hours_required}', shape['hours'] >= GATE.utc_hours_required)
    for basis, stats, suffix in (('booked', booked, ''), ('research_fill', research, '_research_fill')):
        mean = stats['mean']
        put(f'mean_net50{suffix}_usd', _round(mean), '> 0', bool(mean is not None and mean > 0))
        low = stats['ci']['low']
        put(f'ci95_low_net50{suffix}_usd', _round(low), '> 0', bool(low is not None and low > 0))
    # Same period = from the hypothesis's first entry to the earlier of its last close and
    # the moment the control could no longer enter (retired or out of cash). Both books
    # are compared on trades opened inside that window only, on each basis.
    control_id = CONTROL_OF[book_id]
    window = control_window(book_id, rows, control_book)
    coverage = window['coverage']
    put('control_coverage', _round(coverage),
        f'>= {GATE.min_control_coverage} (control entry-enabled and funded)',
        bool(coverage is not None and coverage >= GATE.min_control_coverage))
    control_digest = _history_digest(control_book, control_id) if isinstance(control_book, dict) else None
    control_count = _count_until(control_digest, now) if control_digest is not None else 0
    same = {}
    for basis, suffix in (('booked', ''), ('research_fill', '_research_fill')):
        mine = _window_rows(digest, basis, count, window['start'], window['end'])
        theirs = (_window_rows(control_digest, basis, control_count, window['start'], window['end'])
                  if control_digest is not None else {'rows': [], 'mean': None})
        # The control's CI decides this criterion only once the sample criterion can pass:
        # below the gate's minimum closes it is the cheap normal approximation.
        control_ci = (decision_ci(theirs['rows'], key=BASIS_FIELDS[basis][0],
                                  bootstrap=number >= GATE.min_closes)
                      if theirs['rows'] else {'low': None, 'high': None, 'method': None})
        half_width = (None if control_ci['low'] is None or control_ci['high'] is None
                      else (control_ci['high'] - control_ci['low']) / 2)
        margin = None if mine['mean'] is None or theirs['mean'] is None else mine['mean'] - theirs['mean']
        put(f'beats_same_period_control{suffix}_usd', _round(margin),
            f'> control CI half-width ({_round(half_width)})',
            bool(margin is not None and half_width is not None and margin > half_width))
        same[basis] = {'book_closed_trades': len(mine['rows']), 'book_mean_net50_usd': _round(mine['mean']),
                       'control_closed_trades': len(theirs['rows']), 'control_mean_net50_usd': _round(theirs['mean']),
                       'control_ci_half_width_usd': _round(half_width), 'control_ci_method': control_ci.get('method'),
                       'control_ci_method_reason': control_ci.get('method_reason')}
    put('top_pair_share', _round(shape['top_share']), f'<= {GATE.max_top_pair_share}',
        bool(shape['top_share'] is not None and shape['top_share'] <= GATE.max_top_pair_share))
    put('mean_without_best_pair_usd', _round(shape['without_best']), '> 0',
        bool(shape['without_best'] is not None and shape['without_best'] > 0))
    put('best_day_share_of_pnl', _round(shape['best_day_share']), f'<= {GATE.max_best_day_share}',
        bool(shape['best_day_share'] is not None and shape['best_day_share'] <= GATE.max_best_day_share))
    put('max_drawdown_pct_3slot_1000', None,
        f'<= {_whole(GATE.max_drawdown_pct_3slot_1000)} (research: 3-slot $1000 replay; not evaluated in the Lab)',
        None)
    put('entries_on_rug_flagged_pools', shape['rug_entries'], '= 0', shape['rug_entries'] == 0)
    # Vanished (no mark beyond max hold + grace) and drained (liquidity 0 at exit) closes,
    # closes on a stale or unavailable quote, and an open position past its max hold
    # that still has no mark (it counts as a pending vanished close).
    pending = int(unpriced_past_max_hold(book.get('position'), now))
    share = (shape['unpriced'] + pending) / (number + pending) if number + pending else None
    put('vanished_or_unpriced_share', _round(share), f'< {GATE.max_vanished_or_unpriced_share}',
        bool(share is not None and share < GATE.max_vanished_or_unpriced_share))
    # LAB_FORWARD_FILL_BASIS_V2: research-fill legs valued at the decision print (no later
    # observation in 60 s) or closes without a valued shadow; pending shadows are excluded.
    unobserved = coverage_rf['unobserved_leg_share']
    put('research_fill_unobserved_leg_share', _round(unobserved),
        f'< {GATE.max_research_fill_unobserved_leg_share}',
        bool(unobserved is not None and unobserved < GATE.max_research_fill_unobserved_leg_share))
    evaluable = [item['pass'] for item in criteria.values() if item['pass'] is not None]
    not_evaluated = [name for name, item in criteria.items() if item['pass'] is None]
    all_evaluable_pass = bool(evaluable) and all(evaluable)
    gate_met = all_evaluable_pass and not not_evaluated
    return {'version': PROMOTION_GATE_VERSION, 'book_id': book_id, 'control_book_id': control_id,
            'fill_basis_version': FILL_BASIS_VERSION,
            'control_closed_trades_same_period': same['booked']['control_closed_trades'],
            'control_mean_net50_usd': same['booked']['control_mean_net50_usd'],
            'book_closed_trades_same_period': same['booked']['book_closed_trades'],
            'book_mean_net50_usd_same_period': same['booked']['book_mean_net50_usd'],
            'same_period': same,
            'control_window': {key: value for key, value in window.items() if key != 'coverage'},
            'book_cash_state': cash_state(book),
            'control_cash_state': cash_state(control_book) if isinstance(control_book, dict) else None,
            # LAB_FORWARD_SIGNAL_CARRY_V1: signal episodes each book lost to a still-pending price
            # cross-check; a large difference between the two would bias the comparison.
            'signal_carry': {'book': signal_carry_counters(book, book_id),
                             'control': (signal_carry_counters(control_book, control_id)
                                         if isinstance(control_book, dict) else None)},
            'research_fill': {'closed_trades': research['count'], 'pending': coverage_rf['pending'],
                              'missing': coverage_rf['missing'], 'valuation_failed': coverage_rf['valuation_failed'],
                              'legs': copy.deepcopy(coverage_rf['legs']),
                              'mean_minus_booked_net50_usd': _round(coverage_rf['mean_minus_booked_net50_usd'])},
            'book_max_drawdown_pct_booked': _round(shape['max_drawdown_pct'], 4),
            'criteria': criteria,
            'all_evaluable_pass': all_evaluable_pass,
            'not_evaluated': not_evaluated,
            # Only a gate whose every criterion was evaluated can be met; the Lab cannot evaluate
            # the 3-slot drawdown, so passing here means 'replay pending', never 'met'.
            'gate_met': gate_met,
            'gate_status': ('met' if gate_met else 'evaluable_criteria_pass_replay_pending'
                            if all_evaluable_pass else 'not_met'),
            'automatic_promotion': AUTOMATIC_PROMOTION,
            'note': ('Owner review only. Passing never promotes a book; the 3-slot drawdown needs an offline '
                     'replay. Booked fills are the decision print and the triggering mark, not executable '
                     'quotes; the research-fill basis re-prices them at the next DexScreener refresh.')}


def apply_kill_rules(books, review, *, registered_ids, now) -> dict:
    """LAB_FORWARD_KILL_RULE_V1 for the four books; merged into the shared lifecycle review.

    A retirement only stops new entries: balance, history and the exits of an
    open position are untouched. It persists like the shared lifecycle marker
    (never re-enabled automatically, even if later closes improve, and across
    config hashes). A book that is not retired but cannot fund its fixed entry
    (LAB_FORWARD_CASH_STATE_V1) gets status 'cash_exhausted' instead of 'active'.

    LAB_FORWARD_CONTROL_CONTINUITY_V1: the hypotheses are reviewed first; while a
    control's hypothesis can still enter, the control's met kill rule is
    published but deferred (reason 'control_kill_rule_deferred') and a control
    out of cash stays active in zero-capital mode, so the gate's same-period
    comparison covers the hypothesis's whole period.
    """
    review = dict(review or {})
    retired = list(review.get('retired_strategy_ids') or [])
    draining = list(review.get('retired_open_position_ids') or [])
    active = list(review.get('active_registered_strategy_ids') or [])
    exhausted = []
    zero_capital = []
    deferred = []
    reviewed = []
    # Hypotheses first: each control reads its hypothesis's marker of this same review.
    for book_id in (*HYPOTHESIS_IDS, *(book for book in BOOK_IDS if book not in HYPOTHESIS_IDS)):
        book = books.get(book_id) if isinstance(books, dict) else None
        if book_id not in registered_ids or not isinstance(book, dict):
            continue
        current = evidence(book, book_id, now)
        cash = cash_state(book)
        carry = signal_carry_counters(book, book_id)
        continuity = book_id in HYPOTHESIS_OF and hypothesis_can_enter(books, book_id, registered_ids)
        previous = book.get('strategy_lifecycle') or {}
        if isinstance(previous, dict) and previous.get('status') == 'retired':
            # Any retirement persists with its original evidence; only a reviewed change re-enables.
            marker = {**previous, 'entry_enabled': False, 'position_management_enabled': True,
                      'current_evidence': current, 'cash': cash, 'signal_carry': carry}
        elif current['kill_rule_met'] and not continuity:
            marker = {'version': KILL_RULE_VERSION, 'status': 'retired', 'entry_enabled': False,
                      'position_management_enabled': True, 'reason': 'pre_registered_kill_rule',
                      'evidence': current, 'cash': cash, 'retired_at': now, 'signal_carry': carry}
        elif cash['exhausted'] and not continuity:
            # LAB_FORWARD_CASH_STATE_V1: the fixed $200 entry can no longer be funded, so the
            # book cannot trade again; it is not 'active' and its kill rule may never evaluate.
            marker = {'version': KILL_RULE_VERSION, 'status': CASH.status, 'entry_enabled': False,
                      'position_management_enabled': True, 'reason': CASH.reason,
                      'kill_rule_evaluable': current['closed_trades'] >= KILL_RULE.min_closes,
                      'evidence': current, 'cash': cash, 'cash_state_version': CASH_STATE_VERSION,
                      'signal_carry': carry}
        else:
            reason = ('control_kill_rule_deferred' if current['kill_rule_met']
                      else 'kill_rule_min_closes_not_reached' if current['closed_trades'] < KILL_RULE.min_closes
                      else 'kill_rule_threshold_not_met')
            marker = {'version': KILL_RULE_VERSION, 'status': 'active', 'entry_enabled': True,
                      'position_management_enabled': True, 'reason': reason,
                      'evidence': current, 'cash': cash, 'signal_carry': carry,
                      'capital_mode': ZERO_CAPITAL if cash['exhausted'] else FUNDED}
            if book_id in HYPOTHESIS_OF:
                marker['control_continuity'] = {
                    'version': CONTROL_CONTINUITY_VERSION, 'hypothesis': HYPOTHESIS_OF[book_id],
                    'hypothesis_can_enter': continuity,
                    'kill_rule_deferred': bool(current['kill_rule_met']),
                    'zero_capital_entries': bool(cash['exhausted'])}
        book['strategy_lifecycle'] = marker
        reviewed.append(book_id)
        if marker['status'] == 'retired':
            retired.append(book_id)
            if book.get('position'):
                draining.append(book_id)
        elif marker['status'] == CASH.status:
            exhausted.append(book_id)
        else:
            active.append(book_id)
            if marker.get('capital_mode') == ZERO_CAPITAL:
                zero_capital.append(book_id)
            if marker.get('reason') == 'control_kill_rule_deferred':
                deferred.append(book_id)
    # Gates last, so each one reads its control's marker of this same review.
    for book_id in reviewed:
        if book_id in CONTROL_OF:
            books[book_id]['strategy_lifecycle']['promotion_gate'] = promotion_gate(
                books[book_id], book_id, books.get(CONTROL_OF[book_id]), now)
    review.update({'retired_strategy_ids': sorted(set(retired)),
                   'retired_open_position_ids': sorted(set(draining)),
                   'active_registered_strategy_ids': sorted(set(active)),
                   'lab_forward_cash_exhausted_ids': sorted(set(exhausted)),
                   'lab_forward_zero_capital_control_ids': sorted(set(zero_capital)),
                   'lab_forward_kill_rule_deferred_ids': sorted(set(deferred)),
                   'lab_forward_kill_rule': {'version': KILL_RULE_VERSION, 'book_ids': list(BOOK_IDS),
                                             'replaces_shared_lifecycle_heuristic': True,
                                             'cash_state_version': CASH_STATE_VERSION,
                                             'control_continuity_version': CONTROL_CONTINUITY_VERSION,
                                             'fill_basis_version': FILL_BASIS_VERSION,
                                             **asdict(KILL_RULE)}})
    return review


# ------------------------------------------------------------------ diagnostics and published config

def new_diagnostics(book_id) -> dict:
    return {'version': VERSION, 'book_id': book_id, 'config_hash': CONFIG_HASHES[book_id],
            'role': 'random_control' if book_id in HYPOTHESIS_OF else 'hypothesis',
            'universe_candidates': 0, 'universe_rejections': {}, 'signal_rejections': {},
            'signals': 0, 'heat_mode': HEAT_MODE, 'admission_cost_cap_pct': admission_cost_cap_pct(book_id),
            'notional_usd': NOTIONAL_USD, 'exits': asdict(EXITS[book_id]),
            # LAB_FORWARD_SIGNAL_CARRY_V1 in this refresh: carried signals retried on the pool's
            # current observation; 'signal_carry' (cumulative, per config hash) is added by the Lab.
            'carried_signals_retried': 0, 'price_crosscheck_pending_signals': 0,
            'fill_basis_version': FILL_BASIS_VERSION,
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
            'close_policy': asdict(CLOSE_POLICY), 'cash_state': asdict(CASH),
            'control_continuity': asdict(CONTROL_CONTINUITY), 'signal_carry': asdict(SIGNAL_CARRY),
            'fill_basis': asdict(FILL_BASIS), 'fill_basis_version': FILL_BASIS_VERSION,
            'known_versions': list(KNOWN_VERSIONS),
            'cost_model': _hashable(asdict(COST_MODEL)), 'tape_pin_required': TAPE_PIN_REQUIRED,
            # A NEO_LAB_* override: the config hashes differ from the pinned ones and entries fail closed.
            'preregistered_cost_model': asdict(PREREGISTERED_COST_MODEL),
            'cost_model_overrides': cost_model_overrides(),
            'mark_liquidity_version': MARK_LIQUIDITY_VERSION,
            'fill_shadow_storage_version': FILL_SHADOW_STORAGE_VERSION,
            'universe_reasons': list(UNIVERSE_REASONS), 'signal_reasons': list(SIGNAL_REASONS),
            'automatic_promotion': AUTOMATIC_PROMOTION, 'profitability_proven': False,
            'evidence_status': 'PRE_REGISTERED_HYPOTHESES_NEGATIVE_ABSOLUTE_RESEARCH_RESULT'}
