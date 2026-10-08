"""Signal families that replay the LIVE engines' entry rules on the harness Past view.

Each live rule set is imported read-only from backend/ (bytecode writing disabled) and applied to a
coin dict rebuilt from past-only fields. Three flow variants per family:
  VF    - tape-verified 30 s window present, fresh (<= 12 s old) and passing
          promoted_entry_guard.flow_admission thresholds (+ the family's own flow rule)
  PROXY - DexScreener 5-minute txns proxy fixed before any outcome was seen:
          b5 >= 3 and b5 / max(s5, 1) >= 1.2
  NONE  - no flow requirement (upper bound on frequency)
"""
import sys, math
sys.dont_write_bytecode = True
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/../../backend' + '')
import harness as H
import winner_ensemble as WE
import order_flow_adaptive_oct4 as OFA
import cost_first_engine_profile as CFP

SOL = 'So11111111111111111111111111111111111111112'


def coin(P):
    return {'address': P.static('mint'), 'pairAddress': P.static('pair'), 'dexId': P.static('dex'),
            'quoteTokenAddress': SOL if P.static('quote_sol') == 1 else 'other',
            'priceUsd': P('price'), 'priceNative': P('pnative'), 'liquidityUsd': P('liq'),
            'marketCap': P('mcap'), 'priceChange': {'m5': P('pc5'), 'h1': P('pc1h')},
            'txns': {'m5': {'buys': P('b5'), 'sells': P('s5')}}, 'volume': {'h1': P('v1h')},
            'ageMinutes': P('age'), 'score': P('score')}


def ok(x):
    return x == x


def vf_admit(P):
    t = P('vf_trades')
    if not ok(t):
        return None
    age = P('vf_age_ms')
    if not (ok(age) and 0 <= age <= 12_000):
        return None
    w, b, s = P('vf_wallets'), P('vf_buy'), P('vf_sell')
    w = w if ok(w) else 0
    b = b if ok(b) else 0
    s = s if ok(s) else 0
    if t >= 3 and w >= 2 and b > 0 and b >= 1.2 * max(s, 1.0):
        return {'trades': t, 'buy_usd': b, 'sell_usd': s, 'unique_wallets': w, 'max_sell_usd': 0}
    return None


def proxy(P):
    b5, s5 = P('b5'), P('s5')
    return ok(b5) and ok(s5) and b5 >= 3 and b5 / max(s5, 1.0) >= 1.2


def main_requested(P):
    liq, age = P('liq'), P('age')
    age = age if ok(age) else 999999
    base = 35 if liq < 8000 else 50 if liq < 15000 else 75 if liq < 30000 else 100 if liq < 60000 else 150 if liq < 120000 else 200
    if age <= 15:
        base *= .7
    elif age <= 45:
        base *= .85
    return round(max(25.0, min(200.0, base)), 2)


def ladder_min(req):
    cur = math.floor(req * 100) / 100
    steps = [cur]
    while len(steps) < 3 and cur > 25:
        cur = max(25.0, math.floor(cur * .5 * 100) / 100)
        steps.append(cur)
    return steps[-1]


# ---------------------------------------------------------------- WINNER_ENSEMBLE (main 8878)
def we_market(P):
    if not (P('liq') > 0):
        return False
    c = coin(P)
    if not WE.market_candidates(c):
        return False
    rt = P.rt_cost_pct(ladder_min(main_requested(P)))
    return rt is not None and rt <= 1.5


def we_signal(variant):
    def sig(P):
        if not we_market(P):
            return False
        if variant == 'NONE':
            return True
        if variant == 'PROXY':
            return proxy(P)
        f = vf_admit(P)
        return f is not None and bool(WE.matches(coin(P), f))
    return sig


# ---------------------------------------------------------------- ORDER_FLOW_ADAPTIVE (18801)
def ofa_market(P):
    if not (P('liq') > 0):
        return False
    rej = [x for x in OFA.market_rejections(coin(P), now=P.t) if x != 'stale_feed']
    if rej:
        return False
    rt = P.rt_cost_pct(200.0)
    return rt is not None and rt <= 2.75


def ofa_signal(variant):
    def sig(P):
        if not ofa_market(P):
            return False
        if variant == 'NONE':
            return True
        if variant == 'PROXY':
            return proxy(P)
        f = vf_admit(P)
        if f is None:
            return False
        ratio = f['buy_usd'] / max(f['sell_usd'], 1.0)
        conv = P('conv')
        return (f['trades'] >= 4 and ratio >= 1.3 and f['buy_usd'] >= 150 and f['unique_wallets'] >= 4
                and ok(conv) and conv >= 72)
    return sig


# ---------------------------------------------------------------- COST_FIRST (18802) + rug screen
def cf_market(P):
    if not (P('liq') > 0):
        return False
    # The V1 universe this study measured (see funnel.py); the rug screen follows.
    if CFP.physical_universe_rejections(coin(P), 200.0):
        return False
    return not H.interim_rug_risk(P)


def cf_signal(variant):
    def sig(P):
        if not cf_market(P):
            return False
        if variant == 'NONE':
            return True
        if variant == 'PROXY':
            return proxy(P)
        return vf_admit(P) is not None
    return sig


def cf_size(P):
    return min(200.0, math.floor(P('liq') * 0.001 * 100) / 100)


FAMILIES = {
    'WE': {'market': we_market, 'signal': we_signal, 'size_fn': main_requested},
    'OFA': {'market': ofa_market, 'signal': ofa_signal, 'size_fn': None},
    'CF': {'market': cf_market, 'signal': cf_signal, 'size_fn': cf_size},
}
EXITS = dict(stop=-5.0, tp=10.0, hold_min=60.0, cooldown_s=1200)


def rnd(prob, salt):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)
