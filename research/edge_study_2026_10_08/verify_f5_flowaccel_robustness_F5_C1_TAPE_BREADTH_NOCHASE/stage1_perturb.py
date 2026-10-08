"""Stage 1: reproduce C1 under the final harness, then perturb every threshold one at a time (+/-20-30%) and jointly
(30 random draws, every threshold jittered uniformly within +/-25%). Saves trades to stage1.pkl."""
import sys, time, random, json
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_robustness_F5_C1_TAPE_BREADTH_NOCHASE')
import common_v as C

H, S = C.H, C.S
t0 = time.time()
C.H.load()
print('loaded in %.1fs' % (time.time() - t0), flush=True)

out = {'runs': {}}

# --- base: original signal vs replica vs leaderboard cache
t1 = time.time()
orig = H.simulate(S.CONFIGS[0]['signal'], **S.CONFIGS[0]['kwargs'])
rep = C.run(dict(C.BASE))
import pickle
lb = pickle.load(open(C.LB + '/trades/f5_flowaccel.pkl', 'rb'))
lbt = [r for r in lb['rows'] if r['name'] == 'F5_C1_TAPE_BREADTH_NOCHASE' and r['variant'] == 'orig'][0]['trades']
key = lambda x: (x['pair'], x['entry_t'], x['exit_t'], round(x['net50'], 9))
print('orig n', len(orig), 'replica n', len(rep), 'leaderboard n', len(lbt), '| orig==replica', [key(x) for x in orig] == [key(x) for x in rep],
      '| orig==leaderboard', [key(x) for x in orig] == [key(x) for x in lbt], '%.1fs' % (time.time() - t1), flush=True)
out['runs']['BASE'] = {'params': dict(C.BASE), 'trades': rep}

# --- one-at-a-time perturbations
OAT = {
    'ub': [7, 8, 12, 13],
    'newb': [4, 6, 7],
    'top': [0.21, 0.24, 0.36, 0.39],
    'nc_lo': [-1.5, -2.5],
    'nc_hi': [3.5, 4.0, 6.0, 6.5],
    'liq': [14_000, 16_000, 24_000, 26_000],
    'live': [420, 480, 720, 780],
    'win': [210, 240, 360, 390],
    'nc_win': [210, 240, 360, 390],
    'stop': [-7.0, -8.0, -12.0, -13.0],
    'tp': [4.2, 4.8, 7.2, 7.8],
    'hold': [21, 24, 36, 39],
    'cooldown': [210, 240, 360, 390],
    'size_frac': [0.0014, 0.0016, 0.0024, 0.0026],
}
for k, vals in OAT.items():
    for v in vals:
        p = dict(C.BASE)
        p[k] = v
        t1 = time.time()
        tr = C.run(p)
        tr_, ho_ = C.split(tr)
        name = '%s=%s' % (k, v)
        out['runs'][name] = {'params': p, 'trades': tr}
        print('%-18s n=%3d train %7.3f$ (n%3d) holdout %7.3f$ (n%3d, pairs %2d)  %.1fs' % (
            name, len(tr), C.mean([x['usd50'] for x in tr_]), len(tr_), C.mean([x['usd50'] for x in ho_]), len(ho_),
            len({x['pair'] for x in ho_}), time.time() - t1), flush=True)

# --- joint random perturbations (every threshold jittered +/-25% simultaneously)
rng = random.Random(20261008)
JIT = ['ub', 'newb', 'top', 'nc_lo', 'nc_hi', 'liq', 'live', 'win', 'nc_win', 'stop', 'tp', 'hold']
for d in range(30):
    p = dict(C.BASE)
    for k in JIT:
        v = C.BASE[k] * rng.uniform(0.75, 1.25)
        if k in ('ub', 'newb'):
            v = max(1, int(round(v)))
        p[k] = v
    t1 = time.time()
    tr = C.run(p)
    tr_, ho_ = C.split(tr)
    name = 'JOINT_%02d' % d
    out['runs'][name] = {'params': p, 'trades': tr}
    print('%-10s n=%3d train %7.3f$ (n%3d) holdout %7.3f$ (n%3d, pairs %2d) %.1fs | %s' % (
        name, len(tr), C.mean([x['usd50'] for x in tr_]), len(tr_), C.mean([x['usd50'] for x in ho_]), len(ho_),
        len({x['pair'] for x in ho_}), time.time() - t1,
        json.dumps({k: (round(p[k], 3) if isinstance(p[k], float) else p[k]) for k in JIT})), flush=True)

C.save('stage1.pkl', out)
print('done %.1fs' % (time.time() - t0))
