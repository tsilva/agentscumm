# Agent context in run archives

`run.json` is the run manifest for outcomes, measurements, source provenance, skills, screenshots, agent capabilities, and initial prompt assets. New exports use schema version 3. Verification also accepts versions 1 and 2, including the former `stats.json` filename. It reads `run.json` when present and reports errors in that file directly.

## Recorded context

Before starting or resuming gameplay, Play Game requires a clean AgentSCUMM checkout. Staged, unstaged, and untracked changes block play until the user commits them; ignored run data stays excluded. After verifying the full `HEAD` commit, the skill immediately initializes `.internal/<game-id>/<run-id>/run.json` before any gameplay or startup-context capture. This working manifest has `kind: "working_run"`, `status: "in_progress"`, game/run identifiers, `started_at`, and `git.play_start` with the full commit ID, branch, `dirty: false`, and `source: "clean_play_preflight"`.

Resume checks preserve the original starting record and append timestamped clean revisions to `git.resume_checkpoints`. Missing historical starting commits remain unknown. This working manifest is separate from the verified manifest generated under `runs/`; it is not an archive accepted by the verifier. Its checkout revision does not establish which revision the attached MCP server loaded. Preserve server-reported startup provenance separately.

`agent_context.tools.available_at_start` records tool definitions found in initial session/turn metadata or initial prompt schemas. This includes built-in tools such as web search and shell execution as well as MCP tools. Prose mentions of a tool do not establish availability. A recorded subset remains `partial`; absence of definitions is `unavailable`, not evidence that no tools existed.

`agent_context.tools.observed_during_run` separately indexes direct recorded calls, MCP call events, and tool references in submitted `exec` code. The latter are labeled because conditional code might not execute. These entries are evidence of tool use or attempted use, not a reconstructed initial inventory or deduplicated invocation counts. Names such as `web__run` are normalized to `web.run`; `mcp__agentscumm__act` becomes `mcp.agentscumm.act`.

AgentSCUMM uses the package version for the MCP handshake. Bump it when changing the MCP tool surface. The server also reports this version and the names of the tools registered for that process in `status.sourceRevision.mcp_version` and `mcp_tools`. The Git revision distinguishes builds with the same package version, and the dirty flag records local changes. The exporter records attached server observations under `git.agentscumm_runtime`. It records revisions matched to gameplay sessions under `git.agentscumm_gameplay_runtime`, including sessions matched to the local client's run profile. The MCP tool list can vary with launch flags, and game-dependent native actions are reported separately by `status.nativeControls`.

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

The bounded local worker waits for the specified gameplay turn's `task_complete` event, then snapshots the rollout exactly through that record. Final reply and usage records are included; later follow-up turns are excluded. It calls the exporter with completed status, observed ending evidence, initial context, referenced skills and actual walkthrough URLs. Each archive saves input, output and cached input tokens plus an API-equivalent USD token cost using current official rates; see [pricing rules and unavailable costs](run-archives.md#token-pricing). Queue accepts `--pricing-rates` for an explicit verified rate file; otherwise export fetches public pricing automatically. The finalization receipt reports `api_cost_status` and `total_api_price_usd`. Only after verification does it append aggregate results to local `BENCHMARKS.md`. Trajectory-checksum markers make registration idempotent and preserve existing editorial notes. The job does not operate the game, access its backend, commit, push, or publish.

Each game has a benchmark table ordered by the recorded UTC cutoff, newest first. The cost cell shows the USD estimate rounded to two decimal places, or `Unknown` when pricing is unavailable. The archive retains the full-precision estimate, rate verification date and any pricing tier assumptions. The `Input tokens (% cached)` cell shows the total and its cached percentage, rounded to two decimal places, or `N/A` for the percentage when total input is zero. Registration upgrades older tables in place, calculates cache percentages from existing counts when possible, and fills missing measurements from the matching verified archive. Re-registering an existing run preserves its archived cost and row identity.

`finalization.json` and `finalization.log` in the run's working directory record progress and failure. `queued` is not archive success. The gameplay turn must end before its worker can finish; do not wait for the worker in that same turn. A five-minute default timeout prevents an abandoned job from waiting indefinitely. Interrupted turns, missing telemetry or skills, and conflicting archives fail explicitly. A verified archive is preserved if benchmark registration fails; retry only that step with:

```sh
python3 -B scripts/finalize-run.py register /path/to/archive
```

Public results also show status, game, UTC date, model and reasoning effort, walkthrough assistance, elapsed completion time, total submitted actions, and action sequences. Status is ✅ for a completed archive. An unfinished Flight of the Amazon Queen run can show `2/7 · Opened the jungle river crossing` from verified engine telemetry. If that telemetry is unavailable, registration shows the operator's declared estimate, such as `~40%`, or `Unknown`. The run date comes from the archive's recorded cutoff in UTC. Elapsed time includes pauses and resumes, or time so far for unfinished runs. Tokens are total input plus output, including cached/repeated context. Full capability and provenance details stay in `run.json`. Assistance is unspecified when the archive has no declared walkthrough URLs.

Registration inserts a row into its game's table and preserves surrounding editorial notes. The `<!-- agentscumm:benchmark:TRAJECTORY_SHA256 -->` deduplication marker belongs inside the row so it does not break Markdown table rendering. Older manually documented results with the same marker are still recognized. Public rows omit private completion-note text, original prompt contents, and conversation paths.

`MCP version` shows the AgentSCUMM package version and abbreviated startup revision where known. A run that used several revisions can list them together. A dirty build carries a `dirty` label. `MCP tools` labels the server's registered inventory separately from a historical chat startup list or observed calls. For a local-client run with no attached gameplay actions, registration uses the local gameplay server's provenance.
