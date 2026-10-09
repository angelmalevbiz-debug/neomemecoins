"""Shared loader and per-point precomputation for the hf_architecture study (PAPER research, read-only).

Binds leaderboard/harness_final.py as `harness` exactly as rescore_lab_hypotheses.py / run_all.py do.
"""
import importlib.util
import math
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.normpath(os.path.join(HERE, '..', 'research', 'edge_study_2026_10_08'))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_harness():
    sys.path.insert(0, DEEP)
    H = _load('harness_final', os.path.join(DEEP, 'leaderboard', 'harness_final.py'))
    sys.modules['harness'] = H
    return H


NAN = float('nan')
PAID_BIT = 4  # SRC_BITS['latest'] (paid profile, token-profiles/latest)


def heat_flags(s, H):
    """Approximate HEAT_VETO_STACK_V1 per point (O(n) per pair). Returns bytearray of veto (1 = vetoed).

    (a) 5-min return >= +3% vs the price at or before t-300 s (needs a point at or before t-300 s, else warming)
    (b) b5/(b5+s5) >= 0.70  (c) v5/(v1h/12) >= 1.3  (d) pc6h >= 200 or pc24 >= 150
    (e) fee >= 100 bps and a paid 'latest' flag within 60 min  (f) price <= 0.75 x 15-min high or ret5 <= -20%
    (g) v5/liq >= 0.095.  Warm-up: no point at or before t-300 s, or pair first seen < 900 s ago -> veto.
    """
    ts, pr = s['t'], s['price']
    b5, s5, v5, v1h, pc6, pc24, liq, src = s['b5'], s['s5'], s['v5'], s['v1h'], s['pc6h'], s['pc24'], s['liq'], s['src']
    n = len(ts)
    out = bytearray(n)
    j300 = 0       # pointer: last index with t <= t_i - 300 s
    from collections import deque
    dq = deque()   # monotonic deque of indices for the 15-min max
    last_paid = -1e18
    t_first = ts[0] if n else 0
    for i in range(n):
        t, p = ts[i], pr[i]
        if int(src[i]) & PAID_BIT:
            last_paid = t
        # sliding max over (t-900 s, t]
        while dq and pr[dq[-1]] <= p:
            dq.pop()
        dq.append(i)
        while dq and ts[dq[0]] <= t - 900_000:
            dq.popleft()
        high = pr[dq[0]] if dq else p
        while j300 + 1 <= i and ts[j300 + 1] <= t - 300_000:
            j300 += 1
        ref = pr[j300] if ts[j300] <= t - 300_000 else NAN
        veto = False
        if not (p > 0):
            veto = True
        warming = not (ref == ref and ref > 0) or (t - t_first) < 900_000
        ret5 = (p / ref - 1) if (ref == ref and ref > 0 and p > 0) else NAN
        if warming:
            veto = True
        if ret5 == ret5 and (ret5 >= 0.03 or ret5 <= -0.20):
            veto = True
        bb, ss = b5[i], s5[i]
        if bb == bb and ss == ss and bb + ss >= 1 and bb / (bb + ss) >= 0.70:
            veto = True
        a, h = v5[i], v1h[i]
        if a == a and h == h and h > 0 and a / (h / 12) >= 1.3:
            veto = True
        if (pc6[i] == pc6[i] and pc6[i] >= 200) or (pc24[i] == pc24[i] and pc24[i] >= 150):
            veto = True
        if p > 0 and high > 0 and p <= 0.75 * high:
            veto = True
        L = liq[i]
        if a == a and L == L and L > 0 and a / L >= 0.095:
            veto = True
        if H.fee_bps(s, i) >= 100 and t - last_paid <= 3_600_000:
            veto = True
        out[i] = 1 if veto else 0
    return out


def universe_flags(s, H, notional, caps):
    """Per point: rug ok (rug_guard_v1 + interim screen), liq >= 50k, model rt cost at notional; returns
    (rug_ok bytearray, cost list, dict cap -> bytearray eligible-before-heat)."""
    n = len(s['t'])
    rug_ok = bytearray(n)
    cost = [NAN] * n
    elig = {c: bytearray(n) for c in caps}
    liq = s['liq']
    for i in range(n):
        P = H.Past(s, i)
        if H.rug_guard_v1(P) or H.interim_rug_risk(P):
            continue
        rug_ok[i] = 1
        if not (liq[i] >= 50_000):
            continue
        c = H.roundtrip_cost_pct(s, i, notional)
        if c is None:
            continue
        cost[i] = c
        for cap in caps:
            if c <= cap:
                elig[cap][i] = 1
    return rug_ok, cost, elig


def tick():
    return time.time()
