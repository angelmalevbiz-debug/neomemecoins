"""Is +3.72%/trade (holdout, salt f2r1) anything but the luck of one random draw?

A. Salt distribution: the identical rule with 400 other salts (same universe, same p=0.005, same exits).
B. Population: enter at EVERY eligible point (p=1, cooldown/one-position rules unchanged) = the expectation the random
   draws scatter around, split train / holdout.
C. Rug-screen leakage: interim screen (thresholds chosen after seeing holdout rugs) vs the train-only variant, no screen,
   and interim + rug_guard_v1.
D. Does the pc1h<=-12 context add anything? Same random draws in the liq>=50k universe without the context.
"""
import sys
sys.dont_write_bytecode = True
import json, time, statistics as st
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'
sys.path.insert(0, OUT)
import indep as I

t0 = time.time()
ser, meta = I.load()
CUT = I.cut_time()
sh_tr, sh_ho = (CUT - meta['t0']) / 3.6e6, (meta['t1'] - CUT) / 3.6e6
OBS = 3.721
res = {}


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def gate(tr, ho):
    if len(ho) < 20:
        return False
    pairs = {}
    for x in ho:
        pairs[x['pair']] = pairs.get(x['pair'], 0) + 1
    return (len(pairs) >= 8 and mean([x['net50'] for x in ho]) > 0 and tr and mean([x['net50'] for x in tr]) > 0
            and max(pairs.values()) / len(ho) <= 0.35)


def salt_study(elig, prob, salts, label):
    rows = []
    for salt in salts:
        trs = I.run(elig, salt, prob)
        tr, ho = I.split(trs)
        rows.append({'salt': salt, 'n_tr': len(tr), 'n_ho': len(ho),
                     'tr_mean': mean([x['net50'] for x in tr]), 'ho_mean': mean([x['net50'] for x in ho]),
                     'ho_mean_model': mean([x['net0'] for x in ho]),
                     'ho_pairs': len({x['pair'] for x in ho}), 'gate_like': gate(tr, ho)})
    hm = [r['ho_mean'] for r in rows if r['ho_mean'] is not None]
    tm = [r['tr_mean'] for r in rows if r['tr_mean'] is not None]
    hn = [r['n_ho'] for r in rows]
    s = {'label': label, 'salts': len(rows), 'prob': prob,
         'holdout_mean_of_means': round(mean(hm), 3), 'holdout_median_of_means': round(st.median(hm), 3),
         'holdout_sd_of_means': round(st.pstdev(hm), 3), 'holdout_p05': round(pct(hm, .05), 3),
         'holdout_p95': round(pct(hm, .95), 3), 'share_holdout_mean_gt0': round(sum(v > 0 for v in hm) / len(hm), 3),
         'share_holdout_mean_ge_obs_3.721': round(sum(v >= OBS for v in hm) / len(hm), 3),
         'train_mean_of_means': round(mean(tm), 3), 'share_train_mean_gt0': round(sum(v > 0 for v in tm) / len(tm), 3),
         'share_both_gt0': round(sum(1 for r in rows if (r['tr_mean'] or -1) > 0 and (r['ho_mean'] or -1) > 0) / len(rows), 3),
         'share_gate_like_pass': round(sum(r['gate_like'] for r in rows) / len(rows), 3),
         'holdout_n_mean': round(mean(hn), 1)}
    print(json.dumps(s), round(time.time() - t0, 1), 's', flush=True)
    return s, rows


def population(elig, label):
    trs = I.run(elig, 'pop', 1.0)
    tr, ho = I.split(trs)
    a, b = I.summ(tr, span_h=sh_tr), I.summ(ho, span_h=sh_ho)
    bm = I.summ(ho, 'usd0', span_h=sh_ho)
    keep = ('n', 'pairs', 'win', 'mean_usd', 'mean_pct', 'median_pct', 'pf', 'ci95_pairhour', 'ci95_pair', 'top_pair_share', 'exits')
    out = {'label': label, 'train': {k: a.get(k) for k in keep}, 'holdout': {k: b.get(k) for k in keep},
           'holdout_model_mean_pct': bm.get('mean_pct')}
    print('POP', json.dumps(out, default=str), flush=True)
    return out


SALTS = ['v%03d' % k for k in range(400)]
elig = {sc: I.eligible_index(screen=sc) for sc in ('interim', 'train_only', 'none', 'interim+guard_v1')}
for sc, e in elig.items():
    print('eligible', sc, 'pairs', len(e), 'points', sum(len(v) for v in e.values()))

# A + B for the submitted screen
res['A_interim'], rows_interim = salt_study(elig['interim'], 0.005, SALTS, 'ctx1h interim screen, 400 salts')
res['A_interim_rank_of_f2r1'] = None
res['B_pop_interim'] = population(elig['interim'], 'ctx1h interim screen, every eligible point')
# C screens
for sc in ('train_only', 'none', 'interim+guard_v1'):
    res['C_' + sc], _ = salt_study(elig[sc], 0.005, SALTS[:200], 'ctx1h %s, 200 salts' % sc)
    res['C_pop_' + sc] = population(elig[sc], 'ctx1h %s, every eligible point' % sc)
# f2r1 itself under each screen
for sc in ('interim', 'train_only', 'none', 'interim+guard_v1'):
    trs = I.run(elig[sc], 'f2r1', 0.005)
    tr, ho = I.split(trs)
    a, b = I.summ(tr, span_h=sh_tr), I.summ(ho, span_h=sh_ho)
    res['f2r1_' + sc] = {'train_n': a['n'], 'train_mean_pct': a.get('mean_pct'), 'holdout_n': b['n'],
                         'holdout_pairs': b.get('pairs'), 'holdout_mean_pct': b.get('mean_pct'),
                         'holdout_mean_usd': b.get('mean_usd'), 'holdout_ci95_pair': b.get('ci95_pair'),
                         'holdout_exits': b.get('exits')}
    print('f2r1', sc, json.dumps(res['f2r1_' + sc], default=str), flush=True)
# D: no context
elig_u = I.eligible_index(pc1h_max=None, screen='interim')
print('eligible universe-only points', sum(len(v) for v in elig_u.values()), 'pairs', len(elig_u))
res['D_universe_p0005'], _ = salt_study(elig_u, 0.0005, SALTS[:100], 'liq>=50k + interim, no pc1h context, p=0.0005, 100 salts')
res['D_pop_universe'] = population(elig_u, 'liq>=50k + interim, no context, every eligible point')

hm = sorted(r['ho_mean'] for r in rows_interim if r['ho_mean'] is not None)
res['A_interim_rank_of_obs'] = {'salts_with_holdout_mean_ge_obs': sum(v >= OBS for v in hm), 'of': len(hm)}
with open(OUT + '/v03_salts.json', 'w', encoding='utf-8') as fh:
    json.dump({'summary': res, 'rows_interim': rows_interim}, fh, indent=1, default=str)
print('secs', round(time.time() - t0, 1))
