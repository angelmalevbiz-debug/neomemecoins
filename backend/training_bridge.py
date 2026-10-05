"""Shared, bounded, asynchronous PAPER observation channel; never calls an API.

Import is inert. The main process explicitly starts the worker. Account exits do
not await training, disk writes, candidate evaluation, or learning. Missing proof
is recorded as a rejection rather than converted to an executable opportunity.
"""
import copy
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from engine_runtime import atomic_json
from paper_training import USDC, digest, number, raw_int

_BRIDGE = None
ROUTE_EVIDENCE_TTL_MS = 30_000
SOL = 'So11111111111111111111111111111111111111112'
RECENT_OBSERVATION_IDS = 8192


class TrainingBridge:
    def __init__(self, root, config=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.queue = queue.Queue(maxsize=4096)
        self.cache = {}
        self.lock = threading.Lock()
        self.pending_ids = set()
        self.recent_ids = OrderedDict()
        self.coalesced = 0
        self.dropped = 0
        try:
            previous = json.loads((self.root / 'training.json').read_text(encoding='utf-8'))
            # Keep the producer count monotonic across parent-process restarts;
            # the worker stores a reset baseline for the current PAPER epoch.
            self.dropped = int(number(previous.get('recording_drops_baseline'))) + int(number(previous.get('recording_drops_total')))
            for row_id in previous.get('seen_ids', [])[-RECENT_OBSERVATION_IDS:]:
                if isinstance(row_id, str):
                    self.recent_ids[row_id] = None
        except (OSError, ValueError, TypeError):
            pass
        self.error = None
        self.processed = 0
        self.latest = {'status': 'starting', 'paper_only': True}
        self.config = config
        self.process = None
        self.closed = False
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='paper-training', daemon=True)
        self.thread.start()

    def submit(self, observation):
        if self.closed:
            return False
        payload = copy.deepcopy(observation)
        row_id = payload.get('id') if isinstance(payload, dict) and not payload.get('_reset') else None
        if row_id:
            # Monitor paths can report the same exact coin/flow/proof several
            # times in one millisecond. Coalesce only identical content hashes;
            # new evidence and distinct proof updates retain their own rows.
            # Queue insertion and pending registration share the lock so the
            # writer cannot finish before the ID becomes visible to producers.
            with self.lock:
                if row_id in self.pending_ids or row_id in self.recent_ids:
                    self.coalesced += 1
                    return True
                self.pending_ids.add(row_id)
                try:
                    self.queue.put_nowait(payload)
                except queue.Full:
                    self.pending_ids.discard(row_id)
                    self.dropped += 1
                    self.error = 'Training queue full; missing observations invalidate evidence coverage'
                    return False
            return True
        try:
            self.queue.put_nowait(payload)
            return True
        except queue.Full:
            self.dropped += 1
            self.error = 'Training queue full; missing observations invalidate evidence coverage'
            return False

    def _record_persisted(self, row_id):
        if not row_id:
            return
        with self.lock:
            self.pending_ids.discard(row_id)
            self.recent_ids[row_id] = None
            self.recent_ids.move_to_end(row_id)
            while len(self.recent_ids) > RECENT_OBSERVATION_IDS:
                self.recent_ids.popitem(last=False)

    def _forget_pending(self, row_id):
        if row_id:
            with self.lock:
                self.pending_ids.discard(row_id)

    def run(self):
        try:
            config_path = self.root / 'worker_config.json'
            atomic_json(config_path, self.config or {})
            with (self.root / 'worker_error.log').open('ab') as diagnostic:
                self.process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name('training_worker.py')),
                     '--root', str(self.root.resolve()), '--config', str(config_path.resolve())],
                    stdout=subprocess.DEVNULL, stderr=diagnostic,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            while not self.stop_event.is_set():
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    self.read_snapshot()
                    continue
                row_id = item.get('id') if isinstance(item, dict) and not item.get('_reset') else None
                try:
                    if item is None:
                        return
                    if item.get('_reset'):
                        journal = self.root / 'observations.jsonl'
                        atomic_json(self.root / 'reset.request.json', {
                            'at': int(time.time()*1000),
                            # Queue ordering gives an exact durable boundary;
                            # timestamps alone cannot exclude future-dated imports.
                            'journal_offset': journal.stat().st_size if journal.exists() else 0,
                            'recording_drops_total': self.dropped})
                        with self.lock:
                            self.cache.clear()
                            self.pending_ids.clear()
                            self.recent_ids.clear()
                            self.coalesced = 0
                        self.error = None
                    else:
                        # Persist provenance before evaluating. Accepted observations are
                        # a replayable shared stream, not a count of independent trades.
                        with (self.root / 'observations.jsonl').open('a', encoding='utf-8') as handle:
                            handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False)+'\n')
                            handle.flush()
                            os.fsync(handle.fileno())
                        self.processed += 1
                        self._record_persisted(row_id)
                    self.read_snapshot()
                except Exception as exc:
                    self._forget_pending(row_id)
                    self.error = f'{type(exc).__name__}: {exc}'
                    self.latest = dict(self.latest, recorder={
                        'processed': self.processed, 'backlog': self.queue.qsize(),
                        'dropped': self.dropped, 'error': self.error}, status='degraded')
                finally:
                    self.queue.task_done()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            self.latest = {'status': 'error', 'paper_only': True, 'error': self.error}

    def read_snapshot(self):
        if self.process is not None and self.process.poll() is not None:
            self.error = f'Training worker exited with code {self.process.returncode}'
        path = self.root / 'training_snapshot.json'
        if path.exists():
            for attempt in range(5):
                try:
                    self.latest = json.loads(path.read_text(encoding='utf-8'))
                    if self.error == 'Unreadable training worker snapshot':
                        self.error = None
                    return
                except (OSError, ValueError):
                    if attempt == 4:
                        self.error = 'Unreadable training worker snapshot'
                    else:
                        # Atomic replace can briefly contend with a Windows
                        # reader. Retry before degrading the primary dashboard.
                        time.sleep(.01)

    def close(self):
        self.closed = True
        try:
            self.queue.put(None, timeout=.2)
        except queue.Full:
            self.dropped += self.queue.qsize()
            self.error = 'Training recorder shutdown before queue drained'
            self.stop_event.set()
        self.thread.join(timeout=3)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    @staticmethod
    def quote_at(quote, stamp):
        """Route proof may remain valid for 30s; this is NEVER a fill timestamp."""
        if not isinstance(quote, dict):
            return 0
        try:
            at = int(quote.get('quoted_at') or 0)
            return at if 0 < at <= stamp and stamp-at <= ROUTE_EVIDENCE_TTL_MS else 0
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def quote_identity(quote, mint, pair, side):
        """Attest only recorded mint/pool proof; never infer it from a label."""
        if not isinstance(quote, dict):
            return False
        raw = quote.get('raw_quote')
        if isinstance(raw, dict):
            if raw.get('inputMint') != (USDC if side == 'buy' else mint) or raw.get('outputMint') != (mint if side == 'buy' else USDC):
                return False
            legs = [leg.get('swapInfo') for leg in raw.get('routePlan') or []
                    if isinstance(leg, dict) and isinstance(leg.get('swapInfo'), dict)
                    and mint in (leg['swapInfo'].get('inputMint'), leg['swapInfo'].get('outputMint'))]
            # The liquidity model prices THIS scanner pool. A main-engine exit
            # on another AMM remains legitimate, but cannot prove this pool's
            # liquidity model is executable. Retain it only as source evidence.
            return bool(legs) and all(leg.get('ammKey') == pair for leg in legs)
        # Validated pool-RPC marks carry explicit identity instead of a Jupiter
        # quote. A quantity-only object provides insufficient identity evidence.
        return quote.get('address') == mint and quote.get('pairAddress') == pair

    @staticmethod
    def _costs(coin, guard, entry, sale, stamp):
        """Forecast explicit model costs from recorded evidence, never quote fills.

        The scanner's SOL/USD ratio is a timestamped budget reference, not a
        token->SOL->USDC conversion. Missing reference/rent proof stays unknown.
        Jupiter fee observations can inform an assumed rate only when EVERY
        route leg provides a supported fee asset and integer fee quantity.
        """
        metrics = guard.get('metrics') or {}
        fresh_coin = 0 < number(coin.get('updatedAt')) <= stamp and stamp-number(coin.get('updatedAt')) <= ROUTE_EVIDENCE_TTL_MS
        sol_reference = None
        if fresh_coin and coin.get('quoteTokenAddress') in (None, SOL):
            usd, native = number(coin.get('priceUsd')), number(coin.get('priceNative'))
            if usd > 0 and native > 0: sol_reference = usd/native
        # A separately stamped quote-asset reference is also admissible.
        reference_at = number(metrics.get('sol_usd_observed_at'))
        if sol_reference is None and 0 < reference_at <= stamp and stamp-reference_at <= ROUTE_EVIDENCE_TTL_MS:
            if number(metrics.get('sol_usd')) > 0: sol_reference = number(metrics['sol_usd'])
        network_sol = number(os.getenv('NEO_EXEC_NETWORK_FEE_SOL', '0.0001'), math.nan)
        if not math.isfinite(network_sol) or network_sol < 0: raise ValueError('invalid configured network budget')
        network_budget = max(.03,network_sol*sol_reference) if sol_reference is not None else None

        def observed_fee(quote, names):
            for name in names:
                if name in quote:
                    fee = number(quote[name], math.nan)
                    return fee if math.isfinite(fee) and fee >= 0 else None
            return None

        entry_network = observed_fee(entry,('entry_network_fee_usd','network_fee_usd'))
        exit_network = observed_fee(sale,('exit_network_fee_usd','network_fee_usd'))
        if network_budget is not None:
            entry_network = max(network_budget,entry_network or 0)
            exit_network = max(network_budget,exit_network or 0)
        observed_main_reserve = observed_fee(entry,('entry_account_reserve_usd',))
        if observed_main_reserve is None:
            observed_main_reserve = observed_fee(metrics,('entry_account_reserve_usd',))
        rent_lamports = number(metrics.get('token_account_rent_lamports'), math.nan)
        rent_reference = rent_lamports/1e9*sol_reference if math.isfinite(rent_lamports) and rent_lamports > 0 and sol_reference is not None else None
        reserves = [value for value in (observed_main_reserve,rent_reference) if value is not None]
        reserve = max(reserves) if reserves else None
        # Independent books do not inherit the main account's existing ATA.
        # Charge a conservative first-account reserve on EACH modeled entry;
        # inventory/recovery is unknown. Preserve the actual main cost separately.
        if reserve == 0 and rent_reference is None: reserve = None
        decimals = metrics.get('decimals')
        fallback = 125. if str(coin.get('dexId') or '').lower() == 'pumpswap' else number(os.getenv('NEO_EXEC_GENERIC_DEX_FEE_BPS','30'),math.nan)
        fee_samples = {}
        fee_evidence = []
        for side, quote in (('buy',entry),('sell',sale)):
            raw = quote.get('raw_quote') or {}
            legs = raw.get('routePlan') or []
            quoted_at = TrainingBridge.quote_at(quote,stamp)
            if not legs or not quoted_at: continue
            try:
                amount = raw_int(raw['inAmount'])
                input_usd = amount/1e6 if side == 'buy' else amount/(10**decimals)*number(coin.get('priceUsd'))
                if input_usd <= 0: continue
                total_fee_usd = 0.
                observations = []
                for leg in legs:
                    swap = leg['swapInfo']
                    fee_raw = raw_int(swap['feeAmount'])
                    if fee_raw < 0: raise ValueError('negative fee')
                    fee_mint = swap['feeMint']
                    if fee_mint == USDC: value = fee_raw/1e6
                    elif fee_mint == SOL and sol_reference is not None: value = fee_raw/1e9*sol_reference
                    elif fee_mint == coin['address'] and isinstance(decimals,int) and not isinstance(decimals,bool):
                        value = fee_raw/(10**decimals)*number(coin.get('priceUsd'))
                    else: raise ValueError('unknown fee asset')
                    total_fee_usd += value
                    observations.append({'mint':fee_mint,'raw_amount':fee_raw,'estimated_usd':value})
                platform = raw.get('platformFee')
                if platform:
                    bps = number(platform.get('feeBps'),math.nan)
                    if not math.isfinite(bps) or bps < 0: raise ValueError('unknown platform fee')
                    total_fee_usd += input_usd*bps/10000
                bps = total_fee_usd/input_usd*10000
                if not math.isfinite(bps) or not 0 <= bps <= 500: continue
                fee_samples[side] = bps
                fee_evidence.append({'side':side,'quoted_at':quoted_at,'fee_bps_estimate':bps,'legs':observations})
            except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError):
                continue
        entry_dex_fee = fee_samples.get('buy',fallback)
        exit_dex_fee = fee_samples.get('sell',fallback)
        # One observed side cannot make an unobserved opposite side free.
        fee_bps = max(entry_dex_fee,exit_dex_fee)
        if not math.isfinite(fee_bps) or not 0 <= fee_bps <= 500: raise ValueError('invalid model AMM fee assumption')
        known = entry_network is not None and exit_network is not None and reserve is not None
        return {'network_fee_usd': max(entry_network,exit_network) if entry_network is not None and exit_network is not None else None,
                'entry_network_fee_usd':entry_network,'exit_network_fee_usd':exit_network,
                'entry_account_reserve_usd':reserve,'dex_fee_bps':fee_bps,
                'observed_main_entry_account_reserve_usd':observed_main_reserve,
                'entry_dex_fee_bps':entry_dex_fee,'exit_dex_fee_bps':exit_dex_fee,'costs_known':known,
                'fee_basis':('RECORDED_ROUTE_FEE_ESTIMATE_V1' if len(fee_samples)==2 else 'PARTIAL_ROUTE_FEE_ESTIMATE_WITH_UNKNOWN_SIDE_ASSUMPTION' if fee_samples else 'VERSIONED_AMM_FEE_ASSUMPTION_NOT_GUARANTEED_BOUND'),
                'network_fee_basis':'RECORDED_FEE_AND_CONFIGURED_SOL_BUDGET_ESTIMATE',
                'account_reserve_basis':'MAX_MAIN_COST_AND_FIRST_ACCOUNT_RENT_PER_ENTRY_NO_INVENTORY_OR_RECOVERY',
                'sol_usd_reference':sol_reference,'sol_reference_at':coin.get('updatedAt') if fresh_coin and sol_reference is not None else reference_at,
                'fee_observations':fee_evidence}

    def observe(self, coin, flow, **kwargs):
        try:
            return self._observe(coin, flow, **kwargs)
        except Exception as exc:
            # Recording problems must not interrupt a primary liquidation.
            self.dropped += 1
            self.error = f'Observation refused: {type(exc).__name__}: {exc}'
            return False

    def _observe(self, coin, flow, *, safety=None, validation=None, quotes=None,
                reasons=None, context=None, now=None):
        stamp = int(now if now is not None else time.time()*1000)
        mint, pair = coin.get('address'), coin.get('pairAddress')
        if not mint or not pair:
            return False
        key = (mint, pair)
        with self.lock:
            cached = self.cache.setdefault(key, {})
            if safety is not None:
                cached['safety'] = copy.deepcopy(safety)
            if validation is not None:
                cached['validation'] = copy.deepcopy(validation)
                # Preserve when the reference became available. Re-reading a
                # cached pass result is not a fresh independent verification.
                cached['price_checked_at'] = validation.get('reference_received_at') or 0
            if quotes:
                entry, sale, mark = quotes.get('entry'), quotes.get('exit'), quotes.get('mark')
                entry_at = self.quote_at(entry, stamp) if self.quote_identity(entry, mint, pair, 'buy') else 0
                sale_at = self.quote_at(sale, stamp) if self.quote_identity(sale, mint, pair, 'sell') else 0
                if 'entry' in quotes:
                    cached['entry'] = copy.deepcopy(entry) if entry_at else {}
                    cached['buy_verified_at'] = entry_at
                    if entry_at and validation is not None and validation.get('jupiter_tiebreak'):
                        cached['price_checked_at'] = entry_at
                if 'exit' in quotes:
                    cached['sale'] = copy.deepcopy(sale) if sale_at else {}
                    cached['sell_verified_at'] = sale_at
                    cached['sell_route'] = bool(sale_at)
                mark_at = self.quote_at(mark, stamp) if self.quote_identity(mark, mint, pair, 'sell') else 0
                if 'mark' in quotes:
                    cached['sale'] = copy.deepcopy(mark) if mark_at else {}
                    cached['sell_route'] = bool(mark_at)
                    cached['sell_verified_at'] = mark_at
            if 'exit_quote' in (reasons or []):
                cached['sell_route'] = False
            if any(reason in ('entry_quote','quote_inconsistent','invalid_quote') for reason in (reasons or [])):
                cached['buy_verified_at'] = 0
                if 'quote_inconsistent' in (reasons or []): cached['sell_route'] = False
            proof = copy.deepcopy(cached)
        guard = proof.get('safety') or {}
        price = proof.get('validation') or {}
        entry = proof.get('entry') or {}
        buy_at = int(number(proof.get('buy_verified_at')))
        sale_at = int(number(proof.get('sell_verified_at')))
        known_buy = bool(entry and 0 < buy_at <= stamp and stamp-buy_at <= ROUTE_EVIDENCE_TTL_MS)
        known_sell = bool(proof.get('sale') and proof.get('sell_route') and 0 < sale_at <= stamp and stamp-sale_at <= ROUTE_EVIDENCE_TTL_MS)
        valid_times = ([buy_at] if known_buy else [])+([sale_at] if known_sell else [])
        route_time = min(valid_times) if valid_times else 0
        checked = int(number(guard.get('checked_at')))
        normalized_flow = copy.deepcopy(flow or {})
        normalized_flow['ratio'] = normalized_flow.get('buy_sell_usd_ratio', normalized_flow.get('ratio', 0))
        # Zero timestamps remain unknown; never make a stale flow fresh by stamping it.
        normalized_flow['latest_at'] = normalized_flow.get('latest_at', normalized_flow.get('last_event_at', 0))
        allowed = guard.get('status') == 'pass' and not guard.get('provisional_early')
        known_price = (price.get('status') == 'pass' and price.get('mint') == mint and price.get('pair') == pair
                       and 0 <= stamp-number(proof.get('price_checked_at')) <= 30000)
        # The model uses market price+liquidity, not quote output. Its explicit fee
        # assumption is documented, and therefore cannot double-charge quote fees.
        costs = self._costs(coin,guard,entry,proof.get('sale') or {},stamp)
        observation = {
            'available_at': stamp, 'observed_at': stamp,
            'coin': copy.deepcopy(coin), 'flow': normalized_flow, 'context': copy.deepcopy(context or {}),
            'safety': {'allowed': allowed, 'checked_at': checked, 'mint': guard.get('mint'),
                       'pair': guard.get('pair'), 'evidence': guard},
            'execution': {'mode': 'RECORDED_LIQUIDITY_MODEL', 'mint': mint, 'pair': pair,
                          'decimals': (guard.get('metrics') or {}).get('decimals'),
                          **costs,
                          'buy_route': known_buy, 'sell_route': known_sell,
                          'buy_verified_at':buy_at,'sell_verified_at':sale_at,
                          'verified_at': route_time, 'price_verified': known_price,
                          'route_evidence_ttl_ms':ROUTE_EVIDENCE_TTL_MS,
                          'route_evidence_is_execution_quote':False},
            'quotes': [], 'rejection_reasons': list(reasons or []),
            'source': {'kind': 'MAIN_SHARED_READ_ONLY_OBSERVATION',
                       'execution_evidence': copy.deepcopy(quotes or {}),
                       'cached_route_evidence':{'buy':entry,'sell':proof.get('sale') or {}},
                       'price_evidence': price},
            'recording_drops_total': self.dropped,
        }
        # Two distinct safety/quote updates in one millisecond must not collide.
        observation['id'] = digest(observation)
        return self.submit(observation)


def start(root=None, config=None):
    global _BRIDGE
    if _BRIDGE is None:
        root = root or os.getenv('NEO_TRAINING_ROOT', '.runtime/training')
        _BRIDGE = TrainingBridge(root, config=config)
    return _BRIDGE


def observe(coin, flow, **kwargs):
    return _BRIDGE.observe(coin, flow, **kwargs) if _BRIDGE else False


def enabled():
    return _BRIDGE is not None


def snapshot():
    if _BRIDGE:
        result = copy.deepcopy(_BRIDGE.latest)
        result['recorder'] = {'processed': _BRIDGE.processed, 'backlog': _BRIDGE.queue.qsize(),
                              'dropped': _BRIDGE.dropped, 'coalesced': _BRIDGE.coalesced,
                              'error': _BRIDGE.error}
        if _BRIDGE.error:
            result['status'] = 'degraded'
        return result
    return {'status': 'disabled', 'paper_only': True, 'reason': 'Worker starts only in main()'}


def request_reset():
    return _BRIDGE.submit({'_reset': True}) if _BRIDGE else False


def stop():
    global _BRIDGE
    if _BRIDGE:
        _BRIDGE.close()
        _BRIDGE = None
