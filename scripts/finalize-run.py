#!/usr/bin/env python3
"""Finish a local run archive after its gameplay turn ends and register its benchmark."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("agentscumm_export", ROOT / "scripts/export-run.py")
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
MLFLOW_SPEC = importlib.util.spec_from_file_location("agentscumm_run_mlflow", ROOT / "scripts/run_mlflow.py")
MLFLOW = importlib.util.module_from_spec(MLFLOW_SPEC)
MLFLOW_SPEC.loader.exec_module(MLFLOW)
require = EXPORT.require
LEGACY_BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Total actions | Action sequences |"
LEGACY_BENCHMARK_SEPARATOR = "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |"
PREVIOUS_BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Input tokens | Output tokens | Cached input tokens | Total API price (USD) | Total actions | Action sequences |"
ANNOTATED_BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Input tokens | Output tokens | Cached input tokens (% of input) | Total API cost (USD) | Total actions | Action sequences |"
PREVIOUS_BENCHMARK_SEPARATOR = "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
SEPARATE_CACHE_BENCHMARK_HEADER = "| Status | Game | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Tokens | Input tokens | Output tokens | Cached input tokens (% of input) | Date (UTC) |"
SEPARATE_CACHE_BENCHMARK_SEPARATOR = "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
INLINE_CACHE_BENCHMARK_HEADER = "| Status | Game | Total API cost (USD) | Model / effort | Walkthrough assistance | Completion time | Total actions | Action sequences | Tokens | Input tokens | Output tokens | Date (UTC) |"
PERCENTAGE_BENCHMARK_HEADER = INLINE_CACHE_BENCHMARK_HEADER.replace("| Input tokens |", "| Input tokens (% cached) |")
FLAT_BENCHMARK_HEADER = PERCENTAGE_BENCHMARK_HEADER.replace("| Tokens |", "| Total tokens |")
FLAT_BENCHMARK_SEPARATOR = "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
BENCHMARK_HEADER = FLAT_BENCHMARK_HEADER.replace(" Game |", "").rstrip() + " MCP version | MCP tools |"
BENCHMARK_SEPARATOR = FLAT_BENCHMARK_SEPARATOR.replace("--- | --- |", "--- |", 1) + " --- | --- |"
GAME_SECTION = re.compile(r"## (.+)\n<!-- agentscumm:game:([a-z0-9-]+) -->\n")
BENCHMARK_TIME = re.compile(r"<!-- agentscumm:recorded-through:([0-9T:.+-]+) -->")
BENCHMARK_NOTES = ("✅ marks a completed archive. Unfinished runs show the verified milestone count and latest milestone when available. "
                   "Otherwise they show a declared estimate or Unknown. "
                   "Time is elapsed conversation time (HH:MM:SS), including pauses and resumes, or elapsed so far for unfinished runs. "
                   "Tokens include cached/repeated input plus output; actions count submitted inputs, including waits. "
                   "Missing walkthrough declarations mean unspecified assistance, not unaided play.\n")
PREVIOUS_BENCHMARK_PRICING_NOTES = (
    "Input tokens include cached input; output includes reasoning. "
    "API price is API-equivalent model-token cost in USD, excluding separately billed tools and taxes. "
    "Standard assumed marks missing recorded processing tiers; Unknown means no verified archived price.\n"
)
PREVIOUS_RATE_NOTE = (
    "The rates date is when those prices were verified for the archive and can differ from the run date. "
    "Standard assumed marks missing processing tiers. "
)
ARCHIVED_RATE_NOTE = "The archive records the rate verification date and any pricing tier assumptions. "
BENCHMARK_PRICING_NOTES = (
    "Cache percentage is cached input divided by total input, rounded to two decimal places. "
    "It is N/A when total input is zero. Output includes reasoning tokens.\n\n"
    "Total API cost estimates what the recorded model tokens would cost through each model's default API provider. "
    "Each request uses the archived official rates for its exact model, processing tier and context length. "
    "Multiply uncached input, cached input and output by their respective USD rates per million tokens, "
    "divide by one million, and sum across requests. Apply separate cache-write rates when applicable. "
    + ARCHIVED_RATE_NOTE + "Unknown means no verified archived cost. "
    "The estimate excludes separately billed tools and taxes. [Pricing details](docs/run-archives.md#token-pricing)\n"
)


def private_directory(repo, directory):
    repo = Path(repo).resolve()
    EXPORT.archive_root(repo)
    require("/.internal/" in (repo / ".gitignore").read_text().splitlines(), "Missing /.internal/ exclusion")
    tracked = subprocess.check_output(["git", "-C", str(repo), "ls-files", "--", ".internal"], text=True)
    require(not tracked, "Private run files must not be tracked")
    subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", ".internal/finalize-check.json"], check=True)
    directory = Path(os.path.abspath(directory))
    require(directory.is_relative_to(repo / ".internal") and directory != repo / ".internal",
            "Finalizer working directory must be inside the owning checkout's .internal/")
    return EXPORT.safe_directory(directory)


def atomic_json(path, value):
    require(not path.is_symlink(), "Linked local state file")
    fd, scratch = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(scratch, path)
    finally:
        if Path(scratch).exists():
            Path(scratch).unlink()


def mlflow_archives(repo):
    root = repo / "runs"
    require(not root.is_symlink(), "Linked run archive root")
    if not root.exists():
        return []
    return sorted(
        archive for game in root.iterdir()
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", game.name) and not game.is_symlink() and game.is_dir()
        for archive in game.iterdir()
        if re.fullmatch(r"[0-9]{8}-[A-Za-z0-9][A-Za-z0-9._-]{0,99}", archive.name) and archive.is_dir()
    )


def mlflow_receipt(directory, config, archive):
    require(not any(part.is_symlink() for part in (archive, *archive.parents)), "Linked run archive path")
    metadata = archive / "run.json"
    if not metadata.exists() and not metadata.is_symlink():
        metadata = archive / "stats.json"
    require(not metadata.is_symlink() and metadata.is_file(), "Missing or linked archive metadata")
    digest = hashlib.sha256(metadata.read_bytes()).hexdigest()
    key = hashlib.sha256(EXPORT.dump([config, digest]).encode()).hexdigest()
    receipt = directory / f"{key}.json"
    require(not receipt.is_symlink(), "Linked MLflow delivery receipt")
    return receipt


def mlflow_queue(repo, *, archive=None, status_only=False):
    config = MLFLOW.load_config(repo)
    require(status_only or config is not None, "Configure .local/mlflow.json or MLFLOW_TRACKING_URI before flushing")
    archives = [archive] if archive is not None else mlflow_archives(repo)
    directory = repo / ".internal/mlflow"
    require(not directory.is_symlink() and not directory.parent.is_symlink(), "Linked MLflow receipt directory")

    def process():
        entries, unavailable = [], False
        for path in archives:
            entry = {"archive": str(path), "status": "pending"}
            entries.append(entry)
            try:
                receipt = mlflow_receipt(directory, config, path)
                if config is not None and receipt.exists():
                    require(receipt.is_file(), "Invalid MLflow delivery receipt")
                    saved = json.loads(receipt.read_text())
                    require(isinstance(saved, dict) and saved.get("status") == "published"
                            and saved.get("run_id") and saved.get("experiment_id"), "Invalid MLflow delivery receipt")
                    entry.update(saved)
                elif not status_only and not unavailable:
                    stats = EXPORT.verify_run(path)
                    require(mlflow_receipt(directory, config, path) == receipt,
                            "Archive metadata changed during verification; retry")
                    published = MLFLOW.publish(config, stats)
                    atomic_json(receipt, published)
                    entry.update(published, sent=True)
            except MLFLOW.TrackingUnavailable as error:
                entry.update(status="failed", error=str(error))
                unavailable = True
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
                entry.update(status="failed", error=str(error))
        return {
            "destination_configured": config is not None,
            "sent": sum(entry.get("sent", False) for entry in entries),
            "published": sum(entry["status"] == "published" for entry in entries),
            "pending": sum(entry["status"] != "published" for entry in entries),
            "failed": sum(entry["status"] == "failed" for entry in entries),
            "archives": entries,
        }

    if status_only or not archives:
        return process()
    private_directory(repo, directory)
    # Hold one checkout-wide lock through receipt creation so both upload commands
    # serialize delivery. The OS releases it if the process is interrupted.
    fd = os.open(directory / "flush.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("An MLflow flush is already in progress") from error
        return process()


def turn_cutoff(rollout, turn_id):
    """Return the byte cutoff for this turn, never a previous or later turn."""
    require(rollout.is_file() and not rollout.is_symlink(), "Rollout must be a regular, unlinked file")
    active, offset = None, 0
    with rollout.open("rb") as stream:
        for line in stream:
            offset += len(line)
            if not line.endswith(b"\n"):
                break
            # Avoid decoding image-heavy records while polling for lifecycle events.
            if b'"event_msg"' not in line or not any(k in line for k in (b'"task_started"', b'"task_complete"', b'"turn_aborted"')):
                continue
            row = json.loads(line)
            payload = row.get("payload", {})
            if row.get("type") != "event_msg":
                continue
            kind = payload.get("type")
            if kind == "task_started":
                active = payload.get("turn_id")
            identity = payload.get("turn_id") or active
            if identity == turn_id:
                require(kind != "turn_aborted", "Gameplay turn was aborted; completed export was not created")
                if kind == "task_complete":
                    return offset
    return None


def snapshot_turn(rollout, destination, cutoff):
    with rollout.open("rb") as source, destination.open("xb") as output:
        os.chmod(destination, 0o600)
        remaining = cutoff
        while remaining:
            data = source.read(min(remaining, 1024 * 1024))
            require(data, "Rollout shrank while capturing completed turn")
            output.write(data)
            remaining -= len(data)


def public_text(value):
    return html.escape(" ".join(str(value).split()), quote=False).replace("|", "\\|").replace("`", "&#96;")


def benchmark_input(total, cached):
    if total is None:
        return "Unknown"
    if total == 0:
        return "0 (N/A)"
    if cached is None:
        return f"{total:,} (Unknown)"
    return f"{total:,} ({cached / total:.2%})"


def benchmark_usage(stats):
    cost = stats.get("api_cost") or {}
    api_price = "Unknown"
    if cost.get("status") == "priced":
        api_price = "$" + format(EXPORT.USAGE.rate(cost["total_api_price_usd"]), ",.2f")
    tokens = stats["tokens"]
    output = tokens.get("output_tokens")
    return [benchmark_input(tokens.get("input_tokens"), tokens.get("cached_input_tokens")),
            f"{output:,}" if output is not None else "Unknown", api_price]


def benchmark_status(stats):
    if stats["status"] == "completed":
        return "✅"
    progress = stats.get("verified_progress")
    if progress:
        label = progress["latest_milestone"] or "No milestones verified"
        return f"{progress['completed']}/{progress['total']} · {label}"
    estimate = stats.get("estimated_progress_percent")
    EXPORT.validate_progress(estimate)
    return f"~{estimate:g}%" if estimate is not None else "Unknown"


def benchmark_actions(stats):
    return [f"{stats.get('total_actions', sum(stats['actions'].values())):,}",
            f"{stats.get('total_action_sequences', stats['action_sequences']['count']):,}"]


def benchmark_marker(stats):
    digest = stats["trajectory"]["sha256"]
    require(re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid trajectory checksum")
    return f"<!-- agentscumm:benchmark:{digest} -->"


def benchmark_revisions(stats):
    git = stats.get("git") or {}
    if "agentscumm_gameplay_runtime" in git:
        return git["agentscumm_gameplay_runtime"]
    if stats.get("local_mcp_action_sequences") and not stats.get("mcp_tool_calls", {}).get("agentscumm.act"):
        return []
    return git.get("agentscumm_runtime") or []


def benchmark_mcp(stats):
    revisions = benchmark_revisions(stats)
    versions, inventories = [], []
    for revision in revisions:
        version = revision.get("mcp_version") or ""
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.+-]+)?", version):
            version = ""
        commit = revision.get("commit_id") or ""
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
            version = " @ ".join(filter(None, (version, commit[:7])))
        version = version or "Unknown"
        if revision.get("dirty") is True:
            version += " (dirty)"
        versions.append(version)
        inventory = revision.get("mcp_tools")
        if isinstance(inventory, list) and inventory and all(
                isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_.-]+", name) for name in inventory):
            inventories.append((version, ", ".join(sorted(inventory))))
    version_text = ", ".join(dict.fromkeys(versions)) or "Unknown"
    if inventories:
        partial = len(inventories) != len(revisions)
        inventories = list(dict.fromkeys(inventories))
        tools = inventories[0][1] if len({names for _, names in inventories}) == 1 else " / ".join(
            f"{version} · {names}" for version, names in inventories)
        if partial:
            tools += " (inventory unknown for some revisions)"
        return version_text, "Server · " + tools

    context_tools = (stats.get("agent_context") or {}).get("tools", {})
    prefix = "mcp.agentscumm."
    startup = sorted({tool["name"][len(prefix):] for tool in context_tools.get("available_at_start", [])
                      if tool["name"].startswith(prefix)})
    if startup:
        coverage = " (partial)" if not context_tools.get("inventory_complete") else ""
        return version_text, f"Chat startup{coverage} · " + ", ".join(startup)
    prefix = "agentscumm."
    observed = sorted(name[len(prefix):] for name in stats.get("mcp_tool_calls", {}) if name.startswith(prefix))
    return version_text, "Startup unknown. Observed " + ", ".join(observed) if observed else "Unknown"


def benchmark_cells(stats):
    completed = EXPORT.USAGE.timestamp(stats["recorded_through"]).astimezone(timezone.utc)
    seconds = round(stats["wall_clock_seconds"])
    input_tokens, output_tokens, api_cost = benchmark_usage(stats)
    cells = [
        benchmark_status(stats), api_cost,
        ", ".join(stats["models"]) + " / " + (", ".join(stats["reasoning_levels"]) or "Unknown"),
        "Yes" if stats["walkthrough_urls"] else "Unspecified",
        f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}",
        *benchmark_actions(stats),
        f"{stats['tokens']['total_tokens']:,}",
        input_tokens, output_tokens, completed.date().isoformat(), *benchmark_mcp(stats),
    ]
    # Keep the deduplication marker inside the row so Markdown renders one table.
    cells = [public_text(value) for value in cells]
    cells[6] += f" {benchmark_marker(stats)}"
    cells[10] += f" <!-- agentscumm:recorded-through:{completed.isoformat()} -->"
    return cells


def benchmark_row(cells):
    return "| " + " | ".join(cells) + " |\n"


def row_cells(line, count):
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
    require(len(cells) == count, "Invalid benchmark row")
    return cells


def upgrade_benchmark_table(text):
    """Upgrade the previous table without inventing missing archive measurements."""
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.strip() in (
        LEGACY_BENCHMARK_HEADER, PREVIOUS_BENCHMARK_HEADER, ANNOTATED_BENCHMARK_HEADER,
        SEPARATE_CACHE_BENCHMARK_HEADER, INLINE_CACHE_BENCHMARK_HEADER, PERCENTAGE_BENCHMARK_HEADER,
        FLAT_BENCHMARK_HEADER)]
    require(len(headers) <= 1, "Multiple benchmark comparison tables; resolve before registration")
    if headers and lines[headers[0]].strip() == PERCENTAGE_BENCHMARK_HEADER:
        lines[headers[0]] = FLAT_BENCHMARK_HEADER + "\n"
    elif headers and lines[headers[0]].strip() != FLAT_BENCHMARK_HEADER:
        position = headers[0]
        header = lines[position].strip()
        legacy = header == LEGACY_BENCHMARK_HEADER
        separator = {
            LEGACY_BENCHMARK_HEADER: LEGACY_BENCHMARK_SEPARATOR,
            SEPARATE_CACHE_BENCHMARK_HEADER: SEPARATE_CACHE_BENCHMARK_SEPARATOR,
            INLINE_CACHE_BENCHMARK_HEADER: FLAT_BENCHMARK_SEPARATOR,
        }.get(header, PREVIOUS_BENCHMARK_SEPARATOR)
        require(position + 1 < len(lines) and lines[position + 1].strip() == separator,
                "Invalid previous benchmark comparison table separator")
        lines[position:position + 2] = [FLAT_BENCHMARK_HEADER + "\n", FLAT_BENCHMARK_SEPARATOR + "\n"]
        position += 2
        while position < len(lines) and lines[position].lstrip().startswith("|"):
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[position].strip())[1:-1]]
            if header == INLINE_CACHE_BENCHMARK_HEADER:
                require(len(cells) == 12, "Invalid previous benchmark row")
                cells[9] = cells[9].replace("% cached)", "%)")
            else:
                require(len(cells) == (9 if legacy else 13), "Invalid previous benchmark row")
                if legacy:
                    cells[7:7] = ["Unknown"] * 4
                if header != SEPARATE_CACHE_BENCHMARK_HEADER:
                    cost = re.sub(r" · rates [0-9]{4}-[0-9]{2}-[0-9]{2}$", "", cells[10]).removesuffix(" (Standard assumed)")
                    cells = [*cells[:2], cost, *cells[3:6], *cells[11:13], *cells[6:10], cells[2]]
                total = cells[9].replace(",", "")
                cached = cells[11].partition(" ")[0].replace(",", "")
                if total.isdecimal():
                    cells[9] = benchmark_input(int(total), int(cached) if cached.isdecimal() else None)
                del cells[11]
            lines[position] = "| " + " | ".join(cells) + " |\n"
            position += 1
    text = "".join(lines)
    text = text.replace(PREVIOUS_RATE_NOTE, ARCHIVED_RATE_NOTE, 1)
    text = text.replace(PREVIOUS_BENCHMARK_PRICING_NOTES.strip(), BENCHMARK_PRICING_NOTES.strip(), 1)
    if BENCHMARK_PRICING_NOTES.strip() not in text:
        text = text.rstrip() + "\n\n" + BENCHMARK_PRICING_NOTES
    return text


def benchmark_sort_key(row):
    cutoff = BENCHMARK_TIME.search(row[10])
    return cutoff[1] if cutoff else row[10]


def game_table(title, game_id, rows):
    return (f"## {title}\n<!-- agentscumm:game:{game_id} -->\n\n"
            + BENCHMARK_HEADER + "\n" + BENCHMARK_SEPARATOR + "\n"
            + "".join(map(benchmark_row, sorted(rows, key=benchmark_sort_key, reverse=True))))


def read_table(lines, start, separator, columns):
    require(start + 1 < len(lines) and lines[start + 1].strip() == separator,
            "Invalid benchmark comparison table separator")
    end = start + 2
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return end, [row_cells(line, columns) for line in lines[start + 2:end]]


def migrate_flat_table(text):
    lines = text.splitlines(keepends=True)
    for start, line in enumerate(lines):
        if line.strip() == FLAT_BENCHMARK_HEADER:
            end, rows = read_table(lines, start, FLAT_BENCHMARK_SEPARATOR, 12)
            games = {}
            for row in rows:
                title = row.pop(1)
                games.setdefault(title, []).append(row + ["Unknown", "Unknown"])
            tables = []
            for title, rows in games.items():
                game_id = re.sub(r"[^a-z0-9]+", "-", html.unescape(title).lower()).strip("-")
                tables.append(game_table(title, game_id, rows))
            lines[start:end] = ["\n".join(tables)]
            break
    return "".join(lines)


def update_benchmark(text, stats, title):
    text = migrate_flat_table(upgrade_benchmark_table(text))
    marker = benchmark_marker(stats)
    matches = text.count(marker)
    require(matches <= 1, "Duplicate benchmark identity; resolve before registration")
    new_row = benchmark_cells(stats)
    lines = text.splitlines(keepends=True)
    game_id = stats["game_id"]
    title = public_text(title or game_id.replace("-", " ").title())
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", game_id), "Invalid benchmark game ID")
    last_table_end = None
    for heading, line in enumerate(lines[:-1]):
        section = GAME_SECTION.fullmatch(line + lines[heading + 1])
        if not section:
            continue
        start = heading + 3
        require(start < len(lines) and lines[start].strip() == BENCHMARK_HEADER,
                "Missing game benchmark table")
        end, rows = read_table(lines, start, BENCHMARK_SEPARATOR, len(new_row))
        last_table_end = end
        for row in rows:
            if marker in row[6]:
                if stats.get("verified_progress") and row[0] != "✅":
                    row[0] = new_row[0]
                for index in (1, 5, 6, 8, 9, 10):
                    row[index] = new_row[index]
                # Older manifests may omit provenance already verified from the trajectory.
                for index in (11, 12):
                    if row[index] == "Unknown" or new_row[12].startswith("Server · "):
                        row[index] = new_row[index]
                lines[heading:end] = [game_table(section[1], game_id, rows)]
                return "".join(lines), False
        if not matches and section[2] == game_id:
            lines[heading:end] = [game_table(section[1], game_id, rows + [new_row])]
            return "".join(lines), True

    require(not matches, "Benchmark identity is outside a game table")
    table = game_table(title, game_id, [new_row])
    if last_table_end is None:
        position = 1 if lines and lines[0].strip() == "# Benchmarks" else 0
        lines[position:position] = ["\n", table] if position else [table, "\n"]
    else:
        lines[last_table_end:last_table_end] = ["\n", table]
    return "".join(lines), True


def benchmark_root():
    if os.environ.get("SCUMMBENCH_REGISTRY_ROOT"):
        return Path(os.environ["SCUMMBENCH_REGISTRY_ROOT"]).resolve()
    try:
        result = subprocess.run(["scummbench", "paths"], check=True, capture_output=True, text=True)
        return Path(json.loads(result.stdout)["repository"]).resolve()
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        raise ValueError("ScummBench is required for result tracking; install its editable checkout. No local fallback.") from error


def register_benchmark(archive, *, repo=ROOT, title=None):
    command = ["scummbench", "register-archive", "--archive", str(Path(archive).absolute()),
               "--source-repo", str(Path(repo).resolve()),
               "--store", str(benchmark_root())]
    if title:
        command += ["--game-title", title]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return bool(json.loads(result.stdout)["benchmark_added"])
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        raise ValueError("ScummBench registration failed; preserve the archive and retry. No AgentSCUMM result write occurred.") from error


def finalize(request):
    repo = Path(request["repo"]).resolve()
    work = private_directory(repo, request["work_dir"])
    state_file = work / "finalization.json"
    state = {"status": "waiting_for_turn", "turn_id": request["turn_id"], "pid": os.getpid()}
    atomic_json(state_file, state)
    try:
        require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", request["game_id"]), "Invalid game ID")
        require(request["completion_note"].strip(), "Ending evidence is required")
        require(0 < request["timeout_seconds"] <= 3600, "Finalizer timeout must be between 0 and 3600 seconds")
        rollout = Path(request["rollout"])
        identity = rollout.stat()
        deadline = time.monotonic() + request["timeout_seconds"]
        while True:
            current = rollout.stat()
            require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), "Rollout was replaced while waiting")
            cutoff = turn_cutoff(rollout, request["turn_id"])
            if cutoff is not None:
                break
            require(time.monotonic() < deadline, "Timed out waiting for the gameplay turn to finish")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        state["status"] = "exporting"
        atomic_json(state_file, state)
        with tempfile.TemporaryDirectory(prefix=".finalize-", dir=work) as temporary:
            source = Path(temporary) / "completed-rollout.jsonl"
            snapshot_turn(rollout, source, cutoff)
            header = next(r["payload"] for _, r in EXPORT.records(source) if r.get("type") == "session_meta")
            date = EXPORT.USAGE.timestamp(header["timestamp"]).astimezone(ZoneInfo("Europe/Lisbon")).strftime("%Y%m%d")
            run_id = request.get("run_id") or header["id"]
            require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", run_id), "Invalid run ID")
            archive = repo / "runs" / request["game_id"] / f"{date}-{run_id}"
            if archive.exists():
                stats = EXPORT.verify_run(archive)
                require(stats["status"] == "completed" and stats["game_id"] == request["game_id"]
                        and stats["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest(),
                        "Existing archive belongs to a different source cutoff; it was preserved")
            else:
                archive, stats = EXPORT.export_run(source, request["game_id"], repo=repo, run_id=run_id,
                    status="completed", completion_note=request["completion_note"],
                    walkthroughs=request.get("walkthrough_urls", []), skills=request.get("skills", []),
                    agent_context=request.get("agent_context"), pricing_rates=request.get("pricing_rates"),
                    work_dir=work)
        state.update(status="registering_benchmark", archive=str(archive))
        atomic_json(state_file, state)
        added = register_benchmark(archive, repo=repo, title=request.get("game_title"))
        state.update(status="completed", benchmark_added=added, results_owner="scummbench",
                     benchmark_path=str(benchmark_root() / "BENCHMARKS.md"), verified=True,
                     mlflow={"status": "pending", "command": "flush-mlflow"},
                     api_cost_status=stats.get("api_cost", {}).get("status", "unavailable"),
                     total_api_price_usd=stats.get("api_cost", {}).get("total_api_price_usd"))
        atomic_json(state_file, state)
        return state
    except Exception as error:
        state.update(status="failed", error=str(error))
        atomic_json(state_file, state)
        raise


def queue(request):
    work = private_directory(request["repo"], request["work_dir"])
    request_file = work / "finalize-request.json"
    state_file = work / "finalization.json"
    if request_file.exists():
        require(not request_file.is_symlink() and json.loads(request_file.read_text()) == request,
                "A different finalization request already exists; preserve it and use another working directory")
        require(state_file.is_file() and not state_file.is_symlink(), "Existing finalization request has no valid state; inspect before retrying")
        return json.loads(state_file.read_text())
    EXPORT.write_json(request_file, request)
    atomic_json(state_file, {"status": "queued", "turn_id": request["turn_id"]})
    log = work / "finalization.log"
    require(not log.is_symlink(), "Linked finalizer log")
    try:
        with log.open("ab") as output:
            os.chmod(log, 0o600)
            process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "worker", "--request", str(request_file)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True, close_fds=True)
        # Reap the child if the launcher stays alive; the detached worker also
        # survives the launcher exiting immediately after the queue receipt.
        threading.Thread(target=process.wait, daemon=True).start()
        return {"status": "queued", "pid": process.pid, "state": str(state_file)}
    except Exception as error:
        atomic_json(state_file, {"status": "failed", "error": str(error)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("queue", help="Queue local post-turn export and benchmark registration")
    for name in ("rollout", "work-dir", "agent-context"):
        start.add_argument("--" + name, type=Path, required=name != "agent-context")
    for name in ("turn-id", "game-id", "completion-note"):
        start.add_argument("--" + name, required=True)
    start.add_argument("--game-title")
    start.add_argument("--run-id")
    start.add_argument("--timeout-seconds", type=float, default=300)
    start.add_argument("--walkthrough-url", action="append", default=[])
    start.add_argument("--skill", type=Path, action="append", default=[])
    start.add_argument("--pricing-rates", type=Path, help="Official rates JSON verified on the export date")
    worker = commands.add_parser("worker", help="Run or retry a stored finalization request")
    worker.add_argument("--request", type=Path, required=True)
    register = commands.add_parser("register", help="Register an already verified archive without exporting again")
    register.add_argument("archive", type=Path)
    register.add_argument("--game-title")
    tracking = commands.add_parser("publish-mlflow", help="Send or retry a verified archive in MLflow")
    tracking.add_argument("archive", type=Path)
    flush = commands.add_parser("flush-mlflow", help="Send pending run archives to MLflow")
    flush.add_argument("--status", action="store_true", help="List local delivery state without contacting MLflow")
    args = parser.parse_args()
    try:
        if args.command == "queue":
            require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.game_id), "Invalid game ID")
            require(0 < args.timeout_seconds <= 3600 and args.completion_note.strip(), "Invalid timeout or missing ending evidence")
            result = queue({"repo": str(ROOT), "rollout": str(args.rollout.absolute()), "work_dir": str(args.work_dir.absolute()),
                "turn_id": args.turn_id, "game_id": args.game_id, "game_title": args.game_title, "run_id": args.run_id,
                "completion_note": args.completion_note, "timeout_seconds": args.timeout_seconds,
                "agent_context": str(args.agent_context.absolute()) if args.agent_context else None,
                "pricing_rates": str(args.pricing_rates.absolute()) if args.pricing_rates else None,
                "walkthrough_urls": args.walkthrough_url,
                "skills": [str(path.absolute()) for path in args.skill] + [str(ROOT / ".agents/skills/agentscumm_play-game/SKILL.md")]})
        elif args.command == "worker":
            require(args.request.is_file() and not args.request.is_symlink(), "Request must be a regular, unlinked file")
            result = finalize(json.loads(args.request.read_text()))
        elif args.command == "register":
            result = {"benchmark_added": register_benchmark(args.archive.absolute(), title=args.game_title)}
        elif args.command == "publish-mlflow":
            result = mlflow_queue(ROOT, archive=args.archive.absolute())["archives"][0]
        else:
            result = mlflow_queue(ROOT, status_only=args.status)
        print(EXPORT.dump(result))
        if result.get("failed") or result.get("status") == "failed":
            sys.exit(1)
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Finalization failed: {error}\n")


if __name__ == "__main__":
    main()
