"""Synthesis step 0 (PRE-CUTOFF DATA ONLY): constants needed to freeze the forward pre-registration.

- CUT_T = end of the recorded dataset (meta t1).
- V_HOT threshold: 80th percentile of v5/liq over 1-per-minute samples in universe S1 (PumpSwap SOL, liq >= $20k,
  fee >= 55 bps, passes rug_guard_structural) using ONLY pre-cutoff data.
- Full pair ids for the 4 live PAPER positions reported by forensics (abbreviated prefixes), so the forward step can
  describe what happened to them after the cutoff. Prints abbreviated ids only.
Nothing after the cutoff is read here.
"""
import sys, os, math, json
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
LB = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/leaderboard'
sys.path.insert(0, LB)
import harness_final as H

HERE = os.path.dirname(os.path.abspath(__file__))
series, meta = H.load()
print('meta', meta)
vals = []
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts = s['t']
    last_m = None
    for i in range(len(ts)):
        m = int(ts[i] // 60000)
        if m == last_m:
            continue
        last_m = m
        P = H.Past(s, i)
        liq = P('liq')
        if not (liq >= 20_000):
            continue
        if P.fee_bps() < 55:
            continue
        if H.rug_guard_structural(P):
            continue
        v5 = P('v5')
        if v5 == v5 and liq > 0:
            vals.append(v5 / liq)
vals.sort()
q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
print('S1 pre-cutoff minute samples', len(vals), 'v5/liq q50 %.4f q80 %.4f q90 %.4f' % (q(.5), q(.8), q(.9)))

want = {'GhBPuDpt': 'WOSE', 'D2pVedgH': 'GOIF', 'HiPe6mDS': 'SARP', 'AGZjpu5v': 'VSOF', '69fyvgoT': 'swordinu'}
found = {}
for pair, s in series.items():
    for pre, sym in want.items():
        if pair.startswith(pre):
            found[pre] = pair
            k = len(s['t']) - 1
            print('%s %s sym=%s last_t=%d price=%.3g liq=%.0f mcap=%.0f age_min=%.0f fee=%s' % (
                sym, pre, s['sym'], s['t'][k], s['price'][k], s['liq'][k], s['mcap'][k], s['age'][k], H.fee_bps(s, k)))
with open(os.path.join(HERE, 'pre_constants.json'), 'w') as fh:
    json.dump({'CUT_T': meta['t1'], 'T0': meta['t0'], 'V_HOT_Q80': q(.8), 'n_samples': len(vals),
               'live_pairs': found}, fh, indent=1)
print('wrote pre_constants.json')
