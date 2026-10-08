import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from stats import desc, fmt, ok

rows = json.load(open(os.path.join(HERE, 'acct_rows.json'), encoding='utf-8'))
NAN = float('nan')
for r in rows:
    for k, v in list(r.items()):
        if v is None:
            r[k] = NAN
g = lambda r, k: r.get(k, NAN)
K = ('n', 'clusters', 'mean', 'median', 'p10', 'p90', 'ci95')
print('plain 2-hop routes: eq %d pb %d ps %d xq %d of %d' % (tuple(sum(1 for r in rows if (tag + '_native_px') in r) for tag in ('eq', 'pb', 'ps', 'xq')) + (len(rows),)))
print('\n== SOL/USD: Jupiter USDC->SOL leg vs DexScreener-implied (price/priceNative), %')
print('  vs snapshot', fmt(desc(rows, lambda r: g(r, 'solusd_jup_vs_dex_pct')), K))
print('  vs fresh obs', fmt(desc(rows, lambda r: g(r, 'solusd_jup_vs_obs_pct')), K))
print('\n== token (pool) leg in SOL vs priceNative mark: buy premium / sell discount, minus fee tier (model core w/o USD leg)')
for lab, f in [('buy premium vs snap pn', lambda r: g(r, 'pb_native_cost_snap')),
               ('sell discount vs snap pn', lambda r: g(r, 'ps_native_cost_snap')),
               ('buy premium vs fresh obs pn', lambda r: g(r, 'pb_native_cost_obs')),
               ('sell discount vs fresh obs pn', lambda r: g(r, 'ps_native_cost_obs')),
               ('half spread (buy+sell)/2 snap', lambda r: (g(r, 'pb_native_cost_snap') + g(r, 'ps_native_cost_snap')) / 2),
               ('fee tier %', lambda r: r['fee'] / 100),
               ('half spread - fee - impact', lambda r: (g(r, 'pb_native_cost_snap') + g(r, 'ps_native_cost_snap')) / 2 - r['fee'] / 100 - 100 * r['N'] / r['liq']),
               ('mark offset (buy-sell)/2 snap', lambda r: (g(r, 'pb_native_cost_snap') - g(r, 'ps_native_cost_snap')) / 2),
               ('mark offset (buy-sell)/2 obs', lambda r: (g(r, 'pb_native_cost_obs') - g(r, 'ps_native_cost_obs')) / 2),
               ('EXIT sell discount vs fresh obs pn', lambda r: g(r, 'xq_native_cost_obs')),
               ]:
    print('  %-38s %s' % (lab, fmt(desc(rows, f), K)))
print('\n  by token: half spread - fee - impact | mark offset snap')
tok = collections.defaultdict(list)
for r in rows:
    tok[(r['sym'], r['fee'])].append(r)
for k, rs in sorted(tok.items(), key=lambda kv: -len(kv[1])):
    print('   %-20s %s | %s' % (k, fmt(desc(rs, lambda r: (g(r, 'pb_native_cost_snap') + g(r, 'ps_native_cost_snap')) / 2 - r['fee'] / 100 - 100 * r['N'] / r['liq']), ('n', 'mean', 'median')),
                             fmt(desc(rs, lambda r: (g(r, 'pb_native_cost_snap') - g(r, 'ps_native_cost_snap')) / 2), ('mean', 'median'))))
print('\n== harness-style exit fill: quote vs NEXT dataset point after the exit quote (post-reset)')
pr = [r for r in rows if ok(g(r, 'out_real_raw_next'))]
for reason in (None, 'EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET'):
    rs = [r for r in pr if reason is None or r['reason'] == reason]
    print('  %-24s next-point age %s' % (reason or 'ALL', fmt(desc(rs, lambda r: g(r, 'obs_next_age_s')), ('n', 'median', 'p90'))))
    print('  %-24s exit real vs next mark - model core %s' % ('', fmt(desc(rs, lambda r: g(r, 'out_real_raw_next') - g(r, 'out_model_core')), K)))
    print('  %-24s exit real vs concurrent mark - core %s' % ('', fmt(desc(rs, lambda r: g(r, 'out_real_raw_obs') - g(r, 'out_model_core')), K)))
