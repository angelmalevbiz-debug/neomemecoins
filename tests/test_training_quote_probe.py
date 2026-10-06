"""Route-preflight tests use synthetic responses and never contact Jupiter."""
import unittest
from unittest.mock import patch

import engine_execution
from paper_training import USDC
from training_quote_probe import collect_exact_pool_quotes

NOW = 1_800_000_000_000
MINT = "So11111111111111111111111111111111111111112"
PAIR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def inputs():
    coin = {"address": MINT, "pairAddress": PAIR, "priceUsd": 1,
            "liquidityUsd": 100_000, "updatedAt": NOW, "score": 60}
    flow = {"fresh": True, "quality": "COMPLETE", "latest_at": NOW,
            "coverage": {"status": "COMPLETE", "address": MINT,
                         "pairAddress": PAIR}, "trades": 3,
            "unique_wallets": 2, "buy_usd": 25, "buy_sell_usd_ratio": 1.1}
    safety = {"status": "pass", "mint": MINT, "pair": PAIR,
              "checked_at": NOW, "metrics": {"decimals": 9,
              "token_account_rent_lamports": 2_039_280, "sol_usd": 200}}
    validation = {"status": "pass", "mint": MINT, "pair": PAIR,
                  "reference_price": 1, "reference_received_at": NOW}
    return coin, flow, safety, validation


def route_quote(inp, out, amount, at=NOW, pair=PAIR, impact="0.001"):
    return {"inputMint": inp, "outputMint": out, "inAmount": str(amount),
            "outAmount": "19000000" if out == USDC else "1000000",
            "otherAmountThreshold": "18000000" if out == USDC else "950000",
            "swapMode": "ExactIn", "priceImpactPct": impact,
            "contextSlot": 42, "slippageBps": 100,
            "routePlan": [{"swapInfo": {"ammKey": pair, "inputMint": inp,
                          "outputMint": out, "inAmount": str(amount),
                          "outAmount": "19000000" if out == USDC else "1000000"}}],
            "_received_at": at}


class TrainingQuoteProbeTests(unittest.TestCase):
    def test_fresh_exact_pool_buy_and_sell_are_tagged_background_not_fills(self):
        coin, flow, safety, validation = inputs()
        calls = []

        def quote(inp, out, amount, *, purpose):
            calls.append((inp, out, amount, purpose))
            return route_quote(inp, out, amount)

        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW, quote=quote)
        self.assertEqual(reason, "ok")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[3] == "background" for call in calls))
        self.assertEqual(calls[0][:3], (USDC, MINT, 20_000_000))
        self.assertEqual(calls[1][:3], (MINT, USDC, 950_000))
        self.assertEqual(evidence["entry"]["raw_quote"]["inputMint"], USDC)
        self.assertEqual(evidence["exit"]["raw_quote"]["outputMint"], USDC)
        self.assertNotIn("simulated_fill_at", evidence["entry"]["raw_quote"])
        self.assertGreater(evidence["entry"]["entry_account_reserve_usd"], 0)

    def test_failed_risk_or_independent_price_check_spends_no_quote_capacity(self):
        coin, flow, safety, validation = inputs()
        safety["status"] = "blocked"
        called = []
        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW,
                quote=lambda *args, **kwargs: called.append(args))
        self.assertIsNone(evidence)
        self.assertEqual(reason, "safety_not_fresh_pass")
        self.assertEqual(called, [])

        safety["status"] = "pass"
        validation["reference_price"] = None
        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW,
                quote=lambda *args, **kwargs: called.append(args))
        self.assertIsNone(evidence)
        self.assertEqual(reason, "independent_price_not_fresh_pass")
        self.assertEqual(called, [])

    def test_wrong_pool_and_high_impact_routes_are_rejected(self):
        coin, flow, safety, validation = inputs()
        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW,
                quote=lambda inp, out, amount, **kw: route_quote(inp, out, amount, pair="A"*32))
        self.assertIsNone(evidence)
        self.assertEqual(reason, "exact_pool_buy_unavailable")

        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW,
                quote=lambda inp, out, amount, **kw: route_quote(inp, out, amount, impact="0.03"))
        self.assertIsNone(evidence)
        self.assertEqual(reason, "buy_impact_limit")

    def test_unknown_rent_cost_fails_before_quote(self):
        coin, flow, safety, validation = inputs()
        safety["metrics"].pop("token_account_rent_lamports")
        called = []
        with patch.object(engine_execution, "stamp", return_value=NOW):
            evidence, reason = collect_exact_pool_quotes(
                coin, flow, safety, validation, now=NOW,
                quote=lambda *args, **kwargs: called.append(args))
        self.assertIsNone(evidence)
        self.assertEqual(reason, "rent_or_sol_cost_unknown")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
