import sys
from rugcommon import _series, H
for pre in ('7wCarEYc', 'BAPyQHY6', 'EpugLBw1', 'ERFmQZQU', '8ZMkMgWM', 'GhBPuDpt', 'A7cJ8yHX'):
    for p, s in _series.items():
        if p.startswith(pre):
            k = len(s['t']) - 1
            P = H.Past(s, k)
            print(s['sym'], pre, 'liq %.0f mcap %.0f lmc %.4f age_d %.1f fee %d reuse %d pump %s first_seen_h %.1f' % (
                s['liq'][k], s['mcap'][k], s['liq'][k] / s['mcap'][k], s['age'][k] / 1440, H.fee_bps(s, k),
                H.other_pairs_same_ticker_before(P), (s['mint'] or '').endswith('pump'), (s['t'][0] - H.load()[1]['t0']) / 3.6e6))
