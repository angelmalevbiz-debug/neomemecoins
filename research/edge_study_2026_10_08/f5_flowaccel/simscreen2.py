"""F5 simulation screen 2 (TRAIN entries only). Tape-breadth threshold / sub-universe / exit variants, and
DexScreener-only analogues inside the same tape-live universe (does the tape add anything?)."""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import H, fin, va1h, bshare5, universe, rnd
import tapefeat as TF

series, meta = H.load()
CUT = H.split_t(meta)
LOG = os.path.join(HERE, 'configs_log.jsonl')
KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'pf', 'ci95_mean_usd', 'top_pair_share', 'trades_per_hour', 'exits')


def tape_live(P):
    a = TF.last_available_age_s(P.static('pair'), P.t)
    return a is not None and a <= 600


def U(fee_lo=52.5, fee_hi=125, liq_lo=20_000):
    return lambda P: universe(P, fee_lo, fee_hi, liq_lo) and tape_live(P)


def tb(ub_min=10, newb_min=5, top_max=0.3, net_pos=True, univ=None):
    univ = univ or U()

    def sig(P):
        if not univ(P):
            return False
        f = TF.flow(P.static('pair'), P.t, 300)
        if f['ub'] < ub_min or f['newb'] < newb_min:
            return False
        if not (fin(f['top_share']) and f['top_share'] < top_max):
            return False
        return f['net_sol'] > 0 if net_pos else True
    return sig


def ds_b5(P):
    return U()(P) and P('b5') >= 10 and fin(bshare5(P)) and bshare5(P) >= 0.55


def ds_uw(P):
    uw = P('hp_uw5')
    return U()(P) and fin(uw) and uw >= 10 and fin(bshare5(P)) and bshare5(P) >= 0.55


BASE_EXIT = dict(stop=-10, tp=5, hold_min=15)
RUNS = [
    ('A tb base fee52-95', tb(univ=U(52.5, 95)), BASE_EXIT),
    ('A tb base fee100-125', tb(univ=U(100, 125)), BASE_EXIT),
    ('A tb base liq>=50k', tb(univ=U(52.5, 125, 50_000)), BASE_EXIT),
    ('B ub5 newb3', tb(5, 3), BASE_EXIT),
    ('B ub20 newb10', tb(20, 10), BASE_EXIT),
    ('B top<0.5', tb(10, 5, 0.5), BASE_EXIT),
    ('B no newb', tb(10, 0), BASE_EXIT),
    ('B no net>0', tb(10, 5, 0.3, False), BASE_EXIT),
    ('C DS b5>=10 bshare>=.55 (tape-live univ)', ds_b5, BASE_EXIT),
    ('C DS hp_uw5>=10 bshare>=.55 (tape-live univ)', ds_uw, BASE_EXIT),
    ('D tb base S7/T5/H15', tb(), dict(stop=-7, tp=5, hold_min=15)),
    ('D tb base S10/T4/H10', tb(), dict(stop=-10, tp=4, hold_min=10)),
    ('D tb base S15/T6/H20', tb(), dict(stop=-15, tp=6, hold_min=20)),
    ('D tb base S10/T6/H30', tb(), dict(stop=-10, tp=6, hold_min=30)),
]
cut_mid = meta['t0'] + 0.3 * (meta['t1'] - meta['t0'])  # halves of TRAIN for a stability check
n_cfg = 0
for name, sig, kw in RUNS:
    t0 = time.time()
    tr = H.simulate(sig, t_to=CUT, tag=name, **kw)
    n_cfg += 1
    sm = H.summarize(tr)
    a = [x for x in tr if x['entry_t'] < cut_mid]
    b = [x for x in tr if x['entry_t'] >= cut_mid]
    ha = H.summarize(a).get('mean_pct'), len(a)
    hb = H.summarize(b).get('mean_pct'), len(b)
    print('%-46s %s | net0 %s | train halves %s %s | %.0fs' % (name, json.dumps({k: sm.get(k) for k in KEEP}), H.summarize(tr, 'usd0').get('mean_pct'), ha, hb, time.time() - t0))
    sys.stdout.flush()
    with open(LOG, 'a') as fh:
        fh.write(json.dumps({'stage': 'simscreen2', 'name': name, 'exit': kw, 'part': 'train', 'net50': {k: sm.get(k) for k in KEEP}}) + '\n')
# random baselines in the tape-live universe, base exit and alternatives
for name, kw in (('RANDOM tape-live S10/T5/H15', BASE_EXIT), ('RANDOM tape-live S10/T6/H30', dict(stop=-10, tp=6, hold_min=30))):
    tr = H.simulate(lambda P: U()(P) and rnd(0.01)(P), t_to=CUT, **kw)
    sm = H.summarize(tr)
    print('%-46s %s' % (name, json.dumps({k: sm.get(k) for k in KEEP})))
print('configs evaluated on train in this screen:', n_cfg)
