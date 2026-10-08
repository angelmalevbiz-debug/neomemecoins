"""Audit step 5: harness cost model vs engine paper_market_feasibility.modeled_roundtrip on raw rows; NaN/zero inputs."""
import array, math, sqlite3, sys, collections, importlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/audit')
H = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else 'harness')
import paper_market_feasibility as F

SOL = 'So11111111111111111111111111111111111111112'
con = sqlite3.connect('file:%s/obs.sqlite3?mode=ro' % H.DEEP, uri=True)
st = collections.Counter()
diffs = []
fee_mismatch = collections.Counter()
for price, pn, liq, mc, fdv, dex, qs in con.execute(
        "select price, pnative, liq, mcap, fdv, dex, quote_sol from o where (rowid % 23) = 0"):
    coin = {'priceUsd': price, 'priceNative': pn, 'liquidityUsd': liq, 'marketCap': mc, 'fdv': fdv,
            'dexId': dex, 'quoteTokenAddress': SOL if qs == 1 else 'X'}
    s = {'price': array.array('d', [H._f(price)]), 'pnative': array.array('d', [H._f(pn)]), 'liq': array.array('d', [H._f(liq)]),
         'mcap': array.array('d', [H._f(mc)]), 'dex': (dex or '').lower(), 'quote_sol': qs}
    if 'fdv' in H.NUM:
        s['fdv'] = array.array('d', [H._f(fdv)])
    st['rows'] += 1
    ef = F.pumpswap_fee_bps(coin)
    hf = H.fee_bps(s, 0)
    if ef != hf:
        why = 'mcap_missing_fdv_used' if not (H._f(mc) > 0) and H._f(fdv) > 0 else 'other'
        fee_mismatch[(why, (dex or '').lower())] += 1
    e = F.modeled_roundtrip(coin, 200.0)
    h = H.roundtrip_cost_pct(s, 0, 200.0)
    if e.get('status') != 'estimate':
        st['engine_unavailable'] += 1
        if h is not None:
            st['engine_unavailable_harness_has_value'] += 1
        continue
    if h is None:
        st['harness_none_engine_value'] += 1
        continue
    d = abs(-e['initial_pnl_pct'] - h)
    diffs.append(d)
    if d > 1e-9:
        st['cost_diff_gt_1e-9'] += 1
for k in sorted(st):
    print(k, st[k])
print('fee tier mismatches harness vs engine:', dict(fee_mismatch))
diffs.sort()
print('abs cost diff pct: max %.3g p99 %.3g' % (diffs[-1], diffs[int(.99 * len(diffs))]))

# NaN / non-positive inputs in the kept series (pumpswap SOL)
series, meta = H.load()
c = collections.Counter()
for p, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    for k in range(len(s['t'])):
        c['points'] += 1
        if not (s['price'][k] > 0):
            c['price_bad'] += 1
        if not (s['pnative'][k] > 0):
            c['pnative_bad'] += 1
        if not (s['liq'][k] > 0):
            c['liq_bad'] += 1
        if not (s['mcap'][k] > 0):
            c['mcap_bad'] += 1
print('kept pumpswap-SOL series points with missing/non-positive inputs:', dict(c))
# fee tier by market cap in SOL implied by sol_usd: sanity of sol_usd range
sus = []
for p, s in series.items():
    if s['dex'] == 'pumpswap' and s['quote_sol'] == 1:
        for k in range(0, len(s['t']), 50):
            v = H.sol_usd(s, k)
            if v > 0:
                sus.append(v)
sus.sort()
print('sol_usd implied p1 %.2f p50 %.2f p99 %.2f' % (sus[int(.01 * len(sus))], sus[len(sus) // 2], sus[int(.99 * len(sus))]))
con.close()
