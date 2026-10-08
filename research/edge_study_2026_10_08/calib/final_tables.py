"""Final tables for REPORT.md (engine-booked basis = raw Jupiter quote - 10 bps/leg engine buffer).
error = real - harness model (positive = harness too optimistic)."""
import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from stats import desc, ok

rows = json.load(open(os.path.join(HERE, 'acct_rows.json'), encoding='utf-8'))
NAN = float('nan')
for r in rows:
    for k, v in list(r.items()):
        if v is None:
            r[k] = NAN
g = lambda r, k: r.get(k, NAN)
BUF = 0.10  # engine 10 bps/leg buffer on top of the raw quote


def fb(f):
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def lb(l):
    return 'liq<50k' if l < 50e3 else ('liq50-250k' if l < 250e3 else 'liq>=250k')


def line(label, rs, f, cover=None):
    d = desc(rs, f)
    if d.get('n', 0) == 0:
        return '| %s | 0 | | | | | | |' % label
    cov = ''
    if cover is not None:
        xs = [f(r) for r in rs if ok(f(r))]
        cov = '%.0f%%' % (100 * sum(1 for x in xs if x <= cover) / len(xs))
    ci = '[%+.2f, %+.2f]' % d['ci95'] if ok(d['ci95'][0]) else 'n/a'
    return '| %s | %d | %d | %+.2f | %+.2f | %+.2f | %s | %s |' % (label, d['n'], d['clusters'], d['mean'], d['median'], d['p90'], ci, cov)


HDR = '| bucket | n | clusters | mean | median | p90 | CI95 mean | covered by stress |\n|---|---|---|---|---|---|---|---|'
groups = collections.OrderedDict()
groups['ALL'] = rows
for r in rows:
    groups.setdefault('%s / %s' % (fb(r['fee']), lb(r['liq'])), []).append(r)

print('### T1 same-instant round trip: engine-booked RT - harness RT (fee+2N/L+20bps/leg+network), % of notional')
print('stress covers if error <= +1.00 (2 x 50 bps)')
print(HDR)
for k, rs in groups.items():
    print(line(k, rs, lambda r: g(r, 'rt_booked') - r['rt_model_full'], 1.0))
print('\nraw Jupiter RT (no buffer/network/rent) - model core (fee+impact only):')
print(HDR)
for k, rs in groups.items():
    print(line(k, rs, lambda r: g(r, 'rt_real_raw') - r['rt_model_core'], 1.0))
print('\nmeans: real booked RT / harness RT / raw RT / core RT')
for k, rs in groups.items():
    m = lambda f: desc(rs, f).get('mean', NAN)
    print('  %-26s %.2f / %.2f / %.2f / %.2f' % (k, m(lambda r: g(r, 'rt_booked')), m(lambda r: r['rt_model_full']), m(lambda r: g(r, 'rt_real_raw')), m(lambda r: r['rt_model_core'])))

print('\n### T2 entry leg vs the mark: booked fill cost - harness entry cost (per leg), %; stress covers if <= +0.50')
print('snapshot mark (all trades, ~8 s old):')
print(HDR)
for k, rs in groups.items():
    print(line(k, rs, lambda r: g(r, 'in_real_booked') - r['in_model_full'], 0.5))
pr = [r for r in rows if ok(g(r, 'in_real_raw_obs'))]
print('fresh dataset mark (post-reset, median age 0.35 s):')
print(HDR)
print(line('fill quote', pr, lambda r: g(r, 'in_real_raw_obs') + BUF - r['in_model_full'], 0.5))
print(line('preflight buy', pr, lambda r: g(r, 'pb_real_raw_obs') + BUF - r['in_model_full'], 0.5))
print(line('preflight sell (same instant)', pr, lambda r: g(r, 'ps_real_raw_obs') + BUF - (g(r, 'ps_model_core') + 0.2), 0.5))

print('\n### T3 exit leg: booked exit cost - harness exit cost (per leg), %; stress covers if <= +0.50')
nonstale = [r for r in rows if r['reason'] != 'STALE_MARKET_EXIT']
print('vs ledger exit mark (excluding STALE_MARKET_EXIT, whose marks were stale by construction):')
print(HDR)
for reason in ('ALL non-stale', 'EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET', 'TAKE_PROFIT_10_NET', 'STALE_MARKET_EXIT (stale mark)'):
    rs = nonstale if reason == 'ALL non-stale' else [r for r in rows if r['reason'] == reason.split(' ')[0]]
    print(line(reason, rs, lambda r: g(r, 'out_real_raw') + BUF - g(r, 'out_model_full'), 0.5))
print('vs the NEXT dataset point after the exit quote (= the harness fill), post-reset:')
pn = [r for r in rows if ok(g(r, 'out_real_raw_next'))]
print(HDR)
for reason in ('ALL', 'EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET'):
    rs = pn if reason == 'ALL' else [r for r in pn if r['reason'] == reason]
    print(line(reason, rs, lambda r: g(r, 'out_real_raw_next') + BUF - g(r, 'out_model_full'), 0.5))

print('\n### T4 whole trade: booked pnl - harness pnl, % of notional (negative = harness optimistic)')
print('harness on the trade\'s own ledger marks (excl. STALE_MARKET_EXIT):')
print('| set | n | clusters | mean | median | p90 | CI95 mean | |\n|---|---|---|---|---|---|---|---|')
for reason in ('ALL non-stale', 'EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET'):
    rs = nonstale if reason == 'ALL non-stale' else [r for r in rows if r['reason'] == reason]
    print(line(reason + ' vs harness0', rs, lambda r: r['pnl_booked_pct'] - r['pnl_harness_pct']))
    print(line(reason + ' vs harness+50', rs, lambda r: r['pnl_booked_pct'] - r['pnl_harness50_pct']))
po = [r for r in rows if ok(g(r, 'pnl_obs_h0'))]
print('harness analogue on dataset marks (post-reset; entry = dataset mark at the entry quote, exit = next dataset point):')
for reason in ('ALL', 'EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET'):
    rs = po if reason == 'ALL' else [r for r in po if r['reason'] == reason]
    print(line(reason + ' vs harness0', rs, lambda r: r['pnl_booked_pct'] - g(r, 'pnl_obs_h0')))
    print(line(reason + ' vs harness+50', rs, lambda r: r['pnl_booked_pct'] - g(r, 'pnl_obs_h50')))
    print(line(reason + ' ex-rent vs harness0', rs, lambda r: r['pnl_booked_pct'] + 100 * r['rent_usd'] / r['N'] - g(r, 'pnl_obs_h0')))
print('\nfixed costs booked by the engine but not by the harness: rent %s USD, network/leg %s USD (harness 0.0001 SOL = %.3f USD)' % (
    sorted(set(round(r['rent_usd'], 3) for r in rows))[:6], sorted(set(round(r['net_fee_usd'], 3) for r in rows))[:6],
    0.0001 * sum(r['sol_usd'] for r in rows) / len(rows)))
print('notional range', min(r['N'] for r in rows), max(r['N'] for r in rows), 'N/L %% range %.3f..%.3f' % (min(r['n_over_l_pct'] for r in rows), max(r['n_over_l_pct'] for r in rows)))
