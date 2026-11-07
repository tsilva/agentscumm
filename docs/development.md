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
receipts, zero exit status on success, and Play Game client shutdown. Run the
focused checks with `node --test test/evaluation.test.js test/play-game-client.test.js`.
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

## Game internals maps

Each game internals map names engine-specific rooms, variables, flags, and
objects and defines milestone rules over that state. The evaluator loads
`src/game-internals/<game-slug>.yaml` for the launched game. Use the
[`agentscumm_game-internals-map-author` skill](../.agents/skills/agentscumm_game-internals-map-author/SKILL.md)
to research and author maps.

Define `schema_version: 1`, `game`, `engine_id`,
and ordered `milestones` with unique IDs and display labels. In the optional
`rooms` mapping, assign a native room ID to each room label, such as
`ending-credits: 110`. Use `when: { room: ending-credits }` to reference it.
Room rules also accept a numeric `room_id` and an optional `room_ready`
condition. Three other optional mappings name native game state.

- `variables` maps labels to `{ bank, index }`. Use
  `{ variable: ending-outcome, one_of: [1, 3, 4] }` or compare with `equals`.
- `flags` maps labels to `{ bank, index, mask }`, where the mask selects a
  single bit. Use `{ flag: escaped-atlantis }` to match a set bit, or add
  `equals: false` to match a clear bit.
- `objects` maps labels to numeric object IDs. Use
  `{ object: airlock-exit, equals: 1 }` to compare the object's state, or use
  `one_of` to accept several states.

The loader rejects undeclared labels. Rules also accept direct bank predicates,
`all`, `any`, and references to earlier milestones. Edition-specific maps
require the native game ID and index MD5. For `highest_stage` maps, the
furthest verified milestone counts the earlier stages even when the player
took a route that bypassed them.
Maps with `telemetry: snapshot` evaluate the latest native snapshot and can
bind a variable with `{ bank, key }` for a named telemetry bank entry. Use
markers that persist in saves to retain credit when a player leaves a room.
Cover each chapter as thoroughly as the native state allows. Trace named
variables, flags, object states, and room IDs to the scripts that change them.
Add a checkpoint for each distinct puzzle or story outcome the evidence
supports. Check the starting values, intermediate values, and later writes
before selecting a predicate. Combine equivalent outcomes for alternate
routes into one stage.
Verify rules against the target edition and test restores,
alternate paths, and the live ending before adding a completion milestone.
Mapped games record milestone changes in their private session logs for
benchmark export. Progress tracking runs independently of stop conditions.
Draft maps in `src/game-internals/drafts/` are research configs outside runtime
scoring. They can describe predicates that still need provider or evaluator support.

## Runtime and verification

`pnpm test:live` launches Flight of the Amazon Queen on the configured live site through stdio MCP, opens the spectator, checks screenshot dimensions, exercises click/key/wait sequences and retry behavior, and retrieves frame history. It uses an isolated temporary profile and closes its browser processes afterward. It does not solve the game. Local verification captures stay under ignored `.internal/`.

Runtime code is in `src/`: `index.js` exposes MCP, `runtime.js` owns the game lifecycle and controls, `health.js` observes mechanical runtime failures, `viewer.js` serves the spectator, `macos-app.js` builds and manages the native spectator, `history.js` bounds visual history, and `actions.js` validates and serializes input. `display.js` observes render geometry for shared screenshot and spectator cropping; tests cover pillarboxing, letterboxing, widescreen and portrait games, black scenes, menus and native window aspect changes. The macOS app source and bundle metadata are in `macos/`. ScummWEB remains the separate browser game runtime; this project does not copy its game payloads.

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
