"""Descriptive splits over samples.pkl, TRAIN rows only (entries before the 60% split)."""
import sys, pickle, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = pickle.load(open((__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools/samples.pkl', 'rb'))
TR = [r for r in rows if r['train']]
print('rows', len(rows), 'train', len(TR))


def stat(rs, hz=3600, label=''):
    xs = [r for r in rs if r.get('f%d' % hz) is not None]
    if not xs:
        print('  %-40s n=0' % label)
        return
    f = sorted(r['f%d' % hz] for r in xs)
    c = [r['c%d' % hz] for r in xs if r.get('c%d' % hz) is not None]
    n = len(f)
    pairs = len({r['pair'] for r in xs})
    van = sum(1 for r in xs if r['van%d' % hz]) / n
    mfe = [r['mfe%d' % hz] for r in xs if r.get('mfe%d' % hz) is not None]
    hit10 = sum(1 for m in mfe if m >= 10) / max(1, len(mfe))
    hit30 = sum(1 for m in mfe if m >= 30) / max(1, len(mfe))
    drain = sum(1 for r in xs if r.get('lmin%d' % hz) is not None and r['lmin%d' % hz] < 0.2) / n
    # per-pair mean (equal weight per pair) to dampen clusters
    pp = {}
    for r in xs:
        pp.setdefault(r['pair'], []).append(r['f%d' % hz])
    ppm = sum(sum(v) / len(v) for v in pp.values()) / len(pp)
    print('  %-40s n=%5d pairs=%4d mean=%7.2f pairmean=%7.2f med=%7.2f p10=%7.2f p90=%7.2f win=%4.1f%% cpmean=%7.2f van=%3.0f%% drain=%3.0f%% mfe>=10:%3.0f%% mfe>=30:%3.0f%%' % (
        label, n, pairs, sum(f) / n, ppm, f[n // 2], f[int(n * .1)], f[int(n * .9)], 100 * sum(1 for x in f if x > 0) / n,
        sum(c) / max(1, len(c)), 100 * van, 100 * drain, 100 * hit10, 100 * hit30))


def by(rs, key, edges, hz=3600, name=None):
    print('-- by', name or key, 'h=%dm' % (hz // 60))
    for lo, hi in zip(edges[:-1], edges[1:]):
        sub = [r for r in rs if r[key] == r[key] and lo <= r[key] < hi]
        stat(sub, hz, '%s in [%s,%s)' % (name or key, lo, hi))


young = [r for r in TR if r['age'] < 240]
print('== young (age<240) train samples')
stat(young, 3600, 'all young')
by(young, 'age', [0, 3, 15, 30, 60, 120, 240])
by(young, 'obs_min', [0, 1, 3, 10, 30, 60, 1e9])
by(young, 'pts5', [0, 3, 6, 12, 30, 60, 1e9])
print('-- by src bit at sample (young)')
for b, nm in ((1, 'boosted'), (2, 'boosted-latest'), (4, 'latest'), (8, 'gecko-new'), (16, 'catalog')):
    stat([r for r in young if r['src'] & b], 3600, 'src has %s' % nm)
    stat([r for r in young if not r['src'] & b], 3600, 'src lacks %s' % nm)
stat([r for r in young if r['src'] == 8], 3600, 'src == gecko-new only')
by(young, 'liq', [0, 15e3, 25e3, 40e3, 60e3, 100e3, 250e3, 1e12])
by(young, 'fee', [0, 51, 96, 101, 126])
by(young, 'lm', [0, 0.05, 0.1, 0.2, 0.3, 0.5, 10])
print('-- rug / reuse')
stat([r for r in young if r['rug']], 3600, 'interim_rug_risk')
stat([r for r in young if not r['rug']], 3600, 'not rug')
stat([r for r in young if r['reuse'] >= 1], 3600, 'reuse>=1')
stat([r for r in young if r['mint_pump']], 3600, 'mint ends pump')
stat([r for r in young if not r['mint_pump']], 3600, 'mint not pump')
print('== mid-age 240-1440 train samples')
mid = [r for r in TR if 240 <= r['age'] <= 1440]
stat(mid, 3600, 'all mid')
by(mid, 'liq', [0, 15e3, 25e3, 40e3, 60e3, 100e3, 250e3, 1e12])
print('== persistent young: age<240, obs_min>=10, not rug')
pers = [r for r in young if r['obs_min'] >= 10 and not r['rug']]
stat(pers, 3600, 'persistent')
for hz in (300, 900, 1800, 3600):
    stat(pers, hz, 'persistent h=%d' % hz)
by(pers, 'liq5', [0, 0.9, 0.98, 1.02, 1.1, 1.3, 100])
by(pers, 'liq15', [0, 0.9, 0.98, 1.02, 1.1, 1.3, 100])
by(pers, 'p15', [0, 0.8, 0.95, 1.05, 1.2, 1.5, 100])
by(pers, 'p5', [0, 0.9, 0.98, 1.02, 1.1, 1.3, 100])
by(pers, 'pc1h', [-100, -30, -10, 0, 20, 100, 1e9])
by(pers, 'b5', [0, 10, 30, 60, 120, 1e9])
by(pers, 'uw5', [0, 5, 15, 30, 60, 1e9])
by(pers, 'lm', [0, 0.05, 0.1, 0.2, 0.3, 10])
by(pers, 'liq', [0, 15e3, 25e3, 40e3, 60e3, 100e3, 250e3, 1e12])
print('-- buy ratio b5/(b5+s5)')
for lo, hi in ((0, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)):
    stat([r for r in pers if (r['b5'] + r['s5']) > 0 and lo <= r['b5'] / (r['b5'] + r['s5']) < hi], 3600, 'br5 [%s,%s)' % (lo, hi))
