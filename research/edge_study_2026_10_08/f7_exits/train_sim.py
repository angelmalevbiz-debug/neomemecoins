"""TRAIN ONLY (entries before the 60% cut): exact H.simulate runs of the shortlisted configs + count-matched
random baselines, to confirm the path-lab ranking survives the real cooldown / one-position semantics."""
import sys, time, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f7_exits')
import harness as H
from common import utag, size_rule, sig_mom

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
keep = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')


def be_exit(arm, floor):
    def f(P, pos):
        return 'BE' if pos['peak'] >= arm and pos['net'] <= floor else None
    return f


def hi20_mom(P):
    return sig_mom(P) and utag(P) == 'hi20'


def hi20_rnd(prob, salt):
    return lambda P: utag(P) == 'hi20' and H.hashed_coin(P.static('pair'), P.t, prob, salt)


def cf_rnd(prob, salt):
    return lambda P: utag(P) == 'cf' and H.hashed_coin(P.static('pair'), P.t, prob, salt)


EX = {
    'TRAIL s-35 a40 w2 h240': dict(stop=-35, tp=None, trail_arm=40, trail=2, hold_min=240),
    'FIX s-35 t40 h240': dict(stop=-35, tp=40, hold_min=240),
    'BE s-35 t40 a20 b0.5 h120': dict(stop=-35, tp=40, hold_min=120, exit_fn=be_exit(20, 0.5)),
    'FIX sNone t5 h240': dict(stop=-1000, tp=5, hold_min=240),
}
t0 = time.time()
runs = [('mom/hi20', hi20_mom, e) for e in ('TRAIL s-35 a40 w2 h240', 'FIX s-35 t40 h240', 'BE s-35 t40 a20 b0.5 h120')]
runs += [('rnd/cf p0.01 salt f7', cf_rnd(0.01, 'f7'), 'FIX sNone t5 h240')]
for p in (0.002, 0.005):
    runs += [('rnd/hi20 p%s salt f7b' % p, hi20_rnd(p, 'f7b'), 'TRAIL s-35 a40 w2 h240')]
    runs += [('rnd/hi20 p%s salt f7b' % p, hi20_rnd(p, 'f7b'), 'BE s-35 t40 a20 b0.5 h120')]
runs += [('rnd/cf p0.01 salt f7b', cf_rnd(0.01, 'f7b'), 'FIX sNone t5 h240')]
for label, sig, ex in runs:
    tr = H.simulate(sig, size_fn=size_rule, t_to=cut, cooldown_s=300, **EX[ex])
    s = H.summarize(tr)
    s0 = H.summarize(tr, 'usd0')
    print('%-24s %-28s' % (label, ex), json.dumps({k: s.get(k) for k in keep}), '| model mean%', s0.get('mean_pct'),
          '| secs', round(time.time() - t0, 1))
