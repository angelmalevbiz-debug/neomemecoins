import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from stats import desc, fmt, ok

rows = json.load(open(os.path.join(HERE, 'acct_rows.json'), encoding='utf-8'))
for r in rows:
    for k, v in list(r.items()):
        if v is None:
            r[k] = float('nan')

print('== per token')
tok = collections.defaultdict(list)
for r in rows:
    tok[(r['sym'], r['pair8'])].append(r)
for k, rs in sorted(tok.items(), key=lambda kv: -len(kv[1])):
    print('%-12s %s n=%3d fee=%s liq=%.0f..%.0f N=%s mcap=%.2gM accts=%s reasons=%s' % (
        k[0], k[1], len(rs), sorted(set(r['fee'] for r in rs)), min(r['liq'] for r in rs), max(r['liq'] for r in rs),
        sorted(set(round(r['N']) for r in rs)), sum(r['mcap'] for r in rs) / len(rs) / 1e6,
        dict(collections.Counter(r['acct'] for r in rs)), dict(collections.Counter(r['reason'] for r in rs))))

print('\n== snapshot age at entry quote (ms)', fmt(desc(rows, lambda r: r['snap_age_ms'])))
print('== mark_in vs snapshot price %', fmt(desc(rows, lambda r: 100 * (r['mark_in'] / r['snap_price'] - 1))))

print('\n== ENTRY LEG (cost %, + = cost)')
for lab, f in [('real raw (Jupiter outAmount vs mark)', lambda r: r.get('in_real_raw')),
               ('real booked (raw + 10bps buffer)', lambda r: r.get('in_real_booked')),
               ('model core (fee+2N/L)', lambda r: r['in_model_core']),
               ('model full (+20bps)', lambda r: r['in_model_full']),
               ('real raw - model core', lambda r: r.get('in_real_raw') - r['in_model_core']),
               ('real booked - model full', lambda r: r.get('in_real_booked') - r['in_model_full']),
               ('preflight buy raw vs same mark', lambda r: r.get('pb_real_raw')),
               ('Jupiter-reported buy impact %', lambda r: r.get('in_jup_impact')),
               ]:
    print('  %-40s %s' % (lab, fmt(desc(rows, f))))

print('\n== INSTANT ROUND TRIP (preflight buy -> sell, mark-free)')
for lab, f in [('real raw (no buffer/network/rent)', lambda r: r.get('rt_real_raw')),
               ('engine booked (entry_roundtrip_pnl)', lambda r: r.get('rt_booked')),
               ('engine worst-case (slippage floor)', lambda r: r.get('rt_worst_booked')),
               ('model core (fees+impact)', lambda r: r['rt_model_core']),
               ('model full (harness RT)', lambda r: r['rt_model_full']),
               ('real raw - model core', lambda r: r.get('rt_real_raw') - r['rt_model_core']),
               ('booked - model full', lambda r: r.get('rt_booked') - r['rt_model_full']),
               ('booked ex-rent - model full', lambda r: r.get('rt_booked') - 100 * r['rent_usd'] / r['N'] - r['rt_model_full']),
               ('buy/sell implied price gap %', lambda r: r.get('buy_sell_gap_pct')),
               ('sell leg raw vs mark', lambda r: r.get('ps_real_raw')),
               ('Jupiter-reported sell impact %', lambda r: r.get('ps_jup_impact')),
               ('dt buy->sell quote ms', lambda r: r.get('rt_dt_ms')),
               ]:
    print('  %-40s %s' % (lab, fmt(desc(rows, f))))

print('\n== EXIT LEG')
for reason in [None, 'EXIT_IMPACT_EMERGENCY', 'STALE_MARKET_EXIT', 'STOP_LOSS_NET_TARGET', 'TAKE_PROFIT_10_NET']:
    rs = [r for r in rows if reason is None or r['reason'] == reason]
    print(' reason=%s n=%d' % (reason or 'ALL', len(rs)))
    for lab, f in [('real raw (vs ledger exit mark)', lambda r: r.get('out_real_raw')),
                   ('model core', lambda r: r.get('out_model_core')),
                   ('model full', lambda r: r.get('out_model_full')),
                   ('real raw - model full', lambda r: r.get('out_real_raw') - r.get('out_model_full')),
                   ('Jupiter-reported exit impact %', lambda r: r.get('x_jup_impact')),
                   ('liq exit/entry', lambda r: r['liq_x'] / r['liq']),
                   ('price move entry->exit mark %', lambda r: r['move_pct']),
                   ]:
        print('    %-38s %s' % (lab, fmt(desc(rs, f))))

print('\n== WHOLE TRADE: booked pnl - harness pnl on the same marks (negative = harness optimistic)')
for reason in [None, 'EXIT_IMPACT_EMERGENCY', 'STALE_MARKET_EXIT', 'STOP_LOSS_NET_TARGET', 'TAKE_PROFIT_10_NET']:
    rs = [r for r in rows if reason is None or r['reason'] == reason]
    print('  %-24s booked %s' % (reason or 'ALL', fmt(desc(rs, lambda r: r['pnl_booked_pct']), ('n', 'mean', 'median'))))
    print('  %-24s harness0 %s' % ('', fmt(desc(rs, lambda r: r['pnl_harness_pct']), ('n', 'mean', 'median'))))
    print('  %-24s booked-harness0 %s' % ('', fmt(desc(rs, lambda r: r['pnl_booked_pct'] - r['pnl_harness_pct']))))
    print('  %-24s booked-harness50 %s' % ('', fmt(desc(rs, lambda r: r['pnl_booked_pct'] - r['pnl_harness50_pct']))))

print('\n== POST-RESET trades with dataset marks (what the harness would have used)')
pr = [r for r in rows if ok(r.get('obs_in_mark', float('nan')))]
print(' n', len(pr))
for lab, f in [('obs mark age at entry quote s', lambda r: r.get('obs_in_age_s')),
               ('entry real raw vs obs mark', lambda r: r.get('in_real_raw_obs')),
               ('entry real raw vs obs - model core', lambda r: r.get('in_real_raw_obs') - r['in_model_core']),
               ('obs mark age at exit quote s', lambda r: r.get('obs_x_age_s')),
               ('exit real raw vs obs mark', lambda r: r.get('out_real_raw_obs')),
               ('exit real raw vs obs - model full', lambda r: r.get('out_real_raw_obs') - r.get('out_model_full')),
               ('ledger exit mark vs obs mark %', lambda r: r.get('ledger_vs_obs_exit_mark_pct')),
               ]:
    print('   %-40s %s' % (lab, fmt(desc(pr, f))))
for r in pr:
    print('   %s %-9s %-22s in_raw=%.3f in_obs=%.3f out_raw=%.3f out_obs=%s xage=%s ledger_vs_obs=%s jupimp_x=%.3f' % (
        r['acct'], r['sym'], r['reason'], r.get('in_real_raw'), r.get('in_real_raw_obs'), r.get('out_real_raw'),
        '%.3f' % r['out_real_raw_obs'] if ok(r.get('out_real_raw_obs', float('nan'))) else '-',
        '%.0f' % r['obs_x_age_s'] if ok(r.get('obs_x_age_s', float('nan'))) else '-',
        '%.3f' % r['ledger_vs_obs_exit_mark_pct'] if ok(r.get('ledger_vs_obs_exit_mark_pct', float('nan'))) else '-',
        r['x_jup_impact']))
