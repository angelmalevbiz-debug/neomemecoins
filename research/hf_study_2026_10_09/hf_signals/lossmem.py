"""Achievable trades/hour of the frozen configs under the engine's POOL_LOSS_MEMORY_V1 (2 losses -> 6 h block per
pool per book), and a regression check that the simulator edit leaves the frozen results unchanged."""
import json, os, sys
from common import HERE
import hfsim as S
from signals import UNIS, SIGS
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
d = S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
HO_H = (meta['t1'] - cut) / 3.6e6
F = json.load(open(os.path.join(HERE, 'final_eval.json')))
for res in F['configs']:
    cfg = res['config']
    kw = dict(heat_on=cfg['heat_on'], slots=cfg['slots'], hold_s=cfg['hold_s'], tp=cfg['tp'], sl=cfg['sl'],
              cooldown_s=60, size=200.0, t_from=cut)
    tr, c = S.run_book(rows, UNIS[cfg['uni']], SIGS[cfg['sig']], **kw)
    st = S.stats(tr, HO_H, cfg['slots'] * 200.0)
    same = st['n'] == res['holdout']['book']['n'] and abs(st['mean_net50'] - res['holdout']['book']['mean_net50']) < 1e-9
    trm, cm = S.run_book(rows, UNIS[cfg['uni']], SIGS[cfg['sig']], loss_memory=(2, 6 * 3_600_000), **kw)
    sm = S.stats(trm, HO_H, cfg['slots'] * 200.0)
    print('%s holdout: regression %s (n %d) | with POOL_LOSS_MEMORY_V1: n %d tph %.1f net50 %+.3f pairs %d, blocked signals %d' % (
        cfg['name'], 'OK' if same else 'MISMATCH', st['n'], sm['n'], sm['tph'], sm['mean_net50'], sm['pairs'],
        cm['loss_mem_block']))
