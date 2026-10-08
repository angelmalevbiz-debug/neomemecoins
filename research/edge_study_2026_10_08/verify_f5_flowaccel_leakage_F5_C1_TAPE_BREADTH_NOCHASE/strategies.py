"""VERIFIER configs for F5_C1_TAPE_BREADTH_NOCHASE (leakage / implementation lens). NOT candidates.

These exist so the integrator can re-run the independent re-implementation and the latency-honest variant:
  VERIFY_F5_C1_INDEP        independent re-coding of the C1 rule (own tape reader, same 'first-time buyer'
                            semantics as the original); reproduces the original 91 trades exactly under harness_final.
  VERIFY_F5_C1_INDEP_LAG5S  identical rule, but an on-chain swap is usable only 5 s after its ingestion time
                            (decision latency between the tape process and the engine). Holdout turns negative.
Baseline: random entries (hashed coin p=0.0028/point, salt 'vrf0') in the same tape-live, fee 52.5-125 bps,
liq >= $20k, interim-rug-screened universe with the same no-chase filter and the same exits/sizing.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: E402

_HERE = (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'/verify_f5_flowaccel_leakage_F5_C1_TAPE_BREADTH_NOCHASE'
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import indep as I  # noqa: E402  (independent, past-only tape reader over tape_snapshot.sqlite3, read-only)

_sig = I.make_signal(H, newdef='event', lag_ms=0)
_sig_lag5 = I.make_signal(H, newdef='event', lag_ms=5000)
_univ = I.universe_signal(H)

CONFIGS = [
    {'name': 'VERIFY_F5_C1_INDEP',
     'description': 'Independent re-implementation of F5_C1_TAPE_BREADTH_NOCHASE: PumpSwap fee 52.5-125 bps, liq >= $20k, '
                    'not H.interim_rug_risk, tape-live (newest ingested swap <= 10 min old); 5-min no-chase [-2%, +5%]; '
                    'last 5 min of ingested swaps: >= 10 distinct buyers, >= 5 first-time buy events, top buyer < 30% '
                    'of buy SOL, net SOL inflow > 0. Exit -10/+6 net, 30 min; size min($200, 0.2% liq). Verification only.',
     'signal': _sig, 'kwargs': dict(I.EXITS)},
    {'name': 'VERIFY_F5_C1_INDEP_LAG5S',
     'description': 'Same rule, but a tape swap is usable only when ingested >= 5 s before the decision (latency check). '
                    'Verification only.',
     'signal': _sig_lag5, 'kwargs': dict(I.EXITS)},
]

BASELINES = [
    {'name': 'VERIFY_F5_RANDOM_TAPE_LIVE_NOCHASE',
     'description': 'Random entries (hashed coin p=0.0028/point, salt vrf0) in the same tape-live universe with the same '
                    'no-chase filter, exits and sizing.',
     'signal': (lambda P: _univ(P) and H.hashed_coin(P.static('pair'), P.t, 0.0028, 'vrf0')), 'kwargs': dict(I.EXITS)},
]
