"""Activity controls for the isolated paper Strategy Lab only.

No live order execution. These are experimental entry filters, not a promised
win rate. Fees and fill assumptions remain in strategy_lab.py unchanged.
"""
from dataclasses import dataclass
import math
import re
from typing import Any, Callable

POLICY_VERSION = 'LAB_ACTIVE_V3_VERIFIED_SCALPER'
REENTRY_SECONDS = 60
LOSS_REENTRY_SECONDS = 180
SCALPER_REENTRY_SECONDS = 600
SCALPER_LOSS_REENTRY_SECONDS = 3600
SCALPER_MAX_BALANCE_FRACTION = 0.25
SCALPER_MIN_NOTIONAL_USD = 2.0
MAX_FEED_AGE_MS = 20_000
MAX_ENTRY_COST_PCT = 2.75
MIN_NOTIONAL_USD = 10.0
ADDRESS = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class EntryRule:
    score: float
    liquidity: float
    move: tuple[float, float]
    buy_sell: float = 0.9
    liquidity_cap: float = 0.05
    age: tuple[float, float] = (2, 1440)
    hour: tuple[float, float] = (-100, 1000)
    volume_liquidity: tuple[float, float] = (0, 1_000_000)
    flow_trades: int = 0
    flow_ratio: float = 0
    flow_buy: float = 0
    wallets: int = 0
    max_sell: float = math.inf

    def matches(self, f: dict[str, Any], *, require_flow: bool = True) -> bool:
        flow = f.get('flow') or {}
        market_match = (
            number(f.get('score')) >= self.score
            and number(f.get('liq')) >= max(10_000, self.liquidity)
            and self.move[0] <= number(f.get('m5'), -math.inf) <= self.move[1]
            and number(f.get('bs')) >= self.buy_sell
            and number(f.get('lmc')) >= self.liquidity_cap
            and self.age[0] <= number(f.get('age'), math.inf) <= self.age[1]
            and self.hour[0] <= number(f.get('h1'), -math.inf) <= self.hour[1]
            and self.volume_liquidity[0] <= number(f.get('vol_liq')) <= self.volume_liquidity[1]
        )
        return market_match and (not require_flow or (
            number(flow.get('trades')) >= self.flow_trades
            and number(flow.get('ratio')) >= self.flow_ratio
            and number(flow.get('buy_usd')) >= self.flow_buy
            and number(flow.get('unique_wallets')) >= self.wallets
            and number(flow.get('max_sell')) <= self.max_sell
        ))


RULES = {
    'ULTRA_PRECISION': EntryRule(93, 25000, (1, 25), 1.0, .12, (5, 360)),
    'PRECISION': EntryRule(90, 20000, (0, 30), .95, .09, (3, 480)),
    'MOMENTUM': EntryRule(85, 15000, (3, 40), 1.05, .06, (2, 720)),
    'BREAKOUT': EntryRule(86, 20000, (10, 60), 1.2, .06, (2, 720)),
    'LIQUIDITY': EntryRule(80, 40000, (-3, 25), .85, .08, (3, 1440)),
    'ORDER_FLOW': EntryRule(80, 15000, (-5, 30), .8, .04, (2, 1e7), flow_trades=3, flow_ratio=1.2, wallets=2),
    'EARLY': EntryRule(85, 15000, (0, 25), .95, .08, (2, 120)),
    'TREND': EntryRule(83, 20000, (-2, 20), .85, .07, (5, 960), (-5, 200)),
    # The prior 70 closed PAPER trades all entered without any verified recent
    # flow. Require multi-wallet buy pressure and non-negative short/1h trend.
    # This is a conservative candidate guard, not a claim of improved returns.
    'SCALPER': EntryRule(85, 25000, (0, 15), 1.05, .08, (5, 240),
                         (0, 40), (0, 8), flow_trades=4, flow_ratio=1.5,
                         flow_buy=150, wallets=3, max_sell=250),
    'VOLUME_SURGE': EntryRule(83, 15000, (1, 35), 1.0, .06, (2, 720), volume_liquidity=(.2, 1e6)),
    'REVERSAL': EntryRule(80, 20000, (-12, 6), 1.0, .08, (5, 960), (-35, 1000)),
    'FLOW_MOMENTUM': EntryRule(80, 15000, (-1, 35), .8, .04, (2, 1e7), flow_trades=3, flow_ratio=1.5, flow_buy=100),
    'FLOW_MOMENTUM_SCALE_OUT': EntryRule(81, 15000, (0, 30), .9, .04, (2, 1e7), flow_trades=4, flow_ratio=1.5, flow_buy=100),
    'FLOW_ELITE': EntryRule(87, 30000, (-1, 25), 1, .06, (2, 1e7), flow_trades=4, flow_ratio=1.8, flow_buy=180, wallets=3),
    'FLOW_SNIPER': EntryRule(90, 25000, (0, 18), 1.05, .07, (2, 1e7), flow_trades=4, flow_ratio=2.0, flow_buy=200, wallets=4),
    'LIQ_FLOW_CONFLUENCE': EntryRule(85, 50000, (-2, 22), .95, .09, (2, 1e7), flow_trades=3, flow_ratio=1.5, flow_buy=150),
    'BREAKOUT_CONFIRM': EntryRule(87, 30000, (7, 40), 1.15, .06, (2, 1e7), volume_liquidity=(.1, 8), flow_trades=3, flow_ratio=1.3, flow_buy=150),
    'EARLY_FLOW': EntryRule(87, 22000, (0, 22), .95, .09, (2, 150), flow_trades=3, flow_ratio=1.5, wallets=2),
    'TREND_FLOW': EntryRule(85, 30000, (0, 18), 1.0, .06, (2, 1e7), (-5, 120), flow_trades=3, flow_ratio=1.4),
    'DEEP_LIQ_MOMENTUM': EntryRule(85, 80000, (1, 28), 1.0, .05, (2, 1e7), volume_liquidity=(.06, 7)),
    'LOW_VOL_FLOW': EntryRule(85, 30000, (-2, 12), .95, .06, (2, 1e7), (-30, 1000), flow_trades=4, flow_ratio=1.6),
    'HIGH_LMC_FLOW': EntryRule(85, 25000, (-1, 22), .95, .17, (2, 1e7), flow_trades=3, flow_ratio=1.3),
    'VOLUME_QUALITY': EntryRule(87, 30000, (0, 25), 1.05, .06, (2, 1e7), volume_liquidity=(.2, 6), flow_ratio=1.25),
    'BUY_PRESSURE': EntryRule(85, 25000, (-1, 25), 1.25, .06, (2, 1e7), flow_trades=4, flow_ratio=1.6, flow_buy=180),
    'SELL_WALL_SAFE': EntryRule(85, 30000, (-1, 22), 1.0, .06, (2, 1e7), flow_trades=4, flow_ratio=1.5, flow_buy=150, max_sell=250),
    'MICRO_BREAKOUT_SAFE': EntryRule(89, 35000, (3, 22), 1.1, .12, (2, 1e7), flow_ratio=1.3),
    'MATURE_FLOW': EntryRule(85, 40000, (-2, 20), .95, .06, (20, 1440), (-40, 1000), flow_trades=3, flow_ratio=1.5),
    'YOUNG_LIQ': EntryRule(88, 35000, (-1, 22), .95, .12, (2, 180), flow_ratio=1.25),
    'HIGH_SCORE_FLOW': EntryRule(92, 20000, (-2, 22), .95, .06, (2, 1e7), flow_trades=3, flow_ratio=1.3, flow_buy=120),
    'FLOW_PULLBACK': EntryRule(85, 30000, (-6, 6), .95, .06, (2, 1e7), (-5, 1000), flow_trades=4, flow_ratio=1.6, flow_buy=180),
    'SECOND_WAVE': EntryRule(85, 30000, (1, 18), 1.0, .06, (2, 1e7), (5, 180), (.1, 7), flow_trades=3, flow_ratio=1.4),
    'CLEAN_MOMENTUM': EntryRule(87, 30000, (1, 25), 1.05, .06, (2, 1e7), volume_liquidity=(.12, 5), flow_ratio=1.25),
    'CONFLUENCE_MAX': EntryRule(90, 40000, (0, 18), 1.1, .12, (2, 1e7), (-15, 1000), (.12, 6), flow_trades=4, flow_ratio=1.6, wallets=3),
}


def market_features(coin: dict[str, Any], flow: dict[str, Any] | None = None) -> dict[str, Any]:
    """Features shared by Lab and the main PAPER winner ensemble.

    All values come from the current market snapshot; the caller supplies only
    flow events available at decision time. No account or future trade data is
    used to decide whether an entry rule matches.
    """
    tx = (coin.get('txns') or {}).get('m5') or {}
    buys, sells = number(tx.get('buys')), number(tx.get('sells'))
    liquidity = number(coin.get('liquidityUsd'))
    market_cap = number(coin.get('marketCap') or coin.get('fdv'))
    changes = coin.get('priceChange') or {}
    volume_h1 = number((coin.get('volume') or {}).get('h1'))
    return {
        'score': number(coin.get('score')), 'liq': liquidity,
        'm5': number(changes.get('m5')), 'h1': number(changes.get('h1')),
        'bs': buys / max(sells, 1), 'lmc': liquidity / max(market_cap, 1),
        'age': number(coin.get('ageMinutes'), 999999),
        'vol1h': volume_h1, 'vol_liq': volume_h1 / max(liquidity, 1),
        'flow': flow or {'trades': 0, 'buys': 0, 'sells': 0, 'buy_usd': 0,
                         'sell_usd': 0, 'unique_wallets': 0, 'ratio': 0, 'max_sell': 0},
    }


def cooldown_remaining_ms(book: dict, address: str, now: int) -> int:
    latest = max((t for t in book.get('history', []) if t.get('address') == address),
                 key=lambda t: number(t.get('closed_at')), default=None)
    if latest is not None:
        if book.get('id') == 'SCALPER':
            seconds = (SCALPER_LOSS_REENTRY_SECONDS if number(latest.get('pnl_usd')) < 0
                       else SCALPER_REENTRY_SECONDS)
        else:
            seconds = LOSS_REENTRY_SECONDS if number(latest.get('pnl_usd')) < 0 else REENTRY_SECONDS
        return max(0, int(number(latest.get('closed_at'))) + seconds * 1000 - now)
    last = number((book.get('last_entry_by_address') or {}).get(address))
    return max(0, int(last) + REENTRY_SECONDS * 1000 - now) if last else 0


def entry_notional_limit(strategy_id: str, balance: float, requested: float) -> float:
    """Cap SCALPER risk at 25% of cash without exceeding its minimum size."""
    balance, requested = max(0.0, number(balance)), max(0.0, number(requested))
    if strategy_id == 'SCALPER':
        capped = min(requested, balance * SCALPER_MAX_BALANCE_FRACTION)
        return capped if capped >= SCALPER_MIN_NOTIONAL_USD else 0.0
    return min(requested, balance)


def entry_minimum_notional(strategy_id: str) -> float:
    return SCALPER_MIN_NOTIONAL_USD if strategy_id == 'SCALPER' else MIN_NOTIONAL_USD


def usable_feed_coin(coin: dict, now: int) -> bool:
    stamp = number(coin.get('updatedAt'))
    return (bool(ADDRESS.fullmatch(str(coin.get('address') or '')))
            and bool(ADDRESS.fullmatch(str(coin.get('pairAddress') or '')))
            and number(coin.get('priceUsd')) > 0 and number(coin.get('liquidityUsd')) >= 10000
            and stamp > 0 and -5000 <= now - stamp <= MAX_FEED_AGE_MS)


def affordable_entry(coin: dict, balance: float, limit: float,
                     entry: Callable, exit: Callable,
                     minimum_notional: float = MIN_NOTIONAL_USD) -> dict | None:
    """Try smaller paper sizes without changing the cost model or using leverage."""
    balance, limit = number(balance), number(limit)
    minimum_notional = max(0.01, number(minimum_notional, MIN_NOTIONAL_USD))
    if min(balance, limit) < minimum_notional:
        return None
    probe = entry(coin, minimum_notional)
    network = number(probe.get('network_fee_usd'), math.inf)
    cap = math.floor(min(limit, balance - network) * 100) / 100
    size = cap
    attempted = set()
    for _ in range(50):
        if size < minimum_notional:
            return None
        size = max(minimum_notional, math.floor(size * 100) / 100)
        if size in attempted:
            return None
        attempted.add(size)
        opening = entry(coin, size)
        committed = number(opening.get('capital_committed_usd'), math.inf)
        quantity = number(opening.get('quantity'))
        closing = exit(coin, quantity)
        net = number(closing.get('net_proceeds_usd')) - committed
        pct = net / size * 100
        if quantity > 0 and committed <= balance + 1e-9 and -MAX_ENTRY_COST_PCT <= pct <= 0:
            return {'notional': size, 'entry': opening, 'mark': closing,
                    'initial_pnl_usd': net, 'initial_pnl_pct': pct}
        if size == minimum_notional:
            return None
        size = max(minimum_notional, size * 0.85)
    return None


def policy_config() -> dict:
    return {'version': POLICY_VERSION, 'reentry_seconds': REENTRY_SECONDS,
            'loss_reentry_seconds': LOSS_REENTRY_SECONDS,
            'scalper_reentry_seconds': SCALPER_REENTRY_SECONDS,
            'scalper_loss_reentry_seconds': SCALPER_LOSS_REENTRY_SECONDS,
            'scalper_max_balance_fraction': SCALPER_MAX_BALANCE_FRACTION,
            'scalper_min_notional_usd': SCALPER_MIN_NOTIONAL_USD,
            'scalper_min_balance_for_entry': SCALPER_MIN_NOTIONAL_USD / SCALPER_MAX_BALANCE_FRACTION,
            'max_entry_roundtrip_cost_pct': MAX_ENTRY_COST_PCT,
            'feed_max_age_seconds': MAX_FEED_AGE_MS / 1000,
            'execution_basis': 'ESTIMATED_PAPER_COSTS_NOT_LIVE_FILLS'}
