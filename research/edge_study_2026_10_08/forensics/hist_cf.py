"""Historical closes of the three accounts (saved /state snapshots): exit forensics and a
harness counterfactual of the plain -5/+10/60 net geometry without EXIT_IMPACT_EMERGENCY.
Also checks the currently open positions (and the Lab's VSOF) against the interim rug screen."""
import sys, os, json, bisect, datetime
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
sys.path.insert(0, DEEP)
import harness as H
SCR = os.path.dirname(DEEP)
series, meta = H.load()


def ts(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime('%m-%d %H:%M:%S')


def counterfactual(pair, opened_at, notional, stop=-5.0, tp=10.0, hold_min=60.0):
    s = series.get(pair)
    if s is None:
        return None
    t = s['t']
    j = bisect.bisect_left(t, opened_at)
    if j >= len(t) - 1:
        return None
    f = H.entry_fill(s, j, notional)
    if f is None:
        return None
    qty, netfee = f
    for k in range(j + 1, len(t)):
        v = H.exit_value(s, k, qty)
        if v is None:
            continue
        net = 100 * (v - notional - netfee) / notional
        why = 'STOP' if net <= stop else 'TP' if net >= tp else 'HOLD' if t[k] - t[j] >= hold_min * 60000 else None
        if why:
            e = min(k + 1, len(t) - 1)
            v2 = H.exit_value(s, e, qty)
            return round(100 * (v2 - notional - netfee) / notional, 2), why, round((t[e] - t[j]) / 60000, 1)
    return None, 'OPEN_AT_DATA_END', round((t[-1] - t[j]) / 60000, 1)


rows = []
for f, acct in (('state_8878.json', 'main'), ('state_18801.json', 'ofa_18801'), ('state_18802.json', 'cf_18802')):
    d = json.load(open(os.path.join(SCR, f), encoding='utf-8'))
    for h in d['history']:
        cf = counterfactual(h['pairAddress'], h['opened_at'], h['notional_usd'])
        model_now = None
        rows.append({'acct': acct, 'sym': h['symbol'], 'opened': ts(h['opened_at']), 'hold_s': round((h['closed_at'] - h['opened_at']) / 1000),
                     'exit': h['exit_reason'], 'pnl_pct': round(h['pnl_pct'], 2), 'pnl_usd': round(h['pnl_usd'], 2),
                     'market_move_pct': round(h.get('signal_pnl_pct') or 0, 2), 'entry_rt_pct': h.get('entry_roundtrip_pnl_pct'),
                     'entry_impact': round(h.get('entry_price_impact_pct') or 0, 3), 'exit_impact': round(h.get('exit_price_impact_pct') or 0, 3),
                     'liq_change_pct': round(100 * ((h.get('exit_liquidity_usd') or 0) / h['entry_liquidity_usd'] - 1), 2),
                     'cf_5_10_60': cf})
    for p in d['positions']:
        print('OPEN', acct, p['symbol'], p['pairAddress'][:8], ts(p['opened_at']))
for r in rows:
    print(json.dumps(r, ensure_ascii=False))
eie = [r for r in rows if r['exit'] == 'EXIT_IMPACT_EMERGENCY']
print('closes', len(rows), 'EIE', len(eie), 'EIE pnl usd', round(sum(r['pnl_usd'] for r in eie), 2),
      'all pnl usd', round(sum(r['pnl_usd'] for r in rows), 2))
print('EIE exits with |liquidity change| < 2%:', sum(1 for r in eie if abs(r['liq_change_pct']) < 2))
print('EIE exits where market move at exit was >= 0:', sum(1 for r in eie if r['market_move_pct'] >= 0))
cfs = [(r, r['cf_5_10_60']) for r in rows if r['cf_5_10_60'] and r['cf_5_10_60'][0] is not None]
print('counterfactual -5/+10/60 (harness model costs) on the same entries: n', len(cfs),
      'mean %', round(sum(c[0] for _, c in cfs) / len(cfs), 2), 'actual mean %', round(sum(r['pnl_pct'] for r, _ in cfs) / len(cfs), 2))
print('  counterfactual exits', {k: sum(1 for _, c in cfs if c[1] == k) for k in ('STOP', 'TP', 'HOLD')})
# rug screen on the open positions and the Lab VSOF position at their entry times
checks = [('WOSE', 'GhBPuDpt', '2026-10-08 10:12:18'), ('GOIF', 'D2pVedgH', '2026-10-08 10:13:17'),
          ('SARP', 'HiPe6mDS', '2026-10-08 10:13:38'), ('VSOF', 'AGZjpu5v', '2026-10-08 09:52:40'),
          ('swordinu', '69fyvgoT', '2026-10-07 21:41:38')]
for sym, pre, when in checks:
    pair = next((p for p in series if p.startswith(pre)), None)
    if not pair:
        print('rug check', sym, 'pair not in dataset')
        continue
    s = series[pair]
    t = int(datetime.datetime.strptime(when, '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
    i = max(0, bisect.bisect_right(s['t'], t) - 1)
    P = H.Past(s, i)
    mc, liq = P('mcap'), P('liq')
    print('rug check', sym, pre, 'at', when, 'interim_rug_risk', H.interim_rug_risk(P), 'mcap $M', round(mc / 1e6, 1), 'liq $k', round(liq / 1e3),
          'liq/mcap %', round(100 * liq / mc, 2) if mc > 0 else None, 'age_min', round(P('age')), 'other pairs same ticker', H.other_pairs_same_ticker_before(P),
          'fee_bps', P.fee_bps(), '| last liq $k', round(s['liq'][-1] / 1e3), 'last px / entry px', round(s['price'][-1] / P('price'), 3))
json.dump(rows, open(os.path.join(DEEP, 'forensics', 'hist_cf.json'), 'w'), indent=1, ensure_ascii=False)
