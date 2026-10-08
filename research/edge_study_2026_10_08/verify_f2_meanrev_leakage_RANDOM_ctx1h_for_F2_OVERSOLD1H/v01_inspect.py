"""Inspect the leaderboard's recorded trades for RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB (orig + rug_guard_v1)."""
import sys
sys.dont_write_bytecode = True
import pickle, json, time
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'
T0, T1 = 1791362460712, 1791444401772
CUT = T0 + 0.6 * (T1 - T0)

with open(DEEP + '/leaderboard/trades/f2_meanrev.pkl', 'rb') as fh:
    d = pickle.load(fh)
for r in d['rows']:
    print(r['name'], r['variant'], r['kind'], len(r['trades']), r['seconds'], r.get('error'))

keep = {}
for r in d['rows']:
    if r['name'] == 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB':
        keep[r['variant']] = r['trades']
        tr = [x for x in r['trades'] if x['entry_t'] < CUT]
        ho = [x for x in r['trades'] if x['entry_t'] >= CUT]
        print('\n==', r['variant'], 'kwargs', r['kwargs'], 'n', len(r['trades']), 'train', len(tr), 'holdout', len(ho))
        for lab, grp in (('TRAIN', tr), ('HOLDOUT', ho)):
            print('  --', lab, 'mean net50 %.3f  mean usd50 %.3f  mean net0 %.3f' % (
                sum(x['net50'] for x in grp) / len(grp), sum(x['usd50'] for x in grp) / len(grp),
                sum(x['net0'] for x in grp) / len(grp)))
            for x in grp:
                print('   %s %-10s %s dec=%d ent=%d (+%4.0fs) exit=%.1fmin %-12s %-8s net0 %+7.2f net50 %+7.2f mfe %+6.1f mae %+6.1f fee %5.1f liq %8.0f epx %.3g xpx %.3g' % (
                    x['pair'][:8], (x['sym'] or '')[:10], time.strftime('%H:%M:%S', time.gmtime(x['entry_t'] / 1000)),
                    x['decision_t'], x['entry_t'], (x['entry_t'] - x['decision_t']) / 1000, x['hold_s'] / 60,
                    x['reason'], x['flag'], x['net0'], x['net50'], x['mfe'], x['mae'], x['fee_bps'], x['liq'],
                    x['entry_px'], x['exit_px']))

with open(OUT + '/lb_trades.pkl', 'wb') as fh:
    pickle.dump(keep, fh)
