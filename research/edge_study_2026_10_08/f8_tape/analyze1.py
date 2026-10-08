"""TRAIN-only feature screens over samples.pkl (quintile sorts of forward stressed net returns)."""
import math, pickle, sys
from collections import defaultdict

rows = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape/samples.pkl', 'rb'))
tr = [r for r in rows if r['train'] and not r['rug']]
print('train samples (rug-screened)', len(tr), 'pairs', len(set(r['pair'] for r in tr)))


def bucket(r):
    fee, liq = r['fee'], r['liq']
    fb = 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125')
    lb = 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')
    return fb + '/' + lb


def stat(xs):
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return 'n0'
    xs.sort()
    n = len(xs)
    return 'n%5d mean %6.2f med %6.2f win %4.1f' % (n, sum(xs) / n, xs[n // 2], 100 * sum(1 for x in xs if x > 0) / n)


def pairmean(rs, key):
    d = defaultdict(list)
    for r in rs:
        v = r[key]
        if v is not None and v == v:
            d[r['pair']].append(v)
    if not d:
        return float('nan'), 0
    return sum(sum(v) / len(v) for v in d.values()) / len(d), len(d)


H = ('f50_300', 'f50_900', 'f50_1800', 'f50_3600')
print('== base rates by fee/liq bucket (train, covered, rug-screened), stressed net %')
bk = defaultdict(list)
for r in tr:
    bk[bucket(r)].append(r)
for b, rs in sorted(bk.items()):
    print(' ', b, 'pairs', len(set(r['pair'] for r in rs)))
    for h in H:
        pm, npairs = pairmean(rs, h)
        print('     ', h, stat([r[h] for r in rs]), 'pair-mean %.2f' % pm)
    hits = [r['hit'] for r in rs]
    print('      -5/+10 first-hit (model): TP %d STOP %d none %d' % (hits.count('TP'), hits.count('STOP'), hits.count(None)))


def quint(rs, feat, h, q=5):
    v = [r for r in rs if r[feat] is not None and r[feat] == r[feat] and r[h] is not None]
    if len(v) < 50:
        return None
    v.sort(key=lambda r: r[feat])
    out = []
    for k in range(q):
        part = v[k * len(v) // q:(k + 1) * len(v) // q]
        xs = [r[h] for r in part]
        pm, npairs = pairmean(part, h)
        out.append((part[0][feat], part[-1][feat], len(xs), sum(xs) / len(xs), sorted(xs)[len(xs) // 2], pm, npairs))
    return out


FEATS = ['net_60', 'net_300', 'bshare_60', 'bshare_300', 'ub_60', 'ub_300', 'mxb_60', 'mxb_300', 'nb_60', 'ns_60',
         'n_300', 'rep_300', 'tmom_60', 'tmom_300', 'tmom_900', 'dmom_60', 'dmom_300', 'pc5', 'pc1h', 'v5', 'b5', 'age', 'liq', 'fee']
# derived
for r in rows:
    su = r['su'] or 0
    r['netliq_300'] = (r['net_300'] * su / r['liq'] * 100) if (r['liq'] and su) else float('nan')
    r['netliq_60'] = (r['net_60'] * su / r['liq'] * 100) if (r['liq'] and su) else float('nan')
    r['mxbliq_300'] = (r['mxb_300'] * su / r['liq'] * 100) if (r['liq'] and su) else float('nan')
    r['dsb'] = r['b5'] / (r['b5'] + r['s5']) if (r['b5'] + r['s5']) > 0 else float('nan')
    r['v5liq'] = r['v5'] / r['liq'] if r['liq'] else float('nan')
FEATS += ['netliq_60', 'netliq_300', 'mxbliq_300', 'dsb', 'v5liq']
for uni_name, uni in (('ALL covered', tr), ('fee>=55 & liq>=50k', [r for r in tr if r['fee'] >= 55 and r['liq'] >= 50_000]),
                      ('fee100-125 any liq', [r for r in tr if r['fee'] >= 100])):
    print('\n######## universe', uni_name, 'n', len(uni), 'pairs', len(set(r['pair'] for r in uni)))
    for h in ('f50_300', 'f50_900', 'f50_3600'):
        print('=== horizon', h)
        for f in FEATS:
            qs = quint(uni, f, h)
            if not qs:
                continue
            print('  %-11s' % f, ' | '.join('[%.3g..%.3g] %5.2f/%5.2f pm%5.2f(%d)' % (a, b, m, md, pm, np_) for a, b, n, m, md, pm, np_ in qs))
