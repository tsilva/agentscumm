# Benchmarks

| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Input tokens | Output tokens | Cached input tokens (% of input) | Total API cost (USD) | Total actions | Action sequences |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ✅ | Flight of the Amazon Queen | 2026-11-06 | gpt-6.1-sol / high | Yes | 16:51:19 | 234,994,829 | 234,782,913 | 211,916 | 232,678,016 (99.10%) | $46.7736466 (Standard assumed) · rates 2026-11-06 | 8,530 | 950 <!-- agentscumm:benchmark:ea00d2f2c82e3fea5333a08d1bc4545f366f058d045dde0bc20aaf785bec2e81 --> |
| ✅ | Indiana Jones and the Last Crusade | 2026-11-07 | gpt-6.1-sol / high | Yes | 07:28:13 | 191,022,061 | 190,934,079 | 87,982 | 189,691,776 (99.35%) | $30.7119586 (Standard assumed) · rates 2026-11-07 | 2,769 | 547 <!-- agentscumm:benchmark:0004e66106b7c5f0aacb7614317897c1c8b529b183cdca196c41e5196f7d56b3 --> |

✅ marks a completed archive. Unfinished runs show the verified milestone count and latest milestone when available. Otherwise they show a declared estimate or Unknown. Time is elapsed conversation time (HH:MM:SS), including pauses and resumes, or elapsed so far for unfinished runs. Tokens include cached/repeated input plus output; actions count submitted inputs, including waits. Missing walkthrough declarations mean unspecified assistance, not unaided play.

One development run; the initial tool inventory and runtime revision are unknown, and skill copies are later snapshots.

[Archive details](docs/run-archives.md) · [Provenance and automatic registration](docs/run-context.md)

Cache percentage is cached input divided by total input, rounded to two decimal places. It is N/A when total input is zero. Output includes reasoning tokens.

Total API cost estimates what the recorded model tokens would cost through each model's default API provider. Each request uses the archived official rates for its exact model, processing tier and context length. Multiply uncached input, cached input and output by their respective USD rates per million tokens, divide by one million, and sum across requests. Apply separate cache-write rates when applicable. The rates date is when those prices were verified for the archive and can differ from the run date. Standard assumed marks missing processing tiers. Unknown means no verified archived cost. The estimate excludes separately billed tools and taxes. [Pricing details](docs/run-archives.md#token-pricing)
