"""Step 1: build the past-only regime grid and the unfiltered base-strategy trades (saved for train analysis)."""
import os, pickle, sys, time, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f9lib as L
H = L.H

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
t = time.time()
series, meta = H.load()
g = L.build_grid(series, meta)
print('grid s', round(time.time() - t, 1), 'M', g['M'])
t = time.time()
L.add_tide(g, series, L.U_liquid)
print('tide s', round(time.time() - t, 1))
with open(os.path.join(HERE, 'grid.pkl'), 'wb') as fh:
    pickle.dump(g, fh)

# hourly print of the grid
cut = H.split_t(meta)
for m in range(0, g['M'], 30):
    T = g['g0'] + m * L.MIN
    row = {c: (round(g[c][m], 3) if g[c][m] == g[c][m] else None) for c in ('n', 'br15', 'br60', 'med15', 'med60', 'pc5', 'pc1h', 'bs', 'v5', 'v5z', 'sol', 'sol60', 'new60', 'tide', 'tide_n')}
    print(datetime.datetime.fromtimestamp(T / 1000, datetime.UTC).strftime('%H:%M'), 'H' if T >= cut else 'T', row)

EXITS = {
    'E1_-5/+10/60': dict(stop=-5, tp=10, hold_min=60),
    'E2_-15/+20/60': dict(stop=-15, tp=20, hold_min=60),
    'E3_hold30_nostop': dict(stop=-99, tp=None, hold_min=30),
}
BASES = {
    'RANDOM': L.sig_random(0.004),
    'MOMO': L.sig_momentum,
    'DIP': L.sig_dip,
}
allt = {}
for bn, sig in BASES.items():
    for en, kw in EXITS.items():
        t = time.time()
        tr = H.simulate(sig, cooldown_s=300, tag=bn + '|' + en, **kw)
        for x in tr:
            td = x['entry_t'] - L.MIN   # decision time lower bound -> regime row at or before the decision
            for c in ('n', 'br15', 'br60', 'med15', 'med60', 'pc5', 'pc1h', 'bs', 'v5z', 'sol60', 'new60', 'tide'):
                x['R_' + c] = L.at(g, c, td)
            x['hour_utc'] = datetime.datetime.fromtimestamp(x['entry_t'] / 1000, datetime.UTC).hour
        allt[(bn, en)] = tr
        print('sim', bn, en, round(time.time() - t, 1), 's')
        ev = H.evaluate(tr)
        print('   train  ', L.brief(ev['train']))
with open(os.path.join(HERE, 'base_trades.pkl'), 'wb') as fh:
    pickle.dump(allt, fh)
print('saved')
