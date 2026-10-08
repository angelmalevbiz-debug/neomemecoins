"""F3 grid 4 - TRAIN ONLY. Exit variants for the G3d organic signal (wide trailing for fat tails). Counts toward configs_tried."""
import sys, time, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import harness as H
import f3lib as L
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
size_fn = lambda P: min(200.0, P('liq') * 0.005)
sig = lambda P: L.young(P, 1440) and L.observed_for(P, 600) and L.f3_rug_screen(P) and L.organic(P)
CFGS = [
    ('G4a_U1440_obs10|organic|E2_s25_trail40/25_h120', dict(stop=-25, tp=None, trail_arm=40, trail=25, hold_min=120)),
    ('G4b_U1440_obs10|organic|E3_s10_tp25_h30', dict(stop=-10, tp=25, hold_min=30)),
]
for n, (name, ek) in enumerate(CFGS, 1):
    t0 = time.time()
    tr = H.simulate(sig, exit_fn=L.drain_exit(0.6), size_fn=size_fn, t_to=CUT, **ek)
    s50 = H.summarize(tr)
    s0 = H.summarize(tr, 'usd0')
    rob = L.robust_view(tr, ek['hold_min'])
    print('#%d %s secs=%.0f' % (n, name, time.time() - t0))
    print('   net50', json.dumps(L.brief(s50)))
    print('   net0 mean_pct', s0.get('mean_pct'), 'median', s0.get('median_pct'), '| robust', json.dumps(rob))
    sys.stdout.flush()
