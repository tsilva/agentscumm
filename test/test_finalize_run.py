"""Synthetic checks for deferred export and idempotent local benchmark registration."""

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_export_run import fixture, record, rates_fixture, SESSION, THREAD


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
        rates = self.work / "pricing-rates.json"
        rates.write_text(json.dumps(rates_fixture()))
        lookup = patch.object(FINAL.EXPORT.PRICING, "fetch_rates", return_value=rates_fixture())
        lookup.start()
        self.addCleanup(lookup.stop)
        self.request = {"repo": str(self.repo), "work_dir": str(self.work), "rollout": str(self.rollout),
                        "turn_id": "turn-1", "game_id": "synthetic-game", "game_title": "Synthetic Adventure",
                        "completion_note": "Ending observed. Private notes /Users/operator/private " + THREAD,
                        "timeout_seconds": 0.05, "walkthrough_urls": ["https://example.org/guide"], "skills": [],
                        "agent_context": None, "run_id": None, "pricing_rates": str(rates)}

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
        self.assertEqual(state["api_cost_status"], "priced")
        self.assertEqual(state["total_api_price_usd"], "0.000267")
        stats = FINAL.EXPORT.verify_run(state["archive"])
        self.assertEqual(stats["total_actions"], 2)
        self.assertEqual(stats["total_action_sequences"], 1)
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["files"], 2)
        self.assertEqual(stats["agent_context"]["tools"]["coverage"], "partial")
        self.assertEqual(len(stats["agent_context"]["prompt_assets"]), 2)
        benchmark = (self.repo / "BENCHMARKS.md").read_text()
        self.assertIn("Synthetic Adventure", benchmark)
        self.assertIn("| Yes |", benchmark)
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

    def test_regenerated_archive_counts_local_actions_and_refreshes_benchmark(self):
        self.source(fixture(complete=True))
        previous = FINAL.finalize(self.request)
        archive = Path(previous["archive"])
        original = FINAL.EXPORT.verify_run(archive)
        archive.rename(self.work / "previous-archive")

        local_session = "33333333-3333-4333-8333-333333333333"
        foreign_session = "44444444-4444-4444-8444-444444444444"
        profile = str(self.work / "browser")
        def event(second, request, result):
            return {"timestamp": record("unused", {}, second)["timestamp"], "request": request, "result": result}

        def status(session, game):
            revision = {"repository": "agentscumm", "commit_id": ("a" if session == local_session else "b") * 40,
                        "dirty": False, "mcp_version": "0.2.0", "mcp_tools": ["act", "status"]}
            return event(1, {"name": "status"}, {"name": "status", "content": [
                {"sessionId": session, "game": game, "dataDir": profile, "sourceRevision": revision}]})

        def act(second, session=local_session):
            return event(second, {"name": "act", "arguments": {"session_id": session, "request_id": "local",
                         "actions": [{"type": "wait"}, {"type": "key"}, {"type": "screenshot"}]}},
                         {"name": "act", "isError": True})

        before = {**act(0), "timestamp": "2026-11-05T11:59:59Z"}
        rows = [before, status(local_session, "synthetic-game"), status(foreign_session, "other-game"),
                act(2, foreign_session), act(3), act(4), act(8)]
        (self.work / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows) + '{"partial":')

        state = FINAL.finalize(self.request)
        self.assertFalse(state["benchmark_added"])
        stats = FINAL.EXPORT.verify_run(archive)
        self.assertEqual(stats["trajectory"], original["trajectory"])
        self.assertEqual(stats["local_mcp_action_sequences"], 2)
        self.assertEqual(stats["actions"], {"click": 1, "wait": 3, "key": 2, "screenshot": 2})
        self.assertEqual(stats["total_actions"], 8)
        self.assertEqual(stats["total_action_sequences"], 3)
        self.assertEqual(stats["action_sequences"]["mean_length"], 8 / 3)
        self.assertEqual(stats["sessions"], sorted([SESSION, local_session]))
        self.assertEqual([r["commit_id"] for r in stats["git"]["agentscumm_gameplay_runtime"]], ["a" * 40])
        benchmark = (self.repo / "BENCHMARKS.md").read_text()
        self.assertEqual(benchmark.count(stats["trajectory"]["sha256"]), 1)
        self.assertIn("| 8 | 3 <!-- agentscumm:benchmark:", benchmark)
        self.assertIn("| 0.2.0 @ aaaaaaa | Server · act, status |", benchmark)
        self.assertFalse(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertEqual((self.repo / "BENCHMARKS.md").read_text(), benchmark)

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

    def test_unfinished_archive_registers_unknown_progress_without_claiming_unaided_play(self):
        self.source(fixture())
        archive, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo)
        self.assertTrue(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertTrue(FINAL.benchmark_row(FINAL.benchmark_cells(stats)).startswith("| Unknown |"))
        self.assertIn("Unspecified", FINAL.benchmark_row(FINAL.benchmark_cells(stats)))
        self.assertNotIn("Unaided", FINAL.benchmark_row(FINAL.benchmark_cells(stats)))

    def test_unfinished_progress_is_recorded_and_shown_as_an_estimate(self):
        self.source(fixture())
        archive, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo,
            status="stopped", estimated_progress=42.5)
        self.assertEqual(stats["estimated_progress_percent"], 42.5)
        self.assertEqual(FINAL.EXPORT.verify_run(archive)["estimated_progress_percent"], 42.5)
        self.assertTrue(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertIn("| ~42.5% |", (self.repo / "BENCHMARKS.md").read_text())
        for status in ("in_progress", "blocked", "stopped"):
            stats["status"] = status
            self.assertTrue(FINAL.benchmark_row(FINAL.benchmark_cells(stats)).startswith("| ~42.5% |"))

    def test_unfinished_queen_uses_latest_verified_sample_through_the_archive_cutoff(self):
        game = "flight-of-the-amazon-queen"
        profile = self.repo / ".internal" / game / THREAD / "browser"
        (profile / "runs").mkdir(parents=True)
        status = {"sessionId": SESSION, "game": game, "dataDir": str(profile)}
        rows = fixture()
        launch = rows[5]["payload"]["item"]
        launch["arguments"]["game"] = game
        launch["result"] = {"content": [{"type": "text", "text": json.dumps(status)}]}
        self.source(rows)
        ids = ["hotel-escape", "jungle-crossing", "amazon-rescue", "sloth-island", "temple", "valley", "credits"]

        def sample(second, count, label, session=SESSION):
            return {"timestamp": record("unused", {}, second)["timestamp"], "type": "progress",
                    "sessionId": session, "progress": {"game": game, "state": "tracked", "total": 7,
                        "completed": count, "percent": round(count * 100 / 7), "reached": ids[:count],
                        "latest_milestone": label, "map_revision": "a" * 64}}

        log = profile / "runs" / f"{SESSION}.jsonl"
        events = [{"type": "launch", **status}, sample(3, 6, "Cleared the last Valley of Mists obstacle"),
                  sample(5, 2, "Opened the jungle river crossing"),
                  sample(6, 7, "Reached the ending credits", session=THREAD),
                  sample(7, 7, "Reached the ending credits")]
        log.write_text("".join(json.dumps(event) + "\n" for event in events) + '{"incomplete":')

        archive, stats = FINAL.EXPORT.export_run(self.rollout, game, repo=self.repo, status="stopped",
                                                 estimated_progress=85)
        progress = FINAL.EXPORT.verify_run(archive)["verified_progress"]
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["map_revision"], "a" * 64)
        log.unlink()
        self.assertTrue(FINAL.register_benchmark(archive, repo=self.repo))
        benchmark = self.repo / "BENCHMARKS.md"
        self.assertIn("| 2/7 · Opened the jungle river crossing |", benchmark.read_text())
        self.assertFalse(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertNotIn(str(profile), benchmark.read_text())
        for run_status in ("in_progress", "blocked", "stopped"):
            self.assertTrue(FINAL.benchmark_row(FINAL.benchmark_cells({**stats, "status": run_status})).startswith("| 2/7 · "))
        stats["verified_progress"]["completed"] = 7
        (archive / "run.json").write_text(json.dumps(stats))
        with self.assertRaisesRegex(ValueError, "milestone count"):
            FINAL.EXPORT.verify_run(archive)

    def test_local_queen_session_progress_is_discovered_without_a_native_mcp_result(self):
        game = "flight-of-the-amazon-queen"
        work = self.repo / ".internal" / game / THREAD
        profile = work / "browser"
        (profile / "runs").mkdir(parents=True)
        status = {"sessionId": SESSION, "game": game, "dataDir": str(profile)}
        when = record("unused", {}, 4)["timestamp"]
        event = {"timestamp": when, "result": {"name": "status", "isError": False, "content": [status]}}
        (work / "events.jsonl").write_text(json.dumps(event) + "\n")
        sample = {"type": "progress", "sessionId": SESSION, "timestamp": when,
                  "progress": {"game": game, "state": "tracked", "total": 7, "completed": 0,
                               "percent": 0, "reached": [], "latest_milestone": None}}
        log = profile / "runs" / f"{SESSION}.jsonl"
        log.write_text(json.dumps({"type": "launch", **status}) + "\n" + json.dumps(sample) + "\n")
        rows = fixture()
        rows[5]["payload"]["item"]["arguments"]["game"] = game
        self.source(rows)
        _, stats = FINAL.EXPORT.export_run(self.rollout, game, repo=self.repo, status="blocked")
        self.assertTrue(FINAL.benchmark_row(FINAL.benchmark_cells(stats)).startswith("| 0/7 · No milestones verified |"))
        sample["progress"] = {"game": game, "state": "unavailable", "total": 7, "reason": "missing_game_variables"}
        with log.open("a") as output:
            output.write(json.dumps(sample) + "\n")
        context = work / "initial-context.json"
        context.write_text(json.dumps({"schema_version": 1}))
        _, unavailable = FINAL.EXPORT.export_run(self.rollout, game, repo=self.repo, status="blocked",
            run_id="after-restore", agent_context=context, estimated_progress=25)
        self.assertNotIn("verified_progress", unavailable)
        self.assertTrue(FINAL.benchmark_row(FINAL.benchmark_cells(unavailable)).startswith("| ~25% |"))

    def test_comparison_table_appends_runs_as_contiguous_rows_and_preserves_notes(self):
        self.source(fixture(complete=True))
        first = FINAL.finalize(self.request)
        path = self.repo / "BENCHMARKS.md"
        path.write_text(path.read_text() + "\nEditorial note retained.\n")
        rows = fixture(complete=True)
        rows[2]["payload"]["model"] = "comparison-model"
        rows[6]["payload"]["item"]["arguments"]["actions"].append({"type": "wait", "milliseconds": 1})
        self.source(rows)
        second, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo,
            run_id="second", status="completed", completion_note="Ending observed")
        self.assertTrue(FINAL.register_benchmark(second, repo=self.repo, title="Synthetic Adventure"))
        text = path.read_text()
        lines = text.splitlines()
        position = lines.index(FINAL.BENCHMARK_HEADER)
        table = lines[position:position + 4]
        self.assertEqual(text.count(FINAL.BENCHMARK_HEADER), 1)
        self.assertTrue(all(line.startswith("|") for line in table))
        parsed = [[cell.strip() for cell in re.sub(r"<!--.*?-->", "", line).split("|")[1:-1]] for line in table[2:]]
        self.assertEqual(len(parsed), 2)
        self.assertTrue(all(len(row) == 13 for row in parsed))
        self.assertEqual(parsed[0][0], "✅")
        self.assertEqual(parsed[0][2], "test-model / high")
        self.assertEqual(parsed[1][2], "comparison-model / high")
        self.assertEqual(parsed[0][1], "$0.00")
        self.assertEqual(parsed[1][1], "$0.00")
        self.assertEqual(parsed[0][8:10], ["100 (70.00%)", "20"])
        self.assertEqual(parsed[1][8:10], ["100 (70.00%)", "20"])
        self.assertEqual(parsed[0][5:7], ["2", "1"])
        self.assertEqual(parsed[1][5:7], ["3", "1"])
        self.assertEqual(parsed[1][7], f"{stats['tokens']['total_tokens']:,}")
        self.assertEqual(parsed[1][3], "Unspecified")
        self.assertIn("Editorial note retained.", text)
        self.assertFalse(FINAL.register_benchmark(first["archive"], repo=self.repo))
        self.assertFalse(FINAL.register_benchmark(second, repo=self.repo))
        self.assertEqual(path.read_text(), text)

    def test_comparison_date_is_utc_and_rendering_is_one_row(self):
        self.source(fixture(complete=True))
        archive, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo,
            status="completed", completion_note="Ending observed")
        stats["recorded_through"] = "2026-11-06T00:30:00+01:00"
        row = FINAL.benchmark_row(FINAL.benchmark_cells(stats))
        parsed = [cell.strip() for cell in re.sub(r"<!--.*?-->", "", row).split("|")[1:-1]]
        self.assertEqual(parsed[0], "✅")
        self.assertEqual(parsed[10].split()[0], "2026-11-05")
        self.assertEqual(parsed[4], "00:00:07")
        self.assertEqual(len(row.splitlines()), 1)
        self.assertNotIn("Europe/", row)

    def test_game_sections_sort_by_cutoff_even_when_runs_register_out_of_order(self):
        self.source(fixture())
        _, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo)
        text = "# Benchmarks\n\nEditorial note.\n"
        for digest, model, game_id, cutoff in [
            ("a", "Middle", "synthetic-game", "2026-11-08T09:00:00Z"),
            ("b", "Other", "another-game", "2026-11-09T11:00:00Z"),
            ("c", "Oldest", "synthetic-game", "2026-11-07T11:00:00Z"),
            ("d", "Newest", "synthetic-game", "2026-11-08T11:00:00Z"),
        ]:
            run = {**stats, "game_id": game_id, "recorded_through": cutoff, "models": [model],
                   "trajectory": {"sha256": digest * 64}}
            text, added = FINAL.update_benchmark(text, run, None)
            self.assertTrue(added)
        self.assertEqual(text.count("## Synthetic Game"), 1)
        self.assertEqual(text.count("## Another Game"), 1)
        self.assertLess(text.index("Newest"), text.index("Middle"))
        self.assertLess(text.index("Middle"), text.index("Oldest"))
        self.assertLess(text.index("Oldest"), text.index("## Another Game"))
        self.assertIn("Editorial note.", text)

    def test_benchmark_excludes_attached_preflight_when_gameplay_used_only_local_mcp(self):
        attached = {"commit_id": "a" * 40, "dirty": True}
        local = {"commit_id": "b" * 40, "dirty": False, "mcp_version": "0.2.0", "mcp_tools": ["act", "status"]}
        stats = {"git": {"agentscumm_runtime": [attached], "agentscumm_gameplay_runtime": [local]},
                 "local_mcp_action_sequences": 4, "mcp_tool_calls": {"agentscumm.status": 1}}
        self.assertEqual(FINAL.benchmark_mcp(stats), ("0.2.0 @ bbbbbbb", "Server · act, status"))

    def test_legacy_table_upgrade_preserves_rows_notes_and_registration_identity(self):
        self.source(fixture(complete=True))
        archive, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo,
            status="completed", completion_note="Ending observed")
        marker = f"<!-- agentscumm:benchmark:{stats['trajectory']['sha256']} -->"
        row = f"| ✅ | Synthetic Game | 2026-11-05 | test-model / high | Unspecified | 00:00:07 | 120 | 2 | 1 {marker} |\n"
        path = self.repo / "BENCHMARKS.md"
        path.write_text("# Benchmarks\n\n" + FINAL.LEGACY_BENCHMARK_HEADER + "\n"
                        + FINAL.LEGACY_BENCHMARK_SEPARATOR + "\n" + row + "\nEditorial note preserved.\n")
        self.assertFalse(FINAL.register_benchmark(archive, repo=self.repo))
        upgraded = path.read_text()
        self.assertEqual(upgraded.count("<!-- agentscumm:benchmark:"), 1)
        self.assertIn(FINAL.BENCHMARK_HEADER, upgraded)
        usage = "| 100 (70.00%) | 20 |"
        self.assertIn(usage, upgraded)
        self.assertIn("## Synthetic Game", upgraded)
        self.assertIn("| ✅ | $0.00 |", upgraded)
        self.assertIn("Editorial note preserved.", upgraded)
        self.assertFalse(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertEqual(path.read_text(), upgraded)
        # A different legacy row may already have been migrated with unknowns.
        unknown = upgraded.replace(usage, "| Unknown | Unknown |").replace("| $0.00 |", "| Unknown |")
        path.write_text(unknown)
        self.assertFalse(FINAL.register_benchmark(archive, repo=self.repo))
        self.assertEqual(path.read_text(), upgraded)

    def test_previous_table_backfills_cache_percentages_without_repricing(self):
        original = "\n".join([
            FINAL.PREVIOUS_BENCHMARK_HEADER,
            FINAL.PREVIOUS_BENCHMARK_SEPARATOR,
            "| ✅ | Example | 2000-01-01 | test-model | Yes | 00:01:00 | 1,320 | 1,300 | 20 | 800 | $0.25 | 4 | 2 |",
            "| Unknown | Example | 2000-01-02 | test-model | Yes | 00:01:00 | 0 | 0 | 0 | 0 | Unknown | 0 | 0 |",
            "| Unknown | Example | 2000-01-03 | test-model | Yes | 00:01:00 | 20 | Unknown | 20 | 0 | Unknown | 0 | 0 |",
            "", FINAL.PREVIOUS_BENCHMARK_PRICING_NOTES,
        ])
        upgraded = FINAL.upgrade_benchmark_table(original)
        self.assertIn(FINAL.FLAT_BENCHMARK_HEADER, upgraded)
        self.assertIn("| Example | $0.25 |", upgraded)
        self.assertIn("| 1,300 (61.54%) | 20 | 2000-01-01 |", upgraded)
        self.assertIn("| 0 (N/A) | 0 | 2000-01-02 |", upgraded)
        self.assertIn("| Unknown | 20 | 2000-01-03 |", upgraded)
        self.assertNotIn(FINAL.PREVIOUS_BENCHMARK_PRICING_NOTES, upgraded)
        self.assertEqual(FINAL.upgrade_benchmark_table(upgraded), upgraded)

    def test_annotated_table_moves_cost_forward_and_strips_display_labels(self):
        marker = f"<!-- agentscumm:benchmark:{'a' * 64} -->"
        original = "\n".join([
            FINAL.ANNOTATED_BENCHMARK_HEADER,
            FINAL.PREVIOUS_BENCHMARK_SEPARATOR,
            "| ✅ | Example | 2000-01-01 | test-model | Yes | 00:01:00 | 1,320 | 1,300 | 20 | 800 (61.54%) | "
            f"$0.25 (Standard assumed) · rates 2000-01-02 | 4 | 2 {marker} |",
            "", "Editorial note retained.",
        ])
        upgraded = FINAL.upgrade_benchmark_table(original)
        self.assertIn(FINAL.FLAT_BENCHMARK_HEADER, upgraded)
        self.assertIn(f"| ✅ | Example | $0.25 | test-model | Yes | 00:01:00 | 4 | 2 {marker} | "
                      "1,320 | 1,300 (61.54%) | 20 | 2000-01-01 |", upgraded)
        self.assertNotIn("Standard assumed", upgraded)
        self.assertNotIn("2000-01-02", upgraded)
        self.assertIn("Editorial note retained.", upgraded)
        self.assertEqual(FINAL.upgrade_benchmark_table(upgraded), upgraded)

    def test_separate_cache_column_is_folded_into_input_tokens(self):
        original = "\n".join([
            FINAL.SEPARATE_CACHE_BENCHMARK_HEADER,
            FINAL.SEPARATE_CACHE_BENCHMARK_SEPARATOR,
            "| ✅ | Example | $0.25 | test-model | Yes | 00:01:00 | 4 | 2 | 1,320 | 1,300 | 20 | 1,000 (76.92%) | 2000-01-01 |",
            "| ✅ | Example | Unknown | test-model | Yes | 00:01:00 | 4 | 2 | 120 | 100 | 20 | Unknown | 2000-01-02 |",
        ])
        upgraded = FINAL.upgrade_benchmark_table(original)
        self.assertIn(FINAL.FLAT_BENCHMARK_HEADER, upgraded)
        self.assertIn("| Example | $0.25 | test-model | Yes | 00:01:00 | 4 | 2 | "
                      "1,320 | 1,300 (76.92%) | 20 | 2000-01-01 |", upgraded)
        self.assertIn("| 100 (Unknown) | 20 | 2000-01-02 |", upgraded)
        self.assertEqual(FINAL.upgrade_benchmark_table(upgraded), upgraded)

    def test_inline_cache_table_moves_cached_label_to_header(self):
        original = "\n".join([
            FINAL.INLINE_CACHE_BENCHMARK_HEADER,
            FINAL.FLAT_BENCHMARK_SEPARATOR,
            "| ✅ | Example | $0.25 | test-model | Yes | 00:01:00 | 4 | 2 | 120 | 100 (70.00% cached) | 20 | 2000-01-01 |",
        ])
        upgraded = FINAL.upgrade_benchmark_table(original)
        self.assertIn("| Input tokens (% cached) | Output tokens |", upgraded)
        self.assertIn("| 120 | 100 (70.00%) | 20 | 2000-01-01 |", upgraded)
        self.assertEqual(FINAL.upgrade_benchmark_table(upgraded), upgraded)

    def test_tokens_header_is_renamed_without_changing_rows(self):
        row = "| ✅ | Example | $0.25 | test-model | Yes | 00:01:00 | 4 | 2 | 120 | 100 (70.00%) | 20 | 2000-01-01 |\n"
        original = FINAL.PERCENTAGE_BENCHMARK_HEADER + "\n" + FINAL.FLAT_BENCHMARK_SEPARATOR + "\n" + row
        upgraded = FINAL.upgrade_benchmark_table(original)
        self.assertIn("| Total tokens | Input tokens (% cached) |", upgraded)
        self.assertEqual(upgraded.splitlines(keepends=True)[2], row)
        self.assertEqual(FINAL.upgrade_benchmark_table(upgraded), upgraded)

    def test_benchmark_cost_rounds_to_cents_and_distinguishes_missing_price(self):
        self.source(fixture())
        _, stats = FINAL.EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo)
        stats.pop("api_cost")
        self.assertEqual(FINAL.benchmark_usage(stats), ["100 (70.00%)", "20", "Unknown"])
        stats["api_cost"] = {"status": "unavailable", "total_api_price_usd": None}
        self.assertEqual(FINAL.benchmark_usage(stats)[-1], "Unknown")
        stats["api_cost"] = {"status": "priced", "total_api_price_usd": "0"}
        self.assertEqual(FINAL.benchmark_usage(stats)[-1], "$0.00")
        stats["api_cost"]["total_api_price_usd"] = "12.34567"
        self.assertEqual(FINAL.benchmark_usage(stats)[-1], "$12.35")
        self.assertEqual(stats["api_cost"]["total_api_price_usd"], "12.34567")


if __name__ == "__main__":
    unittest.main()
