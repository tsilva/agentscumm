---
name: agentscumm_autoresearch
description: Run one research round with up to three parallel agents testing generic room-solving strategies without solution hints or hardcoded gameplay recipes, using visible spectator windows and comparing verified action counts at Flight of the Amazon Queen's laundry endpoint.
---

# agentscumm: Autoresearch

Run **one round**, with up to three fresh subagents in parallel. This first version
is fixed to Flight of the Amazon Queen's opening hotel and laundry endpoint.
Stop each trial with the launcher's evaluator, then compare verified successes by
**total submitted primitive actions**, not `act` calls. Finish with a report and
wait for explicit user confirmation before another round. Creating or editing
this skill does not launch a round.

## Mandatory strategy boundary

Experiments test **generic strategies that apply to any room**. Never supply
direct or indirect room-solving hints, hardcoded gameplay actions, or solution
recipes. This applies to strategy files, common briefs, dispatch messages,
supplied references, and follow-up messages, even when every worker would get
the same hint. Do not provide known object/item names or combinations, target
coordinates, verb/target sequences, prerequisite routes, object-specific
exclusions, previous successful trajectories, or shortened candidate solutions.

Only the game identity, requested goal, starting mode, tool mechanics, and
operational/evaluation configuration may be task-specific. The fixed endpoint
below is evaluator configuration, not a player action prescription. Players
must discover room-specific targets and solutions from their own trial's MCP
observations. Do not supply walkthroughs, prior gameplay memories, or external
solution research; this overrides Play Game's usual permission to use them.

Before initialization and dispatch, review every player-facing asset for this
boundary. Remove contaminated content before starting a trial. Previous results
may suggest generic changes to observation, planning, batching, or recovery;
never pass their puzzle discoveries to a new player. Keep fresh workers on
`fork_turns: "none"` and do not expose prior-round directories to them.

## Prepare

Read the owning `AGENTS.md` and sibling
[`agentscumm_play-game`](../agentscumm_play-game/SKILL.md). Reuse its clean-commit,
upstream synchronization, immediate `run.json` initialization, startup-context,
and running-server provenance requirements. Do not commit, stash, hide edits,
or relax a blocked gate to start experiments. The coordinator synchronizes once;
workers reuse that successful synchronization and frozen commit.

Select an evaluator-capable ScummWEB **production** build without development
reloads. Reuse a verified local server or the user's selected origin. Do not
hardcode a temporary port, silently use an unpatched deployment, or rebuild,
deploy, or modify the environment during a round. Provider support is documented
in `docs/usage.md`; missing telemetry is an infrastructure failure, not permission
to substitute screenshot matching.

Freeze the same model/effort, game/runtime artifacts, starting mode `skip_intro`,
tool permissions, resource limits, assistance, and common knowledge for all
workers. Inherit the model/effort unless the user requests another; do not compare
different models accidentally. Default to ten minutes wall time per trial,
including startup, unless the user selects another limit. The coordinator monitors
this limit; the launcher does not enforce it. Concurrent contention makes times
diagnostic rather than a controlled speed benchmark.

Prepare up to three strategy files containing distinct behavioral hypotheses and
actual player instructions. Possible contrasts are observing after state changes,
batching only currently predictable inputs, and forming a short hypothesis from
the current observation before acting. These are examples, not mandatory variants.
Give every worker the same permitted starting knowledge and operational references,
subject to the mandatory strategy boundary. Cross-trial gameplay evidence stays
with the coordinator, including across later rounds.

Initialize private coordinator/trial directories with immutable strategy copies:

```sh
python3 SKILL_DIR/scripts/research.py init --base-url SCUMMWEB_ORIGIN \
  --common-context COMMON_CONTEXT_FILE \
  --strategy STRATEGY_A --strategy STRATEGY_B --strategy STRATEGY_C
```

Resolve `SKILL_DIR` from this skill's real path. The helper checks a clean checkout
and ignored storage, verifies the compiled Queen provider, and records its hash.
It does not synchronize Git, start games, or spawn agents. Add
`--previous-round ROUND_DIRECTORY` only after user confirmation of another round;
it records lineage and creates fresh profiles, not authorization.

## Dispatch

Use `collaboration.spawn_agent`, not sidebar chats. Spawn up to three workers
before waiting so they run in parallel, within available slots. Explain when
fewer can run. Do not replace a failed trial with an unreported fourth.
Each trial owns its own MCP process, profile, run directory, memory, screenshots,
logs, and request IDs. Never share the attached MCP connection or an in-use profile.
Each trial must open its own **visible AgentSCUMM spectator window**. The helper
leaves the server's default viewer enabled; never add `--no-viewer`. After game
startup, require `status.viewerOpen: true` and no `viewerError`. If needed, use
`session(operation="open_viewer")` and check status again. An unavailable spectator
is an infrastructure failure; do not silently continue hidden or use browser
automation to replace the MCP spectator.

Immediately before each spawn, run `research.py dispatch ROUND_DIRECTORY TRIAL_ID`.
It records that worker's own dispatch timestamp and deadline; preparation time
before dispatch does not consume its budget. Do not reuse a shared deadline or
reset a dispatched trial's clock. Record the agent ID separately after spawning.

Use `fork_turns: "none"` to avoid leaking prior solutions from this chat. Give
each worker the same common brief, its frozen strategy, and
[the worker contract](references/worker.md). Include the verified root,
coordinator/trial paths, synchronized commit, model/effort, origin, deadline,
assistance policy, and that worker's writable ownership. Workers are not alone
in the workspace and must preserve others' files. They may not delegate or edit
tracked files. Record the returned agent IDs against their trials. Do not send
gameplay hints mid-trial.

Include the worker contract's Codex launch example in each dispatch. Each
trial's `codex-args.json` configures the AgentSCUMM stdio server in its own
Codex process. Require a successful `status` response through that connection
before gameplay. The server arguments include this endpoint:

```json
{"all":[{"variable":"engine_id","equals":"queen"},{"variable":"room_id","equals":71},{"variable":"room_ready","equals":true}]}
```

This affects only these experiment launches. Ordinary launches retain their
existing defaults. Players interact solely through their own AgentSCUMM MCP.
Native variables, receipts, other trials, and game/runtime internals are evaluator
data, not gameplay hints. Directory ownership is a collaboration rule, not
enforced isolation from unrestricted filesystem/browser access; disclose this
limitation rather than claiming the evaluator is inaccessible.

Wait while monitoring deadlines. On timeout the worker closes only its owned
Codex process and records `timeout`. If necessary interrupt the worker and close
its exact owned Codex process after checking process ownership. Verify that its recorded
server PID also exited; if orphaned, stop only that verified owned server.
Preserve partial evidence.
Distinguish infrastructure failures from unsuccessful strategy execution.
Never erase failed inputs or silently restart for a cleaner score.

## Evaluate and report

Follow [the evidence/scoring contract](references/scoring.md). Compute metrics
from server logs, not agent claims. Check receipts, Codex exit outcomes, startup
revisions, and final screenshots. Independently inspect each claimed-success image
and bind `review.json` to the receipt and screenshot checksums. This endpoint is
not game completion: do not queue completed-game finalization or benchmarks.

After every trial has a terminal result and its owned processes have exited:

```sh
python3 SKILL_DIR/scripts/research.py score ROUND_DIRECTORY
```

The helper rechecks provider identity and evidence, then writes `scores.json` and
a private `report.md`. It counts all steps in each unique accepted `act` request,
including waits, screenshots and canceled trailing steps. Cached retries do not
repeat accepted inputs. Delivered/attempted steps, calls, observations, rejected
calls, wait duration, and elapsed time are separate diagnostics. This accepted-
request metric does not claim every submitted step executed or exactly reproduce
older rollout-based benchmarks.

Rank only verified successes under frozen conditions. Failed, timed-out,
infrastructure-failed, and unverified trials cannot win by doing less. Equal
primary scores are ties. One trial per idea identifies the best observed result,
not a statistically established best strategy.

Review frozen briefs, dispatches, supplied references, and worker records for
solution hints and visible-window compliance before declaring a strategy result.
Disqualify a contaminated or hidden trial from the generic-strategy comparison,
preserving its raw score and evidence with the explicit protocol deviation.
Equal distribution of a solution hint does not make it valid. Historical assisted
route tests remain historical evidence, not valid generic-strategy experiments.

Complete the generated report with per-strategy evidence: what it attempted,
where extra actions arose, and why the records suggest it performed worse.
Distinguish observed wasted inputs from causal hypotheses; counts alone do not
prove causes. Include missing evidence and comparison limits. Keep reports in
the ignored round directory and give the user a compact comparison table,
best observed result/tie/no-winner, and report link in this chat.

Identify verified generic winners as candidates for the
[Play Game strategy catalog](../agentscumm_play-game/references/strategies.md).
Preserve their exact tested instructions in the round snapshot. Follow the
catalog's retention procedure when the user asks to keep a strategy.

End the round there. Propose next hypotheses if useful and ask whether to run
another round, but do not create a new round, spawn players, or start games
until the user explicitly confirms. Confirmation authorizes one new round of
up to three fresh experiments, never an unbounded loop.
