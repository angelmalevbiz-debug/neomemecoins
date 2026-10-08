"""Read-only reconstruction of the live entry funnels from obs.sqlite3.

For every observation row the engine's own pure rule functions (imported read-only from
backend/, bytecode writing disabled) are applied to a coin dict rebuilt from the row:
  WINNER_ENSEMBLE (main 8878): data -> pumpswap/SOL -> winner_ensemble.market_candidates
      -> cost proxy (modeled RT at the smallest size-ladder step <= 1.5 %)
      -> verified flow present -> fresh (<= 12 s) -> promoted buy pressure -> rule flow match
  ORDER_FLOW_ADAPTIVE (18801): oct4.market_rejections -> RT($200) <= 2.75 %
      -> verified flow -> fresh -> promoted pressure -> oct4 flow thresholds (30 s proxy) -> conviction >= 72
  COST_FIRST (18802): cost_first universe -> verified flow -> fresh -> promoted pressure
A (minute, pair) unit records the deepest stage any row of that pair reached in that minute.
Outputs JSON + text summary. No writes outside this folder.
"""
import sys, os, json, sqlite3, collections, math, time, datetime
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + '/../../backend' + '')
import winner_ensemble as WE
import order_flow_adaptive_oct4 as OFA
import cost_first_engine_profile as CFP
import promoted_entry_guard as PG
import paper_market_feasibility as PMF

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), 'obs.sqlite3')
SOL = 'So11111111111111111111111111111111111111112'
COLS = ['t', 'upd', 'pair', 'mint', 'dex', 'quote_sol', 'sym', 'price', 'pnative', 'liq', 'mcap', 'fdv',
        'v1h', 'pc5', 'pc1h', 'b5', 's5', 'age', 'score', 'vf_trades', 'vf_buy', 'vf_sell', 'vf_wallets',
        'vf_age_ms', 'ff_q', 'conv', 'rej', 'safe']


def coin_of(r):
    return {'address': r['mint'], 'pairAddress': r['pair'], 'dexId': r['dex'] or '',
            'quoteTokenAddress': SOL if r['quote_sol'] == 1 else 'other',
            'priceUsd': r['price'], 'priceNative': r['pnative'], 'liquidityUsd': r['liq'],
            'marketCap': r['mcap'], 'fdv': r['fdv'],
            'priceChange': {'m5': r['pc5'], 'h1': r['pc1h']},
            'txns': {'m5': {'buys': r['b5'], 'sells': r['s5']}},
            'volume': {'h1': r['v1h']}, 'ageMinutes': r['age'], 'score': r['score'],
            'updatedAt': r['upd']}


def ladder_min(requested):
    sizes = [math.floor(requested * 100) / 100]
    while len(sizes) < 3 and sizes[-1] > 25:
        sizes.append(max(25.0, math.floor(sizes[-1] * 0.5 * 100) / 100))
    return sizes[-1]


def main_requested(coin):
    liq = PMF.number(coin.get('liquidityUsd'))
    age = PMF.number(coin.get('ageMinutes'), 999999)
    base = 35 if liq < 8000 else 50 if liq < 15000 else 75 if liq < 30000 else 100 if liq < 60000 else 150 if liq < 120000 else 200
    if age <= 15:
        base *= .7
    elif age <= 45:
        base *= .85
    return round(max(25.0, min(200.0, base)), 2)


def rt_pct(coin, notional):
    est = PMF.modeled_roundtrip(coin, notional, base_slippage_bps=10, latency_buffer_bps=10)
    if est.get('status') != 'estimate':
        return None
    return -est['initial_pnl_pct']


def flow_of(r):
    if r['vf_trades'] is None:
        return None
    return {'trades': r['vf_trades'], 'buy_usd': r['vf_buy'] or 0.0, 'sell_usd': r['vf_sell'] or 0.0,
            'unique_wallets': r['vf_wallets'] or 0}


def fresh(r):
    a = r['vf_age_ms']
    return a is not None and 0 <= a <= PG.FLOW_MAX_AGE_MS


def pressure(f):
    t, w, b, s = f['trades'], f['unique_wallets'], f['buy_usd'], f['sell_usd']
    return t >= PG.MIN_FLOW_TRADES and w >= PG.MIN_FLOW_WALLETS and b > 0 and b >= PG.MIN_BUY_SELL_RATIO * max(s, 1.0)


def rug_family(r, ticker_seen):
    mc, liq, age = r['mcap'] or 0, r['liq'] or 0, r['age']
    young = not (age is not None and age >= 14 * 1440)
    if mc >= 20e6 and liq > 0 and liq / mc < 0.02 and young:
        return True
    return young and ticker_seen


def norm(sym):
    return ''.join(ch for ch in (sym or '') if ch.isalnum()).casefold()


WE_ST = ['feed', 'pumpswap_sol', 'market_rule', 'cost_ok', 'vf_present', 'vf_fresh', 'buy_pressure', 'rule_flow']
OFA_ST = ['feed', 'pumpswap_sol', 'market_rule', 'cost_ok', 'vf_present', 'vf_fresh', 'buy_pressure', 'oct4_flow', 'conviction']
CF_ST = ['feed', 'pumpswap_sol', 'universe', 'vf_present', 'vf_fresh', 'buy_pressure']


def stage_we(r, coin):
    if not (r['dex'] == 'pumpswap' and r['quote_sol'] == 1 and (r['price'] or 0) > 0):
        return 0, None
    rules = WE.market_candidates(coin)
    if not rules:
        return 1, None
    rt = rt_pct(coin, ladder_min(main_requested(coin)))
    if rt is None or rt > 1.5:
        return 2, rules
    f = flow_of(r)
    if f is None:
        return 3, rules
    if not fresh(r):
        return 4, rules
    if not pressure(f):
        return 5, rules
    norm_flow = {'trades': f['trades'], 'buy_usd': f['buy_usd'], 'sell_usd': f['sell_usd'],
                 'unique_wallets': f['unique_wallets'], 'max_sell_usd': 0}
    if not WE.matches(coin, norm_flow):
        return 6, rules
    return 7, rules


def stage_ofa(r, coin):
    if not (r['dex'] == 'pumpswap' and r['quote_sol'] == 1 and (r['price'] or 0) > 0):
        return 0
    rej = OFA.market_rejections(coin, now=r['t'])
    rej = [x for x in rej if x != 'stale_feed']
    if rej:
        return 1
    rt = rt_pct(coin, 200.0)
    if rt is None or rt > 2.75:
        return 2
    f = flow_of(r)
    if f is None:
        return 3
    if not fresh(r):
        return 4
    if not pressure(f):
        return 5
    ratio = f['buy_usd'] / max(f['sell_usd'], 1.0)
    if not (f['trades'] >= 4 and ratio >= 1.3 and f['buy_usd'] >= 150 and f['unique_wallets'] >= 4):
        return 6
    if not ((r['conv'] or 0) >= 72):
        return 7
    return 8


def stage_cf(r, coin):
    if not (r['dex'] == 'pumpswap' and r['quote_sol'] == 1 and (r['price'] or 0) > 0):
        return 0
    if CFP.universe_rejections(coin, 200.0):
        return 1
    f = flow_of(r)
    if f is None:
        return 2
    if not fresh(r):
        return 3
    if not pressure(f):
        return 4
    return 5


def main():
    t0 = time.time()
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    q = 'SELECT %s FROM o ORDER BY t' % ','.join(COLS)
    units = {}  # (minute, pair) -> dict
    first_seen_ticker = {}  # norm ticker -> set of pairs seen so far
    pair_ticker_other = {}
    # cross-check of the reconstruction against the engine's own rejection names
    xc = collections.Counter()
    nrows = 0
    for row in con.execute(q):
        nrows += 1
        r = dict(zip(COLS, row))
        pair = r['pair']
        if not pair:
            continue
        tk = norm(r['sym'])
        seen = first_seen_ticker.setdefault(tk, set())
        if pair not in seen:
            seen.add(pair)
        other = len(seen) > 1  # another pair with this ticker already seen (past-only)
        coin = coin_of(r)
        sw, rules = stage_we(r, coin)
        so = stage_ofa(r, coin)
        sc = stage_cf(r, coin)
        rej = r['rej'] or ''
        if rej:
            first = rej.split('|')[0]
            xc[(first, 'we_market' if sw >= 2 else 'we_no_market', 'vf' if r['vf_trades'] is not None else 'novf')] += 1
        key = (int(r['t'] // 60000), pair)
        u = units.get(key)
        if u is None:
            u = units[key] = {'we': 0, 'ofa': 0, 'cf': 0, 'sym': r['sym'], 'rug': False, 'rules': set(),
                              'fee': None, 'liq': r['liq'], 'mcap': r['mcap'], 'vf': False}
        u['we'] = max(u['we'], sw)
        u['ofa'] = max(u['ofa'], so)
        u['cf'] = max(u['cf'], sc)
        u['vf'] = u['vf'] or r['vf_trades'] is not None
        if rules:
            u['rules'].update(rules)
        if rug_family(r, other):
            u['rug'] = True
    con.close()
    print('rows', nrows, 'units', len(units), 'secs', round(time.time() - t0, 1))

    minutes = sorted({m for m, _ in units})
    out = {'rows': nrows, 'minutes': len(minutes), 'units': len(units)}

    def per_minute(field, nst, rug_excluded=False):
        counts = {m: [0] * (nst + 1) for m in minutes}
        for (m, p), u in units.items():
            if rug_excluded and u['rug']:
                continue
            for s in range(u[field] + 1):
                counts[m][s] += 1
        res = []
        for s in range(nst + 1):
            xs = sorted(counts[m][s] for m in minutes)
            n = len(xs)
            res.append({'stage': s, 'mean_pairs_per_minute': round(sum(xs) / n, 3), 'median': xs[n // 2],
                        'p90': xs[int(n * .9)], 'pct_minutes_with_any': round(100 * sum(1 for x in xs if x > 0) / n, 1)})
        return res

    out['we_funnel'] = per_minute('we', 7)
    out['ofa_funnel'] = per_minute('ofa', 8)
    out['cf_funnel'] = per_minute('cf', 5)
    out['cf_funnel_rug_screened'] = per_minute('cf', 5, rug_excluded=True)

    def blocked_breakdown(field, nonflow_stage, labels):
        # among units that passed every non-flow rule, where did they stop?
        c = collections.Counter()
        pairs = collections.Counter()
        pairs_vf = collections.Counter()
        for (m, p), u in units.items():
            if u[field] >= nonflow_stage:
                c[labels[u[field]]] += 1
                pairs[u['sym'] + '|' + p[:8]] += 1
                if u[field] > nonflow_stage:
                    pairs_vf[u['sym'] + '|' + p[:8]] += 1
        tot = sum(c.values())
        return {'units_passing_all_nonflow_rules': tot,
                'stop_stage_share_pct': {k: round(100 * v / tot, 1) for k, v in c.most_common()} if tot else {},
                'distinct_pairs': len(pairs),
                'top_pairs_by_units': pairs.most_common(15),
                'distinct_pairs_with_verified_flow': len(pairs_vf),
                'top_pairs_with_verified_flow': pairs_vf.most_common(15)}

    we_lab = {3: 'BLOCKED_no_verified_flow', 4: 'BLOCKED_flow_stale', 5: 'BLOCKED_buy_pressure', 6: 'BLOCKED_rule_flow', 7: 'PASSED_to_safety_and_quote'}
    ofa_lab = {3: 'BLOCKED_no_verified_flow', 4: 'BLOCKED_flow_stale', 5: 'BLOCKED_buy_pressure', 6: 'BLOCKED_oct4_flow_thresholds', 7: 'BLOCKED_conviction', 8: 'PASSED_to_safety_and_quote'}
    cf_lab = {2: 'BLOCKED_no_verified_flow', 3: 'BLOCKED_flow_stale', 4: 'BLOCKED_buy_pressure', 5: 'PASSED_to_safety_and_quote'}
    out['we_blocked'] = blocked_breakdown('we', 3, we_lab)
    out['ofa_blocked'] = blocked_breakdown('ofa', 3, ofa_lab)
    out['cf_blocked'] = blocked_breakdown('cf', 2, cf_lab)
    # cost-first with the rug-family screen
    sub = {k: v for k, v in units.items() if not v['rug']}
    saved = units
    globals_units = sub
    c = collections.Counter(cf_lab[u['cf']] for u in sub.values() if u['cf'] >= 2)
    tot = sum(c.values())
    pairs = collections.Counter(u['sym'] + '|' + p[:8] for (m, p), u in sub.items() if u['cf'] >= 2)
    out['cf_blocked_rug_screened'] = {'units': tot, 'stop_stage_share_pct': {k: round(100 * v / tot, 1) for k, v in c.most_common()} if tot else {},
                                      'distinct_pairs': len(pairs), 'top_pairs': pairs.most_common(15)}
    rugpairs = collections.Counter(u['sym'] + '|' + p[:8] for (m, p), u in units.items() if u['cf'] >= 2 and u['rug'])
    out['cf_universe_units_flagged_rug_family'] = {'units': sum(rugpairs.values()), 'pairs': len(rugpairs), 'top': rugpairs.most_common(20)}

    # rules that matched among units with cost ok (WE)
    rc = collections.Counter()
    for u in units.values():
        if u['we'] >= 3:
            for x in u['rules']:
                rc[x] += 1
    out['we_rules_among_cost_ok_units'] = rc.most_common()
    # verified-flow coverage overall
    vf_units = sum(1 for u in units.values() if u['vf'])
    out['vf_units'] = vf_units
    out['vf_units_share_pct'] = round(100 * vf_units / len(units), 2)
    vfp = collections.Counter(u['sym'] + '|' + p[:8] for (m, p), u in units.items() if u['vf'])
    out['vf_minutes_by_pair_top'] = vfp.most_common(20)
    out['vf_distinct_pairs'] = len(vfp)
    vfm = collections.Counter(m for (m, p), u in units.items() if u['vf'])
    xs = sorted(vfm.get(m, 0) for m in minutes)
    out['vf_pairs_per_minute'] = {'mean': round(sum(xs) / len(xs), 2), 'median': xs[len(xs) // 2], 'max': xs[-1]}
    # hourly WE stage-3 (all non-flow) and stage-7 counts
    hourly = collections.defaultdict(lambda: [set(), set(), set(), set()])
    for (m, p), u in units.items():
        h = datetime.datetime.fromtimestamp(m * 60).strftime('%m-%d %H')
        if u['we'] >= 3:
            hourly[h][0].add(p)
        if u['we'] >= 4:
            hourly[h][1].add(p)
        if u['we'] >= 7:
            hourly[h][2].add(p)
        if u['cf'] >= 2:
            hourly[h][3].add(p)
    out['hourly_distinct_pairs'] = {h: {'we_nonflow_ok': len(v[0]), 'we_with_vf': len(v[1]), 'we_full_pass': len(v[2]), 'cf_universe': len(v[3])}
                                    for h, v in sorted(hourly.items())}
    out['crosscheck_first_rej_vs_reconstruction'] = sorted(([list(k), v] for k, v in xc.items()), key=lambda x: -x[1])[:40]
    with open(os.path.join(HERE, 'funnel.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False, default=list)
    for k in ('we_funnel', 'ofa_funnel', 'cf_funnel', 'cf_funnel_rug_screened'):
        print(k)
        for row in out[k]:
            print('   ', row)
    for k in ('we_blocked', 'ofa_blocked', 'cf_blocked', 'cf_blocked_rug_screened', 'cf_universe_units_flagged_rug_family'):
        print(k, json.dumps(out[k], ensure_ascii=False))
    print('rules', out['we_rules_among_cost_ok_units'])
    print('vf', out['vf_units'], out['vf_units_share_pct'], out['vf_distinct_pairs'], out['vf_pairs_per_minute'])
    print('vf top', out['vf_minutes_by_pair_top'])
    for h, v in out['hourly_distinct_pairs'].items():
        print('  ', h, v)
    print('crosscheck')
    for k, v in out['crosscheck_first_rej_vs_reconstruction']:
        print('   ', k, v)
    print('secs', round(time.time() - t0, 1))


if __name__ == '__main__':
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass
    main()
