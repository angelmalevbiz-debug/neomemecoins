"""Real Jupiter quotes of every closed PAPER account trade (ledger COPIES) vs the harness cost model.

Per trade (all % of notional, positive = cost):
  entry leg      real = 1 - tokens_out * mark / notional    (raw Jupiter outAmount, and engine-booked = raw - 10 bps buffer)
  instant RT     real = preflight buy then preflight sell of the same tokens (mark-free, ~2 s apart)
  exit leg       real = 1 - usdc_out / (tokens_sold * exit mark)
  full trade     real booked pnl% vs harness pnl% from the same entry/exit marks and liquidity
Model = harness formulas on the trade's own coin_snapshot (price, priceNative->SOL/USD, market cap, liquidity).
Writes acct_rows.json (abbreviated addresses only) and prints tables.
"""
import collections, json, math, os, random, sqlite3, statistics, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledgers as L
import costmodel as C

DEEP = os.path.dirname(HERE)
fnum = C.fnum


def q_int(q, k):
    try:
        return int(q.get(k))
    except (TypeError, ValueError, AttributeError):
        return None


def q_impact(q):
    v = fnum((q or {}).get('priceImpactPct'))
    return 100 * v if math.isfinite(v) else float('nan')


def route_labels(q):
    return [((x.get('swapInfo') or {}).get('label')) for x in (q or {}).get('routePlan') or []]


SOL = 'So11111111111111111111111111111111111111112'
USDC = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


def legs(q, mint):
    """Split a USDC<->SOL<->token route: (usdc_raw, sol_raw_on_usdc_leg, sol_raw_on_token_leg, token_raw).
    Only for plain 2-hop routes where every token leg is SOL-paired; else None."""
    rp = [(x.get('swapInfo') or {}) for x in (q or {}).get('routePlan') or []]
    if not rp or any(fnum(x.get('bps'), 10000) != 10000 for x in (q or {}).get('routePlan') or []):
        return None
    tok = [x for x in rp if mint in (x.get('inputMint'), x.get('outputMint'))]
    usd = [x for x in rp if USDC in (x.get('inputMint'), x.get('outputMint'))]
    if len(tok) != 1 or len(usd) != 1 or len(rp) != 2:
        return None
    t, u = tok[0], usd[0]
    if SOL not in (t.get('inputMint'), t.get('outputMint')) or SOL not in (u.get('inputMint'), u.get('outputMint')):
        return None
    if t.get('inputMint') == SOL:   # buy: USDC->SOL->token
        return int(u['inAmount']), int(u['outAmount']), int(t['inAmount']), int(t['outAmount']), 'buy'
    return int(u['outAmount']), int(u['inAmount']), int(t['outAmount']), int(t['inAmount']), 'sell'


def obs_mark(con, pair, t_ms):
    """Latest observation at or before t_ms: (t, price, liq, mcap, pnative)."""
    r = con.execute('SELECT t, price, liq, mcap, pnative FROM o WHERE pair=? AND t<=? ORDER BY t DESC LIMIT 1',
                    (pair, int(t_ms))).fetchone()
    return r


def main():
    trades = L.load_account_trades()
    con = sqlite3.connect('file:%s?mode=ro' % os.path.join(DEEP, 'obs.sqlite3').replace('\\', '/'), uri=True)
    t_obs0 = 1791362460712
    rows = []
    for (acct, tid), (t, src) in trades.items():
        cs = t.get('coin_snapshot') or {}
        dec = int(t.get('token_decimals') or ((t.get('risk_check') or {}).get('metrics') or {}).get('decimals') or 6)
        p0, pn0 = fnum(cs.get('priceUsd')), fnum(cs.get('priceNative'))
        su = p0 / pn0 if p0 > 0 and pn0 > 0 else fnum(((t.get('risk_check') or {}).get('metrics') or {}).get('sol_usd'))
        mc0 = fnum(cs.get('marketCap') or cs.get('fdv'))
        L0 = fnum(cs.get('liquidityUsd'))
        N = fnum(t.get('notional_usd'))
        mark_in = fnum(t.get('market_entry_price'))
        fee0 = C.fee_bps(mc0, su, (cs.get('dexId') or '').lower())
        eq, pb, ps, xq = t.get('entry_quote') or {}, t.get('preflight_buy_quote') or {}, t.get('preflight_sell_quote') or {}, t.get('exit_quote') or {}
        r = {'acct': acct, 'id': L.ab(tid), 'sym': t.get('symbol'), 'pair8': L.ab(t.get('pairAddress')),
             'session': (t.get('session_id') or '')[:32], 'opened_at': t.get('opened_at'), 'closed_at': t.get('closed_at'),
             'reason': t.get('exit_reason'), 'N': N, 'liq': L0, 'mcap': mc0, 'sol_usd': su, 'fee': fee0,
             'n_over_l_pct': 100 * N / L0 if L0 > 0 else float('nan'),
             'mark_in': mark_in, 'snap_price': p0, 'snap_age_ms': fnum(eq.get('_quotedAtMs')) - fnum(cs.get('updatedAt')),
             'entry_route': route_labels(eq), 'exit_route': route_labels(xq)}
        # ---- entry leg
        out_raw = q_int(eq, 'outAmount')
        booked_raw = q_int(t, 'jupiter_token_raw_amount')
        if out_raw and mark_in > 0 and N > 0:
            r['in_real_raw'] = 100 * (1 - out_raw / 10 ** dec * mark_in / N)
            r['in_real_booked'] = 100 * (1 - booked_raw / 10 ** dec * mark_in / N) if booked_raw else float('nan')
        r['in_model_core'] = C.entry_cost_pct(mark_in, L0, fee0, N, base_bps=0.0)
        r['in_model_full'] = C.entry_cost_pct(mark_in, L0, fee0, N)
        r['in_jup_impact'] = q_impact(eq)
        # preflight buy leg vs the same mark (taken a few seconds earlier than the fill quote)
        b_raw = q_int(pb, 'outAmount')
        if b_raw and mark_in > 0:
            r['pb_real_raw'] = 100 * (1 - b_raw / 10 ** dec * mark_in / N)
        r['pb_jup_impact'] = q_impact(pb)
        # ---- instant (mark-free) round trip from the two preflight quotes
        s_in, s_out = q_int(ps, 'inAmount'), q_int(ps, 'outAmount')
        if b_raw and s_in and s_out:
            r['rt_real_raw'] = 100 * (1 - (s_out / 1e6) * (b_raw / s_in) / N)
            # sell-side cost vs buy-side cost split at the instant: implied buy price vs sell price
            pbuy = N / (b_raw / 10 ** dec)
            psell = (s_out / 1e6) / (s_in / 10 ** dec)
            r['buy_sell_gap_pct'] = 100 * (pbuy / psell - 1)
            r['ps_real_raw'] = 100 * (1 - psell / mark_in) if mark_in > 0 else float('nan')
            r['rt_dt_ms'] = fnum(ps.get('_quotedAtMs')) - fnum(pb.get('_quotedAtMs'))
        r['ps_jup_impact'] = q_impact(ps)
        # EXIT_IMPACT_EMERGENCY V1 threshold of this position, and whether the entry-time sell quote already met it
        r['eie_threshold'] = max(0.75, fnum(t.get('entry_price_impact_pct'), 0.0) + 0.50)
        r['eie_at_entry'] = 1.0 if r['ps_jup_impact'] >= r['eie_threshold'] else 0.0
        r['hold_s'] = (fnum(t.get('closed_at')) - fnum(t.get('opened_at'))) / 1000
        # sell-quote "impact" model counterpart: fee + 2*mv/L (what an honest constant-product sell would cost)
        r['ps_model_core'] = C.exit_cost_pct(mark_in, L0, fee0, (b_raw or 0) / 10 ** dec, base_bps=0.0) if b_raw else float('nan')
        r['rt_booked'] = -fnum(t.get('entry_roundtrip_pnl_pct'))
        r['rt_worst_booked'] = -fnum(t.get('entry_worst_case_roundtrip_pnl_pct'))
        r['rt_model_core'] = C.roundtrip_pct(mark_in, L0, su, fee0, N, base_bps=0.0, network=False)
        r['rt_model_full'] = C.roundtrip_pct(mark_in, L0, su, fee0, N)
        r['rent_usd'] = fnum(t.get('entry_account_reserve_usd'), 0.0)
        r['net_fee_usd'] = fnum(t.get('entry_network_fee_usd'), 0.0)
        # ---- exit leg
        mark_x = fnum(t.get('exit_price'))
        Lx = fnum(t.get('exit_liquidity_usd'))
        mcx = mc0 * mark_x / p0 if p0 > 0 and mark_x > 0 else float('nan')
        feex = C.fee_bps(mcx, su)
        sold_raw, usdc_raw = q_int(xq, 'inAmount'), q_int(xq, 'outAmount')
        r.update(mark_x=mark_x, liq_x=Lx, fee_x=feex, x_jup_impact=q_impact(xq),
                 x_quote_at=fnum(xq.get('_quotedAtMs')), move_pct=100 * (mark_x / mark_in - 1) if mark_in > 0 else float('nan'))
        if sold_raw and usdc_raw and mark_x > 0:
            qty = sold_raw / 10 ** dec
            r['out_real_raw'] = 100 * (1 - (usdc_raw / 1e6) / (qty * mark_x))
            r['out_model_core'] = C.exit_cost_pct(mark_x, Lx, feex, qty, base_bps=0.0)
            r['out_model_full'] = C.exit_cost_pct(mark_x, Lx, feex, qty)
        # ---- SOL-denominated decomposition (token leg vs DexScreener priceNative; USDC leg vs DexScreener SOL/USD)
        mint = t.get('address')
        for tag, q in (('eq', eq), ('pb', pb), ('ps', ps), ('xq', xq)):
            lg = legs(q, mint)
            if not lg:
                continue
            usdc_raw, sol_u, sol_t, tok_raw, side = lg
            r[tag + '_jup_solusd'] = (usdc_raw / 1e6) / (sol_u / 1e9)
            r[tag + '_native_px'] = (sol_t / 1e9) / (tok_raw / 10 ** dec)   # SOL per token on the pool leg
            r[tag + '_side'] = side
        if 'eq_jup_solusd' in r and su > 0:
            r['solusd_jup_vs_dex_pct'] = 100 * (r['eq_jup_solusd'] / su - 1)
        if 'pb_native_px' in r and pn0 > 0:
            r['pb_native_cost_snap'] = 100 * (r['pb_native_px'] / pn0 - 1)        # buy premium vs snapshot priceNative
        if 'ps_native_px' in r and pn0 > 0:
            r['ps_native_cost_snap'] = 100 * (1 - r['ps_native_px'] / pn0)        # sell discount vs snapshot priceNative
        r['pn0'] = pn0
        # ---- whole trade: booked vs harness on the same marks
        r['pnl_booked_pct'] = 100 * fnum(t.get('pnl_usd')) / N
        qh, nf = C.entry_fill(mark_in, L0, su, fee0, N)
        vh = C.exit_value(mark_x, Lx, su, feex, qh)
        r['pnl_harness_pct'] = 100 * (vh - N - nf) / N
        qh50, _ = C.entry_fill(mark_in, L0, su, fee0, N, extra_bps=50)
        vh50 = C.exit_value(mark_x, Lx, su, feex, qh50, extra_bps=50)
        r['pnl_harness50_pct'] = 100 * (vh50 - N - nf) / N
        # ---- harness marks from the recorded dataset (post-reset trades only)
        if t.get('opened_at', 0) > t_obs0:
            full_pair = t.get('pairAddress')
            eqt = fnum(eq.get('_quotedAtMs'))
            m1 = obs_mark(con, full_pair, eqt)
            m2 = obs_mark(con, full_pair, r['x_quote_at']) if math.isfinite(r['x_quote_at']) else None
            if m1 and m1[1] and out_raw:
                r['obs_in_age_s'] = (eqt - m1[0]) / 1000
                r['obs_in_mark'] = m1[1]
                r['in_real_raw_obs'] = 100 * (1 - out_raw / 10 ** dec * m1[1] / N)
            # unconditional (not trigger-selected) buy and sell quotes vs the dataset mark at their own times
            mb = obs_mark(con, full_pair, fnum(pb.get('_quotedAtMs')))
            ms = obs_mark(con, full_pair, fnum(ps.get('_quotedAtMs')))
            if mb and mb[1] and b_raw:
                r['pb_real_raw_obs'] = 100 * (1 - b_raw / 10 ** dec * mb[1] / N)
                r['pb_obs_age_s'] = (fnum(pb.get('_quotedAtMs')) - mb[0]) / 1000
            if ms and ms[1] and s_in and s_out:
                r['ps_real_raw_obs'] = 100 * (1 - ((s_out / 1e6) / (s_in / 10 ** dec)) / ms[1])
                r['ps_obs_age_s'] = (fnum(ps.get('_quotedAtMs')) - ms[0]) / 1000
            r['_obs'] = {'mb': mb, 'ms': ms, 'm2': m2, 'm1': m1,
                         'mnext': con.execute('SELECT t, price, liq, mcap, pnative FROM o WHERE pair=? AND t>? ORDER BY t LIMIT 1',
                                              (full_pair, int(r['x_quote_at']))).fetchone() if math.isfinite(r['x_quote_at']) else None}
            if m2 and m2[1] and sold_raw and usdc_raw:
                qty = sold_raw / 10 ** dec
                r['obs_x_age_s'] = (r['x_quote_at'] - m2[0]) / 1000
                r['obs_x_mark'] = m2[1]
                r['obs_x_liq'] = m2[2]
                r['out_real_raw_obs'] = 100 * (1 - (usdc_raw / 1e6) / (qty * m2[1]))
                r['ledger_vs_obs_exit_mark_pct'] = 100 * (mark_x / m2[1] - 1)
        ob = r.pop('_obs', None)
        if ob:
            mb, ms, m2, mn = ob['mb'], ob['ms'], ob['m2'], ob['mnext']
            if mb and mb[4] and 'pb_native_px' in r:
                r['pb_native_cost_obs'] = 100 * (r['pb_native_px'] / mb[4] - 1)
                r['solusd_jup_vs_obs_pct'] = 100 * (r.get('pb_jup_solusd', float('nan')) / (mb[1] / mb[4]) - 1)
            if ms and ms[4] and 'ps_native_px' in r:
                r['ps_native_cost_obs'] = 100 * (1 - r['ps_native_px'] / ms[4])
            if m2 and m2[4] and 'xq_native_px' in r:
                r['xq_native_cost_obs'] = 100 * (1 - r['xq_native_px'] / m2[4])
            if mn and mn[1] and sold_raw and usdc_raw:
                r['obs_next_age_s'] = (mn[0] - r['x_quote_at']) / 1000
                r['out_real_raw_next'] = 100 * (1 - (usdc_raw / 1e6) / (sold_raw / 10 ** dec * mn[1]))
            m1 = ob.get('m1')
            if m1 and m1[1] and m1[2] and m1[4] and mn and mn[1] and mn[2] and mn[4]:
                # harness analogue: buy at the dataset mark at the entry quote, sell at the first dataset point
                # after the exit quote, harness costs from the dataset's own liquidity / market cap / SOL ref.
                su1, sun = m1[1] / m1[4], mn[1] / mn[4]
                f1, fn = C.fee_bps(m1[3], su1), C.fee_bps(mn[3], sun)
                for tag, xb in (('h0', 0.0), ('h50', 50.0)):
                    qh, nfee = C.entry_fill(m1[1], m1[2], su1, f1, N, extra_bps=xb)
                    vh = C.exit_value(mn[1], mn[2], sun, fn, qh, extra_bps=xb)
                    r['pnl_obs_' + tag] = 100 * (vh - N - nfee) / N
        rows.append(r)
    con.close()
    rows.sort(key=lambda r: r['opened_at'])
    with open(os.path.join(HERE, 'acct_rows.json'), 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=0, default=lambda x: None)
    print('trades', len(rows))
    return rows


if __name__ == '__main__':
    main()
