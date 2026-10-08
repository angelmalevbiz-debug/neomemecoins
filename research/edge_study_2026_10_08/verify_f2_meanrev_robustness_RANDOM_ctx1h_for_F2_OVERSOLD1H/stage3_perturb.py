"""Stage 3: one-at-a-time threshold perturbations (+/-20% and +/-30%) of every parameter of the row.
For each perturbation: the row's own coin draw (salt f2r1) AND a 10-salt ensemble of the same random procedure
(its average is the expectation of the perturbed procedure; the single salt shows how the row itself moves).
Usage: stage3_perturb.py a|b   (a: context/universe/coin/cooldown, b: exits)"""
import time
from vcommon import *

part = sys.argv[1] if len(sys.argv) > 1 else 'a'
t0 = time.time()
H.load()
BASE = dict(pc1h_max=-12.0, liq_min=50_000.0, prob=0.005, cooldown_s=300, tp=20.0, stop=-25.0, hold_min=90.0)
GRID_A = {'pc1h_max': [-8.4, -9.6, -12.0, -14.4, -15.6], 'liq_min': [35_000.0, 40_000.0, 60_000.0, 65_000.0],
          'prob': [0.0035, 0.004, 0.006, 0.0065], 'cooldown_s': [210, 240, 360, 390]}
GRID_B = {'tp': [14.0, 16.0, 24.0, 26.0], 'stop': [-17.5, -20.0, -30.0, -32.5], 'hold_min': [63.0, 72.0, 108.0, 117.0]}
grid = GRID_A if part == 'a' else GRID_B
ENS = ['null%03d' % k for k in range(10)]


def run(params, salt):
    sig = make_rand(prob=params['prob'], salt=salt, pc1h_max=params['pc1h_max'], liq_min=params['liq_min'])
    return sim(sig, tp=params['tp'], stop=params['stop'], hold_min=params['hold_min'], cooldown_s=params['cooldown_s'])


rows = []
for name, vals in grid.items():
    for v in vals:
        p = dict(BASE)
        p[name] = v
        tr = run(p, 'f2r1')
        e = evaluate(tr)
        h, t = e['holdout'], e['train']
        ens_ho, ens_tr = [], []
        for salt in ENS:
            x = run(p, salt)
            a, b, _, _ = split(x)
            ens_tr.append(mean_usd(a))
            ens_ho.append(mean_usd(b))
        ens_ho_v = [z for z in ens_ho if z is not None]
        ens_tr_v = [z for z in ens_tr if z is not None]
        row = {'param': name, 'value': v, 'rel': round(v / BASE[name] - 1, 2),
               'row_holdout_n': h.get('n'), 'row_holdout_pairs': h.get('pairs'), 'row_holdout_mean_usd': h.get('mean_usd'),
               'row_holdout_ci': h.get('ci95_mean_usd'), 'row_train_mean_usd': t.get('mean_usd'),
               'row_top_pair_share': h.get('top_pair_share'),
               'row_gate_ex_baseline': bool(h.get('n', 0) >= 20 and (h.get('pairs') or 0) >= 8
                                            and (h.get('mean_usd') or -1) > 0 and (t.get('mean_usd') or -1) > 0
                                            and (h.get('top_pair_share') or 1) <= 0.35),
               'ens10_holdout_mean_usd': round(sum(ens_ho_v) / len(ens_ho_v), 3) if ens_ho_v else None,
               'ens10_holdout_share_pos': round(sum(1 for z in ens_ho_v if z > 0) / len(ens_ho_v), 2) if ens_ho_v else None,
               'ens10_train_mean_usd': round(sum(ens_tr_v) / len(ens_tr_v), 3) if ens_tr_v else None}
        rows.append(row)
        print('%-10s %9s (%+.0f%%) row: H n=%2s pr=%2s $%7s train $%7s gate_exB=%s | ens10 H $%7s (pos %s) train $%7s' % (
            name, v, 100 * row['rel'], row['row_holdout_n'], row['row_holdout_pairs'], row['row_holdout_mean_usd'],
            row['row_train_mean_usd'], row['row_gate_ex_baseline'], row['ens10_holdout_mean_usd'],
            row['ens10_holdout_share_pos'], row['ens10_train_mean_usd']), flush=True)

dump('stage3_perturb_%s.json' % part, {'rows': rows, 'simulate_calls': CALLS['simulate'],
                                       'seconds': round(time.time() - t0, 1)})
print('done', round(time.time() - t0, 1), 's, simulate calls', CALLS['simulate'])
