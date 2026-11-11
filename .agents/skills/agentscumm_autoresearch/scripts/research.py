#!/usr/bin/env python3
"""Prepare and score one AgentSCUMM research round. Never launches a player."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import uuid
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[4]
GAME = "flight-of-the-amazon-queen"
CONDITION = {"all": [{"variable": "engine_id", "equals": "queen"},
                     {"variable": "room_id", "equals": 71},
                     {"variable": "room_ready", "equals": True}]}
TYPES = {"click", "move", "key", "drag", "wait", "screenshot"}
TERMINAL = {"finished", "timeout", "blocked", "infrastructure_failure"}


def prepare_agent(repo):
    spec = importlib.util.spec_from_file_location("agentscumm_prepare_play", ROOT / "scripts/prepare-play.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    revision = helper.prepare(repo)
    require(revision["ready"], "AgentSCUMM changed; commit the automatic version bump and all changes before play")
    return {key: revision[key] for key in ("name", "version", "commit_id", "branch", "dirty", "source")}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp needs a timezone")
    return parsed


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   stderr=subprocess.DEVNULL).strip()


def safe(path, parent):
    path, parent = Path(path).absolute(), Path(parent).resolve()
    require(path.is_relative_to(parent), "Artifact is outside its owned directory")
    current = parent
    for part in path.relative_to(parent).parts:
        require(part not in (".", ".."), "Unsafe artifact path")
        current /= part
        require(not current.is_symlink(), f"Linked artifact is not allowed: {current.name}")
    return path


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, indent=2) + "\n")


def rows(path):
    require(Path(path).stat().st_size <= 64 * 1024 * 1024, "Evidence log is too large")
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def probe_provider(origin):
    url = urlsplit(origin)
    require(url.scheme in ("http", "https") and url.netloc and not url.username and not url.password
            and url.path in ("", "/") and not url.query and not url.fragment,
            "Use a ScummWEB HTTP(S) origin without credentials, path, query or fragment")
    origin = origin.rstrip("/")

    def fetch(route, maximum):
        with urlopen(origin + route, timeout=15) as response:
            require(response.geturl() == origin + route, "Runtime asset redirected; select its actual origin")
            content = response.read(maximum + 1)
        require(len(content) <= maximum, "Runtime asset exceeds the expected size")
        return content

    plugin = fetch("/data/plugins/libqueen.so", 8 * 1024 * 1024)
    require(b"__scummwebEvaluationEnabled" in plugin and b"room_ready" in plugin,
            "Queen binary lacks the evaluator provider; use a rebuilt supported runtime")
    assets = fetch("/runtime-assets.json", 2 * 1024 * 1024)
    source = fetch("/source-info.json", 2 * 1024 * 1024)
    asset_data, source_data = json.loads(assets), json.loads(source)
    require(isinstance(asset_data, dict) and isinstance(source_data, dict), "Invalid runtime manifests")
    return {"origin": origin, "queen_sha256": sha(plugin), "runtime_assets_sha256": sha(assets),
            "source_info_sha256": sha(source), "source_info": source_data,
            "runtime_version": asset_data.get("version")}


def clean_commit(repo):
    require(Path(git(repo, "rev-parse", "--show-toplevel")).resolve() == repo.resolve(), "Select the owning Git root")
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "Play Game requires a clean checkout; have the user commit before initializing a round")
    return git(repo, "rev-parse", "--verify", "HEAD"), git(repo, "branch", "--show-current")


def clean_backbone():
    """Resolve the benchmark's selected backbone without importing agent code."""
    result = subprocess.run(["scummbench", "paths"], check=True, capture_output=True, text=True)
    selected = json.loads(result.stdout).get("scummweb_checkout")
    require(isinstance(selected, str) and Path(selected).is_absolute(), "ScummWEB backbone is unavailable")
    checkout = Path(selected).resolve()
    require(read_json(checkout / "package.json").get("name") == "scummweb", "Not a ScummWEB checkout")
    commit, _ = clean_commit(checkout)
    return {"repository": "scummweb", "commit_id": commit, "dirty": False}


def init_round(repo, args, probe=probe_provider):
    agent = prepare_agent(repo)
    commit, branch = clean_commit(repo)
    backbone = clean_backbone()
    require(1 <= len(args.strategy) <= 3, "A round has one to three strategies")
    require(30 <= args.timeout_seconds <= 3600, "Trial limit must be 30–3600 seconds")
    require("/.internal/" in (repo / ".gitignore").read_text().splitlines(), "Root .gitignore must exclude /.internal/")
    require(not git(repo, "ls-files", "--", ".internal"), "Private research data must not be tracked")
    provider = probe(args.base_url)
    common = Path(args.common_context).read_bytes()
    strategies = [Path(path).read_bytes() for path in args.strategy]
    for content in [common, *strategies]:
        require(0 < len(content) <= 128 * 1024, "Briefs must be nonempty UTF-8 files of at most 128 KiB")
        content.decode("utf-8")
    require(len({sha(s) for s in strategies}) == len(strategies), "Use distinct strategy instructions")
    previous = None
    if args.previous_round:
        previous = safe(Path(args.previous_round).absolute(), repo / ".internal" / GAME)
        require(read_json(previous / "round.json")["state"] == "awaiting_confirmation",
                "Previous round must be reported before another round can be prepared")
    prefix = "research-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    parent = safe(repo / ".internal" / GAME, repo)
    round_dir = safe(parent / (prefix + "-round"), repo)
    require(git(repo, "check-ignore", str(round_dir)), "Round directory must be ignored")
    require(clean_commit(repo)[0] == commit, "Checkout changed during preflight")
    round_dir.mkdir(parents=True, mode=0o700)
    started = now()
    round_data = {"schema_version": 1, "state": "prepared", "game_id": GAME,
                  "round_id": round_dir.name, "created_at": started, "commit_id": commit,
                  "branch": branch, "backbone_revision": backbone, "mode": "skip_intro", "condition": CONDITION,
                  "trial_timeout_seconds": args.timeout_seconds, "model_context": args.model_context,
                  "provider": provider, "common_sha256": sha(common),
                  "previous_round": str(previous) if previous else None, "trials": []}
    # Starting manifests precede all startup context and player interaction.
    for index, content in enumerate(strategies):
        trial = safe(parent / (prefix + "-" + chr(97 + index)), repo)
        trial.mkdir(mode=0o700)
        write_json(trial / "run.json", {"schema_version": 1, "kind": "working_run", "status": "in_progress",
                   "game_id": GAME, "run_id": trial.name, "started_at": started,
                   "agent": agent,
                   "git": {"play_start": {"commit_id": commit, "branch": branch,
                           "dirty": False, "source": "clean_play_preflight"}},
                   "run_limit": {"kind": "stop_condition", "condition": CONDITION},
                   "research_round": str(round_dir), "strategy_sha256": sha(content)})
        round_data["trials"].append({"id": chr(97 + index), "directory": str(trial),
                                    "strategy_sha256": sha(content)})
    write_json(round_dir / "round.json", round_data)
    (round_dir / "common-context.md").write_bytes(common)
    for item, content in zip(round_data["trials"], strategies):
        trial = Path(item["directory"])
        (trial / "strategy.md").write_bytes(content)
        (trial / "common-context.md").write_bytes(common)
        server_args = [str(repo / "src/index.js"), "--data-dir", str(trial / "browser"),
                       "--run-id", str(uuid.uuid4()), "--base-url", provider["origin"],
                       "--evaluation-log", str(trial / "variables.jsonl"),
                       "--result-file", str(trial / "result.json"), "--telemetry-ms", "250",
                       "--stop-condition", json.dumps(CONDITION, separators=(",", ":"))]
        write_json(trial / "codex-args.json", ["--ignore-user-config", "--ephemeral", "--json", "-c",
                   f'mcp_servers.agentscumm={{command="node", args={json.dumps(server_args, ensure_ascii=False)}, '
                   'required=true, tool_timeout_sec=60}'])
    return {"round_directory": str(round_dir), "trials": round_data["trials"],
            "state": "prepared", "players_started": False}


def dispatch_trial(directory, trial_id, repo=ROOT):
    directory = safe(Path(directory).absolute(), repo / ".internal" / GAME)
    manifest = read_json(safe(directory / "round.json", directory))
    require(manifest["state"] == "prepared", "Cannot dispatch a reported round")
    require(clean_commit(repo)[0] == manifest["commit_id"], "Checkout changed before dispatch")
    item = next((r for r in manifest["trials"] if r["id"] == trial_id), None)
    require(item is not None, "Unknown trial")
    trial = safe(item["directory"], repo / ".internal" / GAME)
    started = datetime.now(timezone.utc)
    dispatch = {"dispatched_at": started.isoformat(),
                "deadline": (started + timedelta(seconds=manifest["trial_timeout_seconds"])).isoformat()}
    write_json(trial / "dispatch.json", dispatch)
    return {"trial_directory": str(trial), **dispatch, "players_started": False}


def measure_trial(item, round_data, repo):
    trial = safe(item["directory"], repo / ".internal" / GAME)
    worker = read_json(safe(trial / "worker-result.json", trial))
    require(worker.get("status") in TERMINAL and worker.get("process_exited") is True
            and worker.get("server_process_exited") is True,
            f"Trial {item['id']} is not terminal or its owned processes have not exited")
    result = {"id": item["id"], "directory": str(trial), "status": worker["status"],
              "verified_success": False, "eligible": False, "total_actions": None,
              "summary": worker.get("summary", ""), "issues": []}
    try:
        require(sha(safe(trial / "strategy.md", trial).read_bytes()) == item["strategy_sha256"], "Strategy changed")
        require(sha(safe(trial / "common-context.md", trial).read_bytes()) == round_data["common_sha256"], "Common context changed")
        manifest = read_json(safe(trial / "run.json", trial))
        dispatch = read_json(safe(trial / "dispatch.json", trial))
        require(timestamp(dispatch["deadline"]) == timestamp(dispatch["dispatched_at"])
                + timedelta(seconds=round_data["trial_timeout_seconds"]), "Dispatch budget differs from the frozen round limit")
        baseline = manifest["git"]["play_start"]
        require(baseline["commit_id"] == round_data["commit_id"] and baseline["dirty"] is False, "Starting revision mismatch")
        require(manifest["game_id"] == GAME and manifest["run_limit"]["condition"] == CONDITION, "Trial endpoint changed")
        logs = list(safe(trial / "browser" / "runs", trial).glob("*.jsonl"))
        require(len(logs) == 1, "Need exactly one server session; missing logs or restarts cannot be scored")
        records = rows(safe(logs[0], trial))
        launches = [r for r in records if r.get("type") == "launch"]
        require(len(launches) == 1, "Need exactly one launch")
        launch = launches[0]
        require(launch["game"] == GAME and launch["mode"] == "skip_intro", "Different game or starting mode")
        require(timestamp(launch["timestamp"]) >= timestamp(dispatch["dispatched_at"]), "Game started before dispatch")
        revision = launch["sourceRevision"]
        expected_revision = round_data.get("backbone_revision") or {"repository": "agentscumm", "commit_id": round_data["commit_id"]}
        require(revision["repository"] == expected_revision["repository"]
                and revision["commit_id"] == expected_revision["commit_id"]
                and revision["dirty"] is False, "Server revision mismatch or dirty startup")
        actual_url, expected_url = urlsplit(launch["url"]), urlsplit(round_data["provider"]["origin"])
        require((actual_url.scheme, actual_url.netloc) == (expected_url.scheme, expected_url.netloc),
                "Trial used a different runtime origin")
        starts = [r for r in records if r.get("type") == "action-start"]
        require(len({r["requestId"] for r in starts}) == len(starts), "Repeated accepted request ID")
        require(len({r["actionId"] for r in starts}) == len(starts), "Repeated action ID")
        require(all(r["sessionId"] == launch["sessionId"] for r in starts), "Mixed action sessions")
        counts = Counter()
        for action in starts:
            inputs = action["inputs"]
            require(1 <= len(inputs) <= 32 and action["total"] == len(inputs), "Invalid action-start evidence")
            require(all(a.get("type") in TYPES for a in inputs), "Unsupported action type")
            counts.update(a["type"] for a in inputs)
        finishes = [r for r in records if r.get("type") == "action-finish"]
        executed = [step for r in finishes for step in r.get("steps", [])]
        result.update(total_actions=sum(counts.values()), actions_by_type=dict(counts),
                      action_sequences=len(starts), attempted_steps=len(executed),
                      delivered_steps=sum(s.get("state") in ("delivered", "captured") for s in executed),
                      explicit_wait_ms=sum(a.get("ms", a.get("milliseconds", 0))
                                           for r in starts for a in r["inputs"] if a["type"] == "wait"))
        events_path = safe(trial / "events.jsonl", trial)
        if events_path.exists():
            events = rows(events_path)
            result["act_tool_calls"] = sum(e.get("request", {}).get("name") == "act" for e in events)
            result["observe_calls"] = sum(e.get("request", {}).get("name") == "observe" for e in events)
            result["error_calls"] = sum(e.get("result", {}).get("isError") is True for e in events)
        require(worker["status"] == "finished", f"Worker ended as {worker['status']}")
        receipt_path = safe(trial / "result.json", trial)
        receipt = read_json(receipt_path)
        require(receipt.get("success") is True and receipt.get("reason") == "stop_condition", "No endpoint success")
        require(receipt["condition"] == CONDITION and receipt["game"] == GAME, "Different receipt condition/game")
        variables = receipt["variables"]
        require(variables["engine_id"] == "queen" and variables["room_id"] == 71 and variables["room_ready"] is True,
                "Wrong native endpoint")
        require(receipt["session_id"] == launch["sessionId"] and receipt["source_revision"] == revision, "Receipt provenance mismatch")
        require(receipt["action_sequences"] == len(starts) and starts, "Receipt action count mismatch")
        exit_code = worker.get("codex_exit_code", worker.get("client_exit_code"))
        require(type(exit_code) is int and exit_code == 0, "Missing or unsuccessful player exit")
        detected = timestamp(receipt["detected_at"])
        require(timestamp(dispatch["dispatched_at"]) <= detected <= timestamp(dispatch["deadline"]),
                "Success occurred outside the trial's wall-time budget")
        require(all(timestamp(r["timestamp"]) <= detected for r in starts), "Inputs started after endpoint detection")
        require(len(finishes) == len(starts) and {r["actionId"] for r in finishes} == {r["actionId"] for r in starts},
                "Missing action-finish evidence")
        require(all(r["state"] in ("completed", "failed", "stopped") for r in finishes), "Unfinished action")
        telemetry = rows(safe(trial / "variables.jsonl", trial))
        require(any(r.get("event") == "variables" and r.get("variables", {}).get("room_id") == 70
                    and r["variables"].get("room_ready") is True and timestamp(r["timestamp"]) < detected
                    for r in telemetry), "Missing initial ready hotel-room telemetry")
        require(sum(r.get("event") == "success" for r in telemetry) == 1, "Missing or duplicate terminal telemetry")
        terminal = telemetry[-1]
        require(all(terminal.get(key) == receipt.get(key) for key in
                    ("event", "success", "reason", "condition", "variables", "detected_at",
                     "session_id", "game", "source_revision", "action_sequences")),
                "Terminal telemetry contradicts the success receipt")
        image = safe(Path(receipt["screenshot"]), trial)
        review = read_json(safe(trial / "review.json", trial))
        require(review.get("endpoint_verified") is True and review.get("receipt_sha256") == sha(receipt_path.read_bytes())
                and review.get("screenshot_sha256") == sha(image.read_bytes()), "Missing or stale independent screenshot review")
        result.update(status="success", verified_success=True, elapsed_ms=receipt.get("elapsed_ms"),
                      dispatch_to_goal_ms=(detected - timestamp(dispatch["dispatched_at"])).total_seconds() * 1000,
                      screenshot=str(image), receipt=str(receipt_path))
    except (ValueError, KeyError, TypeError, OSError) as error:
        result["issues"].append(str(error))
        if result["status"] == "finished":
            result["status"] = "unverified"
    return result


def score_round(directory, repo=ROOT, probe=probe_provider):
    directory = safe(Path(directory).absolute(), repo / ".internal" / GAME)
    manifest = read_json(safe(directory / "round.json", directory))
    require(manifest["schema_version"] == 1 and manifest["game_id"] == GAME and manifest["condition"] == CONDITION,
            "Unsupported round manifest")
    require(1 <= len(manifest["trials"]) <= 3, "Round must contain one to three trials")
    require(type(manifest["trial_timeout_seconds"]) is int and 30 <= manifest["trial_timeout_seconds"] <= 3600,
            "Invalid frozen trial limit")
    require(len({r["directory"] for r in manifest["trials"]}) == len(manifest["trials"]), "Duplicate trial directory")
    results = [measure_trial(item, manifest, repo) for item in manifest["trials"]]
    issues = []
    if sha(safe(directory / "common-context.md", directory).read_bytes()) != manifest["common_sha256"]:
        issues.append("Round common context changed")
    if git(repo, "rev-parse", "HEAD") != manifest["commit_id"] or git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        issues.append("AgentSCUMM checkout changed during the round")
    try:
        if probe(manifest["provider"]["origin"]) != manifest["provider"]:
            issues.append("Runtime provider or manifests changed during the round")
    except Exception as error:
        issues.append("Runtime identity could not be reverified: " + str(error))
    for result in results:
        result["eligible"] = result["verified_success"] and not issues
    eligible = [r for r in results if r["eligible"]]
    best = min((r["total_actions"] for r in eligible), default=None)
    winners = [r["id"] for r in eligible if r["total_actions"] == best]
    scores = {"schema_version": 1, "scored_at": now(), "metric": "submitted_steps_in_unique_accepted_act_requests",
              "comparison_issues": issues, "best_trial_ids": winners, "best_total_actions": best,
              "trials": results, "another_round_requires_user_confirmation": True}
    require(not (directory / "scores.json").exists() and not (directory / "report.md").exists(),
            "This round already has a report; preserve it instead of overwriting analysis")
    write_json(directory / "scores.json", scores)
    lines = ["# Autoresearch round", "", "Flight of the Amazon Queen: fresh hotel start → ready laundry room 71.", "",
             "| Trial | Outcome | Submitted actions | Act sequences | Delivered steps |", "| --- | --- | ---: | ---: | ---: |"]
    for r in results:
        lines.append(f"| {r['id']} | {r['status']} | {r.get('total_actions')} | {r.get('action_sequences', 'Unknown')} | {r.get('delivered_steps', 'Unknown')} |")
    lines += ["", "Best observed trial(s): " + (", ".join(winners) if winners else "No verified comparable winner"), "",
              "Primary score includes waits, screenshots and canceled trailing steps. Equal scores are ties.", "",
              "## Strategy evidence", ""]
    for r in results:
        lines += [f"### Trial {r['id']}", "", str(r.get("summary", "")), "",
                  "Evidence directory: " + r["directory"], "",
                  "Verification issues: " + ("; ".join(r["issues"]) or "None"), "",
                  "Coordinator: add concrete request/frame evidence and distinguish observed failures from causal hypotheses.", ""]
    lines += ["## Comparison limits", "", *["- " + issue for issue in issues],
              "- One concurrent trial per strategy; timings are affected by resource contention.",
              "- File ownership and evaluator-only instructions are not an enforced sandbox.",
              "- No complete-game outcome or statistical superiority is established.", "",
              "## Next round", "", "Await explicit user confirmation before preparing or launching another round.", ""]
    (directory / "report.md").write_text("\n".join(lines))
    manifest["state"] = "awaiting_confirmation"
    temporary = directory / "round.updated.json"
    write_json(temporary, manifest)
    temporary.replace(directory / "round.json")
    return {"report": str(directory / "report.md"), "scores": str(directory / "scores.json"),
            "best_trial_ids": winners, "best_total_actions": best, "comparison_issues": issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Prepare one round without starting any players")
    init.add_argument("--base-url", required=True)
    init.add_argument("--common-context", required=True)
    init.add_argument("--strategy", action="append", required=True)
    init.add_argument("--timeout-seconds", type=int, default=600)
    init.add_argument("--model-context", default="Inherited coordinator model/effort; record exact identifiers when exposed")
    init.add_argument("--previous-round")
    score = commands.add_parser("score", help="Verify and compare terminal trials")
    score.add_argument("round_directory")
    dispatch = commands.add_parser("dispatch", help="Record one worker's individual budget immediately before spawning it")
    dispatch.add_argument("round_directory")
    dispatch.add_argument("trial_id")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_round(ROOT, args)
        elif args.command == "dispatch":
            result = dispatch_trial(args.round_directory, args.trial_id)
        else:
            result = score_round(args.round_directory)
        print(json.dumps(result, indent=2))
    except Exception as error:
        parser.exit(1, f"Autoresearch: {error}\n")


if __name__ == "__main__":
    main()
