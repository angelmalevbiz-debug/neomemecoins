"""Exploration 1 (TRAIN ONLY): dip candidates with causal features and forward first-hit indices.

Candidate = PumpSwap SOL pair, liq >= 50k, and price at least 6% below its rolling 5/15/30/60-min
high. Sampled at most once per pair per 30 s.  Only decision points with t < train cut are kept,
so holdout outcomes are never computed here.
"""
import math, pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
import harness as H
import feat as F

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/cands_train.pkl'
TPS = (2, 3, 5, 8, 10, 15, 20, 30)
STOPS = (-3, -5, -8, -12, -20, -30)
HOLDS = (2, 5, 10, 15, 30, 60)
NOTIONAL = 200.0

t0 = time.time()
series, meta = H.load()
cut = H.split_t(meta)
cands = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts, ps, n = s['t'], s['price'], len(s['t'])
    if n < 20:
        continue
    fs = F.compute_series(s)
    last_rec = -1e18
    for i in range(n - 1):
        ti = ts[i]
        if ti >= cut:
            break
        if ti - last_rec < 30_000:
            continue
        liq = s['liq'][i]
        if not (liq >= 50_000):
            continue
        f = fs[i]
        dmin = min((f['dd300'], f['dd900'], f['dd1800'], f['dd3600']), key=lambda v: v if v == v else 9)
        if not (dmin <= -0.06):
            continue
        j = i + 1
        if ts[j] - ti > H.MAX_ENTRY_LAG_MS:
            continue
        fill = H.entry_fill(s, j, NOTIONAL)
        fill50 = H.entry_fill(s, j, NOTIONAL, H.STRESS_BPS)
        if fill is None or fill50 is None:
            continue
        last_rec = ti
        P = H.Past(s, i)
        qty, netfee = fill
        # forward walk (outcomes) - first-hit indices on modeled net0
        tp_k = {x: None for x in TPS}
        st_k = {x: None for x in STOPS}
        hd_k = {x: None for x in HOLDS}
        mfe, mae = -1e9, 1e9
        for k in range(j + 1, n):
            dt = ts[k] - ts[j]
            v = H.exit_value(s, k, qty)
            if v is not None:
                net = 100 * (v - NOTIONAL - netfee) / NOTIONAL
                mfe, mae = max(mfe, net), min(mae, net)
                for x in TPS:
                    if tp_k[x] is None and net >= x:
                        tp_k[x] = k
                for x in STOPS:
                    if st_k[x] is None and net <= x:
                        st_k[x] = k
            for x in HOLDS:
                if hd_k[x] is None and dt >= x * 60_000:
                    hd_k[x] = k
            if dt >= 61 * 60_000:
                break
        liq15 = P.ago('liq', 900)
        p15 = P.ago('price', 900)
        lp_ratio = (liq / liq15) / math.sqrt(ps[i] / p15) if (liq15 > 0 and p15 > 0 and ps[i] > 0) else float('nan')
        liq5 = P.ago('liq', 300)
        p5 = P.ago('price', 300)
        lp_ratio5 = (liq / liq5) / math.sqrt(ps[i] / p5) if (liq5 > 0 and p5 > 0 and ps[i] > 0) else float('nan')
        rec = {'pair': pair, 'sym': s['sym'], 'i': i, 'j': j, 't': ti, 'n': n, 'qty': qty, 'qty50': fill50[0], 'netfee': netfee,
               'liq': liq, 'fee': H.fee_bps(s, i), 'mcap': s['mcap'][i], 'age': s['age'][i],
               'rug': H.interim_rug_risk(P), 'rt': H.roundtrip_cost_pct(s, i, NOTIONAL),
               'lp15': lp_ratio, 'lp5': lp_ratio5,
               'pc5': s['pc5'][i], 'pc1h': s['pc1h'][i], 'pc6h': s['pc6h'][i], 'pc24': s['pc24'][i],
               'b5': s['b5'][i], 's5': s['s5'][i], 'b5_60': P.ago('b5', 60), 's5_60': P.ago('s5', 60),
               'b5_120': P.ago('b5', 120), 's5_120': P.ago('s5', 120),
               'b1h': s['b1h'][i], 's1h': s['s1h'][i], 'v5': s['v5'][i], 'v1h': s['v1h'][i],
               'vf_buy': s['vf_buy'][i], 'vf_sell': s['vf_sell'][i], 'vf_wallets': s['vf_wallets'][i], 'vf_age_ms': s['vf_age_ms'][i],
               'sf_buy': s['sf_buy'][i], 'sf_sell': s['sf_sell'][i], 'hp_uw5': s['hp_uw5'][i], 'hp_rb5': s['hp_rb5'][i],
               'score': s['score'][i], 'risk': s['risk'][i], 'src': s['src'][i], 'boost': s['boost'][i],
               'p_ret60': (ps[i] / P.ago('price', 60) - 1) if P.ago('price', 60) > 0 else float('nan'),
               'p_ret30': (ps[i] / P.ago('price', 30) - 1) if P.ago('price', 30) > 0 else float('nan'),
               'pre_trend': (P.ago('price', 900) / P.ago('price', 3600) - 1) if P.ago('price', 3600) > 0 else float('nan'),
               'tp_k': tp_k, 'st_k': st_k, 'hd_k': hd_k, 'mfe': mfe, 'mae': mae}
        rec.update(f)
        cands.append(rec)
print('candidates', len(cands), 'secs', round(time.time() - t0, 1))
with open(OUT, 'wb') as fh:
    pickle.dump(cands, fh, protocol=pickle.HIGHEST_PROTOCOL)
