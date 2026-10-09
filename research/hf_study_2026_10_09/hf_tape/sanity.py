"""sanity: TRAIN sample. (1) on-chain mid vs DexScreener price at the same moment, (2) gross mid move over 60 s,
(3) cost decomposition of a 60 s B2 trade at $100 by fee bucket (model, calibration, stress). PAPER research only."""
import json, math, os, random, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcommon as C
H = C.H
C.set_half_spread(json.load(open(os.path.join(C.HERE, 'half_spread.json'))))
import booklib as B

D = B.data()
TR = [p for p in D['points'] if p['t'] < D['cut'] and p['liq'] >= 50_000 and p['fee'] <= 95]
rnd = random.Random(5)
sample = rnd.sample(TR, 4000)


def q(xs, ps=(0.05, 0.25, 0.5, 0.75, 0.95)):
    xs = sorted(xs)
    return [round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) for p in ps] if xs else []


ratio, ratio_lag, gross, absg = [], [], [], []
cost = {'le50': [], '55_95': []}
for p in sample:
    s, d = C.series[p['pair']], C.TAPE[p['pair']]
    t = p['t']
    cm = C.chain_mid_sol(s, d, t + 2000)
    i = C.sidx(s, t + 2000)
    if cm is None or i < 0:
        continue
    ratio.append(100 * (cm[0] / s['pnative'][i] - 1))
    # DexScreener price 20 s later vs chain mid now (lag check)
    i2 = C.sidx(s, t + 22000)
    ratio_lag.append(100 * (cm[0] / s['pnative'][i2] - 1))
    cm2 = C.chain_mid_sol(s, d, t + 64000)
    if cm2:
        g = 100 * (cm2[0] / cm[0] - 1)
        gross.append(g)
        absg.append(abs(g))
    # cost decomposition: zero-move round trip at the entry point
    j = i
    r = C.legs(s, j, 100.0, 'HOLD', s['price'][j], j, s['price'][j])
    if r:
        cost[C.fee_bucket(p['fee'])].append((r['net0'], r['netcal'], r['net50']))
print('chain mid vs DexScreener pnative at same moment, %:', q(ratio), 'n', len(ratio))
print('chain mid now vs DexScreener 20 s later, %:', q(ratio_lag))
print('gross chain-mid move over 62 s, %:', q(gross), 'mean', round(sum(gross) / len(gross), 3), 'mean |g|',
      round(sum(absg) / len(absg), 3), 'share |g|>1%', round(sum(1 for x in absg if x > 1) / len(absg), 3))
for b, v in cost.items():
    if v:
        print('zero-move round trip at $100,', b, 'n', len(v), 'net0 %.3f netcal %.3f net50 %.3f' % tuple(
            sum(x[k] for x in v) / len(v) for k in range(3)))
