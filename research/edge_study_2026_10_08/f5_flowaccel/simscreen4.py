"""F5 simulation screen 4 (TRAIN entries only): tape-flow EXITS (sell-pressure exit, sell-into-frenzy) on the
tape-breadth entries, plus the DexScreener analogue with the same no-chase filter (tape vs DS, like for like)."""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from f5sig import H, fin, bshare5, ret, universe, rnd, tape_live, U, tb, TF
from simscreen3 import robust, nochase, ds  # simscreen3 runs on import? guarded by __name__ below

series, meta = H.load()
CUT = H.split_t(meta)
LOG = os.path.join(HERE, 'configs_log.jsonl')
EX = dict(stop=-10, tp=6, hold_min=30)


def sell_pressure_exit(P, pos):
    f = TF.flow(P.static('pair'), P.t, 60)
    if f is None:
        return None
    if f['us'] >= 3 and f['net_sol'] < 0 and f['ssol'] > 2 * f['bsol']:
        return 'FLOW_SELL'
    return None


def frenzy_exit(P, pos):
    if pos['net'] < 3:
        return None
    f = TF.flow(P.static('pair'), P.t, 120)
    if f is not None and f['ub'] >= 15:
        return 'FRENZY'
    return None


RUNS = [
    ('tb nochase + sell-pressure exit', tb(extra=nochase), dict(EX, exit_fn=sell_pressure_exit)),
    ('tb nochase + frenzy exit', tb(extra=nochase), dict(EX, exit_fn=frenzy_exit)),
    ('DS analog nochase', ds(nochase), EX),
]
n_cfg = 0
for name, sig, kw in RUNS:
    t0 = time.time()
    tr = H.simulate(sig, t_to=CUT, **kw)
    n_cfg += 1
    r = robust(tr)
    ex = {}
    for x in tr:
        ex[x['reason']] = ex.get(x['reason'], 0) + 1
    print('%-36s %s exits %s %.0fs' % (name, json.dumps(r), ex, time.time() - t0))
    sys.stdout.flush()
    with open(LOG, 'a') as fh:
        fh.write(json.dumps({'stage': 'simscreen4', 'name': name, 'part': 'train', 'robust': r}) + '\n')
tr = H.simulate(lambda P: U()(P) and nochase(P) and rnd(0.01, 'nc')(P), t_to=CUT, **EX)
print('RANDOM tape-live nochase', json.dumps(robust(tr)))
print('new configs counted:', n_cfg)
