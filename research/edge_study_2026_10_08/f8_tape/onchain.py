"""On-chain marginal pool price from the swap tape (SOL per token -> USD), past-only by availability.

After a swap of dx SOL in a constant-product pool with SOL reserve X:
    BUY : post price = exec * (1 + dx / X)       (exec = SOL paid / tokens received)
    SELL: post price = exec * (1 - dx / X)       (exec = SOL received / tokens sold)
X is taken from the series liquidity (liq_usd / 2 / sol_usd) at the decision point. The fee is left
inside exec on purpose (a BUY's exec includes the fee -> slightly higher entry = conservative).
"""
import bisect, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import harness as H
import tape_load as TL

_BYAV = {}


def _by_av():
    """pair -> (sorted available times, rows (av, et, side, sol, tok)) - events sorted by availability, then block time."""
    if not _BYAV:
        T = TL.load()
        for pair, d in T['tape'].items():
            rows = sorted(zip(d['av'], d['et'], d['side'], d['sol'], d['tok']))
            _BYAV[pair] = ([r[0] for r in rows], rows)
    return _BYAV


def onchain_price_usd(s, k, t, max_age_s=30):
    """USD price implied by the latest-by-block-time swap among those available by t whose block time is within
    max_age_s of t, using series point k (<= decision time) for SOL/USD and reserves. None if unavailable."""
    e = _by_av().get(s['pair'])
    if e is None:
        return None
    avs, rows = e
    hi = bisect.bisect_right(avs, t)
    best = None
    # look back over events available by t (bounded scan)
    for m in range(hi - 1, max(-1, hi - 400), -1):
        av, et, side, sol, tok = rows[m]
        if t - av > 300_000:
            break
        if t - et > max_age_s * 1000 or not (sol == sol) or tok <= 0 or sol <= 0:
            continue
        if best is None or et > best[1]:
            best = rows[m]
    if best is None:
        return None
    av, et, side, sol, tok = best
    su = H.sol_usd(s, k)
    liq = s['liq'][k]
    if not (su > 0 and liq > 0):
        return None
    X = liq / 2 / su
    exec_px = sol / tok
    post = exec_px * (1 + sol / X) if side > 0 else exec_px * max(0.0, 1 - sol / X)
    return post * su
