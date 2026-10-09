"""Owner-authorized virtual funding is not profit, a reset or a loss refill."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import funded_active_paper as active
import strategy_lab as lab
from lab_dashboard_projection import compact_strategy_lab

NOW=1_800_000_000_000


def cohort():
    books={s['id']:lab.empty_book(s) for s in lab.STRATEGIES}
    for sid in active.RULES:
        books[sid].update(portfolio_group='PROMOTED_PAPER',balance=200.0,trade_seq=7,
                          history=[{'trade_no':7,'pnl_usd':-50,'closed_at':NOW-1}])
    return books


class CapitalContributionTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{active.ENV:'1',active.FUNDING_ENV:'1000'})
        env.start();self.addCleanup(env.stop)
        self.books=cohort()

    def test_credits_750_once_without_changing_dollar_pnl_or_history(self):
        before=copy.deepcopy(self.books)
        lot={'trade_no':8,'opened_at':NOW-100,'notional_usd':100,'address':'held','pairAddress':'pool'}
        self.books['EARLY']['position']=lot
        applied=active.apply_authorized_funding(self.books,NOW)
        self.assertEqual(len(applied),4)
        for sid in active.RULES:
            b=self.books[sid]
            self.assertEqual(b['starting_balance'],1000)
            self.assertEqual(b['initial_starting_balance_usd'],250)
            self.assertEqual(b['balance'],950)
            self.assertEqual(b['allocation_usd'],1000)
            self.assertEqual(b['balance']-b['starting_balance'],-50)
            self.assertEqual(b['history'],before[sid]['history'])
            self.assertEqual(b['trade_seq'],7)
            event=b['funding_events'][0]
            self.assertEqual(event['amount_usd'],750)
            self.assertEqual(event['at'],NOW)
            self.assertEqual(event['strategy_id'],sid)
            self.assertFalse(event['profit'] or event['real_money'] or event['history_reset'])
        self.assertIs(self.books['EARLY']['position'],lot)
        for sid in self.books.keys()-active.RULES.keys():
            self.assertEqual(self.books[sid],before[sid])

    def test_restart_after_loss_does_not_replenish_cash_or_recredit(self):
        active.apply_authorized_funding(self.books,NOW)
        self.books['EARLY']['balance']-=25
        restored=json.loads(json.dumps(self.books))
        before=copy.deepcopy(restored)
        self.assertEqual(active.apply_authorized_funding(restored,NOW+1000),[])
        self.assertEqual(restored,before)
        self.assertEqual(restored['EARLY']['balance'],925)

    def test_no_explicit_authorization_makes_no_change(self):
        before=copy.deepcopy(self.books)
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(active.apply_authorized_funding(self.books,NOW),[])
        self.assertEqual(self.books,before)

    def test_unsupported_or_disabled_authorization_fails_closed(self):
        for env in ({active.FUNDING_ENV:'1500'},{active.ENV:'0'}):
            before=copy.deepcopy(self.books)
            with patch.dict(os.environ,env),self.assertRaises(ValueError):
                active.apply_authorized_funding(self.books,NOW)
            self.assertEqual(self.books,before)

    def test_invalid_last_book_prevents_any_partial_credit(self):
        for field,value in (('id','OTHER'),('portfolio_group','TEST'),('balance',float('nan')),
                            ('starting_balance',1500),('funding_events',[False])):
            books=cohort();books['ULTRA_PRECISION'][field]=value
            early_before=copy.deepcopy(books['EARLY'])
            with self.assertRaises(ValueError):active.apply_authorized_funding(books,NOW)
            self.assertEqual(books['EARLY'],early_before)

    def test_inconsistent_or_duplicate_event_is_never_credited(self):
        for events in ([{'id':active.FUNDING_VERSION}],
                       [{'id':active.FUNDING_VERSION},{'id':active.FUNDING_VERSION}]):
            books=cohort();books['ULTRA_PRECISION']['funding_events']=events
            with self.assertRaises(ValueError):active.apply_authorized_funding(books,NOW)
            self.assertEqual(books['EARLY']['balance'],200)

    def test_previous_contributions_remain_and_only_the_difference_is_credited(self):
        b=self.books['EARLY'];old={'id':'OLD','amount_usd':250}
        b.update(starting_balance=500,balance=450,funding_events=[old],initial_starting_balance_usd=250)
        active.apply_authorized_funding(self.books,NOW)
        self.assertEqual(b['funding_events'],[old,b['funding_events'][1]])
        self.assertEqual(b['funding_events'][1]['amount_usd'],500)
        self.assertEqual(b['balance'],950)
        self.assertEqual(b['initial_starting_balance_usd'],250)

    def test_funding_provides_four_slots_but_is_not_a_trade_or_profit(self):
        self.assertEqual(active.capacity(self.books['EARLY'],NOW)['effective_position_capacity'],1)
        active.apply_authorized_funding(self.books,NOW)
        b=self.books['EARLY'];cap=active.capacity(b,NOW)
        self.assertEqual(cap['effective_position_capacity'],4)
        self.assertEqual(cap['orders_last_60m'],0)
        self.assertEqual(cap['daily_loss_limit_usd'],50)
        self.assertEqual(lab.stats(b)['realized_pnl'],-50)
        view=compact_strategy_lab({'books':self.books})['books']['EARLY']
        self.assertEqual(view['funding_events'],b['funding_events'])
        self.assertEqual(view['starting_balance'],1000)
        self.assertEqual(view['initial_starting_balance_usd'],250)

    def test_load_persist_reload_keeps_contribution_and_cohort_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'lab.json';compact=Path(directory)/'compact.json'
            state.write_text(json.dumps({'books':self.books,'portfolio_setup':{
                'version':'PROMOTED_PAPER_COHORT_V1','status':'ACTIVE'}}),encoding='utf-8')
            with patch.object(lab,'STATE_PATH',state),patch.object(lab,'COMPACT_PATH',compact),\
                 patch.object(lab,'RESET_FLAG_PATH',Path(directory)/'reset'),\
                 patch.object(lab,'HF',None),patch.object(lab,'DEFENSE',None),\
                 patch.object(lab,'merge_astra_snapshot',side_effect=lambda s:s),\
                 patch.object(lab,'merge_paired_snapshot',side_effect=lambda s:s):
                loaded=lab.load_state()
                self.assertTrue(loaded.pop('_funding_requires_persist'))
                with patch.object(lab,'STATE',loaded):lab.persist('starting')
                raw=json.loads(state.read_text(encoding='utf-8'))
                self.assertEqual(raw['portfolio_setup']['total_allocated_capital_usd'],4000)
                self.assertEqual(raw['portfolio_setup']['allocation_per_strategy_usd'],1000)
                reloaded=lab.load_state()
                self.assertFalse(reloaded['_funding_requires_persist'])
                self.assertEqual(reloaded['books']['EARLY']['balance'],950)
                with patch.dict(os.environ,{},clear=True):unfunded_env=lab.load_state()
                self.assertEqual(unfunded_env['books']['EARLY']['allocation_usd'],1000)
                self.assertEqual(len(unfunded_env['books']['EARLY']['funding_events']),1)


    def test_startup_cannot_trade_if_credit_cannot_be_persisted(self):
        active.apply_authorized_funding(self.books,NOW)
        state={'books':self.books,'_funding_requires_persist':True}
        with patch.object(lab,'load_state',return_value=state),patch.object(lab,'STATE',{}),\
             patch.object(lab,'LOADED',False),\
             patch.object(lab,'persist',side_effect=OSError('unwritable ledger')) as persist,\
             patch.object(lab,'build_high_frequency') as hf,patch.object(lab,'maybe_open') as enter:
            with self.assertRaises(OSError):lab.main()
            persist.assert_called_once_with('starting','Owner-authorized PAPER capital contribution recorded')
            hf.assert_not_called();enter.assert_not_called()


if __name__=='__main__':unittest.main()
