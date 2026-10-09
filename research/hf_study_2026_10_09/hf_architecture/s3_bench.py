"""S3: CPU / storage micro-benchmarks for the HF Lab architecture (read-only imports of repo modules).

Imports backend/entry_defense, heat_veto, structural_rug_guard and pool_loss_memory from the dev checkout
(sys.dont_write_bytecode; in-memory registry with no path, so nothing is written to the repo). All files
written by this script go to this study's scratch folder.
"""
import json
import os
import random
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r'C:/Users/Chavd/Documents/ChatGPT/memcoin/backend')
import entry_defense            # noqa: E402
import heat_veto                # noqa: E402
import pool_loss_memory         # noqa: E402
import structural_rug_guard as rug  # noqa: E402

OUT = os.path.join(HERE, 'out')
TMP = os.path.join(HERE, 'out', 'bench_tmp')
os.makedirs(TMP, exist_ok=True)
SOL = 'So11111111111111111111111111111111111111112'
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def addr(rnd):
    return ''.join(rnd.choice(B58) for _ in range(44))


def make_feed(rnd, n=90):
    coins = []
    for k in range(n):
        coins.append({'address': addr(rnd), 'pairAddress': addr(rnd), 'symbol': 'T%d' % k, 'dexId': 'pumpswap',
                      'quoteTokenAddress': SOL, 'quoteToken': {'address': SOL},
                      'priceUsd': 0.001 * (1 + rnd.random()), 'priceNative': 0.000005,
                      'liquidityUsd': 50_000 + rnd.random() * 500_000, 'marketCap': 3e6 + rnd.random() * 2e7,
                      'fdv': 3e6, 'ageMinutes': 2_000 + rnd.random() * 30_000,
                      'txns': {'m5': {'buys': rnd.randint(1, 40), 'sells': rnd.randint(1, 40)}},
                      'volume': {'m5': rnd.random() * 2_000, 'h1': 10_000 + rnd.random() * 30_000},
                      'priceChange': {'m5': 0.5, 'h1': 1.0, 'h6': 5.0, 'h24': 10.0}, 'sources': []})
    return coins


def bench_defense():
    rnd = random.Random(7)
    clock = [1_791_500_000_000]
    layer = entry_defense.DefensiveEntryLayer(registry=rug.TickerRegistry(None, clock=lambda: clock[0]),
                                              history=heat_veto.PairHistory())
    feed = make_feed(rnd)
    # 65 simulated minutes of 2-s observations with a price refresh every ~30 s per pair
    t = clock[0]
    t_obs = time.perf_counter()
    steps = 0
    for step in range(65 * 30):
        t += 2_000
        clock[0] = t
        for c in feed:
            if rnd.random() < 2 / 30:
                c['priceUsd'] *= 1 + rnd.gauss(0, 0.004)
            c['updatedAt'] = t
        layer.observe(feed, t)
        steps += 1
    observe_ms = (time.perf_counter() - t_obs) * 1000 / steps
    st = layer.history.status()
    # evaluate: 90 candidates x 8 books (worst case: no per-refresh sharing)
    blocked = {}
    t0 = time.perf_counter()
    reps = 20
    for _ in range(reps):
        for c in feed:
            for _book in range(8):
                layer.evaluate(c, t, blocked_pools=blocked, heat_log_only=False)
    per_eval_us = (time.perf_counter() - t0) * 1e6 / (reps * len(feed) * 8)
    # shared once per refresh (90 evaluations) + per-book pool-rule lookups
    t0 = time.perf_counter()
    for _ in range(reps):
        for c in feed:
            layer.evaluate(c, t, blocked_pools=blocked, heat_log_only=False)
    shared_ms = (time.perf_counter() - t0) * 1000 / reps
    # pool_loss_memory.index over a 50-row capped history vs a 10k-row history
    hist_small = [{'address': addr(rnd), 'pairAddress': addr(rnd), 'closed_at': t - k * 60_000,
                   'pnl_usd': -0.5} for k in range(50)]
    hist_big = [{'address': hist_small[k % 50]['address'], 'pairAddress': hist_small[k % 50]['pairAddress'],
                 'closed_at': t - k * 10_000, 'pnl_usd': -0.5} for k in range(10_000)]
    t0 = time.perf_counter()
    for _ in range(50):
        pool_loss_memory.index(hist_small, t)
    plm_small_ms = (time.perf_counter() - t0) * 1000 / 50
    t0 = time.perf_counter()
    for _ in range(5):
        pool_loss_memory.index(hist_big, t)
    plm_big_ms = (time.perf_counter() - t0) * 1000 / 5
    return {'pair_history': {k: st[k] for k in ('pairs', 'samples')}, 'observe_ms_per_refresh_90_coins': round(observe_ms, 3),
            'evaluate_us_per_candidate': round(per_eval_us, 1),
            'evaluate_ms_per_refresh_90x8_unshared': round(per_eval_us * 90 * 8 / 1000, 2),
            'evaluate_ms_per_refresh_90_shared': round(shared_ms, 2),
            'pool_loss_memory_index_ms_50_rows': round(plm_small_ms, 3),
            'pool_loss_memory_index_ms_10k_rows': round(plm_big_ms, 2)}


def forward_like_close(rnd, k):
    """A close row shaped like today's forward-test close (entry_features, defensive_entry, price_crosscheck,
    research_fill shadow, calibration) to size the cost of keeping HF closes in book['history']."""
    feat = {'score': 88.0, 'liq': 123456.7, 'm5': 1.2, 'h1': 3.4, 'bs': 1.1, 'lmc': 0.05, 'age': 12345.0,
            'vol1h': 23456.0, 'vol_liq': 0.19,
            'flow': {'trades': 0, 'buys': 0, 'sells': 0, 'buy_usd': 0, 'sell_usd': 0, 'unique_wallets': 0, 'ratio': 0,
                     'max_sell': 0}}
    heat = {'history_span_s': 1234.5, 'return_5m_pct': 0.4, 'buy_share_5m': 0.55, 'volume_acceleration': 0.8,
            'change_6h_pct': 4.0, 'change_24h_pct': 10.0, 'fee_tier_bps': 50.0, 'paid_profile_last_60m': False,
            'fraction_of_15m_high': 0.98, 'turnover_5m': 0.01, 'pair_coverage_s': 3600.0,
            'process_coverage_s': 7200.0, 'warming_windows': []}
    structural = {'version': 'STRUCTURAL_RUG_GUARD_V1', 'blocked': False, 'reasons': [], 'liq_mcap': 0.05,
                  'age_min': 12345.0, 'mcap': 2.4e6, 'liquidity_usd': 123456.7, 'ticker': 'abc',
                  'ticker_reused_by': 0, 'registry_version': 'TICKER_REGISTRY_V2_COVERAGE', 'registry_coverage_h': 30.0}
    leg = {'status': 'next_refresh', 'decision_at': 1, 'decision_price': 0.001, 'first_later_at': 2,
           'fill_at': 2, 'fill_price': 0.00101, 'fill_lag_ms': 15000, 'later_observations': 3,
           'last_observed_at': 3, 'resolved_at': 4}
    return {'trade_no': k, 'strategy_id': 'HF_X', 'symbol': 'ABC', 'name': 'Abc Coin', 'address': addr(rnd),
            'pairAddress': addr(rnd), 'dexId': 'pumpswap', 'quoteTokenAddress': SOL, 'entry_price': 0.001,
            'execution_entry_price': 0.00101, 'current_price': 0.001, 'peak_price': 0.0011, 'quantity': 49_000.0,
            'original_quantity': 49_000.0, 'notional_usd': 50.0, 'opened_at': 1, 'updated_at': 2, 'score': 88,
            'entry_features': feat, 'partial_realized_pnl': 0.0, 'partial_exits': [],
            'remaining_cost_basis_usd': 50.02, 'entry_dex_fee_bps': 50.0, 'entry_dex_fee_usd': 0.25,
            'entry_network_fee_usd': 0.02, 'entry_network_cost_basis': 'IDENTIFIED_SOL_POOL_USD_NATIVE_RATIO',
            'entry_quote_token_address': SOL, 'entry_price_impact_pct': 0.08, 'entry_slippage_pct': 0.2,
            'execution_mode': 'DEX_SPOT_MODELED_COSTS_V3_VERIFIED_SOL_DENOMINATION',
            'execution_source': 'DEX_SPOT_WITH_MODELED_FRICTION', 'quote_status': 'fresh', 'mark_received_at': 2,
            'mark_source': 'SHARED_LIVE_FEED_EXACT_POOL', 'quote_age_ms': 0,
            'price_crosscheck': {'status': 'pass', 'reason': '', 'version': 'PRICE_CROSSCHECK_V4',
                                 'observed_price': 0.001, 'reference_price': 0.001, 'divergence_pct': 0.3,
                                 'reference_received_at': 1, 'source': 'GeckoTerminal exact pool',
                                 'pair': addr(rnd), 'mint': addr(rnd), 'provider_market_timestamp_available': False},
            'entry_policy_version': 'HF', 'entry_roundtrip_pnl_pct': -1.2, 'entry_cost_cap_pct': 1.5,
            'stop_loss_net_pct': 3.0, 'stop_headroom_pct': 1.8, 'entry_size_reduced': False,
            'defensive_entry': {'version': 'DEFENSIVE_ENTRY_LAYER_V1', 'allowed': True, 'reasons': [],
                                'log_only_flags': [], 'versions': dict(entry_defense.VERSIONS),
                                'structural_rug_guard': structural,
                                'pool_loss_memory': {'version': 'POOL_LOSS_MEMORY_V1', 'blocked': False, 'reasons': [],
                                                     'consecutive_losses': 0, 'last_loss_at': None,
                                                     'blocked_until': None, 'remaining_ms': 0},
                                'heat_veto': {'version': 'HEAT_VETO_STACK_V1', 'vetoed': False, 'log_only': False,
                                              'reasons': [], 'metrics': heat}},
            'pnl_pct': -2.1, 'open_pnl_usd': -1.05, 'execution_exit_price': 0.00099, 'remaining_fraction': 1.0,
            'exit_price': 0.00099, 'closed_at': 3, 'exit_reason': 'HF_MAX_HOLD_3', 'final_leg_pnl_usd': -1.05,
            'pnl_usd': -1.05, 'balance_after': 950.0, 'exit_dex_fee_usd': 0.25, 'exit_network_fee_usd': 0.02,
            'exit_price_impact_pct': 0.08, 'exit_slippage_pct': 0.2,
            'research_fill': {'version': 'X', 'entry_decision': 'entry_observation', 'entry': dict(leg),
                              'exit': dict(leg), 'result': {'pnl_usd': -1.1, 'net50_usd': -1.6}},
            'cost_calibration': {'version': 'CALIB_V1_2026-10-08', 'fee_bucket': 'fee<=50', 'fee_bucket_bps': 22.0,
                                 'unmeasured_margin_bps': 0.0, 'engine_fixed_bps': 22.0, 'in_measured_range': True,
                                 'total_bps': 44.0},
            'net50_usd': -1.6, 'net50_pct': -3.2}


def hf_close_row(rnd, k):
    """The proposed slim HF_CLOSE_V1 JSONL row (everything an edge report, the kill rule and the dashboard need)."""
    return {'v': 'HF_CLOSE_V1', 'id': 'HF_RND_EST:%d' % k, 'book': 'HF_RND_EST', 'cfg': 'a1b2c3d4e5f6', 'seq': k,
            'mint': addr(rnd), 'pair': addr(rnd), 'sym': 'ABC', 'slot': 3, 'notional_usd': 50.0,
            'decision_at': 1, 'entry_fill_at': 2, 'entry_fill_status': 'next_refresh', 'entry_decision_price': 0.001,
            'entry_fill_price': 0.00101, 'exit_trigger': 'HF_MAX_HOLD_3', 'exit_decision_at': 3,
            'exit_fill_at': 4, 'exit_fill_status': 'quiet', 'exit_decision_price': 0.00099,
            'exit_fill_price': 0.00099, 'fee_bps': 50.0, 'entry_liq_usd': 123456.7, 'exit_liq_usd': 123000.0,
            'calib_bps_leg': 44.0, 'pnl_usd': -1.05, 'pnl_pct': -2.1, 'net0_usd': -0.6, 'net50_usd': -1.6,
            'net50_pct': -3.2, 'balance_after': 950.0, 'day': '2026-10-09', 'heat_flags': [],
            'pool_rule': {'v': 'HF_POOL_COOLDOWN_V1', 'plm_v1_would_block': False}, 'close_kind': 'marked'}


def bench_storage():
    rnd = random.Random(11)
    out = {}
    fwd = forward_like_close(rnd, 1)
    slim = hf_close_row(rnd, 1)
    out['forward_like_close_bytes'] = len(json.dumps(fwd, ensure_ascii=False))
    out['hf_close_row_bytes'] = len(json.dumps(slim, ensure_ascii=False)) + 1
    # whole-ledger cost if one day of 8 HF books x 50 closes/h were kept in book['history']
    for closes in (1_200, 9_600):
        rows = [forward_like_close(rnd, k) for k in range(closes)]
        ledger = {'books': {'HF_%d' % b: {'history': rows[b::8]} for b in range(8)}}
        path = os.path.join(TMP, 'ledger.json')
        t0 = time.perf_counter()
        text = json.dumps(ledger, ensure_ascii=False, allow_nan=False)
        t1 = time.perf_counter()
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        t2 = time.perf_counter()
        out['whole_ledger_%d_closes' % closes] = {'mb': round(len(text) / 1e6, 1), 'dumps_s': round(t1 - t0, 3),
                                                   'write_fsync_s': round(t2 - t1, 3),
                                                   'share_of_2s_poll': round((t2 - t0) / 2, 3)}
        del rows, ledger, text
    # proposed HF state file: 8 books x (8 slots + 50 recent closes + aggregates with 40 pools x 24 h buckets)
    slot = {**forward_like_close(rnd, 0), 'state': 'open', 'pending': None}
    agg = {'n': 1000, 'wins': 10, 'sum_pnl': -1000.0, 'sum_net50': -1600.0, 'sum_net0': -600.0, 'sumsq_net50': 2600.0,
           'by_pair': {addr(rnd)[:44]: [40, -64.0, -40.0] for _ in range(40)},
           'by_day': {'2026-10-%02d' % d: {'n': 120, 'pnl': -120.0, 'net50': -190.0, 'cap_tripped_at': 1} for d in range(1, 31)},
           'by_hour': [[50, -50.0] for _ in range(24)], 'exits': {'HF_MAX_HOLD_3': 950, 'STOP': 40, 'TP': 10},
           'equity_peak': 1000.0, 'max_dd_pct': 30.0, 'recent_close_at': list(range(60))}
    state = {'books': {'HF_%d' % b: {'slots': [dict(slot) for _ in range(8)],
                                     'recent': [hf_close_row(rnd, k) for k in range(50)],
                                     'pools': {addr(rnd): {'cool_until': 1, 'streak': 2, 'block_until': 0}
                                               for _ in range(40)},
                                     'agg': agg} for b in range(8)}}
    t0 = time.perf_counter()
    text = json.dumps(state, ensure_ascii=False)
    t1 = time.perf_counter()
    path = os.path.join(TMP, 'hf_state.json')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    t2 = time.perf_counter()
    out['hf_state_file'] = {'kb': round(len(text) / 1e3, 1), 'dumps_ms': round((t1 - t0) * 1000, 2),
                            'write_fsync_ms': round((t2 - t1) * 1000, 2)}
    # JSONL append of one loop's closes (2 closes) with one fsync
    path = os.path.join(TMP, 'closes.jsonl')
    t0 = time.perf_counter()
    reps = 200
    for k in range(reps):
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(hf_close_row(rnd, 2 * k), ensure_ascii=False) + '\n')
            fh.write(json.dumps(hf_close_row(rnd, 2 * k + 1), ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
    out['jsonl_append_2_rows_fsync_ms'] = round((time.perf_counter() - t0) * 1000 / reps, 2)
    # replay of one day's JSONL (1,200 rows/book) at restart
    with open(path, 'w', encoding='utf-8') as fh:
        for k in range(1_200):
            fh.write(json.dumps(hf_close_row(rnd, k), ensure_ascii=False) + '\n')
    t0 = time.perf_counter()
    with open(path, encoding='utf-8') as fh:
        rows = [json.loads(line) for line in fh]
    out['jsonl_replay_1200_rows_ms'] = round((time.perf_counter() - t0) * 1000, 1)
    out['jsonl_mb_per_book_day_at_50ph'] = round(out['hf_close_row_bytes'] * 1_200 / 1e6, 2)
    return out


def main():
    res = {'defense': bench_defense(), 'storage': bench_storage()}
    print(json.dumps(res, indent=1))
    with open(os.path.join(OUT, 's3_bench.json'), 'w') as fh:
        json.dump(res, fh, indent=1)
    for name in os.listdir(TMP):
        os.remove(os.path.join(TMP, name))


if __name__ == '__main__':
    main()
