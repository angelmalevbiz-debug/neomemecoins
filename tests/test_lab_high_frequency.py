"""LAB_HIGH_FREQUENCY_V1: three PAPER Lab books trading about 50 times an hour each.

PAPER only and offline. Every ledger, journal and checkpoint lives in a temporary
directory; the defensive layer, the exact-pair marks and the price audit are fakes
unless a test says otherwise; nothing calls an external API. The parity fixture
(tests/fixtures/lab_hf_obs_20261008.json) holds real scan-log observations of six
E95 pools around a 30-minute holdout window and the decisions the research
simulator (research/hf_study_2026_10_09/hf_synthesis/syn_lib.py) made on them.
Nothing here measures or claims profitability: the books are expected to lose.
"""
import copy
import dataclasses
import json
import os
from pathlib import Path
import random
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

_TEMP = tempfile.TemporaryDirectory(prefix='neo-lab-hf-tests-')
for _name, _file in (('NEO_MARKET_STATE_PATH', 'state.json'), ('NEO_MARKET_AUDIT_PATH', 'audit.jsonl'),
                     ('NEO_LIVE_TAPE_PATH', 'tape.json'), ('NEO_STRATEGY_LAB_PATH', 'strategy_lab.json'),
                     ('NEO_STRATEGY_LAB_COMPACT_PATH', 'strategy_lab_compact.json'),
                     ('NEO_STRATEGY_LAB_RESET_FLAG', 'strategy_lab.reset'), ('NEO_RISK_CACHE_DIR', 'risk'),
                     ('NEO_PRICE_CHECK_DIR', 'price-check'), ('NEO_TRAINING_ROOT', 'training')):
    os.environ[_name] = str(Path(_TEMP.name) / _file)
os.environ.pop('NEO_MAIN_MARKET_STATE_PATH', None)
for _name in ('NEO_LAB_HF_ENABLED', 'NEO_LAB_HF_RETIRE', 'NEO_LAB_HF_DAILY_CAP_USD', 'NEO_LAB_HF_START_BALANCE_USD'):
    os.environ.pop(_name, None)

import entry_defense
import lab_activity as activity
import lab_forward_tests as lf
import lab_high_frequency as hf
import lab_strategy_lifecycle as lifecycle
import paper_market_feasibility as feasibility
import strategy_lab as lab
import structural_rug_guard as guard

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'lab_hf_obs_20261008.json')
                     .read_text(encoding='utf-8'))
_MODULE_PATCHES = []


def setUpModule():
    for name, file in (('STATE_PATH', 'strategy_lab.json'), ('COMPACT_PATH', 'strategy_lab_compact.json'),
                       ('RESET_FLAG_PATH', 'strategy_lab.reset'), ('HF_ROOT', 'strategy_lab_hf')):
        pinned = patch.object(lab, name, Path(_TEMP.name) / file)
        pinned.start()
        _MODULE_PATCHES.append(pinned)


def tearDownModule():
    while _MODULE_PATCHES:
        _MODULE_PATCHES.pop().stop()


# Frozen strategy config hashes and the default budget hash. A strategy parameter change must
# change these pins, strategy-lock.json (lab_high_frequency) and the table in
# docs/LAB_HIGH_FREQUENCY.md together; tests check all three.
PINNED_CONFIG_HASHES = {
    'HF_RND_E95': '8b830cca4b0e396a8fb9e7472bdd262f73d0a7a5b3568ede8a59f4719cdad492',
    'HF_QUIET_E95': 'c4298e9a8174361585f8b64f0d4e05b41ac34e9a9314e5b64b646d74e557907c',
    'HF_DIP15_E95': '11b3934b3d4cc6ab4b358e9c1fd0e8866c4ee1b63a6efc92f0766845bd34fbf6',
}
PINNED_BUDGET_HASH = 'd6bf441229424a474e97d18ab2afdc17bca385868d0454a2cea4676bbfd639fa'

SOL = feasibility.SOL_QUOTE_MINT
SOL_USD = 150.0
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR
# 2026-10-10 01:00 UTC: UTC day 20736, whose HF session opens at 00:00 ((7 x 20736) mod 24 = 0).
NOW0 = 20736 * DAY + HOUR
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
PASS_STRUCTURAL = {'blocked': False, 'reasons': []}


def ident(prefix, n):
    return (prefix + B58[n % 58] + B58[(n // 58) % 58]).ljust(44, '1')


def pool_key(n):
    return ident('Mint', n), ident('Pair', n)


def make_coin(n, price, stamp, *, liq=200_000.0, mcap=20_000_000.0, v5=10_000.0, symbol=None, **changes):
    """A PumpSwap/SOL pool observation (fee tier 30 bps at the default market cap, SOL at $150)."""
    mint, pair = pool_key(n)
    coin = {'address': mint, 'pairAddress': pair, 'dexId': 'pumpswap', 'quoteTokenAddress': SOL,
            'symbol': symbol or f'HF{n}', 'name': symbol or f'HF{n}', 'priceUsd': price,
            'priceNative': price / SOL_USD, 'liquidityUsd': liq, 'marketCap': mcap, 'volume': {'m5': v5},
            'updatedAt': int(stamp), 'pairCreatedAt': int(stamp - 30 * DAY)}
    coin.update(changes)
    return coin


class FakeDefense:
    """DefensiveEntryLayer stand-in: allows every pool unless told otherwise; counts calls."""

    def __init__(self):
        self.blocked = {}
        self.calls = []
        self.observed = 0
        self.registry = None

    def observe(self, feed, now):
        self.observed += 1

    def evaluate(self, coin, now, *, blocked_pools=None, closed_history=None, heat_log_only=False):
        self.calls.append({'pair': coin['pairAddress'], 'blocked_pools': blocked_pools,
                           'heat_log_only': heat_log_only, 'stamp': coin.get('updatedAt')})
        reasons = self.blocked.get(coin['pairAddress'])
        return {'allowed': not reasons, 'reasons': list(reasons or [])}


def covered_registry(now=NOW0, hours=48):
    """An empty ticker registry that has watched the market without a gap up to ``now`` + 2 h."""
    registry = guard.TickerRegistry()
    stamp = now - hours * HOUR
    while stamp <= now + 2 * HOUR:
        registry.mark_observed(stamp)
        stamp += 30 * MINUTE
    return registry


class FakeMarks:
    def __init__(self):
        self.coins = {}
        self.calls = 0

    def resolve(self, position, prices, now):
        self.calls += 1
        coin = self.coins.get(position['pairAddress'])
        return dict(coin) if coin else None


class FakeAudit:
    def __init__(self, reference=None, status='blocked'):
        self.reference = reference
        self.status = status
        self.checks = []

    def cached(self, coin):
        return dict(self.reference) if self.reference else None

    def check(self, coin):
        self.checks.append(coin['pairAddress'])
        return {'status': self.status, 'reason': 'price_source_disagreement', 'divergence_pct': 12.0}


class BlockingHttp:
    """requests.Session stand-in for a PositionMarkFeed: records each pair asked for, blocks until released.

    No network: the response is an empty pair list (no mark), returned once ``release`` is set.
    """

    def __init__(self):
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.pairs = []

    def get(self, url, timeout=None):
        with self.lock:
            self.pairs.append(url.rsplit('/', 1)[-1])
        self.release.wait(10)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'pairs': []})

    def asked(self):
        with self.lock:
            return list(self.pairs)


def edge_report_module():
    """scripts/paper_edge_report.py (loaded as the report tests load it)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('paper_edge_report_hf', ROOT / 'scripts' / 'paper_edge_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Harness:
    """One HighFrequencyLab on a temporary root with a controllable clock."""

    def __init__(self, test, *, budget=None, environ=None, defense=None, marks=None, audit=None, structural=None,
                 cost=None, root=None, now=NOW0, load=True):
        if root is None:
            folder = tempfile.TemporaryDirectory(prefix='neo-hf-')
            test.addCleanup(folder.cleanup)
            root = Path(folder.name) / 'strategy_lab_hf'
        self.test, self.root, self.now = test, root, now
        self.defense = defense if defense is not None else FakeDefense()
        self.marks = marks if marks is not None else FakeMarks()
        self.audit = audit
        self.structural = structural or (lambda coin, now: dict(PASS_STRUCTURAL))
        self.kwargs = dict(cost=cost or hf.cost_functions(lab), budget=budget or hf.DEFAULT_BUDGET,
                           environ=environ if environ is not None else {})
        self.hf = self.build()
        if load:
            self.hf.load()

    def build(self):
        return hf.HighFrequencyLab(self.root, defense=self.defense, marks=self.marks, price_audit=self.audit,
                                   clock=lambda: self.now, structural_check=lambda c, n: self.structural(c, n),
                                   **self.kwargs)

    def restart(self, *, advance=0):
        """A new process on the same root (the old one is simply abandoned, as after a crash)."""
        self.now += advance
        self.hf = self.build()
        return self.hf.load()

    def coin(self, n, price, **changes):
        stamp = changes.pop('stamp', self.now - 500)
        return make_coin(n, price, stamp, **changes)

    def step(self, *specs, refresh=True, alive=True, advance=2 * SECOND):
        self.now += advance
        feed = []
        for spec in specs:
            if isinstance(spec, dict):
                feed.append(spec)
            else:
                n, price, *rest = spec
                feed.append(self.coin(n, price, **(rest[0] if rest else {})))
        self.hf.update(lab.hf_feed_prices(feed), self.now, alive)
        if refresh:
            self.hf.on_refresh(feed, self.now)
        # The Lab's end of loop (strategy_lab.hf_end_loop): the one journal flush of the loop.
        self.hf.flush_journal()
        return feed

    def rows(self, kind=None, book=None, **match):
        self.hf.journal.flush()
        out = []
        for row in sorted(self.hf.journal.rows(), key=lambda item: item['seq']):
            if kind and row['kind'] != kind:
                continue
            if book and row['book'] != book:
                continue
            if any(row.get(key) != value for key, value in match.items()):
                continue
            out.append(row)
        return out

    def slots(self, book=hf.RND_ID):
        return list(self.hf.books[book]['slots'].values())


def draws(*numbers):
    """RND draws only for the given pools (lab_forward_tests.hashed_coin patched)."""
    pairs = {pool_key(n)[1] for n in numbers}
    return patch.object(lf, 'hashed_coin', side_effect=lambda pair, t, p, salt: pair in pairs)


def open_position(h, n, price=1.0):
    """First observation, a refresh event (RND order) and the next refresh (entry fill)."""
    h.step((n, price))
    h.step((n, price * 1.01))
    h.step((n, price * 1.02))
    return h.slots()[-1] if h.slots() else None


def walk_to_trigger(h, n, price, book=hf.RND_ID):
    slot = next(s for s in h.slots(book) if s['pairAddress'] == pool_key(n)[1])
    target = slot['decision_at'] + hf.EXIT.hold_ms
    while slot['state'] == hf.OPEN:
        h.step((n, price))
        if h.now - 500 > target + 10 * SECOND:
            break
    return slot


def synthetic_close(h, book, pair_n, net50_pct, at, *, fee_bps=30.0, trade_no=None):
    """A close row through the container's own row path (journal + state), for statistics tests."""
    mint, pair = pool_key(pair_n)
    pnl = 25.0 * net50_pct / 100 + 0.25
    row = h.hf._emit(book, 'close', {
        'trade_no': trade_no or 10_000 + h.hf.seq, 'address': mint, 'pairAddress': pair, 'symbol': f'HF{pair_n}',
        'fee_bps': fee_bps, 'decision_at': at - 150 * SECOND, 'exit_fill_at': at - SECOND, 'close_kind': hf.CLOSE_TIME,
        'pnl_usd': round(pnl, 6), 'pnl_pct': round(pnl / 25 * 100, 6), 'net0_usd': round(pnl + 0.2, 6),
        'net0_pct': round((pnl + 0.2) / 25 * 100, 6), 'net50_usd': round(25 * net50_pct / 100, 6),
        'net50_pct': net50_pct, 'closed_at': at}, at)
    return row


# ------------------------------------------------------------------ definitions

class DefinitionTests(unittest.TestCase):
    def test_pinned_hashes_equal_the_docs_and_the_lock(self):
        self.assertEqual(hf.CONFIG_HASHES, PINNED_CONFIG_HASHES)
        self.assertEqual(hf.DEFAULT_BUDGET_HASH, PINNED_BUDGET_HASH)
        lock = json.loads((ROOT / 'strategy-lock.json').read_text(encoding='utf-8'))
        section = lock['lab_high_frequency']
        self.assertEqual(section['config_hashes'], PINNED_CONFIG_HASHES)
        self.assertEqual(section['budget_hash'], PINNED_BUDGET_HASH)
        self.assertEqual(section['version'], hf.VERSION)
        self.assertEqual(section['book_ids'], list(hf.BOOK_IDS))
        self.assertEqual(section['controls'], hf.CONTROL_OF)
        self.assertEqual(section['portfolio_group'], 'HF_EXPERIMENT')
        self.assertEqual((section['start_balance_usd'], section['notional_usd'], section['size_rule'],
                          section['max_open_positions_per_book']), (1000.0, 25.0, 'FIXED_NOTIONAL_NO_BACKOFF', 3))
        self.assertEqual(section['research_prereg_sha256'], hf.RESEARCH_PREREG_SHA256)
        self.assertEqual(section['defensive']['pool_loss_memory'], 'SHADOW_REPLACED_BY_HF_POOL_RULE_V1')
        self.assertFalse(section['automatic_promotion'])
        self.assertFalse(section['profitability_proven'])
        self.assertEqual(section['expected_result'], 'loss at the cost floor')
        self.assertEqual(section['versions'], hf.VERSIONS)
        for book_id, row in section['books'].items():
            self.assertEqual(row['config_hash'], PINNED_CONFIG_HASHES[book_id])
        for path in ('backend/lab_high_frequency.py', 'docs/LAB_HIGH_FREQUENCY.md'):
            self.assertIn(path, lock['support_files_sha256'])
        self.assertIn('LAB_HIGH_FREQUENCY_V1 books (structural + heat enforced; loss memory shadow)',
                      lock['defensive_entry']['applies_to'])
        doc = (ROOT / 'docs' / 'LAB_HIGH_FREQUENCY.md').read_text(encoding='utf-8')
        for book_id, digest in PINNED_CONFIG_HASHES.items():
            self.assertIn(f'| `{book_id}` | `{digest}` |', doc)
        self.assertIn(f'`{PINNED_BUDGET_HASH}`', doc)

    def test_the_lock_mirrors_the_frozen_parameters_and_the_process_rules(self):
        section = json.loads((ROOT / 'strategy-lock.json').read_text(encoding='utf-8'))['lab_high_frequency']
        plain = lambda value: json.loads(json.dumps(dataclasses.asdict(value)))
        for key, value in (('universe', hf.UNIVERSE), ('refresh_event', hf.REFRESH_EVENT),
                           ('book_rules', hf.BOOK_RULES), ('pool_rule', hf.POOL_RULE), ('exit', hf.EXIT),
                           ('fills', hf.FILL), ('print_guard', hf.PRINT_GUARD), ('accounting', hf.ACCOUNTING),
                           ('budget', hf.DEFAULT_BUDGET)):
            with self.subTest(key=key):
                self.assertEqual(section[key], plain(value))
        kill = plain(hf.KILL_RULE)
        self.assertEqual({name: section['kill'][name] for name in kill}, kill)
        self.assertEqual({name: section['defensive'][name] for name in hf.DEFENSIVE_MODES}, hf.DEFENSIVE_MODES)
        budget = hf.config_view()['time_budget']
        process = section['process']['time_budget']
        self.assertEqual((process['version'], process['max_ms_per_loop'], process['consecutive_loops'],
                          process['clear_after_loops'], process['min_degraded_ms']),
                         (budget['version'], budget['max_ms_per_loop'], budget['consecutive_loops'],
                          budget['clear_after_loops'], budget['min_degraded_ms']))
        self.assertIn('restart_gap', section['fills_restart_outage'])
        self.assertIn('hf_end_loop', section['storage']['journal'])

    def test_the_hash_is_canonical_and_covers_the_strategy(self):
        for book_id in hf.BOOK_IDS:
            canonical = json.dumps(hf.book_parameters(book_id), sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=True)
            import hashlib
            self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), hf.config_hash(book_id))
        self.assertEqual(len(set(hf.CONFIG_HASHES.values())), 3)
        changed = dict(hf.SIGNALS)
        changed[hf.RND_ID] = dataclasses.replace(hf.SIGNALS[hf.RND_ID], salt='hfB')
        with patch.object(hf, 'SIGNALS', changed):
            self.assertNotEqual(hf.config_hash(hf.RND_ID), PINNED_CONFIG_HASHES[hf.RND_ID])
        for name, value in (('EXIT', dataclasses.replace(hf.EXIT, hold_ms=60_000)),
                            ('BOOK_RULES', dataclasses.replace(hf.BOOK_RULES, slots=4)),
                            ('POOL_RULE', dataclasses.replace(hf.POOL_RULE, cooldown_ms=300_000)),
                            ('PRINT_GUARD', dataclasses.replace(hf.PRINT_GUARD, gain_cap_ratio=1.2)),
                            ('KILL_RULE', dataclasses.replace(hf.KILL_RULE, first_checkpoint_closes=400))):
            with self.subTest(name=name), patch.object(hf, name, value):
                for book_id in hf.BOOK_IDS:
                    self.assertNotEqual(hf.config_hash(book_id), PINNED_CONFIG_HASHES[book_id])
        model = dataclasses.replace(lf.COST_MODEL, base_slippage_bps=12.0)
        with patch.object(lf, 'COST_MODEL', model):
            self.assertNotEqual(hf.config_hash(hf.QUIET_ID), PINNED_CONFIG_HASHES[hf.QUIET_ID])

    def test_budget_changes_leave_the_strategy_hash_unchanged(self):
        environ = {hf.ENV_DAILY_CAP: '50', hf.ENV_START_BALANCE: '2000'}
        budget = hf.budget_parameters(environ)
        self.assertEqual((budget.daily_cap_usd, budget.start_balance_usd), (50.0, 2000.0))
        self.assertNotEqual(hf.budget_hash(budget), PINNED_BUDGET_HASH)
        h = Harness(self, budget=budget)
        self.assertEqual(h.hf.cfg, PINNED_CONFIG_HASHES)
        self.assertEqual(h.hf.books[hf.RND_ID]['balance'], 2000.0)
        self.assertEqual(hf.budget_parameters({hf.ENV_DAILY_CAP: 'nan'}).daily_cap_usd, 100.0)
        self.assertEqual(hf.budget_env_errors({hf.ENV_DAILY_CAP: '-5'}), [hf.ENV_DAILY_CAP])
        # Session rotation and the floor are budget knobs too.
        self.assertNotEqual(hf.budget_hash(dataclasses.replace(hf.DEFAULT_BUDGET, session_step_hours=0)),
                            PINNED_BUDGET_HASH)
        self.assertNotEqual(hf.budget_hash(dataclasses.replace(hf.DEFAULT_BUDGET, capital_floor_fraction=0.4)),
                            PINNED_BUDGET_HASH)

    def test_never_promoted_and_never_profitable_by_definition(self):
        self.assertFalse(hf.AUTOMATIC_PROMOTION)
        h = Harness(self)
        config = h.hf.config()
        self.assertFalse(config['automatic_promotion'])
        self.assertFalse(config['profitability_proven'])
        self.assertFalse(config['is_entry_authorization'])
        for book_id in hf.BOOK_IDS:
            parameters = hf.book_parameters(book_id)
            self.assertFalse(parameters['automatic_promotion'])
            self.assertFalse(parameters['kill_rule']['lifecycle_12_close_heuristic'])
            self.assertEqual(parameters['defensive']['heat_veto'], 'HEAT_VETO_STACK_V1 enforced (warm-up included)')
        view = h.hf.dashboard_view(h.now)
        self.assertEqual(view['badge'], 'ЕКСПЕРИМЕНТ · ОЧАКВА СЕ ЗАГУБА')
        self.assertFalse(view['automatic_promotion'])

    def test_the_books_are_absent_from_the_lab_ledger_lifecycle_and_rules(self):
        registered = {strategy['id'] for strategy in lab.STRATEGIES}
        self.assertFalse(registered & set(hf.BOOK_IDS))
        self.assertFalse(set(activity.RULES) & set(hf.BOOK_IDS))
        self.assertFalse(set(lab.STRATEGY_START_BALANCES) & set(hf.BOOK_IDS))
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(lab, 'STATE_PATH', Path(tmp) / 'strategy_lab.json'), \
                patch.object(lab, 'RESET_FLAG_PATH', Path(tmp) / 'absent.reset'):
            state = lab.load_state()
        self.assertFalse(set(state['books']) & set(hf.BOOK_IDS))
        review = lifecycle.apply_lifecycle(
            state['books'], registered_ids=registered | set(hf.BOOK_IDS), promoted_ids=set(lab.PROMOTED_STRATEGIES),
            activity_version=activity.POLICY_VERSION, execution_version=lab.EXECUTION_MODEL_VERSION, now=NOW0)
        self.assertFalse(any(book_id in json.dumps(review) for book_id in hf.BOOK_IDS))
        self.assertFalse(any(book_id in json.dumps(state['strategy_lifecycle']) for book_id in hf.BOOK_IDS))

    def test_the_migration_drain_never_touches_the_hf_root(self):
        from lab_portfolio_migration import PROMOTED_STRATEGIES, begin_promotion_drain, promote_strategy_lab
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            books = {}
            for strategy in lab.STRATEGIES:
                start = 100.0 if strategy['id'] == 'SCALPER' else 500.0
                books[strategy['id']] = {'id': strategy['id'], 'name': strategy['name'], 'starting_balance': start,
                                         'balance': start, 'position': None, 'history': [], 'trade_seq': 0}
            for strategy_id in PROMOTED_STRATEGIES:
                pnls = [10.0] * 6 + [-2.0] * 6
                books[strategy_id]['history'] = [
                    {'trade_no': n + 1, 'address': 'MintEpisodeA', 'opened_at': 1_800_000_000_000 + n * 60_000,
                     'closed_at': 1_800_000_030_000 + n * 60_000, 'pnl_usd': pnl} for n, pnl in enumerate(pnls)]
                books[strategy_id]['balance'] += sum(pnls)
            (root / 'strategy_lab.json').write_text(json.dumps({'books': books}), encoding='utf-8')
            h = Harness(self, root=root / 'strategy_lab_hf')
            with draws(1):
                open_position(h, 1)
            h.hf.checkpoint_if_due(h.now, force=True)
            before = {path.relative_to(h.root).as_posix(): path.read_bytes()
                      for path in sorted(h.root.rglob('*')) if path.is_file()}
            begin_promotion_drain(root)
            promote_strategy_lab(root)
            after = {path.relative_to(h.root).as_posix(): path.read_bytes()
                     for path in sorted(h.root.rglob('*')) if path.is_file()}
            self.assertEqual(before, after)
            migrated = json.loads((root / 'strategy_lab.json').read_text(encoding='utf-8'))
            self.assertFalse(set(migrated['books']) & set(hf.BOOK_IDS))

    def test_versions_and_reasons_are_published(self):
        config = hf.config_view()
        self.assertEqual(config['versions']['journal'], 'HF_JOURNAL_V1')
        self.assertEqual(config['config_hashes'], hf.CONFIG_HASHES)
        self.assertEqual(config['names'][hf.RND_ID], 'HF контрола · случайни входове')
        self.assertEqual(config['names'][hf.QUIET_ID], 'HF тихи пулове')
        self.assertEqual(config['names'][hf.DIP15_ID], 'HF дъно 15 мин')
        self.assertEqual(hf.SIGNALS[hf.RND_ID].probability, 0.25)
        self.assertEqual(hf.SIGNALS[hf.RND_ID].salt, 'hfA')
        self.assertEqual(hf.UNIVERSE.max_modeled_roundtrip_pct, activity.MAX_ENTRY_COST_PCT)
        self.assertEqual(hf.FILL.max_fill_lag_ms, lf.FILL_BASIS.max_fill_lag_ms)
        self.assertEqual(hf.KILL_RULE.bootstrap_seed, 20261008)
        self.assertEqual(hf.KILL_RULE.bootstrap_resamples, 2000)


# ------------------------------------------------------------------ refresh events

class RefreshEventTests(unittest.TestCase):
    def test_a_restamp_with_the_same_price_is_not_an_event(self):
        memory = hf.PoolMemory()
        key = pool_key(1)
        self.assertFalse(memory.observe(key, NOW0, 1.0)['event'])
        self.assertFalse(memory.observe(key, NOW0 + 5 * SECOND, 1.0)['event'])
        self.assertTrue(memory.observe(key, NOW0 + 10 * SECOND, 1.01)['event'])
        # Not newer than the last observation: ignored, never an event.
        self.assertFalse(memory.observe(key, NOW0 + 10 * SECOND, 1.02)['new'])

    def test_a_gap_over_60_s_is_not_an_event(self):
        memory = hf.PoolMemory()
        key = pool_key(1)
        memory.observe(key, NOW0, 1.0)
        self.assertFalse(memory.observe(key, NOW0 + 60 * SECOND + 1, 1.01)['event'])
        self.assertTrue(memory.observe(key, NOW0 + 120 * SECOND + 1, 1.02)['event'])

    def test_each_event_is_decided_once(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            feed = h.step((1, 1.01))
            self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
            # The same observation again at the next refresh: not a new event.
            h.now += 2 * SECOND
            h.hf.on_refresh(feed, h.now)
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)

    def test_candidates_are_decided_in_stamp_then_pool_order(self):
        h = Harness(self)
        with draws(1, 2):
            h.step((1, 1.0, {'stamp': h.now}), (2, 1.0, {'stamp': h.now}))
            h.step((2, 1.01, {'stamp': h.now + 1}), (1, 1.01, {'stamp': h.now + 2 * SECOND}))
        orders = h.rows('order', hf.RND_ID, side='entry')
        self.assertEqual([row['pairAddress'] for row in orders], [pool_key(2)[1]])


# ------------------------------------------------------------------ signals

class SignalTests(unittest.TestCase):
    def test_rnd_is_the_research_hashed_coin_on_salt_hfA(self):
        h = Harness(self)
        candidates = []
        rng = random.Random(7)
        for n in range(200):
            stamp = NOW0 + rng.randrange(10 ** 7)
            coin = make_coin(n % 50, 1.0, stamp)
            candidates.append({'coin': coin, 'stamp': stamp, 'key': pool_key(n % 50), 'price': 1.0,
                               'previous_price': 0.99})
        matched = [h.hf._signal(hf.RND_ID, c) is None for c in candidates]
        research = [lf.hashed_coin(c['coin']['pairAddress'], c['stamp'], 0.25, 'hfA') for c in candidates]
        self.assertEqual(matched, research)
        self.assertTrue(10 < sum(matched) < 90)
        # The research formula, verbatim (harness_final.hashed_coin).
        import hashlib
        pair, stamp = 'HMzvsEEmExamplePool1111111111111111111111111', 1_791_486_001_719
        digest = hashlib.blake2b(('%s|%s|%d' % ('hfA', pair, stamp)).encode(), digest_size=8).digest()
        self.assertEqual(lf.hashed_coin(pair, stamp, 0.25, 'hfA'), int.from_bytes(digest, 'big') / 2 ** 64 < 0.25)

    def test_quiet_boundaries(self):
        h = Harness(self)

        def candidate(v5, price, previous, liq=100_000.0):
            coin = make_coin(1, price, NOW0, v5=v5, liq=liq)
            return {'coin': coin, 'stamp': NOW0, 'key': pool_key(1), 'price': price, 'previous_price': previous,
                    'liquidity': liq}
        self.assertIsNone(h.hf._signal(hf.QUIET_ID, candidate(200.0, 1.005, 1.0)))   # turnover exactly 0.002
        self.assertEqual(h.hf._signal(hf.QUIET_ID, candidate(201.0, 1.005, 1.0)), 'hf_quiet_turnover')
        self.assertIsNone(h.hf._signal(hf.QUIET_ID, candidate(100.0, 0.995, 1.0)))
        self.assertEqual(h.hf._signal(hf.QUIET_ID, candidate(100.0, 1.007, 1.0)), 'hf_quiet_step')
        self.assertEqual(h.hf._signal(hf.QUIET_ID, candidate(100.0, 0.993, 1.0)), 'hf_quiet_step')
        no_volume = candidate(100.0, 1.0, 1.0)
        no_volume['coin'].pop('volume')
        self.assertEqual(h.hf._signal(hf.QUIET_ID, no_volume), 'hf_quiet_input_unknown')

    def test_dip15_boundary_and_warming(self):
        h = Harness(self)
        key = pool_key(1)
        memory = h.hf.memory
        start = NOW0
        for index, price in enumerate([1.10, 1.05, 1.00, 1.02, 1.04]):
            memory.observe(key, start + index * 100 * SECOND, price)
        stamp = start + 600 * SECOND
        memory.observe(key, stamp, 1.03)

        def candidate(price, at):
            return {'coin': make_coin(1, price, at), 'stamp': at, 'key': key, 'price': price}
        # Only 10 minutes of history: warming.
        self.assertEqual(h.hf._signal(hf.DIP15_ID, candidate(1.0, stamp)), 'hf_dip15_history_warming')
        later = start + 900 * SECOND
        memory.observe(key, later, 1.0019)
        self.assertIsNone(h.hf._signal(hf.DIP15_ID, candidate(1.0019, later)))      # within 0.2% of the low 1.00
        memory.observe(key, later + SECOND, 1.0021)
        self.assertEqual(h.hf._signal(hf.DIP15_ID, candidate(1.0021, later + SECOND)), 'hf_dip15_not_at_low')
        # The research form, float for float: 1.002 / 1.0 - 1 is just above 0.002.
        self.assertGreater(1.002 / 1.0 - 1, 0.002)
        memory.observe(key, later + 2 * SECOND, 1.002)
        self.assertEqual(h.hf._signal(hf.DIP15_ID, candidate(1.002, later + 2 * SECOND)), 'hf_dip15_not_at_low')
        # The low at start + 200 s leaves the (t - 900 s, t] window: 1.0019 is the new low.
        memory.observe(key, start + 1101 * SECOND, 1.0025)
        low, covered = memory.window_low(key, start + 1101 * SECOND)
        self.assertTrue(covered)
        self.assertEqual(low, 1.0019)


# ------------------------------------------------------------------ book rules

class BookRuleTests(unittest.TestCase):
    def test_three_slots_one_per_pool_and_one_order_per_refresh(self):
        h = Harness(self)
        pools = list(range(1, 7))
        with draws(*pools):
            h.step(*[(n, 1.0) for n in pools])
            h.step(*[(n, 1.01) for n in pools])
            self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
            for index in range(6):
                h.step(*[(n, 1.01 + 0.001 * (index + 2)) for n in pools])
        orders = h.rows('order', hf.RND_ID, side='entry')
        self.assertEqual(len(orders), 3)
        self.assertEqual(len({row['pairAddress'] for row in orders}), 3)
        self.assertEqual(len(h.slots()), 3)
        self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_slots_full')

    def test_the_governor_allows_at_most_50_orders_per_trailing_hour(self):
        h = Harness(self)
        book = h.hf.books[hf.RND_ID]
        book['governor'] = [h.now - HOUR + 30 * SECOND + index * SECOND for index in range(50)]
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            self.assertEqual(h.rows('order', hf.RND_ID, side='entry'), [])
            self.assertEqual(h.hf.diagnostics[hf.RND_ID]['rejections'].get('hf_rate_governor'), 1)
            h.step((1, 1.02), advance=30 * SECOND)
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
        self.assertEqual(hf.BOOK_RULES.governor_max_orders, 50)

    def test_cancelled_orders_count_in_the_governor(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            for _ in range(50):
                h.step((2, 1.0))
        self.assertEqual(len(h.rows('cancel', hf.RND_ID)), 1)
        self.assertEqual(len(h.hf.books[hf.RND_ID]['governor']), 1)

    def test_the_pool_cooldown_is_120_s_after_the_exit_fill_or_cancel(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.0))
            close = h.rows('close', hf.RND_ID)[-1]
            cooldown = close['exit_fill_at'] + hf.POOL_RULE.cooldown_ms
            self.assertEqual(h.hf.books[hf.RND_ID]['cooldowns']['|'.join(pool_key(1))], cooldown)
            price = 1.0
            while h.now - 500 < cooldown - 3 * SECOND:
                price += 0.001
                h.step((1, price))
            self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
            self.assertGreater(h.hf.diagnostics[hf.RND_ID]['rejections'].get('hf_pool_cooldown', 0), 0)
            for _ in range(3):
                price += 0.001
                h.step((1, price))
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 2)
        # A cancel starts the same cooldown on the Lab clock.
        h2 = Harness(self)
        with draws(2):
            h2.step((2, 1.0))
            h2.step((2, 1.01))
            for _ in range(50):
                h2.step((3, 1.0))
            cancel = h2.rows('cancel', hf.RND_ID)[0]
            self.assertEqual(h2.hf.books[hf.RND_ID]['cooldowns']['|'.join(pool_key(2))],
                             cancel['at'] + hf.POOL_RULE.cooldown_ms)

    def test_two_losses_do_not_block_but_the_loss_memory_shadow_records_them(self):
        h = Harness(self)
        with draws(1):
            for cycle in range(3):
                open_position(h, 1, price=1.0 + cycle)
                walk_to_trigger(h, 1, 1.02 + cycle)
                h.step((1, 0.99 + cycle))
                for _ in range(62):
                    h.step((2, 1.0))
        closes = h.rows('close', hf.RND_ID)
        orders = h.rows('order', hf.RND_ID, side='entry')
        self.assertEqual(len(closes), 3)
        self.assertTrue(all(row['pnl_usd'] < 0 for row in closes))
        self.assertEqual([row['plm_v1_would_block'] for row in orders], [False, False, True])
        self.assertEqual([row['plm_v1_would_block'] for row in closes], [False, True, True])
        self.assertTrue(closes[-1]['plm_v1_at_order'])
        # The defensive layer was asked with no loss memory of these books (blocked_pools={}).
        self.assertTrue(h.defense.calls)
        self.assertTrue(all(call['blocked_pools'] == {} and call['heat_log_only'] is False
                            for call in h.defense.calls))

    def test_the_reservation_fails_closed_below_the_order_balance(self):
        h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, start_balance_usd=50.0))
        with draws(1, 2):
            h.step((1, 1.0), (2, 1.0))
            h.step((1, 1.01), (2, 1.01))
            h.step((1, 1.02), (2, 1.02))
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
        self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_insufficient_balance')
        self.assertEqual(h.slots()[0]['reserved_usd'], 25.10)

    def test_a_cost_model_mismatch_fails_closed(self):
        cost = hf.cost_functions(lab)
        resolved = lab.forward_cost_model()
        cost.forward_cost_model = lambda: {**resolved, 'generic_dex_fee_bps': 31.0}
        h = Harness(self, cost=cost)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
        self.assertEqual(h.rows('order'), [])
        self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_cost_model_mismatch')
        self.assertEqual(h.hf.config()['cost_model_mismatches'], ['generic_dex_fee_bps'])
        self.assertEqual(h.hf.dashboard_view(h.now)['books'][0]['status'], 'cost_model_mismatch')

    def test_the_universe_and_the_defensive_layer(self):
        h = Harness(self)
        h.defense.blocked[pool_key(4)[1]] = ['rug_ticker_reuse']
        with draws(1, 2, 3, 4, 5):
            h.step((1, 1.0, {'liq': 40_000.0}), (2, 1.0, {'mcap': 1_000_000.0}), (3, 1.0, {'dexId': 'raydium'}),
                   (4, 1.0), (5, 1.0, {'quoteTokenAddress': 'USDC1111111111111111111111111111111111111'}))
            h.step((1, 1.01, {'liq': 40_000.0}), (2, 1.01, {'mcap': 1_000_000.0}), (3, 1.01, {'dexId': 'raydium'}),
                   (4, 1.01), (5, 1.01, {'quoteTokenAddress': 'USDC1111111111111111111111111111111111111'}))
        stats = h.hf.refresh_stats
        self.assertEqual(stats['universe_rejections'], {'hf_liquidity_below_minimum': 1,
                                                        'hf_fee_tier_above_maximum': 1, 'hf_dex_not_pumpswap': 1,
                                                        'hf_quote_not_sol': 1})
        self.assertEqual(h.rows('order'), [])
        self.assertEqual(h.hf.diagnostics[hf.RND_ID]['defensive_reasons'], {'rug_ticker_reuse': 1})
        # One defensive evaluation per pool per refresh, shared by the three books.
        self.assertEqual(len(h.defense.calls), 1)

    def test_the_modeled_round_trip_is_logged_and_never_size_reduced(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
        order = h.rows('order', hf.RND_ID, side='entry')[0]
        self.assertGreater(order['rt_model_pct'], 0.5)
        self.assertLess(order['rt_model_pct'], hf.UNIVERSE.max_modeled_roundtrip_pct)
        h.step((1, 1.02))
        self.assertEqual(h.rows('fill', hf.RND_ID)[0]['entry']['capital'], round(25.0 + 0.0001 * SOL_USD, 8))


# ------------------------------------------------------------------ fills and exits

class FillTests(unittest.TestCase):
    def test_entry_fills_at_the_next_changed_print_never_at_the_decision_print(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            h.step((1, 1.01))          # same print: the leg waits
            self.assertEqual(h.slots()[0]['state'], hf.ORDERED)
            h.step((1, 1.03))
        fill = h.rows('fill', hf.RND_ID)[0]['entry']
        self.assertEqual(fill['status'], lf.FILL_NEXT_REFRESH)
        self.assertEqual(fill['fill_price'], 1.03)
        self.assertEqual(fill['later_observations'], 2)
        expected = lab.calibrated_entry_execution(make_coin(1, 1.03, 0), 25.0, fill['calib_bps'])
        self.assertAlmostEqual(fill['qty'], expected['quantity'], places=6)
        decision = lab.calibrated_entry_execution(make_coin(1, 1.01, 0), 25.0, fill['calib_bps'])
        self.assertNotAlmostEqual(fill['qty'], decision['quantity'], places=3)
        # The decision print only values the shadow of what other Lab books would book.
        self.assertAlmostEqual(fill['dp']['qty'], decision['quantity'], places=4)

    def test_a_quiet_entry_fills_at_the_first_later_observation(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            first = h.now - 500 + 2 * SECOND
            for _ in range(32):
                h.step((1, 1.01))
        fill = h.rows('fill', hf.RND_ID)[0]['entry']
        self.assertEqual(fill['status'], lf.FILL_QUIET)
        self.assertEqual(fill['fill_at'], first)

    def test_no_next_observation_cancels_and_releases_the_slot(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            for _ in range(46):
                h.step((2, 1.0))
        cancel = h.rows('cancel', hf.RND_ID)[0]
        self.assertEqual(cancel['reason'], 'hf_entry_no_next_observation')
        self.assertEqual(h.slots(), [])
        self.assertEqual(h.hf.books[hf.RND_ID]['balance'], 1000.0)

    def test_the_exit_is_ordered_at_the_first_observation_120_s_after_the_decision(self):
        h = Harness(self)
        with draws(1):
            slot = open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            exit_order = h.rows('order', hf.RND_ID, side='exit')[0]
            self.assertGreaterEqual(exit_order['trigger_at'], slot['decision_at'] + hf.EXIT.hold_ms)
            self.assertLess(exit_order['trigger_at'], slot['decision_at'] + hf.EXIT.hold_ms + 2 * SECOND)
            h.step((1, 1.0))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual((close['close_kind'], close['exit_status']), ('HF_TIME_120', lf.FILL_NEXT_REFRESH))
        self.assertEqual(close['exit_fill_price'], 1.0)
        self.assertEqual(close['trigger_price'], 1.02)

    def test_an_exit_without_an_observation_in_the_window_waits_for_the_next_one(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            for _ in range(50):          # 100 s without the pool (the feed stays alive)
                h.step((2, 1.0))
            self.assertEqual(h.slots()[0]['state'], hf.EXIT_ORDERED)
            self.assertTrue(h.slots()[0]['exit_waiting_next'])
            h.step((1, 0.97))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['exit_status'], 'late_next_observation')
        self.assertEqual(close['exit_fill_price'], 0.97)
        self.assertEqual(close['exit_fill_at'], h.now - 500)

    def test_vanished_closes_at_the_last_mark_minus_10_pct_while_the_feed_is_alive(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            last = h.now
            while h.now - last < 10 * MINUTE + 4 * SECOND:
                h.step((2, 1.0), advance=10 * SECOND)
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['close_kind'], 'VANISHED')
        self.assertEqual(close['exit_status'], lf.FILL_VANISHED)
        self.assertAlmostEqual(close['exit_fill_price'], 1.0 * 1.02 * 0.9, places=9)
        self.assertGreater(h.marks.calls, 0)

    def test_a_dead_feed_never_vanishes_and_a_return_after_10_min_is_a_feed_gap_at_the_lower_price(self):
        for back, valued in ((1.10, 1.02), (0.95, 0.95)):
            with self.subTest(back=back):
                h = Harness(self)
                with draws(1):
                    open_position(h, 1)
                    for _ in range(70):
                        h.step(advance=10 * SECOND, alive=False)
                    self.assertEqual(h.rows('close', hf.RND_ID), [])
                    h.step((1, back))
                close = h.rows('close', hf.RND_ID)[0]
                self.assertEqual(close['close_kind'], 'FEED_GAP')
                self.assertAlmostEqual(close['exit_fill_price'], valued, places=9)

    def test_a_pool_at_liquidity_zero_is_valued_at_zero(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.03, {'liquidityUsd': 0.0}))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['net_proceeds_usd'], 0.0)
        self.assertAlmostEqual(close['pnl_usd'], -close['capital_committed_usd'], places=6)

    def test_the_print_guard_values_outlier_fills_at_the_worse_print_and_caps_gains(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.0 * 1.01))
            h.step((1, 0.80))                       # buy fill 20% below the decision print
            fill = h.rows('fill', hf.RND_ID)[0]['entry']
            self.assertTrue(fill['outlier'])
            self.assertEqual(fill['valuation_price'], 1.01)
            walk_to_trigger(h, 1, 1.12)
            h.step((1, 1.40))                       # sell fill 25% above the trigger print
        close = h.rows('close', hf.RND_ID)[0]
        guard_flags = close['print_guard']
        self.assertTrue(guard_flags['entry_outlier'])
        self.assertTrue(guard_flags['exit_outlier'])
        self.assertEqual(guard_flags['exit_value'], 1.12)
        h2 = Harness(self)
        with draws(1):
            open_position(h2, 1)                    # entry valuation 1.02
            walk_to_trigger(h2, 1, 1.16)
            h2.step((1, 1.20))                      # 3.4% above the trigger: no outlier, but > 1.15 x entry
        close = h2.rows('close', hf.RND_ID)[0]
        self.assertTrue(close['print_guard']['gain_capped'])
        self.assertFalse(close['print_guard']['exit_outlier'])
        self.assertAlmostEqual(close['print_guard']['exit_value'], 1.02 * 1.15, places=9)
        # Losses are never capped.
        self.assertFalse(hf.PRINT_GUARD.losses_capped)

    def test_the_toggle_guard_blocks_a_pool_for_6_h(self):
        h = Harness(self)
        with patch.object(lf, 'hashed_coin', return_value=False):
            h.step((1, 1.0))
            h.step((1, 1.0), advance=5 * SECOND)
            h.step((1, 1.25), advance=20 * SECOND)  # a +25% step
            self.assertEqual(h.hf.memory.toggles, {})
        with draws(1):
            h.step((1, 1.01), advance=60 * SECOND)  # back within 2% of the pre-step price: TOGGLE
            self.assertEqual(h.hf.refresh_stats['universe_rejections'], {'hf_toggle_pool': 1})
            block = h.hf.memory.toggles['|'.join(pool_key(1))]
            self.assertEqual(block['known_at'], h.now - 500)
            self.assertEqual(block['until'] - block['known_at'], 6 * HOUR)
            h.step((1, 1.02))
            self.assertEqual(h.hf.refresh_stats['universe_rejections'], {'hf_toggle_pool': 1})
            self.assertEqual(h.rows('order'), [])
            h.now = block['until'] + 10 * SECOND
            h.step((1, 1.03), advance=0)
            h.step((1, 1.04))
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
        self.assertEqual(h.hf.memory.toggles, {})

    def test_a_structural_block_at_the_fill_cancels_and_an_error_fails_closed(self):
        for structural, reasons in (
                (lambda coin, now: {'blocked': True, 'reasons': ['rug_lp_pullable']}, ['rug_lp_pullable']),
                (Mock(side_effect=RuntimeError('boom')), ['structural_check_error'])):
            with self.subTest(reasons=reasons):
                h = Harness(self, structural=structural)
                with draws(1):
                    h.step((1, 1.0))
                    h.step((1, 1.01))
                    h.step((1, 1.02))
                cancel = h.rows('cancel', hf.RND_ID)[0]
                self.assertEqual(cancel['reason'], 'hf_structural_block_at_fill')
                self.assertEqual(cancel['structural'], reasons)
                self.assertEqual(h.slots(), [])

    def test_the_real_structural_guard_checks_the_fill_observation(self):
        defense = FakeDefense()
        defense.registry = covered_registry()
        h = Harness(self, defense=defense)
        h.hf.structural_check = h.hf._structural_check
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            # The fill observation is LP-pullable (liquidity >= market cap): blocked at the fill.
            h.step((1, 1.02, {'liquidityUsd': 30_000_000.0}))
        self.assertEqual(h.rows('cancel', hf.RND_ID)[0]['structural'], ['rug_lp_pullable'])
        h2 = Harness(self, defense=defense)
        h2.hf.structural_check = h2.hf._structural_check
        with draws(1):
            open_position(h2, 1)
        self.assertEqual(h2.slots()[0]['state'], hf.OPEN)
        self.assertFalse(h2.rows('fill')[0]['entry']['structural']['blocked'])
        # Without a ticker registry the guard cannot vouch: blocked at the fill (fail closed).
        h3 = Harness(self)
        h3.hf.structural_check = h3.hf._structural_check
        with draws(1):
            open_position(h3, 1)
        self.assertEqual(h3.rows('cancel', hf.RND_ID)[0]['structural'], ['rug_input_unknown'])


# ------------------------------------------------------------------ accounting

class AccountingTests(unittest.TestCase):
    def closed(self):
        h = Harness(self, audit=FakeAudit(reference={'status': 'pass', 'divergence_pct': 0.4,
                                                     'reference_received_at': NOW0}))
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.03)
            h.step((1, 1.01))
        return h, h.rows('close', hf.RND_ID)[0]

    def test_net50_is_lab_forward_net50_of_the_booked_pnl(self):
        _, close = self.closed()
        stressed = lf.net50(close['pnl_usd'], 25.0, 'HF_TIME_120')
        self.assertAlmostEqual(close['net50_usd'], stressed['net50_usd'], places=6)
        self.assertAlmostEqual(close['net50_pct'], stressed['net50_pct'], places=6)
        self.assertEqual(lf.exit_reason_extra_bps('HF_TIME_120'), 0.0)
        for kind in hf.CLOSE_KINDS:
            self.assertEqual(lf.exit_reason_extra_bps(kind), 0.0)
        self.assertLess(close['net50_usd'], close['pnl_usd'])
        self.assertLess(close['pnl_usd'], close['net0_usd'])

    def test_the_booked_round_trip_and_the_research_fill_are_the_same(self):
        h, close = self.closed()
        fill = h.rows('fill', hf.RND_ID)[0]['entry']
        calib = lf.calib_extra_bps_per_leg(30.0, 200_000.0, 25.0)['total_bps']
        self.assertAlmostEqual(close['calib_bps'], calib, places=6)
        opened = lab.calibrated_entry_execution(make_coin(1, 1.02, 0), 25.0, calib)
        closed = lab.calibrated_exit_execution(make_coin(1, 1.01, 0), opened['quantity'], calib, drain_aware=True)
        self.assertAlmostEqual(close['pnl_usd'], closed['net_proceeds_usd'] - opened['capital_committed_usd'], places=5)
        research = close['research_fill']
        self.assertEqual(research['minus_booked_usd'], 0.0)
        self.assertEqual(research['v'], lf.FILL_BASIS_VERSION)
        self.assertEqual((research['entry_status'], research['exit_status']),
                         (lf.FILL_NEXT_REFRESH, lf.FILL_NEXT_REFRESH))
        self.assertEqual((research['entry_fill_price'], research['exit_fill_price']), (1.02, 1.01))
        self.assertEqual(research['net50_usd'], close['net50_usd'])
        self.assertEqual(fill['fee_bps'], close['fee_bps'])

    def test_the_decision_print_shadow_is_valued_at_the_decision_and_trigger_prints(self):
        _, close = self.closed()
        calib = close['calib_bps']
        opened = lab.calibrated_entry_execution(make_coin(1, 1.01, 0), 25.0, calib)
        closed = lab.calibrated_exit_execution(make_coin(1, 1.03, 0), opened['quantity'], calib, drain_aware=True)
        pnl = closed['net_proceeds_usd'] - opened['capital_committed_usd']
        self.assertAlmostEqual(close['decision_print_shadow']['pnl_usd'], pnl, places=5)
        self.assertAlmostEqual(close['decision_print_shadow']['net50_usd'],
                               lf.net50(pnl, 25.0, 'HF_TIME_120')['net50_usd'], places=5)

    def test_the_price_audit_never_blocks_and_checks_at_most_twice_a_minute(self):
        audit = FakeAudit(reference=None, status='blocked')
        h = Harness(self, audit=audit)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
        order = h.rows('order', hf.RND_ID, side='entry')[0]
        self.assertEqual(order['price_audit']['basis'], 'check')
        self.assertEqual(order['price_audit']['status'], 'blocked')
        coin = make_coin(9, 1.0, h.now)
        results = [h.hf._audit_decision(coin, h.now + index * SECOND) for index in range(10)]
        self.assertEqual(len(audit.checks), 2)
        self.assertEqual([item['basis'] for item in results].count('skipped_bucket'), 9)
        h.hf._audit_decision(coin, h.now + 61 * SECOND)
        self.assertEqual(len(audit.checks), 3)
        bucket = hf.RateBucket()
        for offset in range(0, 600 * SECOND, 7 * SECOND):
            bucket.take(NOW0 + offset)
            self.assertLessEqual(bucket.used(NOW0 + offset), 2)
        # A cached reference is recorded at both fills (log only).
        _, close = self.closed()
        self.assertEqual(close['price_audit']['entry']['status'], 'pass')
        self.assertEqual(close['price_audit']['exit']['deviation_pct'], 0.4)

    def test_hf_never_calls_jupiter_or_rugcheck(self):
        import engine_rug_guard
        import paper_execution_quotes
        source = (ROOT / 'backend' / 'lab_high_frequency.py').read_text(encoding='utf-8')
        self.assertNotIn('engine_rug_guard', source)
        self.assertNotIn('paper_execution_quotes', source)
        with patch.object(lab, 'schedule_jupiter_price_probe') as probe, \
                patch.object(engine_rug_guard, 'check') as rugcheck, \
                patch.object(paper_execution_quotes, 'quote') as quote:
            h = Harness(self, audit=FakeAudit())
            with draws(1):
                open_position(h, 1)
                walk_to_trigger(h, 1, 1.02)
                h.step((1, 1.0))
        self.assertEqual(len(h.rows('close')), 1)
        for mock in (probe, rugcheck, quote):
            mock.assert_not_called()

    def test_pair_price_integrity_cached_never_fetches(self):
        import pair_price_integrity as integrity
        coin = make_coin(1, 1.0, NOW0)
        with patch.object(integrity.POOL, 'submit') as submit:
            self.assertIsNone(integrity.cached(coin))
            submit.assert_not_called()
            self.assertIsNone(integrity.cached({'address': 'bad', 'pairAddress': 'bad'}))


# ------------------------------------------------------------------ daily loss cap and session rotation

class CapAndSessionTests(unittest.TestCase):
    def test_the_cap_counts_realized_closes_and_negative_open_marks(self):
        h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, daily_cap_usd=1.2))
        with draws(1, 2, 3):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.0))
            realized = h.rows('close', hf.RND_ID)[0]['pnl_usd']
            self.assertGreater(realized, -1.2)
            self.assertEqual(h.rows('cap'), [])
            h.step((2, 1.0))
            h.step((2, 1.01))
            h.step((2, 1.02), (3, 1.0))
        cap = h.rows('cap', hf.RND_ID)
        self.assertEqual(len(cap), 1)
        self.assertLessEqual(cap[0]['pnl_usd'], -1.2)
        self.assertEqual(cap[0]['day'], hf.utc_day(h.now))
        self.assertTrue(h.hf.cap_tripped(h.hf.books[hf.RND_ID], h.now))
        view = h.hf.dashboard_view(h.now)['books'][0]
        self.assertEqual(view['status'], 'cap')
        self.assertEqual(view['until'], (hf.utc_day(h.now) + 1) * DAY)

    def test_the_cap_latches_until_00_utc_while_an_ordered_entry_still_fills(self):
        h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, daily_cap_usd=0.5))
        with draws(1, 2, 3):
            h.step((1, 1.0), (2, 1.0))
            h.step((1, 1.01), (2, 1.0))
            h.step((1, 1.01), (2, 1.01))       # pool 2 ordered; pool 1 still ordered (no new print)
            self.assertEqual([s['state'] for s in h.slots()], [hf.ORDERED, hf.ORDERED])
            h.step((1, 1.02), (2, 1.01))       # pool 1 fills at a loss mark: the cap trips
            self.assertEqual(len(h.rows('cap', hf.RND_ID)), 1)
            h.step((1, 1.02), (2, 1.03), (3, 1.0))   # the ORDERED pool 2 still fills
            self.assertEqual(len(h.rows('fill', hf.RND_ID)), 2)
            h.step((1, 1.02), (2, 1.03), (3, 1.01))
            self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_daily_loss_cap')
            self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 2)
            # Both positions run to their exits while the cap holds.
            while any(slot['state'] == hf.OPEN for slot in h.slots()):
                h.step((1, 1.02), (2, 1.03))
            h.step((1, 1.0), (2, 1.0))
            self.assertEqual(len(h.rows('close', hf.RND_ID)), 2)
            self.assertEqual(len(h.rows('cap', hf.RND_ID)), 1)
            # The next UTC day (2026-10-11, session from 07:00) the book trades again.
            h.now = (hf.utc_day(h.now) + 1) * DAY + 7 * HOUR + 5 * SECOND
            h.step((3, 1.02), advance=0)
            h.step((3, 1.03))
        self.assertEqual(h.hf.cap_tripped(h.hf.books[hf.RND_ID], h.now), False)
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 3)

    def test_the_session_opens_at_7d_mod_24(self):
        self.assertEqual(hf.session_open_hour(20736), 0)     # 2026-10-10
        self.assertEqual(hf.session_open_hour(20737), 7)     # 2026-10-11
        self.assertEqual(hf.session_open_hour(20738), 14)    # 2026-10-12
        self.assertEqual(hf.session_open_hour(20739), 21)
        for day in range(20700, 20800):
            self.assertEqual(hf.session_open_hour(day), (7 * day) % 24)
        h = Harness(self, now=20737 * DAY + 6 * HOUR)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_session_not_open')
            view = h.hf.dashboard_view(h.now)['books'][0]
            self.assertEqual((view['status'], view['open_hour']), ('session_closed', 7))
            h.now = 20737 * DAY + 7 * HOUR
            h.step((1, 1.02))
            h.step((1, 1.03))
        self.assertEqual(len(h.rows('order', hf.RND_ID, side='entry')), 1)
        session = h.rows('session', hf.RND_ID)
        self.assertEqual((session[0]['day'], session[0]['open_hour']), (20737, 7))


# ------------------------------------------------------------------ kill rule

class KillRuleTests(unittest.TestCase):
    def test_the_capital_floor_retires_but_open_slots_still_exit(self):
        h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, start_balance_usd=60.0,
                                                     capital_floor_fraction=0.9, daily_cap_usd=1000.0))
        with draws(1, 2):
            h.step((1, 1.0), (2, 1.0))
            h.step((1, 1.01), (2, 1.0))      # order pool 1
            h.step((1, 1.02), (2, 1.01))     # fill pool 1, order pool 2
            h.step((1, 1.02), (2, 1.02))     # fill pool 2
            self.assertEqual([slot['state'] for slot in h.slots()], [hf.OPEN, hf.OPEN])
            first = h.slots()[0]
            while first['state'] == hf.OPEN:
                h.step((1, 1.02), (2, 1.02))
            h.step((1, 1.03, {'liquidityUsd': 0.0}), (2, 1.02))     # pool 1 drained: about -$25
        retire = h.rows('retire', hf.RND_ID)
        self.assertEqual(retire[0]['reason'], 'capital_floor')
        self.assertLessEqual(retire[0]['evidence']['equity_usd'], 54.0)
        self.assertIsNotNone(h.hf.books[hf.RND_ID]['retired'])
        with draws(2, 4):
            h.step((4, 1.0), (2, 1.02))
            h.step((4, 1.01), (2, 1.02))
            self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_retired')
            second = h.slots()[0]
            while second['state'] == hf.OPEN:
                h.step((2, 1.02))
            h.step((2, 1.0))
        self.assertEqual(len(h.rows('close', hf.RND_ID)), 2)
        self.assertEqual(h.slots(), [])

    def test_the_statistical_checkpoint_needs_500_closes_and_3_days(self):
        h = Harness(self)
        rng = random.Random(3)
        start = NOW0
        for index in range(499):
            at = start + index * 8 * MINUTE
            value = round(-3.6 + rng.uniform(-0.5, 0.5), 4)
            # The same per-trade results in both books: the hypothesis does not beat its control.
            synthetic_close(h, hf.QUIET_ID, index % 12, value, at)
            synthetic_close(h, hf.RND_ID, index % 15, value, at)
        h.now = start + 499 * 8 * MINUTE
        h.hf.update({}, h.now, True)
        self.assertIsNone(h.hf.books[hf.QUIET_ID]['kill']['last'])
        synthetic_close(h, hf.QUIET_ID, 3, -3.6, h.now)
        h.hf.update({}, h.now, True)
        last = h.hf.books[hf.QUIET_ID]['kill']['last']
        self.assertIsNotNone(last)
        self.assertEqual(last['closes'], 500)
        self.assertGreaterEqual(last['utc_days'], 3)
        self.assertTrue(last['met'])
        self.assertLess(last['ci95_net50_usd'][1], 0)
        self.assertIn('fee<=50', last['within_fee_bucket_gap_pct'])
        retire = h.rows('retire', hf.QUIET_ID)[0]
        self.assertEqual(retire['reason'], 'kill_checkpoint')
        self.assertIsNone(h.hf.books[hf.DIP15_ID]['retired'])

    def test_a_hypothesis_clearly_better_than_the_control_is_not_retired(self):
        h = Harness(self)
        rng = random.Random(5)
        for index in range(500):
            at = NOW0 + index * 8 * MINUTE
            synthetic_close(h, hf.DIP15_ID, index % 12, -1.0 + rng.uniform(-0.3, 0.3), at)
            synthetic_close(h, hf.RND_ID, index % 15, -3.6 + rng.uniform(-0.3, 0.3), at)
        h.now = NOW0 + 500 * 8 * MINUTE
        h.hf.update({}, h.now, True)
        last = h.hf.books[hf.DIP15_ID]['kill']['last']
        self.assertFalse(last['met'])
        self.assertGreater(last['gap_net50_pct'], last['control_ci_half_width_pct'])
        self.assertEqual(h.hf.books[hf.DIP15_ID]['kill']['next_checkpoint'], 750)
        self.assertIsNone(h.hf.books[hf.DIP15_ID]['retired'])

    def test_too_few_days_postpones_the_checkpoint(self):
        h = Harness(self)
        for index in range(520):
            synthetic_close(h, hf.QUIET_ID, index % 12, -3.6, NOW0 + index * 2 * MINUTE)
        h.now = NOW0 + 520 * 2 * MINUTE
        h.hf.update({}, h.now, True)
        self.assertIsNone(h.hf.books[hf.QUIET_ID]['kill']['last'])

    def test_kill_evaluation_uses_the_lab_forward_pair_bootstrap(self):
        rows = [{'address': f'm{index % 7}', 'pairAddress': f'p{index % 7}', 'net50_usd': -0.9 + 0.01 * (index % 5),
                 'net50_pct': -3.6 + 0.04 * (index % 5), 'fee_bps': 30.0} for index in range(60)]
        evaluation = hf.kill_evaluation(rows, rows)
        params = lf.KillRuleParameters(bootstrap_resamples=2000, bootstrap_seed=20261008)
        ci = lf.bootstrap_ci(rows, key='net50_usd', params=params)
        self.assertEqual(evaluation['ci95_net50_usd'], [round(ci['low'], 6), round(ci['high'], 6)])
        self.assertEqual(evaluation['ci_method'], 'pair_bootstrap')
        self.assertEqual(evaluation['gap_net50_pct'], 0.0)
        self.assertTrue(evaluation['met'])

    def test_control_continuity(self):
        h = Harness(self, environ={hf.ENV_RETIRE: 'HF_QUIET_E95,HF_DIP15_E95'})
        h.step()
        reasons = {row['book']: row['reason'] for row in h.rows('retire')}
        self.assertEqual(reasons, {hf.QUIET_ID: 'operator_flag', hf.DIP15_ID: 'operator_flag',
                                   hf.RND_ID: 'all_hypotheses_retired'})
        h2 = Harness(self, environ={hf.ENV_RETIRE: 'HF_RND_E95'})
        h2.step()
        self.assertEqual({row['book'] for row in h2.rows('retire')}, {hf.RND_ID})
        view = {book['id']: book for book in h2.hf.dashboard_view(h2.now)['books']}
        self.assertTrue(view[hf.QUIET_ID]['control_retired'])
        self.assertEqual(view[hf.RND_ID]['status'], 'retired')
        self.assertNotEqual(view[hf.QUIET_ID]['status'], 'retired')

    def test_a_new_config_hash_gets_its_own_first_checkpoint(self):
        # Review finding: the checkpoint counter was per book, not per config hash. After a
        # strategy-hash change at 600 closes (next checkpoint 750) the new sample was first
        # evaluated at 750 closes instead of at its own 500 closes and 3 UTC days, and the
        # dashboard kept showing the old sample's evaluation.
        h = Harness(self)
        rng = random.Random(7)

        def closes(count, start, spacing):
            at = start
            for index in range(count):
                at = start + index * spacing
                synthetic_close(h, hf.DIP15_ID, index % 12, round(-1.0 + rng.uniform(-0.3, 0.3), 4), at)
                synthetic_close(h, hf.RND_ID, index % 15, round(-3.6 + rng.uniform(-0.3, 0.3), 4), at)
            return at
        h.now = closes(500, NOW0, 8 * MINUTE)
        h.hf.update({}, h.now, True)
        h.now = closes(100, h.now + 8 * MINUTE, 8 * MINUTE)
        h.hf.update({}, h.now, True)
        old_cfg = h.hf.cfg[hf.DIP15_ID]
        kill = h.hf.books[hf.DIP15_ID]['kill']
        self.assertEqual((kill['cfg'], kill['last']['closes'], kill['next_checkpoint']), (old_cfg, 500, 750))
        self.assertFalse(kill['last']['met'])
        # A strategy-hash change: a new evidence sample in the same book and ledger.
        new_cfg = 'e' * 64
        h.hf.cfg[hf.DIP15_ID] = new_cfg
        h.now = closes(499, h.now + HOUR, 10 * MINUTE)
        h.hf.update({}, h.now, True)
        kill = h.hf.books[hf.DIP15_ID]['kill']
        self.assertEqual((kill['cfg'], kill['last'], kill['next_checkpoint'], kill['checkpoints']),
                         (new_cfg, None, 500, 0))
        self.assertEqual((kill['previous']['cfg'], kill['previous']['checkpoints']), (old_cfg, 1))
        self.assertIsNone(next(book for book in h.hf.dashboard_view(h.now)['books']
                               if book['id'] == hf.DIP15_ID)['kill']['last'])
        h.now += 10 * MINUTE
        synthetic_close(h, hf.DIP15_ID, 3, -1.0, h.now)
        h.hf.update({}, h.now, True)
        kill = h.hf.books[hf.DIP15_ID]['kill']
        self.assertEqual((kill['cfg'], kill['last']['closes'], kill['next_checkpoint']), (new_cfg, 500, 750))
        self.assertGreaterEqual(kill['last']['utc_days'], 3)
        self.assertIsNone(h.hf.books[hf.DIP15_ID]['retired'])
        view = next(book for book in h.hf.dashboard_view(h.now)['books'] if book['id'] == hf.DIP15_ID)
        self.assertEqual((view['kill']['closes'], view['kill']['last']['closes'], view['kill']['next_checkpoint']),
                         (500, 500, 750))
        # The per-config state is checkpointed with its config hash.
        h.hf.checkpoint_if_due(h.now, force=True)
        restarted = h.build()
        restarted.load()
        stored = restarted.books[hf.DIP15_ID]['kill']
        self.assertEqual((stored['cfg'], stored['last']['closes'], stored['previous']['cfg']), (new_cfg, 500, old_cfg))

    def test_kill_state_for_adopts_a_fresh_state_and_replaces_another_configs(self):
        fresh = hf.new_book(hf.QUIET_ID, hf.DEFAULT_BUDGET)['kill']
        adopted = hf.kill_state_for(fresh, 'a' * 64)
        self.assertEqual((adopted['cfg'], adopted['next_checkpoint'], adopted['last']), ('a' * 64, 500, None))
        evaluated = {**adopted, 'next_checkpoint': 1000, 'last': {'closes': 750}, 'checkpoints': 2}
        self.assertEqual(hf.kill_state_for(evaluated, 'a' * 64), evaluated)
        replaced = hf.kill_state_for(evaluated, 'b' * 64)
        self.assertEqual((replaced['cfg'], replaced['next_checkpoint'], replaced['last'], replaced['checkpoints']),
                         ('b' * 64, 500, None, 0))
        self.assertEqual(replaced['previous'], {'cfg': 'a' * 64, 'last': {'closes': 750}, 'checkpoints': 2})
        # A state of unknown config that already evaluated is never inherited.
        legacy = {'next_checkpoint': 750, 'last': {'closes': 600}, 'checkpoints': 1}
        self.assertEqual(hf.kill_state_for(legacy, 'c' * 64)['next_checkpoint'], 500)


# ------------------------------------------------------------------ persistence and restart

def state_signature(container):
    """Everything a restart must rebuild (cooldowns still running at the container's clock)."""
    now = container.clock()
    return {book_id: {'balance': book['balance'], 'trade_no': book['trade_no'],
                      'slots': {key: slot['state'] for key, slot in book['slots'].items()},
                      'aggregates': hf._rounded_aggregate(book['aggregates']),
                      'cooldowns': {key: until for key, until in book['cooldowns'].items() if until > now},
                      'retired': book['retired'], 'cap': book['cap'], 'last_closes': book['last_closes']}
            for book_id, book in container.books.items()}


class PersistenceTests(unittest.TestCase):
    def test_the_journal_is_written_before_the_checkpoint(self):
        h = Harness(self)
        checkpoint = json.loads((h.root / 'state.json').read_text(encoding='utf-8'))
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
        journal = list((h.root / 'journal' / hf.RND_ID).glob('*.jsonl'))
        self.assertEqual(len(journal), 1)
        rows = [json.loads(line) for line in journal[0].read_text(encoding='utf-8').splitlines()]
        self.assertIn('order', [row['kind'] for row in rows])
        self.assertEqual(json.loads((h.root / 'state.json').read_text(encoding='utf-8'))['seq'], checkpoint['seq'])
        h.hf.checkpoint_if_due(h.now + 20 * SECOND)
        stored = json.loads((h.root / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['seq'], max(row['seq'] for row in h.rows()))
        self.assertEqual(stored['version'], 'HF_CHECKPOINT_V1')
        self.assertLess(len(json.dumps(stored)), 200_000)

    def test_a_crash_replays_the_journal_exactly_once(self):
        h = Harness(self)
        with draws(1, 2):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.0))
            h.hf.checkpoint_if_due(h.now, force=True)
            for _ in range(70):
                h.step((2, 1.0))
            open_position(h, 2)
            walk_to_trigger(h, 2, 1.02)
            h.step((2, 1.0))
        self.assertEqual(len(h.rows('close')), 2)
        before = state_signature(h.hf)
        seq = h.hf.seq
        report = h.restart()
        self.assertGreater(report['replayed_rows'], 0)
        self.assertEqual(report['restart_cancels'], 0)
        self.assertEqual(state_signature(h.hf), before)
        self.assertEqual(h.hf.seq, seq)
        again = h.restart()
        self.assertEqual(again['replayed_rows'], 0)
        self.assertEqual(state_signature(h.hf), before)
        self.assertEqual(h.hf.books[hf.RND_ID]['aggregates'][PINNED_CONFIG_HASHES[hf.RND_ID]]['n'], 2)

    def test_an_unreadable_checkpoint_is_rebuilt_from_the_journal(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.0))
        before = state_signature(h.hf)
        (h.root / 'state.json').write_text('{broken', encoding='utf-8')
        report = h.restart()
        self.assertEqual(report['checkpoint'], 'unreadable_rebuilt_from_journal')
        self.assertEqual(state_signature(h.hf), before)

    def test_a_torn_last_line_is_skipped_and_later_rows_stay_intact(self):
        h = Harness(self)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
        path = next((h.root / 'journal' / hf.RND_ID).glob('*.jsonl'))
        with path.open('ab') as handle:
            handle.write(b'{"v":1,"seq":99')
        report = h.restart()
        self.assertEqual(report['torn_tails'], 1)
        with draws(2):
            h.step((2, 1.0))
            h.step((2, 1.01))
        rows = h.rows()
        self.assertGreaterEqual(h.hf.journal.malformed_rows, 1)
        self.assertIn(pool_key(2)[1], [row['pairAddress'] for row in rows if row['kind'] == 'order'])
        self.assertTrue(path.read_bytes().startswith(b'{'))
        self.assertIn(b'"seq":99\n', path.read_bytes())

    def test_a_line_torn_inside_a_multibyte_symbol_never_stops_the_load_or_the_reports(self):
        # Review finding: rows are UTF-8 (ensure_ascii=False) and carry the symbol. A crash that tore
        # the last line inside a Cyrillic or emoji character made the text-mode read raise
        # UnicodeDecodeError: load() failed (HF stayed off), on every later restart too, and so did
        # the kill checkpoint's journal read and evaluate_paper_lab.py --hf-dir.
        h = Harness(self)
        symbol = 'ПЕПЕ🐸'
        with draws(1):
            h.step((1, 1.0, {'symbol': symbol}))
            h.step((1, 1.01, {'symbol': symbol}))
        path = next((h.root / 'journal' / hf.RND_ID).glob('*.jsonl'))
        order = next(line for line in path.read_bytes().splitlines() if b'"kind":"order"' in line)
        emoji = '🐸'.encode('utf-8')
        self.assertIn(emoji, order)                       # stored as UTF-8, not \\u escapes
        with path.open('ab') as handle:
            handle.write(order[:order.index(emoji) + 2])  # torn 2 bytes into the 4-byte emoji
        with self.assertRaises(UnicodeDecodeError):
            path.read_text(encoding='utf-8')
        report = h.restart(advance=4 * SECOND)
        self.assertEqual(report['torn_tails'], 1)
        self.assertEqual(h.hf.journal.malformed_rows, 1)
        self.assertEqual(report['restart_cancels'], 1)    # the ORDERED slot, replayed and cancelled
        with draws(2):
            h.step((2, 1.0))
            h.step((2, 1.01))
        rows = h.rows()
        orders = [row for row in rows if row['kind'] == 'order' and row['side'] == 'entry']
        self.assertEqual([(row['pairAddress'], row['symbol']) for row in orders],
                         [(pool_key(1)[1], symbol), (pool_key(2)[1], 'HF2')])
        # A second restart over the same bytes loads again (nothing was removed, nothing raises).
        h.restart(advance=4 * SECOND)
        self.assertEqual(h.hf.journal.malformed_rows, 1)
        # The report readers count the torn line as one malformed line and keep every other row.
        from paper_lab_metrics import measure_hf_rows, read_hf_journal
        read_rows, malformed = read_hf_journal(h.root)
        self.assertEqual(malformed, 1)
        self.assertEqual(sorted(row['seq'] for row in read_rows), sorted(row['seq'] for row in h.rows()))
        self.assertEqual(measure_hf_rows(read_rows)['rejected_rows']['malformed'], 0)
        edge = edge_report_module()
        collector = edge.LedgerCollector()
        collector.add_hf_journal(h.root)
        self.assertEqual(collector.rejected['malformed'], 1)

    def test_every_row_carries_the_journal_epoch_and_a_reset_starts_a_new_one(self):
        h = Harness(self)
        epoch = h.hf.epoch
        self.assertRegex(epoch, r'^[0-9a-f]{16}$')
        self.assertEqual((h.hf.load_report['epoch'], h.hf.load_report['epoch_source']), (epoch, 'new'))
        with draws(1):
            open_position(h, 1)
        rows = h.rows()
        self.assertTrue(rows)
        self.assertEqual({row['epoch'] for row in rows}, {epoch})
        h.hf.checkpoint_if_due(h.now, force=True)
        self.assertEqual(json.loads((h.root / 'state.json').read_text(encoding='utf-8'))['epoch'], epoch)
        self.assertEqual((h.restart()['epoch_source'], h.hf.epoch), ('checkpoint', epoch))
        self.assertEqual(h.hf.metrics()['epoch'], epoch)
        (h.root / 'state.json').write_text('{broken', encoding='utf-8')
        self.assertEqual((h.restart()['epoch_source'], h.hf.epoch), ('journal', epoch))
        # A reset archives the lineage and starts a new one (seq restarts at 1).
        h.hf = h.build()
        report = h.hf.load(reset=True)
        self.assertEqual(report['epoch_source'], 'new')
        self.assertNotEqual(h.hf.epoch, epoch)
        archived = {json.loads(line)['epoch'] for path in Path(report['archive']).rglob('*.jsonl')
                    for line in path.read_text(encoding='utf-8').splitlines() if line.strip()}
        self.assertEqual(archived, {epoch})

    def test_two_sessions_with_the_same_seq_stay_two_trades_in_the_reports(self):
        # Review finding: seq restarts at 1 after a reset, so an archived session's close and the
        # current session's close shared (book, cfg, seq): paper_edge_report excluded both as a
        # conflict and measure_hf_rows dropped the second as a duplicate.
        h = Harness(self)

        def one_trade(price):
            with draws(1):
                open_position(h, 1)
                walk_to_trigger(h, 1, 1.02)
                h.step((1, price))
            return h.rows('close', hf.RND_ID)[-1]
        first = one_trade(0.99)
        h.hf.checkpoint_if_due(h.now, force=True)
        h.hf = h.build()
        archive = Path(h.hf.load(reset=True)['archive'])
        h.now += 10 * MINUTE
        second = one_trade(0.98)
        self.assertEqual(first['seq'], second['seq'])
        self.assertNotEqual(first['epoch'], second['epoch'])
        self.assertNotEqual(first['pnl_usd'], second['pnl_usd'])
        edge = edge_report_module()
        collector = edge.LedgerCollector()
        collector.add_hf_journal(h.root)          # the current session (archives skipped)
        collector.add_hf_journal(archive)         # the archived session, as the runbook says
        report = edge.build_report(collector, iterations=20, seed=3)
        self.assertEqual(report['dedupe']['unique_closed_trades'], 2)
        self.assertEqual(report['dedupe']['conflicting_ids_excluded'], 0)
        self.assertEqual(report['high_frequency']['unique_closed_trades'], 2)
        from paper_lab_metrics import measure_hf_rows, read_hf_journal
        rows = read_hf_journal(h.root)[0] + read_hf_journal(archive)[0]
        measured = measure_hf_rows(rows)
        book = measured['books'][f"{hf.RND_ID}:{PINNED_CONFIG_HASHES[hf.RND_ID][:12]}"]
        self.assertEqual(measured['rejected_rows']['duplicate'], 0)
        self.assertEqual((book['closes'], book['journal_epochs']), (2, 2))
        self.assertAlmostEqual(book['booked_usd'], first['pnl_usd'] + second['pnl_usd'], places=6)

    def test_a_restart_cancels_ordered_slots_and_resumes_open_ones(self):
        h = Harness(self)
        with draws(1, 2):
            slot = open_position(h, 1)
            h.step((1, 1.02), (2, 1.0))
            h.step((1, 1.02), (2, 1.01))
            self.assertEqual([s['state'] for s in h.slots()], [hf.OPEN, hf.ORDERED])
            h.hf.checkpoint_if_due(h.now, force=True)
            report = h.restart(advance=3 * MINUTE)
            self.assertEqual((report['restart_cancels'], report['resumed_open']), (1, 1))
            self.assertEqual(h.rows('cancel', hf.RND_ID)[0]['reason'], 'restart')
            self.assertEqual([s['state'] for s in h.slots()], [hf.OPEN])
            h.step((1, 1.02))
            exit_order = h.rows('order', hf.RND_ID, side='exit')[0]
            self.assertTrue(exit_order['late_exit_restart'])
            self.assertGreater(exit_order['trigger_at'], slot['decision_at'] + hf.EXIT.hold_ms + MINUTE)
            h.step((1, 1.0))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertTrue(close['late_exit_restart'])

    def test_an_outage_over_10_min_closes_held_slots_as_a_flagged_feed_gap(self):
        # Review finding: a deploy, watchdog recovery or rollback longer than 10 minutes closes the
        # held slots as FEED_GAP at min(pre-gap, return) (that rule takes precedence over
        # late_exit_restart), and those closes could not be told apart from real feed gaps.
        outcomes = {}
        for minutes in (3, 15, 45):
            with self.subTest(minutes=minutes):
                h = Harness(self)
                with draws(1):
                    open_position(h, 1)
                    h.step((1, 1.02))
                    self.assertEqual(h.slots()[0]['state'], hf.OPEN)
                    h.hf.checkpoint_if_due(h.now, force=True)
                    stopped_at = h.now
                    h.restart(advance=minutes * MINUTE)
                    for price in (1.03, 1.04, 1.05):
                        h.step((1, price))
                close = h.rows('close', hf.RND_ID)[0]
                outcomes[minutes] = close
                if minutes == 3:
                    self.assertEqual((close['close_kind'], close['exit_status']), ('HF_TIME_120', lf.FILL_NEXT_REFRESH))
                    self.assertTrue(close['late_exit_restart'])
                    self.assertNotIn('restart_gap', close)
                    continue
                self.assertEqual((close['close_kind'], close['exit_status']), ('FEED_GAP', 'feed_gap'))
                self.assertAlmostEqual(close['exit_fill_price'], 1.02, places=9)   # min(pre-gap, return)
                self.assertTrue(close['restart_gap'])
                self.assertEqual(close['restart_outage_ms'], h.hf.started_at - stopped_at)
                self.assertGreater(close['gap_ms'], 10 * MINUTE)
                self.assertIn('restart_gap', h.hf.books[hf.RND_ID]['last_closes'][0]['flags'])
        self.assertLess(outcomes[15]['pnl_usd'], outcomes[3]['pnl_usd'])
        # A real feed gap while the Lab runs (marked after the restart) is never flagged.
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            h.hf.checkpoint_if_due(h.now, force=True)
            h.restart(advance=MINUTE)
            h.step((1, 1.02))
            for _ in range(70):
                h.step(advance=10 * SECOND, alive=False)
            h.step((1, 0.99))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['close_kind'], 'FEED_GAP')
        self.assertNotIn('restart_gap', close)

    def test_a_vanish_across_a_restart_is_flagged(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            h.hf.checkpoint_if_due(h.now, force=True)
            h.restart(advance=12 * MINUTE)
            for _ in range(8):
                h.step((2, 1.0), advance=10 * SECOND)
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['close_kind'], 'VANISHED')
        self.assertTrue(close['restart_gap'])

    def test_an_exit_ordered_slot_is_never_stranded_by_a_failed_close(self):
        # Review finding (a): the time-only branch ignored a failed close of a resolved quiet leg, so
        # the slot stayed EXIT_ORDERED (slot, pool and $25.10 held) while the pool kept printing.
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            slot = h.slots()[0]
            self.assertEqual(slot['state'], hf.EXIT_ORDERED)
            original = h.hf.cost.calibrated_exit_execution
            failing = {'on': True}

            def exit_execution(*args, **kwargs):
                if failing['on']:
                    raise ValueError('network_price_unknown')
                return original(*args, **kwargs)
            h.hf.cost = SimpleNamespace(**{**vars(h.hf.cost), 'calibrated_exit_execution': exit_execution})
            h.step((1, 1.02))                        # the same print: the quiet candidate fill
            for _ in range(60):                      # then no print while the feed is alive: the leg
                h.step((2, 1.0))                     # resolves quiet at the window end, on time alone
                if slot['exit_leg']['status'] != lf.FILL_PENDING:
                    break
            self.assertEqual(slot['exit_leg']['status'], lf.FILL_QUIET)
            self.assertEqual(h.rows('close'), [])    # the close failed (no network price)
            self.assertTrue(slot['exit_waiting_next'])
            failing['on'] = False
            h.step((1, 1.01))
        close = h.rows('close', hf.RND_ID)[0]
        self.assertEqual(close['exit_status'], 'late_next_observation')
        self.assertEqual(h.slots(), [])
        # A slot restored in that state (resolved leg, no close, not waiting) recovers the same way.
        h2 = Harness(self)
        with draws(1):
            open_position(h2, 1)
            walk_to_trigger(h2, 1, 1.02)
            slot = h2.slots()[0]
            slot['exit_leg']['status'] = lf.FILL_QUIET
            slot['exit_waiting_next'] = False
            h2.step((1, 1.0))
        self.assertEqual(len(h2.rows('close', hf.RND_ID)), 1)
        self.assertEqual(h2.slots(), [])

    def test_a_stop_between_the_journal_and_the_state_never_loses_the_row(self):
        # Review finding (b): a KeyboardInterrupt (the local stop) between journal.append and _apply
        # let persist('stopped') checkpoint a seq that included the unapplied close, so the restart
        # never replayed it: balance 1000, no aggregate, the slot EXIT_ORDERED for good.
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            h.hf.checkpoint_if_due(h.now, force=True)
            apply = h.hf._apply

            def interrupted(row):
                if row['kind'] == 'close':
                    raise KeyboardInterrupt
                return apply(row)
            with patch.object(h.hf, '_apply', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                h.step((1, 1.0))
            # persist('stopped'): the journal is flushed, the checkpoint is not written.
            self.assertFalse(h.hf.checkpoint_if_due(h.now, force=True))
            self.assertEqual(h.hf.checkpoint_stats['skipped_in_flight'], 1)
            journaled = h.rows('close', hf.RND_ID)
            self.assertEqual(len(journaled), 1)
            stored = json.loads((h.root / 'state.json').read_text(encoding='utf-8'))
            self.assertLess(stored['seq'], journaled[0]['seq'])
            report = h.restart(advance=4 * SECOND)
        self.assertGreaterEqual(report['replayed_rows'], 1)
        book = h.hf.books[hf.RND_ID]
        self.assertEqual(book['slots'], {})
        self.assertAlmostEqual(book['balance'], 1000.0 + journaled[0]['pnl_usd'], places=6)
        self.assertEqual(book['aggregates'][PINNED_CONFIG_HASHES[hf.RND_ID]]['n'], 1)
        self.assertTrue(h.hf.checkpoint_if_due(h.now, force=True))

    def test_an_exit_ordered_leg_keeps_resolving_after_a_restart(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
            walk_to_trigger(h, 1, 1.02)
            self.assertEqual(h.slots()[0]['state'], hf.EXIT_ORDERED)
            h.hf.checkpoint_if_due(h.now, force=True)
            h.restart(advance=4 * SECOND)
            self.assertEqual(h.slots()[0]['state'], hf.EXIT_ORDERED)
            h.step((1, 0.99))
        self.assertEqual(h.rows('close', hf.RND_ID)[0]['exit_status'], lf.FILL_NEXT_REFRESH)

    def test_journal_files_rotate_at_00_utc(self):
        h = Harness(self, now=20736 * DAY + 23 * HOUR + 59 * MINUTE + 50 * SECOND)
        with draws(1):
            h.step((1, 1.0))
            h.step((1, 1.01))
            h.step((1, 1.02), advance=8 * SECOND)
        files = sorted(path.name for path in (h.root / 'journal' / hf.RND_ID).glob('*.jsonl'))
        self.assertEqual(files, ['2026-10-10.jsonl', '2026-10-11.jsonl'])

    def test_a_reset_archives_the_hf_root_and_starts_empty(self):
        h = Harness(self)
        with draws(1):
            open_position(h, 1)
        h.hf.checkpoint_if_due(h.now, force=True)
        container = h.build()
        report = container.load(reset=True)
        self.assertIsNotNone(report['archive'])
        archive = Path(report['archive'])
        self.assertTrue((archive / 'state.json').is_file())
        self.assertTrue(any((archive / 'journal').rglob('*.jsonl')))
        self.assertEqual(container.books[hf.RND_ID]['slots'], {})
        self.assertEqual(container.books[hf.RND_ID]['balance'], 1000.0)

    def test_aggregates_equal_a_recomputation_from_the_journal(self):
        for seed in (11, 12, 13):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, daily_cap_usd=4.0))
                prices = {n: 1.0 for n in range(1, 5)}
                drawn = set()

                def fake_draw(pair, t, p, salt):
                    key = (pair, t, salt)
                    if key not in drawn and rng.random() < 0.4:
                        drawn.add(key)
                    return key in drawn
                with patch.object(lf, 'hashed_coin', side_effect=fake_draw):
                    for loop in range(900):
                        specs = []
                        for n in prices:
                            if rng.random() < 0.6:
                                prices[n] = round(prices[n] * (1 + rng.uniform(-0.02, 0.02)), 8)
                                liq = 0.0 if rng.random() < 0.002 else 200_000.0
                                specs.append((n, prices[n], {'liquidityUsd': liq, 'v5': rng.choice([100.0, 10_000.0])}))
                        h.step(*specs, alive=bool(specs) or rng.random() < 0.5,
                               advance=rng.choice([2 * SECOND, 2 * SECOND, 30 * SECOND]))
                        if loop % 250 == 249:
                            if rng.random() < 0.5:
                                h.hf.checkpoint_if_due(h.now, force=True)
                            h.restart()
                rows = h.rows()
                self.assertGreater(sum(1 for row in rows if row['kind'] == 'close'), 10)
                folded = hf.fold_aggregates(rows)
                for book_id in hf.BOOK_IDS:
                    self.assertEqual(hf._rounded_aggregate(h.hf.books[book_id]['aggregates']),
                                     hf._rounded_aggregate(folded.get(book_id, {})))
                    closes = [row for row in rows if row['kind'] == 'close' and row['book'] == book_id]
                    balance = h.hf.books[book_id]['start_balance'] + sum(row['pnl_usd'] for row in closes)
                    self.assertAlmostEqual(h.hf.books[book_id]['balance'], balance, places=4)
                    if closes:
                        agg = h.hf.books[book_id]['aggregates'][PINNED_CONFIG_HASHES[book_id]]
                        self.assertEqual(agg['n'], len(closes))
                        self.assertEqual(sum(value[0] for value in agg['by_pair'].values()), len(closes))
                for row in rows:
                    size = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                    self.assertLessEqual(size, 1800 if row['kind'] == 'close' else 1300, row['kind'])


# ------------------------------------------------------------------ performance and the dashboard view

class PerformanceAndViewTests(unittest.TestCase):
    def test_three_books_three_slots_and_90_coins_stay_under_50_ms(self):
        layer = entry_defense.DefensiveEntryLayer(registry=covered_registry())

        class Allowing:
            """The real layer's evaluate runs (its cost is measured); its verdict is ignored."""
            registry = layer.registry

            def observe(self, feed, now):
                layer.observe(feed, now)

            def evaluate(self, coin, now, **kwargs):
                layer.evaluate(coin, now, **kwargs)
                return {'allowed': True, 'reasons': []}
        h = Harness(self, defense=Allowing())
        h.hf.structural_check = h.hf._structural_check
        rng = random.Random(1)
        prices = {n: 1.0 + n / 100 for n in range(90)}
        timings = []
        with patch.object(lf, 'hashed_coin', return_value=True):
            for loop in range(120):
                for n in prices:
                    prices[n] *= 1 + rng.uniform(-0.01, 0.01)
                feed = [h.coin(n, prices[n], v5=100.0) for n in prices]
                h.now += 2 * SECOND
                started = time.perf_counter()
                h.hf.update(lab.hf_feed_prices(feed), h.now, True)
                h.hf.on_refresh(feed, h.now)
                timings.append((time.perf_counter() - started) * 1000)
        self.assertEqual({len(book['slots']) for book in h.hf.books.values()} - {0, 1, 2, 3}, set())
        self.assertGreater(len(h.rows('close')), 0)
        warm = timings[10:]
        self.assertLess(sum(warm) / len(warm), 50.0)

    def test_the_view_stays_under_16_kb_and_names_its_states(self):
        h = Harness(self)
        for book_id in hf.BOOK_IDS:
            for index in range(25):
                row = synthetic_close(h, book_id, index, -3.6, h.now - index * MINUTE)
                h.hf.books[book_id]['last_closes'][0]['symbol'] = 'S' * 40
        view = h.hf.dashboard_view(h.now)
        size = len(json.dumps(view, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        self.assertLessEqual(size, 16 * 1024)
        self.assertEqual(view['title'], 'Висока честота (HF)')
        self.assertEqual(len(view['books'][0]['last_closes']), 10)
        self.assertEqual([book['id'] for book in view['books']], list(hf.BOOK_IDS))
        book = view['books'][1]
        for field in ('trades_last_60m', 'trades_today', 'open_slots', 'max_slots', 'booked_today_usd',
                      'net50_today_usd', 'booked_total_usd', 'net50_total_usd', 'mean_booked_pct', 'mean_net50_pct',
                      'win_rate_net50_pct', 'usd_per_hour_today', 'cap', 'kill', 'gap_vs_control', 'top_pair_share',
                      'cancels_today', 'last_closes'):
            self.assertIn(field, book)
        self.assertEqual(book['gap_vs_control']['gap_net50_pct'], 0.0)
        self.assertIn('fee<=50', book['gap_vs_control']['within_fee_bucket_pct'])
        self.assertEqual(view['books'][0]['status'], 'warming')

    def test_the_time_budget_degrades_new_orders_only(self):
        h = Harness(self)
        with draws(1, 2):
            open_position(h, 1)
            for _ in range(3):
                h.hf.record_loop_time(300.0)
            self.assertTrue(h.hf.degraded)
            h.step((1, 1.02), (2, 1.0))
            h.step((1, 1.02), (2, 1.01))
            self.assertEqual(h.hf.diagnostics[hf.RND_ID]['blocked_reason'], 'hf_degraded')
            walk_to_trigger(h, 1, 1.02)
            h.step((1, 1.0))
            self.assertEqual(len(h.rows('close')), 1)
            # Hysteresis: a fast (degraded, order-free) loop does not clear it.
            h.hf.record_loop_time(10.0)
            self.assertTrue(h.hf.degraded)
            h.now += hf.TIME_BUDGET_MIN_DEGRADED_MS
            for _ in range(hf.TIME_BUDGET_CLEAR_LOOPS):
                h.hf.record_loop_time(10.0)
        self.assertFalse(h.hf.degraded)
        metrics = h.hf.metrics()['time_budget']
        self.assertEqual((metrics['degraded_episodes'], metrics['degraded_cleared_at']), (1, h.now))

    def test_sustained_slowness_stays_degraded_instead_of_flapping(self):
        # Review finding: degraded cleared at the first loop under budget, and a degraded loop is fast
        # because it does no order work, so slow order work ran in about 3 of every 4 loops.
        h = Harness(self)
        slow_loops = 0
        for loop in range(1200):                    # 40 minutes of 2 s loops
            h.now += 2 * SECOND
            slow = not h.hf.degraded                # order work is slow whenever it runs
            slow_loops += slow
            h.hf.record_loop_time(400.0 if slow else 5.0)
        # At most 3 slow loops per degraded episode, and episodes at least 5 minutes apart.
        episodes = h.hf.metrics()['time_budget']['degraded_episodes']
        self.assertLessEqual(episodes, 1 + 40 // 5)
        self.assertLessEqual(slow_loops, 3 * episodes)
        self.assertLess(slow_loops / 1200, 0.03)
        # The degraded window never clears before 5 minutes, even with every loop in budget.
        h2 = Harness(self)
        for _ in range(3):
            h2.hf.record_loop_time(300.0)
        for _ in range(149):                        # 298 s of fast loops
            h2.now += 2 * SECOND
            h2.hf.record_loop_time(1.0)
        self.assertTrue(h2.hf.degraded)
        h2.now += 2 * SECOND
        h2.hf.record_loop_time(1.0)
        self.assertFalse(h2.hf.degraded)
        self.assertEqual(hf.config_view()['time_budget']['version'], 'HF_TIME_BUDGET_V1')

    def test_each_journal_file_is_fsynced_at_most_once_per_lab_loop(self):
        # Review finding: update() and on_refresh() each flushed, so a book that closed in update and
        # ordered in on_refresh fsynced its day file twice in one loop. The Lab now flushes once, at
        # the end of the loop (strategy_lab.hf_end_loop), before persist() checkpoints.
        h = Harness(self)
        rng = random.Random(5)
        prices = {n: 1.0 for n in range(1, 13)}
        journal = h.hf.journal
        written = []                                 # (loop, path) of every file write + fsync
        original = journal.flush
        current = {'loop': -1}

        def counting_flush():
            written.extend((current['loop'], str(path)) for path in journal._pending)
            return original()
        journal.flush = counting_flush
        touched_both = 0
        with patch.object(lf, 'hashed_coin', side_effect=lambda pair, t, p, salt: rng.random() < 0.5):
            for loop in range(600):
                current['loop'] = loop
                h.now += 2 * SECOND
                for n in prices:
                    prices[n] = round(prices[n] * (1 + rng.uniform(-0.01, 0.01)), 8)
                feed = [h.coin(n, prices[n]) for n in prices]
                h.hf.update(lab.hf_feed_prices(feed), h.now, True)
                after_update = {str(path): len(lines) for path, lines in journal._pending.items()}
                h.hf.on_refresh(feed, h.now)
                touched_both += any(len(lines) > after_update[str(path)] for path, lines in journal._pending.items()
                                    if str(path) in after_update)
                h.hf.flush_journal()               # strategy_lab.hf_end_loop
                h.hf.checkpoint_if_due(h.now)      # persist(): nothing left to flush
        self.assertGreater(len([row for row in h.rows() if row['kind'] == 'close']), 20)
        self.assertGreater(touched_both, 0)          # the case the finding measured happened
        self.assertEqual(len(written), len(set(written)))


# ------------------------------------------------------------------ the Lab process

class StrategyLabIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='neo-hf-lab-')
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        # The Lab clock (and so the HF clock, injected at build time) is the test's: a session open hour.
        self.clock = {'now': NOW0}
        defense = FakeDefense()
        defense.registry = covered_registry()
        self.patches = [patch.object(lab, 'now_ms', side_effect=lambda: self.clock['now']),
                        patch.object(lab, 'STATE_PATH', root / 'strategy_lab.json'),
                        patch.object(lab, 'COMPACT_PATH', root / 'strategy_lab_compact.json'),
                        patch.object(lab, 'HF_ROOT', root / 'strategy_lab_hf'),
                        patch.object(lab, 'HF', None),
                        patch.object(lab, 'DEFENSE', None),
                        patch.object(lab, 'STATE', {'started_at': NOW0, 'updated_at': NOW0, 'status': 'starting',
                                                    'books': {s['id']: lab.empty_book(s) for s in lab.STRATEGIES}}),
                        patch.object(lab, 'entry_defense_layer', return_value=defense),
                        patch.object(lab, 'POSITION_MARK_FEED', FakeMarks()),
                        patch.object(lab, 'HF_MARK_FEED', FakeMarks()),
                        patch.object(lab, 'HF_BUILD', {'attempts': 0, 'failures': 0, 'last_attempt_at': None,
                                                       'last_error': None, 'next_attempt_at': None,
                                                       'built_at': None}),
                        patch.object(lab, 'RESET_REQUESTED', False),
                        patch.object(lab, 'price_integrity', FakeAudit())]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.root = root

    def test_persist_publishes_hf_only_in_the_compact_projection(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
        self.assertIs(lab.HF, container)
        with draws(1):
            for price in (1.0, 1.01, 1.02):
                self.clock['now'] += 2 * SECOND
                feed = [make_coin(1, price, self.clock['now'] - 500)]
                errors = []
                lab.hf_update(feed, errors)
                lab.hf_refresh(feed, errors)
                lab.hf_end_loop(1.0, errors)
        self.assertNotIn('hf_error', lab.STATE)
        self.assertEqual(container.books[hf.RND_ID]['slots']['1']['state'], hf.OPEN)
        lab.persist('online')
        full = json.loads((self.root / 'strategy_lab.json').read_text(encoding='utf-8'))
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        self.assertNotIn('high_frequency', full)
        self.assertFalse(set(full['books']) & set(hf.BOOK_IDS))
        self.assertIn('high_frequency', compact)
        self.assertEqual([book['id'] for book in compact['high_frequency']['books']], list(hf.BOOK_IDS))
        published = full['activity_config']['lab_high_frequency']
        self.assertEqual(published['config_hashes'], PINNED_CONFIG_HASHES)
        self.assertTrue(published['running'])
        self.assertIn('hf', full['persistence'])
        self.assertEqual(full['persistence']['hf']['version'], hf.VERSION)
        text = json.dumps(full)
        self.assertNotIn('"kind": "order"', text)
        self.assertTrue(any((self.root / 'strategy_lab_hf' / 'journal').rglob('*.jsonl')))

    def test_a_stop_forces_a_checkpoint(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
        container.dirty = True
        container.last_checkpoint_at = lab.now_ms()
        with patch.object(container, 'checkpoint_if_due', wraps=container.checkpoint_if_due) as checkpoint:
            lab.persist('stopped')
        self.assertTrue(checkpoint.call_args.kwargs['force'])
        self.assertFalse(container.dirty)

    def test_disabled_hf_builds_nothing_and_says_so(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '0'}):
            self.assertIsNone(lab.build_high_frequency())
            lab.persist('online')
        full = json.loads((self.root / 'strategy_lab.json').read_text(encoding='utf-8'))
        self.assertFalse(full['activity_config']['lab_high_frequency']['enabled'])
        self.assertFalse(full['activity_config']['lab_high_frequency']['running'])
        self.assertFalse((self.root / 'strategy_lab_hf').exists())

    def test_an_hf_error_is_recorded_and_never_stops_the_loop(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
        errors = []
        with patch.object(container, 'update', side_effect=RuntimeError('broken update')), \
                patch.object(container, 'on_refresh', side_effect=ValueError('broken refresh')):
            lab.hf_update([], errors)
            lab.hf_refresh([], errors)
            lab.hf_end_loop(1.0, errors)
        self.assertIn('broken update', lab.STATE['hf_error'])
        self.assertIn('broken refresh', lab.STATE['hf_error'])
        lab.hf_end_loop(1.0, [])
        self.assertNotIn('hf_error', lab.STATE)

    def test_a_lab_reset_flag_archives_the_hf_books(self):
        flag = self.root / 'strategy_lab.reset'
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}), \
                patch.object(lab, 'RESET_FLAG_PATH', flag), patch.object(lab, 'RESET_REQUESTED', False):
            container = lab.build_high_frequency()
            container.checkpoint_if_due(lab.now_ms(), force=True)
            flag.write_text('reset', encoding='utf-8')
            lab.load_state()
            self.assertTrue(lab.RESET_REQUESTED)
            container = lab.build_high_frequency()
        self.assertIsNotNone(container.load_report['archive'])

    def test_an_hf_failure_reaches_the_published_state_and_the_metrics(self):
        # Review finding: hf_error stayed in the full ledger; the compact projection that main's
        # GET /state serves dropped it, and persistence.hf had no error field, so a broken HF looked
        # healthy (running true) on /state.
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
        for loop in range(3):
            errors = []
            self.clock['now'] += 2 * SECOND
            with patch.object(container, 'update', side_effect=PermissionError('journal locked')):
                lab.hf_update([], errors)
            lab.hf_refresh([], errors)
            lab.hf_end_loop(1.0, errors)
            lab.persist('online')
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        self.assertIn('PermissionError: journal locked', compact['hf_error'])
        published = compact['persistence']['hf']['errors']
        self.assertEqual((published['count'], published['consecutive_loops']), (3, 3))
        self.assertIn('journal locked', published['last'])
        self.assertEqual(published['last_at'], self.clock['now'])
        self.assertIn('journal locked', compact['high_frequency']['last_error']['text'])
        # A checkpoint that cannot be written is reported, and the metrics are still published.
        container.dirty = True
        with patch.object(container, 'checkpoint_if_due', side_effect=PermissionError('state.json locked')):
            lab.hf_end_loop(1.0, [])
            lab.persist('online')
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        self.assertIn('checkpoint: PermissionError: state.json locked', compact['hf_error'])
        self.assertEqual(compact['persistence']['hf']['errors']['count'], 4)
        self.assertEqual(compact['persistence']['hf']['errors']['consecutive_loops'], 0)
        # A clean loop clears hf_error; the counters keep the history; the view drops the error later.
        lab.hf_end_loop(1.0, [])
        lab.persist('online')
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        self.assertNotIn('hf_error', compact)
        self.assertEqual(compact['persistence']['hf']['errors']['count'], 4)
        # The rollout check reads persistence.hf.refresh.at (the last HF refresh) for staleness.
        self.assertIn('at', compact['persistence']['hf']['refresh'])
        self.clock['now'] += hf.ERROR_VIEW_MS + SECOND
        self.assertIsNone(container.dashboard_view(self.clock['now'])['last_error'])

    def test_the_end_of_loop_flushes_the_journal_once(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
        with draws(1):
            for price in (1.0, 1.01):
                self.clock['now'] += 2 * SECOND
                feed = [make_coin(1, price, self.clock['now'] - 500)]
                errors = []
                lab.hf_update(feed, errors)
                lab.hf_refresh(feed, errors)
                pending = container.journal.pending()
                lab.hf_end_loop(1.0, errors)
                self.assertEqual(container.journal.pending(), 0)
        self.assertGreater(pending, 0)
        self.assertTrue(any((self.root / 'strategy_lab_hf' / 'journal').rglob('*.jsonl')))
        with patch.object(container, 'flush_journal', side_effect=OSError('disk full')):
            errors = []
            lab.hf_end_loop(1.0, errors)
        self.assertIn('journal: OSError: disk full', lab.STATE['hf_error'])

    def test_a_failed_build_is_retried_every_minute_and_its_error_is_published(self):
        # Review finding: main() built HF once; a failed load (a torn journal line, a locked
        # checkpoint) left HF off until a manual restart, and the dashboard showed only 'Няма HF
        # данни' although hf_error and activity_config.lab_high_frequency were on /state.
        original = hf.HighFrequencyLab.load
        failing = {'on': True}

        def load(container, reset=False):
            if failing['on']:
                raise PermissionError('state.json locked')
            return original(container, reset=reset)
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}), patch.object(hf.HighFrequencyLab, 'load', load):
            try:                                   # main(): the first build
                lab.build_high_frequency()
            except Exception as exc:
                lab.hf_build_failed(exc)
            self.assertIsNone(lab.HF)
            lab.hf_end_loop(1.0, [])
            lab.persist('online')
            compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
            self.assertEqual(compact['hf_error'], 'build: PermissionError: state.json locked')
            self.assertNotIn('high_frequency', compact)
            config = compact['activity_config']['lab_high_frequency']
            self.assertEqual((config['enabled'], config['running']), (True, False))
            self.assertEqual((config['build']['attempts'], config['build']['failures']), (1, 1))
            self.assertEqual(config['build']['last_error'], 'build: PermissionError: state.json locked')
            self.assertEqual(compact['persistence']['hf']['build']['next_attempt_at'],
                             self.clock['now'] + lab.HF_BUILD_RETRY_MS)
            self.assertFalse(compact['persistence']['hf']['loaded'])
            # Not before the retry interval; then a failed retry is counted and rescheduled.
            self.clock['now'] += lab.HF_BUILD_RETRY_MS - SECOND
            self.assertFalse(lab.hf_retry_build())
            self.assertEqual(lab.HF_BUILD['attempts'], 1)
            self.clock['now'] += SECOND
            self.assertFalse(lab.hf_retry_build())
            self.assertEqual((lab.HF_BUILD['attempts'], lab.HF_BUILD['failures']), (2, 2))
            failing['on'] = False
            self.clock['now'] += lab.HF_BUILD_RETRY_MS
            self.assertTrue(lab.hf_retry_build())
            self.assertIsNotNone(lab.HF)
            self.assertFalse(lab.hf_retry_build())             # built: nothing more to retry
            lab.hf_end_loop(1.0, [])
            lab.persist('online')
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        self.assertNotIn('hf_error', compact)
        self.assertIn('high_frequency', compact)
        self.assertTrue(compact['activity_config']['lab_high_frequency']['running'])
        build = compact['persistence']['hf']['build']
        self.assertEqual((build['attempts'], build['failures'], build['last_error']), (3, 2, None))
        self.assertEqual(build['built_at'], self.clock['now'])
        # Disabled HF is never retried.
        with patch.object(lab, 'HF', None), patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '0'}):
            lab.HF_BUILD['next_attempt_at'] = 0
            self.assertFalse(lab.hf_retry_build())

    def test_a_failed_reset_build_archives_the_hf_root_once(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            container = lab.build_high_frequency()
            container.checkpoint_if_due(lab.now_ms(), force=True)
        archive = self.root / 'strategy_lab_hf' / 'archive'
        original = hf.HighFrequencyLab.checkpoint_if_due
        calls = {'n': 0}

        def flaky(container, now, force=False):
            calls['n'] += 1
            if calls['n'] == 1:
                raise PermissionError('state.json locked')
            return original(container, now, force)
        lab.HF = None
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}), patch.object(lab, 'RESET_REQUESTED', True), \
                patch.object(hf.HighFrequencyLab, 'checkpoint_if_due', flaky):
            try:
                lab.build_high_frequency()
            except PermissionError as exc:
                lab.hf_build_failed(exc)
            self.assertIsNone(lab.HF)
            self.assertFalse(lab.RESET_REQUESTED)          # the archive was made: never again
            self.assertEqual(len(list(archive.iterdir())), 1)
            self.clock['now'] += lab.HF_BUILD_RETRY_MS
            self.assertTrue(lab.hf_retry_build())
            self.assertFalse(lab.HF.load_report['reset'])
        self.assertEqual(len(list(archive.iterdir())), 1)

    def test_the_lab_gives_hf_its_own_mark_feed_once(self):
        own = FakeMarks()
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}), patch.object(lab, 'HF_MARK_FEED', None), \
                patch.object(hf, 'new_mark_feed', return_value=own) as factory:
            first = lab.build_high_frequency()
            second = lab.build_high_frequency()
        self.assertEqual(factory.call_count, 1)
        self.assertIs(first.marks, own)
        self.assertIs(second.marks, own)
        self.assertIsNot(first.marks, lab.POSITION_MARK_FEED)
        import lab_position_marks
        feed = hf.new_mark_feed()
        try:
            self.assertIsInstance(feed, lab_position_marks.PositionMarkFeed)
            self.assertIsNot(feed, lab_position_marks.POSITION_MARK_FEED)
            self.assertEqual(feed._interval, hf.FILL.mark_request_interval_s)
        finally:
            feed._executor.shutdown(wait=True)
        with self.assertRaises(ValueError):
            hf.HighFrequencyLab(self.root / 'other_hf', cost=hf.cost_functions(lab), defense=FakeDefense(),
                                marks=lab_position_marks.POSITION_MARK_FEED)

    def test_the_rollout_universe_figure_is_published_as_a_rolling_window(self):
        with patch.dict(os.environ, {'NEO_LAB_HF_ENABLED': '1'}):
            lab.build_high_frequency()
        for price in (1.0, 1.01):
            self.clock['now'] += 2 * SECOND
            feed = [make_coin(1, price, self.clock['now'] - 500), make_coin(2, price, self.clock['now'] - 500)]
            errors = []
            lab.hf_update(feed, errors)
            lab.hf_refresh(feed, errors)
            lab.hf_end_loop(1.0, errors)
        self.clock['now'] += 2 * SECOND
        lab.hf_refresh(feed, [])                   # the same scan again: no new observation
        lab.persist('online')
        compact = json.loads((self.root / 'strategy_lab_compact.json').read_text(encoding='utf-8'))
        metrics = compact['persistence']['hf']
        self.assertEqual(metrics['refresh']['universe'], 0)
        self.assertEqual((metrics['refresh_60s']['universe'], metrics['refresh_60s']['refreshes_with_universe']), (2, 1))
        self.assertEqual(metrics['refresh_60s']['last_universe_at'], self.clock['now'] - 2 * SECOND)
        self.assertEqual(compact['high_frequency']['universe']['universe_60s'], 2)


class MarkFeedIsolationTests(unittest.TestCase):
    """Review finding: HF shared lab_position_marks.POSITION_MARK_FEED (one worker, 1 request/s, 12 s
    cache) with every Lab book, so up to 9 out-of-feed HF pools queued refreshes ahead of an existing
    Lab position's, whose resolve then returned None (stale, no stop/TP/hold evaluation)."""

    def feed(self):
        import lab_position_marks
        feed = lab_position_marks.PositionMarkFeed(interval_seconds=hf.FILL.mark_request_interval_s)
        http = BlockingHttp()
        feed._http = http

        def stop():
            feed._executor.shutdown(wait=False, cancel_futures=True)   # drop the queued refreshes
            http.release.set()                                         # then let the running one end
            feed._executor.shutdown(wait=True)
        self.addCleanup(stop)
        return feed, http

    def test_hf_refreshes_never_queue_ahead_of_a_lab_position(self):
        lab_feed, lab_http = self.feed()
        hf_feed, hf_http = self.feed()
        h = Harness(self, marks=hf_feed)
        with draws(1, 2, 3):
            h.step((1, 1.0), (2, 1.0), (3, 1.0))
            h.step((1, 1.01), (2, 1.0), (3, 1.0))       # order pool 1
            h.step((1, 1.02), (2, 1.01), (3, 1.0))      # fill 1, order 2
            h.step((1, 1.02), (2, 1.02), (3, 1.01))     # fill 2, order 3
            h.step((1, 1.02), (2, 1.02), (3, 1.02))     # fill 3
        self.assertEqual([slot['state'] for slot in h.slots()], [hf.OPEN] * 3)
        # The three held pools leave the main feed: HF asks for exact-pair marks, on its own feed.
        h.step((9, 1.0), refresh=False)
        held = {(slot['address'], slot['pairAddress']) for slot in h.slots()}
        self.assertEqual(hf_feed._pending, held)
        self.assertEqual(lab_feed._pending, set())
        # An existing Lab position out of the feed is refreshed first on the Lab's feed.
        mint, pair = ident('LabMint', 1), ident('LabPair', 1)
        self.assertIsNone(lab_feed.resolve({'address': mint, 'pairAddress': pair}, {}, h.now))
        self.assertEqual(lab_feed._pending, {(mint, pair)})
        deadline = time.monotonic() + 5
        while not lab_http.asked() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(lab_http.asked(), [pair])
        self.assertTrue(set(hf_http.asked()) <= {key[1] for key in held})
        self.assertFalse(set(lab_http.asked()) & {key[1] for key in held})
        # HF keeps resolving every loop without ever touching the Lab's feed.
        for _ in range(5):
            h.step((9, 1.0), refresh=False)
        self.assertEqual(lab_feed._pending, {(mint, pair)})
        self.assertEqual(lab_http.asked(), [pair])


class GateIsolationTests(unittest.TestCase):
    """Review finding: lab.main() builds a real HF container, so a test that runs it must never
    reach an HF root outside its temp dir, inside the gate or run alone."""

    def test_the_gate_points_the_hf_root_at_its_temp_dir_and_clears_the_hf_knobs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('run_python_checks', ROOT / 'scripts' / 'run_python_checks.py')
        checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checks)
        with tempfile.TemporaryDirectory(prefix='neo-gate-') as tmp:
            shell = {'NEO_STRATEGY_LAB_HF_DIR': r'C:\neo\live\strategy_lab_hf', 'NEO_LAB_HF_ENABLED': '0',
                     'NEO_LAB_HF_RETIRE': 'HF_QUIET_E95', 'NEO_LAB_HF_DAILY_CAP_USD': '5',
                     'NEO_LAB_HF_START_BALANCE_USD': '9', 'NEO_MAIN_MARKET_STATE_PATH': r'C:\neo\state.json'}
            env = checks.isolated_environment(Path(tmp), base=shell)
            self.assertEqual(env['NEO_STRATEGY_LAB_HF_DIR'], str(Path(tmp) / 'strategy_lab_hf'))
            for name in ('NEO_LAB_HF_ENABLED', 'NEO_LAB_HF_RETIRE', 'NEO_LAB_HF_DAILY_CAP_USD',
                         'NEO_LAB_HF_START_BALANCE_USD', 'NEO_MAIN_MARKET_STATE_PATH'):
                self.assertNotIn(name, env)
            for name, value in env.items():
                if name in checks.ISOLATED_PATHS:
                    self.assertTrue(Path(value).is_relative_to(Path(tmp)), name)

    def test_lab_main_tests_run_alone_write_no_hf_root(self):
        import subprocess
        import sys
        modules = []
        for suite in ('tests', 'backend/tests'):
            for path in sorted((ROOT / suite).glob('test_*.py')):
                text = path.read_text(encoding='utf-8')
                if path.resolve() != Path(__file__).resolve() and ('lab.main()' in text or 'strategy_lab.main()' in text):
                    modules.append((suite, path.stem))
        self.assertIn(('tests', 'test_lab_persistence_repair'), modules)
        for suite, module in modules:
            with self.subTest(module=f'{suite}/{module}'), \
                    tempfile.TemporaryDirectory(prefix='neo-sentinel-hf-') as tmp:
                sentinel = Path(tmp)
                env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(ROOT / 'backend'), NEO_ENGINE_MODE='PAPER',
                           NEO_STRATEGY_LAB_PATH=str(sentinel / 'lab' / 'strategy_lab.json'),
                           NEO_STRATEGY_LAB_COMPACT_PATH=str(sentinel / 'lab' / 'strategy_lab_compact.json'),
                           NEO_STRATEGY_LAB_HF_DIR=str(sentinel / 'hf'), NEO_LAB_HF_ENABLED='1')
                result = subprocess.run([sys.executable, '-m', 'unittest', module], cwd=ROOT / suite, env=env,
                                        capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                self.assertEqual(sorted(str(path.relative_to(sentinel)) for path in sentinel.rglob('*')), [])


# ------------------------------------------------------------------ parity with the research simulator

def fixture_observations(fixture):
    observations = []
    for pair, pool in fixture['pools'].items():
        stamp = fixture['base_ms']
        values = None
        for point in pool['points']:
            stamp += point[0]
            if len(point) > 1:
                values = point[1:]
            price, native, liquidity, cap, volume = values
            coin = {'address': pool['mint'], 'pairAddress': pair, 'dexId': 'pumpswap', 'quoteTokenAddress': SOL,
                    'symbol': pool['symbol'], 'name': pool['symbol'], 'priceUsd': price, 'priceNative': native,
                    'liquidityUsd': liquidity, 'marketCap': cap, 'updatedAt': stamp}
            if volume is not None:
                coin['volume'] = {'m5': volume}
            observations.append(coin)
    observations.sort(key=lambda coin: (coin['updatedAt'], coin['pairAddress']))
    return observations


class ResearchFlags:
    """The research's engine-guard (eg) and heat flags of each refresh event row, as the defensive layer."""

    def __init__(self, events):
        self.flags = {(pair, t): (eg, heat) for pair, rows in events.items() for t, eg, heat in rows}
        self.registry = None

    def observe(self, feed, now):
        pass

    def evaluate(self, coin, now, **kwargs):
        eg, heat = self.flags.get((coin['pairAddress'], int(coin['updatedAt'])), (False, True))
        return {'allowed': bool(eg and not heat), 'reasons': [] if eg and not heat else ['research_flags']}


class ParityTests(unittest.TestCase):
    def test_real_observations_replay_the_research_decisions(self):
        """syn_lib.run_book on the fixture's rows: every order, fill time and booked result.

        The research has no Lab clock: a 2-s Lab refresh is the bin [k x 2 s, (k + 1) x 2 s) of
        observation times (its one-order-per-refresh rule), decided at its end. Observations
        before the window only warm the HF memory (the research run started at the window).
        net50 differs by about 0.0015 pp: the Lab's net50 applies the 50 bps per leg to the booked
        result (lab_forward_tests.net50), the research inside the per-leg price penalty.
        """
        h = Harness(self, budget=dataclasses.replace(hf.DEFAULT_BUDGET, session_step_hours=0),
                    defense=ResearchFlags(FIXTURE['events']), now=FIXTURE['base_ms'])
        observations = fixture_observations(FIXTURE)
        window_start, window_end = FIXTURE['window']
        end = observations[-1]['updatedAt'] + 4 * SECOND
        index = 0
        tick = FIXTURE['base_ms']
        while tick <= end:
            now = tick + 2 * SECOND
            feed = []
            while index < len(observations) and observations[index]['updatedAt'] < now:
                feed.append(observations[index])
                index += 1
            h.now = now
            h.hf.update(lab.hf_feed_prices(feed), now, lf.feed_alive(feed, now))
            if window_start <= tick < window_end:
                h.hf.on_refresh(feed, now)
            else:
                for coin in feed:
                    h.hf.memory.observe((coin['address'], coin['pairAddress']), coin['updatedAt'], coin['priceUsd'])
            tick += 2 * SECOND
        rows = h.rows()
        total = 0
        for book_id, expected in FIXTURE['expected'].items():
            with self.subTest(book=book_id):
                orders = [row for row in rows if row['book'] == book_id and row['kind'] == 'order'
                          and row['side'] == 'entry']
                closes = {row['trade_no']: row for row in rows if row['book'] == book_id and row['kind'] == 'close'}
                self.assertEqual([(row['pairAddress'], row['decision_at']) for row in orders],
                                 [(item['pair'], item['decision_at']) for item in expected])
                for order, item in zip(orders, expected):
                    close = closes[order['trade_no']]
                    self.assertEqual((close['entry_fill_at'], close['exit_fill_at']),
                                     (item['entry_fill_at'], item['exit_fill_at']))
                    self.assertEqual(close['close_kind'], 'HF_TIME_120')
                    self.assertAlmostEqual(close['pnl_pct'], item['booked_pct'], places=4)
                    self.assertAlmostEqual(close['net0_pct'], item['net0_pct'], places=4)
                    self.assertAlmostEqual(close['net50_pct'], item['net50_pct'], delta=0.01)
                total += len(expected)
        self.assertGreaterEqual(total, 40)
        self.assertGreaterEqual(FIXTURE['counters']['HF_RND_E95']['slots'], 1)


if __name__ == '__main__':
    unittest.main()
