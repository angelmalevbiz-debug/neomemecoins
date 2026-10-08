"""Step (b) data: past-only features at decision points sampled once per minute per pair, with forward labels.

Decision points: PumpSwap, SOL quote, first point of each new 60 s slot per pair (past-only view).
Labels (looked up from labels.pkl, used only as targets):
  y2h      a terminal (not recovered) drain event (LIQ_FAST / PRICE_FAST / PRICE_SLOW) in (t, t + 2 h]
  y1h      same within 1 h
  kind     the drain kinds of that event
  van2h    the pair vanished from the feed within 2 h without an observed drain (unknown outcome)
  cens     t + 2 h is beyond the end of the dataset (label window truncated)
Points at or after the pair's drain event are dropped (the pool is already dead).
Saves rug/feat.pkl : list of dicts.
"""
import math, os, pickle, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
NAN = float('nan')


def ratio(a, b):
    return a / b if (a == a and b == b and b > 0) else NAN


def feats(P, s, first_t, peak):
    liq, mc = P('liq'), P('mcap')
    b5, s5, b1, s1, b6, s6 = P('b5'), P('s5'), P('b1h'), P('s1h'), P('b6h'), P('s6h')
    liq10 = P.ago('liq', 600)
    age = P('age')
    tx5 = (b5 if b5 == b5 else 0) + (s5 if s5 == s5 else 0)
    tx1 = (b1 if b1 == b1 else 0) + (s1 if s1 == s1 else 0)
    return {
        'liq': liq, 'mcap': mc, 'lmc': ratio(liq, mc), 'age': age, 'seen': (P.t - first_t) / 60000,
        'pump': 1 if (P.static('mint') or '').endswith('pump') else 0, 'fee': P.fee_bps(),
        'reuse': H.other_pairs_same_ticker_before(P),
        'b5': b5, 's5': s5, 'bs5': b5 / max(s5, 1) if b5 == b5 and s5 == s5 else NAN,
        'b1h': b1, 's1h': s1, 'bs1h': b1 / max(s1, 1) if b1 == b1 and s1 == s1 else NAN,
        'bs6h': b6 / max(s6, 1) if b6 == b6 and s6 == s6 else NAN,
        'tx5': tx5, 'tx1h': tx1,
        'v5': P('v5'), 'v1h': P('v1h'), 'v24': P('v24'),
        'vliq1h': ratio(P('v1h'), liq), 'usd_per_tx5': ratio(P('v5'), tx5), 'usd_per_tx1h': ratio(P('v1h'), tx1),
        'pc5': P('pc5'), 'pc1h': P('pc1h'), 'pc6h': P('pc6h'), 'pc24': P('pc24'),
        'src': int(P('src')) if P('src') == P('src') else 0, 'boost': P('boost'), 'score': P('score'), 'risk': P('risk'),
        'uw5': P('hp_uw5'), 'rb5': P('hp_rb5'), 'sfw': P('sf_wallets'), 'vfw': P('vf_wallets'),
        'liq_chg10': ratio(liq, liq10), 'liq_dd': ratio(liq, peak),
        'mc_per_agemin': ratio(mc, max(age, 1.0)) if age == age else NAN,
    }


def main():
    t0 = time.time()
    series, meta = H.load()
    lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
    t_end = meta['t1']
    out = []
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        r = lab[pair]
        terminal = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
        dt = r['drain_t'] if terminal else None
        dk = r['drain_k'] if r['drain_t'] is not None else None
        ts = s['t']
        slot = None
        peak = NAN
        for i in range(len(ts)):
            if dk is not None and i >= dk:
                break
            lv = s['liq'][i]
            if lv == lv and not (peak >= lv):
                peak = lv
            sl = int(ts[i] // 60000)
            if sl == slot:
                continue
            slot = sl
            P = H.Past(s, i)
            f = feats(P, s, ts[0], peak)
            t = ts[i]
            f.update({'pair': pair, 't': t, 'i': i,
                      'y2h': 1 if dt is not None and t < dt <= t + 7_200_000 else 0,
                      'y1h': 1 if dt is not None and t < dt <= t + 3_600_000 else 0,
                      'kind': '+'.join(r['kinds']) if dt is not None else '',
                      'mins_to_drain': (dt - t) / 60000 if dt is not None else NAN,
                      'van2h': 1 if (dt is None and r['vanished'] and ts[-1] <= t + 7_200_000) else 0,
                      'cens': 1 if t + 7_200_000 > t_end else 0,
                      'interim': 1 if H.interim_rug_risk(P) else 0})
            out.append(f)
    with open(os.path.join(HERE, 'feat.pkl'), 'wb') as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    n = len(out)
    print('points', n, 'pairs', len({x['pair'] for x in out}), 'y2h', sum(x['y2h'] for x in out),
          'drain pairs with a point in 2h window', len({x['pair'] for x in out if x['y2h']}),
          'van2h', sum(x['van2h'] for x in out), 'cens', sum(x['cens'] for x in out), 'secs', round(time.time() - t0, 1))


if __name__ == '__main__':
    main()
