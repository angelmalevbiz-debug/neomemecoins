"""Extra adversarial diagnostics for F5_C1 (rug_guard_v1): time-local random entries, universe activity per 3 h
block, jackknife / leave-one-pair-out on the holdout, pool-population shift between train and holdout."""
import json, os, pickle, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

H, TF = C.H, C.TF
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def main():
    series, meta = H.load()
    t0, t1 = meta['t0'], meta['t1']
    cut = H.split_t(meta)
    with open(os.path.join(C.OUT, 'base_guard.pkl'), 'rb') as fh:
        T = pickle.load(fh)
    tr = [x for x in T if x['entry_t'] < cut]
    ho = [x for x in T if x['entry_t'] >= cut]
    res = {}

    # 1) holdout trade list
    res['holdout_trades'] = [{'sym_pair': C.short(x), 'entry_h': round((x['entry_t'] - t0) / 3.6e6, 2),
                              'net50': round(x['net50'], 2), 'net0': round(x['net0'], 2), 'reason': x['reason'],
                              'hold_min': round(x['hold_s'] / 60, 1), 'mfe': round(x['mfe'], 2),
                              'mae': round(x['mae'], 2), 'fee': x['fee_bps'], 'liq': int(x['liq']),
                              'size': round(x['size'], 1)} for x in ho]
    for r in res['holdout_trades']:
        print('  ', r)

    # 2) jackknife and leave-one-pair-out on holdout
    jk = []
    for k in range(len(ho)):
        rest = ho[:k] + ho[k + 1:]
        jk.append(sum(x['usd50'] for x in rest) / len(rest))
    lopo = {}
    for p in sorted(set(x['pair'] for x in ho)):
        rest = [x for x in ho if x['pair'] != p]
        lopo[C.short(next(x for x in ho if x['pair'] == p))] = C.stats(rest)
    res['jackknife_holdout_mean_usd'] = {'min': round(min(jk), 3), 'max': round(max(jk), 3)}
    res['leave_one_pair_out_holdout'] = lopo
    print('jackknife', res['jackknife_holdout_mean_usd'])
    for k, v in lopo.items():
        print('  LOPO without', k, v)

    # 3) pool population shift (fee / liquidity of entries)
    def pop(S):
        if not S:
            return {}
        liq = sorted(x['liq'] for x in S)
        fee = sorted(x['fee_bps'] for x in S)
        return {'n': len(S), 'liq_min': int(liq[0]), 'liq_med': int(liq[len(liq) // 2]), 'liq_max': int(liq[-1]),
                'fee_min': fee[0], 'fee_med': fee[len(fee) // 2], 'fee_max': fee[-1],
                'n_liq_ge_190k': sum(1 for v in liq if v >= 190_000), 'n_fee_le_90': sum(1 for v in fee if v <= 90)}
    res['population'] = {'train': pop(tr), 'holdout': pop(ho)}
    print('population', res['population'])
    # sub-universe check: same signal restricted to liq >= 190k (a post-hoc description of the holdout pools)
    T190 = H.simulate(lambda P: P('liq') >= 190_000 and C.make_signal()(P), **C.make_kwargs())
    a, b = C.split(T190)
    res['post_hoc_liq_ge_190k'] = {'train': C.stats(a), 'holdout': C.stats(b)}
    print('liq>=190k', res['post_hoc_liq_ge_190k'])

    # 4) universe activity per 3 h block: eligible points / pairs (guarded universe + no-chase), signal fires
    u = C.universe_nc(dict(C.DEFAULTS))
    sig = C.make_signal()
    nb = int((t1 - t0) // (3 * 3_600_000)) + 1
    elig = [0] * nb
    fires = [0] * nb
    epairs = [set() for _ in range(nb)]
    tape_pairs = [set() for _ in range(nb)]
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        has_tape = TF.tape().get(pair) is not None
        for i in range(0, len(ts) - 1, 3):   # every 3rd point (~15 s) for speed
            b_ = int((ts[i] - t0) // (3 * 3_600_000))
            P = H.Past(s, i)
            if has_tape:
                age = TF.last_available_age_s(pair, ts[i])
                if age is not None and age <= 600:
                    tape_pairs[b_].add(pair)
            if u(P):
                elig[b_] += 1
                epairs[b_].add(pair)
                if sig(P):
                    fires[b_] += 1
    res['activity_by_3h_block'] = [{'block': '%d-%dh' % (3 * k, 3 * k + 3), 'tape_live_pairs': len(tape_pairs[k]),
                                    'eligible_points_sampled': elig[k], 'eligible_pairs': len(epairs[k]),
                                    'signal_fires_sampled': fires[k]} for k in range(nb)]
    for r in res['activity_by_3h_block']:
        print('  ', r)

    # 5) time-local random entries: same pair, entry within +/-15 min of each C1 holdout entry, any eligible point
    wins = {}
    for x in ho:
        wins.setdefault(x['pair'], []).append((x['entry_t'] - 900_000, x['entry_t'] + 900_000))

    def near(P):
        w = wins.get(P.static('pair'))
        return w is not None and any(a_ <= P.t <= b_ for a_, b_ in w)
    tl = []
    for k in range(10):
        Tk = H.simulate(lambda P, k=k: near(P) and u(P) and H.hashed_coin(P.static('pair'), P.t, 0.05, 'vtl%d' % k),
                        pairs=set(wins), t_from=cut, **C.make_kwargs())
        tl.append(C.stats(Tk))
    m = [s['mean_usd'] for s in tl if s['mean_usd'] is not None]
    res['time_local_random_holdout'] = {'runs': tl, 'mean_of_means': round(sum(m) / len(m), 3) if m else None,
                                        'runs_pos': sum(1 for v in m if v > 0), 'runs_ge_C1': sum(1 for v in m if v >= 6.106)}
    print('time-local random', res['time_local_random_holdout']['mean_of_means'], res['time_local_random_holdout']['runs_pos'],
          res['time_local_random_holdout']['runs_ge_C1'], [s['n'] for s in tl])

    # 6) time-local random in TRAIN around C1 train entries (does local timing help in train too?)
    wins_tr = {}
    for x in tr:
        wins_tr.setdefault(x['pair'], []).append((x['entry_t'] - 900_000, x['entry_t'] + 900_000))

    def near_tr(P):
        w = wins_tr.get(P.static('pair'))
        return w is not None and any(a_ <= P.t <= b_ for a_, b_ in w)
    tl2 = []
    for k in range(10):
        Tk = H.simulate(lambda P, k=k: near_tr(P) and u(P) and H.hashed_coin(P.static('pair'), P.t, 0.05, 'vtt%d' % k),
                        pairs=set(wins_tr), t_to=cut, **C.make_kwargs())
        tl2.append(C.stats(Tk))
    m2 = [s['mean_usd'] for s in tl2 if s['mean_usd'] is not None]
    res['time_local_random_train'] = {'runs': tl2, 'mean_of_means': round(sum(m2) / len(m2), 3) if m2 else None,
                                      'runs_pos': sum(1 for v in m2 if v > 0), 'C1_train_mean_usd': -6.454}
    print('time-local random train', res['time_local_random_train']['mean_of_means'], [s['n'] for s in tl2])
    # pair-level bootstrap CI (harness summary) for holdout
    sh = H.summarize(ho, span_h=(t1 - cut) / 3.6e6)
    res['holdout_summary_harness'] = sh
    res['train_summary_harness'] = H.summarize(tr, span_h=(cut - t0) / 3.6e6)
    print('holdout summary', json.dumps(sh))
    print('train summary', json.dumps(res['train_summary_harness']))
    with open(os.path.join(C.OUT, 'results_diag.json'), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1, default=str)


if __name__ == '__main__':
    t = time.time()
    main()
    print('done %.1fs' % (time.time() - t))
