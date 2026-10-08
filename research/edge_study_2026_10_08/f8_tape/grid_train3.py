"""TRAIN-ONLY: big-SELL dip-buy (contrarian) with on-chain entry and on-chain exit fills."""
import itertools, sys, time
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import sig as S
import tsim
import tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
EXITS = {
    'E1 -10/+25/30m': dict(stop=-10, tp=25, hold_min=30),
    'E2 -15/trail10>8/60m': dict(stop=-15, tp=None, trail_arm=10, trail=8, hold_min=60),
    'E3 -20/+40/60m': dict(stop=-20, tp=40, hold_min=60),
}
keep = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd', 'top_pair_share', 'exits')
t0 = time.time()
n_cfg = 0
for min_sol, min_age, (ename, ekw) in itertools.product((3.0, 5.0, 10.0), (0.0, 60.0), EXITS.items()):
    sig = S.whale_signal(min_sol=min_sol, min_age_min=min_age, min_liq=20_000, side=-1)
    tr = tsim.simulate(sig, pairs=PAIRS, t_to=cut, entry_mode='onchain', exit_mode='onchain', **ekw)
    trs = tsim.simulate(sig, pairs=PAIRS, t_to=cut, entry_mode='series', exit_mode='series', **ekw)
    n_cfg += 1
    tr = [x for x in tr if x['entry_t'] < cut]
    trs = [x for x in trs if x['entry_t'] < cut]
    sm = H.summarize(tr)
    sms = H.summarize(trs)
    prem = [x['entry_px_used'] / x['entry_px_series'] - 1 for x in tr if x['entry_px_series'] > 0]
    print('cfg%02d SELL>=%g age>=%g %s  onchain/series entry premium mean %.2f%%' % (n_cfg, min_sol, min_age, ename, 100 * sum(prem) / max(1, len(prem))))
    print('     onchain net50', {k: sm.get(k) for k in keep})
    print('     series  net50 mean %s med %s pf %s n %s' % (sms.get('mean_pct'), sms.get('median_pct'), sms.get('pf'), sms.get('n')))
    sys.stdout.flush()
print('configs', n_cfg, 'secs', round(time.time() - t0, 1))
