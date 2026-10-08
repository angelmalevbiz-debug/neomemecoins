"""Shared tape helpers for the audit (read-only over tape_snapshot.sqlite3)."""
import bisect, json, math, sqlite3
import harness as H

TAPE = H.DEEP + '/tape_snapshot.sqlite3'


def load_tape(pairs=None, min_events=0):
    """pair -> (times_ms list, raw SOL/token price list, is_buy list), sorted by time then event index."""
    con = sqlite3.connect('file:%s?mode=ro' % TAPE, uri=True)
    out = {}
    q = 'select pair, event_time, payload from events'
    for pair, et, payload in con.execute(q):
        if pairs is not None and pair not in pairs:
            continue
        d = json.loads(payload)
        if (d.get('quote_asset') or '') != 'So11111111111111111111111111111111111111112':
            continue
        ta, qa = d.get('token_amount'), d.get('quote_amount')
        if not (ta and qa and ta > 0 and qa > 0):
            continue
        out.setdefault(pair, []).append((et, d.get('event_index') or 0, qa / ta, d.get('direction') == 'BUY', qa))
    con.close()
    res = {}
    for p, ev in out.items():
        if len(ev) < min_events:
            continue
        ev.sort()
        res[p] = ([e[0] for e in ev], [e[2] for e in ev], [e[3] for e in ev], [e[4] for e in ev])
    return res


class TapePrice:
    """Fee-adjusted last-trade mid estimate from the tape for one pair."""

    def __init__(self, s, tape, buy_incl_fee=True):
        self.s = s
        self.ts, self.px, self.buy, self.qa = tape
        self.buy_incl_fee = buy_incl_fee

    def fee_at(self, t):
        k = max(0, bisect.bisect_right(self.s['t'], t) - 1)
        return H.fee_bps(self.s, k) / 1e4

    def mid(self, t, max_age_ms=10_000, adjust=True):
        a = bisect.bisect_right(self.ts, t) - 1
        if a < 0 or t - self.ts[a] > max_age_ms:
            return None
        # use the last second's trades: geometric mean of fee-adjusted prices of the swaps in that block-second
        sec = self.ts[a]
        b = a
        vals = []
        while b >= 0 and self.ts[b] == sec:
            p = self.px[b]
            if adjust:
                f = self.fee_at(t)
                p = p / (1 + f) if self.buy[b] else p / (1 - f)
            vals.append(math.log(p))
            b -= 1
        return math.exp(sum(vals) / len(vals))

    def dense(self, t, before_ms=10_000, after_ms=10_000):
        a = bisect.bisect_right(self.ts, t) - 1
        if a < 0 or t - self.ts[a] > before_ms:
            return False
        return a + 1 < len(self.ts) and self.ts[a + 1] - t <= after_ms
