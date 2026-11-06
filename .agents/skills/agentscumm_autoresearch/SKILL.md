---
name: agentscumm_autoresearch
description: Run one research round with up to three parallel agents testing different instructions for reaching Flight of the Amazon Queen's laundry room with fewer total actions, compare verified results, and report before awaiting confirmation for another round.
---

# agentscumm: Autoresearch

Run **one round**, with up to three fresh subagents in parallel. This first version
is fixed to Flight of the Amazon Queen's opening hotel and laundry endpoint.
Stop each trial with the launcher's evaluator, then compare verified successes by
**total submitted primitive actions**, not `act` calls. Finish with a report and
wait for explicit user confirmation before another round. Creating or editing
this skill does not launch a round.

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
batching only currently predictable inputs, and planning a short prerequisite
route before acting. These are examples, not mandatory variants. Give every
worker the same supplied references and starting knowledge; do not give only one
a known solution or prior trajectory. Earlier results may inform new ideas, but
cross-trial evidence stays with the coordinator until this round ends.

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

Each trial's `client-args.json` configures the maintained Play Game
`scripts/mcp-client.mjs`, explicitly including this endpoint:

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
client and records `timeout`. If necessary interrupt the worker and close its
exact owned client after checking process ownership. Verify that its recorded
server PID also exited; if orphaned, stop only that verified owned server.
Preserve partial evidence.
Distinguish infrastructure failures from unsuccessful strategy execution.
Never erase failed inputs or silently restart for a cleaner score.

## Evaluate and report

Follow [the evidence/scoring contract](references/scoring.md). Compute metrics
from server logs, not agent claims. Check receipts, client exit outcomes, startup
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

Complete the generated report with per-strategy evidence: what it attempted,
where extra actions arose, and why the records suggest it performed worse.
Distinguish observed wasted inputs from causal hypotheses; counts alone do not
prove causes. Include missing evidence and comparison limits. Keep reports in
the ignored round directory and give the user a compact comparison table,
best observed result/tie/no-winner, and report link in this chat.

End the round there. Propose next hypotheses if useful and ask whether to run
another round, but do not create a new round, spawn players, or start games
until the user explicitly confirms. Confirmation authorizes one new round of
up to three fresh experiments, never an unbounded loop.
