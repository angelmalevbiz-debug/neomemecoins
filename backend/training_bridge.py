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
import observation_journal
from paper_training import USDC, digest, number, raw_int

_BRIDGE = None
ROUTE_EVIDENCE_TTL_MS = 30_000
SOL = 'So11111111111111111111111111111111111111112'
RECENT_OBSERVATION_IDS = 8192
RECORDER_BATCH_SIZE = 128
WORKER_RESTART_MAX_SECONDS = 30
OBSERVATION_MIN_INTERVAL_MS = 3000
_NO_DEFERRED_ITEM = object()


class TrainingBridge:
    def __init__(self, root, config=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.queue = queue.Queue(maxsize=8192)
        self.cache = {}
        self.lock = threading.Lock()
        self.pending_ids = set()
        self.recent_ids = OrderedDict()
        self.last_observation_samples = {}
        self.coalesced = 0
        self.dropped = 0
        sidecar_dropped = 0
        recording_baseline = 0
        try:
            previous = json.loads((self.root / 'training.json').read_text(encoding='utf-8'))
            # Keep the producer count monotonic across parent-process restarts;
            # the worker stores a reset baseline for the current PAPER epoch.
            recording_baseline = int(number(previous.get('recording_drops_baseline')))
            self.dropped = recording_baseline + int(number(previous.get('recording_drops_total')))
            for row_id in previous.get('seen_ids', [])[-RECENT_OBSERVATION_IDS:]:
                if isinstance(row_id, str):
                    self.recent_ids[row_id] = None
        except (OSError, ValueError, TypeError):
            pass
        self.error = None
        # Queue drops must outlive a process restart. Without this small
        # sidecar, a restart could hide a coverage gap and later approve a
        # candidate against incomplete evidence.
        try:
            recorder = json.loads((self.root / 'recorder_status.json').read_text(encoding='utf-8'))
            sidecar_dropped = int(number(recorder.get('dropped_total')))
            self.dropped = max(self.dropped, sidecar_dropped)
        except (OSError, ValueError, TypeError):
            pass
        # If the older learner checkpoint knows about drops but no sidecar
        # exists yet, the writer thread will publish that count on startup.
        self._persisted_dropped = sidecar_dropped
        self.recording_drop_baseline = recording_baseline
        self.recording_drop_gap = self.dropped > recording_baseline
        if self.recording_drop_gap:
            self.error = 'Training epoch has recorded gaps; candidate promotion stays blocked until an archived PAPER reset'
        self.processed = 0
        self.latest = {'status': 'starting', 'paper_only': True}
        self.quote_probe_path = self.root / 'quote_probe_status.json'
        self.quote_probe = {'status': 'WAITING_FOR_QUALIFIED_FLOW', 'attempts': 0,
                            'successes': 0, 'last_attempt_at': 0,
                            'last_updated_at': 0, 'reason': ''}
        try:
            saved_probe = json.loads(self.quote_probe_path.read_text(encoding='utf-8'))
            if isinstance(saved_probe, dict):
                self.quote_probe.update(saved_probe)
        except (OSError, ValueError, TypeError):
            pass
        self.config = config
        self.worker_lifecycle_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.process = None
        self.worker_restart_attempts = 0
        self.worker_restart_at = 0.0
        self.worker_exit_reported_pid = None
        self.worker_snapshot_mtime_at_launch = 0
        self.closed = False
        self.shutdown_complete = False
        self.stop_event = threading.Event()
        self._deferred = _NO_DEFERRED_ITEM
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
                if self.closed:
                    return False
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
        with self.lock:
            if self.closed:
                return False
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

    def _persist_drop_count(self):
        if self.dropped <= self._persisted_dropped:
            return True
        try:
            atomic_json(self.root / 'recorder_status.json', {
                'version': 1, 'dropped_total': self.dropped,
                'updated_at': int(time.time()*1000)})
            self._persisted_dropped = self.dropped
            return True
        except Exception as exc:
            self.error = f'Cannot persist training recorder gap: {type(exc).__name__}: {exc}'
            return False

    def _write_batch(self, batch):
        """Append adjacent observations with one durable sync, preserving order."""
        encoded = ''.join(json.dumps(item, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')) + '\n' for item in batch)
        observation_journal.append(self.root / 'observations.jsonl', encoded.encode('utf-8'))
        rows = observation_journal.layout(self.root / 'observations.jsonl')
        self.journal_status = {'version': observation_journal.VERSION,
                              'parts': len(rows), 'logical_bytes': sum(n for _, _, n in rows),
                              'legacy_prefix_preserved': len(rows) > 1,
                              'part_limit_bytes': observation_journal.PART_BYTES}

    def _write_reset_boundary(self):
        if not self._persist_drop_count():
            raise RuntimeError('cannot archive training reset before recorder gap is durable')
        journal = self.root / 'observations.jsonl'
        atomic_json(self.root / 'reset.request.json', {
            'at': int(time.time()*1000),
            # Queue order gives an exact durable boundary; timestamps alone
            # cannot exclude future-dated imports.
            'journal_offset': observation_journal.total_size(journal),
            'recording_drops_total': self.dropped})
        with self.lock:
            self.cache.clear()
            self.pending_ids.clear()
            self.recent_ids.clear()
            self.last_observation_samples.clear()
            self.coalesced = 0
        self.recording_drop_baseline = self.dropped
        self.recording_drop_gap = False
        self._persisted_dropped = self.dropped
        self.error = None

    def _start_worker(self, config_path):
        snapshot = self.root / 'training_snapshot.json'
        try:
            self.worker_snapshot_mtime_at_launch = snapshot.stat().st_mtime_ns
        except OSError:
            self.worker_snapshot_mtime_at_launch = 0
        self.worker_exit_reported_pid = None
        diagnostic = (self.root / 'worker_error.log').open('ab')
        try:
            worker_python = sys.executable
            if os.name == 'nt' and sys.prefix != sys.base_prefix:
                # Windows venv python.exe is a launcher with a second process.
                # This learner uses only our source and the standard library;
                # launch its base interpreter directly so Popen owns the PID
                # holding training.process.lock, and wait really closes it.
                worker_python = getattr(sys, '_base_executable', None)
                if not worker_python or not Path(worker_python).is_file():
                    raise RuntimeError('Cannot locate the actual Windows training interpreter')
            self.process = subprocess.Popen(
                [worker_python, str(Path(__file__).with_name('training_worker.py')),
                 '--root', str(self.root.resolve()), '--config', str(config_path.resolve())],
                stdout=subprocess.DEVNULL, stderr=diagnostic,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        finally:
            diagnostic.close()

    def _ensure_worker(self, config_path):
        with self.worker_lifecycle_lock:
            self._ensure_worker_locked(config_path)

    def _ensure_worker_locked(self, config_path):
        # Shutdown sets closed before queuing its sentinel. A worker exit racing
        # with the final recorder batch must not start a fresh child behind it.
        if self.closed or self.stop_event.is_set():
            return
        if self.process is not None:
            returncode = self.process.poll()
            if returncode is None:
                snapshot = self.root / 'training_snapshot.json'
                try:
                    snapshot_ready = snapshot.stat().st_mtime_ns > self.worker_snapshot_mtime_at_launch
                except OSError:
                    snapshot_ready = False
                if snapshot_ready and self.error and self.error.startswith('Training worker'):
                    self.error = ('Training epoch has recorded gaps; candidate promotion stays blocked until an archived PAPER reset'
                                  if self.recording_drop_gap else None)
                    self.worker_restart_attempts = 0
                return
            pid = getattr(self.process, 'pid', None)
            if pid != self.worker_exit_reported_pid:
                self.worker_exit_reported_pid = pid
                self.worker_restart_attempts += 1
                delay = min(2 ** (self.worker_restart_attempts - 1), WORKER_RESTART_MAX_SECONDS)
                self.worker_restart_at = time.monotonic() + delay
                self.error = f'Training worker exited with code {returncode}; retrying in {delay}s'
        if time.monotonic() < self.worker_restart_at:
            return
        try:
            self._start_worker(config_path)
        except Exception as exc:
            self.worker_restart_attempts += 1
            delay = min(2 ** (self.worker_restart_attempts - 1), WORKER_RESTART_MAX_SECONDS)
            self.worker_restart_at = time.monotonic() + delay
            self.error = f'Training worker could not start: {type(exc).__name__}: {exc}; retrying in {delay}s'

    def run(self):
        try:
            config_path = self.root / 'worker_config.json'
            atomic_json(config_path, self.config or {})
            self._persist_drop_count()
            self._ensure_worker(config_path)
            while not self.stop_event.is_set():
                if self._deferred is not _NO_DEFERRED_ITEM:
                    item, self._deferred = self._deferred, _NO_DEFERRED_ITEM
                else:
                    try:
                        item = self.queue.get(timeout=.1)
                    except queue.Empty:
                        self._ensure_worker(config_path)
                        self.read_snapshot()
                        continue
                if item is None:
                    self.queue.task_done()
                    return
                if isinstance(item, dict) and item.get('_reset'):
                    try:
                        self._write_reset_boundary()
                        self.read_snapshot()
                    except Exception as exc:
                        self.error = f'{type(exc).__name__}: {exc}'
                    finally:
                        self.queue.task_done()
                    continue

                batch = [item]
                while len(batch) < RECORDER_BATCH_SIZE:
                    try:
                        following = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if following is None or (isinstance(following, dict) and following.get('_reset')):
                        # Handle control messages at their exact queue position.
                        # Keeping the item deferred avoids reordering around a
                        # reset boundary or shutdown sentinel.
                        self._deferred = following
                        break
                    batch.append(following)
                try:
                    # The append-only journal is durable before it enters the
                    # training process. Batch syncing keeps the reader fast
                    # without making main-bot entry/exit handling wait.
                    self._write_batch(batch)
                    self.processed += len(batch)
                    for row in batch:
                        row_id = row.get('id') if isinstance(row, dict) else None
                        self._record_persisted(row_id)
                    self._persist_drop_count()
                    self._ensure_worker(config_path)
                    self.read_snapshot()
                except Exception as exc:
                    for row in batch:
                        row_id = row.get('id') if isinstance(row, dict) else None
                        self._forget_pending(row_id)
                    self.dropped += len(batch)
                    self._persist_drop_count()
                    self.error = f'{type(exc).__name__}: {exc}'
                    self.latest = dict(self.latest, recorder={
                        'processed': self.processed, 'backlog': self.queue.qsize(),
                        'dropped': self.dropped, 'error': self.error}, status='degraded')
                finally:
                    for _ in batch:
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
        with self.close_lock:
            if self.shutdown_complete:
                return
            with self.worker_lifecycle_lock:
                with self.lock:
                    already_closed = self.closed
                    self.closed = True
            if not already_closed:
                try:
                    self.queue.put(None, timeout=.2)
                except queue.Full:
                    self.error = 'Training recorder shutdown before queue drained'
                    self.stop_event.set()
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                self.stop_event.set()
                self.thread.join(timeout=3)
            # No worker can start after closed was set under the same lock.
            process = self.process
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError('Training recorder did not stop; shutdown is incomplete')
            # Only now is the queue stable. Count any rows that the bounded
            # shutdown could not journal, including a deferred control message.
            # Never acknowledge them as persisted or erase the coverage gap.
            abandoned = 0
            if self._deferred is not _NO_DEFERRED_ITEM:
                abandoned += self._deferred is not None
                self._deferred = _NO_DEFERRED_ITEM
                self.queue.task_done()
            while True:
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                abandoned += item is not None
                self.queue.task_done()
            if abandoned:
                self.dropped += abandoned
                self.recording_drop_gap = True
                self.error = 'Training recorder shutdown before queue drained; missing observations invalidate evidence coverage'
            if not self._persist_drop_count():
                raise RuntimeError('Training shutdown coverage gap could not be saved')
            self.shutdown_complete = True

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

    @staticmethod
    def _stable_sample_value(value):
        """Remove poll timestamps while retaining point-in-time market evidence."""
        volatile = {
            'available_at', 'observed_at', 'decision_at', 'updatedat', 'updated_at',
            'checked_at', 'reference_received_at', 'quoted_at', 'last_updated_at',
            'received_at', 'timestamp', 'age_ms', 'age_seconds',
        }
        if isinstance(value, dict):
            return {key: TrainingBridge._stable_sample_value(item)
                    for key, item in value.items() if str(key).lower() not in volatile}
        if isinstance(value, list):
            return [TrainingBridge._stable_sample_value(item) for item in value]
        return value

    @staticmethod
    def _moved(current, previous, fraction):
        current, previous = number(current), number(previous)
        scale = max(abs(current), abs(previous), 1e-9)
        return abs(current - previous) / scale >= fraction

    def _coalesce_market_snapshot(self, key, stamp, coin, flow, safety, validation,
                                  quotes, reasons, known_buy, known_sell):
        """Bound repeat polls without suppressing new flow, risk, or route evidence."""
        tx5 = (coin.get('txns') or {}).get('m5') or {}
        volume = coin.get('volume') or {}
        change = coin.get('priceChange') or {}
        flow = flow or {}
        sample = {
            'at': stamp,
            'price': number(coin.get('priceUsd')),
            'liquidity': number(coin.get('liquidityUsd')),
            'market_cap': number(coin.get('marketCap') or coin.get('fdv')),
            'score': number(coin.get('score')),
            'risk_score': number(coin.get('riskScore')),
            'age_minute': int(number(coin.get('ageMinutes'))),
            'm5_change': number(change.get('m5')),
            'h1_change': number(change.get('h1')),
            'm5_volume': number(volume.get('m5')),
            'm5_buys': int(number(tx5.get('buys'))),
            'm5_sells': int(number(tx5.get('sells'))),
            'flow_latest_at': int(number(flow.get('latest_at') or flow.get('last_event_at'))),
            'flow_values': tuple(number(flow.get(name)) for name in
                                 ('trades', 'buys', 'sells', 'buy_usd', 'sell_usd',
                                  'unique_wallets', 'buy_sell_usd_ratio', 'ratio')),
            'safety': digest(self._stable_sample_value(safety or {})),
            'validation': digest(self._stable_sample_value(validation or {})),
            'quotes': digest(self._stable_sample_value(quotes or {})),
            'route_quotes': bool(quotes and any(name in quotes for name in ('entry', 'exit'))),
            'known_routes': (bool(known_buy), bool(known_sell)),
            'reasons': tuple(sorted(set(str(reason) for reason in (reasons or [])))),
        }
        with self.lock:
            if not hasattr(self, 'last_observation_samples'):
                self.last_observation_samples = {}
            previous = self.last_observation_samples.get(key)
            if previous is not None and stamp - previous['at'] < OBSERVATION_MIN_INTERVAL_MS:
                material = (
                    sample['reasons'] != previous['reasons']
                    or sample['safety'] != previous['safety']
                    or sample['validation'] != previous['validation']
                    or sample['route_quotes']
                    or sample['quotes'] != previous['quotes']
                    or sample['known_routes'] != previous['known_routes']
                    or sample['flow_latest_at'] > previous['flow_latest_at']
                    or sample['flow_values'] != previous['flow_values']
                    or (sample['m5_buys'], sample['m5_sells']) !=
                       (previous['m5_buys'], previous['m5_sells'])
                    or self._moved(sample['price'], previous['price'], .005)
                    or self._moved(sample['liquidity'], previous['liquidity'], .05)
                    or self._moved(sample['market_cap'], previous['market_cap'], .05)
                    or self._moved(sample['m5_volume'], previous['m5_volume'], .20)
                    or abs(sample['score'] - previous['score']) >= 2
                    or abs(sample['risk_score'] - previous['risk_score']) >= 5
                    or abs(sample['m5_change'] - previous['m5_change']) >= 1
                    or abs(sample['h1_change'] - previous['h1_change']) >= 2
                    or sample['age_minute'] != previous['age_minute']
                )
                if not material:
                    self.coalesced += 1
                    return True
            # Reserve the sampling slot before queue insertion. A full queue is
            # counted once per distinct sample interval instead of once per
            # half-second retry from the position guard.
            self.last_observation_samples[key] = sample
        return False

    def note_quote_probe(self, status, *, reason='', at=None, attempted=False, success=False):
        """Persist compact diagnostics for independent, read-only route checks."""
        with self.lock:
            if attempted:
                self.quote_probe['attempts'] = int(number(self.quote_probe.get('attempts'))) + 1
                self.quote_probe['last_attempt_at'] = int(at or time.time()*1000)
            if success:
                self.quote_probe['successes'] = int(number(self.quote_probe.get('successes'))) + 1
            self.quote_probe.update(status=str(status)[:80], reason=str(reason)[:120],
                                    last_updated_at=int(at or time.time()*1000))
            try:
                atomic_json(self.quote_probe_path, self.quote_probe)
            except Exception as exc:
                self.error = f'Quote probe status could not be saved: {type(exc).__name__}'

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
        if self._coalesce_market_snapshot(key, stamp, coin, normalized_flow, guard,
                                          price, quotes, reasons, known_buy, known_sell):
            return True
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


def note_quote_probe(status, **kwargs):
    return _BRIDGE.note_quote_probe(status, **kwargs) if _BRIDGE else False


def snapshot():
    if _BRIDGE:
        result = copy.deepcopy(_BRIDGE.latest)
        with _BRIDGE.lock:
            if hasattr(_BRIDGE, 'quote_probe'):
                result['quote_probe'] = copy.deepcopy(_BRIDGE.quote_probe)
        dropped_total = _BRIDGE.dropped
        dropped_baseline = getattr(_BRIDGE, 'recording_drop_baseline', 0)
        result['recorder'] = {'processed': _BRIDGE.processed, 'backlog': _BRIDGE.queue.qsize(),
                              # The dashboard's current-training gap must not
                              # inherit archived losses from before a reset.
                              'dropped': max(0, dropped_total - dropped_baseline),
                              'dropped_total': dropped_total, 'dropped_baseline': dropped_baseline,
                              'coalesced': _BRIDGE.coalesced,
                              'journal': getattr(_BRIDGE, 'journal_status', None),
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
