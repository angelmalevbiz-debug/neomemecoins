"""Causal recorded-evidence replay through the actual primary PAPER engine.

Uses Monitor.maybe_open/update_positions and State's atomic ledger. Missing raw
quote, price or safety evidence causes WAIT; chart history is never made into a
quote. Controlled adapters have no network fallback. This is separate from the
parallel experiment engine and therefore also exercises production control flow.

Every freshness window the adapters apply is derived from the constants of the
engine being replayed (see derive_windows); the adapter carries no freshness
literal of its own. A recorded row without quote evidence is a WAIT, never a
spent quote attempt, so it cannot arm the engine's quote-retry cooldown.

Exit variants (REPLAY_EXIT_VARIANT_V1) apply an alternative exit rule set to the
same recorded episodes offline. They change exit decisions only: entry sizing,
planned risk fields and every admission gate keep the baseline values, and a
variant report is always labelled as such.
"""
import copy
import math
import time
from pathlib import Path
from unittest.mock import patch

FRESHNESS_WINDOWS_VERSION = 'REPLAY_LIVE_DERIVED_WINDOWS_V1'
EXIT_VARIANT_VERSION = 'REPLAY_EXIT_VARIANT_V1'
EXIT_VARIANT_FIELDS = ('stop_pct', 'take_profit_pct', 'disable_exit_impact_emergency', 'max_hold_minutes')
# Archived engines (compare_main.py frozen arm) can predate the named preflight
# and guard constants. Only then are these documented legacy values used, and
# the report labels each window's basis so arms are never silently different.
FROZEN_ENGINE_FALLBACKS = {'preview_sell_ms': 4_000+750, 'flow_ms': 15_000}
# Live quote-stage rejection names: a recorded row carrying one of these is the
# engine's own failed quote attempt (recorded by the engine right after it),
# so replaying it may arm the quote-retry cooldown exactly as the engine did.
LIVE_QUOTE_STAGE_REJECTIONS = frozenset({'quote_inconsistent', 'invalid_quote', 'quote_age',
                                         'impact', 'roundtrip_cost', 'worst_case_cost'})
# Rejections the engine can only raise in its commit section, after the quote
# bundle was recorded. The live engine records such a rejection milliseconds
# after the evidence row, linked to the same bundle by execution.buy_verified_at;
# the evidence row and that row are one live decision (see link_commit_outcomes).
# The Jupiter price tiebreak runs after the quote bundle (market_monitor:
# price_integrity.jupiter_tiebreak on the bundle's fill price) and rejects with
# 'price_tiebreak_failed' or the unresolved review reason. These names occur
# only post-quote, so a linked row carrying one is that bundle's outcome.
PRICE_TIEBREAK_REJECTIONS = frozenset({
    'price_tiebreak_failed', 'price_source_disagreement_needs_jupiter', 'price_unavailable_needs_jupiter',
    'price_crosscheck_pending_needs_jupiter'})
COMMIT_STAGE_REJECTIONS = frozenset({
    'audit_pending', 'liquidation_unavailable', 'drawdown_limit', 'invalid_pair', 'invalid_price',
    'stale_feed', 'promoted_verified_flow_unavailable', 'promoted_verified_flow_stale',
    'promoted_buy_pressure_unconfirmed', 'winner_signal', 'loss_learning_hold', 'winner_flow_signal',
    'promoted_safety_unavailable', 'balance', 'risk_budget_unavailable', 'quote_age', 'stale_signal'}
    | PRICE_TIEBREAK_REJECTIONS)
# The clock each stage of a recorded decision is evaluated at (reported with
# the freshness windows so every age in a report has a stated reference time).
CLOCK_BASIS = {
    'entry_stage': 'entry.preflight_started_at (first preflight buy quote) for an entry-evidence row, '
                   'otherwise the row available_at',
    'preflight_consistency': 'min(row available_at, final buy raw_quote _simulated_fill_at or _received_at): '
                             'the engine runs preflight_failure right after the final buy quote lands, so a '
                             'recording lag after it is not quote age',
    'commit': 'row available_at, or the linked commit-outcome row available_at when the engine recorded one',
}


class ReplayClock:
    def __init__(self,replay):self.replay=replay
    def time(self):return self.replay.now/1000
    def gmtime(self,seconds=None):return time.gmtime(self.time() if seconds is None else seconds)
    def strftime(self,fmt,parts=None):return time.strftime(fmt,self.gmtime() if parts is None else parts)
    def __getattr__(self,name):return getattr(time,name)


def derive_windows(market):
    """Freshness windows and their basis, taken from the replayed engine's constants."""
    quotes = market.paper_quotes
    guard = getattr(market, 'promoted_guard', None)
    windows, basis = {}, {}
    windows['final_quote_ms'] = int(quotes.MAX_AGE_MS)
    basis['final_quote_ms'] = 'engine_execution.MAX_AGE_MS (signal_fresh_at_commit: now-quoted_at)'
    windows['quote_landing_ms'] = int(quotes.MAX_AGE_MS)
    basis['quote_landing_ms'] = 'engine_execution.MAX_AGE_MS (valid() rechecked after the simulated delay)'
    windows['mark_ms'] = int(quotes.MAX_AGE_MS)
    basis['mark_ms'] = 'engine_execution.MAX_AGE_MS (exit quote freshness at the mark)'
    preview = getattr(quotes, 'PREFLIGHT_PREVIEW_MAX_AGE_MS', None)
    final = getattr(quotes, 'FINAL_QUOTE_MAX_AGE_MS', None)
    if preview is not None and final is not None:
        windows['preview_sell_ms'] = int(preview)+int(final)
        basis['preview_sell_ms'] = 'engine_execution.PREFLIGHT_PREVIEW_MAX_AGE_MS + FINAL_QUOTE_MAX_AGE_MS'
    else:
        windows['preview_sell_ms'] = FROZEN_ENGINE_FALLBACKS['preview_sell_ms']
        basis['preview_sell_ms'] = 'frozen engine fallback (archived preflight literals 4000+750)'
    # The first preflight buy quote has no wall-clock age bound at commit in the
    # live engine: it must have landed fresh (quote_landing_ms), precede the
    # preview sale, and stay within the context-slot drift that the engine's own
    # consistent_preflight enforces. The adapter therefore applies none either.
    windows['preflight_buy_ms'] = None
    basis['preflight_buy_ms'] = 'no wall-clock bound: landing freshness, chronology and engine slot-drift check only'
    safety = [int(market.rug_guard.TTL_MS)]
    safety_basis = ['engine_rug_guard.TTL_MS']
    if guard is not None and hasattr(guard, 'SAFETY_MAX_AGE_MS'):
        safety.append(int(guard.SAFETY_MAX_AGE_MS))
        safety_basis.append('promoted_entry_guard.SAFETY_MAX_AGE_MS')
    windows['safety_ms'] = min(safety)
    basis['safety_ms'] = 'min(' + ', '.join(safety_basis) + ')'
    windows['price_ms'] = int(market.price_integrity.TTL_MS)
    basis['price_ms'] = 'pair_price_integrity.TTL_MS'
    if guard is not None and hasattr(guard, 'FLOW_MAX_AGE_MS'):
        windows['flow_ms'] = int(guard.FLOW_MAX_AGE_MS)
        basis['flow_ms'] = 'promoted_entry_guard.FLOW_MAX_AGE_MS (age of the recorded flow computation)'
    else:
        windows['flow_ms'] = FROZEN_ENGINE_FALLBACKS['flow_ms']
        basis['flow_ms'] = 'frozen engine fallback (archived adapter literal 15000)'
    windows['final_valuation_ms'] = int(market.entry_policy.MAX_ENTRY_QUOTE_AGE_MS)
    basis['final_valuation_ms'] = 'engine_entry_policy.MAX_ENTRY_QUOTE_AGE_MS (mark validity in update_positions)'
    signal = getattr(quotes, 'MAX_SIGNAL_AGE_MS', None)
    windows['signal_ms'] = None if signal is None else int(signal)
    basis['signal_ms'] = 'engine_execution.MAX_SIGNAL_AGE_MS (enforced by the engine at commit; informational here)'
    return windows, basis


def parse_exit_variant(text):
    """Parse 'stop_pct=8,take_profit_pct=12,disable_exit_impact_emergency=1,max_hold_minutes=30'."""
    rules = {}
    for item in str(text or '').split(','):
        item = item.strip()
        if not item:
            continue
        if '=' not in item:
            raise ValueError(f'exit variant rule {item!r} must be key=value')
        key, value = (part.strip() for part in item.split('=', 1))
        rules[key] = value
    return normalize_exit_variant(rules)


def normalize_exit_variant(rules):
    if rules is None:
        return None
    if not isinstance(rules, dict):
        raise ValueError('exit variant must be a mapping')
    unknown = sorted(set(rules)-set(EXIT_VARIANT_FIELDS))
    if unknown:
        raise ValueError(f'unknown exit variant fields {unknown}; supported: {list(EXIT_VARIANT_FIELDS)}')
    result = {}
    for key in ('stop_pct', 'take_profit_pct', 'max_hold_minutes'):
        if key in rules:
            try:
                value = float(rules[key])
            except (TypeError, ValueError):
                raise ValueError(f'exit variant {key} must be a positive number') from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'exit variant {key} must be a positive number')
            result[key] = value
    if 'disable_exit_impact_emergency' in rules:
        value = rules['disable_exit_impact_emergency']
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered not in {'1', '0', 'true', 'false', 'yes', 'no'}:
                raise ValueError('exit variant disable_exit_impact_emergency must be true/false')
            value = lowered in {'1', 'true', 'yes'}
        result['disable_exit_impact_emergency'] = bool(value)
    if not result:
        raise ValueError('exit variant must change at least one exit rule')
    return result


def variant_label(rules):
    return '_'.join(f'{key}-{str(rules[key]).lower()}' for key in EXIT_VARIANT_FIELDS if key in rules)


class MainReplay:
    def __init__(self, root, *, adaptive=False, exit_variant=None, label=None):
        import market_monitor as market
        self.market = market
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.now = 1
        self.row = {}
        self.adaptive = adaptive
        self.patches = []
        self.windows, self.window_basis = derive_windows(market)
        for name, filename in [('STATE_PATH','state.json'),('AUDIT_PATH','audit.jsonl'),('LIVE_TAPE_PATH','tape.json')]:
            self._patch(market, name, self.root/filename)
        self._patch(market, 'now_ms', lambda:self.now)
        self._patch(market, 'time', ReplayClock(self))
        self._patch(market, 'sol_usd_market_price', lambda:0.)
        self._patch(market, 'STATE', market.State())
        self.monitor = market.Monitor()
        self._patch(market.STATE, 'live_flow', self.flow)
        self._patch(market.price_integrity, 'check', self.price)
        self._patch(market.price_integrity, 'jupiter_tiebreak', self.tiebreak)
        self._patch(market.rug_guard, 'check', self.safety)
        self._patch(market.paper_quotes, 'prepare_entry', self.prepare)
        self._patch(market.paper_quotes, 'position_mark', self.mark)
        self._patch(market.pumpswap_stop, 'prepare_entry', lambda coin,n,sol,**kwargs:self.prepare(coin['address'],coin['pairAddress'],n))
        self._patch(market.pumpswap_stop, 'position_mark', self.mark)
        self._patch(market.pumpswap_stop, 'prime_positions', lambda *_:False)
        # Any accidental network access is an error, never a historical fallback.
        self._patch(market.requests.sessions.Session, 'request', self.forbid_network)
        self.baseline_exit_rules = {
            'stop_pct': market.STOP_LOSS_PCT, 'take_profit_pct': market.TAKE_PROFIT_PCT,
            'exit_impact_emergency_pct': market.EXIT_IMPACT_EMERGENCY_PCT,
            'max_hold_minutes': getattr(getattr(market, 'exit_policy', None), 'FIXED_MAX_HOLD_MINUTES', None)}
        self.exit_variant = normalize_exit_variant(exit_variant)
        self.variant_label = None
        if self.exit_variant:
            self.variant_label = str(label or variant_label(self.exit_variant))
            self._apply_exit_variant()
        # Newest exact-pool market observation available so far; the live
        # commit reads the scanner-refreshed feed, not the candidate snapshot.
        self.latest_market = {}
        self.latest_flow = {}
        self.row_at = 1
        self.stage = 'entry'
        self.commit_facts = None
        self.commit_linked_rows = 0
        self.positions_unvalued_at_end = 0
        self.replayed = 0
        self.unusable = 0
        self.decisions = []
        self.cooldown_arms_suppressed = 0
        self.rows_with_entry_evidence = 0

    def _apply_exit_variant(self):
        market = self.market
        policy = getattr(market, 'exit_policy', None)
        if policy is None or not hasattr(policy, 'exit_reason'):
            raise ValueError('this engine has no versioned exit policy; exit variants need engine_exit_policy')
        rules = self.exit_variant
        original = policy.exit_reason

        def variant_exit_reason(position, context, **kwargs):
            for key in ('stop_pct', 'take_profit_pct', 'max_hold_minutes'):
                if key in rules:
                    kwargs[key] = rules[key]
            return original(position, context, **kwargs)

        self._patch(policy, 'exit_reason', variant_exit_reason)
        if rules.get('disable_exit_impact_emergency'):
            self._patch(market, 'EXIT_IMPACT_EMERGENCY_PCT', math.inf)

    def _patch(self, target, name, value):
        p = patch.object(target, name, value)
        p.start()
        self.patches.append(p)

    @staticmethod
    def forbid_network(*args, **kwargs):
        raise RuntimeError('Network is prohibited in primary PAPER replay')

    def evidence(self):
        source = self.row.get('source') or {}
        return source.get('execution_evidence', {}) if isinstance(source, dict) else {}

    def has_engine_entry_evidence(self):
        entry = self.evidence().get('entry') or {}
        return bool(isinstance(entry, dict) and entry.get('raw_quote') and entry.get('token_raw_amount') is not None)

    def may_arm_quote_cooldown(self):
        """Only a recorded quote attempt (evidence or the engine's own quote-stage
        rejection) may arm the quote-retry cooldown; evidence-less rows are WAIT."""
        if self.has_engine_entry_evidence():
            return True
        recorded = set(self.row.get('rejection_reasons') or [])
        return bool(recorded & LIVE_QUOTE_STAGE_REJECTIONS)

    def recorded(self, stamp, window_ms):
        """True when a recorded fact is available at this row and not older than window_ms.

        Availability is bounded by the row's own availability (a fact recorded
        with the row is never in the future of that row), while the age is
        measured at the stage clock, which precedes the row for the entry-stage
        checks of an entry-evidence row.
        """
        try:
            stamp = float(stamp or 0)
        except (TypeError, ValueError):
            return False
        return 0 < stamp <= max(self.row_at, self.now) and self.now-stamp <= window_ms

    def flow(self, *_args, **_kwargs):
        """Recorded exact-pool flow as the engine saw it at this stage.

        Entry stage: the row's own flow (what the live engine evaluated before
        its quote sequence). Commit stage: the flow the live commit evaluated,
        which is the linked commit-outcome row's flow when the engine recorded
        one, otherwise the newest recorded flow computation at or before now.
        """
        flow = self.row.get('flow') or {}
        if self.stage == 'commit':
            coin = self.row.get('coin') or {}
            key = (coin.get('address'), coin.get('pairAddress'))
            if self.commit_facts:
                flow = self.commit_facts.get('flow') or {}
            else:
                flow = self.latest_flow.get(key) or flow
        flow = copy.deepcopy(flow)
        proof = flow.get('verified_flow') if isinstance(flow.get('verified_flow'), dict) else {}
        computed_at = flow.get('decision_at') or proof.get('window_at') or flow.get('latest_at') or 0
        if not self.recorded(computed_at, self.windows['flow_ms']):
            flow['quality'] = 'UNKNOWN'
            flow['fresh'] = False
            flow['verified_flow'] = None
        return flow

    def safety(self, _coin):
        proof = self.row.get('safety') or {}
        guard = copy.deepcopy(proof.get('evidence') or {})
        if not guard:
            guard = {'status':'unavailable','reasons':['recorded_safety_missing']}
        coin = self.row.get('coin') or {}
        if (guard.get('mint') != coin.get('address') or guard.get('pair') != coin.get('pairAddress')
                or not self.recorded(guard.get('checked_at'), self.windows['safety_ms'])):
            guard['status'] = 'unavailable'
        return guard

    def price(self, _coin):
        source = self.row.get('source') or {}
        proof = copy.deepcopy(source.get('price_evidence') or {}) if isinstance(source, dict) else {}
        coin = self.row.get('coin') or {}
        stamp = proof.get('reference_received_at')
        if not stamp and proof.get('jupiter_tiebreak') is True:
            # A genuine main-engine Jupiter tiebreak may have no Gecko price.
            # Recheck its saved exact-pool final quote and reported implied
            # price instead of turning a missing provider timestamp into now.
            entry=self.evidence().get('entry') or {}
            guard=(self.row.get('safety') or {}).get('evidence') or {}
            try:
                decimals=guard['metrics']['decimals']
                if type(decimals) is not int or not 0<=decimals<=18:
                    raise ValueError('decimals unknown')
                if (not self.raw_quote_valid(entry.get('raw_quote'),self.market.paper_quotes.USDC,coin.get('address'),entry['input_usdc_raw'],
                                             max_age_ms=self.windows['final_quote_ms'])
                        or not self.market.paper_quotes.same_token_pool(entry['raw_quote'],coin.get('address'),coin.get('pairAddress'))):
                    raise ValueError('quote identity unknown')
                implied=int(entry['input_usdc_raw'])/1e6/(int(entry['token_raw_amount'])/(10**decimals))
                observed=float(proof['observed_price'])
                if (not observed>0 or not math.isclose(float(proof['jupiter_entry_price']),implied,rel_tol=1e-9,abs_tol=1e-9)
                        or abs(implied/observed-1)*100>3):
                    raise ValueError('tiebreak price mismatch')
                stamp=entry.get('quoted_at')
            except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError):
                stamp=None
        if (proof.get('mint') != coin.get('address') or proof.get('pair') != coin.get('pairAddress')
                or not self.recorded(stamp, self.windows['price_ms'])):
            return {'status':'unavailable','reason':'recorded_price_provenance_missing'}
        return proof

    def tiebreak(self, validation, *_args, **_kwargs):
        """Jupiter tiebreak after the quote bundle.

        When the engine recorded this bundle's tiebreak rejection (a linked
        commit-outcome row), that recorded outcome is the live decision.
        Otherwise only the recorded price evidence is available.
        """
        recorded = sorted(set((self.commit_facts or {}).get('reasons') or []) & PRICE_TIEBREAK_REJECTIONS)
        if recorded:
            outcome = copy.deepcopy(validation) if isinstance(validation, dict) else {}
            outcome.update(status='blocked', reason=recorded[0], recorded_commit_outcome=True)
            return outcome
        return self.price(None)

    def raw_quote_valid(self, raw, input_mint, output_mint, amount, *, max_age_ms):
        """Recorded raw quote identity, quantity and chronology.

        A quote must have landed within the engine's quote validity window and
        before the replay clock; max_age_ms is the role-specific wall-clock
        bound at the recorded row (None when the live engine applies none).
        """
        if not isinstance(raw, dict):
            return False
        try:
            def quantity(value):
                if isinstance(value,bool) or not isinstance(value,(str,int)) or isinstance(value,str) and not value.isdigit():
                    raise ValueError('invalid raw quantity')
                return int(value)
            stamp = int(raw.get('_received_at') or raw.get('available_at') or 0)
            observed=int(raw.get('_observed_at') or stamp)
            landed=int(raw.get('_simulated_fill_at') or observed)
            output=quantity(raw.get('outAmount'))
            floor=quantity(raw.get('otherAmountThreshold'))
            # A quote recorded with this row landed no later than the row's
            # availability; its age is measured at the stage clock.
            return (raw.get('inputMint') == input_mint and raw.get('outputMint') == output_mint
                    and quantity(raw.get('inAmount')) == quantity(amount) and 0 < floor <= output
                    and raw.get('swapMode') == 'ExactIn' and bool(raw.get('routePlan'))
                    and 0 < stamp <= observed <= landed <= max(self.row_at, self.now)
                    and landed-stamp <= self.windows['quote_landing_ms']
                    and (max_age_ms is None or self.now-stamp <= max_age_ms))
        except (ValueError, TypeError, OverflowError):
            return False

    def prepare_clock(self):
        """Recorded time of the live preflight consistency check.

        The engine evaluates preflight_failure right after the final buy quote
        landed (its _simulated_fill_at); the evidence row is written later. The
        chain is therefore checked at min(row, final landing), never at the
        recording time, so a recording lag cannot turn into quote age.
        """
        entry = self.evidence().get('entry') or {}
        raw = entry.get('raw_quote') if isinstance(entry, dict) else None
        if not isinstance(raw, dict):
            return self.row_at
        try:
            received = int(raw.get('_received_at') or raw.get('available_at') or 0)
            landed = int(raw.get('_simulated_fill_at') or received)
        except (TypeError, ValueError, OverflowError):
            return self.row_at
        return min(self.row_at, landed) if landed > 0 else self.row_at

    def prepare(self, mint, pair, notional, *_args, **_kwargs):
        # The live quote sequence consumed the time between the entry-stage
        # checks and the final quote landing; the preflight chain is checked
        # at that recorded clock and the commit at the recorded row.
        self.now = max(self.now, self.prepare_clock())
        try:
            prepared = self._prepare_checked(mint,pair,notional)
        except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError):
            prepared = None
        self.now = max(self.now, self.row_at)
        if prepared is not None:
            self.stage = 'commit'
            if self.commit_facts:
                # The engine recorded this bundle's commit rejection a few ms
                # after the evidence row; the commit happened at that time.
                try:
                    committed_at = int(self.commit_facts.get('available_at') or 0)
                except (TypeError, ValueError):
                    committed_at = 0
                if committed_at >= self.row_at:
                    self.row_at = self.now = committed_at
            coin = self.row.get('coin') or {}
            # The live commit reads the scanner-refreshed feed, never the
            # candidate snapshot that started the quote sequence.
            self.market.STATE.feed = [self._current_market(coin)]
        return prepared

    def _prepare_checked(self, mint, pair, notional):
        evidence = self.evidence()
        entry, sale = evidence.get('entry'), evidence.get('exit')
        coin = self.row.get('coin') or {}
        windows = self.windows
        if (not entry or not sale or mint != coin.get('address') or pair != coin.get('pairAddress')
                or int(entry.get('input_usdc_raw') or 0) != int(round(notional*1e6))
                or not 0 <= self.now-float(entry.get('quoted_at') or 0) <= windows['final_quote_ms']
                or not 0 <= self.now-float(sale.get('quoted_at') or 0) <= windows['preview_sell_ms']):
            return None
        usdc=self.market.paper_quotes.USDC
        if not self.raw_quote_valid(entry.get('raw_quote'),usdc,mint,entry['input_usdc_raw'],max_age_ms=windows['final_quote_ms']):
            return None
        if not self.market.paper_quotes.same_token_pool(entry['raw_quote'],mint,pair):
            return None
        initial=entry.get('preflight_buy_quote')
        preflight_sale=entry.get('preflight_sell_quote')
        if bool(initial) != bool(preflight_sale):
            return None
        if initial:
            # Production sells the FIRST quoted quantity in preflight, then
            # buys at a new final quote. The preview is a conservative estimate
            # adjusted DOWN when the final quantity decreased, not an exact
            # final-quantity sell quote and never a retrospectively cheap fill.
            if not self.raw_quote_valid(initial,usdc,mint,entry['input_usdc_raw'],max_age_ms=windows['preflight_buy_ms']):
                return None
            if not self.market.paper_quotes.same_token_pool(initial,mint,pair):
                return None
            buffer=int(entry.get('assumed_buffer_bps',getattr(self.market.paper_quotes,'BUFFER_BPS',10)))
            if not 0<=buffer<10000:
                return None
            first_amount=int(initial['outAmount'])*(10000-buffer)//10000
            final_amount=int(entry['raw_quote']['outAmount'])*(10000-buffer)//10000
            if int(entry['token_raw_amount']) != final_amount:
                return None
            if preflight_sale != sale.get('raw_quote') or not self.raw_quote_valid(preflight_sale,mint,usdc,first_amount,max_age_ms=windows['preview_sell_ms']):
                return None
            first_at=int(initial.get('_received_at') or initial.get('available_at') or 0)
            sale_at=int(preflight_sale.get('_received_at') or preflight_sale.get('available_at') or 0)
            final_at=int(entry['raw_quote'].get('_received_at') or entry['raw_quote'].get('available_at') or 0)
            if (not first_at <= int(initial.get('_simulated_fill_at') or first_at) <= sale_at
                    or not sale_at <= int(preflight_sale.get('_simulated_fill_at') or sale_at) <= final_at
                    or int(entry['quoted_at']) != final_at or int(sale['quoted_at']) != sale_at):
                return None
            first={'token_raw_amount':first_amount,'input_usdc_raw':int(initial['inAmount']),
                   'quoted_at':first_at,'context_slot':initial.get('contextSlot')}
            provider_expected=int(preflight_sale['outAmount'])/1e6
            native_sale={'quoted_at':sale_at,'provider_expected_usdc':provider_expected}
            final={**entry,'context_slot':entry['raw_quote'].get('contextSlot')}
            checker=getattr(self.market.paper_quotes,'consistent_preflight',None)
            if checker is not None and not checker(first,native_sale,final,now=self.now):
                return None
            if checker is None:
                # Frozen engines without consistent_preflight: the archived
                # preflight thresholds apply (quantity drift, final age, preview
                # age, positive-return bound), labelled as frozen fallbacks.
                quotes=self.market.paper_quotes
                drift=getattr(quotes,'PREFLIGHT_MAX_QUANTITY_DRIFT',.005)
                final_max=getattr(quotes,'FINAL_QUOTE_MAX_AGE_MS',750)
                preview_max=getattr(quotes,'PREFLIGHT_PREVIEW_MAX_AGE_MS',4000)
                if (abs(final_amount/first_amount-1)>drift
                        or not 0<=self.now-final_at<=final_max
                        or not 0<=final_at-sale_at<=preview_max
                        or provider_expected>notional*1.001):
                    return None
            adjustment=min(1.,final_amount/first_amount)
            expected=provider_expected*(1-buffer/10000)*adjustment
            floor=int(preflight_sale['otherAmountThreshold'])/1e6*adjustment
            if (sale.get('is_preflight_estimate') is not True
                    or not math.isclose(float(entry.get('preflight_quantity_adjustment')),adjustment,rel_tol=1e-12,abs_tol=1e-12)
                    or not math.isclose(float(sale['expected_usdc']),expected,rel_tol=1e-9,abs_tol=1e-8)
                    or not math.isclose(float(sale['floor_usdc']),floor,rel_tol=1e-9,abs_tol=1e-8)
                    or not math.isclose(float(sale['provider_expected_usdc']),provider_expected,rel_tol=1e-9,abs_tol=1e-8)):
                return None
        else:
            # Compatibility with a saved exact-quantity/simple legacy fixture;
            # no scaling or preflight facts are inferred if they were not saved.
            if not self.raw_quote_valid(sale.get('raw_quote'),mint,usdc,entry['token_raw_amount'],max_age_ms=windows['preview_sell_ms']):
                return None
            if float(sale['quoted_at'])>float(entry['quoted_at']):
                return None
        return copy.deepcopy((entry,sale))

    def mark(self, position, _coin, _network, *_args, **_kwargs):
        q = self.evidence().get('mark')
        if not q or not 0 <= self.now-float(q.get('quoted_at') or 0) <= self.windows['mark_ms']:
            return None
        coin=self.row.get('coin') or {}
        if coin.get('address') != position.get('address') or coin.get('pairAddress') != position.get('pairAddress'):
            return None
        raw = q.get('token_input_raw')
        if raw is None or int(raw) != int(position.get('jupiter_token_raw_amount') or 0):
            return None
        if not self.raw_quote_valid(q.get('raw_quote'),position['address'],self.market.paper_quotes.USDC,raw,max_age_ms=self.windows['mark_ms']):
            return None
        # This is recorded simulated execution evidence, never an on-chain fill.
        return copy.deepcopy(q)

    def is_position_guard_tick(self):
        """Only a recorded position-guard tick values a position.

        The live guard records a mark row for every sell quote it obtained and
        an 'exit_quote' rejection row when the quote failed. Scanner snapshots
        and entry rejections between those ticks are not valuation attempts;
        treating them as failed quotes would mark the held position unavailable
        and block every later entry with liquidation_unavailable.
        """
        if self.evidence().get('mark'):
            return True
        return 'exit_quote' in set(self.row.get('rejection_reasons') or [])

    def _tick_positions(self, coin):
        """Tick only the position whose exact pool this row records.

        One live guard tick quotes every open position, but each recorded row
        carries at most one pool's mark. Other positions keep their state: they
        are deferred past this row's clock instead of being read as failed
        sell quotes, and their own mark rows value them.
        """
        state = self.market.STATE
        key = (coin.get('address'), coin.get('pairAddress'))
        deferred = {}
        with state.lock:
            for position in state.positions:
                if (position.get('address'), position.get('pairAddress')) != key:
                    deferred[position.get('id')] = position.get('next_exit_retry_at')
                    position['next_exit_retry_at'] = self.now+1
        try:
            self.monitor.update_positions({key: coin})
        finally:
            with state.lock:
                for position in state.positions:
                    if position.get('id') in deferred:
                        position['next_exit_retry_at'] = deferred[position.get('id')]

    def _current_market(self, coin):
        """Newest exact-pool observation at or before the replay clock.

        The live entry commit reads STATE.feed, which the scanner refreshes
        between candidate selection and commit; the recorded candidate snapshot
        is older. Later rows never leak in: only observations already ingested
        with a source timestamp at or before now are considered.
        """
        key = (coin.get('address'), coin.get('pairAddress'))
        stamp = coin.get('updatedAt')
        try:
            stamp = float(stamp)
        except (TypeError, ValueError):
            stamp = 0.
        current = self.latest_market.get(key)
        if 0 < stamp <= self.now:
            try:
                previous = float((current or {}).get('updatedAt') or 0)
            except (TypeError, ValueError):
                previous = 0.
            if current is None or stamp >= previous:
                current = copy.deepcopy(coin)
                self.latest_market[key] = current
        return copy.deepcopy(current) if current is not None else copy.deepcopy(coin)

    def entry_stage_clock(self, row, at):
        """The live entry-stage checks ran before the quote sequence began."""
        source = row.get('source') or {}
        entry = ((source.get('execution_evidence') or {}) if isinstance(source, dict) else {}).get('entry') or {}
        if not isinstance(entry, dict) or entry.get('token_raw_amount') is None:
            return at
        try:
            started = int(entry.get('preflight_started_at') or (entry.get('preflight_buy_quote') or {}).get('_received_at') or 0)
        except (TypeError, ValueError):
            return at
        return started if 0 < started <= at else at

    def _remember_flow(self, row, at):
        flow = row.get('flow')
        coin = row.get('coin') or {}
        if not isinstance(flow, dict):
            return
        try:
            computed_at = float(flow.get('decision_at') or 0)
        except (TypeError, ValueError):
            return
        key = (coin.get('address'), coin.get('pairAddress'))
        previous = self.latest_flow.get(key)
        try:
            previous_at = float((previous or {}).get('decision_at') or 0)
        except (TypeError, ValueError):
            previous_at = 0.
        if 0 < computed_at <= at and (previous is None or computed_at >= previous_at):
            self.latest_flow[key] = copy.deepcopy(flow)

    @staticmethod
    def link_commit_outcomes(rows):
        """Map each entry-evidence row index to the engine's recorded commit rejection.

        After recording a quote bundle the live engine either commits (the next
        same-pool rows are a scanner snapshot, marks or 'cooldown') or rejects
        in its commit section and records that rejection milliseconds later
        with execution.buy_verified_at/sell_verified_at equal to the bundle's
        quote times. That linked row is the same live decision; its flow is the
        commit-time flow the engine evaluated.
        """
        linked = {}
        for index, row in enumerate(rows):
            source = row.get('source') or {}
            evidence = (source.get('execution_evidence') or {}) if isinstance(source, dict) else {}
            entry, sale = evidence.get('entry') or {}, evidence.get('exit') or {}
            if not isinstance(entry, dict) or entry.get('token_raw_amount') is None or not entry.get('raw_quote'):
                continue
            coin = row.get('coin') or {}
            key = (coin.get('address'), coin.get('pairAddress'))
            try:
                bundle = (int(entry.get('quoted_at') or 0), int((sale or {}).get('quoted_at') or 0))
            except (TypeError, ValueError):
                continue
            for candidate in rows[index+1:]:
                later = candidate.get('coin') or {}
                if (later.get('address'), later.get('pairAddress')) != key:
                    continue
                later_source = candidate.get('source') or {}
                later_evidence = (later_source.get('execution_evidence') or {}) if isinstance(later_source, dict) else {}
                if later_evidence.get('mark') or later_evidence.get('entry'):
                    break
                reasons = set(candidate.get('rejection_reasons') or [])
                if not reasons:
                    continue
                execution = candidate.get('execution') or {}
                try:
                    verified = (int(execution.get('buy_verified_at') or 0), int(execution.get('sell_verified_at') or 0))
                except (TypeError, ValueError):
                    verified = (0, 0)
                if reasons <= COMMIT_STAGE_REJECTIONS and verified == bundle:
                    linked[index] = candidate
                break
        return linked

    def ingest(self, row, commit_outcome=None):
        at = int(row.get('available_at') or 0)
        observed = int(row.get('observed_at') or 0)
        if not 0 < observed <= at or at < self.now:
            self.unusable += 1
            return False
        self.row, self.row_at = copy.deepcopy(row), at
        self.now = self.entry_stage_clock(self.row, at)
        self.stage = 'entry'
        self.commit_facts = None
        if commit_outcome is not None:
            self.commit_facts = {'flow': copy.deepcopy(commit_outcome.get('flow') or {}),
                                 'available_at': commit_outcome.get('available_at'),
                                 'reasons': list(commit_outcome.get('rejection_reasons') or [])}
            self.commit_linked_rows += 1
        coin = copy.deepcopy(row.get('coin') or {})
        if not coin.get('address') or not coin.get('pairAddress'):
            self.now = at
            self.unusable += 1
            return False
        market = self.market
        self._remember_flow(row, at)
        current = self._current_market(coin)
        market.STATE.feed = [current]
        if hasattr(market.STATE,'position_market'):
            market.STATE.position_market[f'{coin["address"]}:{coin["pairAddress"]}'] = current
        if self.has_engine_entry_evidence():
            self.rows_with_entry_evidence += 1
        # Archived engines (compare_main.py frozen arm) may predate the cooldown.
        cooldowns = getattr(self.monitor, 'entry_quote_retry_after', None)
        retry_before = dict(cooldowns) if isinstance(cooldowns, dict) else None
        trades_before = len(market.STATE.history)
        if self.is_position_guard_tick():
            self._tick_positions(coin)
        opened_before = {p.get('id') for p in market.STATE.positions}
        self.monitor.maybe_open([coin])
        if (retry_before is not None and not self.may_arm_quote_cooldown()
                and self.monitor.entry_quote_retry_after != retry_before):
            # No quote was spent on this row in the recording; a WAIT must not
            # block the next recorded quote attempt with a synthetic cooldown.
            self.monitor.entry_quote_retry_after = retry_before
            self.cooldown_arms_suppressed += 1
        self.now = at
        if self.adaptive:
            for p in market.STATE.positions:
                p['exit_policy'] = 'adaptive'
        market.STATE.save()
        diagnostics = getattr(market.STATE, 'entry_diagnostics', None) or {}
        self.decisions.append({
            'available_at': at, 'id': row.get('id'),
            'opened': bool({p.get('id') for p in market.STATE.positions}-opened_before),
            'closed': len(market.STATE.history) > trades_before,
            'rejections': dict(diagnostics.get('rejections') or {}),
            'recorded_rejection_reasons': list(row.get('rejection_reasons') or []),
            'entry_evidence': self.has_engine_entry_evidence(),
            'commit_outcome_linked': commit_outcome is not None,
        })
        self.replayed += 1
        return True

    def decision_summary(self):
        counts = {}
        recorded_rows = agreeing = 0
        for decision in self.decisions:
            for reason in decision['rejections']:
                counts[reason] = counts.get(reason, 0)+1
            recorded = set(decision['recorded_rejection_reasons'])
            if recorded:
                recorded_rows += 1
                if recorded == set(decision['rejections']):
                    agreeing += 1
        return {'rows': len(self.decisions), 'rows_with_entry_evidence': self.rows_with_entry_evidence,
                'rows_opened': sum(d['opened'] for d in self.decisions),
                'rows_closed': sum(d['closed'] for d in self.decisions),
                'rejection_counts': dict(sorted(counts.items())),
                'recorded_rejection_rows': recorded_rows,
                'recorded_rejection_rows_reproduced': agreeing,
                'cooldown_arms_suppressed': self.cooldown_arms_suppressed,
                'commit_outcome_rows_linked': self.commit_linked_rows,
                'positions_unvalued_at_end': self.positions_unvalued_at_end}

    def exit_variant_report(self):
        if not self.exit_variant:
            return {'is_baseline': True, 'label': 'baseline', 'version': EXIT_VARIANT_VERSION,
                    'rules': {}, 'effective_exit_rules': dict(self.baseline_exit_rules)}
        effective = dict(self.baseline_exit_rules)
        for key in ('stop_pct', 'take_profit_pct', 'max_hold_minutes'):
            if key in self.exit_variant:
                effective[key] = self.exit_variant[key]
        if self.exit_variant.get('disable_exit_impact_emergency'):
            effective['exit_impact_emergency_pct'] = None
        return {'is_baseline': False, 'label': self.variant_label, 'version': EXIT_VARIANT_VERSION,
                'rules': dict(self.exit_variant), 'baseline_exit_rules': dict(self.baseline_exit_rules),
                'effective_exit_rules': effective,
                'scope': 'exit decisions only; entry admission, sizing, planned_stop_net_pct and '
                         'planned_risk_usd keep the baseline rules; a max_hold_minutes override labels its '
                         'fixed-policy hold exit MAX_HOLD_<limit>, all other exit labels are unchanged',
                'profitability_proven': False}

    def finalize_open_positions(self):
        """A position held past its last recorded mark has no executable valuation.

        Recorded marks exist only while the live engine held the position. When
        the replay still holds one at the end and its last mark is older than
        the engine's own mark validity window, the engine is ticked without a
        quote so it reports unknown valuation (full-loss risk), never a modeled
        value past the last recorded sell quote.
        """
        state = self.market.STATE
        for position in list(state.positions):
            try:
                marked_at = float(position.get('execution_quote_at') or position.get('updated_at') or 0)
            except (TypeError, ValueError):
                marked_at = 0.
            if 0 <= self.now-marked_at <= self.windows['final_valuation_ms']:
                continue
            key = f"{position.get('address')}:{position.get('pairAddress')}"
            coin = copy.deepcopy(state.position_market.get(key) or position.get('coin_snapshot') or
                                 {'address': position.get('address'), 'pairAddress': position.get('pairAddress')})
            self.row = {'coin': coin, 'flow': {}, 'safety': {}, 'source': {}}
            self.stage = 'entry'
            self.commit_facts = None
            self._tick_positions(coin)
            self.positions_unvalued_at_end += 1
        if self.positions_unvalued_at_end:
            state.save()

    def replay(self, observations):
        ordered = [row for _, row in sorted(enumerate(observations), key=lambda item:(item[1].get('available_at',0),item[0]))]
        linked = self.link_commit_outcomes(ordered)
        for index, row in enumerate(ordered):
            self.ingest(row, commit_outcome=linked.get(index))
        self.finalize_open_positions()
        try:
            snapshot = self.market.STATE.snapshot()
        except NameError:
            # The frozen HEAD has a documented /state NameError. Preserve its
            # executed decision/account path; report the limited raw ledger scope.
            state=self.market.STATE
            trades=state.history
            snapshot={'stats':{'closed_trades':len(trades),
                'wins':sum(t.get('pnl_usd',0)>0 for t in trades),
                'demo_balance_usd':state.demo_balance_usd,
                'metric_scope':'legacy retained history; not lifetime',
                'snapshot_defect':'undefined live_quote in frozen HEAD'},
                'positions':copy.deepcopy(state.positions),'history':copy.deepcopy(trades)}
        variant = self.exit_variant_report()
        return {'engine':'ACTUAL_PRIMARY_PAPER_PATH',
                'report_kind':'BASELINE' if variant['is_baseline'] else 'EXIT_VARIANT',
                'exit_policy':'adaptive' if self.adaptive else 'fixed',
                'exit_variant':variant,
                'freshness_windows':{'version':FRESHNESS_WINDOWS_VERSION,'windows':dict(self.windows),
                                     'basis':dict(self.window_basis),'clock_basis':dict(CLOCK_BASIS)},
                'records':self.replayed,'invalid_records':self.unusable,
                'decision_summary':self.decision_summary(),
                'coverage_note':'Only recorded exact-quantity raw quotes are executable; sparse snapshots cannot reconstruct missing history',
                # Export the actual ledger, not the public UI's compact recent
                # rows, so replay evidence and raw quantities remain auditable.
                'stats':snapshot['stats'],'positions':copy.deepcopy(self.market.STATE.positions),
                'history':copy.deepcopy(self.market.STATE.history),
                'history_scope':'all history retained by the selected engine; frozen engines may have a documented retention cap'}

    def close(self):
        self.monitor.stop()
        for p in reversed(self.patches):
            p.stop()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
