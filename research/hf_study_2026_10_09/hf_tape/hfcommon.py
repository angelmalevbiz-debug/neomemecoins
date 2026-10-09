"""hf_tape shared helpers (PAPER research only, read-only over the research data).

Loads leaderboard/harness_final.py exactly as rescore_lab_hypotheses.py does (bound as sys.modules['harness']),
the series cache, and the compact tape cache built by tape_build.py.

Honesty rules implemented here:
- A tape event is usable for a DECISION at time t only when its ingestion time av <= t and its block time et <= t.
- The on-chain FILL price at time x uses the true chain state: the last swap with block time <= x (any ingestion
  time), converted to a post-trade marginal price (constant product) and corrected for the decoder's measured
  half-spread. Costs on top of that mid are the harness model (fee + 2N/L impact + 20 bps) plus the I2 calibration.
- DexScreener fills use H.fill_index (first later point whose price differs, within 60 s).
"""
import bisect, importlib.util, math, os, pickle, sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.normpath(os.path.join(HERE, '..', 'research', 'edge_study_2026_10_08'))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, DEEP)
H = _load('harness_final', os.path.join(DEEP, 'leaderboard', 'harness_final.py'))
sys.modules['harness'] = H

series, meta = H.load()
T0, T1 = meta['t0'], meta['t1']
CUT = H.split_t(meta, 0.6)

with open(os.path.join(HERE, 'tape_hf.pkl'), 'rb') as _fh:
    _TP = pickle.load(_fh)
TAPE = _TP['tape']
TMETA = _TP['meta']
NAN = float('nan')


def ab(x):
    return (x or '')[:8]


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def sidx(s, t):
    """Last series index with t_i <= t, or -1."""
    return bisect.bisect_right(s['t'], t) - 1


# ------------------------------------------------------------------ guards / veto (decision-time, past-only)
def heat_flags(P):
    """HEAT_VETO_STACK (README / synthesis run_forward.py), all inputs are DexScreener fields at the decision point."""
    out = []
    p = P('price')
    p5 = P.ago('price', 300)
    ret5 = (p / p5 - 1) if (p > 0 and p5 > 0) else NAN
    b5, s5 = P('b5'), P('s5')
    bshare = b5 / (b5 + s5) if (fin(b5) and fin(s5) and b5 + s5 >= 1) else NAN
    v5, v1h = P('v5'), P('v1h')
    vacc = v5 / (v1h / 12) if (fin(v5) and fin(v1h) and v1h > 0) else NAN
    pc6h, pc24 = P('pc6h'), P('pc24')
    if ret5 >= 0.03:
        out.append('ret5')
    if bshare >= 0.70:
        out.append('bshare')
    if vacc >= 1.3:
        out.append('vacc')
    if pc6h >= 200 or pc24 >= 150:
        out.append('pc')
    fee = P.fee_bps()
    if fee >= 100 and any(int(x) & 4 for x in P.window('src', 3600) if x == x):
        out.append('paid')
    win = [x for x in P.window('price', 900) if x > 0]
    hi15 = max(win) if win else NAN
    if (p > 0 and hi15 > 0 and p / hi15 - 1 <= -0.25) or (ret5 <= -0.20):
        out.append('crash')
    liq = P('liq')
    if fin(v5) and liq > 0 and v5 / liq >= 0.095:
        out.append('turnover')
    return out


# ------------------------------------------------------------------ tape flow (decision-time, past-only)
def flow(pair, t, window_s, d=None):
    """Confirmed swaps with block time in (t - window, t] AND ingested by t.
    SWAP_ACTOR_NOT_TRANSACTION_SIGNER buys are excluded from buys/buyers (conservative, per forensics rec);
    such sells still count as sells."""
    d = d or TAPE.get(pair)
    if d is None:
        return None
    et, av = d['et'], d['av']
    hi = bisect.bisect_right(et, t)
    lo = bisect.bisect_right(et, t - window_s * 1000, 0, hi)
    nb = ns = 0
    bsol = ssol = 0.0
    buyers, sellers = {}, set()
    sg, q, w, ac = d['sgn'], d['q'], d['w'], d['actor']
    for k in range(lo, hi):
        if av[k] > t:
            continue
        if sg[k] > 0:
            if ac[k]:
                continue
            nb += 1
            bsol += q[k]
            buyers[w[k]] = buyers.get(w[k], 0.0) + q[k]
        else:
            ns += 1
            ssol += q[k]
            sellers.add(w[k])
    top = max(buyers.values()) if buyers else 0.0
    return {'nb': nb, 'ns': ns, 'bsol': bsol, 'ssol': ssol, 'ub': len(buyers), 'us': len(sellers),
            'top_share': (top / bsol) if bsol > 0 else NAN, 'net_sol': bsol - ssol}


# ------------------------------------------------------------------ on-chain mid
HALF_SPREAD = {}   # fee bucket -> half spread (fraction), measured by explore1.py; set_half_spread() installs it


def fee_bucket(fee):
    return 'le50' if fee <= 50 else ('55_95' if fee <= 95 else '100_125')


def set_half_spread(hs):
    HALF_SPREAD.clear()
    HALF_SPREAD.update(hs)


def post_price_sol(s, d, k):
    """Post-trade marginal price (SOL/token) after tape event k, constant product with SOL reserve from the series
    liquidity at the last point <= its block time. None if inputs are missing."""
    et = d['et'][k]
    i = sidx(s, et)
    if i < 0:
        i = 0
    su = H.sol_usd(s, i)
    liq = s['liq'][i]
    if not (su > 0 and liq > 0):
        return None
    X = liq / 2 / su
    q, tok = d['q'][k], d['tok'][k]
    ex = q / tok
    if d['sgn'][k] > 0:
        return ex * (1 + q / X)
    return ex * max(1e-9, 1 - q / X)


def chain_mid_sol(s, d, x, max_age_s=None):
    """(mid SOL/token, block time of the swap used, k) from the last swap with block time <= x (true chain state;
    ingestion time ignored because this prices a fill, not a decision). None when no swap / inputs missing or the
    last swap is older than max_age_s."""
    et = d['et']
    k = bisect.bisect_right(et, x) - 1
    if k < 0:
        return None
    if max_age_s is not None and x - et[k] > max_age_s * 1000:
        return None
    pp = post_price_sol(s, d, k)
    if pp is None:
        return None
    i = max(0, sidx(s, et[k]))
    h = HALF_SPREAD.get(fee_bucket(H.fee_bps(s, i)), 0.0)
    mid = pp / (1 + h) if d['sgn'][k] > 0 else pp / (1 - h)
    return mid, et[k], k


def ingested_mid_sol(s, d, t):
    """Mid from the last swap (by block time) among those INGESTED by t (decision-safe), or None."""
    et, av = d['et'], d['av']
    hi = bisect.bisect_right(et, t)
    k = hi - 1
    steps = 0
    while k >= 0 and av[k] > t and steps < 400:
        k -= 1
        steps += 1
    if k < 0 or av[k] > t:
        return None
    pp = post_price_sol(s, d, k)
    if pp is None:
        return None
    i = max(0, sidx(s, et[k]))
    h = HALF_SPREAD.get(fee_bucket(H.fee_bps(s, i)), 0.0)
    return (pp / (1 + h) if d['sgn'][k] > 0 else pp / (1 - h)), et[k]


# ------------------------------------------------------------------ costs (harness primitives)
def legs(s, j, size, reason, entry_px_usd, ev, exit_px_usd):
    """net0 / net50 / netcal (%) of buying `size` USD at series point j with mid entry_px_usd and selling at series
    point ev with mid exit_px_usd. entry_fill() prices at s['price'][j]; quantities are rescaled to the given mid
    (qty is proportional to 1/price). I2 calibration evaluated at the entry point, exit-reason extra on the exit."""
    cx = H.calib_extra_bps_per_leg(H.fee_bps(s, j), s['liq'][j], size)
    rx = H.calib_exit_reason_extra_bps(reason)
    p_j = s['price'][j]
    if not (p_j > 0 and entry_px_usd > 0 and exit_px_usd >= 0):
        return None
    out = {}
    for key, ex_e, ex_x in (('net0', 0.0, 0.0), ('net50', H.STRESS_BPS + cx, H.STRESS_BPS + cx + rx),
                            ('netcal', cx, cx + rx)):
        f = H.entry_fill(s, j, size, ex_e)
        if f is None:
            return None
        qty = f[0] * p_j / entry_px_usd
        v = H.exit_value(s, ev, qty, ex_x, exit_px_usd)
        if v is None:
            return None
        out[key] = 100 * (v - size - f[1]) / size
    out['cx'] = cx
    out['rx'] = rx
    return out
