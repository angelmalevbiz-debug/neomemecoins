"""F6 panel builder: one row per (1-minute tick, alive PumpSwap SOL pool) with past-only factors and
forward net returns (used ONLY on train ticks for factor selection; holdout rows are not inspected
until the final pre-selected configs are run).

Row decision point: i = last point with t <= T and T - t_i <= 60 s. Entry fill at point i+1 (skipped if
t_{i+1} - t_i > 60 s, harness rule). Forward exit for horizon h: first point after T+h
(index k+1 where k = last point <= T+h), the same rule the rotation simulator uses.
"""
import sys, time, bisect, math, pickle, os
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, 'panel.pkl')
NAN = float('nan')
HORIZONS = (300, 900, 1800, 3600)


def ok(x):
    return x == x and x is not None


def rel(a, b):
    return a / b - 1 if (ok(a) and ok(b) and b > 0) else NAN


def factors(s, i):
    P = H.Past(s, i)
    liq, mc, age = P('liq'), P('mcap'), P('age')
    price = P('price')
    v5, v1h, v6h = P('v5'), P('v1h'), P('v6h')
    b5, s5, b1h, s1h = P('b5'), P('s5'), P('b1h'), P('s1h')
    w = P.window('price', 900)
    lr = []
    for a, b in zip(w, w[1:]):
        if a > 0 and b > 0:
            lr.append(math.log(b / a))
    vol15 = math.sqrt(sum(x * x for x in lr) / len(lr)) * math.sqrt(len(lr)) if len(lr) >= 5 else NAN
    hist_s = (P.t - s['t'][0]) / 1000
    f = {
        'liq': liq, 'mcap': mc, 'age': age, 'fee': P.fee_bps(), 'rt': P.rt_cost_pct(200.0),
        'rug': H.interim_rug_risk(P),
        'hist_s': hist_s,
        'ret1': rel(price, P.ago('price', 60)) if hist_s >= 60 else NAN,
        'ret5': rel(price, P.ago('price', 300)) if hist_s >= 300 else NAN,
        'ret15': rel(price, P.ago('price', 900)) if hist_s >= 900 else NAN,
        'ret60': rel(price, P.ago('price', 3600)) if hist_s >= 3600 else NAN,
        'pc5': P('pc5'), 'pc1h': P('pc1h'), 'pc6h': P('pc6h'), 'pc24': P('pc24'),
        'v5': v5, 'v1h': v1h, 'v6h': v6h, 'v24': P('v24'),
        'vacc': (v5 * 12 / v1h) if (ok(v5) and ok(v1h) and v1h > 0) else NAN,
        'vacc1h': (v1h * 6 / v6h) if (ok(v1h) and ok(v6h) and v6h > 0) else NAN,
        'turn1h': (v1h / liq) if (ok(v1h) and ok(liq) and liq > 0) else NAN,
        'b5': b5, 's5': s5, 'b1h': b1h, 's1h': s1h,
        'bshare5': (b5 / (b5 + s5)) if (ok(b5) and ok(s5) and b5 + s5 > 0) else NAN,
        'bshare1h': (b1h / (b1h + s1h)) if (ok(b1h) and ok(s1h) and b1h + s1h > 0) else NAN,
        'liqg5': rel(liq, P.ago('liq', 300)) if hist_s >= 300 else NAN,
        'liqg15': rel(liq, P.ago('liq', 900)) if hist_s >= 900 else NAN,
        'liqg60': rel(liq, P.ago('liq', 3600)) if hist_s >= 3600 else NAN,
        'vol15': vol15,
        'uw5': P('hp_uw5'), 'rb5': P('hp_rb5'), 'vf_trades': P('vf_trades'), 'vf_buy': P('vf_buy'),
        'vf_sell': P('vf_sell'), 'vf_wallets': P('vf_wallets'), 'sf_buy': P('sf_buy'), 'sf_sell': P('sf_sell'),
        'sf_wallets': P('sf_wallets'), 'boost': P('boost'), 'score': P('score'), 'risk': P('risk'),
        'src': P('src'), 'conv': P('conv'),
    }
    return f


def forward(s, i, T, h, notional=200.0):
    """(net0, net50, flag) for entry at i+1 and exit at the first point after T+h."""
    ts = s['t']
    n = len(ts)
    j = i + 1
    if j >= n or ts[j] - ts[i] > H.MAX_ENTRY_LAG_MS:
        return None
    f0 = H.entry_fill(s, j, notional)
    f5 = H.entry_fill(s, j, notional, H.STRESS_BPS)
    if f0 is None or f5 is None:
        return None
    k = bisect.bisect_right(ts, T + h * 1000) - 1
    if k + 1 < n:
        e, px, flag = k + 1, s['price'][k + 1], ''
    else:
        e = n - 1
        if T + h * 1000 > ts[-1] and H.load()[1]['t1'] - ts[e] > 600_000:
            px, flag = s['price'][e] * (1 - H.VANISH_HAIRCUT_PCT / 100), 'VANISHED'
        else:
            # dataset ends before the horizon: no forward value
            if T + h * 1000 > H.load()[1]['t1']:
                return None
            px, flag = s['price'][e], 'END'
    v0 = H.exit_value(s, e, f0[0], 0.0, px)
    v5 = H.exit_value(s, e, f5[0], H.STRESS_BPS, px)
    return (100 * ((v0 or 0) - notional - f0[1]) / notional, 100 * ((v5 or 0) - notional - f0[1]) / notional, flag)


def build(step_ms=60_000):
    series, meta = H.load()
    ps = [s for s in series.values() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1]
    t0, t1 = int(meta['t0']), int(meta['t1'])
    ticks = list(range(t0 + 60_000, t1, step_ms))
    rows = []
    for T in ticks:
        for s in ps:
            ts = s['t']
            if ts[0] > T or ts[-1] < T - 60_000:
                continue
            i = bisect.bisect_right(ts, T) - 1
            if i < 0 or T - ts[i] > 60_000:
                continue
            r = factors(s, i)
            r['pair'] = s['pair']
            r['T'] = T
            r['i'] = i
            fw = {}
            for h in HORIZONS:
                fw[h] = forward(s, i, T, h)
            r['fw'] = fw
            rows.append(r)
    return {'ticks': ticks, 'rows': rows, 'cut': H.split_t(meta), 'meta': meta}


def load_panel():
    if not os.path.exists(PANEL):
        t = time.time()
        d = build()
        with open(PANEL, 'wb') as fh:
            pickle.dump(d, fh, protocol=pickle.HIGHEST_PROTOCOL)
        print('panel built', len(d['rows']), 'rows', round(time.time() - t, 1), 's')
    with open(PANEL, 'rb') as fh:
        return pickle.load(fh)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    d = load_panel()
    rows = d['rows']
    cut = d['cut']
    print('rows', len(rows), 'train rows', sum(1 for r in rows if r['T'] < cut))
    # coverage of factors (NOT returns)
    keys = [k for k in rows[0] if k not in ('pair', 'T', 'i', 'fw', 'rug')]
    for k in keys:
        n_ok = sum(1 for r in rows if ok(r[k]))
        print('%-10s coverage %.3f' % (k, n_ok / len(rows)))
    # data coverage by 30 min
    meta = d['meta']
    by = {}
    for r in rows:
        b = int((r['T'] - meta['t0']) // 1_800_000)
        by[b] = by.get(b, 0) + 1
    print('rows per 30min bucket', [by.get(b, 0) for b in range(0, 46)])
    print('cut hour', round((cut - meta['t0']) / 3.6e6, 2))
