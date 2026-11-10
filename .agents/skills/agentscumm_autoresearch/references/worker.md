# Trial worker contract

The coordinator supplies this contract, the common brief, one frozen strategy,
a trial directory, and `codex-args.json`. Do not read sibling trials, prior
gameplay memories, or other strategy files. You are not alone in the workspace.
Own only the assigned trial directory; do not edit tracked files, commit, push,
change branches, stop another server, or delegate. Purchases, billing changes,
credentials work, and outbound communications are outside this task. Reply only
to the coordinator.

Your instructions must describe a generic strategy usable in any room. No common
brief, strategy, dispatch, or supplied reference may provide a room solution,
object/item hints, combinations, target coordinates, prescribed gameplay
sequences, or discoveries from earlier trials. Game identity, goal, start mode,
tool mechanics, and operational/evaluator configuration are allowed. If supplied
content violates this boundary, report the contamination and stop before play;
do not use it, even if other workers received it too. Discover room-specific
actions only from your own trial's MCP observations. Do not consult walkthroughs,
external solution research, or previous gameplay records.

Read the owning `AGENTS.md` and `agentscumm_play-game` skill. Reuse the
coordinator's synchronization and verify the same clean full commit. Preserve
its immediately initialized `run.json`. Prepare the player prompt from the
common brief, frozen strategy, deadline, and gameplay requirements below.
Include the same file ownership, delegation and assistance limits in the prompt.
Store the prompt and the Codex JSONL output in the trial directory. Launch a
fresh Codex player from the repository root with the recorded model and effort:

```sh
python3 -c 'import json,os,pathlib,sys; p=pathlib.Path(sys.argv[1]); args=json.loads((p/"codex-args.json").read_text()); prompt=(p/"player-prompt.md").read_text(); os.execvp("codex", ["codex", "exec", *args, "-m", sys.argv[2], "-c", "model_reasoning_effort="+sys.argv[3], "--", prompt])' TRIAL_DIRECTORY MODEL EFFORT > TRIAL_DIRECTORY/codex-events.jsonl
```

The player uses the AgentSCUMM tools attached by Codex. In its startup prompt
require an immediate `status` call, verification of the expected clean
`sourceRevision` and trial-owned `dataDir`, and recording of the actual server
PID and provenance in `run.json`. Before sending game input, capture startup
tools and immutable prompt assets in `initial-context.json` and initialize
`memory.md`. A stale revision or mismatched profile blocks the trial.

Start `flight-of-the-amazon-queen` with `mode: "skip_intro"`,
poll readiness, and verify your own visible spectator: require `status.viewerOpen`
to be true with no `viewerError`. Never pass `--no-viewer`. If necessary, call
`session(operation="open_viewer")` and check status again. Record the viewer
verification in `run.json`; if the window cannot open, exit the owned
Codex process and report `infrastructure_failure` rather than continuing hidden.
Play using MCP screenshots/controls. Follow your strategy
within the common permissions/assistance policy. Ground dependent actions in
actual screenshots; a prediction is not an observation. Preserve failed actions
and uncertainties in memory, rather than repeating unchanged inputs.

Do not read variables, receipts, browser storage, or engine internals to choose
gameplay actions. Keep the configured endpoint and use the attached MCP tools.
A server disconnect at the endpoint ends the player turn. Preserve the outcome
and finish the turn so Codex can exit. After the player exits, read the evaluator
receipt to report the outcome and keep the evaluator and server logs intact.
This endpoint does not authorize completed-game finalization.

Use your individual `dispatch.json` deadline, including startup. Preparation
before dispatch is outside the budget; do not reset it or use another worker's
deadline. On timeout or a blocker,
close only the owned Codex process, preserve evidence, and return. Do not restart for a
cleaner score. Record the actual exit code returned by the process tool; requested
termination or a receipt alone does not prove exit status. Verify your recorded
server PID also exited. Clean up an orphan only after verifying it is your exact
owned server; never stop another trial's process.

After Codex exits write `worker-result.json`:

```json
{
  "status": "finished",
  "codex_exit_code": 0,
  "process_exited": true,
  "server_process_exited": true,
  "summary": "Describe the observed outcome and your strategy's main successes or failures.",
  "evidence": ["screenshots/actual-image.jpg"]
}
```

Use `finished`, `timeout`, `blocked`, or `infrastructure_failure` honestly. Missing
exit telemetry remains null. The receipt establishes success, not the word
`finished`. Preserve the starting revision in `run.json`; record outcome without
calling the game completed. Return the trial path, process outcome, useful
screenshots and brief evidence-based analysis. The coordinator scores separately.
