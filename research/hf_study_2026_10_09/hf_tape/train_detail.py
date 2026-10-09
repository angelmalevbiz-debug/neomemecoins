"""train_detail: TRAIN-only. Burst strength vs outcome, random controls at matched density, slot/cooldown effects.
PAPER research only."""
import json, os, sys, time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import booklib as B

rows = json.load(open(os.path.join(B.HERE, 'grid_train.json')))
K = ('n_cand', 'n', 'tph', 'mean_net50', 'mean_net0', 'pairs', 'top_pair_share')
print('== burst strength (no chase filter off), hold 60 / 120, net>=1 SOL, top<0.5')
for fee in ('le95', 'le50', '55_95'):
    for heat in ('off', 'veto'):
        for W in ('f30', 'f60'):
            for hold in (60, 120):
                line = []
                for ub in (2, 3, 4, 6, 8, 12):
                    nm = '%s_ub%d_net1.0_top0.5' % (W, ub)
                    r = next((x for x in rows if x['fee'] == fee and x['heat'] == heat and x['sig'] == nm and x['hold'] == hold), None)
                    line.append('ub%d:%s' % (ub, ('n%d tph%.1f %.2f' % (r['n'], r['tph'], r['mean_net50'])) if r else '-'))
                a = next((x for x in rows if x['fee'] == fee and x['heat'] == heat and x['sig'] == 'ALL' and x['hold'] == hold), None)
                print('%s %s %s h%d | ALL n%s %.2f | %s' % (fee, heat, W, hold, a['n'] if a else '-', a['mean_net50'] if a else float('nan'), ' '.join(line)))

print('\n== best strict-burst rows (ub>=4, top<0.5, net>=1) by train net50, any rate, n>=100')
strict = [r for r in rows if r['sig'] != 'ALL' and int(r['sig'].split('_ub')[1].split('_')[0]) >= 4 and 'top0.5' in r['sig']
          and 'net1.0' in r['sig'] and r['n'] >= 100]
strict.sort(key=lambda r: -r['mean_net50'])
for r in strict[:12]:
    print(json.dumps({k: r[k] for k in ('fee', 'heat', 'sig', 'hold') + K}))

# ---------------------------------------------------------------- random controls on TRAIN for candidate configs
D = B.data()
PTS, CUT, T0 = D['points'], D['cut'], D['t0']
SPAN = (CUT - T0) / 3.6e6
TR = sorted([p for p in PTS if p['t'] < CUT], key=lambda p: (p['t'], p['pair']))
FEES = {'le50': lambda f: f <= 50, '55_95': lambda f: 50 < f <= 95, 'le95': lambda f: f <= 95}


def U(fee, heat):
    return [p for p in TR if p['liq'] >= 50_000 and FEES[fee](p['fee']) and (heat == 'off' or not p['heat'])]


def sig(W, ub, net, top, chase):
    def f(p):
        fl = p[W]
        if fl is None or fl['ub'] < ub:
            return False
        if net == 0 and not fl['net_sol'] > 0:
            return False
        if net > 0 and not fl['net_sol'] >= net:
            return False
        if top < 1 and not fl['top_share'] < top:
            return False
        if chase and not p['tret60'] <= 2.0:
            return False
        return True
    return f


cands = {
    'C1_burst60_loose_noveto': ('le95', 'off', sig('f60', 2, 0, 1.01, True), 60),
    'C1b_burst30_loose_noveto': ('le95', 'off', sig('f30', 2, 0, 1.01, True), 60),
    'C3_all_veto': ('le95', 'veto', lambda p: True, 60),
}
if strict:
    r = strict[0]
    parts = r['sig'].split('_')
    W = parts[0]
    ub = int(parts[1][2:])
    cands['C2_strict_best'] = (r['fee'], r['heat'], sig(W, ub, 1.0, 0.5, r['sig'].endswith('nochase')), r['hold'])
for name, (fee, heat, sf, hold) in cands.items():
    u = U(fee, heat)
    c = [p for p in u if sf(p)]
    dens = len(c) / len(u)
    print('\n==', name, fee, heat, 'hold', hold, 'universe', len(u), 'cands', len(c), 'density', round(dens, 3))
    for model in ('B2', 'B5', 'A'):
        tr, sk = B.book(c, model, hold, slots=4)
        print('  SIGNAL', model, json.dumps(B.summ(tr, SPAN)), sk)
    for salt in ('r1', 'r2', 'r3'):
        rc = [p for p in u if B.coin(p['pair'], p['t'], dens, salt)]
        tr, sk = B.book(rc, 'B2', hold, slots=4)
        print('  RANDOM', salt, 'B2', json.dumps(B.summ(tr, SPAN)))
    for slots in (2, 8, 16):
        tr, sk = B.book(c, 'B2', hold, slots=slots)
        s = B.summ(tr, SPAN, boot=False)
        print('  slots', slots, 'n', s['n'], 'tph', s['tph'], 'net50', s['mean_net50'], sk)
    for cd in (30, 60, 120):
        tr, sk = B.book(c, 'B2', hold, slots=4, cooldown_s=cd)
        s = B.summ(tr, SPAN, boot=False)
        print('  cooldown', cd, 'n', s['n'], 'tph', s['tph'], 'net50', s['mean_net50'])
