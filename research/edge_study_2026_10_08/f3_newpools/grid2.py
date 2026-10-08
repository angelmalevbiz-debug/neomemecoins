"""F3 grid 2 - TRAIN ONLY. Alternative early-life hypotheses. Every configuration counts toward configs_tried."""
import sys, time, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f3_newpools')
import harness as H
import f3lib as L
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
CUT = H.split_t(meta)
size_fn = lambda P: min(200.0, P('liq') * 0.005)
R = L.ratio


def grown_survivor(P):
    """Age < 4 h, in the feed >= 30 min, strict screen, liquidity +20% and price +30% over 30 min, >= 10 unique buyers 5m."""
    if not (L.young(P, 240) and L.observed_for(P, 1800) and L.f3_rug_screen(P)):
        return False
    return R(P('liq'), P.ago('liq', 1800)) >= 1.2 and R(P('price'), P.ago('price', 1800)) >= 1.3 and P('hp_uw5') >= 10


def runner_dip(P):
    """Age < 4 h, in the feed >= 20 min, strict screen; ran >= 1.5x (max/min over 20 min), now 20-40% off the high,
    liquidity intact (screen), buyers outnumber sellers over 5 min."""
    if not (L.young(P, 240) and L.observed_for(P, 1200) and L.f3_rug_screen(P)):
        return False
    w = P.window('price', 1200)
    if len(w) < 10:
        return False
    hi, lo = max(w), min(w)
    if not (lo > 0 and hi / lo >= 1.5):
        return False
    x = P('price') / hi
    return 0.6 <= x <= 0.8 and P('b5') > P('s5')


def grad_scalp(P):
    """First 5 minutes after graduation, strict screen, active buying."""
    if not (L.young(P, 5) and L.f3_rug_screen(P)):
        return False
    return P('b5') >= 20 and P('hp_uw5') >= 10 and P('pc5') > 0


CFGS = [
    ('G2a_grown_survivor|E1', grown_survivor, dict(stop=-15, tp=None, trail_arm=20, trail=15, hold_min=60)),
    ('G2a_grown_survivor|E2', grown_survivor, dict(stop=-25, tp=None, trail_arm=40, trail=25, hold_min=120)),
    ('G2b_runner_dip|E1', runner_dip, dict(stop=-15, tp=None, trail_arm=20, trail=15, hold_min=60)),
    ('G2b_runner_dip|E2', runner_dip, dict(stop=-25, tp=None, trail_arm=40, trail=25, hold_min=120)),
    ('G2c_grad_scalp|S8_TP15_H10', grad_scalp, dict(stop=-8, tp=15, hold_min=10)),
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
