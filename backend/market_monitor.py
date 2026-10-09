#!/usr/bin/env python3
import copy, hashlib, json, math, os, re, shutil, threading, time, uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
import requests
import engine_execution as paper_quotes
import engine_rug_guard as rug_guard
import engine_entry_policy as entry_policy
import gold_order_flow as order_flow
import pair_price_integrity as price_integrity
import pumpswap_stop_quote as pumpswap_stop
import engine_runtime as runtime
import engine_exit_policy as exit_policy
import training_bridge
import winner_ensemble
import order_flow_adaptive_oct4 as oct4
import cost_first_engine_profile as cost_first_profile
import entry_defense
import pool_loss_memory
import structural_rug_guard
import promoted_entry_guard as promoted_guard
import entry_size_backoff
import market_discovery
import honest_quote_transport as quote_transport
import paper_market_feasibility as market_feasibility
import entry_quote_priority
import funded_active_paper
import compat_file_lock as file_lock
from paper_training import (DEFAULT_CONFIG as TRAINING_DEFAULT_CONFIG, LEARNER_SCORE_VERSION as TRAINING_SCORE_VERSION,
                            training_candidate_signal)
from training_quote_probe import collect_exact_pool_quotes
from lab_dashboard_projection import compact_strategy_lab
from shared_snapshot_io import read_shared_text

HOST = os.getenv('NEO_MONITOR_HOST', '127.0.0.1')
PORT = int(os.getenv('NEO_MONITOR_PORT', '8788'))
SCAN_SECONDS = max(2, int(os.getenv('NEO_SCAN_SECONDS', '3')))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '0.5'))
STATE_PATH = Path(os.getenv('NEO_MARKET_STATE_PATH', '/var/lib/neo-market/state.json'))
AUDIT_PATH = Path(os.getenv('NEO_MARKET_AUDIT_PATH', '/var/lib/neo-market/audit.jsonl'))
LIVE_TAPE_PATH = Path(os.getenv('NEO_LIVE_TAPE_PATH', '/var/lib/neo-market/live_tape.json'))
STRATEGY_LAB_PATH = Path(os.getenv('NEO_STRATEGY_LAB_PATH', '/var/lib/neo-market/strategy_lab.json'))
STRATEGY_LAB_COMPACT_PATH = Path(os.getenv('NEO_STRATEGY_LAB_COMPACT_PATH', str(STRATEGY_LAB_PATH.parent / 'strategy_lab_compact.json')))
POSITION_SCAN_SECONDS = float(os.getenv('NEO_POSITION_SCAN_SECONDS', '0.5'))
DEX_API = 'https://api.dexscreener.com'
MAX_FEED = 90
TRAINING_QUOTE_PROBE_INTERVAL_MS = 60_000
TRAINING_PREFLIGHT_RETRY_MS = 15_000
ENTRY_SCORE = 60.0
MAX_POSITIONS = 8
STOP_LOSS_PCT = 5.0
STOP_EXECUTION_BUFFER_PCT = 0.5  # Planned risk allowance, never a fill clamp.
STOP_EXECUTION_ARM_NET_PCT = 5.0
EXIT_IMPACT_EMERGENCY_PCT = 0.75
# Forensics only (the exit rule itself is unchanged): the sell quote that fired
# EXIT_IMPACT_EMERGENCY, the confirming re-quote, the entry preflight impacts and
# liquidity at entry versus exit are recorded on every such close.
EXIT_IMPACT_EMERGENCY_ENTRY_MARGIN_PCT = 0.50
EXIT_IMPACT_EMERGENCY_FORENSICS_VERSION = 'EXIT_IMPACT_EMERGENCY_FORENSICS_V1'
# A cross-process entry-lease or exit-priority defer is not a quote: it neither
# consumes the per-scan quote budget nor arms the per-token retry cooldown.
QUOTE_PREPARATION_DEFER_CODES = frozenset({'ENTRY_SEQUENCE_BUSY', 'EXIT_PRIORITY_PENDING'})
QUOTE_PREPARATION_ROLLING_WINDOW_MS = 60 * 60_000  # rolling histogram covers the last 60 minutes
QUOTE_PREPARATION_ROLLING_MAX_SCANS = 4096          # memory bound on scans kept inside that window
QUOTE_PREPARATION_MAX_CODES = 32        # distinct codes per histogram; extras fold into OTHER
# Per-failure rows are diagnostics, not ledger evidence: they go to a size-capped
# sidecar next to AUDIT_PATH (plain append, no fsync, never under STATE.lock), at
# most one row per (pair, code) per QUOTE_RETRY_COOLDOWN_MS and a few per scan.
# audit.jsonl receives no per-failure rows; the in-memory histograms stay complete.
QUOTE_PREPARATION_LOG_NAME = 'quote_preparation.jsonl'
QUOTE_PREPARATION_LOG_MAX_BYTES = 5 * 1024 * 1024
QUOTE_PREPARATION_LOG_PER_SCAN = 12
QUOTE_PREPARATION_LOG_RATE_KEYS = 4096
QUOTE_PREPARATION_LOG_LOCK = threading.Lock()
TAKE_PROFIT_PCT = 10.0
TRAILING_PCT = 4.0
MAX_HOLD_MINUTES = 60
WEAK_CHECK_MINUTES = 5
STALE_EXIT_MINUTES = 7
STALE_MIN_PROFIT_PCT = 3.0
LEARNING_WINDOW = 60
HEALTH_WINDOW = 12
STARTING_BALANCE_USD = 1000.0
# Market score model of score_pair. V2 (2026-10-08): the +9 'good liquidity/MC'
# bonus applies only for 0.15 <= liq/MC < 0.6; liq/MC >= 1 is an LP-pull risk
# (-10). Research: LP-pullable pools scored 86.7 on average and sat at SETUP on
# 96.9% of rows under the unbounded V1 bonus.
SCORE_VERSION = 'NEO_MARKET_SCORE_V2_LIQ_MC_BAND'
PREVIOUS_SCORE_VERSION = 'NEO_MARKET_SCORE_V1'
SCORE_GOOD_LIQ_MC_RANGE = (0.15, 0.6)
SCORE_LP_RISK_LIQ_MC = 1.0
# V2 is an entry change only. The ORDER_FLOW_ADAPTIVE exit context of a held
# position (conviction -> CONVICTION_EXIT/PROFIT_LOCK, hold mode, max hold)
# and the entry hold mode its blind-flow fallback keeps read the V1 score
# (coin scoreV1), so exits under GOLD_ADAPTIVE_NET_CANDIDATE_V1 are unchanged
# for positions opened before and after this change. The PAPER_TRAINING_V1
# learners stay on V1 as well: their recorded context (GOLD_ADAPTIVE exits)
# comes from Monitor.training_context and their min_score reads scoreV1
# (paper_training.LEARNER_SCORE_VERSION).
EXIT_CONTEXT_SCORE_VERSION = PREVIOUS_SCORE_VERSION
# RugCheck/price prewarm population (see Monitor.prewarm_entry_checks).
PREWARM_VERSION = 'PREWARM_V2_DEFENSIVE_POPULATION'
PREWARM_MAX_CANDIDATES = 4
TRADE_NOTIONAL_USD = float(os.getenv('NEO_TRADE_NOTIONAL_USD', '200'))
MAX_DAILY_LOSS_USD = float(os.getenv('NEO_MAX_DAILY_LOSS_USD', '100'))
MAX_POSITION_RISK_USD = float(os.getenv('NEO_MAX_POSITION_RISK_USD', '250'))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv('NEO_MAX_TOTAL_EXPOSURE_PCT', '100'))
MAX_DRAWDOWN_PCT = float(os.getenv('NEO_MAX_DRAWDOWN_PCT', '20'))
MIN_LIQUIDITY_USD = 10000.0

# One validated effective threshold object governs every EARLY signal call.
STRICT_ENTRY_SCORE = float(os.getenv('NEO_STRICT_ENTRY_SCORE', '58'))
STRICT_MIN_CONVICTION = float(os.getenv('NEO_STRICT_MIN_CONVICTION', '30'))
STRICT_MIN_LIQUIDITY_USD = float(os.getenv('NEO_STRICT_MIN_LIQUIDITY_USD', '4000'))
STRICT_MAX_ENTRY_IMPACT_PCT = float(os.getenv('NEO_STRICT_MAX_ENTRY_IMPACT_PCT', '1.75'))
POLICY_MAX_ROUNDTRIP_COST_PCT = 1.5
POLICY_MAX_WORST_CASE_COST_PCT = 2.5
STRICT_MAX_ROUNDTRIP_COST_PCT = min(
    float(os.getenv('NEO_STRICT_MAX_ROUNDTRIP_COST_PCT', str(POLICY_MAX_ROUNDTRIP_COST_PCT))),
    POLICY_MAX_ROUNDTRIP_COST_PCT,
    STOP_LOSS_PCT * promoted_guard.MAX_STOP_BUDGET_COST_FRACTION,
)
STRICT_MAX_WORST_CASE_COST_PCT = min(
    float(os.getenv('NEO_STRICT_MAX_WORST_CASE_COST_PCT', str(POLICY_MAX_WORST_CASE_COST_PCT))),
    POLICY_MAX_WORST_CASE_COST_PCT,
    STOP_LOSS_PCT * promoted_guard.MAX_STOP_BUDGET_COST_FRACTION,
)
EFFECTIVE_ENTRY_THRESHOLDS = order_flow.EntryThresholds(STRICT_ENTRY_SCORE, STRICT_MIN_LIQUIDITY_USD, STRICT_MIN_CONVICTION)
for _threshold in (STRICT_MAX_ENTRY_IMPACT_PCT, STRICT_MAX_ROUNDTRIP_COST_PCT, STRICT_MAX_WORST_CASE_COST_PCT,
                   TRADE_NOTIONAL_USD, MAX_DAILY_LOSS_USD, POSITION_SCAN_SECONDS, MAX_POSITION_RISK_USD,
                   MAX_TOTAL_EXPOSURE_PCT, MAX_DRAWDOWN_PCT):
    if not math.isfinite(_threshold) or _threshold < 0: raise ValueError('invalid PAPER configuration')
if TRADE_NOTIONAL_USD <= 0 or POSITION_SCAN_SECONDS <= 0: raise ValueError('invalid PAPER configuration')
if MAX_POSITION_RISK_USD <= 0 or not 0 < MAX_TOTAL_EXPOSURE_PCT <= 100 or not 0 <= MAX_DRAWDOWN_PCT <= 100:
    raise ValueError('invalid PAPER risk limits')

# Explicit strategy selection. The ensemble stays the default production strategy.
# ORDER_FLOW_ADAPTIVE is an opt-in per-engine profile (NEO_SIGNAL_STRATEGY, set per
# account by the user gateway) whose effective values are strategy-owned and never
# environment-tuned, so code, strategy lock and runtime cannot drift apart silently.
# COST_FIRST_ESTABLISHED_PAPER_V1 is a second opt-in profile: the cost-first universe
# and EXIT_IMPACT_EMERGENCY_V2 on the engine's own gates, sizes and risk limits.
DEFAULT_SIGNAL_STRATEGY = winner_ensemble.VERSION
SUPPORTED_SIGNAL_STRATEGIES = (winner_ensemble.VERSION, oct4.STRATEGY_ID, cost_first_profile.STRATEGY_ID)
SAME_TOKEN_COOLDOWN_SECONDS = 1200
_ENGINE_DEFAULTS = {
    'SCAN_SECONDS': SCAN_SECONDS, 'POSITION_SCAN_SECONDS': POSITION_SCAN_SECONDS,
    'MAX_POSITIONS': MAX_POSITIONS, 'TRADE_NOTIONAL_USD': TRADE_NOTIONAL_USD,
    'MAX_DAILY_LOSS_USD': MAX_DAILY_LOSS_USD, 'STRICT_ENTRY_SCORE': STRICT_ENTRY_SCORE,
    'STRICT_MIN_CONVICTION': STRICT_MIN_CONVICTION, 'STRICT_MIN_LIQUIDITY_USD': STRICT_MIN_LIQUIDITY_USD,
    'STRICT_MAX_ENTRY_IMPACT_PCT': STRICT_MAX_ENTRY_IMPACT_PCT,
    'STRICT_MAX_ROUNDTRIP_COST_PCT': STRICT_MAX_ROUNDTRIP_COST_PCT,
    'STRICT_MAX_WORST_CASE_COST_PCT': STRICT_MAX_WORST_CASE_COST_PCT,
    'EFFECTIVE_ENTRY_THRESHOLDS': EFFECTIVE_ENTRY_THRESHOLDS,
    'MAX_QUOTED_CANDIDATES': entry_policy.MAX_QUOTED_CANDIDATES,
}
SIGNAL_STRATEGY = DEFAULT_SIGNAL_STRATEGY
ADAPTIVE_PROFILE = None
COST_FIRST_ACTIVE = False
ENTRY_POLICY_VERSION = winner_ensemble.ENTRY_POLICY_VERSION
POSITION_EXIT_POLICY = 'fixed'
MAX_QUOTED_CANDIDATES = entry_policy.MAX_QUOTED_CANDIDATES


def activate_strategy(strategy_id: str) -> str:
    """Select the engine's signal strategy; unknown identifiers fail closed at startup."""
    global SIGNAL_STRATEGY, ADAPTIVE_PROFILE, ENTRY_POLICY_VERSION, POSITION_EXIT_POLICY, COST_FIRST_ACTIVE
    global SCAN_SECONDS, POSITION_SCAN_SECONDS, MAX_POSITIONS, TRADE_NOTIONAL_USD, MAX_DAILY_LOSS_USD
    global STRICT_ENTRY_SCORE, STRICT_MIN_CONVICTION, STRICT_MIN_LIQUIDITY_USD, STRICT_MAX_ENTRY_IMPACT_PCT
    global STRICT_MAX_ROUNDTRIP_COST_PCT, STRICT_MAX_WORST_CASE_COST_PCT, EFFECTIVE_ENTRY_THRESHOLDS
    global MAX_QUOTED_CANDIDATES
    strategy_id = str(strategy_id or '').strip() or DEFAULT_SIGNAL_STRATEGY
    if strategy_id not in SUPPORTED_SIGNAL_STRATEGIES:
        raise ValueError(f'Unsupported NEO_SIGNAL_STRATEGY {strategy_id!r}; supported: {SUPPORTED_SIGNAL_STRATEGIES}')
    if strategy_id == oct4.STRATEGY_ID:
        profile = oct4.PROFILE
        if profile.stop_loss_pct != STOP_LOSS_PCT:
            raise ValueError('ORDER_FLOW_ADAPTIVE stop must equal the engine net stop')
        COST_FIRST_ACTIVE = False
        ADAPTIVE_PROFILE = profile
        ENTRY_POLICY_VERSION = oct4.ENTRY_POLICY_VERSION
        POSITION_EXIT_POLICY = 'adaptive'
        SCAN_SECONDS = int(profile.scan_seconds)
        POSITION_SCAN_SECONDS = float(profile.position_scan_seconds)
        MAX_POSITIONS = int(profile.max_positions)
        TRADE_NOTIONAL_USD = float(profile.trade_notional_usd)
        MAX_DAILY_LOSS_USD = float(profile.max_daily_loss_usd)
        STRICT_ENTRY_SCORE = float(profile.strict_entry_score)
        STRICT_MIN_CONVICTION = float(profile.strict_min_conviction)
        STRICT_MIN_LIQUIDITY_USD = float(profile.strict_min_liquidity_usd)
        STRICT_MAX_ENTRY_IMPACT_PCT = float(profile.strict_max_entry_impact_pct)
        STRICT_MAX_ROUNDTRIP_COST_PCT = float(profile.strict_max_roundtrip_cost_pct)
        STRICT_MAX_WORST_CASE_COST_PCT = float(profile.strict_max_worst_case_cost_pct)
        EFFECTIVE_ENTRY_THRESHOLDS = order_flow.EntryThresholds(
            STRICT_ENTRY_SCORE, STRICT_MIN_LIQUIDITY_USD, STRICT_MIN_CONVICTION)
        MAX_QUOTED_CANDIDATES = int(profile.max_quoted_candidates)
    elif strategy_id == cost_first_profile.STRATEGY_ID:
        # Universe, size rule and exit policy are profile-owned; every risk limit,
        # cost cap, cadence and the net stop stay at the engine defaults.
        if (cost_first_profile.EIE_V2.floor_pct != EXIT_IMPACT_EMERGENCY_PCT
                or cost_first_profile.EIE_V2.entry_margin_pct != EXIT_IMPACT_EMERGENCY_ENTRY_MARGIN_PCT):
            raise ValueError('EXIT_IMPACT_EMERGENCY_V2 must keep the engine floor and margin')
        ADAPTIVE_PROFILE = None
        COST_FIRST_ACTIVE = True
        ENTRY_POLICY_VERSION = cost_first_profile.ENTRY_POLICY_VERSION
        POSITION_EXIT_POLICY = cost_first_profile.EXIT_POLICY
        for name, value in _ENGINE_DEFAULTS.items():
            globals()[name] = value
    else:
        ADAPTIVE_PROFILE = None
        COST_FIRST_ACTIVE = False
        ENTRY_POLICY_VERSION = winner_ensemble.ENTRY_POLICY_VERSION
        POSITION_EXIT_POLICY = 'fixed'
        for name, value in _ENGINE_DEFAULTS.items():
            globals()[name] = value
    SIGNAL_STRATEGY = strategy_id
    return SIGNAL_STRATEGY


def market_candidate(coin: dict[str, Any], now: int | None = None, ticker_registry=None) -> bool:
    """Market-only candidate screen of the active strategy; flow and conviction follow.

    The cost-first universe includes STRUCTURAL_RUG_GUARD_V1, which needs the
    engine's ticker registry (Monitor.defense.registry) and fails closed
    without it.
    """
    if ADAPTIVE_PROFILE is not None:
        return oct4.is_market_candidate(coin, now=now_ms() if now is None else now, profile=ADAPTIVE_PROFILE)
    if COST_FIRST_ACTIVE:
        return cost_first_profile.is_market_candidate(
            coin, TRADE_NOTIONAL_USD, now=now_ms() if now is None else now, ticker_registry=ticker_registry)
    return bool(winner_ensemble.market_candidates(coin))


def strategy_rule_config() -> dict[str, Any]:
    if ADAPTIVE_PROFILE is not None:
        return oct4.effective_entry_thresholds(ADAPTIVE_PROFILE)
    if COST_FIRST_ACTIVE:
        return {cost_first_profile.STRATEGY_ID: cost_first_profile.universe_parameters()}
    return winner_ensemble.rule_config()


def shared_feed_candidate(coin: dict[str, Any], now: int, ticker_registry=None) -> bool:
    """Retain the funded Lab's universe too, not just main's entry candidates.

    Candidate retention never authorizes a fill or bypasses the Lab's own
    capital, confirmed flow, defense, safety, price and execution cost gates.
    Main's quote preparation still uses market_candidate(), not this union.
    """
    return (market_candidate(coin, now, ticker_registry) or
            (funded_active_paper.quality_enabled() and
             any(funded_active_paper.matches(sid, coin) for sid in funded_active_paper.RULES)))


activate_strategy(os.getenv('NEO_SIGNAL_STRATEGY', DEFAULT_SIGNAL_STRATEGY))

# Legacy deterministic PAPER friction model; quote-backed positions use the
# versioned engine_execution adapter. PumpSwap canonical fee tiers mirror pump.fun fees
# published 2026-05-20. Non-PumpSwap pools use the conservative fallback below.
GENERIC_DEX_FEE_BPS = float(os.getenv('NEO_EXEC_GENERIC_DEX_FEE_BPS', '30'))
BASE_SLIPPAGE_BPS = float(os.getenv('NEO_EXEC_BASE_SLIPPAGE_BPS', '10'))
LATENCY_BUFFER_BPS = float(os.getenv('NEO_EXEC_LATENCY_BUFFER_BPS', '10'))
NETWORK_FEE_SOL = float(os.getenv('NEO_EXEC_NETWORK_FEE_SOL', '0.0001'))
MAX_PRICE_IMPACT_PCT = float(os.getenv('NEO_EXEC_MAX_PRICE_IMPACT_PCT', '20'))

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NEO-Meme-Market-Monitor/2.0', 'Accept': 'application/json'})
SOL_MINT = 'So11111111111111111111111111111111111111112'
_SOL_USD_CACHE = {'price': 0.0, 'ts': 0}
_SOL_USD_LOCK = threading.Lock()
GECKO_NEW_POOLS_URL = 'https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1'
GECKO_NEW_POOLS_TTL_MS = 15_000
_GECKO_NEW_POOLS_CACHE = {'ts': 0, 'pairs': []}
_GECKO_NEW_POOLS_LOCK = threading.Lock()
_PUMP_CATALOG = market_discovery.PoolCatalog(quote_transport.ROOT / 'pumpswap-discovery-catalog.json')

def now_ms() -> int:
    return int(time.time() * 1000)


def num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


SOLANA_ADDRESS_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def is_valid_solana_address(value: Any) -> bool:
    return isinstance(value, str) and bool(SOLANA_ADDRESS_RE.fullmatch(value.strip()))


def sol_usd_from_coin(coin: dict[str, Any]) -> float:
    price_usd = num(coin.get('priceUsd'))
    price_native = num(coin.get('priceNative'))
    if coin.get('quoteTokenAddress') not in (None, 'So11111111111111111111111111111111111111112'):
        return 0.0
    if price_usd > 0 and price_native > 0:
        return price_usd / price_native
    return 0.0


def pumpswap_fee_bps(coin: dict[str, Any]) -> float:
    if str(coin.get('dexId') or '').lower() != 'pumpswap':
        return GENERIC_DEX_FEE_BPS
    sol_usd = sol_usd_from_coin(coin)
    market_cap_usd = num(coin.get('marketCap') or coin.get('fdv'))
    if sol_usd <= 0 or market_cap_usd <= 0:
        return 125.0
    market_cap_sol = market_cap_usd / sol_usd
    tiers = (
        (420, 125.0), (1470, 120.0), (2460, 115.0), (3440, 110.0),
        (4420, 105.0), (9820, 100.0), (14740, 95.0), (19650, 90.0),
        (24560, 85.0), (29470, 80.0), (34380, 75.0), (39300, 70.0),
        (44210, 65.0), (49120, 60.0), (54030, 55.0), (58940, 52.5),
        (63860, 50.0), (68770, 47.5), (73681, 45.0), (78590, 42.5),
        (83500, 40.0), (88400, 37.5), (93330, 35.0), (98240, 32.5),
    )
    for max_mc_sol, fee_bps in tiers:
        if market_cap_sol < max_mc_sol:
            return fee_bps
    return 30.0


def execution_friction(coin: dict[str, Any], trade_value_usd: float) -> dict[str, float]:
    liquidity = max(num(coin.get('liquidityUsd')), 1.0)
    trade_value = max(0.0, trade_value_usd)
    # DexScreener liquidity is approximately both sides of the pool in USD.
    # For a constant-product AMM, quote-side reserve is roughly half of that,
    # so average fill impact is approximately trade_value / (liquidity / 2).
    impact_pct = min(MAX_PRICE_IMPACT_PCT, (2.0 * trade_value / liquidity) * 100.0)
    slippage_pct = BASE_SLIPPAGE_BPS / 100.0
    latency_pct = LATENCY_BUFFER_BPS / 100.0
    fee_bps = pumpswap_fee_bps(coin)
    network_fee_usd = NETWORK_FEE_SOL * sol_usd_from_coin(coin)
    return {
        'impact_pct': impact_pct,
        'slippage_pct': slippage_pct,
        'latency_pct': latency_pct,
        'total_price_penalty_pct': impact_pct + slippage_pct + latency_pct,
        'dex_fee_bps': fee_bps,
        'network_fee_usd': network_fee_usd,
    }


def entry_execution(coin: dict[str, Any], notional_usd: float) -> dict[str, float]:
    market_price = num(coin.get('priceUsd'))
    friction = execution_friction(coin, notional_usd)
    penalty = friction['total_price_penalty_pct'] / 100.0
    fill_price = market_price * (1.0 + penalty)
    dex_fee_usd = notional_usd * friction['dex_fee_bps'] / 10000.0
    token_budget_usd = max(0.0, notional_usd - dex_fee_usd)
    quantity = token_budget_usd / fill_price if fill_price > 0 else 0.0
    return {
        **friction,
        'market_price': market_price,
        'fill_price': fill_price,
        'dex_fee_usd': dex_fee_usd,
        'quantity': quantity,
        'capital_committed_usd': notional_usd + friction['network_fee_usd'],
    }


def exit_execution(coin: dict[str, Any], quantity: float) -> dict[str, float]:
    market_price = num(coin.get('priceUsd'))
    market_value_usd = max(0.0, quantity * market_price)
    friction = execution_friction(coin, market_value_usd)
    penalty = friction['total_price_penalty_pct'] / 100.0
    fill_price = max(0.0, market_price * (1.0 - penalty))
    gross_proceeds_usd = max(0.0, quantity * fill_price)
    dex_fee_usd = gross_proceeds_usd * friction['dex_fee_bps'] / 10000.0
    net_proceeds_usd = max(0.0, gross_proceeds_usd - dex_fee_usd - friction['network_fee_usd'])
    return {
        **friction,
        'market_price': market_price,
        'fill_price': fill_price,
        'market_value_usd': market_value_usd,
        'gross_proceeds_usd': gross_proceeds_usd,
        'dex_fee_usd': dex_fee_usd,
        'net_proceeds_usd': net_proceeds_usd,
    }


def enforce_paper_stop_cap(
    quote: dict[str, Any], notional: float, entry_cost: float, quantity: float
) -> tuple[dict[str, Any], float, float, bool]:
    """Compatibility entry point: return the observed modeled proceeds unchanged.

    Historical V8 silently invented proceeds through gaps. The name remains
    solely for older callers; there is no accounting cap in the repaired model.
    """
    pnl = num(quote.get('net_proceeds_usd')) - notional - entry_cost
    return quote, pnl, pnl / max(notional, 1e-18) * 100.0, False


def read_strategy_lab() -> dict[str, Any]:
    """Read the prebuilt compact Lab snapshot; full histories stay on disk."""
    try:
        data = json.loads(STRATEGY_LAB_COMPACT_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    try:
        data = json.loads(STRATEGY_LAB_PATH.read_text(encoding='utf-8'))
        return compact_strategy_lab(data)
    except Exception:
        return {'status': 'offline', 'books': {}, 'stats': {}}

def read_live_tape() -> dict[str, Any]:
    try:
        data = json.loads(read_shared_text(LIVE_TAPE_PATH, encoding='utf-8'))
        return data if isinstance(data, dict) else {'status': 'offline', 'events': []}
    except Exception:
        return {'status': 'offline', 'events': []}

def compact_public_trade(trade: dict[str, Any]) -> dict[str, Any]:
    fields = (
        'id', 'address', 'pairAddress', 'name', 'symbol', 'imageUrl',
        'entry_price', 'current_price', 'peak_price', 'execution_entry_price',
        'execution_exit_price', 'notional_usd', 'score', 'current_score',
        'opened_at', 'updated_at', 'closed_at', 'exit_price', 'exit_reason',
        'trade_no', 'session_id', 'pnl_usd', 'pnl_pct', 'balance_before',
        'balance_after', 'dex_url', 'strategy_id', 'strategy_matches', 'strategy_matches_at_entry',
        'signal_evidence', 'entry_policy_version',
        'exit_policy_version', 'signal_pnl_pct', 'entry_roundtrip_pnl_pct',
        'observed_exit_pnl_pct', 'observed_exit_pnl_usd', 'paper_stop_capped',
        'stop_execution_source',
        # Exit forensics (numbers only; raw provider quotes stay private).
        'entry_price_impact_pct', 'exit_price_impact_pct', 'entry_liquidity_usd',
        'exit_liquidity_usd', 'exit_route_matches_entry_pool', 'exit_impact_emergency',
    )
    return {field: trade.get(field) for field in fields if field in trade}


def abbreviate_address(value: Any) -> str:
    """Short pool/mint label for logs and audit rows; full addresses stay in fields."""
    text = str(value or '')
    return text if len(text) <= 12 else f'{text[:4]}…{text[-4:]}'


def _raw_quote_impact_pct(raw_quote: Any) -> float | None:
    """Percent impact from a stored raw provider quote (fraction string), else None."""
    try:
        value = float((raw_quote or {}).get('priceImpactPct')) * 100
    except (TypeError, ValueError, AttributeError):
        return None
    return value if math.isfinite(value) else None


def compact_exit_quote_evidence(quote: dict[str, Any]) -> dict[str, Any]:
    """Numbers and route pools of one sell quote; never the raw provider payload."""
    pools: list[str] = []
    route = quote.get('route')
    for leg in route if isinstance(route, list) else []:
        key = leg.get('ammKey') if isinstance(leg, dict) else None
        if key and key not in pools:
            pools.append(str(key))
    return {'impact_pct': num(quote.get('impact_pct')), 'quoted_at': quote.get('quoted_at'),
            'route_pools': pools, 'route_matches_entry_pool': quote.get('route_matches_entry_pool'),
            'from_cache': bool(quote.get('from_cache')), 'execution_source': quote.get('execution_source'),
            'net_proceeds_usd': quote.get('net_proceeds_usd'), 'context_slot': quote.get('context_slot'),
            'quote_age_ms': quote.get('quote_age_ms'), 'queue_ms': quote.get('queue_ms'),
            'http_ms': quote.get('http_ms')}


def exit_impact_emergency_record(position: dict[str, Any], quote: dict[str, Any], coin: dict[str, Any],
                                 threshold_pct: float, now: int, *, trigger_is_requote: bool = False) -> dict[str, Any]:
    """Evidence for one EXIT_IMPACT_EMERGENCY close. Fields only; the rule is unchanged."""
    entry_liquidity = position.get('entry_liquidity_usd')
    exit_liquidity = coin.get('liquidityUsd')
    ratio = (round(num(exit_liquidity) / num(entry_liquidity), 4)
             if num(entry_liquidity) > 0 and exit_liquidity is not None else None)
    return {
        'version': EXIT_IMPACT_EMERGENCY_FORENSICS_VERSION,
        'rule': 'exit_quote_impact_pct >= max(exit_impact_emergency_pct, entry_price_impact_pct + entry_margin_pct)',
        'rule_changed': False,
        'threshold_pct': round(float(threshold_pct), 6),
        'exit_impact_emergency_pct': EXIT_IMPACT_EMERGENCY_PCT,
        'entry_margin_pct': EXIT_IMPACT_EMERGENCY_ENTRY_MARGIN_PCT,
        'entry_preflight': {
            'buy_impact_pct': position.get('entry_price_impact_pct'),
            'preflight_buy_impact_pct': _raw_quote_impact_pct(position.get('preflight_buy_quote')),
            'preflight_sell_impact_pct': _raw_quote_impact_pct(position.get('preflight_sell_quote')),
            'buy_quoted_at': position.get('jupiter_entry_quote_at'),
            'entry_roundtrip_pnl_pct': position.get('entry_roundtrip_pnl_pct'),
        },
        'trigger_quote': compact_exit_quote_evidence(quote),
        'trigger_is_requote': trigger_is_requote,
        'confirming_quote': None,
        'confirming_quote_meets_threshold': None,
        'booked_quote': 'trigger',
        'liquidity': {'entry_usd': entry_liquidity, 'exit_usd': exit_liquidity,
                      'exit_to_entry_ratio': ratio, 'exit_observed_at': coin.get('updatedAt')},
        'triggered_at': now,
    }


def api(path: str) -> Any:
    response = SESSION.get(f'{DEX_API}{path}', timeout=15)
    response.raise_for_status()
    return response.json()


def catalog_provider_api(url: str) -> Any:
    # Only a fixed address-catalog endpoint calls this helper. Its prices and
    # observations never become market/flow evidence for account admission.
    if url != market_discovery.PAPRIKA_URL:
        raise ValueError('unsupported address catalog provider')
    response = SESSION.get(url, timeout=(1.0, 5.0))
    response.raise_for_status()
    return response.json()


def sol_usd_market_price() -> float:
    current = now_ms()
    with _SOL_USD_LOCK:
        cached_price = num(_SOL_USD_CACHE.get('price'))
        cached_at = int(_SOL_USD_CACHE.get('ts') or 0)
    if cached_price > 0 and 0 <= current - cached_at <= 60_000:
        return cached_price
    try:
        response = SESSION.get(f'{DEX_API}/latest/dex/tokens/{SOL_MINT}', timeout=(1.0, 3.0))
        response.raise_for_status()
        pairs = response.json().get('pairs') or []
        candidates = []
        for pair in pairs:
            base = pair.get('baseToken') or {}
            if pair.get('chainId') != 'solana' or base.get('address') != SOL_MINT:
                continue
            price = num(pair.get('priceUsd'))
            liquidity = num((pair.get('liquidity') or {}).get('usd'))
            if price > 0:
                candidates.append((liquidity, price))
        if candidates:
            price = max(candidates)[1]
            with _SOL_USD_LOCK:
                _SOL_USD_CACHE.update(price=price, ts=current)
            return price
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        pass
    return cached_price if cached_price > 0 else 0.0


def signal(kind: str, title: str, detail: str) -> dict[str, str]:
    return {'kind': kind, 'title': title, 'detail': detail}


class State:
    def __init__(self, load_state: bool = True) -> None:
        self.lock = threading.RLock()
        # True only after the ledger was read (or legitimately absent). Shutdown
        # paths must not persist a default account over an unread ledger.
        self.loaded = False
        self.running = True
        self.status = 'starting'
        self.message = 'Starting NEO live market monitor.'
        self.last_scan_at = 0
        self.scan_count = 0
        self.feed: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.position_market: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.price_history: dict[str, list[dict[str, Any]]] = {}
        self.source_status = {'dexscreener': 'starting'}
        self.demo_starting_balance_usd = STARTING_BALANCE_USD
        self.demo_balance_usd = STARTING_BALANCE_USD
        self.demo_started_at = now_ms()
        self.demo_session_id = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        self.trade_seq = 0
        self.pending_audit: list[dict[str, Any]] = []
        self.audit_status = 'ok'
        self.equity_peak_usd = STARTING_BALANCE_USD
        self.entry_diagnostics = {'status': 'starting', 'policy_version': ENTRY_POLICY_VERSION}
        # DEFENSIVE_ENTRY_LAYER_V1 status (ticker registry and pair history) published by
        # every scan, also while the account is paused (entry_diagnostics only refreshes
        # when entries are evaluated). In memory only; never part of the ledger.
        self.defensive_entry_layer: dict[str, Any] | None = None
        self.risk_day_key = time.strftime('%Y-%m-%d', time.gmtime())
        self.risk_day_start_balance_usd = STARTING_BALANCE_USD
        if load_state: self.load()

    def load(self) -> None:
        if not STATE_PATH.exists():
            self.loaded = True
            return
        try:
            data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('positions', []), list) or not isinstance(data.get('history', []), list):
                raise ValueError('invalid account schema')
            if int(data.get('schema_version') or 1) > 3:
                raise ValueError('unsupported future account schema')
            ids = [p.get('id') for p in data.get('positions', [])]
            if any(not i for i in ids) or len(ids) != len(set(ids)):
                raise ValueError('missing or duplicate open position identifiers')
            if not math.isfinite(float(data.get('demo_balance_usd', STARTING_BALANCE_USD))):
                raise ValueError('nonfinite balance')
            self.positions = data.get('positions', [])
            self.running = bool(data.get('running', True))
            self.status = str(data.get('status') or ('starting' if self.running else 'paused'))
            self.history = data.get('history', [])
            self.position_market = data.get('position_market', {})
            self.pending_audit = data.get('pending_audit', [])
            self.audit_status = 'pending' if self.pending_audit else 'ok'
            self.events = data.get('events', [])[-100:]
            self.demo_starting_balance_usd = num(data.get('demo_starting_balance_usd'), STARTING_BALANCE_USD)
            self.demo_balance_usd = num(data.get('demo_balance_usd'), self.demo_starting_balance_usd)
            self.equity_peak_usd = num(data.get('equity_peak_usd'), max(self.demo_starting_balance_usd,self.demo_balance_usd))
            self.demo_started_at = int(data.get('demo_started_at') or self.demo_started_at)
            self.demo_session_id = str(data.get('demo_session_id') or self.demo_session_id)
            self.trade_seq = int(data.get('trade_seq') or 0)
            today = time.strftime('%Y-%m-%d', time.gmtime())
            if str(data.get('risk_day_key') or '') == today:
                self.risk_day_key = today
                self.risk_day_start_balance_usd = num(
                    data.get('risk_day_start_balance_usd'), self.demo_balance_usd
                )
            else:
                self.risk_day_key = today
                self.risk_day_start_balance_usd = self.demo_balance_usd
            raw = data.get('price_history', {})
            if isinstance(raw, dict):
                self.price_history = {k: v[-480:] for k, v in raw.items() if isinstance(v, list)}
            self.loaded = True
        except Exception as exc:
            # Never boot a silently reset $1000 account from an unreadable file.
            raise RuntimeError('Account state is unreadable; refusing automatic reset') from exc

    def _persist(self) -> None:
        self.equity_peak_usd = max(self.equity_peak_usd, self.equity_usd())
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        keep = {c.get('address') for c in self.feed[:50]}
        keep |= {p.get('address') for p in self.positions}
        keep |= {t.get('address') for t in self.history[:100]}
        price_history = {k: v[-480:] for k, v in self.price_history.items() if k in keep}
        runtime.atomic_json(STATE_PATH,{
            'schema_version': 3,
            'running': self.running, 'status': self.status,
            'positions': self.positions,
            'position_market': self.position_market,
            'history': self.history,
            'pending_audit': self.pending_audit,
            'events': self.events[-100:],
            'demo_starting_balance_usd': self.demo_starting_balance_usd,
            'demo_balance_usd': self.demo_balance_usd,
            'equity_peak_usd': self.equity_peak_usd,
            'demo_started_at': self.demo_started_at,
            'demo_session_id': self.demo_session_id,
            'trade_seq': self.trade_seq,
            'risk_day_key': self.risk_day_key,
            'risk_day_start_balance_usd': self.risk_day_start_balance_usd,
            'price_history': price_history,
        })

    def save(self) -> None:
        """Commit the authoritative ledger before draining the durable audit outbox."""
        with self.lock:
            self._persist()
            if not self.pending_audit: return
            try:
                for row in list(self.pending_audit): append_audit(row['event'], row['payload'])
                old_pending = self.pending_audit
                self.pending_audit = []
                try: self._persist()
                except Exception:
                    self.pending_audit = old_pending
                    raise
                self.audit_status = 'ok'
            except Exception as exc:
                self.audit_status = 'pending:' + type(exc).__name__
                self.message = 'Audit write pending; ledger committed and retryable.'

    def commit(self, event, payload, **changes):
        """Atomic balance/position/history transition; failed state write rolls back memory."""
        with self.lock:
            previous = {key: getattr(self, key) for key in changes}
            previous_peak = self.equity_peak_usd
            previous_pending = self.pending_audit
            payload = dict(payload, event_id=f"{payload.get('session_id', self.demo_session_id)}:{event}:{payload.get('id', uuid.uuid4().hex)}")
            for key, value in changes.items(): setattr(self, key, value)
            self.pending_audit = previous_pending + [{'event': event, 'payload': payload}]
            try: self.save()
            except Exception:
                for key, value in previous.items(): setattr(self, key, value)
                self.pending_audit = previous_pending
                self.equity_peak_usd = previous_peak
                raise

    def reset_with_archive(self):
        """An explicit PAPER reset archives the full pre-reset state and audit first."""
        with self.lock:
            self.save()
            archive = STATE_PATH.parent / 'archives' / (str(now_ms()) + '-' + uuid.uuid4().hex[:8])
            archive.mkdir(parents=True, exist_ok=False)
            manifest = {'old_session_id': self.demo_session_id, 'created_at': now_ms(), 'files': []}
            for source in (STATE_PATH, AUDIT_PATH):
                if source.exists():
                    target = archive / source.name
                    shutil.copy2(source, target)
                    manifest['files'].append({'source': str(source), 'archive': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
            runtime.atomic_json(archive / 'manifest.json', manifest)
            new_session = time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
            self.commit('RESET', {'id': new_session, 'session_id': new_session, 'starting_balance_usd': STARTING_BALANCE_USD, 'archive': str(archive)},
                positions=[], history=[], events=[], price_history={}, position_market={},
                demo_starting_balance_usd=STARTING_BALANCE_USD, demo_balance_usd=STARTING_BALANCE_USD,
                demo_started_at=now_ms(), demo_session_id=new_session, trade_seq=0,
                equity_peak_usd=STARTING_BALANCE_USD,
                risk_day_key=time.strftime('%Y-%m-%d', time.gmtime()), risk_day_start_balance_usd=STARTING_BALANCE_USD,
                entry_diagnostics={'status': 'reset', 'policy_version': ENTRY_POLICY_VERSION})
            self.event(f'New PAPER session with ${STARTING_BALANCE_USD:.2f}; archive {archive.name}.')
            self.save()
            return str(archive)

    def event(self, text: str) -> None:
        self.events.insert(0, {'ts': now_ms(), 'text': text[:500]})
        self.events = self.events[:100]
        self.message = text[:500]

    def reserved_usd(self) -> float:
        return sum(num(p.get('capital_committed_usd'), num(p.get('notional_usd'))) for p in self.positions)

    def unrealized_pnl_usd(self) -> float:
        return sum(num(p.get('pnl_usd')) for p in self.positions)

    def available_balance_usd(self) -> float:
        return max(0.0, self.demo_balance_usd - self.reserved_usd())

    def equity_usd(self) -> float:
        return self.demo_balance_usd + self.unrealized_pnl_usd()

    def refresh_risk_day(self) -> None:
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if self.risk_day_key != today:
            self.risk_day_key = today
            self.risk_day_start_balance_usd = self.demo_balance_usd

    def risk_day_pnl(self) -> float:
        self.refresh_risk_day()
        return self.equity_usd() - self.risk_day_start_balance_usd

    def realized_today(self) -> float:
        day = time.strftime('%Y-%m-%d', time.gmtime())
        total = 0.0
        for trade in self.history:
            stamp = int(trade.get('closed_at', 0)) / 1000
            if stamp and time.strftime('%Y-%m-%d', time.gmtime(stamp)) == day:
                total += num(trade.get('pnl_usd'))
        return total

    def live_flow(self, address: str, seconds: int = 30, pair_address: str | None = None) -> dict[str, Any]:
        tape = read_live_tape()
        decision_at = now_ms()
        cutoff = decision_at - seconds * 1000
        coverage = (tape.get('pair_coverage') or {}).get(pair_address, {})
        quality = str(coverage.get('status') or 'UNKNOWN').upper()
        if not pair_address or num(coverage.get('complete_since_ms'), decision_at+1) > cutoff:
            quality = 'DEGRADED' if quality == 'COMPLETE' else 'UNKNOWN'
        tape_stamp = num(tape.get('updated_at'))
        pair_poll = num(coverage.get('last_poll_at'))
        available_rows = [e for e in tape.get('events', []) if e.get('address') == address
                and (not pair_address or e.get('pairAddress') == pair_address)
                and cutoff <= num(e.get('ts')) <= decision_at
                and 0 < num(e.get('available_at', e.get('ingested_at'))) <= decision_at]
        if any(e.get('quality_flags') or num(e.get('usd_amount')) <= 0 for e in available_rows):
            quality = 'DEGRADED'
        rows = []
        event_ids = set()
        for event in available_rows:
            if event.get('quality_flags') or num(event.get('usd_amount')) <= 0: continue
            event_id = event.get('event_id')
            direction = event.get('direction')
            if (event.get('confirmed_swap') is not True or not event.get('wallet')
                    or not isinstance(event_id, str) or not event_id
                    or direction not in {'BUY', 'SELL'}):
                quality = 'DEGRADED'
                continue
            event_at = num(event.get('event_time'), num(event.get('ts')))
            observed_at = num(event.get('observed_at'))
            available_at = num(event.get('available_at', event.get('ingested_at')))
            if not (cutoff <= event_at <= observed_at <= available_at <= decision_at):
                quality = 'DEGRADED'
                continue
            if event_id in event_ids:
                quality = 'DEGRADED'
                continue
            event_ids.add(event_id)
            rows.append(event)
        buys = [e for e in rows if e.get('direction') == 'BUY']
        sells = [e for e in rows if e.get('direction') == 'SELL']
        buy_usd = sum(num(e.get('usd_amount')) for e in buys)
        sell_usd = sum(num(e.get('usd_amount')) for e in sells)
        buy_counts: dict[str, int] = {}
        sell_counts: dict[str, int] = {}
        for e in buys:
            wallet = e.get('wallet')
            if wallet:
                buy_counts[wallet] = buy_counts.get(wallet, 0) + 1
        for e in sells:
            wallet = e.get('wallet')
            if wallet:
                sell_counts[wallet] = sell_counts.get(wallet, 0) + 1
        buyer_wallets = set(buy_counts)
        seller_wallets = set(sell_counts)
        repeat_buy_wallets = sum(1 for count in buy_counts.values() if count >= 2)
        whale_buy_usd = sum(num(e.get('usd_amount')) for e in buys if num(e.get('usd_amount')) >= 750)
        whale_sell_usd = sum(num(e.get('usd_amount')) for e in sells if num(e.get('usd_amount')) >= 750)
        verified_flow = None
        exact_complete_window = bool(
            seconds == promoted_guard.FLOW_WINDOW_MS // 1000
            and quality == 'COMPLETE'
            and str(coverage.get('status') or '').upper() == 'COMPLETE'
            and coverage.get('address') == address
            and coverage.get('pairAddress') == pair_address
            and 0 < num(coverage.get('complete_since_ms')) <= cutoff
            and 0 < pair_poll <= decision_at
            and decision_at - pair_poll <= promoted_guard.FLOW_MAX_AGE_MS
            and 0 < tape_stamp <= decision_at
            and decision_at - tape_stamp <= promoted_guard.FLOW_MAX_AGE_MS
        )
        if exact_complete_window:
            verified_flow = {
                'source': promoted_guard.FLOW_SOURCE,
                'coverage_status': 'COMPLETE',
                'window_ms': promoted_guard.FLOW_WINDOW_MS,
                'address': address,
                'pairAddress': pair_address,
                'window_at': tape_stamp,
                'latest_event_at': max([num(e.get('event_time'), num(e.get('ts'))) for e in rows] or [0]),
                'available_at': max([num(e.get('available_at', e.get('ingested_at'))) for e in rows] or [0]),
                'trades': len(rows),
                'unique_wallets': len({e.get('wallet') for e in rows if e.get('wallet')}),
                'buy_usd': round(buy_usd, 2),
                'sell_usd': round(sell_usd, 2),
            }
        return {
            'quality': quality, 'coverage': coverage, 'decision_at': decision_at,
            'fresh': quality == 'COMPLETE',
            'latest_at': max([num(e.get('available_at', e.get('ingested_at'))) for e in rows] or [0]),
            'seconds': seconds, 'trades': len(rows), 'buys': len(buys), 'sells': len(sells),
            'buy_usd': round(buy_usd, 2), 'sell_usd': round(sell_usd, 2),
            'net_buy_usd': round(buy_usd - sell_usd, 2),
            'buy_sell_usd_ratio': round(buy_usd / max(sell_usd, 1.0), 2),
            'unique_wallets': len(buyer_wallets | seller_wallets),
            'buyer_wallets': len(buyer_wallets), 'seller_wallets': len(seller_wallets),
            'wallet_buy_sell_ratio': round(len(buyer_wallets) / max(len(seller_wallets), 1), 2),
            'repeat_buy_wallets': repeat_buy_wallets,
            'whale_buy_usd': round(whale_buy_usd, 2), 'whale_sell_usd': round(whale_sell_usd, 2),
            'max_buy_usd': round(max([num(e.get('usd_amount')) for e in buys] or [0]), 2),
            'max_sell_usd': round(max([num(e.get('usd_amount')) for e in sells] or [0]), 2),
            'verified_flow': verified_flow,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            lifetime = trade_metrics(self.history)
            wins, closed = lifetime['wins'], lifetime['closed_trades']
            closed_total = closed
            tape = read_live_tape()
            return {
                'running': self.running,
                'status': self.status,
                'message': self.message,
                'last_scan_at': self.last_scan_at,
                'scan_count': self.scan_count,
                'feed': self.feed,
                'positions': self.positions,
                'history': [compact_public_trade(t) for t in self.history[:100]],
                'events': self.events[:30],
                'source_status': self.source_status,
                'entry_diagnostics': self.entry_diagnostics,
                # Refreshed by every scan, paused or running (ticker registry coverage, seeds).
                'defensive_entry_layer': self.defensive_entry_layer,
                'live_tape': [],
                'live_tape_status': {k: tape.get(k) for k in ('status','tracked_pairs','updated_at','source','error','entry_scheduling')},
            'strategy_lab': read_strategy_lab(),
            'strategy_learning': winner_ensemble.learning_snapshot(self.history),
                'paper_training': training_bridge.snapshot(),
                'stats': {
                    'feed_count': len(self.feed),
                    'open_positions': len(self.positions),
                    'closed_trades': closed_total,
                    'wins': wins,
                    'win_rate': round((wins / closed) * 100, 1) if closed else 0,
                    'metrics': {'lifetime': lifetime,
                        'session': trade_metrics([t for t in self.history if t.get('session_id') == self.demo_session_id]),
                        'rolling_100': trade_metrics(self.history[:100]),
                        'policy_versions': {v: trade_metrics([t for t in self.history if str(t.get('exit_policy_version') or 'legacy_unknown') == v])
                            for v in {str(t.get('exit_policy_version') or 'legacy_unknown') for t in self.history}}},
                    'historical_records_missing': max(0, self.trade_seq - len(self.positions) - len(self.history)),
                    'audit_status': self.audit_status,
                    'unavailable_liquidation_positions': sum(1 for p in self.positions if p.get('valuation_status') == 'unavailable'),
                    'conservative_open_risk_usd': sum(num(p.get('conservative_risk_usd'), num(p.get('capital_committed_usd'))) for p in self.positions),
                    'drawdown_pct': max(0,1-self.equity_usd()/max(self.equity_peak_usd,1))*100,
                    'realized_today_usd': round(self.realized_today(), 2),
                    'risk_day_pnl_usd': round(self.risk_day_pnl(), 2),
                    'daily_risk_remaining_usd': (round(max(0.,MAX_DAILY_LOSS_USD+self.risk_day_pnl()),4) if MAX_DAILY_LOSS_USD > 0 else None),
                    'daily_risk_cap_enabled': MAX_DAILY_LOSS_USD > 0,
                    'risk_day_start_balance_usd': round(self.risk_day_start_balance_usd, 2),
                    'demo_starting_balance_usd': round(self.demo_starting_balance_usd, 2),
                    'demo_balance_usd': round(self.demo_balance_usd, 2),
                    'demo_equity_usd': round(self.equity_usd(), 2),
                    'demo_available_usd': round(self.available_balance_usd(), 2),
                    'demo_reserved_usd': round(self.reserved_usd(), 2),
                    'unrealized_pnl_usd': round(self.unrealized_pnl_usd(), 2),
                    'realized_total_usd': round(self.demo_balance_usd - self.demo_starting_balance_usd, 2),
                    'return_pct': round(((self.equity_usd() - self.demo_starting_balance_usd) / max(self.demo_starting_balance_usd, 1)) * 100, 3),
                    'demo_started_at': self.demo_started_at,
                    'demo_session_id': self.demo_session_id,
                },
                'config': strategy_config_overlay({
                    'scan_seconds': SCAN_SECONDS,
                    'position_scan_seconds': POSITION_SCAN_SECONDS,
                    'entry_score': STRICT_ENTRY_SCORE if ADAPTIVE_PROFILE is not None else winner_ensemble.MIN_SCORE,
                    'max_positions': MAX_POSITIONS,
                    'stop_loss_pct': STOP_LOSS_PCT,
                    'stop_loss_basis': 'EXECUTABLE_NET_PNL',
                    'stop_trigger_net_pct': -STOP_LOSS_PCT,
                    'take_profit_basis': 'EXECUTABLE_NET_PNL',
                    'reentry_seconds': SAME_TOKEN_COOLDOWN_SECONDS, 'loss_reentry_seconds': SAME_TOKEN_COOLDOWN_SECONDS,
                    'same_token_cooldown_seconds': SAME_TOKEN_COOLDOWN_SECONDS,
                    'signal_strategy': SIGNAL_STRATEGY,
                    'ensemble_strategies': [oct4.STRATEGY_ID] if ADAPTIVE_PROFILE is not None else list(winner_ensemble.STRATEGIES),
                    'signal_source_commit': oct4.SOURCE_COMMIT if ADAPTIVE_PROFILE is not None else winner_ensemble.VERSION,
                    'learning_mode': oct4.LEARNING_MODE if ADAPTIVE_PROFILE is not None else 'SAME_POLICY_PAPER_OUTCOME_THROTTLE_V1',
                    'strategy_profile': oct4.config_snapshot(ADAPTIVE_PROFILE) if ADAPTIVE_PROFILE is not None else None,
                    'config_ownership': config_ownership(),
                    'risk_overlay': 'PLANNED_NET_STOP_NO_FILL_GUARANTEE',
                    'execution_verification_version': 'QUOTE_EVIDENCE_V9',
                    'rug_guard': rug_guard.VERSION,
                    'defensive_entry': dict(entry_defense.VERSIONS),
                    'score_version': SCORE_VERSION,
                    'exit_context_score_version': EXIT_CONTEXT_SCORE_VERSION,
                    # PAPER_TRAINING_V1 learners: min_score and recorded context conviction.
                    'training_score_version': TRAINING_SCORE_VERSION,
                    'prewarm_version': PREWARM_VERSION,
                    'paper_only': True,
                    'runtime_version': runtime.VERSION,
                    'daily_budget_sizing': True,
                    'stop_execution_buffer_pct': STOP_EXECUTION_BUFFER_PCT,
                    'exit_impact_emergency_pct': EXIT_IMPACT_EMERGENCY_PCT,
                    'take_profit_pct': TAKE_PROFIT_PCT,
                    'trailing_pct': TRAILING_PCT,
                    'max_hold_minutes': MAX_HOLD_MINUTES,
                    'min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD if ADAPTIVE_PROFILE is not None else winner_ensemble.MIN_LIQUIDITY_USD,
                    'trade_notional_usd': TRADE_NOTIONAL_USD,
                    'max_daily_loss_usd': MAX_DAILY_LOSS_USD,
                    'max_position_full_loss_risk_usd': MAX_POSITION_RISK_USD,
                    'max_total_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,
                    'max_drawdown_pct': MAX_DRAWDOWN_PCT,
                    'drawdown_cap_enabled': MAX_DRAWDOWN_PCT > 0,
                    'daily_loss_cap_enabled': MAX_DAILY_LOSS_USD > 0,
                    'starting_balance_usd': STARTING_BALANCE_USD,
                    'execution_mode': 'PAPER_QUOTE_OR_OBSERVED_POOL_MODEL',
                    'execution_note': (oct4.EXECUTION_NOTE if ADAPTIVE_PROFILE is not None else
                                       'PAPER only; broad verified-flow candidate rule plus four selective rules share one account. Exact-pool quotes and fees are modeled, not executed fills; gaps and unsellable losses remain possible.'),
                    'entry_policy_version': ENTRY_POLICY_VERSION,
                    'exit_policy': POSITION_EXIT_POLICY, 'exit_policy_version': exit_policy_version(),
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': strategy_rule_config(),
                    'max_quoted_candidates_per_scan': MAX_QUOTED_CANDIDATES,
                    'max_quote_attempts_per_scan': MAX_QUOTED_CANDIDATES,
                    'quote_size_backoff_version': 'FLAT_NOTIONAL_NO_BACKOFF' if ADAPTIVE_PROFILE is not None else entry_size_backoff.VERSION,
                    'quote_size_backoff_min_usd': TRADE_NOTIONAL_USD if ADAPTIVE_PROFILE is not None else entry_size_backoff.MIN_ENTRY_NOTIONAL_USD,
                    'quote_size_backoff_attempts': 1 if ADAPTIVE_PROFILE is not None else entry_size_backoff.MAX_SIZE_ATTEMPTS,
                    'quote_retry_cooldown_ms': entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS,
                    'strict_entry_score': STRICT_ENTRY_SCORE,
                    'strict_min_conviction': STRICT_MIN_CONVICTION,
                    'strict_min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD,
                    'strict_max_entry_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT,
                    'strict_max_roundtrip_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
                    'strict_max_worst_case_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
                    'verified_entry_policy': {
                        **promoted_guard.policy_config(STOP_LOSS_PCT),
                        'maximum_roundtrip_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
                        'maximum_worst_case_roundtrip_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
                    },
                    'jupiter_slippage_bps': paper_quotes.SLIPPAGE_BPS,
                    'generic_dex_fee_bps': GENERIC_DEX_FEE_BPS,
                    'base_slippage_bps': BASE_SLIPPAGE_BPS,
                    'latency_buffer_bps': LATENCY_BUFFER_BPS,
                    'network_fee_sol_per_leg': NETWORK_FEE_SOL,
                    'max_price_impact_pct': MAX_PRICE_IMPACT_PCT,
                }),
            }
    def token_snapshot(self, address: str) -> dict[str, Any] | None:
        with self.lock:
            coin = next((c for c in self.feed if c.get('address') == address), None)
            if not coin:
                trade = next((t for t in self.history if t.get('address') == address), None)
                if trade:
                    coin = trade.get('coin_snapshot')
            if not coin:
                return None
            return {
                'coin': coin,
                'history': self.price_history.get(address, [])[-480:],
                'position': next((p for p in self.positions if p.get('address') == address), None),
                'trades': [t for t in self.history if t.get('address') == address][:20],
                'live_tape': [e for e in read_live_tape().get('events', []) if e.get('address') == address][:80],
                'flow': self.live_flow(address, 60),
            }


def append_audit(event: str, payload: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {'schema_version': 3, 'ts': now_ms(), 'event': event, 'session_id': STATE.demo_session_id, **payload}
    event_id = record.get('event_id')
    if event_id and AUDIT_PATH.exists():
        # Repair incomplete final append after an interruption, then deduplicate
        # the outbox retry by the transaction ID committed with account state.
        with AUDIT_PATH.open('rb+') as handle:
            content = handle.read()
            if content and not content.endswith(b'\n'):
                tail_start = content.rfind(b'\n') + 1
                try:
                    json.loads(content[tail_start:])
                    handle.seek(0, 2); handle.write(b'\n'); handle.flush(); os.fsync(handle.fileno())
                except (ValueError, UnicodeDecodeError):
                    handle.truncate(tail_start); handle.flush(); os.fsync(handle.fileno())
        for line in AUDIT_PATH.read_text(encoding='utf-8').splitlines():
            if json.loads(line).get('event_id') == event_id: return
    with AUDIT_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        handle.flush(); os.fsync(handle.fileno())


def quote_preparation_log_path() -> Path:
    return AUDIT_PATH.with_name(QUOTE_PREPARATION_LOG_NAME)


def _compact_quote_preparation_log(path: Path, keep_bytes: int) -> None:
    """Keep only the newest whole rows within keep_bytes (atomic replace)."""
    with path.open('rb') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - max(0, keep_bytes))
        handle.seek(start)
        tail = handle.read()
    if start > 0:
        newline = tail.find(b'\n')
        tail = tail[newline + 1:] if newline >= 0 else b''
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(tail)
    os.replace(temp, path)


def append_quote_preparation_log(payload: dict[str, Any]) -> bool:
    """Append one diagnostics row to the capped sidecar; returns False when dropped.

    Deliberately not durable: no fsync, no event_id dedupe and no STATE.lock, so a
    burst of failed quote preparations never slows ledger commits."""
    record = {'schema_version': 1, 'ts': now_ms(), 'event': 'QUOTE_PREPARATION_FAILED',
              'session_id': STATE.demo_session_id, **payload}
    line = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    cap = int(QUOTE_PREPARATION_LOG_MAX_BYTES)
    if len(line) > cap // 2:
        return False
    path = quote_preparation_log_path()
    with QUOTE_PREPARATION_LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(line) > cap:
            _compact_quote_preparation_log(path, cap // 2)
        with path.open('ab') as handle:
            handle.write(line)
    return True


def trade_metrics(trades):
    known = [t for t in trades if isinstance(t.get('pnl_usd'), (int, float)) and math.isfinite(t['pnl_usd'])]
    wins = sum(t['pnl_usd'] > 0 for t in known)
    loss = -sum(min(0., t['pnl_usd']) for t in known)
    gain = sum(max(0., t['pnl_usd']) for t in known)
    return {'closed_trades': len(known), 'wins': wins, 'losses': sum(t['pnl_usd'] < 0 for t in known),
            'breakeven': sum(t['pnl_usd'] == 0 for t in known), 'unknown_results': len(trades)-len(known),
            'win_rate': round(100*wins/len(known), 4) if known else None,
            'net_pnl_usd': round(gain-loss, 8), 'profit_factor': gain/loss if loss else None,
            'profit_factor_status': 'defined' if loss else ('infinite_no_losses' if gain else 'undefined_no_losses')}


def effective_config_hash():
    config = {'entry_version': ENTRY_POLICY_VERSION,
              'ensemble_version': winner_ensemble.VERSION, 'ensemble_rules': winner_ensemble.rule_config(),
              'exit_version': exit_policy.VERSION, 'stop_pct': STOP_LOSS_PCT, 'take_profit_pct': TAKE_PROFIT_PCT,
              'risk_buffer_pct': STOP_EXECUTION_BUFFER_PCT, 'daily_loss_usd': MAX_DAILY_LOSS_USD,
              'max_positions': MAX_POSITIONS, 'notional_usd': TRADE_NOTIONAL_USD,
              'max_position_full_loss_usd': MAX_POSITION_RISK_USD,'max_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,'max_drawdown_pct':MAX_DRAWDOWN_PCT,
              'max_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT, 'max_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
              'max_conservative_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
              'verified_entry_policy': promoted_guard.policy_config(STOP_LOSS_PCT),
              'quote_size_backoff_version': entry_size_backoff.VERSION,
              'quote_size_backoff_min_usd': entry_size_backoff.MIN_ENTRY_NOTIONAL_USD,
              'quote_size_backoff_attempts': entry_size_backoff.MAX_SIZE_ATTEMPTS,
              'quote_retry_cooldown_ms': entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS,
              'execution_evidence_version': 'QUOTE_EVIDENCE_V9',
              'quote_adapter': paper_quotes.transport.ADAPTER_VERSION,
              'slippage_tolerance_bps': paper_quotes.SLIPPAGE_BPS,
              'assumed_execution_buffer_bps': paper_quotes.BUFFER_BPS,
              'simulated_execution_delay_ms': paper_quotes.SIMULATED_DELAY_MS,
              'max_signal_age_ms': paper_quotes.MAX_SIGNAL_AGE_MS,
              # Every strategy: DEFENSIVE_ENTRY_LAYER_V1 and the market score model.
              'defensive_entry': entry_defense.config(),
              'score_version': SCORE_VERSION,
              'exit_context_score_version': EXIT_CONTEXT_SCORE_VERSION}
    if ADAPTIVE_PROFILE is not None:
        config.update({'signal_strategy': SIGNAL_STRATEGY, 'strategy_profile': ADAPTIVE_PROFILE.as_dict(),
                       'exit_policy': POSITION_EXIT_POLICY, 'exit_version': exit_policy_version(),
                       'scan_seconds': SCAN_SECONDS, 'position_scan_seconds': POSITION_SCAN_SECONDS,
                       'strategy_thresholds': oct4.effective_entry_thresholds(ADAPTIVE_PROFILE),
                       'same_token_cooldown_seconds': SAME_TOKEN_COOLDOWN_SECONDS,
                       'max_quoted_candidates': MAX_QUOTED_CANDIDATES,
                       'size_policy': 'FLAT_NOTIONAL_NO_BACKOFF'})
    if COST_FIRST_ACTIVE:
        config.update({'signal_strategy': SIGNAL_STRATEGY, 'exit_policy': POSITION_EXIT_POLICY,
                       'exit_version': exit_policy_version(),
                       'strategy_profile': cost_first_profile.hash_basis(
                           cap_usd=TRADE_NOTIONAL_USD, stop_loss_pct=STOP_LOSS_PCT, take_profit_pct=TAKE_PROFIT_PCT),
                       'same_token_cooldown_seconds': SAME_TOKEN_COOLDOWN_SECONDS,
                       'max_quoted_candidates': MAX_QUOTED_CANDIDATES,
                       'size_policy': cost_first_profile.SIZE_POLICY})
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def strategy_config_overlay(config: dict[str, Any]) -> dict[str, Any]:
    """/state config of the active profile; the default and adaptive configs pass through unchanged."""
    if not COST_FIRST_ACTIVE:
        return config
    engine_ownership = _engine_config_ownership()
    config.update({
        'entry_score': None,
        'ensemble_strategies': [cost_first_profile.STRATEGY_ID],
        'signal_source_commit': cost_first_profile.SIGNAL_SOURCE,
        'learning_mode': cost_first_profile.LEARNING_MODE,
        'strategy_profile': cost_first_profile.config_snapshot(
            cap_usd=TRADE_NOTIONAL_USD, stop_loss_pct=STOP_LOSS_PCT, take_profit_pct=TAKE_PROFIT_PCT,
            engine_ownership=engine_ownership),
        'min_liquidity_usd': cost_first_profile.cost_first.UNIVERSE.min_liquidity_usd,
        'execution_note': cost_first_profile.EXECUTION_NOTE,
        'universe_parameters': cost_first_profile.universe_parameters(),
        'size_rule': cost_first_profile.size_rule(TRADE_NOTIONAL_USD),
        'exit_parameters': cost_first_profile.exit_parameters(STOP_LOSS_PCT, TAKE_PROFIT_PCT),
        'exit_impact_emergency_version': cost_first_profile.EIE_VERSION,
        'max_hold_minutes': exit_policy.FIXED_MAX_HOLD_MINUTES,
    })
    return config


# Below the conviction (72) that lets a mode outlive its max hold: without live
# flow a position keeps its entry mode but is not held past that mode's limit.
ADAPTIVE_BLIND_CONVICTION = 71.0
# A quote bundle plus the commit check must finish inside the 8 s signal limit.
ADAPTIVE_QUOTE_LATENCY_MARGIN_MS = 2000


def adaptive_exit_context(position: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Fail safe when the held pool has no COMPLETE 30 s exact-pool window.

    The shared tape follows a bounded set of pools and a personal engine's held
    pool can drop out of it. Empty windows would score as heavy selling, so the
    flow-driven rungs (CONVICTION_EXIT, ORDERFLOW_EXIT, CONVICTION_PROFIT_LOCK)
    are disabled and the entry hold mode is kept. Stop, trailing, the mode's max
    hold, the absolute max hold and the safety exits still apply.
    """
    fast = context.get('fast_flow') or {}
    if str(fast.get('quality') or '').upper() == 'COMPLETE':
        return context
    hold = position.get('adaptive_hold') or oct4.hold_mode(num(position.get('entry_conviction'), 72.0))
    return {**context, 'conviction': ADAPTIVE_BLIND_CONVICTION, 'mode': hold['mode'],
            'max_hold_minutes': hold['max_hold_minutes'], 'target_pct': hold['target_pct'],
            'trail_arm_pct': hold['trail_arm_pct'], 'trail_pct': hold['trail_pct'],
            'fast_flow': {}, 'flow_context_unavailable': True,
            'observed_conviction': context.get('conviction')}


def exit_policy_version() -> str:
    return position_exit_policy_version(POSITION_EXIT_POLICY)


def position_exit_policy_version(policy: str) -> str:
    """Version stamped for one position's own exit policy (never the engine's current one)."""
    if policy == 'adaptive':
        return exit_policy.ADAPTIVE_VERSION
    if policy == cost_first_profile.EXIT_POLICY:
        return cost_first_profile.EXIT_POLICY_VERSION
    return exit_policy.VERSION


def config_ownership() -> dict[str, str]:
    """Where every effective runtime value comes from, so drift is visible in /state."""
    if ADAPTIVE_PROFILE is not None:
        return oct4.config_ownership(ADAPTIVE_PROFILE)
    if COST_FIRST_ACTIVE:
        return cost_first_profile.config_ownership(_engine_config_ownership())
    return _engine_config_ownership()


def _engine_config_ownership() -> dict[str, str]:
    return {
        'signal_strategy': 'winner_ensemble.VERSION constant',
        'entry_policy_version': 'winner_ensemble.ENTRY_POLICY_VERSION constant',
        'exit_policy': 'fixed constant (engine_exit_policy.VERSION)',
        'scan_seconds': 'NEO_SCAN_SECONDS env, default 3 (minimum 2)',
        'position_scan_seconds': 'NEO_POSITION_SCAN_SECONDS env, default 0.5',
        'max_positions': 'market_monitor.MAX_POSITIONS constant',
        'trade_notional_usd': 'NEO_TRADE_NOTIONAL_USD env, default 200; sized down by liquidity, age and daily budget',
        'max_daily_loss_usd': 'NEO_MAX_DAILY_LOSS_USD env, default 100',
        'stop_loss_pct': 'market_monitor.STOP_LOSS_PCT constant',
        'take_profit_pct': 'market_monitor.TAKE_PROFIT_PCT constant (fixed exit policy)',
        'trailing_pct': 'market_monitor.TRAILING_PCT constant (unused by the fixed exit policy)',
        'max_hold_minutes': 'engine_exit_policy fixed 60 (market_monitor.MAX_HOLD_MINUTES is informational)',
        'same_token_cooldown_seconds': 'market_monitor.SAME_TOKEN_COOLDOWN_SECONDS constant',
        'entry_score': 'winner_ensemble.RULES minimum score',
        'min_liquidity_usd': 'winner_ensemble.RULES minimum liquidity',
        'effective_entry_thresholds': 'winner_ensemble.RULES constants',
        'strict_entry_score': 'NEO_STRICT_ENTRY_SCORE env (published; not an ensemble entry gate)',
        'strict_min_conviction': 'NEO_STRICT_MIN_CONVICTION env (published; not an ensemble entry gate)',
        'strict_min_liquidity_usd': 'NEO_STRICT_MIN_LIQUIDITY_USD env (published; not an ensemble entry gate)',
        'strict_max_entry_impact_pct': 'NEO_STRICT_MAX_ENTRY_IMPACT_PCT env, default 1.75',
        'strict_max_roundtrip_cost_pct': 'min(NEO_STRICT_MAX_ROUNDTRIP_COST_PCT env, policy 1.5, stop budget fraction)',
        'strict_max_worst_case_cost_pct': 'min(NEO_STRICT_MAX_WORST_CASE_COST_PCT env, policy 2.5, stop budget fraction)',
        'max_quoted_candidates_per_scan': 'engine_entry_policy.MAX_QUOTED_CANDIDATES constant',
        'defensive_entry': 'entry_defense DEFENSIVE_ENTRY_LAYER_V1 constants for every strategy (no environment override)',
        'score_version': f'market_monitor.SCORE_VERSION constant {SCORE_VERSION}',
        'exit_context_score_version': f'market_monitor.EXIT_CONTEXT_SCORE_VERSION constant {EXIT_CONTEXT_SCORE_VERSION} (adaptive exit context reads coin scoreV1)',
        'environment_overrides_honored': 'scan, position scan, notional, daily loss, risk caps, strict thresholds (cost caps can only be lowered)',
    }


def early_requested_notional(coin: dict[str, Any], learning: dict[str, Any]) -> float:
    """Use smaller scouts in thin/very-new pools so $200 impact does not kill entries."""
    liquidity = num(coin.get('liquidityUsd'))
    age = num(coin.get('ageMinutes'), 999999)
    if liquidity < 8000:
        base = 35.0
    elif liquidity < 15000:
        base = 50.0
    elif liquidity < 30000:
        base = 75.0
    elif liquidity < 60000:
        base = 100.0
    elif liquidity < 120000:
        base = 150.0
    else:
        base = TRADE_NOTIONAL_USD

    if age <= 15:
        base *= 0.70
    elif age <= 45:
        base *= 0.85

    base *= num(learning.get('size_multiplier'), 1.0)
    return round(max(25.0, min(TRADE_NOTIONAL_USD, base)), 2)

def _iso_ms(value: Any) -> int:
    try:
        text = str(value or '').strip()
        if not text:
            return 0
        return int(datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0


def gecko_new_pumpswap_pairs() -> list[dict[str, Any]]:
    """Discover brand-new PumpSwap pools without waiting for profile/boost indexing.

    Cached for 15s (~4 public requests/minute). These rows still pass the same
    rug, exact-pool Jupiter, impact and stop checks before any PAPER entry.
    """
    current = now_ms()
    with _GECKO_NEW_POOLS_LOCK:
        cached_at = int(_GECKO_NEW_POOLS_CACHE.get('ts') or 0)
        cached_pairs = list(_GECKO_NEW_POOLS_CACHE.get('pairs') or [])
        retry_after = int(_GECKO_NEW_POOLS_CACHE.get('retry_after') or 0)
    if cached_at > 0 and (0 <= current - cached_at <= GECKO_NEW_POOLS_TTL_MS or current < retry_after):
        return cached_pairs

    parsed: list[dict[str, Any]] = []
    try:
        response = SESSION.get(
            GECKO_NEW_POOLS_URL,
            headers={'Accept': 'application/json;version=20230203'},
            timeout=(1.0, 3.0),
        )
        response.raise_for_status()
        rows = response.json().get('data') or []
        for row in rows:
            attrs = row.get('attributes') or {}
            rel = row.get('relationships') or {}
            dex_id = str((((rel.get('dex') or {}).get('data') or {}).get('id')) or '').lower()
            if dex_id != 'pumpswap':
                continue
            base_id = str((((rel.get('base_token') or {}).get('data') or {}).get('id')) or '')
            quote_id = str((((rel.get('quote_token') or {}).get('data') or {}).get('id')) or '')
            mint = base_id.removeprefix('solana_')
            quote_mint = quote_id.removeprefix('solana_')
            pair = str(attrs.get('address') or '')
            if quote_mint != SOL_MINT or not is_valid_solana_address(mint) or not is_valid_solana_address(pair):
                continue

            price_usd = num(attrs.get('base_token_price_usd'))
            sol_usd = num(attrs.get('quote_token_price_usd'))
            price_native = num(attrs.get('base_token_price_native_currency'))
            if price_native <= 0 and price_usd > 0 and sol_usd > 0:
                price_native = price_usd / sol_usd
            liquidity = num(attrs.get('reserve_in_usd'))
            if price_usd <= 0 or liquidity <= 0:
                continue

            name = str(attrs.get('name') or 'TOKEN / SOL')
            symbol = (name.split('/')[0].strip() or 'TOKEN')[:32]
            changes = attrs.get('price_change_percentage') or {}
            volumes = attrs.get('volume_usd') or {}
            transactions = attrs.get('transactions') or {}
            created = _iso_ms(attrs.get('pool_created_at'))
            txns = {}
            for window in ('m5', 'h1', 'h6', 'h24'):
                src = transactions.get(window) or transactions.get('m5') or {}
                txns[window] = {
                    'buys': int(num(src.get('buys'))),
                    'sells': int(num(src.get('sells'))),
                }
            volume = {window: num(volumes.get(window) or volumes.get('m5')) for window in ('m5','h1','h6','h24')}
            change = {window: num(changes.get(window) or changes.get('m5')) for window in ('m5','h1','h6','h24')}
            fdv = num(attrs.get('fdv_usd'))
            market_cap = num(attrs.get('market_cap_usd')) or fdv

            parsed.append({
                'chainId': 'solana',
                'pairAddress': pair,
                'dexId': 'pumpswap',
                'url': f'https://www.geckoterminal.com/solana/pools/{pair}',
                'baseToken': {'address': mint, 'name': symbol, 'symbol': symbol},
                'quoteToken': {'address': SOL_MINT, 'name': 'Wrapped SOL', 'symbol': 'SOL'},
                'priceUsd': price_usd,
                'priceNative': price_native,
                'marketCap': market_cap,
                'fdv': fdv,
                'liquidity': {'usd': liquidity},
                'volume': volume,
                'priceChange': change,
                'txns': txns,
                'pairCreatedAt': created,
                'info': {},
                '_early_source': 'gecko-new-pools',
                '_market_observed_at': now_ms(),
            })
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        # Cache failed/empty attempts too; otherwise each 3s scan hits a
        # rate-limited provider. Old rows retain their original observation.
        with _GECKO_NEW_POOLS_LOCK:
            rate_limited = getattr(getattr(exc, 'response', None), 'status_code', None) == 429
            _GECKO_NEW_POOLS_CACHE.update(ts=current, pairs=cached_pairs,
                retry_after=current + (180_000 if rate_limited else 60_000))
        return cached_pairs

    with _GECKO_NEW_POOLS_LOCK:
        _GECKO_NEW_POOLS_CACHE.update(ts=current, pairs=list(parsed), retry_after=0)
    return parsed


STATE = State(load_state=False)


def discover() -> tuple[list[str], dict[str, dict[str, Any]]]:
    metadata: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for address in _PUMP_CATALOG.get(api, provider_fetch=catalog_provider_api):
        metadata[address] = {'sources': ['pumpswap-address-catalog'], 'icon': '',
                             'header': '', 'description': '', 'links': [], 'boost_amount': 0}
        order.append(address)
    sources = [
        ('latest', '/token-profiles/latest/v1'),
        ('boosted', '/token-boosts/top/v1'),
        ('boosted-latest', '/token-boosts/latest/v1'),
    ]
    for source_name, path in sources:
        try:
            rows = api(path)
        except Exception as exc:
            STATE.event(f'{source_name} discovery warning: {exc}')
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get('chainId') != 'solana':
                continue
            address = str(row.get('tokenAddress') or '').strip()
            if not is_valid_solana_address(address):
                continue
            if address not in metadata:
                metadata[address] = {
                    'sources': [], 'icon': row.get('icon') or '',
                    'header': row.get('header') or '',
                    'description': row.get('description') or '',
                    'links': row.get('links') or [], 'boost_amount': 0,
                }
                order.append(address)
            info = metadata[address]
            if source_name not in info['sources']:
                info['sources'].append(source_name)
            info['icon'] = info['icon'] or row.get('icon') or ''
            info['header'] = info['header'] or row.get('header') or ''
            info['description'] = info['description'] or row.get('description') or ''
            info['links'] = info['links'] or row.get('links') or []
            info['boost_amount'] = max(num(info['boost_amount']), num(row.get('amount')), num(row.get('totalAmount')))
    return order[:90], metadata


def fetch_pairs(addresses: list[str]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    clean: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        address = str(raw or '').strip()
        if not is_valid_solana_address(address) or address in seen:
            continue
        clean.append(address)
        seen.add(address)
    for i in range(0, len(clean), 30):
        batch = clean[i:i + 30]
        if not batch:
            continue
        try:
            rows = api('/tokens/v1/solana/' + ','.join(batch))
            if isinstance(rows, list):
                pairs.extend(rows)
        except Exception as exc:
            STATE.event(f'Market data warning: DexScreener batch unavailable ({type(exc).__name__})')
    return pairs


def best_pairs(pairs: list[dict[str, Any]], *, prefer_pumpswap_mints=()) -> dict[str, dict[str, Any]]:
    def priority(pair, address):
        catalog_pump = address in prefer_pumpswap_mints and pair.get('dexId') == 'pumpswap'
        # WSOL supports the shared Lab cost model and validated native reserve
        # observations. USDC pools still have the aggregate route fallback;
        # this discovery preference never changes an existing held pool.
        quote = pair.get('quoteToken')
        sol_model = bool(catalog_pump and isinstance(quote, dict)
                         and quote.get('address') == SOL_MINT)
        return catalog_pump, sol_model, num((pair.get('liquidity') or {}).get('usd'))

    best: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        address = (pair.get('baseToken') or {}).get('address')
        if not address:
            continue
        old = best.get(address)
        if old is None or priority(pair, address) > priority(old, address):
            best[address] = pair
    return best


def exact_position_pair(position: dict[str, Any], pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    address = str(position.get('address') or '')
    pair_address = str(position.get('pairAddress') or '')
    if not address or not pair_address:
        return None
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        base_address = str((pair.get('baseToken') or {}).get('address') or '')
        current_pair = str(pair.get('pairAddress') or '')
        if base_address == address and current_pair == pair_address:
            return pair
    return None


def early_market_pairs(early_pairs, dex_pairs, current):
    """Keep early pool identity without a cached price masking fresh data."""
    fresh_dex = {(str((pair.get('baseToken') or {}).get('address') or ''),
                  str(pair.get('pairAddress') or '')): pair for pair in dex_pairs
                 if pair.get('chainId') == 'solana' and num(pair.get('priceUsd')) > 0}
    markets = []
    for pair in early_pairs:
        identity = (str((pair.get('baseToken') or {}).get('address') or ''),
                    str(pair.get('pairAddress') or ''))
        if identity in fresh_dex:
            markets.append(fresh_dex[identity])
        elif 0 < num(pair.get('_market_observed_at')) <= current and current - num(pair.get('_market_observed_at')) <= GECKO_NEW_POOLS_TTL_MS:
            markets.append(pair)
    return markets


def score_pair_models(pair: dict[str, Any], meta: dict[str, Any]):
    """score_pair plus the NEO_MARKET_SCORE_V1 score of the same observation (exit context basis)."""
    liq = num((pair.get('liquidity') or {}).get('usd'))
    volume = pair.get('volume') or {}
    vol_h1 = num(volume.get('h1'))
    mc = num(pair.get('marketCap')) or num(pair.get('fdv'))
    changes = pair.get('priceChange') or {}
    change_m5 = num(changes.get('m5'))
    change_h1 = num(changes.get('h1'))
    tx5 = (pair.get('txns') or {}).get('m5') or {}
    buys, sells = num(tx5.get('buys')), num(tx5.get('sells'))
    tx_count = buys + sells
    created = int(pair.get('pairCreatedAt') or 0)
    age = max(0.0, (now_ms() - created) / 60000) if created else 999999
    buy_sell = buys / max(sells, 1)
    vol_liq = vol_h1 / max(liq, 1)
    liq_mc = liq / max(mc, 1) if mc else 0
    score, signals = 36.0, []

    if liq >= 50000:
        score += 18; signals.append(signal('positive', 'Силна ликвидност', f'${liq:,.0f}'))
    elif liq >= 20000:
        score += 13; signals.append(signal('positive', 'Добра ликвидност', f'${liq:,.0f}'))
    elif liq >= 10000:
        score += 7; signals.append(signal('neutral', 'Приемлива ликвидност', f'${liq:,.0f}'))
    elif liq < 5000:
        score -= 22; signals.append(signal('risk', 'Много ниска ликвидност', f'${liq:,.0f}'))
    else:
        score -= 7; signals.append(signal('risk', 'Тънка ликвидност', f'${liq:,.0f}'))

    if vol_h1 >= 50000:
        score += 12; signals.append(signal('positive', 'Силен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 >= 10000:
        score += 7; signals.append(signal('positive', 'Активен 1h volume', f'${vol_h1:,.0f}'))
    elif vol_h1 < 1000:
        score -= 8; signals.append(signal('risk', 'Слаб volume', f'${vol_h1:,.0f}'))

    if tx_count >= 120:
        score += 10; signals.append(signal('positive', 'Много активни сделки', f'{int(tx_count)} tx / 5m'))
    elif tx_count >= 35:
        score += 6; signals.append(signal('positive', 'Добра активност', f'{int(tx_count)} tx / 5m'))
    elif tx_count < 8:
        score -= 6; signals.append(signal('risk', 'Малко сделки', f'{int(tx_count)} tx / 5m'))

    if 1.05 <= buy_sell <= 2.8:
        score += 8; signals.append(signal('positive', 'Купувачите водят', f'Buy/Sell {buy_sell:.2f}x'))
    elif buy_sell > 5:
        score -= 6; signals.append(signal('risk', 'Неестествен buy imbalance', f'{buy_sell:.2f}x'))
    elif buy_sell < 0.65:
        score -= 8; signals.append(signal('risk', 'Продавачите доминират', f'{buy_sell:.2f}x'))

    if 1.5 <= change_m5 <= 18:
        score += 8; signals.append(signal('positive', 'Здрав кратък momentum', f'{change_m5:+.1f}% / 5m'))
    elif 18 < change_m5 <= 45:
        score += 3; signals.append(signal('neutral', 'Бърз pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 > 60:
        score -= 12; signals.append(signal('risk', 'Вертикален pump', f'{change_m5:+.1f}% / 5m'))
    elif change_m5 < -20:
        score -= 12; signals.append(signal('risk', 'Силен спад', f'{change_m5:+.1f}% / 5m'))

    if 0.25 <= age <= 360:
        score += 8; signals.append(signal('positive', 'Ранен етап', f'{age:.1f} мин.'))
    elif age < 0.25:
        score -= 5; signals.append(signal('risk', 'Pair под 15 секунди', f'{age:.2f} мин.'))
    elif age > 4320:
        score -= 4
    exit_basis_offset = 0.0
    if mc > 0:
        # NEO_MARKET_SCORE_V2_LIQ_MC_BAND: a pool holding most of the supply is an
        # LP-pull risk, not good liquidity; 0.6 <= liq/MC < 1 earns no bonus.
        if liq_mc >= SCORE_LP_RISK_LIQ_MC:
            score -= 10; signals.append(signal('risk', 'Ликвидност >= MC (LP риск)', f'{liq_mc * 100:.1f}%'))
            v2_liq_mc = -10
        elif SCORE_GOOD_LIQ_MC_RANGE[0] <= liq_mc < SCORE_GOOD_LIQ_MC_RANGE[1]:
            score += 9; signals.append(signal('positive', 'Добро liquidity/MC', f'{liq_mc * 100:.1f}%'))
            v2_liq_mc = 9
        elif liq_mc < 0.03:
            score -= 10; signals.append(signal('risk', 'Слаб liquidity/MC', f'{liq_mc * 100:.1f}%'))
            v2_liq_mc = -10
        else:
            v2_liq_mc = 0
        # NEO_MARKET_SCORE_V1 term (+9 for any liq/MC >= 0.15), kept only for the
        # exit context (EXIT_CONTEXT_SCORE_VERSION); every other term is shared.
        v1_liq_mc = 9 if liq_mc >= 0.15 else -10 if liq_mc < 0.03 else 0
        exit_basis_offset = float(v1_liq_mc - v2_liq_mc)

    if 0.10 <= vol_liq <= 4.0:
        score += 6
    elif vol_liq > 7:
        score -= 12; signals.append(signal('risk', 'Volume/liquidity extreme', f'{vol_liq:.1f}x'))

    if any('boost' in source for source in meta.get('sources', [])):
        score += 4; signals.append(signal('neutral', 'Boosted discovery', 'Повишена market видимост'))
    if abs(change_h1) > 250:
        score -= 8; signals.append(signal('risk', 'Екстремен 1h move', f'{change_h1:+.0f}%'))

    score_v1 = round(clamp(score + exit_basis_offset), 1)
    score = round(clamp(score), 1)
    risk = round(100 - score, 1)
    posture = 'SETUP' if score >= ENTRY_SCORE else 'WATCH' if score >= 60 else 'WAIT' if score >= 45 else 'SKIP'
    return score, risk, posture, signals[:8], score_v1


def score_pair(pair: dict[str, Any], meta: dict[str, Any]):
    """NEO_MARKET_SCORE_V2_LIQ_MC_BAND score, risk, posture and signals of one pair."""
    return score_pair_models(pair, meta)[:4]


def exit_context_score(coin: dict[str, Any]) -> float:
    """EXIT_CONTEXT_SCORE_VERSION (V1) score of an observation for the adaptive exit context.

    make_coin publishes it as scoreV1; an observation recorded before the V2
    model carries only 'score', which was computed by V1.
    """
    value = num(coin.get('scoreV1'), math.nan)
    return value if math.isfinite(value) else num(coin.get('score'))


def make_coin(address: str, pair: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    score, risk, posture, signals, score_v1 = score_pair_models(pair, meta)
    base, info = pair.get('baseToken') or {}, pair.get('info') or {}
    volume, changes, txns = pair.get('volume') or {}, pair.get('priceChange') or {}, pair.get('txns') or {}
    created = int(pair.get('pairCreatedAt') or 0)
    return {
        'address': address,
        'pairAddress': pair.get('pairAddress') or '',
        'dexId': pair.get('dexId') or '',
        'dexUrl': pair.get('url') or '',
        'name': base.get('name') or 'Unknown token',
        'symbol': base.get('symbol') or 'TOKEN',
        'imageUrl': info.get('imageUrl') or meta.get('icon') or '',
        'headerUrl': info.get('header') or meta.get('header') or '',
        'description': meta.get('description') or '',
        'priceUsd': num(pair.get('priceUsd')),
        'priceNative': num(pair.get('priceNative')),
        'quoteTokenAddress': (pair.get('quoteToken') or {}).get('address'),
        'marketCap': num(pair.get('marketCap')),
        'fdv': num(pair.get('fdv')),
        'liquidityUsd': num((pair.get('liquidity') or {}).get('usd')),
        'volume': {k: num(volume.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'priceChange': {k: num(changes.get(k)) for k in ('m5', 'h1', 'h6', 'h24')},
        'txns': {
            k: {'buys': int(num((txns.get(k) or {}).get('buys'))), 'sells': int(num((txns.get(k) or {}).get('sells')))}
            for k in ('m5', 'h1', 'h6', 'h24')
        },
        'pairCreatedAt': created,
        'ageMinutes': round(max(0, (now_ms() - created) / 60000), 1) if created else None,
        'sources': meta.get('sources', []),
        'boostAmount': num(meta.get('boost_amount')),
        'websites': (info.get('websites') or [])[:3],
        'socials': (info.get('socials') or [])[:5],
        'score': score, 'riskScore': risk, 'posture': posture, 'scoreVersion': SCORE_VERSION,
        # Exit-context basis of held ORDER_FLOW_ADAPTIVE positions (EXIT_CONTEXT_SCORE_VERSION).
        'scoreV1': score_v1,
        'signals': signals, 'updatedAt': int(pair.get('_market_observed_at') or now_ms()),
    }

class Monitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.position_lock = threading.Lock()
        self.position_poll_lock = threading.Lock()
        self.entry_lock = threading.Lock()
        self.entry_quote_retry_after: dict[str, int] = {}
        # Quote-preparation failure codes: since this engine started (not
        # persisted; the ledger schema is unchanged) and a rolling time window.
        self.quote_preparation_codes_lifetime: dict[str, int] = {}
        self.quote_preparation_lifetime_since = now_ms()
        self.quote_preparation_recent: deque = deque(maxlen=QUOTE_PREPARATION_ROLLING_MAX_SCANS)
        self.quote_preparation_log_after: dict[tuple[str, str], int] = {}
        self.training_probe_lock = threading.Lock()
        self.training_probe_inflight = False
        self.training_probe_last_attempt_at = 0
        self.training_probe_retry_after: dict[tuple[str, str], int] = {}
        # DEFENSIVE_ENTRY_LAYER_V1 of this engine process: its own ticker
        # registry (sidecar next to the account state) and pair history, created
        # on first use so importing the module reads no file.
        self._entry_defense = None
        self._entry_defense_lock = threading.Lock()
        self.discovery = runtime.DiscoveryCache(discover, refresh_seconds=8, max_age_seconds=90)

    @property
    def defense(self):
        """This engine's entry_defense.DefensiveEntryLayer."""
        with self._entry_defense_lock:
            if self._entry_defense is None:
                # A registry without current coverage is seeded read-only from the other
                # services' sidecars (main's for a personal engine, the Lab's and the
                # tape's; see sibling_registry_paths, TICKER_REGISTRY_SEED_V4: also while
                # running, and their sightings every 5 min while it vouches).
                path = entry_defense.registry_path_for(STATE_PATH)
                self._entry_defense = entry_defense.DefensiveEntryLayer(
                    registry_path=path, seed_paths=entry_defense.sibling_registry_paths(path))
            return self._entry_defense

    def defensive_layer_status(self) -> dict[str, Any]:
        """The layer's status() for /state (never raises; an error is named, never hidden)."""
        try:
            return self.defense.status()
        except Exception as exc:
            return {'version': entry_defense.VERSION, 'status_error': type(exc).__name__}

    def observe_entry_defense(self, feed: list[dict[str, Any]], now: int | None = None) -> None:
        """Feed one scan into the ticker registry and pair history (never raises)."""
        try:
            self.defense.observe(feed, now_ms() if now is None else now)
        except Exception as exc:
            with STATE.lock:
                STATE.event('Defensive entry observation: ' + type(exc).__name__)

    @staticmethod
    def entry_loss_index(now: int) -> dict:
        """POOL_LOSS_MEMORY_V1 index of this account's own closed history (read-only)."""
        with STATE.lock:
            history = list(getattr(STATE, 'history', None) or [])
        return pool_loss_memory.index(history, now)

    def defensive_entry_decision(self, coin: dict[str, Any], now: int, *, blocked_pools=None) -> dict[str, Any]:
        """DEFENSIVE_ENTRY_LAYER_V1 decision; asked before flow promotion, RugCheck and quotes."""
        if blocked_pools is None:
            blocked_pools = self.entry_loss_index(now)
        return self.defense.evaluate(coin, now, blocked_pools=blocked_pools)

    @staticmethod
    def _bounded_code_add(histogram: dict[str, int], code: str, count: int = 1) -> None:
        if code not in histogram and len(histogram) >= QUOTE_PREPARATION_MAX_CODES:
            code = 'OTHER'
        histogram[code] = histogram.get(code, 0) + count

    def _record_quote_preparation_failure(self, report: dict[str, Any], coin: dict[str, Any], notional: float,
                                          attempt_index: int, failure: dict[str, Any], code: str,
                                          deferred: bool, latency_ms: int, dex_id: str) -> None:
        """Complete histogram, bounded examples and a rate-limited sidecar row."""
        self._bounded_code_add(report.setdefault('quote_preparation_codes', {}), code)
        examples = report.setdefault('quote_preparation_failures', [])
        if len(examples) < 3:
            examples.append({'symbol': str(coin.get('symbol') or '')[:40], 'notional_usd': notional,
                             'counted_as_quote_attempt': not deferred, 'latency_ms': latency_ms, **failure})
        pair, mint = str(coin.get('pairAddress') or ''), str(coin.get('address') or '')
        now = now_ms()
        key = (pair, code)
        if self.quote_preparation_log_after.get(key, 0) > now:
            report['quote_preparation_log_rate_limited'] = int(report.get('quote_preparation_log_rate_limited') or 0) + 1
            return
        logged = int(report.get('quote_preparation_log_events') or 0)
        if logged >= QUOTE_PREPARATION_LOG_PER_SCAN:
            report['quote_preparation_log_skipped'] = int(report.get('quote_preparation_log_skipped') or 0) + 1
            return
        if len(self.quote_preparation_log_after) >= QUOTE_PREPARATION_LOG_RATE_KEYS:
            self.quote_preparation_log_after = {k: v for k, v in self.quote_preparation_log_after.items() if v > now}
            while len(self.quote_preparation_log_after) >= QUOTE_PREPARATION_LOG_RATE_KEYS:
                self.quote_preparation_log_after.pop(next(iter(self.quote_preparation_log_after)))
        self.quote_preparation_log_after[key] = now + entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS
        payload = {'code': code, 'stage': failure.get('stage'), 'pool': abbreviate_address(pair),
                   'pair_address': pair, 'mint': mint, 'symbol': str(coin.get('symbol') or '')[:40],
                   'dex_id': dex_id, 'notional_usd': round(float(notional), 8), 'attempt': attempt_index + 1,
                   'latency_ms': int(latency_ms), 'queue_ms': failure.get('queue_ms'),
                   'http_ms': failure.get('http_ms'), 'http_status': failure.get('http_status'),
                   'retry_at': failure.get('retry_at'), 'deferred': deferred,
                   'counted_as_quote_attempt': not deferred, 'scan_count': STATE.scan_count,
                   'checked_at': report.get('checked_at')}
        try:
            if append_quote_preparation_log(payload):
                report['quote_preparation_log_events'] = logged + 1
        except Exception as exc:
            report['quote_preparation_log_error'] = type(exc).__name__

    def _finish_quote_preparation_histograms(self, report: dict[str, Any]) -> dict[str, Any]:
        """Attach the rolling (time window) and since-engine-start code histograms."""
        scan_codes = dict(report.get('quote_preparation_codes') or {})
        checked_at = report.get('checked_at')
        checked_at = int(checked_at) if isinstance(checked_at, (int, float)) else now_ms()
        if scan_codes:
            self.quote_preparation_recent.append((checked_at, scan_codes))
            for code, count in scan_codes.items():
                self._bounded_code_add(self.quote_preparation_codes_lifetime, code, count)
        horizon = max(checked_at, now_ms()) - QUOTE_PREPARATION_ROLLING_WINDOW_MS
        while self.quote_preparation_recent and self.quote_preparation_recent[0][0] < horizon:
            self.quote_preparation_recent.popleft()
        rolling: dict[str, int] = {}
        for _at, codes in self.quote_preparation_recent:
            for code, count in codes.items():
                self._bounded_code_add(rolling, code, count)
        report['quote_preparation_codes'] = scan_codes
        report['quote_preparation_codes_rolling'] = rolling
        report['quote_preparation_rolling_scans'] = len(self.quote_preparation_recent)
        report['quote_preparation_rolling_window_minutes'] = QUOTE_PREPARATION_ROLLING_WINDOW_MS // 60_000
        report['quote_preparation_rolling_since'] = (self.quote_preparation_recent[0][0]
                                                     if self.quote_preparation_recent else None)
        report['quote_preparation_codes_lifetime'] = dict(self.quote_preparation_codes_lifetime)
        report['quote_preparation_lifetime_since'] = self.quote_preparation_lifetime_since
        report['quote_preparation_lifetime_scope'] = 'SINCE_ENGINE_START'
        report['quote_preparation_defer_codes'] = sorted(QUOTE_PREPARATION_DEFER_CODES)
        return report

    def prewarm_entry_checks(self, feed: list[dict[str, Any]]) -> None:
        """Warm the price and RugCheck reports of likely next entries; avoid provider queues.

        PREWARM_V2_DEFENSIVE_POPULATION: only pools the defensive entry layer
        can allow are warmed. STRUCTURAL_RUG_GUARD_V1 blocks every pool younger
        than 12 h, so the old young-pool population (ageMinutes <= 360) could
        never be entered; candidates are now established pools (pair age >= 720
        min at now) that pass the active strategy's market screen and the layer,
        ranked by 5-minute activity and score, at most 4 per scan. Prewarming
        never admits an entry; every gate still runs at entry.
        """
        candidates = []
        stamp = now_ms()
        blocked_pools = self.entry_loss_index(stamp)
        registry = self.defense.registry
        for coin in feed:
            age = structural_rug_guard.age_minutes(coin, stamp)
            if (age is None or age < structural_rug_guard.PARAMS.young_pool_max_age_minutes
                    or num(coin.get('liquidityUsd')) < 4000 or not market_candidate(coin, stamp, registry)):
                continue
            if not self.defensive_entry_decision(coin, stamp, blocked_pools=blocked_pools)['allowed']:
                continue
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = num(tx.get('buys')) + num(tx.get('sells'))
            candidates.append(((activity, num(coin.get('score'))), coin))
        candidates.sort(key=lambda row: row[0], reverse=True)
        for _, coin in candidates[:PREWARM_MAX_CANDIDATES]:
            price_integrity.check(coin)
            rug_guard.check(coin)

    def update_price_history(self, feed: list[dict[str, Any]]) -> None:
        stamp = now_ms()
        for coin in feed[:50]:
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            address = coin['address']
            points = STATE.price_history.setdefault(address, [])
            points.append({
                'ts': stamp,
                'price': price,
                'liquidity': round(num(coin.get('liquidityUsd')), 2),
                'volumeH1': round(num((coin.get('volume') or {}).get('h1')), 2),
                'score': num(coin.get('score')),
            })
            STATE.price_history[address] = points[-480:]

    def market_context(self, coin: dict[str, Any], position: dict[str, Any] | None = None) -> dict[str, Any]:
        """October 4 conviction context of one observation.

        With a held ``position`` (the ORDER_FLOW_ADAPTIVE exit path) the
        neo_score term reads the EXIT_CONTEXT_SCORE_VERSION score (V1), so the
        V2 score model never changes exits. Without one (entries) it reads the
        current score and also publishes ``exit_basis_conviction`` (same flow,
        V1 score) for the hold mode a new position keeps for blind-flow exits.
        """
        address = coin.get('address') or (position or {}).get('address')
        fast = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
        slow = STATE.live_flow(address, 300, str(coin.get('pairAddress') or ''))
        changes = coin.get('priceChange') or {}
        tx_m5 = (coin.get('txns') or {}).get('m5') or {}
        m5 = num(changes.get('m5'))
        h1 = num(changes.get('h1'))
        market_ratio = num(tx_m5.get('buys')) / max(num(tx_m5.get('sells')), 1.0)
        liquidity = num(coin.get('liquidityUsd'))
        entry_liquidity = num((position or {}).get('entry_liquidity_usd'), liquidity)
        liquidity_ratio = liquidity / max(entry_liquidity, 1.0)

        def conviction_for(neo_score):
            return oct4.conviction_score(
                fast_ratio=num(fast.get('buy_sell_usd_ratio')), slow_ratio=num(slow.get('buy_sell_usd_ratio')),
                unique_wallets=num(slow.get('unique_wallets')), repeat_buy_wallets=num(slow.get('repeat_buy_wallets')),
                whale_buy_usd=num(slow.get('whale_buy_usd')), whale_sell_usd=num(slow.get('whale_sell_usd')),
                m5=m5, h1=h1, market_ratio=market_ratio, liquidity_ratio=liquidity_ratio,
                neo_score=neo_score)

        held = bool(position)
        exit_basis_score = exit_context_score(coin)
        conviction = conviction_for(exit_basis_score if held else num(coin.get('score')))
        hold = oct4.hold_mode(conviction)
        mode, max_hold, target, trail_arm, trail = (hold['mode'], hold['max_hold_minutes'], hold['target_pct'],
                                                    hold['trail_arm_pct'], hold['trail_pct'])
        basis = {} if held else {'exit_basis_conviction': conviction_for(exit_basis_score)}

        return {
            **basis,
            'conviction': conviction, 'mode': mode, 'max_hold_minutes': max_hold,
            'target_pct': target, 'trail_arm_pct': trail_arm, 'trail_pct': trail,
            'm5': round(m5, 3), 'h1': round(h1, 3), 'market_buy_sell_ratio': round(market_ratio, 3),
            'liquidity_ratio_vs_entry': round(liquidity_ratio, 3),
            'fast_flow': fast, 'slow_flow': slow,
            'holder_proxy': {
                'unique_wallets_5m': slow.get('unique_wallets', 0),
                'repeat_buy_wallets_5m': slow.get('repeat_buy_wallets', 0),
                'wallet_buy_sell_ratio_5m': slow.get('wallet_buy_sell_ratio', 0),
                'whale_buy_usd_5m': slow.get('whale_buy_usd', 0),
                'whale_sell_usd_5m': slow.get('whale_sell_usd', 0),
            },
        }

    def training_context(self, coin: dict[str, Any], position: dict[str, Any] | None = None,
                         context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Context recorded for the PAPER_TRAINING_V1 learners: the V1 score basis.

        GOLD_ADAPTIVE (exit_policy 'adaptive') exits read the recorded
        conviction and hold mode, so like ORDER_FLOW_ADAPTIVE exits they stay
        on EXIT_CONTEXT_SCORE_VERSION: an entry context (it carries
        exit_basis_conviction) gets the V1-basis conviction and hold mode; a
        held position's context already is V1-based. The learners' min_score
        reads coin scoreV1 (paper_training.learner_score).
        """
        context = self.market_context(coin, position) if context is None else context
        if not isinstance(context, dict) or not context:
            return context
        if 'exit_basis_conviction' not in context:
            return {**context, 'conviction_score_version': EXIT_CONTEXT_SCORE_VERSION}
        conviction = context['exit_basis_conviction']
        hold = oct4.hold_mode(conviction)
        return {**context, 'conviction': conviction, 'mode': hold['mode'],
                'max_hold_minutes': hold['max_hold_minutes'], 'target_pct': hold['target_pct'],
                'trail_arm_pct': hold['trail_arm_pct'], 'trail_pct': hold['trail_pct'],
                'conviction_score_version': EXIT_CONTEXT_SCORE_VERSION,
                # The engine's own entry conviction (SCORE_VERSION), for reference only.
                'entry_conviction': context.get('conviction'), 'entry_score_version': SCORE_VERSION}

    def _quote_unavailable(self, position, session, reason, error='no_sell_route', forensics=None):
        with STATE.lock:
            live = next((p for p in STATE.positions if p.get('id') == position.get('id')), None)
            if live is None or STATE.demo_session_id != session: return
            if forensics is not None:
                # Keep the quote that fired EXIT_IMPACT_EMERGENCY with the pending
                # exit so the eventual close can still record it as the trigger.
                live['exit_impact_emergency'] = forensics
            attempts = int(live.get('exit_retry_count') or 0) + 1
            delay = min(60_000, 1000 * 2 ** min(attempts-1, 6))
            quote_at = num(live.get('execution_quote_at'), num(live.get('updated_at')))
            live.update(quote_status='unavailable', valuation_status='unavailable',
                        exit_state='UNSELLABLE' if attempts >= 6 else ('PENDING_EXIT' if reason else 'VALUATION_UNAVAILABLE'),
                        quote_error=error, quote_error_at=now_ms(), exit_retry_count=attempts,
                        next_exit_retry_at=now_ms()+delay,
                        last_known_pnl_usd=live.get('pnl_usd'), valuation_age_ms=max(0, now_ms()-quote_at),
                        conservative_risk_usd=num(live.get('capital_committed_usd'), num(live.get('notional_usd'))))
            if reason: live['pending_exit_reason'] = reason
            if attempts == 1 or attempts == 6:
                STATE.event('Unavailable sell route; PAPER exposure retained, bounded retries scheduled.')
            STATE.save()

    def book_paper_exit(self, position, quote, reason, coin=None):
        """Book a simulated sale once; partial legs aggregate into one completed trade.

        No submission/signing is reachable here. Net proceeds already include
        quote AMM fees/impact and the exit network fee; entry costs are allocated
        once to each disposed fraction of the original position.
        """
        coin = coin or {}
        with STATE.lock:
            live = next((p for p in STATE.positions if p.get('id') == position.get('id')), None)
            if live is None or position.get('session_id', STATE.demo_session_id) != STATE.demo_session_id: return None
            if any(t.get('id') == live.get('id') for t in STATE.history): return None
            raw = int(live.get('jupiter_token_raw_amount') or 0)
            sold_raw = int(quote.get('token_input_raw') or raw)
            if raw and not 0 < sold_raw <= raw: raise ValueError('exit raw quantity exceeds position')
            fraction = sold_raw/raw if raw else 1.0
            notional = num(live.get('notional_usd'))
            entry_cost = num(live.get('entry_network_fee_usd')) + num(live.get('entry_account_reserve_usd'))
            allocated = (notional+entry_cost)*fraction
            net = num(quote.get('net_proceeds_usd'), math.nan)
            if not math.isfinite(net): raise ValueError('invalid exit net proceeds')
            pnl = net-allocated
            total_pnl = num(live.get('realized_partial_pnl_usd'))+pnl
            original_notional = num(live.get('original_notional_usd'), notional)
            before = STATE.demo_balance_usd
            balance = round(before+pnl, 8)
            leg = {'sold_token_raw': sold_raw, 'fraction_remaining_sold': fraction, 'net_proceeds_usd': net,
                   'allocated_entry_cost_usd': allocated, 'pnl_usd': pnl, 'quoted_at': quote.get('quoted_at'),
                   'execution_source': quote.get('execution_source'), 'exit_network_fee_usd': num(quote.get('network_fee_usd'))}
            legs = list(live.get('partial_fills') or [])+[leg]
            event_id = f"{live['id']}:{raw}:{sold_raw}:{quote.get('quoted_at',now_ms())}"
            if fraction < 1:
                remaining = dict(live, quantity=num(live.get('quantity'))*(1-fraction),
                    jupiter_token_raw_amount=raw-sold_raw, notional_usd=notional*(1-fraction),
                    entry_network_fee_usd=num(live.get('entry_network_fee_usd'))*(1-fraction),
                    entry_account_reserve_usd=num(live.get('entry_account_reserve_usd'))*(1-fraction),
                    capital_committed_usd=(notional+entry_cost)*(1-fraction), original_notional_usd=original_notional,
                    realized_partial_pnl_usd=total_pnl, partial_fills=legs, pending_exit_reason=reason,
                    pnl_usd=num(live.get('pnl_usd'))*(1-fraction), planned_risk_usd=num(live.get('planned_risk_usd'))*(1-fraction))
                positions = [remaining if p.get('id') == live['id'] else p for p in STATE.positions]
                STATE.commit('PARTIAL_EXIT', dict(leg, id=event_id, position_id=live['id']),
                             positions=positions, demo_balance_usd=balance)
                return remaining
            closed = dict(position, id=live['id'], session_id=STATE.demo_session_id,
                notional_usd=original_notional, pnl_usd=round(total_pnl, 8), pnl_pct=round(total_pnl/max(original_notional,1e-18)*100, 8),
                closed_at=now_ms(), exit_price=coin.get('priceUsd'), execution_exit_price=quote.get('fill_price'),
                exit_reason=reason, exit_net_proceeds_usd=net, exit_gross_proceeds_usd=quote.get('gross_proceeds_usd'),
                exit_dex_fee_usd=num(quote.get('dex_fee_usd')), exit_network_fee_usd=num(quote.get('network_fee_usd')),
                exit_price_impact_pct=num(quote.get('impact_pct')), exit_slippage_pct=num(quote.get('slippage_pct')),
                exit_quote=quote.get('raw_quote'), exit_liquidity_usd=coin.get('liquidityUsd'), partial_fills=legs,
                balance_before=before, balance_after=balance, paper_stop_capped=False, simulated_fill=True,
                jupiter_exit_quote_at=quote.get('quoted_at'), exit_policy_version=position.get('exit_policy_version', exit_policy.VERSION),
                exit_route_matches_entry_pool=quote.get('route_matches_entry_pool'), fees_included_in_quote=True,
                execution_source=quote.get('execution_source', 'MODEL_V1'), exit_state='CLOSED')
            STATE.commit('EXIT', closed, demo_balance_usd=balance,
                         positions=[p for p in STATE.positions if p.get('id') != live['id']], history=[closed]+STATE.history)
            STATE.event(f"PAPER EXIT #{closed.get('trade_no')} {closed.get('symbol')} · {reason} · {closed['pnl_pct']:+.2f}% net")
            return closed

    def update_positions(self, by_address: dict[str, dict[str, Any]]) -> None:
        # Called under position_lock; never hold STATE.lock across network calls.
        with STATE.lock:
            positions = [dict(p) for p in STATE.positions]
            session = STATE.demo_session_id
            pinned = {k:dict(v) for k,v in STATE.position_market.items()}
        for position in positions:
            address, pair = position.get('address'), position.get('pairAddress')
            key = f'{address}:{pair}'
            stamp = now_ms()
            # Pinned observations survive a closed trade and can be older than
            # a new entry in the same pool. Use the newest real observation,
            # never the tuple-key priority or a timestamp synthesized at entry.
            candidates = [by_address.get((address,pair)), by_address.get(address),
                          pinned.get(key), position.get('coin_snapshot')]
            observations = [item for item in candidates if isinstance(item,dict)
                and item.get('address') == address and item.get('pairAddress') == pair
                and 0 < num(item.get('updatedAt')) <= stamp]
            coin = max(observations,key=lambda item:num(item.get('updatedAt')),default={})
            reason = position.get('pending_exit_reason')
            quote_at = num(position.get('execution_quote_at'), num(position.get('updated_at')))
            if stamp < num(position.get('next_exit_retry_at')):
                with STATE.lock:
                    live = next((p for p in STATE.positions if p.get('id') == position.get('id')),None)
                    if live is not None: live['valuation_age_ms'] = max(0, stamp-quote_at)
                continue
            fresh_market = 0 <= stamp-num(coin.get('updatedAt')) <= entry_policy.MAX_FEED_AGE_MS
            if fresh_market and num(coin.get('liquidityUsd')) < num(position.get('entry_liquidity_usd'))*.80:
                reason = reason or 'LIQUIDITY_EMERGENCY'
            if not fresh_market and stamp-num(coin.get('updatedAt')) > 60_000:
                reason = reason or 'STALE_MARKET_EXIT'
            is_quote = position.get('execution_mode') in {'JUPITER_QUOTE_V2', 'PUMPSWAP_RPC_ENTRY_V1'}
            sol_usd = sol_usd_from_coin(coin) or sol_usd_market_price()
            network = max(.03, NETWORK_FEE_SOL*sol_usd)
            # Every quote-backed position uses one full token->USDC route.
            # Native token->SOL reserve marks remain diagnostics and cannot
            # invent realized USDC or add a second request on route failure.
            quote = (paper_quotes.position_mark(position,coin,network,force=bool(reason)) if is_quote
                     else exit_execution(coin,num(position.get('quantity'))) if fresh_market else None)
            mark_valid = quote is not None and math.isfinite(num(quote.get('net_proceeds_usd'),math.nan)) and (not is_quote or 0 <= now_ms()-num(quote.get('quoted_at')) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS)
            if training_bridge.enabled():
                training_bridge.observe(coin,STATE.live_flow(address,pair_address=pair),
                    safety=rug_guard.check(coin),validation=price_integrity.check(coin),
                    context=self.training_context(coin,position),
                    quotes={'mark':quote} if mark_valid else None, reasons=['exit_quote'] if not mark_valid else None,
                    now=now_ms())
            if not mark_valid:
                self._quote_unavailable(position,session,reason)
                continue
            notional = num(position.get('notional_usd'))
            entry_cost = num(position.get('entry_network_fee_usd'))+num(position.get('entry_account_reserve_usd'))
            pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
            pct = pnl/max(notional,1e-18)*100
            peak_pct = max(pct,num(position.get('peak_net_pnl_pct'), pct))
            hold = (now_ms()-int(position.get('opened_at',now_ms())))/60000
            policy = str(position.get('exit_policy') or 'fixed')
            # A position keeps the exit policy it was opened with. cost_first reuses the
            # fixed net geometry; only its impact emergency (V2) differs.
            decision_policy = cost_first_profile.EXIT_GEOMETRY_POLICY if policy == cost_first_profile.EXIT_POLICY else policy
            context = self.market_context(coin,position) if policy == 'adaptive' else {}
            if policy == 'adaptive':
                context = adaptive_exit_context(position, context)
            reason = reason or exit_policy.exit_reason(position, context, net_pct=pct,peak_net_pct=peak_pct,
                hold_minutes=hold,stop_pct=STOP_LOSS_PCT,take_profit_pct=TAKE_PROFIT_PCT,policy=decision_policy)
            emergency_v2_state = None
            if policy == cost_first_profile.EXIT_POLICY:
                v2 = self.exit_impact_emergency_v2(position, quote, coin, network, reason, is_quote, context,
                                                   notional=notional, entry_cost=entry_cost, peak_pct=peak_pct, hold=hold)
                reason, quote, pnl, pct, peak_pct = v2['reason'], v2['quote'], v2['pnl'], v2['pct'], v2['peak_pct']
                emergency_threshold, emergency_forensics = v2['threshold_pct'], v2['forensics']
                emergency_v2_state = v2['state']
            else:
                emergency_threshold = max(EXIT_IMPACT_EMERGENCY_PCT,num(position.get('entry_price_impact_pct'))+.50)
                emergency_forensics = position.get('exit_impact_emergency') if reason == 'EXIT_IMPACT_EMERGENCY' else None
                if num(quote.get('impact_pct')) >= emergency_threshold:
                    if not reason:
                        reason = 'EXIT_IMPACT_EMERGENCY'
                        # Forensics only: keep the sell quote that fired the unchanged rule.
                        emergency_forensics = exit_impact_emergency_record(position,quote,coin,emergency_threshold,now_ms())
                if reason == 'EXIT_IMPACT_EMERGENCY' and emergency_forensics is None:
                    # A pending emergency from an older position record: the re-quote is all we have.
                    emergency_forensics = exit_impact_emergency_record(position,quote,coin,emergency_threshold,now_ms(),
                                                                       trigger_is_requote=True)
            if reason and is_quote and quote.get('from_cache'):
                quote = paper_quotes.position_mark(position,coin,network,force=True)
                if quote is None:
                    self._quote_unavailable(position,session,reason,forensics=emergency_forensics)
                    continue
                if not math.isfinite(num(quote.get('net_proceeds_usd'),math.nan)):
                    self._quote_unavailable(position,session,reason,'invalid_sell_quote',forensics=emergency_forensics)
                    continue
                if not 0 <= now_ms()-num(quote.get('quoted_at')) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS:
                    self._quote_unavailable(position,session,reason,'stale_sell_quote',forensics=emergency_forensics)
                    continue
                pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
                pct = pnl/max(notional,1e-18)*100
                peak_pct = max(peak_pct,pct)
                # A profit signal must still hold at the actual simulated sale.
                if reason.startswith(('TAKE_PROFIT', 'ADAPTIVE_TP', 'ADAPTIVE_TRAILING', 'CONVICTION_PROFIT')):
                    reason = exit_policy.exit_reason(position,context,net_pct=pct,peak_net_pct=peak_pct,
                        hold_minutes=hold,stop_pct=STOP_LOSS_PCT,take_profit_pct=TAKE_PROFIT_PCT,policy=decision_policy)
            if emergency_forensics is not None:
                booked = compact_exit_quote_evidence(quote)
                trigger = emergency_forensics.get('trigger_quote') or {}
                if (booked.get('quoted_at'), booked.get('from_cache')) != (trigger.get('quoted_at'), trigger.get('from_cache')):
                    emergency_forensics = dict(emergency_forensics, confirming_quote=booked, booked_quote='confirming',
                        confirming_quote_meets_threshold=num(quote.get('impact_pct')) >= emergency_threshold)
                emergency_forensics = dict(emergency_forensics, booked_impact_pct=num(quote.get('impact_pct')),
                    liquidity=dict(emergency_forensics.get('liquidity') or {}, exit_usd=coin.get('liquidityUsd'),
                        exit_observed_at=coin.get('updatedAt'),
                        exit_to_entry_ratio=(round(num(coin.get('liquidityUsd'))/num(position.get('entry_liquidity_usd')),4)
                            if num(position.get('entry_liquidity_usd')) > 0 and coin.get('liquidityUsd') is not None else None)))
            market = num(coin.get('priceUsd'), num(position.get('current_price')))
            entry = num(position.get('entry_price'))
            updated = dict(position, current_price=market,peak_price=max(market,num(position.get('peak_price'))),
                pnl_usd=round(pnl,8),pnl_pct=round(pct,8),peak_net_pnl_pct=peak_pct,
                mfe_net_pct=max(peak_pct,num(position.get('mfe_net_pct'),pct)),
                mae_net_pct=min(pct,num(position.get('mae_net_pct'),pct)),
                signal_pnl_pct=(market-entry)/entry*100 if entry else None,
                active_stop_signal_trigger_pct=None, legacy_chart_stop_ignored=position.get('stop_signal_trigger_pct'),
                planned_stop_net_pct=-STOP_LOSS_PCT,hard_stop_net_pct=None,paper_stop_capped=False,
                current_execution_price=quote.get('fill_price'), quote_status='fresh', valuation_status='available',
                valuation_age_ms=max(0,now_ms()-num(quote.get('quoted_at'),now_ms())), last_known_pnl_usd=pnl,
                conservative_risk_usd=notional+entry_cost, execution_quote_at=quote.get('quoted_at',now_ms()),
                execution_quote_source=quote.get('execution_source','MODEL_V1'), updated_at=now_ms(),
                pending_exit_reason=reason,exit_state='PENDING_EXIT' if reason else 'OPEN',exit_retry_count=0,next_exit_retry_at=0,
                exit_policy_version=position_exit_policy_version(policy),
                market_context=context, estimated_exit_dex_fee_usd=num(quote.get('dex_fee_usd')),
                estimated_exit_network_fee_usd=num(quote.get('network_fee_usd')),
                estimated_exit_price_impact_pct=num(quote.get('impact_pct')),
                estimated_exit_slippage_pct=num(quote.get('slippage_pct'))+num(quote.get('latency_pct')))
            if emergency_forensics is not None:
                updated['exit_impact_emergency'] = emergency_forensics
            if emergency_v2_state is not None:
                updated['exit_impact_emergency_v2'] = emergency_v2_state
            with STATE.lock:
                live = next((p for p in STATE.positions if p.get('id') == position.get('id')),None)
                if live is None or STATE.demo_session_id != session: continue
                if not reason:
                    live.update(updated)
                    continue
            self.book_paper_exit(updated,quote,reason,coin)

    def exit_impact_emergency_v2(self, position, quote, coin, network, reason, is_quote, context, *,
                                 notional, entry_cost, peak_pct, hold):
        """EXIT_IMPACT_EMERGENCY_V2 for cost_first positions: sell-anchored, arm then confirm.

        The first mark at/above the threshold only arms. After confirm_delay_ms a fresh
        forced exact-pool sell quote decides: still at/above the threshold exits with that
        quote, anything else disarms (and is recorded). Stop, target, max hold and the
        liquidity/stale safety exits always take priority; V2 never overrides them.
        """
        params = cost_first_profile.EIE_V2
        rule = cost_first_profile.emergency_threshold(position, params)
        threshold = rule['threshold_pct']
        state = dict(position.get('exit_impact_emergency_v2')
                     or {'version': cost_first_profile.EIE_VERSION, 'armed': False, 'armed_at': None,
                         'arm_count': 0, 'disarm_count': 0, 'disarms': []})
        pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
        out = {'reason': reason, 'quote': quote, 'pnl': pnl, 'pct': pnl/max(notional,1e-18)*100,
               'peak_pct': peak_pct, 'threshold_pct': threshold, 'forensics': None, 'state': state}
        if reason == cost_first_profile.EIE_REASON:
            # A confirmed V2 emergency whose sale is being retried keeps its evidence.
            forensics = position.get('exit_impact_emergency')
            if forensics is None:
                forensics = dict(exit_impact_emergency_record(position, quote, coin, threshold, now_ms(),
                                                              trigger_is_requote=True),
                                 rule_version=cost_first_profile.EIE_VERSION, rule=cost_first_profile.EIE_V2_RULE,
                                 rule_changed=True, threshold_anchor=rule['anchor'])
            out['forensics'] = forensics
            return out
        if reason:
            if state.get('armed'):
                out['state'] = {**state, 'armed': False, 'superseded_by': reason, 'superseded_at': now_ms()}
            return out
        if not state.get('armed'):
            if num(quote.get('impact_pct')) >= threshold:
                # Only an exact-entry-pool mark can ever confirm, and every confirmation
                # spends a forced exit-priority quote from the shared budget: an off-pool
                # mark, a mark inside the re-arm cooldown or beyond the per-position
                # confirmation budget is counted without arming.
                blocked = cost_first_profile.arm_block(state, quote, now=now_ms(), params=params)
                if blocked:
                    out['state'] = cost_first_profile.blocked_trigger(state, code=blocked, now=now_ms(),
                                                                      impact_pct=quote.get('impact_pct'))
                    return out
                out['state'] = {**state, **rule, 'armed': True, 'armed_at': now_ms(),
                                'arm_count': int(state.get('arm_count') or 0) + 1,
                                'trigger_quote': compact_exit_quote_evidence(quote)}
            return out
        armed_at = num(state.get('armed_at'))
        if now_ms()-armed_at < params.confirm_delay_ms:
            return out
        confirm = paper_quotes.position_mark(position, coin, network, force=True) if is_quote else None
        if is_quote:
            state = cost_first_profile.confirm_attempted(state, now_ms(), params)
            out['state'] = state
        problem = cost_first_profile.confirm_quote_problem(
            confirm, armed_at=armed_at, now=now_ms(), max_age_ms=entry_policy.MAX_ENTRY_QUOTE_AGE_MS,
            threshold_pct=threshold, params=params)
        compact_confirm = compact_exit_quote_evidence(confirm) if isinstance(confirm, dict) else None
        if cost_first_profile.confirm_quote_is_mark(confirm, problem, now=now_ms(),
                                                    max_age_ms=entry_policy.MAX_ENTRY_QUOTE_AGE_MS):
            # Any finite, fresh confirmation quote is the newest valid mark: the net
            # geometry (stop first, then target and max hold) is evaluated on it before
            # the emergency decides or disarms.
            pnl = num(confirm.get('net_proceeds_usd'))-notional-entry_cost
            pct = pnl/max(notional,1e-18)*100
            out.update(quote=confirm, pnl=pnl, pct=pct, peak_pct=max(peak_pct, pct))
            decided = exit_policy.exit_reason(position, context, net_pct=pct, peak_net_pct=out['peak_pct'],
                hold_minutes=hold, stop_pct=STOP_LOSS_PCT, take_profit_pct=TAKE_PROFIT_PCT,
                policy=cost_first_profile.EXIT_GEOMETRY_POLICY)
            if decided:
                out['reason'] = decided
                out['state'] = {**state, 'armed': False, 'superseded_by': decided, 'superseded_at': now_ms(),
                                'confirm_quote': compact_confirm}
                return out
        if problem is None:
            confirmed_at = now_ms()
            record = exit_impact_emergency_record(position, confirm, coin, threshold, int(armed_at))
            record.update(
                rule_version=cost_first_profile.EIE_VERSION, rule=cost_first_profile.EIE_V2_RULE, rule_changed=True,
                threshold_anchor=state.get('anchor'), anchor_source=state.get('anchor_source'),
                anchor_impact_pct=state.get('anchor_impact_pct'), entry_margin_pct=params.entry_margin_pct,
                trigger_quote=state.get('trigger_quote'), trigger_is_requote=False, armed_at=int(armed_at),
                confirmed_at=confirmed_at, confirm_delay_ms=num(confirm.get('quoted_at'))-armed_at,
                confirming_quote=compact_confirm, confirming_quote_meets_threshold=True, booked_quote='confirming')
            out.update(reason=cost_first_profile.EIE_REASON, forensics=record,
                       state={**state, 'armed': False, 'confirmed_at': confirmed_at, 'confirm_quote': compact_confirm})
            return out
        out['state'] = cost_first_profile.disarmed(state, code=problem, now=now_ms(), confirm=compact_confirm,
                                                   params=params)
        return out

    def fast_position_check(self) -> None:
        if not self.position_lock.acquire(blocking=False): return
        try:
            with STATE.lock:
                coins={c['address']:dict(c) for c in STATE.feed}
                for key, coin in STATE.position_market.items():
                    coins[(coin.get('address'),coin.get('pairAddress'))]=dict(coin)
            # Quote the held raw token amount directly; do not block a stop on a
            # slow DexScreener discovery request or select a different chart pool.
            self.update_positions(coins)
            with STATE.lock: STATE.save()
        except Exception as exc:
            with STATE.lock: STATE.event('Проверка на позицията: '+type(exc).__name__)
        finally:
            self.position_lock.release()

    def run_position_guard(self) -> None:
        while not self.stop_event.is_set():
            if STATE.positions: self.fast_position_check()
            if STATE.running:
                with STATE.lock: feed=[dict(c) for c in STATE.feed]
                self.maybe_open(feed)
            self.stop_event.wait(POSITION_SCAN_SECONDS)

    def maybe_open(self, feed: list[dict[str, Any]]) -> None:
        if not STATE.running or not self.entry_lock.acquire(blocking=False): return
        report = {'policy_version': ENTRY_POLICY_VERSION, 'signal_strategy': SIGNAL_STRATEGY, 'checked_at': now_ms(),
                  'candidates': len(feed), 'evaluated': 0, 'signal_passed': 0,
                  'quoted': 0, 'quote_attempts': 0, 'quote_defers': 0, 'size_retries': 0, 'opened': 0,
                  'rejections': {}, 'examples': [], 'max_positions': MAX_POSITIONS,
                  'max_quote_attempts_per_scan': MAX_QUOTED_CANDIDATES,
                  'quote_preparation_codes': {},
                  'defensive_entry': entry_defense.new_summary(), 'score_version': SCORE_VERSION}
        # The decision feed is part of the pair history and ticker registry
        # (idempotent: an already observed coin snapshot adds nothing).
        self.observe_entry_defense(feed, report['checked_at'])
        registry = self.defense.registry
        report['defensive_entry']['layer'] = self.defense.status()
        estimates = [market_feasibility.execution_feasibility(
                         coin, STRICT_MAX_ROUNDTRIP_COST_PCT,
                         base_slippage_bps=0, latency_buffer_bps=0)
                     for coin in feed
                     if not entry_policy.signal_data_rejections(coin, now=report['checked_at'])
                     and market_candidate(coin, report['checked_at'], registry)]
        report['market_cost_feasibility'] = {
            'basis': 'OPTIMISTIC_PAPER_FEE_AND_BUFFER_MODEL', 'is_execution_quote': False,
            'checked_market_candidates': len(estimates),
            'fixed_cost_infeasible_candidates': sum(row['model_cost_feasible'] is False for row in estimates),
            'unknown_candidates': sum(row['model_cost_feasible'] is None for row in estimates),
            'maximum_roundtrip_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
            'minimum_model_roundtrip_cost_pct': min(
                (row['minimum_model_roundtrip_cost_pct'] for row in estimates
                 if row['minimum_model_roundtrip_cost_pct'] is not None), default=None),
            'excluded_costs': ['price_impact', 'network_fees', 'rent'],
            'quotes_still_required': True, 'profitability_proven': False,
        }
        try:
            self._maybe_open_checked(feed, report)
        except Exception as exc:
            entry_policy.record(report,['entry_error'],metrics={'type':type(exc).__name__})
        finally:
            with STATE.lock:
                STATE.entry_diagnostics = entry_policy.finish(self._finish_quote_preparation_histograms(report))
            self.entry_lock.release()
        # Learner route checks run in a separate daemon and only consume the
        # shared, low-priority quote path after main-account entry evaluation.
        self.schedule_training_quote_probe(feed)

    def schedule_training_quote_probe(self, feed: list[dict[str, Any]]) -> None:
        if not training_bridge.enabled():
            return
        with STATE.lock:
            if not STATE.running or STATE.positions:
                return
        stamp = now_ms()
        with self.training_probe_lock:
            if (self.training_probe_inflight
                    or stamp-self.training_probe_last_attempt_at < TRAINING_QUOTE_PROBE_INTERVAL_MS):
                return
        blocked_reason = None
        qualified_flow = False
        blocked_pools = self.entry_loss_index(stamp)
        for coin in feed:
            mint, pair = str(coin.get('address') or ''), str(coin.get('pairAddress') or '')
            if not mint or not pair:
                continue
            key = (mint, pair)
            if self.training_probe_retry_after.get(key, 0) > stamp:
                blocked_reason = blocked_reason or 'preflight_retry_cooldown'
                continue
            flow = STATE.live_flow(mint, 30, pair)
            if not training_candidate_signal(coin, flow, now=stamp):
                continue
            qualified_flow = True
            # DEFENSIVE_ENTRY_LAYER_V1 before the price and RugCheck calls and
            # before any probe quote; a blocked pool spends nothing.
            defensive = self.defensive_entry_decision(coin, stamp, blocked_pools=blocked_pools)
            if not defensive['allowed']:
                blocked_reason = blocked_reason or (defensive['reasons'] or ['defensive_entry'])[0]
                continue
            # These checks are cached/non-blocking. The only quote work below
            # is placed on its own thread after both independent checks pass.
            validation = price_integrity.check(coin)
            safety = rug_guard.check(coin)
            current = now_ms()
            reason = self.training_probe_preflight_reason(coin, safety, validation, now=current)
            if reason:
                blocked_reason = blocked_reason or reason
                self.training_probe_retry_after[key] = current + TRAINING_PREFLIGHT_RETRY_MS
                continue
            if not training_candidate_signal(coin, flow, now=current):
                blocked_reason = blocked_reason or 'signal_or_flow_expired'
                continue
            with self.training_probe_lock:
                current = now_ms()
                if (self.training_probe_inflight
                        or current-self.training_probe_last_attempt_at < TRAINING_QUOTE_PROBE_INTERVAL_MS):
                    return
                reason = self.training_probe_preflight_reason(coin, safety, validation, now=current)
                if reason or not training_candidate_signal(coin, flow, now=current):
                    blocked_reason = blocked_reason or reason or 'signal_or_flow_expired'
                    self.training_probe_retry_after[key] = current + TRAINING_PREFLIGHT_RETRY_MS
                    continue
                self.training_probe_inflight = True
                self.training_probe_last_attempt_at = current
            training_bridge.note_quote_probe('PREFLIGHT_PASSED', attempted=True, at=current)
            worker = threading.Thread(
                target=self.run_training_quote_probe,
                args=(dict(coin), copy.deepcopy(flow), copy.deepcopy(safety),
                      copy.deepcopy(validation)),
                name='neo-training-route-probe', daemon=True)
            try:
                worker.start()
            except RuntimeError:
                with self.training_probe_lock:
                    self.training_probe_inflight = False
                training_bridge.note_quote_probe('WORKER_START_FAILED', reason='worker_start_failed', at=now_ms())
            return
        if blocked_reason:
            training_bridge.note_quote_probe('WAITING_FOR_FRESH_PREFLIGHT',
                                             reason=blocked_reason, at=now_ms())
        elif not qualified_flow:
            training_bridge.note_quote_probe('WAITING_FOR_QUALIFIED_FLOW', at=now_ms())

    @staticmethod
    def training_probe_preflight_reason(coin, safety, validation, *, now):
        """Admit only proof that the read-only collector can actually use.

        Risk cache passes can outlive the learner's shorter evidence window.
        Preserve their original timestamps instead of calling an old pass a
        successful preflight or spending a probe on known missing cost proof.
        The collector rechecks these facts when its worker starts.
        """
        mint, pair = coin.get('address'), coin.get('pairAddress')
        ttl = TRAINING_DEFAULT_CONFIG['evidence_ttl_ms']
        if (not isinstance(safety, dict) or safety.get('status') != 'pass'
                or safety.get('provisional_early') or safety.get('mint') != mint
                or safety.get('pair') != pair
                or not 0 <= now-num(safety.get('checked_at'), -1) <= ttl):
            return 'safety_not_fresh_pass'
        metrics = safety.get('metrics') or {}
        if not isinstance(metrics, dict):
            return 'rent_or_sol_cost_unknown'
        rent = num(metrics.get('token_account_rent_lamports'), math.nan)
        sol = num(metrics.get('sol_usd'), math.nan)
        if not math.isfinite(rent) or rent <= 0 or not math.isfinite(sol) or sol <= 0:
            return 'rent_or_sol_cost_unknown'
        if (not isinstance(validation, dict) or validation.get('status') != 'pass'
                or validation.get('mint') != mint or validation.get('pair') != pair
                or num(validation.get('reference_price')) <= 0
                or not 0 <= now-num(validation.get('reference_received_at'), -1) <= ttl):
            return 'independent_price_not_fresh_pass'
        return None

    def run_training_quote_probe(self, coin, flow, safety, validation) -> None:
        mint, pair = str(coin.get('address') or ''), str(coin.get('pairAddress') or '')
        try:
            with STATE.lock:
                current = next((dict(item) for item in STATE.feed
                                if item.get('address') == mint and item.get('pairAddress') == pair), None)
                if STATE.positions or not STATE.running or current is None:
                    training_bridge.note_quote_probe('SKIPPED', reason='account_state_changed', at=now_ms())
                    return
            now = now_ms()
            current_flow = STATE.live_flow(mint, 30, pair)
            if not training_candidate_signal(current, current_flow, now=now):
                training_bridge.note_quote_probe('SKIPPED', reason='signal_or_flow_expired', at=now)
                return
            defensive = self.defensive_entry_decision(current, now)
            if not defensive['allowed']:
                training_bridge.note_quote_probe(
                    'SKIPPED', reason=(defensive['reasons'] or ['defensive_entry'])[0], at=now)
                return
            quotes, reason = collect_exact_pool_quotes(
                current, current_flow, safety, validation, now=now)
            if not quotes:
                # Preserve the failed preflight as a rejected observation. It
                # cannot become an executed trade or reuse an older route.
                training_bridge.observe(current, current_flow, safety=safety,
                    validation=validation, reasons=[reason, 'entry_quote'],
                    context=self.training_context(current), now=now_ms())
                training_bridge.note_quote_probe('ROUTE_REJECTED', reason=reason, at=now_ms())
                return
            stamp = now_ms()
            if (not training_candidate_signal(current, current_flow, now=stamp)
                    or not 0 <= stamp-num(current.get('updatedAt'), -1) <= TRAINING_DEFAULT_CONFIG['feed_ttl_ms']):
                training_bridge.note_quote_probe('SKIPPED', reason='signal_or_market_expired', at=stamp)
                return
            recorded = training_bridge.observe(
                current, current_flow, safety=safety, validation=validation,
                quotes=quotes, context=self.training_context(current), now=stamp)
            training_bridge.note_quote_probe(
                'ROUTE_EVIDENCE_RECORDED' if recorded else 'RECORDING_REFUSED',
                reason='' if recorded else 'observation_not_queued', at=stamp,
                success=bool(recorded))
        except Exception as exc:
            training_bridge.note_quote_probe('ROUTE_REJECTED', reason=type(exc).__name__, at=now_ms())
        finally:
            with self.training_probe_lock:
                self.training_probe_inflight = False

    def _maybe_open_checked(self, feed: list[dict[str, Any]], report: dict[str, Any]) -> None:
        def reject(report, reasons, coin=None, metrics=None):
            entry_policy.record(report,reasons,coin,metrics)
            if coin:
                training_bridge.observe(coin,STATE.live_flow(coin.get('address'),pair_address=coin.get('pairAddress')),
                    reasons=reasons,now=now_ms())
        session_at_check = STATE.demo_session_id
        if STATE.pending_audit:
            with STATE.lock: STATE.save()
            if STATE.pending_audit:
                reject(report, ['audit_pending'])
                return
        STATE.refresh_risk_day()
        if MAX_DAILY_LOSS_USD > 0 and STATE.risk_day_pnl() <= -MAX_DAILY_LOSS_USD:
            reject(report, ['daily_limit'])
            return
        if any(p.get('valuation_status') == 'unavailable' for p in STATE.positions):
            reject(report,['liquidation_unavailable'])
            return
        if MAX_DRAWDOWN_PCT > 0 and (1-STATE.equity_usd()/max(STATE.equity_peak_usd,1))*100 >= MAX_DRAWDOWN_PCT:
            reject(report,['drawdown_limit'])
            return
        if len(STATE.positions) >= MAX_POSITIONS:
            reject(report, ['position_open'])
            return
        if STATE.available_balance_usd() < min(TRADE_NOTIONAL_USD, 10.0):
            reject(report, ['balance'])
            return
        open_addresses = {p.get('address') for p in STATE.positions}
        now = now_ms()
        # Keep per-token cooldown so high frequency does not become revenge re-entry.
        recent = {t.get('address') for t in STATE.history if now-int(t.get('closed_at',0))<SAME_TOKEN_COOLDOWN_SECONDS*1000}
        registry = self.defense.registry
        # POOL_LOSS_MEMORY_V1 from this account's own closed history, once per scan.
        blocked_pools = self.entry_loss_index(now)
        defensive_summary = report.setdefault('defensive_entry', entry_defense.new_summary())
        defensive_summary['pool_loss_cooldown_pools'] = len(blocked_pools)
        prioritized_feed = entry_quote_priority.prioritize_entry_candidates(
            feed, STRICT_MAX_ROUNDTRIP_COST_PCT,
            is_candidate=lambda coin: market_candidate(coin, now, registry)
                and not entry_policy.signal_data_rejections(coin, now=now),
        )
        for coin in prioritized_feed:
            if len(STATE.positions) >= MAX_POSITIONS:
                break
            address = coin.get('address')
            if not address or address in open_addresses or address in recent:
                reject(report, ['cooldown'], coin)
                continue
            report['evaluated'] += 1
            score = num(coin.get('score'))
            liquidity = num(coin.get('liquidityUsd'))
            age = num(coin.get('ageMinutes'), 999999)
            change_m5 = num((coin.get('priceChange') or {}).get('m5'))
            tx_m5 = (coin.get('txns') or {}).get('m5') or {}
            buys_m5 = num(tx_m5.get('buys'))
            sells_m5 = num(tx_m5.get('sells'))
            buy_sell_ratio = buys_m5 / max(sells_m5, 1.0)
            market_cap = num(coin.get('marketCap') or coin.get('fdv'))
            liquidity_mc_ratio = liquidity / max(market_cap, 1.0)

            rejected = entry_policy.signal_data_rejections(coin, now=now_ms())
            if rejected:
                reject(report, rejected, coin)
                continue
            if ADAPTIVE_PROFILE is not None:
                market_rejected = oct4.market_rejections(coin, now=now_ms(), profile=ADAPTIVE_PROFILE)
                if market_rejected:
                    reject(report, market_rejected, coin, oct4.signal_metrics(coin, {}, {}))
                    continue
                market_candidates = [oct4.STRATEGY_ID]
            elif COST_FIRST_ACTIVE:
                # The cost-first universe replaces the ensemble market screen; its
                # rejection names and observed values are kept as examples. It
                # includes STRUCTURAL_RUG_GUARD_V1 with this engine's registry.
                screen_at = now_ms()
                universe_rejected = cost_first_profile.universe_rejections(
                    coin, TRADE_NOTIONAL_USD, now=screen_at, ticker_registry=registry)
                if universe_rejected:
                    reject(report, universe_rejected, coin,
                           cost_first_profile.universe_metrics(
                               coin, TRADE_NOTIONAL_USD, now=screen_at, ticker_registry=registry))
                    continue
                market_candidates = [cost_first_profile.STRATEGY_ID]
            else:
                market_candidates = winner_ensemble.market_candidates(coin)
                if not market_candidates:
                    reject(report, ['winner_signal'], coin)
                    continue
            # DEFENSIVE_ENTRY_LAYER_V1 for every signal strategy, before the quote
            # retry state, flow promotion, the price and RugCheck calls and quotes.
            defensive = self.defensive_entry_decision(coin, now_ms(), blocked_pools=blocked_pools)
            entry_defense.record(defensive_summary, defensive, coin)
            if not defensive['allowed']:
                reject(report, defensive['reasons'], coin, entry_defense.metrics(defensive))
                continue
            retry_after = self.entry_quote_retry_after.get(address, 0)
            if retry_after > now_ms():
                reject(report, ['quote_retry_cooldown'], coin, {'retry_after_ms': retry_after})
                continue
            if retry_after:
                self.entry_quote_retry_after.pop(address, None)
            entry_mode = (oct4.ENTRY_POLICY_VERSION if ADAPTIVE_PROFILE is not None
                          else cost_first_profile.ENTRY_POLICY_VERSION if COST_FIRST_ACTIVE
                          else 'WINNER_ENSEMBLE_VERIFIED_FLOW')
            # The confirmed 30-second exact-pool window is the modern fail-closed
            # evidence gate for every strategy.
            flow = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
            flow_admission = promoted_guard.flow_admission(
                coin, {'verified_flow': flow.get('verified_flow')}, now_ms())
            if not flow_admission['allow']:
                reject(report, [flow_admission['reason']], coin)
                continue
            if ADAPTIVE_PROFILE is not None:
                # October 4 decisions: the 60-second exact-pool window and the
                # conviction model decide, with the historical rejection names.
                decision_flow = STATE.live_flow(address, oct4.ENTRY_FLOW_WINDOW_SECONDS, str(coin.get('pairAddress') or ''))
                context = self.market_context(coin)
                signal_rejected = oct4.signal_rejections(coin, decision_flow, context, now=now_ms(), profile=ADAPTIVE_PROFILE)
                if signal_rejected:
                    reject(report, signal_rejected, coin, oct4.signal_metrics(coin, decision_flow, context))
                    continue
                signal_age = now_ms() - num(coin.get('updatedAt'))
                if signal_age > paper_quotes.MAX_SIGNAL_AGE_MS - ADAPTIVE_QUOTE_LATENCY_MARGIN_MS:
                    # The 15 s feed cadence means a candidate ages past the 8 s commit
                    # limit between scans; wait for the next fresh scan instead.
                    reject(report, ['stale_signal'], coin, {'signal_age_ms': round(signal_age)})
                    continue
                raw_strategy_matches = strategy_matches = [oct4.STRATEGY_ID]
                policy_learning = dict(oct4.NO_LEARNING)
            elif COST_FIRST_ACTIVE:
                # No extra signal rule: the confirmed exact-pool flow gate above is the
                # evidence requirement; no outcome-based throttle (fixed hypothesis).
                decision_flow = flow
                raw_strategy_matches = strategy_matches = [cost_first_profile.STRATEGY_ID]
                policy_learning = dict(cost_first_profile.NO_LEARNING)
                context = self.market_context(coin)
            else:
                decision_flow = flow
                raw_strategy_matches = winner_ensemble.matches(coin, flow)
                with STATE.lock:
                    learning_history = list(STATE.history)
                strategy_matches, policy_learning = winner_ensemble.apply_learning(
                    raw_strategy_matches, learning_history)
                if not strategy_matches:
                    reason = 'loss_learning_hold' if raw_strategy_matches else 'winner_flow_signal'
                    reject(report, [reason], coin, {'market_candidates': market_candidates})
                    continue
                context = self.market_context(coin)
            report['signal_passed'] += 1
            # Start independent price and rug checks together. Both helpers are
            # cached/asynchronous; running them concurrently avoids serial provider
            # latency without weakening known-risk vetoes.
            validation=price_integrity.check(coin)
            safety=rug_guard.check(coin)
            training_bridge.observe(coin,flow,safety=safety,validation=validation,
                                    context=self.training_context(coin,context=context),now=now_ms())
            price_review=(
                validation.get('status')=='review'
                and validation.get('reason') in {
                    'price_source_disagreement_needs_jupiter',
                    'price_unavailable_needs_jupiter',
                    'price_crosscheck_pending_needs_jupiter',
                }
            )
            if validation.get('status')!='pass' and not price_review:
                reject(report,[validation.get('reason') or 'price_unavailable'],coin,validation)
                continue
            if safety.get('status') != 'pass' or safety.get('provisional_early'):
                reject(report, safety.get('reasons') or ['risk_check_pending'], coin)
                continue
            risk_admission = promoted_guard.risk_admission(
                coin, safety, now_ms(), promoted_guard.SAFETY_MAX_AGE_MS)
            if not risk_admission['allow']:
                reject(report, [risk_admission['reason']], coin)
                continue
            strategy_id = SIGNAL_STRATEGY
            # The ensemble produces candidate signals only. Verified flow and
            # safety above, followed by executable quote gates below, govern entry.
            # Training candidates remain isolated from this account.
            policy_losses = policy_learning['losses']
            learning = {
                'sample': policy_learning['closed_trades'],
                'win_rate': policy_learning['win_rate_pct'],
                'profit_factor': policy_learning['profit_factor'],
                'recent_losses': policy_losses,
                'bonus': 0, 'size_multiplier': 1.0,
                'avg_pnl_pct': None,
            }
            recovery = False
            price = num(coin.get('priceUsd'))
            if price <= 0:
                continue
            positive = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'positive'][:4]
            risks = [s['title'] for s in coin.get('signals', []) if s.get('kind') == 'risk'][:4]
            exposure_available = max(0,STATE.equity_usd()*MAX_TOTAL_EXPOSURE_PCT/100-STATE.reserved_usd())
            available_before = min(STATE.available_balance_usd(),exposure_available)
            sol_usd=num(safety.get('metrics',{}).get('sol_usd')) or sol_usd_from_coin(coin) or sol_usd_market_price()
            if sol_usd<=0:
                reject(report,['network_price_unknown'],coin); continue
            pre_network_fee = max(.03, NETWORK_FEE_SOL * sol_usd)
            seen_before=any(t.get('address')==address for t in STATE.history)
            rent_lamports=num(safety.get('metrics',{}).get('token_account_rent_lamports'))
            if not seen_before and rent_lamports<=0:
                reject(report,['risk_data_unavailable'],coin); continue
            entry_rent=0.0 if seen_before else rent_lamports/1e9*sol_usd
            # Fit cash, full-loss exposure and the configured daily allowance.
            fixed_cost_budget=2*pre_network_fee+entry_rent
            # Reserve each open position's planned stop budget separately from
            # the full-loss exposure used by the capital and position limits.
            open_planned_risk=sum(num(p.get('planned_risk_usd')) for p in STATE.positions)
            # October 4 sized every entry at the flat strategy notional; the ensemble
            # scales scouts by liquidity and age. Daily budget and exposure caps apply to both.
            # The cost-first profile applies the universe's liquidity size rule to the
            # engine notional; the daily-budget sizing below still applies to it.
            requested_base = (TRADE_NOTIONAL_USD if ADAPTIVE_PROFILE is not None
                              else cost_first_profile.requested_notional(coin, TRADE_NOTIONAL_USD) if COST_FIRST_ACTIVE
                              else early_requested_notional(coin, learning))
            requested_notional = min(requested_base,MAX_POSITION_RISK_USD-fixed_cost_budget)
            notional = runtime.plan_notional(
                requested_notional,available_before,MAX_DAILY_LOSS_USD,
                STATE.risk_day_pnl()-open_planned_risk,
                STOP_LOSS_PCT,STOP_EXECUTION_BUFFER_PCT,fixed_cost_budget,
            )
            if notional < 10:
                reject(report,['risk_budget_unavailable'],coin); return
            dex_id = str(coin.get('dexId') or '').lower()
            entry_network_fee=pre_network_fee
            if report['quote_attempts'] >= MAX_QUOTED_CANDIDATES:
                reject(report, ['quote_budget'], coin)
                continue
            report['quoted'] += 1
            # October 4 quoted the flat strategy notional once; the ensemble may
            # retry smaller sizes after a pure cost rejection.
            quote_sizes = [notional] if ADAPTIVE_PROFILE is not None else entry_size_backoff.quote_notional_steps(notional)
            retry_cause = {'cost': False, 'quote': False}

            def check_quote_size(attempt_notional, attempt_index):
                if report['quote_attempts'] >= MAX_QUOTED_CANDIDATES:
                    reject(report, ['quote_budget'], coin)
                    return None, ['quote_budget']
                if attempt_index:
                    report['size_retries'] += 1
                preparation_started_at = now_ms()
                if dex_id == 'pumpswap':
                    prepared = pumpswap_stop.prepare_entry(
                        coin, attempt_notional, sol_usd,
                        buffer_bps=paper_quotes.BUFFER_BPS,
                        slippage_bps=paper_quotes.SLIPPAGE_BPS,
                    )
                else:
                    prepared = paper_quotes.prepare_entry(
                        address, str(coin.get('pairAddress') or ''), attempt_notional
                    )
                if not prepared:
                    preparation_failure = paper_quotes.last_preparation_error()
                    failure_code = str(preparation_failure.get('code') or 'QUOTE_UNAVAILABLE')
                    # Another account's short entry bundle or a pending exit is a
                    # shared-budget defer, not bad token evidence and not a quote:
                    # it consumes no per-scan budget and arms no retry cooldown.
                    # Provider failures (TIMEOUT, RATE_LIMITED, route consistency)
                    # keep counting as attempts and arm the per-token cooldown.
                    deferred = failure_code in QUOTE_PREPARATION_DEFER_CODES
                    if deferred:
                        report['quote_defers'] += 1
                    else:
                        report['quote_attempts'] += 1
                    self._record_quote_preparation_failure(
                        report, coin, attempt_notional, attempt_index, preparation_failure,
                        failure_code, deferred, now_ms() - preparation_started_at, dex_id)
                    retry_cause['quote'] = not deferred
                    reject(report, ['quote_inconsistent'], coin,
                           {'notional_usd': attempt_notional, 'attempt': attempt_index + 1,
                            'quote_preparation': preparation_failure,
                            'counted_as_quote_attempt': not deferred})
                    return None, ['quote_inconsistent']
                report['quote_attempts'] += 1
                live_quote, initial_exit = prepared
                expected_token_raw = int(live_quote['token_raw_amount'])
                immediate_exit_net = max(0.0, num(initial_exit.get('expected_usdc')) - entry_network_fee)
                worst_case_exit_net = max(0.0, num(initial_exit.get('floor_usdc')) - entry_network_fee)
                immediate_roundtrip_pct = (
                    (immediate_exit_net - attempt_notional - entry_network_fee - entry_rent)
                    / max(attempt_notional, 1e-18)
                ) * 100.0
                worst_case_roundtrip_pct = (
                    (worst_case_exit_net - attempt_notional - entry_network_fee - entry_rent)
                    / max(attempt_notional, 1e-18)
                ) * 100.0
                impact_pct = num(live_quote.get('price_impact_pct'))
                quote_rejected = entry_policy.quote_rejections(
                    live_quote, immediate_roundtrip_pct, worst_case_roundtrip_pct,
                    max_impact=STRICT_MAX_ENTRY_IMPACT_PCT,
                    max_cost=STRICT_MAX_ROUNDTRIP_COST_PCT,
                    max_conservative_cost=STRICT_MAX_WORST_CASE_COST_PCT, now=now_ms(),
                )
                if quote_rejected:
                    retry_cause['cost'] = True
                    next_notional = quote_sizes[attempt_index + 1] if attempt_index + 1 < len(quote_sizes) else None
                    reject(report, quote_rejected, coin, {
                        'notional_usd': attempt_notional,
                        'next_notional_usd': next_notional,
                        'expected_roundtrip_pct': round(immediate_roundtrip_pct, 4),
                        'conservative_roundtrip_pct': round(worst_case_roundtrip_pct, 4),
                        'entry_impact_pct': round(impact_pct, 4),
                    })
                    return None, quote_rejected
                return {
                    'live_quote': live_quote, 'initial_exit': initial_exit,
                    'expected_token_raw': expected_token_raw, 'impact_pct': impact_pct,
                    'immediate_roundtrip_pct': immediate_roundtrip_pct,
                    'worst_case_roundtrip_pct': worst_case_roundtrip_pct,
                }, []

            if ADAPTIVE_PROFILE is not None:
                flat_candidate, flat_rejections = check_quote_size(notional, 0)
                selected_quote = ((notional, flat_candidate, 1)
                                  if flat_candidate is not None and not flat_rejections else None)
            else:
                selected_quote = entry_size_backoff.first_quote_passing_costs(notional, check_quote_size)
            if selected_quote is None:
                if retry_cause['cost'] or retry_cause['quote']:
                    self.entry_quote_retry_after[address] = now_ms() + entry_size_backoff.QUOTE_RETRY_COOLDOWN_MS
                continue
            notional, selected_data, _attempt_count = selected_quote
            live_quote = selected_data['live_quote']
            initial_exit = selected_data['initial_exit']
            expected_token_raw = selected_data['expected_token_raw']
            impact_pct = selected_data['impact_pct']
            immediate_roundtrip_pct = selected_data['immediate_roundtrip_pct']
            worst_case_roundtrip_pct = selected_data['worst_case_roundtrip_pct']
            stop_signal_trigger_pct = None
            quote_slippage_pct = paper_quotes.BUFFER_BPS / 100.0
            quote_network_fee = entry_network_fee
            decimals=int(safety['metrics']['decimals'])
            quantity=expected_token_raw/(10**decimals)
            quote_fill_price=notional/max(quantity,1e-18)
            if price_review:
                validation=price_integrity.jupiter_tiebreak(validation,quote_fill_price)
                if validation.get('status')!='pass':
                    reject(
                        report,[validation.get('reason') or 'price_tiebreak_failed'],coin,validation
                    )
                    continue
            entry_quote = {
                'fill_price': quote_fill_price,
                'quantity': quantity,
                'capital_committed_usd': notional + quote_network_fee + entry_rent,
                'dex_fee_bps': 0.0,
                'dex_fee_usd': 0.0,
                'network_fee_usd': quote_network_fee,
                'impact_pct': impact_pct,
                'slippage_pct': quote_slippage_pct,
                'latency_pct': 0.0,
            }
            if quantity <= 0:
                continue
            training_bridge.observe(coin,flow,safety=safety,validation=validation,
                quotes={'entry':dict(live_quote,entry_network_fee_usd=entry_network_fee,
                                     entry_account_reserve_usd=entry_rent),
                        'exit':dict(initial_exit,exit_network_fee_usd=entry_network_fee)},
                context=self.training_context(coin,context=context),now=now_ms())
            with STATE.lock:
                if STATE.demo_session_id!=session_at_check or not STATE.running or len(STATE.positions)>=MAX_POSITIONS:
                    return
                # Position liquidation runs concurrently with quote preparation.
                # Revalidate portfolio vetoes under the same lock as the entry
                # commit; a preflight pass cannot authorize newly unknown risk.
                if STATE.pending_audit:
                    reject(report, ['audit_pending'], coin)
                    return
                if any(p.get('valuation_status') == 'unavailable' for p in STATE.positions):
                    reject(report, ['liquidation_unavailable'], coin)
                    return
                if MAX_DRAWDOWN_PCT > 0 and (1-STATE.equity_usd()/max(STATE.equity_peak_usd,1))*100 >= MAX_DRAWDOWN_PCT:
                    reject(report, ['drawdown_limit'], coin)
                    return
                current_coin = next((c for c in STATE.feed if c.get('address') == address and c.get('pairAddress') == coin.get('pairAddress')), coin)
                final_rejections = entry_policy.signal_data_rejections(current_coin,now=now_ms())
                if final_rejections:
                    reject(report,final_rejections,coin)
                    continue
                final_flow = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
                final_flow_admission = promoted_guard.flow_admission(
                    current_coin, {'verified_flow': final_flow.get('verified_flow')}, now_ms())
                if not final_flow_admission['allow']:
                    reject(report, [final_flow_admission['reason']], coin)
                    continue
                if ADAPTIVE_PROFILE is not None:
                    final_decision_flow = STATE.live_flow(address, oct4.ENTRY_FLOW_WINDOW_SECONDS, str(coin.get('pairAddress') or ''))
                    final_context = self.market_context(current_coin)
                    final_signal_rejected = oct4.signal_rejections(
                        current_coin, final_decision_flow, final_context, now=now_ms(), profile=ADAPTIVE_PROFILE)
                    if final_signal_rejected:
                        reject(report, final_signal_rejected, coin,
                               oct4.signal_metrics(current_coin, final_decision_flow, final_context))
                        continue
                    final_market_candidates = final_raw_strategy_matches = final_strategy_matches = [oct4.STRATEGY_ID]
                    final_policy_learning = dict(oct4.NO_LEARNING)
                elif COST_FIRST_ACTIVE:
                    final_at = now_ms()
                    final_universe_rejected = cost_first_profile.universe_rejections(
                        current_coin, TRADE_NOTIONAL_USD, now=final_at, ticker_registry=registry)
                    if final_universe_rejected:
                        reject(report, final_universe_rejected, coin,
                               cost_first_profile.universe_metrics(
                                   current_coin, TRADE_NOTIONAL_USD, now=final_at, ticker_registry=registry))
                        continue
                    final_decision_flow = final_flow
                    final_market_candidates = final_raw_strategy_matches = final_strategy_matches = [
                        cost_first_profile.STRATEGY_ID]
                    final_policy_learning = dict(cost_first_profile.NO_LEARNING)
                    final_context = self.market_context(current_coin)
                else:
                    final_decision_flow = final_flow
                    final_market_candidates = winner_ensemble.market_candidates(current_coin)
                    final_raw_strategy_matches = winner_ensemble.matches(current_coin, final_flow)
                    with STATE.lock:
                        final_learning_history = list(STATE.history)
                    final_strategy_matches, final_policy_learning = winner_ensemble.apply_learning(
                        final_raw_strategy_matches, final_learning_history)
                    if not final_market_candidates:
                        reject(report, ['winner_signal'], coin)
                        continue
                    if not final_strategy_matches:
                        reason = ('loss_learning_hold'
                                  if final_raw_strategy_matches
                                  else 'winner_flow_signal')
                        reject(report, [reason], coin)
                        continue
                    final_context = self.market_context(current_coin)
                # The defensive layer is rechecked on the commit-time observation
                # (heat and loss memory can change during quote preparation).
                final_defensive_at = now_ms()
                final_defensive = self.defensive_entry_decision(
                    current_coin, final_defensive_at, blocked_pools=self.entry_loss_index(final_defensive_at))
                if not final_defensive['allowed']:
                    defensive_summary['commit_recheck_blocked'] = int(
                        defensive_summary.get('commit_recheck_blocked') or 0) + 1
                    reject(report, final_defensive['reasons'], coin, entry_defense.metrics(final_defensive))
                    continue
                final_risk_admission = promoted_guard.risk_admission(
                    current_coin, safety, now_ms(), promoted_guard.SAFETY_MAX_AGE_MS)
                if not final_risk_admission['allow']:
                    reject(report, [final_risk_admission['reason']], coin)
                    continue
                if STATE.available_balance_usd()<entry_quote['capital_committed_usd'] or (MAX_DAILY_LOSS_USD > 0 and STATE.risk_day_pnl()<=-MAX_DAILY_LOSS_USD):
                    reject(report,['balance'],coin); return
                live_open_risk=sum(num(p.get('planned_risk_usd')) for p in STATE.positions)
                current_exposure_available=max(0,STATE.equity_usd()*MAX_TOTAL_EXPOSURE_PCT/100-STATE.reserved_usd())
                permitted = runtime.plan_notional(
                    requested_notional,min(STATE.available_balance_usd(),current_exposure_available),MAX_DAILY_LOSS_USD,
                    STATE.risk_day_pnl()-live_open_risk,
                    STOP_LOSS_PCT,STOP_EXECUTION_BUFFER_PCT,fixed_cost_budget,
                )
                if notional>permitted:
                    reject(report,['risk_budget_unavailable'],coin); return
                if not 0 <= now_ms()-int(live_quote['quoted_at']) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS:
                    reject(report,['quote_age'],coin); return
                if not paper_quotes.signal_fresh_at_commit(num(current_coin.get('updatedAt')),live_quote,now=now_ms()):
                    reject(report,['stale_signal'],coin)
                    continue
                next_trade_no = STATE.trade_seq + 1
                position = {
                    'id': f'{STATE.demo_session_id}:{address}:{next_trade_no}', 'address': address,
                    'pairAddress': coin.get('pairAddress'), 'name': coin.get('name'),
                    'symbol': coin.get('symbol'), 'imageUrl': coin.get('imageUrl'),
                    'entry_price': price, 'market_entry_price': price,
                    'execution_entry_price': round(entry_quote['fill_price'], 12),
                    'current_price': price, 'peak_price': price,
                    'trade_no': next_trade_no, 'session_id': STATE.demo_session_id, 'strategy_id': strategy_id,
                    'strategy_matches': final_strategy_matches,
                    'strategy_matches_at_entry': final_raw_strategy_matches,
                    'signal_evidence': oct4.SIGNAL_EVIDENCE if ADAPTIVE_PROFILE is not None else 'CONFIRMED_PUMPSWAP_FLOW_WITH_VERIFIED_EXECUTION_CHECKS',
                    'entry_mode': entry_mode,
                    'provisional_early_safety': bool(safety.get('provisional_early')),
                    'learning_mode': oct4.LEARNING_MODE if ADAPTIVE_PROFILE is not None else 'SAME_POLICY_PAPER_OUTCOME_THROTTLE_V1',
                    'entry_flow': final_flow,
                    'entry_decision_flow': final_decision_flow if ADAPTIVE_PROFILE is not None else None,
                    # The hold mode the blind-flow exit fallback keeps is an exit input:
                    # it reads the EXIT_CONTEXT_SCORE_VERSION (V1) conviction of the same flow.
                    'adaptive_hold': oct4.hold_mode(num(final_context.get(
                        'exit_basis_conviction', final_context.get('conviction')))) if ADAPTIVE_PROFILE is not None else None,
                    'verified_entry_flow': final_flow.get('verified_flow'),
                    'entry_context': final_context if ADAPTIVE_PROFILE is not None else context,
                    'entry_conviction': (final_context if ADAPTIVE_PROFILE is not None else context).get('conviction'),
                    'entry_hold_mode': (final_context if ADAPTIVE_PROFILE is not None else context).get('mode'), 'learning_sample': final_policy_learning['closed_trades'],
                    'learning_win_rate': final_policy_learning['win_rate_pct'], 'learning_profit_factor': final_policy_learning['profit_factor'],
                    'learning_recent_losses': final_policy_learning['losses'], 'learning_bonus': 0,
                    'learning_avg_pnl_pct': None,
                    'learning_size_multiplier': learning.get('size_multiplier'),
                    'entry_market_features': {
                        'score': num(current_coin.get('score')),
                        'liquidity_usd': num(current_coin.get('liquidityUsd')),
                        'm5_pct': num((current_coin.get('priceChange') or {}).get('m5')),
                        'h1_pct': num((current_coin.get('priceChange') or {}).get('h1')),
                        'buys_m5': num(((current_coin.get('txns') or {}).get('m5') or {}).get('buys')),
                        'sells_m5': num(((current_coin.get('txns') or {}).get('m5') or {}).get('sells')),
                        'market_cap_usd': num(current_coin.get('marketCap') or current_coin.get('fdv')),
                        'age_minutes': num(current_coin.get('ageMinutes')),
                        'volume_h1_usd': num(((current_coin.get('volume') or {}).get('h1'))),
                    },
                    'requested_notional_usd': round(requested_notional, 8),
                    'notional_usd': round(notional, 8),
                    # The cost-first size rule may set a smaller base than the engine
                    # notional; only a cut below that base is a daily-budget limit.
                    'size_limited_by_daily_budget': notional<min(
                        requested_base if COST_FIRST_ACTIVE else TRADE_NOTIONAL_USD,
                        available_before-fixed_cost_budget),
                    'planned_risk_usd': notional*(STOP_LOSS_PCT+STOP_EXECUTION_BUFFER_PCT)/100+fixed_cost_budget,
                    'conservative_risk_usd': notional+quote_network_fee+entry_rent,
                    'original_notional_usd': notional,
                    'capital_committed_usd': round(entry_quote['capital_committed_usd'], 8),
                    'quantity': quantity, 'score': coin.get('score'),
                    'current_score': coin.get('score'), 'opened_at': now_ms(),
                    'signal_observed_at': num(current_coin.get('updatedAt')),
                    'signal_age_at_entry_ms': now_ms()-num(current_coin.get('updatedAt')),
                    'simulated_fill_at': live_quote.get('simulated_fill_at',live_quote.get('quoted_at')),
                    'execution_queue_ms': live_quote.get('queue_ms'), 'execution_http_ms': live_quote.get('http_ms'),
                    'updated_at': now_ms(), 'signal_pnl_pct': 0,
                    'pnl_pct': immediate_roundtrip_pct, 'pnl_usd': immediate_roundtrip_pct*notional/100,
                    'execution_mode': live_quote.get('execution_source') or 'JUPITER_QUOTE_V2',
                    'jupiter_usdc_in_raw': int(live_quote.get('input_usdc_raw') or 0),
                    'jupiter_token_raw_expected': int(live_quote.get('token_raw_expected') or 0),
                    'jupiter_token_raw_amount': expected_token_raw,
                    'jupiter_entry_route': live_quote.get('route') or [],
                    'jupiter_entry_quote_at': int(live_quote.get('quoted_at') or now_ms()),
                    'jupiter_entry_price_impact_pct': impact_pct,
                    'jupiter_slippage_bps': int(live_quote.get('slippage_bps') or paper_quotes.SLIPPAGE_BPS),
                    'entry_roundtrip_pnl_pct': round(immediate_roundtrip_pct, 4),
                    'entry_policy_version': ENTRY_POLICY_VERSION,
                    'exit_policy': POSITION_EXIT_POLICY, 'exit_policy_version': exit_policy_version(),
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': strategy_rule_config(),
                    # DEFENSIVE_ENTRY_LAYER_V1 decision on the commit-time observation.
                    'defensive_entry': entry_defense.compact(final_defensive),
                    'score_version': SCORE_VERSION,
                    'exit_context_score_version': EXIT_CONTEXT_SCORE_VERSION,
                    'signal_source_commit': oct4.SOURCE_COMMIT if ADAPTIVE_PROFILE is not None else winner_ensemble.VERSION,
                    'execution_verification_version': 'QUOTE_EVIDENCE_V9',
                    'entry_quote': live_quote.get('raw_quote'),
                    'preflight_buy_quote': live_quote.get('preflight_buy_quote'),
                    'preflight_sell_quote': live_quote.get('preflight_sell_quote'),
                    'price_crosscheck': validation,
                    'preflight_is_cost_estimate_not_same_time_fill': True,
                    'entry_worst_case_roundtrip_pnl_pct': round(worst_case_roundtrip_pct, 4),
                    'stop_signal_trigger_pct': None,
                    'hard_stop_net_pct': None, 'planned_stop_net_pct': -STOP_LOSS_PCT,
                    'entry_dex_fee_bps': round(entry_quote['dex_fee_bps'], 4),
                    'entry_dex_fee_usd': round(entry_quote['dex_fee_usd'], 8),
                    'entry_network_fee_usd': round(entry_quote['network_fee_usd'], 8),
                    'entry_account_reserve_usd': entry_rent, 'token_decimals': decimals,
                    'risk_check': safety,
                    'take_profit_net_pct': (final_context.get('target_pct') if ADAPTIVE_PROFILE is not None else TAKE_PROFIT_PCT),
                    'cost_assumptions': 'Jupiter AMM fees included; 10bps/leg buffer, network budget, account rent reserve',
                    'entry_price_impact_pct': round(entry_quote['impact_pct'], 6),
                    'entry_slippage_pct': round(entry_quote['slippage_pct'] + entry_quote['latency_pct'], 6),
                    'why_entry': positive, 'risks_at_entry': risks,
                    'balance_at_entry': round(STATE.demo_balance_usd, 8),
                    'available_before_entry': round(available_before, 8),
                    'available_after_entry': round(max(0.0, available_before - entry_quote['capital_committed_usd']), 8),
                    'entry_liquidity_usd': coin.get('liquidityUsd'),
                    'entry_volume_h1': (coin.get('volume') or {}).get('h1'),
                    'entry_market_cap': coin.get('marketCap') or coin.get('fdv'),
                    'entry_change_m5': (coin.get('priceChange') or {}).get('m5'),
                    'entry_buy_sell_ratio': round(buy_sell_ratio, 4),
                    'entry_liquidity_mc_ratio': round(liquidity_mc_ratio, 4),
                    'entry_scan_count': STATE.scan_count, 'dex_url': coin.get('dexUrl'),
                    'coin_snapshot': coin,
                }
                if COST_FIRST_ACTIVE:
                    position.update({
                        'signal_evidence': cost_first_profile.SIGNAL_EVIDENCE,
                        'learning_mode': cost_first_profile.LEARNING_MODE,
                        'signal_source_commit': cost_first_profile.SIGNAL_SOURCE,
                        'strategy_profile_version': cost_first_profile.PROFILE_VERSION,
                        'cost_first_universe': cost_first_profile.universe_metrics(
                            current_coin, TRADE_NOTIONAL_USD, now=final_defensive_at, ticker_registry=registry),
                        'size_policy': cost_first_profile.SIZE_POLICY,
                        'size_rule_notional_usd': round(requested_base, 8),
                        # Anchor of EXIT_IMPACT_EMERGENCY_V2: the entry preflight sell quote.
                        'entry_sell_impact_pct': cost_first_profile.entry_sell_impact_pct(initial_exit),
                        'exit_impact_emergency_version': cost_first_profile.EIE_VERSION,
                    })
                # Pin the exact-pool observation that passed the final freshness
                # check together with the entry. Until scan_once refreshes the
                # held pool, the position guard otherwise sees only a record left
                # by an earlier trade in this pool or the older candidate snapshot.
                with STATE.lock:
                    STATE.commit('ENTRY', position, positions=STATE.positions+[position], trade_seq=next_trade_no,
                                 position_market={**STATE.position_market,
                                                  f"{address}:{coin.get('pairAddress')}": dict(current_coin)})
                report['opened'] += 1
                open_addresses.add(address)
                STATE.event(
                    f"PAPER ENTRY #{position['trade_no']} ${coin.get('symbol')} market ${price:.10g} "
                    f"→ fill ${entry_quote['fill_price']:.10g} · ${notional:.2f} · fee {entry_quote['dex_fee_bps'] / 100:.3f}% "
                    f"· impact {entry_quote['impact_pct']:.2f}% · {strategy_id} · NEO {coin.get('score'):.0f}/100"
                )

    def scan_once(self) -> None:
        if not self.scan_lock.acquire(blocking=False):
            return
        try:
            addresses, metadata = self.discovery.get()
            early_pairs = gecko_new_pumpswap_pairs()
            for early_pair in early_pairs:
                address = str((early_pair.get('baseToken') or {}).get('address') or '')
                if not address:
                    continue
                if address not in addresses:
                    addresses.append(address)
                info = metadata.setdefault(address, {
                    'sources': [], 'icon': '', 'header': '', 'description': '',
                    'links': [], 'boost_amount': 0,
                })
                if 'gecko-new-pools' not in info['sources']:
                    info['sources'].append('gecko-new-pools')
            for position in STATE.positions:
                address = position.get('address')
                if address and address not in addresses:
                    addresses.append(address)
                    metadata[address] = metadata.get(address) or {
                        'sources': ['open-position'], 'icon': position.get('imageUrl') or '',
                        'header': '', 'description': '', 'links': [], 'boost_amount': 0,
                    }
            if not addresses:
                with STATE.lock:
                    STATE.status = 'discovering'
                    STATE.message = 'Проверява пазарните източници.'
                return
            dex_pairs = fetch_pairs(addresses)
            early_markets = early_market_pairs(early_pairs, dex_pairs, now_ms())
            pairs = early_markets + dex_pairs
            catalog_mints = {address for address, meta in metadata.items()
                             if 'pumpswap-address-catalog' in meta.get('sources', [])}
            chosen = best_pairs(pairs, prefer_pumpswap_mints=catalog_mints)

            # For the first 15 minutes, preserve the newly-created exact PumpSwap
            # pool instead of silently switching to an older/higher-liquidity pair.
            newest_early: dict[str, dict[str, Any]] = {}
            for early_pair in early_markets:
                address = str((early_pair.get('baseToken') or {}).get('address') or '')
                created = int(early_pair.get('pairCreatedAt') or 0)
                if not address or not created or now_ms() - created > 15 * 60 * 1000:
                    continue
                previous = newest_early.get(address)
                if previous is None or created > int(previous.get('pairCreatedAt') or 0):
                    newest_early[address] = early_pair
            chosen.update(newest_early)

            feed = []
            for address in addresses:
                pair = chosen.get(address)
                if not pair:
                    continue
                coin = make_coin(address, pair, metadata.get(address, {}))
                if coin['priceUsd'] > 0:
                    feed.append(coin)
            feed.sort(key=lambda c: (num(c.get('score')), num((c.get('volume') or {}).get('h1'))), reverse=True)
            # Every scan feeds the defensive layer's ticker registry and pair
            # history with the whole discovered feed, before it is trimmed.
            scan_at = now_ms()
            self.observe_entry_defense(feed, scan_at)
            registry = self.defense.registry
            # Published on /state by every scan, also while the account is paused
            # (maybe_open, which fills entry_diagnostics, returns at once then).
            layer_status = self.defensive_layer_status()
            # Retain plausible market candidates before the bounded feed is
            # trimmed. Display order stays score-ranked; all entry gates still
            # run later and held pools are independently refreshed below.
            feed = entry_quote_priority.bounded_feed(
                feed, MAX_FEED, STRICT_MAX_ROUNDTRIP_COST_PCT,
                is_candidate=lambda coin: shared_feed_candidate(coin, scan_at, registry),
            )
            by_address = {c['address']: c for c in feed}
            for position in STATE.positions:
                address = position.get('address')
                if not address:
                    continue
                pair = exact_position_pair(position, pairs)
                if not pair:
                    by_address.pop(address, None)
                    continue
                snap = position.get('coin_snapshot') or {}
                meta = {
                    'sources': ['open-position-pinned-pair'],
                    'icon': snap.get('imageUrl') or '',
                    'header': '',
                    'description': '',
                    'links': [],
                    'boost_amount': 0,
                }
                by_address[address] = make_coin(address, pair, meta)
            with STATE.lock:
                for position in STATE.positions:
                    held = by_address.get(position.get('address'))
                    if held and held.get('pairAddress') == position.get('pairAddress'):
                        STATE.position_market[f"{position.get('address')}:{position.get('pairAddress')}"] = dict(held)
                STATE.feed = feed
                STATE.defensive_entry_layer = layer_status
                STATE.last_scan_at = now_ms()
                STATE.scan_count += 1
                STATE.status = 'monitoring'
                STATE.source_status = {'dexscreener': 'online'}
                self.update_price_history(feed)
                setups = sum(1 for c in feed if c.get('posture') == 'SETUP')
                STATE.message = STATE.entry_diagnostics.get('message') or f'Проверени {len(feed)} token-а; отворени позиции: {len(STATE.positions)}.'
                STATE.save()
            # Prewarm the price and RugCheck reports of the established pools the
            # defensive layer can allow (PREWARM_V2_DEFENSIVE_POPULATION).
            if training_bridge.enabled():
                for coin in feed:
                    training_bridge.observe(coin,STATE.live_flow(coin['address'],pair_address=coin.get('pairAddress')),
                        context=self.training_context(coin),now=now_ms())
            self.prewarm_entry_checks(feed)
            # Entry preparation and quotes must not hold the account/UI lock.
            self.maybe_open(feed)
        except Exception as exc:
            with STATE.lock:
                STATE.status = 'error'
                STATE.source_status = {'dexscreener': 'error'}
                STATE.event(f'Market monitor error: {exc}')
                STATE.save()
        finally:
            self.scan_lock.release()

    def run(self) -> None:
        with STATE.lock:
            STATE.status = 'starting'
            STATE.event('NEO public market monitor started.')
        while not self.stop_event.is_set():
            self.scan_once()
            self.stop_event.wait(SCAN_SECONDS)

    def stop(self) -> None:
        self.stop_event.set()
        self.discovery.stop()
        # Keep the ticker memory across a restart (atomic sidecar; never raises).
        if self._entry_defense is not None:
            self._entry_defense.registry.flush()


MONITOR = Monitor()

class ApiHandler(BaseHTTPRequestHandler):
    server_version = 'NEOMarketMonitor/2.0'

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            self.send_json({'ok': True, 'status': STATE.status, 'running': STATE.running, 'feed_count': len(STATE.feed)})
            return
        if parsed.path == '/state':
            self.send_json(STATE.snapshot())
            return
        if parsed.path == '/token':
            address = (parse_qs(parsed.query).get('address') or [''])[0]
            result = STATE.token_snapshot(address)
            self.send_json(result if result else {'error': 'token_not_found'}, 200 if result else 404)
            return
        self.send_json({'error': 'not_found'}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/control/start':
            with STATE.lock:
                STATE.running = True
                STATE.status = 'starting'
                STATE.event('Monitoring enabled.')
                STATE.save()
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json(STATE.snapshot())
            return
        if path == '/control/stop':
            with STATE.lock:
                STATE.running = False
                STATE.status = 'paused'
                STATE.event('Monitoring paused.')
                STATE.save()
            self.send_json(STATE.snapshot())
            return
        if path == '/control/rescan':
            threading.Thread(target=MONITOR.scan_once, daemon=True).start()
            self.send_json({'ok': True})
            return
        if path == '/control/reset':
            try:
                with MONITOR.position_lock, MONITOR.entry_lock:
                    STATE.reset_with_archive()
                    training_bridge.request_reset()
                self.send_json(STATE.snapshot())
            except Exception as exc:
                self.send_json({'error': 'reset_archive_or_persist_failed', 'type': type(exc).__name__}, 500)
            return
        self.send_json({'error': 'not_found'}, 404)


ENGINE_LOCK_HANDLE = None


def acquire_engine_lock(lock_path: Path | None = None):
    """One writer per PAPER ledger: a second engine on the same state file refuses to start.

    The lock is held for the life of the process and released by the OS on exit,
    including a crash, so a restarted engine can always reclaim it.
    """
    global ENGINE_LOCK_HANDLE
    path = Path(lock_path) if lock_path else STATE_PATH.with_name(STATE_PATH.name + '.engine.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+b')
    try:
        file_lock.flock(handle, file_lock.LOCK_EX | file_lock.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f'Another PAPER engine already writes {STATE_PATH}; refusing a second writer.') from exc
    ENGINE_LOCK_HANDLE = handle
    return handle


def main() -> None:
    mode = os.getenv('NEO_ENGINE_MODE', 'PAPER').upper()
    if mode not in {'PAPER', 'REPLAY', 'SHADOW'}:
        raise ValueError('Only PAPER/REPLAY/SHADOW modes are supported')
    acquire_engine_lock()
    STATE.load()
    STATE.save()
    training_bridge.start(STATE_PATH.parent / 'training')
    thread = threading.Thread(target=MONITOR.run, name='neo-market-monitor', daemon=True)
    thread.start()
    guard = threading.Thread(target=MONITOR.run_position_guard, name='neo-position-guard', daemon=True)
    guard.start()
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f'NEO market monitor API listening on http://{HOST}:{PORT}', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        MONITOR.stop()
        training_bridge.stop()
        server.shutdown()


if __name__ == '__main__':
    main()
