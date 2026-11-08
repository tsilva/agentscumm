# Run archives

Play Game automatically queues export after observing the ending; see [automatic finalization](run-context.md#automatic-finalization). This guide covers manual exports and archive contents. Run commands from the repository root.

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

The date is the conversation's start date in Europe/Lisbon. The run ID defaults to its conversation UUID; `--run-id` can provide a different identifier. Python 3.9 or newer is required. No Python packages or model API calls are needed. Export makes a bounded public HTTPS pricing lookup; verification works offline.

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

For an unfinished attempt, use `in_progress`, `stopped`, or `blocked` status. The exporter automatically saves the latest verified milestone sample at the rollout cutoff as `verified_progress` when it finds a matching AgentSCUMM session log. This includes the count, total, latest milestone, session ID and sample time. For a run played through the local MCP client, use its working run ID with `--run-id`, or pass the working directory's startup declaration with `--agent-context`, so the exporter can find the local client's session records.

If progress telemetry is unavailable, `--estimated-progress 40` supplies an optional estimate stored as `estimated_progress_percent` (0–100). Completed exports record 100 percent. Register any verified archive with `python3 -B scripts/finalize-run.py register /path/to/archive` to add its status and measurements to the comparison table. Only completed archives receive a checkmark.

`trajectory.jsonl` preserves every available rollout record in order, including messages, tool calls and results, context changes, compactions, and per-response token telemetry. It replaces embedded image bytes with relative screenshot references containing `id`, `path`, and `sha256`. JSON-encoded tool-result strings retain their string wrappers. Available opaque reasoning records are preserved without attempting to decrypt hidden content. This is a conversation archive, not a video recording of every periodic MCP frame.

`skills/` bundles skill directories referenced by `SKILL.md` paths or `$skill-name` invocations in conversation messages and tool arguments. It excludes passive host catalogs, injected instruction blocks, and tool results from discovery. Relative paths follow the recorded conversation/turn working directory. Named skills resolve against referenced paths and conventional local/global skill directories; missing or ambiguous names require a repeatable `--skill /path/to/SKILL.md` argument. This option also adds a skill that automatic discovery missed.

Each bundle includes `SKILL.md` and its local supporting scripts, references, and assets. `skills/index.json` maps original references and trajectory record numbers to archived files, SHA-256 hashes, byte counts, and executable flags. Skill discovery symlinks resolve to their actual directory; links inside that directory are not followed. Caches and dependency installations are excluded. Missing skills and omitted links, special files, or environment/access configuration are reported explicitly and prevent a completed export. Copied files remain private with mode `0600`; original executable flags are recorded in the manifest.

These are **export-time local snapshots**, not verified copies of the versions used during play. The recorded tool outputs remain in the trajectory as evidence of what was read. For future reproducibility, capture skills and effective instructions at run start. Supporting files outside a skill directory, external dependencies, run memory, walkthrough page contents, game data, saves, and runtime binaries are not bundled automatically. The exporter neither executes skills nor downloads their dependencies.

Screenshot IDs derive from their full image bytes. Identical images share a file. `screenshots/index.json` maps each file to its original MCP frame IDs, session IDs, timestamps, and action associations where available. The exporter recovers truncated native tool summaries from full copies using matching lengths and surviving fragments. It can also recover the ordered images from a recognized direct forwarding loop or from a single image-producing AgentSCUMM call whose forwarded count, frame metadata or call summary, and surviving fragments match. For an exec cell that yields, the exporter follows its cell ID to the wait result.

When forwarding screenshots through a JavaScript helper, keep each frame's metadata immediately before its image and preserve the result order. If a tool summary was truncated across several images, the archive retains the recorded fragment and links the full forwarded sequence. Any lost intermediate frame metadata remains unavailable. Missing model-visible images and image gaps of uncertain origin block completed export. When an exec response contains complete image output that excludes a truncated single-frame AgentSCUMM result, the exporter can complete the run and records that gap as `unforwarded_mcp_result` with the surviving fragment.

`run.json` records:

- Game, run/conversation IDs, status, completion evidence, source cutoff, and wall-clock duration including pauses.
- Recorded harness name/version/provider, model IDs, reasoning levels, and token usage grouped by model and effort.
- Git commit IDs and branches: `git.conversation` preserves the checkout recorded when the conversation started; `git.agentscumm_runtime` preserves server-start revisions returned by AgentSCUMM; `git.exporter` separately records the export checkout. Dirty working trees are flagged where recorded. Missing startup provenance remains explicitly unknown; an export-time commit is never substituted for it. A conversation checkout commit does not establish the deployed ScummWEB revision.
- Total input, cached input, uncached input, output, reasoning output, request count, and uncached-input-plus-output tokens. Cached input is already part of input; reasoning is already part of output.
- `api_cost`: API-equivalent model-token price in USD for the recorded model(s), with the complete rate snapshot, official source, retrieval date, usage cutoff, per-model/tier/context-band breakdown, exclusions and assumptions. See the pricing rules below.
- Unique screenshot files, all image references, model-visible image blocks, original frame IDs, recovered truncated references, and unresolved images.
- Skill bundle/file/reference counts, manifest checksum, unresolved references, and explicit export-time provenance.
- `agent_context`: available-at-start tool definitions (including non-MCP tools), separately observed tool use, and checksummed initial prompt assets with trajectory references. Missing initial inventory remains explicitly unknown. Use `--agent-context` for additional startup declarations and injected files; see [the context format and provenance rules](run-context.md).
- MCP call counts and summed MCP call time from the rollout, plus declared walkthrough URLs. The action totals also include requests from the local client's `events.jsonl` when the working run is available. Automatic finalization uses its working directory. For manual exports, pass that run's `--agent-context` or use its working run ID with `--run-id`. The exporter counts local requests logged within the rollout cutoff for sessions identified with the matching game and run profile. `local_mcp_action_sequences` records how many sequences came from this log when it contributed to the totals.
- `actions` counts submitted steps by type, `total_actions` sums those steps, and `total_action_sequences` counts submitted `act` calls. These totals include waits and explicit screenshot steps and measure submissions independently of game acceptance.
- `action_sequences`: sequence count plus `min_length`, `max_length`, `mean_length`, `std_length`, and `median_length`. Length is the number of submitted inputs in each recorded `act` call, including waits, moves, keys, clicks, drags, and explicit screenshot steps. Screenshot steps are observations, not game inputs; `actions.screenshot` separates them when present. Duplicate telemetry is counted once; failed calls and separately requested cached replays are included as submitted calls. Standard deviation uses the whole population (`std_type: "population"`). A run with no sequences has count zero and null length statistics; one sequence has standard deviation zero.
- Source and trajectory checksums, record counts, schema versions, coverage, and limitations. Usage is validated against cumulative counters; missing or inconsistent telemetry fails explicitly.

Verify a portable archive independently:

```sh
python3 -B scripts/export-run.py --verify runs/GAME_ID/YYYYMMDD-RUN_ID
pnpm test:export
```

Verification checks the trajectory checksum and record count, image checksums and references, the skill manifest and file inventory, and initial prompt asset references and copies. It also recomputes recorded token totals and prices from the trajectory and saved rates. Verification works without network access or the original skill or asset files. New archives use schema version 3 in `run.json`; older versions 1 and 2, including those named `stats.json`, remain verifiable with their original coverage limits. Archives contain the complete recorded conversation and remain private local files under ignored `runs/`.

## Token pricing

Every new export, including automatic finalization, saves `tokens.input_tokens`, `tokens.output_tokens`, `tokens.cached_input_tokens` and `api_cost.total_api_price_usd`. The price is a decimal string to preserve precision. Input includes cached input, and output includes reasoning; neither subset is billed twice. Pricing applies to each request separately, including long-context bands and model changes.

The default lookup fetches [current official OpenAI API pricing](https://developers.openai.com/api/docs/pricing) at export time, using exact model IDs and published tier rates. It supports the flagship text-token tables and validates their units and context boundary. Recorded `priority` uses Fast pricing. Missing tiers use an explicitly disclosed Standard scenario; recorded tiers are never overridden. The scenario uses global processing with no regional premium. It represents API-equivalent token usage, not a Codex subscription bill, and excludes other chats/agents, separately billed tools and taxes. Cache-write costs require explicitly verified accounting and retention-specific rates; the automatic lookup leaves such costs unresolved.

For a model or pricing table outside that lookup's coverage, supply `--pricing-rates /path/to/rates.json` to either `export-run.py` or `finalize-run.py queue`. Use the [Price Thread rate-file format](../.agents/skills/agentscumm_price-thread/references/pricing.md), exact observed models, official sources and rates verified on the export date. No model alias or different provider is substituted; the current usage reader supports OpenAI telemetry only.

A lookup failure, unsupported model/tier, stale supplied rates or unresolved cache-write rate saves `api_cost.status: "unavailable"`, a reason and a null price while preserving the archive and measured tokens. A priced archive has `status: "priced"`. Finalization receipts also report pricing status and total, independently of archive completion. Missing pricing must remain visible as unknown, never zero. Existing archives are preserved and are not retroactively repriced. Verification uses the saved rates, so later price changes do not alter an archived result.
