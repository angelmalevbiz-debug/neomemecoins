"""S2: achievable trades/hour of multi-slot HF PAPER books with honest next-refresh fills (read-only research).

Random entries (deterministic hashed coin, p = 0.25 per eligible observation) in the HF universe, because the
architecture question is throughput, cost and loss-cap behaviour, not a signal. Parameters are chosen on TRAIN
(first 60%); HOLDOUT is reported for exactly 3 configs pre-chosen by a fixed rule printed before they run.
"""
import json
import os
import pickle
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcommon as C
import hfsim

OUT = os.path.join(C.HERE, 'out')
TIERS = {'A': {'stop': 3.0, 'tp': 3.0}, 'B': {'stop': 5.0, 'tp': 5.0}}
# capacity mode: unlimited PAPER cash, no kill, no cap (throughput only); economic variants set start/cap/kill
BASE = {'notional': 50.0, 'cooldown_s': 120, 'heat': 1, 'prob': 0.25, 'start': 1e9, 'kill_floor': None}
ECON = {'start': 1000.0, 'kill_floor': 0.5, 'daily_cap_usd': 100.0}


def cfg(tier, slots, hold, **kw):
    c = dict(BASE)
    c.update(TIERS[tier])
    c.update({'tier': tier, 'slots': slots, 'hold_min': hold})
    c.update(kw)
    c['name'] = 'HF_RND_%s_s%d_h%g%s' % (tier, slots, hold, ''.join('_%s%s' % (k, v) for k, v in sorted(kw.items())))
    return c


def main():
    t0 = time.time()
    H = C.load_harness()
    series, meta = H.load()
    with open(os.path.join(OUT, 's1_elig.pkl'), 'rb') as fh:
        elig = pickle.load(fh)
    events = []
    for p in elig:
        for i, t in enumerate(series[p]['t']):
            events.append((t, p, i))
    events.sort()
    cut = H.split_t(meta)
    T0, T1 = meta['t0'], meta['t1']
    print('events', len(events), 'pairs', len(elig), round(time.time() - t0, 1), 's', flush=True)
    rows = {}
    act = {}
    for tier in ('A', 'B'):
        mins = set()
        for p, el in elig.items():
            ts = series[p]['t']
            a, h = el[tier], el['heat']
            for i in range(len(ts)):
                if a[i] and not h[i]:
                    mins.add(int(ts[i] // 60_000))
        act[tier] = mins
    def active_hours(tier, lo, hi):
        return sum(1 for m in act[tier] if lo // 60_000 <= m < hi // 60_000) / 60.0

    def run(c, part):
        lo, hi = (T0, cut) if part == 'train' else (cut, T1)
        res = hfsim.Book(H, series, elig, c, events, lo, hi).run()
        sm = hfsim.summarize(H, res, lo, hi, 1000.0)
        ah = active_hours(c['tier'], lo, hi)
        sm['active_hours'] = round(ah, 2)
        sm['trades_per_active_hour'] = round(sm.get('n', 0) / ah, 2) if ah else None
        rows[(c['name'], part)] = sm
        keep = ('n', 'trades_per_hour', 'active_hours', 'trades_per_active_hour', 'win_rate_net0', 'ci95_mean_usd50_pair', 'total_usd_booked', 'mean_net50_pct', 'mean_net0_pct', 'mean_booked_pct', 'mean_usd50',
                'usd50_per_hour', 'win_rate_net50', 'pairs', 'top_pair_share', 'max_drawdown_pct_net50',
                'avg_hold_s', 'avg_entry_lag_s', 'cancelled_entries', 'paused_days', 'pause_at_utc_hour', 'killed_after_h', 'exits')
        print(json.dumps({'cfg': c['name'], 'part': part, **{k: sm.get(k) for k in keep}}), flush=True)
        return sm

    # ---- TRAIN sweep (selection only)
    train = []
    for tier in ('A', 'B'):
        for slots in (4, 6, 8, 12):
            for hold in (2, 3, 5):
                c = cfg(tier, slots, hold)
                train.append((c, run(c, 'train')))
    # sensitivity on train: heat log-only, cooldown, loss memory variants, daily cap, notional
    sens = [cfg('B', 6, 3, heat=0), cfg('B', 6, 3, cooldown_s=60), cfg('B', 6, 3, cooldown_s=300),
            cfg('B', 6, 3, plm=(2, 6 * 3_600_000)), cfg('B', 6, 3, brake=(4, 30 * 60_000)),
            cfg('B', 6, 3, notional=100.0), cfg('B', 6, 3, notional=25.0), cfg('A', 6, 3, plm=(2, 6 * 3_600_000)),
            cfg('B', 6, 3, prob=1.0), cfg('A', 6, 3, prob=1.0),
            cfg('B', 6, 3, start=1000.0, daily_cap_usd=100.0), cfg('A', 6, 3, start=1000.0, daily_cap_usd=100.0),
            cfg('B', 6, 3, start=1000.0, kill_floor=0.5), cfg('A', 6, 3, start=1000.0, kill_floor=0.5),
            cfg('B', 6, 3, start=1000.0), cfg('B', 6, 3, start=5000.0, daily_cap_usd=250.0, kill_floor=0.5)]
    for c in sens:
        run(c, 'train')
    print('train done', round(time.time() - t0, 1), 's', flush=True)

    # ---- pre-chosen holdout configs (fixed rule, decided on train only)
    def first_reaching(tier, hold, target=50.0):
        for c, sm in train:
            if c['tier'] == tier and c['hold_min'] == hold and (sm.get('trades_per_hour') or 0) >= target:
                return c
        best = max((x for x in train if x[0]['tier'] == tier and x[0]['hold_min'] == hold),
                   key=lambda x: x[1].get('trades_per_hour') or 0)
        return best[0]
    ca = first_reaching('A', 3)
    cb = first_reaching('B', 3)
    cbc = dict(cb)
    cbc.update(ECON)
    cbc['name'] = cb['name'] + '_start1000_cap100_kill50'
    chosen = [ca, cb, cbc]
    print('HOLDOUT_CHOSEN', json.dumps([c['name'] for c in chosen]), flush=True)
    for c in chosen:
        run(c, 'holdout')
    with open(os.path.join(OUT, 's2_rows.json'), 'w') as fh:
        json.dump({'%s|%s' % k: v for k, v in rows.items()}, fh, indent=1, default=str)
    print('done', round(time.time() - t0, 1), 's', flush=True)


if __name__ == '__main__':
    main()
