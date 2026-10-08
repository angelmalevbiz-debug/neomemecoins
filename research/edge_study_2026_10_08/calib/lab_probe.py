"""Strategy-lab $10 Jupiter price probes (raw outAmount, no buffer) vs the lab's observed mark.

price_crosscheck.jupiter_entry_price = 10 USDC / tokens_out of a read-only Jupiter quote taken <=10 s before the
lab entry; observed_price = the signal mark (DexScreener priceUsd of the pool). Signed buy premium =
jupiter/observed - 1 = pool fee + USDC-leg fee + impact(~$10, negligible) + mark offset.
"""
import collections, json, math, os, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledgers as L
import costmodel as C
from stats import desc, fmt, ok

DEEP = os.path.dirname(HERE)
fnum = C.fnum


def main():
    lab = L.load_lab_trades()
    con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'obs.sqlite3').replace('\\', '/'), uri=True)
    rows = []
    for key, (t, src) in lab.items():
        pc = t.get('price_crosscheck') or {}
        jp, op = fnum(pc.get('jupiter_entry_price')), fnum(pc.get('observed_price'))
        if not (jp > 0 and op > 0):
            continue
        ef = t.get('entry_features') or {}
        liq = fnum(ef.get('liq'))
        lmc = fnum(ef.get('lmc'))
        su = fnum(t.get('entry_network_fee_usd')) / 0.0001
        mc = liq / lmc if lmc > 0 else float('nan')
        fee_tab = C.fee_bps(mc, su)
        r = {'book': key[0], 'pair8': L.ab(t.get('pairAddress')), 'sym': t.get('symbol'), 'opened_at': t.get('opened_at'),
             'liq': liq, 'mcap': mc, 'sol_usd': su, 'fee_tab': fee_tab, 'fee_lab': fnum(t.get('entry_dex_fee_bps')),
             'premium_pct': 100 * (jp / op - 1), 'mode': t.get('execution_mode'),
             'lab_exec_vs_mark_pct': 100 * (fnum(t.get('execution_entry_price')) / fnum(t.get('entry_price')) - 1)}
        m = con.execute('SELECT t, price, liq, mcap, pnative, dex FROM o WHERE pair=? AND t<=? ORDER BY t DESC LIMIT 1',
                        (t.get('pairAddress'), int(fnum(t.get('opened_at'), 0)))).fetchone()
        if m and (fnum(t.get('opened_at')) - m[0]) < 30_000 and m[1] and m[4]:
            r['obs_age_s'] = (fnum(t.get('opened_at')) - m[0]) / 1000
            r['obs_vs_observed_pct'] = 100 * (m[1] / op - 1)
            r['dex'] = m[5]
            r['fee_obs'] = C.fee_bps(m[3], m[1] / m[4], (m[5] or '').lower())
        rows.append(r)
    con.close()
    print('lab probes', len(rows), 'pairs', len(set(r['pair8'] for r in rows)))
    print('fee_tab vs fee_lab agree', sum(1 for r in rows if r['fee_tab'] == r['fee_lab']), 'of', len(rows))
    print('obs-matched', sum(1 for r in rows if 'obs_age_s' in r), 'dex', collections.Counter(r.get('dex') for r in rows))
    K = ('n', 'clusters', 'mean', 'median', 'p10', 'p90', 'ci95')
    fee = lambda r: r.get('fee_obs', r['fee_lab'] if ok(r['fee_lab']) else r['fee_tab'])
    print('premium %', fmt(desc(rows, lambda r: r['premium_pct']), K))
    print('premium - fee tier (= mark offset + USDC leg)', fmt(desc(rows, lambda r: r['premium_pct'] - fee(r) / 100), K))
    for fb, lo, hi in (('fee<=50', 0, 50), ('fee55-95', 55, 95), ('fee100-125', 100, 125)):
        rs = [r for r in rows if lo <= fee(r) <= hi]
        print('  %-11s premium %s' % (fb, fmt(desc(rs, lambda r: r['premium_pct']), ('n', 'clusters', 'mean', 'median'))))
        print('  %-11s premium-fee %s' % ('', fmt(desc(rs, lambda r: r['premium_pct'] - fee(r) / 100), K)))
    for lb, lo, hi in (('liq<50k', 0, 50e3), ('liq50-250k', 50e3, 250e3), ('liq>=250k', 250e3, 1e12)):
        rs = [r for r in rows if lo <= r['liq'] < hi]
        print('  %-11s premium-fee %s' % (lb, fmt(desc(rs, lambda r: r['premium_pct'] - fee(r) / 100), K)))
    print('obs mark vs lab observed price %', fmt(desc(rows, lambda r: r.get('obs_vs_observed_pct', float('nan'))), K))
    print('lab modeled exec vs mark %', fmt(desc(rows, lambda r: r['lab_exec_vs_mark_pct']), K))
    print('lab modeled exec - real premium (lab optimism if <0)', fmt(desc(rows, lambda r: r['lab_exec_vs_mark_pct'] - r['premium_pct']), K))
    json.dump(rows, open(os.path.join(HERE, 'lab_probe_rows.json'), 'w', encoding='utf-8'), ensure_ascii=False)


if __name__ == '__main__':
    main()
