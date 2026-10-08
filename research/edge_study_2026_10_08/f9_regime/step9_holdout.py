"""Step 9: the ONE holdout look for the 3 pre-selected configs (no iteration after this).
Also: random baselines (strategies.BASELINES), unfiltered-base comparators, 20-salt null distributions,
and the pre-registered panel test of the market-dip regime effect in the holdout window."""
import os, sys, json, random
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategies as S
H = S.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
T0 = meta['t0']
OUT = {}
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'mean_usd', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')


def full(name, sig, kw):
    tr = H.simulate(sig, **kw)
    ev = H.evaluate(tr)
    ho = [x for x in tr if x['entry_t'] >= cut]
    trn = [x for x in tr if x['entry_t'] < cut]
    res = {part: {k: ev[part].get(k) for k in KEEP} for part in ('train', 'holdout', 'train_model', 'holdout_model')}
    res['portfolio_holdout_slots3'] = H.portfolio(ho, 3)
    res['portfolio_holdout_slots3_model'] = H.portfolio(ho, 3, key='usd0')
    res['portfolio_all_slots3'] = H.portfolio(tr, 3)
    res['portfolio_train_slots3'] = H.portfolio(trn, 3)
    OUT[name] = res
    print('==', name)
    for part in ('train', 'holdout', 'train_model', 'holdout_model'):
        print('   %-13s %s' % (part, res[part]))
    print('   portfolio holdout slots=3 net50 %s | model %s | all-span %s' % (res['portfolio_holdout_slots3'], res['portfolio_holdout_slots3_model'], res['portfolio_all_slots3']))
    return tr


for c in S.CONFIGS:
    full(c['name'], c['signal'], c['kwargs'])
for c in S.BASELINES:
    full(c['name'], c['signal'], c['kwargs'])
full('COMPARATOR_DIP_unfiltered_E2', S.sig_dip, S.E2)
full('COMPARATOR_DIP_unfiltered_H30', S.sig_dip, S.E3)

# 20-salt null distributions on holdout
print('== 20-salt null distributions (holdout mean net50 %)')
setups = [('F9_DIP_MKTDIP_E2', S.E2, 0.0007, 0.004, S._mkt_dip),
          ('F9_DIP_MKTDIPN_H30', S.E3, 0.00035, 0.004, S._mkt_dip_native),
          ('F9_BASKET_MKTDIP_E2', S.E2, 0.004, None, None)]
OUT['nulls'] = {}
for name, kw, p_plain, p_reg, reg in setups:
    cfg_mean = OUT[name]['holdout'].get('mean_pct')
    for label, mk in (('random_no_filter', lambda salt: (lambda P: S.U_liquid(P) and S.rnd(p_plain, salt)(P))),
                      ('random_plus_same_filter', (lambda salt: (lambda P: S.U_liquid(P) and S.rnd(p_reg, salt)(P) and reg(P))) if p_reg else None)):
        if mk is None:
            continue
        means, ns = [], []
        for k in range(20):
            rt = [x for x in H.simulate(mk('ho%d' % k), t_from=cut, **kw)]
            if rt:
                means.append(sum(x['net50'] for x in rt) / len(rt))
                ns.append(len(rt))
        means.sort()
        rank = sum(1 for m in means if cfg_mean is not None and m < cfg_mean) / len(means)
        d = {'avg_n': sum(ns) / len(ns), 'mean': sum(means) / len(means), 'p5': means[1], 'p50': means[10], 'p95': means[18],
             'config_holdout_mean': cfg_mean, 'config_pct_rank': rank}
        OUT['nulls'][name + '|' + label] = d
        print('  %-22s %-24s %s' % (name, label, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}))


# pre-registered panel test of the regime effect, holdout window
def hb_ci(rows, key, reps=1000, seed=3):
    blocks = {}
    for r in rows:
        blocks.setdefault(int((r['t'] - T0) // 3.6e6), []).append(r[key])
    bl = list(blocks.values())
    allv = [v for b in bl for v in b]
    if len(bl) < 3:
        return (sum(allv) / len(allv) if allv else float('nan')), None, None, len(bl)
    rnd = random.Random(seed)
    ms = []
    for _ in range(reps):
        tot = cnt = 0
        for _ in range(len(bl)):
            b = bl[rnd.randrange(len(bl))]
            tot += sum(b)
            cnt += len(b)
        ms.append(tot / cnt)
    ms.sort()
    return sum(allv) / len(allv), ms[int(reps * .025)], ms[int(reps * .975)], len(bl)


print('== panel U_liquid, HOLDOUT: 1/min random samples, forward net50 %, by med15 bin (train showed ~+1..1.5 pt for med15<-0.2)')
rows = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last = -1e18
    for i in range(len(ts) - 1):
        if ts[i] - last < 60_000 or ts[i] < cut:
            continue
        P = H.Past(s, i)
        if not S.U_liquid(P):
            continue
        last = ts[i]
        f30 = H.forward_net(s, i, 1800, 200.0, 50.0)
        f60 = H.forward_net(s, i, 3600, 200.0, 50.0)
        if f30 is None or f60 is None:
            continue
        rows.append({'t': ts[i], 'pair': pair, 'f30': f30, 'f60': f60, 'med15': S.regime('med15', ts[i]), 'med15n': S.regime('med15n', ts[i])})
OUT['panel_holdout'] = {}
for col in ('med15', 'med15n'):
    for lo, hi in ((-99, -0.5), (-0.5, -0.2), (-0.2, 0.0), (0.0, 0.3), (0.3, 99), (-99, -0.2), (-0.2, 99)):
        sub = [r for r in rows if r[col] == r[col] and lo <= r[col] < hi]
        if not sub:
            print('   %-6s [%5.1f,%5.1f) n=0' % (col, lo, hi))
            continue
        m30, a30, b30, nb = hb_ci(sub, 'f30')
        m60, a60, b60, _ = hb_ci(sub, 'f60')
        OUT['panel_holdout']['%s[%s,%s)' % (col, lo, hi)] = {'n': len(sub), 'hours': nb, 'f30': m30, 'f30_ci': [a30, b30], 'f60': m60, 'f60_ci': [a60, b60]}
        print('   %-6s [%5.1f,%5.1f) n=%5d hours=%2d pairs=%2d  f30=%6.2f [%s,%s]  f60=%6.2f [%s,%s]' % (
            col, lo, hi, len(sub), nb, len({r['pair'] for r in sub}), m30, a30 and round(a30, 2), b30 and round(b30, 2),
            m60, a60 and round(a60, 2), b60 and round(b60, 2)))
# share of holdout minutes in the regime (for context)
g = S.regime_grid()
mins = [g['med15'][m] for m in range(g['M']) if g['g0'] + m * S.MIN >= cut and g['med15'][m] == g['med15'][m]]
mins_tr = [g['med15'][m] for m in range(g['M']) if g['g0'] + m * S.MIN < cut and g['med15'][m] == g['med15'][m]]
OUT['regime_share'] = {'train_minutes': len(mins_tr), 'train_share_med15_lt_-0.2': sum(1 for v in mins_tr if v < -0.2) / len(mins_tr),
                       'holdout_minutes': len(mins), 'holdout_share_med15_lt_-0.2': sum(1 for v in mins if v < -0.2) / len(mins)}
print('regime share', OUT['regime_share'])
with open(os.path.join(HERE, 'holdout_results.json'), 'w', encoding='utf-8') as fh:
    json.dump(OUT, fh, indent=1, default=str)
print('saved holdout_results.json')
