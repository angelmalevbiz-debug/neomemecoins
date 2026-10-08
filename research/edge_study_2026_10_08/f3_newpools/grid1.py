"""F3 grid 1 - TRAIN ONLY (entries before the 60% split). Every configuration here counts toward configs_tried."""
import sys, time, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import harness as H
import f3lib as L
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
size_fn = lambda P: min(200.0, P('liq') * 0.005)

UNIVERSES = {
    'U30_any': lambda P: L.young(P, 30) and L.f3_rug_screen(P),
    'U120_obs10': lambda P: L.young(P, 120) and L.observed_for(P, 600) and L.f3_rug_screen(P),
    'U240_obs10': lambda P: L.young(P, 240) and L.observed_for(P, 600) and L.f3_rug_screen(P),
}
SIGNALS = {
    'rnd2pct': lambda P: H.hashed_coin(P.static('pair'), P.t, 0.02, 'f3'),
    'organic': lambda P: L.organic(P),
    'breakout': lambda P: L.breakout(P),
}
EXITS = {
    'E1_s15_trail20/15_h60': dict(stop=-15, tp=None, trail_arm=20, trail=15, hold_min=60),
    'E2_s25_trail40/25_h120': dict(stop=-25, tp=None, trail_arm=40, trail=25, hold_min=120),
    'E3_s10_tp25_h30': dict(stop=-10, tp=25, hold_min=30),
}
n_cfg = 0
results = []
for un, uf in [(k, v) for k, v in UNIVERSES.items() if len(sys.argv) < 2 or k in sys.argv[1:]]:
    for sn, sf in SIGNALS.items():
        for en, ek in EXITS.items():
            n_cfg += 1
            t0 = time.time()
            sig = (lambda uf, sf: (lambda P: uf(P) and sf(P)))(uf, sf)
            tr = H.simulate(sig, exit_fn=L.drain_exit(0.6), size_fn=size_fn, t_to=CUT, **ek)
            s50 = H.summarize(tr)
            s0 = H.summarize(tr, 'usd0')
            rob = L.robust_view(tr, ek['hold_min'])
            name = '%s|%s|%s' % (un, sn, en)
            results.append((name, s50, s0, rob))
            print('#%d %s secs=%.0f' % (n_cfg, name, time.time() - t0))
            print('   net50', json.dumps(L.brief(s50)))
            print('   net0 mean_pct', s0.get('mean_pct'), 'median', s0.get('median_pct'), '| robust', json.dumps(rob))
            sys.stdout.flush()
print('configs', n_cfg)
