"""F3 grid 3 - TRAIN ONLY. Looser organic-growth variants (more trades). Counts toward configs_tried."""
import sys, time, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import harness as H
import f3lib as L
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
size_fn = lambda P: min(200.0, P('liq') * 0.005)
E1 = dict(stop=-15, tp=None, trail_arm=20, trail=15, hold_min=60)
CFGS = [
    ('G3a_U240_obs10|organic_loose(1.05/1.05/uw5)|E1',
     lambda P: L.young(P, 240) and L.observed_for(P, 600) and L.f3_rug_screen(P) and L.organic(P, 1.05, 1.05, 5), E1),
    ('G3b_U240_obs5|organic(1.10/1.10/uw5)|E1',
     lambda P: L.young(P, 240) and L.observed_for(P, 300) and L.f3_rug_screen(P) and L.organic(P), E1),
    ('G3c_U240_obs5|organic_loose(1.05/1.05/uw5)|E1',
     lambda P: L.young(P, 240) and L.observed_for(P, 300) and L.f3_rug_screen(P) and L.organic(P, 1.05, 1.05, 5), E1),
    ('G3d_U1440_obs10|organic(1.10/1.10/uw5)|E1',
     lambda P: L.young(P, 1440) and L.observed_for(P, 600) and L.f3_rug_screen(P) and L.organic(P), E1),
]
for n, (name, sig, ek) in enumerate(CFGS, 1):
    t0 = time.time()
    tr = H.simulate(sig, exit_fn=L.drain_exit(0.6), size_fn=size_fn, t_to=CUT, **ek)
    s50 = H.summarize(tr)
    s0 = H.summarize(tr, 'usd0')
    rob = L.robust_view(tr, ek['hold_min'])
    print('#%d %s secs=%.0f' % (n, name, time.time() - t0))
    print('   net50', json.dumps(L.brief(s50)))
    print('   net0 mean_pct', s0.get('mean_pct'), 'median', s0.get('median_pct'), '| robust', json.dumps(rob))
    for x in tr:
        print('      ', x['pair'][:8], (x['sym'] or '')[:10], 'net50 %.1f' % x['net50'], x['reason'], x['flag'], 'hold_min %.1f' % (x['hold_s'] / 60), 'mfe %.1f' % x['mfe'], 'fee', x['fee_bps'], 'liq %.0f' % x['liq'])
    sys.stdout.flush()
