"""F3 early-life pools - the 3 configurations pre-selected on TRAIN (entries before the 60% split), plus
random-entry baselines in the same universes with the same exits. Self-contained: imports only the harness.

All three share one idea (the only one that was not clearly negative on train): a PumpSwap pool younger
than the age cap that has already stayed in the scanner feed for >= 10 minutes, passes a strict rug/shape
screen, and shows organic growth over the last 15 minutes (liquidity and price both +10%, >= 5 unique
buyers in 5 min, buy share 50-80%). Exits differ. See REPORT.md for the holdout verdict (NOT a candidate).
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

NAN = float('nan')


def _ratio(a, b):
    return a / b if (b > 0 and a == a) else NAN


def observed_for(P, seconds):
    """The pool already had a point >= `seconds` ago (it has been in the scanner feed that long)."""
    v = P.ago('t', seconds)
    return v == v


def young(P, max_age_min):
    a = P('age')
    return a == a and a < max_age_min


def f3_rug_screen(P, min_liq=15_000, lm_lo=0.15, lm_hi=0.70):
    """Strict early-life screen (past-only):
    - liquidity >= $15k and liquidity/mcap in [0.15, 0.70] (pump.fun graduates sit ~0.3-0.45; the
      fake-liquidity launch family has liq/mcap 1.5-2.0, the fake-mcap family < 0.05)
    - liquidity >= 85% of its 15-min max (no drain in progress)
    - no single-print price jump/drop > 3x among the last 5 points (glitch / pull)
    - H.interim_rug_risk is False (fake mcap or ticker reuse)."""
    liq, mc = P('liq'), P('mcap')
    if not (liq >= min_liq and mc > 0):
        return False
    lm = liq / mc
    if not (lm_lo <= lm <= lm_hi):
        return False
    w = P.window('liq', 900)
    if w and liq < 0.85 * max(w):
        return False
    for b in range(1, 5):
        p0, p1 = P('price', back=b), P('price', back=b - 1)
        if p0 > 0 and p1 > 0 and (p1 / p0 > 3 or p0 / p1 > 3):
            return False
    return not H.interim_rug_risk(P)


def organic(P, liq_up=1.10, px_up=1.10, min_uw=5, br_lo=0.5, br_hi=0.8):
    if not (_ratio(P('liq'), P.ago('liq', 900)) >= liq_up):
        return False
    if not (_ratio(P('price'), P.ago('price', 900)) >= px_up):
        return False
    if not (P('hp_uw5') >= min_uw):
        return False
    b, s = P('b5'), P('s5')
    if not (b + s > 0):
        return False
    return br_lo <= b / (b + s) <= br_hi


def drain_exit(frac=0.6):
    """Exit when liquidity falls below frac x the entry-point liquidity (rug in progress)."""
    def fn(P, pos):
        l0 = P('liq', back=P.i - pos['entry_i'])
        if l0 > 0 and not (P('liq') >= frac * l0):
            return 'DRAIN'
        return None
    return fn


def size_fn(P):
    return min(200.0, P('liq') * 0.005)


def universe(max_age_min):
    return lambda P: young(P, max_age_min) and observed_for(P, 600) and f3_rug_screen(P)


def strategy(max_age_min):
    u = universe(max_age_min)
    return lambda P: u(P) and organic(P)


def random_in(max_age_min, prob, salt):
    u = universe(max_age_min)
    return lambda P: u(P) and H.hashed_coin(P.static('pair'), P.t, prob, salt)


E1 = dict(stop=-15, tp=None, trail_arm=20, trail=15, hold_min=60)
E2 = dict(stop=-25, tp=None, trail_arm=40, trail=25, hold_min=120)

CONFIGS = [
    {'name': 'F3_ORGANIC_24H_TRAIL20',
     'description': 'PumpSwap pool < 24 h old, in the feed >= 10 min, strict rug/shape screen, liq & price +10% over 15 min, '
                    '>= 5 unique buyers/5m, buy share 50-80%; stop -15, trail arm +20 / give-back 15, hold 60 min, drain exit; '
                    'size min($200, 0.5% of liq).',
     'signal': strategy(1440),
     'kwargs': dict(E1, exit_fn=drain_exit(0.6), size_fn=size_fn)},
    {'name': 'F3_ORGANIC_24H_WIDETRAIL40',
     'description': 'Same entry as F3_ORGANIC_24H_TRAIL20; wide fat-tail exit: stop -25, trail arm +40 / give-back 25, hold 120 min, drain exit.',
     'signal': strategy(1440),
     'kwargs': dict(E2, exit_fn=drain_exit(0.6), size_fn=size_fn)},
    {'name': 'F3_ORGANIC_2H_TRAIL20',
     'description': 'Same as F3_ORGANIC_24H_TRAIL20 restricted to pools < 2 h old (the purest early-life version).',
     'signal': strategy(120),
     'kwargs': dict(E1, exit_fn=drain_exit(0.6), size_fn=size_fn)},
]

# Random entries in the same universe, same exits/sizing; probabilities chosen (on TRAIN) for a similar trade count.
BASELINES = [
    {'name': 'RANDOM_U24H_TRAIL20', 'description': 'Random entries (hashed coin) in the F3 24 h universe, exits as F3_ORGANIC_24H_TRAIL20.',
     'signal': random_in(1440, 0.002, 'f3b'),
     'kwargs': dict(E1, exit_fn=drain_exit(0.6), size_fn=size_fn)},
    {'name': 'RANDOM_U24H_WIDETRAIL40', 'description': 'Random entries in the F3 24 h universe, exits as F3_ORGANIC_24H_WIDETRAIL40.',
     'signal': random_in(1440, 0.002, 'f3b'),
     'kwargs': dict(E2, exit_fn=drain_exit(0.6), size_fn=size_fn)},
    {'name': 'RANDOM_U2H_TRAIL20', 'description': 'Random entries in the F3 2 h universe, exits as F3_ORGANIC_2H_TRAIL20.',
     'signal': random_in(120, 0.005, 'f3b'),
     'kwargs': dict(E1, exit_fn=drain_exit(0.6), size_fn=size_fn)},
]
