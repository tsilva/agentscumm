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
- `agent_context`: available-at-start tool definitions (including non-MCP tools), separately observed tool use, and checksummed initial prompt assets with trajectory references. Missing initial inventory remains explicitly unknown. Use `--agent-context` for additional startup declarations and injected files; see [the context format and provenance rules](run-context.md).
- MCP call counts, input actions by type, `total_actions` (the sum across action types), `total_action_sequences` (the number of submitted `act` calls), summed MCP call time, and declared walkthrough URLs. These totals follow the submitted-input accounting below, including waits; they do not establish that the game accepted every input.
- `action_sequences`: sequence count plus `min_length`, `max_length`, `mean_length`, `std_length`, and `median_length`. Length is the number of submitted inputs in each recorded `act` call, including waits, moves, keys, clicks, and drags. Duplicate telemetry is counted once; failed calls and separately requested cached replays are included as submitted calls. Standard deviation uses the whole population (`std_type: "population"`). A run with no sequences has count zero and null length statistics; one sequence has standard deviation zero.
- Source and trajectory checksums, record counts, schema versions, coverage, and limitations. Usage is validated against cumulative counters; missing or inconsistent telemetry fails explicitly.

Verify a portable archive independently:

```sh
python3 -B scripts/export-run.py --verify runs/GAME_ID/YYYYMMDD-RUN_ID
pnpm test:export
```

Verification checks the trajectory checksum and record count, image checksums and references, the skill manifest and file inventory, and initial prompt asset references and copies. Verification works without the original skill or asset files. New archives use schema version 2 in `run.json`; older version 1 archives, including those named `stats.json`, remain verifiable with their original coverage limits. Archives contain the complete recorded conversation and remain private local files under ignored `runs/`.
