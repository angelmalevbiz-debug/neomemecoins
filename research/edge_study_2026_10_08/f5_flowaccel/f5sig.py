"""F5 signal factories (past-only)."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from common import H, fin, va1h, va6h, ba1h, bshare5, ret, universe, rnd  # noqa: F401
import tapefeat as TF


def tape_live(P, max_age_s=600):
    a = TF.last_available_age_s(P.static('pair'), P.t)
    return a is not None and a <= max_age_s


def U(fee_lo=52.5, fee_hi=125, liq_lo=20_000, liq_hi=float('inf')):
    return lambda P: universe(P, fee_lo, fee_hi, liq_lo, liq_hi) and tape_live(P)


def tb(ub_min=10, newb_min=5, top_max=0.3, net_pos=True, univ=None, window_s=300, extra=None):
    """Tape breadth: many distinct buyers (and first-time buyers) in the last window, no single buyer
    dominating buy SOL, and net SOL inflow - all from swaps already ingested at decision time."""
    univ = univ or U()

    def sig(P):
        if not univ(P):
            return False
        if extra is not None and not extra(P):
            return False
        f = TF.flow(P.static('pair'), P.t, window_s)
        if f['ub'] < ub_min or f['newb'] < newb_min:
            return False
        if not (fin(f['top_share']) and f['top_share'] < top_max):
            return False
        return f['net_sol'] > 0 if net_pos else True
    return sig
