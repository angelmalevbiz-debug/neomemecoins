"""p01: per-point eligibility flags for the HF cost-floor study (PAPER research only).

For every PumpSwap SOL point with liquidity >= $50k, stores (pair id, index, t, fee bps, liq, flag bits):
  G1    1   H.rug_guard_v1 blocks (LP_PULLABLE / YOUNG_POOL / FAKE_MCAP 1 %, fail closed)
  GE    2   engine STRUCTURAL_RUG_GUARD_V1 extras block: fake-cap at 2 %, ticker reuse by a DIFFERENT mint (<14 d),
            missing / placeholder ticker (<14 d)
  heat (HEAT_VETO_STACK_V1, frozen engine thresholds, research history):
  HW    4   history warming (no 5-min reference in the contiguous segment, crash window < 15 min observed,
            paid-profile lookback < 60 min of process coverage at fee >= 100)
  HU    8   price unknown
  HR   16   5-min return >= +3 %          HB   32  5-min buy share >= 0.70
  HV   64   v5/(v1h/12) >= 1.3            HX  128  pc6h >= 200 or pc24 >= 150
  HA  256   fee >= 100 and paid 'latest' flag within 60 min
  HC  512   crash: price <= 75 % of 15-min high or 5-min return <= -20 %
  HT 1024   turnover v5/liq >= 0.095
  CHG 2048  price differs from the previous point (a DexScreener refresh)
Writes elig.pkl next to this script. Read-only on all inputs.
"""
import array, collections, math, os, pickle, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfc_common as C

H = C.harness()
T0 = time.time()
series, meta = H.load()
print('loaded', meta, round(time.time() - T0, 1), 's', flush=True)
CUT = H.split_t(meta, 0.6)

G1, GE, HW, HU, HR, HB, HV, HX, HA, HC, HT, CHG = 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048
HEAT = HW | HU | HR | HB | HV | HX | HA | HC | HT
D14 = 14 * 1440.0

# ---- ticker registry (engine rule: reuse = another pool with a DIFFERENT mint seen at or before now)
byticker = collections.defaultdict(list)
for pair, s in series.items():
    tk = H.norm_ticker(s['sym'])
    if tk in ('', 'token'):
        continue
    byticker[tk].append((s['t'][0], s['mint'], pair))
reuse_from = {}
for pair, s in series.items():
    tk = H.norm_ticker(s['sym'])
    if tk in ('', 'token'):
        reuse_from[pair] = -1.0          # missing ticker: blocked under 14 d
        continue
    ts_other = [t0 for t0, mint, p in byticker[tk] if p != pair and mint != s['mint']]
    reuse_from[pair] = min(ts_other) if ts_other else float('inf')

pairs = []
P_ID, P_I, P_T, P_FEE, P_LIQ, P_BITS = (array.array('i'), array.array('i'), array.array('d'), array.array('f'),
                                        array.array('f'), array.array('i'))
nps = 0
chk = chk_bad = 0
for pair, s in series.items():
    if s['dex'] != 'pumpswap' or s['quote_sol'] != 1:
        continue
    ts, px, lq, mc, age = s['t'], s['price'], s['liq'], s['mcap'], s['age']
    v5, v1h, b5, s5, pc6, pc24, src = s['v5'], s['v1h'], s['b5'], s['s5'], s['pc6h'], s['pc24'], s['src']
    n = len(ts)
    nps += n
    pid = len(pairs)
    pairs.append(pair)
    rf = reuse_from[pair]
    seg = pres = 0
    ref = -1
    dq = collections.deque()
    last_paid = -1e18
    for i in range(n):
        t = ts[i]
        if i:
            g = t - ts[i - 1]
            if g > 120_000:
                seg = i
            if g > 3_660_000:
                pres = i
        p = px[i]
        sv = src[i]
        if sv == sv and int(sv) & 4:
            last_paid = t
        if p > 0:
            while dq and px[dq[-1]] <= p:
                dq.pop()
            dq.append(i)
        while dq and ts[dq[0]] <= t - 900_000:
            dq.popleft()
        tgt = t - 300_000
        while ref + 1 <= i and ts[ref + 1] <= tgt:
            ref += 1
        liq = lq[i]
        if not (liq >= 50_000):
            continue
        bits = 0
        if i and px[i] != px[i - 1]:
            bits |= CHG
        # ---- structural guard
        m, a = mc[i], age[i]
        ok = liq == liq and m == m and a == a and liq > 0 and m > 0 and a >= 0
        if not ok:
            bits |= G1 | GE
        else:
            lmc = liq / m
            if lmc >= 1.0 or a < 720.0 or (m >= 20e6 and lmc < 0.01 and a < D14):
                bits |= G1
            if a < D14 and ((m >= 20e6 and lmc < 0.02) or t >= rf):
                bits |= GE
        fee = H.fee_bps(s, i)
        # ---- heat veto
        ret5 = float('nan')
        if not (p > 0):
            bits |= HU
        else:
            if t - ts[seg] >= 300_000 and ref >= seg and px[ref] > 0:
                ret5 = (p / px[ref] - 1) * 100
            else:
                bits |= HW
            if ret5 >= 3.0:
                bits |= HR
            hi = px[dq[0]] if dq else p
            hi = max(hi, p)
            if p <= 0.75 * hi or ret5 <= -20.0:
                bits |= HC
            elif t - ts[pres] < 900_000:
                bits |= HW
        bb, ss = b5[i], s5[i]
        if bb == bb and ss == ss and bb >= 0 and ss >= 0 and bb + ss >= 1 and bb / (bb + ss) >= 0.70:
            bits |= HB
        vv, vh = v5[i], v1h[i]
        if vv == vv and vh == vh and vv >= 0 and vh > 0 and vv / (vh / 12) >= 1.3:
            bits |= HV
        if pc6[i] >= 200 or pc24[i] >= 150:
            bits |= HX
        if fee >= 100:
            if t - last_paid <= 3_600_000:
                bits |= HA
            elif t - meta['t0'] < 3_600_000:
                bits |= HW
        if vv == vv and vv >= 0 and vv / liq >= 0.095:
            bits |= HT
        # sanity: inline G1 == H.rug_guard_v1 on a sample
        if (i % 97) == 0:
            chk += 1
            if bool(bits & G1) != H.rug_guard_v1(H.Past(s, i)):
                chk_bad += 1
        P_ID.append(pid); P_I.append(i); P_T.append(t); P_FEE.append(fee); P_LIQ.append(liq); P_BITS.append(bits)

print('pumpswap SOL points', nps, 'stored liq>=50k', len(P_T), 'G1 parity checks', chk, 'mismatches', chk_bad,
      'secs', round(time.time() - T0, 1), flush=True)
out = {'pairs': pairs, 'pid': P_ID, 'i': P_I, 't': P_T, 'fee': P_FEE, 'liq': P_LIQ, 'bits': P_BITS,
       'cut': CUT, 't0': meta['t0'], 't1': meta['t1'],
       'bitnames': {'G1': G1, 'GE': GE, 'HW': HW, 'HU': HU, 'HR': HR, 'HB': HB, 'HV': HV, 'HX': HX, 'HA': HA,
                    'HC': HC, 'HT': HT, 'CHG': CHG, 'HEAT': HEAT}}
with open(os.path.join(C.HERE, 'elig.pkl'), 'wb') as fh:
    pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)

# ---- descriptive stats per universe
UNIV = {'a_fee50_liq250k': lambda f, l: f <= 50 and l >= 250_000,
        'b_fee95_liq100k': lambda f, l: f <= 95 and l >= 100_000,
        'c_all_liq50k': lambda f, l: l >= 50_000}
N = len(P_T)
for uname, uf in UNIV.items():
    for label, mask in (('nofilter', 0), ('guardV1', G1), ('guardEng', G1 | GE), ('guardEng+heat', G1 | GE | HEAT)):
        for part in ('train', 'holdout'):
            mins = collections.defaultdict(set)
            npts = 0
            pset = set()
            chg = 0
            for k in range(N):
                t = P_T[k]
                if (t < CUT) != (part == 'train'):
                    continue
                if not uf(P_FEE[k], P_LIQ[k]):
                    continue
                if P_BITS[k] & mask:
                    continue
                npts += 1
                pset.add(P_ID[k])
                mins[int(t // 60_000)].add(P_ID[k])
                if P_BITS[k] & CHG:
                    chg += 1
            span_min = ((CUT - meta['t0']) if part == 'train' else (meta['t1'] - CUT)) / 60_000
            conc = [len(v) for v in mins.values()]
            mean_conc = sum(conc) / span_min if span_min else 0
            conc_sorted = sorted(conc + [0] * max(0, int(span_min) - len(conc)))
            med = conc_sorted[len(conc_sorted) // 2] if conc_sorted else 0
            p10 = conc_sorted[len(conc_sorted) // 10] if conc_sorted else 0
            print('%-17s %-14s %-7s points %7d pairs %4d  pools/min mean %.2f p10 %d median %d max %d  refresh share %.3f'
                  % (uname, label, part, npts, len(pset), mean_conc, p10, med, max(conc) if conc else 0,
                     chg / npts if npts else 0), flush=True)

# heat reason shares in each guarded universe (train)
for uname, uf in UNIV.items():
    cnt = collections.Counter()
    tot = 0
    for k in range(N):
        if P_T[k] >= CUT or not uf(P_FEE[k], P_LIQ[k]) or P_BITS[k] & (G1 | GE):
            continue
        tot += 1
        b = P_BITS[k]
        if b & HEAT:
            cnt['any'] += 1
        for nm, bit in (('HW', HW), ('HU', HU), ('HR', HR), ('HB', HB), ('HV', HV), ('HX', HX), ('HA', HA),
                        ('HC', HC), ('HT', HT)):
            if b & bit:
                cnt[nm] += 1
    print('heat shares train', uname, 'guarded points', tot, {k: round(v / tot, 3) for k, v in sorted(cnt.items())} if tot else {})
print('done secs', round(time.time() - T0, 1))
