"""Past-only order-flow features from the swap tape.

A feature computed for decision time t uses only swap events with available <= t (the moment the
engine actually had them) and event_time in (t - W, t]. Events that arrive late (backfill) are
therefore invisible until they arrive, exactly as live.
"""
import bisect, math
import tape_load as TL

NAN = float('nan')


class PairTape:
    __slots__ = ('et', 'av', 'side', 'w', 'sol', 'tok', 'av_sorted', 'first_seen')

    def __init__(self, d):
        self.et, self.av, self.side, self.w, self.sol, self.tok = d['et'], d['av'], d['side'], d['w'], d['sol'], d['tok']
        self.av_sorted = sorted(d['av'])
        # first event_time each wallet appears in this pair (for "new buyer" features; used only with av filter)
        fs = {}
        for k, w in enumerate(self.w):
            if w not in fs:
                fs[w] = k
        self.first_seen = fs

    def covered(self, t, within_s=300):
        """True when at least one event became available in (t - within_s, t]."""
        a = self.av_sorted
        k = bisect.bisect_right(a, t) - 1
        return k >= 0 and a[k] > t - within_s * 1000

    def window(self, t, W):
        """Indices of events with event_time in (t-W s, t] and available <= t."""
        lo = bisect.bisect_right(self.et, t - W * 1000)
        hi = bisect.bisect_right(self.et, t)
        av = self.av
        return [k for k in range(lo, hi) if av[k] <= t]

    def last_price(self, t, max_back_s=600):
        """Implied SOL/token price of the latest swap available by t (event_time within max_back_s)."""
        hi = bisect.bisect_right(self.et, t)
        lo = bisect.bisect_right(self.et, t - max_back_s * 1000)
        for k in range(hi - 1, lo - 1, -1):
            if self.av[k] <= t and self.tok[k] > 0 and self.sol[k] > 0:
                return self.sol[k] / self.tok[k]
        return NAN

    def feats(self, t, W):
        idx = self.window(t, W)
        bs = ss = 0.0
        nb = ns = 0
        buyers, sellers = set(), set()
        mx = 0.0
        newb = 0
        for k in idx:
            v = self.sol[k]
            if not (v == v):
                continue
            if self.side[k] > 0:
                bs += v
                nb += 1
                buyers.add(self.w[k])
                mx = max(mx, v)
            else:
                ss += v
                ns += 1
                sellers.add(self.w[k])
        tot = bs + ss
        return {'nb': nb, 'ns': ns, 'bsol': bs, 'ssol': ss, 'net': bs - ss, 'ub': len(buyers), 'us': len(sellers),
                'mxb': mx, 'bshare': bs / tot if tot > 0 else NAN, 'n': nb + ns,
                'rep': (nb / len(buyers)) if buyers else NAN}


_PT = None


def tapes():
    global _PT
    if _PT is None:
        T = TL.load()
        _PT = {p: PairTape(d) for p, d in T['tape'].items()}
    return _PT
