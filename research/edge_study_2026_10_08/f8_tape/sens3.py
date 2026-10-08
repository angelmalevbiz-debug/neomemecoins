"""Fragility of the post-hoc fully-on-chain BIGSELL_DIPBUY_3SOL holdout result (no selection)."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/f8_tape')
import harness as H
import strategies as ST
import sig as S, tsim, tape_load as TL

series, meta = H.load()
cut = H.split_t(meta)
PAIRS = set(TL.load()['tape'].keys())
for name, ms in (('3SOL', 3.0), ('5SOL', 5.0)):
    tr = tsim.simulate(S.whale_signal(min_sol=ms, side=-1, **ST.COMMON), pairs=PAIRS, entry_mode='onchain', exit_mode='onchain',
                       trigger_mode='onchain', **ST.EXIT_WIDE)
    ho = [x for x in tr if x['entry_t'] >= cut]
    ho.sort(key=lambda x: -x['usd50'])
    print(name, 'holdout n', len(ho), 'sum usd50 %.1f' % sum(x['usd50'] for x in ho))
    print('  top5', [(x['sym'][:8], x['pair'][:8], round(x['net50'], 1), x['reason']) for x in ho[:5]])
    print('  without top1 sum %.1f mean %.2f%% | without top3 sum %.1f mean %.2f%%' % (
        sum(x['usd50'] for x in ho[1:]), sum(x['net50'] for x in ho[1:]) / (len(ho) - 1),
        sum(x['usd50'] for x in ho[3:]), sum(x['net50'] for x in ho[3:]) / (len(ho) - 3)))
    bypair = {}
    for x in ho:
        bypair.setdefault(x['pair'][:8] + ' ' + (x['sym'] or '')[:8], []).append(x['usd50'])
    print('  by pair usd50', sorted(((k, round(sum(v), 1), len(v)) for k, v in bypair.items()), key=lambda r: -r[1]))
    excl = max(bypair, key=lambda k: sum(bypair[k]))
    rest = [x for x in ho if (x['pair'][:8] + ' ' + (x['sym'] or '')[:8]) != excl]
    print('  leave-best-pair-out mean %.2f%% n %d' % (sum(x['net50'] for x in rest) / len(rest), len(rest)))
    print('  portfolio holdout slots=3', H.portfolio(ho, slots=3), 'train', H.portfolio([x for x in tr if x['entry_t'] < cut], slots=3))
    print('  exits', H.summarize(ho)['exits'], 'trades/hour', H.summarize(ho)['trades_per_hour'], 'model mean', H.summarize(ho, 'usd0')['mean_pct'])
