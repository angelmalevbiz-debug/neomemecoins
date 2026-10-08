"""Build a 1/min/pair sample of tape-covered decision points with past-only flow features,
DexScreener aggregates, and forward net returns (labels). Saved to samples.pkl. Read-only on inputs."""
import math, pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import flow as F

t0 = time.time()
series, meta = H.load()
PT = F.tapes()
cut = H.split_t(meta)
NAN = float('nan')
rows = []
for pair, pt in PT.items():
    s = series.get(pair)
    if s is None or s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        t = ts[i]
        if t - last < 60_000:
            continue
        if not pt.covered(t, 300):
            continue
        last = t
        P = H.Past(s, i)
        r = {'pair': pair, 't': t, 'i': i, 'train': t < cut, 'fee': H.fee_bps(s, i), 'liq': s['liq'][i],
             'mcap': s['mcap'][i], 'age': s['age'][i], 'rug': H.interim_rug_risk(P),
             'pc5': s['pc5'][i], 'pc1h': s['pc1h'][i], 'b5': s['b5'][i], 's5': s['s5'][i], 'v5': s['v5'][i],
             'v1h': s['v1h'][i], 'b1h': s['b1h'][i], 's1h': s['s1h'][i], 'su': H.sol_usd(s, i),
             'rt': H.roundtrip_cost_pct(s, i, 200.0)}
        for W in (30, 60, 120, 300, 900):
            f = pt.feats(t, W)
            for k, v in f.items():
                r['%s_%d' % (k, W)] = v
        lp = pt.last_price(t, 120)
        for back in (60, 300, 900):
            pb = pt.last_price(t - back * 1000, 300)
            # NB: last_price(t - back) filters av <= t - back; that is stricter than needed but past-only
            r['tmom_%d' % back] = (lp / pb - 1) * 100 if (lp > 0 and pb > 0) else NAN
        # DexScreener price momentum from series (past-only)
        for back in (60, 300, 900):
            pa = P.ago('price', back)
            r['dmom_%d' % back] = (s['price'][i] / pa - 1) * 100 if pa > 0 else NAN
        for hz in (120, 300, 900, 1800, 3600):
            r['f0_%d' % hz] = H.forward_net(s, i, hz)
            r['f50_%d' % hz] = H.forward_net(s, i, hz, extra_bps=H.STRESS_BPS)
        # path labels: would -5/+10 (net, model) hit TP before stop within 60 min (approx via points)
        e = H.entry_fill(s, i + 1, 200.0) if i + 1 < len(ts) else None
        hit = None
        mfe = mae = NAN
        if e is not None and ts[i + 1] - t <= 60_000:
            qty, nf = e
            mfe, mae = -1e9, 1e9
            for k in range(i + 2, len(ts)):
                if ts[k] - ts[i + 1] > 3_600_000:
                    break
                v = H.exit_value(s, k, qty)
                if v is None:
                    continue
                net = 100 * (v - 200 - nf) / 200
                mfe, mae = max(mfe, net), min(mae, net)
                if hit is None and net <= -5:
                    hit = 'STOP'
                if hit is None and net >= 10:
                    hit = 'TP'
            if mfe < -1e8:
                mfe = mae = NAN
        r['hit'] = hit
        r['mfe60'] = mfe
        r['mae60'] = mae
        rows.append(r)
print('samples', len(rows), 'train', sum(r['train'] for r in rows), 'pairs', len(set(r['pair'] for r in rows)),
      round(time.time() - t0, 1), 's')
with open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/samples.pkl', 'wb') as fh:
    pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
