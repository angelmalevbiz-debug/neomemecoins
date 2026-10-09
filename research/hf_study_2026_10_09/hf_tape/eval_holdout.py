"""eval_holdout: the 3 configs PRE-CHOSEN on TRAIN (grid_train.out / train_detail.out), run once on the holdout
(last 40 % of time). PAPER research only.

Pre-chosen (frozen before this script first ran):
 HF_TAPE_C1_BURST60_NOVETO  best tape buy-burst config with train >= 45 trades/h (B2 net50 basis):
     universe  PumpSwap SOL, H.rug_guard_v1 passes, fee <= 95 bps, liq >= $50k, decision at the ingestion moment of a
               real-time swap in a seated (tape-live) pool; heat veto NOT applied (logged only) because the veto
               removes 78 % of tape decision moments and leaves < 1 burst trade/hour (train);
     signal    last 60 s of verified swaps ingested by t: >= 2 distinct buyers (actor-flagged buys excluded), net SOL
               inflow > 0, ingested tape-mid change over 60 s <= +2 % (no chase);
     book      $100, time exit 60 s, 4 slots, one position per pool, no cooldown.
     control   random entries (hashed coin, density matched on train 0.415) in the same universe, same book.
 HF_TAPE_C3_ALL_VETO  best train net50 with >= 45 trades/h overall: same universe WITH the heat veto, enter at every
               decision moment (flow-agnostic = random timing inside seated pools), same book.
 HF_TAPE_C4_LE50_VETO  cheapest pools: as C3 but fee <= 50 bps only.
Fill models: B2 (primary; chain mid 2 s after decision), B5 (5 s), A (DexScreener next refresh).
Daily loss cap variant: book capital $1000, no new entries for the rest of the UTC day once realized day P&L <= -$100.
"""
import json, os, sys, time
from collections import defaultdict

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import booklib as B

t00 = time.time()
D = B.data()
PTS = sorted(D['points'], key=lambda p: (p['t'], p['pair']))
CUT, T0, T1 = D['cut'], D['t0'], D['t1']
SPAN = {'train': (CUT - T0) / 3.6e6, 'holdout': (T1 - CUT) / 3.6e6}
PART = {'train': [p for p in PTS if p['t'] < CUT], 'holdout': [p for p in PTS if p['t'] >= CUT]}


def sig_c1(p):
    f = p['f60']
    return f is not None and f['ub'] >= 2 and f['net_sol'] > 0 and p['tret60'] <= 2.0


CFG = {
    'HF_TAPE_C1_BURST60_NOVETO': dict(fee=95, veto=False, sig=sig_c1, dens=0.415),
    'HF_TAPE_C3_ALL_VETO': dict(fee=95, veto=True, sig=lambda p: True, dens=None),
    'HF_TAPE_C4_LE50_VETO': dict(fee=50, veto=True, sig=lambda p: True, dens=None),
}
HOLD, SLOTS, CAP = 60, 4, 100.0
OUT = {}


def best_pair_removed(tr):
    if not tr:
        return None
    cnt = defaultdict(float)
    for x in tr:
        cnt[x['pair']] += x['usd50']
    bp = max(cnt, key=lambda k: cnt[k])
    rest = [x for x in tr if x['pair'] != bp]
    return round(sum(x['net50'] for x in rest) / len(rest), 3) if rest else None


def day_table(tr):
    by = defaultdict(lambda: [0, 0.0, None, None])
    for x in tr:
        d = B.day(x['t'])
        by[d][0] += 1
        by[d][1] += x['usd50']
        by[d][2] = x['t'] if by[d][2] is None else min(by[d][2], x['t'])
        by[d][3] = x['t'] if by[d][3] is None else max(by[d][3], x['t'])
    return {time.strftime('%m-%d', time.gmtime(d * 86400)): {'n': v[0], 'usd50': round(v[1], 2),
            'first': time.strftime('%H:%M', time.gmtime(v[2] / 1000)), 'last_entry': time.strftime('%H:%M', time.gmtime(v[3] / 1000))}
            for d, v in sorted(by.items())}


for name, c in CFG.items():
    OUT[name] = {}
    for part in ('train', 'holdout'):
        U = [p for p in PART[part] if p['liq'] >= 50_000 and p['fee'] <= c['fee'] and (not c['veto'] or not p['heat'])]
        cands = [p for p in U if c['sig'](p)]
        r = {'universe_points': len(U), 'candidates': len(cands)}
        for model in ('B2', 'B5', 'A'):
            tr, sk = B.book(cands, model, HOLD, slots=SLOTS)
            s = B.summ(tr, SPAN[part])
            s['best_pair_removed_net50'] = best_pair_removed(tr)
            s['skipped'] = sk
            r[model] = s
            if model == 'B2':
                tr8, _ = B.book(cands, model, HOLD, slots=8)
                r['B2_slots8'] = B.summ(tr8, SPAN[part], boot=False)
                trc, skc = B.book(cands, model, HOLD, slots=SLOTS, cap_usd=CAP)
                sc = B.summ(trc, SPAN[part], boot=False)
                sc['days'] = day_table(trc)
                sc['skipped'] = skc
                r['B2_daycap100'] = sc
                r['B2_net50_sd'] = round((sum((x['net50'] - s['mean_net50']) ** 2 for x in tr) / max(1, len(tr) - 1)) ** 0.5, 3) if tr else None
                if part == 'holdout':
                    json.dump([{'pair': x['pair'][:8], 't': x['t'], 'tx': x['tx'], 'net0': round(x['net0'], 3),
                                'net50': round(x['net50'], 3), 'fee': x['fee'], 'liq': round(x['liq']), 'flag': x['flag']}
                               for x in tr], open(os.path.join(B.HERE, 'holdout_trades_%s.json' % name), 'w'))
        # random control at matched density in the same universe (for signal configs)
        if c['dens'] is not None:
            rc = {}
            for salt in ('r1', 'r2', 'r3'):
                rnd = [p for p in U if B.coin(p['pair'], p['t'], c['dens'], salt)]
                for model in ('B2', 'A'):
                    tr, _ = B.book(rnd, model, HOLD, slots=SLOTS)
                    rc['%s_%s' % (salt, model)] = B.summ(tr, SPAN[part])
            r['random_control'] = rc
        OUT[name][part] = r
        b2 = r['B2']
        print('%-28s %-7s B2 n %5d tph %6.2f net50 %7.3f net0 %7.3f $/h %8.2f win50 %5.1f pairs %3d top %.3f CI %s | B5 %.3f | A %.3f (tph %.1f) | cap100 n %d tph %.2f $ %.1f' % (
            name, part, b2['n'], b2['tph'], b2['mean_net50'], b2['mean_net0'], b2['usd50_per_h'], b2['win50'], b2['pairs'],
            b2['top_pair_share'], b2.get('ci95_net50_pair'), r['B5'].get('mean_net50', float('nan')),
            r['A'].get('mean_net50', float('nan')), r['A'].get('tph', 0), r['B2_daycap100']['n'],
            r['B2_daycap100']['tph'], r['B2_daycap100'].get('sum_usd50', 0)), flush=True)
        if c['dens'] is not None:
            for k, v in r['random_control'].items():
                print('     random %s n %d tph %.2f net50 %.3f net0 %.3f CI %s' % (k, v['n'], v['tph'], v['mean_net50'], v['mean_net0'], v.get('ci95_net50_pair')))
json.dump(OUT, open(os.path.join(B.HERE, 'holdout_results.json'), 'w'), indent=1)
print('done', round(time.time() - t00, 1), 's')
