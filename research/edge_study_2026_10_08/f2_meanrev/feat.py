"""Causal dip / stabilization features for the F2 mean-reversion family.

Every feature at index k depends only on points 0..k of the same pair (forward pass with
monotonic deques).  Two ways to use it:
  * compute_series(s)       - exploration: arrays for a whole pair, built strictly in time order.
  * Tracker()(P)            - strategies: advanced through the harness Past view only
                              (P(f, back=...) reads), so it cannot see the future.
"""
import math
from collections import deque

NAN = float('nan')
WINDOWS = (300, 900, 1800, 3600)    # rolling-max windows, seconds
LOW_WIN = 300                        # rolling-min window for the recent low, seconds


class _State:
    __slots__ = ('last', 'maxq', 'minq', 'feat')

    def __init__(self):
        self.last = -1
        self.maxq = {w: deque() for w in WINDOWS}   # (t, price, k) decreasing price
        self.minq = deque()                          # (t, price, k) increasing price
        self.feat = None


def _push(st, t, p, k):
    if not (p > 0):
        return
    for w, q in st.maxq.items():
        while q and q[-1][1] <= p:
            q.pop()
        q.append((t, p, k))
        lim = t - w * 1000
        while q and q[0][0] <= lim:
            q.popleft()
    q = st.minq
    while q and q[-1][1] >= p:
        q.pop()
    q.append((t, p, k))
    lim = t - LOW_WIN * 1000
    while q and q[0][0] <= lim:
        q.popleft()


def _features(st, t, p):
    f = {}
    for w, q in st.maxq.items():
        if q and p > 0:
            f['dd%d' % w] = p / q[0][1] - 1
            f['tmax%d' % w] = (t - q[0][0]) / 1000
        else:
            f['dd%d' % w] = NAN
            f['tmax%d' % w] = NAN
    if st.minq and p > 0:
        f['low5'] = st.minq[0][1]
        f['bounce5'] = p / st.minq[0][1] - 1
        f['tlow5'] = (t - st.minq[0][0]) / 1000
    else:
        f['low5'] = NAN
        f['bounce5'] = NAN
        f['tlow5'] = NAN
    return f


class Tracker:
    """Per-pair causal state advanced through the Past view (past-only reads)."""

    def __init__(self):
        self.st = {}

    def __call__(self, P):
        pair = P.static('pair')
        st = self.st.get(pair)
        i = P.history_len() - 1
        if st is None or i < st.last:
            st = _State()
            self.st[pair] = st
        if i != st.last:
            for k in range(st.last + 1, i + 1):
                back = i - k
                _push(st, P('t', back=back), P('price', back=back), k)
            st.last = i
            st.feat = _features(st, P('t'), P('price'))
        return st.feat


def compute_series(s):
    """List of feature dicts for every index of one pair (exploration helper; causal)."""
    st = _State()
    out = []
    ts, ps = s['t'], s['price']
    for k in range(len(ts)):
        _push(st, ts[k], ps[k], k)
        out.append(_features(st, ts[k], ps[k]))
    return out
