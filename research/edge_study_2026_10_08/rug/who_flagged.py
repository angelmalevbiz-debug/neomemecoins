"""Which clean-negative pools does an atom flag (TRAIN), and what happened to them later (diagnostic)?"""
import collections, pickle, os, sys
from rugcommon import load_points, _series, HERE, T1

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
pts = [x for x in load_points() if x['part'] == (sys.argv[2] if len(sys.argv) > 2 else 'train')]
D14 = 14 * 1440
ATOMS = {
    'FAKE': lambda x: x['lmc'] < 0.02 and x['mcap'] >= 20e6 and x['age'] < D14,
    'REUSE1': lambda x: x['reuse'] >= 1 and x['age'] < D14,
    'IMBAL5': lambda x: x['bs1h'] >= 5 and x['age'] < D14,
    'YOUNG720': lambda x: x['age'] < 720,
    'YOUNG1440': lambda x: x['age'] < 1440,
    'LP1': lambda x: x['lmc'] >= 1.0,
}
f = ATOMS[sys.argv[1]]
c = collections.defaultdict(list)
for x in pts:
    if x['clean_neg'] and x['liq'] >= 50_000 and f(x):
        c[x['pair']].append(x)
for p, v in sorted(c.items(), key=lambda kv: -len(kv[1])):
    r = lab[p]
    x = v[-1]
    fate = 'DRAINED %s at +%.1fh' % ('+'.join(r['kinds']), (r['drain_t'] - x['t']) / 3.6e6) if r['drain_t'] else (
        'vanished %.1fh before end' % ((T1 - r['t_last']) / 3.6e6) if r['vanished'] else 'alive at end')
    print('%-10s %s pts %4d liq %8.0f mcap %11.0f lmc %.4f age %7.0f reuse %2d bs1h %5.1f pump %d fee %3d | %s' % (
        (_series[p]['sym'] or '')[:10], p[:8], len(v), x['liq'], x['mcap'], x['lmc'], x['age'], x['reuse'], x['bs1h'], x['pump'], x['fee'], fate))
