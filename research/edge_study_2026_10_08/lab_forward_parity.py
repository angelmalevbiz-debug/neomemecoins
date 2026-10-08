"""Parity check: the Lab's LAB_FORWARD_TESTS_V1 signals against the frozen research functions.

Run after rebuilding the dataset (see README.md):
    python research/edge_study_2026_10_08/lab_forward_parity.py [hours] [start_fraction]

It replays the scan-log points of every PumpSwap/SOL pair, in time order, through the Lab's own
implementation (heat_veto.PairHistory + lab_forward_tests.ForwardFeedMemory + lab_forward_tests.evaluate,
each point stamped with its research time). At each point it compares the Lab's signal with the frozen
research one (synthesis/strategies.py under leaderboard/harness_final.py) for LAB_A, LAB_B and both random
controls. It also compares the per-minute market regime med15.

Both sides are compared WITHOUT the rug screens: in the Lab, STRUCTURAL_RUG_GUARD_V1 runs in the defensive
entry layer, not in lab_forward_tests. LAB_A's research age >= 60 min rule is applied to the Lab side here,
because the structural guard's 720-min young-pool rule subsumes it in the Lab.

Expected differences: none, except a reference or previous observation older than the 61-min pair-history
retention, which the research reads and the Lab has forgotten (it reports no reference or no previous
observation). Read-only: it never touches the live runtime or a ledger. PAPER research only.
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.dont_write_bytecode = True


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _none(value):
    return None if value != value else value


def main(hours=21.0, start_fraction=0.06):
    sys.path.insert(0, HERE)
    H = _load('harness_final', os.path.join(HERE, 'leaderboard', 'harness_final.py'))
    sys.modules['harness'] = H
    S = _load('lab_hypotheses', os.path.join(HERE, 'synthesis', 'strategies.py'))
    sys.path.insert(0, os.path.join(REPO, 'backend'))
    import heat_veto
    import lab_forward_tests as lf

    series, meta = H.load()
    t_start = meta['t0'] + start_fraction * (meta['t1'] - meta['t0'])
    t_end = t_start + hours * 3_600_000
    warm_from = t_start - 70 * 60_000
    events = sorted((s['t'][i], pair, i) for pair, s in series.items()
                    if s['dex'] == 'pumpswap' and s['quote_sol'] == 1
                    for i in range(len(s['t'])) if warm_from <= s['t'][i] < t_end)
    history, memory = heat_veto.PairHistory(), lf.ForwardFeedMemory()
    books = (('LAB_A', lf.LAB_A_ID), ('LAB_B', lf.LAB_B_ID), ('RND_A', lf.RND_A_ID), ('RND_B', lf.RND_B_ID))
    counts = {key: {'both': 0, 'research_only': 0, 'lab_only': 0} for key, _ in books}
    regime = {'equal': 0, 'differ': 0, 'one_undefined': 0}
    examples, minutes = [], set()
    started = time.time()
    for t, pair, i in events:
        s = series[pair]
        stamp = int(t)
        coin = {'address': s['mint'], 'pairAddress': pair, 'dexId': 'pumpswap', 'quoteTokenAddress': lf.SOL_QUOTE_MINT,
                'priceUsd': _none(s['price'][i]), 'priceNative': _none(s['pnative'][i]),
                'liquidityUsd': _none(s['liq'][i]), 'marketCap': _none(s['mcap'][i]),
                'txns': {'m5': {'buys': _none(s['b5'][i])}, 'h1': {'buys': _none(s['b1h'][i])}},
                'priceChange': {'h24': _none(s['pc24'][i])}, 'updatedAt': stamp}
        history.observe([coin], stamp)
        memory.observe([coin], stamp)
        if t < t_start:
            continue
        P = H.Past(s, i)
        aged = P('age') >= 60
        universe_a = aged and P.fee_bps() <= 95 and P('liq') >= 50_000
        universe_b = P('liq') >= 50_000
        med15 = S.market_med15(P.t)
        research = {
            'LAB_A': universe_a and P.history_len() >= 2 and S._surge(P, 0) and not S._surge(P, 1),
            'LAB_B': universe_b and S._dip(P) and med15 == med15 and med15 < -0.2,
            'RND_A': universe_a and H.hashed_coin(pair, P.t, 0.0005, 'synA'),
            'RND_B': universe_b and H.hashed_coin(pair, P.t, 0.0007, 'synB'),
        }
        for key, book_id in books:
            result = lf.evaluate(book_id, coin, stamp, memory, history)
            lab = result['matched'] and (aged if key in ('LAB_A', 'RND_A') else True)
            label = 'both' if research[key] and lab else 'research_only' if research[key] else 'lab_only' if lab else None
            if label:
                counts[key][label] += 1
            if label in ('research_only', 'lab_only') and len(examples) < 20:
                examples.append({'book': key, 'kind': label, 'symbol': s['sym'], 'pair': pair[:8], 't': stamp,
                                 'lab_reasons': result['universe_rejections'] + result['signal_rejections']})
        minute = int(t // 60_000 * 60_000)
        if minute not in minutes:
            minutes.add(minute)
            mine = memory.regime(minute, history)['med15_pct']
            theirs = _none(S.market_med15(minute))
            if mine is None and theirs is None or (mine is not None and theirs is not None
                                                   and abs(mine - theirs) < 1e-9):
                regime['equal'] += 1
            elif mine is None or theirs is None:
                regime['one_undefined'] += 1
            else:
                regime['differ'] += 1
    print('window_hours', hours, 'points', len(events), 'seconds', round(time.time() - started, 1))
    for key, row in counts.items():
        print(key, row)
    print('regime_minutes', regime)
    for row in examples:
        print(row)


if __name__ == '__main__':
    args = [float(x) for x in sys.argv[1:3]]
    main(*args)
