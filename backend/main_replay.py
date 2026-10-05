"""Causal recorded-evidence replay through the actual primary PAPER engine.

Uses Monitor.maybe_open/update_positions and State's atomic ledger. Missing raw
quote, price or safety evidence causes WAIT; chart history is never made into a
quote. Controlled adapters have no network fallback. This is separate from the
parallel experiment engine and therefore also exercises production control flow.
"""
import copy
import math
import time
from pathlib import Path
from unittest.mock import patch


class ReplayClock:
    def __init__(self,replay):self.replay=replay
    def time(self):return self.replay.now/1000
    def gmtime(self,seconds=None):return time.gmtime(self.time() if seconds is None else seconds)
    def strftime(self,fmt,parts=None):return time.strftime(fmt,self.gmtime() if parts is None else parts)
    def __getattr__(self,name):return getattr(time,name)


class MainReplay:
    def __init__(self, root, *, adaptive=False):
        import market_monitor as market
        self.market = market
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.now = 1
        self.row = {}
        self.adaptive = adaptive
        self.patches = []
        for name, filename in [('STATE_PATH','state.json'),('AUDIT_PATH','audit.jsonl'),('LIVE_TAPE_PATH','tape.json')]:
            self._patch(market, name, self.root/filename)
        self._patch(market, 'now_ms', lambda:self.now)
        self._patch(market, 'time', ReplayClock(self))
        self._patch(market, 'sol_usd_market_price', lambda:0.)
        self._patch(market, 'STATE', market.State())
        self.monitor = market.Monitor()
        self._patch(market.STATE, 'live_flow', self.flow)
        self._patch(market.price_integrity, 'check', self.price)
        self._patch(market.price_integrity, 'jupiter_tiebreak', lambda *args:self.price(None))
        self._patch(market.rug_guard, 'check', self.safety)
        self._patch(market.paper_quotes, 'prepare_entry', self.prepare)
        self._patch(market.paper_quotes, 'position_mark', self.mark)
        self._patch(market.pumpswap_stop, 'prepare_entry', lambda coin,n,sol,**kwargs:self.prepare(coin['address'],coin['pairAddress'],n))
        self._patch(market.pumpswap_stop, 'position_mark', self.mark)
        self._patch(market.pumpswap_stop, 'prime_positions', lambda *_:False)
        # Any accidental network access is an error, never a historical fallback.
        self._patch(market.requests.sessions.Session, 'request', self.forbid_network)
        self.replayed = 0
        self.unusable = 0

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

    def flow(self, *_args, **_kwargs):
        flow = copy.deepcopy(self.row.get('flow') or {})
        if not 0 <= self.now-float(flow.get('latest_at') or 0) <= 15000:
            flow['quality'] = 'UNKNOWN'
        return flow

    def safety(self, _coin):
        proof = self.row.get('safety') or {}
        guard = copy.deepcopy(proof.get('evidence') or {})
        if not guard:
            guard = {'status':'unavailable','reasons':['recorded_safety_missing']}
        coin = self.row.get('coin') or {}
        if (guard.get('mint') != coin.get('address') or guard.get('pair') != coin.get('pairAddress')
                or not 0 <= self.now-float(guard.get('checked_at') or 0) <= 30000):
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
                if (not self.raw_quote_valid(entry.get('raw_quote'),self.market.paper_quotes.USDC,coin.get('address'),entry['input_usdc_raw'])
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
                or not stamp or not 0 <= self.now-float(stamp) <= 30000):
            return {'status':'unavailable','reason':'recorded_price_provenance_missing'}
        return proof

    def raw_quote_valid(self, raw, input_mint, output_mint, amount):
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
            return (raw.get('inputMint') == input_mint and raw.get('outputMint') == output_mint
                    and quantity(raw.get('inAmount')) == quantity(amount) and 0 < floor <= output
                    and raw.get('swapMode') == 'ExactIn' and bool(raw.get('routePlan'))
                    and 0 < stamp <= observed <= landed <= self.now and self.now-stamp <= 4000)
        except (ValueError, TypeError, OverflowError):
            return False

    def prepare(self, mint, pair, notional):
        try:
            return self._prepare_checked(mint,pair,notional)
        except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError):
            return None

    def _prepare_checked(self, mint, pair, notional):
        evidence = self.evidence()
        entry, sale = evidence.get('entry'), evidence.get('exit')
        coin = self.row.get('coin') or {}
        if (not entry or not sale or mint != coin.get('address') or pair != coin.get('pairAddress')
                or int(entry.get('input_usdc_raw') or 0) != int(round(notional*1e6))
                or not 0 <= self.now-float(entry.get('quoted_at') or 0) <= 2000
                or not 0 <= self.now-float(sale.get('quoted_at') or 0) <= 4000):
            return None
        usdc=self.market.paper_quotes.USDC
        if not self.raw_quote_valid(entry.get('raw_quote'),usdc,mint,entry['input_usdc_raw']):
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
            if not self.raw_quote_valid(initial,usdc,mint,entry['input_usdc_raw']):
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
            if preflight_sale != sale.get('raw_quote') or not self.raw_quote_valid(preflight_sale,mint,usdc,first_amount):
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
            if checker is None and (abs(final_amount/first_amount-1)>.005
                                    or not 0<=self.now-final_at<=750
                                    or not 0<=final_at-sale_at<=4000
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
            if not self.raw_quote_valid(sale.get('raw_quote'),mint,usdc,entry['token_raw_amount']):
                return None
            if float(sale['quoted_at'])>float(entry['quoted_at']):
                return None
        return copy.deepcopy((entry,sale))

    def mark(self, position, _coin, _network, *_args, **_kwargs):
        q = self.evidence().get('mark')
        if not q or not 0 <= self.now-float(q.get('quoted_at') or 0) <= 2000:
            return None
        coin=self.row.get('coin') or {}
        if coin.get('address') != position.get('address') or coin.get('pairAddress') != position.get('pairAddress'):
            return None
        raw = q.get('token_input_raw')
        if raw is None or int(raw) != int(position.get('jupiter_token_raw_amount') or 0):
            return None
        if not self.raw_quote_valid(q.get('raw_quote'),position['address'],self.market.paper_quotes.USDC,raw):
            return None
        # This is recorded simulated execution evidence, never an on-chain fill.
        return copy.deepcopy(q)

    def ingest(self, row):
        at = int(row.get('available_at') or 0)
        observed = int(row.get('observed_at') or 0)
        if not 0 < observed <= at or at < self.now:
            self.unusable += 1
            return False
        self.row, self.now = copy.deepcopy(row), at
        coin = copy.deepcopy(row.get('coin') or {})
        if not coin.get('address') or not coin.get('pairAddress'):
            self.unusable += 1
            return False
        market = self.market
        market.STATE.feed = [coin]
        if hasattr(market.STATE,'position_market'):
            market.STATE.position_market[f'{coin["address"]}:{coin["pairAddress"]}'] = coin
        self.monitor.update_positions({(coin['address'],coin['pairAddress']):coin})
        self.monitor.maybe_open([coin])
        if self.adaptive:
            for p in market.STATE.positions:
                p['exit_policy'] = 'adaptive'
        market.STATE.save()
        self.replayed += 1
        return True

    def replay(self, observations):
        for _, row in sorted(enumerate(observations), key=lambda item:(item[1].get('available_at',0),item[0])):
            self.ingest(row)
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
        return {'engine':'ACTUAL_PRIMARY_PAPER_PATH','exit_variant':'adaptive' if self.adaptive else 'fixed',
                'records':self.replayed,'invalid_records':self.unusable,
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
