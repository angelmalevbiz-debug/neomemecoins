"""Calibrated execution-cost adjustment for the shared PAPER backtest harness (CALIB_V1, 2026-10-08).

Apply it as the per-leg `extra_bps` of harness.entry_fill / harness.exit_value (both legs), e.g.

    import calibration as K
    x = K.extra_bps_per_leg(H.fee_bps(s, j), s['liq'][j], size)
    qty, netfee = H.entry_fill(s, j, size, x)
    proceeds = H.exit_value(s, e, qty, x + K.exit_reason_extra_bps(reason), px)

or approximately on finished harness trades: K.calibrated_net_pct(trade).

What the harness already charges per leg: PumpSwap fee tier + constant-product impact 2N/L + 20 bps
(10 slippage + 10 latency) + 0.0001 SOL network.  What the numbers below add is measured against 117 closed
PAPER account trades (deduplicated across all ledger copies; real Jupiter entry, preflight buy/sell and exit
quotes; 5 tokens carry 113 of them) plus 162 strategy-lab $10 Jupiter price probes on 12 pools.

Measured (REPORT.md has the tables; '+' = real costs more than the harness):
  * Same-instant round trip (preflight buy then sell, mark-free), engine-booked minus harness:
      fee<=50 & liq>=250k  -0.10 % (n=23, CI -0.14..-0.06)   fee55-95 & 50-250k  -0.15 % (n=61, CI -0.21..-0.09)
      fee100-125 & 50-250k -0.51 % (n=29, CI -0.80..-0.27)    -> fee tier + 2N/L is right or conservative.
      Pool-leg half spread minus (fee + 2N/L) = -0.03 % mean (n=87): the fee table is validated for tiers 30-105.
  * Entry fill vs the DexScreener mark the harness fills at: real buys land ABOVE the mark by more than the model
    (fresh dataset marks, post-reset, n=25, booked-minus-harness +0.35 %, CI -0.00..+0.80, p90 +1.5 %; lab probes
    fee<=50 +0.50 %, fee55-95 +0.09 %, fee100-125 -0.60 % in harness-equivalent terms). The same-instant SELL is
    correspondingly better than the model (-0.67 %): the mark lags the pool in the direction of the recent move.
  * Exits: unconditional sells are not worse than the model; exits triggered by an adverse quote/move are:
      EXIT_IMPACT_EMERGENCY (quote-triggered) +0.18 % vs the harness next-point fill (n=20);
      STOP_LOSS +1.97 % vs the harness next-point fill (n=3, fresh marks; +3.78 % on n=30 older trades whose
      ledger marks were partly stale).
  * Whole trade, harness analogue on dataset marks (n=25 post-reset, fee 30 pools mostly):
      booked minus harness = -0.76 %/trade (CI -1.25..-0.26); non-stop exits -0.56 % (n=22, CI -1.14..-0.03)
      -> ~28 bps per leg at the trades' notional; with +50 bps/leg stress the mean gap is +0.23 % (covered on
      average, not per trade: 60 % of trades covered).
  * Engine fixed costs the harness omits: token-account rent ~0.185 USD booked as a loss per trade (refundable
    on-chain, but the PAPER engine books it) and 0.03 USD network per leg vs the harness 0.0001 SOL (~0.012 USD).

Coverage limits: notional 12-200 USD, N/L 0.009-0.25 %, liquidity 33k-465k USD (only 2 trades < 50k),
fee tiers 30 / 60 / 75 / 90-120.  Outside that range the conservative mode adds UNMEASURED_MARGIN_BPS.
"""

VERSION = 'CALIB_V1_2026-10-08'

# --- variable component per leg (bps), engine-booked basis (raw Jupiter quote minus the engine's 10 bps/leg buffer)
# central = measured point estimate; conservative = what we recommend applying (never below FLOOR_BPS)
CENTRAL_BPS = {
    'fee<=50': 22.0,      # whole-trade fit on n=22 non-stop post-reset trades (28 bps/leg incl. ~5.5 fixed at $200);
                          # component check: entry lag +40/2 - same-instant RT -10/2 + quote-exit selection +18/2 = +24
    'fee55-95': -5.0,     # entry lag +9/2 (lab probes), same-instant RT -15/2 = -3; quote-triggered exits here were
                          # not worse than the mark (EIE exits -0.48 %, n=11, older ledger marks) -> rounded to -5
    'fee100-125': -55.0,  # entry lag -60/2 (lab probes), same-instant RT -51/2: the harness over-costs these tiers
}
FLOOR_BPS = 10.0               # conservative floor: no measured under-costing, but thin fresh-mark evidence off fee<=50
UNMEASURED_MARGIN_BPS = 25.0   # prior (not a measurement) for liq < 50k or N/L > 0.3 % (outside the calibrated range)

# --- fixed engine costs not in the harness (engine basis only)
ENGINE_RENT_USD = 0.185              # token-account rent the PAPER engine books as a loss once per trade
ENGINE_NETWORK_USD_PER_LEG = 0.03
HARNESS_NETWORK_USD_PER_LEG = 0.012  # 0.0001 SOL at ~120 USD/SOL

# --- exit-reason specific extra on the EXIT leg only (bps)
STOP_EXIT_EXTRA_BPS = 200.0    # fresh-mark STOP exits +197 bps vs the harness next-point fill (n=3, CI 119..275)
TRAIL_EXIT_EXTRA_BPS = 100.0   # prior: trailing exits also fire after an adverse move; unmeasured (no trail closes)

CAL_LIQ_MIN = 50_000.0
CAL_NL_MAX_PCT = 0.30


def fee_bucket(fee_bps):
    f = float(fee_bps)
    return 'fee<=50' if f <= 50 else ('fee55-95' if f <= 95 else 'fee100-125')


def in_calibrated_range(fee_bps, liq_usd, notional_usd):
    try:
        liq, n = float(liq_usd), float(notional_usd)
    except (TypeError, ValueError):
        return False
    return liq >= CAL_LIQ_MIN and n > 0 and 100.0 * n / liq <= CAL_NL_MAX_PCT


def fixed_engine_bps_per_leg(notional_usd):
    """Rent (half per leg) plus the network-fee difference, in bps of notional per leg."""
    n = float(notional_usd)
    if not n > 0:
        return 0.0
    return 1e4 * (ENGINE_RENT_USD / 2 + (ENGINE_NETWORK_USD_PER_LEG - HARNESS_NETWORK_USD_PER_LEG)) / n


def extra_bps_per_leg(fee_bps, liq_usd, notional_usd, basis='engine', conservative=True):
    """Extra cost in bps per leg to add on top of the harness model (apply to BOTH legs).

    basis='engine'  : match what the PAPER engine books (Jupiter quote - 10 bps buffer, 0.03 USD network/leg, rent).
    basis='market'  : match the raw Jupiter quote only (no engine buffer, no rent, harness network).
    conservative=True (recommended): floor at FLOOR_BPS and add UNMEASURED_MARGIN_BPS outside the calibrated range.
    conservative=False: central point estimates (may be negative), for sensitivity analysis only.
    """
    b = fee_bucket(fee_bps)
    x = CENTRAL_BPS[b]
    if basis == 'market':
        x -= 10.0  # the engine's own 10 bps/leg fill buffer is not a market cost
    elif basis != 'engine':
        raise ValueError('basis must be engine or market')
    if conservative:
        x = max(x, FLOOR_BPS if basis == 'engine' else 0.0)
        if not in_calibrated_range(fee_bps, liq_usd, notional_usd):
            x += UNMEASURED_MARGIN_BPS
    if basis == 'engine':
        x += fixed_engine_bps_per_leg(notional_usd)
    return float(x)


def exit_reason_extra_bps(reason, conservative=True):
    """Additional bps on the EXIT leg for exits fired by an adverse move (the mark lags the pool)."""
    r = str(reason or '').upper()
    if r.startswith('STOP'):
        return STOP_EXIT_EXTRA_BPS
    if r.startswith('TRAIL') and conservative:
        return TRAIL_EXIT_EXTRA_BPS
    return 0.0


def extra_bps_for_series(s, k, notional_usd, harness_module=None, **kw):
    """Convenience: read the fee tier and liquidity of harness series s at point k."""
    if harness_module is None:
        import harness as harness_module  # noqa: the shared harness, same API as the audited one
    return extra_bps_per_leg(harness_module.fee_bps(s, k), s['liq'][k], notional_usd, **kw)


def calibrated_net_pct(trade, basis='engine', conservative=True, start='net0'):
    """First-order re-pricing of a finished harness trade dict (fields net0, fee_bps, liq, size, reason).

    Each extra bp on both legs lowers net % by ~2 bp (exact factor (1-e)^2); exit-reason extra on one leg.
    Returns the calibrated net % (use instead of net0; compare with net50 to see if the stress covered it).
    """
    x = extra_bps_per_leg(trade['fee_bps'], trade['liq'], trade['size'], basis, conservative)
    e = exit_reason_extra_bps(trade.get('reason'), conservative)
    return float(trade[start]) - (2.0 * x + e) / 100.0


def calibrated_usd(trade, **kw):
    return trade['size'] * calibrated_net_pct(trade, **kw) / 100.0


if __name__ == '__main__':
    for fee in (30, 47.5, 75, 95, 100, 125):
        for liq in (30e3, 100e3, 450e3):
            for n in (50, 200):
                print('fee %5.1f liq %7.0f N %4d -> engine %6.1f bps/leg (central %6.1f), market %6.1f' % (
                    fee, liq, n, extra_bps_per_leg(fee, liq, n), extra_bps_per_leg(fee, liq, n, conservative=False),
                    extra_bps_per_leg(fee, liq, n, basis='market')))
