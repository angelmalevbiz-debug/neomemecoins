"""Q3: does tape-verified 30 s flow predict forward returns better than DexScreener txns proxies?

Sampling: PumpSwap/SOL pairs, one point per pair per 60 s (first point after the previous sample),
forward_net(s, i, h) for h in 5/15/30 min at $200 (model costs; +50 bps/leg stress also kept).
Every sample carries past-only features only. The proxy definitions are fixed BEFORE looking at
outcomes and mirror the promoted thresholds on DexScreener's 5-minute txn counts:
  PROXY_PRESSURE   : b5 >= 3 and b5 / max(s5, 1) >= 1.2
  VF_PRESSURE      : verified 30 s window present, fresh (<= 12 s), trades >= 3, wallets >= 2,
                     buy_usd >= 1.2 * max(sell_usd, 1)   (= promoted_entry_guard.flow_admission)
Selection effects: verified flow only exists on the 4 tape seats, which the scheduler gives to
pins and to pools matched by the engine's rules, so covered and uncovered samples differ. The
primary comparison is therefore INSIDE the covered sample (same pools, same minutes):
  2 x 2 table VF_PRESSURE x PROXY_PRESSURE, plus a within-pair demeaned contrast.
Writes flowq.json in this folder.
"""
import sys, os, json, math, random, collections
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

HERE = os.path.dirname(os.path.abspath(__file__))
HZ = (300, 900, 1800)


def isnum(x):
    return x == x and x is not None


def stats(rows, key):
    xs = [r[key] for r in rows if r.get(key) is not None]
    if not xs:
        return {'n': 0}
    xs.sort()
    n = len(xs)
    # cluster bootstrap over (pair, hour)
    cl = collections.defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            cl[(r['pair'], int(r['t'] // 3.6e6))].append(r[key])
    groups = list(cl.values())
    lo = hi = None
    if len(groups) >= 5:
        rnd = random.Random(11)
        means = []
        for _ in range(1000):
            tot = cnt = 0
            for _ in range(len(groups)):
                g = groups[rnd.randrange(len(groups))]
                tot += sum(g); cnt += len(g)
            means.append(tot / cnt)
        means.sort()
        lo, hi = round(means[25], 3), round(means[975], 3)
    return {'n': n, 'pairs': len({r['pair'] for r in rows if r.get(key) is not None}), 'clusters': len(groups),
            'mean': round(sum(xs) / n, 3), 'median': round(xs[n // 2], 3), 'win_pct': round(100 * sum(1 for x in xs if x > 0) / n, 1),
            'p10': round(xs[int(n * .1)], 3), 'p90': round(xs[int(n * .9)], 3), 'ci95_mean': [lo, hi]}


def main():
    series, meta = H.load()
    rows = []
    for pair, s in series.items():
        if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
            continue
        ts = s['t']
        last = -1e18
        for i in range(len(ts) - 1):
            if ts[i] - last < 60_000:
                continue
            last = ts[i]
            P = H.Past(s, i)
            liq = P('liq')
            if not (liq > 0):
                continue
            fee = P.fee_bps()
            b5, s5 = P('b5'), P('s5')
            vft = P('vf_trades')
            vf = isnum(vft)
            vf_fresh = vf and isnum(P('vf_age_ms')) and 0 <= P('vf_age_ms') <= 12_000
            vf_press = bool(vf_fresh and vft >= 3 and (P('vf_wallets') or 0) >= 2 and (P('vf_buy') or 0) > 0
                            and P('vf_buy') >= 1.2 * max(P('vf_sell') or 0, 1.0))
            proxy = bool(isnum(b5) and isnum(s5) and b5 >= 3 and b5 / max(s5, 1) >= 1.2)
            r = {'pair': pair, 't': ts[i], 'fee': fee, 'liq': liq, 'vf': vf, 'vf_fresh': vf_fresh, 'vf_press': vf_press,
                 'proxy': proxy, 'rug': H.interim_rug_risk(P),
                 'fb': 'fee<=50' if fee <= 50 else ('fee55-95' if fee <= 95 else 'fee100-125'),
                 'lb': 'liq>=250k' if liq >= 250_000 else ('liq50-250k' if liq >= 50_000 else 'liq<50k')}
            ok = False
            for h in HZ:
                v = H.forward_net(s, i, h)
                r['f%d' % h] = v
                ok = ok or v is not None
            if ok:
                rows.append(r)
    print('samples', len(rows), 'with vf', sum(r['vf'] for r in rows))
    out = {'samples': len(rows), 'samples_with_vf': sum(r['vf'] for r in rows)}
    clean = [r for r in rows if not r['rug']]
    out['clean_samples'] = len(clean)
    cov = [r for r in clean if r['vf']]
    unc = [r for r in clean if not r['vf']]
    # 1. selection: covered vs uncovered base rates per fee/liq bucket
    sel = {}
    for fb in ('fee<=50', 'fee55-95', 'fee100-125'):
        for lb in ('liq>=250k', 'liq50-250k', 'liq<50k'):
            a = [r for r in cov if r['fb'] == fb and r['lb'] == lb]
            b = [r for r in unc if r['fb'] == fb and r['lb'] == lb]
            if len(a) >= 30:
                sel['%s|%s' % (fb, lb)] = {'covered_f900': stats(a, 'f900'), 'uncovered_f900': stats(b, 'f900')}
    out['selection_by_bucket'] = sel
    # 2. inside the covered sample: 2x2
    tab = {}
    for vp in (True, False):
        for px in (True, False):
            sub = [r for r in cov if r['vf_press'] == vp and r['proxy'] == px]
            tab['vf_press=%s|proxy=%s' % (vp, px)] = {('f%d' % h): stats(sub, 'f%d' % h) for h in HZ}
    out['covered_2x2'] = tab
    out['covered_vf_press'] = {str(vp): {('f%d' % h): stats([r for r in cov if r['vf_press'] == vp], 'f%d' % h) for h in HZ} for vp in (True, False)}
    out['covered_proxy'] = {str(px): {('f%d' % h): stats([r for r in cov if r['proxy'] == px], 'f%d' % h) for h in HZ} for px in (True, False)}
    out['uncovered_proxy'] = {str(px): {('f%d' % h): stats([r for r in unc if r['proxy'] == px], 'f%d' % h) for h in HZ} for px in (True, False)}
    # 3. within-pair demeaned contrast (removes pair-level selection): for each pair, mean f900 of
    # signal-on minus signal-off samples, averaged over pairs having both, weighted equally.
    def within(rows_, flag, key='f900'):
        by = collections.defaultdict(lambda: [[], []])
        for r in rows_:
            if r.get(key) is None:
                continue
            by[r['pair']][0 if r[flag] else 1].append(r[key])
        diffs = [(sum(a) / len(a) - sum(b) / len(b), len(a), len(b)) for a, b in by.values() if len(a) >= 3 and len(b) >= 3]
        if not diffs:
            return {'pairs': 0}
        ds = sorted(d for d, _, _ in diffs)
        rnd = random.Random(5)
        bs = sorted(sum(rnd.choice(ds) for _ in ds) / len(ds) for _ in range(2000))
        return {'pairs': len(ds), 'mean_diff_pp': round(sum(ds) / len(ds), 3), 'median_diff_pp': round(ds[len(ds) // 2], 3),
                'pairs_positive_pct': round(100 * sum(1 for d in ds if d > 0) / len(ds), 1),
                'ci95_pair_bootstrap': [round(bs[50], 3), round(bs[1950], 3)]}
    out['within_pair_covered'] = {'vf_press_f900': within(cov, 'vf_press'), 'proxy_f900': within(cov, 'proxy'),
                                  'vf_press_f300': within(cov, 'vf_press', 'f300'), 'proxy_f300': within(cov, 'proxy', 'f300'),
                                  'vf_press_f1800': within(cov, 'vf_press', 'f1800'), 'proxy_f1800': within(cov, 'proxy', 'f1800')}
    out['within_pair_uncovered'] = {'proxy_f900': within(unc, 'proxy'), 'proxy_f300': within(unc, 'proxy', 'f300'),
                                    'proxy_f1800': within(unc, 'proxy', 'f1800')}
    # 4. agreement between the two signals inside the covered sample
    agree = collections.Counter((r['vf_press'], r['proxy']) for r in cov)
    out['covered_agreement'] = {'%s|%s' % k: v for k, v in agree.items()}
    # 5. fee<=75 (main-cost-feasible) covered subset
    low = [r for r in cov if r['fee'] <= 75]
    out['covered_low_fee_vf_press'] = {str(vp): stats([r for r in low if r['vf_press'] == vp], 'f900') for vp in (True, False)}
    out['covered_low_fee_proxy'] = {str(px): stats([r for r in low if r['proxy'] == px], 'f900') for px in (True, False)}
    with open(os.path.join(HERE, 'flowq.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1)[:20000])


if __name__ == '__main__':
    main()
