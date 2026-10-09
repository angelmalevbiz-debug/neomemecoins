"""Signal and universe definitions for the hf_signals study (past-only event-row features). PAPER research only."""
from common import H, fin

UNIS = {
    # every universe already passes H.rug_guard_v1 (events table) and has liq >= $20k
    'U50': lambda r: r['liq'] >= 50_000 and r['fee'] <= 50,        # cheapest established pools
    'U95': lambda r: r['liq'] >= 50_000 and r['fee'] <= 95,        # non-toxic fee tiers
    # engine-equivalent STRUCTURAL_RUG_GUARD_V1 (fake mcap at 2 %, ticker reuse across mints): see add_guard.py
    'E50': lambda r: r['eg'] and r['liq'] >= 50_000 and r['fee'] <= 50,
    'E95': lambda r: r['eg'] and r['liq'] >= 50_000 and r['fee'] <= 95,
}


def _dnet(r):
    return r['db5'] - r['ds5'] if fin(r['db5']) and fin(r['ds5']) else float('nan')


def rnd(p, salt):
    return lambda r: H.hashed_coin(r['pair'], r['t'], p, salt)


SIGS = {
    'MOM_STEP_0.3': lambda r: r['r1'] >= 0.003,
    'MOM_STEP_0.5': lambda r: r['r1'] >= 0.005,
    'MOM_STEP_1.0': lambda r: r['r1'] >= 0.010,
    'REV_STEP_0.3': lambda r: r['r1'] <= -0.003,
    'REV_STEP_0.5': lambda r: r['r1'] <= -0.005,
    'REV_STEP_1.0': lambda r: r['r1'] <= -0.010,
    'MOM_2STEP_0.5': lambda r: r['r2'] >= 0.005,
    'REV_2STEP_0.5': lambda r: r['r2'] <= -0.005,
    'BUYJUMP_2': lambda r: _dnet(r) >= 2,
    'BUYJUMP_4': lambda r: _dnet(r) >= 4,
    'BUYJUMP_DB5_3': lambda r: r['db5'] >= 3,
    'SELLJUMP_REV_3': lambda r: _dnet(r) <= -3,
    'BSHARE_HI_0.62': lambda r: r['bshare'] >= 0.62,
    'BSHARE_LO_0.50': lambda r: r['bshare'] <= 0.50,
    'VACC_HI_1.0': lambda r: r['vacc'] >= 1.0,
    'VACC_LO_0.7': lambda r: r['vacc'] <= 0.7,
    'AT_15M_HIGH': lambda r: r['hi15'] >= -0.0005 and r['r1'] > 0,
    'PULLBACK_BOUNCE': lambda r: r['hi15'] <= -0.01 and r['r1'] > 0,
    'NEAR_15M_LOW': lambda r: r['lo15'] <= 0.002,
    'MKT_UP': lambda r: r['mkt_r60_120'] >= 0.0005,
    'MKT_DN': lambda r: r['mkt_r60_120'] <= -0.0005,
    'MKT_UP_MOM': lambda r: r['mkt_r60_120'] >= 0.0005 and r['r1'] > 0,
    'MKT_DN_COINDIP': lambda r: r['mkt_r300_120'] <= -0.002 and r['r1'] < 0,
    'QUIET': lambda r: r['turn'] <= 0.002 and abs(r['r1']) <= 0.006,
    'QUIET_REV': lambda r: r['turn'] <= 0.002 and -0.006 <= r['r1'] <= -0.003,
    'SLOW_REFRESH': lambda r: r['dt_prev'] >= 60,
}
RANDOM_P = 0.25
RANDOM_SALTS = ('hfA', 'hfB', 'hfC', 'hfD', 'hfE')
