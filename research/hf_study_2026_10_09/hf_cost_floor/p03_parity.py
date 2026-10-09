"""p03: parity of hfsim.path/price with H.simulate on per-trade outcomes (time exit and net bracket)."""
import bisect, os, sys, time, random
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfsim as S
H = S.H

rnd = random.Random(5)
pids = list(range(len(S.PAIRS)))
rnd.shuffle(pids)
sel = set(S.PAIRS[p] for p in pids[:120])
pid_of = {p: k for k, p in enumerate(S.PAIRS)}
t0 = time.time()
for label, kw, spec in (('time60', dict(stop=-1e9, tp=None, hold_min=1.0), ('time', 60)),
                        ('time300', dict(stop=-1e9, tp=None, hold_min=5.0), ('time', 300)),
                        ('net2_cap600', dict(stop=-2.0, tp=2.0, hold_min=10.0), ('net', 2.0, 600))):
    sig = lambda P: P('liq') >= 50_000 and H.hashed_coin(P.static('pair'), P.t, 0.01, 'par')
    tr = H.simulate(sig, notional=200.0, cooldown_s=0, pairs=sel, **kw)
    bad = 0
    worst = 0.0
    for x in tr:
        pid = pid_of[x['pair']]
        s = S.SER[pid]
        i = bisect.bisect_left(s['t'], x['decision_t'])
        y = S.price(pid, i, spec, 200.0)
        d = max(abs(x['net50'] - y['net50']), abs(x['net0'] - y['net0']), abs(x['netcal'] - y['netcal']))
        worst = max(worst, d)
        if d > 1e-9 or x['exit_t'] != y['exit_t'] or x['reason'] != y['reason'] or x['entry_t'] != y['entry_t']:
            bad += 1
            if bad <= 3:
                print('MISMATCH', label, x['reason'], y['reason'], x['net50'], y['net50'], x['exit_t'], y['exit_t'])
    print(label, 'harness trades', len(tr), 'mismatches', bad, 'max abs diff', worst, 'secs', round(time.time() - t0, 1),
          flush=True)
