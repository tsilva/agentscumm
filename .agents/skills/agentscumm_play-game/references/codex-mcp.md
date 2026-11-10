# Codex MCP setup and recovery

Register AgentSCUMM as a standard stdio server in Codex. From the repository root:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js"
```

Codex starts the server and discovers its tools. Use the configured `agentscumm` tools to call `status`, `session`, `observe` and `act`. Set `tool_timeout_sec = 60` or higher on the AgentSCUMM MCP entry if the host's timeout would end an action before its reply.

If a connection still reports an older checkout, reload MCP through Codex or open the checkout in a fresh Codex session with the configured server. Compare the new server's `status.sourceRevision` with the clean checkout before playing. Save any active progress before restarting its server, and keep unrelated instances running.

For persistent or isolated saves, configure `--instance NAME` or `--data-dir PATH` on the server command. To resume a previous run, reuse its original data directory and pass its recorded `run_id` to `session` with `mode: "continue"`. Historical fallback runs stored the data directory at `RUN_DIRECTORY/browser` and the save-scope ID in `RUN_DIRECTORY/run-scope.json`. Keep that profile intact and check that its previous owner has exited before connecting. Older unscoped saves use Continue without a run ID.
