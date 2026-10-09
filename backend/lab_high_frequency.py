"""LAB_HIGH_FREQUENCY_V1: three PAPER Lab books that trade about 50 times an hour each.

PAPER only. Nothing here builds, signs or sends a transaction, calls Jupiter or
RugCheck, or claims profitability. Owner request 2026-10-09: "it has to trade
more often like 50 trades per hour for each strategy". A deep study
(research/edge_study_2026_10_08, research/hf_study_2026_10_09) found no
strategy with positive expectancy after costs, so the existing strategies, the
structural rug guard and the cost caps stay exactly as they are; this family is
an additive, isolated measurement that is expected to lose about the round-trip
cost on every trade:

- HF_RND_E95    random-entry control: lab_forward_tests.hashed_coin(pairAddress,
                updatedAt, 0.25, 'hfA') at a refresh event;
- HF_QUIET_E95  hypothesis: volume.m5 / liquidityUsd <= 0.002 and a refresh step
                |price / previous observation price - 1| <= 0.6% (pool selection);
- HF_DIP15_E95  hypothesis: price <= 1.002 x the pool's trailing 15-min low.

Shared rules (frozen dataclasses below, all in every strategy config hash):
HF_UNIVERSE_E95_V1 (PumpSwap SOL, liquidity >= $50k, fee tier <= 95 bps,
modeled $25 round trip <= 2.75%), HF_REFRESH_EVENT_V1 (decide only at a fresh
DexScreener refresh), STRUCTURAL_RUG_GUARD_V1 and HEAT_VETO_STACK_V1 enforced
through one DefensiveEntryLayer.evaluate per pool per refresh, POOL_LOSS_MEMORY_V1
as a shadow only (replaced by HF_POOL_RULE_V1: a 120 s pool cooldown),
HF_BOOK_RULES_V1 (3 slots, $25 fixed, one position per pool, at most 50 orders
per trailing hour, 1 order per refresh), HF_EXIT_TIME_120_V1, HF_FILL_NEXT_REFRESH_V1
(both legs fill at the next DexScreener refresh, research harness F1),
HF_PRINT_GUARD_V1, HF_ACCOUNTING_V1 (CALIB_V1 booked, net0, net50, research-fill
and decision-print shadows, HF_PRICE_AUDIT_V1 log-only), HF_DAILY_LOSS_CAP_V1
with HF_SESSION_ROTATION_V1 and HF_KILL_RULE_V1.

Storage (HF_JOURNAL_V1 / HF_CHECKPOINT_V1): every state change is a journal row
(append-only strategy_lab_hf/journal/<BOOK>/<YYYY-MM-DD>.jsonl, flushed and
fsynced once per touched file per loop) applied to the in-memory state by one
function, so a restart that replays the rows after the last checkpoint
(strategy_lab_hf/state.json) rebuilds exactly the same state. The books live
outside STATE['books'] and strategy_lab.json: the Lab ledger, the lifecycle, the
promotion and migration code never see them.
"""
import bisect
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from types import SimpleNamespace
import uuid

import entry_defense
import heat_veto
import lab_activity as activity
import lab_forward_tests as lab_forward
import paper_market_feasibility as feasibility
import pool_loss_memory
import structural_rug_guard as rug

VERSION = 'LAB_HIGH_FREQUENCY_V1'
ENTRY_POLICY_VERSION = VERSION
UNIVERSE_VERSION = 'HF_UNIVERSE_E95_V1'
REFRESH_EVENT_VERSION = 'HF_REFRESH_EVENT_V1'
SIGNALS_VERSION = 'HF_SIGNALS_V1'
BOOK_RULES_VERSION = 'HF_BOOK_RULES_V1'
RATE_GOVERNOR_VERSION = 'HF_RATE_GOVERNOR_V1'
POOL_RULE_VERSION = 'HF_POOL_RULE_V1'
EXIT_VERSION = 'HF_EXIT_TIME_120_V1'
FILL_VERSION = 'HF_FILL_NEXT_REFRESH_V1'
PRINT_GUARD_VERSION = 'HF_PRINT_GUARD_V1'
ACCOUNTING_VERSION = 'HF_ACCOUNTING_V1'
PRICE_AUDIT_VERSION = 'HF_PRICE_AUDIT_V1'
DAILY_LOSS_CAP_VERSION = 'HF_DAILY_LOSS_CAP_V1'
SESSION_ROTATION_VERSION = 'HF_SESSION_ROTATION_V1'
KILL_RULE_VERSION = 'HF_KILL_RULE_V1'
JOURNAL_VERSION = 'HF_JOURNAL_V1'
CHECKPOINT_VERSION = 'HF_CHECKPOINT_V1'
VERSIONS = {
    'family': VERSION, 'universe': UNIVERSE_VERSION, 'refresh_event': REFRESH_EVENT_VERSION,
    'signals': SIGNALS_VERSION, 'book_rules': BOOK_RULES_VERSION, 'rate_governor': RATE_GOVERNOR_VERSION,
    'pool_rule': POOL_RULE_VERSION, 'exit': EXIT_VERSION, 'fill': FILL_VERSION, 'print_guard': PRINT_GUARD_VERSION,
    'accounting': ACCOUNTING_VERSION, 'price_audit': PRICE_AUDIT_VERSION, 'daily_loss_cap': DAILY_LOSS_CAP_VERSION,
    'session_rotation': SESSION_ROTATION_VERSION, 'kill_rule': KILL_RULE_VERSION, 'journal': JOURNAL_VERSION,
    'checkpoint': CHECKPOINT_VERSION,
}
PORTFOLIO_GROUP = 'HF_EXPERIMENT'
AUTOMATIC_PROMOTION = False
PROFITABILITY_PROVEN = False
EXPECTED_RESULT = 'loss at the cost floor'
RESEARCH_SOURCE = 'research/hf_study_2026_10_09/hf_synthesis/syn_lib.py'
RESEARCH_PREREG = 'research/hf_study_2026_10_09/hf_synthesis/PREREG_holdout.json'
# sha256 of the pre-registration file, and of the simulator and holdout script it pinned.
RESEARCH_PREREG_SHA256 = 'a545c392bfb7896b5e42404d0d278a29ff8f1193fec23d4a1bce75088d92ea85'
RESEARCH_SYN_LIB_SHA256 = '407befddb64e04f3c5727a12d945def498cfd2c3e8d43c76a25b7b5c38b903d5'
RESEARCH_HOLDOUT_SHA256 = '2f1b39cc316d4e328b0f7ce117803180e00dcdbf4168ffa49b4ba24fb883800a'
SOL_QUOTE_MINT = feasibility.SOL_QUOTE_MINT

RND_ID = 'HF_RND_E95'
QUIET_ID = 'HF_QUIET_E95'
DIP15_ID = 'HF_DIP15_E95'
BOOK_IDS = (RND_ID, QUIET_ID, DIP15_ID)
CONTROL_ID = RND_ID
HYPOTHESIS_IDS = (QUIET_ID, DIP15_ID)
CONTROL_OF = {QUIET_ID: RND_ID, DIP15_ID: RND_ID}
BOOK_NAMES = {
    RND_ID: 'HF контрола · случайни входове',
    QUIET_ID: 'HF тихи пулове',
    DIP15_ID: 'HF дъно 15 мин',
}

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# Slot states.
ORDERED = 'ORDERED'
OPEN = 'OPEN'
EXIT_ORDERED = 'EXIT_ORDERED'
CLOSED = 'CLOSED'
CANCELLED = 'CANCELLED'
SLOT_STATES = (ORDERED, OPEN, EXIT_ORDERED, CLOSED, CANCELLED)

# Close kinds (also the exit reason; none carries a net50 stop or trailing extra).
CLOSE_TIME = 'HF_TIME_120'
CLOSE_VANISHED = 'VANISHED'
CLOSE_FEED_GAP = 'FEED_GAP'
CLOSE_KINDS = (CLOSE_TIME, CLOSE_VANISHED, CLOSE_FEED_GAP)
# An exit leg with no observation within the window fills at the next observation (harness i + 1).
FILL_LATE_NEXT = 'late_next_observation'
FILL_FEED_GAP = 'feed_gap'

# Reasons. Universe and toggle reasons are shared by the three books; the others are per book.
UNIVERSE_REASONS = ('hf_dex_not_pumpswap', 'hf_quote_not_sol', 'hf_liquidity_below_minimum',
                    'hf_fee_tier_above_maximum', 'hf_network_price_unknown', 'hf_modeled_roundtrip_above_cap',
                    'hf_toggle_pool')
SIGNAL_REASONS = ('hf_rnd_not_drawn', 'hf_quiet_turnover', 'hf_quiet_step', 'hf_quiet_input_unknown',
                  'hf_dip15_history_warming', 'hf_dip15_not_at_low')
BOOK_REASONS = ('hf_retired', 'hf_cost_model_mismatch', 'hf_degraded', 'hf_daily_loss_cap', 'hf_session_not_open',
                'hf_slots_full', 'hf_insufficient_balance', 'hf_pool_held', 'hf_pool_cooldown', 'hf_rate_governor',
                'hf_defensive_entry', 'hf_no_refresh_event', 'hf_universe_empty', 'hf_not_loaded')
CANCEL_REASONS = ('hf_entry_no_next_observation', 'hf_structural_block_at_fill', 'hf_entry_unpriced', 'restart')
RETIRE_REASONS = ('capital_floor', 'kill_checkpoint', 'operator_flag', 'all_hypotheses_retired')
CANCEL_NO_NEXT = 'hf_entry_no_next_observation'
CANCEL_STRUCTURAL = 'hf_structural_block_at_fill'
CANCEL_UNPRICED = 'hf_entry_unpriced'
CANCEL_RESTART = 'restart'

ENV_ENABLED = 'NEO_LAB_HF_ENABLED'
ENV_START_BALANCE = 'NEO_LAB_HF_START_BALANCE_USD'
ENV_DAILY_CAP = 'NEO_LAB_HF_DAILY_CAP_USD'
ENV_RETIRE = 'NEO_LAB_HF_RETIRE'
ENV_ROOT = 'NEO_STRATEGY_LAB_HF_DIR'
# Process budget (not a strategy parameter): HF work over TIME_BUDGET_MS in TIME_BUDGET_LOOPS
# consecutive Lab loops stops new HF orders (hf_degraded); exits continue.
TIME_BUDGET_MS = 250.0
TIME_BUDGET_LOOPS = 3
# Cost functions strategy_lab injects (avoids a circular import; the shared model stays there).
COST_FUNCTIONS = ('entry_execution', 'exit_execution', 'calibrated_entry_execution', 'calibrated_exit_execution',
                  'pumpswap_fee_bps', 'sol_usd_from_coin', 'pair_liquidity_usd', 'forward_cost_model')


# ------------------------------------------------------------------ frozen parameters

@dataclass(frozen=True)
class UniverseParameters:
    version: str = UNIVERSE_VERSION
    feed: str = "the Lab's main /state feed; every candidate passes lab_activity.usable_feed_coin"
    dex_id: str = 'pumpswap'
    quote_token_address: str = SOL_QUOTE_MINT
    min_liquidity_usd: float = 50_000.0
    max_fee_tier_bps: float = 95.0
    fee_tier_source: str = 'strategy_lab.pumpswap_fee_bps'
    roundtrip_notional_usd: float = 25.0
    max_modeled_roundtrip_pct: float = activity.MAX_ENTRY_COST_PCT
    roundtrip_basis: str = ('strategy_lab.entry_execution + exit_execution at the decision observation, '
                            'uncalibrated; logged on every order, never size-reduced')
    structural_rug_guard: str = ('STRUCTURAL_RUG_GUARD_V1 enforced at the decision (defensive layer) and again '
                                 'on the entry fill observation (hf_structural_block_at_fill); fail closed')


@dataclass(frozen=True)
class RefreshEventParameters:
    version: str = REFRESH_EVENT_VERSION
    basis: str = ("a new (pairAddress, updatedAt) observation of the main feed whose priceUsd differs from the "
                  "same pool's previous observation, which is at most max_previous_age_ms older")
    max_previous_age_ms: int = 60_000
    decided_once: bool = True
    memory: str = "last (updatedAt, priceUsd) per pool from the main feed only; a pool unseen for 61 min is dropped"
    memory_retention_ms: int = 61 * MINUTE_MS
    candidate_order: str = 'updatedAt, pairAddress ascending'
    max_orders_per_refresh_per_book: int = 1


@dataclass(frozen=True)
class SignalParameters:
    """One book's entry signal at a refresh event (fields of other kinds stay None and are not hashed)."""
    kind: str
    version: str = SIGNALS_VERSION
    probability: float | None = None
    salt: str | None = None
    hash: str | None = None
    max_turnover_m5: float | None = None
    turnover: str | None = None
    max_abs_step: float | None = None
    step: str | None = None
    window_ms: int | None = None
    max_above_low: float | None = None
    window: str | None = None


@dataclass(frozen=True)
class BookRuleParameters:
    version: str = BOOK_RULES_VERSION
    slots: int = 3
    notional_usd: float = 25.0
    size_rule: str = 'FIXED_NOTIONAL_NO_BACKOFF'
    network_fee_reserve_usd: float = 0.10
    min_order_balance_usd: float = 25.10
    reservation: str = ('the min_order_balance_usd is reserved at order time (balance - reservations of '
                        'unclosed slots must cover it) and released at the close or cancel')
    one_position_per_pool: bool = True
    pool_held: str = 'from the order until its exit fill (or cancel)'
    governor_version: str = RATE_GOVERNOR_VERSION
    governor_max_orders: int = 50
    governor_window_ms: int = HOUR_MS
    governor_basis: str = 'decision observation time (updatedAt); an order counts even if it is later cancelled'
    cost_model: str = ('the resolved Lab cost model must equal lab_forward_tests.PREREGISTERED_COST_MODEL, '
                       'else every book refuses orders (hf_cost_model_mismatch, fail closed)')


@dataclass(frozen=True)
class PoolRuleParameters:
    version: str = POOL_RULE_VERSION
    cooldown_ms: int = 120_000
    cooldown_after: str = ("the book's exit fill (its observation time) or cancel (Lab clock) on the pool; "
                           'compared with the decision observation time')
    loss_brake: str = 'none'
    replaces: str = 'POOL_LOSS_MEMORY_V1 for these books only (shadow: plm_v1_would_block on every order and close)'
    plm_shadow_window_ms: int = 6 * HOUR_MS
    plm_shadow: str = "pool_loss_memory.index over the book's in-memory closes of the last 6 h"


@dataclass(frozen=True)
class ExitParameters:
    version: str = EXIT_VERSION
    hold_ms: int = 120_000
    trigger: str = ('the first observation of the pool with updatedAt >= decision_at + hold_ms that is also '
                    'later than the entry fill')
    stop_loss: str = 'none'
    take_profit: str = 'none'
    reason: str = CLOSE_TIME


@dataclass(frozen=True)
class FillParameters:
    version: str = FILL_VERSION
    source: str = ('research harness_final F1 (fill_index); both legs lab_forward_tests.new_fill_leg / '
                   'advance_fill_leg with FILL_BASIS')
    fill_basis_version: str = lab_forward.FILL_BASIS_VERSION
    max_fill_lag_ms: int = lab_forward.FILL_BASIS.max_fill_lag_ms
    resolve_grace_ms: int = lab_forward.FILL_BASIS.resolve_grace_ms
    fill: str = ('the first later exact-pool observation whose priceUsd differs from the leg decision print, '
                 'within max_fill_lag_ms (next_refresh); none: the first later observation (quiet, resolved at '
                 'the window end)')
    entry_no_next: str = f'cancel {CANCEL_NO_NEXT} and release the slot'
    exit_no_next: str = f'wait for the next observation and fill there ({FILL_LATE_NEXT})'
    marks: str = 'the shared feed exact pool, else lab_position_marks.POSITION_MARK_FEED.resolve'
    vanish_after_ms: int = 600_000
    vanish_confirm_ms: int = 60_000
    vanish_haircut_pct: float = lab_forward.CLOSE_POLICY.vanish_haircut_pct
    vanish: str = ('no new usable mark for vanish_after_ms while the shared feed is alive (and this process has '
                   'looked for vanish_confirm_ms): VANISHED at the last mark minus the haircut '
                   '(lab_forward_tests.vanished_coin)')
    feed_gap_ms: int = 600_000
    feed_gap: str = ('a held pool that returns after more than feed_gap_ms: FEED_GAP at min(pre-gap price, return '
                     'price); a return at liquidity 0 values at the return')
    drained: str = 'reported liquidity 0 values the sale at 0 (drain-aware booked exit)'
    restart: str = ('ORDERED slots are cancelled (restart); OPEN slots resume and an overdue exit is ordered at '
                    'the first observation after the restart (late_exit_restart); EXIT_ORDERED legs keep resolving')


@dataclass(frozen=True)
class PrintGuardParameters:
    version: str = PRINT_GUARD_VERSION
    toggle_step_pct: float = 15.0
    toggle_return_pct: float = 2.0
    toggle_window_ms: int = 600_000
    toggle_block_ms: int = 6 * HOUR_MS
    toggle: str = ('a step |dp| >= toggle_step_pct between consecutive main-feed observations whose price comes '
                   'back within toggle_return_pct of the pre-step price within toggle_window_ms; known at the '
                   'return observation; the pool is ineligible for every HF book for toggle_block_ms')
    outlier_fill_pct: float = 15.0
    outlier_valuation: str = ('a fill print >= outlier_fill_pct away from its leg decision print is valued at the '
                              'less favourable of the two (buy: higher, sell: lower), flag fill_outlier')
    gain_cap_ratio: float = 1.15
    gain_cap: str = 'exit valuation price <= gain_cap_ratio x entry valuation price, flag gain_capped'
    losses_capped: bool = False


@dataclass(frozen=True)
class AccountingParameters:
    version: str = ACCOUNTING_VERSION
    execution_model: str = lab_forward.EXECUTION_MODEL
    booked: str = ('strategy_lab.calibrated_entry_execution(fill coin, 25, calib) and calibrated_exit_execution('
                   'fill coin, qty, calib, drain_aware=True) at the print-guarded fill prices')
    calibration_basis: str = ('lab_forward_tests.calib_extra_bps_per_leg(fee tier at the entry fill, liquidity at '
                              'the entry fill, 25) total_bps per leg (CALIB_V1)')
    net50: str = "lab_forward_tests.net50(booked pnl, 25, reason) with reason HF_TIME_120, VANISHED or FEED_GAP"
    net0: str = 'uncalibrated entry_execution / exit_execution at the same valuation prices'
    research_fill: str = (f'{lab_forward.FILL_BASIS_VERSION} shape (leg statuses, fill prices, lags, net50) re-valued '
                          'at the leg fill observations; booking is at the research fill, minus_booked_usd is 0')
    decision_print_shadow: str = ('the same trade at the decision print and the exit trigger print, booked model '
                                  'without the print guard (what other Lab books book): pnl_usd, net50_usd')
    price_audit_version: str = PRICE_AUDIT_VERSION
    price_audit: str = ('log-only: pair_price_integrity.cached at both fills (status, deviation_pct, '
                        'reference_age_ms); pair_price_integrity.check (schedules a GeckoTerminal fetch) at '
                        'decisions, at most price_audit_max_checks_per_minute in any rolling 60 s across all HF '
                        'books; never blocks; no Jupiter or RugCheck call')
    price_audit_max_checks_per_minute: int = 2


@dataclass(frozen=True)
class BudgetParameters:
    """Capital and loss budget (budget hash; a change never starts a new evidence sample)."""
    version: str = DAILY_LOSS_CAP_VERSION
    start_balance_usd: float = 1000.0
    daily_cap_usd: float = 100.0
    cap_basis: str = "booked P&L of today's (UTC) closes plus the sum of min(0, open booked marks)"
    cap_action: str = 'no new orders until 00:00 UTC; ORDERED entries and open slots run to exit; a cap row'
    session_rotation_version: str = SESSION_ROTATION_VERSION
    session_step_hours: int = 7
    session_rule: str = ('UTC day d = floor(ms / 86,400,000); new orders only from hour (session_step_hours x d) '
                         'mod 24 (hf_session_not_open before)')
    capital_floor_fraction: float = 0.5
    env: str = f'{ENV_START_BALANCE} (start_balance_usd), {ENV_DAILY_CAP} (daily_cap_usd)'


@dataclass(frozen=True)
class KillRuleParameters:
    version: str = KILL_RULE_VERSION
    action: str = 'retire permanently: no new orders; open slots run to exit; balance and journal untouched'
    capital_floor: str = 'equity (balance + open booked marks) <= budget capital_floor_fraction x start balance'
    first_checkpoint_closes: int = 500
    min_utc_days_with_closes: int = 3
    checkpoint_every_closes: int = 250
    applies_to: str = 'statistical checkpoint: hypotheses only, on the closes of their current strategy config'
    max_mean_net50_usd_exclusive: float = 0.0
    ci_method: str = 'lab_forward_tests.bootstrap_ci (pair groups)'
    bootstrap_resamples: int = lab_forward.KILL_RULE.bootstrap_resamples
    bootstrap_seed: int = lab_forward.KILL_RULE.bootstrap_seed
    max_ci95_high_usd_exclusive: float = 0.0
    gap_rule: str = ('hypothesis mean net50_pct - control same-period mean net50_pct <= the control CI half-width '
                     '(bootstrap of the control same-period net50_pct); all three conditions retire')
    operator_flag_env: str = ENV_RETIRE
    control_continuity: str = ('the control enters while any hypothesis can; it retires when every hypothesis '
                               'is retired or on its own floor (hypotheses then run with control_retired)')
    lifecycle_12_close_heuristic: bool = False
    automatic_promotion: bool = AUTOMATIC_PROMOTION


UNIVERSE = UniverseParameters()
REFRESH_EVENT = RefreshEventParameters()
BOOK_RULES = BookRuleParameters()
POOL_RULE = PoolRuleParameters()
EXIT = ExitParameters()
FILL = FillParameters()
PRINT_GUARD = PrintGuardParameters()
ACCOUNTING = AccountingParameters()
KILL_RULE = KillRuleParameters()
DEFAULT_BUDGET = BudgetParameters()
SIGNALS = {
    RND_ID: SignalParameters(kind='random', probability=0.25, salt='hfA',
                             hash=('lab_forward_tests.hashed_coin: blake2b(digest_size=8) of '
                                   '"salt|pairAddress|int(updatedAt)", big-endian / 2**64 < probability')),
    QUIET_ID: SignalParameters(kind='quiet_pool', max_turnover_m5=0.002,
                               turnover='volume.m5 / liquidityUsd at the decision observation (no volume: no signal)',
                               max_abs_step=0.006,
                               step="|priceUsd / the pool's previous observation priceUsd - 1|"),
    DIP15_ID: SignalParameters(kind='near_15m_low', window_ms=900_000, max_above_low=0.002,
                               window=("priceUsd / the minimum priceUsd of the pool's observations in "
                                       "(updatedAt - window_ms, updatedAt] (current one included) - 1 <= "
                                       "max_above_low (price <= 1.002 x the 15-min low, research form); the pool's "
                                       'HF memory must reach back to updatedAt - window_ms, else '
                                       'hf_dip15_history_warming')),
}
DEFENSIVE_MODES = {
    'layer': entry_defense.VERSION,
    'call': 'DefensiveEntryLayer.evaluate(coin, now, blocked_pools={}, heat_log_only=False), once per pool per refresh',
    'structural_rug_guard': f'{rug.VERSION} enforced (decision and entry fill)',
    'heat_veto': f'{heat_veto.VERSION} enforced (warm-up included)',
    'pool_loss_memory': 'SHADOW_REPLACED_BY_HF_POOL_RULE_V1',
}


def _drop_none(values) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def book_parameters(book_id) -> dict:
    """Canonical frozen strategy parameters of one book (the basis of its config hash)."""
    if book_id not in BOOK_IDS:
        raise KeyError(book_id)
    hypothesis = book_id in HYPOTHESIS_IDS
    return {
        'version': VERSION, 'book_id': book_id, 'portfolio_group': PORTFOLIO_GROUP,
        'role': 'hypothesis' if hypothesis else 'random_control',
        'control': CONTROL_OF.get(book_id),
        'hypotheses': [] if hypothesis else list(HYPOTHESIS_IDS),
        'research': {'source': RESEARCH_SOURCE, 'prereg': RESEARCH_PREREG, 'prereg_sha256': RESEARCH_PREREG_SHA256,
                     'syn_lib_sha256': RESEARCH_SYN_LIB_SHA256},
        'universe': asdict(UNIVERSE),
        'refresh_event': asdict(REFRESH_EVENT),
        'signal': _drop_none(asdict(SIGNALS[book_id])),
        'book_rules': asdict(BOOK_RULES),
        'pool_rule': asdict(POOL_RULE),
        'exit': asdict(EXIT),
        'fill': asdict(FILL),
        'print_guard': asdict(PRINT_GUARD),
        'accounting': {**asdict(ACCOUNTING), 'calibration': asdict(lab_forward.CALIB),
                       'net50_parameters': asdict(lab_forward.NET50), 'close_reasons': list(CLOSE_KINDS)},
        'defensive': dict(DEFENSIVE_MODES),
        'costs': {'execution_model': lab_forward.EXECUTION_MODEL,
                  'model': lab_forward._hashable(asdict(lab_forward.COST_MODEL))},
        'kill_rule': asdict(KILL_RULE),
        'automatic_promotion': AUTOMATIC_PROMOTION,
    }


def config_hash(book_id) -> str:
    """sha256 of the book's canonical parameter JSON (the lab_forward_tests basis)."""
    return hashlib.sha256(lab_forward.canonical_json(book_parameters(book_id)).encode('utf-8')).hexdigest()


CONFIG_HASHES = {book_id: config_hash(book_id) for book_id in BOOK_IDS}


def _env_amount(environ, name, default):
    raw = (environ or {}).get(name)
    if raw is None or str(raw).strip() == '':
        return default, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, name
    if not math.isfinite(value) or value <= 0:
        return default, name
    return value, None


def budget_parameters(environ=None) -> BudgetParameters:
    """The budget knobs from the environment (an invalid value keeps the default and is reported)."""
    environ = os.environ if environ is None else environ
    start, _ = _env_amount(environ, ENV_START_BALANCE, DEFAULT_BUDGET.start_balance_usd)
    cap, _ = _env_amount(environ, ENV_DAILY_CAP, DEFAULT_BUDGET.daily_cap_usd)
    return BudgetParameters(start_balance_usd=start, daily_cap_usd=cap)


def budget_env_errors(environ=None) -> list:
    environ = os.environ if environ is None else environ
    return [name for name in (ENV_START_BALANCE, ENV_DAILY_CAP)
            if _env_amount(environ, name, 1.0)[1] is not None]


def budget_hash(budget: BudgetParameters = DEFAULT_BUDGET) -> str:
    return hashlib.sha256(lab_forward.canonical_json(asdict(budget)).encode('utf-8')).hexdigest()


DEFAULT_BUDGET_HASH = budget_hash(DEFAULT_BUDGET)


def enabled(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_ENABLED, '1')).strip() == '1'


def retire_flag_ids(environ=None) -> list:
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_RETIRE) or '')
    return sorted({part.strip() for part in raw.replace(';', ',').split(',') if part.strip() in BOOK_IDS})


def cost_functions(source):
    """The Lab's cost functions as one object (strategy_lab passes itself)."""
    return SimpleNamespace(**{name: getattr(source, name) for name in COST_FUNCTIONS})


def session_open_hour(day, budget: BudgetParameters = DEFAULT_BUDGET) -> int:
    """HF_SESSION_ROTATION_V1: the UTC hour from which new orders are allowed on UTC day ``day``."""
    return int((int(budget.session_step_hours) * int(day)) % 24)


def utc_day(ms) -> int:
    return int(ms // DAY_MS)


def day_label(ms) -> str:
    return time.strftime('%Y-%m-%d', time.gmtime(int(ms) / 1000))


# ------------------------------------------------------------------ small helpers

def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _round(value, digits=6):
    value = _finite(value)
    return None if value is None else round(value, digits)


def _sig(value, digits=10):
    value = _finite(value)
    return None if value is None else float('%.*g' % (digits, value))


def _identity(coin):
    if not isinstance(coin, dict):
        return None
    mint, pair = coin.get('address'), coin.get('pairAddress')
    if not isinstance(mint, str) or not isinstance(pair, str) or not mint.strip() or not pair.strip():
        return None
    return mint.strip(), pair.strip()


def _pool_text(key) -> str:
    return f'{key[0]}|{key[1]}'


def _volume_m5(coin):
    volume = coin.get('volume') if isinstance(coin.get('volume'), dict) else None
    return None if volume is None else _finite(volume.get('m5'))


def _repriced(coin, price):
    """``coin`` valued at ``price`` (priceNative scaled alike, so the SOL price and the network fee stay)."""
    old = _finite(coin.get('priceUsd'))
    price = _finite(price)
    if price is None or old is None or old <= 0 or price == old:
        return coin
    out = dict(coin)
    native = _finite(coin.get('priceNative'))
    if native is not None and native > 0:
        out['priceNative'] = native * price / old
    out['priceUsd'] = price
    return out


def _snapshot_coin(slot, snapshot, stamp=None):
    """A valuation coin from a fill snapshot (lab_forward_tests.fill_snapshot form)."""
    if not isinstance(snapshot, dict):
        return None
    coin = {name: value for name, value in snapshot.items() if name != 'observed_at' and value is not None}
    coin.update(address=slot['address'], pairAddress=slot['pairAddress'],
                updatedAt=snapshot.get('observed_at') if stamp is None else stamp)
    return coin


def _fee_bucket(fee_bps):
    fee = _finite(fee_bps)
    if fee is None:
        return 'unknown'
    return 'fee<=50' if fee <= 50 else 'fee55-95' if fee <= 95 else 'fee100-125'


def _mean(values):
    values = [value for value in values if value is not None]
    return math.fsum(values) / len(values) if values else None


# ------------------------------------------------------------------ per-pool memory (refresh events, DIP15, toggles)

class PoolMemory:
    """HF's own per-pool observation memory, fed only from the main feed at each Lab refresh.

    Per pool: the last (updatedAt, priceUsd), price runs [first_at, last_at, price] for the
    15-minute low, the first retained observation (DIP15 coverage) and the recent big steps
    of the toggle guard. A pool not observed for 61 min is dropped; nothing is persisted
    except the toggle blocks (checkpoint).
    """

    def __init__(self, *, refresh: RefreshEventParameters = REFRESH_EVENT,
                 dip: SignalParameters = SIGNALS[DIP15_ID], guard: PrintGuardParameters = PRINT_GUARD):
        self.refresh, self.dip, self.guard = refresh, dip, guard
        self._pools = {}
        self.toggles = {}

    def __len__(self):
        return len(self._pools)

    def observe(self, key, stamp, price) -> dict:
        """One observation; returns {'new', 'event', 'previous_at', 'previous_price', 'toggle'}."""
        record = self._pools.get(key)
        if record is not None and stamp <= record['last_at']:
            return {'new': False, 'event': False, 'previous_at': record['last_at'],
                    'previous_price': record['last_price'], 'toggle': False}
        if record is None:
            self._pools[key] = {'since': stamp, 'last_at': stamp, 'last_price': price,
                                'runs': deque([[stamp, stamp, price]]), 'steps': deque()}
            return {'new': True, 'event': False, 'previous_at': None, 'previous_price': None, 'toggle': False}
        previous_at, previous_price = record['last_at'], record['last_price']
        event = price != previous_price and stamp - previous_at <= self.refresh.max_previous_age_ms
        runs = record['runs']
        if runs and runs[-1][2] == price:
            runs[-1][1] = stamp
        else:
            runs.append([stamp, stamp, price])
        horizon = stamp - self.dip.window_ms
        while len(runs) > 1 and runs[0][1] <= horizon:
            runs.popleft()
        toggle = self._toggle(key, record, stamp, price, previous_price)
        record['last_at'], record['last_price'] = stamp, price
        return {'new': True, 'event': event, 'previous_at': previous_at, 'previous_price': previous_price,
                'toggle': toggle}

    def _toggle(self, key, record, stamp, price, previous_price) -> bool:
        guard = self.guard
        steps = record['steps']
        while steps and stamp - steps[0][0] > guard.toggle_window_ms:
            steps.popleft()
        known = False
        for index, (step_at, pre_price) in enumerate(list(steps)):
            if step_at < stamp and pre_price > 0 and abs(price / pre_price - 1) * 100 <= guard.toggle_return_pct:
                del steps[index]
                self.toggles[_pool_text(key)] = {'known_at': int(stamp), 'until': int(stamp + guard.toggle_block_ms)}
                known = True
                break
        if previous_price > 0 and price > 0 and abs(price / previous_price - 1) * 100 >= guard.toggle_step_pct:
            steps.append((stamp, previous_price))
        return known

    def toggled(self, key, stamp) -> bool:
        block = self.toggles.get(_pool_text(key))
        return bool(block and block['known_at'] <= stamp < block['until'])

    def window_low(self, key, stamp):
        """(minimum price over (stamp - window, stamp], covered) of one pool."""
        record = self._pools.get(key)
        if record is None:
            return None, False
        horizon = stamp - self.dip.window_ms
        low = min((run[2] for run in record['runs'] if run[1] > horizon and run[0] <= stamp), default=None)
        return low, record['since'] <= horizon

    def prune(self, now) -> int:
        horizon = now - self.refresh.memory_retention_ms
        stale = [key for key, record in self._pools.items() if record['last_at'] < horizon]
        for key in stale:
            del self._pools[key]
        for name in [name for name, block in self.toggles.items() if block['until'] <= now]:
            del self.toggles[name]
        return len(stale)


class RateBucket:
    """HF_PRICE_AUDIT_V1 bucket: at most ``limit`` uses in any rolling ``window_ms`` (all HF books)."""

    def __init__(self, limit=ACCOUNTING.price_audit_max_checks_per_minute, window_ms=MINUTE_MS):
        self.limit, self.window_ms = int(limit), int(window_ms)
        self._uses = deque()

    def _prune(self, now):
        while self._uses and self._uses[0] <= now - self.window_ms:
            self._uses.popleft()

    def take(self, now) -> bool:
        self._prune(now)
        if len(self._uses) >= self.limit:
            return False
        self._uses.append(now)
        return True

    def used(self, now) -> int:
        self._prune(now)
        return len(self._uses)


# ------------------------------------------------------------------ journal (HF_JOURNAL_V1)

class Journal:
    """Append-only per-book, per-UTC-day JSONL files; one flush + fsync per touched file per loop."""

    def __init__(self, root):
        self.root = Path(root)
        self._pending = OrderedDict()
        self.bytes_written = 0
        self.rows_written = 0
        self.flushes = 0
        self.last_flush_files = 0
        self.last_flush_ms = 0.0
        self.malformed_rows = 0

    def path(self, book, at) -> Path:
        return self.root / book / f'{day_label(at)}.jsonl'

    def append(self, row) -> None:
        text = json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        self._pending.setdefault(self.path(row['book'], row['at']), []).append(text)

    def pending(self) -> int:
        return sum(len(lines) for lines in self._pending.values())

    def flush(self) -> int:
        if not self._pending:
            self.last_flush_files = 0
            return 0
        started = time.perf_counter()
        files = 0
        while self._pending:
            path, lines = next(iter(self._pending.items()))
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ''.join(line + '\n' for line in lines).encode('utf-8')
            with path.open('ab') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._pending.pop(path)
            self.bytes_written += len(data)
            self.rows_written += len(lines)
            files += 1
        self.flushes += 1
        self.last_flush_files = files
        self.last_flush_ms = round((time.perf_counter() - started) * 1000, 3)
        return files

    def files(self, book=None, min_day=None):
        if not self.root.is_dir():
            return []
        books = [book] if book else sorted(path.name for path in self.root.iterdir() if path.is_dir())
        out = []
        for name in books:
            folder = self.root / name
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob('*.jsonl')):
                if min_day is not None and path.stem < min_day:
                    continue
                out.append(path)
        return out

    def repair_torn_tails(self) -> int:
        """A crash can leave a partial last line: end it with a newline so later rows stay intact.

        No byte is removed; the partial line is skipped as malformed on every read.
        """
        repaired = 0
        for path in self.files():
            try:
                size = path.stat().st_size
                if size == 0:
                    continue
                with path.open('rb') as handle:
                    handle.seek(size - 1)
                    last = handle.read(1)
                if last != b'\n':
                    with path.open('ab') as handle:
                        handle.write(b'\n')
                        handle.flush()
                        os.fsync(handle.fileno())
                    repaired += 1
            except OSError:
                continue
        return repaired

    def rows(self, book=None, min_day=None, kinds=None):
        """Every parseable row (file order); malformed lines are counted and skipped."""
        out = []
        for path in self.files(book, min_day):
            try:
                with path.open('r', encoding='utf-8') as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            self.malformed_rows += 1
                            continue
                        if not isinstance(row, dict) or not isinstance(row.get('seq'), int):
                            self.malformed_rows += 1
                            continue
                        if kinds is None or row.get('kind') in kinds:
                            out.append(row)
            except OSError:
                continue
        return out


def _atomic_write_json(path: Path, data) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    text = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    with tmp.open('w', encoding='utf-8') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        size = os.fstat(handle.fileno()).st_size
    for attempt in range(8):
        try:
            tmp.replace(path)
            return size
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.4, .025 * (2 ** attempt)))
    return size


# ------------------------------------------------------------------ aggregates (per book and config hash)

AGG_KEYS = ('booked_usd', 'booked_pct', 'net0_usd', 'net0_pct', 'net50_usd', 'net50_pct')


def new_aggregate() -> dict:
    return {'n': 0, 'orders': 0, 'cancels': 0, 'wins': {'booked': 0, 'net50': 0, 'net0': 0},
            'sum': {key: 0.0 for key in AGG_KEYS}, 'sumsq': {key: 0.0 for key in AGG_KEYS},
            'by_pair': {}, 'by_fee_bucket': {}, 'by_day': {}, 'by_hour_utc': {}, 'close_ts': [],
            'cum_booked_usd': 0.0, 'equity_peak_usd': 0.0, 'max_drawdown_usd': 0.0,
            'first_close_at': None, 'last_close_at': None}


def _day_entry(agg, at):
    return agg['by_day'].setdefault(day_label(at), {'orders': 0, 'closes': 0, 'cancels': {}, 'booked': 0.0,
                                                    'net50': 0.0, 'cap_tripped_at': None})


def aggregate_into(book_aggregates, row) -> None:
    """Fold one journal row into one book's {cfg: aggregate} (orders, cancels, closes and caps)."""
    kind = row.get('kind')
    if kind not in ('order', 'cancel', 'close', 'cap'):
        return
    if kind == 'order' and row.get('side') != 'entry':
        return
    agg = book_aggregates.setdefault(row['cfg'], new_aggregate())
    at = int(row['at'])
    day = _day_entry(agg, at)
    if kind == 'order':
        agg['orders'] += 1
        day['orders'] += 1
        return
    if kind == 'cancel':
        agg['cancels'] += 1
        reason = str(row.get('reason'))
        day['cancels'][reason] = day['cancels'].get(reason, 0) + 1
        return
    if kind == 'cap':
        day['cap_tripped_at'] = at
        return
    values = {'booked_usd': row.get('pnl_usd'), 'booked_pct': row.get('pnl_pct'),
              'net0_usd': row.get('net0_usd'), 'net0_pct': row.get('net0_pct'),
              'net50_usd': row.get('net50_usd'), 'net50_pct': row.get('net50_pct')}
    values = {key: _finite(value) or 0.0 for key, value in values.items()}
    agg['n'] += 1
    for key, value in values.items():
        agg['sum'][key] += value
        agg['sumsq'][key] += value * value
    for basis, key in (('booked', 'booked_usd'), ('net50', 'net50_usd'), ('net0', 'net0_usd')):
        agg['wins'][basis] += int(values[key] > 0)
    pair = str(row.get('pairAddress'))
    by_pair = agg['by_pair'].setdefault(pair, [0, 0.0, 0.0])
    by_pair[0] += 1
    by_pair[1] += values['net50_usd']
    by_pair[2] += values['booked_usd']
    bucket = agg['by_fee_bucket'].setdefault(_fee_bucket(row.get('fee_bps')), [0, 0.0, 0.0, 0.0])
    bucket[0] += 1
    bucket[1] += values['net50_usd']
    bucket[2] += values['booked_usd']
    bucket[3] += values['net50_pct']
    day['closes'] += 1
    day['booked'] += values['booked_usd']
    day['net50'] += values['net50_usd']
    hour = agg['by_hour_utc'].setdefault(str(int((at % DAY_MS) // HOUR_MS)), [0, 0.0, 0.0])
    hour[0] += 1
    hour[1] += values['net50_usd']
    hour[2] += values['booked_usd']
    ts = agg['close_ts']
    ts.append(at)
    while ts and ts[0] <= at - HOUR_MS:
        ts.pop(0)
    agg['cum_booked_usd'] += values['booked_usd']
    agg['equity_peak_usd'] = max(agg['equity_peak_usd'], agg['cum_booked_usd'])
    agg['max_drawdown_usd'] = max(agg['max_drawdown_usd'], agg['equity_peak_usd'] - agg['cum_booked_usd'])
    agg['first_close_at'] = at if agg['first_close_at'] is None else agg['first_close_at']
    agg['last_close_at'] = at


def fold_aggregates(rows) -> dict:
    """Aggregates recomputed from journal rows (deduplicated by seq, in seq order)."""
    aggregates = {}
    seen = set()
    for row in sorted(rows, key=lambda item: item['seq']):
        if row['seq'] in seen:
            continue
        seen.add(row['seq'])
        aggregate_into(aggregates.setdefault(row['book'], {}), row)
    return aggregates


def _rounded_aggregate(agg) -> dict:
    """An aggregate with its float sums rounded (for comparisons across fold orders)."""
    def walk(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value
    return walk(agg)


def _pair_groups(by_pair):
    """[(net50 usd total, closes)] per pair in first-close order."""
    return [(float(value[1]), int(value[0])) for value in by_pair.values() if int(value[0]) > 0]


def pair_bootstrap_gap(groups_a, groups_b, *, notional, reps=1000, seed=20261009):
    """Pair-bootstrap CI95 of mean(a) - mean(b) in % of notional (each book's pairs resampled independently)."""
    if len(groups_a) < 3 or len(groups_b) < 3:
        return None
    rng = random.Random(seed)
    draw = rng.random

    def mean(groups):
        size = len(groups)
        total = count = 0.0
        for _ in range(size):
            value, n = groups[int(draw() * size)]
            total += value
            count += n
        return total / count

    out = sorted(100.0 * (mean(groups_a) - mean(groups_b)) / notional for _ in range(reps))
    return [round(out[int(reps * .025)], 4), round(out[min(reps - 1, int(reps * .975))], 4)]


# ------------------------------------------------------------------ the HF container

def new_book(book_id, budget: BudgetParameters) -> dict:
    return {'id': book_id, 'balance': float(budget.start_balance_usd), 'start_balance': float(budget.start_balance_usd),
            'trade_no': 0, 'slots': {}, 'retired': None, 'cap': None, 'session': None, 'governor': [],
            'recent': [], 'cooldowns': {}, 'kill': {'next_checkpoint': KILL_RULE.first_checkpoint_closes,
                                                    'last': None, 'checkpoints': 0},
            'aggregates': {}, 'last_closes': []}


class HighFrequencyLab:
    """Three HF books with their own capital, journal and checkpoint (strategy_lab_hf/)."""

    def __init__(self, root, *, cost, defense, marks=None, price_audit=None, clock=None, budget=None,
                 environ=None, structural_check=None):
        self.root = Path(root)
        self.cost = cost
        self.defense = defense
        self.marks = marks
        self.price_audit = price_audit
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.environ = os.environ if environ is None else environ
        self.budget = budget if budget is not None else budget_parameters(self.environ)
        self.budget_hash = budget_hash(self.budget)
        self.structural_check = structural_check or self._structural_check
        self.cfg = {book_id: config_hash(book_id) for book_id in BOOK_IDS}
        self.memory = PoolMemory()
        self.audit_bucket = RateBucket()
        self.books = {book_id: new_book(book_id, self.budget) for book_id in BOOK_IDS}
        self.journal = Journal(self.root / 'journal')
        self.seq = 0
        self.loaded = False
        self.started_at = None
        self.degraded = False
        self.slow_loops = 0
        self.loop_ms = []
        self.dirty = False
        self.lock = threading.RLock()
        self.retire_flags = retire_flag_ids(self.environ)
        self.load_report = {}
        self.checkpoint_stats = {'writes': 0, 'bytes': 0, 'last_at': None, 'last_ms': None, 'seq': 0}
        self.last_checkpoint_at = 0
        self.refresh_stats = {'at': None, 'observations': 0, 'events': 0, 'universe': 0, 'pools': 0,
                              'universe_rejections': {}, 'defensive_calls': 0, 'pools_in_memory': 0}
        self.diagnostics = {book_id: {} for book_id in BOOK_IDS}
        self.audit_stats = {'checks': 0, 'skipped_by_bucket': 0, 'cached_fills': 0, 'errors': 0}
        self._gap_cache = {}
        self._mismatch = None

    # ---------------------------------------------------------------- configuration

    def cost_model_mismatches(self) -> list:
        if self._mismatch is None:
            try:
                self._mismatch = lab_forward.cost_model_mismatches(**self.cost.forward_cost_model())
            except Exception:  # an unreadable cost model is a mismatch (fail closed)
                self._mismatch = ['unreadable_cost_model']
        return list(self._mismatch)

    def _structural_check(self, coin, now):
        return rug.check(coin, now, getattr(self.defense, 'registry', None))

    # ---------------------------------------------------------------- rows

    def _emit(self, book_id, kind, fields, at, *, cfg=None) -> dict:
        self.seq += 1
        row = {'v': 1, 'seq': self.seq, 'kind': kind, 'book': book_id, 'cfg': cfg or self.cfg[book_id],
               'budget': self.budget_hash, 'at': int(at), 'entry_policy_version': ENTRY_POLICY_VERSION, **fields}
        self.journal.append(row)
        self._apply(row)
        self.dirty = True
        return row

    def _apply(self, row) -> None:
        """Apply one journal row to the state (live and on replay alike)."""
        self.seq = max(self.seq, int(row['seq']))
        book = self.books.get(row.get('book'))
        if book is None:
            return
        kind = row.get('kind')
        aggregate_into(book['aggregates'], row)
        if kind == 'order' and row.get('side') == 'entry':
            self._apply_order(book, row)
        elif kind == 'order' and row.get('side') == 'exit':
            self._apply_exit_order(book, row)
        elif kind == 'fill':
            self._apply_fill(book, row)
        elif kind == 'cancel':
            self._apply_cancel(book, row)
        elif kind == 'close':
            self._apply_close(book, row)
        elif kind == 'cap':
            book['cap'] = {'day': int(row['day']), 'tripped_at': int(row['at']), 'pnl_usd': row.get('pnl_usd')}
        elif kind == 'session':
            book['session'] = {'day': int(row['day']), 'open_hour': int(row['open_hour']),
                               'opened_at': int(row['at'])}
        elif kind == 'retire':
            if book['retired'] is None:
                book['retired'] = {'reason': row.get('reason'), 'at': int(row['at']),
                                   'evidence': row.get('evidence')}

    def _apply_order(self, book, row) -> None:
        trade_no = int(row['trade_no'])
        book['trade_no'] = max(int(book.get('trade_no') or 0), trade_no)
        decision = _snapshot_coin(row, row.get('decision'), row['decision_at'])
        slot = {'trade_no': trade_no, 'state': ORDERED, 'cfg': row['cfg'], 'address': row['address'],
                'pairAddress': row['pairAddress'], 'symbol': row.get('symbol'),
                'pairCreatedAt': row.get('pairCreatedAt'), 'decision_at': int(row['decision_at']),
                'decision_price': row['decision_price'], 'ordered_at': int(row['at']),
                'reserved_usd': BOOK_RULES.min_order_balance_usd, 'fee_bps': row.get('fee_bps'),
                'rt_model_pct': row.get('rt_model_pct'), 'plm_v1_would_block': bool(row.get('plm_v1_would_block')),
                'audit': row.get('price_audit'),
                'entry_leg': lab_forward.new_fill_leg(decision, row['decision_at']),
                'entry': None, 'exit_leg': None, 'trigger': None, 'last_mark': None, 'last_mark_at': None,
                'mark_pnl_usd': 0.0, 'exit_waiting_next': False, 'late_exit_restart': False}
        book['slots'][str(trade_no)] = slot
        bisect.insort(book['governor'], int(row['decision_at']))

    def _apply_exit_order(self, book, row) -> None:
        slot = book['slots'].get(str(row['trade_no']))
        if slot is None:
            return
        trigger = _snapshot_coin(slot, row.get('trigger'), row['trigger_at'])
        slot['state'] = EXIT_ORDERED
        slot['trigger'] = {'at': int(row['trigger_at']), 'price': row['trigger_price'],
                           'snapshot': row.get('trigger')}
        slot['exit_leg'] = lab_forward.new_fill_leg(trigger, row['trigger_at'])
        slot['late_exit_restart'] = bool(row.get('late_exit_restart'))
        slot['exit_waiting_next'] = False

    def _apply_fill(self, book, row) -> None:
        slot = book['slots'].get(str(row['trade_no']))
        if slot is None:
            return
        entry = dict(row['entry'])
        slot['state'] = OPEN
        slot['entry'] = entry
        slot['entry_leg'] = None
        slot['last_mark'] = entry.get('mark')
        slot['last_mark_at'] = int(entry['fill_at'])
        slot['mark_pnl_usd'] = _finite(entry.get('mark_pnl_usd')) or 0.0

    def _release(self, book, slot, cooldown_from) -> None:
        book['slots'].pop(str(slot['trade_no']), None)
        key = _pool_text((slot['address'], slot['pairAddress']))
        book['cooldowns'][key] = int(cooldown_from + POOL_RULE.cooldown_ms)

    def _apply_cancel(self, book, row) -> None:
        slot = book['slots'].get(str(row['trade_no']))
        if slot is not None:
            self._release(book, slot, int(row['at']))

    def _apply_close(self, book, row) -> None:
        slot = book['slots'].get(str(row['trade_no']))
        book['balance'] = round(float(book['balance']) + (_finite(row.get('pnl_usd')) or 0.0), 8)
        if slot is not None:
            self._release(book, slot, int(row.get('exit_fill_at') or row['at']))
        book['recent'].append([row['address'], row['pairAddress'], int(row['at']), _finite(row.get('pnl_usd'))])
        horizon = int(row['at']) - POOL_RULE.plm_shadow_window_ms
        book['recent'] = [item for item in book['recent'] if item[2] > horizon]
        book['last_closes'].insert(0, compact_close(row))
        del book['last_closes'][20:]

    # ---------------------------------------------------------------- load, restart, reset

    @property
    def state_path(self) -> Path:
        return self.root / 'state.json'

    def _archive_for_reset(self, now) -> str | None:
        """Move the journal and checkpoint into strategy_lab_hf/archive/reset-<ms>-<id>/ (never deleted)."""
        targets = [path for path in (self.root / 'journal', self.state_path) if path.exists()]
        if not targets:
            return None
        destination = self.root / 'archive' / f'reset-{int(now)}-{uuid.uuid4().hex[:8]}'
        destination.mkdir(parents=True, exist_ok=False)
        for path in targets:
            os.replace(path, destination / path.name)
        return str(destination)

    def load(self, reset=False) -> dict:
        """Restore the checkpoint, replay newer journal rows, then apply the restart rules."""
        with self.lock:
            now = int(self.clock())
            self.root.mkdir(parents=True, exist_ok=True)
            report = {'reset': bool(reset), 'archive': None, 'checkpoint': 'absent', 'replayed_rows': 0,
                      'restart_cancels': 0, 'resumed_open': 0, 'resumed_exit_ordered': 0, 'torn_tails': 0}
            if reset:
                report['archive'] = self._archive_for_reset(now)
            min_day = None
            if self.state_path.exists():
                try:
                    data = json.loads(self.state_path.read_text(encoding='utf-8'))
                    if not isinstance(data, dict) or data.get('version') != CHECKPOINT_VERSION:
                        raise ValueError('unknown checkpoint')
                    self._restore(data)
                    report['checkpoint'] = 'loaded'
                    written = _finite(data.get('written_at'))
                    if written is not None:
                        min_day = day_label(written - DAY_MS)
                except (OSError, ValueError, KeyError, TypeError):
                    # The journal is the record: rebuild from every row.
                    self.books = {book_id: new_book(book_id, self.budget) for book_id in BOOK_IDS}
                    self.seq = 0
                    self.memory.toggles = {}
                    report['checkpoint'] = 'unreadable_rebuilt_from_journal'
            report['torn_tails'] = self.journal.repair_torn_tails()
            checkpoint_seq = self.seq
            rows = [row for row in self.journal.rows(min_day=min_day) if row['seq'] > checkpoint_seq]
            seen = set()
            for row in sorted(rows, key=lambda item: item['seq']):
                if row['seq'] in seen:
                    continue
                seen.add(row['seq'])
                self._apply(row)
                report['replayed_rows'] += 1
            self.checkpoint_stats['seq'] = checkpoint_seq
            # Restart rules (HF_FILL_NEXT_REFRESH_V1).
            for book_id in BOOK_IDS:
                book = self.books[book_id]
                for slot in sorted(list(book['slots'].values()), key=lambda item: item['trade_no']):
                    if slot['state'] == ORDERED:
                        self._emit(book_id, 'cancel', self._slot_fields(slot, reason=CANCEL_RESTART), now,
                                   cfg=slot['cfg'])
                        report['restart_cancels'] += 1
                    elif slot['state'] == OPEN:
                        slot['restarted_at'] = now
                        report['resumed_open'] += 1
                    elif slot['state'] == EXIT_ORDERED:
                        report['resumed_exit_ordered'] += 1
            self.journal.flush()
            self.started_at = now
            self.loaded = True
            self.load_report = report
            self.checkpoint_if_due(now, force=True)
            return report

    def _restore(self, data) -> None:
        self.seq = int(data.get('seq') or 0)
        books = data.get('books') or {}
        for book_id in BOOK_IDS:
            stored = books.get(book_id)
            book = new_book(book_id, self.budget)
            if isinstance(stored, dict):
                for name in book:
                    if name in stored:
                        book[name] = stored[name]
            self.books[book_id] = book
        self.memory.toggles = dict(data.get('toggles') or {})

    def checkpoint(self, now) -> dict:
        return {'version': CHECKPOINT_VERSION, 'family': VERSION, 'seq': self.seq, 'written_at': int(now),
                'config_hashes': dict(self.cfg), 'budget_hash': self.budget_hash, 'budget': asdict(self.budget),
                'books': {book_id: self._checkpoint_book(book, now) for book_id, book in self.books.items()},
                'toggles': dict(self.memory.toggles)}

    def _checkpoint_book(self, book, now) -> dict:
        out = {name: value for name, value in book.items() if name != 'slots'}
        out['slots'] = {key: {name: value for name, value in slot.items() if not name.startswith('_')}
                        for key, slot in book['slots'].items()}
        out['governor'] = [stamp for stamp in book['governor'] if stamp > now - BOOK_RULES.governor_window_ms - MINUTE_MS]
        out['cooldowns'] = {key: until for key, until in book['cooldowns'].items() if until > now - MINUTE_MS}
        out['recent'] = [item for item in book['recent'] if item[2] > now - POOL_RULE.plm_shadow_window_ms]
        return out

    def checkpoint_if_due(self, now, force=False) -> bool:
        """HF_CHECKPOINT_V1: journal first, then state.json atomically (every 15 s when changed, and on stop)."""
        with self.lock:
            if not self.loaded:
                return False
            self.journal.flush()
            if not force and (not self.dirty or now - self.last_checkpoint_at < 15_000):
                return False
            started = time.perf_counter()
            for book in self.books.values():
                book['governor'] = [stamp for stamp in book['governor']
                                    if stamp > now - BOOK_RULES.governor_window_ms - MINUTE_MS]
                book['cooldowns'] = {key: until for key, until in book['cooldowns'].items()
                                     if until > now - MINUTE_MS}
            size = _atomic_write_json(self.state_path, self.checkpoint(now))
            self.dirty = False
            self.last_checkpoint_at = now
            self.checkpoint_stats.update(writes=self.checkpoint_stats['writes'] + 1, bytes=size, last_at=int(now),
                                         last_ms=round((time.perf_counter() - started) * 1000, 3), seq=self.seq)
            return True

    # ---------------------------------------------------------------- per-loop work

    def record_loop_time(self, milliseconds) -> bool:
        """Time budget: > 250 ms of HF work in 3 consecutive loops sets hf_degraded (exits continue).

        It clears at the first loop within the budget (no new orders also means less work).
        """
        with self.lock:
            value = _finite(milliseconds) or 0.0
            self.loop_ms.append(round(value, 3))
            del self.loop_ms[:-30]
            self.slow_loops = self.slow_loops + 1 if value > TIME_BUDGET_MS else 0
            self.degraded = self.slow_loops >= TIME_BUDGET_LOOPS
            return self.degraded

    def _observations(self, slot, prices, now):
        key = (slot['address'], slot['pairAddress'])
        coins = []
        coin = prices.get(key) if isinstance(prices, dict) else None
        if isinstance(coin, dict):
            coins.append(coin)
        elif self.marks is not None:
            try:
                mark = self.marks.resolve({'address': key[0], 'pairAddress': key[1]}, prices or {}, now)
            except Exception:
                mark = None
            if isinstance(mark, dict):
                coins.append(mark)
        out = []
        for coin in coins:
            if _identity(coin) != key or not (_finite(coin.get('priceUsd')) or 0) > 0:
                continue
            stamp = lab_forward.observation_ms(coin, now)
            if stamp is not None:
                out.append((int(stamp), coin))
        return sorted(out, key=lambda item: item[0])

    def update(self, prices, now, feed_alive) -> None:
        """Every loop: entry fills, marks, exit triggers and fills, vanish, caps, floors and the kill rule."""
        with self.lock:
            if not self.loaded:
                return
            for book_id in BOOK_IDS:
                book = self.books[book_id]
                for key in sorted(book['slots'], key=int):
                    slot = book['slots'].get(key)
                    if slot is None:
                        continue
                    observed = False
                    for stamp, coin in self._observations(slot, prices, now):
                        if stamp <= int(slot.get('_seen') or 0):
                            continue
                        slot['_seen'] = stamp
                        observed = True
                        self._step(book_id, slot, coin, stamp, now)
                        if str(slot['trade_no']) not in book['slots']:
                            break
                    if str(slot['trade_no']) not in book['slots']:
                        continue
                    if observed:
                        slot.pop('_unpriced_since', None)
                    else:
                        slot.setdefault('_unpriced_since', now)
                    self._step(book_id, slot, None, None, now, feed_alive=feed_alive)
            self._limits(now)
            self.journal.flush()

    def _step(self, book_id, slot, coin, stamp, now, *, feed_alive=False) -> None:
        """One observation (or, with ``coin`` None, the passage of time) through a slot's state machine."""
        book = self.books[book_id]
        key = (slot['address'], slot['pairAddress'])
        if slot['state'] == ORDERED:
            leg = slot['entry_leg']
            if coin is not None:
                seen = slot.setdefault('_coins', {})
                seen[stamp] = coin
                if len(seen) > 4:
                    first = (leg or {}).get('first_later_at')
                    for old in sorted(seen)[:-2]:
                        if old != first:
                            seen.pop(old, None)
            if lab_forward.advance_fill_leg(leg, coin, now, key=key):
                self._resolve_entry(book_id, slot, now)
            if str(slot['trade_no']) not in book['slots'] or slot['state'] != OPEN or coin is None:
                return
            if stamp <= int(slot['entry']['fill_at']):
                return
        if slot['state'] == OPEN:
            if coin is None:
                self._vanish_check(book_id, slot, now, feed_alive)
                return
            if self._feed_gap(book_id, slot, coin, stamp, now):
                return
            self._mark(slot, coin, stamp)
            if stamp >= slot['decision_at'] + EXIT.hold_ms and stamp > int(slot['entry']['fill_at']):
                late = bool(slot.get('restarted_at') and slot['restarted_at'] >= slot['decision_at'] + EXIT.hold_ms)
                self._emit(book_id, 'order', {
                    'side': 'exit', 'trade_no': slot['trade_no'], 'address': slot['address'],
                    'pairAddress': slot['pairAddress'], 'trigger_at': int(stamp),
                    'trigger_price': _finite(coin.get('priceUsd')),
                    'trigger': lab_forward.fill_snapshot(coin, stamp), 'late_exit_restart': late}, now,
                    cfg=slot['cfg'])
            return
        if slot['state'] != EXIT_ORDERED:
            return
        if coin is None:
            leg = slot['exit_leg']
            if not slot['exit_waiting_next'] and lab_forward.advance_fill_leg(leg, None, now, key=key):
                if leg['status'] in (lab_forward.FILL_NEXT_REFRESH, lab_forward.FILL_QUIET):
                    self._close_at_leg(book_id, slot, now)
                else:
                    slot['exit_waiting_next'] = True
                    self.dirty = True
            if str(slot['trade_no']) in book['slots']:
                self._vanish_check(book_id, slot, now, feed_alive)
            return
        if slot['exit_waiting_next']:
            if not self._feed_gap(book_id, slot, coin, stamp, now):
                self._close_late(book_id, slot, coin, stamp, now)
            return
        leg = slot['exit_leg']
        if lab_forward.advance_fill_leg(leg, coin, now, key=key):
            if leg['status'] in (lab_forward.FILL_NEXT_REFRESH, lab_forward.FILL_QUIET):
                if not self._close_at_leg(book_id, slot, now, fill_coin=coin if leg['fill_at'] == stamp else None):
                    slot['exit_waiting_next'] = True
                return
            # No observation within the window: the one that arrived after it is the fill (harness i + 1).
            slot['exit_waiting_next'] = True
            if not self._feed_gap(book_id, slot, coin, stamp, now):
                self._close_late(book_id, slot, coin, stamp, now)
            return
        if str(slot['trade_no']) in book['slots']:
            self._mark(slot, coin, stamp)

    def _mark(self, slot, coin, stamp) -> None:
        slot['last_mark'] = lab_forward.mark_snapshot(coin, stamp, slot.get('last_mark'))
        slot['last_mark_at'] = int(stamp)
        entry = slot.get('entry') or {}
        try:
            quote = self.cost.calibrated_exit_execution(coin, _finite(entry.get('qty')) or 0.0,
                                                        _finite(entry.get('calib_bps')) or 0.0, drain_aware=True)
            slot['mark_pnl_usd'] = round(_finite(quote.get('net_proceeds_usd')) - (_finite(entry.get('capital')) or 0.0), 6)
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    def _feed_gap(self, book_id, slot, coin, stamp, now) -> bool:
        """A held pool returning after more than FILL.feed_gap_ms: FEED_GAP at min(pre-gap, return)."""
        last = _finite(slot.get('last_mark_at'))
        if last is None or stamp - last <= FILL.feed_gap_ms:
            return False
        pre = _snapshot_coin(slot, slot.get('last_mark'), int(last))
        back_price = _finite(coin.get('priceUsd')) or 0.0
        pre_price = _finite((pre or {}).get('priceUsd')) or 0.0
        back_liquidity = lab_forward.reported_liquidity_usd(coin)
        use_return = pre is None or (back_liquidity is not None and back_liquidity <= 0) or (
            back_price > 0 and back_price < pre_price)
        value_coin = coin if use_return else pre
        return self._close(book_id, slot, now, kind=CLOSE_FEED_GAP, exit_coin=value_coin, exit_fill_at=stamp,
                           exit_status=FILL_FEED_GAP, exit_lag_ms=None, exit_outlier_basis=None)

    def _vanish_check(self, book_id, slot, now, feed_alive) -> bool:
        last = _finite(slot.get('last_mark_at'))
        since = _finite(slot.get('_unpriced_since'))
        if (not feed_alive or last is None or since is None or now - last < FILL.vanish_after_ms
                or now - since < FILL.vanish_confirm_ms):
            return False
        coin = lab_forward.vanished_coin({'address': slot['address'], 'pairAddress': slot['pairAddress'],
                                          'last_mark': slot.get('last_mark')})
        if coin is None:
            slot['vanish_unvaluable'] = True
            return False
        return self._close(book_id, slot, now, kind=CLOSE_VANISHED, exit_coin=coin, exit_fill_at=int(now),
                           exit_status=lab_forward.FILL_VANISHED, exit_lag_ms=None, exit_outlier_basis=None)

    # ---------------------------------------------------------------- entry fill

    def _resolve_entry(self, book_id, slot, now) -> None:
        leg = slot['entry_leg']
        if leg['status'] == lab_forward.FILL_NO_NEXT:
            self._emit(book_id, 'cancel', self._slot_fields(slot, reason=CANCEL_NO_NEXT), now, cfg=slot['cfg'])
            return
        fill_at = int(leg['fill_at'])
        fill_coin = _snapshot_coin(slot, leg.get('fill'), fill_at)
        seen = (slot.get('_coins') or {}).get(fill_at)
        static = {'symbol': slot.get('symbol'), 'pairCreatedAt': slot.get('pairCreatedAt')}
        check_coin = {**{k: v for k, v in static.items() if v is not None}, **(seen or {}), **fill_coin}
        try:
            structural = self.structural_check(check_coin, now)
            blocked = bool(structural.get('blocked'))
        except Exception as exc:  # fail closed
            structural = {'blocked': True, 'reasons': ['structural_check_error'], 'error': type(exc).__name__}
            blocked = True
        if blocked:
            self._emit(book_id, 'cancel', self._slot_fields(
                slot, reason=CANCEL_STRUCTURAL, structural=list(structural.get('reasons') or [])), now,
                cfg=slot['cfg'])
            return
        decision_price = _finite(leg.get('decision_price')) or 0.0
        fill_price = _finite(leg.get('fill_price')) or 0.0
        outlier = decision_price > 0 and abs(fill_price / decision_price - 1) * 100 >= PRINT_GUARD.outlier_fill_pct
        valuation = max(decision_price, fill_price) if outlier else fill_price
        value_coin = _repriced(fill_coin, valuation)
        notional = BOOK_RULES.notional_usd
        try:
            fee = float(self.cost.pumpswap_fee_bps(fill_coin))
            liquidity = float(self.cost.pair_liquidity_usd(fill_coin))
            calib = lab_forward.calib_extra_bps_per_leg(fee, liquidity, notional)
            calib_bps = float(calib['total_bps'])
            booked = self.cost.calibrated_entry_execution(value_coin, notional, calib_bps)
            model = self.cost.entry_execution(value_coin, notional)
            mark = self.cost.calibrated_exit_execution(fill_coin, float(booked['quantity']), calib_bps,
                                                       drain_aware=True)
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            self._emit(book_id, 'cancel', self._slot_fields(slot, reason=CANCEL_UNPRICED), now, cfg=slot['cfg'])
            return
        shadow = None
        decision_coin = _snapshot_coin(slot, leg.get('decision'), slot['decision_at'])
        try:
            dp = self.cost.calibrated_entry_execution(decision_coin, notional, calib_bps)
            shadow = {'qty': _sig(dp['quantity'], 12), 'capital': _round(dp['capital_committed_usd'], 8)}
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            shadow = None
        capital = float(booked['capital_committed_usd'])
        entry = {
            'fill_at': fill_at, 'fill_price': _sig(fill_price, 12), 'status': leg['status'],
            'lag_ms': leg.get('fill_lag_ms'), 'later_observations': leg.get('later_observations'),
            'valuation_price': _sig(valuation, 12), 'outlier': bool(outlier),
            'fee_bps': _round(fee, 4), 'liq': _round(liquidity, 2), 'calib_bps': _round(calib_bps, 6),
            'qty': _sig(booked['quantity'], 12), 'capital': _round(capital, 8),
            'qty0': _sig(model['quantity'], 12), 'capital0': _round(model['capital_committed_usd'], 8),
            'dp': shadow,
            # The fill observation as booked (lab_forward_tests.fill_snapshot): last mark and research-fill input.
            'mark': dict(leg.get('fill') or {}),
            'mark_pnl_usd': _round(_finite(mark['net_proceeds_usd']) - capital, 6),
            'structural': {'blocked': False, 'reasons': list(structural.get('reasons') or [])},
            'audit': self._audit_cached(fill_coin, now),
        }
        self._emit(book_id, 'fill', {'side': 'entry', 'trade_no': slot['trade_no'], 'address': slot['address'],
                                     'pairAddress': slot['pairAddress'], 'entry': entry}, now, cfg=slot['cfg'])

    def _audit_cached(self, coin, now):
        if self.price_audit is None or not hasattr(self.price_audit, 'cached'):
            return None
        try:
            result = self.price_audit.cached(coin)
        except Exception:
            self.audit_stats['errors'] += 1
            return None
        if not isinstance(result, dict):
            return None
        self.audit_stats['cached_fills'] += 1
        received = _finite(result.get('reference_received_at'))
        return {'status': result.get('status'), 'deviation_pct': _round(result.get('divergence_pct'), 4),
                'reference_age_ms': None if received is None else int(now - received)}

    def _audit_decision(self, coin, now):
        """At a decision: the cached reference, else at most 2 check() calls per rolling minute (log only)."""
        if self.price_audit is None:
            return None
        cached = self._audit_cached(coin, now)
        if cached is not None:
            return {'basis': 'cached', **cached}
        if not self.audit_bucket.take(now):
            self.audit_stats['skipped_by_bucket'] += 1
            return {'basis': 'skipped_bucket'}
        try:
            result = self.price_audit.check(coin)
        except Exception:
            self.audit_stats['errors'] += 1
            return {'basis': 'check_error'}
        self.audit_stats['checks'] += 1
        result = result if isinstance(result, dict) else {}
        return {'basis': 'check', 'status': result.get('status'), 'reason': result.get('reason'),
                'deviation_pct': _round(result.get('divergence_pct'), 4)}

    # ---------------------------------------------------------------- exits and closes

    def _close_at_leg(self, book_id, slot, now, fill_coin=None) -> bool:
        leg = slot['exit_leg']
        coin = _snapshot_coin(slot, leg.get('fill'), int(leg['fill_at']))
        if fill_coin is not None:
            coin = {**fill_coin, **coin}
        return self._close(book_id, slot, now, kind=CLOSE_TIME, exit_coin=coin, exit_fill_at=int(leg['fill_at']),
                           exit_status=leg['status'], exit_lag_ms=leg.get('fill_lag_ms'),
                           exit_outlier_basis=_finite(leg.get('decision_price')))

    def _close_late(self, book_id, slot, coin, stamp, now) -> bool:
        trigger_at = int((slot.get('trigger') or {}).get('at') or stamp)
        closed = self._close(book_id, slot, now, kind=CLOSE_TIME, exit_coin=coin, exit_fill_at=int(stamp),
                             exit_status=FILL_LATE_NEXT, exit_lag_ms=int(stamp - trigger_at),
                             exit_outlier_basis=_finite((slot.get('trigger') or {}).get('price')))
        if not closed:
            self._mark(slot, coin, stamp)
        return closed

    def _value_round_trip(self, slot, exit_coin, *, exit_outlier_basis):
        """(booked dict, flags) of one close: print-guarded exit valuation, booked, net0 and net50."""
        entry = slot['entry']
        qty, capital = float(entry['qty']), float(entry['capital'])
        calib = float(entry['calib_bps'])
        price = _finite(exit_coin.get('priceUsd')) or 0.0
        outlier = bool(exit_outlier_basis and exit_outlier_basis > 0
                       and abs(price / exit_outlier_basis - 1) * 100 >= PRINT_GUARD.outlier_fill_pct)
        valuation = min(price, exit_outlier_basis) if outlier else price
        entry_valuation = _finite(entry.get('valuation_price')) or 0.0
        cap = entry_valuation * PRINT_GUARD.gain_cap_ratio
        capped = entry_valuation > 0 and valuation > cap
        if capped:
            valuation = cap
        value_coin = _repriced(exit_coin, valuation)
        booked = self.cost.calibrated_exit_execution(value_coin, qty, calib, drain_aware=True)
        model = self.cost.exit_execution(value_coin, float(entry.get('qty0') or 0.0))
        pnl = float(booked['net_proceeds_usd']) - capital
        net0 = float(model['net_proceeds_usd']) - float(entry.get('capital0') or capital)
        return ({'pnl': pnl, 'net0': net0, 'proceeds': float(booked['net_proceeds_usd']),
                 'valuation': valuation, 'value_coin': value_coin},
                {'exit_outlier': outlier, 'gain_capped': bool(capped)})

    def _research_value(self, slot, exit_coin, exit_valuation):
        """The booked model re-run from the stored entry fill observation (research_fill.minus_booked_usd)."""
        entry = slot['entry']
        calib = float(entry['calib_bps'])
        entry_coin = _repriced(_snapshot_coin(slot, entry.get('mark'), int(entry['fill_at'])),
                               _finite(entry.get('valuation_price')))
        opened = self.cost.calibrated_entry_execution(entry_coin, BOOK_RULES.notional_usd, calib)
        quantity = float(_sig(opened['quantity'], 12))
        closed = self.cost.calibrated_exit_execution(_repriced(exit_coin, exit_valuation), quantity, calib,
                                                     drain_aware=True)
        return {'pnl': float(closed['net_proceeds_usd']) - float(_round(opened['capital_committed_usd'], 8))}

    def _close(self, book_id, slot, now, *, kind, exit_coin, exit_fill_at, exit_status, exit_lag_ms,
               exit_outlier_basis) -> bool:
        book = self.books[book_id]
        entry = slot.get('entry')
        if not entry or not isinstance(exit_coin, dict):
            return False
        notional = BOOK_RULES.notional_usd
        try:
            booked, flags = self._value_round_trip(slot, exit_coin, exit_outlier_basis=exit_outlier_basis)
            # Research fill (LAB_FORWARD_FILL_BASIS_V4 shape): the booked model re-run from the stored entry
            # fill observation and the exit fill observation; booking is at the research fill, so it equals it.
            research = self._research_value(slot, exit_coin, booked['valuation'])
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            return False
        pnl, net0 = booked['pnl'], booked['net0']
        stressed = lab_forward.net50(pnl, notional, kind)
        research_net50 = lab_forward.net50(research['pnl'], notional, kind)
        shadow = {'pnl_usd': None, 'net50_usd': None}
        trigger = slot.get('trigger') or {}
        dp = entry.get('dp') or {}
        trigger_coin = _snapshot_coin(slot, trigger.get('snapshot'), trigger.get('at')) if trigger else None
        if kind == CLOSE_TIME and trigger_coin and dp.get('qty'):
            try:
                quote = self.cost.calibrated_exit_execution(trigger_coin, float(dp['qty']), float(entry['calib_bps']),
                                                            drain_aware=True)
                dp_pnl = float(quote['net_proceeds_usd']) - float(dp['capital'])
                shadow = {'pnl_usd': _round(dp_pnl), 'net50_usd': lab_forward.net50(dp_pnl, notional, kind)['net50_usd']}
            except (ValueError, TypeError, ZeroDivisionError, KeyError):
                pass
        recent = [{'address': item[0], 'pairAddress': item[1], 'closed_at': item[2], 'pnl_usd': item[3]}
                  for item in book['recent']]
        recent.append({'address': slot['address'], 'pairAddress': slot['pairAddress'], 'closed_at': int(now),
                       'pnl_usd': pnl})
        plm = (slot['address'], slot['pairAddress']) in pool_loss_memory.index(recent, now)
        exit_price = _finite(exit_coin.get('priceUsd'))
        fields = {
            'trade_no': slot['trade_no'], 'address': slot['address'], 'pairAddress': slot['pairAddress'],
            'symbol': slot.get('symbol'),
            'fee_bps': entry.get('fee_bps'), 'liq_at_fill': entry.get('liq'),
            'decision_at': slot['decision_at'], 'decision_price': slot['decision_price'],
            'entry_fill_at': entry['fill_at'], 'entry_fill_price': entry['fill_price'],
            'entry_status': entry['status'], 'entry_lag_ms': entry.get('lag_ms'),
            'trigger_at': trigger.get('at'), 'trigger_price': trigger.get('price'),
            'exit_fill_at': int(exit_fill_at), 'exit_fill_price': _sig(exit_price, 12), 'exit_status': exit_status,
            'exit_lag_ms': exit_lag_ms, 'exit_liquidity_usd': _round(lab_forward.reported_liquidity_usd(exit_coin), 2),
            'close_kind': kind,
            'qty': entry['qty'], 'capital_committed_usd': entry['capital'],
            'net_proceeds_usd': _round(booked['proceeds'], 8),
            'pnl_usd': _round(pnl), 'pnl_pct': _round(pnl / notional * 100),
            'net0_usd': _round(net0), 'net0_pct': _round(net0 / notional * 100),
            'net50_usd': stressed['net50_usd'], 'net50_pct': stressed['net50_pct'], 'calib_bps': entry['calib_bps'],
            'research_fill': {'v': lab_forward.FILL_BASIS_VERSION, 'entry_status': entry['status'],
                              'exit_status': exit_status, 'entry_fill_price': entry['fill_price'],
                              'exit_fill_price': _sig(exit_price, 12), 'entry_lag_ms': entry.get('lag_ms'),
                              'exit_lag_ms': exit_lag_ms, 'net50_usd': research_net50['net50_usd'],
                              'net50_pct': research_net50['net50_pct'],
                              'minus_booked_usd': _round(research['pnl'] - pnl)},
            'decision_print_shadow': shadow,
            'print_guard': {'entry_outlier': bool(entry.get('outlier')), 'exit_outlier': flags['exit_outlier'],
                            'gain_capped': flags['gain_capped'], 'entry_value': entry.get('valuation_price'),
                            'exit_value': _sig(booked['valuation'], 12)},
            'plm_v1_would_block': bool(plm), 'plm_v1_at_order': bool(slot.get('plm_v1_would_block')),
            'price_audit': {'entry': entry.get('audit'), 'exit': self._audit_cached(exit_coin, now)},
            'late_exit_restart': bool(slot.get('late_exit_restart')),
            'balance_after': _round(float(book['balance']) + pnl, 6), 'closed_at': int(now),
        }
        self._emit(book_id, 'close', fields, now, cfg=slot['cfg'])
        return True

    def _slot_fields(self, slot, **extra) -> dict:
        return {'trade_no': slot['trade_no'], 'address': slot['address'], 'pairAddress': slot['pairAddress'],
                'decision_at': slot['decision_at'], **extra}

    # ---------------------------------------------------------------- limits: cap, floor, retirement, kill rule

    def _open_marks(self, book):
        return [(_finite(slot.get('mark_pnl_usd')) or 0.0) for slot in book['slots'].values()
                if slot['state'] in (OPEN, EXIT_ORDERED)]

    def day_pnl(self, book, now) -> float:
        label = day_label(now)
        realized = 0.0
        for agg in book['aggregates'].values():
            realized += float((agg['by_day'].get(label) or {}).get('booked') or 0.0)
        return realized + sum(min(0.0, value) for value in self._open_marks(book))

    def equity(self, book) -> float:
        return float(book['balance']) + sum(self._open_marks(book))

    def cap_tripped(self, book, now) -> bool:
        cap = book.get('cap')
        return bool(cap and int(cap['day']) == utc_day(now))

    def can_enter(self, book_id) -> bool:
        return self.books[book_id]['retired'] is None

    def _retire(self, book_id, reason, now, evidence=None) -> None:
        if self.books[book_id]['retired'] is None:
            self._emit(book_id, 'retire', {'reason': reason, 'evidence': evidence}, now)

    def _limits(self, now) -> None:
        for book_id in BOOK_IDS:
            book = self.books[book_id]
            if not self.cap_tripped(book, now):
                pnl = self.day_pnl(book, now)
                if pnl <= -float(self.budget.daily_cap_usd):
                    self._emit(book_id, 'cap', {'day': utc_day(now), 'pnl_usd': _round(pnl),
                                                'limit_usd': float(self.budget.daily_cap_usd)}, now)
            if book['retired'] is None:
                equity = self.equity(book)
                if equity <= float(self.budget.capital_floor_fraction) * float(book['start_balance']):
                    self._retire(book_id, 'capital_floor', now, {'equity_usd': _round(equity),
                                                                 'start_usd': book['start_balance']})
            if book['retired'] is None and book_id in self.retire_flags:
                self._retire(book_id, 'operator_flag', now, {'env': ENV_RETIRE})
        for book_id in HYPOTHESIS_IDS:
            self._kill_checkpoint(book_id, now)
        if (self.books[CONTROL_ID]['retired'] is None
                and all(self.books[book_id]['retired'] is not None for book_id in HYPOTHESIS_IDS)):
            self._retire(CONTROL_ID, 'all_hypotheses_retired', now)

    def journal_closes(self, book_id, cfg=None, since=None) -> list:
        rows = OrderedDict()
        for row in self.journal.rows(book_id, kinds=('close',)):
            if cfg is not None and row.get('cfg') != cfg:
                continue
            if since is not None and int(row.get('at') or 0) < since:
                continue
            rows.setdefault(row['seq'], row)
        return sorted(rows.values(), key=lambda row: row['seq'])

    def _kill_checkpoint(self, book_id, now, force=False) -> dict | None:
        book = self.books[book_id]
        if book['retired'] is not None:
            return None
        cfg = self.cfg[book_id]
        agg = book['aggregates'].get(cfg)
        closes = int((agg or {}).get('n') or 0)
        kill = book['kill']
        days = sum(1 for value in ((agg or {}).get('by_day') or {}).values() if value.get('closes'))
        if not force and (closes < int(kill['next_checkpoint']) or days < KILL_RULE.min_utc_days_with_closes):
            return None
        self.journal.flush()
        rows = self.journal_closes(book_id, cfg)
        evaluation = kill_evaluation(rows, self.journal_closes(CONTROL_ID, since=int(agg['first_close_at'])
                                                               if agg and agg.get('first_close_at') else None))
        evaluation.update(closes=closes, utc_days=days, at=int(now), checkpoint=int(kill['next_checkpoint']))
        kill['last'] = evaluation
        kill['checkpoints'] = int(kill.get('checkpoints') or 0) + 1
        step = KILL_RULE.checkpoint_every_closes
        following = KILL_RULE.first_checkpoint_closes
        while following <= closes:
            following += step
        kill['next_checkpoint'] = following
        self.dirty = True
        if evaluation['met']:
            self._retire(book_id, 'kill_checkpoint', now, {key: evaluation[key] for key in (
                'closes', 'utc_days', 'mean_net50_usd', 'ci95_net50_usd', 'gap_net50_pct',
                'control_ci_half_width_pct', 'within_fee_bucket_gap_pct')})
        return evaluation

    # ---------------------------------------------------------------- decisions (each Lab refresh)

    def _universe(self, coin):
        reasons = []
        if str(coin.get('dexId') or '').lower() != UNIVERSE.dex_id:
            reasons.append('hf_dex_not_pumpswap')
        if feasibility.quote_token_address(coin) != UNIVERSE.quote_token_address:
            reasons.append('hf_quote_not_sol')
        liquidity = _finite(self.cost.pair_liquidity_usd(coin)) or 0.0
        if liquidity < UNIVERSE.min_liquidity_usd:
            reasons.append('hf_liquidity_below_minimum')
        if reasons:
            return reasons, None, liquidity, None
        fee = _finite(self.cost.pumpswap_fee_bps(coin))
        if fee is None or fee > UNIVERSE.max_fee_tier_bps:
            return ['hf_fee_tier_above_maximum'], fee, liquidity, None
        notional = UNIVERSE.roundtrip_notional_usd
        try:
            entry = self.cost.entry_execution(coin, notional)
            exit_quote = self.cost.exit_execution(coin, float(entry['quantity']))
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            return ['hf_network_price_unknown'], fee, liquidity, None
        roundtrip = 100.0 * (float(entry['capital_committed_usd']) - float(exit_quote['net_proceeds_usd'])) / notional
        if roundtrip > UNIVERSE.max_modeled_roundtrip_pct:
            return ['hf_modeled_roundtrip_above_cap'], fee, liquidity, roundtrip
        return [], fee, liquidity, roundtrip

    def _signal(self, book_id, candidate) -> str | None:
        """None when the book's signal matches the candidate, else its reason."""
        params = SIGNALS[book_id]
        coin, stamp = candidate['coin'], candidate['stamp']
        if params.kind == 'random':
            return None if lab_forward.hashed_coin(coin['pairAddress'], stamp, params.probability,
                                                   params.salt) else 'hf_rnd_not_drawn'
        price = candidate['price']
        if params.kind == 'quiet_pool':
            volume, liquidity = _volume_m5(coin), candidate['liquidity']
            previous = candidate['previous_price']
            if volume is None or not liquidity or not previous:
                return 'hf_quiet_input_unknown'
            if volume / liquidity > params.max_turnover_m5:
                return 'hf_quiet_turnover'
            if abs(price / previous - 1) > params.max_abs_step:
                return 'hf_quiet_step'
            return None
        low, covered = self.memory.window_low(candidate['key'], stamp)
        if not covered or low is None:
            return 'hf_dip15_history_warming'
        # The research form (lo15 = price / low - 1 <= 0.002), float for float.
        return None if price / low - 1 <= params.max_above_low else 'hf_dip15_not_at_low'

    def _book_gate(self, book_id, now) -> str | None:
        book = self.books[book_id]
        if book['retired'] is not None:
            return 'hf_retired'
        if self.cost_model_mismatches():
            return 'hf_cost_model_mismatch'
        if self.degraded:
            return 'hf_degraded'
        if self.cap_tripped(book, now):
            return 'hf_daily_loss_cap'
        day = utc_day(now)
        open_hour = session_open_hour(day, self.budget)
        if int((now % DAY_MS) // HOUR_MS) < open_hour:
            return 'hf_session_not_open'
        session = book.get('session')
        if not session or int(session.get('day', -1)) != day:
            self._emit(book_id, 'session', {'day': day, 'open_hour': open_hour}, now)
        if len(book['slots']) >= BOOK_RULES.slots:
            return 'hf_slots_full'
        reserved = sum(float(slot.get('reserved_usd') or BOOK_RULES.min_order_balance_usd)
                       for slot in book['slots'].values())
        if float(book['balance']) - reserved < BOOK_RULES.min_order_balance_usd:
            return 'hf_insufficient_balance'
        return None

    def on_refresh(self, feed, now) -> dict:
        """One Lab refresh: refresh events, the shared universe, then at most one order per book."""
        with self.lock:
            if not self.loaded:
                return {'blocked_reason': 'hf_not_loaded'}
            try:
                self.defense.observe(feed, now)
            except Exception:
                pass
            events = []
            observations = 0
            for coin in feed or ():
                key = _identity(coin)
                price = _finite(coin.get('priceUsd')) if isinstance(coin, dict) else None
                if key is None or price is None or price <= 0:
                    continue
                stamp = lab_forward.observation_ms(coin, now)
                if stamp is None:
                    continue
                observations += 1
                info = self.memory.observe(key, int(stamp), price)
                if info['event'] and activity.usable_feed_coin(coin, now):
                    events.append({'stamp': int(stamp), 'key': key, 'coin': coin, 'price': price,
                                   'previous_price': info['previous_price']})
            self.memory.prune(now)
            events.sort(key=lambda item: (item['stamp'], item['key'][1]))
            universe_rejections = {}
            candidates = []
            for event in events:
                reasons, fee, liquidity, roundtrip = self._universe(event['coin'])
                if not reasons and self.memory.toggled(event['key'], event['stamp']):
                    reasons = ['hf_toggle_pool']
                if reasons:
                    universe_rejections[reasons[0]] = universe_rejections.get(reasons[0], 0) + 1
                    continue
                event.update(fee=fee, liquidity=liquidity, roundtrip=roundtrip)
                candidates.append(event)
            defensive = {}
            stats = {'at': int(now), 'observations': observations, 'events': len(events),
                     'universe': len(candidates), 'pools': len({event['key'] for event in candidates}),
                     'universe_rejections': universe_rejections, 'defensive_calls': 0,
                     'pools_in_memory': len(self.memory)}
            for book_id in BOOK_IDS:
                self.diagnostics[book_id] = self._decide(book_id, candidates, defensive, stats, now)
            self.refresh_stats = stats
            self.journal.flush()
            return stats

    def _defensive(self, cache, stats, candidate, now):
        key = candidate['key']
        if key not in cache:
            stats['defensive_calls'] += 1
            try:
                decision = self.defense.evaluate(candidate['coin'], now, blocked_pools={}, heat_log_only=False)
            except Exception as exc:  # fail closed
                decision = entry_defense.error_decision(exc)
            cache[key] = decision if isinstance(decision, dict) else {'allowed': False,
                                                                     'reasons': [entry_defense.ERROR_REASON]}
        return cache[key]

    def _decide(self, book_id, candidates, defensive, stats, now) -> dict:
        book = self.books[book_id]
        diag = {'at': int(now), 'events': stats['events'], 'universe': len(candidates), 'signals': 0,
                'ordered': 0, 'rejections': {}, 'blocked_reason': None}
        gate = self._book_gate(book_id, now)
        if gate is not None:
            diag['blocked_reason'] = gate
            if gate == 'hf_session_not_open':
                diag['session_open_hour'] = session_open_hour(utc_day(now), self.budget)
            return diag
        if not candidates:
            diag['blocked_reason'] = 'hf_no_refresh_event' if not stats['events'] else 'hf_universe_empty'
            return diag
        held = {(slot['address'], slot['pairAddress']) for slot in book['slots'].values()}
        governor = book['governor']

        def reject(reason):
            diag['rejections'][reason] = diag['rejections'].get(reason, 0) + 1

        for candidate in candidates:
            key, stamp = candidate['key'], candidate['stamp']
            if key in held:
                reject('hf_pool_held')
                continue
            if stamp < int(book['cooldowns'].get(_pool_text(key), 0)):
                reject('hf_pool_cooldown')
                continue
            reason = self._signal(book_id, candidate)
            if reason is not None:
                reject(reason)
                continue
            diag['signals'] += 1
            while governor and governor[0] <= stamp - BOOK_RULES.governor_window_ms:
                governor.pop(0)
            if len(governor) >= BOOK_RULES.governor_max_orders:
                reject('hf_rate_governor')
                continue
            decision = self._defensive(defensive, stats, candidate, now)
            if not decision.get('allowed'):
                reject('hf_defensive_entry')
                for item in decision.get('reasons') or ():
                    diag.setdefault('defensive_reasons', {})
                    diag['defensive_reasons'][item] = diag['defensive_reasons'].get(item, 0) + 1
                continue
            self._order(book_id, candidate, decision, now)
            diag['ordered'] = 1
            return diag
        diag['blocked_reason'] = max(diag['rejections'], key=diag['rejections'].get) if diag['rejections'] else None
        return diag

    def _order(self, book_id, candidate, decision, now) -> dict:
        book = self.books[book_id]
        coin, stamp, key = candidate['coin'], candidate['stamp'], candidate['key']
        recent = [{'address': item[0], 'pairAddress': item[1], 'closed_at': item[2], 'pnl_usd': item[3]}
                  for item in book['recent']]
        plm = key in pool_loss_memory.index(recent, now)
        fields = {
            'side': 'entry', 'trade_no': int(book['trade_no']) + 1, 'address': key[0], 'pairAddress': key[1],
            'symbol': str(coin.get('symbol') or '')[:40] or None,
            'pairCreatedAt': _finite(coin.get('pairCreatedAt')),
            'decision_at': int(stamp), 'decision_price': _sig(candidate['price'], 12),
            'previous_price': _sig(candidate.get('previous_price'), 12),
            'decision': lab_forward.fill_snapshot(coin, stamp),
            'fee_bps': _round(candidate.get('fee'), 4), 'liq': _round(candidate.get('liquidity'), 2),
            'rt_model_pct': _round(candidate.get('roundtrip'), 4),
            'signal': SIGNALS[book_id].kind, 'plm_v1_would_block': bool(plm),
            'price_audit': self._audit_decision(coin, now),
        }
        return self._emit(book_id, 'order', fields, now)

    # ---------------------------------------------------------------- published views

    def config(self) -> dict:
        return config_view(self.cfg, self.budget, enabled_flag=True,
                           cost_model_mismatches=self.cost_model_mismatches(),
                           budget_env_errors=budget_env_errors(self.environ), retire_flags=self.retire_flags)

    def metrics(self) -> dict:
        with self.lock:
            now = int(self.clock())
            return {
                'version': VERSION, 'loaded': self.loaded, 'degraded': self.degraded, 'slow_loops': self.slow_loops,
                'loop_ms_last': self.loop_ms[-1] if self.loop_ms else None,
                'loop_ms_max_recent': max(self.loop_ms) if self.loop_ms else None,
                'refresh': dict(self.refresh_stats),
                'journal': {'version': JOURNAL_VERSION, 'bytes_written': self.journal.bytes_written,
                            'rows_written': self.journal.rows_written, 'flushes': self.journal.flushes,
                            'last_flush_files': self.journal.last_flush_files,
                            'last_flush_ms': self.journal.last_flush_ms,
                            'malformed_rows_skipped': self.journal.malformed_rows},
                'checkpoint': {'version': CHECKPOINT_VERSION, **self.checkpoint_stats},
                'load': dict(self.load_report),
                'price_audit': {'version': PRICE_AUDIT_VERSION, **self.audit_stats,
                                'checks_last_60s': self.audit_bucket.used(now),
                                'max_checks_per_minute': ACCOUNTING.price_audit_max_checks_per_minute},
                'orders_last_60m': {book_id: sum(1 for stamp in book['governor'] if stamp > now - HOUR_MS)
                                    for book_id, book in self.books.items()},
                'open_slots': {book_id: len(book['slots']) for book_id, book in self.books.items()},
                'seq': self.seq,
            }

    def _book_status(self, book_id, now) -> dict:
        book = self.books[book_id]
        if book['retired'] is not None:
            return {'status': 'retired', 'reason': book['retired'].get('reason')}
        if self.cost_model_mismatches():
            return {'status': 'cost_model_mismatch'}
        if self.degraded:
            return {'status': 'degraded'}
        if self.cap_tripped(book, now):
            return {'status': 'cap', 'until': int((utc_day(now) + 1) * DAY_MS)}
        open_hour = session_open_hour(utc_day(now), self.budget)
        if int((now % DAY_MS) // HOUR_MS) < open_hour:
            return {'status': 'session_closed', 'open_hour': open_hour}
        diag = self.diagnostics.get(book_id) or {}
        warming_reasons = (diag.get('defensive_reasons') or {})
        if self.started_at is not None and now - self.started_at < heat_veto.PARAMS.crash_window_seconds * 1000:
            return {'status': 'warming'}
        if (not diag.get('ordered') and diag.get('rejections')
                and set(diag['rejections']) <= {'hf_defensive_entry', 'hf_dip15_history_warming'}
                and ('heat_history_warming' in warming_reasons or 'hf_dip15_history_warming' in diag['rejections'])):
            return {'status': 'warming'}
        return {'status': 'active'}

    def _gap(self, book_id, now) -> dict | None:
        if book_id not in HYPOTHESIS_IDS:
            return None
        hyp = self.books[book_id]['aggregates'].get(self.cfg[book_id])
        ctrl = self.books[CONTROL_ID]['aggregates'].get(self.cfg[CONTROL_ID])
        if not hyp or not ctrl or not hyp['n'] or not ctrl['n']:
            return None
        notional = BOOK_RULES.notional_usd
        point = 100.0 * (hyp['sum']['net50_usd'] / hyp['n'] - ctrl['sum']['net50_usd'] / ctrl['n']) / notional
        cache_key = (book_id, hyp['n'], ctrl['n'])
        cached = self._gap_cache.get(book_id)
        if cached is None or (cached['key'] != cache_key and now - cached['at'] >= 60_000):
            ci = pair_bootstrap_gap(_pair_groups(hyp['by_pair']), _pair_groups(ctrl['by_pair']), notional=notional)
            cached = {'key': cache_key, 'at': now, 'ci': ci}
            self._gap_cache[book_id] = cached
        within = {}
        for bucket in ('fee<=50', 'fee55-95'):
            a, b = hyp['by_fee_bucket'].get(bucket), ctrl['by_fee_bucket'].get(bucket)
            if a and b and a[0] and b[0]:
                within[bucket] = round(a[3] / a[0] - b[3] / b[0], 4)
        return {'gap_net50_pct': round(point, 4), 'ci95': cached['ci'], 'ci_closes': list(cached['key'][1:]),
                'within_fee_bucket_pct': within, 'basis': 'all closes of each current config (pair bootstrap)'}

    def dashboard_view(self, now) -> dict:
        """The compact HF section of the Lab dashboard projection (at most 16 KB)."""
        with self.lock:
            books = []
            today = day_label(now)
            for book_id in BOOK_IDS:
                book = self.books[book_id]
                agg = book['aggregates'].get(self.cfg[book_id]) or new_aggregate()
                totals = {'booked': 0.0, 'net50': 0.0, 'n': 0}
                for item in book['aggregates'].values():
                    totals['booked'] += item['sum']['booked_usd']
                    totals['net50'] += item['sum']['net50_usd']
                    totals['n'] += item['n']
                day = {'closes': 0, 'booked': 0.0, 'net50': 0.0, 'cancels': {}, 'orders': 0}
                for item in book['aggregates'].values():
                    entry = item['by_day'].get(today) or {}
                    day['closes'] += int(entry.get('closes') or 0)
                    day['orders'] += int(entry.get('orders') or 0)
                    day['booked'] += float(entry.get('booked') or 0.0)
                    day['net50'] += float(entry.get('net50') or 0.0)
                    for reason, count in (entry.get('cancels') or {}).items():
                        day['cancels'][reason] = day['cancels'].get(reason, 0) + count
                last_hour = sum(1 for item in book['aggregates'].values()
                                for stamp in item['close_ts'] if stamp > now - HOUR_MS)
                session = book.get('session') or {}
                opened = int(session['opened_at']) if session.get('day') == utc_day(now) else None
                hours = None
                if opened is not None:
                    end = int((book.get('cap') or {}).get('tripped_at') or now) if self.cap_tripped(book, now) else now
                    hours = max(0.0, (end - opened) / HOUR_MS)
                n = agg['n']
                top = max((value[0] for value in agg['by_pair'].values()), default=0)
                kill = book['kill']
                last_kill = kill.get('last') or {}
                books.append({
                    'id': book_id, 'name': BOOK_NAMES[book_id],
                    'role': 'hypothesis' if book_id in HYPOTHESIS_IDS else 'random_control',
                    'control': CONTROL_OF.get(book_id), 'config_hash': self.cfg[book_id],
                    **self._book_status(book_id, now),
                    'control_retired': bool(book_id in HYPOTHESIS_IDS and self.books[CONTROL_ID]['retired']),
                    'retired': book['retired'] and {'reason': book['retired'].get('reason'),
                                                    'at': book['retired'].get('at')},
                    'trades_last_60m': last_hour, 'trades_today': day['closes'], 'orders_today': day['orders'],
                    'orders_last_60m': sum(1 for stamp in book['governor'] if stamp > now - HOUR_MS),
                    'open_slots': len(book['slots']), 'max_slots': BOOK_RULES.slots,
                    'balance_usd': _round(book['balance'], 2), 'equity_usd': _round(self.equity(book), 2),
                    'start_balance_usd': book['start_balance'],
                    'booked_today_usd': _round(day['booked'], 2), 'net50_today_usd': _round(day['net50'], 2),
                    'booked_total_usd': _round(totals['booked'], 2), 'net50_total_usd': _round(totals['net50'], 2),
                    'closes_total': totals['n'], 'closes_config': n,
                    'mean_booked_pct': _round(agg['sum']['booked_pct'] / n, 4) if n else None,
                    'mean_net50_pct': _round(agg['sum']['net50_pct'] / n, 4) if n else None,
                    'win_rate_net50_pct': _round(100.0 * agg['wins']['net50'] / n, 2) if n else None,
                    'usd_per_hour_today': ({'booked': _round(day['booked'] / hours, 2),
                                            'net50': _round(day['net50'] / hours, 2)}
                                           if hours and hours >= 0.05 else None),
                    'cap': {'limit_usd': float(self.budget.daily_cap_usd),
                            'used_usd': _round(max(0.0, -self.day_pnl(book, now)), 2),
                            'tripped_at': (book.get('cap') or {}).get('tripped_at') if self.cap_tripped(book, now)
                            else None},
                    'session_open_hour': session_open_hour(utc_day(now), self.budget),
                    'kill': {'closes': n, 'next_checkpoint': kill.get('next_checkpoint'),
                             'utc_days_with_closes': sum(1 for value in agg['by_day'].values() if value.get('closes')),
                             'min_utc_days': KILL_RULE.min_utc_days_with_closes,
                             'last': {key: last_kill.get(key) for key in (
                                 'at', 'closes', 'met', 'mean_net50_usd', 'ci95_net50_usd', 'gap_net50_pct',
                                 'control_ci_half_width_pct')} if last_kill else None,
                             'applies': book_id in HYPOTHESIS_IDS},
                    'gap_vs_control': self._gap(book_id, now),
                    'top_pair_share': _round(top / n, 4) if n else None, 'pairs': len(agg['by_pair']),
                    'cancels_today': day['cancels'],
                    'last_closes': book['last_closes'][:10],
                    'blocked_reason': (self.diagnostics.get(book_id) or {}).get('blocked_reason'),
                })
            return {'version': VERSION, 'title': 'Висока честота (HF)',
                    'badge': 'ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА', 'portfolio_group': PORTFOLIO_GROUP,
                    'updated_at': int(now), 'loaded': self.loaded, 'degraded': self.degraded,
                    'notional_usd': BOOK_RULES.notional_usd, 'hold_seconds': EXIT.hold_ms // 1000,
                    'expected_net50_pct_per_trade': -3.6, 'automatic_promotion': AUTOMATIC_PROMOTION,
                    'profitability_proven': PROFITABILITY_PROVEN,
                    'universe': {key: self.refresh_stats.get(key) for key in ('events', 'universe', 'pools')},
                    'books': books}


def compact_close(row) -> dict:
    """A dashboard close (last 10 shown per book)."""
    guard = row.get('print_guard') or {}
    flags = [name for name in ('entry_outlier', 'exit_outlier', 'gain_capped') if guard.get(name)]
    if row.get('late_exit_restart'):
        flags.append('late_exit_restart')
    if row.get('plm_v1_would_block'):
        flags.append('plm_v1_would_block')
    return {'trade_no': row.get('trade_no'), 'seq': row.get('seq'), 'symbol': row.get('symbol'),
            'pairAddress': row.get('pairAddress'), 'closed_at': row.get('at'), 'kind': row.get('close_kind'),
            'pnl_usd': row.get('pnl_usd'), 'pnl_pct': row.get('pnl_pct'), 'net50_usd': row.get('net50_usd'),
            'net50_pct': row.get('net50_pct'), 'fee_bps': row.get('fee_bps'),
            'hold_s': (round((int(row['exit_fill_at']) - int(row['decision_at'])) / 1000, 1)
                       if row.get('exit_fill_at') and row.get('decision_at') else None),
            'flags': flags}


def kill_evaluation(rows, control_rows) -> dict:
    """HF_KILL_RULE_V1 statistical checkpoint of one hypothesis (all three conditions retire)."""
    params = lab_forward.KillRuleParameters(bootstrap_resamples=KILL_RULE.bootstrap_resamples,
                                            bootstrap_seed=KILL_RULE.bootstrap_seed)
    usd = [_finite(row.get('net50_usd')) for row in rows]
    usd = [value for value in usd if value is not None]
    mean_usd = _mean(usd)
    ci = lab_forward.bootstrap_ci(rows, key='net50_usd', params=params) if rows else {'low': None, 'high': None}
    pct = _mean(_finite(row.get('net50_pct')) for row in rows)
    control_pct = _mean(_finite(row.get('net50_pct')) for row in control_rows)
    control_ci = (lab_forward.bootstrap_ci(control_rows, key='net50_pct', params=params)
                  if control_rows else {'low': None, 'high': None})
    half = None
    if control_ci.get('low') is not None and control_ci.get('high') is not None:
        half = (control_ci['high'] - control_ci['low']) / 2
    gap = None if pct is None or control_pct is None else pct - control_pct
    within = {}
    for bucket in ('fee<=50', 'fee55-95'):
        a = [_finite(row.get('net50_pct')) for row in rows if _fee_bucket(row.get('fee_bps')) == bucket]
        b = [_finite(row.get('net50_pct')) for row in control_rows if _fee_bucket(row.get('fee_bps')) == bucket]
        if _mean(a) is not None and _mean(b) is not None:
            within[bucket] = round(_mean(a) - _mean(b), 4)
    met = bool(mean_usd is not None and mean_usd < KILL_RULE.max_mean_net50_usd_exclusive
               and ci.get('high') is not None and ci['high'] < KILL_RULE.max_ci95_high_usd_exclusive
               and gap is not None and half is not None and gap <= half)
    return {'version': KILL_RULE_VERSION, 'met': met, 'mean_net50_usd': _round(mean_usd),
            'ci95_net50_usd': [_round(ci.get('low')), _round(ci.get('high'))], 'ci_method': ci.get('method'),
            'mean_net50_pct': _round(pct), 'control_mean_net50_pct': _round(control_pct),
            'control_closes_same_period': len(control_rows), 'gap_net50_pct': _round(gap),
            'control_ci_half_width_pct': _round(half), 'within_fee_bucket_gap_pct': within}


def config_view(hashes=None, budget: BudgetParameters = DEFAULT_BUDGET, *, enabled_flag=True,
                cost_model_mismatches=None, budget_env_errors=None, retire_flags=None) -> dict:
    """Published definition (activity_config.lab_high_frequency)."""
    hashes = dict(CONFIG_HASHES if hashes is None else hashes)
    return {
        'version': VERSION, 'enabled': bool(enabled_flag), 'versions': dict(VERSIONS),
        'portfolio_group': PORTFOLIO_GROUP, 'book_ids': list(BOOK_IDS), 'names': dict(BOOK_NAMES),
        'controls': dict(CONTROL_OF), 'config_hashes': hashes,
        'config_hash_basis': ('sha256 of json.dumps(book_parameters(book_id), sort_keys=True, separators=(",", ":"), '
                              'ensure_ascii=True) (lab_forward_tests basis)'),
        'budget_hash': budget_hash(budget), 'budget': asdict(budget),
        'signals': {book_id: _drop_none(asdict(SIGNALS[book_id])) for book_id in BOOK_IDS},
        'notional_usd': BOOK_RULES.notional_usd, 'slots': BOOK_RULES.slots,
        'max_orders_per_hour': BOOK_RULES.governor_max_orders, 'hold_seconds': EXIT.hold_ms // 1000,
        'defensive': dict(DEFENSIVE_MODES),
        'cost_model_mismatches': list(cost_model_mismatches or []),
        'budget_env_errors': list(budget_env_errors or []), 'retire_flags': list(retire_flags or []),
        'research_source': RESEARCH_SOURCE, 'research_prereg_sha256': RESEARCH_PREREG_SHA256,
        'automatic_promotion': AUTOMATIC_PROMOTION, 'profitability_proven': PROFITABILITY_PROVEN,
        'expected_result': EXPECTED_RESULT, 'is_entry_authorization': False,
        'storage': 'strategy_lab_hf/ (journal/<BOOK>/<YYYY-MM-DD>.jsonl and state.json); never in strategy_lab.json',
        'time_budget': {'max_ms_per_loop': TIME_BUDGET_MS, 'consecutive_loops': TIME_BUDGET_LOOPS,
                        'action': 'hf_degraded: no new orders, exits continue; clears at the first loop within budget'},
    }
