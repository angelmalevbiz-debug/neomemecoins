"""Read-only extraction of the main observations log into a compact SQLite dataset.

Never writes to the runtime. Reads the live append-only file up to a fixed byte cutoff.
"""
import json, os, sys, time, sqlite3
from multiprocessing import Pool

SRC = os.environ.get('NEO_OBSERVATIONS', 'C:/Users/Chavd/neomemecoins/.runtime/accounts/training/observations.jsonl')  # read-only input
OUT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(OUT, 'obs.sqlite3')
COLS = ['t','upd','pair','mint','dex','quote_sol','sym','price','pnative','liq','mcap','fdv',
        'v5','v1h','v6h','v24','pc5','pc1h','pc6h','pc24','b5','s5','b1h','s1h','b6h','s6h','b24','s24',
        'age','created','boost','sources','score','risk','posture','fee_bps','sol_usd',
        'ff_q','vf_trades','vf_buy','vf_sell','vf_wallets','vf_age_ms',
        'sf_q','sf_buy','sf_sell','sf_wallets','sf_trades','hp_uw5','hp_rb5','conv','mode','safe','rej']

def g(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d

def num(x):
    try:
        return float(x) if x is not None else None
    except Exception:
        return None

def row(r):
    c = r.get('coin') or {}
    ctx = r.get('context') or {}
    ff = ctx.get('fast_flow') or r.get('flow') or {}
    vf = ff.get('verified_flow') or {}
    sf = ctx.get('slow_flow') or {}
    hp = ctx.get('holder_proxy') or {}
    ex = r.get('execution') or {}
    t = r.get('observed_at')
    vf_age = None
    if vf and vf.get('latest_event_at') and t:
        vf_age = t - vf.get('latest_event_at')
    return (t, c.get('updatedAt'), c.get('pairAddress'), c.get('address'), c.get('dexId'),
            1 if c.get('quoteTokenAddress') == 'So11111111111111111111111111111111111111112' else 0,
            c.get('symbol'), num(c.get('priceUsd')), num(c.get('priceNative')), num(c.get('liquidityUsd')),
            num(c.get('marketCap')), num(c.get('fdv')),
            num(g(c,'volume','m5')), num(g(c,'volume','h1')), num(g(c,'volume','h6')), num(g(c,'volume','h24')),
            num(g(c,'priceChange','m5')), num(g(c,'priceChange','h1')), num(g(c,'priceChange','h6')), num(g(c,'priceChange','h24')),
            g(c,'txns','m5','buys'), g(c,'txns','m5','sells'), g(c,'txns','h1','buys'), g(c,'txns','h1','sells'),
            g(c,'txns','h6','buys'), g(c,'txns','h6','sells'), g(c,'txns','h24','buys'), g(c,'txns','h24','sells'),
            num(c.get('ageMinutes')), c.get('pairCreatedAt'), num(c.get('boostAmount')), '|'.join(c.get('sources') or []),
            num(c.get('score')), num(c.get('riskScore')), c.get('posture'), num(ex.get('dex_fee_bps')), num(ex.get('sol_usd_reference')),
            ff.get('quality'), vf.get('trades'), num(vf.get('buy_usd')), num(vf.get('sell_usd')), vf.get('unique_wallets'), vf_age,
            sf.get('quality'), num(sf.get('buy_usd')), num(sf.get('sell_usd')), sf.get('unique_wallets'), sf.get('trades'),
            hp.get('unique_wallets_5m'), hp.get('repeat_buy_wallets_5m'), num(ctx.get('conviction')), ctx.get('mode'),
            1 if g(r,'safety','allowed') else 0, '|'.join(str(x) for x in (r.get('rejection_reasons') or []))[:300])

def work(args):
    idx, start, end = args
    part = os.path.join(OUT, 'part_%02d.sqlite3' % idx)
    if os.path.exists(part):
        os.remove(part)
    con = sqlite3.connect(part)
    con.execute('CREATE TABLE o (%s)' % ','.join(COLS))
    buf, n, bad = [], 0, 0
    q = 'INSERT INTO o VALUES (%s)' % ','.join('?' * len(COLS))
    with open(SRC, 'rb') as f:
        f.seek(start)
        if start:
            f.readline()
        while f.tell() <= end:
            line = f.readline()
            if not line:
                break
            try:
                r = json.loads(line)
            except Exception:
                bad += 1
                continue
            buf.append(row(r)); n += 1
            if len(buf) >= 20000:
                con.executemany(q, buf); buf.clear()
    if buf:
        con.executemany(q, buf)
    con.commit(); con.close()
    return idx, n, bad

if __name__ == '__main__':
    t0 = time.time()
    size = os.path.getsize(SRC)
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    chunk = size // workers
    jobs = [(i, i * chunk, size if i == workers - 1 else (i + 1) * chunk) for i in range(workers)]
    with Pool(workers) as p:
        res = p.map(work, jobs)
    total = sum(r[1] for r in res); bad = sum(r[2] for r in res)
    print('parsed', total, 'bad', bad, 'cutoff_bytes', size, 'secs', round(time.time() - t0))
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute('CREATE TABLE o (%s)' % ','.join(COLS))
    for i, _, _ in jobs:
        part = os.path.join(OUT, 'part_%02d.sqlite3' % i)
        con.execute("ATTACH ? AS p", (part,))
        con.execute('INSERT INTO o SELECT * FROM p.o')
        con.commit()
        con.execute('DETACH p')
        os.remove(part)
    con.execute('CREATE INDEX o_pair_t ON o(pair, t)')
    con.execute('CREATE INDEX o_t ON o(t)')
    con.commit()
    r = con.execute('SELECT count(*), count(DISTINCT pair), min(t), max(t) FROM o').fetchone()
    print('rows', r[0], 'pairs', r[1], 'hours', round((r[3] - r[2]) / 3.6e6, 2), 'min_t', r[2], 'max_t', r[3], 'secs', round(time.time() - t0))
    con.close()
