"""F9 regime / timing filters - selected configs (chosen on TRAIN only) and their random baselines.

Regime used: a past-only, market-wide 'dip' state built once per wall-clock minute T from every
PumpSwap/SOL pool that printed a point in the 3 minutes up to T with liquidity >= $20k:
  med15  = median over those pools of (price at T / price 15 min earlier - 1), in %, USD prices
  med15n = the same with SOL-native prices (pnative), i.e. the SOL/USD move removed
  br15   = share of those pools whose price is above its price 15 min earlier
Every value at T uses only points with t <= T; a signal at time t reads the row floor(t / 60 s).
The comparison point must be at most 10 min staler than T - 15 min, and >= 8 pools are required.

Universe for all configs: PumpSwap SOL pools with liquidity >= $50k that pass H.interim_rug_risk.
Hypothesis kept from train: entering AFTER a market-wide 15-minute decline (contrarian timing) is
less bad than entering when the market is rising. On train the filter moved single-salt bases by
+1..+4 pts/trade, but averaged over 20 random salts it added only ~+0.4..+0.9 pts to random entries -
small next to the ~4-5.5 % stressed round-trip cost of these pools. The dip rule combined with the
market-dip filter beat 20/20 random+filter nulls on train (the 'systematic dip reverts' story).
Selection used 450+ train evaluations; expect heavy shrinkage out of sample.
"""
import math, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

MIN = 60_000
ACTIVE_MS = 180_000
LAG_TOL_MS = 600_000
_GRID = None


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def regime_grid():
    """Past-only per-minute market regime (med15, med15n, br15). Built once, cached."""
    global _GRID
    if _GRID is not None:
        return _GRID
    series, meta = H.load()
    g0 = int(math.ceil(meta['t0'] / MIN) * MIN)
    M = int((meta['t1'] - g0) // MIN) + 1
    r15 = [[] for _ in range(M)]
    r15n = [[] for _ in range(M)]
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts, px, pn, lq = s['t'], s['price'], s['pnative'], s['liq']
        n = len(ts)
        k = kk = 0
        m_first = int(math.ceil((ts[0] - g0) / MIN))
        m_end = min(M - 1, int((ts[-1] + ACTIVE_MS - g0) // MIN))
        for m in range(max(0, m_first), m_end + 1):
            T = g0 + m * MIN
            while k + 1 < n and ts[k + 1] <= T:
                k += 1
            if ts[k] > T or T - ts[k] > ACTIVE_MS or not (lq[k] >= 20_000):
                continue
            tt = T - 900_000
            while kk + 1 < n and ts[kk + 1] <= tt:
                kk += 1
            if ts[kk] <= tt and tt - ts[kk] <= LAG_TOL_MS:
                if px[kk] > 0 and px[k] > 0:
                    r15[m].append(px[k] / px[kk] - 1)
                    if pn[k] > 0 and pn[kk] > 0:
                        r15n[m].append(pn[k] / pn[kk] - 1)
    nan = float('nan')
    g = {'g0': g0, 'M': M, 'med15': [nan] * M, 'med15n': [nan] * M, 'br15': [nan] * M}
    for m in range(M):
        if len(r15[m]) >= 8:
            g['med15'][m] = _median(r15[m]) * 100
            g['br15'][m] = sum(1 for x in r15[m] if x > 0) / len(r15[m])
        if len(r15n[m]) >= 8:
            g['med15n'][m] = _median(r15n[m]) * 100
    _GRID = g
    return g


def regime(col, t_ms):
    g = regime_grid()
    m = int((t_ms - g['g0']) // MIN)
    if m < 0 or m >= g['M']:
        return float('nan')
    return g[col][m]


# ---------------------------------------------------------------- universe and base signals
def U_liquid(P):
    if not (P('liq') >= 50_000):
        return False
    return not H.interim_rug_risk(P)


def sig_dip(P):
    """Own price down >= 10 % over 15 min, liquidity >= 85 % of 15 min ago, 24h change > -50 %."""
    if not U_liquid(P):
        return False
    p, p15 = P('price'), P.ago('price', 900)
    if not (p > 0 and p15 > 0 and p / p15 - 1 <= -0.10):
        return False
    l, l15 = P('liq'), P.ago('liq', 900)
    if not (l15 > 0 and l / l15 >= 0.85):
        return False
    pc24 = P('pc24')
    return pc24 == pc24 and pc24 > -50


def below(col, thr):
    def f(P):
        v = regime(col, P.t)
        return v == v and v < thr
    return f


def rnd(prob, salt='r'):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


_mkt_dip = below('med15', -0.2)
_mkt_dip_native = below('med15n', -0.2)
_breadth_low = below('br15', 0.35)

E2 = dict(stop=-15, tp=20, hold_min=60, cooldown_s=300)
E3 = dict(stop=-99, tp=None, hold_min=30, cooldown_s=300)

CONFIGS = [
    {'name': 'F9_DIP_MKTDIP_E2',
     'description': 'Dip entry (own price -10% in 15 min, liquidity held, 24h change > -50%) in the rug-screened '
                    'liq>=$50k PumpSwap universe, only while the market-wide median 15-min move is below -0.2% '
                    '(contrarian timing). Exits: stop -15%, target +20%, max hold 60 min (net, modeled).',
     'signal': lambda P: sig_dip(P) and _mkt_dip(P),
     'kwargs': dict(E2)},
    {'name': 'F9_DIP_MKTDIPN_H30',
     'description': 'Same dip entry, only while the market-wide median 15-min move in SOL terms (SOL/USD move '
                    'removed) is below -0.2%. Exit: plain 30-min hold, no stop/target.',
     'signal': lambda P: sig_dip(P) and _mkt_dip_native(P),
     'kwargs': dict(E3)},
    {'name': 'F9_BASKET_MKTDIP_E2',
     'description': 'Pure timing test (no selection, no random salt): enter every universe pool while the market-wide '
                    'median 15-min move is below -0.2%. Exits: stop -15%, target +20%, max hold 60 min. '
                    'Train-negative; kept as the falsification test of regime timing on its own.',
     'signal': lambda P: U_liquid(P) and _mkt_dip(P),
     'kwargs': dict(E2)},
]

# Random entries in the same universe with the same exits; probabilities calibrated on TRAIN trade counts.
BASELINES = [
    {'name': 'RND_for_F9_DIP_MKTDIP_E2',
     'description': 'Random entries (p=0.0007/point) in the rug-screened liq>=$50k universe, no regime filter, exits -15/+20/60.',
     'signal': lambda P: U_liquid(P) and rnd(0.0007, 'f9a')(P),
     'kwargs': dict(E2)},
    {'name': 'RND_for_F9_DIP_MKTDIPN_H30',
     'description': 'Random entries (p=0.00035/point) in the same universe, no regime filter, plain 30-min hold.',
     'signal': lambda P: U_liquid(P) and rnd(0.00035, 'f9c')(P),
     'kwargs': dict(E3)},
    {'name': 'RND_for_F9_BASKET_MKTDIP_E2',
     'description': 'Random entries (p=0.004/point) in the same universe, no regime filter, exits -15/+20/60.',
     'signal': lambda P: U_liquid(P) and rnd(0.004, 'f9d')(P),
     'kwargs': dict(E2)},
]
