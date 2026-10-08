"""Shared past-only signal pieces for F5 (flow acceleration / buyer breadth)."""
import sys, os
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def ratio(a, b):
    return a / b if (fin(a) and fin(b) and b > 0) else float('nan')


def va1h(P):
    return ratio(P('v5'), P('v1h') / 12)


def va6h(P):
    return ratio(P('v5'), P('v6h') / 72)


def ba1h(P):
    return ratio(P('b5'), P('b1h') / 12)


def bshare5(P):
    b, s = P('b5'), P('s5')
    return ratio(b, b + s) if fin(b) and fin(s) else float('nan')


def ret(P, seconds):
    p0, p1 = P('price'), P.ago('price', seconds)
    return 100 * (p0 / p1 - 1) if fin(p1) and p1 > 0 and fin(p0) else float('nan')


def universe(P, fee_lo, fee_hi, liq_lo, liq_hi=float('inf')):
    """PumpSwap fee tier in [fee_lo, fee_hi], liquidity in [liq_lo, liq_hi), never the interim rug family."""
    liq = P('liq')
    if not (liq_lo <= liq < liq_hi):
        return False
    f = P.fee_bps()
    if not (fee_lo <= f <= fee_hi):
        return False
    return not H.interim_rug_risk(P)


def rnd(prob, salt='f5'):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)
