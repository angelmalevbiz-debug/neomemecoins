"""grid_train: TRAIN-only grid of tape buy-burst scalp books (4 slots, $100, one position per pool, time exit).
Selection basis: fill model B2 (on-chain mid 2 s after the decision), calibrated net50. PAPER research only.
Writes grid_train.json (all rows) and prints the leaders."""
import json, os, sys, time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import booklib as B

t00 = time.time()
D = B.data()
PTS, CUT, T0 = D['points'], D['cut'], D['t0']
SPAN = (CUT - T0) / 3.6e6
TR = sorted([p for p in PTS if p['t'] < CUT], key=lambda p: (p['t'], p['pair']))
print('train points', len(TR), 'span h', round(SPAN, 2))

FEES = {'le50': lambda f: f <= 50, '55_95': lambda f: 50 < f <= 95, 'le95': lambda f: f <= 95}


def universe(p, fee, heat):
    return p['liq'] >= 50_000 and FEES[fee](p['fee']) and (heat == 'off' or not p['heat'])


def mk_sig(W, ub, net, top, chase):
    def f(p):
        fl = p[W]
        if fl is None:
            return False
        if fl['ub'] < ub:
            return False
        if net == 0 and not fl['net_sol'] > 0:
            return False
        if net > 0 and not fl['net_sol'] >= net:
            return False
        if top < 1 and not (fl['top_share'] < top):
            return False
        if chase and not (p['tret60'] <= 2.0):
            return False
        return True
    return f


SIGS = {'ALL': lambda p: True}
for W in ('f30', 'f60'):
    for ub in (2, 3, 4, 6, 8, 12):
        for net in (0, 1.0):
            for top in (0.5, 1.01):
                for chase in (False, True):
                    SIGS['%s_ub%d_net%s_top%s%s' % (W, ub, net, top, '_nochase' if chase else '')] = mk_sig(W, ub, net, top, chase)
print('signals', len(SIGS))

rows = []
for fee in FEES:
    for heat in ('veto', 'off'):
        U = [p for p in TR if universe(p, fee, heat)]
        for sname, sf in SIGS.items():
            cands = [p for p in U if sf(p)]
            if len(cands) < 30:
                continue
            for hold in (60, 120, 180, 300):
                tr, sk = B.book(cands, 'B2', hold, slots=4)
                s = B.summ(tr, SPAN, boot=False)
                rows.append({'fee': fee, 'heat': heat, 'sig': sname, 'hold': hold, 'n_univ': len(U),
                             'n_cand': len(cands), **s})
        print('done', fee, heat, 'universe pts', len(U), 'rows', len(rows), round(time.time() - t00, 1), 's', flush=True)

json.dump(rows, open(os.path.join(B.HERE, 'grid_train.json'), 'w'))
ok = [r for r in rows if r.get('tph', 0) >= 45]
print('configs', len(rows), 'with train tph >= 45:', len(ok))
ok.sort(key=lambda r: -r['mean_net50'])
print('\nTOP 25 by train mean net50 (B2) with tph >= 45')
for r in ok[:25]:
    print(json.dumps({k: r[k] for k in ('fee', 'heat', 'sig', 'hold', 'n_cand', 'n', 'tph', 'mean_net50', 'mean_net0',
                                         'usd50_per_h', 'pairs', 'top_pair_share', 'fb_share')}))
print('\nBest per (fee, heat) with tph >= 45:')
best = {}
for r in ok:
    k = (r['fee'], r['heat'])
    if k not in best:
        best[k] = r
for k, r in best.items():
    print(k, json.dumps({kk: r[kk] for kk in ('sig', 'hold', 'n', 'tph', 'mean_net50', 'mean_net0', 'pairs', 'top_pair_share')}))
print('\nALL-signal (trade every decision moment) rows:')
for r in rows:
    if r['sig'] == 'ALL':
        print(json.dumps({kk: r[kk] for kk in ('fee', 'heat', 'hold', 'n', 'tph', 'mean_net50', 'mean_net0', 'pairs', 'top_pair_share')}))
print('max tph overall', max(r['tph'] for r in rows))
print('done', round(time.time() - t00, 1), 's')
