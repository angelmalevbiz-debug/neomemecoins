"""p02: feed coverage (minutes with any scan point) and DexScreener refresh cadence in the guarded universes."""
import os, pickle, sys, time, collections, datetime
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfc_common as C

H = C.harness()
series, meta = H.load()
E = pickle.load(open(os.path.join(C.HERE, 'elig.pkl'), 'rb'))
CUT = E['cut']
mins = set()
for pair, s in series.items():
    for t in s['t']:
        mins.add(int(t // 60_000))
m0, m1 = int(meta['t0'] // 60_000), int(meta['t1'] // 60_000)
mc = int(CUT // 60_000)
print('dataset UTC', datetime.datetime.utcfromtimestamp(meta['t0'] / 1000), '->', datetime.datetime.utcfromtimestamp(meta['t1'] / 1000),
      'cut', datetime.datetime.utcfromtimestamp(CUT / 1000))
print('train minutes', mc - m0, 'covered', sum(1 for m in range(m0, mc) if m in mins))
print('holdout minutes', m1 - mc + 1, 'covered', sum(1 for m in range(mc, m1 + 1) if m in mins))
# list gaps > 5 min
gaps = []
prev = None
for m in range(m0, m1 + 1):
    if m in mins:
        if prev is not None and m - prev > 5:
            gaps.append((prev, m))
        prev = m
for a, b in gaps:
    print('feed gap', datetime.datetime.utcfromtimestamp(a * 60), '->', datetime.datetime.utcfromtimestamp(b * 60), b - a, 'min')

# refresh cadence: seconds between price changes for guarded + heat-free universe points
G1, GE, HEAT, CHG = E['bitnames']['G1'], E['bitnames']['GE'], E['bitnames']['HEAT'], E['bitnames']['CHG']
pairs = E['pairs']
for uname, uf in (('a', lambda f, l: f <= 50 and l >= 250_000), ('b', lambda f, l: f <= 95 and l >= 100_000),
                  ('c', lambda f, l: l >= 50_000)):
    ivs = []
    pts_gap = []
    for k in range(len(E['t'])):
        if not uf(E['fee'][k], E['liq'][k]) or E['bits'][k] & (G1 | GE):
            continue
        s = series[pairs[E['pid'][k]]]
        i = E['i'][k]
        if i == 0:
            continue
        pts_gap.append(s['t'][i] - s['t'][i - 1])
        if E['bits'][k] & CHG:
            # time since the previous change in this pair
            j = i - 1
            while j > 0 and s['price'][j] == s['price'][j - 1]:
                j -= 1
            ivs.append((s['t'][i] - s['t'][j]) / 1000)
    ivs.sort(); pts_gap.sort()
    q = lambda xs, f: xs[int(f * (len(xs) - 1))] if xs else None
    print('univ', uname, 'refresh interval s p10/p50/p90', q(ivs, .1), q(ivs, .5), q(ivs, .9), 'n', len(ivs),
          '| point spacing s p50/p90', q(pts_gap, .5) / 1000, q(pts_gap, .9) / 1000)
