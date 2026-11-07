# Play strategy catalog

Strategies guide how the player observes, ranks interactions, and responds to
feedback. Each applies across rooms and games. Supply puzzle knowledge only
under the selected run's assistance policy.

| Strategy ID | Role | Approach |
| --- | --- | --- |
| [feedback-based-pruning](strategies/feedback-based-pruning.md) | **Default** | Lower unsuccessful candidates and reconsider them when new evidence changes their prerequisites. |
| [interaction-ranking](strategies/interaction-ranking.md) | Comparison baseline | Rank visible interactions by likely progress, reversibility, and information per input. Also called affordance ranking. |
| [prerequisite-testing](strategies/prerequisite-testing.md) | Alternative | Rank testable prerequisites for the next state and check the leading hypothesis cheaply. |

## Select and record

- For a new ordinary run, select the user's requested ID or display name. With
  no explicit choice, select the entry marked **Default**. If a requested name
  is unknown or ambiguous, show the available IDs and resolve the choice before
  play. An experiment uses the strategy snapshot assigned by its coordinator.
- After initializing `run.json` and before gameplay, copy the exact selected
  instructions to `strategy.md` in the ignored run directory. For an experiment,
  reuse its existing frozen copy. Record `play_strategy` in `run.json` with
  `id`, `source_path` when available, run-relative `snapshot_path`, and `sha256`.
  Read the snapshot as the instructions for the run. Declare it in startup
  `prompt_assets` only when it was actually supplied at startup, such as a
  frozen experiment brief.
- On resume, use the recorded snapshot, even if the catalog default changed.
  If the user explicitly changes strategy, keep the previous snapshot and add a
  timestamped selection record with a new snapshot. For a historical run with
  no strategy record, preserve that gap and record only the strategy selected
  for resumed play. Keep the original startup context intact.

## Retain a tested strategy

Use verified comparisons to nominate winners for the catalog. When the user
asks to retain a strategy, copy its tested generic instructions into a separate
Markdown document and add its stable ID to the table. Keep puzzle solutions,
hardcoded actions, and private run evidence out of catalog entries. Record the
evidence and comparison limits in the experiment report. Give substantively
different variants new IDs so prior selections stay reproducible. Change the
default when the user selects one.
