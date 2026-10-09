"""FINAL (run once): the 3 configurations frozen on TRAIN (stage1/stage2 *_E grids, engine-equivalent guard) and
their random controls, on train and HOLDOUT. PAPER research only.

Frozen selection rules (train only):
  A best train net50 among heat-ON configs with train trades/hour >= 50          -> HF_A_QUIET_E95_HEAT
  B requested signal family (step momentum/reversion, 15-min high/low distance, buy share, volume acceleration,
    market move) with heat OFF, highest median train gap vs random across its stage-2 cells among family-universes
    that reach >= 50 trades/hour, then its cell with the best train gap at >= 50 trades/hour -> HF_B_NEAR15LOW_E95
  C best train net50 among 3-slot configs with train trades/hour >= 45            -> HF_C_QUIET_E95_3SLOT
Random controls: same universe, heat setting, exits, slots, cooldown; hashed coin p=0.25 per refresh event, 5 salts.
"""
import bisect, json, math, os, sqlite3, sys, time
from common import H, HERE, DEEP, fin, ab
import hfsim as S
from signals import UNIS, SIGS, rnd, RANDOM_P, RANDOM_SALTS
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
d = S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
TR_H = (cut - meta['t0']) / 3.6e6
HO_H = (meta['t1'] - cut) / 3.6e6
SIZE = 200.0
CONFIGS = [
    dict(name='HF_A_QUIET_E95_HEAT', uni='E95', sig='QUIET', heat_on=True, slots=5, hold_s=60, tp=None, sl=None),
    dict(name='HF_B_NEAR15LOW_E95_NOHEAT', uni='E95', sig='NEAR_15M_LOW', heat_on=False, slots=5, hold_s=120, tp=1.0, sl=1.0),
    dict(name='HF_C_QUIET_E95_NOHEAT_3SLOT', uni='E95', sig='QUIET', heat_on=False, slots=3, hold_s=120, tp=1.0, sl=None),
]
RESULTS = {'meta': {'train_h': TR_H, 'holdout_h': HO_H, 'cut': cut, 't0': meta['t0'], 't1': meta['t1']}, 'configs': []}


def run(cfg, sigf, window, cap=None, size=SIZE, occ='trigger'):
    kw = dict(heat_on=cfg['heat_on'], slots=cfg['slots'], hold_s=cfg['hold_s'], tp=cfg['tp'], sl=cfg['sl'],
              cooldown_s=60, size=size, cap_usd=cap, occ=occ)
    if window == 'train':
        kw['t_to'] = cut
    else:
        kw['t_from'] = cut
    return S.run_book(rows, UNIS[cfg['uni']], sigf, **kw)


def best_pair_removed(tr):
    cnt = {}
    for x in tr:
        cnt[x['pair']] = cnt.get(x['pair'], 0.0) + x['usd50']
    if not cnt:
        return None
    best = max(cnt, key=lambda p: cnt[p])
    rest = [x for x in tr if x['pair'] != best]
    return round(sum(x['net50'] for x in rest) / len(rest), 3) if rest else None


def cost_part(tr):
    xs = [x['gross'] - x['net50'] for x in tr if fin(x['gross'])]
    return round(sum(xs) / len(xs), 3) if xs else None


def per_day(tr):
    out = {}
    for x in tr:
        dd = S.day_of(x['decision_t'])
        o = out.setdefault(dd, [0, 0.0, 0.0])
        o[0] += 1
        o[1] += x['usd50']
        o[2] += x['net50']
    return {str(k): {'n': v[0], 'usd50': round(v[1], 2), 'mean_net50': round(v[2] / v[0], 3)} for k, v in sorted(out.items())}


for cfg in CONFIGS:
    res = {'config': cfg}
    for window, hrs in (('train', TR_H), ('holdout', HO_H)):
        tr, c = run(cfg, SIGS[cfg['sig']], window)
        st = S.stats(tr, hrs, cfg['slots'] * SIZE, ci=True)
        st['best_pair_removed_net50'] = best_pair_removed(tr)
        st['cost_pct'] = cost_part(tr)
        st['counters'] = c
        st['per_day'] = per_day(tr)
        rnds = []
        for salt in RANDOM_SALTS:
            rt, rc = run(cfg, rnd(RANDOM_P, salt), window)
            rs = S.stats(rt, hrs, cfg['slots'] * SIZE, ci=(salt == RANDOM_SALTS[0]))
            rs['cost_pct'] = cost_part(rt)
            rs['best_pair_removed_net50'] = best_pair_removed(rt)
            rnds.append(rs)
        keys = ('mean_net50', 'mean_net0', 'mean_gross', 'tph', 'usd50_per_hour', 'usd0_per_hour', 'win50', 'win0',
                'mean_usd50', 'cost_pct', 'pairs', 'top_pair_share', 'mdd_pct', 'hours_ge40', 'median_hold_s')
        rmean = {k: round(sum(r[k] for r in rnds) / len(rnds), 3) for k in keys}
        rmean['salt_net50'] = [r['mean_net50'] for r in rnds]
        rmean['ci95_net50_pair_salt0'] = rnds[0].get('ci95_net50_pair')
        st['gap_net50_vs_random'] = round(st['mean_net50'] - rmean['mean_net50'], 3)
        st['gap_gross_vs_random'] = round(st['mean_gross'] - rmean['mean_gross'], 3)
        st['gap_cost_vs_random'] = round(rmean['cost_pct'] - st['cost_pct'], 3)
        # pair-bootstrap CI of the gap: resample pairs of the signal book and of random salt 0 independently
        res[window] = {'book': st, 'random': rmean}
        print('== %s %s: n %d tph %.1f (hours>=40: %.2f) net50 %+.3f [%s] net0 %+.3f gross %+.3f cost %.3f | $/trade %+.2f $/h %+.0f (net0 $/h %+.0f) win50 %.1f win0 %.1f pairs %d top %.2f bestpair-removed %+.3f mdd %.0f%%' % (
            cfg['name'], window, st['n'], st['tph'], st['hours_ge40'], st['mean_net50'], st['ci95_net50_pair'],
            st['mean_net0'], st['mean_gross'], st['cost_pct'], st['mean_usd50'], st['usd50_per_hour'],
            st['usd0_per_hour'], st['win50'], st['win0'], st['pairs'], st['top_pair_share'],
            st['best_pair_removed_net50'] or 0, st['mdd_pct']))
        print('   random(5 salts): net50 %+.3f %s net0 %+.3f gross %+.3f cost %.3f tph %.1f $/h %+.0f win50 %.1f pairs %.0f | GAP net50 %+.3f (gross %+.3f, cost %+.3f)' % (
            rmean['mean_net50'], rmean['salt_net50'], rmean['mean_net0'], rmean['mean_gross'], rmean['cost_pct'],
            rmean['tph'], rmean['usd50_per_hour'], rmean['win50'], rmean['pairs'], st['gap_net50_vs_random'],
            st['gap_gross_vs_random'], st['gap_cost_vs_random']))
        print('   exits', st['exits'], 'per day', st['per_day'])
    # daily loss cap overlay on holdout: 10 % of book capital per UTC day
    cap = 0.10 * cfg['slots'] * SIZE
    tr, c = run(cfg, SIGS[cfg['sig']], 'holdout', cap=cap)
    days = {}
    for x in tr:
        dd = S.day_of(x['decision_t'])
        o = days.setdefault(dd, {'n': 0, 'usd50': 0.0, 'first': x['decision_t'], 'last': x['decision_t']})
        o['n'] += 1
        o['usd50'] += x['usd50']
        o['last'] = x['decision_t']
    res['holdout_cap'] = {'cap_usd': cap, 'n': len(tr), 'cap_blocked_signals': c['cap_block'],
                          'days': {str(k): {'n': v['n'], 'usd50': round(v['usd50'], 2),
                                            'active_min': round((v['last'] - v['first']) / 60000, 1)} for k, v in days.items()}}
    print('   holdout with daily cap $%.0f: trades %d, per UTC day %s' % (cap, len(tr), res['holdout_cap']['days']))
    RESULTS['configs'].append(res)

# --------------------------------------------------------------------- sizing and occupancy sensitivity (TRAIN only)
A = CONFIGS[0]
sz = {}
for size in (25.0, 50.0, 100.0, 200.0):
    tr, c = run(A, SIGS[A['sig']], 'train', size=size)
    st = S.stats(tr, TR_H, A['slots'] * size)
    sz[size] = {k: st[k] for k in ('n', 'tph', 'mean_net50', 'mean_net0', 'mean_usd50', 'usd50_per_hour', 'usd0_per_hour')}
    print('sizing (train, config A) $%.0f: n %d tph %.1f net50 %+.3f net0 %+.3f $/trade %+.3f $/h %+.1f; hours to a $100 cap %.2f' % (
        size, st['n'], st['tph'], st['mean_net50'], st['mean_net0'], st['mean_usd50'], st['usd50_per_hour'],
        100.0 / -st['usd50_per_hour'] if st['usd50_per_hour'] < 0 else float('inf')))
RESULTS['sizing_train_A'] = sz
tr, c = run(A, SIGS[A['sig']], 'train', occ='fill')
st = S.stats(tr, TR_H, A['slots'] * SIZE)
RESULTS['occupancy_fill_train_A'] = {k: st[k] for k in ('n', 'tph', 'mean_net50')}
print('occupancy=fill (train, config A): n %d tph %.1f net50 %+.3f' % (st['n'], st['tph'], st['mean_net50']))

# --------------------------------------------------------------------- tape check of fill honesty (all windows)
TAPE = os.path.join(DEEP, 'tape_snapshot.sqlite3')
SOL = 'So11111111111111111111111111111111111111112'
need = set()
book_trades = {}
for cfg in CONFIGS:
    for label, sigf in (('book', SIGS[cfg['sig']]), ('random', rnd(RANDOM_P, RANDOM_SALTS[0]))):
        tr1, _ = run(cfg, sigf, 'train')
        tr2, _ = run(cfg, sigf, 'holdout')
        book_trades[(cfg['name'], label)] = tr1 + tr2
        need |= {x['pair'] for x in tr1 + tr2}
con = sqlite3.connect('file:%s?mode=ro' % TAPE, uri=True)
tape = {}
for pair, et, payload in con.execute('select pair, event_time, payload from events'):
    if pair not in need:
        continue
    try:
        dd = json.loads(payload)
    except ValueError:
        continue
    if (dd.get('quote_asset') or '') != SOL:
        continue
    ta, qa = dd.get('token_amount'), dd.get('quote_amount')
    if not (ta and qa and ta > 0 and qa > 0):
        continue
    tape.setdefault(pair, []).append((et, dd.get('direction') == 'BUY', qa / ta))
con.close()


class Mid:
    def __init__(self, ev):
        ev.sort()
        self.bt = [e[0] for e in ev if e[1]]
        self.bp = [e[2] for e in ev if e[1]]
        self.st = [e[0] for e in ev if not e[1]]
        self.sp = [e[2] for e in ev if not e[1]]

    def at(self, t, win=20_000):
        a = bisect.bisect_right(self.bt, t) - 1
        b = bisect.bisect_right(self.st, t) - 1
        if a < 0 or b < 0 or t - self.bt[a] > win or t - self.st[b] > win:
            return None
        return math.sqrt(self.bp[a] * self.sp[b])


mids = {p: Mid(v) for p, v in tape.items()}
RESULTS['tape'] = {}
for key, trs in book_trades.items():
    diffs = []
    for x in trs:
        m = mids.get(x['pair'])
        if m is None or x['flag'] or not fin(x['gross']):
            continue
        a = m.at(x['decision_t'] + 2000)
        b = m.at(x['sell_t'] + 2000)
        if a and b:
            diffs.append(x['gross'] - 100 * (b / a - 1))
    if diffs:
        diffs.sort()
        r = {'n_checked': len(diffs), 'n_trades': len(trs), 'optimism_mean_pct': round(sum(diffs) / len(diffs), 3),
             'optimism_median_pct': round(diffs[len(diffs) // 2], 3),
             'mean_abs_diff_pct': round(sum(abs(v) for v in diffs) / len(diffs), 3)}
    else:
        r = {'n_checked': 0, 'n_trades': len(trs)}
    RESULTS['tape']['%s|%s' % key] = r
    print('tape check %-30s %-6s %s' % (key[0], key[1], r))
json.dump(RESULTS, open(os.path.join(HERE, 'final_eval.json'), 'w'), indent=1, default=str)
print('secs', round(time.time() - T0, 1))
