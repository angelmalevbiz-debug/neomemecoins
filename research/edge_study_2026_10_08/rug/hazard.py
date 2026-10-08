"""Step (c): drain hazard per position-hour by universe slice, with and without the guards.

Unit: a per-minute decision point (PumpSwap, SOL quote, before the pair's drain).
hazard = share of points followed by a terminal drain event within the next 60 min
         (= probability that a 60-min position opened there meets a drain; ~ per position-hour).
Points whose 60-min window passes the end of the data, or whose pair vanished from the feed
within 60 min without an observed drain (unknown fate), are excluded and counted separately (van%).
"""
import collections, os, pickle, sys
from rugcommon import HERE, T1

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
fl = pickle.load(open(os.path.join(HERE, 'flags.pkl'), 'rb'))
HOUR = 3_600_000


def slice_of(x):
    f, l = x['fee'], x['liq']
    fb = 'fee<=50' if f <= 50 else ('fee55-95' if f < 100 else 'fee100-125')
    lb = 'liq>=250k' if l >= 250e3 else ('liq50-250k' if l >= 50e3 else ('liq20-50k' if l >= 20e3 else 'liq<20k'))
    return fb + ' ' + lb


rows = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, set(), set()]))
for x in fl:
    r = lab[x['pair']]
    terminal = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
    t = x['t']
    if t + HOUR > T1:
        continue
    y = 1 if (terminal and t < r['drain_t'] <= t + HOUR) else 0
    van = (not y) and r['vanished'] and r['t_last'] <= t + HOUR and not terminal
    for part in ('all', x['part'] if x['part'] != 'drop' else 'train'):
        for sl in (slice_of(x), 'ALL liq>=20k' if x['liq'] >= 20e3 else None, 'ALL'):
            if sl is None:
                continue
            for g in ('none', 'G1', 'G2', 'G3', 'INTERIM'):
                if g != 'none' and x[g]:
                    continue
                e = rows[(part, sl)][g]
                if van:
                    e[2] += 1
                    continue
                e[0] += 1
                e[1] += y
                e[3].add(x['pair'])
                if y:
                    e[4].add(x['pair'])
order = ['fee<=50 liq>=250k', 'fee<=50 liq50-250k', 'fee55-95 liq>=250k', 'fee55-95 liq50-250k', 'fee55-95 liq20-50k',
         'fee100-125 liq>=250k', 'fee100-125 liq50-250k', 'fee100-125 liq20-50k', 'fee100-125 liq<20k', 'ALL liq>=20k', 'ALL']
for part in ('all', 'train', 'holdout'):
    print('==== part', part, '(hazard = P(terminal drain within 60 min) per position-hour; retained = share of points not flagged)')
    print('%-24s %-8s %7s %5s %6s %9s %8s %6s' % ('slice', 'guard', 'points', 'pairs', 'drains', 'hazard%/h', 'retained', 'van%'))
    for sl in order:
        if (part, sl) not in rows:
            continue
        base = rows[(part, sl)]['none'][0]
        for g in ('none', 'G1', 'G2', 'G3', 'INTERIM'):
            e = rows[(part, sl)][g]
            if not e[0] and not e[2]:
                print('%-24s %-8s %7d' % (sl, g, 0))
                continue
            print('%-24s %-8s %7d %5d %6d %9.2f %8.3f %6.1f' % (sl, g, e[0], len(e[3]), len(e[4]), 100 * e[1] / max(1, e[0]),
                                                           e[0] / max(1, base), 100 * e[2] / max(1, e[0] + e[2])))
        print()
