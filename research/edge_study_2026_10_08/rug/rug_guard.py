"""Past-only rug / drain guard for the recorded PumpSwap dataset (research deliverable, PAPER only).

rug_guard_v1(P) -> True means DO NOT TRADE this pool at this decision point.
P is the harness Past view (harness.Past); only P(...) at the current point and P.static() are used.

Rule (thresholds chosen on the TRAIN 60% of the 22.8 h dataset only; see REPORT.md):
  block when ANY of
    1. LP_PULLABLE     liquidity_usd >= market_cap_usd          (liq/mcap >= 1.0)
       A normal pump.fun graduation leaves ~20% of supply in the pool (liq/mcap ~0.4 at migration, lower
       as the price rises). liq/mcap >= 1 means the pool holds most of the supply: a creator-seeded pool
       whose LP tokens the creator controls. Of the 30 pools with this signature at >= $20k liquidity
       whose fate was observable (>= 5 min of data after the signal), 24 were LP-pulled (liquidity -> 0,
       price unchanged, no swaps on the tape) within the 22.8 h; 4 were alive at the end.
       (203 more appeared for ~1 min in the new-pools feed and vanished: fate unknown.)
    2. YOUNG_POOL      pair age < 720 minutes (12 h)
       TRAIN 2 h drain rate by pair age (all liquidity): <15 m 87%, 15-60 m 62%, 1-6 h 22%,
       6-24 h 5%, 1-14 d 1.5%. The age gate is the single strongest past-only predictor.
    3. FAKE_MCAP       market_cap_usd >= $20M and liq/mcap < 1% and pair age < 14 days
       The serial fake-market-cap family (USDP, IOF, UDR, DOTF, GOIF, WSOS, VSOF, SARP ...):
       $250M-$1B 'market cap' on $1-3M of liquidity, supply outside the pool, drained by the holder.
       (1% vs 2% tied on TRAIN; 1% was taken for fewer flags. HOLDOUT later showed 2% would also have
       caught DAWS (1.86%) - see REPORT.md; that variant is holdout-informed, not validated.)
  and fail closed: unknown / non-positive liquidity, market cap or age -> block.
  (Docstring corrected after the freeze; the rule logic and thresholds are unchanged.)

rug_guard_structural(P): rules 1 + 3 only (no age gate) - for strategies that must trade young pools;
  it leaves the young-pool hazard in place (see REPORT.md hazard table).
"""
import sys

sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402  (kept for API parity; the guard itself needs only the Past view)

VERSION = 'RUG_GUARD_V1_RESEARCH_2026_10_08'
LP_PULLABLE_MIN_LIQ_TO_MCAP = 1.0
YOUNG_POOL_MAX_AGE_MIN = 720.0
FAKE_MCAP_MIN_USD = 20e6
FAKE_MCAP_MAX_LIQ_TO_MCAP = 0.01
FAKE_MCAP_MAX_AGE_MIN = 14 * 1440.0


def _inputs(P):
    liq, mc, age = P('liq'), P('mcap'), P('age')
    ok = liq == liq and mc == mc and age == age and liq > 0 and mc > 0 and age >= 0
    return ok, liq, mc, age


def rug_reasons(P, young_gate=True):
    """List of rule names that fire at this point (empty list = pass)."""
    ok, liq, mc, age = _inputs(P)
    if not ok:
        return ['INPUT_UNKNOWN']
    out = []
    lmc = liq / mc
    if lmc >= LP_PULLABLE_MIN_LIQ_TO_MCAP:
        out.append('LP_PULLABLE')
    if young_gate and age < YOUNG_POOL_MAX_AGE_MIN:
        out.append('YOUNG_POOL')
    if mc >= FAKE_MCAP_MIN_USD and lmc < FAKE_MCAP_MAX_LIQ_TO_MCAP and age < FAKE_MCAP_MAX_AGE_MIN:
        out.append('FAKE_MCAP')
    return out


def rug_guard_v1(P):
    """True = do not trade (rules 1 + 2 + 3, fail closed)."""
    return bool(rug_reasons(P, young_gate=True))


def rug_guard_structural(P):
    """True = do not trade (rules 1 + 3 only, fail closed)."""
    return bool(rug_reasons(P, young_gate=False))


def young_only(P):
    """Reference: rule 2 alone (the strict train-objective winner)."""
    ok, liq, mc, age = _inputs(P)
    return (not ok) or age < YOUNG_POOL_MAX_AGE_MIN
