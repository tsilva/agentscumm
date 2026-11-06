#!/usr/bin/env python3
"""Read-only Codex usage snapshots and explicit-rate OpenAI cost calculation."""

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse


FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timestamps must include a timezone")
    return result


def usage(raw):
    require(isinstance(raw, dict), "Missing usage breakdown")
    result = {}
    for key in FIELDS:
        value = raw.get(key, 0 if key in (
            "cache_write_input_tokens", "reasoning_output_tokens") else None)
        require(type(value) is int and value >= 0, f"Invalid/missing {key}")
        result[key] = value
    require(result["cached_input_tokens"] <= result["input_tokens"],
            "Cached input exceeds input")
    require(result["reasoning_output_tokens"] <= result["output_tokens"],
            "Reasoning output exceeds output")
    require(result["input_tokens"] + result["output_tokens"] == result["total_tokens"],
            "Input plus output does not equal total")
    return result


def summed(requests):
    return {key: sum(r["usage"][key] for r in requests) for key in FIELDS}


def context(payload):
    return {"model": payload.get("model"),
            "effort": payload.get("effort", payload.get("reasoning_effort")),
            "service_tier": payload.get("service_tier")}


def snapshot(path, thread_id, before=None):
    cutoff = timestamp(before) if before else None
    digest = hashlib.sha256()
    contexts, requests, counters, seen = {}, [], [], {}
    metadata = None
    active_context = {}
    warnings = []
    thread_total = None
    running = dict.fromkeys(FIELDS, 0)
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        remaining = size
        line_number = 0
        while remaining:
            line = stream.readline(remaining)
            if not line:
                raise ValueError("Rollout shrank while reading; retry the snapshot")
            remaining -= len(line)
            digest.update(line)
            line_number += 1
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if remaining == 0 and not line.endswith(b"\n"):
                    warnings.append("Skipped unfinished trailing JSON record")
                    continue
                raise ValueError(f"Malformed rollout record at line {line_number}") from None
            if cutoff and timestamp(record["timestamp"]) > cutoff:
                continue
            kind, payload = record.get("type"), record.get("payload", {})
            if kind == "session_meta":
                require(metadata is None, "Multiple session headers; select one rollout")
                metadata = payload
            elif kind == "turn_context":
                active_context = context(payload)
                if payload.get("turn_id"):
                    contexts[payload["turn_id"]] = active_context
            elif kind == "token_usage_record":
                require(payload.get("thread_id") == thread_id,
                        "Usage contains another thread; resolve fork/agent scope first")
                response = payload.get("response_id")
                require(isinstance(response, str) and response,
                        "Per-response usage lacks a response ID")
                item = {"response_id": response, "turn_id": payload.get("turn_id"),
                        "timestamp": record["timestamp"], "usage": usage(payload.get("usage"))}
                if response in seen:
                    require(seen[response]["usage"] == item["usage"] and
                            seen[response]["turn_id"] == item["turn_id"],
                            "Conflicting duplicate response usage")
                    continue
                require(item["turn_id"] in contexts, "Usage has no matching model turn context")
                item.update(contexts[item["turn_id"]])
                seen[response] = item
                requests.append(item)
                require(payload.get("thread_token_usage") is not None,
                        "Missing cumulative coverage check in per-response telemetry")
                thread_total = usage(payload["thread_token_usage"])
                running = {key: running[key] + item["usage"][key] for key in FIELDS}
                require(running == thread_total,
                        "Per-response usage has a missing prefix, gap, or counter reset")
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                if info:
                    counters.append((record["timestamp"], info, dict(active_context)))
    require(metadata is not None and metadata.get("id") == thread_id,
            "Session header does not match the selected thread ID")
    require(metadata.get("model_provider") == "openai",
            "Missing or unsupported provider; only verified OpenAI telemetry is supported")
    require(not metadata.get("forked_from_id"),
            "Forked rollout: resolve inherited-history scope before pricing")
    if requests:
        mode = "per_response"
        # token_count can legitimately lag the durable per-response record, but not lead it.
        for _, info, _ in counters:
            require(usage(info.get("total_token_usage"))["total_tokens"] <=
                    thread_total["total_tokens"], "Usage counter exceeds per-response coverage")
    else:
        mode = "cumulative_deltas"
        previous = dict.fromkeys(FIELDS, 0)
        for when, info, observed in counters:
            total = usage(info.get("total_token_usage"))
            if total == previous:
                continue
            delta = {key: total[key] - previous[key] for key in FIELDS}
            require(all(value >= 0 for value in delta.values()), "Cumulative counter reset")
            require(delta == usage(info.get("last_token_usage")),
                    "Legacy usage has a missing prefix or gap; request pricing is unresolved")
            requests.append({"timestamp": when, "usage": usage(delta), **observed})
            previous = total
    require(requests, "No recorded token usage within the selected cutoff")
    require(all(isinstance(r.get("model"), str) and r["model"] for r in requests),
            "Missing recorded model identity")
    totals = summed(requests)
    groups = defaultdict(list)
    for item in requests:
        groups[(item["model"], item.get("effort"), item.get("service_tier"))].append(item)
    return {"schema_version": 1, "thread_id": thread_id, "provider": "openai",
            "source": {"path": str(Path(path).resolve()), "bytes": size,
                       "sha256": digest.hexdigest()},
            "captured_at": datetime.now(timezone.utc).isoformat(), "requested_cutoff": before,
            "usage_as_of": max(requests, key=lambda r: timestamp(r["timestamp"]))["timestamp"],
            "coverage": mode,
            "scope": "One thread rollout; excludes other agents/threads and tool charges",
            "warnings": warnings, "totals": totals,
            "groups": [{"model": model, "effort": effort, "service_tier": tier,
                        "request_count": len(items), "usage": summed(items)}
                       for (model, effort, tier), items in groups.items()],
            "requests": requests}


def rate(value):
    require(not isinstance(value, bool), "Invalid rate")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid rate") from None
    require(result.is_finite() and result >= 0, "Rates must be finite and nonnegative")
    return result


def select_band(config, input_tokens):
    bands = config.get("bands")
    require(isinstance(bands, list) and bands, "Missing pricing bands")
    previous = -1
    selected = None
    for index, band in enumerate(bands):
        maximum = band.get("max_input_tokens")
        if maximum is None:
            require(index == len(bands) - 1, "Unbounded band must be last")
        else:
            require(type(maximum) is int and maximum > previous,
                    "Context bands must have increasing integer ceilings")
            previous = maximum
        if selected is None and (maximum is None or input_tokens <= maximum):
            selected = (index, band)
    require(selected is not None, "No pricing band covers this request")
    return selected


def price(data, rates, assume_tier=None):
    require(data.get("schema_version") == 1 and data.get("provider") == "openai",
            "Unsupported usage snapshot")
    date.fromisoformat(rates["retrieved_on"])
    require(rates.get("sources"), "Official pricing sources are required")
    for source in rates["sources"]:
        url = urlparse(source)
        require(url.scheme == "https" and url.hostname in (
            "developers.openai.com", "platform.openai.com", "learn.chatgpt.com", "openai.com"),
            "Pricing sources must be official OpenAI HTTPS URLs")
    require(data.get("requests"), "Snapshot has no requests")
    require(summed(data["requests"]) == usage(data.get("totals")),
            "Snapshot totals do not match its requests")
    groups = {}
    missing_tier = False
    for item in data["requests"]:
        tokens = usage(item.get("usage"))
        model = item.get("model")
        observed_tier = item.get("service_tier")
        tier = observed_tier or assume_tier
        require(tier, "Processing tier unavailable; supply a disclosed --assume-tier scenario")
        # API responses use priority for Fast; ambiguous auto/default must be resolved separately.
        require(tier not in ("auto", "default"), "Processing tier is unresolved (auto/default)")
        missing_tier |= not bool(observed_tier)
        config = rates.get("models", {}).get(model, {}).get(tier)
        require(config is not None, f"No verified rates for model {model}, tier {tier}")
        band_index, band = select_band(config, tokens["input_tokens"])
        writes = tokens["cache_write_input_tokens"]
        if writes:
            require(config.get("cache_write_accounting") == "included_in_input" and
                    "cache_write_input" in band,
                    "Nonzero cache writes require verified accounting and retention-specific rate")
        counts = {"input": tokens["input_tokens"] - tokens["cached_input_tokens"] - writes,
                  "cached_input": tokens["cached_input_tokens"],
                  "cache_write_input": writes, "output": tokens["output_tokens"]}
        require(counts["input"] >= 0, "Input subsets exceed total input")
        key = (model, item.get("effort"), tier, band_index)
        if key not in groups:
            groups[key] = {"model": model, "effort": item.get("effort"), "tier": tier,
                           "max_input_tokens": band.get("max_input_tokens"),
                           "request_count": 0, "tokens": dict.fromkeys(counts, 0),
                           "rates_usd_per_million": {}, "costs_usd": {}}
        group = groups[key]
        group["request_count"] += 1
        for category, count in counts.items():
            if category == "cache_write_input" and not count and category not in band:
                continue
            require(category in band, f"Missing {category} rate")
            amount = rate(band[category])
            group["tokens"][category] += count
            group["rates_usd_per_million"][category] = str(amount)
            group["costs_usd"][category] = str(
                Decimal(group["costs_usd"].get(category, "0")) + count * amount / 1_000_000)
    total = sum((Decimal(cost) for group in groups.values()
                 for cost in group["costs_usd"].values()), Decimal(0))
    return {"thread_id": data["thread_id"], "usage_as_of": data["usage_as_of"],
            "currency": "USD", "total_model_token_cost": str(total),
            "pricing_retrieved_on": rates["retrieved_on"], "sources": rates["sources"],
            "assumptions": ([f"Missing processing tiers priced as {assume_tier}"]
                            if missing_tier else []),
            "exclusions": ["Other threads/agents", "Separately billed tools", "Taxes"],
            "warnings": data.get("warnings", []), "groups": list(groups.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("snapshot", help="Snapshot one exact Codex rollout")
    collect.add_argument("rollout", type=Path)
    collect.add_argument("--thread-id", required=True)
    collect.add_argument("--before", help="Inclusive ISO timestamp with timezone")
    calculate = commands.add_parser("price", help="Price a snapshot with verified rates")
    calculate.add_argument("snapshot", type=Path)
    calculate.add_argument("--rates", required=True, type=Path)
    calculate.add_argument("--assume-tier", help="Disclosed scenario for missing tier only")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(args.rollout, args.thread_id, args.before)
        else:
            result = price(json.loads(args.snapshot.read_text()),
                           json.loads(args.rates.read_text()), args.assume_tier)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
