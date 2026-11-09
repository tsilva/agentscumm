# AgentSCUMM

AgentSCUMM owns mechanical MCP controls, observations, capture history, and its spectator. Keep gameplay discovery, planning, and memory outside the MCP.

## Play a game

Use [$agentscumm_play-game](.agents/skills/agentscumm_play-game/SKILL.md) when the user asks to play, finish, continue, or resume a named ScummWEB game. The invocation ID is `agentscumm_play-game`; its display name is `agentscumm: Play Game`.

The user-selected run layout is `.internal/<game-id>/<run-id>/`. All gameplay memory, screenshots, logs, and other working artifacts may live there. This gameplay-specific layout overrides the general reference/QA/evidence subdirectory convention for these runs. Preserve Git exclusion and keep run data out of commits.

Before starting or resuming play, the Play Game skill requires a clean AgentSCUMM checkout: the user must commit all staged, unstaged, and untracked changes. Once verified, initialize the working run directory and `run.json` with the full starting commit immediately, before startup-context capture or game interaction. Preserve the original baseline on resume and record later clean revisions as resume checkpoints. Never commit ignored run data to satisfy this gate.

Final conversation archives use the user-selected `runs/<game-id>/<yyyymmdd>-<run-id>/` layout. Keep `/runs/` Git-ignored. Use `scripts/export-run.py` to preserve the complete Codex rollout as `trajectory.jsonl`, externalize all embedded screenshots into `screenshots/`, and write verified `run.json`. The date is the conversation's start date in Europe/Lisbon; the default run ID is its conversation UUID. Screenshot references use stable content-based IDs; `screenshots/index.json` retains original session/frame IDs. Follow `docs/run-archives.md` for the run-export procedure. Export as completed only after explicit game-ending evidence and a finished conversation turn; preparatory snapshots remain `in_progress`. Never interrupt or restart a live game to export it.

Include recorded Git commit IDs in `run.json`: preserve the conversation checkout revision and AgentSCUMM server startup revisions separately from the export checkout. Preserve dirty-state flags where available; never infer a missing startup revision from the current checkout or treat a conversation checkout as proof of the deployed game's version.

Every new run export must store measured input tokens, output tokens and cached input tokens, plus the API-equivalent model-token total in USD at current official provider rates for the exact model(s) used. Preserve the rates, source/retrieval date, context bands, usage cutoff and tier assumptions in `run.json`. The exporter fetches supported official pricing automatically; unavailable pricing remains explicitly null with a reason, never zero or a substituted model. See `docs/run-archives.md` for accounting, supported telemetry and explicit rate-file overrides.

Run exports must also bundle referenced skill directories under `skills/`, with supporting files and a verified checksum/reference manifest. Use the exporter's `--skill /path/to/SKILL.md` option for additional skills or unresolved named references. Report missing references explicitly. Export-time copies must never be represented as verified historical versions; preserve the provenance distinction and do not claim that a conversation archive alone guarantees reproducible gameplay.

Use `run.json` as the run manifest. Track available-at-start tools, including non-MCP capabilities, separately from observed use, and index recorded initial prompt assets. Missing inventory remains unknown; do not substitute this chat's tools for a historical run's capabilities. See `docs/run-context.md` for startup declarations and asset provenance. Maintain `BENCHMARKS.md` with verified aggregate results, assistance, outcomes and comparison limits; keep full private runs out of the public log.

The Play Game skill must capture startup context. After verifying the ending and saving the evidence, it closes the spectator through the completed session's MCP connection and automatically queues `scripts/finalize-run.py`. Its bounded local worker waits for the exact gameplay turn's completion, exports and verifies the run, then registers the result in local `BENCHMARKS.md` idempotently. Report queued work as queued until `finalization.json` confirms success. Do not wait for the finalizer within the turn it is waiting to archive, and do not publish or push as part of this bookkeeping.

Before gameplay, the skill synchronizes the current branch with its configured upstream and compares the required clean commit with the selected MCP connection's `status.sourceRevision`. A stale or dirty server must be restarted from that checkout and verified again. Routine local inspection and restart/reconnection are preauthorized. Prefer the attached MCP; if it is unavailable or cannot reconnect, autonomously use the skill's existing `scripts/mcp-client.mjs` fallback without asking for approval. Do not spend repeated turns debugging the desktop host or ask the user to perform routine restarts. Preserve saves, server configuration and unrelated sessions; the fallback's isolated run-local profile does not inherit another profile's saves. Actual local-client failures and unverifiable provenance still block gameplay and must be reported with evidence.

The gameplay skill uses only AgentSCUMM MCP tools to interact with games, through either the attached connection or the maintained local MCP client. Server implementation and backend selection belong to the MCP server; the skill may manage its process and connection for preflight and recovery, but must not change its implementation or substitute browser automation or direct game-runtime access. Local run records and web research such as walkthroughs remain allowed. Other browser work follows the user's normal Brave preference.

## Autoresearch a gameplay strategy

Use [$agentscumm_autoresearch](.agents/skills/agentscumm_autoresearch/SKILL.md)
for one research round comparing up to three parallel gameplay subagents. Its
display name is `agentscumm: Autoresearch`. The first task is fixed to Flight of
the Amazon Queen's opening hotel and a launcher-configured laundry-room endpoint.
Rank verified successes by total submitted primitive actions, report evidence
and limitations, then wait for explicit user confirmation before another round.
Each trial owns an isolated MCP process/profile and ignored run directory.
Experiment conditions do not change normal launch or Play Game defaults.
Creating or editing the skill alone does not start gameplay.

Experiment instructions must be generic strategies applicable to any room.
Never supply room-specific solution hints, object combinations, coordinates,
hardcoded action recipes, or prior gameplay discoveries in player-facing
briefs, strategies, references, or dispatches. Each experiment must run with its
own visible AgentSCUMM spectator window; hidden trials are not valid comparisons.

## Price a conversation

Use [$agentscumm_price-thread](.agents/skills/agentscumm_price-thread/SKILL.md) to evaluate a Codex conversation's recorded token usage and calculate its hypothetical OpenAI API cost with the models actually used and current official rates. Its display name is `agentscumm: Price Thread`. This read-only analysis is separate from gameplay and requires no model API key. Report the usage cutoff, cache breakdown, context bands, and any processing-tier assumption; missing telemetry or pricing must remain explicit.

## Author a game internals map

Use [$agentscumm_game-internals-map-author](.agents/skills/agentscumm_game-internals-map-author/SKILL.md)
when the user asks to author or verify a map in ScummWEB's `data/game-internals/`.
Trace native state to the target edition and verify the milestone rules with
ScummWEB's tracker and AgentSCUMM telemetry as appropriate.
