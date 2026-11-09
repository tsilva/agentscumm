# Local MCP connection

Use this already-authorized fallback when the attached AgentSCUMM connection is unavailable, stale or cannot reconnect. Do not ask the user for another exception or spend repeated turns debugging the desktop host. This client starts the same repository MCP server and forwards its tools; it is not a separate gameplay controller.

Complete the clean-commit gate, initialize or resume `run.json`, and capture startup context first. From the repository root, start the existing client with a persistent stdin-capable process session:

```sh
node .agents/skills/agentscumm_play-game/scripts/mcp-client.mjs \
  --run-dir .internal/GAME_ID/RUN_ID
```

Replace the placeholders with the verified run directory. Add `--resume` when that directory already contains the client's `browser/` data. Keep the process and its stdin alive; a one-shot shell pipeline would close the client and server. The initial JSON line reports `ready`, client PID, run directory and tool names. Initialization alone does not establish server freshness or game readiness.

Send one JSON tool call per line through that same process's stdin, using the current tool schemas. For example:

```json
{"name":"status","arguments":{}}
{"name":"list_games","arguments":{}}
{"name":"session","arguments":{"operation":"start","game":"flight-of-the-amazon-queen","mode":"skip_intro"}}
{"name":"status","arguments":{"wait_ms":20000}}
{"name":"observe","arguments":{}}
```

These are sequential examples, not a startup batch: check `status.sourceRevision` against the required clean commit before launching, and poll readiness before acting. Each response reports `name`, `isError` and `content`. The client decodes text payloads and saves returned images in `screenshots/`, exposing their absolute paths in image entries. Inspect those files with the image-viewing tool. `events.jsonl` records calls and responses; retain it with the run. Continue using `act` and the fresh/history/frames sources of `observe` through this connection, preserving session/request-ID rules and screenshot-grounded actions.

The client uses an isolated `browser/` profile inside the run directory, rather than the desktop-attached server's default profile. It does not automatically inherit that profile's saves or live progress. Preserve existing sessions and profiles; never copy an in-use profile or silently replace an ongoing run with a new game. Reuse the local client's live process when healthy. For new runs, the client writes `run-scope.json` once and reuses its ID with the browser profile on `--resume`. Older run directories keep the unscoped save recovery route. Start with `mode: "continue"` to recover the latest supported save. If `status.capabilities` includes `save` and `load`, those `act` steps accept integer native slots in this run's save scope.

### Recover a save from the attached server

Use this route when the attached connection has lost the original game instance and the run has a known native save. The new attached instance may report `idle` because it has a different data directory.

1. Identify the original data directory from recorded MCP status or its recorded instance ID and the [instance storage rules](../../../../docs/usage.md#parallel-instances). Verify that the original server and its Chromium process have stopped and that no other process owns that directory. If ownership is uncertain, leave the profile alone and report the recovery blocker.
2. In the same verified, ignored working run, copy the original data directory's `chromium-profile/` to a private `browser/chromium-profile/`. Check that the source profile and destination paths are free of linked directories. Keep the source intact. Use this copy only when the run has no existing `browser/` data or `run-scope.json`. Leave an existing local profile and scope intact and use its own resume route. For a scoped save, create `run-scope.json` with `{"runId":"RECORDED_RUN_ID"}` using the original game session's returned `runId`. Leave the scope file absent only for a verified legacy unscoped save.
3. Start the local client with `--resume`. Match the original ScummWEB origin with `--base-url` if it differs from the default. Verify `status.sourceRevision`, then launch the same game in `continue` mode with the original `run_id` when scoped. Compare the restored room and inventory with the recorded checkpoint before further inputs. If the expected save is missing, preserve both profiles and investigate the recovery through MCP.

Record the source instance, profile copy, save scope, restored checkpoint and new session in the run records. The copy holds save data needed for further resumes.

### Recover a legacy Beneath a Steel Sky slot

To bring a known manual slot from an older Beneath a Steel Sky run into its own save scope, add `--legacy-sky-slot N` to the client command with `--resume` and start in `continue` mode. Use the same profile and ScummWEB origin. A compatible ScummWEB build copies the slot and leaves the original intact. Verify the restored scene and native controls before play. Later resumes can omit this flag.

### Connection lifecycle

Record `local_mcp_client` as the connection route and retain the server's actual `sourceRevision` separately from checkout metadata. Do not describe this route as a repaired desktop connection. Keep the client alive while gameplay or the spectator is needed. Send `quit` only when intentionally ending that connection; closing the client terminates its owned game and viewer while preserving saved browser data. A failed client startup or tool call is a concrete error to investigate, not permission to bypass MCP or invent progress.

### Evaluator endpoint

For a user-requested evaluator endpoint, the client forwards `--stop-condition`
(the JSON predicate), `--evaluation-log`, `--result-file`, and `--telemetry-ms` to
the launcher. Configure these before startup; see the evaluator section in
`docs/usage.md` for the typed schema and supported native provider. Keep output
paths inside the ignored run directory. Preserve the requested condition in the
run manifest as an explicit run limit. Use the ordinary clean-commit and runtime
provenance gates; evaluator mode does not bypass them.

The launcher automatically closes the owned session when the condition matches.
The client reports `evaluation-ended` and its success flag/receipt path, then
exits. The final `act` response may be interrupted by transport shutdown. Verify
the receipt's `success: true` and `reason: "stop_condition"` before treating that
closure as the requested endpoint. Record the result and saved evidence in the
working manifest/memory. An endpoint success is not a completed game: do not
queue completed-game finalization or register a completed-game benchmark unless
the endpoint itself is verified game-ending evidence. Never restart the game
merely because an expected evaluator success closed its MCP connection.
