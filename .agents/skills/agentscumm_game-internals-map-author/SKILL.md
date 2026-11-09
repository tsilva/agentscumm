---
name: agentscumm_game-internals-map-author
description: Build, expand, or verify game internals maps in ScummWEB, using AgentSCUMM telemetry to verify them. Use for mapping native room IDs, variable slots, flags, object IDs, and milestones to a game's engine and edition.
---

# Author a game internals map

Use the verified sibling ScummWEB checkout for map changes. Read its
[map guide](../../../../scummweb/docs/game-progress.md),
`lib/game-progress/compile.mjs`, and an existing map with comparable telemetry.
See [AgentSCUMM's telemetry guide](../../../docs/usage.md#query-native-game-state)
for reading native state.

## Establish the edition and evidence

1. Resolve the launch slug and identify the engine and exact game data edition
   from the configured ScummWEB runtime. Pin the native game ID and index MD5
   when the mapping depends on an edition. Check which room IDs, banks, keys,
   and indices the provider actually publishes.
2. Trace game scripts, engine code, or native telemetry across all chapters and
   known routes. For each candidate, identify the initial state, the writes
   that mark an outcome, later changes, and what survives a save restore.
   Cross-check script resource IDs against the IDs the runtime publishes.
   Walkthroughs can help enumerate outcomes. Verify their state predicates
   independently against game data or telemetry.
3. Keep private research and captures in the repository's ignored `.internal/`
   directory. Use an isolated MCP profile for live checks and preserve existing
   saves and active sessions. Use the Play Game skill for requests to play or
   finish a game.

## Write the map

- Put the runtime map in ScummWEB at `data/game-internals/<game-slug>.yaml`. Add verified
  labels to `rooms`, `variables`, `flags`, and `objects` as applicable, including
  useful state that has no milestone. Reference those labels in rules. Keep
  existing IDs stable when expanding a map and comment on opaque native values.
- Include every distinct puzzle or story outcome the evidence and telemetry
  support. Prefer markers that persist in saves. Use `any` for equivalent
  route outcomes and `all` where a room or value needs additional context.
  Check that `highest_stage` ordering makes sense for each supported route.
- Require evidence of a winning outcome for completion. Check that entry into
  an ending room, an escape in progress, and losing outcomes cannot match on
  their own. Use snapshot telemetry for keyed variables. Keep unsupported
  predicates in research notes or drafts outside the runtime map.

## Verify and report

- Run `node --test test/game-progress.test.mjs` in ScummWEB. Exercise its
  published tracker with native captures or synthetic inputs tied to traced
  script values. Check fresh state, intermediate stages, save restore,
  alternate routes, and the winning and losing endings where applicable.
- Update the coverage table in ScummWEB's `docs/game-progress.md` with the
  edition and milestone count. Report what was verified in live play
  or by replay, plus any routes, stages, or editions that remain unverified.
