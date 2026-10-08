"""TRAIN-ONLY simulate grid 2: oversold / pullback-in-runner variants, tail-keeping exits, stop below the low, small size."""
import json, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev')
from sigs import *

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
OUT = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f2_meanrev/grid2.jsonl'


def pc_min_signal(base_kwargs, pc6h_min=None, pc24_min=None):
    inner = dip_signal(**base_kwargs)

    def sig(P):
        if pc6h_min is not None and not (P('pc6h') >= pc6h_min):
            return False
        if pc24_min is not None and not (P('pc24') >= pc24_min):
            return False
        return inner(P)
    return sig


def below_low_exit(buffer=0.03):
    """Stop when price trades below the 5-min low seen at the decision point by `buffer`."""
    tr = Tracker()

    def ex(P, pos):
        if 'low' not in pos:
            back = P.history_len() - 1 - (pos['entry_i'] - 1)
            lo = None
            t_dec = P('t', back=back)
            k = back
            while k <= P.history_len() - 1:
                tk = P('t', back=k)
                if tk < t_dec - 300_000:
                    break
                v = P('price', back=k)
                lo = v if lo is None else min(lo, v)
                k += 1
            pos['low'] = lo
        if pos['low'] and P('price') < pos['low'] * (1 - buffer):
            return 'BELOW_LOW'
        return None
    return ex


SIGNALS = {
    'oversold6h_stab': (dip_signal, dict(pc6h_max=-15, mode='stab')),
    'oversold1h_any': (dip_signal, dict(pc1h_max=-12)),
    'oversold1h_stab': (dip_signal, dict(pc1h_max=-12, mode='stab')),
    'oversold24h_any': (dip_signal, dict(pc24_max=-30)),
    'runner_pullback_stab': (pc_min_signal, dict(base_kwargs=dict(mode='stab', dd_lo=-0.22, dd_hi=-0.10), pc6h_min=50)),
    'dip12_18_holdonly': (dip_signal, dict(dd_lo=-0.18, dd_hi=-0.12)),
}
EXITS = {
    'hold60_nostop': dict(tp=None, stop=-30, hold_min=60),
    'hold120_nostop': dict(tp=None, stop=-30, hold_min=120),
    'e20_25_90': dict(tp=20, stop=-25, hold_min=90),
    'trail15_8_25_120': dict(tp=None, trail_arm=15, trail=8, stop=-25, hold_min=120),
    'belowlow3_tp10_30': ('belowlow', dict(tp=10, stop=-20, hold_min=30)),
    'e15_20_60_size50': dict(tp=15, stop=-20, hold_min=60, notional=50.0),
}

n_cfg = 0
t0 = time.time()
with open(OUT, 'a', encoding='utf-8') as fh:
    for sname, (fac, sk) in SIGNALS.items():
        for ename, ek in EXITS.items():
            if isinstance(ek, tuple):
                kw = dict(ek[1])
                kw['exit_fn'] = below_low_exit(0.03)
            else:
                kw = dict(ek)
            tr = H.simulate(fac(**sk), t_to=CUT, **kw)
            n_cfg += 1
            st = H.summarize(tr)
            st0 = H.summarize(tr, 'usd0')
            d = {}
            for x in tr:
                d.setdefault(x['pair'], []).append(x['net50'])
            pm = round(sum(sum(v) / len(v) for v in d.values()) / len(d), 2) if d else None
            fh.write(json.dumps({'grid': 2, 'signal': sname, 'exit': ename, 'train': st, 'pairmean50': pm}) + '\n')
            if st['n']:
                print('%-22s %-18s n %4d pairs %3d win %5.1f mean %6.2f med %6.2f pm %6.2f pf %s ci %s top %.2f m0 %6.2f | %s' % (
                    sname, ename, st['n'], st['pairs'], st['win_rate'], st['mean_pct'], st['median_pct'], pm or 0,
                    st['pf'], st['ci95_mean_usd'], st['top_pair_share'], st0['mean_pct'], st['exits']), flush=True)
            else:
                print(sname, ename, 'n=0', flush=True)
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
