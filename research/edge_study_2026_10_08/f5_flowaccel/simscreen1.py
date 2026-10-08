"""F5 simulation screen 1 (TRAIN entries only: t_to = split). Signals x exit sets; random baselines in the same universe."""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import H, fin, va1h, va6h, ba1h, bshare5, ret, universe, rnd
import tapefeat as TF

series, meta = H.load()
CUT = H.split_t(meta)
LOG = os.path.join(HERE, 'configs_log.jsonl')


def hi_mid(P):
    return universe(P, 100, 125, 50_000, 250_000)


def hi_20k(P):
    return universe(P, 100, 125, 20_000)


def mid_50k(P):
    return universe(P, 52.5, 95, 50_000)


def s_spike_hi(P):
    if not hi_mid(P):
        return False
    v = va1h(P)
    return fin(v) and v >= 1.4


def s_broad_ds(P):
    if not hi_20k(P):
        return False
    uw, rb, bs = P('hp_uw5'), P('hp_rb5'), bshare5(P)
    return fin(uw) and uw >= 20 and fin(rb) and rb / uw < 0.3 and fin(bs) and bs >= 0.55


def tape_breadth(P, ub_min=10, newb_min=5, top_max=0.3):
    age = TF.last_available_age_s(P.static('pair'), P.t)
    if age is None or age > 600:
        return False
    f = TF.flow(P.static('pair'), P.t, 300)
    return f['ub'] >= ub_min and f['newb'] >= newb_min and fin(f['top_share']) and f['top_share'] < top_max and f['net_sol'] > 0


def s_tape_breadth_mid(P):
    return universe(P, 52.5, 125, 20_000) and tape_breadth(P)


def s_wake_mid(P):
    if not mid_50k(P):
        return False
    a = P('age')
    v = va6h(P)
    return fin(a) and a >= 1440 and fin(v) and v >= 3


SIGNALS = {
    'spike_hi50-250k_va1h>=1.4': (s_spike_hi, lambda P: hi_mid(P)),
    'broad_ds_hi20k_uw5>=20': (s_broad_ds, lambda P: hi_20k(P)),
    'tape_breadth_fee>=52_liq20k': (s_tape_breadth_mid, lambda P: universe(P, 52.5, 125, 20_000) and TF.last_available_age_s(P.static('pair'), P.t) is not None and TF.last_available_age_s(P.static('pair'), P.t) <= 600),
    'wake_mid50k_va6h>=3_age1d': (s_wake_mid, lambda P: mid_50k(P) and fin(P('age')) and P('age') >= 1440),
}
EXITS = {
    'S5/T10/H60': dict(stop=-5, tp=10, hold_min=60),
    'S10/T8/H30': dict(stop=-10, tp=8, hold_min=30),
    'S20/T15/H30': dict(stop=-20, tp=15, hold_min=30),
    'S10/T5/H15': dict(stop=-10, tp=5, hold_min=15),
    'S10/trail8-4/H60': dict(stop=-10, tp=None, trail_arm=8, trail=4, hold_min=60),
}
KEEP = ('n', 'pairs', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'top_pair_share', 'trades_per_hour', 'exits')
n_cfg = 0
t00 = time.time()
for sname, (sig, univ) in SIGNALS.items():
    for ename, kw in EXITS.items():
        t0 = time.time()
        tr = H.simulate(sig, t_to=CUT, tag=sname, **kw)
        n_cfg += 1
        sm = H.summarize(tr)
        sm0 = H.summarize(tr, 'usd0')
        n = len(tr)
        # random baseline in the same universe with similar trade count
        rb = None
        if n:
            # estimate universe point count once per signal for the random probability
            pass
        print('%-30s %-18s net50 %s | net0 mean %s | %.0fs' % (sname, ename, json.dumps({k: sm.get(k) for k in KEEP}), sm0.get('mean_pct'), time.time() - t0))
        with open(LOG, 'a') as fh:
            fh.write(json.dumps({'stage': 'simscreen1', 'signal': sname, 'exit': ename, 'part': 'train', 'net50': {k: sm.get(k) for k in KEEP}, 'net0_mean': sm0.get('mean_pct')}) + '\n')
    sys.stdout.flush()
# random baselines per universe with the same exit sets (train)
for sname, (sig, univ) in SIGNALS.items():
    # count universe points to set the probability to ~ the signal's trade count
    for ename, kw in EXITS.items():
        if ename not in ('S5/T10/H60', 'S20/T15/H30', 'S10/T5/H15'):
            continue
        t0 = time.time()
        tr = H.simulate(lambda P: univ(P) and rnd(0.01)(P), t_to=CUT, tag='rnd_' + sname, **kw)
        sm = H.summarize(tr)
        print('RANDOM in %-25s %-18s net50 %s | %.0fs' % (sname, ename, json.dumps({k: sm.get(k) for k in KEEP}), time.time() - t0))
        sys.stdout.flush()
print('configs (signal x exit) evaluated on train:', n_cfg, 'total secs', round(time.time() - t00))
