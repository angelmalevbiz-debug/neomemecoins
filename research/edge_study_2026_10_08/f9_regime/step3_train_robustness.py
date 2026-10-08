"""Step 3 (TRAIN ONLY): in-signal regime filters, threshold neighbourhoods, hour/pair concentration,
and larger/lower-cost universes for a risk-on filter. Simulations stop taking entries at the split."""
import os, pickle, sys, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f9lib as L
H = L.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
with open(os.path.join(HERE, 'grid.pkl'), 'rb') as fh:
    g = pickle.load(fh)
configs = 0


def line(tr):
    if not tr:
        return 'n=0'
    xs = [x['net50'] for x in tr]
    hrs = {}
    for x in tr:
        hrs.setdefault(int((x['entry_t'] - meta['t0']) // 3.6e6), []).append(x['net50'])
    # leave-one-hour-out min / max mean
    loo = []
    for h in hrs:
        rest = [v for hh, vv in hrs.items() if hh != h for v in vv]
        if rest:
            loo.append(sum(rest) / len(rest))
    cnt = {}
    for x in tr:
        cnt[x['pair']] = cnt.get(x['pair'], 0) + 1
    sm = H.summarize(tr)
    return ('n=%3d pairs=%2d hrs=%2d mean50=%6.2f med=%6.2f win=%4.1f pf=%s top=%.2f ci=%s LOHO[min=%.2f max=%.2f] exits=%s'
            % (len(tr), len(cnt), len(hrs), sum(xs) / len(xs), sm['median_pct'], sm['win_rate'], sm['pf'],
               max(cnt.values()) / len(tr), sm['ci95_mean_usd'], min(loo) if loo else float('nan'), max(loo) if loo else float('nan'), sm['exits']))


def run(name, sig, **kw):
    global configs
    configs += 1
    tr = H.simulate(sig, t_to=cut, **kw)
    print('  %-48s %s' % (name, line(tr)))
    return tr


def filt(base, col, lo=None, hi=None):
    def f(P):
        if not base(P):
            return False
        v = L.at(g, col, P.t)
        if v != v:
            return False
        if lo is not None and v < lo:
            return False
        if hi is not None and v >= hi:
            return False
        return True
    return f


E2 = dict(stop=-15, tp=20, hold_min=60)
E3 = dict(stop=-99, tp=None, hold_min=30)
print('== DIP with contrarian market filters (TRAIN)')
for en, kw in (('E2', E2), ('E3', E3)):
    run('DIP %s unfiltered' % en, L.sig_dip, **kw)
    for th in (0.3, 0.0, -0.1, -0.2, -0.3):
        run('DIP %s med15<%.1f' % (en, th), filt(L.sig_dip, 'med15', hi=th), **kw)
    for th in (0.5, 0.45, 0.4):
        run('DIP %s br15<%.2f' % (en, th), filt(L.sig_dip, 'br15', hi=th), **kw)
print('== MOMO with contrarian market filters (TRAIN)')
for en, kw in (('E2', E2), ('E3', E3)):
    run('MOMO %s unfiltered' % en, L.sig_momentum, **kw)
    for th in (0.0, -0.1, -0.2):
        run('MOMO %s med15<%.1f' % (en, th), filt(L.sig_momentum, 'med15', hi=th), **kw)
    for th in (-3.0, -4.5):
        run('MOMO %s tide<%.1f' % (en, th), filt(L.sig_momentum, 'tide', hi=th), **kw)
print('== RANDOM with contrarian market filters (TRAIN)')
for en, kw in (('E2', E2), ('E3', E3)):
    run('RANDOM %s unfiltered' % en, L.sig_random(0.004), **kw)
    for th in (0.0, -0.2):
        run('RANDOM %s med15<%.1f' % (en, th), filt(L.sig_random(0.004), 'med15', hi=th), **kw)


# larger / lower-cost universes for a risk-on filter (big pools drift with the market?)
def U_big(fee_max, liq_min, rt_max):
    def u(P):
        liq = P('liq')
        if not (liq >= liq_min) or P.fee_bps() > fee_max:
            return False
        if H.interim_rug_risk(P):
            return False
        rt = P.rt_cost_pct(min(200.0, liq * 0.001))
        return rt is not None and rt <= rt_max
    return u


print('== big/low-cost universes: pairs and random baselines (TRAIN), hold 60 no stop / E1')
E1 = dict(stop=-5, tp=10, hold_min=60)
H60 = dict(stop=-99, tp=None, hold_min=60)
for uname, U in (('lowcost fee<=50 liq>=250k rt<=1.2', U_big(50, 250e3, 1.2)),
                 ('fee<=95 liq>=250k rt<=2.5', U_big(95, 250e3, 2.5)),
                 ('any fee liq>=250k rt<=3.5', U_big(125, 250e3, 3.5)),
                 ('fee<=50 liq>=100k rt<=1.5', U_big(50, 100e3, 1.5))):
    rs = lambda P, U=U: U(P) and H.hashed_coin(P.static('pair'), P.t, 0.01, 'big')
    for en, kw in (('H60', H60), ('E1', E1)):
        run('RND[%s] %s' % (uname, en), rs, **kw)
        for col, lo in (('br60', 0.6), ('med60', 1.0), ('pc1h', 1.6)):
            run('RND[%s] %s %s>=%.2f' % (uname, en, col, lo), filt(rs, col, lo=lo), **kw)
print('configs_tried step3:', configs)
