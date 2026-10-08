"""Inspect the leaderboard's cached trades for the verified row (read-only)."""
import sys
sys.dont_write_bytecode = True
import pickle, time
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
with open(DEEP + '/leaderboard/trades/f2_meanrev.pkl', 'rb') as fh:
    d = pickle.load(fh)
T0, T1 = 1791362460712, 1791444401772
cut = T0 + 0.6 * (T1 - T0)
for r in d['rows']:
    print(r['name'], r['variant'], r['kind'], len(r['trades']), r.get('seconds'))
for r in d['rows']:
    if r['name'] == 'RANDOM_ctx1h_for_F2_OVERSOLD1H_STAB':
        print('==', r['variant'])
        for x in r['trades']:
            print('%s %-10s %-8s %5.1fh %-8s %-4s %7.2f %7.2f mfe %6.1f mae %6.1f fee %5.1f liq %9.0f hold %5.1fm' % (
                'H' if x['entry_t'] >= cut else 'T', (x['sym'] or '?')[:10], x['pair'][:8], (x['entry_t'] - T0) / 3.6e6,
                x['reason'], x['flag'], x['net0'], x['net50'], x['mfe'], x['mae'], x['fee_bps'], x['liq'],
                x['hold_s'] / 60))
