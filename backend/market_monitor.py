#!/usr/bin/env python3
import copy, hashlib, json, math, os, re, shutil, threading, time, uuid
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
from lab_dashboard_projection import compact_strategy_lab

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
ENTRY_SCORE = 60.0
MAX_POSITIONS = 8
STOP_LOSS_PCT = 5.0
STOP_EXECUTION_BUFFER_PCT = 0.5  # Planned risk allowance, never a fill clamp.
STOP_EXECUTION_ARM_NET_PCT = 5.0
EXIT_IMPACT_EMERGENCY_PCT = 0.75
TAKE_PROFIT_PCT = 10.0
TRAILING_PCT = 4.0
MAX_HOLD_MINUTES = 60
WEAK_CHECK_MINUTES = 5
STALE_EXIT_MINUTES = 7
STALE_MIN_PROFIT_PCT = 3.0
LEARNING_WINDOW = 60
HEALTH_WINDOW = 12
STARTING_BALANCE_USD = 1000.0
TRADE_NOTIONAL_USD = float(os.getenv('NEO_TRADE_NOTIONAL_USD', '200'))
MAX_DAILY_LOSS_USD = float(os.getenv('NEO_MAX_DAILY_LOSS_USD', '0'))
MAX_POSITION_RISK_USD = float(os.getenv('NEO_MAX_POSITION_RISK_USD', '250'))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv('NEO_MAX_TOTAL_EXPOSURE_PCT', '100'))
MAX_DRAWDOWN_PCT = float(os.getenv('NEO_MAX_DRAWDOWN_PCT', '0'))
MIN_LIQUIDITY_USD = 10000.0

# One validated effective threshold object governs every EARLY signal call.
STRICT_ENTRY_SCORE = float(os.getenv('NEO_STRICT_ENTRY_SCORE', '58'))
STRICT_MIN_CONVICTION = float(os.getenv('NEO_STRICT_MIN_CONVICTION', '30'))
STRICT_MIN_LIQUIDITY_USD = float(os.getenv('NEO_STRICT_MIN_LIQUIDITY_USD', '4000'))
STRICT_MAX_ENTRY_IMPACT_PCT = float(os.getenv('NEO_STRICT_MAX_ENTRY_IMPACT_PCT', '1.75'))
STRICT_MAX_ROUNDTRIP_COST_PCT = float(os.getenv('NEO_STRICT_MAX_ROUNDTRIP_COST_PCT', '2.75'))
STRICT_MAX_WORST_CASE_COST_PCT = float(os.getenv('NEO_STRICT_MAX_WORST_CASE_COST_PCT', '4.0'))
EFFECTIVE_ENTRY_THRESHOLDS = order_flow.EntryThresholds(STRICT_ENTRY_SCORE, STRICT_MIN_LIQUIDITY_USD, STRICT_MIN_CONVICTION)
for _threshold in (STRICT_MAX_ENTRY_IMPACT_PCT, STRICT_MAX_ROUNDTRIP_COST_PCT, STRICT_MAX_WORST_CASE_COST_PCT,
                   TRADE_NOTIONAL_USD, MAX_DAILY_LOSS_USD, POSITION_SCAN_SECONDS, MAX_POSITION_RISK_USD,
                   MAX_TOTAL_EXPOSURE_PCT, MAX_DRAWDOWN_PCT):
    if not math.isfinite(_threshold) or _threshold < 0: raise ValueError('invalid PAPER configuration')
if TRADE_NOTIONAL_USD <= 0 or POSITION_SCAN_SECONDS <= 0: raise ValueError('invalid PAPER configuration')
if MAX_POSITION_RISK_USD <= 0 or not 0 < MAX_TOTAL_EXPOSURE_PCT <= 100 or not 0 <= MAX_DRAWDOWN_PCT <= 100:
    raise ValueError('invalid PAPER risk limits')

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
        data = json.loads(LIVE_TAPE_PATH.read_text(encoding='utf-8'))
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
        'balance_after', 'dex_url', 'strategy_id', 'entry_policy_version',
        'exit_policy_version', 'signal_pnl_pct', 'entry_roundtrip_pnl_pct',
        'observed_exit_pnl_pct', 'observed_exit_pnl_usd', 'paper_stop_capped',
        'stop_execution_source',
    )
    return {field: trade.get(field) for field in fields if field in trade}


def api(path: str) -> Any:
    response = SESSION.get(f'{DEX_API}{path}', timeout=15)
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
        self.entry_diagnostics = {'status': 'starting', 'policy_version': entry_policy.POLICY_VERSION}
        self.risk_day_key = time.strftime('%Y-%m-%d', time.gmtime())
        self.risk_day_start_balance_usd = STARTING_BALANCE_USD
        if load_state: self.load()

    def load(self) -> None:
        if not STATE_PATH.exists():
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
                entry_diagnostics={'status': 'reset', 'policy_version': entry_policy.POLICY_VERSION})
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
            if event_id and event_id in event_ids: continue
            if event_id: event_ids.add(event_id)
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
                'live_tape': [],
                'live_tape_status': {k: tape.get(k) for k in ('status','tracked_pairs','updated_at','source','error')},
                'strategy_lab': read_strategy_lab(),
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
                'config': {
                    'scan_seconds': SCAN_SECONDS,
                    'position_scan_seconds': POSITION_SCAN_SECONDS,
                    'entry_score': STRICT_ENTRY_SCORE,
                    'max_positions': MAX_POSITIONS,
                    'stop_loss_pct': STOP_LOSS_PCT,
                    'stop_loss_basis': 'EXECUTABLE_NET_PNL',
                    'stop_trigger_net_pct': -STOP_LOSS_PCT,
                    'take_profit_basis': 'EXECUTABLE_NET_PNL',
                    'reentry_seconds': 1200, 'loss_reentry_seconds': 1200,
                    'signal_strategy': 'ORDER_FLOW_EARLY_FIXED_PAPER_V9',
                    'signal_source_commit': order_flow.SOURCE_COMMIT,
                    'risk_overlay': 'PLANNED_NET_STOP_NO_FILL_GUARANTEE',
                    'execution_verification_version': 'QUOTE_EVIDENCE_V9',
                    'rug_guard': rug_guard.VERSION,
                    'paper_only': True,
                    'runtime_version': runtime.VERSION,
                    'daily_budget_sizing': True,
                    'stop_execution_buffer_pct': STOP_EXECUTION_BUFFER_PCT,
                    'exit_impact_emergency_pct': EXIT_IMPACT_EMERGENCY_PCT,
                    'take_profit_pct': TAKE_PROFIT_PCT,
                    'trailing_pct': TRAILING_PCT,
                    'max_hold_minutes': MAX_HOLD_MINUTES,
                    'min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD,
                    'trade_notional_usd': TRADE_NOTIONAL_USD,
                    'max_daily_loss_usd': MAX_DAILY_LOSS_USD,
                    'max_position_full_loss_risk_usd': MAX_POSITION_RISK_USD,
                    'max_total_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,
                    'max_drawdown_pct': MAX_DRAWDOWN_PCT,
                    'drawdown_cap_enabled': MAX_DRAWDOWN_PCT > 0,
                    'daily_loss_cap_enabled': MAX_DAILY_LOSS_USD > 0,
                    'starting_balance_usd': STARTING_BALANCE_USD,
                    'execution_mode': 'PAPER_QUOTE_OR_OBSERVED_POOL_MODEL',
                    'execution_note': 'PAPER only; observed quotes with modeled fills, full fees and uncapped gap losses. Validated learning portfolios are separate.',
                    'entry_policy_version': entry_policy.POLICY_VERSION,
                    'exit_policy': 'fixed', 'exit_policy_version': exit_policy.VERSION,
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': EFFECTIVE_ENTRY_THRESHOLDS.as_dict(),
                    'signal_source_commit': order_flow.SOURCE_COMMIT,
                    'max_quoted_candidates_per_scan': entry_policy.MAX_QUOTED_CANDIDATES,
                    'strict_entry_score': STRICT_ENTRY_SCORE,
                    'strict_min_conviction': STRICT_MIN_CONVICTION,
                    'strict_min_liquidity_usd': STRICT_MIN_LIQUIDITY_USD,
                    'strict_max_entry_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT,
                    'strict_max_roundtrip_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
                    'jupiter_slippage_bps': paper_quotes.SLIPPAGE_BPS,
                    'generic_dex_fee_bps': GENERIC_DEX_FEE_BPS,
                    'base_slippage_bps': BASE_SLIPPAGE_BPS,
                    'latency_buffer_bps': LATENCY_BUFFER_BPS,
                    'network_fee_sol_per_leg': NETWORK_FEE_SOL,
                    'max_price_impact_pct': MAX_PRICE_IMPACT_PCT,
                },
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
    config = {'entry': EFFECTIVE_ENTRY_THRESHOLDS.as_dict(), 'entry_version': entry_policy.POLICY_VERSION,
              'exit_version': exit_policy.VERSION, 'stop_pct': STOP_LOSS_PCT, 'take_profit_pct': TAKE_PROFIT_PCT,
              'risk_buffer_pct': STOP_EXECUTION_BUFFER_PCT, 'daily_loss_usd': MAX_DAILY_LOSS_USD,
              'max_positions': MAX_POSITIONS, 'notional_usd': TRADE_NOTIONAL_USD,
              'max_position_full_loss_usd': MAX_POSITION_RISK_USD,'max_exposure_pct': MAX_TOTAL_EXPOSURE_PCT,'max_drawdown_pct':MAX_DRAWDOWN_PCT,
              'max_impact_pct': STRICT_MAX_ENTRY_IMPACT_PCT, 'max_cost_pct': STRICT_MAX_ROUNDTRIP_COST_PCT,
              'max_conservative_cost_pct': STRICT_MAX_WORST_CASE_COST_PCT,
              'execution_evidence_version': 'QUOTE_EVIDENCE_V9',
              'quote_adapter': paper_quotes.transport.ADAPTER_VERSION,
              'slippage_tolerance_bps': paper_quotes.SLIPPAGE_BPS,
              'assumed_execution_buffer_bps': paper_quotes.BUFFER_BPS,
              'simulated_execution_delay_ms': paper_quotes.SIMULATED_DELAY_MS,
              'max_signal_age_ms': paper_quotes.MAX_SIGNAL_AGE_MS}
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


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
    if cached_pairs and 0 <= current - cached_at <= GECKO_NEW_POOLS_TTL_MS:
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
            })
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return cached_pairs

    with _GECKO_NEW_POOLS_LOCK:
        _GECKO_NEW_POOLS_CACHE.update(ts=current, pairs=list(parsed))
    return parsed


STATE = State(load_state=False)


def discover() -> tuple[list[str], dict[str, dict[str, Any]]]:
    metadata: dict[str, dict[str, Any]] = {}
    order: list[str] = []
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


def best_pairs(pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if pair.get('chainId') != 'solana':
            continue
        address = (pair.get('baseToken') or {}).get('address')
        if not address:
            continue
        liquidity = num((pair.get('liquidity') or {}).get('usd'))
        old = best.get(address)
        old_liquidity = num((old.get('liquidity') or {}).get('usd')) if old else -1
        if old is None or liquidity > old_liquidity:
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


def score_pair(pair: dict[str, Any], meta: dict[str, Any]):
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
    if mc > 0:
        if liq_mc >= 0.15:
            score += 9; signals.append(signal('positive', 'Добро liquidity/MC', f'{liq_mc * 100:.1f}%'))
        elif liq_mc < 0.03:
            score -= 10; signals.append(signal('risk', 'Слаб liquidity/MC', f'{liq_mc * 100:.1f}%'))

    if 0.10 <= vol_liq <= 4.0:
        score += 6
    elif vol_liq > 7:
        score -= 12; signals.append(signal('risk', 'Volume/liquidity extreme', f'{vol_liq:.1f}x'))

    if any('boost' in source for source in meta.get('sources', [])):
        score += 4; signals.append(signal('neutral', 'Boosted discovery', 'Повишена market видимост'))
    if abs(change_h1) > 250:
        score -= 8; signals.append(signal('risk', 'Екстремен 1h move', f'{change_h1:+.0f}%'))

    score = round(clamp(score), 1)
    risk = round(100 - score, 1)
    posture = 'SETUP' if score >= ENTRY_SCORE else 'WATCH' if score >= 60 else 'WAIT' if score >= 45 else 'SKIP'
    return score, risk, posture, signals[:8]


def make_coin(address: str, pair: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    score, risk, posture, signals = score_pair(pair, meta)
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
        'score': score, 'riskScore': risk, 'posture': posture,
        'signals': signals, 'updatedAt': now_ms(),
    }

class Monitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.position_lock = threading.Lock()
        self.position_poll_lock = threading.Lock()
        self.entry_lock = threading.Lock()
        self.discovery = runtime.DiscoveryCache(discover, refresh_seconds=8, max_age_seconds=90)

    def prewarm_entry_checks(self, feed: list[dict[str, Any]]) -> None:
        """Warm only the most time-sensitive candidates; avoid provider queues."""
        candidates = []
        for coin in feed:
            age = num(coin.get('ageMinutes'), 999999)
            if num(coin.get('score')) < 60 or num(coin.get('liquidityUsd')) < 4000 or age > 360:
                continue
            tx = (coin.get('txns') or {}).get('m5') or {}
            activity = num(tx.get('buys')) + num(tx.get('sells'))
            priority = (
                1 if age <= 45 else 0,
                1 if age <= 180 else 0,
                activity,
                num(coin.get('score')),
            )
            candidates.append((priority, coin))
        candidates.sort(key=lambda row: row[0], reverse=True)
        for _, coin in candidates[:4]:
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
        score = 50.0

        fast_ratio = num(fast.get('buy_sell_usd_ratio'))
        slow_ratio = num(slow.get('buy_sell_usd_ratio'))
        if fast_ratio >= 3.0:
            score += 18
        elif fast_ratio >= 2.0:
            score += 12
        elif fast_ratio >= 1.4:
            score += 6
        elif fast_ratio < 0.8:
            score -= 20
        elif fast_ratio < 1.0:
            score -= 10

        if slow_ratio >= 2.0:
            score += 12
        elif slow_ratio >= 1.4:
            score += 7
        elif slow_ratio < 0.8:
            score -= 15
        elif slow_ratio < 1.0:
            score -= 7

        if num(slow.get('unique_wallets')) >= 10:
            score += 6
        elif num(slow.get('unique_wallets')) >= 5:
            score += 3
        elif num(slow.get('unique_wallets')) <= 2:
            score -= 5

        if num(slow.get('repeat_buy_wallets')) >= 3:
            score += 6
        elif num(slow.get('repeat_buy_wallets')) >= 1:
            score += 3

        whale_buy = num(slow.get('whale_buy_usd'))
        whale_sell = num(slow.get('whale_sell_usd'))
        if whale_buy > 0 and whale_buy >= whale_sell * 1.3:
            score += 6
        elif whale_sell > 0 and whale_sell >= max(whale_buy * 1.3, 750.0):
            score -= 8

        if 0 <= m5 <= 10:
            score += 8
        elif -2 <= m5 < 0:
            score += 2
        elif m5 > 20:
            score -= 8
        elif m5 < -5:
            score -= 15

        if 0 <= h1 <= 120:
            score += 4
        elif h1 < -15:
            score -= 8
        elif h1 > 250:
            score -= 5

        if market_ratio >= 1.4:
            score += 8
        elif market_ratio >= 1.1:
            score += 4
        elif market_ratio < 0.8:
            score -= 8

        if liquidity_ratio >= 0.95:
            score += 3
        elif liquidity_ratio < 0.80:
            score -= 15
        elif liquidity_ratio < 0.90:
            score -= 7

        neo_score = num(coin.get('score'))
        if neo_score >= 95:
            score += 4
        elif neo_score < 85:
            score -= 4

        conviction = round(clamp(score), 1)
        if conviction >= 85:
            mode, max_hold, target, trail_arm, trail = 'RUNNER', 60.0, None, 15.0, 7.0
        elif conviction >= 72:
            mode, max_hold, target, trail_arm, trail = 'STRONG', 30.0, None, 12.0, 6.0
        elif conviction >= 58:
            mode, max_hold, target, trail_arm, trail = 'NORMAL', 15.0, 20.0, 9.0, 5.0
        elif conviction >= 45:
            mode, max_hold, target, trail_arm, trail = 'CAUTIOUS', 8.0, 14.0, 7.0, 4.0
        else:
            mode, max_hold, target, trail_arm, trail = 'WEAK', 4.0, 8.0, 5.0, 3.0

        return {
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

    def _quote_unavailable(self, position, session, reason, error='no_sell_route'):
        with STATE.lock:
            live = next((p for p in STATE.positions if p.get('id') == position.get('id')), None)
            if live is None or STATE.demo_session_id != session: return
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
            incoming = by_address.get((address,pair)) or by_address.get(address)
            coin = incoming if incoming and incoming.get('pairAddress') == pair else pinned.get(key)
            coin = coin or position.get('coin_snapshot') or {}
            if coin.get('pairAddress') != pair: coin = {}
            stamp = now_ms()
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
                    context=self.market_context(coin,position),
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
            context = self.market_context(coin,position) if policy == 'adaptive' else {}
            reason = reason or exit_policy.exit_reason(position, context, net_pct=pct,peak_net_pct=peak_pct,
                hold_minutes=hold,stop_pct=STOP_LOSS_PCT,take_profit_pct=TAKE_PROFIT_PCT,policy=policy)
            if num(quote.get('impact_pct')) >= max(EXIT_IMPACT_EMERGENCY_PCT,num(position.get('entry_price_impact_pct'))+.50):
                reason = reason or 'EXIT_IMPACT_EMERGENCY'
            if reason and is_quote and quote.get('from_cache'):
                quote = paper_quotes.position_mark(position,coin,network,force=True)
                if quote is None:
                    self._quote_unavailable(position,session,reason)
                    continue
                if not math.isfinite(num(quote.get('net_proceeds_usd'),math.nan)):
                    self._quote_unavailable(position,session,reason,'invalid_sell_quote')
                    continue
                if not 0 <= now_ms()-num(quote.get('quoted_at')) <= entry_policy.MAX_ENTRY_QUOTE_AGE_MS:
                    self._quote_unavailable(position,session,reason,'stale_sell_quote')
                    continue
                pnl = num(quote.get('net_proceeds_usd'))-notional-entry_cost
                pct = pnl/max(notional,1e-18)*100
                peak_pct = max(peak_pct,pct)
                # A profit signal must still hold at the actual simulated sale.
                if reason.startswith(('TAKE_PROFIT', 'ADAPTIVE_TP', 'ADAPTIVE_TRAILING', 'CONVICTION_PROFIT')):
                    reason = exit_policy.exit_reason(position,context,net_pct=pct,peak_net_pct=peak_pct,
                        hold_minutes=hold,stop_pct=STOP_LOSS_PCT,take_profit_pct=TAKE_PROFIT_PCT,policy=policy)
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
                exit_policy_version=exit_policy.ADAPTIVE_VERSION if policy == 'adaptive' else exit_policy.VERSION,
                market_context=context, estimated_exit_dex_fee_usd=num(quote.get('dex_fee_usd')),
                estimated_exit_network_fee_usd=num(quote.get('network_fee_usd')),
                estimated_exit_price_impact_pct=num(quote.get('impact_pct')),
                estimated_exit_slippage_pct=num(quote.get('slippage_pct'))+num(quote.get('latency_pct')))
            with STATE.lock:
                live = next((p for p in STATE.positions if p.get('id') == position.get('id')),None)
                if live is None or STATE.demo_session_id != session: continue
                if not reason:
                    live.update(updated)
                    continue
            self.book_paper_exit(updated,quote,reason,coin)

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
        report = {'policy_version': entry_policy.POLICY_VERSION, 'checked_at': now_ms(),
                  'candidates': len(feed), 'evaluated': 0, 'signal_passed': 0,
                  'quoted': 0, 'opened': 0, 'rejections': {}, 'examples': [], 'max_positions': MAX_POSITIONS}
        try:
            self._maybe_open_checked(feed, report)
        except Exception as exc:
            entry_policy.record(report,['entry_error'],metrics={'type':type(exc).__name__})
        finally:
            with STATE.lock:
                STATE.entry_diagnostics = entry_policy.finish(report)
            self.entry_lock.release()

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
        recent = {t.get('address') for t in STATE.history if now-int(t.get('closed_at',0))<20*60*1000}
        for coin in feed:
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

            flow = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
            context = self.market_context(coin)
            rejected = entry_policy.signal_rejections(
                coin, flow, context, min_score=EFFECTIVE_ENTRY_THRESHOLDS.min_score,
                min_liquidity=EFFECTIVE_ENTRY_THRESHOLDS.min_liquidity,
                min_conviction=EFFECTIVE_ENTRY_THRESHOLDS.min_conviction, now=now_ms(),
            )
            if rejected:
                reject(report, rejected, coin)
                continue
            report['signal_passed'] += 1
            entry_mode = order_flow.entry_mode(coin, flow, context, EFFECTIVE_ENTRY_THRESHOLDS)
            if not entry_mode:
                reject(report, ['gold_signal'], coin)
                continue
            # Start independent price and rug checks together. Both helpers are
            # cached/asynchronous; running them concurrently avoids serial provider
            # latency without weakening known-risk vetoes.
            validation=price_integrity.check(coin)
            safety=rug_guard.check(coin)
            training_bridge.observe(coin,flow,safety=safety,validation=validation,context=context,now=now_ms())
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
            if report['quoted'] >= entry_policy.MAX_QUOTED_CANDIDATES:
                reject(report, ['quote_budget'], coin)
                continue
            strategy_id = 'ORDER_FLOW_EARLY_FIXED_PAPER_V9'
            # Main policy stays fixed. Candidate training/promotions run in the
            # separate training PAPER accounts with their own validation gates.
            learning = {'sample': 0, 'win_rate': 0, 'profit_factor': None,
                        'recent_losses': 0, 'bonus': 0, 'size_multiplier': 1.0}
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
            requested_notional = min(early_requested_notional(coin, learning),MAX_POSITION_RISK_USD-fixed_cost_budget)
            notional = runtime.plan_notional(
                requested_notional,available_before,MAX_DAILY_LOSS_USD,
                STATE.risk_day_pnl()-open_planned_risk,
                STOP_LOSS_PCT,STOP_EXECUTION_BUFFER_PCT,fixed_cost_budget,
            )
            if notional < 10:
                reject(report,['risk_budget_unavailable'],coin); return
            report['quoted'] += 1
            dex_id = str(coin.get('dexId') or '').lower()
            if dex_id == 'pumpswap':
                prepared = pumpswap_stop.prepare_entry(
                    coin, notional, sol_usd,
                    buffer_bps=paper_quotes.BUFFER_BPS,
                    slippage_bps=paper_quotes.SLIPPAGE_BPS,
                )
            else:
                prepared = paper_quotes.prepare_entry(
                    address, str(coin.get('pairAddress') or ''), notional
                )
            if not prepared:
                reject(report,['quote_inconsistent'],coin)
                continue
            live_quote,initial_exit=prepared
            entry_network_fee=pre_network_fee
            expected_token_raw=int(live_quote['token_raw_amount'])
            immediate_exit_net = max(0.0, num(initial_exit.get('expected_usdc')) - entry_network_fee)
            worst_case_exit_net = max(0.0, num(initial_exit.get('floor_usdc')) - entry_network_fee)
            immediate_roundtrip_pct = (
                (immediate_exit_net - notional - entry_network_fee - entry_rent) / max(notional, 1e-18)
            ) * 100.0
            worst_case_roundtrip_pct = (
                (worst_case_exit_net - notional - entry_network_fee - entry_rent) / max(notional, 1e-18)
            ) * 100.0
            impact_pct = num(live_quote.get('price_impact_pct'))
            quote_rejected = entry_policy.quote_rejections(
                live_quote, immediate_roundtrip_pct, worst_case_roundtrip_pct,
                max_impact=STRICT_MAX_ENTRY_IMPACT_PCT,
                max_cost=STRICT_MAX_ROUNDTRIP_COST_PCT,
                max_conservative_cost=STRICT_MAX_WORST_CASE_COST_PCT, now=now_ms(),
            )
            if quote_rejected:
                reject(report, quote_rejected, coin, {
                    'expected_roundtrip_pct': round(immediate_roundtrip_pct, 4),
                    'conservative_roundtrip_pct': round(worst_case_roundtrip_pct, 4),
                    'entry_impact_pct': round(impact_pct, 4),
                })
                continue
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
                context=context,now=now_ms())
            with STATE.lock:
                if STATE.demo_session_id!=session_at_check or not STATE.running or len(STATE.positions)>=MAX_POSITIONS:
                    return
                current_coin = next((c for c in STATE.feed if c.get('address') == address and c.get('pairAddress') == coin.get('pairAddress')), coin)
                final_flow = STATE.live_flow(address, 30, str(coin.get('pairAddress') or ''))
                final_context = self.market_context(current_coin)
                final_rejections = entry_policy.signal_rejections(current_coin,final_flow,final_context,
                    min_score=EFFECTIVE_ENTRY_THRESHOLDS.min_score,min_liquidity=EFFECTIVE_ENTRY_THRESHOLDS.min_liquidity,
                    min_conviction=EFFECTIVE_ENTRY_THRESHOLDS.min_conviction,now=now_ms())
                if final_rejections:
                    reject(report,final_rejections,coin)
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
                    'entry_mode': entry_mode,
                    'provisional_early_safety': bool(safety.get('provisional_early')),
                    'learning_mode': 'FIXED_MAIN_SEPARATE_VALIDATED_TRAINING', 'entry_flow': final_flow,
                    'entry_context': context, 'entry_conviction': context.get('conviction'),
                    'entry_hold_mode': context.get('mode'), 'learning_sample': learning['sample'],
                    'learning_win_rate': learning['win_rate'], 'learning_profit_factor': learning['profit_factor'],
                    'learning_recent_losses': learning['recent_losses'], 'learning_bonus': learning['bonus'],
                    'learning_avg_pnl_pct': learning.get('avg_pnl_pct'),
                    'learning_size_multiplier': learning.get('size_multiplier'),
                    'requested_notional_usd': round(requested_notional, 8),
                    'notional_usd': round(notional, 8),
                    'size_limited_by_daily_budget': notional<min(TRADE_NOTIONAL_USD,available_before-fixed_cost_budget),
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
                    'entry_policy_version': entry_policy.POLICY_VERSION,
                    'exit_policy': 'fixed', 'exit_policy_version': exit_policy.VERSION,
                    'effective_config_hash': effective_config_hash(),
                    'effective_entry_thresholds': EFFECTIVE_ENTRY_THRESHOLDS.as_dict(),
                    'signal_source_commit': order_flow.SOURCE_COMMIT,
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
                    'risk_check': safety, 'take_profit_net_pct': TAKE_PROFIT_PCT,
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
                STATE.commit('ENTRY', position, positions=STATE.positions+[position], trade_seq=next_trade_no)
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
            pairs = list(early_pairs) + dex_pairs
            chosen = best_pairs(pairs)

            # For the first 15 minutes, preserve the newly-created exact PumpSwap
            # pool instead of silently switching to an older/higher-liquidity pair.
            newest_early: dict[str, dict[str, Any]] = {}
            for early_pair in early_pairs:
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
            feed = feed[:MAX_FEED]
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
                STATE.last_scan_at = now_ms()
                STATE.scan_count += 1
                STATE.status = 'monitoring'
                STATE.source_status = {'dexscreener': 'online'}
                self.update_price_history(feed)
                setups = sum(1 for c in feed if c.get('posture') == 'SETUP')
                STATE.message = STATE.entry_diagnostics.get('message') or f'Проверени {len(feed)} token-а; отворени позиции: {len(STATE.positions)}.'
                STATE.save()
            # Prewarm provider checks before the short EARLY flow window fires.
            if training_bridge.enabled():
                for coin in feed:
                    training_bridge.observe(coin,STATE.live_flow(coin['address'],pair_address=coin.get('pairAddress')),
                        context=self.market_context(coin,{}),now=now_ms())
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


def main() -> None:
    mode = os.getenv('NEO_ENGINE_MODE', 'PAPER').upper()
    if mode not in {'PAPER', 'REPLAY', 'SHADOW'}:
        raise ValueError('Only PAPER/REPLAY/SHADOW modes are supported')
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
