"""Event table for the hf_signals study. PAPER research only.

One row per DexScreener REFRESH event (a point whose price differs from the previous point, contiguous feed
< 60 s) of a PumpSwap SOL pair that passes H.rug_guard_v1 and has liq >= $20k. Features are past-only (they use
points <= i). Outcomes use the audited harness: H.forward_net (next-refresh entry fill, horizon measured from the
decision, exit at the next refresh after the horizon, feed-gap/vanish rules) at net0 (model) and net50
(model + 50 bps/leg + CALIB_V1 per-leg extra; HOLD exits carry no stop/trail extra). Market-wide features are
computed in global time order from events at or before the decision time only.
"""
import bisect, math, os, pickle, sys, time
from common import H, fin, guard_ok_arrays, HERE, NAN
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
series, meta = H.load()
cut = H.split_t(meta, 0.6)
V_HOT_Q80 = 0.09525965037839669   # synthesis/pre_constants.json (pre-cutoff 80th pct of v5/liq)
HORIZONS = (60, 120, 180, 300)
NOTIONAL = 200.0

rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts, px, lq, mc, ag = s['t'], s['price'], s['liq'], s['mcap'], s['age']
    b5, s5, v5, v1h, pc6h, pc24 = s['b5'], s['s5'], s['v5'], s['v1h'], s['pc6h'], s['pc24']
    n = len(ts)
    prev_ref = None          # index of the previous refresh event in the current contiguous segment
    for i in range(1, n):
        if ts[i] - ts[i - 1] > 60_000:
            prev_ref = None
            continue
        if px[i] == px[i - 1]:
            continue
        # refresh event at i
        this_prev = prev_ref
        prev_ref = i
        if not (lq[i] >= 20_000) or not guard_ok_arrays(lq[i], mc[i], ag[i]):
            continue
        if not (px[i] > 0 and px[i - 1] > 0):
            continue
        P = H.Past(s, i)
        r1 = px[i] / px[i - 1] - 1
        # second step: price at the refresh before i-1's price was set
        r2 = NAN
        if this_prev is not None and px[this_prev - 1] > 0:
            r2 = px[i] / px[this_prev - 1] - 1
        dt_prev = (ts[i] - ts[this_prev]) / 1000 if this_prev is not None else NAN
        p60, p120, p300 = P.ago('price', 60), P.ago('price', 120), P.ago('price', 300)
        r60 = px[i] / p60 - 1 if p60 > 0 else NAN
        r120 = px[i] / p120 - 1 if p120 > 0 else NAN
        r300 = px[i] / p300 - 1 if p300 > 0 else NAN
        win = [x for x in P.window('price', 900) if x > 0]
        hi15 = max(win) if win else NAN
        lo15 = min(win) if win else NAN
        db5 = b5[i] - b5[i - 1] if fin(b5[i]) and fin(b5[i - 1]) else NAN
        ds5 = s5[i] - s5[i - 1] if fin(s5[i]) and fin(s5[i - 1]) else NAN
        dv5 = v5[i] - v5[i - 1] if fin(v5[i]) and fin(v5[i - 1]) else NAN
        bshare = b5[i] / (b5[i] + s5[i]) if fin(b5[i]) and fin(s5[i]) and b5[i] + s5[i] >= 1 else NAN
        vacc = v5[i] / (v1h[i] / 12) if fin(v5[i]) and fin(v1h[i]) and v1h[i] > 0 else NAN
        turn = v5[i] / lq[i] if fin(v5[i]) else NAN
        fee = H.fee_bps(s, i)
        latest60 = False
        if fee >= 100:
            latest60 = any(int(x) & 4 for x in P.window('src', 3600) if x == x)
        v_mom = (r300 >= 0.03) or (bshare >= 0.70) or (vacc >= 1.3) or (pc6h[i] >= 200) or (pc24[i] >= 150)
        v_att = fee >= 100 and latest60
        v_crash = (hi15 > 0 and px[i] / hi15 - 1 <= -0.25) or (r300 <= -0.20)
        v_hot = fin(turn) and turn >= V_HOT_Q80
        cx = H.calib_extra_bps_per_leg(fee, lq[i], NOTIONAL)
        rt = H.roundtrip_cost_pct(s, i, NOTIONAL)
        out = {}
        for h in HORIZONS:
            out['n0_%d' % h] = H.forward_net(s, i, h, NOTIONAL, 0.0)
            out['n50_%d' % h] = H.forward_net(s, i, h, NOTIONAL, 50.0 + cx)
        # gross mid move entry-fill -> exit-fill at 120 s (diagnostic, no costs)
        j = H.fill_index(s, i)
        g120 = NAN
        if j is not None:
            k = bisect.bisect_left(ts, ts[i] + 120_000, j + 1)
            if k < n and ts[k] - ts[k - 1] <= H.FEED_GAP_MS:
                e = H.fill_index(s, k) or k
                g120 = px[e] / px[j] - 1
        rows.append(dict(pair=pair, t=ts[i], i=i, liq=lq[i], mcap=mc[i], age=ag[i], fee=fee, cx=cx, rt=rt,
                         r1=r1, r2=r2, dt_prev=dt_prev, r60=r60, r120=r120, r300=r300,
                         hi15=px[i] / hi15 - 1 if hi15 > 0 else NAN, lo15=px[i] / lo15 - 1 if lo15 > 0 else NAN,
                         b5=b5[i], s5=s5[i], db5=db5, ds5=ds5, dv5=dv5, v5=v5[i], bshare=bshare, vacc=vacc,
                         turn=turn, pc6h=pc6h[i], pc24=pc24[i],
                         v_mom=bool(v_mom), v_att=bool(v_att), v_crash=bool(v_crash), v_hot=bool(v_hot),
                         heat=bool(v_mom or v_att or v_crash or v_hot), g120=g120, **out))
print('events', len(rows), 'secs', round(time.time() - T0, 1))

# market-wide features, global time order, past-only (events with t <= now)
rows.sort(key=lambda r: (r['t'], r['pair']))
from collections import deque
W60 = deque()
W120 = deque()


def _med(xs):
    xs = sorted(xs)
    m = len(xs)
    if m == 0:
        return NAN
    return xs[m // 2] if m % 2 else 0.5 * (xs[m // 2 - 1] + xs[m // 2])


for r in rows:
    t = r['t']
    W60.append(r)
    W120.append(r)
    while W60 and W60[0]['t'] <= t - 60_000:
        W60.popleft()
    while W120 and W120[0]['t'] <= t - 120_000:
        W120.popleft()
    # last step per pair in the window (excluding the deciding pair itself)
    last = {}
    for x in W60:
        if x['pair'] != r['pair'] and x['liq'] >= 50_000:
            last[x['pair']] = x['r1']
    r['mkt_r1_60'] = _med(list(last.values())) if len(last) >= 5 else NAN
    r['mkt_n60'] = len(last)
    last = {}
    for x in W120:
        if x['pair'] != r['pair'] and x['liq'] >= 50_000 and fin(x['r300']):
            last[x['pair']] = x['r300']
    r['mkt_r300_120'] = _med(list(last.values())) if len(last) >= 5 else NAN
    last = {}
    for x in W120:
        if x['pair'] != r['pair'] and x['liq'] >= 50_000 and fin(x['r60']):
            last[x['pair']] = x['r60']
    r['mkt_r60_120'] = _med(list(last.values())) if len(last) >= 5 else NAN
    r['split'] = 'train' if t < cut else 'holdout'
with open(os.path.join(HERE, 'events.pkl'), 'wb') as fh:
    pickle.dump({'rows': rows, 'cut': cut, 'meta': meta}, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('saved', len(rows), 'secs', round(time.time() - T0, 1))
