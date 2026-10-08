"""Coverage + latency census of the swap tape vs the harness price series (read-only)."""
import bisect, sys, time
from collections import Counter
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import tape_load as TL

t0 = time.time()
T = TL.load()
tape = T['tape']
series, meta = H.load()
print('loaded', round(time.time() - t0, 1), 's; series t0..t1', meta['t0'], meta['t1'])
cut = H.split_t(meta)


def q(xs, ps=(0.05, 0.25, 0.5, 0.75, 0.95, 0.99)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) for p in ps] if xs else []


lat = []
in_series = 0
ev_in_span = 0
ev_in_series_span = 0
rows = []
for pair, d in tape.items():
    et, av = d['et'], d['av']
    s = series.get(pair)
    n_span = sum(1 for x in et if meta['t0'] <= x <= meta['t1'])
    ev_in_span += n_span
    for a, b in zip(et, av):
        if meta['t0'] <= a <= meta['t1']:
            lat.append((b - a) / 1000)
    if s is None:
        rows.append((pair, len(et), n_span, None))
        continue
    in_series += 1
    ev_in_series_span += n_span
    rows.append((pair, len(et), n_span, s))
print('tape pairs', len(tape), 'in series', in_series, 'events in data span', ev_in_span, 'of which pairs in series', ev_in_series_span)
print('latency available-event_time seconds q05/25/50/75/95/99:', q(lat))
print('share latency <=10s', round(sum(1 for x in lat if x <= 10) / len(lat), 3), '<=30s', round(sum(1 for x in lat if x <= 30) / len(lat), 3),
      '<=60s', round(sum(1 for x in lat if x <= 60) / len(lat), 3), '<=300s', round(sum(1 for x in lat if x <= 300) / len(lat), 3))

# latency by hour of the span
byh = {}
for pair, d in tape.items():
    for a, b in zip(d['et'], d['av']):
        if meta['t0'] <= a <= meta['t1']:
            byh.setdefault(int((a - meta['t0']) // 3.6e6), []).append((b - a) / 1000)
print('hour: n events, median latency s, p90 latency')
for h in sorted(byh):
    xs = sorted(byh[h])
    print('  h%02d' % h, len(xs), round(xs[len(xs) // 2], 1), round(xs[int(.9 * len(xs))], 1))

# concurrency: how many pairs had >=1 event (by event_time) in each 5-min bucket; and by available
conc = Counter()
for pair, d in tape.items():
    seen = set(int((a - meta['t0']) // 300_000) for a in d['et'] if meta['t0'] <= a <= meta['t1'])
    for b in seen:
        conc[b] += 1
vals = [conc.get(b, 0) for b in range(int((meta['t1'] - meta['t0']) // 300_000) + 1)]
print('pairs with tape events per 5-min bucket q05/25/50/75/95:', q(vals), 'mean', round(sum(vals) / len(vals), 2))

# fee tier / liquidity profile at tape event times for pairs in series
prof = Counter()
for pair, n, n_span, s in rows:
    if s is None or n_span == 0:
        continue
    d = tape[pair]
    mid = [a for a in d['et'] if meta['t0'] <= a <= meta['t1']]
    k = bisect.bisect_right(s['t'], mid[len(mid) // 2]) - 1
    k = max(0, k)
    fee = H.fee_bps(s, k)
    liq = s['liq'][k]
    fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
    lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
    prof[(s['dex'], fb, lb)] += 1
    prof[('events', fb, lb)] += n_span
print('tape pairs (with events in span) by dex/fee/liq at their median event time:')
for k, v in sorted(prof.items(), key=lambda kv: -kv[1]):
    print('  ', k, v)

# signature completeness per pair: processed vs everything
tot = Counter()
for pair, st in T['sigcov'].items():
    for state, (n, lo, hi) in st.items():
        tot[state] += n
print('signature states', dict(tot))
comp = []
for pair, st in T['sigcov'].items():
    allc = sum(v[0] for v in st.values())
    proc = st.get('processed', (0,))[0] + st.get('non_swap', (0,))[0]
    if allc >= 50:
        comp.append(proc / allc)
print('per-pair processed+non_swap share of signatures (pairs with >=50 sigs) q05/25/50/75/95:', q([100 * c for c in comp]), 'pairs', len(comp))

# top pairs by event count
rows.sort(key=lambda r: -r[2])
print('top 25 tape pairs by events in span:')
for pair, n, n_span, s in rows[:25]:
    if s is None:
        print('  ', pair[:8], n_span, 'NOT IN SERIES', T['pairs_meta'].get(pair, {}).get('symbol'))
        continue
    d = tape[pair]
    et = [a for a in d['et'] if meta['t0'] <= a <= meta['t1']]
    k = bisect.bisect_right(s['t'], et[len(et) // 2]) - 1
    st = T['sigcov'].get(pair, {})
    allc = sum(v[0] for v in st.values())
    proc = st.get('processed', (0,))[0]
    print('  ', pair[:8], s['sym'], 'ev', n_span, 'hours %.1f-%.1f' % ((et[0] - meta['t0']) / 3.6e6, (et[-1] - meta['t0']) / 3.6e6),
          'fee', H.fee_bps(s, max(k, 0)), 'liq', round(s['liq'][max(k, 0)]), 'sigs', allc, 'proc', proc,
          'buys', sum(1 for a, sd in zip(d['et'], d['side']) if sd > 0 and meta['t0'] <= a <= meta['t1']),
          'wallets', len(set(w for a, w in zip(d['et'], d['w']) if meta['t0'] <= a <= meta['t1'])))
print('events train/holdout split by event_time:', sum(1 for d in tape.values() for a in d['et'] if meta['t0'] <= a < cut),
      sum(1 for d in tape.values() for a in d['et'] if cut <= a <= meta['t1']))
w_tr = set(); w_ho = set()
for d in tape.values():
    for a, w in zip(d['et'], d['w']):
        if meta['t0'] <= a < cut:
            w_tr.add(w)
        elif cut <= a <= meta['t1']:
            w_ho.add(w)
print('wallets train', len(w_tr), 'holdout', len(w_ho), 'both', len(w_tr & w_ho))
# wallets active across many pools
wp = {}
for pair, d in tape.items():
    for w in set(d['w']):
        wp[w] = wp.get(w, 0) + 1
print('wallet pool-count distribution:', Counter(min(v, 10) for v in wp.values()))
