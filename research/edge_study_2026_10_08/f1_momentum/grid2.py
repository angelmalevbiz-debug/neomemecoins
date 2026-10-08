"""F1 grid 2 (TRAIN ONLY): trend-with-pullback refinement, LO-scaled momentum, wider exits.

Appends every configuration to grid2.jsonl (counted in configs_tried).
"""
import json, os, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import features as F
from grid1 import uni, keep, pair_robust, series, meta, feats, cut, entry_sets

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'grid2.jsonl')


def dipup(th, dip):
    return lambda s, f, i: s['pc1h'][i] >= th and f['r300'][i] <= -dip


SIG_HI = {}
for th in (10, 20, 35):
    for dip in (2, 4, 6):
        SIG_HI['dipup_pc1h%d_r300le-%d' % (th, dip)] = dipup(th, dip)
SIG_HI['dipup6h_pc6h30_pc1h0_r300le-3'] = lambda s, f, i: s['pc6h'][i] >= 30 and s['pc1h'][i] >= 0 and f['r300'][i] <= -3
SIG_LO = {
    'lo_spike_r300ge0.75_vacc1.2_bsh.55': lambda s, f, i: f['r300'][i] >= 0.75 and f['vacc'][i] >= 1.2 and f['bsh5'][i] >= 0.55,
    'lo_trend_r1800ge2_hi600ge-0.3': lambda s, f, i: f['r1800'][i] >= 2 and f['hi600'][i] >= -0.3,
}
EXITS = {
    'E3_s8_tp40_trail15/8_h120': dict(stop=-8, tp=40, trail_arm=15, trail=8, hold_min=120),
    'E5_s10_trail20/10_h120': dict(stop=-10, tp=None, trail_arm=20, trail=10, hold_min=120),
    'E7_s15_trail25/12_h180': dict(stop=-15, tp=None, trail_arm=25, trail=12, hold_min=180),
    'E8_s20_h60': dict(stop=-20, tp=None, hold_min=60),
    'E9_s8_tp15_h60': dict(stop=-8, tp=15, hold_min=60),
}

if __name__ == '__main__':
    n_cfg = 0
    t0 = time.time()
    plan = [(u, SIG_HI) for u in ('HI30', 'HI50', 'MID')] + [('LO', SIG_LO)]
    with open(OUT, 'w', encoding='utf-8') as fh:
        for uname, sigs in plan:
            for sname, sig in sigs.items():
                sets = entry_sets(sig, uname)
                signal = (lambda sets: (lambda P: P.i in sets.get(P.s['pair'], ())))(sets)
                for ename, kw in EXITS.items():
                    tr = H.simulate(signal, pairs=set(sets), t_to=cut, **kw)
                    tr = [x for x in tr if x['entry_t'] < cut]
                    n_cfg += 1
                    sm = H.summarize(tr)
                    rec = {'universe': uname, 'signal': sname, 'exit': ename, 'train': keep(sm),
                           'train_model_mean_pct': H.summarize(tr, 'usd0').get('mean_pct'), 'pair_mean_net50': pair_robust(tr)}
                    fh.write(json.dumps(rec) + '\n')
                    print(uname, sname, ename, 'n', sm.get('n'), 'pairs', sm.get('pairs'), 'mean50', sm.get('mean_pct'),
                          'med', sm.get('median_pct'), 'pm', rec['pair_mean_net50'], 'top', sm.get('top_pair_share'),
                          'model', rec['train_model_mean_pct'], flush=True)
    print('configs evaluated', n_cfg, 'secs', round(time.time() - t0, 1))
