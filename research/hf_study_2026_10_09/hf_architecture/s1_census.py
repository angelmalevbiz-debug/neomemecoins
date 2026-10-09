"""S1: HF universe census and DexScreener refresh cadence (PAPER research, read-only).

For each PumpSwap/SOL point: structural rug screen (rug_guard_v1 + interim screen), liquidity >= $50k,
modeled round-trip cost at $50 <= cap (1.5% = 0.5 x 3% stop; 2.5% = 0.5 x 5% stop), approximate heat veto.
Reports eligible pools per minute (concurrency), distinct pools per hour, refresh lags (decision -> next
price change, harness F1) and writes per-pair eligibility to a pickle for s2.
"""
import json
import os
import pickle
import statistics
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcommon as C

OUT = os.path.join(C.HERE, 'out')
os.makedirs(OUT, exist_ok=True)
NOTIONAL = 50.0
CAPS = (1.5, 2.5)


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[k]


def main():
    t0 = time.time()
    H = C.load_harness()
    series, meta = H.load()
    cut = H.split_t(meta)
    print('loaded', meta, round(time.time() - t0, 1), 's', flush=True)
    keep = {}
    per_min = {('A', 0): {}, ('A', 1): {}, ('B', 0): {}, ('B', 1): {}}  # (tier, heat_enforced) -> minute -> set
    lag_next, lag_status = [], {'next_refresh': 0, 'quiet': 0, 'none': 0}
    change_iv = []
    n_pairs = n_points = 0
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        n_pairs += 1
        n_points += len(s['t'])
        rug_ok, cost, elig = C.universe_flags(s, H, NOTIONAL, CAPS)
        if not any(elig[CAPS[-1]]):
            continue
        heat = C.heat_flags(s, H)
        keep[pair] = {'rug_ok': rug_ok, 'cost': cost, 'A': elig[1.5], 'B': elig[2.5], 'heat': heat}
        ts, pr = s['t'], s['price']
        last_change = None
        for i in range(len(ts)):
            if i and pr[i] != pr[i - 1]:
                if last_change is not None:
                    change_iv.append((ts[i] - last_change) / 1000)
                last_change = ts[i]
            for tier, arr in (('A', elig[1.5]), ('B', elig[2.5])):
                if arr[i]:
                    m = int(ts[i] // 60_000)
                    per_min[(tier, 0)].setdefault(m, set()).add(pair)
                    if not heat[i]:
                        per_min[(tier, 1)].setdefault(m, set()).add(pair)
            if elig[2.5][i] and not heat[i]:
                j = H.fill_index(s, i)
                if j is None:
                    lag_status['none'] += 1
                else:
                    lag_next.append((ts[j] - ts[i]) / 1000)
                    lag_status['next_refresh' if pr[j] != pr[i] else 'quiet'] += 1
    print('pairs pumpswap/SOL', n_pairs, 'points', n_points, 'kept pairs', len(keep), round(time.time() - t0, 1), 's',
          flush=True)
    t_min0, t_min1 = int(meta['t0'] // 60_000), int(meta['t1'] // 60_000)
    res = {'meta': meta, 'cut': cut, 'notional': NOTIONAL, 'caps': CAPS, 'pumpswap_sol_pairs': n_pairs,
           'pumpswap_sol_points': n_points, 'pairs_ever_eligible_cap2.5': len(keep)}
    for (tier, heat_on), mins in per_min.items():
        for part, lo, hi in (('train', t_min0, int(cut // 60_000)), ('holdout', int(cut // 60_000), t_min1 + 1)):
            counts = [len(mins.get(m, ())) for m in range(lo, hi)]
            hours = {}
            for m in range(lo, hi):
                hours.setdefault(m // 60, set()).update(mins.get(m, ()))
            hc = [len(v) for v in hours.values()]
            allp = set()
            for m in range(lo, hi):
                allp |= mins.get(m, set())
            res['%s_heat%s_%s' % (tier, heat_on, part)] = {
                'pools_per_minute_mean': round(sum(counts) / max(1, len(counts)), 2),
                'pools_per_minute_p10_p50_p90': [pct(counts, .1), pct(counts, .5), pct(counts, .9)],
                'minutes_with_zero': sum(1 for c in counts if c == 0), 'minutes': len(counts),
                'distinct_pools_per_hour_mean': round(sum(hc) / max(1, len(hc)), 1),
                'distinct_pools_total': len(allp)}
    res['fill_lag_s_p10_p50_p90_mean'] = [pct(lag_next, .1), pct(lag_next, .5), pct(lag_next, .9),
                                          round(sum(lag_next) / max(1, len(lag_next)), 1)]
    res['fill_status_counts'] = lag_status
    res['price_change_interval_s_p10_p50_p90'] = [pct(change_iv, .1), pct(change_iv, .5), pct(change_iv, .9)]
    print(json.dumps(res, indent=1, default=str), flush=True)
    with open(os.path.join(OUT, 's1_census.json'), 'w') as fh:
        json.dump(res, fh, indent=1, default=str)
    with open(os.path.join(OUT, 's1_elig.pkl'), 'wb') as fh:
        pickle.dump(keep, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print('done', round(time.time() - t0, 1), 's', flush=True)


if __name__ == '__main__':
    main()
