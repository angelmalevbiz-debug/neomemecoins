"""Audit step 7: which DexScreener-based fill rule best approximates the on-chain price at decision + 2 s?

Truth = tape mid at t_i + 2 s, mid = sqrt(last BUY print * last SELL print), each within 20 s (both directions
needed, which removes the bid/ask bounce of last-trade prices). Rules compared (all DexScreener prices):
  B   : next point j = i + 1 (harness v1)
  F   : first later point whose price differs from the price at i (next DexScreener refresh), within 60 s
  F20 : first later point with t >= t_i + 20 s whose price differs from the price at i, within 90 s
  P20 : first later point with t >= t_i + 20 s (no freshness requirement), within 90 s
Context = the DexScreener move seen at decision point i vs the previous distinct price (bounce / momentum).
"""
import bisect, collections, math, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/audit')
import harness as H
from tapelib import load_tape

series, meta = H.load()
ps = {p for p, s in series.items() if s['dex'] == 'pumpswap' and s['quote_sol'] == 1}
tape = load_tape(ps, min_events=200)


class Mid:
    def __init__(self, tp):
        ts, px, buy, qa = tp
        self.bt = [t for t, b in zip(ts, buy) if b]; self.bp = [p for p, b in zip(px, buy) if b]
        self.st = [t for t, b in zip(ts, buy) if not b]; self.sp = [p for p, b in zip(px, buy) if not b]

    def at(self, t, win=20_000):
        a = bisect.bisect_right(self.bt, t) - 1
        b = bisect.bisect_right(self.st, t) - 1
        if a < 0 or b < 0 or t - self.bt[a] > win or t - self.st[b] > win:
            return None
        return math.sqrt(self.bp[a] * self.sp[b])


def rule_points(s, i):
    ts, pr = s['t'], s['pnative']
    n = len(ts)
    out = {}
    if i + 1 < n and ts[i + 1] - ts[i] <= 60_000:
        out['B'] = i + 1
    for name, minlag, maxlag, fresh in (('F', 0, 60_000, True), ('F20', 20_000, 90_000, True), ('P20', 20_000, 90_000, False)):
        k = i + 1
        while k < n and ts[k] - ts[i] <= maxlag:
            if ts[k] - ts[i] >= minlag and (not fresh or pr[k] != pr[i]):
                out[name] = k
                break
            k += 1
    return out


RULES = ('B', 'F', 'F20', 'P20')
err = collections.defaultdict(list)          # (rule, context) -> signed log error bps (truth/fill)
for p, tp in tape.items():
    s = series[p]
    M = Mid(tp)
    ts, pr = s['t'], s['pnative']
    prev_distinct = None
    for i in range(1, len(ts) - 1):
        if pr[i] != pr[i - 1]:
            prev_distinct = pr[i - 1]
        truth = M.at(ts[i] + 2000)
        if truth is None or not (pr[i] > 0):
            continue
        rp = rule_points(s, i)
        if len(rp) < len(RULES):
            continue
        mv = math.log(pr[i] / prev_distinct) * 100 if prev_distinct and prev_distinct > 0 else 0.0
        ctx = 'dn>2%' if mv < -2 else 'dn0.5-2' if mv < -0.5 else 'flat' if mv <= 0.5 else 'up0.5-2' if mv <= 2 else 'up>2%'
        fresh = 'fresh' if pr[i] != pr[i - 1] else 'stale'
        for r in RULES:
            e = math.log(truth / pr[rp[r]]) * 1e4
            err[(r, ctx)].append(e); err[(r, 'ALL')].append(e)
            err[(r, fresh + ':' + ctx)].append(e)
        # signal-price baseline: filling at the decision price itself (zero latency)
        e0 = math.log(truth / pr[i]) * 1e4
        err[('SIGNAL_PX', ctx)].append(e0); err[('SIGNAL_PX', 'ALL')].append(e0)


def st(xs):
    xs = sorted(xs)
    n = len(xs)
    mean = sum(xs) / n
    mae = sum(abs(x) for x in xs) / n
    return n, mean, xs[n // 2], sorted(abs(x) for x in xs)[n // 2], mae


print('signed error bps = log(truth / fill); BUY fill optimistic if > 0, SELL fill optimistic if < 0')
print('%-10s %-10s %7s %8s %8s %9s %8s' % ('rule', 'context', 'n', 'mean', 'median', 'med|err|', 'MAE'))
for ctx in ('ALL', 'dn>2%', 'dn0.5-2', 'flat', 'up0.5-2', 'up>2%'):
    for r in ('SIGNAL_PX',) + RULES:
        xs = err.get((r, ctx))
        if not xs:
            continue
        n, mean, med, mabs, mae = st(xs)
        print('%-10s %-10s %7d %8.1f %8.1f %9.1f %8.1f' % (r, ctx, n, mean, med, mabs, mae))
print('-- decisions at a FRESH point (DexScreener price just changed) only')
for ctx in ('dn>2%', 'up>2%'):
    for r in RULES:
        xs = err.get((r, 'fresh:' + ctx))
        if xs:
            n, mean, med, mabs, mae = st(xs)
            print('%-10s %-14s %7d %8.1f %8.1f %9.1f %8.1f' % (r, 'fresh:' + ctx, n, mean, med, mabs, mae))
