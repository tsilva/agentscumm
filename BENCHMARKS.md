# Benchmarks

| Status | Game | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Total tokens | Input tokens (% cached) | Output tokens | Date (UTC) |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ✅ | Flight of the Amazon Queen | $46.77 | gpt-6.1-sol / high | Yes | 16:51:19 | 8,530 | 950 <!-- agentscumm:benchmark:ea00d2f2c82e3fea5333a08d1bc4545f366f058d045dde0bc20aaf785bec2e81 --> | 234,994,829 | 234,782,913 (99.10%) | 211,916 | 2026-11-06 |
| ✅ | Indiana Jones and the Last Crusade | $30.71 | gpt-6.1-sol / high | Yes | 07:28:13 | 2,769 | 547 <!-- agentscumm:benchmark:0004e66106b7c5f0aacb7614317897c1c8b529b183cdca196c41e5196f7d56b3 --> | 191,022,061 | 190,934,079 (99.35%) | 87,982 | 2026-11-07 |

✅ marks a completed archive. Unfinished runs show the verified milestone count and latest milestone when available. Otherwise they show a declared estimate or Unknown. Time is elapsed conversation time (HH:MM:SS), including pauses and resumes, or elapsed so far for unfinished runs. Tokens include cached/repeated input plus output; actions count submitted inputs, including waits. Missing walkthrough declarations mean unspecified assistance, not unaided play.

One development run; the initial tool inventory and runtime revision are unknown, and skill copies are later snapshots.

[Archive details](docs/run-archives.md) · [Provenance and automatic registration](docs/run-context.md)

Cache percentage is cached input divided by total input, rounded to two decimal places. It is N/A when total input is zero. Output includes reasoning tokens.

Total API cost estimates what the recorded model tokens would cost through each model's default API provider. Each request uses the archived official rates for its exact model, processing tier and context length. Multiply uncached input, cached input and output by their respective USD rates per million tokens, divide by one million, and sum across requests. Apply separate cache-write rates when applicable. The archive records the rate verification date and any pricing tier assumptions. Unknown means no verified archived cost. The estimate excludes separately billed tools and taxes. [Pricing details](docs/run-archives.md#token-pricing)
