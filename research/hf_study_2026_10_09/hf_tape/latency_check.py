"""latency_check: does the pre-chosen C1 buy-burst signal carry a GROSS timing edge on chain, and how much does
latency eat? Within-pool difference of the gross chain-mid return (no costs) over 60 s / 180 s between C1-signal
decision points and all other decision points of the same pool (C1 universe: guarded, fee <= 95, liq >= $50k).
Entry clocks: (Z) 1 s after the block time of the newest swap in the window (zero-ingestion-latency fiction),
(I0) at the ingestion time, (I2) ingestion + 2 s, (I5) ingestion + 5 s. PAPER research only."""
import json, os, random, sys
from collections import defaultdict

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcommon as C
C.set_half_spread(json.load(open(os.path.join(C.HERE, 'half_spread.json'))))
import booklib as B

D = B.data()


def sig_c1(p):
    f = p['f60']
    return f is not None and f['ub'] >= 2 and f['net_sol'] > 0 and p['tret60'] <= 2.0


def newest_block(pair, t):
    d = C.TAPE[pair]
    import bisect
    hi = bisect.bisect_right(d['et'], t)
    k = hi - 1
    while k >= 0 and d['av'][k] > t:
        k -= 1
    return d['et'][k] if k >= 0 else None


def gross(pair, t_entry, hold_s):
    s, d = C.series[pair], C.TAPE[pair]
    a = C.chain_mid_sol(s, d, t_entry)
    b = C.chain_mid_sol(s, d, t_entry + hold_s * 1000)
    if not a or not b:
        return None
    return 100 * (b[0] / a[0] - 1)


def contrast(rows, reps=1000, seed=3):
    by = defaultdict(lambda: ([], []))
    for pr, sg, v in rows:
        by[pr][0 if sg else 1].append(v)
    items = [(a, b) for a, b in by.values() if a and b]
    if len(items) < 3:
        return None
    m = lambda xs: sum(xs) / len(xs)

    def est(its):
        num = sum(len(a) * (m(a) - m(b)) for a, b in its)
        den = sum(len(a) for a, b in its)
        return num / den
    e = est(items)
    rnd = random.Random(seed)
    bs = sorted(est([items[rnd.randrange(len(items))] for _ in items]) for _ in range(reps))
    return {'diff_pp': round(e, 3), 'ci95': [round(bs[25], 3), round(bs[974], 3)], 'pools': len(items),
            'sig_n': sum(len(a) for a, b in items)}


out = {}
for part, sel in (('train', lambda p: p['t'] < D['cut']), ('holdout', lambda p: p['t'] >= D['cut'])):
    U = [p for p in D['points'] if sel(p) and p['liq'] >= 50_000 and p['fee'] <= 95]
    rnd = random.Random(11)
    if len(U) > 12000:
        U = rnd.sample(U, 12000)
    res = {}
    for clock in ('Z', 'I0', 'I2', 'I5'):
        for hold in (60, 180):
            rows = []
            for p in U:
                if clock == 'Z':
                    nb = newest_block(p['pair'], p['t'])
                    if nb is None:
                        continue
                    te = nb + 1000
                else:
                    te = p['t'] + {'I0': 0, 'I2': 2000, 'I5': 5000}[clock]
                g = gross(p['pair'], te, hold)
                if g is None:
                    continue
                rows.append((p['pair'], sig_c1(p), g))
            res['%s_%ds' % (clock, hold)] = contrast(rows)
            res['%s_%ds' % (clock, hold)]['mean_gross_all'] = round(sum(r[2] for r in rows) / len(rows), 3)
    out[part] = res
    print(part, json.dumps(res, indent=0))
json.dump(out, open(os.path.join(C.HERE, 'latency_check.json'), 'w'), indent=1)
