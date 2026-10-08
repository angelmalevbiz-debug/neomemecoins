"""Tape-aware variant of H.simulate (same rules) whose ENTRY fill price can come from the on-chain tape.

Why: DexScreener prices lag the chain by ~5-30 s. Right after a large on-chain buy the series often still
shows the pre-buy price, so H.simulate would let a follower buy below the price that actually exists
on-chain. entry_mode:
    'series'  - harness behaviour (fill at series price of point j)
    'onchain' - fill at the on-chain marginal price implied by the latest swap available at t_j (block time
                within 30 s), falling back to the series price when the tape has nothing fresh
    'max'     - the higher of the two (conservative)
exit_mode 'series' (default, harness) or 'onchain' (same substitution at the exit fill point).
trigger_mode 'series' (default): exit TRIGGERS are evaluated on the series price exactly like H.simulate
(= an engine that decides on DexScreener data but executes on-chain); 'onchain' values the position at the
on-chain price whenever the tape has a fresh swap (sensitivity: a fully tape-priced engine).
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import harness as H
import onchain as OC


def _entry_fill_at(s, j, notional, price, extra_bps=0.0):
    liq, su = s['liq'][j], H.sol_usd(s, j)
    if not (price > 0 and liq > 0 and su > 0):
        return None
    fee = H.fee_bps(s, j) / 1e4
    impact = min(20.0, 2 * notional / liq * 100)
    pen = (impact + (H.BASE_BPS + extra_bps) / 100) / 100
    return notional * (1 - fee) / (price * (1 + pen)), H.NETWORK_SOL * su


def _px(s, k, mode):
    p = s['price'][k]
    if mode == 'series':
        return p, False
    oc = OC.onchain_price_usd(s, k, s['t'][k], 30)
    if oc is None:
        return p, False
    if mode == 'onchain':
        return oc, True
    if mode == 'max':
        return max(p, oc), oc > p
    if mode == 'min':
        return min(p, oc), oc < p
    raise ValueError(mode)


def simulate(signal, *, stop=-5.0, tp=10.0, trail_arm=None, trail=None, hold_min=60.0, exit_fn=None,
             notional=200.0, size_fn=None, cooldown_s=300, dexes=('pumpswap',), pairs=None,
             t_from=None, t_to=None, tag='', entry_mode='onchain', exit_mode='series', trigger_mode='series'):
    series, meta = H.load()
    t_end = meta['t1']
    out = []
    for pair, s in series.items():
        if pairs is not None and pair not in pairs:
            continue
        if dexes is not None and s['dex'] not in dexes:
            continue
        if s['quote_sol'] != 1:
            continue
        ts, n = s['t'], len(s['t'])
        i, next_ok = 0, -1.0
        while i < n - 1:
            ti = ts[i]
            if ti < next_ok or (t_from is not None and ti < t_from) or (t_to is not None and ti >= t_to):
                i += 1
                continue
            P = H.Past(s, i)
            if not signal(P):
                i += 1
                continue
            j = i + 1
            if ts[j] - ti > H.MAX_ENTRY_LAG_MS:
                i += 1
                continue
            size = size_fn(P) if size_fn else notional
            if not size or size <= 0:
                i += 1
                continue
            epx, used_oc = _px(s, j, entry_mode)
            fill = _entry_fill_at(s, j, size, epx)
            fill50 = _entry_fill_at(s, j, size, epx, H.STRESS_BPS)
            if fill is None or fill50 is None:
                i += 1
                continue
            qty, netfee = fill
            qty50 = fill50[0]
            peak, mfe, mae = -1e9, -1e9, 1e9
            pos = {'entry_t': ts[j], 'entry_i': j, 'size': size, 'qty': qty, 'net': 0.0, 'peak': 0.0}
            trig, k = None, j
            for k in range(j + 1, n):
                if trigger_mode == 'series':
                    v = H.exit_value(s, k, qty)
                else:
                    v = H.exit_value(s, k, qty, 0.0, _px(s, k, trigger_mode)[0])
                if v is None:
                    continue
                net = 100 * (v - size - netfee) / size
                mfe, mae = max(mfe, net), min(mae, net)
                peak = max(peak, net)
                pos['net'], pos['peak'] = net, peak
                if net <= stop:
                    trig = 'STOP'
                elif tp is not None and net >= tp:
                    trig = 'TP'
                elif trail_arm is not None and peak >= trail_arm and net <= peak - trail:
                    trig = 'TRAIL'
                elif (ts[k] - ts[j]) >= hold_min * 60_000:
                    trig = 'HOLD'
                elif exit_fn is not None:
                    trig = exit_fn(H.Past(s, k), pos)
                if trig:
                    break
            if trig and k + 1 < n:
                e, flag = k + 1, ''
                px = _px(s, e, exit_mode)[0]
            else:
                e = k if trig else n - 1
                trig = trig or 'OPEN_AT_END'
                last_px = _px(s, e, exit_mode)[0]   # same price source as every other exit fill
                if t_end - ts[e] > 600_000:
                    flag, px = 'VANISHED', last_px * (1 - H.VANISH_HAIRCUT_PCT / 100)
                else:
                    flag, px = 'END', last_px
            v0 = H.exit_value(s, e, qty, 0.0, px)
            v50 = H.exit_value(s, e, qty50, H.STRESS_BPS, px)
            net0 = 100 * ((v0 or 0.0) - size - netfee) / size
            net50 = 100 * ((v50 or 0.0) - size - netfee) / size
            out.append({'pair': pair, 'sym': s['sym'], 'entry_t': ts[j], 'exit_t': ts[e], 'reason': trig,
                        'flag': flag, 'net0': net0, 'net50': net50, 'usd0': size * net0 / 100,
                        'usd50': size * net50 / 100, 'size': size,
                        'mfe': mfe if mfe > -1e8 else 0.0, 'mae': mae if mae < 1e8 else 0.0,
                        'hold_s': (ts[e] - ts[j]) / 1000, 'fee_bps': H.fee_bps(s, j), 'liq': s['liq'][j],
                        'rt_cost': H.roundtrip_cost_pct(s, j, size), 'tag': tag,
                        'entry_px_series': s['price'][j], 'entry_px_used': epx, 'entry_onchain': used_oc})
            next_ok = ts[e] + cooldown_s * 1000
            i = e + 1
    out.sort(key=lambda x: x['entry_t'])
    return out
