"""Run pricing accounting and archive-integrity regressions using synthetic rates."""

import copy
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import test_export_run as fixtures
from test_export_run import EXPORT, fixture, record, rates_fixture, THREAD


class PricingTests(unittest.TestCase):
    setUp = fixtures.ExportTests.setUp
    source = fixtures.ExportTests.source
    export = fixtures.ExportTests.export

    def test_cached_input_and_reasoning_are_not_billed_twice(self):
        self.source()
        path, stats = self.export()
        cost = stats["api_cost"]
        self.assertEqual(cost["status"], "priced")
        # Fixture rates per million tokens: $2 uncached input, $0.10 cached input, $10 output.
        expected = (30 * Decimal("2") + 70 * Decimal("0.10") + 20 * Decimal("10")) / 1_000_000
        self.assertEqual(Decimal(cost["total_api_price_usd"]), expected)
        self.assertEqual(cost["groups"][0]["tokens"], {
            "input": 30, "cached_input": 70, "cache_write_input": 0, "output": 20})
        self.assertEqual(cost["assumptions"], ["Missing processing tiers priced as standard",
                                               "Global processing; no regional premium"])
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_mixed_models_observed_priority_and_request_context_bands(self):
        rows = fixture()
        rows[2]["payload"]["service_tier"] = "priority"
        rows += [record("turn_context", {"turn_id": "turn-2", "model": "comparison-model",
                  "effort": "low", "service_tier": "standard"}, 7)]
        usage = {"input_tokens": 272001, "cached_input_tokens": 272000, "output_tokens": 20,
                 "reasoning_output_tokens": 5, "cache_write_input_tokens": 0, "total_tokens": 272021}
        total = {key: value + rows[-2]["payload"]["usage"][key] for key, value in usage.items()}
        rows.append(record("token_usage_record", {"thread_id": THREAD, "turn_id": "turn-2",
                    "response_id": "response-2", "usage": usage, "thread_token_usage": total}, 8))
        self.source(rows)
        path, stats = self.export()
        cost = stats["api_cost"]
        self.assertEqual(cost["total_api_price_usd"], "0.055238")
        self.assertEqual([group["tier"] for group in cost["groups"]], ["priority", "standard"])
        self.assertEqual([group["max_input_tokens"] for group in cost["groups"]], [272000, None])
        self.assertEqual(cost["assumptions"], ["Global processing; no regional premium"])
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_lookup_failure_or_missing_model_preserves_tokens_with_null_price(self):
        self.source()
        with patch.object(EXPORT.PRICING, "fetch_rates", side_effect=OSError("Offline")):
            path, stats = self.export(run_id="offline")
        self.assertEqual(stats["tokens"]["input_tokens"], 100)
        self.assertEqual(stats["api_cost"]["status"], "unavailable")
        self.assertIsNone(stats["api_cost"]["total_api_price_usd"])
        self.assertEqual(EXPORT.verify_run(path), stats)
        rates = rates_fixture()
        rates["models"] = {}
        with patch.object(EXPORT.PRICING, "fetch_rates", return_value=rates):
            _, stats = self.export(run_id="missing-model")
        self.assertIsNone(stats["api_cost"]["total_api_price_usd"])
        self.assertIn("No verified rates", stats["api_cost"]["reason"])

    def test_verification_rejects_changed_total_tokens_cost_or_rates(self):
        self.source()
        path, original = self.export()
        variants = []
        modified = copy.deepcopy(original)
        modified["tokens"]["input_tokens"] += 1
        variants.append(modified)
        modified = copy.deepcopy(original)
        modified["api_cost"]["total_api_price_usd"] = "0"
        variants.append(modified)
        modified = copy.deepcopy(original)
        modified["api_cost"]["rates"]["models"]["test-model"]["standard"]["bands"][0]["input"] = "999"
        variants.append(modified)
        for stats in variants:
            (path / "run.json").write_text(json.dumps(stats))
            with self.assertRaises(ValueError):
                EXPORT.verify_run(path)

    def test_cache_writes_need_verified_retention_and_are_subtracted_from_input(self):
        rows = fixture()
        rows[-1]["payload"]["usage"]["cache_write_input_tokens"] = 10
        rows[-1]["payload"]["thread_token_usage"]["cache_write_input_tokens"] = 10
        self.source(rows)
        _, stats = self.export(run_id="unknown-write-rate")
        self.assertEqual(stats["api_cost"]["status"], "unavailable")
        self.assertIsNone(stats["api_cost"]["total_api_price_usd"])
        rates = rates_fixture()
        config = rates["models"]["test-model"]["standard"]
        config["cache_write_accounting"] = "included_in_input"
        config["bands"][0]["cache_write_input"] = "2.5"
        with patch.object(EXPORT.PRICING, "fetch_rates", return_value=rates):
            path, stats = self.export(run_id="verified-write-rate")
        self.assertEqual(stats["api_cost"]["total_api_price_usd"], "0.000272")
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_explicit_rates_must_be_fresh_but_verification_is_offline(self):
        self.source()
        rates = rates_fixture()
        rates["retrieved_on"] = "2000-01-01"
        source = self.repo / "rates.json"
        source.write_text(json.dumps(rates))
        _, stats = self.export(pricing_rates=source, run_id="stale")
        self.assertEqual(stats["api_cost"]["status"], "unavailable")
        path, stats = self.export(run_id="priced")
        with patch.object(EXPORT.PRICING, "fetch_rates", side_effect=AssertionError("No network during verification")):
            self.assertEqual(EXPORT.verify_run(path), stats)

    def test_parser_requires_exact_models_units_and_context_boundary(self):
        document = """Prices per 1M tokens.
### Standard pricing data
| Model | Short context input | Short context output | Short context cached input | Long context input | Long context output | Long context cached input |
| --- | --- | --- | --- | --- | --- | --- |
| test-model | $2 | $10 | $0.1 | $4 | $15 | $0.2 |
### Fast pricing data
| Model | Input | Output | Cached input |
| --- | --- | --- | --- |
| test-model | $4 | $20 | $0.2 |
Short context: ≤272K input tokens. Long context: >272K input tokens.
"""
        parsed = EXPORT.PRICING.parse_rates(document, ["test-model"], "2026-11-06")
        self.assertEqual(parsed["models"]["test-model"]["standard"]["bands"][1]["input"], "4")
        self.assertEqual(parsed["models"]["test-model"]["priority"]["bands"][0]["output"], "20")
        for text, models in ((document, ["other-model"]),
                             (document.replace("≤272K", "≤300K"), ["test-model"]),
                             (document.replace("per 1M", "per 1K"), ["test-model"]),
                             (document.replace("$0.1", "-"), ["test-model"])):
            with self.assertRaises(ValueError):
                EXPORT.PRICING.parse_rates(text, models, "2026-11-06")
