"""F5 event-study sample builder (past-only features, forward outcomes). Writes samples.pkl in this folder.

One sample per pair every >= 30 s (PumpSwap, SOL quote). Features use only points <= i (via H.Past);
outcomes use H.forward_net (entry at i+1, the harness latency rule) and the price path for MFE/MAE.
"""
import sys, time, math, pickle, os, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
t0 = time.time()
series, meta = H.load()
cut = H.split_t(meta)
NAN = float('nan')


def fin(x):
    return x == x and x not in (float('inf'), float('-inf'))


def ratio(a, b):
    return a / b if (fin(a) and fin(b) and b > 0) else NAN


def features(P):
    v5, v1h, v6h = P('v5'), P('v1h'), P('v6h')
    b5, s5, b1h, s1h = P('b5'), P('s5'), P('b1h'), P('s1h')
    f = {}
    f['va1h'] = ratio(v5, v1h / 12 if fin(v1h) else NAN)
    f['va6h'] = ratio(v5, v6h / 72 if fin(v6h) else NAN)
    f['va1h6h'] = ratio(v1h, v6h / 6 if fin(v6h) else NAN)
    f['ba1h'] = ratio(b5, b1h / 12 if fin(b1h) else NAN)
    f['bshare5'] = ratio(b5, (b5 + s5) if fin(b5) and fin(s5) else NAN)
    f['bshare1h'] = ratio(b1h, (b1h + s1h) if fin(b1h) and fin(s1h) else NAN)
    f['tx5'] = (b5 + s5) if fin(b5) and fin(s5) else NAN
    f['avg_tx_usd'] = ratio(v5, f['tx5'])
    # changes of the rolling 5m windows over the last 60/120 s (true short-term acceleration)
    v5_1 = P.ago('v5', 60)
    v5_2 = P.ago('v5', 120)
    b5_1 = P.ago('b5', 60)
    b5_2 = P.ago('b5', 120)
    f['dv5_60'] = ratio(v5, v5_1)
    f['dv5_120'] = ratio(v5, v5_2)
    f['db5_60'] = (b5 - b5_1) if fin(b5) and fin(b5_1) else NAN
    f['db5_120'] = (b5 - b5_2) if fin(b5) and fin(b5_2) else NAN
    f['uw5'] = P('hp_uw5')
    f['rb5'] = P('hp_rb5')
    f['uw_per_tx'] = ratio(P('hp_uw5'), f['tx5'])
    f['sf_w'] = P('sf_wallets')
    f['sf_bshare'] = ratio(P('sf_buy'), (P('sf_buy') + P('sf_sell')) if fin(P('sf_buy')) and fin(P('sf_sell')) else NAN)
    f['vf_w'] = P('vf_wallets')
    f['vf_tr'] = P('vf_trades')
    f['vf_bshare'] = ratio(P('vf_buy'), (P('vf_buy') + P('vf_sell')) if fin(P('vf_buy')) and fin(P('vf_sell')) else NAN)
    f['vf_age'] = P('vf_age_ms')
    f['pc5'] = P('pc5')
    f['pc1h'] = P('pc1h')
    f['pc6h'] = P('pc6h')
    p0 = P('price')
    p60 = P.ago('price', 60)
    p300 = P.ago('price', 300)
    p900 = P.ago('price', 900)
    f['r60'] = 100 * (p0 / p60 - 1) if fin(p60) and p60 > 0 else NAN
    f['r300'] = 100 * (p0 / p300 - 1) if fin(p300) and p300 > 0 else NAN
    f['r900'] = 100 * (p0 / p900 - 1) if fin(p900) and p900 > 0 else NAN
    l300 = P.ago('liq', 300)
    f['dliq300'] = 100 * (P('liq') / l300 - 1) if fin(l300) and l300 > 0 else NAN
    f['age'] = P('age')
    f['score'] = P('score')
    f['risk'] = P('risk')
    f['conv'] = P('conv')
    f['boost'] = P('boost')
    f['src'] = P('src')
    f['hist_s'] = (P.t - P.static('t')[0]) / 1000
    return f


def fwd(s, i, seconds, extra_bps=0.0, notional=200.0):
    """H.forward_net, but a pair that vanished before the horizon (dataset continued) closes at its
    last price minus the harness VANISH haircut instead of being dropped (no survivorship bias)."""
    v = H.forward_net(s, i, seconds, notional, extra_bps)
    if v is not None:
        return v
    ts = s['t']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    if ts[i] + seconds * 1000 > meta['t1']:
        return None  # dataset ended: genuinely unknown
    if meta['t1'] - ts[-1] <= 600_000:
        return None
    f = H.entry_fill(s, i + 1, notional, extra_bps)
    if f is None:
        return None
    px = s['price'][n - 1] * (1 - H.VANISH_HAIRCUT_PCT / 100)
    val = H.exit_value(s, n - 1, f[0], extra_bps, px)
    return None if val is None else 100 * (val - notional - f[1]) / notional


rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts, pr = s['t'], s['price']
    n = len(ts)
    last = -1e18
    for i in range(n - 1):
        if ts[i] - last < 30_000:
            continue
        last = ts[i]
        P = H.Past(s, i)
        liq = s['liq'][i]
        if not (liq > 0):
            continue
        fee = H.fee_bps(s, i)
        r = {'pair': pair, 't': ts[i], 'train': ts[i] < cut, 'fee': fee, 'liq': liq, 'mcap': s['mcap'][i],
             'rug': H.interim_rug_risk(P), 'rt': H.roundtrip_cost_pct(s, i, 200.0)}
        r.update(features(P))
        for hz in (300, 900, 1800, 3600):
            r['f%d' % hz] = fwd(s, i, hz, extra_bps=50)
        r['f1800_0'] = fwd(s, i, 1800)
        r['f3600_0'] = fwd(s, i, 3600)
        # price-path excursions over the next 30 min from the entry point (i+1)
        j = i + 1
        k = bisect.bisect_right(ts, ts[i] + 1800_000, j)
        if j < n and pr[j] > 0:
            seg = pr[j:k] if k > j else pr[j:j + 1]
            r['mfe30'] = 100 * (max(seg) / pr[j] - 1)
            r['mae30'] = 100 * (min(seg) / pr[j] - 1)
            r['cover30'] = (ts[k - 1] - ts[i]) / 1000 if k > j else 0
        else:
            r['mfe30'] = r['mae30'] = NAN
            r['cover30'] = 0
        r['vanish'] = (meta['t1'] - ts[-1]) > 600_000
        rows.append(r)
print('samples', len(rows), 'secs', round(time.time() - t0, 1))
with open(os.path.join(HERE, 'samples.pkl'), 'wb') as fh:
    pickle.dump({'rows': rows, 'cut': cut, 'meta': meta}, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('saved', round(time.time() - t0, 1))
