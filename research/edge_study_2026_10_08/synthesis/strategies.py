"""SYNTHESIS - PAPER Lab forward-test hypotheses (NOT evidence-backed edges). PAPER research only.

None of these configurations passed the candidate gate in the 22.8 h study (0 candidates out of 27 family configs,
65,093 train evaluations). They are the three hypotheses with the most consistent RELATIVE evidence (better than random
entries in the same universe in both train and holdout, or the only positive holdout under the audited harness), each
combined with the frozen rug guard. Their measured absolute expectancy after calibrated stressed costs is NEGATIVE or
unproven. They exist to be forward-tested in the PAPER Lab with pre-registered kill/promote rules, never to be traded
as income sources on today's evidence.

  LAB_A_SURGE_EST_GUARD   = f4_attention E2_surge_nontoxic, unchanged, + rug_guard_v1.
        Evidence: train -3.2 %/trade vs its random baseline about -4.6..-5.1; holdout -3.2 % (calibrated net50, n=32,
        16 pairs) vs random -5.0. Relative edge ~+1.8..+2.5 pp in both splits; absolute negative.
  LAB_B_DIP_MKTDIP_GUARD  = f9_regime F9_DIP_MKTDIP_E2, unchanged, + rug_guard_v1.
        Evidence: holdout -1.27 % (n=12, 6 pairs) vs random -3.85 %; post-hoc decomposition: coin dips bought while
        the whole market dips lose far less than coin-specific dips (-0.4 vs -4.7 % holdout). Weak, small n.
  LAB_C_TAPE_BREADTH_EST  = f5_flowaccel F5_C1_TAPE_BREADTH_NOCHASE + rug_guard_v1, restricted to established mid-fee
        pools (liq >= $150k, fee 52.5-90 bps). The restriction is POST-HOC (all 11 positive guarded holdout trades sat
        in $196-338k / 60-90 bps pools; train had ~6 trades there and they lost). Only forward data can test it.
        It needs the engine's on-chain tape: here it reads f5_flowaccel/tapefeat.py (read-only tape snapshot cache).
        Refuted as a standalone edge by four verifiers (train negative under audited fills; holdout positive only
        with zero tape latency). Forward-test only with real (Jupiter/on-chain) fills.

Imports: the harness is imported exactly as specified for integrators. rug_guard_v1 is taken from the harness when
present (harness_final / leaderboard) and otherwise from a verbatim local copy of rug/rug_guard.py logic
(RUG_GUARD_V1_RESEARCH_2026_10_08), so the module also runs under harness v1 / v2.
All signals read only the Past view (and, for LAB_C, tape events whose ingestion time <= decision time).
"""
import math, os, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

# ------------------------------------------------------------------ rug guard (frozen rule, verbatim logic)
def _rug_guard_local(P):
    """True = do not trade. LP_PULLABLE (liq/mcap >= 1), YOUNG_POOL (age < 720 min), FAKE_MCAP (mcap >= $20M and
    liq/mcap < 1% and age < 14 d); fail closed on missing / non-positive liq, mcap or age."""
    liq, mc, age = P('liq'), P('mcap'), P('age')
    if not (liq == liq and mc == mc and age == age and liq > 0 and mc > 0 and age >= 0):
        return True
    lmc = liq / mc
    if lmc >= 1.0:
        return True
    if age < 720.0:
        return True
    if mc >= 20e6 and lmc < 0.01 and age < 14 * 1440.0:
        return True
    return False


rug_guard = getattr(H, 'rug_guard_v1', _rug_guard_local)


def _fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def _rnd(prob, salt):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


# ------------------------------------------------------------------ LAB_A: organic buy-count surge (f4 E2)
def _surge(P, back, mult=3):
    b5, b1h = P('b5', back=back), P('b1h', back=back)
    return b5 >= 30 and b1h > 0 and b5 >= mult * b1h / 12


def _u_a(P):
    """E2 universe: age >= 60 min, fee <= 95 bps, liq >= $50k, interim rug screen, plus rug_guard_v1."""
    if not (P('age') >= 60):
        return False
    if not (P.fee_bps() <= 95 and P('liq') >= 50_000):
        return False
    return not H.interim_rug_risk(P) and not rug_guard(P)


def sig_lab_a(P):
    if P.history_len() < 2:
        return False
    if not (_surge(P, 0) and not _surge(P, 1)):
        return False
    return _u_a(P)


EXITS_A = dict(stop=-5.0, tp=10.0, hold_min=60.0)

# ------------------------------------------------------------------ LAB_B: coin dip while the market dips (f9)
_MIN = 60_000
_ACTIVE_MS = 180_000
_LAG_TOL_MS = 600_000
_GRID = {}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _regime_grid():
    """Past-only per-minute market-wide median 15-min move (verbatim f9_regime construction). Cached per dataset
    (keyed by the loaded meta, so a re-loaded / extended dataset rebuilds it)."""
    series, meta = H.load()
    key = (meta['t0'], meta['t1'], len(series))
    g = _GRID.get(key)
    if g is not None:
        return g
    g0 = int(math.ceil(meta['t0'] / _MIN) * _MIN)
    M = int((meta['t1'] - g0) // _MIN) + 1
    r15 = [[] for _ in range(M)]
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts, px, lq = s['t'], s['price'], s['liq']
        n = len(ts)
        k = kk = 0
        m_first = int(math.ceil((ts[0] - g0) / _MIN))
        m_end = min(M - 1, int((ts[-1] + _ACTIVE_MS - g0) // _MIN))
        for m in range(max(0, m_first), m_end + 1):
            T = g0 + m * _MIN
            while k + 1 < n and ts[k + 1] <= T:
                k += 1
            if ts[k] > T or T - ts[k] > _ACTIVE_MS or not (lq[k] >= 20_000):
                continue
            tt = T - 900_000
            while kk + 1 < n and ts[kk + 1] <= tt:
                kk += 1
            if ts[kk] <= tt and tt - ts[kk] <= _LAG_TOL_MS and px[kk] > 0 and px[k] > 0:
                r15[m].append(px[k] / px[kk] - 1)
    med = [float('nan')] * M
    for m in range(M):
        if len(r15[m]) >= 8:
            med[m] = _median(r15[m]) * 100
    g = {'g0': g0, 'M': M, 'med15': med}
    _GRID.clear()
    _GRID[key] = g
    return g


def market_med15(t_ms):
    g = _regime_grid()
    m = int((t_ms - g['g0']) // _MIN)
    if m < 0 or m >= g['M']:
        return float('nan')
    return g['med15'][m]


def _u_b(P):
    """f9 universe (liq >= $50k, interim rug screen) plus rug_guard_v1."""
    if not (P('liq') >= 50_000):
        return False
    return not H.interim_rug_risk(P) and not rug_guard(P)


def _dip(P):
    p, p15 = P('price'), P.ago('price', 900)
    if not (p > 0 and p15 > 0 and p / p15 - 1 <= -0.10):
        return False
    l, l15 = P('liq'), P.ago('liq', 900)
    if not (l15 > 0 and l / l15 >= 0.85):
        return False
    pc24 = P('pc24')
    return pc24 == pc24 and pc24 > -50


def sig_lab_b(P):
    if not _u_b(P) or not _dip(P):
        return False
    v = market_med15(P.t)
    return v == v and v < -0.2


EXITS_B = dict(stop=-15.0, tp=20.0, hold_min=60.0, cooldown_s=300)

# ------------------------------------------------------------------ LAB_C: tape breadth in established mid-fee pools
_TF = None


def _tf():
    global _TF
    if _TF is None:
        d = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f5_flowaccel'
        if d not in sys.path:
            sys.path.insert(0, d)
        import tapefeat as TF  # read-only: loads the existing tape_cache.pkl
        _TF = TF
    return _TF


def _u_c(P):
    liq = P('liq')
    if not (liq >= 150_000):
        return False
    if not (52.5 <= P.fee_bps() <= 90):
        return False
    if H.interim_rug_risk(P) or rug_guard(P):
        return False
    age = _tf().last_available_age_s(P.static('pair'), P.t)
    return age is not None and age <= 600


def _nochase(P):
    p0, p1 = P('price'), P.ago('price', 300)
    if not (_fin(p1) and p1 > 0 and _fin(p0)):
        return False
    r = 100 * (p0 / p1 - 1)
    return -2 <= r <= 5


def _tape_breadth(P):
    f = _tf().flow(P.static('pair'), P.t, 300)
    return (f is not None and f['ub'] >= 10 and f['newb'] >= 5 and _fin(f['top_share'])
            and f['top_share'] < 0.3 and f['net_sol'] > 0)


def sig_lab_c(P):
    return _u_c(P) and _nochase(P) and _tape_breadth(P)


def _size_c(P):
    return min(200.0, 0.002 * P('liq'))


EXITS_C = dict(stop=-10.0, tp=6.0, hold_min=30.0, size_fn=_size_c)

CONFIGS = [
    {'name': 'LAB_A_SURGE_EST_GUARD',
     'description': 'Organic buy-count surge in established non-toxic pools: DexScreener 5-min buys cross 3x their 1-h '
                    'pace (b5 >= 30, previous point below), fee <= 95 bps, liq >= $50k, interim screen + rug_guard_v1 '
                    '(so age >= 12 h). Exits -5/+10 net, 60 min, $200. Lab hypothesis: relative edge vs random only.',
     'signal': sig_lab_a, 'kwargs': dict(EXITS_A)},
    {'name': 'LAB_B_DIP_MKTDIP_GUARD',
     'description': 'Coin dip (-10 % in 15 min, liquidity >= 85 % of 15 min ago, 24h change > -50 %) bought only while '
                    'the market-wide median 15-min move is below -0.2 %; liq >= $50k, interim screen + rug_guard_v1. '
                    'Exits -15/+20 net, 60 min, $200. Lab hypothesis.',
     'signal': sig_lab_b, 'kwargs': dict(EXITS_B)},
    {'name': 'LAB_C_TAPE_BREADTH_EST',
     'description': 'Tape-live established mid-fee pool (liq >= $150k, fee 52.5-90 bps, interim screen + rug_guard_v1): '
                    'last 5 min >= 10 distinct buyer wallets, >= 5 first-time-buyer events, top buyer < 30 % of buy SOL, '
                    'net SOL inflow > 0, 5-min price change in [-2 %, +5 %]. Exits -10/+6 net, 30 min, size '
                    'min($200, 0.2 % liq). POST-HOC restriction: forward data only.',
     'signal': sig_lab_c, 'kwargs': dict(EXITS_C)},
]

BASELINES = [
    {'name': 'RND_LAB_A',
     'description': 'Random entries (hashed coin p=0.0005/point, salt synA) in the LAB_A universe, same exits.',
     'signal': lambda P: _u_a(P) and _rnd(0.0005, 'synA')(P), 'kwargs': dict(EXITS_A)},
    {'name': 'RND_LAB_B',
     'description': 'Random entries (p=0.0007/point, salt synB) in the LAB_B universe without dip or regime filter, '
                    'same exits.',
     'signal': lambda P: _u_b(P) and _rnd(0.0007, 'synB')(P), 'kwargs': dict(EXITS_B)},
    {'name': 'RND_LAB_C',
     'description': 'Random entries (p=0.007/point, salt synC) in the LAB_C tape-live universe with the same no-chase '
                    'filter, same exits and sizing.',
     'signal': lambda P: _u_c(P) and _nochase(P) and _rnd(0.007, 'synC')(P), 'kwargs': dict(EXITS_C)},
]

BASELINE_MAP = {'LAB_A_SURGE_EST_GUARD': ['RND_LAB_A'], 'LAB_B_DIP_MKTDIP_GUARD': ['RND_LAB_B'],
                'LAB_C_TAPE_BREADTH_EST': ['RND_LAB_C']}
