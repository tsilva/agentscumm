# Agent context in run archives

`run.json` is the run manifest: outcome, measurements, source provenance, skills, screenshots, agent capabilities, and initial prompt assets. New exports use schema version 2. Verification also accepts version 1 archives, including the former `stats.json` filename. It prefers `run.json` when present and does not silently fall back from invalid current metadata.

## Recorded context

Before starting or resuming gameplay, Play Game requires a clean AgentSCUMM checkout. Staged, unstaged, and untracked changes block play until the user commits them; ignored run data stays excluded. After verifying the full `HEAD` commit, the skill immediately initializes `.internal/<game-id>/<run-id>/run.json` before any gameplay or startup-context capture. This working manifest has `kind: "working_run"`, `status: "in_progress"`, game/run identifiers, `started_at`, and `git.play_start` with the full commit ID, branch, `dirty: false`, and `source: "clean_play_preflight"`.

Resume checks preserve the original starting record and append timestamped clean revisions to `git.resume_checkpoints`. Missing historical starting commits remain unknown. This working manifest is separate from the verified manifest generated under `runs/`; it is not an archive accepted by the verifier. Its checkout revision does not establish which revision the attached MCP server loaded. Preserve server-reported startup provenance separately.

`agent_context.tools.available_at_start` records tool definitions found in initial session/turn metadata or initial prompt schemas. This includes built-in tools such as web search and shell execution as well as MCP tools. Prose mentions of a tool do not establish availability. A recorded subset remains `partial`; absence of definitions is `unavailable`, not evidence that no tools existed.

`agent_context.tools.observed_during_run` separately indexes direct recorded calls, MCP call events, and tool references in submitted `exec` code. The latter are labeled because conditional code might not execute. These entries are evidence of tool use or attempted use, not a reconstructed initial inventory or deduplicated invocation counts. Names such as `web__run` are normalized to `web.run`; `mcp__agentscumm__act` becomes `mcp.agentscumm.act`.

`agent_context.prompt_assets` indexes recorded session instructions and system/developer/user content blocks before the first recorded agent response. It includes instruction blocks, skill catalogs, environment context, the initial request, images, and attachments where recorded. Each entry has its role, kind, trajectory record number, JSON pointer, byte count, and checksum. Checksums cover the compact UTF-8 JSON representation of the archived value, including external screenshot references. Verification resolves these pointers without the original host files. References to external attachments do not prove that their full contents were retained.

The boundary and coverage limitations are explicit. Later instruction changes remain in the trajectory but are not labeled initial assets. Host inputs omitted from the rollout cannot be reconstructed from the current machine's configuration.

## Supplying missing startup information

When the harness preserves a startup inventory outside the rollout, pass it with:

```sh
python3 -B scripts/export-run.py \
  --rollout /path/to/rollout.jsonl \
  --game-id GAME_ID \
  --agent-context /path/to/initial-context.json
```

The context file uses this format:

```json
{
  "schema_version": 1,
  "tools": {
    "inventory_complete": false,
    "available_at_start": [
      {"name": "web.run", "kind": "builtin"},
      {"name": "exec_command", "kind": "builtin"},
      {"name": "mcp__agentscumm__act", "kind": "mcp"}
    ]
  },
  "prompt_assets": [
    {
      "name": "Initial route notes",
      "kind": "reference",
      "path": "route-notes.txt"
    }
  ]
}
```

Use `inventory_complete: true` only for a known complete startup list. This records an `operator_declared_complete` claim; it does not independently verify historical availability. Tool entries may include additional schema fields. Asset paths resolve relative to the context file. Their bytes are copied under `context/asset-<sha256>` with mode `0600` and verified without their source files. Copies are labeled export-time snapshots; declaring initial injection does not establish that the current file matches its historical contents. Supply only material actually injected at startup. References found or skills read later belong in the trajectory and skill bundles.

For reliable future comparisons, save the tool inventory and immutable prompt assets when the run starts, before changing instructions or files. Do not include credentials or unrelated host configuration in the declaration.

## Automatic finalization

Play Game captures `initial-context.json` in its ignored working directory before gameplay and retains it on resume. After confirming the ending and saving evidence, it queues `scripts/finalize-run.py` before its final reply. See the skill's [archiving procedure](../.agents/skills/agentscumm_play-game/references/run-archiving.md) for startup capture and command arguments.

The bounded local worker waits for the specified gameplay turn's `task_complete` event, then snapshots the rollout exactly through that record. Final reply and usage records are included; later follow-up turns are excluded. It calls the exporter with completed status, observed ending evidence, initial context, referenced skills and actual walkthrough URLs. Only after verification does it append aggregate results to local `BENCHMARKS.md`. Trajectory-checksum markers make registration idempotent and preserve existing editorial notes. The job does not operate the game, access its backend, commit, push, or publish.

`finalization.json` and `finalization.log` in the run's working directory record progress and failure. `queued` is not archive success. The gameplay turn must end before its worker can finish; do not wait for the worker in that same turn. A five-minute default timeout prevents an abandoned job from waiting indefinitely. Interrupted turns, missing telemetry or skills, and conflicting archives fail explicitly. A verified archive is preserved if benchmark registration fails; retry only that step with:

```sh
python3 -B scripts/finalize-run.py register /path/to/completed-archive
```

Public results use one compact comparison table with status, game, UTC date, model/reasoning, walkthrough assistance, elapsed completion time, total tokens, total submitted actions, and action sequences. Status is ✅ for a completed game; unfinished runs show their declared `estimated_progress_percent` (for example, `~40%`) or `Unknown` when no estimate was recorded. Estimates are supplied by the operator, never inferred from action or token counts. The UTC date comes from the archive's recorded cutoff; elapsed time includes pauses and resumes, or time so far for unfinished runs. Tokens are total input plus output, including cached/repeated context. Full capability/provenance details stay in `run.json`. No declared walkthrough URLs means assistance is unspecified, not automatically unaided.

Registration appends a row to the existing table and preserves surrounding editorial notes. The `<!-- agentscumm:benchmark:TRAJECTORY_SHA256 -->` deduplication marker belongs inside the row so it does not break Markdown table rendering. Older manually documented results with the same marker are still recognized. Public rows omit private completion-note text, original prompt contents, and conversation paths.
