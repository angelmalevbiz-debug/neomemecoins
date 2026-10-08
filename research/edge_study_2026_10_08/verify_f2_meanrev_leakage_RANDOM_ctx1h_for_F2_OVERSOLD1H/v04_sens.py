"""Fragility of the f2r1 holdout result: leave-one-pair-out, drop best trades, exit-fill model, END marks, holdout
halves, no-fill skips, and an on-chain tape check of the holdout fills where the tape covers the pool."""
import sys
sys.dont_write_bytecode = True
import bisect, json, sqlite3, time
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
OUT = DEEP + '/verify_f2_meanrev_leakage_RANDOM_ctx1h_for_F2_OVERSOLD1H'
sys.path.insert(0, OUT)
import indep as I

t0 = time.time()
ser, meta = I.load()
CUT = I.cut_time()
sh_ho = (meta['t1'] - CUT) / 3.6e6
res = {}


def m(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


elig = I.eligible_index(screen='interim')
base = I.run(elig, 'f2r1', 0.005)
tr, ho = I.split(base)
hv = [x['net50'] for x in ho]
print('holdout f2r1 n', len(ho), 'mean net50 %', m(hv))

# 1. leave-one-pair-out
pairs = sorted({x['pair'] for x in ho})
loo = []
for p in pairs:
    rest = [x['net50'] for x in ho if x['pair'] != p]
    loo.append((p[:8], ser[p]['sym'], sum(1 for x in ho if x['pair'] == p), m(rest)))
loo.sort(key=lambda r: r[3])
res['loo_pair'] = loo
print('LOO pair (pair, sym, n removed, holdout mean without it):')
for r in loo:
    print('  ', r)
# 2. drop best trades
sv = sorted(hv, reverse=True)
res['drop_best'] = {'all': m(sv), 'drop1': m(sv[1:]), 'drop2': m(sv[2:]), 'drop3': m(sv[3:])}
print('drop best trades', res['drop_best'])
# 3. exit-fill model sensitivity (same entries: entries depend on exits through cooldown, so rerun fully)
for mode in ('trigger', 'min'):
    trs = I.run(elig, 'f2r1', 0.005, exit_mode=mode)
    a, b = I.split(trs)
    res['exit_mode_' + mode] = {'train_mean': m([x['net50'] for x in a]), 'holdout_n': len(b),
                                'holdout_mean': m([x['net50'] for x in b])}
    print('exit fill mode', mode, res['exit_mode_' + mode])
# 4. END marks: value open-at-end positions with the 10% vanish haircut instead of the last print
trs = I.run(elig, 'f2r1', 0.005, end_haircut=10.0)
a, b = I.split(trs)
res['end_haircut10'] = {'holdout_mean': m([x['net50'] for x in b]), 'n': len(b)}
res['excl_end'] = m([x['net50'] for x in ho if x['flag'] != 'END'])
print('END marks -10%', res['end_haircut10'], 'excluding END trades', res['excl_end'])
# 5. holdout halves (by entry time)
mid = CUT + (meta['t1'] - CUT) / 2
h1 = [x['net50'] for x in ho if x['entry_t'] < mid]
h2 = [x['net50'] for x in ho if x['entry_t'] >= mid]
res['holdout_halves'] = {'first_n': len(h1), 'first_mean': m(h1), 'second_n': len(h2), 'second_mean': m(h2)}
# train thirds
c1, c2 = meta['t0'] + (CUT - meta['t0']) / 3, meta['t0'] + 2 * (CUT - meta['t0']) / 3
res['train_thirds'] = [m([x['net50'] for x in tr if lo <= x['entry_t'] < hi]) for lo, hi in
                       ((meta['t0'], c1), (c1, c2), (c2, CUT))]
print('holdout halves', res['holdout_halves'], 'train thirds', res['train_thirds'])
# 6. no-fill skips
sk = []
I.run(elig, 'f2r1', 0.005, count_skips=sk)
res['nofill_skips'] = [(p[:8], ser[p]['sym'], ser[p]['t'][i], 'holdout' if ser[p]['t'][i] >= CUT else 'train',
                        round((ser[p]['t'][i + 1] - ser[p]['t'][i]) / 1000) if i + 1 < len(ser[p]['t']) else None)
                       for p, i in sk]
print('no-fill skips', res['nofill_skips'])
# what would a skipped trade have done if filled at the next point whatever the lag
for p, i in sk:
    s = ser[p]
    if i + 1 < len(s['t']):
        j = i + 1
        q = I.buy(s, j, 200.0)
        later = [k for k in range(j + 1, len(s['t'])) if s['t'][k] - s['t'][j] <= 90 * 60_000]
        if q and later:
            k = later[-1]
            v = I.sell(s, k, q[0])
            print('   skipped trade, hypothetical buy at next point, 90-min mark net0 %.2f%%' % (100 * (v - 200 - q[1]) / 200))

# 7. on-chain tape check of the holdout fills (raw SOL price ratio exit/entry, series vs tape last swap in +-15 s)
want = {x['pair'] for x in ho}
con = sqlite3.connect('file:%s?mode=ro' % (DEEP + '/tape_snapshot.sqlite3'), uri=True)
tape = {}
for p in want:
    rows = con.execute('select event_time, payload from events where pair = ? order by event_time', (p,)).fetchall()
    ts, px = [], []
    for et, pl in rows:
        d = json.loads(pl)
        if (d.get('quote_asset') or '') != 'So11111111111111111111111111111111111111112':
            continue
        ta, qa = d.get('token_amount'), d.get('quote_amount')
        if not (ta and qa and ta > 0 and qa > 0):
            continue
        ts.append(et)
        px.append(qa / ta)
    tape[p] = (ts, px)
con.close()


def tape_px(p, t, win=15_000):
    ts, px = tape.get(p, ([], []))
    a = bisect.bisect_right(ts, t) - 1
    cands = []
    if a >= 0 and t - ts[a] <= win:
        cands.append(px[a])
    if a + 1 < len(ts) and ts[a + 1] - t <= win:
        cands.append(px[a + 1])
    return sum(cands) / len(cands) if cands else None


def series_native(p, t):
    s = ser[p]
    k = bisect.bisect_left(s['t'], t)
    return s['pnative'][k] if k < len(s['t']) and s['t'][k] == t else None


chk = []
for x in ho:
    p = x['pair']
    te, tx = tape_px(p, x['entry_t']), tape_px(p, x['exit_t'])
    se, sx = series_native(p, x['entry_t']), series_native(p, x['exit_t'])
    if te and tx and se and sx and x['flag'] == '':
        chk.append((p[:8], x['sym'], x['reason'], round(100 * (sx / se - 1), 2), round(100 * (tx / te - 1), 2)))
res['tape_check'] = chk
print('tape check (pair, sym, reason, series raw ret %, tape raw ret %):')
for r in chk:
    print('  ', r)
if chk:
    d = [a[3] - a[4] for a in chk]
    res['tape_check_mean_series_minus_tape'] = round(sum(d) / len(d), 2)
    print('  mean series-minus-tape raw return %.2f pts over %d trades' % (sum(d) / len(d), len(d)))
with open(OUT + '/v04_sens.json', 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=1, default=str)
print('secs', round(time.time() - t0, 1))
