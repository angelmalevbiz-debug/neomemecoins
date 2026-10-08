import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from stats import desc, fmt, ok

rows = json.load(open(os.path.join(HERE, 'acct_rows.json'), encoding='utf-8'))
for r in rows:
    for k, v in list(r.items()):
        if v is None:
            r[k] = float('nan')
NAN = float('nan')
g = lambda r, k: r.get(k, NAN)


def fee_bucket(f):
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def liq_bucket(l):
    return 'liq<50k' if l < 50e3 else ('liq50-250k' if l < 250e3 else 'liq>=250k')


K = ('n', 'clusters', 'mean', 'median', 'p90', 'ci95')
print('== INSTANT RT error by bucket (booked - harness full; raw - model core). negative = harness conservative')
for keyf, name in [(lambda r: (fee_bucket(r['fee']), liq_bucket(r['liq'])), 'fee x liq'), (lambda r: r['sym'], 'token'),
                   (lambda r: 'N/L<0.1%' if r['n_over_l_pct'] < 0.1 else ('N/L0.1-0.2%' if r['n_over_l_pct'] < 0.2 else 'N/L>=0.2%'), 'N/L')]:
    print(' --', name)
    grp = collections.defaultdict(list)
    for r in rows:
        grp[keyf(r)].append(r)
    for k, rs in sorted(grp.items(), key=lambda kv: str(kv[0])):
        print('   %-32s booked-full %s' % (k, fmt(desc(rs, lambda r: g(r, 'rt_booked') - r['rt_model_full']), K)))
        print('   %-32s raw-core    %s | raw %s | core %s' % ('', fmt(desc(rs, lambda r: g(r, 'rt_real_raw') - r['rt_model_core']), ('n', 'mean', 'median', 'p90')),
                                                       fmt(desc(rs, lambda r: g(r, 'rt_real_raw')), ('mean',)), fmt(desc(rs, lambda r: r['rt_model_core']), ('mean',))))

print('\n== per-leg vs FRESH dataset marks (post-reset, mark age ~0.3-1 s): raw - model core (fee+impact)')
pr = [r for r in rows if ok(g(r, 'pb_real_raw_obs'))]
print(' n', len(pr), collections.Counter(r['sym'] for r in pr))
for lab, f in [('preflight BUY vs obs mark - core', lambda r: g(r, 'pb_real_raw_obs') - r['in_model_core']),
               ('fill BUY (entry_quote) vs obs - core', lambda r: g(r, 'in_real_raw_obs') - r['in_model_core']),
               ('preflight SELL vs obs mark - core', lambda r: g(r, 'ps_real_raw_obs') - g(r, 'ps_model_core')),
               ('EXIT sell vs obs mark - core', lambda r: g(r, 'out_real_raw_obs') - g(r, 'out_model_core')),
               ('  EXIT (EIE only)', lambda r: (g(r, 'out_real_raw_obs') - g(r, 'out_model_core')) if r['reason'] == 'EXIT_IMPACT_EMERGENCY' else NAN),
               ('  EXIT (STOP only)', lambda r: (g(r, 'out_real_raw_obs') - g(r, 'out_model_core')) if r['reason'] == 'STOP_LOSS_NET_TARGET' else NAN),
               ('mean(BUY,SELL preflight) - core', lambda r: (g(r, 'pb_real_raw_obs') - r['in_model_core'] + g(r, 'ps_real_raw_obs') - g(r, 'ps_model_core')) / 2),
               ('preflight buy obs age s', lambda r: g(r, 'pb_obs_age_s')),
               ('preflight sell obs age s', lambda r: g(r, 'ps_obs_age_s')),
               ('model core per leg', lambda r: r['in_model_core'])]:
    print('   %-38s %s' % (lab, fmt(desc(pr, f), K)))

print('\n== per-leg vs SNAPSHOT mark (all 117; snapshot ~8 s old): raw - model core')
for lab, f in [('preflight BUY vs snap - core', lambda r: g(r, 'pb_real_raw') - r['in_model_core']),
               ('preflight SELL vs snap - core', lambda r: g(r, 'ps_real_raw') - g(r, 'ps_model_core')),
               ('mean(BUY,SELL)', lambda r: (g(r, 'pb_real_raw') - r['in_model_core'] + g(r, 'ps_real_raw') - g(r, 'ps_model_core')) / 2)]:
    print('   %-38s %s' % (lab, fmt(desc(rows, f), K)))
    for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
        rs = [r for r in rows if fee_bucket(r['fee']) == fb]
        print('      %-35s %s' % (fb, fmt(desc(rs, f), K)))

print('\n== EXIT_IMPACT_EMERGENCY (V1) quote noise')
n = len(rows)
eie = [r for r in rows if r['reason'] == 'EXIT_IMPACT_EMERGENCY']
print(' share of closes: %d/%d = %.1f%%' % (len(eie), n, 100 * len(eie) / n))
print(' threshold %s' % fmt(desc(rows, lambda r: r['eie_threshold']), ('mean', 'median', 'p10', 'p90')))
print(' entry-time SELL quote already >= threshold: %.1f%% of all trades' % (100 * sum(r['eie_at_entry'] for r in rows) / n))
for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
    rs = [r for r in rows if fee_bucket(r['fee']) == fb]
    if rs:
        print('   %-12s n=%3d  EIE-at-entry %.1f%%  EIE closes %.1f%%  reported sell impact median %.3f  model sell core median %.3f' % (
            fb, len(rs), 100 * sum(r['eie_at_entry'] for r in rs) / len(rs),
            100 * sum(1 for r in rs if r['reason'] == 'EXIT_IMPACT_EMERGENCY') / len(rs),
            sorted(r['ps_jup_impact'] for r in rs)[len(rs) // 2], sorted(g(r, 'ps_model_core') for r in rs)[len(rs) // 2]))
print(' hold seconds EIE closes %s' % fmt(desc(eie, lambda r: r['hold_s']), ('n', 'mean', 'median', 'p10', 'p90')))
print(' hold seconds other closes %s' % fmt(desc([r for r in rows if r['reason'] != 'EXIT_IMPACT_EMERGENCY'], lambda r: r['hold_s']), ('n', 'mean', 'median', 'p10', 'p90')))
print(' Jupiter-reported impact minus honest model cost (fee+impact):')
print('   buy  (entry quote) %s' % fmt(desc(rows, lambda r: r['in_jup_impact'] - r['in_model_core']), K))
print('   sell (preflight)   %s' % fmt(desc(rows, lambda r: r['ps_jup_impact'] - g(r, 'ps_model_core')), K))
print('   reported impact minus model IMPACT only (2N/L): buy %s' % fmt(desc(rows, lambda r: r['in_jup_impact'] - 2 * r['N'] / r['liq'] * 100), ('mean', 'median')))
print('   reported sell impact vs reported buy impact (same instant): %s' % fmt(desc(rows, lambda r: r['ps_jup_impact'] - g(r, 'pb_jup_impact')), K))
print('   reported EXIT impact on EIE closes vs fresh-mark realised exit cost (post-reset):')
pe = [r for r in eie if ok(g(r, 'out_real_raw_obs'))]
print('     reported %s | realised vs mark %s | model core %s' % (fmt(desc(pe, lambda r: r['x_jup_impact']), ('n', 'mean', 'median')),
      fmt(desc(pe, lambda r: g(r, 'out_real_raw_obs')), ('mean', 'median')), fmt(desc(pe, lambda r: g(r, 'out_model_core')), ('mean', 'median'))))
print(' EIE closes: realised exit cost vs ledger mark minus model core %s' % fmt(desc(eie, lambda r: g(r, 'out_real_raw') - g(r, 'out_model_core')), K))
print(' EIE closes: price move entry->exit mark %s' % fmt(desc(eie, lambda r: r['move_pct']), K))
print(' EIE closes: booked pnl%% %s' % fmt(desc(eie, lambda r: r['pnl_booked_pct']), K))
print(' EIE closes: booked pnl minus entry-time booked RT (i.e. loss beyond the instant round trip) %s' % fmt(desc(eie, lambda r: r['pnl_booked_pct'] + g(r, 'rt_booked')), K))
