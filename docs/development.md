# Development and verification

Run commands from the repository root.

```sh
pnpm check
pnpm test
pnpm test:live
```

`pnpm test` uses a synthetic local game and real owned Chromium sessions. It covers five-tool discovery and read-only annotations, fresh/exact/history observations, ordered in-sequence screenshots, partial captures and capture failures, byte-identical cached capture replay, MCP image responses, delayed startup, crop-relative click/move/drag delivery across canvas geometry changes, input validation, request deduplication, partial sequence failures, frame retention, spectator isolation, default-muted audio, footer sound toggling with rendered audio samples and same-origin request checks, document replacement, relaunch, startup errors, MCP cancellation, and shutdown during an input wait. Health regressions include delayed iframe document parsing, real WebGL context loss, runtime aborts/traps, a stalled renderer, failure during a sequence without replay, wall-clock jumps, and a shared navigation/runtime startup deadline.

On macOS, the test suite also opens the native spectator, checks its app identity and loaded icon, verifies successive live-frame requests, and checks reuse, reopening, owner-loss cleanup, and server shutdown. This check needs a graphical desktop and Xcode Command Line Tools.

Parallel-instance coverage starts independent stdio MCP servers with automatic profiles, exercises simultaneous games and identical request IDs, rejects cross-instance session input and live-profile reuse, and verifies that history, spectator endpoints and shutdown remain independent. It also checks persistent profile recovery and concurrent native spectator processes on macOS. `src/instances.js` guards data-directory ownership across server processes.

Evaluator coverage tests typed stop predicates, pending room transitions, brief
visits retained between polls, missing telemetry, active-wait interruption,
blocked post-goal inputs, cached partial results, private telemetry, result
receipts and zero server exit status on success. Run the focused checks with
`node --test test/evaluation.test.js`.
Game variable coverage also verifies ordered writes between polls, save
baselines, invalid providers and slots, and the matching snapshot in a
launcher receipt.
The stdio MCP checks cover opt-in telemetry on unmapped games, bank paging,
state changes, unsupported providers, stale session IDs and isolation from
ordinary player responses.
When diagnosing timing failures under competing browser/build load, run the
suite with `node --test --test-concurrency=1 test/*.test.js`, then
`pnpm test:export`. The synthetic checks do not establish the room mapping or
arrival timing of a real game; verify that through MCP against the pinned native
provider before treating a new endpoint as a benchmark.

## Story progress integration

[ScummWEB owns the maps and story tracker](https://github.com/tsilva/scummweb/blob/main/docs/game-progress.md).
AgentSCUMM discovers the map for the launched slug, feeds validated telemetry
to ScummWEB's revision-checked tracker in the owned game frame, and records the
result and map revision. AgentSCUMM keeps its private stop-condition evaluator;
the progress receipt only includes samples through the matched stop.

Use the [`agentscumm_game-internals-map-author` skill](../.agents/skills/agentscumm_game-internals-map-author/SKILL.md)
for map research and verification in the sibling ScummWEB checkout. Run
`node --test test/evaluation.test.js` to verify the integration, including older
backends without the tracker.
Deploy ScummWEB's tracker assets before updating AgentSCUMM if the deployment
needs continuous progress reporting during the migration.

## Runtime and verification

`pnpm test` runs Node test files serially to avoid competing browser and native spectator workloads. Tests of parallel sessions still exercise concurrency within their own cases.

Install the editable ScummBench checkout before the MCP tests. For disposable test journals, set `SCUMMBENCH_RUNS_DIR` to a task-owned temporary directory; finalizer tests isolate their ScummBench registry through `SCUMMBENCH_REGISTRY_ROOT`. Neither setting changes normal gameplay defaults.

`pnpm test:live` launches Flight of the Amazon Queen on the configured live site through stdio MCP, opens the spectator, checks screenshot dimensions, exercises click/key/wait sequences and retry behavior, and retrieves frame history. It uses an isolated temporary profile and closes its browser processes afterward. It does not solve the game. Local verification captures stay under ignored `.internal/`.

Runtime code is in `src/`: `index.js` requires the ScummBench gateway, `server.js` exposes the internal managed MCP backend, `runtime.js` owns the game lifecycle and controls, `health.js` observes mechanical runtime failures, `viewer.js` serves the spectator, `macos-app.js` builds and manages the native spectator, `history.js` bounds visual history, and `actions.js` validates and serializes input. `display.js` defines fixed crops shared by screenshots, the spectator and input. The macOS app source and bundle metadata are in `macos/`. ScummWEB remains the separate browser game runtime; this project does not copy its game payloads.

Dependency policy uses pnpm's seven-day minimum release age and blocks exotic transitive dependency sources. Lifecycle scripts are disabled; browser installation is an explicit step.

Project considerations and future work live in the ignored root `INBOX.md`. Imported source notes remain in ignored `.local/repo-inbox/`; local-only material requires independent backup.

The repository-owned `agentscumm_autoresearch` skill coordinates one round of up
to three isolated players and uses its standard-library `scripts/research.py`
helper to prepare and score evidence. `pnpm test:autoresearch` runs synthetic
evidence tests without launching games or calling models. They cover isolated
starting manifests, visible-spectator launch defaults, opt-in endpoint arguments,
clean-checkout refusal, pending processes, canceled trailing actions, replay accounting, failed/late/unverified
trials, ties, runtime drift, and fresh round lineage. `pnpm test` includes them.
See the skill for the user-confirmed round boundary and actual gameplay procedure.
Player-facing experiment instructions must be generic strategies usable in any
room, without solution hints, hardcoded gameplay recipes, or prior gameplay
discoveries. Each trial opens its own visible AgentSCUMM spectator window.
The coordinator reviews those protocol requirements separately from the helper's
mechanical scoring checks.

## Runtime ownership

New connections use AgentSCUMM → ScummBench MCP → ScummWEB backbone.
`src/server.js` is retired and refuses new launches. Legacy mechanical modules
and spectator resources remain available to already running sessions; new code
must use ScummWEB's `agent-backbone/` implementation. ScummBench has no runtime
or archive-registration dependency on this repository. Verify backbone changes
in ScummWEB with `pnpm check:backbone` and `pnpm test:backbone`.
