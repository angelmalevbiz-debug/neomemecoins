"""F4 attention / promotion-event strategies (PAPER research only).

GRID holds every configuration evaluated on TRAIN (counted in configs_tried).
CONFIGS holds the <= 3 configurations pre-selected on TRAIN; BASELINES the matching random-entry
baselines (same universe, same exits, similar trade count).

All signals are past-only: they read the harness Past view, plus per-pair state built incrementally
in time order (FirstSeen). Every signal applies the harness interim rug screen.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

WARMUP_MS = 300_000  # a flag already present in the first 5 minutes of the recording is not a fresh event
LATEST, BOOSTED, BOOSTED_LATEST = 4, 1, 2


def _t0():
    return H.load()[1]['t0']


class FirstSeen:
    """Per-pair, time-ordered memo of the first point where pred(P, back) held.
    Built only from points <= now; resets when a new simulation restarts a pair at an earlier index."""

    def __init__(self, pred):
        self.pred = pred
        self.m = {}

    def get(self, P):
        pair = P.static('pair')
        i = P.history_len() - 1
        st = self.m.get(pair)
        if st is None or st[0] > i:
            st = [-1, None, None, None]   # scanned_i, first_i, first_price, first_t
            self.m[pair] = st
        for k in range(st[0] + 1, i + 1):
            if st[1] is None and self.pred(P, i - k):
                st[1], st[2], st[3] = k, P('price', back=i - k), P('t', back=i - k)
        st[0] = i
        return st


def _flag(mask):
    return lambda P, back: int(P('src', back=back)) & mask != 0


def _boost(P, back):
    return P('boost', back=back) > 0


def _fresh(st):
    return st[1] is not None and st[3] - _t0() >= WARMUP_MS


def ok(P):
    return not H.interim_rug_risk(P)


# ------------------------------------------------------------------ signal builders
def sig_first_flag(mask_or_boost):
    """Enter at the first point a pair ever carries the flag (paid profile 'latest' list, or a boost)."""
    fs = FirstSeen(_boost if mask_or_boost == 'boost' else _flag(mask_or_boost))

    def f(P):
        st = fs.get(P)
        return _fresh(st) and st[1] == P.history_len() - 1 and ok(P)
    return f


def sig_post_latest_bounce(min_age_s=600, max_age_s=2700, drop=0.5, bounce=1.05):
    """Capitulation bounce after a paid-profile event: price <= drop x event price, +5% in the last 60 s."""
    fs = FirstSeen(_flag(LATEST))

    def f(P):
        st = fs.get(P)
        if not _fresh(st):
            return False
        dt = (P.t - st[3]) / 1000
        if not (min_age_s <= dt <= max_age_s):
            return False
        p, p60 = P('price'), P.ago('price', 60)
        return p > 0 and st[2] > 0 and p <= drop * st[2] and p60 > 0 and p / p60 >= bounce and ok(P)
    return f


def sig_boost_list_member(max_fee=95, min_liq=50_000):
    def f(P):
        return (int(P('src')) & (BOOSTED | BOOSTED_LATEST)) != 0 and P.fee_bps() <= max_fee and P('liq') >= min_liq \
            and P('pc5') > 0 and ok(P)
    return f


def _surge(P, back, mult=3):
    b5, b1h = P('b5', back=back), P('b1h', back=back)
    return b5 >= 30 and b1h > 0 and b5 >= mult * b1h / 12


def sig_surge(fee_lo, fee_hi, liq_lo, liq_hi, pc5_sign=None):
    """Organic attention surge: 5-min buy count crosses 3x its 1-h pace (age >= 60 min)."""
    def f(P):
        if not (P('age') >= 60) or P.history_len() < 2:
            return False
        if not (_surge(P, 0) and not _surge(P, 1)):
            return False
        fee, liq = P.fee_bps(), P('liq')
        if not (fee_lo <= fee <= fee_hi and liq_lo <= liq < liq_hi):
            return False
        if pc5_sign is not None and not ((P('pc5') > 0) == (pc5_sign > 0)):
            return False
        return ok(P)
    return f


DEF = dict(stop=-5, tp=10, hold_min=60)
GRID = [
    {'name': 'A1_latest_first_default', 'description': 'Enter at the first point a pair carries the paid-profile latest flag; -5/+10/60',
     'make': lambda: sig_first_flag(LATEST), 'kwargs': dict(DEF)},
    {'name': 'A2_latest_first_quick', 'description': 'Same event, quick scalp -5/+5/5min',
     'make': lambda: sig_first_flag(LATEST), 'kwargs': dict(stop=-5, tp=5, hold_min=5)},
    {'name': 'A3_latest_first_trail', 'description': 'Same event, stop -8, no TP, trail arm 5 / give-back 4, hold 30',
     'make': lambda: sig_first_flag(LATEST), 'kwargs': dict(stop=-8, tp=None, trail_arm=5, trail=4, hold_min=30)},
    {'name': 'B1_boost_first_default', 'description': 'Enter at the first point a pair ever shows a boost amount; -5/+10/60',
     'make': lambda: sig_first_flag('boost'), 'kwargs': dict(DEF)},
    {'name': 'B2_boost_first_long', 'description': 'Same event, -10/+20/120',
     'make': lambda: sig_first_flag('boost'), 'kwargs': dict(stop=-10, tp=20, hold_min=120)},
    {'name': 'B3_boost_list_member', 'description': 'On a boosted list, fee<=95, liq>=50k, pc5>0; -5/+10/60, cooldown 30 min',
     'make': lambda: sig_boost_list_member(), 'kwargs': dict(DEF, cooldown_s=1800)},
    {'name': 'D1_post_latest_bounce', 'description': '10-45 min after the first latest flag, price <= 50% of event price and +5% in 60 s; -10/+15/20',
     'make': lambda: sig_post_latest_bounce(), 'kwargs': dict(stop=-10, tp=15, hold_min=20)},
    {'name': 'E1_surge_mid_dip', 'description': 'Buy-count surge (3x 1h pace) in fee55-95 / liq 50-250k with pc5<0; -5/+10/60',
     'make': lambda: sig_surge(55, 95, 50_000, 250_000, -1), 'kwargs': dict(DEF)},
    {'name': 'E2_surge_nontoxic', 'description': 'Buy-count surge (3x 1h pace), fee<=95, liq>=50k, any direction; -5/+10/60',
     'make': lambda: sig_surge(0, 95, 50_000, 1e18, None), 'kwargs': dict(DEF)},
]

# ------------------------------------------------------------------ random-entry baselines (same universes)
def _rnd(prob, salt):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)


def base_latest_list(prob):
    """Random times while the pair is on the paid-profile latest list (after warmup), rug-screened."""
    r = _rnd(prob, 'f4A')

    def f(P):
        return (int(P('src')) & LATEST) != 0 and P.t - _t0() >= WARMUP_MS and ok(P) and r(P)
    return f


def base_boosted(prob):
    """Random times while the pair shows a boost or sits on a boosted list (after warmup), rug-screened."""
    r = _rnd(prob, 'f4B')

    def f(P):
        return ((int(P('src')) & (BOOSTED | BOOSTED_LATEST)) != 0 or P('boost') > 0) and P.t - _t0() >= WARMUP_MS \
            and ok(P) and r(P)
    return f


def base_nontoxic(prob):
    """Random times in the E2 universe: fee<=95, liq>=50k, age>=60 min, rug-screened."""
    r = _rnd(prob, 'f4E')

    def f(P):
        return P('age') >= 60 and P.fee_bps() <= 95 and P('liq') >= 50_000 and ok(P) and r(P)
    return f


# Pre-selected on TRAIN only (one per attention hypothesis; every one was already negative on TRAIN,
# so none can qualify as a candidate - they are kept so the integrator can reproduce the refutation).
_G = {g['name']: g for g in GRID}


def _cfg(name):
    g = _G[name]
    return {'name': name, 'description': g['description'], 'signal': g['make'](), 'kwargs': dict(g['kwargs'])}


CONFIGS = [_cfg('A1_latest_first_default'), _cfg('B1_boost_first_default'), _cfg('E2_surge_nontoxic')]
BASELINES = [
    {'name': 'RND_A1_on_latest_list', 'description': 'Random entries while on the paid-profile latest list; -5/+10/60',
     'signal': base_latest_list(0.003), 'kwargs': dict(DEF)},
    {'name': 'RND_B1_boosted', 'description': 'Random entries while boosted / on a boosted list; -5/+10/60',
     'signal': base_boosted(0.0001), 'kwargs': dict(DEF)},
    {'name': 'RND_E2_nontoxic', 'description': 'Random entries, fee<=95, liq>=50k, age>=60; -5/+10/60',
     'signal': base_nontoxic(0.0005), 'kwargs': dict(DEF)},
]
