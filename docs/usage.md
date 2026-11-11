# Usage and controls

Run commands from the repository root. For installation and a first playthrough, start with the [README](../README.md#quick-start).

## Spectator setup

On macOS, install Xcode Command Line Tools if needed (`xcode-select --install`). The first spectator launch compiles a small AppKit/WebKit app and converts `macos/AppIcon.png` into its Dock icon, without additional npm dependencies. Later launches reuse the built app. You can build it ahead of time with `pnpm build:macos`, which prints the `.app` path. Launch it through MCP so it receives the active spectator session; the bundle is not a standalone game launcher. The app also appears in Command-Tab. Its window title includes the app, running game and instance, such as **AgentSCUMM - Flight of the Amazon Queen [agent-a]**. Closing its window or choosing **Quit AgentSCUMM** closes only the spectator; closing the MCP server terminates both the game and spectator. If the native build fails, the game remains available through MCP and `status` reports `viewerError`.

To register without automatically opening a window:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js" --no-viewer
```

`node src/index.js` launches the mandatory ScummBench MCP gateway. The globally installed editable `scummbench` command is required. Its internal AgentSCUMM backend is not a supported direct entry point. Its stdout is reserved for MCP protocol messages. It does not launch a game until a client calls `session` with `operation: "start"`.

### Query native game state

Start the MCP server with `node src/index.js --telemetry` and point `--base-url`
at a ScummWEB build with the telemetry query API. AgentSCUMM requests
`?telemetry=1` when launching a game through `session`.
Call the read-only `telemetry` tool with its `session_id` to get the detected
game identity, current room where the engine exposes one, and available bank
names and lengths. The tool works independently of game internals maps.

To read a bank, supply its name and optionally a zero-based `offset` and
`limit`. For example, after discovering a `script_vars` bank, call
`telemetry({session_id, bank: "script_vars", offset: 0, limit: 16})`.
The default page is 64 values and the maximum is 256. Keyed banks have
`keyed: true` in their metadata and return sorted `keys` alongside their
`values`. SCI values that refer to VM objects are `null` at their original
indices. Names, keys and indices are specific to the engine, game and edition.

The query reports `pending` during launch, `available` when a room or bank
has been sampled, and `unavailable` when neither is present. In any response
`room: null` and `banks: []` mean those fields are unavailable. A room
reported by name can have a null numeric ID. Some engines
expose just one of them or publish only after entering gameplay. Each sample
carries its runtime timestamp so callers can tell when it changes. Sampling
can miss values that change between engine ticks. The tool is only enabled
for a server explicitly launched with `--telemetry`. Ordinary gameplay
observations and the spectator do not expose raw values.

## Evaluator telemetry and stop conditions

The launcher accepts an evaluator-only condition before the MCP client starts a
game. For example, Queen's hotel laundry/downstairs room is room 71 in the
supported engine build:

```sh
node src/index.js --instance laundry-trial \
  --evaluation-log /absolute/trial/variables.jsonl \
  --result-file /absolute/trial/result.json \
  --telemetry-ms 250 \
  --stop-condition '{"all":[{"variable":"engine_id","equals":"queen"},{"variable":"room_id","equals":71},{"variable":"room_ready","equals":true}]}'
```

Launch the game through the usual MCP `session` tool after connecting. Each
trial needs fresh output paths. With no explicit paths, files are placed inside
that instance's data directory. Set these flags on the AgentSCUMM MCP server command in Codex.

Conditions are a nonempty `all` list of typed predicates. Room conditions
accept `engine_id` (string), `room_id` (integer), `room_name` (string), and
`room_ready` (boolean). Room snapshots include `schema_version` and
`native_time_ms`. Any evaluation option enables telemetry. Omit
`--stop-condition` to record variables without a success trigger.

This requires ScummWEB's opt-in evaluation hooks. Engine identity is available
for all games on a supporting runtime. Queen and SCUMM provide room and variable
telemetry. When a stop condition requires missing telemetry, evaluation fails
after five seconds of ready gameplay. No screenshot matching or inferred room
IDs substitute for missing telemetry. Pin the engine/game data build when
comparing trials. `room_ready` means the engine has presented an update with no
pending room transition or running cutaway; it does not promise dialogue has
finished or that a puzzle is solved.

The evaluator polls independently of player observations, and also checks
between delivered action steps. Native room/ready transitions are queued so a
brief visit between polls is retained. Queue overflow, invalid telemetry, and
backwards timestamps fail the trial. Snapshots are appended periodically to the
evaluation log, never to gameplay MCP results, frame metadata, or the spectator.
The sampling interval is 50–5000 ms (default 250). It bounds polling cadence,
not exact detection latency under load; receipts preserve both native event
time and host detection time.

On a match, the evaluator latches success, aborts pending waits/key holds, blocks
later steps and new inputs, writes the result and a final screenshot, closes the
owned game/spectator, and exits **0**. A partial action reports `stopped`, with an
interrupted wait distinguished from uncertain input delivery. The receipt
contains `success: true`, `reason: "stop_condition"`, the matching variables,
condition, session/revision IDs and action-sequence count. Its screenshot is an
audit capture taken after detection, not an exact native-event frame. Runtime
evaluation errors exit **1** with `success: false`. Closing before success is
also unsuccessful; an ordinary process exit alone is never proof of reaching a
goal. Stdout remains exclusively MCP; stderr announces the terminal result-file
path. Result/log files are created exclusively and cannot overwrite a prior
trial's outputs.

Keep evaluator files and browser/runtime inspection outside the player's tool
access when requiring evaluator-only information. Separate files and omitted MCP
fields do not isolate a player with unrestricted filesystem/browser access.

### Game variables

A runtime that publishes game variables exposes them as indexed banks.
Queen provides `game_state` with 211 signed integer slots. For example, slot
93 tracks the hotel escape sequence. To stop on a write from `0` to `1`,
pass this condition to the launcher:

```sh
--stop-condition '{"all":[{"variable":"engine_id","equals":"queen"},{"variable":"game_state.93","from":0,"equals":1}]}'
```

Omit `from` to match the value in a baseline or after any write. A baseline
after loading a save can satisfy equality. A predicate with `from` requires
an actual write. Variable predicates can be combined with other indexed
predicates and `engine_id`. Each condition supports one `from` predicate.
Room conditions are evaluated separately because room readiness describes
the last presented frame. Invalid slots, incompatible value types, and
runtimes without the game variable feed fail explicitly.

The evaluator log contains periodic `variables` rows with a `game_variables`
snapshot, plus every `game_variables_baseline` and `game_variables_change`
record in native order. A baseline replaces all banks. Each change includes
its bank, zero-based index, previous and new values, and native time. Query
the latest sampled value or watch the write history from outside the game:

```sh
jq -r 'select(.event == "variables" and .game_variables) | .game_variables.game_state[93]' variables.jsonl | tail -1
tail -F variables.jsonl | jq --unbuffered -c 'select(.event == "game_variables_change" and .bank == "game_state" and .index == 93)'
```

Variable success receipts include the matching native record as
`game_variable_match` and the bank snapshot at that point as `game_variables`.
Use the pinned runtime and game data when interpreting slot numbers.

### Story progress

On a ScummWEB build that publishes a [map for the game and edition](https://github.com/tsilva/scummweb/blob/main/docs/game-progress.md#map-coverage),
the spectator footer shows the story percentage and the next milestone.
At 100%, it shows the percentage alone. The footer is outside game screenshots
and MCP responses. Unmapped games and older builds keep ordinary play and
explicit stop conditions available.

AgentSCUMM records milestone changes and the ScummWEB map revision in the
private session log for [run export](run-archives.md). Evaluator-enabled
launches also include progress in private telemetry and result receipts when
available. A tracking error is recorded separately as `progress-error`.

To watch progress without stopping the game, supply
`--evaluation-log /absolute/run/variables.jsonl` and omit `--stop-condition`.

```sh
tail -F /absolute/run/variables.jsonl | jq --unbuffered -c 'select(.progress) | .progress'
```

## Parallel instances

Give each agent a separate stdio MCP server process. With no storage options, each process automatically gets a unique instance ID and persistent data directory at `.agentscumm/instances/<instance-id>/`. Agents can play the same game or different games concurrently. Each instance has its own browser profile, saves/cache, action queue, request cache, frame history, loopback spectator endpoint and spectator window. Closing or restarting one server leaves the others running. Each process still owns one active game; sharing one MCP connection does not create independent games.

For repeatable experiments and save recovery, configure each agent's MCP launcher with a distinct name:

```sh
# Agent A's MCP server command
node src/index.js --instance agent-a

# Agent B's MCP server command, in a separate client process
node src/index.js --instance agent-b
```

Names contain 1–64 lowercase letters, digits, hyphens or underscores and must start with a letter or digit. Named profiles live at `.agentscumm/instances/<name>/`. Reusing the same name after the server exits preserves its saves/cache; use `session(operation="start", mode="continue")` to request game-supported recovery. A restart without `--instance` or `--data-dir` creates a fresh profile. To reuse an automatically allocated profile, supply its full `status.instanceId` as `--instance` on the next launch.

`status` reports `instanceId`, `instanceLabel`, `serverPid` and the absolute `dataDir`, including before a game starts. Spectator titles show the instance name or a short generated label so simultaneous runs of the same game can be distinguished. Session IDs and action request caches remain specific to their owning server. Use each agent's own MCP connection for every call.

An explicit `--data-dir PATH` continues to use exactly that directory. It is mutually exclusive with `--instance`. For saves created by earlier AgentSCUMM versions in the original shared default directory, launch with `--data-dir .agentscumm`. The local Play Game client already passes a run-specific `--data-dir`; use a different run directory for each concurrent agent. Never copy or delete an in-use profile.

An ownership guard rejects a second game launch against a live instance's data directory and reports an explicit error without closing the owner. The guard lasts until server shutdown, including while its game session is closed. Dead-process ownership records are reclaimed on the next launch. Chromium's own profile locks are also preserved. Use distinct names or directories for concurrent servers, including servers running older versions that lack the guard.

## Tools

| Tool | Behavior |
| --- | --- |
| `list_games` | Lists known catalog slugs, intro-checkpoint support, and this instance’s imported games. Entries with `launchable: false` need a fixed crop. |
| `library` | `operation="import"` accepts 1–256 absolute local ZIP `paths` and starts an asynchronous import job. Poll `operation="status"` for the site’s results. Close an active game before importing. |
| `session` | Operations are `start`, `close`, `open_viewer`, and `close_viewer`. Start accepts `game` (default Flight of the Amazon Queen), `mode` (`skip_intro`, `beginning`, `continue`, default `skip_intro`), and `restart` (default false). It returns a session ID immediately and reuses the active game unless restart is explicit. `close` ends the game and clears history while preserving saves and cache. `close_viewer` requires the active `session_id` after startup and closes the read-only spectator while preserving the game and history. |
| `status` | Checks renderer/runtime health and reports startup phase, connection, capture health, history coverage, spectator and recent actions. Optional `wait_ms` (0–25000, default 0) waits while loading; repeat until ready or an error. |
| `observe` | Optional `source`: `fresh` (default), `frames`, or `history`. Fresh captures now, including loading/error screens. Frames requires `frame_ids` (1–8) and returns exact retained images in requested order. History accepts `seconds` (0.1–300, default 15), `count` (1–8, default 4), and optional `action_id`. Images report their source and original frame metadata; expired images are never replaced by fresh captures. |
| `act` | Executes an ordered sequence and returns one final receipt with per-step outcomes and any explicitly requested screenshots. |

Start with `session(operation="start", game="...")`, poll `status(wait_ms=10000)` while loading, then `observe()` and `act(...)`. Readiness means rendered startup content, which may still be a menu or intro. `status`, `observe`, and `list_games` are read-only tools, separate from session changes and input.

The session and observation tools replace the former names: `launch_game` → `session(operation="start")`, `close_game` → `session(operation="close")`, `open_viewer` → `session(operation="open_viewer")`, `session_status` → `status()`, `wait_for_ready(timeout_ms=N)` → `status(wait_ms=N)`, `get_frame(frame_id=ID)` → `observe(source="frames", frame_ids=[ID])`, and `review(...)` → `observe(source="history", ...)`. The old tool names are not registered. Reconnect clients to a newly started server to discover the current schemas; changing source files does not update an already running server.

Flight of the Amazon Queen is the default game. The MCP provides the interface; Codex handles gameplay through the Play Game skill below.

A game needs a [fixed bounding box](../src/display.js) to launch. Elvira II and Flight of the Amazon Queen are configured. With the default `960 × 720` viewport, their box begins at `(154, 115)` and measures `651 × 490` pixels. Custom viewport sizes use the whole viewport. A missing definition returns `GAME_CROP_REQUIRED` before AgentSCUMM opens or replaces a session.

## Import local games

To bootstrap a Chromium profile at server startup, pass the directory containing
your ScummVM game ZIPs:

```sh
node src/index.js --instance my-library --import-games "/path/to/ScummVM games"
```

The launcher finds up to 256 ZIPs recursively and skips hidden entries and nested
symlinks. Package unpacked games as one ZIP per game. Poll `library(operation="status")`
for the result before starting an imported game. For a one-shot import that prints
a JSON report and exits, add `--import-only`, then launch the same instance again.
The same command also accepts `--data-dir PATH` in place of `--instance`.

Close the active game, then call `library(operation="import", paths=["/absolute/path/Game.zip", ...])`.
Poll `library(operation="status")` until its state is `completed` or `failed`. Completion means the
site finished scanning; inspect `summary` and `items` for individual errors and skipped bundled
copies. The server uses ScummWEB’s normal game ZIP input and its existing scanner and duplicate
handling. It does not extract game files itself or upload ZIPs to a server.

Imported entries appear in `list_games` with `source: "local_import"`. An entry with `launchable: false` needs a fixed AgentSCUMM crop before launch. Start an eligible imported game by passing its returned `slug` to `session`. All games use `/<slug>/play` on the configured base URL. If that route requests local files,
the server reattaches the registered ZIPs through its importer and activates the route’s
Play or Continue action. It never navigates through homepage cards or assumes that a
game title appears in the launch button’s accessible name. Imported games do not
have intro checkpoints, so `skip_intro` launches their beginning.

The original ZIPs remain where they are. Keep the source disk mounted and preserve the paths.
The browser file-input fallback provides tab-local File objects rather than persistent file
handles. The instance records paths and catalog entries in `local-library.json` under its own
data directory, and reattaches those ZIPs through the importer when a direct game route
requests its source files.
It does not copy the whole collection into the instance or inherit another instance’s imports.
Library registration does not verify that each game can reach its ending.

## Play Game skill

Use [$agentscumm_play-game](../.agents/skills/agentscumm_play-game/SKILL.md), displayed as **agentscumm: Play Game**, and name a game:

```text
$agentscumm_play-game Flight of the Amazon Queen
```

New runs use feedback-based pruning by default. To choose another
[catalog strategy](../.agents/skills/agentscumm_play-game/references/strategies.md),
include its ID or name in the request.

```text
$agentscumm_play-game Flight of the Amazon Queen using interaction-ranking
```

The skill records the selected instructions with the run and reuses them on resume
unless you choose a different strategy.

Codex launches the game, opens the spectator, and works toward its ending. It keeps free-form memory, screenshots, references, and other working data in ignored `.internal/<game-id>/<run-id>/`. Discovery, planning, and memory stay outside the MCP. You can ask it to resume a prior run or stop at a chosen milestone; completion requires evidence from the game.

The skill is stored in `.agents/skills/agentscumm_play-game/` and is discoverable in this repository. To expose this repository-owned skill globally on another machine, link that folder into the host's skill directory:

```sh
mkdir -p "$HOME/.codex/skills"
ln -s "$PWD/.agents/skills/agentscumm_play-game" "$HOME/.codex/skills/agentscumm_play-game"
```

Before play, verify `status.scummbench.mediated: true`; missing ScummBench blocks gameplay. The skill requires a clean checkout synchronized with its configured upstream. Compare the full commit with `status.sourceRevision.commit_id` from Codex's built-in MCP connection. The MCP must report that revision and a clean startup. Reload a stale server through Codex, preserving saves and launch settings, and verify the replacement before sending input. See [Codex MCP setup and recovery](../.agents/skills/agentscumm_play-game/references/codex-mcp.md).

Use AgentSCUMM's MCP tools for all gameplay input and observations. Local run records and web research, including walkthroughs, remain available to support gameplay. Resume can restore only progress saved by the game.

The [MCP preflight](../.agents/skills/agentscumm_play-game/SKILL.md#test-mcp-tools-before-play) checks the exposed tools and native controls in a disposable session. A failed check stops play and reports the failing operation. A pass restarts into the requested opening or saved run. A separate test profile protects live sessions with unsaved progress.

Play Game captures startup context and automatically queues verified run export and a local benchmark entry after observing the ending. The finalizer waits for that gameplay turn to finish so its final reply and usage are included. Its `finalization.json` receipt distinguishes queued work, success and archival failures; see [automatic finalization](run-context.md#automatic-finalization).

## Price Thread skill

Use [$agentscumm_price-thread](../.agents/skills/agentscumm_price-thread/SKILL.md), displayed as **agentscumm: Price Thread**, to estimate what a Codex conversation's recorded model-token usage would cost through the OpenAI API:

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

Coordinates use **CSS pixels relative to the returned JPEG**. Its top-left pixel is `(0, 0)`. AgentSCUMM adds the fixed crop offset before sending input. The JPEG carries its own dimensions, and actions require no frame reference. Loading and error observations are full-viewport diagnostics.

Example `act` arguments, using the session ID from `session(operation="start")` and a new client request ID:

```json
{
  "session_id": "QZ7mK3tV2pA",
  "request_id": "F4nRq8D1zUw",
  "actions": [
    { "type": "move", "x": 480, "y": 360 },
    { "type": "click", "x": 480, "y": 360, "button": "left" },
    { "type": "wait", "ms": 800 },
    { "type": "screenshot" }
  ]
}
```

Supported actions:

- `move`: `x`, `y`.
- `click`: `x`, `y`, optional `button` (`left`, `right`, `middle`), `count` (1 or 2), and `hold_ms` (20–1000, default 80).
- `key`: a single gameplay `key`, with optional `hold_ms` (0–5000, default 80). Letters, digits, F1–F12, arrows, Enter, Escape, Space, Tab, Backspace, Delete, Home, End, PageUp, PageDown and individual modifier keys are supported. Browser shortcut combinations are rejected.
- `drag`: `from_x`, `from_y`, `x`, `y`, optional `button`, `steps`, and `hold_ms`
  (0–1000, default 150). The mouse stays down at the source before moving and at the
  target before release for `hold_ms` each. Both holds count toward the 20-second budget.
- `wait`: `ms` (0–10000).
- `pause` and `resume`: explicitly hold or release native game updates. Repeating either is safe.
- `wait_ticks`: `ticks` (1–120). Advance that many engine updates or runtime frame presentations, then hold.
- `save` and `load`: integer `id` (1 through `status.agentControls.maxSaveSlot`), a native save slot in the current run.
- `screenshot`: no additional fields; captures at this exact position in the sequence without issuing input.

Sequences have at most 32 steps, including up to eight screenshots, and a 20-second combined input hold and wait budget. The server validates the whole sequence before input starts. Accepted sequences run serially and stop at the first error. Use an explicit `wait` step for delays within a sequence.

A batch reply contains ordered `steps`. An empty object means the step completed normally. A native control reports its `control` result. A screenshot step reports `frame_id` and `capture_ms` and contributes one JPEG after the receipt. Image order follows screenshot step order. `capture_ms` is a monotonic timestamp for comparing frames from the same running server. The JPEG carries its dimensions.

A failed batch includes an error and labels unfinished steps, such as `skipped` or `delivery-uncertain`. The browser can receive an input without the game performing the intended action. End the batch with a screenshot when you need to check what the game did.

Wait on the original call for its receipt. When the server's 50-second deadline stops the batch, it returns `{"error":"ACTION_TIMEOUT"}`. Allow at least 60 seconds for Codex to receive the reply. If the reply is missing or you need details after a timeout, call `status` with `session_id` and `request_id`. Add `include_images: true` to recover the batch's explicit captures. An `unknown` request has no recorded attempt. An `expired` request was used but its receipt has left the cache.

The server keeps the last 100 receipts within the current game session. Reusing the same request ID with identical actions returns the cached reply and sets `replayed: true`. For a request that never reached the server, resubmission can execute it. Use a new request ID for new input. Session and frame IDs are short random handles. Supply a unique request ID with each batch. A new game launch creates a new session ID and clears its request history.

### Native game time and saves

On a compatible ScummWEB build, `status.nativeControls` reports `available`, the supported `actions`, and `tickSource`. A game that services controls at its update boundary reports `engine`. The shared runtime reports `frame` and steps through ScummVM frame presentations. Both clocks are independent of AgentSCUMM screenshot FPS. When no controls are available, status returns an empty list and a reason, such as `engine_not_advertised`, `runtime_bridge_missing`, or `unscoped_run`. Status rechecks for controls until the engine starts advertising them.

If a batch requests an unavailable native action, `act` rejects the whole batch before sending input. The MCP error includes `code: "NATIVE_CONTROL_UNSUPPORTED"`, the requested `control`, and the `reason`. Ordinary input and screenshots remain available. Each successful native step reports its confirmed `tick` and `paused` state. Native requests share a 20-second batch window. `wait_ticks` can use the remaining window, and other native operations have a 10-second limit.

Use `pause → screenshot` to inspect a stable scene, then `wait_ticks → screenshot` to step forward. In `resume → wait_ticks → pause`, the server hands the tick budget to the engine before releasing it, so the batch advances exactly the requested ticks. Sky and Queen queue the click in `resume → click` before releasing the paused engine. Other engines resume first so they can observe the button press. The ordinary `wait` step measures wall-clock time.

Use supported native controls in the same `act.actions` array as ordinary input. For example, `pause → save → resume → click` checkpoints before a planned click. `pause → load → screenshot` lets you inspect a restored scene before resuming play with `resume`. Check `status.nativeControls.actions` for the current run before choosing a control.

`save` and `load` are advertised separately and use ScummVM's normal save format. The engine can reject either action in the current scene, such as an intro or menu. Queen exposes slots 1–98 and requires a boundary between commands and room transitions. Saving to an existing manual ID replaces that slot in the run. Loading a missing ID fails and stops the batch. A paused load stays paused and allows a new frame before holding. Observe again before choosing actions based on the restored state.

Fresh game launches get a new `runId` by default. Native saves are scoped to that run, game and owned browser profile. To reopen a save after closing a session or restarting the server, use the same browser data directory and supply the original `run_id` on launch. If ScummWEB supports Continue for the edition, `mode="continue"` opens its latest native save. You can also open the game with `mode="beginning"` and use `load` when the engine allows it. The local Play Game client retains its run ID across `--resume`. An unscoped `continue` with no configured run ID remains available for existing browser saves.

### Visual history and spectator

Periodic capture starts only when the game reaches `ready`, then runs independently of MCP calls, targeting 10 frames per second. Loading dashboards and startup-check screenshots never enter the buffer. Explicit `observe` calls can still inspect loading or failed sessions, but those images are unbuffered and unavailable through historical `observe` sources. The spectator begins displaying game frames at readiness. The rolling JPEG frame buffer retains up to 60 seconds, capped at 64 MiB; actual coverage and freshness are reported by `status`. Capture rate depends on rendering and machine load. This is sampled visual history, not a guaranteed recording of every animation frame or an encoded video file. Audio is not captured.

Use `observe(source="history")` with `seconds` and `count`, then `observe(source="frames", frame_ids=[...])` for retained frame IDs. Frames carry timestamps, game/session IDs, and action/step associations. Buffer data stays in memory and expires. Changing or closing the game clears it. Cached action results retain their explicit captures even after rolling-buffer eviction.

The viewer serves screenshots and status from a loopback HTTP endpoint, with a separate local sound toggle. Its window is separate from the hidden game, so viewer clicks, keyboard input, resizing, and closure cannot change game input or viewport dimensions. It displays the same game-only frames returned by MCP observations and action captures, using their screenshot-relative `displayBounds`. The spectator uses the same fixed rectangle as MCP screenshots and inputs. Notifications and game effects leave that rectangle in place. The native window follows the resulting aspect ratio as games, menus, or resolutions change and stays within the visible desktop. ScummWEB's web-player menu, exit, fullscreen, touch-control overlays, and cursor-grab prompt are hidden in the owned session. A compact footer shows **Actions:** followed by the number of accepted action sequences in the current game session. Each new `act` sequence counts once, including sequences that later fail; cached retries and rejected inputs do not increment it. The counter resets when a new game session starts and stays outside game screenshots. A **Sound off / Sound on** button sits at the bottom-right of the footer. Game sound is off by default for each new session; enabling it plays ScummVM audio on the machine running AgentSCUMM. The setting lasts for that game session, including reopening the spectator. Muting changes output gain without pausing the audio clock or issuing game input. Audio is not streamed to remote spectators or saved in frame history. There are no spectator headers or added letterboxing; full status remains available through MCP. Fresh images, explicit action captures, cached replay, and visual history share this crop and coordinate space. On macOS, AppKit keeps the resizable content area at the game's aspect ratio and owns the window title and Dock icon, while a WebKit view with an ephemeral data store displays the game-only spectator page. Other platforms use a separate Chromium app window. The viewer exposes no game-input endpoint; its same-origin sound endpoint only changes local audio output.

### Loading, recovery, and storage

Launch readiness combines ScummWEB's target-matched runtime status, intro-checkpoint status when requested, loading state, a rendered-frame signal, and two visual checks inside the canvas area. A timeout fallback is reported as an error rather than accepted as proof of a rendered scene. A completely black startup scene remains unconfirmed until visible content appears or the launch deadline expires. Readiness can still show a menu or intro; Codex inspects the screenshot to decide the next action.

Startup allows 120 seconds for browser startup, navigation, and runtime loading. During a catalog archive download, each increase in reported bytes renews that budget, up to 15 minutes from launch. Once bytes have arrived, the renderer has the remaining budget to mount the archive and draw the first scene. Stalled downloads and launches with no reported progress still time out. Both limits use a monotonic clock. `status(wait_ms=...)` uses shorter bounded calls so client tool timeouts need not cover the complete download. Failures remain inspectable through `status` and, when possible, `observe`.

After startup, the server checks health before and after input steps, during background capture, and when status is requested. Lost game WebGL contexts, runtime aborts, WebAssembly traps, and runtime error status clear readiness and stop subsequent input. A renderer that does not answer a health probe within three seconds also becomes an explicit error. `runtimeHealth` reports the last check time, failure message, and bounded script diagnostics; session logs retain detected runtime failures. A connected page alone does not establish a healthy game. A responsive health probe also cannot prove that an engine accepted an action or finished restoring a save. Static scenes and ordinary script diagnostics do not by themselves trigger a restart or imply a frozen engine.

Recovery is explicit: inspect the error, then use `session` with `operation: "start"`, `restart: true` and the desired mode, or close and relaunch. The server does not replay failed input or automatically reload a failed game, because that could discard unsaved progress. Startup readiness confirms rendered content; observe again after a save restore to verify the intended scene and inventory.

The owned browser profile, game saves/cache, and JSONL action logs live in an isolated directory under ignored repository-local `.agentscumm/instances/` by default. Use a named instance or explicit data directory to recover saves across server restarts; see [parallel instances](#parallel-instances). Cached macOS app bundles live in repository-local `.agentscumm/macos/`, keyed by their source, app icon, and architecture, and can be reused by multiple spectator processes. The browser uses no personal browsing profile. Do not remove an in-use profile.

An unexpected game-document replacement or browser/page closure blocks further input. Use `session(operation="close")` and relaunch explicitly; `mode: "continue"` requests ScummWEB's supported save recovery. Closing the MCP client terminates its owned game and viewer processes while leaving persistent browser data intact. Browser/process failure can lose progress since the last game save. Host sleep also stops useful capture.

CLI options:

| Option | Default |
| --- | --- |
| `--width`, `--height` | `960`, `720`; integers from 320 to 1920, fixed for the server lifetime |
| `--fps` | `10`; 1–15 |
| `--buffer-seconds` | `60`; 1–300, still subject to the byte limit |
| `--instance` | Omitted: generate an isolated instance; a name selects persistent `.agentscumm/instances/<name>/` storage |
| `--data-dir` | Omitted: use `.agentscumm/instances/<generated-id>/`; an explicit path is used exactly, and cannot be combined with `--instance` |
| `--import-games DIR` | Omitted: skip startup import; otherwise recursively import local game ZIPs into this profile |
| `--import-only` | Omitted: keep the MCP server running; with `--import-games`, report results and exit |
| `--base-url` | `https://scummweb.tsilva.eu`; override for a compatible development instance |
| `--no-viewer` | Disable automatic spectator-window opening; its read-only URL remains available |
