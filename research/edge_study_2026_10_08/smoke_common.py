import harness as H


def cost_first(P):
    """The live COST_FIRST_UNIVERSE_V1 rule: PumpSwap, SOL quote, fee <= 50 bps, liq >= $250k, RT <= 1.2%."""
    liq = P('liq')
    if not (liq >= 250_000):
        return False
    if P.fee_bps() > 50:
        return False
    size = min(200.0, liq * 0.001)
    rt = P.rt_cost_pct(size)
    return rt is not None and rt <= 1.2


def rnd(prob, salt='r'):
    return lambda P: H.hashed_coin(P.static('pair'), P.t, prob, salt)
