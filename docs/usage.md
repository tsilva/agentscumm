# Usage and controls

Run commands from the repository root. For installation and a first playthrough, start with the [README](../README.md#quick-start).

## Spectator setup

On macOS, install Xcode Command Line Tools if needed (`xcode-select --install`). The first spectator launch compiles a small AppKit/WebKit app and converts `macos/AppIcon.png` into its Dock icon, without additional npm dependencies. Later launches reuse the built app. You can build it ahead of time with `pnpm build:macos`, which prints the `.app` path. Launch it through MCP so it receives the active spectator session; the bundle is not a standalone game launcher. The app also appears in Command-Tab. Its window title includes the app and running game, such as **AgentSCUMM - Flight of the Amazon Queen**. Closing its window or choosing **Quit AgentSCUMM** closes only the spectator; closing the MCP server terminates both the game and spectator. If the native build fails, the game remains available through MCP and `status` reports `viewerError`.

To register without automatically opening a window:

```sh
codex mcp add agentscumm -- node "$PWD/src/index.js" --no-viewer
```

`node src/index.js` runs the stdio server directly. Its stdout is reserved for MCP protocol messages. It does not launch a game until a client calls `session` with `operation: "start"`.

## Tools

| Tool | Behavior |
| --- | --- |
| `list_games` | Lists known catalog slugs and intro-checkpoint support. Future catalog slugs can also be launched. |
| `session` | Required `operation`: `start`, `close`, or `open_viewer`. Start accepts `game` (default Flight of the Amazon Queen), `mode` (`skip_intro`, `beginning`, `continue`; default `skip_intro`), and `restart` (default false). It returns a session ID immediately; starting the active game reuses it unless restart is explicit. Close clears history but preserves saves/cache. The spectator is read-only. |
| `status` | Checks renderer/runtime health and reports startup phase, connection, capture health, history coverage, spectator and recent actions. Optional `wait_ms` (0–25000, default 0) waits while loading; repeat until ready or an error. |
| `observe` | Optional `source`: `fresh` (default), `frames`, or `history`. Fresh captures now, including loading/error screens. Frames requires `frame_ids` (1–8) and returns exact retained images in requested order. History accepts `seconds` (0.1–300, default 15), `count` (1–8, default 4), and optional `action_id`. Images report their source and original frame metadata; expired images are never replaced by fresh captures. |
| `act` | Executes an ordered sequence of inputs and explicit `screenshot` steps, returning each requested capture and a final screenshot with per-step status. |

Start with `session(operation="start", game="...")`, poll `status(wait_ms=10000)` while loading, then `observe()` and `act(...)`. Readiness means rendered startup content, which may still be a menu or intro. `status`, `observe`, and `list_games` are read-only tools, separate from session changes and input.

This five-tool interface replaces the former names: `launch_game` → `session(operation="start")`, `close_game` → `session(operation="close")`, `open_viewer` → `session(operation="open_viewer")`, `session_status` → `status()`, `wait_for_ready(timeout_ms=N)` → `status(wait_ms=N)`, `get_frame(frame_id=ID)` → `observe(source="frames", frame_ids=[ID])`, and `review(...)` → `observe(source="history", ...)`. The old tool names are not registered. Reconnect clients to a newly started server to discover the current schemas; changing source files does not update an already running server.

Flight of the Amazon Queen is the default game. The MCP provides the interface; Codex handles gameplay through the Play Game skill below.

## Play Game skill

Use [$agentscumm_play-game](../.agents/skills/agentscumm_play-game/SKILL.md), displayed as **agentscumm: Play Game**, and name a game:

```text
$agentscumm_play-game Flight of the Amazon Queen
```

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

Coordinates use **CSS pixels relative to the fixed screenshot**, including any black borders. The default viewport is **960 × 720**, with device scale factor 1. ScummVM may change the internal canvas size during startup or menus; screenshots and input retain the fixed viewport coordinate system. Each observation includes `canvasBounds` to locate the canvas and `displayBounds` to locate its rendered game area within that viewport.

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
- `drag`: `from_x`, `from_y`, `x`, `y`, optional `button` and `steps`.
- `wait`: `milliseconds` (0–10000).
- `screenshot`: no additional fields; captures at this exact position in the sequence without issuing input.

Sequences have at most 32 steps, including up to eight explicit screenshots, and a 20-second combined explicit hold/wait budget. The whole sequence is validated before input starts. Sequences run serially and stop at the first error. `after_ms` allows 0–3000 ms before the final screenshot; this is a bounded delay, not a promise that a game animation has ended.

For a hover survey, submit `move → wait → screenshot → move → wait → screenshot` in one `act` call. Explicit captures are returned in sequence order, each with its frame ID, timestamp and zero-based step index. The result summary maps `screenshots[].step` to `frame_id` and reports `selectedFrames` for the number of returned images, including the final frame. Screenshot steps report `captured`; a failed capture reports `capture-failed` and stops subsequent steps. Previously captured images remain in the partial result. The automatic final screenshot is additional to requested captures. Batches do not branch on images or interpret labels; inspect the returned images before deciding a dependent next action.

**Retry the same request ID with identical arguments after an uncertain tool response.** The last 100 request results are cached per game session, and retries return the original result without sending input again. Older used IDs are rejected rather than re-executed. A session accepts up to 10,000 distinct action requests. A new launch creates a new session ID and clears request history; stale session IDs cannot issue input.

Results distinguish captured screenshots, delivered inputs and uncertain delivery and include partial completion if a sequence fails. Delivered means the browser accepted the input operation, not that the game completed the intended action. Inspect the returned screenshot or history before deciding what to do next. MCP cancellation interrupts explicit waits and key holds and stops the remaining sequence; already delivered input is not undone. The same request ID continues to identify that attempt.

### Visual history and spectator

Periodic capture starts only when the game reaches `ready`, then runs independently of MCP calls, targeting 10 frames per second. Loading dashboards and startup-check screenshots never enter the buffer. Explicit `observe` calls can still inspect loading or failed sessions, but those images are unbuffered and unavailable through historical `observe` sources. The spectator begins displaying game frames at readiness. The rolling JPEG frame buffer retains up to 60 seconds, capped at 64 MiB; actual coverage and freshness are reported by `status`. Capture rate depends on rendering and machine load. This is sampled visual history, not a guaranteed recording of every animation frame or an encoded video file. Audio is not captured.

Use `observe(source="history")` with `seconds` and `count`, then `observe(source="frames", frame_ids=[...])` for retained frame IDs. Frames carry timestamps, game/session IDs, and action/step associations. Buffer data stays in memory and expires; game changes and `session(operation="close")` clear it. Action results retain all explicit captures and their final screenshot while cached, even after rolling-buffer eviction, but do not create a permanent video archive.

The viewer serves only screenshots and status from a loopback HTTP endpoint. Its window is separate from the hidden game, so viewer clicks, keyboard input, resizing, and closure cannot change game input or viewport dimensions. It displays the rendered game area, cropping each buffered frame to its recorded `displayBounds` (or full canvas bounds when render geometry is unavailable). The server observes onscreen WebGL draw clipping and viewports to remove padding inside the renderer as well as outside the canvas. It does not trim pixels by brightness, so dark scenes retain their geometry. Full-output menus and overlays expand the crop when they draw outside the game area. The native window follows the resulting aspect ratio as games, menus, or resolutions change and stays within the visible desktop. ScummWEB's web-player menu, exit, fullscreen, and touch-control overlays are hidden in the owned session. There are no spectator headers, status bars, or added letterboxing; status remains available through MCP. This display crop does not change MCP screenshots or input coordinates, which still use the fixed viewport. On macOS, AppKit keeps the resizable content area at the game's aspect ratio and owns the window title and Dock icon, while a WebKit view with an ephemeral data store displays the game-only spectator page. Other platforms use a separate Chromium app window. No control endpoint is exposed through the viewer.

### Loading, recovery, and storage

Launch readiness combines ScummWEB's target-matched runtime status, intro-checkpoint status when requested, loading state, a rendered-frame signal, and two visual checks inside the canvas area. A timeout fallback is reported as an error rather than accepted as proof of a rendered scene. A completely black startup scene remains unconfirmed until visible content appears or the launch deadline expires. Readiness can still show a menu or intro; Codex inspects the screenshot to decide the next action.

The startup deadline is one 120-second monotonic budget covering browser startup, navigation, and runtime loading. Changes to the wall clock do not expire that budget. `status(wait_ms=...)` uses shorter bounded calls so client tool timeouts need not cover the complete download. Failures remain inspectable through `status` and, when possible, `observe`.

After startup, the server checks health before and after input steps, during background capture, and when status is requested. Lost game WebGL contexts, runtime aborts, WebAssembly traps, and runtime error status clear readiness and stop subsequent input. A renderer that does not answer a health probe within three seconds also becomes an explicit error. `runtimeHealth` reports the last check time, failure message, and bounded script diagnostics; session logs retain detected runtime failures. A connected page alone does not establish a healthy game. A responsive health probe also cannot prove that an engine accepted an action or finished restoring a save. Static scenes and ordinary script diagnostics do not by themselves trigger a restart or imply a frozen engine.

Recovery is explicit: inspect the error, then use `session` with `operation: "start"`, `restart: true` and the desired mode, or close and relaunch. The server does not replay failed input or automatically reload a failed game, because that could discard unsaved progress. Startup readiness confirms rendered content; observe again after a save restore to verify the intended scene and inventory.

The owned browser profile, game saves/cache, and JSONL action logs live in ignored `.agentscumm/` next to this repository by default. Cached macOS app bundles live in repository-local `.agentscumm/macos/`, keyed by their source, app icon, and architecture. The browser uses no personal browsing profile. Only one running server should use a given data directory; give parallel servers distinct `--data-dir` values. Do not remove an in-use profile.

An unexpected game-document replacement or browser/page closure blocks further input. Use `session(operation="close")` and relaunch explicitly; `mode: "continue"` requests ScummWEB's supported save recovery. There is no automatic restart, arbitrary rewind, or MCP save/load API in this version. Closing the MCP client terminates its owned game and viewer processes while leaving persistent browser data intact. Browser/process failure can lose progress since the last game save. Host sleep also stops useful capture.

CLI options:

| Option | Default |
| --- | --- |
| `--width`, `--height` | `960`, `720`; integers from 320 to 1920, fixed for the server lifetime |
| `--fps` | `10`; 1–15 |
| `--buffer-seconds` | `60`; 1–300, still subject to the byte limit |
| `--data-dir` | Repository-local `.agentscumm/` |
| `--base-url` | `https://scummweb.tsilva.eu`; override for a compatible development instance |
| `--no-viewer` | Disable automatic spectator-window opening; its read-only URL remains available |
