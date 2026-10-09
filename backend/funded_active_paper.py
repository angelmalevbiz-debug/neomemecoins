"""Opt-in, bounded prospective policy for the four named PAPER Lab books.

No LIVE executor, fake trades, leverage, ledger reset or profitability claim.
The old position/ledger format remains readable; new positions freeze their
own exits. Observation seats and admission use the same candidate universe.
"""
import math
import os
import json
from dataclasses import replace

import structural_rug_guard as rug

VERSION = 'FUNDED_ACTIVE_PAPER_V3_MOMENTUM_PULSE_100'
CAPACITY_TEST_VERSION = 'PAPER_CAPACITY_TEST_V1_FIXED_100'
CAPACITY_TEST_ENV = 'NEO_LAB_CAPACITY_TEST_ENABLED'
MANAGED_VERSIONS = frozenset({'FUNDED_ACTIVE_PAPER_V1', 'FUNDED_ACTIVE_PAPER_V2_FIXED_100', VERSION,
                              CAPACITY_TEST_VERSION})
ENV = 'NEO_LAB_FUNDED_ACTIVE_ENABLED'
FUNDING_ENV = 'NEO_LAB_AUTHORIZED_CAPITAL_USD'
FUNDING_VERSION = 'USER_AUTHORIZED_PAPER_CAPITAL_1000_20261009'
MAX_SLOTS = 4
MAX_NOTIONAL_USD = 100.0
MIN_NOTIONAL_USD = 100.0
MAX_POSITION_FRACTION = .5
MAX_EXPOSURE_FRACTION = .5
ORDERS_PER_HOUR = 50
DAILY_LOSS_FRACTION = .05
SOL = 'So11111111111111111111111111111111111111112'
# Fixed hypotheses, not thresholds chosen from profitable historical trades.
RULES = {
    'EARLY': dict(age=(2, 120), liquidity=50_000, move=(1, 20), hour=(-10, 100),
                  buy_sell=1.0, liquidity_cap=.02, turnover=(.05, 8), stop=6.0, cost=2.75),
    # The fresh confirmed 30-second buy pulse remains the actual admission
    # signal. Don't wait for a lagging five-minute +0.5% candle to see it.
    'MOMENTUM': dict(age=(2, 10_000_000), liquidity=100_000, move=(0, 15), hour=(-10, 40),
                     buy_sell=1.0, liquidity_cap=.015, turnover=(.03, 6), stop=4.0, cost=2.0),
    'PRECISION': dict(age=(5, 10_000_000), liquidity=150_000, move=(.5, 8), hour=(0, 20),
                      buy_sell=1.5, liquidity_cap=.02, turnover=(.03, 3), stop=4.0, cost=1.5),
    'ULTRA_PRECISION': dict(age=(10, 10_000_000), liquidity=250_000, move=(.5, 6), hour=(0, 15),
                            buy_sell=2.0, liquidity_cap=.025, turnover=(.03, 2), stop=3.0, cost=1.5),
}


def enabled():
    return os.getenv(ENV) == '1'


def capacity_test_enabled():
    # Never selected implicitly or in LIVE, even if a shell inherits the flag.
    return (enabled() and os.getenv(CAPACITY_TEST_ENV)=='1' and os.getenv('NEO_ENGINE_MODE')=='PAPER'
            and os.getenv('NEO_EXECUTION_MODE','PAPER')=='PAPER')


def reporting_version():
    return CAPACITY_TEST_VERSION if capacity_test_enabled() else VERSION


def applies(book):
    return enabled() and book.get('id') in RULES and book.get('portfolio_group') == 'PROMOTED_PAPER'


def apply_authorized_funding(books, now):
    """One audited virtual top-up, never replenishment of trading losses.

    Explicitly enabled only by the owner-authorized local launcher. Validate
    the entire cohort before any credit; preserve histories and open lots.
    Restart uses total contributed capital, never a target cash balance.
    """
    target = os.getenv(FUNDING_ENV)
    if target is None:
        return []
    if target != '1000' or not enabled():
        raise ValueError('Unsupported or disabled PAPER capital authorization')
    plan = []
    for sid in RULES:
        book = books.get(sid)
        if (not isinstance(book, dict) or book.get('id') != sid
                or book.get('portfolio_group') != 'PROMOTED_PAPER'):
            raise ValueError('Capital authorization requires the complete funded PAPER cohort')
        contributed = finite(book.get('starting_balance'), math.nan)
        balance = finite(book.get('balance'), math.nan)
        events = book.get('funding_events', [])
        if (not math.isfinite(contributed) or not 0 < contributed <= 1000
                or not math.isfinite(balance) or balance < 0 or not isinstance(events, list)
                or not all(isinstance(e, dict) for e in events)):
            raise ValueError('Invalid PAPER funding ledger')
        previous = [e for e in events if e.get('id') == FUNDING_VERSION]
        if len(previous) > 1 or (previous and contributed != 1000):
            raise ValueError('Inconsistent PAPER funding authorization')
        if contributed < 1000:
            if previous:
                raise ValueError('Refusing repeated PAPER capital credit')
            plan.append((book, 1000-contributed, contributed, balance, events))
    applied = []
    for book, amount, contributed, balance, events in plan:
        event = dict(id=FUNDING_VERSION, strategy_id=book['id'],
                     kind='PAPER_CAPITAL_CONTRIBUTION', amount_usd=amount,
                     at=int(now), contributed_before_usd=contributed, contributed_after_usd=1000.0,
                     balance_before_usd=balance, balance_after_usd=balance+amount,
                     real_money=False, profit=False, history_reset=False)
        book.setdefault('initial_starting_balance_usd', contributed)
        book['funding_events'] = [*events, event]
        book['balance'] = balance+amount
        book['starting_balance'] = book['allocation_usd'] = 1000.0
        applied.append({'strategy_id':book['id'], **event})
    return applied


def finite(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (ValueError, TypeError, OverflowError):
        return default


def matches(strategy_id, coin):
    rule = RULES.get(strategy_id)
    if not rule or str(coin.get('dexId') or '').lower() != 'pumpswap':
        return False
    raw_quote = coin.get('quoteToken')
    if (coin.get('quoteTokenAddress') != SOL or
            (raw_quote is not None and (not isinstance(raw_quote, dict) or
             raw_quote.get('address', SOL) != SOL))):
        return False
    tx = ((coin.get('txns') or {}).get('m5') or {})
    changes, volume = coin.get('priceChange') or {}, coin.get('volume') or {}
    cap = coin.get('marketCap')
    if cap is None:
        cap = coin.get('fdv')
    values = [finite(v, math.nan) for v in (coin.get('ageMinutes'), coin.get('liquidityUsd'),
               cap, changes.get('m5'), changes.get('h1'), tx.get('buys'), tx.get('sells'), volume.get('h1'))]
    if not all(math.isfinite(v) for v in values):
        return False
    age, liq, cap, move, hour, buys, sells, turnover = values
    if cap <= 0 or liq <= 0 or min(buys, sells, turnover) < 0 or buys != int(buys) or sells != int(sells):
        return False
    return (rule['age'][0] <= age <= rule['age'][1] and liq >= rule['liquidity']
            and rule['move'][0] <= move <= rule['move'][1]
            and rule['hour'][0] <= hour <= rule['hour'][1]
            and buys / max(sells, 1) >= rule['buy_sell']
            and rule['liquidity_cap'] <= liq / cap < 1
            and rule['turnover'][0] <= turnover / liq <= rule['turnover'][1])


def structural_parameters(strategy_id):
    """PAPER scope: retain ticker sightings/reuse veto with one hour of continuity."""
    return replace(rug.PARAMS, young_pool_max_age_minutes=RULES[strategy_id]['age'][0],
                   ticker_registry_min_coverage_minutes=60.0)


def seed_pair_history(history, path, now):
    """Replay genuine recent main-feed observations, never invent coverage or prices."""
    result={'version':'FUNDED_HEAT_SEED_V1','status':'NO_SEED','samples':0}
    if not enabled() or not path.is_file():
        return result
    try:
        if path.stat().st_size>32*1024*1024:
            raise ValueError('seed too large')
        seed=json.loads(path.read_text(encoding='utf-8'))
        until=finite(seed.get('observed_until'),-1)
        if seed.get('version')!='FUNDED_HEAT_SEED_V1' or not 0<=now-until<=120_000:
            return {**result,'status':'STALE_OR_UNKNOWN_SEED'}
        rows=seed.get('observations')
        if not isinstance(rows,list) or not rows or len(rows)>150_000:
            raise ValueError('invalid row bound')
        previous=0
        for row in rows:
            available=finite(row.get('available_at'),-1)
            c=row.get('coin') or {}
            observed=finite(c.get('updatedAt'),-1)
            if (not until-3_660_000<=observed<=available<=until<=now or available<previous
                    or not isinstance(c.get('sources'),list) or not c['sources']
                    or not all(isinstance(s,str) and s.strip() for s in c['sources'])
                    or not rug.market_observation(c) or finite(c.get('priceUsd'))<=0
                    or not all(isinstance(c.get(k),str) and c[k].strip() for k in ('address','pairAddress'))):
                raise ValueError('invalid observation')
            previous=available
        for row in rows:
            result['samples']+=int(history.observe_coin(row['coin'],row['available_at']))
        history.prune(now)
        return {**result,'status':'REPLAYED_REAL_OBSERVATIONS','observed_until':until,
                'source':seed.get('source'),'profitability_proven':False}
    except (OSError,ValueError,TypeError,AttributeError,KeyError):
        return {**result,'status':'INVALID_SEED_IGNORED'}


def positions(book):
    rows = [p for p in (book.get('positions') or []) if isinstance(p, dict)]
    legacy = book.get('position')
    # JSON round-tripping creates separate objects for the compatibility alias.
    def key(p):
        return (p.get('strategy_id'), p.get('trade_no'), p.get('opened_at'), p.get('address'), p.get('pairAddress'))
    if isinstance(legacy, dict) and not any(key(p) == key(legacy) for p in rows):
        rows.insert(0, legacy)
    return rows


def attach(book, position):
    rows = positions(book)
    rows.append(position)
    book['positions'] = rows
    book['position'] = rows[0]


def synchronize_alias(book):
    if isinstance(book.get('positions'), list):
        book['positions'] = positions(book)
        book['position'] = book['positions'][0] if book['positions'] else None


def remove(book, position):
    rows = [p for p in positions(book) if p is not position]
    book['positions'] = rows
    book['position'] = rows[0] if rows else None


def current_trades(book):
    return [t for t in book.get('history', []) if t.get('entry_policy_version') == reporting_version()]


def is_active_position(position):
    return position.get('entry_policy_version') in MANAGED_VERSIONS


def capacity(book, now):
    rows = positions(book)
    exposure = sum(finite(p.get('remaining_cost_basis_usd'), finite(p.get('notional_usd'))) for p in rows)
    balance = max(0.0, finite(book.get('balance')))
    free = max(0.0, balance * MAX_EXPOSURE_FRACTION - exposure)
    # Changing size must not reset the hourly/daily risk ledger of V1.
    managed_trades = [t for t in book.get('history', []) if is_active_position(t)]
    opened = managed_trades + [p for p in rows if is_active_position(p)]
    orders = sum(0 <= now - finite(t.get('opened_at')) < 3_600_000 for t in opened)
    today = [t for t in managed_trades if int(finite(t.get('closed_at'))) // 86_400_000 == now // 86_400_000]
    marked_loss = sum(finite(p.get('open_pnl_usd')) for p in rows)
    daily_net = sum(finite(t.get('pnl_usd')) for t in today) + marked_loss
    daily_budget = max(0.0, finite(book.get('starting_balance'))) * DAILY_LOSS_FRACTION
    unpriced=any(p.get('quote_status') in {'stale','unavailable'} for p in rows)
    reason = ('funded_active_marks_unavailable' if unpriced else
              'funded_active_daily_loss_limit' if daily_net <= -daily_budget else
              'funded_active_slots_full' if len(rows) >= MAX_SLOTS else
              'funded_active_hourly_order_limit' if orders >= ORDERS_PER_HOUR else
              'funded_active_exposure_limit' if free < MIN_NOTIONAL_USD else None)
    return dict(open_positions=len(rows), max_positions=MAX_SLOTS, exposure_usd=round(exposure, 6),
                funded_capital_usd=finite(book.get('starting_balance')),
                fixed_notional_usd=MAX_NOTIONAL_USD,
                effective_position_capacity=min(MAX_SLOTS, int(balance*MAX_EXPOSURE_FRACTION//MAX_NOTIONAL_USD)),
                available_exposure_usd=free, orders_last_60m=orders, target_orders_per_hour=ORDERS_PER_HOUR,
                daily_net_usd=round(daily_net, 6), daily_loss_limit_usd=daily_budget, blocked_reason=reason)


def exit_parameters(strategy_id):
    return dict(version=VERSION, stop_loss_net_pct=RULES[strategy_id]['stop'],
                take_profit_net_pct=6.0 if strategy_id == 'EARLY' else 4.0,
                max_hold_minutes=4.0, profit_trail_arm_net_pct=2.0, profit_trail_drawdown_pct=1.0)


def exit_reason(position, net_pct, hold_minutes):
    exits = position['exit_parameters']
    if net_pct <= -exits['stop_loss_net_pct']:
        return 'FUNDED_ACTIVE_STOP_NET'
    if net_pct >= exits['take_profit_net_pct']:
        return 'FUNDED_ACTIVE_TAKE_PROFIT_NET'
    peak = max(finite(position.get('peak_net_pct'), net_pct), net_pct)
    if peak >= exits['profit_trail_arm_net_pct'] and peak - net_pct >= exits['profit_trail_drawdown_pct']:
        return 'FUNDED_ACTIVE_PROFIT_TRAIL_NET'
    if hold_minutes >= exits['max_hold_minutes']:
        return 'FUNDED_ACTIVE_MAX_HOLD_4'
    return None


def performance(book, now):
    trades = current_trades(book)
    wins = sum(finite(t.get('pnl_usd')) > 0 for t in trades)
    policy_opens=trades+[p for p in positions(book) if p.get('entry_policy_version')==reporting_version()]
    return {**capacity(book, now), 'version': reporting_version(), 'fixed_notional_usd': MAX_NOTIONAL_USD,
            'capacity_test':capacity_test_enabled(), 'strategy_validation':False,
            'risk_limits_shadow_only':capacity_test_enabled(),
            'strategy_policy_version':VERSION,
            'strategy_policy_closed_trades':sum(t.get('entry_policy_version')==VERSION for t in book.get('history',[])),
            'orders_last_60m':sum(0<=now-finite(p.get('opened_at'))<3_600_000 for p in policy_opens),
            'trades': len(trades), 'wins': wins,
            'win_rate': round(wins / len(trades) * 100, 1) if trades else None,
            'net_pnl_usd': round(sum(finite(t.get('pnl_usd')) for t in trades), 4),
            'closed_last_60m': sum(0 <= now - finite(t.get('closed_at')) < 3_600_000 for t in trades),
            'fees_included': True, 'profitability_proven': False, 'real_execution_enabled': False}


def candidate_config():
    return {sid: {**rule, 'candidate_branches': [{'id': VERSION + '_' + sid,
            'constraints': dict(rule), 'score_is_admission_gate': False}]} for sid, rule in RULES.items()}


def config():
    return dict(version=reporting_version(), enabled=enabled(), max_positions_per_strategy=MAX_SLOTS,
                capacity_test_enabled=capacity_test_enabled(),capacity_test_version=CAPACITY_TEST_VERSION,
                capacity_test_rules={'target_total_slots':16,'notional_usd':100,'minimum_liquidity_usd':50_000,
                    'strategy_signal_required':False,'fresh_full_safety_required':True,
                    'independent_exact_pool_price_required':True,'max_observation_age_ms':12_000,
                    'flow_and_defense_and_cost_caps_and_loss_rate_limits':'RECORDED_SHADOW_ONLY',
                    'cash_and_exposure_enforced':True,'synthetic_ticks_or_fills':False,
                    'profitable_strategy_validation':False},
                maximum_notional_usd=MAX_NOTIONAL_USD, max_position_fraction=MAX_POSITION_FRACTION,
                minimum_notional_usd=MIN_NOTIONAL_USD, entry_size_rule='FIXED_NOTIONAL_NO_BACKOFF',
                max_exposure_fraction=MAX_EXPOSURE_FRACTION, target_orders_per_hour=ORDERS_PER_HOUR,
                target_is_not_a_minimum_or_promise=True, daily_loss_fraction=DAILY_LOSS_FRACTION,
                ticker_min_continuous_coverage_minutes=60, main_ticker_guard_unchanged=True,
                rules=candidate_config(), exits={sid: exit_parameters(sid) for sid in RULES},
                costs_unchanged=True, profitability_proven=False, real_execution_enabled=False)
