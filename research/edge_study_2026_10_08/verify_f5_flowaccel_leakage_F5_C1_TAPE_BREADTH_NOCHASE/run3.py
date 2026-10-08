"""Step 3: the information-timing leak test and trade-level diffs.

(1) STALE-FILL / FRESH-SIGNAL check. The signal reads on-chain swaps ingested up to the decision time t, but the
    simulator fills at the next DexScreener refresh, and DexScreener prices lag the chain by ~20 s. If the signal
    (recent on-chain buying) predicts that the DexScreener print still sits BELOW the true on-chain price, the
    backtest buys cheaper than was possible. Truth = on-chain mid at decision + 2 s (sqrt(last BUY px * last SELL px),
    each within 20 s; fallback last print within 20 s), like audit/t07. Same at the exit trigger + 2 s.
    BUY bias = log(truth/fill) bps (> 0 = optimistic); SELL bias = log(truth/fill) (< 0 = optimistic).
    Re-priced net = (1 + net50) * (truth_exit/fill_exit) / (truth_entry/fill_entry) - 1 (first order).
    Compared with random-entry trades in the same universe (leaderboard F5_RANDOM_TAPE_LIVE_NOCHASE, orig).
(2) Which holdout trades the interim rug screen removes (rug_none vs interim).
(3) Which trades change between tape lag 0 s and 5 s.
"""
import sys
sys.dont_write_bytecode = True
import bisect, importlib.util, json, math, os, pickle, sqlite3, time

DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


H = _load('harness_final', DEEP + '/leaderboard/harness_final.py')
sys.modules['harness'] = H
sys.path.insert(0, OUT)
import indep as I  # noqa: E402

t0 = time.time()
series, meta = H.load()
CUT = H.split_t(meta)
WSOL = 'So11111111111111111111111111111111111111112'

# ---- on-chain prints (truth uses block time only; ingestion time irrelevant for truth)
con = sqlite3.connect('file:%s?mode=ro' % (DEEP + '/tape_snapshot.sqlite3'), uri=True)
PR = {}
for pair, et, payload in con.execute('SELECT pair, event_time, payload FROM events'):
    p = json.loads(payload)
    if not p.get('confirmed_swap') or p.get('quote_asset') != WSOL:
        continue
    d = p.get('direction')
    try:
        q, a = float(p.get('quote_amount')), float(p.get('token_amount'))
    except (TypeError, ValueError):
        continue
    if not (q > 0 and a > 0) or d not in ('BUY', 'SELL'):
        continue
    PR.setdefault(pair, []).append((int(et), d == 'BUY', q / a))
con.close()
MID = {}
for pair, v in PR.items():
    v.sort()
    MID[pair] = ([x[0] for x in v if x[1]], [x[2] for x in v if x[1]], [x[0] for x in v if not x[1]],
                 [x[2] for x in v if not x[1]], [x[0] for x in v], [x[2] for x in v])
print('prints loaded', len(PR), round(time.time() - t0, 1), 's', flush=True)


def truth(pair, t, win=20_000):
    m = MID.get(pair)
    if m is None:
        return None, None
    bt, bp, st, sp, at, ap = m
    a = bisect.bisect_right(bt, t) - 1
    b = bisect.bisect_right(st, t) - 1
    if a >= 0 and b >= 0 and t - bt[a] <= win and t - st[b] <= win:
        return math.sqrt(bp[a] * sp[b]), 'mid'
    c = bisect.bisect_right(at, t) - 1
    if c >= 0 and t - at[c] <= win:
        return ap[c], 'last'
    return None, None


def idx_at(s, t):
    return bisect.bisect_left(s['t'], t)


def bias(trades):
    rows = []
    for x in trades:
        s = series[x['pair']]
        j = idx_at(s, x['entry_t'])
        if j >= len(s['t']) or s['t'][j] != x['entry_t']:
            continue
        fill_e = s['pnative'][j]
        tr_e, kind_e = truth(x['pair'], x['decision_t'] + 2000)
        # exit: valuation point price in SOL = exit_px / sol_usd at that point; use pnative at the exit index
        k = idx_at(s, x['exit_t'])
        fill_x = None
        if k < len(s['t']) and s['price'][k] > 0 and abs(s['price'][k] - x['exit_px']) / s['price'][k] < 1e-9:
            fill_x = s['pnative'][k]
        else:  # GAP / VANISHED valuation (haircut or earlier index): derive SOL price via the trade's own ratio
            kk = k if k < len(s['t']) else len(s['t']) - 1
            su = H.sol_usd(s, kk)
            fill_x = x['exit_px'] / su if su > 0 else None
        tr_x, kind_x = truth(x['pair'], x['trigger_t'] + 2000)
        r = {'pair': x['pair'][:8], 'sym': x['sym'], 'ho': x['entry_t'] >= CUT, 'net50': x['net50'], 'usd50': x['usd50'],
             'size': x['size'], 'reason': x['reason'], 'flag': x['flag']}
        if tr_e and fill_e > 0:
            r['buy_bias_bps'] = 1e4 * math.log(tr_e / fill_e)
            r['kind_e'] = kind_e
        if tr_x and fill_x and fill_x > 0 and x['flag'] not in ('GAP', 'VANISHED'):
            r['sell_bias_bps'] = 1e4 * math.log(tr_x / fill_x)
            r['kind_x'] = kind_x
        if 'buy_bias_bps' in r and 'sell_bias_bps' in r:
            g = math.exp((r['sell_bias_bps'] - r['buy_bias_bps']) / 1e4)
            r['net50_tape'] = 100 * ((1 + x['net50'] / 100) * g - 1)
            r['usd50_tape'] = x['size'] * r['net50_tape'] / 100
        rows.append(r)
    return rows


def agg(rows, part):
    sel = [r for r in rows if (part == 'all' or (part == 'ho') == r['ho'])]
    out = {'n': len(sel)}
    for k in ('buy_bias_bps', 'sell_bias_bps'):
        v = sorted(r[k] for r in sel if k in r)
        if v:
            out[k] = {'n': len(v), 'mean': round(sum(v) / len(v), 1), 'median': round(v[len(v) // 2], 1)}
    both = [r for r in sel if 'net50_tape' in r]
    if both:
        out['repriced'] = {'n': len(both), 'net50_mean': round(sum(r['net50'] for r in both) / len(both), 3),
                           'net50_tape_mean': round(sum(r['net50_tape'] for r in both) / len(both), 3),
                           'usd50_mean': round(sum(r['usd50'] for r in both) / len(both), 3),
                           'usd50_tape_mean': round(sum(r['usd50_tape'] for r in both) / len(both), 3)}
    return out


RES = {}
c1 = pickle.load(open(os.path.join(OUT, 'trades_orig_rerun.pkl'), 'rb'))
lb = pickle.load(open(DEEP + '/leaderboard/trades/f5_flowaccel.pkl', 'rb'))
rnd = [r for r in lb['rows'] if r['name'] == 'F5_RANDOM_TAPE_LIVE_NOCHASE' and r['variant'] == 'orig'][0]['trades']
rndu = [r for r in lb['rows'] if r['name'] == 'F5_RANDOM_TAPE_LIVE' and r['variant'] == 'orig'][0]['trades']
for name, trs in (('C1', c1), ('RANDOM_TAPE_LIVE_NOCHASE', rnd), ('RANDOM_TAPE_LIVE', rndu)):
    rows = bias(trs)
    RES[name] = {'all': agg(rows, 'all'), 'train': agg(rows, 'tr'), 'holdout': agg(rows, 'ho')}
    print(name, json.dumps(RES[name]), flush=True)
    if name == 'C1':
        print('  C1 holdout trades (sym pair net50 buy_bias sell_bias net50_tape):')
        for r in rows:
            if r['ho']:
                print('   ', r['sym'], r['pair'], round(r['net50'], 2), round(r.get('buy_bias_bps', float('nan')), 1),
                      r.get('kind_e'), round(r.get('sell_bias_bps', float('nan')), 1), r.get('kind_x'),
                      round(r.get('net50_tape', float('nan')), 2), r['reason'], r['flag'])
        RES['C1_rows'] = rows

# ---- (2) rug screen removals in holdout
tr_none = H.simulate(I.make_signal(H, newdef='event', rug='none'), **I.EXITS)
k_int = set((x['pair'], x['entry_t']) for x in c1)
rem = [x for x in tr_none if x['entry_t'] >= CUT and (x['pair'], x['entry_t']) not in k_int]
by = {}
for x in rem:
    by.setdefault((x['sym'], x['pair'][:8]), []).append(x['net50'])
RES['rug_removed_holdout'] = sorted(([k[0], k[1], len(v), round(sum(v) / len(v), 2)] for k, v in by.items()), key=lambda z: -z[2])
print('holdout trades only without the rug screen:', len(rem), RES['rug_removed_holdout'])
# why were they screened: other pairs with the same ticker / mcap rule at entry
why = {}
for x in rem:
    s = series[x['pair']]
    i = bisect.bisect_left(s['t'], x['decision_t'])
    P = H.Past(s, i)
    tag = ('ticker_reuse=%d' % H.other_pairs_same_ticker_before(P), 'age_d=%.1f' % (P('age') / 1440))
    why.setdefault(x['sym'] + ':' + x['pair'][:8], tag)
RES['rug_removed_why'] = why
print('why', why)

# ---- (3) lag 0 vs lag 5 s
tr5 = H.simulate(I.make_signal(H, newdef='event', lag_ms=5000), **I.EXITS)
k0 = {(x['pair'], x['entry_t']): x for x in c1 if x['entry_t'] >= CUT}
k5 = {(x['pair'], x['entry_t']): x for x in tr5 if x['entry_t'] >= CUT}
only0 = [k0[k] for k in k0 if k not in k5]
only5 = [k5[k] for k in k5 if k not in k0]
RES['lag0_vs_lag5_holdout'] = {
    'common': len(set(k0) & set(k5)),
    'only_lag0': [(x['sym'], x['pair'][:8], int(x['decision_t']), round(x['net50'], 2), round(x['usd50'], 2)) for x in only0],
    'only_lag5': [(x['sym'], x['pair'][:8], int(x['decision_t']), round(x['net50'], 2), round(x['usd50'], 2)) for x in only5]}
print(json.dumps(RES['lag0_vs_lag5_holdout'], indent=1))
with open(os.path.join(OUT, 'run3.json'), 'w') as fh:
    json.dump(RES, fh, indent=1, default=str)
print('done', round(time.time() - t0, 1), 's')
