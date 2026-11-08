# Benchmarks

## Flight of the Amazon Queen
<!-- agentscumm:game:flight-of-the-amazon-queen -->

| Status | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Total tokens | Input tokens (% cached) | Output tokens | Date (UTC) | MCP version | MCP tools |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| ✅ | $28.14 | gpt-6.1-sol / high | Yes | 03:09:12 | 8,988 | 686 <!-- agentscumm:benchmark:75e60dd72cd1cb2f1673d8e8f6cf58074d164fe86bbac85f2cbef54ebb92d5e7 --> | 163,716,147 | 163,639,706 (99.23%) | 76,441 | 2026-11-08 <!-- agentscumm:recorded-through:2026-11-08T19:17:24.457000+00:00 --> | 0.1.0 @ f7ba88b | Chat startup (partial) · act, library, list_games, observe, session, status |
| ✅ | $46.77 | gpt-6.1-sol / high | Yes | 16:51:19 | 8,530 | 950 <!-- agentscumm:benchmark:ea00d2f2c82e3fea5333a08d1bc4545f366f058d045dde0bc20aaf785bec2e81 --> | 234,994,829 | 234,782,913 (99.10%) | 211,916 | 2026-11-06 <!-- agentscumm:recorded-through:2026-11-06T08:34:32.955000+00:00 --> | Unknown | Startup unknown. Observed act, launch_game, list_games, observe, review, session_status, wait_for_ready |

## Indiana Jones and the Last Crusade
<!-- agentscumm:game:indiana-jones-and-the-last-crusade -->

| Status | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Total tokens | Input tokens (% cached) | Output tokens | Date (UTC) | MCP version | MCP tools |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| ✅ | $30.71 | gpt-6.1-sol / high | Yes | 07:28:13 | 2,769 | 547 <!-- agentscumm:benchmark:0004e66106b7c5f0aacb7614317897c1c8b529b183cdca196c41e5196f7d56b3 --> | 191,022,061 | 190,934,079 (99.35%) | 87,982 | 2026-11-07 <!-- agentscumm:recorded-through:2026-11-07T18:16:40.877000+00:00 --> | 0.1.0 @ e6e2194, 0.1.0 @ 2e0e952 | Server · act, library, list_games, observe, session, status |

## Beneath a Steel Sky
<!-- agentscumm:game:beneath-a-steel-sky -->

| Status | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Total tokens | Input tokens (% cached) | Output tokens | Date (UTC) | MCP version | MCP tools |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| ✅ | $26.61 | gpt-6.1-sol / high | Yes | 01:48:58 | 3,463 | 625 <!-- agentscumm:benchmark:34aa50b805ea0f59c91846fe484a2888454a1f18e40274f7a7cc1528b10016e1 --> | 159,426,734 | 159,365,731 (99.44%) | 61,003 | 2026-11-08 <!-- agentscumm:recorded-through:2026-11-08T11:42:45.481000+00:00 --> | 0.1.0 @ 9e874c6 | Chat startup (partial) · act, library, list_games, observe, session, status |

✅ marks a completed archive. Unfinished runs show the verified milestone count and latest milestone when available. Otherwise they show a declared estimate or Unknown. Time is elapsed conversation time (HH:MM:SS), including pauses and resumes, or elapsed so far for unfinished runs. Tokens include cached/repeated input plus output; actions count submitted inputs, including waits. Missing walkthrough declarations mean unspecified assistance, not unaided play.

The November 6 Queen result is from a development run with an unknown MCP version and startup inventory. Its archived skill copies are later snapshots.

MCP versions pair the server version with its startup Git revision where known. For historical runs with a recorded clean revision, the version comes from that revision's MCP handshake code. The Indiana Jones result uses local server revisions and inventories recorded in the playthrough. Server inventories, partial chat startup inventories, and observed calls are labeled separately.

[Archive details](docs/run-archives.md) · [Provenance and automatic registration](docs/run-context.md)

Cache percentage is cached input divided by total input, rounded to two decimal places. It is N/A when total input is zero. Output includes reasoning tokens.

Total API cost estimates what the recorded model tokens would cost through each model's default API provider. Each request uses the archived official rates for its exact model, processing tier and context length. Multiply uncached input, cached input and output by their respective USD rates per million tokens, divide by one million, and sum across requests. Apply separate cache-write rates when applicable. The archive records the rate verification date and any pricing tier assumptions. Unknown means no verified archived cost. The estimate excludes separately billed tools and taxes. [Pricing details](docs/run-archives.md#token-pricing)
