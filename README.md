<p align="center">
  <img src="logo.png" alt="AgentSCUMM Cursor Detective logo" width="256" />
  <br />
  <!-- repo-tagline:start -->
  <strong>🕹️ Give AI agents controls for point-and-click adventures 🧭</strong>
  <!-- repo-tagline:end -->
</p>

# AgentSCUMM

Give Codex reliable controls and visual history for point-and-click games running on [ScummWEB](https://scummweb.tsilva.eu).

AgentSCUMM is a local MCP server. It owns a headless Chromium game session, opens a separate read-only spectator window, and keeps a rolling buffer of recent frames. On macOS, the spectator is a native AgentSCUMM app with a dedicated cursor-detective Dock icon. Codex supplies the reasoning; this repository supplies the tools. No model API key or agent framework is required.

## Quick start

Requires Node.js 22 or newer and pnpm 10. A graphical desktop is needed for the spectator window.

```sh
pnpm install --frozen-lockfile
pnpm browser:install
```

From the repository root, register the server with Codex:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js"
```

Open a new Codex chat with the MCP enabled. Ask it to launch `flight-of-the-amazon-queen`, wait for readiness, and show a screenshot. A spectator window opens automatically. Close that window freely; the game continues, and `open_viewer` can reopen it.

On macOS, install Xcode Command Line Tools if needed (`xcode-select --install`). The first spectator launch compiles a small AppKit/WebKit app and converts `macos/AppIcon.png` into its Dock icon, without additional npm dependencies. Later launches reuse the built app. You can build it ahead of time with `pnpm build:macos`, which prints the `.app` path. Launch it through MCP so it receives the active spectator session; the bundle is not a standalone game launcher. The app also appears in Command-Tab. Its window title includes the app and running game, such as **AgentSCUMM - Flight of the Amazon Queen**. Closing its window or choosing **Quit AgentSCUMM** closes only the spectator; closing the MCP server terminates both the game and spectator. If the native build fails, the game remains available through MCP and `session_status` reports `viewerError`.

To register without automatically opening a window:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js" --no-viewer
```

`node src/index.js` runs the stdio server directly. Its stdout is reserved for MCP protocol messages. It does not launch a game until a client calls `launch_game`.

## Tools

| Tool | Behavior |
| --- | --- |
| `list_games` | Lists known catalog slugs and intro-checkpoint support. Future catalog slugs can also be launched. |
| `launch_game` | Starts loading a game and returns a session ID immediately. Supports `skip_intro` (default), `beginning`, and `continue`. Calling again for the active game reuses it unless `restart: true`. |
| `wait_for_ready` | Waits up to 25 seconds and returns current status. Repeat while `phase` is `loading`. |
| `session_status` | Reports startup phase, connection, capture health, history coverage, spectator URL, and recent actions. |
| `observe` | Returns a fresh screenshot as an MCP image, with frame ID, session ID, timestamp, viewport dimensions, and canvas bounds. Loading/error observations are unbuffered and cannot be retrieved with `get_frame`. |
| `act` | Executes a serialized action sequence, reports each step, and returns a fresh screenshot. |
| `review` | Returns up to eight timestamped frames from the recent buffer, optionally associated with an action ID. |
| `get_frame` | Retrieves a retained frame at full resolution. Evicted frames produce an explicit error. |
| `open_viewer` | Opens or reopens the spectator window. |
| `close_game` | Closes the game page and clears buffered frames, preserving saves and cached assets. |

Flight of the Amazon Queen is the default game. Escaping its opening hotel is the first planned solving milestone. The MCP provides the interface; Codex handles gameplay through the skill below.

## Play Game skill

Use [$agentscumm_play-game](.agents/skills/agentscumm_play-game/SKILL.md), displayed as **agentscumm: Play Game**, and name a game:

```text
$agentscumm_play-game Flight of the Amazon Queen
```

Codex launches the game, opens the spectator, and works toward its ending. It keeps free-form memory, screenshots, references, and other working data in ignored `.internal/<game-id>/<run-id>/`. Discovery, planning, and memory stay outside the MCP. You can ask it to resume a prior run or stop at a chosen milestone; completion requires evidence from the game.

The skill is stored in `.agents/skills/agentscumm_play-game/` and is discoverable in this repository. To expose this repository-owned skill globally on another machine, link that folder into the host's skill directory:

```sh
mkdir -p "$HOME/.codex/skills"
ln -s "$PWD/.agents/skills/agentscumm_play-game" "$HOME/.codex/skills/agentscumm_play-game"
```

The gameplay skill interacts with games only through the attached AgentSCUMM MCP tools. Backend selection and implementation belong to the server. If the tools are unavailable, the skill reports the connection blocker. Local run records and web research, including walkthroughs, remain available to support gameplay. Resume can restore only progress saved by the game.

Play Game captures startup context and automatically queues verified run export and a local benchmark entry after observing the ending. The finalizer waits for that gameplay turn to finish so its final reply and usage are included. Its `finalization.json` receipt distinguishes queued work, success and archival failures; see [automatic finalization](docs/run-context.md#automatic-finalization).

## Benchmarks

See [BENCHMARKS.md](BENCHMARKS.md) for recorded results, assistance, measurements, and comparison limits. The first logged result is a completed, walkthrough-assisted Flight of the Amazon Queen playthrough.

## Export a run

Keep working notes in `.internal/<game-id>/<run-id>/`. Completed conversation archives use a separate, Git-ignored layout:

```text
runs/<game-id>/<yyyymmdd>-<run-id>/
├── trajectory.jsonl
├── run.json
├── skills/
│   ├── index.json
│   └── skill-<sha256>/
│       ├── SKILL.md
│       └── ...
└── screenshots/
    ├── index.json
    └── screenshot-<sha256>.jpg
```

The date is the conversation's start date in Europe/Lisbon. The run ID defaults to its conversation UUID; `--run-id` can provide a different identifier. Python 3.9 or newer is required. No Python packages or model API calls are needed.

After observing the game's ending and letting the gameplay conversation finish its turn, export its local Codex rollout:

```sh
python3 -B scripts/export-run.py \
  --rollout /path/to/rollout.jsonl \
  --game-id flight-of-the-amazon-queen \
  --status completed \
  --completion-note "The ending and credits were observed." \
  --walkthrough-url https://www.walkthroughking.com/text/flightoftheamazonqueen.aspx
```

Use the ending evidence actually observed in `--completion-note`. The exporter checks that the latest recorded turn finished, but does not infer a game victory from a conversation finishing. For a preparatory snapshot, omit `--status completed` and use a distinct `--run-id`; its status remains `in_progress`. Existing archives are never overwritten. Exporting reads a bounded source snapshot and does not connect to, stop, or restart the game.

`trajectory.jsonl` preserves every available rollout record in order, including messages, tool calls and results, context changes, compactions, and per-response token telemetry. It replaces embedded image bytes with relative screenshot references containing `id`, `path`, and `sha256`. JSON-encoded tool-result strings retain their string wrappers. Available opaque reasoning records are preserved without attempting to decrypt hidden content. This is a conversation archive, not a video recording of every periodic MCP frame.

`skills/` bundles skill directories referenced by `SKILL.md` paths or `$skill-name` invocations in conversation messages and tool arguments. It excludes passive host catalogs, injected instruction blocks, and tool results from discovery. Relative paths follow the recorded conversation/turn working directory. Named skills resolve against referenced paths and conventional local/global skill directories; missing or ambiguous names require a repeatable `--skill /path/to/SKILL.md` argument. This option also adds a skill that automatic discovery missed.

Each bundle includes `SKILL.md` and its local supporting scripts, references, and assets. `skills/index.json` maps original references and trajectory record numbers to archived files, SHA-256 hashes, byte counts, and executable flags. Skill discovery symlinks resolve to their actual directory; links inside that directory are not followed. Caches and dependency installations are excluded. Missing skills and omitted links, special files, or environment/access configuration are reported explicitly and prevent a completed export. Copied files remain private with mode `0600`; original executable flags are recorded in the manifest.

These are **export-time local snapshots**, not verified copies of the versions used during play. The recorded tool outputs remain in the trajectory as evidence of what was read. For future reproducibility, capture skills and effective instructions at run start. Supporting files outside a skill directory, external dependencies, run memory, walkthrough page contents, game data, saves, and runtime binaries are not bundled automatically. The exporter neither executes skills nor downloads their dependencies.

Screenshot IDs derive from their full image bytes. Identical images share a file; `screenshots/index.json` maps each file to its original MCP frame IDs, session IDs, timestamps, and action associations where available. Truncated native tool summaries are recovered from full copies using matching length and preserved prefix/suffix, or a recognized direct image-forwarding loop with matching fragments and output order. A summary truncated across multiple images retains its recorded fragment and links the full forwarded image sequence; missing intermediate frame metadata stays explicitly unavailable. Unrecoverable or non-embedded images are reported explicitly and prevent a completed export.

`run.json` records:

- Game, run/conversation IDs, status, completion evidence, source cutoff, and wall-clock duration including pauses.
- Recorded harness name/version/provider, model IDs, reasoning levels, and token usage grouped by model and effort.
- Git commit IDs and branches: `git.conversation` preserves the checkout recorded when the conversation started; `git.agentscumm_runtime` preserves server-start revisions returned by AgentSCUMM; `git.exporter` separately records the export checkout. Dirty working trees are flagged where recorded. Missing startup provenance remains explicitly unknown; an export-time commit is never substituted for it. A conversation checkout commit does not establish the deployed ScummWEB revision.
- Total input, cached input, uncached input, output, reasoning output, request count, and uncached-input-plus-output tokens. Cached input is already part of input; reasoning is already part of output.
- Unique screenshot files, all image references, model-visible image blocks, original frame IDs, recovered truncated references, and unresolved images.
- Skill bundle/file/reference counts, manifest checksum, unresolved references, and explicit export-time provenance.
- `agent_context`: available-at-start tool definitions (including non-MCP tools), separately observed tool use, and checksummed initial prompt assets with trajectory references. Missing initial inventory remains explicitly unknown. Use `--agent-context` for additional startup declarations and injected files; see [the context format and provenance rules](docs/run-context.md).
- MCP call counts, input actions by type, `total_actions` (the sum across action types), `total_action_sequences` (the number of submitted `act` calls), summed MCP call time, and declared walkthrough URLs. These totals follow the submitted-input accounting below, including waits; they do not establish that the game accepted every input.
- `action_sequences`: sequence count plus `min_length`, `max_length`, `mean_length`, `std_length`, and `median_length`. Length is the number of submitted inputs in each recorded `act` call, including waits, moves, keys, clicks, and drags. Duplicate telemetry is counted once; failed calls and separately requested cached replays are included as submitted calls. Standard deviation uses the whole population (`std_type: "population"`). A run with no sequences has count zero and null length statistics; one sequence has standard deviation zero.
- Source and trajectory checksums, record counts, schema versions, coverage, and limitations. Usage is validated against cumulative counters; missing or inconsistent telemetry fails explicitly.

Verify a portable archive independently:

```sh
python3 -B scripts/export-run.py --verify runs/GAME_ID/YYYYMMDD-RUN_ID
pnpm test:export
```

Verification checks the trajectory checksum and record count, image checksums and references, the skill manifest and file inventory, and initial prompt asset references and copies. Verification works without the original skill or asset files. New archives use schema version 2 in `run.json`; older version 1 archives, including those named `stats.json`, remain verifiable with their original coverage limits. Archives contain the complete recorded conversation and remain private local files under ignored `runs/`.

## Price Thread skill

Use [$agentscumm_price-thread](.agents/skills/agentscumm_price-thread/SKILL.md), displayed as **agentscumm: Price Thread**, to estimate what a Codex conversation's recorded model-token usage would cost through the OpenAI API:

```text
$agentscumm_price-thread Calculate the API cost of my Flight of the Amazon Queen conversation.
```

The skill identifies the conversation, snapshots its recorded usage, resolves the models and reasoning efforts used, and fetches current official pricing. Its Python standard-library helper deduplicates response records, checks cumulative coverage, and prices each request using the applicable context band. Cached input and reasoning output are counted once. Reports include the usage cutoff and any assumed processing tier. Missing telemetry or unsupported pricing produces an explicit limitation instead of an invented total. No API key, gameplay session, or paid model request is required; the estimate preserves recorded cache usage and excludes unmeasured tool charges and taxes.

The skill is discoverable inside this repository. To make it available globally, link `.agents/skills/agentscumm_price-thread` into the host's skill directory using the same installation pattern as Play Game. Test the helper with:

```sh
python3 -B -m unittest discover -s .agents/skills/agentscumm_price-thread/tests -v
```

## Game controls

### Input and sequences

Coordinates use **CSS pixels relative to the fixed screenshot**, including any black borders. The default viewport is **960 × 720**, with device scale factor 1. ScummVM may change the internal canvas size during startup or menus; screenshots and input retain the fixed viewport coordinate system. Each observation includes `canvasBounds` to locate the game within that viewport.

Example `act` arguments, using IDs returned by `launch_game` and `observe`:

```json
{
  "session_id": "00000000-0000-4000-8000-000000000001",
  "request_id": "inspect-center-1",
  "frame_id": "frame-12",
  "actions": [
    { "type": "move", "x": 480, "y": 360 },
    { "type": "click", "x": 480, "y": 360, "button": "left" },
    { "type": "wait", "milliseconds": 800 }
  ],
  "after_ms": 200
}
```

Supported actions:

- `move`: `x`, `y`.
- `click`: `x`, `y`, optional `button` (`left`, `right`, `middle`), `count` (1 or 2), and `hold_ms` (20–1000, default 80).
- `key`: a single gameplay `key`, with optional `hold_ms` (0–5000, default 80). Letters, digits, F1–F12, arrows, Enter, Escape, Space, Tab, Backspace, Delete, Home, End, PageUp, PageDown and individual modifier keys are supported. Browser shortcut combinations are rejected.
- `drag`: `from_x`, `from_y`, `x`, `y`, optional `button` and `steps`.
- `wait`: `milliseconds` (0–10000).

Sequences have at most 32 actions and a 20-second combined explicit hold/wait budget. The whole sequence is validated before input starts. Sequences run serially and stop at the first error. `after_ms` allows 0–3000 ms before the final screenshot; this is a bounded delay, not a promise that a game animation has ended.

**Retry the same request ID with identical arguments after an uncertain tool response.** The last 100 request results are cached per game session, and retries return the original result without sending input again. Older used IDs are rejected rather than re-executed. A session accepts up to 10,000 distinct action requests. A new launch creates a new session ID and clears request history; stale session IDs cannot issue input.

Results distinguish delivered steps from uncertain delivery and include partial completion if a sequence fails. Delivered means the browser accepted the input operation, not that the game completed the intended action. Inspect the returned screenshot or history before deciding what to do next. MCP cancellation interrupts explicit waits and key holds and stops the remaining sequence; already delivered input is not undone. The same request ID continues to identify that attempt.

### Visual history and spectator

Periodic capture starts only when the game reaches `ready`, then runs independently of MCP calls, targeting 10 frames per second. Loading dashboards and startup-check screenshots never enter the buffer. Explicit `observe` calls can still inspect loading or failed sessions, but those images are unbuffered and unavailable through `review` or `get_frame`. The spectator begins displaying game frames at readiness. The rolling JPEG frame buffer retains up to 60 seconds, capped at 64 MiB; actual coverage and freshness are reported by `session_status`. Capture rate depends on rendering and machine load. This is sampled visual history, not a guaranteed recording of every animation frame or an encoded video file. Audio is not captured.

Use `review` with `seconds` and `count`, then `get_frame` for any retained frame ID. Frames carry timestamps, game/session IDs, and action/step associations. Buffer data stays in memory and expires; game changes and `close_game` clear it. Action results retain their associated screenshot while cached, but do not create a permanent video archive.

The viewer serves only screenshots and status from a loopback HTTP endpoint. Its window is separate from the hidden game, so viewer clicks, keyboard input, resizing, and closure cannot change game input or viewport dimensions. It displays only the game canvas, cropping each buffered frame to its recorded canvas bounds. ScummWEB's web-player menu, exit, fullscreen, and touch-control overlays are hidden in the owned session. There are no spectator headers, status bars, or added letterboxing; status remains available through MCP. This display crop does not change MCP screenshots or input coordinates, which still use the fixed viewport. On macOS, AppKit keeps the resizable content area at the game's aspect ratio and owns the window title and Dock icon, while a WebKit view with an ephemeral data store displays the game-only spectator page. Other platforms use a separate Chromium app window. No control endpoint is exposed through the viewer.

### Loading, recovery, and storage

Launch readiness combines ScummWEB's target-matched runtime status, intro-checkpoint status when requested, loading state, a rendered-frame signal, and two visual checks inside the canvas area. A timeout fallback is reported as an error rather than accepted as proof of a rendered scene. A completely black startup scene remains unconfirmed until visible content appears or the launch deadline expires. Readiness can still show a menu or intro; Codex inspects the screenshot to decide the next action.

The startup deadline is 120 seconds. `wait_for_ready` uses shorter bounded calls so client tool timeouts need not cover the complete download. Failures remain inspectable through `session_status` and, when possible, `observe`.

The owned browser profile, game saves/cache, and JSONL action logs live in ignored `.agentscumm/` next to this repository by default. Cached macOS app bundles live in repository-local `.agentscumm/macos/`, keyed by their source, app icon, and architecture. The browser uses no personal browsing profile. Only one running server should use a given data directory; give parallel servers distinct `--data-dir` values. Do not remove an in-use profile.

An unexpected game-document replacement or browser/page closure blocks further input. Use `close_game` and relaunch explicitly; `mode: "continue"` requests ScummWEB's supported save recovery. There is no automatic restart, arbitrary rewind, or MCP save/load API in this version. Closing the MCP client terminates its owned game and viewer processes while leaving persistent browser data intact. Browser/process failure can lose progress since the last game save. Host sleep also stops useful capture.

CLI options:

| Option | Default |
| --- | --- |
| `--width`, `--height` | `960`, `720`; integers from 320 to 1920, fixed for the server lifetime |
| `--fps` | `10`; 1–15 |
| `--buffer-seconds` | `60`; 1–300, still subject to the byte limit |
| `--data-dir` | Repository-local `.agentscumm/` |
| `--base-url` | `https://scummweb.tsilva.eu`; override for a compatible development instance |
| `--no-viewer` | Disable automatic spectator-window opening; its read-only URL remains available |

## Development and verification

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
