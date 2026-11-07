#!/usr/bin/env python3
"""Finish a local run archive after its gameplay turn ends and register its benchmark."""

import argparse
from datetime import datetime, timezone
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
require = EXPORT.require
LEGACY_BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Total actions | Action sequences |"
LEGACY_BENCHMARK_SEPARATOR = "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |"
PREVIOUS_BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Input tokens | Output tokens | Cached input tokens | Total API price (USD) | Total actions | Action sequences |"
BENCHMARK_HEADER = "| Status | Game | Date (UTC) | Model / effort | Walkthrough assistance | Completion time | Tokens | Input tokens | Output tokens | Cached input tokens (% of input) | Total API cost (USD) | Total actions | Action sequences |"
BENCHMARK_SEPARATOR = "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
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
BENCHMARK_PRICING_NOTES = (
    "Cache percentage is cached input divided by total input, rounded to two decimal places. "
    "It is N/A when total input is zero. Output includes reasoning tokens.\n\n"
    "Total API cost estimates what the recorded model tokens would cost through each model's default API provider. "
    "Each request uses the archived official rates for its exact model, processing tier and context length. "
    "Multiply uncached input, cached input and output by their respective USD rates per million tokens, "
    "divide by one million, and sum across requests. Apply separate cache-write rates when applicable. "
    "The rates date is when those prices were verified for the archive and can differ from the run date. "
    "Standard assumed marks missing processing tiers. Unknown means no verified archived cost. "
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
    require(not path.is_symlink(), "Linked finalization state")
    fd, scratch = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(scratch, path)
    finally:
        if Path(scratch).exists():
            Path(scratch).unlink()


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


def benchmark_cached_input(cached, total):
    if cached is None:
        return "Unknown"
    percentage = "Unknown"
    if total is not None:
        percentage = f"{cached / total:.2%}" if total else "N/A"
    return f"{cached:,} ({percentage})"


def benchmark_usage(stats):
    cost = stats.get("api_cost") or {}
    api_price = "Unknown"
    if cost.get("status") == "priced":
        api_price = "$" + format(EXPORT.USAGE.rate(cost["total_api_price_usd"]), ",.2f")
        if "Missing processing tiers priced as standard" in cost.get("assumptions", []):
            api_price += " (Standard assumed)"
        if cost.get("pricing_retrieved_on"):
            api_price += f" · rates {cost['pricing_retrieved_on']}"
    tokens = stats["tokens"]
    return [*(f"{tokens[key]:,}" if tokens.get(key) is not None else "Unknown"
              for key in ("input_tokens", "output_tokens")),
            benchmark_cached_input(tokens.get("cached_input_tokens"), tokens.get("input_tokens")), api_price]


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


def benchmark_entry(stats, title=None):
    digest = stats["trajectory"]["sha256"]
    require(re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid trajectory checksum")
    title = title or stats["game_id"].replace("-", " ").title()
    completed = EXPORT.USAGE.timestamp(stats["recorded_through"]).astimezone(timezone.utc).date()
    seconds = round(stats["wall_clock_seconds"])
    cells = [
        benchmark_status(stats), title, completed.isoformat(),
        ", ".join(stats["models"]) + " / " + (", ".join(stats["reasoning_levels"]) or "Unknown"),
        "Yes" if stats["walkthrough_urls"] else "Unspecified",
        f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}",
        f"{stats['tokens']['total_tokens']:,}",
        *benchmark_usage(stats),
        *benchmark_actions(stats),
    ]
    # Keep the deduplication marker inside the row so Markdown renders one table.
    return "| " + " | ".join(public_text(value) for value in cells) + f" <!-- agentscumm:benchmark:{digest} --> |\n"


def upgrade_benchmark_table(text, stats=None):
    """Upgrade the previous table without inventing missing archive measurements."""
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines)
               if line.strip() in (LEGACY_BENCHMARK_HEADER, PREVIOUS_BENCHMARK_HEADER, BENCHMARK_HEADER)]
    require(len(headers) <= 1, "Multiple benchmark comparison tables; resolve before registration")
    if headers and lines[headers[0]].strip() != BENCHMARK_HEADER:
        position = headers[0]
        legacy = lines[position].strip() == LEGACY_BENCHMARK_HEADER
        separator = LEGACY_BENCHMARK_SEPARATOR if legacy else BENCHMARK_SEPARATOR
        require(position + 1 < len(lines) and lines[position + 1].strip() == separator,
                "Invalid previous benchmark comparison table separator")
        lines[position:position + 2] = [BENCHMARK_HEADER + "\n", BENCHMARK_SEPARATOR + "\n"]
        position += 2
        while position < len(lines) and lines[position].lstrip().startswith("|"):
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[position].strip())[1:-1]]
            require(len(cells) == (9 if legacy else 13), "Invalid previous benchmark row")
            if legacy:
                cells[7:7] = ["Unknown"] * 4
            elif re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+", cells[9]):
                total = cells[7].replace(",", "")
                cells[9] = benchmark_cached_input(int(cells[9].replace(",", "")),
                                                  int(total) if total.isdecimal() else None)
            lines[position] = "| " + " | ".join(cells) + " |\n"
            position += 1
    if stats is not None:
        marker = f"<!-- agentscumm:benchmark:{stats['trajectory']['sha256']} -->"
        matching = [i for i, line in enumerate(lines) if marker in line]
        require(len(matching) <= 1, "Duplicate benchmark identity; resolve before registration")
        if matching:
            position = matching[0]
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[position].strip())[1:-1]]
            require(len(cells) == 13, "Invalid benchmark row")
            if stats.get("verified_progress") and cells[0] != "✅":
                cells[0] = public_text(benchmark_status(stats))
            cells[7:] = [*map(public_text, benchmark_usage(stats)), *benchmark_actions(stats)]
            cells[-1] += f" {marker}"
            lines[position] = "| " + " | ".join(cells) + " |\n"
    text = "".join(lines)
    text = text.replace(PREVIOUS_BENCHMARK_PRICING_NOTES.strip(), BENCHMARK_PRICING_NOTES.strip(), 1)
    if BENCHMARK_PRICING_NOTES.strip() not in text:
        text = text.rstrip() + "\n\n" + BENCHMARK_PRICING_NOTES
    return text


def register_benchmark(archive, *, repo=ROOT, title=None):
    repo, archive = Path(repo).resolve(), Path(os.path.abspath(archive))
    require(archive.is_relative_to(repo / "runs"), "Benchmark archive must belong to this checkout")
    require(not any(path.is_symlink() for path in [archive, *archive.parents]), "Linked benchmark archive path")
    stats = EXPORT.verify_run(archive)
    require(stats["status"] in ("completed", "in_progress", "stopped", "blocked"), "Invalid benchmark run status")
    require(stats["status"] != "completed" or stats.get("completion_note"), "Completed benchmarks require ending evidence")
    private = private_directory(repo, repo / ".internal/benchmark-registration")
    lock = private / "update.lock"
    acquired = False
    deadline = time.monotonic() + 10
    while not acquired:
        try:
            with lock.open("x") as stream:
                os.chmod(lock, 0o600)
                stream.write(str(os.getpid()))
            acquired = True
        except FileExistsError:
            require(time.monotonic() < deadline, "Benchmark update lock is busy; retry registration")
            time.sleep(0.1)
    try:
        target = repo / "BENCHMARKS.md"
        require(not target.is_symlink(), "Linked benchmark file")
        original = target.read_bytes() if target.exists() else None
        text = original.decode() if original is not None else "# Benchmarks\n\n" + BENCHMARK_NOTES
        text = upgrade_benchmark_table(text, stats)
        marker = f"<!-- agentscumm:benchmark:{stats['trajectory']['sha256']} -->"
        added = marker not in text
        if added:
            entry = benchmark_entry(stats, title)
            lines = text.splitlines(keepends=True)
            headers = [i for i, line in enumerate(lines) if line.strip() == BENCHMARK_HEADER]
            if headers:
                position = headers[0] + 1
                require(position < len(lines) and lines[position].strip() == BENCHMARK_SEPARATOR,
                        "Invalid benchmark comparison table separator")
                position += 1
                while position < len(lines) and lines[position].lstrip().startswith("|"):
                    position += 1
                if position and not lines[position - 1].endswith("\n"):
                    lines[position - 1] += "\n"
                lines.insert(position, entry)
                text = "".join(lines)
            else:
                table = BENCHMARK_HEADER + "\n" + BENCHMARK_SEPARATOR + "\n" + entry + "\n"
                anchor = "## Adding results"
                text = text.replace(anchor, table + anchor, 1) if anchor in text else text.rstrip() + "\n\n" + table
        if original is not None and text.encode() == original:
            return False
        require((target.read_bytes() if target.exists() else None) == original, "Benchmark file changed during registration; retry")
        fd, scratch = tempfile.mkstemp(prefix=".benchmark-", dir=private)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.chmod(scratch, 0o644)
            require((target.read_bytes() if target.exists() else None) == original, "Concurrent benchmark edit; retry")
            os.replace(scratch, target)
        finally:
            if Path(scratch).exists():
                Path(scratch).unlink()
        return added
    finally:
        lock.unlink()


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
        state.update(status="completed", benchmark_added=added, verified=True,
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
        else:
            result = {"benchmark_added": register_benchmark(args.archive.absolute(), title=args.game_title)}
        print(EXPORT.dump(result))
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Finalization failed: {error}\n")


if __name__ == "__main__":
    main()
