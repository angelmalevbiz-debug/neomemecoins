"""TRAIN-only broad buckets of dip candidates."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from ana_common import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cs = load_cands()
print('train candidates', len(cs))
EXITS = [(5, -8, 15), (10, -12, 30), (3, -5, 10), (15, -20, 60), (None, -30, 5), (None, -30, 15), (None, -30, 60)]
NCFG = 0


def show(label, sub):
    global NCFG
    print('--', label)
    for ex in EXITS:
        NCFG += 1
        print('   exit tp=%s stop=%s hold=%s  ' % ex, fmt(stats(sub, *ex)))


def liqb(c):
    l = c['liq']
    return '>=250k' if l >= 250e3 else ('100-250k' if l >= 100e3 else '50-100k')


def feeb(c):
    f = c['fee']
    return '<=50' if f <= 50 else ('55-95' if f <= 95 else '100-125')


rug = [c for c in cs if c['rug']]
ok = [c for c in cs if not c['rug']]
print('rug-flagged', len(rug), 'clean', len(ok))
show('ALL clean (any dd<=-6%)', ok)
show('RUG-flagged', rug)
lp_bad = [c for c in ok if nz(c['lp15'], 1) < 0.9]
show('clean but LP ratio15 < 0.9 (liquidity pulled beyond price-implied)', lp_bad)
ok2 = [c for c in ok if nz(c['lp15'], 1) >= 0.9]
for lb in ('50-100k', '100-250k', '>=250k'):
    for fb in ('<=50', '55-95', '100-125'):
        sub = [c for c in ok2 if liqb(c) == lb and feeb(c) == fb]
        if len(sub) >= 30:
            show('liq %s fee %s' % (lb, fb), sub)
print('configs counted', NCFG)
