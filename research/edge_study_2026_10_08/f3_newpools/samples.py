"""Build a minute-sampled feature/outcome table for PumpSwap SOL pools (past-only features at i,
outcomes from i+1 onward). Saved to samples.pkl in this folder for descriptive analysis.

Outcome conventions mirror the harness: entry fills at i+1; exit value at the first point at or
after the horizon; if the pair vanished from the feed before the horizon (last point > 10 min before
the dataset end) the exit is at the last price -10% (harness VANISH haircut); 'cp' variants also
apply an uncapped constant-product exit impact (the harness caps impact at 20%)."""
import sys, time, bisect, pickle, math
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools/samples.pkl'
NOTIONAL = 100.0
t0 = time.time()
series, meta = H.load()
T1 = meta['t1']
cut = H.split_t(meta)


def cp_exit(s, k, qty, px, extra_bps):
    """Exit proceeds with uncapped constant-product impact: receive R*V/(R+V), R = liq/2."""
    liq, su = s['liq'][k], H.sol_usd(s, k)
    if not (px > 0):
        return None
    V = qty * px
    R = liq / 2 if liq > 0 else 1e-9
    got = R * V / (R + V)
    got *= (1 - (H.BASE_BPS + extra_bps) / 1e4) * (1 - H.fee_bps(s, k) / 1e4)
    return max(0.0, got - H.NETWORK_SOL * su)


def outcome(s, i, hz):
    ts = s['t']
    n = len(ts)
    if i + 1 >= n or ts[i + 1] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    f = H.entry_fill(s, i + 1, NOTIONAL, H.STRESS_BPS)
    if f is None:
        return None
    qty, nf = f
    target = ts[i] + hz * 1000
    k = bisect.bisect_left(ts, target, i + 2)
    vanished = False
    if k >= n:
        k = n - 1
        if T1 - ts[k] > 600_000:
            vanished = True
        elif T1 < target:
            return None  # dataset ended before horizon: unknown
    px = s['price'][k] * (0.9 if vanished else 1.0)
    v = H.exit_value(s, k, qty, H.STRESS_BPS, px)
    vc = cp_exit(s, k, qty, px, H.STRESS_BPS)
    # MFE over the window (net, stressed, harness impact)
    mfe = -1e9
    lmin = s['liq'][i]
    for kk in range(i + 2, k + 1):
        vv = H.exit_value(s, kk, qty, H.STRESS_BPS)
        if vv is not None:
            mfe = max(mfe, 100 * (vv - NOTIONAL - nf) / NOTIONAL)
        if s['liq'][kk] < lmin:
            lmin = s['liq'][kk]
    return (None if v is None else 100 * (v - NOTIONAL - nf) / NOTIONAL,
            None if vc is None else 100 * (vc - NOTIONAL - nf) / NOTIONAL,
            vanished, mfe if mfe > -1e8 else None, lmin / s['liq'][i] if s['liq'][i] > 0 else None)


rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    n = len(ts)
    t_first = ts[0]
    last = -1e18
    src_or = 0
    for i in range(n - 1):
        src_or |= int(s['src'][i])
        if ts[i] - last < 60_000:
            continue
        age = s['age'][i]
        if not (age == age) or age > 1440:
            continue
        last = ts[i]
        P = H.Past(s, i)
        liq, mc = s['liq'][i], s['mcap'][i]
        r = {'pair': pair, 't': ts[i], 'train': ts[i] < cut, 'age': age, 'liq': liq, 'mcap': mc,
             'lm': liq / mc if mc > 0 else float('nan'), 'fee': H.fee_bps(s, i), 'src': int(s['src'][i]), 'src_or': src_or,
             'obs_min': (ts[i] - t_first) / 60000, 'pts5': len(P.window('t', 300)),
             'liq5': liq / P.ago('liq', 300) if P.ago('liq', 300) > 0 else float('nan'),
             'liq15': liq / P.ago('liq', 900) if P.ago('liq', 900) > 0 else float('nan'),
             'p5': s['price'][i] / P.ago('price', 300) if P.ago('price', 300) > 0 else float('nan'),
             'p15': s['price'][i] / P.ago('price', 900) if P.ago('price', 900) > 0 else float('nan'),
             'pc5': s['pc5'][i], 'pc1h': s['pc1h'][i], 'b5': s['b5'][i], 's5': s['s5'][i], 'b1h': s['b1h'][i], 's1h': s['s1h'][i],
             'v5': s['v5'][i], 'v1h': s['v1h'][i], 'uw5': s['hp_uw5'][i], 'rb5': s['hp_rb5'][i],
             'vf_trades': s['vf_trades'][i], 'vf_buy': s['vf_buy'][i], 'vf_sell': s['vf_sell'][i], 'vf_wallets': s['vf_wallets'][i],
             'sf_buy': s['sf_buy'][i], 'sf_sell': s['sf_sell'][i], 'sf_wallets': s['sf_wallets'][i],
             'boost': s['boost'][i], 'score': s['score'][i], 'risk': s['risk'][i], 'conv': s['conv'][i],
             'reuse': H.other_pairs_same_ticker_before(P), 'rug': H.interim_rug_risk(P),
             'mint_pump': (s['mint'] or '').endswith('pump'),
             'still60': ts[-1] >= ts[i] + 3600_000 or T1 - ts[-1] <= 600_000}
        for hz in (300, 900, 1800, 3600):
            o = outcome(s, i, hz)
            if o is None:
                r['f%d' % hz] = None
                continue
            r['f%d' % hz], r['c%d' % hz], r['van%d' % hz], r['mfe%d' % hz], r['lmin%d' % hz] = o
        rows.append(r)
with open(OUT, 'wb') as fh:
    pickle.dump(rows, fh, protocol=pickle.HIGHEST_PROTOCOL)
print('rows', len(rows), 'secs', round(time.time() - t0, 1))
