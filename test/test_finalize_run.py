"""Synthetic checks for deferred export and idempotent local benchmark registration."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_export_run import fixture, record, THREAD


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("finalize_run", ROOT / "scripts/finalize-run.py")
FINAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINAL)


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / ".gitignore").write_text("/runs/\n/.internal/\n")
        self.work = FINAL.private_directory(self.repo, self.repo / ".internal/synthetic-game/run-one")
        self.rollout = self.repo / "rollout.jsonl"
        self.request = {"repo": str(self.repo), "work_dir": str(self.work), "rollout": str(self.rollout),
                        "turn_id": "turn-1", "game_id": "synthetic-game", "game_title": "Synthetic Adventure",
                        "completion_note": "Ending observed. Private notes /Users/operator/private " + THREAD,
                        "timeout_seconds": 0.05, "walkthrough_urls": ["https://example.org/guide"], "skills": [],
                        "agent_context": None, "run_id": None}

    def source(self, rows):
        self.rollout.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def state(self):
        return json.loads((self.work / "finalization.json").read_text())

    def test_completed_turn_exports_all_metadata_and_registers_once(self):
        self.source(fixture(complete=True))
        skill = self.repo / ".agents/skills/synthetic_play-game"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: synthetic_play-game\n---\nSynthetic skill\n")
        (skill / "references/hints.txt").write_text("Synthetic supporting file")
        self.request["skills"] = [str(skill / "SKILL.md")]
        injected = self.work / "injected.txt"
        injected.write_text("Synthetic injected asset")
        context = self.work / "initial-context.json"
        context.write_text(json.dumps({"schema_version": 1, "tools": {"inventory_complete": False,
            "available_at_start": [{"name": "web.run", "kind": "builtin"}]}, "prompt_assets": [
            {"name": "Initial notes", "kind": "reference", "path": "injected.txt"}]}))
        self.request["agent_context"] = str(context)
        state = FINAL.finalize(self.request)
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["verified"])
        stats = FINAL.EXPORT.verify_run(state["archive"])
        self.assertEqual(stats["total_actions"], 2)
        self.assertEqual(stats["total_action_sequences"], 1)
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["files"], 2)
        self.assertEqual(stats["agent_context"]["tools"]["coverage"], "partial")
        self.assertEqual(len(stats["agent_context"]["prompt_assets"]), 2)
        benchmark = (self.repo / "BENCHMARKS.md").read_text()
        self.assertIn("Synthetic Adventure", benchmark)
        self.assertIn("Walkthrough-assisted", benchmark)
        self.assertIn("test-model / high", benchmark)
        self.assertNotIn("/Users/operator", benchmark)
        self.assertNotIn(THREAD, benchmark)
        self.assertNotIn("Synthetic injected asset", benchmark)
        repeated = FINAL.finalize(self.request)
        self.assertFalse(repeated["benchmark_added"])
        self.assertEqual((self.repo / "BENCHMARKS.md").read_text(), benchmark)
        self.assertFalse(list(self.work.glob(".finalize-*")))

    def test_queue_waits_for_final_reply_and_usage_then_finishes_in_background(self):
        self.source(fixture())
        self.request["timeout_seconds"] = 3
        launcher = ("import importlib.util,json,sys;"
                    f"s=importlib.util.spec_from_file_location('finalize_run',{str(ROOT / 'scripts/finalize-run.py')!r});"
                    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                    "print(json.dumps(m.queue(json.loads(sys.argv[1]))))")
        result = subprocess.run([sys.executable, "-B", "-c", launcher, json.dumps(self.request)],
                                capture_output=True, text=True, check=True, timeout=5)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "queued")
        with self.rollout.open("a") as stream:
            stream.write(json.dumps(record("response_item", {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Final completion reply retained"}]}, 7)) + "\n")
            usage = fixture()[-1]["payload"]["usage"]
            stream.write(json.dumps(record("token_usage_record", {"thread_id": THREAD, "turn_id": "turn-1",
                "response_id": "final-response", "usage": usage,
                "thread_token_usage": {key: value * 2 for key, value in usage.items()}}, 8)) + "\n")
            stream.write(json.dumps(record("event_msg", {"type": "task_complete", "turn_id": "turn-1"}, 9)) + "\n")
        deadline = time.monotonic() + 5
        while self.state()["status"] not in ("completed", "failed") and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.state()["status"], "completed", self.state())
        archive = Path(self.state()["archive"])
        self.assertIn("Final completion reply retained", (archive / "trajectory.jsonl").read_text())
        stats = FINAL.EXPORT.verify_run(archive)
        self.assertEqual(stats["tokens"]["request_count"], 2)
        self.assertEqual(stats["tokens"]["total_tokens"], 240)
        repeated = FINAL.queue(self.request)
        self.assertEqual(repeated["status"], "completed")

    def test_next_user_turn_is_excluded_from_completed_archive(self):
        finished = fixture(complete=True)
        rows = finished + [record("event_msg", {"type": "task_started", "turn_id": "turn-2"}, 8),
            record("response_item", {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Later private follow-up excluded"}]}, 9)]
        self.source(rows)
        state = FINAL.finalize(self.request)
        stats = FINAL.EXPORT.verify_run(state["archive"])
        self.assertEqual(stats["source"]["records"], len(finished))
        self.assertNotIn("Later private follow-up", (Path(state["archive"]) / "trajectory.jsonl").read_text())

    def test_wrong_turn_timeout_and_aborted_turn_never_register_completion(self):
        self.source(fixture(complete=True))
        self.request["turn_id"] = "turn-that-never-finished"
        with self.assertRaisesRegex(ValueError, "Timed out"):
            FINAL.finalize(self.request)
        self.assertEqual(self.state()["status"], "failed")
        self.request["turn_id"] = "turn-1"
        self.source(fixture() + [record("event_msg", {"type": "turn_aborted", "turn_id": "turn-1"}, 7)])
        with self.assertRaisesRegex(ValueError, "aborted"):
            FINAL.finalize(self.request)
        self.assertFalse((self.repo / "BENCHMARKS.md").exists())
        self.assertFalse(list((self.repo / "runs").glob("*/*/run.json")))

    def test_verification_failure_does_not_publish_benchmark(self):
        rows = fixture(complete=True)
        rows.insert(-2, record("response_item", {"type": "custom_tool_call", "name": "exec",
            "input": 'await tools.exec_command({cmd:"cat /missing/synthetic/SKILL.md"})'}, 5))
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "unavailable skill"):
            FINAL.finalize(self.request)
        self.assertEqual(self.state()["status"], "failed")
        self.assertFalse((self.repo / "BENCHMARKS.md").exists())

    def test_registration_failure_preserves_archive_and_can_be_retried(self):
        self.source(fixture(complete=True))
        other = self.repo / "other.md"
        other.write_text("Do not change")
        benchmark = self.repo / "BENCHMARKS.md"
        benchmark.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Linked benchmark"):
            FINAL.finalize(self.request)
        state = self.state()
        self.assertEqual(state["status"], "failed")
        FINAL.EXPORT.verify_run(state["archive"])
        self.assertEqual(other.read_text(), "Do not change")
        benchmark.unlink()
        benchmark.write_text("# Benchmarks\n\nEditorial notes preserved.\n\n## Adding results\n")
        self.assertTrue(FINAL.register_benchmark(state["archive"], repo=self.repo))
        self.assertFalse(FINAL.register_benchmark(state["archive"], repo=self.repo))
        self.assertIn("Editorial notes preserved.", benchmark.read_text())

    def test_existing_conflicting_archive_and_queue_request_are_preserved(self):
        self.source(fixture(complete=True))
        first = FINAL.finalize(self.request)
        original = (Path(first["archive"]) / "run.json").read_bytes()
        rows = fixture(complete=True)
        rows[3]["payload"]["content"][0]["text"] = "Changed source"
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "different source cutoff"):
            FINAL.finalize(self.request)
        self.assertEqual((Path(first["archive"]) / "run.json").read_bytes(), original)
        FINAL.EXPORT.write_json(self.work / "finalize-request.json", self.request)
        with self.assertRaisesRegex(ValueError, "different finalization request"):
            FINAL.queue({**self.request, "completion_note": "Different ending evidence"})

    def test_unfinished_archive_cannot_be_registered_and_missing_guide_is_not_unaided(self):
        self.source(fixture())
        archive, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo)
        with self.assertRaisesRegex(ValueError, "Only verified completed"):
            FINAL.register_benchmark(archive, repo=self.repo)
        self.assertIn("Unspecified", FINAL.benchmark_entry(stats))
        self.assertNotIn("Unaided", FINAL.benchmark_entry(stats))


if __name__ == "__main__":
    unittest.main()
