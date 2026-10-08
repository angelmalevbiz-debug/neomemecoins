"""Audit step 4: look-ahead tests of the Past view, ticker index, simulate (poisoned-future test) and forward_net.

Usage: python t04_lookahead.py [harness_module_name]   (default: harness)
"""
import array, bisect, copy, importlib, math, random, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/audit')
H = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else 'harness')
print('module', H.__file__)
t0 = time.time()
series, meta = H.load()
rnd = random.Random(11)
pairs = [p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1 and len(s['t']) > 50]

# 1) Past.ago / window vs brute force
bad = 0
for _ in range(20000):
    s = series[rnd.choice(pairs)]
    i = rnd.randrange(len(s['t']))
    sec = rnd.choice([-30, 0, 1, 5, 15, 30, 60, 300, 3600, 1e7]) * (1 if rnd.random() < .8 else rnd.random())
    P = H.Past(s, i)
    ts = s['t']
    now = ts[i]
    ks = [k for k in range(i + 1) if ts[k] <= now - sec * 1000]
    want = s['price'][ks[-1]] if ks else float('nan')
    got = P.ago('price', sec)
    if not ((math.isnan(want) and math.isnan(got)) or want == got):
        bad += 1
    wk = [s['price'][k] for k in range(i + 1) if ts[k] > now - sec * 1000]
    if list(P.window('price', sec)) != wk:
        bad += 1
print('ago/window brute-force mismatches:', bad)

# 2) Past.static leaks the full future column
s = series[pairs[0]]
P = H.Past(s, 10)
try:
    col = P.static('price')
    leak = hasattr(col, '__len__') and len(col) > P.i + 1
    print('P.static("price") returns', type(col).__name__, 'len', len(col) if hasattr(col, '__len__') else None, 'at i=10 -> LEAK' if leak else '-> no leak')
except Exception as e:
    print('P.static("price") raised', type(e).__name__, e, '-> guarded')
try:
    print('P.s accessible directly:', hasattr(P, 's'))
except Exception as e:
    print('P.s raised', e)

# 3) ticker index: counts only pairs first seen at or before now (brute force)
bad = 0
first = {p: s2['t'][0] for p, s2 in series.items()}
by_tk = {}
for p, s2 in series.items():
    by_tk.setdefault(H.norm_ticker(s2['sym']), []).append(p)
for _ in range(5000):
    p = rnd.choice(pairs)
    s2 = series[p]
    i = rnd.randrange(len(s2['t']))
    P = H.Past(s2, i)
    want = sum(1 for q in by_tk.get(H.norm_ticker(s2['sym']), []) if q != p and first[q] <= s2['t'][i])
    if H.other_pairs_same_ticker_before(P) != want:
        bad += 1
print('ticker-index brute-force mismatches:', bad)


# 4) poisoned-future test of simulate: scramble every value after T*; decisions and closed trades before T* must not change
def make_signal(log):
    def sig(P):
        a = P('price')
        b = P.ago('price', 120)
        w = P.window('v5', 300)
        rug = H.interim_rug_risk(P)
        rt = P.rt_cost_pct(200)
        fee = P.fee_bps()
        x = (a > 0 and b > 0 and a / b > 1.01) or H.hashed_coin(P.static('pair'), P.t, 0.003)
        x = x and not rug and rt is not None and fee >= 0 and len(w) >= 0
        log.append((P.static('pair'), P.t, bool(x)))
        return x
    return sig


def exit_fn_factory(log):
    def ex(P, pos):
        log.append((P.static('pair'), P.t, round(pos['net'], 9)))
        return 'XF' if P('pc5') < -20 else None
    return ex


Tstar = meta['t0'] + 0.5 * (meta['t1'] - meta['t0'])
print("eligible pairs", len(pairs)); sub = set(rnd.sample(pairs, min(len(pairs), 200)))
kw = dict(stop=-5, tp=10, hold_min=30, cooldown_s=120, pairs=sub)
log1, xlog1 = [], []
tr1 = H.simulate(make_signal(log1), exit_fn=exit_fn_factory(xlog1), **kw)
orig = H._LOADED
pois = {}
prng = random.Random(5)
for p, s2 in orig[0].items():
    if p not in sub:
        pois[p] = s2
        continue
    c = dict(s2)
    for f in H.NUM:
        if f == 't':
            continue
        arr = array.array('d', s2[f])
        for k in range(len(arr)):
            if s2['t'][k] > Tstar:
                arr[k] = arr[k] * prng.uniform(0.2, 5.0) if arr[k] == arr[k] else arr[k]
        c[f] = arr
    pois[p] = c
H._LOADED = (pois, orig[1])
H._TICKERS = None
log2, xlog2 = [], []
tr2 = H.simulate(make_signal(log2), exit_fn=exit_fn_factory(xlog2), **kw)
H._LOADED = orig
H._TICKERS = None
d1 = [x for x in log1 if x[1] <= Tstar]
d2 = [x for x in log2 if x[1] <= Tstar]
print('poisoned-future: decision calls before T*: %d vs %d, identical=%s' % (len(d1), len(d2), d1 == d2))
x1 = [x for x in xlog1 if x[1] <= Tstar]
x2 = [x for x in xlog2 if x[1] <= Tstar]
print('poisoned-future: exit_fn calls before T*: %d vs %d, identical=%s' % (len(x1), len(x2), x1 == x2))
c1 = [(x['pair'], x['entry_t'], x['exit_t'], round(x['net0'], 9)) for x in tr1 if x['exit_t'] <= Tstar]
c2 = [(x['pair'], x['entry_t'], x['exit_t'], round(x['net0'], 9)) for x in tr2 if x['exit_t'] <= Tstar]
print('poisoned-future: trades closed before T*: %d vs %d, identical=%s' % (len(c1), len(c2), c1 == c2))

# 5) cross-pair state built inside a signal: does a signal at time t ever get called AFTER a call at a later time?
order = [x[1] for x in log1]
inversions = sum(1 for a, b in zip(order, order[1:]) if b < a)
print('signal call order: time inversions between consecutive calls = %d of %d (per-pair iteration => global state inside a signal can see other pairs\' future)' % (inversions, len(order) - 1))

# 6) structural checks on trades: entry is the point after the decision, exit after the trigger, no overlap per pair
tr = H.simulate(lambda P: H.hashed_coin(P.static('pair'), P.t, 0.01), stop=-5, tp=10, hold_min=60, cooldown_s=300)
byp = {}
for x in tr:
    byp.setdefault(x['pair'], []).append(x)
ov = cd = 0
for p, xs in byp.items():
    xs.sort(key=lambda r: r['entry_t'])
    for a, b in zip(xs, xs[1:]):
        if b['entry_t'] <= a['exit_t']:
            ov += 1
        if b['entry_t'] < a['exit_t'] + 300_000:
            cd += 1
print('random trades', len(tr), 'overlaps', ov, 'cooldown violations (entry within 300 s of previous exit)', cd)

# 7) forward_net survivorship: how often None because the pair has no point after the horizon although the dataset continued
none_vanish = none_end = ok = 0
for p in pairs:
    s2 = series[p]
    ts = s2['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000:
            continue
        last = ts[i]
        v = H.forward_net(s2, i, 3600)
        if v is None:
            if ts[i] + 3600_000 <= meta['t1'] and ts[-1] < ts[i] + 3600_000:
                none_vanish += 1
            else:
                none_end += 1
        else:
            ok += 1
print('forward_net(3600) samples: ok %d, None because pair vanished before horizon (dataset continued) %d, None near dataset end/lag %d' % (ok, none_vanish, none_end))
print('secs', round(time.time() - t0, 1))
