# Benchmarks

The first recorded full-game completion was a walkthrough-assisted playthrough of **Flight of the Amazon Queen**. It demonstrates that the agent can reach this game's ending through the MCP controls. Individual development runs are not controlled comparisons or estimates of success rate.

## Results

<!-- agentscumm:benchmark:ea00d2f2c82e3fea5333a08d1bc4545f366f058d045dde0bc20aaf785bec2e81 -->
| Run | Dates (Europe/Lisbon) | Game | Recorded model / reasoning | Assistance | Result |
| --- | --- | --- | --- | --- | --- |
| FOTAQ-001 | 2026-11-05–06 | Flight of the Amazon Queen | `gpt-6.1-sol` / high | Public walkthroughs and web search | Completed: rescue epilogue and end credits |

### FOTAQ-001

| Measurement | Recorded result |
| --- | ---: |
| Elapsed conversation time, including pauses | 16 h 51 min 19 s |
| Model requests | 995 |
| Submitted action sequences | 950 |
| Submitted inputs, including waits | 8,530 |
| Uncached input tokens | 2,104,897 |
| Cached input tokens | 232,678,016 |
| Output tokens | 211,916 |
| Total input + output tokens | 234,994,829 |
| Unique archived screenshots | 967 |

Token totals count processing across all requests, including repeated context. Cached tokens are part of input; the 32,089 reasoning tokens are part of output. Submitted inputs include movement, clicks, keys, and waits; they do not count only successful puzzle actions. Elapsed time includes user pauses, interruptions, and resumed play, so it is not a continuous solving-time measurement.

The agent used walkthroughs throughout, including [Walkthrough King](https://www.walkthroughking.com/text/flightoftheamazonqueen.aspx), and consulted other guides. This run does not establish unaided completion. The user requested continuation and recovery from a saved game, and the gameplay skill changed during the run. Gameplay controls and recovery used AgentSCUMM MCP; shell, file editing, and web tooling also appear in the recorded conversation.

The archived trajectory and screenshots passed checksum and reference verification. The trajectory SHA-256 is `ea00d2f2c82e3fea5333a08d1bc4545f366f058d045dde0bc20aaf785bec2e81`. The complete initial tool inventory and AgentSCUMM server startup revision were not recorded. Attached skill copies are export-time snapshots, not verified historical versions. These gaps prevent treating the run as a fully pinned reproducibility baseline.

## Adding results

Play Game automatically registers verified completed archives locally after the gameplay turn finishes. Entries use trajectory-checksum markers to prevent duplicates. To retry registration after an export succeeded, run `python3 -B scripts/finalize-run.py register /path/to/archive`. Existing editorial notes are preserved; the helper does not commit or publish results.
Add each run with its game, dates, exact recorded model and reasoning level, assistance, outcome, completion evidence, and comparable usage/action measurements. Include stopped, blocked, and failed attempts in any planned comparison; select a common stopping rule before comparing models. Report sample counts and variation when repeated trials exist.

Use verified `run.json` metadata as the source. Keep available-at-start tools separate from observed tool use, and record injected instructions, skills, images, reference material, and other assets with their provenance. Unknown inputs stay unknown. Record whether play began from a fresh game or a save, any user intervention, and the runtime/game revisions when available. Keep full private conversations and captures out of this public log; publish concise aggregate results and limitations here.
