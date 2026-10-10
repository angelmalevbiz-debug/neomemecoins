"""Independent, prospective PAPER micro-momentum book. Never a LIVE executor.

Own capital/ledger/marks, no changes to funded books or their frozen exits.
Observed momentum is a hypothesis, not an estimate of future profit.
"""
import copy
import hashlib
import json
import math
import os
from dataclasses import replace

import entry_defense
import funded_active_paper as active
import pool_loss_memory
import promoted_entry_guard as guard
import structural_rug_guard as rug
from lab_position_marks import fresh_exact_coin, mark_observed_at

VERSION = 'PAPER_FAST_SCALP_V1_250_5M'
ENV = 'NEO_LAB_FAST_SCALP_ENABLED'
CAPITAL = 1000.0
NOTIONAL = 250.0
COST_CAP_PCT = 1.5
STOP_USD = 7.5
TARGET_USD = 5.0
ARM_USD = 2.0
GIVEBACK_USD = .75
HOLD_MS = 300_000
DELAY_MS = 2000
PRICE_TTL_MS = 30_000
EXPECTED_COST_MODEL = dict(execution_model='DEX_SPOT_MODELED_COSTS_V3_VERIFIED_SOL_DENOMINATION',
    generic_dex_fee_bps=30.0, base_slippage_bps=10.0, latency_buffer_bps=10.0,
    network_fee_sol=.0001, max_price_impact_pct=20.0)


def paper_mode():
    return os.getenv('NEO_ENGINE_MODE') == os.getenv('NEO_EXECUTION_MODE') == 'PAPER'


def enabled():
    return paper_mode() and os.getenv(ENV) == '1'


def config():
    return dict(version=VERSION, starting_capital_usd=CAPITAL, notional_usd=NOTIONAL,
        max_roundtrip_cost_pct=COST_CAP_PCT, take_profit_net_usd=TARGET_USD,
        stop_loss_net_usd=STOP_USD, profit_arm_net_usd=ARM_USD,
        profit_giveback_usd=GIVEBACK_USD, max_hold_seconds=HOLD_MS//1000,
        minimum_fill_delay_ms=DELAY_MS, price_reference_max_age_ms=PRICE_TTL_MS,
        signal_window_seconds=60, observed_move_over_cost_buffer_pct=.25,
        maximum_observed_move_pct=3.0, winning_pool_cooldown_seconds=30,
        losing_pool_cooldown_seconds=300, max_provider_candidates_per_refresh=1,
        market_screen='FUNDED_V9_MOMENTUM', ticker_reuse_log_only=False,
        heat_log_only=False, real_execution_enabled=False, profitability_proven=False,
        cost_model=dict(EXPECTED_COST_MODEL),
        execution_basis='DEX_SPOT_MODELED_COSTS_V3_VERIFIED_SOL_DENOMINATION',
        independent_capital=True, automatic_topup=False, forced_fill=False)


CONFIG_HASH = hashlib.sha256(json.dumps(config(), sort_keys=True).encode()).hexdigest()


def number(value, default=math.nan):
    return active.finite(value, default)


def pulse(coin, history, now, cost_pct):
    """Reference must have actually been available 60s ago in this segment."""
    record = history.view(coin)
    end = min(now, number(coin.get('updatedAt'), 0))
    target = end-60_000
    if (not record or number(record.get('since'), math.inf) > target
            or not 0 <= now-number(record.get('last_seen'), 0) <= 12_000):
        return None
    reference = next(((t, p) for t, p, _ in reversed(record['samples'])
                      if record['since'] <= t <= target), None)
    if not reference or target-reference[0] > 45_000 or reference[1] <= 0:
        return None
    move = (number(coin.get('priceUsd'))/reference[1]-1)*100
    if not math.isfinite(move) or not cost_pct+.25 <= move < 3.0:
        return None
    return dict(reference_at=reference[0], reference_price=reference[1],
        observed_at=end, observed_price=coin['priceUsd'], window_seconds=60,
        observed_return_pct=move, required_return_pct=cost_pct+.25,
        future_return_predicted=False)


class FastScalpLab:
    def __init__(self, path, *, write, entry, exit, defense, marks, risk, price, clock, cost_model):
        if cost_model != EXPECTED_COST_MODEL:
            raise ValueError('Fast-scalp cost model mismatch; refusing mixed evidence')
        self.path, self.write = path, write
        self.entry, self.exit = entry, exit
        self.defense, self.marks = defense, marks
        self.risk, self.price, self.clock = risk, price, clock
        self.state = None
        self.error = None
        self.load()

    def load(self):
        if self.path.exists():
            state = json.loads(self.path.read_text(encoding='utf-8'))
            if (state.get('version') != VERSION or state.get('config_hash') != CONFIG_HASH
                    or state.get('starting_balance') != CAPITAL
                    or not isinstance(state.get('positions'), list)
                    or not isinstance(state.get('history'), list)
                    or not isinstance(state.get('funding_event'), dict)
                    or state['funding_event'].get('amount_usd') != CAPITAL
                    or not math.isfinite(number(state.get('balance')))
                    or state['balance'] < 0):
                raise ValueError('Unrecognized fast-scalp ledger; refusing reset or credit')
            # Committed cash remains in balance until close, just like funded Lab.
            expected = CAPITAL+sum(number(t.get('pnl_usd')) for t in state['history'])
            if not math.isfinite(expected) or abs(state['balance']-expected) > .00001:
                raise ValueError('Fast-scalp cash does not reconcile')
            if any(p.get('entry_policy_version') != VERSION for p in state['positions']):
                raise ValueError('Unknown fast-scalp open lot')
            self.state = state
        else:
            if not enabled():
                raise ValueError('No authorization to create a PAPER experiment')
            now = self.clock()
            self.state = dict(version=VERSION, config_hash=CONFIG_HASH,
                starting_balance=CAPITAL, balance=CAPITAL, trade_seq=0,
                created_at=now, updated_at=now, positions=[], history=[], pending_entry=None,
                funding_event=dict(id=VERSION+'_INITIAL_VIRTUAL_CAPITAL', at=now,
                    amount_usd=CAPITAL, kind='PAPER_EXPERIMENT_INITIAL_CAPITAL',
                    real_money=False, profit=False, transfer_from_funded_books=False))
            self.write(self.path, self.state)  # Durable credit before any admission.
        self.error = None

    def free_cash(self):
        return self.state['balance']-sum(p['capital_committed_usd'] for p in self.state['positions'])

    def blocked_pool(self, coin, now):
        same = lambda p: (p.get('address') == coin.get('address')
                          or p.get('pairAddress') == coin.get('pairAddress'))
        if any(same(p) for p in self.state['positions']):
            return 'fast_pool_already_open'
        for trade in reversed(self.state['history']):
            if same(trade):
                pause = 300_000 if trade['pnl_usd'] <= 0 else 30_000
                if now-trade['closed_at'] < pause:
                    return 'fast_pool_cooldown'
                break
        return None

    def defense_check(self, coin, now):
        return self.defense.evaluate(coin, now,
            blocked_pools=pool_loss_memory.index(self.state['history'], now),
            structural_parameters=replace(rug.PARAMS, young_pool_max_age_minutes=2,
                                          ticker_registry_min_coverage_minutes=60),
            heat_log_only=False, ticker_reuse_log_only=False)

    def plan(self, coin, features, now):
        if not fresh_exact_coin(coin, coin.get('address'), coin.get('pairAddress'), now):
            return None, 'fast_market_stale'
        flow = guard.flow_admission(coin, features, now)
        if not flow['allow']:
            return None, flow['reason']
        reason = self.blocked_pool(coin, now)
        if reason:
            return None, reason
        defensive = self.defense_check(coin, now)
        if not defensive['allowed']:
            return None, (defensive.get('reasons') or ['defensive_entry'])[0]
        opening = self.entry(coin, NOTIONAL)
        marked = self.exit(coin, opening['quantity'])
        committed = number(opening.get('capital_committed_usd'))
        friction = (committed-number(marked.get('net_proceeds_usd')))/NOTIONAL*100
        if not math.isfinite(friction) or not 0 <= friction <= COST_CAP_PCT:
            return None, 'fast_roundtrip_cost'
        if not math.isfinite(committed) or committed < NOTIONAL or committed > self.free_cash():
            return None, 'fast_cash_unavailable'
        signal = pulse(coin, self.defense.history, now, friction)
        if signal is None:
            return None, 'fast_move_does_not_cover_cost'
        return dict(opening=opening, marked=marked, friction=friction,
                    signal=signal, defensive=defensive), None

    def open_pending(self, coins, flows, now):
        pending = self.state.get('pending_entry')
        if not pending or now < pending['signal_at']+DELAY_MS:
            return
        self.state['pending_entry'] = None
        coin = coins.get((pending['address'], pending['pairAddress']))
        if not coin or now-pending['signal_at'] > 12_000 or mark_observed_at(coin) < pending['signal_at']+DELAY_MS:
            self.reject('fast_pending_signal_expired')
            return
        features = flows.get((coin['address'], coin['pairAddress'])) or {}
        plan, reason = self.plan(coin, features, now)
        if reason:
            self.reject(reason)
            return
        risk = self.risk(coin)
        valid = guard.risk_admission(coin, risk, self.clock(), guard.SAFETY_MAX_AGE_MS)
        if not valid['allow']:
            self.reject(valid['reason'])
            return
        price = self.price(coin)
        commit = self.clock()
        if (price.get('status') != 'pass' or price.get('mint') != coin['address']
                or price.get('pair') != coin['pairAddress']
                or not 0 <= commit-number(price.get('reference_received_at'), 0) <= PRICE_TTL_MS):
            self.reject('fast_independent_price_unavailable')
            return
        # Provider latency may expire flow, safety, signal, cash or defense.
        plan, reason = self.plan(coin, features, commit)
        if reason or not guard.risk_admission(coin, risk, commit, guard.SAFETY_MAX_AGE_MS)['allow']:
            self.reject(reason or 'promoted_safety_unavailable')
            return
        opening, marked = plan['opening'], plan['marked']
        self.state['trade_seq'] += 1
        self.state['positions'].append(dict(trade_no=self.state['trade_seq'],
            strategy_id='FAST_SCALP', symbol=coin.get('symbol'), address=coin['address'],
            pairAddress=coin['pairAddress'], opened_at=commit, entry_signal_at=pending['signal_at'],
            entry_policy_version=VERSION, config_hash=CONFIG_HASH, execution_mode='PAPER',
            notional_usd=NOTIONAL, quantity=opening['quantity'],
            capital_committed_usd=opening['capital_committed_usd'],
            entry_price=opening['fill_price'], current_price=coin['priceUsd'],
            entry_network_fee_usd=opening['network_fee_usd'], entry_dex_fee_usd=opening['dex_fee_usd'],
            entry_roundtrip_cost_pct=plan['friction'], verified_entry_flow=copy.deepcopy(features['verified_flow']),
            risk_guard=copy.deepcopy(risk), price_crosscheck=copy.deepcopy(price),
            defensive_entry=entry_defense.compact(plan['defensive']), micro_signal=plan['signal'],
            exit_parameters=config(), pending_exit=None, profit_floor_usd=None,
            peak_net_usd=marked['net_proceeds_usd']-opening['capital_committed_usd'],
            open_pnl_usd=marked['net_proceeds_usd']-opening['capital_committed_usd'],
            quote_status='fresh', mark_received_at=mark_observed_at(coin)))

    def update(self, coins, now):
        for pos in list(self.state['positions']):
            coin = self.marks.resolve(pos, coins, now)
            if (not fresh_exact_coin(coin, pos['address'], pos['pairAddress'], now, 12_000)
                    or mark_observed_at(coin) < max(pos['opened_at'], pos['mark_received_at'])):
                pos['quote_status'] = 'stale'
                continue  # Never close at an invented price, not even on timeout.
            quote = self.exit(coin, pos['quantity'])
            net = number(quote.get('net_proceeds_usd'))-pos['capital_committed_usd']
            if not math.isfinite(net):
                raise ValueError('Nonfinite fast-scalp exit')
            pos.update(current_price=coin['priceUsd'], open_pnl_usd=net,
                quote_status='fresh', mark_received_at=mark_observed_at(coin))
            pending = pos.get('pending_exit')
            if (pending and now >= pending['signal_at']+DELAY_MS
                    and mark_observed_at(coin) >= pending['signal_at']+DELAY_MS):
                balance = self.state['balance']+net
                if balance < 0:
                    raise ValueError('Negative PAPER cash; refusing new entries')
                trade = {**pos, 'closed_at':now, 'exit_price':quote['fill_price'],
                    'pnl_usd':net, 'reason':pending['reason'], 'balance_after':balance,
                    'exit_dex_fee_usd':quote['dex_fee_usd'],
                    'exit_network_fee_usd':quote['network_fee_usd'],
                    'pending_exit':None, 'exit_trigger':pending}
                self.state['history'].append(trade)
                self.state['balance'] = balance
                self.state['positions'].remove(pos)
                continue
            peak = max(pos['peak_net_usd'], net)
            pos['peak_net_usd'] = peak
            if peak >= ARM_USD:
                pos['profit_floor_usd'] = max(number(pos.get('profit_floor_usd'), -math.inf), peak-GIVEBACK_USD)
            reason = ('FAST_STOP_NET' if net <= -STOP_USD else
                      'FAST_TARGET_NET' if net >= TARGET_USD else
                      'FAST_PROFIT_LOCK' if pos['profit_floor_usd'] is not None and net <= pos['profit_floor_usd'] else
                      'FAST_MAX_HOLD_5M' if now-pos['opened_at'] >= HOLD_MS else None)
            if reason and not pending:
                pos['pending_exit'] = dict(reason=reason, signal_at=now, trigger_net_usd=net)

    def reject(self, reason):
        diag = self.state['diagnostics']
        diag['rejections'][reason] = diag['rejections'].get(reason, 0)+1
        diag['blocked_reason'] = reason

    def step(self, feed, flows, *, refresh=True):
        if not paper_mode():
            return  # Never build, fund, mark or transact in LIVE.
        if self.error:
            self.load()  # Re-read durable state after any uncertain write; no duplicate entries.
        now = self.clock()
        self.state['diagnostics'] = dict(at=now, market_candidates=0, signal_candidates=0,
                                         rejections={}, blocked_reason=None)
        coins = {(c.get('address'), c.get('pairAddress')):c for c in feed if isinstance(c, dict)}
        try:
            self.update(coins, now)  # Exits keep running even with entries switched off.
            if enabled() and refresh:
                self.open_pending(coins, flows, now)
                now = self.clock()
                if not self.state.get('pending_entry') and self.free_cash() >= NOTIONAL:
                    # Cheap gates across the feed, at most one provider candidate next refresh.
                    candidates = []
                    for coin in coins.values():
                        if not active.matches('MOMENTUM', coin):
                            continue
                        self.state['diagnostics']['market_candidates'] += 1
                        plan, reason = self.plan(coin, flows.get((coin['address'], coin['pairAddress'])) or {}, now)
                        if reason:
                            self.reject(reason)
                        else:
                            candidates.append((plan['signal']['observed_return_pct']-plan['friction'], coin))
                    self.state['diagnostics']['signal_candidates'] = len(candidates)
                    if candidates:
                        coin = max(candidates, key=lambda row:row[0])[1]
                        self.state['pending_entry'] = dict(address=coin['address'], pairAddress=coin['pairAddress'], signal_at=now)
                        self.state['diagnostics']['blocked_reason'] = 'fast_waiting_next_observation'
                elif self.free_cash() < NOTIONAL:
                    self.reject('fast_cash_unavailable')
            elif not enabled():
                self.state['pending_entry'] = None
                self.state['diagnostics']['blocked_reason'] = 'fast_entries_disabled_exits_continue'
            if not self.state['diagnostics']['market_candidates'] and not self.state['diagnostics']['blocked_reason']:
                self.state['diagnostics']['blocked_reason'] = 'fast_no_market_signal'
            self.state['updated_at'] = self.clock()
            self.write(self.path, self.state)
            self.error = None
        except Exception as exc:
            self.error = type(exc).__name__+': '+str(exc)[:180]
            raise

    def view(self):
        state = self.state
        trades = state['history']
        wins = sum(t['pnl_usd'] > 0 for t in trades)
        now = self.clock()
        def compact(row):
            fields = ('trade_no','symbol','address','pairAddress','opened_at','notional_usd',
                'current_price','open_pnl_usd','quote_status','profit_floor_usd','pending_exit',
                'closed_at','pnl_usd','reason','entry_policy_version','mark_received_at')
            return {key:copy.deepcopy(row[key]) for key in fields if key in row}
        return dict(version=VERSION, config_hash=CONFIG_HASH, config=config(),
            status='degraded' if self.error else 'online', error=self.error,
            enabled=enabled(), updated_at=state['updated_at'], starting_balance=CAPITAL,
            balance=state['balance'], available_cash_usd=self.free_cash(),
            realized_pnl_usd=state['balance']-CAPITAL,
            unrealized_pnl_usd=sum(p['open_pnl_usd'] for p in state['positions']),
            trades=len(trades), wins=wins, win_rate_pct=100*wins/len(trades) if trades else None,
            entries_last_60m=sum(0 <= now-p['opened_at'] < 3_600_000 for p in trades+state['positions']),
            diagnostics=copy.deepcopy(state.get('diagnostics')), positions=[compact(p) for p in state['positions']],
            history=[compact(p) for p in trades[-20:]], independent_capital=True,
            profitability_proven=False, real_execution_enabled=False)
