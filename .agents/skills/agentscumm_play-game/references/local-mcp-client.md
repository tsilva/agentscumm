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
{"name":"session_status","arguments":{}}
{"name":"list_games","arguments":{}}
{"name":"launch_game","arguments":{"game":"flight-of-the-amazon-queen","mode":"skip_intro"}}
{"name":"wait_for_ready","arguments":{"timeout_ms":20000}}
{"name":"observe","arguments":{}}
```

These are sequential examples, not a startup batch: check `session_status.sourceRevision` against the required clean commit before launching, and poll readiness before acting. Each response reports `name`, `isError` and `content`. The client decodes text payloads and saves returned images in `screenshots/`, exposing their absolute paths in image entries. Inspect those files with the image-viewing tool. `events.jsonl` records calls and responses; retain it with the run. Continue using `act`, `review` and `get_frame` through this connection, preserving session/request-ID rules and screenshot-grounded actions.

The client uses an isolated `browser/` profile inside the run directory, rather than the desktop-attached server's default profile. It does not automatically inherit that profile's saves or live progress. Preserve existing sessions and profiles; never copy an in-use profile or silently replace an ongoing run with a new game. Reuse the local client's live process when healthy; `--resume` preserves its existing browser data after a restart, and `launch_game` with `mode: "continue"` requests supported save recovery.

Record `local_mcp_client` as the connection route and retain the server's actual `sourceRevision` separately from checkout metadata. Do not describe this route as a repaired desktop connection. Keep the client alive while gameplay or the spectator is needed. Send `quit` only when intentionally ending that connection; closing the client terminates its owned game and viewer while preserving saved browser data. A failed client startup or tool call is a concrete error to investigate, not permission to bypass MCP or invent progress.
