"""STAGE 2 (TRAIN ONLY): brackets x holds x slots for the stage-1 survivors and a random control per cell.
PAPER research only. Never evaluates entries at or after the 60 % cut."""
import json, os, sys, time
from common import H, HERE
import hfsim as S
from signals import UNIS, SIGS, rnd, RANDOM_P, RANDOM_SALTS
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
d = S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
TR_H = (cut - meta['t0']) / 3.6e6
SIZE = 200.0
KEEP = ['QUIET', 'QUIET_REV', 'SLOW_REFRESH', 'AT_15M_HIGH', 'MKT_UP', 'BSHARE_LO_0.50', 'VACC_LO_0.7',
        'NEAR_15M_LOW', 'MOM_STEP_0.3', 'REV_STEP_0.3']
BRACKETS = [(None, None), (1.0, None), (2.0, None), (3.0, None), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
UNAMES = sys.argv[1].split(',') if len(sys.argv) > 1 else list(UNIS)
TAG = sys.argv[2] if len(sys.argv) > 2 else ''
out = open(os.path.join(HERE, '%s%s.jsonl' % (os.path.splitext(os.path.basename(__file__))[0], TAG)), 'w')
for uname, uni in [(u, UNIS[u]) for u in UNAMES]:
    for heat_on in (True, False):
        for slots in (3, 5):
            for hold in (60, 120, 180, 300):
                for tp, sl in BRACKETS:
                    rands = []
                    for salt in RANDOM_SALTS[:3]:
                        tr, c = S.run_book(rows, uni, rnd(RANDOM_P, salt), heat_on=heat_on, slots=slots, hold_s=hold,
                                           tp=tp, sl=sl, cooldown_s=60, t_to=cut, size=SIZE)
                        rands.append(S.stats(tr, TR_H, slots * SIZE))
                    rm = sum(x['mean_net50'] for x in rands) / len(rands)
                    rtph = sum(x['tph'] for x in rands) / len(rands)
                    rusd = sum(x['usd50_per_hour'] for x in rands) / len(rands)
                    out.write(json.dumps({'uni': uname, 'heat': heat_on, 'slots': slots, 'hold': hold, 'tp': tp,
                                          'sl': sl, 'sig': 'RANDOM', 'stats': rands[0], 'random_mean_net50': rm,
                                          'random_tph': rtph, 'random_usd_h': rusd}) + '\n')
                    for sname in KEEP:
                        tr, c = S.run_book(rows, uni, SIGS[sname], heat_on=heat_on, slots=slots, hold_s=hold, tp=tp,
                                           sl=sl, cooldown_s=60, t_to=cut, size=SIZE)
                        st = S.stats(tr, TR_H, slots * SIZE)
                        st['gap_vs_random'] = round(st['mean_net50'] - rm, 3) if st['n'] else None
                        out.write(json.dumps({'uni': uname, 'heat': heat_on, 'slots': slots, 'hold': hold, 'tp': tp,
                                              'sl': sl, 'sig': sname, 'stats': st, 'counters': c,
                                              'random_mean_net50': rm, 'random_tph': rtph,
                                              'random_usd_h': rusd}) + '\n')
                out.flush()
            print(uname, heat_on, slots, 'done', round(time.time() - T0, 1), 'trade cache', len(S._TC))
out.close()
print('secs', round(time.time() - T0, 1))
