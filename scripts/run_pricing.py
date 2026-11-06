"""Public, bounded pricing lookup for run archives; no credentials or model calls."""

from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.request


SOURCE = "https://developers.openai.com/api/docs/pricing"
MAX_BYTES = 2_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_rates(document, models, retrieved_on):
    """Read only the documented flagship text-token tables, by exact model/tier."""
    require("Prices per 1M tokens." in document, "Pricing units unavailable")
    require("Short context: ≤272K input tokens. Long context: >272K input tokens." in document,
            "Pricing context boundary changed; review official rates")
    document = document.split("Cyber models", 1)[0]
    wanted = set(models)
    result, tier, headers = {}, None, None
    tiers = {"Standard": "standard", "Fast": "priority", "Batch": "batch",
             "Flex": "flex", "Ultrafast": "ultrafast"}
    for line in document.splitlines():
        heading = re.fullmatch(r"### (Standard|Fast|Batch|Ultrafast) pricing data|#### (Batch|Flex)", line.strip())
        if heading:
            tier = tiers[heading[1] or heading[2]]
            headers = None
            continue
        if not line.startswith("|"):
            headers = None
            continue
        cells = [cell.strip() for cell in line.strip().split("|")[1:-1]]
        if cells[0] == "Model":
            headers = cells
            continue
        if not headers or not tier or cells[0] not in wanted:
            continue
        require(len(cells) == len(headers), "Pricing table shape changed")
        row = dict(zip(headers, cells))
        bands = []
        prefixes = (("Short context ", 272000), ("Long context ", None)) if "Short context input" in row else (("", None),)
        for prefix, ceiling in prefixes:
            band = {"max_input_tokens": ceiling}
            for category, column in (("input", "input"), ("cached_input", "cached input"), ("output", "output")):
                label = prefix + column if prefix else column.capitalize()
                value = row.get(label)
                require(isinstance(value, str) and re.fullmatch(r"\$[0-9]+(?:\.[0-9]+)?", value),
                        f"Missing or unsupported {category} rate for {cells[0]}, {tier}")
                band[category] = value[1:]
            bands.append(band)
        config = {"bands": bands}
        previous = result.setdefault(cells[0], {}).get(tier)
        require(previous is None or previous == config, "Conflicting pricing rows")
        result[cells[0]][tier] = config
    require(wanted <= result.keys(), "Official pricing does not cover every exact recorded model")
    return {"retrieved_on": retrieved_on, "sources": [SOURCE], "models": result}


def fetch_rates(models):
    request = urllib.request.Request(SOURCE + ".md", headers={"Accept": "text/markdown"})
    with urllib.request.urlopen(request, timeout=15) as response:
        require(response.geturl() in (SOURCE, SOURCE + ".md"), "Unexpected pricing redirect")
        data = response.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "Pricing document exceeds size limit")
    now = datetime.now(timezone.utc)
    rates = parse_rates(data.decode("utf-8"), models, now.date().isoformat())
    rates["retrieved_at"] = now.isoformat()
    rates["source_sha256"] = hashlib.sha256(data).hexdigest()
    return rates


def calculate(usage_helper, snapshot, rates):
    report = usage_helper.price(snapshot, rates, assume_tier="standard")
    # Source-read warnings live in run.json; an unfinished trailing source record
    # is deliberately absent from the normalized archived trajectory.
    report.pop("warnings", None)
    report["assumptions"].append("Global processing; no regional premium")
    return {"status": "priced", "provider": snapshot["provider"],
            "basis": "api_equivalent_model_tokens", "total_api_price_usd": report["total_model_token_cost"],
            "rates": rates, **report}


def collect(usage_helper, snapshot, rates_path=None):
    # Lookup failures must preserve the archive and measured usage, never a zero/partial total.
    try:
        if rates_path is None:
            rates = fetch_rates({item["model"] for item in snapshot["requests"]})
        else:
            with open(rates_path, encoding="utf-8") as stream:
                rates = json.load(stream)
            require(isinstance(rates, dict), "Supplied pricing must be a JSON object")
            require(rates.get("retrieved_on") == datetime.now(timezone.utc).date().isoformat(),
                    "Supplied pricing must be verified on the export date")
        return calculate(usage_helper, snapshot, rates)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "unavailable", "provider": snapshot["provider"],
                "basis": "api_equivalent_model_tokens", "currency": "USD",
                "total_api_price_usd": None,
                "reason": str(error), "usage_as_of": snapshot["usage_as_of"]}
