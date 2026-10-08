"""Step (d): do the engine's own risk signals mark the rug families? (raw obs rows, read-only)

Engine signals recomputed exactly as backend/market_monitor.score_pair does from the stored fields:
  'unnatural buy imbalance'  buys5m / max(sells5m, 1) > 5            (score -6)
  'weak liquidity/MC'        liquidity / (marketCap or fdv) < 0.03   (score -10)
  'good liquidity/MC'        liquidity / (marketCap or fdv) >= 0.15  (score +9)
Stored per row: score, risk (=100-score), posture (SETUP/WAIT/SKIP), safe (safety.allowed), rej (rejection reasons).
Groups (ex-post membership, descriptive only):
  FAKE_FAMILY  pool ever had mcap >= $20M, liq/mcap < 2%, age < 14 d
  LP_FAMILY    pool ever had liq/mcap >= 1.0 with liq >= $20k
  OTHER_DRAINED  other pools with a terminal drain event
  OTHER        everything else with liq >= $50k at some point
"""
import collections, os, pickle, sqlite3, sys
from rugcommon import HERE, _series

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = os.path.dirname(HERE)
lab = {r['pair']: r for r in pickle.load(open(os.path.join(HERE, 'labels.pkl'), 'rb'))['rows']}
D14 = 14 * 1440
group = {}
for p, s in _series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    fake = lp = big = False
    for i in range(len(s['t'])):
        liq, mc, age = s['liq'][i], s['mcap'][i], s['age'][i]
        if liq >= 50_000:
            big = True
        if mc > 0 and liq > 0:
            if mc >= 20e6 and liq / mc < 0.02 and age < D14:
                fake = True
            if liq / mc >= 1.0 and liq >= 20_000:
                lp = True
    r = lab[p]
    drained = r['drain_t'] is not None and not all(r['events'][k][2] for k in r['kinds'])
    group[p] = 'FAKE_FAMILY' if fake else 'LP_FAMILY' if lp else 'OTHER_DRAINED' if drained else 'OTHER' if big else None
print('pools per group', collections.Counter(v for v in group.values() if v))
print('drained per group', collections.Counter(group[p] for p in group if group[p] and lab[p]['drain_t'] is not None))

con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'obs.sqlite3'), uri=True)
q = "SELECT pair, liq, mcap, fdv, b5, s5, score, risk, posture, safe, rej FROM o WHERE dex='pumpswap' AND quote_sol=1"
agg = collections.defaultdict(lambda: collections.Counter())
sums = collections.defaultdict(lambda: collections.defaultdict(float))
rej = collections.defaultdict(collections.Counter)
for pair, liq, mcap, fdv, b5, s5, score, risk, posture, safe, rj in con.execute(q):
    g = group.get(pair)
    if not g:
        continue
    a = agg[g]
    a['rows'] += 1
    mc = mcap if mcap else fdv
    if liq and mc:
        lmc = liq / mc
        a['weak_lmc'] += lmc < 0.03
        a['good_lmc'] += lmc >= 0.15
    if b5 is not None and s5 is not None:
        a['imbal'] += (b5 / max(s5, 1)) > 5
    a['posture_' + str(posture)] += 1
    a['safe'] += 1 if safe else 0
    if score is not None:
        sums[g]['score'] += score
        sums[g]['n_score'] += 1
    for tok in (rj or '').split('|'):
        if tok:
            rej[g][tok.split(':')[0][:50]] += 1
for g in ('FAKE_FAMILY', 'LP_FAMILY', 'OTHER_DRAINED', 'OTHER'):
    a = agg[g]
    n = a['rows']
    if not n:
        continue
    print('== %s rows %d' % (g, n))
    print('   unnatural buy imbalance (b5/s5>5): %.1f%% | weak liq/MC (<3%%): %.1f%% | GOOD liq/MC (>=15%%, +9 score): %.1f%%' % (
        100 * a['imbal'] / n, 100 * a['weak_lmc'] / n, 100 * a['good_lmc'] / n))
    print('   mean engine score %.1f | posture SETUP %.1f%% WAIT %.1f%% SKIP %.1f%% | safety.allowed %.1f%%' % (
        sums[g]['score'] / max(1, sums[g]['n_score']), 100 * a['posture_SETUP'] / n, 100 * a['posture_WAIT'] / n, 100 * a['posture_SKIP'] / n,
        100 * a['safe'] / n))
    print('   rejection reasons (row share):', ', '.join('%s %.2f%%' % (k, 100 * v / n) for k, v in rej[g].most_common(10)))
