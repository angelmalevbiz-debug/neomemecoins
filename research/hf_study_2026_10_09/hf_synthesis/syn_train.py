"""hf_synthesis TRAIN-only selection of the shared HF book rules and train screen of the candidate books.
PAPER research only. Never reads holdout outcomes (every run_book call is bounded by t_to = cut).

Frozen selection rule for the SHARED book rules (written before running):
  candidates: hold {60,120} s x slots {3,4,5,6} x pacing {none, gap60, gap72, bucket72x3} x pool cooldown
  {60,120,300} s x loss brake {off, 4 consecutive booked losses -> 30 min}; $25 notional; heat veto enforced;
  universe E95 (engine-equivalent structural guard, liq >= $50k, fee <= 95 bps); slot busy until the exit fill.
  Scored on the RANDOM control (hashed coin p=0.25 per refresh event, 5 salts, mean).
  Keep configs with mean train rate in [47, 60] trades/h and top-pair share <= 0.20.
  Choose the lowest booked loss per hour; ties within 2 % -> fewer slots, then 'gap' pacing before 'bucket'
  before 'none', then hold 60 before 120, then the longer cooldown, then brake on.
Then (train only) the candidate hypothesis/reference books run under the chosen shared rules.
"""
import json, os, sys, time
import syn_lib as L

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
T0 = time.time()
d = L.S.events()
rows, cut, meta = d['rows'], d['cut'], d['meta']
t0 = meta['t0']
OUT = os.path.join(L.HERE, 'train')
os.makedirs(OUT, exist_ok=True)
SALTS = ('hfA', 'hfB', 'hfC', 'hfD', 'hfE')
P = 0.25

# ---------------------------------------------------------------- parity with hf_signals/hfsim.run_book (occ='fill')
uni = L.UNIVERSES['E95']
a, _ = L.run_book(rows, uni, L.rnd(P, 'hfA'), slots=5, hold_s=60, cooldown_s=60, size=25.0, t_to=cut, refresh_ms=1)
b, _ = L.S.run_book(rows, uni, L.rnd(P, 'hfA'), heat_on=True, slots=5, hold_s=60, tp=None, sl=None, cooldown_s=60,
                    size=25.0, occ='fill', t_to=cut)
ka = {(x['pair'], x['i']): x for x in a}
kb = {(x['pair'], x['i']): x for x in b}
mism = sum(1 for k in ka if k not in kb or abs(ka[k]['net50'] - kb[k]['net50']) > 1e-9
           or abs(ka[k]['net0'] - kb[k]['net0']) > 1e-9 or ka[k]['exit_t'] != kb[k]['exit_t'])
mism += sum(1 for k in kb if k not in ka)
print('PARITY vs hfsim.run_book(occ=fill): n %d vs %d, mismatches %d' % (len(a), len(b), mism), flush=True)

# pre-filter rows for speed (universe and heat are book-independent filters)
E95 = [r for r in rows if r['t'] < cut and uni(r) and not r['heat']]
print('train E95 heat-passing refresh events', len(E95), 'pairs', len({r['pair'] for r in E95}), flush=True)


def tag(cfg):
    pc = cfg['pacing']
    ps = 'none' if pc[0] == 'none' else ('gap%d' % pc[1] if pc[0] == 'gap' else 'bucket%dx%d' % (pc[1], pc[2]))
    br = 'brake4x30' if cfg['brake'] else 'nobrake'
    return 'h%d_s%d_%s_cd%d_%s' % (cfg['hold_s'], cfg['slots'], ps, cfg['cooldown_s'], br)


grid = []
for hold in (60, 120):
    for slots in (3, 4, 5, 6):
        for pacing in (('none',), ('gap', 60), ('gap', 72), ('bucket', 72, 3)):
            for cd in (60, 120, 300):
                for brake in (None, (4, 1_800_000)):
                    grid.append(dict(hold_s=hold, slots=slots, pacing=pacing, cooldown_s=cd, brake=brake))
res = []
with open(os.path.join(OUT, 'grid_control.jsonl'), 'w') as fh:
    for cfg in grid:
        per = []
        for salt in SALTS:
            tr, c = L.run_book(E95, lambda r: True, L.rnd(P, salt), slots=cfg['slots'], hold_s=cfg['hold_s'],
                               cooldown_s=cfg['cooldown_s'], pacing=cfg['pacing'], brake=cfg['brake'], size=25.0,
                               heat_on=False, t_to=cut)
            per.append(L.stats(tr, t0, cut))
        keys = ('tph', 'hour_p10', 'hour_median', 'hours_ge40', 'pairs', 'top_pair_share', 'mean_net50', 'mean_booked',
                'mean_net0', 'usd50_per_trade', 'booked_usd_per_trade', 'usd50_per_hour', 'booked_usd_per_hour',
                'win50', 'fee_le50_share', 'reentry_le60s_share', 'reentry_le300s_share', 'mean_cycle_s')
        m = {k: round(sum(p[k] for p in per) / len(per), 3) for k in keys}
        row = {'tag': tag(cfg), 'cfg': {**cfg, 'pacing': list(cfg['pacing']), 'brake': list(cfg['brake']) if cfg['brake'] else None},
               'control_mean': m, 'salt_net50': [p['mean_net50'] for p in per], 'salt_tph': [p['tph'] for p in per]}
        res.append(row)
        fh.write(json.dumps(row) + '\n')
print('grid rows', len(res), 'secs', round(time.time() - T0, 1), flush=True)

PACE_ORDER = {'gap': 0, 'bucket': 1, 'none': 2}
ok = [r for r in res if 47 <= r['control_mean']['tph'] <= 60 and r['control_mean']['top_pair_share'] <= 0.20]
print('configs inside [47,60]/h with top-pair <= 0.20:', len(ok))
best_loss = max(r['control_mean']['booked_usd_per_hour'] for r in ok)        # least negative


def tiebreak(r):
    cfg = r['cfg']
    return (cfg['slots'], PACE_ORDER[cfg['pacing'][0]], cfg['hold_s'], -cfg['cooldown_s'], 0 if cfg['brake'] else 1)


near = [r for r in ok if r['control_mean']['booked_usd_per_hour'] >= best_loss * 1.02]
chosen = sorted(near, key=tiebreak)[0]
print('CHOSEN shared rules:', chosen['tag'], json.dumps(chosen['control_mean']))
for r in sorted(ok, key=lambda r: -r['control_mean']['booked_usd_per_hour'])[:25]:
    m = r['control_mean']
    print('  %-34s tph %5.1f p10h %4s ge40 %.2f net50 %+.3f booked %+.3f $/tr50 %+.3f bk$/h %+.2f 50$/h %+.2f pairs %4.1f top %.3f le50 %.2f re60 %.2f re300 %.2f cyc %.0f' % (
        r['tag'], m['tph'], m['hour_p10'], m['hours_ge40'], m['mean_net50'], m['mean_booked'], m['usd50_per_trade'],
        m['booked_usd_per_hour'], m['usd50_per_hour'], m['pairs'], m['top_pair_share'], m['fee_le50_share'],
        m['reentry_le60s_share'], m['reentry_le300s_share'], m['mean_cycle_s']))
print('-- rate landscape (control mean tph) by hold/slots/pacing at cd120 nobrake / brake:')
for r in res:
    c = r['cfg']
    if c['cooldown_s'] == 120:
        m = r['control_mean']
        print('  %-34s tph %5.1f p10h %4s net50 %+.3f bk$/h %+.2f top %.3f' % (r['tag'], m['tph'], m['hour_p10'],
              m['mean_net50'], m['booked_usd_per_hour'], m['top_pair_share']))
json.dump({'chosen': chosen, 'n_ok': len(ok)}, open(os.path.join(OUT, 'chosen_shared_rules.json'), 'w'), indent=1)

# ---------------------------------------------------------------- candidate books under the chosen shared rules (TRAIN)
cfg = chosen['cfg']
pac = tuple(cfg['pacing'])
brk = tuple(cfg['brake']) if cfg['brake'] else None
BOOKS = [
    ('RND_E95', 'E95', 'RND', True),
    ('QUIET_E95', 'E95', 'QUIET', True),
    ('NEAR15LOW_E95', 'E95', 'NEAR15LOW', True),
    ('NEAR15LOW_E95_HEATLOG', 'E95', 'NEAR15LOW', False),
    ('RND_E95_HEATLOG', 'E95', 'RND', False),
    ('RND_L50', 'L50', 'RND', True),
    ('RND_E50', 'E50', 'RND', True),
    ('QUIET_L50', 'L50', 'QUIET', True),
]
screen = {}
with open(os.path.join(OUT, 'books_train.jsonl'), 'w') as fh:
    for name, u, pred, heat in BOOKS:
        preds = [(s, L.rnd(P, s)) for s in SALTS] if pred == 'RND' else [('sig', L.PREDICATES[pred])]
        per, trs = [], []
        for label, pf in preds:
            tr, c = L.run_book(rows, L.UNIVERSES[u], pf, slots=cfg['slots'], hold_s=cfg['hold_s'],
                               cooldown_s=cfg['cooldown_s'], pacing=pac, brake=brk, size=25.0, heat_on=heat, t_to=cut)
            st = L.stats(tr, t0, cut, ci=(label in ('sig', 'hfA')))
            st['counters'] = c
            per.append(st)
            trs.append(tr)
        screen[name] = (per, trs)
        m = {k: round(sum(p[k] for p in per) / len(per), 3) for k in ('tph', 'mean_net50', 'mean_booked', 'mean_net0',
             'mean_gross', 'mean_dp50', 'usd50_per_trade', 'booked_usd_per_hour', 'usd50_per_hour', 'pairs',
             'top_pair_share', 'fee_le50_share', 'hours_ge40', 'hour_p10', 'reentry_le60s_share', 'win50')}
        fh.write(json.dumps({'book': name, 'mean': m, 'per': per}) + '\n')
        print('BOOK %-22s tph %5.1f p10h %5.1f ge40 %.2f net50 %+.3f booked %+.3f net0 %+.3f gross %+.3f dp50 %+.3f $/tr %+.3f bk$/h %+.2f 50$/h %+.2f pairs %4.1f top %.3f le50 %.2f re60 %.2f win50 %.2f ci %s' % (
            name, m['tph'], m['hour_p10'], m['hours_ge40'], m['mean_net50'], m['mean_booked'], m['mean_net0'],
            m['mean_gross'], m['mean_dp50'], m['usd50_per_trade'], m['booked_usd_per_hour'], m['usd50_per_hour'],
            m['pairs'], m['top_pair_share'], m['fee_le50_share'], m['reentry_le60s_share'], m['win50'],
            per[0].get('ci95_net50_pct_pair')), flush=True)
ctrl = screen['RND_E95'][1][0]
for name in ('QUIET_E95', 'NEAR15LOW_E95', 'RND_L50', 'RND_E50', 'QUIET_L50'):
    tr = screen[name][1][0]
    print('  gap vs RND_E95(hfA) %-16s net50 %s gross %s' % (name, L.gap_ci(tr, ctrl, 'net50'), L.gap_ci(tr, ctrl, 'gross')))
tr = screen['NEAR15LOW_E95_HEATLOG'][1][0]
print('  gap NEAR15LOW_HEATLOG vs RND_E95_HEATLOG(hfA) net50 %s gross %s' % (
    L.gap_ci(tr, screen['RND_E95_HEATLOG'][1][0], 'net50'), L.gap_ci(tr, screen['RND_E95_HEATLOG'][1][0], 'gross')))
print('secs', round(time.time() - T0, 1))
