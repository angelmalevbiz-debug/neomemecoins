"""F2 mean reversion / dip buying - pre-selected configs (chosen on TRAIN only) and random baselines.

Self-contained: past-only features are built through the harness Past view (P(f, back=k) reads of
points <= now), advanced incrementally per pair in time order.  No future data is touched.

CONFIGS (pre-registered on train before any holdout look):
  1. F2_OVERSOLD1H_STAB_20_25_90  - coin already down >= 12% over 1 h (DexScreener pc1h), then a further
     8..25% drop below its 15-min high, then stabilisation (5-min low at least 60 s old, price 1..4% off
     that low).  Exit +20% / -25% net, 90 min.
  2. F2_OVERSOLD_COMBO_STAB_25_30_120 - same, context pc1h <= -12% OR pc6h <= -15%; exit +25 / -30 / 120 min.
  3. F2_OVERSOLD1H_KNIFE_CONTROL  - the 'catch the falling knife' control for (1): identical context and dip,
     but buys AT the 5-min low (low < 10 s old) with no stabilisation.  Not expected to be a candidate.
Universe for all: PumpSwap SOL-quoted pools, liquidity >= $50k, interim rug screen applied.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
from collections import deque

NAN = float('nan')
LIQ_MIN = 50_000


# ------------------------------------------------------------------ causal features
class _State:
    __slots__ = ('last', 'maxq', 'minq', 'feat')

    def __init__(self):
        self.last = -1
        self.maxq = deque()   # rolling 15-min max of price: (t, price), decreasing
        self.minq = deque()   # rolling 5-min min of price: (t, price), increasing
        self.feat = None


def _push(st, t, p):
    if not (p > 0):
        return
    q = st.maxq
    while q and q[-1][1] <= p:
        q.pop()
    q.append((t, p))
    while q and q[0][0] <= t - 900_000:
        q.popleft()
    q = st.minq
    while q and q[-1][1] >= p:
        q.pop()
    q.append((t, p))
    while q and q[0][0] <= t - 300_000:
        q.popleft()


class Tracker:
    """dd900 (price vs rolling 15-min high), tlow5 (s since the rolling 5-min low), bounce5 (price vs that low).

    Advanced per pair through the Past view only; the value at index i depends only on points 0..i."""

    def __init__(self):
        self.st = {}

    def __call__(self, P):
        pair = P.static('pair')
        i = P.history_len() - 1
        st = self.st.get(pair)
        if st is None or i < st.last:
            st = _State()
            self.st[pair] = st
        if i != st.last:
            for k in range(st.last + 1, i + 1):
                back = i - k
                _push(st, P('t', back=back), P('price', back=back))
            st.last = i
            t, p = P('t'), P('price')
            if st.maxq and st.minq and p > 0:
                st.feat = (p / st.maxq[0][1] - 1, (t - st.minq[0][0]) / 1000, p / st.minq[0][1] - 1)
            else:
                st.feat = (NAN, NAN, NAN)
        return st.feat


def _universe(P):
    if not (P('liq') >= LIQ_MIN):
        return False
    return not H.interim_rug_risk(P)


def _context(P, pc1h_max=None, pc6h_max=None):
    ok1 = pc1h_max is not None and P('pc1h') <= pc1h_max
    ok6 = pc6h_max is not None and P('pc6h') <= pc6h_max
    return ok1 or ok6


def make_signal(pc1h_max=-12.0, pc6h_max=None, mode='stab', dd_lo=-0.25, dd_hi=-0.08,
                tlow_min=60.0, bounce_lo=0.01, bounce_hi=0.04):
    tr = Tracker()

    def signal(P):
        if not _universe(P):
            return False
        if not _context(P, pc1h_max, pc6h_max):
            return False
        dd, tlow, bounce = tr(P)
        if not (dd_lo <= dd <= dd_hi):
            return False
        if mode == 'stab':
            return tlow >= tlow_min and bounce_lo <= bounce < bounce_hi
        if mode == 'knife':
            return tlow < 10
        return True
    return signal


def make_random(prob, salt, pc1h_max=None, pc6h_max=None):
    """Random entries in the same universe (and the same coarse context when given)."""
    def signal(P):
        if not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not _universe(P):
            return False
        if pc1h_max is None and pc6h_max is None:
            return True
        return _context(P, pc1h_max, pc6h_max)
    return signal


EXIT_A = dict(tp=20.0, stop=-25.0, hold_min=90.0)
EXIT_B = dict(tp=25.0, stop=-30.0, hold_min=120.0)

CONFIGS = [
    {'name': 'F2_OVERSOLD1H_STAB_20_25_90',
     'description': 'PumpSwap liq>=50k, rug screen; pc1h<=-12%; price 8-25% below 15-min high; 5-min low >=60 s old '
                    'and price 1-4% above it (stabilised). Exit +20% / -25% net / 90 min. '
                    'Train (net50): n31, 13 pairs, mean +2.28%, median +4.54%, PF 1.25.',
     'signal': make_signal(pc1h_max=-12.0, mode='stab'),
     'kwargs': dict(EXIT_A)},
    {'name': 'F2_OVERSOLD_COMBO_STAB_25_30_120',
     'description': 'Same dip + stabilisation, context pc1h<=-12% OR pc6h<=-15%. Exit +25% / -30% net / 120 min. '
                    'Train (net50): n37, 13 pairs, mean +1.61%, PF 1.15.',
     'signal': make_signal(pc1h_max=-12.0, pc6h_max=-15.0, mode='stab'),
     'kwargs': dict(EXIT_B)},
    {'name': 'F2_OVERSOLD1H_KNIFE_CONTROL',
     'description': "Falling-knife control for config 1: same universe, context and 8-25% dip, but buys AT the 5-min "
                    "low (low <10 s old), no stabilisation. Exit +20% / -25% / 90 min.",
     'signal': make_signal(pc1h_max=-12.0, mode='knife'),
     'kwargs': dict(EXIT_A)},
]

BASELINES = [
    {'name': 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB',
     'description': 'Random entries (hashed coin p=0.005) in liq>=50k + rug screen + pc1h<=-12%, exits as config 1.',
     'signal': make_random(0.005, 'f2r1', pc1h_max=-12.0),
     'kwargs': dict(EXIT_A)},
    {'name': 'RANDOM_ctxcombo_for_F2_OVERSOLD_COMBO',
     'description': 'Random entries (p=0.004) in liq>=50k + rug screen + (pc1h<=-12% or pc6h<=-15%), exits as config 2.',
     'signal': make_random(0.004, 'f2r1', pc1h_max=-12.0, pc6h_max=-15.0),
     'kwargs': dict(EXIT_B)},
    {'name': 'RANDOM_universe_for_F2_KNIFE_CONTROL',
     'description': 'Random entries (p=0.0005) in liq>=50k + rug screen only, exits as config 1/3.',
     'signal': make_random(0.0005, 'f2r1'),
     'kwargs': dict(EXIT_A)},
]
