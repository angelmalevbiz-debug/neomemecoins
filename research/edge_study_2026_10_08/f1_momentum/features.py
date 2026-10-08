"""Past-only per-point momentum features for PumpSwap/SOL pairs (F1 research). Read-only over the dataset.

Every feature at point i uses only points <= i (same semantics as harness.Past.ago / window).
Forward outcomes (fwd_*) are computed separately and are ONLY for exploratory analysis on train.
"""
import array, bisect, collections, math, os, pickle, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'feat.pkl')
NAN = float('nan')
FEATS = ['fee', 'r60', 'r180', 'r300', 'r600', 'r1800', 'hi600', 'hi1800', 'lo1800', 'vacc', 'bsh5', 'bsh1h',
         'liqg300', 'liqg1800', 'rug', 'ntick', 'v5acc_prev']


def _ago_idx(ts, i, sec):
    return bisect.bisect_right(ts, ts[i] - sec * 1000, 0, i + 1) - 1


def pair_features(s):
    ts, px, liq = s['t'], s['price'], s['liq']
    n = len(ts)
    out = {f: array.array('d', [NAN]) * n for f in FEATS}
    dq600, dq1800, dqlo = collections.deque(), collections.deque(), collections.deque()
    for i in range(n):
        p = px[i]
        out['fee'][i] = H.fee_bps(s, i)
        for sec, name in ((60, 'r60'), (180, 'r180'), (300, 'r300'), (600, 'r600'), (1800, 'r1800')):
            k = _ago_idx(ts, i, sec)
            if k >= 0 and px[k] > 0 and p > 0:
                out[name][i] = 100 * (p / px[k] - 1)
        # rolling max (exclusive of now) over the window: breakout distance
        for dq, sec, name in ((dq600, 600, 'hi600'), (dq1800, 1800, 'hi1800')):
            while dq and ts[dq[0]] <= ts[i] - sec * 1000:
                dq.popleft()
            if dq and px[dq[0]] > 0 and p > 0:
                out[name][i] = 100 * (p / px[dq[0]] - 1)   # >0 means new high vs prior window max
            while dq and px[dq[-1]] <= p:
                dq.pop()
            dq.append(i)
        while dqlo and ts[dqlo[0]] <= ts[i] - 1800 * 1000:
            dqlo.popleft()
        if dqlo and px[dqlo[0]] > 0 and p > 0:
            out['lo1800'][i] = 100 * (p / px[dqlo[0]] - 1)  # distance above 30-min low
        while dqlo and px[dqlo[-1]] >= p:
            dqlo.pop()
        dqlo.append(i)
        v5, v1h = s['v5'][i], s['v1h'][i]
        if v1h > 0 and v5 >= 0:
            out['vacc'][i] = v5 / (v1h / 12)
        b5, s5 = s['b5'][i], s['s5'][i]
        if b5 + s5 > 0:
            out['bsh5'][i] = b5 / (b5 + s5)
        b1, s1 = s['b1h'][i], s['s1h'][i]
        if b1 + s1 > 0:
            out['bsh1h'][i] = b1 / (b1 + s1)
        for sec, name in ((300, 'liqg300'), (1800, 'liqg1800')):
            k = _ago_idx(ts, i, sec)
            if k >= 0 and liq[k] > 0 and liq[i] > 0:
                out[name][i] = 100 * (liq[i] / liq[k] - 1)
        P = H.Past(s, i)
        out['rug'][i] = 1.0 if H.interim_rug_risk(P) else 0.0
        out['ntick'][i] = H.other_pairs_same_ticker_before(P)
        k = _ago_idx(ts, i, 300)
        if k >= 0 and s['v1h'][k] > 0:
            out['v5acc_prev'][i] = s['v5'][k] / (s['v1h'][k] / 12)
    return out


def build():
    series, meta = H.load()
    feats = {}
    t0 = time.time()
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        feats[pair] = pair_features(s)
    with open(CACHE, 'wb') as fh:
        pickle.dump(feats, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print('features built', len(feats), 'pairs', round(time.time() - t0, 1), 's')
    return feats


def load():
    if not os.path.exists(CACHE):
        return build()
    with open(CACHE, 'rb') as fh:
        return pickle.load(fh)


if __name__ == '__main__':
    build()
