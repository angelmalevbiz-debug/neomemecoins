"""PAPER candidate rules plus bounded, auditable loss feedback.

Signals only identify candidates. The engine still requires confirmed exact-pool
flow, fresh safety, executable quotes, cost limits, and available risk budget.
The rolling outcome summary is descriptive until a rule has enough same-policy
closed trades; it does not claim or guarantee future profitability.
"""
from dataclasses import asdict
import math
from typing import Any

import lab_activity

VERSION = 'WINNER_ENSEMBLE_PAPER_V1'
ENTRY_POLICY_VERSION = 'WINNER_ENSEMBLE_VERIFIED_ENTRY_V3'
VERIFIED_FLOW_MOMENTUM = 'VERIFIED_FLOW_MOMENTUM'
STRATEGIES = (VERIFIED_FLOW_MOMENTUM, 'EARLY', 'MOMENTUM', 'PRECISION', 'ULTRA_PRECISION')

# The broad market screen increases the candidate pool. Confirmation remains
# strict: the promoted-entry guard enforces fresh chain evidence, while this
# rule rejects weak buy pressure and low-quality market structure.
RULES = {
    VERIFIED_FLOW_MOMENTUM: lab_activity.EntryRule(
        76, 10_000, (-3, 45), .9, .04, (1, 1440), (-25, 300), (.05, 12),
        flow_trades=3, flow_ratio=1.2, wallets=2,
    ),
    **{name: lab_activity.RULES[name] for name in STRATEGIES[1:]},
}

MIN_SCORE = min(RULES[name].score for name in STRATEGIES)
MIN_LIQUIDITY_USD = min(RULES[name].liquidity for name in STRATEGIES)
MIN_TRADES_BEFORE_THROTTLE = 12
THROTTLE_POSTERIOR_WIN_RATE = .35
MAX_LEARNING_CLOSES = 100


def market_candidates(coin: dict[str, Any]) -> list[str]:
    """Return rules whose market-only conditions pass before asking for flow."""
    features = lab_activity.market_features(coin)
    return [name for name in STRATEGIES if RULES[name].matches(features, require_flow=False)]


def _normalized_flow(flow: dict[str, Any] | None) -> dict[str, Any]:
    flow = flow or {}
    proof = flow.get('verified_flow') if isinstance(flow.get('verified_flow'), dict) else {}
    buy_usd = flow.get('buy_usd', proof.get('buy_usd', 0))
    sell_usd = flow.get('sell_usd', proof.get('sell_usd', 0))
    try:
        ratio = float(flow.get('buy_sell_usd_ratio') or 0)
    except (TypeError, ValueError, OverflowError):
        ratio = 0
    if not ratio:
        try:
            ratio = float(buy_usd or 0) / max(float(sell_usd or 0), 1)
        except (TypeError, ValueError, OverflowError):
            ratio = 0
    return {
        'trades': flow.get('trades', proof.get('trades', 0)),
        'ratio': ratio,
        'buy_usd': buy_usd,
        'sell_usd': sell_usd,
        'unique_wallets': flow.get('unique_wallets', proof.get('unique_wallets', 0)),
        'max_sell': flow.get('max_sell_usd', math.inf),
    }


def matches(coin: dict[str, Any], flow: dict[str, Any] | None = None) -> list[str]:
    """Return market rules whose market and confirmed-flow fields both pass."""
    features = lab_activity.market_features(coin, _normalized_flow(flow))
    return [name for name in market_candidates(coin)
            if RULES[name].matches(features)]


def learning_snapshot(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize same-policy PAPER closes and conservatively throttle weak rules.

    Overall results use the most recent 100 valid, unique current-policy closes;
    each rule independently uses its most recent 100 matching closes. Unknown
    outcomes are reported and excluded. Each close is attributed to every rule
    that matched on entry. This is useful for screening but is not a causal
    experiment because rule matches overlap. Holds do not expire with time.
    """
    ignored: dict[str, int] = {}

    def ignore(reason: str) -> None:
        ignored[reason] = ignored.get(reason, 0) + 1

    def finite_number(value: Any) -> float | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (ValueError, OverflowError):
            return None

    validated: list[dict[str, Any]] = []
    for trade in history:
        if not isinstance(trade, dict):
            ignore('invalid_record')
            continue
        if trade.get('entry_policy_version') != ENTRY_POLICY_VERSION:
            ignore('other_policy')
            continue
        trade_id = trade.get('id')
        if not isinstance(trade_id, str) or not trade_id.strip():
            ignore('missing_close_id')
            continue
        stamp = finite_number(trade.get('closed_at'))
        pnl = finite_number(trade.get('pnl_usd'))
        if (stamp is None or stamp <= 0
                or trade.get('exit_state', 'CLOSED') != 'CLOSED'):
            ignore('invalid_closed_outcome')
            continue
        if pnl is None:
            ignore('unknown_pnl')
            continue
        # New entries retain all rules matching at admission, including rules
        # held out by learning. Their actual overlapping PAPER outcomes remain
        # descriptive evidence; no hypothetical or unexecuted trades are added.
        matched = trade.get('strategy_matches_at_entry', trade.get('strategy_matches'))
        if isinstance(matched, str):
            matched = [matched]
        if not isinstance(matched, (list, tuple)):
            ignore('invalid_strategy_matches')
            matched = []
        matched_rules = []
        for name in matched:
            if not isinstance(name, str) or name not in RULES:
                ignore('invalid_strategy_match')
                continue
            if name not in matched_rules:
                matched_rules.append(name)
            else:
                ignore('duplicate_strategy_match')
        validated.append({
            'id': trade_id, 'closed_at': stamp, 'pnl_usd': float(pnl),
            'strategy_matches': [name for name in STRATEGIES if name in matched_rules],
            'exit_reason': str(trade.get('exit_reason') or 'UNKNOWN'),
        })

    # A closed ledger entry is immutable. Count a repeated ID once, and exclude
    # conflicting results rather than arbitrarily choosing the winning copy.
    by_id: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for trade in validated:
        trade_id = trade['id']
        if trade_id in by_id:
            ignore('duplicate_close_id')
            if trade != by_id[trade_id]:
                conflicts.add(trade_id)
        else:
            by_id[trade_id] = trade
    if conflicts:
        ignored['conflicting_close_ids'] = len(conflicts)
    valid_closes = sorted(
        (trade for trade_id, trade in by_id.items() if trade_id not in conflicts),
        key=lambda trade: (trade['closed_at'], trade['id']), reverse=True,
    )
    closed = valid_closes[:MAX_LEARNING_CLOSES]
    per_rule: dict[str, list[dict[str, Any]]] = {name: [] for name in STRATEGIES}
    for trade in valid_closes:
        for name in trade['strategy_matches']:
            if len(per_rule[name]) < MAX_LEARNING_CLOSES:
                per_rule[name].append(trade)

    strategies: dict[str, dict[str, Any]] = {}
    disabled: list[str] = []
    for name in STRATEGIES:
        trades = per_rule[name]
        pnls = [trade['pnl_usd'] for trade in trades]
        wins = sum(pnl > 0 for pnl in pnls)
        net_pnl = sum(pnls)
        # Beta(2,2) smoothing keeps tiny samples from being treated as evidence.
        posterior_win_rate = (wins + 2) / (len(pnls) + 4)
        loss_reasons: dict[str, int] = {}
        for trade, pnl in zip(trades, pnls):
            if pnl < 0:
                reason = str(trade.get('exit_reason') or 'UNKNOWN')
                loss_reasons[reason] = loss_reasons.get(reason, 0) + 1
        is_disabled = (
            len(trades) >= MIN_TRADES_BEFORE_THROTTLE
            and net_pnl < 0
            and posterior_win_rate < THROTTLE_POSTERIOR_WIN_RATE
        )
        if is_disabled:
            disabled.append(name)
        strategies[name] = {
            'closed_trades': len(trades),
            'wins': wins,
            'losses': sum(pnl < 0 for pnl in pnls),
            'breakeven': sum(pnl == 0 for pnl in pnls),
            'win_rate_pct': round(100 * wins / len(trades), 1) if trades else 0,
            'posterior_win_rate_pct': round(100 * posterior_win_rate, 1),
            'net_pnl_usd': round(net_pnl, 4),
            'loss_reasons': dict(sorted(loss_reasons.items(), key=lambda item: (-item[1], item[0]))),
            'throttled': is_disabled,
            'recovery_requirement': ('REVIEWED_POLICY_OR_NEW_VALID_OVERLAPPING_OUTCOMES'
                                     if is_disabled else None),
            'oldest_close_at': trades[-1]['closed_at'] if trades else None,
            'latest_close_at': trades[0]['closed_at'] if trades else None,
        }
    all_pnls = [trade['pnl_usd'] for trade in closed]
    wins = sum(pnl > 0 for pnl in all_pnls)
    gross_profit = sum(pnl for pnl in all_pnls if pnl > 0)
    gross_loss = -sum(pnl for pnl in all_pnls if pnl < 0)
    return {
        'policy_version': ENTRY_POLICY_VERSION,
        'closed_trades': len(closed),
        'wins': wins,
        'losses': sum(pnl < 0 for pnl in all_pnls),
        'breakeven': sum(pnl == 0 for pnl in all_pnls),
        'win_rate_pct': round(100 * wins / len(closed), 1) if closed else 0,
        'net_pnl_usd': round(sum(all_pnls), 4),
        'profit_factor': round(gross_profit / gross_loss, 4) if gross_loss else None,
        'min_trades_before_throttle': MIN_TRADES_BEFORE_THROTTLE,
        'window_max_closed_trades': MAX_LEARNING_CLOSES,
        'window_basis': 'MOST_RECENT_VALID_CLOSED_AT_PER_RULE',
        'valid_policy_closed_trades': len(valid_closes),
        'ignored_data': ignored,
        'throttled_strategies': disabled,
        'strategies': strategies,
        'attribution_note': 'Each close is counted for every matched rule; overlapping matches are not causal proof.',
    }


def apply_learning(matches_: list[str], history: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    learning = learning_snapshot(history)
    disabled = set(learning['throttled_strategies'])
    return [name for name in matches_ if name not in disabled], learning


def rule_config() -> dict[str, dict[str, Any]]:
    return {
        name: {key: (None if isinstance(value, float) and not math.isfinite(value) else value)
               for key, value in asdict(RULES[name]).items()}
        for name in STRATEGIES
    }
