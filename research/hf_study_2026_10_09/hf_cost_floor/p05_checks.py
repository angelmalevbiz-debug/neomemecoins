"""p05: TRAIN sanity checks on the candidate configs: gross-move distribution, zero-move share, outliers, per-pair."""
import os, sys, collections
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfsim as S

SPAN = (S.CUT - S.T0) / 3.6e6
CANDS = [('a', ('time', 120), 5, 25.0, {}), ('a', ('time', 60), 3, 25.0, {}),
         ('c', ('time', 120), 3, 25.0, {'cooldown_s': 300}), ('b', ('time', 60), 3, 25.0, {'cooldown_s': 300})]
q = S._q
for univ, spec, slots, size, kw in CANDS:
    tr, info = S.run_book(univ, spec, slots, size, t_from=S.T0, t_to=S.CUT, **kw)
    g = [100 * (x['exit_px'] / x['entry_px'] - 1) for x in tr]
    zero = sum(1 for x in tr if x['exit_px'] == x['entry_px'])
    print('==', univ, spec, slots, size, kw, 'n', len(tr))
    print('  gross move %% mean %.3f median %.3f p10 %.3f p90 %.3f p1 %.3f p99 %.3f | zero-move share %.3f' % (
        sum(g) / len(g), q(g, .5), q(g, .1), q(g, .9), q(g, .01), q(g, .99), zero / len(tr)))
    n50 = [x['net50'] for x in tr]
    print('  net50 mean %.3f median %.3f p10 %.3f p90 %.3f sd %.3f' % (
        sum(n50) / len(n50), q(n50, .5), q(n50, .1), q(n50, .9),
        (sum((v - sum(n50) / len(n50)) ** 2 for v in n50) / (len(n50) - 1)) ** .5))
    # mean gross without the top 1% and bottom 1%
    gs = sorted(g)
    k = max(1, len(gs) // 100)
    print('  gross mean trimmed 1%%/1%%: %.3f' % (sum(gs[k:-k]) / len(gs[k:-k])))
    top = sorted(tr, key=lambda x: -x['net0'])[:4]
    for x in top:
        print('  top', S.C.ab(x['pair']), x['reason'], x['flag'], 'net0 %.2f' % x['net0'], 'hold %.0f' % x['hold_s'],
              'fee', x['fee_bps'], 'liq %.0f' % x['liq'])
    bot = sorted(tr, key=lambda x: x['net0'])[:3]
    for x in bot:
        print('  bot', S.C.ab(x['pair']), x['reason'], x['flag'], 'net0 %.2f' % x['net0'], 'hold %.0f' % x['hold_s'],
              'fee', x['fee_bps'], 'liq %.0f' % x['liq'])
    per = collections.defaultdict(list)
    for x, gg in zip(tr, g):
        per[x['pair']].append((gg, x['net50'], x['fee_bps']))
    rows = sorted(per.items(), key=lambda kv: -len(kv[1]))
    for p, v in rows[:10]:
        print('  pair %s n %4d gross mean %+.3f net50 mean %+.3f fee %.1f' % (
            S.C.ab(p), len(v), sum(a for a, _, _ in v) / len(v), sum(b for _, b, _ in v) / len(v), v[0][2]))
    # hourly trade counts
    hrs = collections.Counter(int(x['dec_t'] // 3_600_000) for x in tr)
    h0, h1 = int(S.T0 // 3_600_000), int(S.CUT // 3_600_000)
    print('  trades per clock hour:', [hrs.get(h, 0) for h in range(h0, h1 + 1)])
