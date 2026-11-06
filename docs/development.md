# Development and verification

Run commands from the repository root.

```sh
pnpm check
pnpm test
pnpm test:live
```

`pnpm test` uses a synthetic local game and real owned Chromium sessions. It covers MCP image responses, delayed startup, coordinate delivery across canvas geometry changes, input validation, request deduplication, partial sequence failures, frame retention, spectator isolation, document replacement, relaunch, startup errors, MCP cancellation, and shutdown during an input wait.

On macOS, the test suite also opens the native spectator, checks its app identity and loaded icon, verifies successive live-frame requests, and checks reuse, reopening, owner-loss cleanup, and server shutdown. This check needs a graphical desktop and Xcode Command Line Tools.

`pnpm test:live` launches Flight of the Amazon Queen on the configured live site through stdio MCP, opens the spectator, checks screenshot dimensions, exercises click/key/wait sequences and retry behavior, and retrieves frame history. It uses an isolated temporary profile and closes its browser processes afterward. It does not solve the game. Local verification captures stay under ignored `.internal/`.

Runtime code is in `src/`: `index.js` exposes MCP, `runtime.js` owns the game lifecycle and controls, `viewer.js` serves the spectator, `macos-app.js` builds and manages the native spectator, `history.js` bounds visual history, and `actions.js` validates and serializes input. The macOS app source and bundle metadata are in `macos/`. ScummWEB remains the separate browser game runtime; this project does not copy its game payloads.

Dependency policy uses pnpm's seven-day minimum release age and blocks exotic transitive dependency sources. Lifecycle scripts are disabled; browser installation is an explicit step.

Project considerations and future work live in the ignored root `INBOX.md`. Imported source notes remain in ignored `.local/repo-inbox/`; local-only material requires independent backup.
