"""F3 helpers: early-life universe, signals, exits, robustness re-pricing. Past-only by construction."""
import sys, math, bisect
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H

NAN = float('nan')


def observed_for(P, seconds):
    """True when this pool already had a point at least `seconds` ago (it has been in the feed that long)."""
    return P.ago('t', seconds) == P.ago('t', seconds)  # not NaN


def f3_rug_screen(P, min_liq=15_000, lm_lo=0.15, lm_hi=0.70):
    """Strict early-life screen: interim serial-rug guard + sane pump-graduate shape.
    - not H.interim_rug_risk (fake mcap / ticker reuse)
    - liquidity >= min_liq and liquidity/mcap within [lm_lo, lm_hi] (pump graduates sit ~0.3-0.45;
      very low = thin pool behind an inflated cap, very high = odd pool)
    - liquidity not collapsing: liq >= 85% of its max over the last 15 min
    - no single-print price spike > 3x in the last 5 points (data glitch / pull)"""
    liq, mc = P('liq'), P('mcap')
    if not (liq >= min_liq and mc > 0):
        return False
    lm = liq / mc
    if not (lm_lo <= lm <= lm_hi):
        return False
    w = P.window('liq', 900)
    if w and liq < 0.85 * max(w):
        return False
    for b in range(1, 5):
        p0, p1 = P('price', back=b), P('price', back=b - 1)
        if p0 > 0 and p1 > 0 and (p1 / p0 > 3 or p0 / p1 > 3):
            return False
    if H.interim_rug_risk(P):
        return False
    return True


def young(P, max_age):
    a = P('age')
    return a == a and a < max_age


def ratio(a, b):
    return a / b if (b > 0 and a == a) else NAN


def organic(P, liq_up=1.10, px_up=1.10, min_uw=5, br_lo=0.5, br_hi=0.8):
    """Organic growth: liquidity and price both up over 15 min, several unique buyers, buy-heavy but not one-sided."""
    if not (ratio(P('liq'), P.ago('liq', 900)) >= liq_up):
        return False
    if not (ratio(P('price'), P.ago('price', 900)) >= px_up):
        return False
    uw = P('hp_uw5')
    if not (uw >= min_uw):
        return False
    b, s = P('b5'), P('s5')
    if not (b + s > 0):
        return False
    br = b / (b + s)
    return br_lo <= br <= br_hi


def breakout(P, min_pc1h=20.0, min_b5=30):
    return P('pc1h') >= min_pc1h and P('b5') >= min_b5 and P('pc5') > 0


def drain_exit(frac=0.6):
    """Exit when liquidity falls below frac x liquidity at entry (rug in progress)."""
    def fn(P, pos):
        back = P.i - pos['entry_i']
        l0 = P('liq', back=back)
        l = P('liq')
        if l0 > 0 and not (l >= frac * l0):
            return 'DRAIN'
        return None
    return fn


# ---------------------------------------------------------------- robustness re-pricing
def cp_net50(trade):
    """Re-price a harness trade's exit with uncapped constant-product impact (R = liq/2) and the +50 bps
    stress; entry as in the harness. Drained pools then return ~0 instead of the harness's 20%-capped haircut."""
    series, meta = H.load()
    s = series[trade['pair']]
    ts = s['t']
    j = bisect.bisect_left(ts, trade['entry_t'])
    e = bisect.bisect_left(ts, trade['exit_t'])
    f = H.entry_fill(s, j, trade['size'], H.STRESS_BPS)
    if f is None:
        return trade['net50']
    qty, nf = f
    px = s['price'][e] * (0.9 if trade['flag'] == 'VANISHED' else 1.0)
    liq = s['liq'][e]
    R = liq / 2 if liq > 0 else 1e-9
    V = qty * px
    got = R * V / (R + V) * (1 - (H.BASE_BPS + H.STRESS_BPS) / 1e4) * (1 - H.fee_bps(s, e) / 1e4) - H.NETWORK_SOL * H.sol_usd(s, e)
    return 100 * (max(0.0, got) - trade['size'] - nf) / trade['size']


def gap_flag(trade, hold_min):
    """True when the exit fill came long after the hold limit because the feed had a gap (unknowable price)."""
    return trade['hold_s'] > hold_min * 60 + 600


def robust_view(trades, hold_min):
    """Mean net50 % with (a) uncapped CP exits and (b) gap trades removed."""
    if not trades:
        return {}
    cp = [cp_net50(x) for x in trades]
    ng = [x['net50'] for x in trades if not gap_flag(x, hold_min)]
    return {'cp_mean_pct': round(sum(cp) / len(cp), 3), 'cp_median_pct': round(sorted(cp)[len(cp) // 2], 3),
            'gap_trades': len(trades) - len(ng),
            'nogap_mean_pct': round(sum(ng) / len(ng), 3) if ng else None,
            'vanished': sum(1 for x in trades if x['flag'] == 'VANISHED')}


KEEP = ('n', 'pairs', 'clusters', 'win_rate', 'mean_pct', 'median_pct', 'mean_usd', 'sum_usd', 'pf', 'ci95_mean_usd',
        'trades_per_hour', 'avg_hold_min', 'top_pair_share', 'exits')


def brief(summ):
    return {k: summ.get(k) for k in KEEP}
