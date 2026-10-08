"""Dump the leaderboard's stored F5_C1 trades (both variants) - read only."""
import sys, pickle, json
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
LB = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/leaderboard'
d = pickle.load(open(LB + '/trades/f5_flowaccel.pkl', 'rb'))
# meta from series cache is heavy; recompute cut from known span
T0, T1 = 1791362460712, 1791444401772
CUT = T0 + 0.6 * (T1 - T0)
print('cut', CUT)
for row in d['rows']:
    if row['name'] not in ('F5_C1_TAPE_BREADTH_NOCHASE', 'F5_RANDOM_TAPE_LIVE_NOCHASE'):
        continue
    tr = row['trades']
    ho = [x for x in tr if x['entry_t'] >= CUT]
    trn = [x for x in tr if x['entry_t'] < CUT]
    print('==', row['name'], row['variant'], 'n', len(tr), 'train', len(trn), 'holdout', len(ho))
    if row['name'].startswith('F5_C1'):
        for part, sub in (('TRAIN', trn), ('HOLDOUT', ho)):
            print(' ', part)
            for x in sub:
                print('   %s %-10s %s dec=%d ent=%d exit=%d %-12s %-9s net0=%7.2f net50=%7.2f usd50=%7.2f size=%6.1f fee=%5.1f liq=%9.0f hold=%5.1fm' % (
                    x['pair'][:8], x['sym'][:10], '', x['decision_t'], x['entry_t'], x['exit_t'], x['reason'], x['flag'],
                    x['net0'], x['net50'], x['usd50'], x['size'], x['fee_bps'], x['liq'], x['hold_s'] / 60))
            if sub:
                print('   mean usd50 %.3f mean net50 %.3f' % (sum(x['usd50'] for x in sub) / len(sub), sum(x['net50'] for x in sub) / len(sub)))
