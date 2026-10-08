"""Signal factories for the F2 dip-buy family (past-only: Past view + causal Tracker)."""
import math, sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
import harness as H
from feat import Tracker


def nz(x, d=0.0):
    return x if (x is not None and x == x) else d


def universe(P, liq_min=50_000, liq_max=None, fee_min=None, fee_max=None):
    liq = P('liq')
    if not (liq >= liq_min):
        return False
    if liq_max is not None and not (liq < liq_max):
        return False
    if fee_min is not None or fee_max is not None:
        fee = P.fee_bps()
        if fee_min is not None and fee < fee_min:
            return False
        if fee_max is not None and fee > fee_max:
            return False
    return not H.interim_rug_risk(P)


def dip_signal(liq_min=50_000, liq_max=None, fee_min=None, fee_max=None, dd_lo=-0.25, dd_hi=-0.08, dd_key='dd900',
               mode='any', tlow_min=60, bounce_lo=0.01, bounce_hi=0.04, sells_falling=False,
               pc6h_max=None, pc1h_max=None, pc24_max=None, crash_guard=-0.25, ret60_min=None):
    """mode: 'any' (dip only), 'knife' (at the 5-min low, no stabilization), 'stab' (low >= tlow_min s old and
    bounce in [bounce_lo, bounce_hi))."""
    tr = Tracker()

    def sig(P):
        if not universe(P, liq_min, liq_max, fee_min, fee_max):
            return False
        if pc6h_max is not None and not (P('pc6h') <= pc6h_max):
            return False
        if pc1h_max is not None and not (P('pc1h') <= pc1h_max):
            return False
        if pc24_max is not None and not (P('pc24') <= pc24_max):
            return False
        f = tr(P)
        d = f[dd_key]
        if not (dd_lo <= d <= dd_hi):
            return False
        if crash_guard is not None and not (f['dd900'] >= crash_guard):
            return False
        if mode == 'knife':
            if not (f['tlow5'] < 10):
                return False
        elif mode == 'stab':
            if not (f['tlow5'] >= tlow_min and bounce_lo <= f['bounce5'] < bounce_hi):
                return False
        if sells_falling:
            s60 = P.ago('s5', 60)
            if not (P('s5') < s60):
                return False
        if ret60_min is not None:
            p60 = P.ago('price', 60)
            if not (p60 > 0 and P('price') / p60 - 1 >= ret60_min):
                return False
        return True
    return sig


def random_signal(prob, salt, liq_min=50_000, liq_max=None, fee_min=None, fee_max=None, pc6h_max=None, pc1h_max=None):
    def sig(P):
        if not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not universe(P, liq_min, liq_max, fee_min, fee_max):
            return False
        if pc6h_max is not None and not (P('pc6h') <= pc6h_max):
            return False
        if pc1h_max is not None and not (P('pc1h') <= pc1h_max):
            return False
        return True
    return sig


def retrace_exit(r=0.5, dd_key='dd900'):
    """Gross price target: recover fraction r of the entry-time drawdown (cached in pos on first call)."""
    tr = Tracker()

    def ex(P, pos):
        if 'tgt' not in pos:
            back = P.history_len() - 1 - (pos['entry_i'] - 1)   # decision point = entry_i - 1
            # recompute the drawdown at the decision point from past data only
            hi = -1.0
            t_dec = P('t', back=back)
            k = back
            while True:
                tk = P('t', back=k)
                if tk != tk or tk < t_dec - 900_000:
                    break
                hi = max(hi, P('price', back=k))
                k += 1
                if k > P.history_len() - 1:
                    break
            p_dec = P('price', back=back)
            p_ent = P('price', back=back - 1)
            dd = p_dec / hi - 1 if hi > 0 else 0.0
            pos['tgt'] = p_dec * (1 + r * (-dd) / (1 + dd)) if dd < 0 else p_ent * 1.05
        if P('price') >= pos['tgt']:
            return 'RETRACE'
        return None
    return ex
