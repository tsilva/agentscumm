# Usage and controls

Run commands from the repository root. For installation and a first playthrough, start with the [README](../README.md#quick-start).

## Spectator setup

On macOS, install Xcode Command Line Tools if needed (`xcode-select --install`). The first spectator launch compiles a small AppKit/WebKit app and converts `macos/AppIcon.png` into its Dock icon, without additional npm dependencies. Later launches reuse the built app. You can build it ahead of time with `pnpm build:macos`, which prints the `.app` path. Launch it through MCP so it receives the active spectator session; the bundle is not a standalone game launcher. The app also appears in Command-Tab. Its window title includes the app, running game and instance, such as **AgentSCUMM - Flight of the Amazon Queen [agent-a]**. Closing its window or choosing **Quit AgentSCUMM** closes only the spectator; closing the MCP server terminates both the game and spectator. If the native build fails, the game remains available through MCP and `status` reports `viewerError`.

To register without automatically opening a window:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js" --no-viewer
```

`node src/index.js` runs the stdio server directly. Its stdout is reserved for MCP protocol messages. It does not launch a game until a client calls `session` with `operation: "start"`.

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
that instance's data directory. The Play Game local MCP client forwards these
same four options and ends when its server stops.

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

Games with a [game internals map](development.md#game-internals-maps) record
milestone changes in their private session log for [benchmark export](run-archives.md).
Evaluator-enabled launches also include `progress` in private telemetry
snapshots and result receipts.

| Game | Verified edition | Milestones |
| --- | --- | ---: |
| Flight of the Amazon Queen | English DOS talkie | 24 |
| Indiana Jones and the Last Crusade | English Amiga | 20 |
| Indiana Jones and the Fate of Atlantis | English DOS CD | 22 |
| DreamWeb | English DOS CD | 15 |
| Beneath a Steel Sky | English DOS CD | 28 |

These maps count milestones up to the furthest confirmed stage, including when
a route bypasses an earlier checkpoint. Fate of Atlantis uses the same scale
for the Team, Wits, and Fists routes. Completion checks distinguish the
winning endings from game-over scenes.

Progress includes the count, total, rounded percentage, and furthest milestone
reached in map order. A restored save rebuilds the score from its current
variables and room. Run archives retain the milestone total used during play.
Each milestone counts once on that save timeline. When the runtime lacks the
required variables, progress is reported as unavailable. This percentage counts
story milestones and does not estimate time remaining.

When progress is available, the spectator footer shows the percentage and the
first milestone still to reach. At 100%, it shows the percentage alone. This
summary appears in the spectator, outside game screenshots and MCP results.

To watch progress without automatically stopping the game, supply
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
| `list_games` | Lists known catalog slugs, intro-checkpoint support, and this instance’s imported games. Future catalog slugs can also be launched. |
| `library` | `operation="import"` accepts 1–256 absolute local ZIP `paths` and starts an asynchronous import job. Poll `operation="status"` for the site’s results. Close an active game before importing. |
| `session` | Required `operation`: `start`, `close`, or `open_viewer`. Start accepts `game` (default Flight of the Amazon Queen), `mode` (`skip_intro`, `beginning`, `continue`; default `skip_intro`), and `restart` (default false). It returns a session ID immediately; starting the active game reuses it unless restart is explicit. Close clears history but preserves saves/cache. The spectator is read-only. |
| `status` | Checks renderer/runtime health and reports startup phase, connection, capture health, history coverage, spectator and recent actions. Optional `wait_ms` (0–25000, default 0) waits while loading; repeat until ready or an error. |
| `observe` | Optional `source`: `fresh` (default), `frames`, or `history`. Fresh captures now, including loading/error screens. Frames requires `frame_ids` (1–8) and returns exact retained images in requested order. History accepts `seconds` (0.1–300, default 15), `count` (1–8, default 4), and optional `action_id`. Images report their source and original frame metadata; expired images are never replaced by fresh captures. |
| `act` | Executes an ordered sequence of inputs and explicit `screenshot` steps, returning each requested capture and a final screenshot with per-step status. |

Start with `session(operation="start", game="...")`, poll `status(wait_ms=10000)` while loading, then `observe()` and `act(...)`. Readiness means rendered startup content, which may still be a menu or intro. `status`, `observe`, and `list_games` are read-only tools, separate from session changes and input.

The session and observation tools replace the former names: `launch_game` → `session(operation="start")`, `close_game` → `session(operation="close")`, `open_viewer` → `session(operation="open_viewer")`, `session_status` → `status()`, `wait_for_ready(timeout_ms=N)` → `status(wait_ms=N)`, `get_frame(frame_id=ID)` → `observe(source="frames", frame_ids=[ID])`, and `review(...)` → `observe(source="history", ...)`. The old tool names are not registered. Reconnect clients to a newly started server to discover the current schemas; changing source files does not update an already running server.

Flight of the Amazon Queen is the default game. The MCP provides the interface; Codex handles gameplay through the Play Game skill below.

## Import local games

Close the active game, then call `library(operation="import", paths=["/absolute/path/Game.zip", ...])`.
Poll `library(operation="status")` until its state is `completed` or `failed`. Completion means the
site finished scanning; inspect `summary` and `items` for individual errors and skipped bundled
copies. The server uses ScummWEB’s normal game ZIP input and its existing scanner and duplicate
handling. It does not extract game files itself or upload ZIPs to a server.

Imported entries appear in `list_games` with `source: "local_import"`. Launch their returned `slug`
with `session(operation="start", game="...")`; all games use `/<slug>/play` on the configured
base URL. If that route requests local files,
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

Before play, the skill requires a clean checkout, synchronizes its current branch with the configured upstream, and compares that full commit with `status.sourceRevision.commit_id`. The MCP must report the matching AgentSCUMM revision and a clean startup. The agent handles stale-server restarts and reconnection itself, preserving configuration and saves, then verifies the replacement. If the attached connection is unavailable or cannot reconnect, the skill automatically uses its existing local MCP client without further approval. That client starts the same MCP server, records calls and screenshots, and uses an isolated browser profile under the run directory; it does not inherit another profile's saves. See the [local-client procedure](../.agents/skills/agentscumm_play-game/references/local-mcp-client.md). Preserve unrelated sessions. A newly started process alone does not prove MCP or game readiness. Concrete local-client failures or unverifiable provenance block play; `session(operation="start", restart=true)` restarts only the game. Restarting the MCP ends its active game session, so save progress first. Without a remote or on detached HEAD, the intended latest revision must be established before playing.

The gameplay skill interacts with games only through AgentSCUMM MCP tools, using the attached connection or the maintained local MCP client. Backend selection and implementation belong to the server; process and connection management support revision checks and recovery. A failed desktop connection alone is not a gameplay blocker when the local client works. Local run records and web research, including walkthroughs, remain available to support gameplay. Resume can restore only progress saved by the game.

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

Coordinates use **CSS pixels relative to the returned screenshot**. Ready-game screenshots contain only the rendered game area, without viewport borders or the web cursor-grab prompt. Their dimensions can change with game resolutions and menus. The hidden viewport defaults to **960 × 720**, with device scale factor 1; input coordinates are translated through the crop offset automatically. Each observation reports its actual `width` and `height`, screenshot-relative `canvasBounds` and `displayBounds`, and the viewport-relative `captureBounds`. Status reports current screenshot `dimensions` separately from `viewportDimensions`. Loading/error observations remain full-viewport diagnostics and are not input references. Pass `frame_id` to reject input based on an observation whose crop geometry has changed. A geometry change during a sequence stops subsequent coordinate inputs; observe again before submitting a new sequence.

Example `act` arguments, using IDs returned by `session(operation="start")` and `observe`:

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
- `drag`: `from_x`, `from_y`, `x`, `y`, optional `button`, `steps`, and `hold_ms`
  (0–1000, default 150). The mouse stays down at the source before moving and at the
  target before release for `hold_ms` each. Both holds count toward the 20-second budget.
- `wait`: `milliseconds` (0–10000).
- `pause` and `resume`: explicitly hold or release native game updates. Repeating either is safe.
- `wait_ticks`: `ticks` (1–120). Advance that many native updates and hold at the resulting frame.
- `save` and `load`: integer `id` (1 through `status.agentControls.maxSaveSlot`), a native save slot in the current run.
- `screenshot`: no additional fields; captures at this exact position in the sequence without issuing input.

Sequences have at most 32 steps, including up to eight explicit screenshots, and a 20-second combined explicit hold/wait budget. The whole sequence is validated before input starts. Sequences run serially and stop at the first error. `after_ms` allows 0–3000 ms before the final screenshot; this is a bounded delay, not a promise that a game animation has ended.

For a hover survey, submit `move → wait → screenshot → move → wait → screenshot` in one `act` call. Explicit captures are returned in sequence order, each with its frame ID, timestamp and zero-based step index. The result summary maps `screenshots[].step` to `frame_id` and reports `selectedFrames` for the number of returned images, including the final frame. Screenshot steps report `captured`; a failed capture reports `capture-failed` and stops subsequent steps. Previously captured images remain in the partial result. The automatic final screenshot is additional to requested captures. Batches do not branch on images or interpret labels; inspect the returned images before deciding a dependent next action.

**Retry the same request ID with identical arguments after an uncertain tool response.** The last 100 request results are cached per game session, and retries return the original result without sending input again. Older used IDs are rejected rather than re-executed. A session accepts up to 10,000 distinct action requests. A new launch creates a new session ID and clears request history; stale session IDs cannot issue input.

Results distinguish captured screenshots, delivered inputs and uncertain delivery and include partial completion if a sequence fails. Delivered means the browser accepted the input operation, not that the game completed the intended action. Inspect the returned screenshot or history before deciding what to do next. MCP cancellation interrupts explicit waits and key holds and stops the remaining sequence; already delivered input is not undone. The same request ID continues to identify that attempt.

### Native game time and saves

Native actions currently work in **Beneath a Steel Sky** (the Sky engine) on a compatible ScummWEB build. For any game, `status.nativeControls` reports `available` and the supported `actions`. When unavailable it returns an empty list and a reason, such as `engine_not_advertised`, `runtime_bridge_missing`, or `unscoped_run`. The engine can advertise controls after the first game frame, so status checks again until the controls become available.

If a batch requests an unavailable native action, `act` rejects the whole batch before sending input. The MCP error includes `code: "NATIVE_CONTROL_UNSUPPORTED"`, the requested `control`, and the `reason`. Ordinary input and screenshots remain available. Each successful native step reports its confirmed `tick` and `paused` state. A tick is one update of the game's logic, independent of screenshot FPS or wall-clock time. Native requests have a 10-second per-operation deadline and a shared 20-second batch window.

Use `pause → screenshot` to inspect a stable scene, then `wait_ticks → screenshot` to step forward. In `resume → wait_ticks → pause`, the server hands the tick budget to the engine before releasing it, so the batch advances exactly the requested ticks. An immediately following click in `resume → click` is queued before releasing a paused engine. `wait` and `after_ms` still measure wall-clock time.

`save` and `load` use ScummVM's normal save format and obey the engine's restrictions on the current scene. Saving to an existing ID replaces that slot in the run. Loading a missing ID fails and stops the batch. A paused load stays paused and renders the restored scene. Observe again before choosing actions based on the restored state.

Fresh game launches get a new `runId` by default. Native saves are scoped to that run, game and owned browser profile. To reopen them after closing a session or restarting the server, use the same browser data directory and call `session(operation="start", mode="continue", run_id=...)` with the returned run ID. This loads the latest native save, after which `load` can select a different slot. The local Play Game client retains its run ID across `--resume`. An unscoped `continue` with no configured run ID remains available for existing browser saves.

### Visual history and spectator

Periodic capture starts only when the game reaches `ready`, then runs independently of MCP calls, targeting 10 frames per second. Loading dashboards and startup-check screenshots never enter the buffer. Explicit `observe` calls can still inspect loading or failed sessions, but those images are unbuffered and unavailable through historical `observe` sources. The spectator begins displaying game frames at readiness. The rolling JPEG frame buffer retains up to 60 seconds, capped at 64 MiB; actual coverage and freshness are reported by `status`. Capture rate depends on rendering and machine load. This is sampled visual history, not a guaranteed recording of every animation frame or an encoded video file. Audio is not captured.

Use `observe(source="history")` with `seconds` and `count`, then `observe(source="frames", frame_ids=[...])` for retained frame IDs. Frames carry timestamps, game/session IDs, and action/step associations. Buffer data stays in memory and expires; game changes and `session(operation="close")` clear it. Action results retain all explicit captures and their final screenshot while cached, even after rolling-buffer eviction, but do not create a permanent video archive.

The viewer serves screenshots and status from a loopback HTTP endpoint, with a separate local sound toggle. Its window is separate from the hidden game, so viewer clicks, keyboard input, resizing, and closure cannot change game input or viewport dimensions. It displays the same game-only frames returned by MCP observations and action captures, using their screenshot-relative `displayBounds`. The server observes onscreen WebGL draw clipping and viewports to remove padding inside the renderer as well as outside the canvas. It does not trim pixels by brightness, so dark scenes retain their geometry. Full-output menus and overlays expand the crop when they draw outside the game area. The native window follows the resulting aspect ratio as games, menus, or resolutions change and stays within the visible desktop. ScummWEB's web-player menu, exit, fullscreen, touch-control overlays, and cursor-grab prompt are hidden in the owned session. A compact footer shows **Actions:** followed by the number of accepted action sequences in the current game session. Each new `act` sequence counts once, including sequences that later fail; cached retries and rejected inputs do not increment it. The counter resets when a new game session starts and stays outside game screenshots. A **Sound off / Sound on** button sits at the bottom-right of the footer. Game sound is off by default for each new session; enabling it plays ScummVM audio on the machine running AgentSCUMM. The setting lasts for that game session, including reopening the spectator. Muting changes output gain without pausing the audio clock or issuing game input. Audio is not streamed to remote spectators or saved in frame history. There are no spectator headers or added letterboxing; full status remains available through MCP. Fresh images, explicit action captures, cached replay, and visual history share this crop and coordinate space. On macOS, AppKit keeps the resizable content area at the game's aspect ratio and owns the window title and Dock icon, while a WebKit view with an ephemeral data store displays the game-only spectator page. Other platforms use a separate Chromium app window. The viewer exposes no game-input endpoint; its same-origin sound endpoint only changes local audio output.

### Loading, recovery, and storage

Launch readiness combines ScummWEB's target-matched runtime status, intro-checkpoint status when requested, loading state, a rendered-frame signal, and two visual checks inside the canvas area. A timeout fallback is reported as an error rather than accepted as proof of a rendered scene. A completely black startup scene remains unconfirmed until visible content appears or the launch deadline expires. Readiness can still show a menu or intro; Codex inspects the screenshot to decide the next action.

The startup deadline is one 120-second monotonic budget covering browser startup, navigation, and runtime loading. Changes to the wall clock do not expire that budget. `status(wait_ms=...)` uses shorter bounded calls so client tool timeouts need not cover the complete download. Failures remain inspectable through `status` and, when possible, `observe`.

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
| `--base-url` | `https://scummweb.tsilva.eu`; override for a compatible development instance |
| `--no-viewer` | Disable automatic spectator-window opening; its read-only URL remains available |
