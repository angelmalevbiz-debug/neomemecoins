"""Audit step 1: what 'upd' is, how the de-dup rule behaves, stale repeats and out-of-order rows (read-only)."""
import math, sqlite3, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
DEEP = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r''
con = sqlite3.connect('file:%s/obs.sqlite3?mode=ro' % DEEP, uri=True)

# 1) is upd per-pair or per-batch?
n_rows, n_upd = con.execute('select count(*), count(distinct upd) from o').fetchone()
print('rows', n_rows, 'distinct upd', n_upd, 'rows per distinct upd', round(n_rows / max(1, n_upd), 1))
print('upd null rows', con.execute('select count(*) from o where upd is null').fetchone()[0])
lag = [r[0] for r in con.execute('select t - upd from o where upd is not null and (rowid % 97) = 0')]
lag.sort()
q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
print('t - upd ms (sample n=%d): p1 %s p10 %s p50 %s p90 %s p99 %s max %s' % (len(lag), q(lag, .01), q(lag, .1), q(lag, .5), q(lag, .9), q(lag, .99), lag[-1]))
top = con.execute('select upd, count(*), count(distinct pair) from o group by upd order by 2 desc limit 3').fetchall()
print('largest upd groups (upd, rows, pairs):', top)

# 2) de-dup replay with diagnostics
stats = collections.Counter()
same_price_next = collections.Counter()
gap_hist = collections.Counter()
cur = None
last_upd = last_t = None
last_kept = None  # (t, upd, price, liq, v5, mcap)
run_unchanged_ms = []
last_change_t = None
pairs_multi = collections.Counter()
prev_raw_t = None
for pair, t, upd, price, liq, v5, mcap, dex, qs in con.execute(
        'select pair, t, upd, price, liq, v5, mcap, dex, quote_sol from o order by pair, t'):
    if pair != cur:
        cur = pair
        last_upd = last_t = None
        last_kept = None
        last_change_t = None
        prev_raw_t = None
    if prev_raw_t is not None and t - prev_raw_t < 1000:
        stats['raw_rows_lt_1s_after_prev_raw'] += 1
    prev_raw_t = t
    newer = upd is not None and (last_upd is None or upd > last_upd)
    if not (last_t is None or newer or (t - last_t >= 15_000)):
        stats['dropped'] += 1
        continue
    if last_t is None:
        stats['kept_first'] += 1
    elif newer:
        stats['kept_newer'] += 1
    else:
        stats['kept_15s'] += 1
        if upd is None:
            stats['kept_15s_upd_none'] += 1
        elif last_upd is not None and upd < last_upd:
            stats['kept_15s_upd_OLDER_than_last'] += 1
        elif upd == last_upd:
            stats['kept_15s_upd_equal'] += 1
    if newer:
        last_upd = upd
    if last_kept is not None:
        same = (price == last_kept[2])
        same_all = same and liq == last_kept[3] and v5 == last_kept[4] and mcap == last_kept[5]
        stats['pts_after_first'] += 1
        stats['same_price_as_prev_kept'] += same
        stats['same_price_liq_v5_mcap_as_prev'] += same_all
        if dex == 'pumpswap' and qs == 1:
            stats['ps_pts_after_first'] += 1
            stats['ps_same_price_as_prev_kept'] += same
            stats['ps_same_all_as_prev'] += same_all
        g = t - last_kept[0]
        b = '<5s' if g < 5000 else '<15s' if g < 15000 else '<60s' if g < 60000 else '<10m' if g < 600000 else '<1h' if g < 3600000 else '>=1h'
        gap_hist[b] += 1
        if price != last_kept[2]:
            if last_change_t is not None:
                run_unchanged_ms.append(t - last_change_t)
            last_change_t = t
    else:
        last_change_t = t
    last_t = t
    last_kept = (t, upd, price, liq, v5, mcap)
    stats['kept'] += 1
for k in sorted(stats):
    print(k, stats[k])
print('share kept points whose price equals previous kept point: all %.3f, pumpswap-SOL %.3f' % (
    stats['same_price_as_prev_kept'] / stats['pts_after_first'], stats['ps_same_price_as_prev_kept'] / stats['ps_pts_after_first']))
print('share identical price+liq+v5+mcap: all %.3f, pumpswap-SOL %.3f' % (
    stats['same_price_liq_v5_mcap_as_prev'] / stats['pts_after_first'], stats['ps_same_all_as_prev'] / stats['ps_pts_after_first']))
print('gap histogram between kept points:', dict(gap_hist))
run_unchanged_ms.sort()
print('time between price changes (kept pts) ms: p10 %s p50 %s p90 %s p99 %s' % (q(run_unchanged_ms, .1), q(run_unchanged_ms, .5), q(run_unchanged_ms, .9), q(run_unchanged_ms, .99)))
con.close()
