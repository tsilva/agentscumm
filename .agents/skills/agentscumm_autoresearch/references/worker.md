# Trial worker contract

The coordinator supplies this contract, the common brief, one frozen strategy,
a trial directory, and `client-args.json`. Do not read sibling trials, prior
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
its immediately initialized `run.json`. Before game interaction, capture exposed
startup tools and immutable prompt assets in `initial-context.json`, create
`memory.md`, and verify the fresh local MCP's `sourceRevision` matches the commit
with `dirty: false`. Record connection route, provenance, client tool-session ID,
PID and server PID. A stale or dirty revision blocks this trial.

Start the maintained Play Game client in a persistent PTY (`exec_command` with
`tty: true`) from the repository root. Load `client-args.json` without consuming
the stdin that the client needs for later MCP requests. For example, replace
`TRIAL_DIRECTORY` with your absolute assigned path:

```sh
python3 -c 'import json,os,sys; os.execvp("node", ["node", ".agents/skills/agentscumm_play-game/scripts/mcp-client.mjs", *json.load(open(sys.argv[1]))])' TRIAL_DIRECTORY/client-args.json
```

Use `python3 -c`, a direct argument array, or equivalently stdin-preserving
launching. Do not use `python3 - <<...`, a one-shot pipe, or stdin redirection:
the client would inherit EOF and close immediately. Retain the returned process
session and send subsequent JSON lines with `write_stdin`. After `ready`, send
`{"name":"status","arguments":{}}` through that same session immediately.
Verify the response, server PID, and clean required revision before launching a
game; a `ready` line alone does not establish a usable connection.

Use a fresh process/profile; do not add `--resume`.
Start `flight-of-the-amazon-queen` with `mode: "skip_intro"`,
poll readiness, and verify your own visible spectator: require `status.viewerOpen`
to be true with no `viewerError`. Never pass `--no-viewer`. If necessary, call
`session(operation="open_viewer")` and check status again. Record the viewer
verification in `run.json`; if the window cannot open, close only your owned
client and report `infrastructure_failure` rather than continuing hidden.
Play using MCP screenshots/controls. Follow your strategy
within the common permissions/assistance policy. Ground dependent actions in
actual screenshots; a prediction is not an observation. Preserve failed actions
and uncertainties in memory, rather than repeating unchanged inputs.

Do not read variables, receipts, browser storage, or engine internals to choose
gameplay actions. Do not change the endpoint or install a controller. After the
client ends, you may read its receipt to report the outcome. Never force success,
edit evaluator/server logs, or restart after expected success. This endpoint
does not authorize completed-game finalization.

Use your individual `dispatch.json` deadline, including startup. Preparation
before dispatch is outside the budget; do not reset it or use another worker's
deadline. On timeout or a blocker,
close only your client, preserve evidence, and return. Do not restart for a
cleaner score. Record the actual exit code returned by the process tool; requested
termination or a receipt alone does not prove exit status. Verify your recorded
server PID also exited. Clean up an orphan only after verifying it is your exact
owned server; never stop another trial's process.

After the client exits write `worker-result.json`:

```json
{
  "status": "finished",
  "client_exit_code": 0,
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
