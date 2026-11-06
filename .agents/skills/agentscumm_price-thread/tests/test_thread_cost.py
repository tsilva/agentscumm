"""Synthetic regression checks for usage coverage and monetary accounting."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "thread_cost", Path(__file__).parents[1] / "scripts" / "thread_cost.py")
cost = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cost)


def tokens(input=100, cached=80, output=10, reasoning=3, writes=0):
    return dict(input_tokens=input, cached_input_tokens=cached,
                cache_write_input_tokens=writes, output_tokens=output,
                reasoning_output_tokens=reasoning, total_tokens=input + output)


def event(kind, payload, second=0):
    return {"timestamp": f"2026-11-05T16:00:{second:02d}Z", "type": kind, "payload": payload}


def header():
    return event("session_meta", {"id": "thread", "model_provider": "openai"})


def turn(model="model-a", turn_id="turn", tier=None):
    return event("turn_context", {"turn_id": turn_id, "model": model,
                                  "effort": "high", "service_tier": tier})


def record(response="r1", use=None, total=None, second=1, turn_id="turn"):
    use = use or tokens()
    return event("token_usage_record", {"thread_id": "thread", "turn_id": turn_id,
                 "response_id": response, "usage": use,
                 "thread_token_usage": total or use}, second)


def counter(use=None, total=None, second=1):
    use = use or tokens()
    return event("event_msg", {"type": "token_count", "info": {
        "last_token_usage": use, "total_token_usage": total or use}}, second)


def rates():
    config = {"bands": [
        {"max_input_tokens": 272000, "input": "2", "cached_input": "0.1", "output": "10"},
        {"max_input_tokens": None, "input": "4", "cached_input": "0.2", "output": "15"}]}
    return {"retrieved_on": "2026-11-05",
            "sources": ["https://developers.openai.com/api/docs/pricing"],
            "models": {"model-a": {"standard": config},
                       "model-b": {"priority": {"bands": [
                           {"max_input_tokens": None, "input": "8", "cached_input": "0.4",
                            "output": "40"}]}}}}


class ThreadCostTests(unittest.TestCase):
    def collect(self, records, before=None, tail=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in records) + tail)
            before_bytes = path.read_bytes()
            result = cost.snapshot(path, "thread", before)
            self.assertEqual(path.read_bytes(), before_bytes)
            return result

    def basic(self):
        return self.collect([header(), turn(), record()])

    def test_cache_and_reasoning_are_not_double_counted(self):
        result = cost.price(self.basic(), rates(), "standard")
        # 20 fresh input, 80 cache, 10 output (of which 3 reasoning).
        self.assertEqual(Decimal(result["total_model_token_cost"]), Decimal("0.000148"))
        self.assertEqual(result["groups"][0]["tokens"]["input"], 20)
        self.assertTrue(result["assumptions"])

    def test_duplicate_records_and_counters_do_not_inflate_usage(self):
        use = tokens()
        doubled = {k: v * 2 for k, v in use.items()}
        data = self.collect([header(), turn(), record(), record(), counter(), counter(),
                             record("r2", use, doubled, 2), counter(use, doubled, 2)])
        self.assertEqual(data["totals"], doubled)
        self.assertEqual(len(data["requests"]), 2)

    def test_cutoff_is_inclusive_and_source_is_unchanged(self):
        doubled = {k: v * 2 for k, v in tokens().items()}
        data = self.collect([header(), turn(), record(), record("r2", total=doubled, second=2)],
                            before="2026-11-05T16:00:01Z")
        self.assertEqual(data["totals"], tokens())

    def test_partial_trailing_record_is_disclosed(self):
        data = self.collect([header(), turn(), record()], tail='{"type":')
        self.assertEqual(len(data["warnings"]), 1)

    def test_malformed_complete_record_fails(self):
        with self.assertRaisesRegex(ValueError, "Malformed rollout"):
            self.collect([header(), turn(), record()], tail="broken\n")

    def test_missing_prefix_and_gaps_fail(self):
        doubled = {k: v * 2 for k, v in tokens().items()}
        with self.assertRaisesRegex(ValueError, "missing prefix"):
            self.collect([header(), turn(), record(total=doubled)])
        with self.assertRaisesRegex(ValueError, "missing prefix"):
            self.collect([header(), turn(), counter(total=doubled)])

    def test_conflicting_duplicate_fails(self):
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.collect([header(), turn(), record(), record(use=tokens(output=20))])

    def test_legacy_cumulative_deltas_and_reset(self):
        doubled = {k: v * 2 for k, v in tokens().items()}
        data = self.collect([header(), turn(), counter(), counter(), counter(total=doubled)])
        self.assertEqual(data["coverage"], "cumulative_deltas")
        self.assertEqual(data["totals"], doubled)
        with self.assertRaisesRegex(ValueError, "counter reset"):
            self.collect([header(), turn(), counter(), counter(total=doubled), counter()])

    def test_model_change_within_same_turn_preserves_each_request_model(self):
        doubled = {k: v * 2 for k, v in tokens().items()}
        data = self.collect([header(), turn(tier="standard"), record(),
                             turn(model="model-b", tier="priority"),
                             record("r2", total=doubled, second=2)])
        result = cost.price(data, rates())
        self.assertEqual([g["model"] for g in result["groups"]], ["model-a", "model-b"])
        self.assertFalse(result["assumptions"])

    def test_unknown_model_provider_and_thread_fail(self):
        data = self.basic()
        data["requests"][0]["model"] = "unknown"
        with self.assertRaisesRegex(ValueError, "No verified rates"):
            cost.price(data, rates(), "standard")
        bad_header = header()
        bad_header["payload"]["model_provider"] = "another-provider"
        with self.assertRaisesRegex(ValueError, "unsupported provider"):
            self.collect([bad_header, turn(), record()])
        with self.assertRaisesRegex(ValueError, "selected thread"):
            self.collect([event("session_meta", {"id": "other"}), turn(), record()])

    def test_unavailable_tier_needs_explicit_scenario(self):
        with self.assertRaisesRegex(ValueError, "Processing tier unavailable"):
            cost.price(self.basic(), rates())

    def test_unknown_model_context_is_not_inferred(self):
        with self.assertRaisesRegex(ValueError, "matching model turn context"):
            self.collect([header(), record()])

    def test_context_band_applied_per_request_at_exact_boundary(self):
        short = tokens(input=272000, cached=100000)
        long = tokens(input=272001, cached=100000)
        total = {k: short[k] + long[k] for k in short}
        data = self.collect([header(), turn(), record(use=short),
                             record("r2", long, total, 2)])
        result = cost.price(data, rates(), "standard")
        self.assertEqual(len(result["groups"]), 2)
        expected = (Decimal(172000) * 2 + Decimal(100000) * Decimal("0.1") + 10 * 10 +
                    Decimal(172001) * 4 + Decimal(100000) * Decimal("0.2") + 10 * 15) / 1000000
        self.assertEqual(Decimal(result["total_model_token_cost"]), expected)

    def test_cache_writes_require_evidence_and_do_not_double_count(self):
        data = self.collect([header(), turn(), record(use=tokens(cached=70, writes=10))])
        with self.assertRaisesRegex(ValueError, "Nonzero cache writes"):
            cost.price(data, rates(), "standard")
        manifest = rates()
        config = manifest["models"]["model-a"]["standard"]
        config["cache_write_accounting"] = "included_in_input"
        config["bands"][0]["cache_write_input"] = "2.5"
        result = cost.price(data, manifest, "standard")
        self.assertEqual(Decimal(result["total_model_token_cost"]), Decimal("0.000172"))

    def test_invalid_rate_and_tampered_snapshot_fail(self):
        manifest = rates()
        manifest["models"]["model-a"]["standard"]["bands"][0]["input"] = "NaN"
        with self.assertRaisesRegex(ValueError, "finite"):
            cost.price(self.basic(), manifest, "standard")
        data = self.basic()
        data["totals"]["total_tokens"] += 1
        with self.assertRaises(ValueError):
            cost.price(data, rates(), "standard")


if __name__ == "__main__":
    unittest.main()
