"""Tape-driven, past-only entry signals for H.simulate.

A tape event can trigger an entry only at the first harness point whose time is >= the moment the
engine had the event (available), and only while the event is fresh (point time - block time <=
fresh_s). Every quantity derived from the series uses the Past view at the decision point.
"""
import bisect, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import harness as H
import tape_load as TL

_IDX = {}


def _index(min_sol, side=1):
    """pair -> (sorted available times, parallel list of (event_time, sol, wallet))."""
    key = (min_sol, side)
    if key not in _IDX:
        T = TL.load()
        out = {}
        for pair, d in T['tape'].items():
            rows = [(av, et, sol, w) for et, av, sd, w, sol in zip(d['et'], d['av'], d['side'], d['w'], d['sol'])
                    if sd == side and sol == sol and sol >= min_sol]
            if rows:
                rows.sort()
                out[pair] = ([r[0] for r in rows], [(r[1], r[2], r[3]) for r in rows])
        _IDX[key] = out
    return _IDX[key]


def fresh_events(P, idx, fresh_s=60):
    """Events that became available in (t_prev, t_now] with block time within fresh_s of now."""
    e = idx.get(P.static('pair'))
    if e is None:
        return ()
    avs, info = e
    now = P.t
    prev = P('t', back=1)
    if not (prev == prev):
        prev = now - 15_000
    lo = bisect.bisect_right(avs, prev)
    hi = bisect.bisect_right(avs, now)
    if lo >= hi:
        return ()
    return [info[k] for k in range(lo, hi) if now - info[k][0] <= fresh_s * 1000]


def sol_usd(P):
    p, n = P('price'), P('pnative')
    return p / n if (p > 0 and n > 0) else 0.0


def whale_signal(min_sol=5.0, min_usd=0.0, min_age_min=0.0, max_age_min=None, min_liq=0.0, max_liq=None,
                 min_fee=0.0, max_fee=200.0, fresh_s=60, rug_screen=True, extra=None, side=1):
    """Enter after a single large tape swap (side=1: BUY follow, side=-1: SELL dip-buy) of >= min_sol SOL."""
    def sig(P):
        idx = _index(min_sol if min_sol > 0 else 0.0, side)
        if P.static('pair') not in idx:
            return False
        ev = fresh_events(P, idx, fresh_s)
        if not ev:
            return False
        su = sol_usd(P)
        if not any(sol * su >= min_usd for _, sol, _ in ev):
            return False
        liq, age = P('liq'), P('age')
        if not (liq >= min_liq) or (max_liq is not None and liq > max_liq):
            return False
        if min_age_min > 0 and not (age >= min_age_min):
            return False
        if max_age_min is not None and not (age <= max_age_min):
            return False
        fee = P.fee_bps()
        if fee < min_fee or fee > max_fee:
            return False
        if rug_screen and H.interim_rug_risk(P):
            return False
        if extra is not None and not extra(P, ev):
            return False
        return True
    return sig


def tape_covered_index():
    """pair -> sorted available times of all events (for coverage tests)."""
    if 'cov' not in _IDX:
        T = TL.load()
        _IDX['cov'] = {p: sorted(d['av']) for p, d in T['tape'].items()}
    return _IDX['cov']


def covered(P, within_s=300):
    a = tape_covered_index().get(P.static('pair'))
    if not a:
        return False
    k = bisect.bisect_right(a, P.t) - 1
    return k >= 0 and a[k] > P.t - within_s * 1000


def random_covered_signal(prob, salt='f8', min_age_min=0.0, min_liq=0.0, max_liq=None, min_fee=0.0, max_fee=200.0,
                          rug_screen=True):
    """Random entries restricted to tape-covered moments with the same static filters (baseline)."""
    def sig(P):
        if P.static('pair') not in tape_covered_index():
            return False
        if not H.hashed_coin(P.static('pair'), P.t, prob, salt):
            return False
        if not covered(P, 300):
            return False
        liq, age = P('liq'), P('age')
        if not (liq >= min_liq) or (max_liq is not None and liq > max_liq):
            return False
        if min_age_min > 0 and not (age >= min_age_min):
            return False
        fee = P.fee_bps()
        if fee < min_fee or fee > max_fee:
            return False
        if rug_screen and H.interim_rug_risk(P):
            return False
        return True
    return sig
