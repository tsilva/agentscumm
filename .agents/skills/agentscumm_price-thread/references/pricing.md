# Pricing inputs and accounting

The helper uses Python's standard library and makes no network requests. Supply freshly verified pricing as JSON data. Do not run a model or change billing to estimate cost.

## Rate-file format

The root object has:

- `retrieved_on`: ISO date when the official sources were fetched.
- `sources`: nonempty array of supporting official HTTPS OpenAI documentation URLs.
- `models`: object keyed by exact model ID. Each value is an object keyed by processing tier (`standard`, `priority`, `flex`, etc.), matching telemetry or an explicit scenario.

Each model/tier object contains `bands`, an ordered array of pricing bands. A band has:

- `max_input_tokens`: inclusive request-input ceiling, or `null` for the final unbounded band. A single unbounded band handles models without a context surcharge.
- `input`, `cached_input`, `output`: USD per million tokens, preferably JSON strings to retain exact decimals.
- `cache_write_input`: USD per million cache-write tokens when applicable. Omit it only when no writes occurred.

When any writes occurred, the model/tier object must also have `cache_write_accounting` set to `included_in_input`, after verifying the telemetry/provider convention. Resolve the cache-retention duration from evidence and encode its applicable rate; do not assume 30 minutes or 12 hours. If that convention or duration cannot be established, the cost is unresolved.

Use exact tier rates in each band rather than multiplying the final total. Model-specific differences, regional premiums, Batch/Flex discounts, and long-context surcharges must be reflected in the selected rates and explained. Do not apply a long-context surcharge to the conversation-wide token sum: choose the band using each request's recorded `input_tokens`.

For an illustrative **synthetic** model with no context bands or cache writes, the structure is:

```json
{
  "retrieved_on": "2026-11-05",
  "sources": ["https://developers.openai.com/api/docs/pricing"],
  "models": {
    "synthetic-model": {
      "standard": {
        "bands": [
          {"max_input_tokens": null, "input": "2", "cached_input": "0.1", "output": "10"}
        ]
      }
    }
  }
}
```

The synthetic rates are not an official quote. Build the real file from the fetched documentation for the observed models. Do not copy example values as current pricing.

## Interpreting the snapshot

`input_tokens` includes `cached_input_tokens`. For supported write accounting, subtract writes too: uncached input = input − cached input − cache-write input. `output_tokens` already includes `reasoning_output_tokens`; do not price reasoning a second time. The helper verifies that input + output = total and that subsets are valid.

The report groups by model, effort, selected tier, and context band. A null tier is unavailable, not Standard. Use `--assume-tier` only for missing tier metadata and report that assumption; it does not override a recorded tier.

The snapshot covers one exact thread ID in one rollout, through its captured byte boundary and optional timestamp cutoff. It records a source hash, coverage mode, warnings, and usage cutoff. It excludes other threads, delegated sessions, and external tool-provider usage. A trailing unfinished JSON record can be skipped with a warning; malformed complete records fail. Unsupported non-OpenAI providers fail. Older logs without per-response records are accepted only when cumulative deltas match the reported request usage, permitting reliable model attribution and context-band selection.

To verify the helper with synthetic records:

```sh
python3 -B -m unittest discover -s SKILL_DIR/tests -v
```
