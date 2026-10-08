"""Small read-only dashboard projection of Strategy Lab state."""

# Scalar planning fields of a cost feasibility summary. The per-candidate
# best_candidates list stays only on funded (PROMOTED_PAPER) books, so the
# compact payload does not grow with every TEST book's sample rows.
COST_FEASIBILITY_SCALAR_FIELDS = (
    'basis', 'is_execution_quote', 'profitability_proven', 'excluded_costs',
    'checked_market_candidates', 'fixed_cost_infeasible_candidates', 'unknown_candidates',
    'maximum_roundtrip_cost_pct', 'minimum_model_roundtrip_cost_pct',
)


def compact_cost_feasibility(summary, *, keep_candidates):
    if not isinstance(summary, dict) or keep_candidates:
        return summary
    return {field: summary[field] for field in COST_FEASIBILITY_SCALAR_FIELDS if field in summary}


def compact_strategy_lab(data):
    if not isinstance(data, dict):
        return {'status': 'offline', 'books': {}, 'stats': {}}

    books = {}
    for key, raw in (data.get('books') or {}).items():
        if not isinstance(raw, dict):
            continue
        position = raw.get('position')
        if isinstance(position, dict):
            position = {
                field: position.get(field)
                for field in ('symbol', 'address', 'pairAddress', 'dexId', 'quoteTokenAddress',
                              'strategy_id', 'opened_at', 'pnl_pct',
                              'open_pnl_usd', 'notional_usd', 'entry_price', 'execution_entry_price',
                              'current_price', 'entry_dex_fee_usd', 'entry_network_fee_usd',
                              'estimated_exit_fee_usd', 'estimated_exit_impact_pct', 'execution_mode',
                              'execution_source', 'entry_policy_version', 'entry_evidence_guard_version',
                              'entry_candidate_rule', 'updated_at', 'mark_received_at', 'mark_source',
                              'quote_status', 'quote_age_ms', 'quote_unavailable_reason',
                              'entry_roundtrip_pnl_pct', 'entry_cost_cap_pct', 'stop_loss_net_pct',
                              'stop_headroom_pct', 'entry_universe_version', 'exit_policy_label',
                              'exit_parameters')
            }
        else:
            position = None
        history = []
        # Strategy Lab stores history newest first, so keep the first 30 rows.
        for trade in (raw.get('history') or [])[:30]:
            if not isinstance(trade, dict):
                continue
            history.append({
                field: trade.get(field)
                for field in ('trade_no', 'strategy_id', 'symbol', 'name', 'address', 'pairAddress',
                              'entry_price', 'execution_entry_price', 'exit_price', 'execution_exit_price',
                              'notional_usd', 'opened_at', 'closed_at', 'score', 'pnl_pct', 'pnl_usd',
                              'balance_before', 'balance_after', 'exit_reason', 'execution_mode',
                              'entry_policy_version', 'entry_evidence_guard_version', 'entry_candidate_rule',
                              'entry_dex_fee_usd', 'exit_dex_fee_usd', 'entry_network_fee_usd',
                              'exit_network_fee_usd', 'entry_price_impact_pct', 'exit_price_impact_pct',
                              'entry_slippage_pct', 'exit_slippage_pct',
                              'entry_roundtrip_pnl_pct', 'entry_cost_cap_pct', 'stop_loss_net_pct',
                              'stop_headroom_pct', 'entry_universe_version', 'exit_policy_label')
            })
        books[key] = {
            'id': raw.get('id', key),
            'name': raw.get('name', key),
            'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0),
            'portfolio_group': raw.get('portfolio_group', 'TEST'),
            'allocation_usd': raw.get('allocation_usd', raw.get('starting_balance', 0)),
            'max_position_fraction': raw.get('max_position_fraction', 1.0),
            'position': position,
            'history': history,
            **({'runtime_compatibility': raw['runtime_compatibility']}
               if isinstance(raw.get('runtime_compatibility'), dict) else {}),
            **({'strategy_lifecycle': raw['strategy_lifecycle']}
               if isinstance(raw.get('strategy_lifecycle'), dict) else {}),
            'entry_diagnostics': {
                field: raw['entry_diagnostics'][field]
                for field in (
                    'at', 'signal_candidates', 'market_rejected_candidates', 'matched_candidates', 'cost_rejected',
                    'cooldown_rejected', 'affordable_candidates', 'price_verification_rejected',
                    'price_crosscheck_pending', 'flow_missing_candidates',
                    'flow_tape_status', 'flow_tape_coverage_pct',
                    'verified_flow_events_60s', 'flow_tape_backlog', 'blocked_reason',
                    'promoted_policy_version', 'promoted_flow_rejected',
                    'promoted_evidence_guard_version', 'promoted_candidate_policy_source',
                    'promoted_safety_rejected', 'promoted_price_rejected',
                    'promoted_cost_rejected', 'promoted_block_reasons',
                    'promoted_max_entry_roundtrip_cost_pct', 'promoted_cost_feasibility', 'profitability_proven',
                    # LAB_ACTIVE_V6: every book publishes its cap and cost-infeasible count.
                    'entry_policy_version', 'max_entry_roundtrip_cost_pct', 'stop_loss_net_pct',
                    'cost_infeasible_candidates', 'cost_feasibility', 'cost_first', 'evidence_guard_version',
                    # DEFENSIVE_ENTRY_LAYER_V1: counts only (examples stay in the full ledger).
                    'defensive_rejected', 'defensive_entry',
                )
                if isinstance(raw.get('entry_diagnostics'), dict)
                and field in raw['entry_diagnostics']
            } if isinstance(raw.get('entry_diagnostics'), dict) else {},
        }
        diagnostics = books[key]['entry_diagnostics']
        if isinstance(diagnostics.get('defensive_entry'), dict):
            defensive = diagnostics['defensive_entry']
            diagnostics['defensive_entry'] = {
                field: defensive[field] for field in (
                    'version', 'checked', 'blocked', 'rejections', 'primary_rejections', 'log_only_flags',
                    'pool_loss_cooldown_pools', 'heat_log_only', 'commit_recheck_blocked') if field in defensive}
        if 'cost_feasibility' in diagnostics:
            diagnostics['cost_feasibility'] = compact_cost_feasibility(
                diagnostics['cost_feasibility'],
                keep_candidates=books[key]['portfolio_group'] == 'PROMOTED_PAPER')

    raw_setup = data.get('portfolio_setup') or {}
    setup_fields = (
        'version', 'status', 'group', 'completed_at', 'draining_since',
        'total_allocated_capital_usd', 'allocation_per_strategy_usd', 'max_position_fraction',
        'strategies', 'accounts_are_independent', 'real_execution_enabled',
        'selection_thresholds', 'selection_evidence', 'historical_simulations',
        'unique_market_episodes_30m', 'evidence_note', 'losing_test_books_restarted',
        'cancelled_open_test_positions', 'legacy_open_positions', 'promotion_error',
    )
    portfolio_setup = {field: raw_setup[field] for field in setup_fields if field in raw_setup}
    legacy_books = {}
    for key, raw in (raw_setup.get('legacy_draining_books') or {}).items():
        if not isinstance(raw, dict):
            continue
        position = raw.get('position')
        if isinstance(position, dict):
            position = {
                field: position.get(field)
                for field in ('symbol', 'address', 'pairAddress', 'dexId', 'quoteTokenAddress',
                              'strategy_id', 'opened_at',
                              'pnl_pct', 'open_pnl_usd', 'notional_usd', 'entry_price',
                              'execution_entry_price', 'current_price', 'execution_mode',
                              'updated_at', 'mark_received_at', 'mark_source', 'quote_status',
                              'quote_age_ms', 'quote_unavailable_reason')
            }
        else:
            position = None
        history = []
        for trade in (raw.get('history') or [])[-10:]:
            if not isinstance(trade, dict):
                continue
            history.append({
                field: trade.get(field)
                for field in ('trade_no', 'strategy_id', 'symbol', 'address', 'pairAddress',
                              'opened_at', 'closed_at', 'pnl_usd', 'pnl_pct', 'exit_reason',
                              'execution_mode')
            })
        legacy_books[key] = {
            'id': raw.get('id', key), 'strategy_id': raw.get('strategy_id', key),
            'name': raw.get('name', key), 'starting_balance': raw.get('starting_balance', 0),
            'balance': raw.get('balance', 0), 'position': position, 'history': history,
        }
    if legacy_books:
        portfolio_setup['legacy_draining_books'] = legacy_books
        portfolio_setup['legacy_open_position_count'] = sum(bool(book.get('position')) for book in legacy_books.values())

    result = {
        'started_at': data.get('started_at'),
        'updated_at': data.get('updated_at'),
        'status': data.get('status', 'offline'),
        'execution_basis': data.get('execution_basis'),
        'execution_note': data.get('execution_note'),
        'books': books,
        'stats': data.get('stats') or {},
        'data_integrity_note': data.get('data_integrity_note'),
        'activity_config': data.get('activity_config') or {},
        'portfolio_setup': portfolio_setup,
        'registry_compatibility': data.get('registry_compatibility') or {},
        'strategy_lifecycle': data.get('strategy_lifecycle') or {},
    }

    astra = data.get('astra')
    if isinstance(astra, dict):
        astra_view = {key: value for key, value in astra.items() if key != 'book'}
        book = astra.get('book')
        if isinstance(book, dict):
            astra_view['book'] = {
                'balance': book.get('balance', 0),
                'starting_balance': book.get('starting_balance', 0),
                'positions': book.get('positions') or [],
                'history': (book.get('history') or [])[:30],
            }
        result['astra'] = astra_view

    paired = data.get('paired')
    if isinstance(paired, dict):
        result['paired'] = paired

    return result
