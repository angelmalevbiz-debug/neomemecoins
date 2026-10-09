"""STAGE 1 (TRAIN ONLY): every signal x universe x heat veto on/off x hold, time exits, 5 slots, $200, 60 s re-entry
cooldown, no daily cap; random controls (5 salts) with the same universe, heat setting, exits and slots.
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
SLOTS, SIZE = 5, 200.0
UNAMES = sys.argv[1].split(',') if len(sys.argv) > 1 else list(UNIS)
TAG = sys.argv[2] if len(sys.argv) > 2 else ''
out = open(os.path.join(HERE, '%s%s.jsonl' % (os.path.splitext(os.path.basename(__file__))[0], TAG)), 'w')
for uname, uni in [(u, UNIS[u]) for u in UNAMES]:
    for heat_on in (True, False):
        for hold in (60, 120, 180, 300):
            rands = []
            for salt in RANDOM_SALTS:
                tr, c = S.run_book(rows, uni, rnd(RANDOM_P, salt), heat_on=heat_on, slots=SLOTS, hold_s=hold,
                                   cooldown_s=60, t_to=cut, size=SIZE)
                st = S.stats(tr, TR_H, SLOTS * SIZE)
                rands.append(st)
                out.write(json.dumps({'uni': uname, 'heat': heat_on, 'hold': hold, 'sig': 'RANDOM_' + salt,
                                      'stats': st, 'counters': c}) + '\n')
            rm = sum(x['mean_net50'] for x in rands) / len(rands)
            rtph = sum(x['tph'] for x in rands) / len(rands)
            print('%s heat=%d hold=%3d RANDOM mean net50 %+.3f (salts %s) tph %.1f' % (
                uname, heat_on, hold, rm, ' '.join('%+.2f' % x['mean_net50'] for x in rands), rtph))
            for sname, sig in SIGS.items():
                tr, c = S.run_book(rows, uni, sig, heat_on=heat_on, slots=SLOTS, hold_s=hold, cooldown_s=60,
                                   t_to=cut, size=SIZE)
                st = S.stats(tr, TR_H, SLOTS * SIZE)
                st['gap_vs_random'] = round(st.get('mean_net50', float('nan')) - rm, 3) if st['n'] else None
                out.write(json.dumps({'uni': uname, 'heat': heat_on, 'hold': hold, 'sig': sname, 'stats': st,
                                      'counters': c, 'random_mean_net50': rm, 'random_tph': rtph}) + '\n')
                if st['n']:
                    print('   %-16s n %5d tph %5.1f net50 %+.3f gap %+.3f net0 %+.3f gross %+.3f win %4.1f pairs %2d top %.2f' % (
                        sname, st['n'], st['tph'], st['mean_net50'], st['gap_vs_random'], st['mean_net0'],
                        st['mean_gross'], st['win50'], st['pairs'], st['top_pair_share']))
            out.flush()
out.close()
print('secs', round(time.time() - T0, 1))
