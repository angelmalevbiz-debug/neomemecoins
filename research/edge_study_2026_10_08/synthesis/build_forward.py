"""Build the FORWARD (post-cutoff) extension of the dataset, read-only.

Streams the engine's append-only observations log from the dataset's cutoff byte (13,206,393,390, from ../build.out)
to the end of file at start time (capped), parses rows exactly like ../build_dataset.py, and keeps points with the same
rule as harness build_cache (engine batch time 'upd' increases, or >= 15 s since the last kept point). Writes ONLY
synthesis/fwd_series.pkl. Never writes to the runtime; opens the log read-only.
"""
import array, json, math, os, pickle, sys, time
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SRC = 'C:/Users/Chavd/neomemecoins/.runtime/accounts/training/observations.jsonl'
START = 13_206_393_390
CAP = 3_000_000_000
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fwd_series.pkl')
SOL = 'So11111111111111111111111111111111111111112'
NUM = ['t', 'price', 'pnative', 'liq', 'mcap', 'v5', 'v1h', 'v6h', 'v24', 'pc5', 'pc1h', 'pc6h', 'pc24',
       'b5', 's5', 'b1h', 's1h', 'b6h', 's6h', 'age', 'boost', 'score', 'risk',
       'vf_trades', 'vf_buy', 'vf_sell', 'vf_wallets', 'vf_age_ms', 'sf_buy', 'sf_sell', 'sf_wallets',
       'hp_uw5', 'hp_rb5', 'conv', 'src']
SRC_BITS = {'boosted': 1, 'boosted-latest': 2, 'latest': 4, 'gecko-new-pools': 8, 'pumpswap-address-catalog': 16}
NAN = float('nan')


def f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else NAN
    except (TypeError, ValueError):
        return NAN


def g(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def main():
    t0 = time.time()
    size = os.path.getsize(SRC)
    end = min(size, START + CAP)
    data, last = {}, {}
    rows = kept = bad = 0
    with open(SRC, 'rb') as fh:
        fh.seek(START - 1)
        prev = fh.read(1)
        if prev != b'\n':
            fh.readline()  # START was not on a line boundary: skip the partial line
        while fh.tell() < end:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b'\n'):
                break  # a line still being written at the end of file
            try:
                r = json.loads(line)
            except Exception:
                bad += 1
                continue
            rows += 1
            c = r.get('coin') or {}
            pair = c.get('pairAddress')
            t = r.get('observed_at')
            if not pair or not t:
                continue
            ctx = r.get('context') or {}
            ff = ctx.get('fast_flow') or r.get('flow') or {}
            vf = ff.get('verified_flow') or {}
            sf = ctx.get('slow_flow') or {}
            hp = ctx.get('holder_proxy') or {}
            upd = c.get('updatedAt')
            lt = last.get(pair)
            if lt is not None:
                last_upd, last_t = lt
                if t <= last_t:
                    continue
                newer = upd is not None and (last_upd is None or upd > last_upd)
                if not (newer or t - last_t >= 15_000):
                    continue
                if newer:
                    last_upd = upd
                last[pair] = (last_upd, t)
            else:
                last[pair] = (upd, t)
            s = data.get(pair)
            if s is None:
                s = {k: array.array('d') for k in NUM}
                s.update({'pair': pair, 'mint': c.get('address'), 'dex': (c.get('dexId') or '').lower(),
                          'quote_sol': 1 if c.get('quoteTokenAddress') == SOL else 0, 'sym': c.get('symbol'),
                          'created': c.get('pairCreatedAt')})
                data[pair] = s
            flags = 0
            for name in (c.get('sources') or []):
                flags |= SRC_BITS.get(name, 0)
            vf_age = (t - vf.get('latest_event_at')) if (vf and vf.get('latest_event_at') and t) else None
            vals = (t, f(c.get('priceUsd')), f(c.get('priceNative')), f(c.get('liquidityUsd')), f(c.get('marketCap')),
                    f(g(c, 'volume', 'm5')), f(g(c, 'volume', 'h1')), f(g(c, 'volume', 'h6')), f(g(c, 'volume', 'h24')),
                    f(g(c, 'priceChange', 'm5')), f(g(c, 'priceChange', 'h1')), f(g(c, 'priceChange', 'h6')),
                    f(g(c, 'priceChange', 'h24')),
                    f(g(c, 'txns', 'm5', 'buys')), f(g(c, 'txns', 'm5', 'sells')), f(g(c, 'txns', 'h1', 'buys')),
                    f(g(c, 'txns', 'h1', 'sells')), f(g(c, 'txns', 'h6', 'buys')), f(g(c, 'txns', 'h6', 'sells')),
                    f(c.get('ageMinutes')), f(c.get('boostAmount')), f(c.get('score')), f(c.get('riskScore')),
                    f(vf.get('trades')), f(vf.get('buy_usd')), f(vf.get('sell_usd')), f(vf.get('unique_wallets')),
                    f(vf_age), f(sf.get('buy_usd')), f(sf.get('sell_usd')), f(sf.get('unique_wallets')),
                    f(hp.get('unique_wallets_5m')), f(hp.get('repeat_buy_wallets_5m')), f(ctx.get('conviction')),
                    float(flags))
            for k, v in zip(NUM, vals):
                s[k].append(v)
            kept += 1
        end_pos = fh.tell()
    t_min = min(s['t'][0] for s in data.values())
    t_max = max(s['t'][-1] for s in data.values())
    meta = {'start_byte': START, 'end_byte': end_pos, 'file_size_at_start': size, 'rows': rows, 'points': kept,
            'bad': bad, 'pairs': len(data), 't_min': t_min, 't_max': t_max}
    with open(OUT, 'wb') as fh:
        pickle.dump({'meta': meta, 'series': data}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print('forward', meta, 'hours %.2f' % ((t_max - t_min) / 3.6e6), 'secs', round(time.time() - t0, 1))


if __name__ == '__main__':
    main()
