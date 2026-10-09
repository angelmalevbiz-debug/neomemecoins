"""hf_synthesis diagnostics on the already-chosen configs (no selection): print outliers, cost ceiling, fill statuses,
cap arithmetic and the reach of a two-print confirmation rule. PAPER research only."""
import json, os, sys
import syn_lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
H = L.H
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
t0, t1 = meta['t0'], meta['t1']
uni = L.UNIVERSES['E95']
SHARED = dict(slots=3, hold_s=120, cooldown_s=120, pacing=('roll', 3600, 50), size=25.0, heat_on=True)
out = {}
# (b) modeled round trip at $25 over E95 heat-passing refresh events
rts = []
for r in rows:
    if uni(r) and not r['heat']:
        s = L.SERIES[r['pair']]
        v = H.roundtrip_cost_pct(s, r['i'], 25.0)
        if v is not None:
            rts.append((v, r['fee']))
rts.sort()
n = len(rts)
out['rt25'] = {'n': n, 'p50': round(rts[n // 2][0], 3), 'p90': round(rts[int(.9 * n)][0], 3),
               'p99': round(rts[int(.99 * n)][0], 3), 'max': round(rts[-1][0], 3),
               'share_gt_2.75': round(sum(1 for v, _ in rts if v > 2.75) / n, 4),
               'by_fee_p50': {}}
for lo, hi in ((0, 50), (51, 75), (76, 95)):
    xs = sorted(v for v, f in rts if lo <= f <= hi)
    if xs:
        out['rt25']['by_fee_p50']['%d-%d' % (lo, hi)] = [len(xs), round(xs[len(xs) // 2], 3), round(xs[-1], 3)]
print('rt25', json.dumps(out['rt25']))

# (a) outsized fill prints in the chosen books (train + holdout), and a two-print confirmation counterfactual
for name, pred in (('RND', L.rnd(0.25, 'hfA')), ('QUIET', L.PREDICATES['QUIET']), ('DIP15', L.PREDICATES['NEAR15LOW'])):
    tr = []
    for a, b in ((t0, cut), (cut, t1)):
        x, _ = L.run_book(rows, uni, pred, t_from=a, t_to=b, **SHARED)
        tr += x
    big_e = big_x = 0
    gross = sorted(x['gross'] for x in tr if L.fin(x['gross']))
    for x in tr:
        s = L.SERIES[x['pair']]
        ts, px = s['t'], s['price']
        i = x['i']
        j = H.fill_index(s, i)
        if j is not None and px[i] > 0 and abs(px[j] / px[i] - 1) >= 0.05:
            big_e += 1
    eq = sum(1 for x in tr if x['entry_quiet']) / len(tr)
    xq = sum(1 for x in tr if x['exit_quiet']) / len(tr)
    flags = {}
    for x in tr:
        k = x['reason'] + ('/' + x['flag'] if x['flag'] else '')
        flags[k] = flags.get(k, 0) + 1
    absg = [abs(g) for g in gross]
    print('%s n %d | |gross|>=5%%: %d (%.3f%%), >=10%%: %d | entry fill print >=5%% from decision: %d | gross p1 %.2f p99 %.2f min %.2f max %.2f | entry quiet %.3f exit quiet %.3f | flags %s' % (
        name, len(tr), sum(1 for g in absg if g >= 5), 100 * sum(1 for g in absg if g >= 5) / len(tr),
        sum(1 for g in absg if g >= 10), big_e, gross[int(.01 * len(gross))], gross[int(.99 * len(gross))],
        gross[0], gross[-1], eq, xq, flags))
    big = sorted(tr, key=lambda x: -abs(x['gross']) if L.fin(x['gross']) else 0)[:5]
    for x in big:
        print('   %s %s gross %+.2f net50 %+.2f liq %.0f fee %.0f %s' % (L.ab(x['pair']), L.SERIES[x['pair']]['sym'],
              x['gross'], x['net50'], x['liq'], x['fee_bps'], x['reason'] + ('/' + x['flag'] if x['flag'] else '')))

# (c) eligible pools per live minute in E95 heat-passing (train / holdout) for the dashboard expectation
for lab, a, b in (('train', t0, cut), ('holdout', cut, t1)):
    per_min = {}
    for r in rows:
        if a <= r['t'] < b and uni(r) and not r['heat']:
            per_min.setdefault(int(r['t'] // 60000), set()).add(r['pair'])
    mins = int((b - a) // 60000)
    vals = sorted(len(per_min.get(m, ())) for m in range(int(a // 60000), int(a // 60000) + mins))
    print('%s eligible pools refreshing per minute: mean %.2f p10 %d p50 %d p90 %d, zero minutes %.3f' % (
        lab, sum(vals) / len(vals), vals[int(.1 * len(vals))], vals[len(vals) // 2], vals[int(.9 * len(vals))],
        sum(1 for v in vals if v == 0) / len(vals)))
