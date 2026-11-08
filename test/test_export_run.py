"""Synthetic end-to-end checks for portable conversation exports."""

import base64
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("export_run", ROOT / "scripts/export-run.py")
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
THREAD = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"


def record(kind, payload, second=0):
    return {"timestamp": f"2026-11-05T12:00:{second:02d}Z", "type": kind, "payload": payload}


def rates_fixture():
    return {"retrieved_on": datetime.now(timezone.utc).date().isoformat(),
            "sources": ["https://developers.openai.com/api/docs/pricing"],
            "models": {model: {tier: {"bands": [
                {"max_input_tokens": 272000, "input": str(multiplier * 2),
                 "cached_input": str(multiplier * 0.1), "output": str(multiplier * 10)},
                {"max_input_tokens": None, "input": str(multiplier * 4),
                 "cached_input": str(multiplier * 0.2), "output": str(multiplier * 15)}]}
                for tier, multiplier in (("standard", 1), ("priority", 2))}
                for model in ("test-model", "comparison-model")}}


def fixture(complete=False):
    usage = {"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 20,
             "reasoning_output_tokens": 5, "cache_write_input_tokens": 0, "total_tokens": 120}
    frame = {"id": "frame-42", "sessionId": SESSION, "mimeType": "image/png",
             "width": 1, "height": 1, "timestamp": "2026-11-05T12:00:03Z", "actionId": "action-1"}
    image = {"type": "image", "mimeType": "image/png", "data": PNG}
    content = [{"type": "text", "text": json.dumps(frame)}, image]
    rows = [
        record("session_meta", {"id": THREAD, "timestamp": "2026-11-05T12:00:00Z",
            "model_provider": "openai", "originator": "Codex Desktop", "cli_version": "test-version", "source": "vscode"}),
        record("event_msg", {"type": "task_started", "turn_id": "turn-1"}),
        record("turn_context", {"turn_id": "turn-1", "model": "test-model", "effort": "high"}),
        record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Play the game"}]}, 1),
        record("response_item", {"type": "reasoning", "encrypted_content": "opaque-record-kept-verbatim"}, 2),
        record("event_msg", {"type": "item_completed", "turn_id": "turn-1", "item": {
            "type": "McpToolCall", "id": "launch-1", "server": "agentscumm", "tool": "launch_game",
            "arguments": {"game": "synthetic-game"}}}, 2),
        record("event_msg", {"type": "item_completed", "turn_id": "turn-1", "started_at_ms": 1000,
            "completed_at_ms": 1500, "item": {"type": "McpToolCall", "id": "act-1", "server": "agentscumm",
                "tool": "act", "arguments": {"session_id": SESSION, "request_id": "step-1",
                    "actions": [{"type": "click", "x": 1, "y": 1}, {"type": "wait", "milliseconds": 100}]},
                "result": {"content": [{"type": "text", "text": json.dumps({"content": content})}]}}}, 3),
        record("response_item", {"type": "custom_tool_call_output", "call_id": "call-1", "output": [
            {"type": "input_image", "image_url": "data:image/png;base64," + PNG, "detail": "original"}]}, 4),
        record("compacted", {"message": "State summary", "replacement_history": [{"type": "message",
            "role": "tool", "content": content}]}, 5),
        record("token_usage_record", {"thread_id": THREAD, "turn_id": "turn-1", "response_id": "response-1",
            "usage": dict(usage), "thread_token_usage": dict(usage)}, 6),
    ]
    if complete:
        rows.append(record("event_msg", {"type": "task_complete", "last_agent_message": "The ending is visible."}, 7))
    return rows


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        (self.repo / ".gitignore").write_text("/runs/\n")
        self.rollout = self.repo / "source.jsonl"
        lookup = patch.object(EXPORT.PRICING, "fetch_rates", return_value=rates_fixture())
        lookup.start()
        self.addCleanup(lookup.stop)

    def source(self, rows=None, trailing=b""):
        self.rows = fixture() if rows is None else rows
        self.rollout.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in self.rows) + trailing)
        return self.rollout

    def export(self, **kwargs):
        return EXPORT.export_run(self.rollout, "synthetic-game", repo=self.repo, **kwargs)

    def test_merged_session_tool_and_screenshot_steps_are_archived(self):
        rows = fixture()
        rows[5]["payload"]["item"].update(tool="session",
            arguments={"operation": "start", "game": "synthetic-game"})
        rows[6]["payload"]["item"]["arguments"]["actions"].append({"type": "screenshot"})
        self.source(rows)
        path, stats = self.export(run_id="merged-tools")
        self.assertEqual(stats["actions"]["screenshot"], 1)
        self.assertEqual(stats["total_actions"], 3)
        self.assertEqual(stats["action_sequences"]["max_length"], 3)
        self.assertEqual(EXPORT.verify_run(path), stats)
        rows[5]["payload"]["item"]["arguments"]["game"] = "different-game"
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "different game"):
            self.export(run_id="wrong-game")

    def test_round_trip_preserves_conversation_and_links_all_image_copies(self):
        self.source()
        original = self.rollout.read_bytes()
        path, stats = self.export()
        self.assertEqual(path.name, f"20261105-{THREAD}")
        self.assertEqual(self.rollout.read_bytes(), original)
        rows = [json.loads(line) for line in (path / "trajectory.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), len(self.rows))
        self.assertEqual(rows[3], self.rows[3], "User messages must be retained")
        self.assertEqual(rows[4], self.rows[4], "Opaque reasoning records must be retained")
        self.assertNotIn(PNG, (path / "trajectory.jsonl").read_text())
        self.assertEqual(stats["screenshots"]["unique_files"], 1)
        self.assertEqual(stats["screenshots"]["references_in_trajectory"], 3)
        self.assertEqual(stats["screenshots"]["model_visible_blocks"], 1)
        self.assertEqual(stats["screenshots"]["original_frame_ids"], 1)
        entry = json.loads((path / "screenshots/index.json").read_text())["screenshots"][0]
        self.assertEqual((path / entry["path"]).read_bytes(), base64.b64decode(PNG))
        self.assertEqual(entry["frames"][0]["id"], "frame-42")
        self.assertEqual(stats["tokens"]["uncached_input_tokens"], 30)
        self.assertEqual(stats["tokens"]["uncached_input_plus_output_tokens"], 50)
        self.assertEqual(stats["tokens"]["total_tokens"], 120)
        self.assertEqual(stats["models"], ["test-model"])
        self.assertEqual(stats["reasoning_levels"], ["high"])
        self.assertEqual(stats["harness"]["version"], "test-version")
        self.assertEqual(stats["actions"], {"click": 1, "wait": 1})
        self.assertEqual(stats["total_actions"], 2)
        self.assertEqual(stats["total_action_sequences"], 1)
        self.assertEqual(stats["action_sequences"], {"count": 1, "min_length": 2,
            "max_length": 2, "mean_length": 2.0, "std_length": 0.0,
            "median_length": 2, "std_type": "population"})
        self.assertEqual(EXPORT.verify_run(path), stats)
        self.assertEqual((path / "run.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((path / ".source.jsonl").exists())

    def test_completed_requires_terminal_turn_and_ending_note(self):
        self.source()
        with self.assertRaisesRegex(ValueError, "still active"):
            self.export(status="completed", completion_note="Ending shown")
        self.source(fixture(complete=True))
        with self.assertRaisesRegex(ValueError, "note describing"):
            self.export(status="completed")
        path, stats = self.export(status="completed", completion_note="Ending and credits observed")
        self.assertEqual(stats["status"], "completed")
        self.assertEqual(EXPORT.verify_run(path)["completion_note"], "Ending and credits observed")

    def skill(self, name="synthetic_play-game", parent=None):
        directory = (parent or self.repo / ".agents/skills") / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\nRead references/guide.md.\n")
        (directory / "references").mkdir()
        (directory / "references/guide.md").write_text("Synthetic guidance\n")
        (directory / "scripts").mkdir()
        (directory / "scripts/play.py").write_text("print('synthetic')\n")
        (directory / "scripts/play.py").chmod(0o755)
        (directory / "__pycache__").mkdir()
        (directory / "__pycache__/ignored.pyc").write_bytes(b"cache")
        return directory

    def test_skill_bundle_preserves_dependencies_and_survives_source_removal(self):
        skill = self.skill()
        alias = self.repo / "discovered-skill"
        alias.symlink_to(skill, target_is_directory=True)
        rows = fixture()
        rows[0]["payload"]["cwd"] = str(self.repo)
        rows.insert(-1, record("response_item", {"type": "custom_tool_call", "name": "functions.exec",
            "input": 'await tools.exec_command({"cmd":"cat ' + str(alias / "SKILL.md") + '"})'}, 5))
        rows.insert(-1, record("response_item", {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Using $synthetic_play-game."}]}, 5))
        self.source(rows)
        path, stats = self.export()
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["files"], 3)
        self.assertEqual(stats["skills"]["unresolved"], [])
        index = json.loads((path / "skills/index.json").read_text())
        self.assertFalse(index["historical_version_verified"])
        bundle = index["skills"][0]
        self.assertTrue(next(item for item in bundle["files"] if item["path"] == "scripts/play.py")["executable"])
        (skill / "references/guide.md").unlink()
        self.assertEqual((path / bundle["path"] / "references/guide.md").read_text(), "Synthetic guidance\n")
        self.assertEqual(EXPORT.verify_run(path), stats)
        (path / bundle["path"] / "SKILL.md").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Skill file checksum"):
            EXPORT.verify_run(path)

    def test_skill_discovery_skips_catalogs_instructions_results_and_shell_variables(self):
        rows = fixture()
        for role, text in (("developer", "- available: /missing/catalog/SKILL.md"),
                           ("user", "# AGENTS.md instructions\nUse $unrelated_skill /missing/instructions/SKILL.md")):
            rows.insert(-1, record("response_item", {"type": "message", "role": role,
                "content": [{"type": "input_text", "text": text}]}, 5))
        rows.insert(-1, record("response_item", {"type": "function_call_output",
            "output": "See /missing/result/SKILL.md and $unrelated_skill"}, 5))
        rows.insert(-1, record("response_item", {"type": "function_call", "arguments": json.dumps({"cmd": "echo $run_dir"})}, 5))
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["skills"]["references"], 0)
        self.assertEqual(stats["skills"]["bundles"], 0)

    def test_skill_cwd_changes_and_quoted_paths_with_spaces(self):
        other = self.repo / "another checkout"
        skill = self.skill(parent=other / ".agents/skills")
        rows = fixture()
        rows.insert(-1, record("turn_context", {"cwd": str(other), "model": "test-model", "effort": "high"}, 5))
        rows.insert(-1, record("event_msg", {"type": "item_completed", "item": {"type": "McpToolCall",
            "arguments": {"command": "cat .agents/skills/synthetic_play-game/SKILL.md"}}}, 5))
        rows.insert(-1, record("response_item", {"type": "function_call",
            "arguments": json.dumps({"cmd": "cat '" + str(skill / "SKILL.md") + "'"})}, 5))
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["references"], 2)
        self.assertEqual(stats["skills"]["unresolved"], [])

    def test_skill_command_workdir_overrides_conversation_checkout(self):
        self.skill()
        rows = fixture()
        rows[0]["payload"]["cwd"] = str(self.repo / "different-checkout")
        rows.insert(-1, record("response_item", {"type": "custom_tool_call", "name": "exec",
            "input": 'await tools.exec_command({cmd:"cat .agents/skills/synthetic_play-game/SKILL.md",workdir:'
                     + json.dumps(str(self.repo)) + '});'}, 5))
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["unresolved"], [])

    def test_missing_skill_blocks_completed_export_and_explicit_path_resolves_name(self):
        rows = fixture(complete=True)
        rows.insert(-2, record("response_item", {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Use $synthetic_play-game"}]}, 5))
        self.source(rows)
        with patch.object(Path, "home", return_value=self.repo / "empty-home"):
            with self.assertRaisesRegex(ValueError, "unavailable skill"):
                self.export(status="completed", completion_note="Ending visible")
            path, stats = self.export(run_id="missing-skill")
            self.assertEqual(len(stats["skills"]["unresolved"]), 1)
            self.assertEqual(EXPORT.verify_run(path), stats)
            skill = self.skill(parent=self.repo / "manual")
            path, stats = self.export(status="completed", completion_note="Ending visible", skills=[skill / "SKILL.md"])
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(stats["skills"]["unresolved"], [])
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_skill_nested_links_are_not_followed_and_prevent_completed_export(self):
        skill = self.skill()
        outside = self.repo / "outside.txt"
        outside.write_text("Do not copy")
        (skill / "references/linked.txt").symlink_to(outside)
        self.source(fixture(complete=True))
        path, stats = self.export(skills=[skill / "SKILL.md"], run_id="linked-skill")
        self.assertEqual(len(stats["skills"]["unresolved"]), 1)
        self.assertFalse(list((path / "skills").rglob("linked.txt")))
        with self.assertRaisesRegex(ValueError, "unavailable skill"):
            self.export(skills=[skill / "SKILL.md"], status="completed", completion_note="Ending visible")

    def test_explicit_skill_disambiguates_local_installations(self):
        first = self.skill()
        self.skill(parent=self.repo / ".codex/skills")
        rows = fixture()
        rows[0]["payload"]["cwd"] = str(self.repo)
        rows.insert(-1, record("response_item", {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Use $synthetic_play-game"}]}, 5))
        self.source(rows)
        with patch.object(Path, "home", return_value=self.repo / "empty-home"):
            _, stats = self.export(run_id="ambiguous")
            self.assertEqual(len(stats["skills"]["unresolved"]), 1)
            path, stats = self.export(skills=[first / "SKILL.md"])
        self.assertEqual(stats["skills"]["unresolved"], [])
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_explicit_skill_resolves_namespaced_invocation_and_identical_bundles_deduplicate(self):
        first = self.skill(parent=self.repo / "one")
        second = self.skill(parent=self.repo / "two")
        rows = fixture()
        rows.insert(-1, record("response_item", {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Using $synthetic:synthetic_play-game"}]}, 5))
        self.source(rows)
        with patch.object(Path, "home", return_value=self.repo / "empty-home"):
            _, stats = self.export(skills=[first / "SKILL.md"])
            self.assertEqual(stats["skills"]["unresolved"], [])
            # Ambiguous locations need explicit path references instead of a name.
            self.source()
            path, stats = self.export(run_id="identical", skills=[first / "SKILL.md", second / "SKILL.md"])
        self.assertEqual(stats["skills"]["bundles"], 1)
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_skill_verifier_rejects_index_tampering_links_and_extra_files(self):
        skill = self.skill()
        self.source()
        path, _ = self.export(skills=[skill / "SKILL.md"])
        index_path = path / "skills/index.json"
        original = index_path.read_bytes()
        index = json.loads(original)
        bundle = path / index["skills"][0]["path"]
        index_path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "Skill index checksum"):
            EXPORT.verify_run(path)
        index_path.write_bytes(original)
        extra = bundle / "extra.txt"
        extra.write_text("Unexpected")
        with self.assertRaisesRegex(ValueError, "Unexpected or missing"):
            EXPORT.verify_run(path)
        extra.unlink()
        guide = bundle / "references/guide.md"
        guide.unlink()
        guide.symlink_to(skill / "references/guide.md")
        with self.assertRaisesRegex(ValueError, "Linked skill"):
            EXPORT.verify_run(path)

    def test_legacy_archive_without_skill_manifest_still_verifies(self):
        self.source()
        path, stats = self.export()
        del stats["skills"]
        del stats["agent_context"]
        del stats["total_actions"]
        del stats["total_action_sequences"]
        stats["schema_version"] = 1
        (path / "run.json").unlink()
        (path / "stats.json").write_text(json.dumps(stats))
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_agent_context_tracks_initial_inputs_and_tools_without_inferring_availability(self):
        rows = fixture()
        rows[0]["payload"]["base_instructions"] = {"text": "Synthetic system instructions"}
        rows.insert(3, record("response_item", {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "### Available skills\nA catalog; search is mentioned in prose."}]}, 1))
        rows.insert(-1, record("response_item", {"type": "custom_tool_call", "name": "exec",
            "input": "const r = await tools.web__run({search_query: [{q: 'synthetic'}]});"}, 5))
        rows.insert(-1, record("response_item", {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": "Late instructions"}]}, 5))
        self.source(rows)
        path, stats = self.export()
        context = stats["agent_context"]
        self.assertEqual(context["tools"]["coverage"], "unavailable")
        self.assertFalse(context["tools"]["inventory_complete"])
        self.assertEqual(context["tools"]["available_at_start"], [])
        observed = {item["name"]: item for item in context["tools"]["observed_during_run"]}
        self.assertEqual(observed["web.run"]["evidence"], ["reference_in_submitted_exec_code"])
        self.assertEqual(observed["mcp.agentscumm.act"]["kind"], "mcp")
        self.assertEqual(len(context["prompt_assets"]), 3)
        self.assertIn(["skill_catalog"], [a["kinds"] for a in context["prompt_assets"]])
        self.assertTrue(all(asset["record"] < context["initial_prompt_boundary"]["before_record"] for asset in context["prompt_assets"]))
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_agent_context_extracts_recorded_initial_tool_definitions(self):
        rows = fixture()
        rows[0]["payload"]["dynamic_tools"] = [{"name": "mcp__agentscumm__act", "inputSchema": {"type": "object"}}]
        rows.insert(3, record("response_item", {"type": "message", "role": "system", "content": [
            {"type": "input_text", "text": "## Namespace: web\n\ntype run = (_: {}) => any;\n"}]}, 1))
        self.source(rows)
        _, stats = self.export()
        tools = stats["agent_context"]["tools"]
        self.assertEqual(tools["coverage"], "partial")
        self.assertEqual({t["name"] for t in tools["available_at_start"]}, {"web.run", "mcp.agentscumm.act"})
        self.assertFalse(tools["inventory_complete"], "Recorded subsets never imply a complete inventory")

    def test_supplied_agent_context_bundles_prompt_assets_and_labels_declarations(self):
        self.source()
        prompt = self.repo / "walkthrough.txt"
        prompt.write_text("Synthetic injected walkthrough")
        declaration = self.repo / "context.json"
        declaration.write_text(json.dumps({"schema_version": 1, "tools": {
            "inventory_complete": True, "available_at_start": [{"name": "web.run", "kind": "builtin"},
            {"name": "mcp__agentscumm__act", "kind": "mcp"}]}, "prompt_assets": [
            {"name": "Walkthrough", "kind": "walkthrough", "path": "walkthrough.txt"}]}))
        path, stats = self.export(agent_context=declaration)
        context = stats["agent_context"]
        self.assertEqual(context["tools"]["coverage"], "operator_declared_complete")
        asset = next(a for a in context["prompt_assets"] if a.get("name") == "Walkthrough")
        self.assertEqual(asset["provenance"], "operator_declared_initial")
        self.assertFalse(asset["historical_version_verified"])
        prompt.unlink()
        declaration.unlink()
        self.assertEqual(EXPORT.verify_run(path), stats)
        (path / asset["path"]).write_text("Changed")
        with self.assertRaisesRegex(ValueError, "Prompt asset checksum"):
            EXPORT.verify_run(path)

    def test_initial_prompt_checksums_are_verified_and_late_images_are_not_initial(self):
        self.source()
        path, stats = self.export()
        self.assertEqual(len(stats["agent_context"]["prompt_assets"]), 1)
        stats["agent_context"]["prompt_assets"][0]["sha256"] = "0" * 64
        (path / "run.json").write_text(json.dumps(stats))
        with self.assertRaisesRegex(ValueError, "Initial prompt asset checksum"):
            EXPORT.verify_run(path)

    def test_invalid_context_prevents_publication_and_new_exports_use_run_json(self):
        self.source()
        declaration = self.repo / "context.json"
        declaration.write_text('{"schema_version": 0}')
        with self.assertRaisesRegex(ValueError, "Unsupported supplied agent context"):
            self.export(agent_context=declaration)
        self.assertFalse(list((self.repo / "runs/synthetic-game").iterdir()))
        path, stats = self.export()
        self.assertEqual(stats["schema_version"], 3)
        self.assertTrue((path / "run.json").is_file())
        self.assertFalse((path / "stats.json").exists())
        (path / "run.json").unlink()
        (path / "run.json").symlink_to(self.repo / "nonexistent.json")
        with self.assertRaisesRegex(ValueError, "Linked run metadata"):
            EXPORT.verify_run(path)

    def test_context_verification_rejects_fabricated_initial_tools_and_observed_use(self):
        self.source()
        path, stats = self.export()
        original = json.dumps(stats)
        stats["agent_context"]["tools"]["available_at_start"] = [{
            "name": "web.run", "kind": "builtin", "provenance": "recorded_initial_schema", "record": 1}]
        stats["agent_context"]["tools"]["coverage"] = "partial"
        (path / "run.json").write_text(json.dumps(stats))
        with self.assertRaisesRegex(ValueError, "Recorded tool inventory mismatch"):
            EXPORT.verify_run(path)
        stats = json.loads(original)
        stats["agent_context"]["tools"]["observed_during_run"] = []
        (path / "run.json").write_text(json.dumps(stats))
        with self.assertRaisesRegex(ValueError, "Recorded tool inventory mismatch"):
            EXPORT.verify_run(path)

    def test_sequence_distribution_counts_submitted_inputs_once_per_tool_event(self):
        rows = fixture()
        # The existing sequence has length 2; add lengths 1, 3, 4 and duplicate
        # telemetry for the existing call. Non-act tools must not contribute.
        for length in (1, 3, 4):
            rows.insert(-1, record("event_msg", {"type": "item_completed", "turn_id": "turn-1",
                "item": {"type": "McpToolCall", "id": f"sequence-{length}", "server": "agentscumm",
                    "tool": "act", "arguments": {"actions": [{"type": "wait", "milliseconds": 1}] * length}}}, 5))
        rows.insert(-1, json.loads(json.dumps(rows[6])))
        rows.insert(-1, record("event_msg", {"type": "item_completed", "turn_id": "turn-1",
            "item": {"type": "McpToolCall", "id": "observe-1", "server": "agentscumm",
                "tool": "observe", "arguments": {}}}, 5))
        self.source(rows)
        _, stats = self.export()
        seq = stats["action_sequences"]
        self.assertEqual(seq["count"], 4)
        self.assertEqual(seq["min_length"], 1)
        self.assertEqual(seq["max_length"], 4)
        self.assertEqual(seq["mean_length"], 2.5)
        self.assertEqual(seq["median_length"], 2.5)
        self.assertAlmostEqual(seq["std_length"], 1.118033988749895)
        self.assertEqual(stats["mcp_tool_calls"]["agentscumm.act"], 4)
        self.assertEqual(stats["total_actions"], 10)
        self.assertEqual(stats["total_action_sequences"], 4)

    def test_no_action_sequences_exports_null_distribution(self):
        rows = fixture()
        rows[6]["payload"]["item"].update(tool="observe", arguments={})
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["total_actions"], 0)
        self.assertEqual(stats["total_action_sequences"], 0)
        self.assertEqual(stats["action_sequences"], {"count": 0, "min_length": None,
            "max_length": None, "mean_length": None, "std_length": None,
            "median_length": None, "std_type": "population"})

    def test_action_totals_must_match_recorded_breakdowns(self):
        self.source()
        path, stats = self.export()
        for field in ("total_actions", "total_action_sequences"):
            for invalid in (stats[field] + 1, True):
                with self.subTest(field=field, invalid=invalid):
                    changed = {**stats, field: invalid}
                    (path / "run.json").write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, field + " count mismatch"):
                        EXPORT.verify_run(path)

    def test_progress_estimates_are_bounded_and_completed_runs_record_100_percent(self):
        self.source(fixture(complete=True))
        for invalid in (-1, 101, float("nan"), float("inf"), True, "40"):
            with self.subTest(estimated_progress=invalid):
                with self.assertRaisesRegex(ValueError, "Estimated progress"):
                    self.export(estimated_progress=invalid)
        with self.assertRaisesRegex(ValueError, "100 percent"):
            self.export(status="completed", completion_note="Ending observed", estimated_progress=40)
        path, stats = self.export(status="completed", completion_note="Ending observed")
        self.assertEqual(stats["estimated_progress_percent"], 100)
        stats["estimated_progress_percent"] = 99
        (path / "run.json").write_text(json.dumps(stats))
        with self.assertRaisesRegex(ValueError, "100 percent"):
            EXPORT.verify_run(path)

    def test_recorded_commit_is_preserved_separately_from_export_checkout(self):
        rows = fixture()
        rows[0]["payload"]["git"] = {"commit_hash": "a" * 40, "branch": "run-branch",
            "repository_url": "https://example.invalid/game.git"}
        self.source(rows)
        _, stats = self.export()
        revision = stats["git"]["conversation"]
        self.assertEqual(revision["commit_id"], "a" * 40)
        self.assertEqual(revision["branch"], "run-branch")
        self.assertEqual(revision["source"], "session_meta.git")
        self.assertIsNone(revision["dirty"], "An unrecorded startup dirty state must stay unknown")
        self.assertIsNone(stats["git"]["exporter"]["commit_id"], "The empty export checkout has no HEAD")

    def test_runtime_startup_revisions_are_extracted_from_nested_mcp_results(self):
        rows = fixture()
        revision = {"repository": "agentscumm", "commit_id": "b" * 40,
            "branch": "main", "dirty": True, "recorded_at": "2026-11-05T12:00:00Z",
            "source": "server_initialization"}
        result = {"content": [{"type": "text", "text": json.dumps({"sourceRevision": revision})}]}
        rows[5]["payload"]["item"]["result"] = result
        rows[6]["payload"]["item"]["result"]["sourceRevision"] = revision
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["git"]["agentscumm_runtime"], [revision])
        self.assertFalse(any("startup commit was not recorded" in s for s in stats["limitations"]))

    def test_missing_startup_commit_stays_unknown(self):
        self.source()
        _, stats = self.export()
        self.assertIsNone(stats["git"]["conversation"]["commit_id"])
        self.assertEqual(stats["git"]["agentscumm_runtime"], [])
        self.assertTrue(any("startup commit was not recorded" in s for s in stats["limitations"]))

    def test_gameplay_revision_is_selected_by_attached_session(self):
        rows = fixture()
        stale = {"repository": "agentscumm", "commit_id": "a" * 40, "dirty": True}
        played = {"repository": "agentscumm", "commit_id": "b" * 40, "dirty": False,
                  "mcp_version": "0.2.0", "mcp_tools": ["act", "session", "status"]}
        rows.insert(5, record("event_msg", {"type": "item_completed", "turn_id": "turn-1",
            "item": {"type": "McpToolCall", "id": "stale-status", "server": "agentscumm", "tool": "status",
                     "result": {"sourceRevision": stale, "sessionId": THREAD, "game": "synthetic-game",
                                "dataDir": str(self.repo / "stale")}}}, 2))
        rows[6]["payload"]["item"]["result"] = {"sourceRevision": played, "sessionId": SESSION,
            "game": "synthetic-game", "dataDir": str(self.repo / "runtime")}
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["git"]["agentscumm_runtime"], [stale, played])
        self.assertEqual(stats["git"]["agentscumm_gameplay_runtime"], [played])

    def test_invalid_recorded_commit_fails_explicitly(self):
        rows = fixture()
        rows[0]["payload"]["git"] = {"commit_hash": "not-a-commit"}
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "Invalid recorded commit"):
            self.export()

    def test_active_partial_append_is_explicit_and_cannot_be_finalized(self):
        self.source(trailing=b'{"timestamp":')
        _, stats = self.export(run_id="partial")
        self.assertTrue(stats["warnings"])
        self.source(fixture(complete=True), trailing=b'{"timestamp":')
        with self.assertRaisesRegex(ValueError, "complete source snapshot"):
            self.export(status="completed", completion_note="Ending shown")

    def test_no_overwrite_and_no_source_mutation(self):
        self.source()
        path, _ = self.export()
        original = (path / "run.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export()
        self.assertEqual((path / "run.json").read_bytes(), original)
        self.assertFalse(list(path.parent.glob(".export-*")))
        self.assertFalse(list(path.parent.glob("*.lock")))

    def test_tampered_or_missing_screenshot_fails_verification(self):
        self.source()
        path, _ = self.export()
        entry = json.loads((path / "screenshots/index.json").read_text())["screenshots"][0]
        (path / entry["path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            EXPORT.verify_run(path)
        (path / entry["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            EXPORT.verify_run(path)

    def test_usage_gaps_fail_without_publishing(self):
        rows = fixture()
        rows[-1]["payload"]["thread_token_usage"]["input_tokens"] += 1
        rows[-1]["payload"]["thread_token_usage"]["total_tokens"] += 1
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "gap"):
            self.export()
        self.assertFalse(list((self.repo / "runs/synthetic-game").iterdir()))

    def test_session_frame_id_conflict_is_rejected(self):
        rows = fixture()
        image = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(b"different bytes").decode()}
        frame = {"id": "frame-42", "sessionId": SESSION, "mimeType": "image/png", "width": 1}
        rows.insert(8, record("response_item", {"type": "message", "role": "tool", "content": [
            {"type": "text", "text": json.dumps(frame)}, image]}, 4))
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "conflicting screenshot"):
            self.export()

    def test_frame_ids_from_distinct_sessions_are_kept(self):
        rows = fixture()
        other = {"id": "frame-42", "sessionId": "another-session", "mimeType": "image/png", "width": 1}
        rows.insert(8, record("response_item", {"type": "message", "role": "tool", "content": [
            {"type": "text", "text": json.dumps(other)}, {"type": "image", "mimeType": "image/png", "data": PNG}]}, 4))
        self.source(rows)
        _, stats = self.export()
        self.assertEqual(stats["screenshots"]["unique_files"], 1)
        self.assertEqual(stats["screenshots"]["original_frame_ids"], 2)

    def test_forwarded_frame_metadata_only_labels_the_next_image(self):
        rows = fixture(complete=True)
        frame = {"id": "frame-42", "sessionId": SESSION, "mimeType": "image/png", "width": 1}
        output = rows[7]["payload"]["output"]
        output.insert(0, {"type": "input_text", "text": json.dumps(frame)})
        for data in (b"unlabeled screenshot", b"another unlabeled screenshot"):
            output.append({"type": "input_image", "image_url":
                           "data:image/png;base64," + base64.b64encode(data).decode()})
        output.insert(3, {"type": "input_text", "text": '{"selectedFrames":1}'})
        self.source(rows)
        path, stats = self.export(status="completed", completion_note="Ending shown")
        self.assertEqual(stats["screenshots"]["unique_files"], 3)
        self.assertEqual(stats["screenshots"]["original_frame_ids"], 1)
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_truncated_mcp_image_recovers_full_model_copy_and_frame_id(self):
        rows = fixture()
        partial = PNG[:20] + f"…{len(PNG) - 40} chars truncated…" + PNG[-20:]
        event = rows[6]["payload"]["item"]["result"]["content"][0]
        decoded = json.loads(event["text"])
        decoded["content"][1]["data"] = partial
        event["text"] = json.dumps(decoded)
        self.source(rows)
        path, stats = self.export()
        self.assertEqual(stats["screenshots"]["recovered_truncated_references"], 1)
        self.assertEqual(stats["screenshots"]["original_frame_ids"], 1)
        self.assertEqual(stats["screenshots"]["unresolved"], [])
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_review_truncation_spanning_multiple_images_preserves_full_sequence(self):
        rows = fixture()
        other = base64.b64encode(b"another-full-image" * 15).decode()
        frame = {"id": "frame-42", "sessionId": SESSION, "mimeType": "image/png", "width": 1}
        result = {"content": [
            {"type": "text", "text": json.dumps({"selectedFrames": 2})},
            {"type": "text", "text": json.dumps(frame)},
            {"type": "image", "mimeType": "image/png", "data": PNG},
            {"type": "text", "text": json.dumps({**frame, "id": "frame-43"})},
            {"type": "image", "mimeType": "image/png", "data": other}]}
        raw = json.dumps(result)
        lo, hi = raw.index(PNG) + 20, raw.index(other) + len(other) - 20
        truncated = raw[:lo] + f"…{hi - lo} chars truncated…" + raw[hi:]
        rows[6]["payload"]["item"].update(tool="review", arguments={"count": 2},
            result={"content": [{"type": "text", "text": truncated}]})
        rows[7]["payload"]["call_id"] = "forward-call"
        rows[7]["payload"]["output"] = [
            {"type": "input_image", "image_url": "data:image/png;base64," + data}
            for data in (PNG, other)]
        rows.insert(6, record("response_item", {"type": "custom_tool_call", "call_id": "forward-call",
            "input": 'const r=await tools.mcp__agentscumm__review({count:2});for(const c of r.content??[]) {if(c.type==="image")image(c);else if(c.type==="text")text(c.text);}'}, 2))
        self.source(rows)
        path, stats = self.export()
        self.assertEqual(stats["screenshots"]["recovered_spanning_tool_summaries"], 1)
        self.assertEqual(stats["screenshots"]["unique_files"], 2)
        self.assertEqual(stats["screenshots"]["unresolved"], [])
        self.assertIn("Intermediate metadata was not recorded", (path / "trajectory.jsonl").read_text())
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_merged_observe_history_truncation_preserves_full_sequence(self):
        rows = fixture()
        other = base64.b64encode(b"another-full-image" * 15).decode()
        frame = {"id": "frame-42", "sessionId": SESSION, "mimeType": "image/png", "width": 1}
        result = {"content": [
            {"type": "text", "text": json.dumps({"selectedFrames": 2})},
            {"type": "text", "text": json.dumps(frame)},
            {"type": "image", "mimeType": "image/png", "data": PNG},
            {"type": "text", "text": json.dumps({**frame, "id": "frame-43"})},
            {"type": "image", "mimeType": "image/png", "data": other}]}
        raw = json.dumps(result)
        lo, hi = raw.index(PNG) + 20, raw.index(other) + len(other) - 20
        truncated = raw[:lo] + f"…{hi - lo} chars truncated…" + raw[hi:]
        rows[6]["payload"]["item"].update(tool="observe", arguments={"source": "history", "count": 2},
            result={"content": [{"type": "text", "text": truncated}]})
        rows[7]["payload"]["call_id"] = "forward-call"
        rows[7]["payload"]["output"] = [
            {"type": "input_image", "image_url": "data:image/png;base64," + data}
            for data in (PNG, other)]
        rows.insert(6, record("response_item", {"type": "custom_tool_call", "call_id": "forward-call",
            "input": 'const r=await tools.mcp__agentscumm__observe({source:"history",count:2});for(const c of r.content??[]) {if(c.type==="image")image(c);else if(c.type==="text")text(c.text);}'}, 2))
        self.source(rows)
        path, stats = self.export()
        self.assertEqual(stats["screenshots"]["recovered_spanning_tool_summaries"], 1)
        self.assertEqual(stats["screenshots"]["unique_files"], 2)
        self.assertEqual(stats["screenshots"]["unresolved"], [])
        self.assertIn("Intermediate metadata was not recorded", (path / "trajectory.jsonl").read_text())
        self.assertEqual(EXPORT.verify_run(path), stats)

    def test_helper_forwarded_images_recover_with_matching_call_and_frame_metadata(self):
        other = base64.b64encode(b"another-full-image" * 15).decode()
        frame = {"sessionId": SESSION, "game": "synthetic-game", "reason": "sequence-screenshot",
                 "width": 1, "height": 1, "id": "frame-42", "mimeType": "image/png"}
        last_frame = {**frame, "reason": "after-action", "id": "frame-43"}
        content = [{"type": "text", "text": '{"selectedFrames":2}'}]
        for metadata, data in ((frame, PNG), (last_frame, other)):
            content.extend([{"type": "text", "text": EXPORT.dump(metadata)},
                            {"type": "image", "mimeType": "image/png", "data": data}])
        raw = json.dumps({"content": content})
        lo, hi = raw.index(PNG) + 20, raw.index(other) + len(other) - 20
        truncated = raw[:lo] + f"…{hi - lo} chars truncated…" + raw[hi:]

        for case in ("helper", "yielded", "wrong-cell", "wrong-session", "competing-result"):
            with self.subTest(case=case):
                rows = fixture(complete=True)
                native = rows[6]
                native["payload"]["item"]["result"] = {"content": [{"type": "text", "text": truncated}]}
                call = {"type": "custom_tool_call", "name": "exec", "call_id": "exec-call",
                        "input": 'await eval(load("helper"))([])'}
                segment = [record("response_item", call, 2)]
                output_type, call_id = "custom_tool_call_output", "exec-call"
                if case in ("yielded", "wrong-cell"):
                    segment.append(record("response_item", {"type": output_type, "call_id": call_id,
                        "output": "Script running with cell ID example:1\nWall time 1 second\n"}, 2))
                    cell = "example:2" if case == "wrong-cell" else "example:1"
                    segment.append(record("response_item", {"type": "function_call", "name": "wait",
                        "call_id": "wait-call", "arguments": json.dumps({"cell_id": cell})}, 2))
                    output_type, call_id = "function_call_output", "wait-call"
                segment.append(native)
                if case == "competing-result":
                    segment.append(record("event_msg", {"type": "item_completed", "item": {
                        "type": "McpToolCall", "server": "other", "tool": "capture",
                        "result": {"content": [content[-1]]}}}, 3))
                output = []
                for metadata, data in ((frame, PNG), (last_frame, other)):
                    if case == "wrong-session":
                        metadata = {**metadata, "sessionId": "another-session"}
                    output.extend([{"type": "input_text", "text": EXPORT.dump(metadata).split(',"id"')[0]},
                                   {"type": "input_image", "image_url": "data:image/png;base64," + data}])
                segment.append(record("response_item", {"type": output_type, "call_id": call_id, "output": output}, 4))
                self.source(rows[:6] + segment + rows[8:])
                if case in ("helper", "yielded"):
                    path, stats = self.export(run_id=case, status="completed", completion_note="Ending shown")
                    self.assertEqual(stats["screenshots"]["recovered_spanning_tool_summaries"], 1)
                    self.assertEqual(stats["screenshots"]["model_visible_blocks"], 2)
                    self.assertEqual(stats["screenshots"]["unresolved"], [])
                    self.assertEqual(EXPORT.verify_run(path), stats)
                else:
                    with self.assertRaisesRegex(ValueError, "unavailable image"):
                        self.export(run_id=case, status="completed", completion_note="Ending shown")

    def test_helper_recovers_group_with_matching_action_receipt(self):
        rows = fixture(complete=True)
        first = {"id": "frame-42", "sessionId": SESSION, "game": "synthetic-game",
                 "actionId": "action-1", "mimeType": "image/png", "width": 1}
        second_image = base64.b64encode(b"second image" * 20).decode()
        receipt = {"action": {"actionId": "action-1", "requestId": "step-1", "steps": []},
                   "selectedFrames": 2, "screenshots": []}
        content = [{"type": "text", "text": json.dumps(receipt)}]
        for frame, data in ((first, PNG), ({**first, "id": "frame-43"}, second_image)):
            content.extend([{"type": "text", "text": json.dumps(frame)},
                            {"type": "image", "mimeType": "image/png", "data": data}])
        raw = json.dumps({"content": content})
        lo, hi = raw.index(PNG) + 20, raw.index(second_image) + len(second_image) - 20
        native = rows[6]["payload"]["item"]
        native["result"] = {"content": [{"type": "text",
            "text": raw[:lo] + f"…{hi - lo} chars truncated…" + raw[hi:]}]}
        rows[7]["payload"]["output"] = [
            {"type": "input_text", "text": json.dumps({**receipt,
                "action": {"actionId": "action-1", "requestId": "step-1"}})},
            *({"type": "input_image", "image_url": "data:image/png;base64," + data}
              for data in (PNG, second_image))]
        rows.insert(6, record("response_item", {"type": "custom_tool_call", "name": "exec",
            "call_id": "call-1", "input": 'await eval(load("helper"))([])'}, 2))
        self.source(rows)
        path, stats = self.export(run_id="matched", status="completed", completion_note="Ending shown")
        self.assertEqual(stats["screenshots"]["recovered_spanning_tool_summaries"], 1)
        self.assertEqual(EXPORT.verify_run(path), stats)
        rows[8]["payload"]["output"][0]["text"] = json.dumps({**receipt,
            "action": {"actionId": "action-1", "requestId": "different"}})
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "unavailable image"):
            self.export(run_id="mismatched", status="completed", completion_note="Ending shown")

    def test_completed_export_preserves_truncated_unforwarded_native_frame(self):
        rows = fixture(complete=True)
        data = base64.b64encode(b"unforwarded image" * 20).decode()
        partial = data[:20] + f"…{len(data) - 40} chars truncated…" + data[-20:]
        native = rows[6]["payload"]["item"]
        decoded = json.loads(native["result"]["content"][0]["text"])
        decoded["content"].insert(0, {"type": "text", "text": '{"selectedFrames":1}'})
        decoded["content"][-1]["data"] = partial
        native["result"]["content"][0]["text"] = json.dumps(decoded)
        call = {"type": "custom_tool_call", "name": "exec", "call_id": "call-1",
                "input": "await tools.mcp__agentscumm__act({});image(other)"}
        rows.insert(6, record("response_item", call, 2))
        self.source(rows)
        path, stats = self.export(run_id="unforwarded", status="completed", completion_note="Ending shown")
        gap, = stats["screenshots"]["unresolved"]
        self.assertEqual(gap["scope"], "unforwarded_mcp_result")
        self.assertIn(partial, (path / "trajectory.jsonl").read_text())
        self.assertEqual(EXPORT.verify_run(path), stats)
        call["input"] = ('const r=await tools.mcp__agentscumm__act({});'
                         'for (const c of r.content??[]) if(c.type==="image")image(c);')
        self.source(rows)
        with self.assertRaisesRegex(ValueError, "unavailable image"):
            self.export(run_id="forwarded", status="completed", completion_note="Ending shown")

    def test_truncated_image_without_full_copy_is_explicit(self):
        rows = fixture(complete=True)
        partial = PNG[:20] + f"…{len(PNG) - 40} chars truncated…" + PNG[-20:]
        event = rows[6]["payload"]["item"]["result"]["content"][0]
        decoded = json.loads(event["text"])
        decoded["content"][1]["data"] = partial
        event["text"] = json.dumps(decoded)
        rows = [r for r in rows if r["type"] not in ("compacted",) and
                r["payload"].get("type") != "custom_tool_call_output"]
        self.source(rows)
        path, stats = self.export(run_id="missing-copy")
        self.assertEqual(stats["screenshots"]["unique_files"], 0)
        self.assertEqual(len(stats["screenshots"]["unresolved"]), 1)
        self.assertIn(partial, (path / "trajectory.jsonl").read_text())
        with self.assertRaisesRegex(ValueError, "unavailable image"):
            self.export(status="completed", completion_note="Ending shown")

    def test_unavailable_remote_image_is_explicit_and_blocks_finalization(self):
        rows = fixture(complete=True)
        rows.insert(8, record("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_image", "image_url": "https://example.invalid/unavailable.png"}]}, 4))
        self.source(rows)
        _, stats = self.export(run_id="remote")
        self.assertEqual(len(stats["screenshots"]["unresolved"]), 1)
        with self.assertRaisesRegex(ValueError, "unavailable image"):
            self.export(status="completed", completion_note="Ending shown")

    def test_path_traversal_links_and_tracked_archives_are_rejected(self):
        self.source()
        with self.assertRaisesRegex(ValueError, "Invalid run ID"):
            self.export(run_id="../../escape")
        runs = self.repo / "runs"
        for sub in runs.iterdir():
            sub.rmdir()
        runs.rmdir()
        target = self.repo / "elsewhere"
        target.mkdir()
        runs.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Linked archive"):
            self.export()
        runs.unlink()
        (self.repo / ".gitignore").write_text("")
        with self.assertRaisesRegex(ValueError, "must contain /runs/"):
            self.export()
        (self.repo / ".gitignore").write_text("/runs/\n")
        runs.mkdir()
        (runs / "tracked.txt").write_text("private capture")
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", "runs/tracked.txt"], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "tracked files"):
            self.export()

    def test_rollout_game_mismatch_is_rejected(self):
        self.source()
        with self.assertRaisesRegex(ValueError, "different game"):
            EXPORT.export_run(self.rollout, "wrong-game", repo=self.repo)


if __name__ == "__main__":
    unittest.main()
