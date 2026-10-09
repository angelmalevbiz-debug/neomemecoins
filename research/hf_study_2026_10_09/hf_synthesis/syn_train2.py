"""hf_synthesis TRAIN-only refinement (v2) of the shared HF book rules. PAPER research only; t_to = cut everywhere.

Why v2 (decided on train evidence only, before any holdout run): the v1 grid (syn_train.out) showed that
  * the 4-booked-loss / 30-min brake cuts the E95 random book to ~19/h (about 99 % of HF closes lose on the booked
    basis, so it is a 4-trades-per-pool limit, not a loss memory);
  * fixed-gap pacing (60/72 s) wastes slot time with random candidate arrival (~30/h at gap60);
  * the v1 rate band was measured over all clock hours, ~28 % of which had (almost) no eligible pool.
v2 grid: hold {60,120} s x slots {3,4,5} x pool cooldown {60,120,300} s x governor {none, <=50 and <=60 orders in
any trailing 60 min} x adverse-move brake {off, gross <= -3 % or <= -5 % -> pool blocked 30 min}; $25; heat enforced;
E95; slot busy until the exit fill; one order per 2-s refresh.
Frozen selection rule v2:
  keep configs where (i) the random control's mean rate over LIVE hours (>= 3 distinct eligible pools refreshing in
  that clock hour) is in [45, 62]/h, (ii) QUIET and NEAR15LOW (heat on) each reach >= 40/h over live hours, (iii) the
  control's top-pair share <= 0.20; choose the lowest control booked loss per hour (full window); ties within 2 % ->
  fewer slots, hold 60 before 120, governor 60 before 50 before none, longer cooldown, brake -3 % before -5 % before off.
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
uni = L.UNIVERSES['E95']
E95 = [r for r in rows if r['t'] < cut and uni(r) and not r['heat']]
LIVE = L.live_hours(rows, uni, t0, cut)
h0, h1 = int(-(-t0 // L.HOUR)), int(cut // L.HOUR)
print('train full hours %d, live hours (>=3 eligible pools) %d' % (h1 - h0, len(LIVE)), flush=True)


def tag(c):
    g = c['gov']
    gs = 'gov%d' % g[2] if g[0] == 'roll' else 'nogov'
    b = 'gb%d' % int(c['gbrake'][0]) if c['gbrake'] else 'nogb'
    return 'h%d_s%d_cd%d_%s_%s' % (c['hold_s'], c['slots'], c['cooldown_s'], gs, b)


def run(c, pred, rws=E95, heat=False, cap=None, t_from=None, t_to=cut):
    return L.run_book(rws, lambda r: True if rws is E95 else uni(r), pred, slots=c['slots'], hold_s=c['hold_s'],
                      cooldown_s=c['cooldown_s'], pacing=c['gov'], gross_brake=c['gbrake'], size=25.0,
                      heat_on=heat, cap_usd=cap, t_from=t_from, t_to=t_to)


KEYS = ('tph', 'tph_live', 'live_hour_p10', 'live_hour_median', 'live_hours_ge40', 'max_hour', 'pairs',
        'top_pair_share', 'mean_net50', 'mean_booked', 'mean_net0', 'mean_gross', 'usd50_per_trade',
        'booked_usd_per_trade', 'usd50_per_hour', 'booked_usd_per_hour', 'win50', 'fee_le50_share',
        'reentry_le60s_share', 'reentry_le300s_share', 'mean_cycle_s', 'mdd_booked_usd')
grid = []
for hold in (60, 120):
    for slots in (3, 4, 5):
        for cd in (60, 120, 300):
            for gov in (('none',), ('roll', 3600, 50), ('roll', 3600, 60)):
                for gb in (None, (-3.0, 1_800_000), (-5.0, 1_800_000)):
                    grid.append(dict(hold_s=hold, slots=slots, cooldown_s=cd, gov=gov, gbrake=gb))
res = []
with open(os.path.join(OUT, 'grid2.jsonl'), 'w') as fh:
    for c in grid:
        per = []
        for salt in SALTS:
            tr, _ = run(c, L.rnd(P, salt))
            per.append(L.stats(tr, t0, cut, live=LIVE))
        m = {k: round(sum(p[k] for p in per) / len(per), 3) for k in KEYS}
        hyp = {}
        for name in ('QUIET', 'NEAR15LOW'):
            tr, _ = run(c, L.PREDICATES[name])
            st = L.stats(tr, t0, cut, live=LIVE)
            hyp[name] = {k: st[k] for k in KEYS}
        row = {'tag': tag(c), 'cfg': {**c, 'gov': list(c['gov']), 'gbrake': list(c['gbrake']) if c['gbrake'] else None},
               'control': m, 'salt_net50': [p['mean_net50'] for p in per], 'hyp': hyp}
        res.append(row)
        fh.write(json.dumps(row) + '\n')
print('grid rows', len(res), 'secs', round(time.time() - T0, 1), flush=True)

GOV = {60: 0, 50: 1}


def tb(r):
    c = r['cfg']
    g = GOV.get(c['gov'][2], 2) if c['gov'][0] == 'roll' else 2
    b = {-3.0: 0, -5.0: 1}.get(c['gbrake'][0], 2) if c['gbrake'] else 2
    return (c['slots'], c['hold_s'], g, -c['cooldown_s'], b)


ok = [r for r in res if 45 <= r['control']['tph_live'] <= 62 and r['control']['top_pair_share'] <= 0.20
      and r['hyp']['QUIET']['tph_live'] >= 40 and r['hyp']['NEAR15LOW']['tph_live'] >= 40]
print('eligible configs', len(ok))
best = max(r['control']['booked_usd_per_hour'] for r in ok)
near = [r for r in ok if r['control']['booked_usd_per_hour'] >= best * 1.02]
chosen = sorted(near, key=tb)[0]
print('CHOSEN v2:', chosen['tag'])
json.dump({'chosen': chosen, 'eligible': [r['tag'] for r in ok]}, open(os.path.join(OUT, 'chosen_v2.json'), 'w'), indent=1)


def line(name, m):
    return ('  %-30s tph %5.1f live %5.1f p10L %4s medL %4s ge40L %.2f max %3s net50 %+.3f bk %+.3f net0 %+.3f gr %+.3f '
            '$/tr %+.3f bk$/h %+.2f 50$/h %+.2f pairs %4.1f top %.3f le50 %.2f re60 %.2f re300 %.2f cyc %.0f mddbk %.0f' % (
                name, m['tph'], m['tph_live'], m['live_hour_p10'], m['live_hour_median'], m['live_hours_ge40'],
                m['max_hour'], m['mean_net50'], m['mean_booked'], m['mean_net0'], m['mean_gross'], m['usd50_per_trade'],
                m['booked_usd_per_hour'], m['usd50_per_hour'], m['pairs'], m['top_pair_share'], m['fee_le50_share'],
                m['reentry_le60s_share'], m['reentry_le300s_share'], m['mean_cycle_s'], m['mdd_booked_usd']))


for r in sorted(ok, key=lambda r: -r['control']['booked_usd_per_hour'])[:30]:
    print(r['tag'])
    print(line('control(5 salts)', r['control']))
    print(line('QUIET', r['hyp']['QUIET']))
    print(line('NEAR15LOW', r['hyp']['NEAR15LOW']))
print('--- landscape: hold 60, slots 4, all cooldown/gov/brake')
for r in res:
    c = r['cfg']
    if c['hold_s'] == 60 and c['slots'] == 4:
        print(line(r['tag'], r['control']), '| Q live %.1f N live %.1f' % (r['hyp']['QUIET']['tph_live'], r['hyp']['NEAR15LOW']['tph_live']))
print('secs', round(time.time() - T0, 1))
