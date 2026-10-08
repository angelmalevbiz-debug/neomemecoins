"""Step (a): label every drain event in the dataset (read-only over the harness cache).

Definitions (fixed before looking at any outcome):
  LIQ_FAST    liquidity <= 20% of its trailing 10-minute peak (a >= 80% drop within <= 10 min)
  PRICE_FAST  price     <= 20% of its trailing 10-minute peak
  PRICE_SLOW  price     <= 20% of its running peak since first observation (any horizon)
A pair's DRAIN event = first point where LIQ_FAST or PRICE_FAST or PRICE_SLOW holds.
'recovered' = liquidity (for LIQ_FAST) or price comes back above 50% of the pre-event peak within
the next 30 min (data glitch / relaunch, not a terminal drain).
Results are cached in rug/labels.pkl for the later steps.
"""
import collections, os, pickle, sys, time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import harness as H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
W = 600_000  # 10 minutes


def trailing_max_events(ts, xs, window_ms, frac):
    """First index k where xs[k] <= frac * max(xs over (t_k - window, t_k]) (NaN/<=0 peaks ignored)."""
    dq = deque()  # indices with decreasing values
    for k in range(len(ts)):
        v = xs[k]
        while dq and ts[dq[0]] <= ts[k] - window_ms:
            dq.popleft()
        if v == v and v > 0:
            peak = xs[dq[0]] if dq else None
            if peak is not None and v <= frac * peak:
                return k, peak
            while dq and xs[dq[-1]] <= v:
                dq.pop()
            dq.append(k)
        elif v == v and v <= 0 and dq:
            # zero / negative reported value counts as a total drop
            return k, xs[dq[0]]
    return None, None


def running_max_event(xs, frac):
    peak = None
    for k, v in enumerate(xs):
        if not (v == v and v > 0):
            continue
        if peak is not None and v <= frac * peak:
            return k, peak
        peak = v if peak is None else max(peak, v)
    return None, None


def recovered(ts, xs, k, peak, horizon_ms=1_800_000, frac=0.5):
    for m in range(k + 1, len(ts)):
        if ts[m] - ts[k] > horizon_ms:
            break
        if xs[m] == xs[m] and xs[m] >= frac * peak:
            return True
    return False


def main():
    t0 = time.time()
    series, meta = H.load()
    print('loaded', round(time.time() - t0, 1), 's', meta)
    t_end = meta['t1']
    rows = []
    for pair, s in series.items():
        ts, n = s['t'], len(s['t'])
        if n < 2:
            continue
        ev = {}
        k, pk = trailing_max_events(ts, s['liq'], W, 0.2)
        if k is not None:
            ev['LIQ_FAST'] = (k, pk, recovered(ts, s['liq'], k, pk))
        k, pk = trailing_max_events(ts, s['price'], W, 0.2)
        if k is not None:
            ev['PRICE_FAST'] = (k, pk, recovered(ts, s['price'], k, pk))
        k, pk = running_max_event(s['price'], 0.2)
        if k is not None:
            ev['PRICE_SLOW'] = (k, pk, recovered(ts, s['price'], k, pk))
        first = min((v[0] for v in ev.values()), default=None)
        kinds = sorted(name for name, v in ev.items() if v[0] == first) if first is not None else []
        rows.append({
            'pair': pair, 'sym': s['sym'], 'mint_pump': (s['mint'] or '').endswith('pump'), 'dex': s['dex'],
            'quote_sol': s['quote_sol'], 'n': n, 't_first': ts[0], 't_last': ts[-1],
            'vanished': t_end - ts[-1] > 600_000, 'events': {name: (ts[v[0]], v[1], v[2]) for name, v in ev.items()},
            'drain_k': first, 'drain_t': ts[first] if first is not None else None, 'kinds': kinds,
            'liq_first': s['liq'][0], 'mcap_first': s['mcap'][0], 'age_first': s['age'][0],
            'liq_peak': max((x for x in s['liq'] if x == x), default=float('nan')),
        })
    with open(os.path.join(HERE, 'labels.pkl'), 'wb') as fh:
        pickle.dump({'meta': meta, 'rows': rows}, fh)
    c = collections.Counter()
    for r in rows:
        key = (r['dex'], r['quote_sol'])
        c[key + ('pairs',)] += 1
        if r['drain_k'] is not None:
            c[key + ('drain',)] += 1
            for kd in r['kinds']:
                c[key + (kd,)] += 1
            rec = all(r['events'][kd][2] for kd in r['kinds'])
            c[key + ('drain_recovered' if rec else 'drain_terminal',)] += 1
        if r['vanished']:
            c[key + ('vanished',)] += 1
    for k in sorted(c):
        print(k, c[k])
    print('secs', round(time.time() - t0, 1))


if __name__ == '__main__':
    main()
