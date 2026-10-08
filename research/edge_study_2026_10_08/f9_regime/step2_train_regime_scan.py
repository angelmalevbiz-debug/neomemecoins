"""Step 2 (TRAIN ONLY): does any past-only regime variable separate good from bad entry times?

(a) Base-strategy trades (3 bases x 3 exits) split by train terciles of each regime variable.
(b) Panel: forward 30/60-min net50 of 1-per-minute random samples in two universes, by regime tercile,
    with hour-block counts. No holdout rows are read in this script.
"""
import os, pickle, sys, time, datetime, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f9lib as L
H = L.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
g = L.build_grid(series, meta)
L.add_tide(g, series, L.U_liquid)
with open(os.path.join(HERE, 'grid.pkl'), 'wb') as fh:
    pickle.dump(g, fh)
RCOLS = ('n', 'br15', 'br60', 'med15', 'med60', 'pc5', 'pc1h', 'bs', 'v5z', 'sol60', 'new60', 'tide')

EXITS = {
    'E1': dict(stop=-5, tp=10, hold_min=60),
    'E2': dict(stop=-15, tp=20, hold_min=60),
    'E3': dict(stop=-99, tp=None, hold_min=30),
}
BASES = {'RANDOM': L.sig_random(0.004), 'MOMO': L.sig_momentum, 'DIP': L.sig_dip}
configs_tried = 0
train_sets = {}
for bn, sig in BASES.items():
    for en, kw in EXITS.items():
        tr = H.simulate(sig, cooldown_s=300, t_to=cut, **kw)   # entries strictly before the split only
        configs_tried += 1
        for x in tr:
            for c in RCOLS:
                x['R_' + c] = L.at(g, c, x['entry_t'] - L.MIN)
        train_sets[(bn, en)] = tr


def terc(vals):
    v = sorted(vals)
    return v[len(v) // 3], v[2 * len(v) // 3]


def stats(xs):
    if not xs:
        return 'n=0'
    m = sum(x['net50'] for x in xs) / len(xs)
    hrs = len({int(x['entry_t'] // 3.6e6) for x in xs})
    prs = len({x['pair'] for x in xs})
    w = 100 * sum(1 for x in xs if x['net50'] > 0) / len(xs)
    return 'n=%3d hrs=%2d pairs=%2d mean50=%6.2f win=%4.1f' % (len(xs), hrs, prs, m, w)


print('== (a) base trades, TRAIN only, by regime tercile (thresholds from the same train trades)')
best = []
for key, tr in train_sets.items():
    print('--', key, stats(tr))
    for c in RCOLS:
        xs = [x for x in tr if x['R_' + c] == x['R_' + c]]
        if len(xs) < 30:
            continue
        lo, hi = terc([x['R_' + c] for x in xs])
        groups = ([x for x in xs if x['R_' + c] < lo], [x for x in xs if lo <= x['R_' + c] < hi], [x for x in xs if x['R_' + c] >= hi])
        configs_tried += 3
        line = []
        for gi, gg in enumerate(groups):
            line.append(stats(gg))
            if gg:
                best.append((sum(x['net50'] for x in gg) / len(gg), key, c, gi, round(lo, 3), round(hi, 3), len(gg), len({int(x['entry_t'] // 3.6e6) for x in gg})))
        print('   %-6s lo<%.3f | mid | hi>=%.3f   %s' % (c, lo, hi, ' || '.join(line)))
    hrs = {}
    for x in tr:
        hrs.setdefault(x['hour_utc'] if 'hour_utc' in x else datetime.datetime.fromtimestamp(x['entry_t'] / 1000, datetime.UTC).hour, []).append(x['net50'])
    print('   by UTC hour (descriptive; holdout hours are disjoint):', {h: (len(v), round(sum(v) / len(v), 1)) for h, v in sorted(hrs.items())})
best.sort(reverse=True)
print('== top 15 train tercile cells (mean net50)')
for b in best[:15]:
    print('  ', [round(b[0], 2)] + list(b[1:]))

# (b) panel of forward returns, train only
print('== (b) panel: forward net50 of 1/min random samples, TRAIN entries only')
def U_low(P):
    liq = P('liq')
    if not (liq >= 250_000) or P.fee_bps() > 50:
        return False
    if H.interim_rug_risk(P):
        return False
    rt = P.rt_cost_pct(min(200.0, liq * 0.001))
    return rt is not None and rt <= 1.2

for uname, U in (('U_liquid', L.U_liquid), ('U_lowcost', U_low)):
    rows = []
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        last = -1e18
        for i in range(len(ts) - 1):
            if ts[i] - last < 60_000 or ts[i] >= cut:
                continue
            P = H.Past(s, i)
            if not U(P):
                continue
            last = ts[i]
            f30 = H.forward_net(s, i, 1800, 200.0, 50.0)
            f60 = H.forward_net(s, i, 3600, 200.0, 50.0)
            if f30 is None:
                continue
            r = {'t': ts[i], 'pair': pair, 'f30': f30, 'f60': f60}
            for c in RCOLS:
                r[c] = L.at(g, c, ts[i])
            rows.append(r)
    print('--', uname, 'samples', len(rows), 'pairs', len({r['pair'] for r in rows}), 'mean f30', round(sum(r['f30'] for r in rows) / len(rows), 2))
    for c in RCOLS:
        xs = [r for r in rows if r[c] == r[c]]
        if len(xs) < 60:
            continue
        lo, hi = terc([r[c] for r in xs])
        out = []
        for gg in ([r for r in xs if r[c] < lo], [r for r in xs if lo <= r[c] < hi], [r for r in xs if r[c] >= hi]):
            f30 = [r['f30'] for r in gg]
            f60 = [r['f60'] for r in gg if r['f60'] is not None]
            hrs = len({int(r['t'] // 3.6e6) for r in gg})
            out.append('n=%4d hrs=%2d f30=%6.2f f60=%6.2f' % (len(gg), hrs, sum(f30) / max(1, len(f30)), sum(f60) / max(1, len(f60))))
        print('   %-6s lo<%.3f hi>=%.3f  %s' % (c, lo, hi, ' || '.join(out)))
print('configs_tried (step2 train evaluations of base x exit x regime cell):', configs_tried)
