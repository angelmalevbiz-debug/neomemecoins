"""F7 shared definitions: universes, a-priori entry signals, size rule. Past-only (Past view) logic only."""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H


def size_rule(P):
    """$200, scaled down in thin pools so modeled impact stays <= 0.4%/leg (N <= 0.2% of liquidity)."""
    liq = P('liq')
    return min(200.0, 0.002 * liq) if liq > 0 else 0.0


def utag(P):
    """Universe tag at the decision point (None = outside every F7 universe). Always rug-screened."""
    liq = P('liq')
    if not (liq >= 20_000):
        return None
    if H.interim_rug_risk(P):
        return None
    fee = P.fee_bps()
    if fee <= 50:
        if liq >= 250_000:
            size = min(200.0, liq * 0.001)
            rt = P.rt_cost_pct(size)
            if rt is not None and rt <= 1.2:
                return 'cf'
        return 'lowfee' if liq >= 50_000 else 'other20'
    if fee <= 95:
        return 'mid' if liq >= 50_000 else 'other20'
    return 'hi' if liq >= 50_000 else 'hi20'


UNIVERSES = {
    'cf': ('cf',),
    'mid': ('mid',),
    'hi': ('hi',),
    'hi20': ('hi20',),
    'all50': ('cf', 'lowfee', 'mid', 'hi'),
}


def in_universe(name):
    tags = UNIVERSES[name]

    def f(P):
        return utag(P) in tags
    return f


# ---- a-priori entry signals (fixed before looking at any result; not tuned)
def sig_rnd(P, prob=0.05, salt='f7'):
    return H.hashed_coin(P.static('pair'), P.t, prob, salt)


def sig_mom(P):
    """Momentum: 5m and 1h DexScreener change both up, buyers outnumber sellers in 5m."""
    return P('pc5') >= 3 and P('pc1h') >= 10 and P('b5') > P('s5')


def sig_vacc(P):
    """Volume acceleration: last-5m volume >= 2.5x the 1h average 5m volume, >= $3k, buys >= sells."""
    v5, v1h = P('v5'), P('v1h')
    return v5 >= 3000 and v1h > 0 and 12 * v5 >= 2.5 * v1h and P('b5') >= P('s5')


SIGNALS = {'rnd': sig_rnd, 'mom': sig_mom, 'vacc': sig_vacc}
