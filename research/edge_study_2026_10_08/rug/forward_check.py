"""Genuine forward check of the FROZEN rug_guard_v1 on observations logged AFTER the dataset cutoff.

Read-only: streams the engine's append-only observations.jsonl from the dataset's cutoff byte
(13,206,393,390, from build.out) to the end of file at start time. Nothing is written outside rug/.
For each PumpSwap SOL-quote pair seen after the cutoff: guard status (liq, mcap|fdv, age only),
drain events (same definitions as labels.py; trailing windows seeded with the last 10 min before the cutoff).
"""
import collections, json, os, sys, time
from collections import deque
from rugcommon import _series, HERE
import rug_guard as G

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
SRC = 'C:/Users/Chavd/neomemecoins/.runtime/accounts/training/observations.jsonl'
START = 13_206_393_390
SOL = 'So11111111111111111111111111111111111111112'
t0 = time.time()
end = os.path.getsize(SRC)
end = min(end, START + 1_500_000_000)
fw = collections.defaultdict(lambda: {'t': [], 'liq': [], 'price': [], 'mcap': [], 'age': [], 'sym': '', 'mint': ''})
n = 0
with open(SRC, 'rb') as f:
    f.seek(START)
    f.readline()
    while f.tell() < end:
        line = f.readline()
        if not line:
            break
        if b'"dexId":"pumpswap"' not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        c = r.get('coin') or {}
        if c.get('quoteTokenAddress') != SOL:
            continue
        p = c.get('pairAddress')
        t = r.get('observed_at')
        if not p or not t:
            continue
        d = fw[p]
        if d['t'] and t <= d['t'][-1]:
            continue
        def num(x):
            try:
                v = float(x)
                return v if v == v else float('nan')
            except (TypeError, ValueError):
                return float('nan')
        mc = num(c.get('marketCap'))
        if not (mc > 0):
            mc = num(c.get('fdv'))
        d['t'].append(t); d['liq'].append(num(c.get('liquidityUsd'))); d['price'].append(num(c.get('priceUsd')))
        d['mcap'].append(mc); d['age'].append(num(c.get('ageMinutes'))); d['sym'] = c.get('symbol') or ''
        d['mint'] = c.get('address') or ''
        n += 1
t_fw0 = min(d['t'][0] for d in fw.values())
t_fw1 = max(d['t'][-1] for d in fw.values())
print('forward rows', n, 'pairs', len(fw), 'window %.1f min' % ((t_fw1 - t_fw0) / 60000), 'read secs', round(time.time() - t0, 1))


class FP:
    """Minimal past view over one forward point (the guard reads liq, mcap, age only)."""
    def __init__(self, d, i):
        self.d, self.i = d, i

    def __call__(self, f, back=0):
        return self.d[f][self.i - back]


def first_event(ts, xs, seed_t, seed_x, window=600_000):
    dq = deque()
    allt, allx = list(seed_t) + list(ts), list(seed_x) + list(xs)
    off = len(seed_t)
    for k in range(len(allt)):
        v = allx[k]
        while dq and allt[dq[0]] <= allt[k] - window:
            dq.popleft()
        if v == v and dq and k >= off and v <= 0.2 * allx[dq[0]]:
            return k - off
        if v == v and v > 0:
            while dq and allx[dq[-1]] <= v:
                dq.pop()
            dq.append(k)
    return None


rows = []
for p, d in fw.items():
    s = _series.get(p)
    seed_t, seed_l, seed_p = [], [], []
    if s is not None:
        for k in range(len(s['t'])):
            if s['t'][k] >= s['t'][-1] - 600_000:
                seed_t.append(s['t'][k]); seed_l.append(s['liq'][k]); seed_p.append(s['price'][k])
    kl = first_event(d['t'], d['liq'], seed_t, seed_l)
    kp = first_event(d['t'], d['price'], seed_t, seed_p)
    ks = [k for k in (kl, kp) if k is not None]
    kd = min(ks) if ks else None
    pre = (kd - 1) if kd else (0 if kd is None else None)
    upto = kd if kd is not None else len(d['t'])
    flags = [G.rug_guard_v1(FP(d, i)) for i in range(upto)]
    reasons0 = G.rug_reasons(FP(d, 0))
    last_reasons = G.rug_reasons(FP(d, max(0, upto - 1))) if upto else ['n/a']
    rows.append({'pair': p, 'sym': d['sym'][:10], 'in_dataset': s is not None, 'drain': kd is not None,
                 'kind': ('LIQ' if kl is not None and kl == kd else '') + ('PRICE' if kp is not None and kp == kd else ''),
                 'n_before': upto, 'flag_share': (sum(flags) / len(flags)) if flags else float('nan'),
                 'flag_last': flags[-1] if flags else None, 'reasons_first': reasons0, 'reasons_last': last_reasons,
                 'liq0': d['liq'][0], 'liq_last': d['liq'][-1], 'minutes': (d['t'][-1] - d['t'][0]) / 60000,
                 'vanished': t_fw1 - d['t'][-1] > 600_000})
dr = [r for r in rows if r['drain'] and r['n_before'] > 0]
print('== forward drains (fast LIQ/PRICE <= 20%% of 10-min peak) with >= 1 point before: %d' % len(dr))
for r in sorted(dr, key=lambda r: -r['liq0']):
    print('  %-10s %s %-8s pts_before %4d flagged_share %.2f flagged_at_last %s liq0 %.0f | last reasons %s | in_dataset %s' % (
        r['sym'], r['pair'][:8], r['kind'], r['n_before'], r['flag_share'], r['flag_last'], r['liq0'], r['reasons_last'], r['in_dataset']))
big = [r for r in dr if r['liq0'] >= 20_000]
print('forward drains with liq0 >= $20k: %d, flagged at last pre-drain point: %d' % (len(big), sum(1 for r in big if r['flag_last'])))
nd = [r for r in rows if not r['drain'] and r['liq0'] >= 50_000 and r['minutes'] >= 10]
print('never-drained forward pairs (liq0 >= $50k, >= 10 min seen): %d, mean share of points flagged %.3f, flagged-ever %d' % (
    len(nd), sum(r['flag_share'] for r in nd) / max(1, len(nd)), sum(1 for r in nd if r['flag_share'] > 0)))
print('== dataset-end survivors of the families, seen after the cutoff:')
watch = {'USDP', 'USDF', 'UDR', 'D O T F', 'SARP', 'GOIF', 'CAPYBARA', 'PQC', 'US', 'WOSE', 'IOF', 'XRPN', 'ATFS'}
for r in rows:
    if r['sym'] in watch and r['in_dataset']:
        print('  %-10s %s drained_after_cutoff %s kind %s minutes_seen %.0f liq_first %.0f liq_last %.0f vanished %s reasons_first %s' % (
            r['sym'], r['pair'][:8], r['drain'], r['kind'], r['minutes'], r['liq0'], r['liq_last'], r['vanished'], r['reasons_first']))
print('secs', round(time.time() - t0, 1))
