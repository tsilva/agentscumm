"""Synthetic research evidence only: no model calls, browsers, or games."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/agentscumm_autoresearch/scripts/research.py"
spec = importlib.util.spec_from_file_location("autoresearch", SCRIPT)
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        (self.repo / ".gitignore").write_text("/.internal/\n")
        (self.repo / "common.md").write_text("Same model, assistance and opening knowledge.")
        for name in "abc":
            (self.repo / f"{name}.md").write_text(f"Distinct strategy {name}")
        self.commit = "a" * 40
        self.backbone = {"repository": "scummweb", "commit_id": "e" * 40, "dirty": False}
        source = patch.object(research, "clean_backbone", return_value=self.backbone)
        source.start()
        self.addCleanup(source.stop)
        self.provider = {"origin": "http://localhost:9999", "queen_sha256": "b" * 64,
                         "runtime_assets_sha256": "c" * 64, "source_info_sha256": "d" * 64}
        self.dirty = False
        identity = patch.object(research, "prepare_agent", return_value={
            "name": "agentscumm", "version": "1.2.3", "commit_id": self.commit,
            "branch": "main", "dirty": False, "source": "clean_play_preflight"})
        identity.start()
        self.addCleanup(identity.stop)

        def fake_git(repo, *args):
            if args == ("rev-parse", "--show-toplevel"):
                return str(self.repo)
            if args[0] == "status":
                return " M source.js" if self.dirty else ""
            if args[0] == "rev-parse":
                return self.commit
            if args[0] == "branch":
                return "main"
            if args[0] == "check-ignore":
                return args[-1]
            if args[0] == "ls-files":
                return ""
            raise AssertionError(args)

        self.mock_git = patch.object(research, "git", side_effect=fake_git)
        self.mock_git.start()
        self.addCleanup(self.mock_git.stop)

    def prepare(self, count=3, previous=None):
        args = argparse.Namespace(strategy=[str(self.repo / f"{n}.md") for n in "abc"[:count]],
                                  common_context=str(self.repo / "common.md"), timeout_seconds=600,
                                  model_context="same inherited model/effort", previous_round=previous,
                                  base_url=self.provider["origin"])
        prepared = research.init_round(self.repo, args, probe=lambda origin: self.provider)
        self.round_dir = Path(prepared["round_directory"])
        self.manifest = research.read_json(self.round_dir / "round.json")
        return prepared

    def evidence(self, index, count, status="finished", stopped=False, review=True):
        item = self.manifest["trials"][index]
        trial = Path(item["directory"])
        dispatch = research.dispatch_trial(self.round_dir, item["id"], self.repo)
        start = research.timestamp(dispatch["dispatched_at"])
        at = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
        session = f"session-{index}"
        revision = dict(self.backbone)
        actions = [{"type": "click", "x": 10, "y": 10} for _ in range(count - 1)] + [{"type": "wait", "milliseconds": 100}]
        steps = [{"index": n, "type": a["type"], "state": "delivered"} for n, a in enumerate(actions)]
        if stopped:
            steps = steps[:1] + [{"index": 1, "type": actions[1]["type"], "state": "interrupted"}]
        logs = [{"type": "launch", "timestamp": at(1), "sessionId": session, "game": research.GAME,
                 "mode": "skip_intro", "sourceRevision": revision,
                 "url": self.provider["origin"] + "/" + research.GAME + "/play#seed=0"},
                {"type": "action-start", "timestamp": at(2), "sessionId": session,
                 "requestId": "one", "actionId": "action-one", "total": len(actions), "inputs": actions},
                {"type": "action-finish", "timestamp": at(3), "sessionId": session,
                 "requestId": "one", "actionId": "action-one", "state": "stopped" if stopped else "completed", "steps": steps}]
        server = trial / "browser/runs"
        server.mkdir(parents=True)
        (server / "session.jsonl").write_text("".join(json.dumps(row) + "\n" for row in logs))
        (trial / "events.jsonl").write_text("\n".join(json.dumps({"request": {"name": "act"}, "result": {"isError": False}}) for _ in range(2)) + "\n")
        research.write_json(trial / "worker-result.json", {"status": status, "client_exit_code": 0,
                            "process_exited": True, "server_process_exited": True, "summary": "Synthetic fixture"})
        variables = {"engine_id": "queen", "room_id": 71, "room_ready": True}
        image = trial / "result.json.jpg"
        image.write_bytes(b"synthetic audit fixture, no actual game")
        receipt = {"event": "success", "success": True, "reason": "stop_condition", "game": research.GAME,
                   "condition": research.CONDITION, "variables": variables, "session_id": session,
                   "source_revision": revision, "action_sequences": 1, "detected_at": at(4),
                   "elapsed_ms": 4000, "screenshot": str(image)}
        research.write_json(trial / "result.json", receipt)
        telemetry = [{"event": "variables", "timestamp": at(1), "variables": {"room_id": 70, "room_ready": True}},
                     {k: v for k, v in receipt.items() if k != "screenshot"}]
        (trial / "variables.jsonl").write_text("".join(json.dumps(row) + "\n" for row in telemetry))
        if review:
            research.write_json(trial / "review.json", {"endpoint_verified": True,
                                "receipt_sha256": research.sha((trial / "result.json").read_bytes()),
                                "screenshot_sha256": research.sha(image.read_bytes())})
        return trial

    def score(self, provider=None):
        research.score_round(self.round_dir, self.repo, probe=lambda origin: provider or self.provider)
        return research.read_json(self.round_dir / "scores.json")

    def test_prepare_does_not_start_players_and_pins_fresh_isolated_runs(self):
        result = self.prepare()
        self.assertFalse(result["players_started"])
        directories = [Path(r["directory"]) for r in result["trials"]]
        self.assertEqual(len(set(directories)), 3)
        for trial in directories:
            manifest = research.read_json(trial / "run.json")
            self.assertEqual(manifest["git"]["play_start"]["commit_id"], self.commit)
            self.assertFalse((trial / "browser").exists())
            codex_args = research.read_json(trial / "codex-args.json")
            self.assertIn("--ephemeral", codex_args)
            config = tomllib.loads(codex_args[codex_args.index("-c") + 1])["mcp_servers"]["agentscumm"]
            self.assertEqual(config["command"], "node")
            self.assertTrue(config["required"])
            args = config["args"]
            self.assertEqual(Path(args[args.index("--data-dir") + 1]), trial / "browser")
            self.assertNotIn("--no-viewer", args, "Every trial must retain the server's visible spectator default")
            self.assertEqual(json.loads(args[args.index("--stop-condition") + 1]), research.CONDITION)
        with self.assertRaises(ValueError):
            research.dispatch_trial(self.round_dir, "missing", self.repo)

    def test_dirty_checkout_blocks_preparation(self):
        self.dirty = True
        with self.assertRaisesRegex(ValueError, "clean checkout"):
            self.prepare()
        self.assertFalse((self.repo / ".internal").exists())

    def test_failure_cannot_win_and_cached_calls_do_not_inflate_accepted_inputs(self):
        self.prepare()
        self.evidence(0, 10)
        self.evidence(1, 7, stopped=True)
        self.evidence(2, 1, status="timeout")
        result = self.score()
        self.assertEqual(result["best_trial_ids"], ["b"])
        self.assertEqual(result["best_total_actions"], 7)
        self.assertEqual(result["trials"][1]["act_tool_calls"], 2)
        self.assertEqual(result["trials"][1]["action_sequences"], 1)
        self.assertEqual(result["trials"][1]["delivered_steps"], 1)
        self.assertEqual(research.read_json(self.round_dir / "round.json")["state"], "awaiting_confirmation")
        with self.assertRaisesRegex(ValueError, "already has a report"):
            self.score()

    def test_background_success_is_valid_and_equal_actions_remain_tied(self):
        self.prepare(2)
        self.evidence(0, 6)
        self.evidence(1, 6)
        self.assertEqual(self.score()["best_trial_ids"], ["a", "b"])

    def test_missing_review_and_changed_strategy_cannot_win(self):
        self.prepare(2)
        self.evidence(0, 2, review=False)
        trial = self.evidence(1, 3)
        (trial / "strategy.md").write_text("Changed mid-trial")
        self.assertEqual(self.score()["best_trial_ids"], [])

    def test_orphan_server_keeps_round_pending(self):
        self.prepare(1)
        trial = self.evidence(0, 3)
        result = research.read_json(trial / "worker-result.json")
        result["server_process_exited"] = False
        (trial / "worker-result.json").write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "processes have not exited"):
            self.score()
        self.assertFalse((self.round_dir / "scores.json").exists())

    def test_provider_drift_invalidates_comparison(self):
        self.prepare(1)
        self.evidence(0, 4)
        result = self.score({**self.provider, "queen_sha256": "different"})
        self.assertEqual(result["best_trial_ids"], [])
        self.assertTrue(result["comparison_issues"])

    def test_late_success_is_not_eligible(self):
        self.prepare(1)
        trial = self.evidence(0, 4)
        dispatch = research.read_json(trial / "dispatch.json")
        dispatch["deadline"] = dispatch["dispatched_at"]
        (trial / "dispatch.json").write_text(json.dumps(dispatch))
        self.assertEqual(self.score()["best_trial_ids"], [])

    def test_new_round_has_fresh_paths_and_preserves_lineage(self):
        self.prepare(1)
        self.evidence(0, 4)
        self.score()
        previous = self.round_dir
        prepared = self.prepare(1, str(previous))
        self.assertNotEqual(prepared["round_directory"], str(previous))
        self.assertEqual(self.manifest["previous_round"], str(previous))

    def test_extended_dispatch_budget_cannot_override_frozen_round_limit(self):
        self.prepare(1)
        trial = self.evidence(0, 4)
        dispatch = research.read_json(trial / "dispatch.json")
        dispatch["deadline"] = (research.timestamp(dispatch["dispatched_at"]) + timedelta(seconds=3600)).isoformat()
        (trial / "dispatch.json").write_text(json.dumps(dispatch))
        result = self.score()
        self.assertEqual(result["best_trial_ids"], [])
        self.assertIn("frozen round limit", result["trials"][0]["issues"][0])

    def test_contradictory_terminal_telemetry_cannot_win(self):
        self.prepare(1)
        trial = self.evidence(0, 4)
        log = trial / "variables.jsonl"
        telemetry = research.rows(log)
        telemetry[-1]["variables"]["room_id"] = 999
        log.write_text("".join(json.dumps(row) + "\n" for row in telemetry))
        result = self.score()
        self.assertEqual(result["best_trial_ids"], [])
        self.assertIn("contradicts", result["trials"][0]["issues"][0])


if __name__ == "__main__":
    unittest.main()
