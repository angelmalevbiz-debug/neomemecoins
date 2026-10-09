"""Diagnostics for the 3 frozen configs (no new configs): holdout gap CI by pair bootstrap, $ drawdown, hourly
trade counts, fee composition; heat on/off for momentum families from the TRAIN stage-1 grid. PAPER research only."""
import json, os, random, sys
from common import H, HERE, fin
import hfsim as S
from signals import UNIS, SIGS, rnd, RANDOM_P, RANDOM_SALTS
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
exec(open(os.path.join(HERE, 'final_eval.py')).read().split('RESULTS = ')[0].split('CONFIGS = ')[1].join(['CONFIGS = ', '']) if False else '')
d = S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
HO_H = (meta['t1'] - cut) / 3.6e6
CONFIGS = json.load(open(os.path.join(HERE, 'final_eval.json')))['configs']


def run(cfg, sigf, cap=None):
    return S.run_book(rows, UNIS[cfg['uni']], sigf, heat_on=cfg['heat_on'], slots=cfg['slots'], hold_s=cfg['hold_s'],
                      tp=cfg['tp'], sl=cfg['sl'], cooldown_s=60, size=200.0, cap_usd=cap, t_from=cut)[0]


def gap_ci(book, rand, key, reps=2000, seed=11):
    gb, gr = {}, {}
    for x in book:
        gb.setdefault(x['pair'], []).append(x[key])
    for x in rand:
        gr.setdefault(x['pair'], []).append(x[key])
    pairs = sorted(set(gb) | set(gr))
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        sb = nb = sr = nr = 0
        for _ in range(len(pairs)):
            p = pairs[rng.randrange(len(pairs))]
            v = gb.get(p, ())
            sb += sum(v); nb += len(v)
            v = gr.get(p, ())
            sr += sum(v); nr += len(v)
        if nb and nr:
            out.append(sb / nb - sr / nr)
    out.sort()
    return round(out[int(len(out) * .025)], 3), round(out[int(len(out) * .975)], 3)


def dd_usd(trades):
    eq = peak = 0.0
    m = 0.0
    for x in sorted(trades, key=lambda r: r['exit_t']):
        eq += x['usd50']
        peak = max(peak, eq)
        m = max(m, peak - eq)
    return round(m, 2)


def fee_mix(trades):
    b = {'fee<=50': 0, 'fee55-95': 0}
    for x in trades:
        b['fee<=50' if x['fee_bps'] <= 50 else 'fee55-95'] += 1
    n = max(1, len(trades))
    liq = sorted(x['liq'] for x in trades)
    return {k: round(v / n, 3) for k, v in b.items()}, round(liq[len(liq) // 2]) if liq else None


for res in CONFIGS:
    cfg = res['config']
    book = run(cfg, SIGS[cfg['sig']])
    rand = []
    for salt in RANDOM_SALTS:
        rand += run(cfg, rnd(RANDOM_P, salt))
    hrs = {}
    for x in book:
        h = int(x['decision_t'] // 3_600_000)
        hrs[h] = hrs.get(h, 0) + 1
    h0, h1 = int(cut // 3_600_000), int(meta['t1'] // 3_600_000)
    counts = sorted(hrs.get(h, 0) for h in range(h0 + 1, h1))   # full hours only
    capped = run(cfg, SIGS[cfg['sig']], cap=0.10 * cfg['slots'] * 200.0)
    fm_b, liq_b = fee_mix(book)
    fm_r, liq_r = fee_mix(rand)
    print('== %s holdout' % cfg['name'])
    print('   gap net50 %+.3f pair-CI95 %s | gap gross %+.3f pair-CI95 %s' % (
        sum(x['net50'] for x in book) / len(book) - sum(x['net50'] for x in rand) / len(rand), gap_ci(book, rand, 'net50'),
        sum(x['gross'] for x in book) / len(book) - sum(x['gross'] for x in rand) / len(rand), gap_ci(book, rand, 'gross')))
    print('   $ drawdown no cap %.0f (start capital $%.0f) | with 10%%/day cap %.0f | full-hour trade counts min %d p10 %d median %d max %d (hours %d)' % (
        dd_usd(book), cfg['slots'] * 200, dd_usd(capped), counts[0], counts[len(counts) // 10], counts[len(counts) // 2],
        counts[-1], len(counts)))
    print('   fee mix book %s median liq $%s | random %s median liq $%s' % (fm_b, liq_b, fm_r, liq_r))
    bt = {}
    for x in book:
        bt[x['pair']] = bt.get(x['pair'], 0) + 1
    print('   top pairs', [(p[:8], n) for p, n in sorted(bt.items(), key=lambda kv: -kv[1])[:5]])

print('== heat veto on/off, TRAIN stage-1 engine-guard grid (5 slots, time exits)')
R = [json.loads(l) for l in open(os.path.join(HERE, 'stage1_train_E.jsonl'))]
idx = {(r['uni'], r['heat'], r['hold'], r['sig']): r for r in R}
for sig in ('RANDOM_hfA', 'MOM_STEP_0.3', 'MOM_STEP_0.5', 'MOM_2STEP_0.5', 'BUYJUMP_2', 'AT_15M_HIGH', 'REV_STEP_0.3', 'QUIET'):
    for hold in (60, 120):
        a, b = idx.get(('E95', True, hold, sig)), idx.get(('E95', False, hold, sig))
        if a and b and a['stats']['n'] and b['stats']['n']:
            print('   E95 hold %3d %-14s heat ON n %5d tph %5.1f net50 %+.3f | OFF n %5d tph %5.1f net50 %+.3f' % (
                hold, sig, a['stats']['n'], a['stats']['tph'], a['stats']['mean_net50'], b['stats']['n'],
                b['stats']['tph'], b['stats']['mean_net50']))
