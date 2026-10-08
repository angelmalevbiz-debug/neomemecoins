"""Descriptive diagnostics of the already-evaluated configs (no selection, no tuning): costs and stop gap-through."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H
import strategies as S

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
series, meta = H.load()
cut = H.split_t(meta)
for cfg in S.CONFIGS + S.BASELINES:
    tr = H.simulate(cfg['signal'], **cfg['kwargs'])
    stops = [x for x in tr if x['reason'] == 'STOP']
    rt = [x['rt_cost'] for x in tr if x['rt_cost'] is not None]
    stop_lvl = cfg['kwargs']['stop']
    print('==', cfg['name'], 'n', len(tr))
    print('  modeled entry RT cost %% mean %.2f  (+1.0 stress)  fee bps mean %.0f  liq median $%.0f' % (
        sum(rt) / len(rt), sum(x['fee_bps'] for x in tr) / len(tr), sorted(x['liq'] for x in tr)[len(tr) // 2]))
    if stops:
        ss = sorted(x['net50'] for x in stops)
        print('  STOP exits %d: mean net50 %.2f (stop level %s), median %.2f, worst %s, share below 2x stop %.2f' % (
            len(stops), sum(ss) / len(ss), stop_lvl, ss[len(ss) // 2], [round(v, 1) for v in ss[:5]],
            sum(1 for v in ss if v < 2 * stop_lvl) / len(ss)))
    w = sorted(tr, key=lambda x: x['net50'])[:4]
    print('  worst trades', [(x['sym'], x['pair'][:8], round(x['net50'], 1), x['reason'], x['flag'], round(x['hold_s'] / 60, 1),
                              'ho' if x['entry_t'] >= cut else 'tr') for x in w])
    b = sorted(tr, key=lambda x: -x['net50'])[:3]
    print('  best trades', [(x['sym'], x['pair'][:8], round(x['net50'], 1), x['reason'], 'ho' if x['entry_t'] >= cut else 'tr') for x in b])
