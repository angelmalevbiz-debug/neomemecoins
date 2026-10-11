from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_funded_paper import aggregate,audit_book,market_audit,trade_row


class PaperTradeAuditTests(unittest.TestCase):
    def trade(self,**kwargs):
        return dict(opened_at=1000,closed_at=61000,notional_usd=250,pnl_usd=-2,
            entry_policy_version='V9',exit_reason='PAPER_HORIZON_MAX_HOLD_15',**kwargs)

    def test_net_pnl_is_not_replaced_with_peak_or_gross_price(self):
        row=trade_row(self.trade(peak_net_pct=8,entry_price=1,exit_price=2))
        self.assertEqual(row['sampled_peak_net_usd'],20)
        result=aggregate([row])
        self.assertEqual(result['net_pnl_usd'],-2)
        self.assertEqual(result['sampled_positive_peak_but_loss'],1)
        self.assertFalse(result['profitability_proven'])

    def test_full_history_cohorts_keep_old_versions_and_sizes_separate(self):
        history=[self.trade() for _ in range(35)]
        history.append({**self.trade(),'entry_policy_version':'OLD','pnl_usd':10,'notional_usd':25})
        result=audit_book(dict(history=history,balance=930,positions=[]),'V9',62000)
        self.assertEqual(result['all_policies']['receipts'],36)
        self.assertEqual(result['current_policy_performance']['receipts'],35)
        self.assertEqual(result['current_policy_performance']['net_pnl_usd'],-70)
        self.assertEqual(len(result['cohorts']),2)
        self.assertEqual(len(result['receipts']),36)

    def test_unknown_values_are_not_fabricated_as_zero_fees_or_zero_win_rate(self):
        row=trade_row({**self.trade(),'pnl_usd':True,'entry_roundtrip_pnl_pct':'bad'})
        self.assertIsNone(row['pnl_usd']);self.assertIsNone(row['initial_roundtrip_pnl_pct'])
        self.assertFalse(row['fees_complete'])
        self.assertIsNone(aggregate([row])['win_rate_pct']);self.assertIsNone(aggregate([])['win_rate_pct'])

    def test_actual_hold_time_and_exit_code_are_preserved(self):
        row=trade_row(self.trade())
        self.assertEqual(row['hold_minutes'],1)
        self.assertEqual(row['exit_reason'],'PAPER_HORIZON_MAX_HOLD_15')

    def test_market_report_deduplicates_and_excludes_held_mints_before_signal_count(self):
        from types import SimpleNamespace
        first=dict(address='held',pairAddress='pool',updatedAt=1000,symbol='A')
        second=dict(address='new',pairAddress='other',updatedAt=1000,symbol='B')
        active=SimpleNamespace(market_rejections=lambda sid,coin:[])
        result=market_audit([first,{**first,'updatedAt':500},second],{},'MOMENTUM',active,
            lambda coin,now:True,2000,held_addresses={'held'})
        self.assertEqual(result['duplicate_usable_rows'],1)
        self.assertEqual(result['already_open'],1)
        self.assertEqual((result['evaluated'],result['passed']),(1,1))


if __name__=='__main__': unittest.main()
