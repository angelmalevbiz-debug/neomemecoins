"""Check calibration.py against the account trades (in-sample for fee<=50) and show its effect on harness runs."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
DEEP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, DEEP)
import calibration as K
import costmodel as C
from stats import desc, fmt, ok

rows = json.load(open(os.path.join(HERE, 'acct_rows.json'), encoding='utf-8'))
NAN = float('nan')
for r in rows:
    for k, v in list(r.items()):
        if v is None:
            r[k] = NAN
g = lambda r, k: r.get(k, NAN)
KK = ('n', 'clusters', 'mean', 'median', 'p10', 'p90', 'ci95')

# 1) same-instant round trip: booked - calibrated harness RT (want <= 0 on average: calibrated harness not optimistic)
for r in rows:
    x = K.extra_bps_per_leg(r['fee'], r['liq'], r['N'])
    xc = K.extra_bps_per_leg(r['fee'], r['liq'], r['N'], conservative=False)
    r['rt_cal'] = C.roundtrip_pct(r['mark_in'], r['liq'], r['sol_usd'], r['fee'], r['N'], base_bps=20 + x)
    r['rt_cal_central'] = C.roundtrip_pct(r['mark_in'], r['liq'], r['sol_usd'], r['fee'], r['N'], base_bps=20 + xc)
print('same-instant RT: booked - harness        ', fmt(desc(rows, lambda r: g(r, 'rt_booked') - r['rt_model_full']), KK))
print('same-instant RT: booked - calibrated      ', fmt(desc(rows, lambda r: g(r, 'rt_booked') - r['rt_cal']), KK))
print('same-instant RT: booked - calibrated(cen) ', fmt(desc(rows, lambda r: g(r, 'rt_booked') - r['rt_cal_central']), KK))
print('same-instant RT: booked - harness+50      ', fmt(desc(rows, lambda r: g(r, 'rt_booked') - C.roundtrip_pct(r['mark_in'], r['liq'], r['sol_usd'], r['fee'], r['N'], base_bps=70)), KK))

# 2) whole trade, post-reset harness analogue (dataset marks): booked pnl - calibrated harness pnl (want ~0)
po = [r for r in rows if ok(g(r, 'pnl_obs_h0'))]
for r in po:
    x = K.extra_bps_per_leg(r['fee'], r['liq'], r['N'])
    e = K.exit_reason_extra_bps('STOP' if r['reason'].startswith('STOP') else r['reason'])
    r['pnl_cal'] = g(r, 'pnl_obs_h0') - (2 * x + e) / 100.0
print('\nwhole trade post-reset (n=%d): booked - harness0 %s' % (len(po), fmt(desc(po, lambda r: r['pnl_booked_pct'] - g(r, 'pnl_obs_h0')), KK)))
print('                               booked - harness50 %s' % fmt(desc(po, lambda r: r['pnl_booked_pct'] - g(r, 'pnl_obs_h50')), KK))
print('                               booked - calibrated %s' % fmt(desc(po, lambda r: r['pnl_booked_pct'] - r['pnl_cal']), KK))
for reason in ('EXIT_IMPACT_EMERGENCY', 'STOP_LOSS_NET_TARGET'):
    rs = [r for r in po if r['reason'] == reason]
    print('   %-24s booked - calibrated %s' % (reason, fmt(desc(rs, lambda r: r['pnl_booked_pct'] - r['pnl_cal']), KK)))
print('   (in-sample for fee<=50: the fee<=50 constant was fitted on these trades)')

# 3) effect on harness runs (first-order re-pricing of finished trades)
if '--sim' in sys.argv:
    import harness as H
    from smoke_common import cost_first, rnd
    keep = ('n', 'pairs', 'mean_pct', 'median_pct', 'sum_usd')
    for label, sig, kw in (
            ('random all PumpSwap -5/+10/60', rnd(0.002), {}),
            ('random cost-first + rug guard -5/+10/60', lambda P: cost_first(P) and not H.interim_rug_risk(P) and rnd(0.01)(P),
             {'size_fn': lambda P: min(200.0, P('liq') * 0.001)})):
        t0 = time.time()
        tr = H.simulate(sig, stop=-5, tp=10, hold_min=60, **kw)
        _, meta = H.load()
        cut = H.split_t(meta)
        for x in tr:
            x['netcal'] = K.calibrated_net_pct(x)
            x['usdcal'] = x['size'] * x['netcal'] / 100
        for part, sel in (('train', lambda x: x['entry_t'] < cut), ('holdout', lambda x: x['entry_t'] >= cut)):
            xs = [x for x in tr if sel(x)]
            if not xs:
                continue
            m = lambda k: sum(x[k] for x in xs) / len(xs)
            stops = sum(1 for x in xs if x['reason'] == 'STOP')
            print('%s [%s] n=%d stops=%d  mean net0 %+.3f  net50 %+.3f  calibrated %+.3f  (calibrated - net50 %+.3f)  secs %.0f' % (
                label, part, len(xs), stops, m('net0'), m('net50'), m('netcal'), m('netcal') - m('net50'), time.time() - t0))
