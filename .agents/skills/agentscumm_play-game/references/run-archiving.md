# Capture and finish a run

Read this when starting/resuming run records and again when the ending is confirmed. These local helpers archive a conversation; they never control a game or choose its backend.

## Before gameplay

First complete the skill's clean-commit gate and immediately initialize `.internal/<game-id>/<run-id>/run.json` with the verified starting commit. This must happen before capturing startup context or interacting with the game. Keep the working manifest's original starting revision on resume; later clean revisions belong in its resume checkpoints, and missing historical starting provenance stays unknown. The working manifest is not a verified export and does not substitute for recorded server-start revisions.

Use that existing ignored working directory to capture `initial-context.json` before gameplay using the format in the repository's `docs/run-context.md`:

- Record the tools exposed to this agent, including built-in shell, web/search, image/file tools, orchestration, and enabled MCP tools. The host's `ALL_TOOLS` metadata provides enabled nested tool names; supplement it with top-level tools from the attached schemas. Read metadata only, without calling unrelated services. Do not copy credentials or host configuration.
- Use `schema_version: 1`, `tools.available_at_start` entries with `name` and `kind` (`builtin` or `mcp`), and `tools.inventory_complete: false` unless the host establishes that the inventory is complete. Preserve unknown coverage. Current capabilities do not reconstruct an earlier run's startup inventory.
- In `prompt_assets`, declare only additional files actually supplied at startup, with `name`, `kind`, and `path`. Preserve their bytes in the working directory at that time and point at those immutable copies. Initial instructions, skill catalogs, user inputs and images recorded in the rollout are also indexed automatically. A reference found later is not an initial injected asset.

For example, `ALL_TOOLS.map(t => ({name: t.name, kind: t.name.startsWith("mcp__") ? "mcp" : "builtin"}))` collects nested names without their large descriptions. Write the JSON with the available local file tools. Retain the initial file on resume; never overwrite it with a new tool list. If a historical run has no startup capture, leave the gap explicit and omit `--agent-context` rather than declaring current tools as its original ones.

Keep the exact walkthrough URLs used and any user intervention in memory. Keep context/assets under the same run directory so export does not depend on files that may change during play. The exporter bundles referenced skills with supporting files; these copies retain their export-time provenance.

## After confirming the ending

Save ending evidence and update memory first. Identify this host's exact Codex rollout using the current conversation ID and its `session_meta.id`; obtain the current gameplay turn ID from its latest `task_started` event. Do not use another chat's rollout or assume a remote chat's file exists locally. Missing rollout/usage access is an archival blocker to report, not permission to fabricate telemetry.

Run the following from the AgentSCUMM checkout **before sending the final gameplay reply**, replacing placeholders with verified values:

```sh
python3 -B scripts/finalize-run.py queue \
  --rollout /path/to/this-conversation-rollout.jsonl \
  --turn-id CURRENT_GAMEPLAY_TURN_ID \
  --game-id GAME_ID \
  --game-title "Game title from the catalog" \
  --work-dir .internal/GAME_ID/RUN_ID \
  --agent-context .internal/GAME_ID/RUN_ID/initial-context.json \
  --completion-note "Describe the ending and saved evidence actually observed." \
  --walkthrough-url https://example.org/the-guide-actually-used
```

Omit `--walkthrough-url` when none was used; repeat it for each guide. Omit `--agent-context` only for a historical run without a startup capture. Do not pass example URLs. The helper includes this skill explicitly and discovers other referenced skills; use repeatable `--skill /path/to/SKILL.md` for missing named references. The archive run ID defaults to the conversation UUID, independently of the notes directory ID.

`queue` starts a bounded local worker, with a five-minute default deadline, that waits for **this exact turn** to finish. The worker snapshots only through its `task_complete` record, excluding later follow-up turns, and exports the complete verified archive under `runs/`. It includes `run.json`, trajectory, screenshots, referenced skill bundles, initial context, token/model/reasoning/runtime provenance, and total actions/action sequences. It then appends the verified aggregate result to the owning checkout's `BENCHMARKS.md`. Full conversations, original prompt contents, private paths and completion-note text stay out of that public summary. No commit, push, external message, or game interaction occurs.

The queue receipt and `finalization.json` distinguish `queued`/`waiting_for_turn`, `exporting`, `registering_benchmark`, `completed`, and `failed`. End the gameplay turn after queueing; waiting in that same turn would prevent completion. In the final reply report game completion and **queued** archiving, with the state-file path. Do not claim the archive or benchmark is finished until state says `completed` with `verified: true`.

## Resume or repair

Read an existing `finalization.json` before scheduling again. Repeating an identical queue request preserves the existing job; conflicting requests are rejected. A worker timeout, interrupted turn, missing skills/telemetry, archive conflict, or registration failure remains explicit in the state and log. Never alter ending evidence, invent missing metadata, overwrite an existing archive, or restart a game to make export succeed.

After repairing a failed local prerequisite, retry the stored request with `python3 -B scripts/finalize-run.py worker --request /path/to/finalize-request.json`. If export succeeded and only registration failed, use `python3 -B scripts/finalize-run.py register /path/to/verified-archive`. Registration verifies the archive and deduplicates by trajectory checksum, including a retry after a completed job. A busy benchmark lock fails after a bounded wait; preserve concurrent edits and retry rather than replacing the whole file. Report unresolved failures on the next handoff.
