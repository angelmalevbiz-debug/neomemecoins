"""F5 screen 2 (TRAIN ONLY): tape-verified flow quintiles + economically-motivated event screens."""
import sys, os, pickle
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
d = pickle.load(open(os.path.join(HERE, 'samples2.pkl'), 'rb'))
ALLROWS = d['rows']
rows = [r for r in ALLROWS if r['train'] and not r['rug']]
fin = lambda x: x is not None and x == x and abs(x) != float('inf')
SCREENS = 0


def stats(sub, key):
    xs = [r[key] for r in sub if fin(r.get(key))]
    if not xs:
        return None
    by = {}
    for r in sub:
        if fin(r.get(key)):
            by.setdefault(r['pair'], []).append(r[key])
    pm = sum(sum(v) / len(v) for v in by.values()) / len(by)
    top = max(len(v) for v in by.values()) / len(xs)
    return len(xs), len(by), sum(xs) / len(xs), pm, 100 * sum(1 for x in xs if x > 0) / len(xs), top


def line(label, sub):
    global SCREENS
    SCREENS += 1
    a, b, c = stats(sub, 'f900'), stats(sub, 'f1800'), stats(sub, 'f3600')
    mfe = sorted(r['mfe30'] for r in sub if fin(r['mfe30']))
    if not b:
        print('   %-34s n=0' % label)
        return
    print('   %-34s n=%5d pr=%4d top=%.2f | f15 %6.2f | f30 %6.2f (pairavg %6.2f, win %4.1f) | f60 %s | mfe30med %5.1f' % (
        label, b[0], b[1], b[5], a[2] if a else float('nan'), b[2], b[3], b[4],
        ('%6.2f (pairavg %6.2f)' % (c[2], c[3])) if c else '  na', mfe[len(mfe) // 2] if mfe else float('nan')))


def quint(U, f):
    vals = sorted(r[f] for r in U if fin(r.get(f)))
    if len(vals) < 150:
        print('  ', f, 'coverage too low', len(vals))
        return
    qs = [vals[int(len(vals) * p)] for p in (0.2, 0.4, 0.6, 0.8)]
    edges = [-float('inf')] + qs + [float('inf')]
    e2 = [edges[0]]
    for e in edges[1:]:
        if e > e2[-1]:
            e2.append(e)
    print('  --', f, 'cov', len(vals), 'edges', [round(x, 3) for x in qs])
    for a, b in zip(e2[:-1], e2[1:]):
        line('(%.3g, %.3g]' % (a, b), [r for r in U if fin(r.get(f)) and a < r[f] <= b])


TAPEF = ['tp_ub120', 'tp_ub300', 'tp_newb300', 'tp_net300', 'tp_net120', 'tp_bshare300', 'tp_ubshare300',
         'tp_top300', 'tp_breadth300', 'tp_uacc', 'tp_bsacc', 'tp_netsol900']
UNIV = {
    'TAPE_MIDHI_fee>50_liq>=20k': lambda r: r['fee'] > 50 and r['liq'] >= 20_000 and r.get('tp_live'),
    'TAPE_LOWFEE_big': lambda r: r['fee'] <= 50 and r['liq'] >= 250_000 and r.get('tp_live'),
}
for un, uf in UNIV.items():
    U = [r for r in rows if uf(r)]
    print('=== universe', un, 'samples', len(U), 'pairs', len({r['pair'] for r in U}))
    line('ALL', U)
    for f in TAPEF:
        quint(U, f)
    # DexScreener-only analogues on the SAME tape-live rows (does tape add anything?)
    for f in ('ba1h', 'bshare5', 'va1h', 'uw5'):
        quint(U, f)

print('\n##### EVENT SCREENS (train, non-rug, PumpSwap) #####')
established = lambda r: fin(r['age']) and r['age'] >= 1440
EV = {
    'E1 wake-up: age>=1d liq>=50k va6h>=3': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['va6h']) and r['va6h'] >= 3,
    'E1b wake-up: age>=1d liq>=50k va6h>=3 r300<2': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['va6h']) and r['va6h'] >= 3 and fin(r['r300']) and r['r300'] < 2,
    'E2 vol leads px: va1h>=2 |r300|<1 bshare5>=.6 liq>=50k': lambda r: r['liq'] >= 50_000 and fin(r['va1h']) and r['va1h'] >= 2 and fin(r['r300']) and abs(r['r300']) < 1 and fin(r['bshare5']) and r['bshare5'] >= 0.6,
    'E2b same, age>=1d': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['va1h']) and r['va1h'] >= 2 and fin(r['r300']) and abs(r['r300']) < 1 and fin(r['bshare5']) and r['bshare5'] >= 0.6,
    'E3 broad buyers DS: uw5>=20 rb5/uw5<.3 bshare5>=.55': lambda r: fin(r['uw5']) and r['uw5'] >= 20 and fin(r['rb5']) and r['rb5'] / r['uw5'] < 0.3 and fin(r['bshare5']) and r['bshare5'] >= 0.55 and r['liq'] >= 20_000,
    'E4 tape breadth: ub300>=10 top<.3 net300>0': lambda r: r.get('tp_live') and r['liq'] >= 20_000 and r['tp_ub300'] >= 10 and fin(r['tp_top300']) and r['tp_top300'] < 0.3 and r['tp_net300'] > 0,
    'E4b tape breadth + new buyers: ub300>=10 newb>=5 top<.3 net>0': lambda r: r.get('tp_live') and r['liq'] >= 20_000 and r['tp_ub300'] >= 10 and r['tp_newb300'] >= 5 and fin(r['tp_top300']) and r['tp_top300'] < 0.3 and r['tp_net300'] > 0,
    'E4c tape whale: top>=.6 net300>0 bsol300>=5': lambda r: r.get('tp_live') and r['liq'] >= 20_000 and fin(r['tp_top300']) and r['tp_top300'] >= 0.6 and r['tp_net300'] > 0 and r['tp_bsol300'] >= 5,
    'E5 capitulation: age>=1d liq>=50k bshare5<=.3 r300<=-5': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['bshare5']) and r['bshare5'] <= 0.3 and fin(r['r300']) and r['r300'] <= -5,
    'E5b capitulation tape: net300<0 us300>=10 r300<=-5 liq>=20k': lambda r: r.get('tp_live') and r['liq'] >= 20_000 and r['tp_net300'] < 0 and r['tp_us300'] >= 10 and fin(r['r300']) and r['r300'] <= -5,
    'E6 quiet established: age>=1d liq>=50k va6h<0.2': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['va6h']) and r['va6h'] < 0.2,
    'E7 ba1h>=3 and va1h>=3, age>=1d liq>=50k': lambda r: established(r) and r['liq'] >= 50_000 and fin(r['ba1h']) and r['ba1h'] >= 3 and fin(r['va1h']) and r['va1h'] >= 3,
}
for name, f in EV.items():
    sub = [r for r in rows if f(r)]
    print('==', name)
    for fb, fl in (('fee<=50', lambda r: r['fee'] <= 50), ('fee55-95', lambda r: 50 < r['fee'] <= 95), ('fee100+', lambda r: r['fee'] > 95)):
        line(fb, [r for r in sub if fl(r)])
print('screens (bins/events) evaluated', SCREENS)
