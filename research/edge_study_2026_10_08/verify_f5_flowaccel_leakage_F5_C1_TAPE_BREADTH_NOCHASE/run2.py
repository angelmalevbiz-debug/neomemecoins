"""Step 2: adversarial sensitivity of F5_C1 (verifier; no selection is made from these holdout numbers).
 A tape-latency sensitivity (event usable only when available + lag <= t)
 B rug-screen sensitivity (none / train-only thresholds / interim)
 C v1 fill rule (next point) - where the family's original train edge came from
 D outlier / concentration: drop top trade, leave-one-pair-out, split-point sensitivity
 E random-entry distribution in the same universe (+no-chase), many salts, matched trade count
 F neighbourhood of the thresholds (is the holdout sign a knife edge?)
 G placebo: the same flow rule evaluated on tape data 10 / 30 min stale
"""
import sys
sys.dont_write_bytecode = True
import importlib.util, json, os, pickle, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
sys.path.insert(0, OUT)
import indep as I  # noqa: E402

t0 = time.time()
series, meta = H.load()
CUT = H.split_t(meta)
I.load_tape()
RES = {}
CONFIGS = 0


def hsum(trades, cut=CUT, key='usd50'):
    ho = [x for x in trades if x['entry_t'] >= cut]
    tr = [x for x in trades if x['entry_t'] < cut]
    out = {}
    for nm, part in (('tr', tr), ('ho', ho)):
        if not part:
            out[nm] = {'n': 0}
            continue
        s = H.summarize(part, key)
        out[nm] = {'n': s['n'], 'pairs': s['pairs'], 'mean_usd': s['mean_usd'], 'mean_pct': s['mean_pct'],
                   'median_pct': s['median_pct'], 'win': s['win_rate'], 'top': s['top_pair_share'],
                   'ci_pair': s['ci95_mean_usd_pair']}
    return out


def run(name, sig, **kw):
    global CONFIGS
    CONFIGS += 1
    k = dict(I.EXITS)
    k.update(kw)
    t1 = time.time()
    tr = H.simulate(sig, **k)
    r = hsum(tr)
    RES[name] = r
    print('%-44s tr n=%-3s mean$=%-8s %%=%-7s | ho n=%-3s pairs=%-3s mean$=%-7s %%=%-7s med=%-7s win=%-5s top=%-5s ci_pair=%s  (%.1fs)' % (
        name, r['tr'].get('n'), r['tr'].get('mean_usd'), r['tr'].get('mean_pct'), r['ho'].get('n'), r['ho'].get('pairs'),
        r['ho'].get('mean_usd'), r['ho'].get('mean_pct'), r['ho'].get('median_pct'), r['ho'].get('win'),
        r['ho'].get('top'), r['ho'].get('ci_pair'), time.time() - t1), flush=True)
    return tr


base = run('C1_indep_event (reference)', I.make_signal(H, newdef='event'))

print('\n== A tape latency')
for lag in (5_000, 15_000, 30_000, 60_000):
    run('lag_%ds' % (lag // 1000), I.make_signal(H, newdef='event', lag_ms=lag))

print('\n== B rug screen')
run('rug_none', I.make_signal(H, newdef='event', rug='none'))
run('rug_train_only', I.make_signal(H, newdef='event', rug='train_only'))
run('rug_interim_and_guard_v1', I.make_signal(H, newdef='event', extra=lambda P: not H.rug_guard_v1(P)))

print('\n== C v1 next-point fills (the family screened under v1)')
H.FILL_RULE = 'next_point'
try:
    run('fill_next_point_v1', I.make_signal(H, newdef='event'))
finally:
    H.FILL_RULE = 'next_refresh'

print('\n== D outliers / concentration / split')
ho = [x for x in base if x['entry_t'] >= CUT]
xs = sorted(ho, key=lambda x: -x['usd50'])
D = {'holdout_sum_usd': round(sum(x['usd50'] for x in ho), 2),
     'top_trade': (xs[0]['sym'], xs[0]['pair'][:8], round(xs[0]['net50'], 2), round(xs[0]['usd50'], 2), round(xs[0]['size'], 1)),
     'mean_usd_wo_top1': round(sum(x['usd50'] for x in xs[1:]) / (len(xs) - 1), 3),
     'mean_usd_wo_top2': round(sum(x['usd50'] for x in xs[2:]) / (len(xs) - 2), 3),
     'mean_pct_wo_top1': round(sum(x['net50'] for x in xs[1:]) / (len(xs) - 1), 3)}
lopo = {}
for p in sorted(set(x['pair'] for x in ho)):
    rest = [x for x in ho if x['pair'] != p]
    lopo[p[:8]] = round(sum(x['usd50'] for x in rest) / len(rest), 3)
D['leave_one_pair_out_mean_usd'] = lopo
splits = {}
for frac in (0.4, 0.5, 0.6, 0.7, 0.8):
    c = H.split_t(meta, frac)
    r = hsum(base, cut=c)
    splits[str(frac)] = {'tr_n': r['tr'].get('n'), 'tr_mean_usd': r['tr'].get('mean_usd'), 'ho_n': r['ho'].get('n'),
                         'ho_mean_usd': r['ho'].get('mean_usd'), 'ho_pairs': r['ho'].get('pairs')}
D['split_sensitivity'] = splits
# per 3-hour block over the whole span
blocks = {}
for x in base:
    b = int((x['entry_t'] - meta['t0']) // (3 * 3.6e6))
    blocks.setdefault(b, []).append(x['usd50'])
D['by_3h_block_mean_usd'] = {k: (len(v), round(sum(v) / len(v), 2)) for k, v in sorted(blocks.items())}
RES['D'] = D
print(json.dumps(D, indent=1))

print('\n== E random entries in the same universe + no-chase (matched count), many salts')
U = I.universe_signal(H)
rnd_means, rnd_n, rnd_tr = [], [], []
for k in range(40):
    salt = 'vrf%d' % k
    sig = (lambda s: (lambda P: U(P) and H.hashed_coin(P.static('pair'), P.t, 0.0028, s)))(salt)
    tr = H.simulate(sig, **I.EXITS)
    CONFIGS += 1
    h = [x for x in tr if x['entry_t'] >= CUT]
    t_ = [x for x in tr if x['entry_t'] < CUT]
    if h:
        rnd_means.append(sum(x['usd50'] for x in h) / len(h))
        rnd_n.append(len(h))
    if t_:
        rnd_tr.append(sum(x['usd50'] for x in t_) / len(t_))
c1_ho = sum(x['usd50'] for x in ho) / len(ho)
c1_tr = sum(x['usd50'] for x in base if x['entry_t'] < CUT) / max(1, len([x for x in base if x['entry_t'] < CUT]))
srt = sorted(rnd_means)
E = {'salts': len(rnd_means), 'ho_n_median': sorted(rnd_n)[len(rnd_n) // 2], 'ho_n_range': [min(rnd_n), max(rnd_n)],
     'ho_mean_usd_quantiles': {q: round(srt[min(len(srt) - 1, int(q * len(srt)))], 3) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
     'ho_mean_usd_max': round(max(srt), 3),
     'c1_ho_mean_usd': round(c1_ho, 3), 'share_random_ho_ge_c1': round(sum(1 for v in rnd_means if v >= c1_ho) / len(rnd_means), 3),
     'tr_mean_usd_median_random': round(sorted(rnd_tr)[len(rnd_tr) // 2], 3), 'c1_tr_mean_usd': round(c1_tr, 3),
     'share_random_tr_le_c1': round(sum(1 for v in rnd_tr if v <= c1_tr) / len(rnd_tr), 3)}
RES['E'] = E
print(json.dumps(E, indent=1), flush=True)

print('\n== F neighbourhood of thresholds')
for nm, kw in (('ub8', dict(ub_min=8)), ('ub12', dict(ub_min=12)), ('newb3', dict(newb_min=3)), ('newb7', dict(newb_min=7)),
               ('top0.25', dict(top_max=0.25)), ('top0.35', dict(top_max=0.35)), ('nochase_-3_+4', dict(nochase=(-3.0, 4.0))),
               ('nochase_-1_+6', dict(nochase=(-1.0, 6.0))), ('live300s', dict(live_s=300))):
    run('nbr_' + nm, I.make_signal(H, newdef='event', **kw))
for nm, kw in (('stop-8_tp6', dict(stop=-8.0)), ('stop-12_tp6', dict(stop=-12.0)), ('tp5', dict(tp=5.0)), ('tp8', dict(tp=8.0)),
               ('hold20', dict(hold_min=20.0)), ('hold45', dict(hold_min=45.0)), ('size$200', dict(size_fn=None, notional=200.0))):
    run('exit_' + nm, I.make_signal(H, newdef='event'), **kw)

print('\n== G placebo: flow measured on stale tape (shifted back), universe and no-chase at decision time')


def stale_signal(shift_ms):
    U2 = I.universe_signal(H)

    def sig(P):
        if not U2(P):
            return False
        fl = I.flow(P.static('pair'), P.t - shift_ms, 300, 0, 'event')
        return (fl is not None and fl['ub'] >= 10 and fl['newb'] >= 5 and fl['top'] == fl['top'] and fl['top'] < 0.3
                and fl['net'] > 0)
    return sig


for sh in (600_000, 1_800_000):
    run('placebo_stale_%dmin' % (sh // 60000), stale_signal(sh))

RES['configs_evaluated_in_run2'] = CONFIGS
with open(os.path.join(OUT, 'run2.json'), 'w') as fh:
    json.dump(RES, fh, indent=1, default=str)
print('done', round(time.time() - t0, 1), 's; configs', CONFIGS)
