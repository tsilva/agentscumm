#!/usr/bin/env python3
"""Export a complete Codex rollout and its images into a portable run archive."""

import argparse
import ast
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / ".agents/skills/agentscumm_price-thread/scripts/thread_cost.py"
SPEC = importlib.util.spec_from_file_location("agentscumm_thread_cost", HELPER)
USAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USAGE)
CONTEXT_SPEC = importlib.util.spec_from_file_location("agentscumm_run_context", ROOT / "scripts/run_context.py")
CONTEXT = importlib.util.module_from_spec(CONTEXT_SPEC)
CONTEXT_SPEC.loader.exec_module(CONTEXT)
PRICING_SPEC = importlib.util.spec_from_file_location("agentscumm_run_pricing", ROOT / "scripts/run_pricing.py")
PRICING = importlib.util.module_from_spec(PRICING_SPEC)
PRICING_SPEC.loader.exec_module(PRICING)
MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
DATA_URL = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)
TRUNCATED = re.compile(r"^(.*?)…([0-9]+) chars truncated…(.*?)$", re.DOTALL)
ASSIGNED_CALL = re.compile(r"const\s+(\w+)\s*=\s*await\s+tools\.mcp__agentscumm__(\w+)\(")
FORWARD_IMAGES = re.compile(
    r"for\s*\(\s*const\s+(\w+)\s+of\s+(\w+)\.content\s*\?\?\s*\[\]\s*\)"
    r"\s*(?:\{\s*)?if\s*\(\s*\1\.type\s*===\s*[\"']image[\"']\s*\)\s*image\s*\(\s*\1\s*\)")
YIELDED_CELL = re.compile(r"^Script running with cell ID ([\w:-]+)\n")
TOOL_OUTPUT_TYPES = {"custom_tool_call_output", "function_call_output"}
SKILL_PATH = re.compile(r"(?<![\w/])((?:/|~/|\.{1,2}/)?[\w.@+-]+(?:/[\w.@+-]+)*/SKILL\.md)")
QUOTED_SKILL_PATH = re.compile(r"[\"'`]((?:/|~/|\.{1,2}/|[\w.@+-]+/)[^\"'`\n]*?/SKILL\.md)[\"'`]")
SKILL_NAME = re.compile(r"\$([a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]*)?)\b")
SKILL_EXCLUDES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".DS_Store",
                  ".internal", ".local", ".pytest_cache"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_progress(value):
    require(value is None or type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100,
            "Estimated progress must be a finite percentage between 0 and 100")


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_json(path, value):
    with open(path, "x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def safe_directory(path):
    """Reject linked ancestors instead of following a caller's archive path."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), f"Linked archive directory: {part}")
        require(not part.exists() or part.is_dir(), f"Not a directory: {part}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def archive_root(repo):
    repo = Path(repo).resolve()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    require(Path(git("rev-parse", "--show-toplevel")).resolve() == repo,
            "Archive root must be the owning Git checkout")
    require("/runs/" in (repo / ".gitignore").read_text().splitlines(),
            "Root .gitignore must contain /runs/")
    require(not git("ls-files", "--", "runs"), "Run archives must not contain tracked files")
    directory = safe_directory(repo / "runs")
    require(git("check-ignore", "runs/export-check/run.json"), "runs/ must be Git-ignored")
    return directory


def recorded_git(header):
    git = header.get("git") or {}
    require(isinstance(git, dict), "Invalid recorded Git metadata")
    commit = git.get("commit_hash")
    require(commit is None or isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit),
            "Invalid recorded commit ID")
    return {"commit_id": commit, "branch": git.get("branch"),
            "repository_url": git.get("repository_url"), "dirty": git.get("dirty"),
            "source": "session_meta.git" if commit else "unavailable",
            "scope": "Conversation checkout at session start; not proof of the deployed game revision."}


def exporter_git(repo):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    try:
        return {"commit_id": git("rev-parse", "HEAD"),
                "branch": git("branch", "--show-current") or None,
                "dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
                "source": "export_checkout", "scope": "Checkout at export time, not run startup."}
    except subprocess.CalledProcessError:
        return {"commit_id": None, "branch": None, "dirty": None, "source": "unavailable"}


def runtime_objects(value):
    """Unwrap objects returned by AgentSCUMM, including JSON text wrappers."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from runtime_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from runtime_objects(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except ValueError:
            return
        yield from runtime_objects(parsed)


def runtime_revisions(objects):
    for value in objects:
        revision = value.get("sourceRevision")
        if isinstance(revision, dict) and revision.get("repository") == "agentscumm":
            commit = revision.get("commit_id")
            require(commit is None or isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit),
                    "Invalid recorded AgentSCUMM commit ID")
            yield revision


def runtime_session(objects):
    for value in objects:
        session = value.get("sessionId")
        game, profile = value.get("game"), value.get("dataDir")
        if (isinstance(session, str)
                and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session)
                and isinstance(game, str) and isinstance(profile, str) and Path(profile).is_absolute()):
            return session, game, Path(profile)


def private_records(path):
    if path.is_file() and not any(part.is_symlink() for part in (path, *path.parents)):
        with path.open("rb") as stream:
            remaining = os.fstat(stream.fileno()).st_size
            while remaining:
                line = stream.readline(remaining)
                if not line.endswith(b"\n"):
                    break  # A live writer may still be appending this row.
                remaining -= len(line)
                yield json.loads(line)


def local_runtime_telemetry(repo, game, run_id, agent_context, work_dir, started, end):
    root = repo / ".internal" / game
    work = root / run_id
    if work_dir is not None and Path(work_dir).absolute().parent == root:
        work = Path(work_dir).absolute()
    elif agent_context:
        context_work = Path(agent_context).absolute().parent
        if context_work.parent == root:
            work = context_work
    latest = None
    sessions, sequences, revisions = set(), [], []
    for event in private_records(work / "events.jsonl"):
        when = USAGE.timestamp(event["timestamp"])
        if not started <= when <= end:
            continue
        result = event.get("result", {})
        if result.get("name") in ("session", "status") and not result.get("isError"):
            objects = list(runtime_objects(result))
            session = runtime_session(objects)
            if session and session[1:] == (game, work / "browser"):
                sessions.add(session[0])
                for revision in runtime_revisions(objects):
                    if revision not in revisions:
                        revisions.append(revision)
                if latest is None or when > latest[0]:
                    latest = when, session
        request = event.get("request")
        if not isinstance(request, dict) or request.get("name") != "act":
            continue
        args = request.get("arguments")
        if not isinstance(args, dict):
            continue
        session_id, steps = args.get("session_id"), args.get("actions")
        if (isinstance(session_id, str) and isinstance(steps, list)
                and all(isinstance(step, dict) and isinstance(step.get("type"), str) for step in steps)):
            sequences.append((session_id, [step["type"] for step in steps]))
    return latest, sessions, [steps for session_id, steps in sequences if session_id in sessions], revisions


def verified_run_progress(active, game, started, end):
    if not active:
        return None
    _, (session, observed_game, profile) = active
    if observed_game != game:
        return None
    launch_verified = False
    latest = None
    for row in private_records(profile / "runs" / f"{session}.jsonl"):
        if row.get("sessionId") != session:
            continue
        if row.get("type") == "launch":
            launch_verified = row.get("game") == game and row.get("dataDir") == str(profile)
        if launch_verified and row.get("type") == "progress":
            when = USAGE.timestamp(row["timestamp"])
            if started <= when <= end:
                latest = row
    if latest and latest["progress"]["state"] == "tracked":
        return {**latest["progress"], "session_id": session, "recorded_at": latest["timestamp"]}


def verify_run_progress(stats):
    progress = stats.get("verified_progress")
    if progress is None:
        return
    require(isinstance(progress, dict) and progress.get("state") == "tracked"
            and progress.get("game") == stats["game_id"]
            and progress.get("session_id") in stats.get("sessions", []), "Invalid verified progress identity")
    when = USAGE.timestamp(progress["recorded_at"])
    require(USAGE.timestamp(stats["started_at"]) <= when <= USAGE.timestamp(stats["recorded_through"]),
            "Verified progress is outside the archive cutoff")
    total, completed, reached = (progress.get(key) for key in ("total", "completed", "reached"))
    require(type(total) is int and type(completed) is int and 0 <= completed <= total <= 100 and total > 0
            and type(progress.get("percent")) is int
            and progress["percent"] == math.floor(completed * 100 / total + 0.5)
            and isinstance(reached, list) and len(reached) == completed
            and all(isinstance(item, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in reached)
            and len(set(reached)) == completed, "Invalid verified milestone count")
    label = progress.get("latest_milestone")
    require(label is None if completed == 0 else isinstance(label, str) and 0 < len(label.strip()) <= 200,
            "Invalid verified milestone label")


def sequence_stats(lengths):
    return {"count": len(lengths),
            "min_length": min(lengths) if lengths else None,
            "max_length": max(lengths) if lengths else None,
            "mean_length": statistics.fmean(lengths) if lengths else None,
            "std_length": statistics.pstdev(lengths) if lengths else None,
            "median_length": statistics.median(lengths) if lengths else None,
            "std_type": "population"}


def copy_snapshot(source, destination):
    require(source.is_file() and not source.is_symlink(), "Rollout must be a regular, unlinked file")
    with source.open("rb") as reader, destination.open("xb") as writer:
        os.chmod(destination, 0o600)
        remaining = os.fstat(reader.fileno()).st_size
        while remaining:
            chunk = reader.read(min(1024 * 1024, remaining))
            require(chunk, "Rollout shrank while snapshotting; retry")
            writer.write(chunk)
            remaining -= len(chunk)


def records(path):
    with path.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            try:
                yield number, json.loads(line)
            except (ValueError, UnicodeDecodeError):
                require(not line.endswith(b"\n") and not stream.read(1),
                        f"Malformed rollout JSON at line {number}")
                # An active rollout may end with an unfinished append. The usage helper
                # records the same exclusion explicitly; completed exports reject it.


def text_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_values(child)
    elif isinstance(value, str):
        yield value
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                return
            yield from text_values(parsed)


def skill_references(source, cwd):
    """Inspect active references, not passive catalogs, instructions or read results."""
    for number, row in records(source):
        payload = row.get("payload", {})
        if row.get("type") == "turn_context" and payload.get("cwd"):
            cwd = Path(payload["cwd"])
        selected = None
        allow_names = False
        if row.get("type") == "response_item":
            kind = payload.get("type")
            if kind in ("function_call", "custom_tool_call"):
                selected = payload.get("arguments", payload.get("input", ""))
            elif kind == "message" and payload.get("role") in ("assistant", "user"):
                content = list(text_values(payload.get("content", [])))
                if not any("<INSTRUCTIONS>" in text or "<skills_instructions>" in text
                           or text.startswith("# AGENTS.md instructions") for text in content):
                    selected = content
                    allow_names = True
        elif row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            selected = payload.get("item", {}).get("arguments")
        # Exec wrappers can target another checkout without changing the turn's cwd.
        working_dirs = set()
        selected_text = list(text_values(selected))
        for text in selected_text:
            for match in re.finditer(r'''(?:["']workdir["']|\bworkdir)\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', text):
                try:
                    working_dirs.add(ast.literal_eval(match[1]))
                except (ValueError, SyntaxError):
                    pass
        reference_cwd = next(iter(working_dirs)) if len(working_dirs) == 1 else str(cwd)
        found = set()
        for text in selected_text:
            quoted = list(QUOTED_SKILL_PATH.finditer(text))
            paths = [match[1] for match in quoted]
            paths += [match[1] for match in SKILL_PATH.finditer(text)
                      if not any(q.start() <= match.start() < q.end() for q in quoted)]
            for reference in paths:
                if ("path", reference) not in found:
                    found.add(("path", reference))
                    yield {"record": number, "kind": "path", "reference": reference, "cwd": reference_cwd}
            for match in SKILL_NAME.finditer(text) if allow_names else ():
                name = match[1]
                # Avoid interpreting ordinary shell variables as skill invocations.
                if (any(char in name for char in "_-:") or name == "imagegen") and ("name", name) not in found:
                    found.add(("name", name))
                    yield {"record": number, "kind": "name", "reference": name, "cwd": str(cwd)}


def snapshot_skills(source, staging, cwd, extra=()):
    references = list(skill_references(source, cwd))
    references += [{"record": None, "kind": "explicit", "reference": str(Path(path).expanduser().absolute()),
                    "cwd": str(cwd)} for path in extra]
    directory = safe_directory(staging / "skills")
    roots, unresolved = {}, []
    # Resolve names against explicit paths first, including paths to discovery symlinks.
    known, explicit = {}, {}
    for ref in references:
        if ref["kind"] != "name":
            path = Path(ref["reference"]).expanduser()
            path = path if path.is_absolute() else Path(ref["cwd"]) / path
            if path.is_file():
                resolved = path.resolve()
                for name in {path.parent.name, resolved.parent.name}:
                    known.setdefault(name, set()).add(resolved)
                    if ref["kind"] == "explicit":
                        explicit.setdefault(name, set()).add(resolved)
    for ref in references:
        if ref["kind"] == "name":
            name = ref["reference"]
            leaf = name.rsplit(":", 1)[-1]
            candidates = set(explicit.get(leaf) or known.get(leaf, ()))
            if not candidates:
                for base in (Path(ref["cwd"]) / ".agents/skills", Path(ref["cwd"]) / ".codex/skills",
                             Path.home() / ".agents/skills", Path.home() / ".codex/skills"):
                    for path in (base / leaf / "SKILL.md", *base.glob(f"*/{leaf}/SKILL.md")):
                        if path.is_file():
                            candidates.add(path.resolve())
            if len(candidates) != 1:
                unresolved.append({**ref, "reason": "Skill name is missing or ambiguous; supply --skill /path/to/SKILL.md."})
                continue
            path = candidates.pop()
        else:
            path = Path(ref["reference"]).expanduser()
            relative = not path.is_absolute()
            path = (path if not relative else Path(ref["cwd"]) / path).resolve()
            if relative and not path.is_file():
                # A relative documentation reference may share an explicitly recorded
                # absolute source path, even when the conversation lives elsewhere.
                candidates = {candidate for paths in known.values() for candidate in paths
                              if candidate.as_posix().endswith("/" + ref["reference"].removeprefix("./"))}
                if len(candidates) == 1:
                    path = candidates.pop()
                    ref = {**ref, "resolution": "unique_recorded_path_suffix"}
        if path.name != "SKILL.md" or not path.is_file():
            unresolved.append({**ref, "reason": "Referenced SKILL.md is unavailable."})
            continue
        roots.setdefault(path.parent, []).append(ref)
    entries = {}
    for root, refs in roots.items():
        files, omitted = [], []
        # Snapshot once into a temporary private bundle; never execute skill code.
        bundle = Path(tempfile.mkdtemp(prefix=".skill-", dir=directory))
        def walk_error(error):
            raise error
        for parent, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            for name in list(dirs):
                path = Path(parent) / name
                if name in SKILL_EXCLUDES:
                    dirs.remove(name)
                elif path.is_symlink():
                    omitted.append(path.relative_to(root).as_posix())
                    dirs.remove(name)
            for name in sorted(names):
                if name in SKILL_EXCLUDES or name.endswith((".pyc", ".pyo")):
                    continue
                path = Path(parent) / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink() or not path.is_file() or name.startswith((".env", ".access", ".infisical")):
                    omitted.append(relative)
                    continue
                target = safe_directory(bundle / Path(relative).parent) / name
                copy_snapshot(path, target)
                data = target.read_bytes()
                files.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "executable": bool(path.stat().st_mode & 0o111)})
        files.sort(key=lambda item: item["path"])
        require(any(item["path"] == "SKILL.md" for item in files), "Skill entrypoint disappeared while snapshotting")
        identity = "skill-" + hashlib.sha256(dump(files).encode()).hexdigest()
        if identity in entries:
            shutil.rmtree(bundle)
            entry = entries[identity]
            entry["source_paths"].append(str(root / "SKILL.md"))
            entry["references"].extend(refs)
        else:
            os.rename(bundle, directory / identity)
            entry = {"id": identity, "name": root.name, "source_paths": [str(root / "SKILL.md")],
                     "path": f"skills/{identity}", "files": files, "references": refs, "omitted": sorted(omitted)}
            entries[identity] = entry
        if omitted:
            unresolved.append({"reference": str(root / "SKILL.md"),
                               "reason": "Bundle contains excluded links, special or configuration files.", "omitted": sorted(omitted)})
    index = {"schema_version": 1, "provenance": "export_time_local_snapshot",
             "historical_version_verified": False,
             "skills": list(entries.values()), "unresolved": unresolved,
             "discovery": "Explicit paths and named invocations in messages/tool arguments; passive instruction catalogs and tool results excluded."}
    write_json(directory / "index.json", index)
    return {"index": "skills/index.json", "sha256": hashlib.sha256((directory / "index.json").read_bytes()).hexdigest(),
            "bundles": len(entries), "files": sum(len(e["files"]) for e in entries.values()),
            "references": len(references), "unresolved": unresolved,
            "provenance": index["provenance"], "historical_version_verified": False}


class Screenshots:
    def __init__(self, directory):
        self.directory = directory
        self.entries = {}
        self.references = 0
        self.unresolved = []
        self.frame_map = {}
        self.candidates = {}
        self.recovered = 0
        self.ordered = {}
        self.ordered_recovered = 0
        self.spanning = {}
        self.spanning_recovered = 0
        self.unforwarded = set()
        self.record_number, self.image_number = None, 0

    @staticmethod
    def fragment_matches(fragment, full):
        match = TRUNCATED.fullmatch(fragment) if isinstance(fragment, str) else None
        if not match:
            return fragment == full
        prefix, removed, suffix = match.groups()
        return len(full) == len(prefix) + int(removed) + len(suffix) and \
            full.startswith(prefix) and full.endswith(suffix)

    @staticmethod
    def native_images(value):
        if isinstance(value, dict):
            if value.get("type") == "image" and "data" in value:
                yield value
            else:
                for child in value.values():
                    yield from Screenshots.native_images(child)
        elif isinstance(value, list):
            for child in value:
                yield from Screenshots.native_images(child)
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                return
            yield from Screenshots.native_images(parsed)

    @staticmethod
    def selected_frames(value):
        if isinstance(value, dict):
            if type(value.get("selectedFrames")) is int:
                return value["selectedFrames"]
            for child in value.values():
                found = Screenshots.selected_frames(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = Screenshots.selected_frames(child)
                if found is not None:
                    return found
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                return None
            return Screenshots.selected_frames(parsed)
        return None

    def save(self, encoded, mime, frame=None, *, reference=True):
        require(mime in MIMES, f"Unsupported embedded image MIME: {mime}")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid embedded screenshot base64") from None
        require(data, "Empty embedded screenshot")
        digest = hashlib.sha256(data).hexdigest()
        screenshot_id = f"screenshot-{digest}"
        relative = f"screenshots/{screenshot_id}.{MIMES[mime]}"
        if screenshot_id not in self.entries:
            file = self.directory / Path(relative).name
            with file.open("xb") as stream:
                os.chmod(file, 0o600)
                stream.write(data)
            self.entries[screenshot_id] = {"id": screenshot_id, "path": relative,
                "mime_type": mime, "sha256": digest, "bytes": len(data),
                "references": 0, "frames": []}
            self.candidates.setdefault((mime, len(encoded)), set()).add(screenshot_id)
        entry = self.entries[screenshot_id]
        require(entry["mime_type"] == mime, "Identical image bytes have conflicting MIME types")
        if reference:
            entry["references"] += 1
            self.references += 1
        if frame:
            alias = {key: frame[key] for key in
                     ("id", "sessionId", "timestamp", "actionId", "step", "width", "height")
                     if key in frame}
            key = (alias.get("sessionId"), alias["id"])
            require(key not in self.frame_map or self.frame_map[key] == screenshot_id,
                    "A session/frame ID refers to conflicting screenshot bytes")
            self.frame_map[key] = screenshot_id
            if alias not in entry["frames"]:
                entry["frames"].append(alias)
        return {"id": screenshot_id, "path": relative, "sha256": digest}

    def prepare(self, source):
        """Index full copies before resolving truncated MCP item summaries."""
        def walk(value):
            if isinstance(value, dict):
                if value.get("type") == "image" and "data" in value:
                    if not isinstance(value["data"], str) or not TRUNCATED.fullmatch(value["data"]):
                        self.save(value["data"], value.get("mimeType"), reference=False)
                    return
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str):
                match = DATA_URL.fullmatch(value)
                if match:
                    self.save(match[2], match[1], reference=False)
                elif value.lstrip().startswith(("{", "[")):
                    try:
                        parsed = json.loads(value)
                    except ValueError:
                        return
                    walk(parsed)
        pending, yielded = None, {}
        for number, row in records(source):
            walk(row)
            payload = row.get("payload", {})
            if row.get("type") == "response_item" and payload.get("type") == "custom_tool_call":
                code = payload.get("input", "")
                forwarded = {m[2] for m in FORWARD_IMAGES.finditer(code)}
                calls = [(m[2], m[1] in forwarded) for m in ASSIGNED_CALL.finditer(code)]
                pending = {"call_id": payload.get("call_id"), "calls": calls, "groups": [],
                           "exec": payload.get("name") in ("exec", "functions.exec"), "results": []}
            elif row.get("type") == "response_item" and payload.get("type") == "function_call":
                pending = None
                if payload.get("name") in ("wait", "functions.wait"):
                    try:
                        arguments = json.loads(payload.get("arguments", ""))
                    except ValueError:
                        continue
                    if isinstance(arguments, dict) and isinstance(arguments.get("cell_id"), str):
                        pending = yielded.pop(arguments["cell_id"], None)
                        if pending:
                            pending["call_id"] = payload.get("call_id")
            elif row.get("type") == "event_msg" and pending:
                item = payload.get("item", {})
                if payload.get("type") != "item_completed" or item.get("type") != "McpToolCall":
                    continue
                images = list(self.native_images(item.get("result")))
                if pending["exec"] and images:
                    pending["results"].append((number, item, images))
                if item.get("server") == "agentscumm":
                    selected = next((i for i, call in enumerate(pending["calls"]) if call[0] == item.get("tool")), None)
                    if selected is not None:
                        _, forwarded = pending["calls"].pop(selected)
                        if forwarded:
                            expected = self.selected_frames(item.get("result"))
                            if expected is None:
                                expected = len(images)
                            pending["groups"].append((number, images, expected))
            elif (row.get("type") == "response_item" and payload.get("type") in TOOL_OUTPUT_TYPES
                  and pending and payload.get("call_id") == pending["call_id"]):
                blocks = payload.get("output", [])
                full, captions = [], []
                header = blocks if isinstance(blocks, str) else ""
                if isinstance(blocks, list):
                    previous = None
                    for block in blocks:
                        if isinstance(block, dict) and block.get("type") == "input_image":
                            url = block.get("image_url")
                            match = DATA_URL.fullmatch(url) if isinstance(url, str) else None
                            full.append((match[1], match[2]) if match else None)
                            captions.append(previous)
                        previous = block.get("text") if isinstance(block, dict) else None
                    if blocks and isinstance(blocks[0], dict):
                        header = blocks[0].get("text", "")
                if full and all(full):
                    groups = pending["groups"] or self.helper_forwarded_group(pending["results"], captions)
                    self.recover_forwarded_groups(groups, full)
                    forwarded = {number for number, _, _ in groups}
                    self.mark_unforwarded((entry for entry in pending["results"] if entry[0] not in forwarded), full)
                cell = YIELDED_CELL.match(header) if pending["exec"] and isinstance(header, str) else None
                if cell:
                    if full:
                        pending["groups"].clear()
                        pending["results"].clear()
                    yielded[cell[1]] = pending
                pending = None

    def mark_unforwarded(self, results, full):
        # A single native frame can be absent from the enclosing exec's complete
        # image output. A summary spanning several frames cannot establish that.
        for number, item, images in results:
            tool = item.get("tool")
            if item.get("server") != "agentscumm" or tool not in ("act", "observe") or len(images) != 1:
                continue
            result = item["result"]
            selected = self.selected_frames(result)
            frame = next((frame for obj in runtime_objects(result) if (frame := self.frame_metadata(obj))), None)
            if not frame:
                continue
            fresh_observation = tool == "observe" and selected is None and frame.get("source") == "fresh"
            if selected != 1 and not fresh_observation:
                continue
            image = images[0]
            fragment = image["data"]
            if isinstance(fragment, str) and TRUNCATED.fullmatch(fragment) and not any(
                image.get("mimeType") == mime and self.fragment_matches(fragment, encoded)
                for mime, encoded in full
            ):
                self.unforwarded.add((number, 0))

    def helper_forwarded_group(self, results, captions):
        # A helper call may hide the forwarding loop from the outer JS source.
        # Require one image-producing result and matching metadata before treating
        # its output order as evidence.
        if len(results) != 1:
            return []
        number, item, images = results[0]
        if item.get("server") != "agentscumm" or item.get("tool") not in ("act", "observe"):
            return []
        result = item["result"]
        expected = self.selected_frames(result)
        if expected != len(captions) or expected < 2:
            return []
        first = next((frame for obj in runtime_objects(result) if (frame := self.frame_metadata(obj))), None)
        if not first or not first.get("sessionId") or not first.get("game"):
            return []
        if captions[0] and all(caption is None for caption in captions[1:]):
            try:
                caption = json.loads(captions[0])
            except ValueError:
                return []
            if not isinstance(caption, dict) or caption.get("selectedFrames") != expected:
                return []
            for obj in runtime_objects(result):
                if obj.get("selectedFrames") != expected:
                    continue
                if item["tool"] == "act":
                    fields = ("actionId", "requestId")
                    action = caption.get("action", {})
                    if (isinstance(action, dict) and isinstance(obj.get("action"), dict)
                            and all(action.get(key) and action[key] == obj["action"].get(key) for key in fields)
                            and first.get("actionId") == action["actionId"]
                            and caption.get("screenshots") == obj.get("screenshots")):
                        return [(number, images, expected)]
                elif caption.get("source") == obj.get("source") and caption["source"] in ("frames", "history"):
                    return [(number, images, expected)]
            return []
        prefix = dump({"sessionId": first["sessionId"], "game": first["game"]})[:-1] + ","
        if (all(isinstance(caption, str) and caption.startswith(prefix) for caption in captions)
                and dump(first).startswith(captions[0])):
            return [(number, images, expected)]
        if item["tool"] == "act" and self.compact_act_captions(result, first, captions):
            return [(number, images, expected)]
        return []

    @staticmethod
    def compact_act_captions(result, first, captions):
        if not all(isinstance(caption, str) for caption in captions):
            return False
        try:
            metadata = [json.loads(caption) for caption in captions]
        except ValueError:
            return False
        if not all(isinstance(frame, dict) and set(frame) == {"frame", "width", "height"}
                   and isinstance(frame["frame"], str) and frame["frame"]
                   and all(type(frame[key]) is int and frame[key] > 0 for key in ("width", "height"))
                   for frame in metadata):
            return False
        frame_ids = [frame["frame"] for frame in metadata]
        if (len(set(frame_ids)) != len(frame_ids)
                or metadata[0] != {"frame": first["id"], "width": first["width"], "height": first.get("height")}):
            return False
        # Explicit screenshots precede the optional final capture in an act response.
        for obj in runtime_objects(result):
            action, screenshots = obj.get("action"), obj.get("screenshots")
            if (isinstance(action, dict) and first.get("actionId")
                    and first["actionId"] == action.get("actionId")
                    and obj.get("selectedFrames") == len(frame_ids) and isinstance(screenshots, list)
                    and len(screenshots) in (len(frame_ids), len(frame_ids) - 1)
                    and all(isinstance(shot, dict) and shot.get("frame_id") == identity
                            for shot, identity in zip(screenshots, frame_ids))):
                return True
        return False

    def recover_forwarded_groups(self, groups, full):
        if not groups or sum(group[2] for group in groups) != len(full):
            return
        offset = 0
        for record_number, images, expected in groups:
            selected = full[offset:offset + expected]
            offset += expected
            if len(images) == expected and all(
                image.get("mimeType") == mime and self.fragment_matches(image["data"], encoded)
                for image, (mime, encoded) in zip(images, selected)):
                for i, (mime, encoded) in enumerate(selected):
                    self.ordered[(record_number, i)] = self.save(encoded, mime, reference=False)["id"]
            elif len(images) == 1 and expected > 1:
                # A single truncated data field can span several original images.
                fragment = TRUNCATED.fullmatch(images[0]["data"])
                if (fragment and all(mime == images[0].get("mimeType") for mime, _ in selected)
                        and selected[0][1].startswith(fragment[1]) and selected[-1][1].endswith(fragment[3])):
                    self.spanning[(record_number, 0)] = [self.save(encoded, mime, reference=False)["id"]
                                                         for mime, encoded in selected]

    def recover(self, encoded, mime):
        match = TRUNCATED.fullmatch(encoded) if isinstance(encoded, str) else None
        if not match:
            return encoded, False
        ordered = self.ordered.get((self.record_number, self.image_number - 1))
        if ordered:
            entry = self.entries[ordered]
            full = base64.b64encode((self.directory / Path(entry["path"]).name).read_bytes()).decode()
            require(self.fragment_matches(encoded, full), "Ordered image recovery fragment mismatch")
            self.recovered += 1
            self.ordered_recovered += 1
            return full, True
        prefix, removed, suffix = match.groups()
        length = len(prefix) + int(removed) + len(suffix)
        matches = []
        for identity in self.candidates.get((mime, length), ()):
            entry = self.entries[identity]
            full = base64.b64encode((self.directory / Path(entry["path"]).name).read_bytes()).decode()
            if full.startswith(prefix) and full.endswith(suffix):
                matches.append(full)
        if len(matches) == 1:
            self.recovered += 1
            return matches[0], True
        return None, True

    @staticmethod
    def frame_metadata(value):
        if isinstance(value, dict):
            if value.get("type") in ("text", "input_text") and isinstance(value.get("text"), str):
                value = value["text"]
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                value = json.loads(value)
            except ValueError:
                return None
        if isinstance(value, dict) and isinstance(value.get("id"), str) and \
                value.get("mimeType", "").startswith("image/") and "width" in value:
            return value
        return None

    def transform(self, value, frame=None):
        if isinstance(value, list):
            result = []
            # MCP imageContent puts each frame's metadata immediately before its image.
            for item in value:
                result.append(self.transform(item, frame))
                frame = self.frame_metadata(item)
            return result
        if isinstance(value, dict):
            if value.get("type") == "image" and "data" in value:
                self.image_number += 1
                spanning = self.spanning.get((self.record_number, self.image_number - 1))
                if spanning:
                    refs = []
                    for i, identity in enumerate(spanning):
                        entry = self.entries[identity]
                        encoded = base64.b64encode((self.directory / Path(entry["path"]).name).read_bytes()).decode()
                        refs.append(self.save(encoded, entry["mime_type"], frame if i == 0 else None))
                    self.recovered += 1
                    self.spanning_recovered += 1
                    return {**{k: v for k, v in value.items() if k != "data"},
                        "screenshot": refs[0], "additional_screenshots": refs[1:],
                        "source_truncation": {"recorded_data": value["data"],
                            "reason": "Recorded tool text was truncated across multiple images; full images recovered from its ordered model-visible output. Intermediate metadata was not recorded."}}
                encoded, truncated = self.recover(value["data"], value.get("mimeType"))
                result = {k: v for k, v in value.items() if k != "data"}
                if encoded is None:
                    reason = "truncated image has no unique full copy in the rollout"
                    gap = {"reason": reason}
                    if (self.record_number, self.image_number - 1) in self.unforwarded:
                        gap["scope"] = "unforwarded_mcp_result"
                    self.unresolved.append({"type": "image", "frame_id": frame.get("id") if frame else None, **gap})
                    return {**result, "unavailable_image": {**gap, "recorded_data": value["data"]}}
                ref = self.save(encoded, value.get("mimeType"), frame)
                return {**result, "screenshot": ref, **({"source_image_truncated": True} if truncated else {})}
            if value.get("type") in ("input_image", "image_url") and "image_url" in value:
                url = value["image_url"]
                raw = url.get("url") if isinstance(url, dict) else url
                match = DATA_URL.fullmatch(raw) if isinstance(raw, str) else None
                if match:
                    ref = self.save(match[2], match[1], frame)
                    return {**{k: v for k, v in value.items() if k != "image_url"},
                            "screenshot": ref, "source_encoding": "data_url"}
                self.unresolved.append({"type": value["type"], "reason": "non-embedded image"})
            return {key: self.transform(item, frame) for key, item in value.items()}
        if isinstance(value, str):
            # MCP results also occur inside JSON-encoded text blocks. Preserve the
            # string wrapper, changing it only when it contains embedded images.
            if value.lstrip().startswith(("{", "[")):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    return value
                transformed = self.transform(decoded, frame)
                return dump(transformed) if transformed != decoded else value
            match = DATA_URL.fullmatch(value)
            if match:
                return self.save(match[2], match[1], frame)
        return value


def export_run(rollout, game_id, *, repo=ROOT, run_id=None, status="in_progress",
               completion_note=None, walkthroughs=(), skills=(), agent_context=None, estimated_progress=None,
               pricing_rates=None, work_dir=None):
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", game_id), "Invalid game ID")
    require(status in ("in_progress", "completed", "stopped", "blocked"), "Invalid run status")
    validate_progress(estimated_progress)
    require(status != "completed" or estimated_progress in (None, 100), "Completed runs require 100 percent progress")
    base = archive_root(repo)
    parent = safe_directory(base / game_id)
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=parent))
    os.chmod(staging, 0o700)
    lock, acquired = None, False
    try:
        source = staging / ".source.jsonl"
        copy_snapshot(Path(rollout), source)
        header = next((r["payload"] for _, r in records(source) if r.get("type") == "session_meta"), None)
        require(header and header.get("id"), "Missing Codex session header")
        git_info = recorded_git(header)
        thread_id = header["id"]
        usage = USAGE.snapshot(source, thread_id)
        started = USAGE.timestamp(header["timestamp"])
        date = started.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%Y%m%d")
        run_id = run_id or thread_id
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", run_id), "Invalid run ID")
        destination = parent / f"{date}-{run_id}"
        lock = parent / f".{destination.name}.lock"
        with lock.open("x") as stream:
            os.chmod(lock, 0o600)
            stream.write(str(os.getpid()))
        acquired = True
        require(not destination.exists() and not destination.is_symlink(), "Run archive already exists")
        skill_info = snapshot_skills(source, staging, Path(header.get("cwd") or repo), skills)
        screenshots = Screenshots(safe_directory(staging / "screenshots"))
        screenshots.prepare(source)
        counts, tools, actions, contexts = Counter(), Counter(), Counter(), []
        sequence_lengths = []
        seen_tools, sessions, observed_games, revisions = set(), set(), set(), []
        attached_revisions, attached_actions = {}, set()
        active_session = None
        last_task, end, source_records, model_images, tool_seconds = None, started, 0, 0, 0.0
        trajectory = staging / "trajectory.jsonl"
        with trajectory.open("x", encoding="utf-8") as output:
            os.chmod(trajectory, 0o600)
            for number, record in records(source):
                source_records += 1
                counts[record.get("type", "unknown")] += 1
                when = USAGE.timestamp(record["timestamp"])
                end = max(end, when)
                payload = record.get("payload", {})
                if record.get("type") == "turn_context":
                    info = {"model": payload.get("model"), "reasoning_effort": payload.get("effort"),
                            "turn_id": payload.get("turn_id")}
                    if info not in contexts:
                        contexts.append(info)
                if record.get("type") == "event_msg":
                    kind = payload.get("type")
                    if kind in ("task_started", "task_complete", "turn_aborted"):
                        last_task = kind
                    item = payload.get("item", {})
                    if kind == "item_completed" and item.get("type") == "McpToolCall":
                        identity = (item.get("id"), payload.get("turn_id"))
                        if identity not in seen_tools:
                            seen_tools.add(identity)
                            tools[f"{item.get('server')}.{item.get('tool')}"] += 1
                            tool_seconds += max(0, payload.get("completed_at_ms", 0) -
                                                payload.get("started_at_ms", 0)) / 1000
                            args = item.get("arguments", {})
                            if item.get("server") == "agentscumm":
                                objects = list(runtime_objects(item.get("result", {})))
                                reported_revisions = list(runtime_revisions(objects))
                                for revision in reported_revisions:
                                    if revision not in revisions:
                                        revisions.append(revision)
                                if item.get("tool") in ("status", "session", "launch_game"):
                                    session = runtime_session(objects)
                                    if session:
                                        sessions.add(session[0])
                                        active_session = when, session
                                        if session[1] == game_id and reported_revisions:
                                            known = attached_revisions.setdefault(session[0], [])
                                            known.extend(r for r in reported_revisions if r not in known)
                                if args.get("session_id"):
                                    sessions.add(args["session_id"])
                                if (item.get("tool") == "launch_game" or
                                        (item.get("tool") == "session" and args.get("operation") == "start")) and args.get("game"):
                                    observed_games.add(args["game"])
                                if item.get("tool") == "act":
                                    if args.get("session_id"):
                                        attached_actions.add(args["session_id"])
                                    sequence_lengths.append(len(args.get("actions", [])))
                                    actions.update(a["type"] for a in args.get("actions", []))
                if record.get("type") == "response_item" and payload.get("type") in TOOL_OUTPUT_TYPES:
                    blocks = payload.get("output", [])
                    if isinstance(blocks, list):
                        model_images += sum(isinstance(b, dict) and b.get("type") == "input_image" for b in blocks)
                screenshots.record_number, screenshots.image_number = number, 0
                output.write(dump(screenshots.transform(record)) + "\n")
        require(not observed_games or observed_games == {game_id},
                "Rollout contains a different game; select a single-game conversation")
        local_session, local_sessions, local_sequences, local_revisions = local_runtime_telemetry(
            Path(repo).resolve(), game_id, run_id, agent_context, work_dir, started, end)
        gameplay_revisions = []
        for session_id, reported_revisions in attached_revisions.items():
            if session_id in attached_actions or (not attached_actions and not local_sessions):
                gameplay_revisions.extend(r for r in reported_revisions if r not in gameplay_revisions)
        gameplay_revisions.extend(r for r in local_revisions if r not in gameplay_revisions)
        sessions.update(local_sessions)
        for steps in local_sequences:
            sequence_lengths.append(len(steps))
            actions.update(steps)
        progress = None
        if status != "completed":
            if local_session and (not active_session or local_session[0] > active_session[0]):
                active_session = local_session
            progress = verified_run_progress(active_session, game_id, started, end)
        if status == "completed":
            require(last_task == "task_complete", "Conversation is still active or interrupted; cannot finalize")
            require(isinstance(completion_note, str) and completion_note.strip(),
                    "Completed runs require a note describing observed ending evidence")
            require(not usage["warnings"], "Completed runs require a complete source snapshot")
            require(all(gap.get("scope") == "unforwarded_mcp_result" for gap in screenshots.unresolved),
                    "Completed runs contain unavailable image references")
            require(screenshots.entries, "Completed runs require screenshot evidence")
            require(not skill_info["unresolved"], "Completed runs contain unavailable skill references")
        index = {"schema_version": 1, "screenshots": list(screenshots.entries.values())}
        write_json(staging / "screenshots/index.json", index)
        totals = usage["totals"]
        tokens = {**totals, "uncached_input_tokens": totals["input_tokens"] - totals["cached_input_tokens"],
                  "uncached_input_plus_output_tokens": totals["input_tokens"] - totals["cached_input_tokens"] + totals["output_tokens"],
                  "request_count": len(usage["requests"]), "usage_as_of": usage["usage_as_of"],
                  "coverage": usage["coverage"], "by_model_and_effort": usage["groups"]}
        stats = {"schema_version": 3, "game_id": game_id, "run_id": run_id,
            "thread_id": thread_id, "status": status, "completion_note": completion_note,
            "estimated_progress_percent": 100 if status == "completed" else estimated_progress,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started.isoformat(), "recorded_through": end.isoformat(),
            "wall_clock_seconds": (end - started).total_seconds(),
            "harness": {"name": header.get("originator"), "version": header.get("cli_version"),
                        "source": header.get("source"), "model_provider": header.get("model_provider")},
            "git": {"conversation": git_info, "agentscumm_runtime": revisions,
                    "exporter": exporter_git(repo)},
            "models": sorted({r["model"] for r in usage["requests"]}),
            "reasoning_levels": sorted({r.get("effort") for r in usage["requests"] if r.get("effort")}),
            "tokens": tokens, "api_cost": PRICING.collect(USAGE, usage, pricing_rates),
            "sessions": sorted(sessions),
            "screenshots": {"unique_files": len(screenshots.entries),
                "references_in_trajectory": screenshots.references, "model_visible_blocks": model_images,
                "original_frame_ids": len(screenshots.frame_map),
                "recovered_truncated_references": screenshots.recovered,
                "recovered_by_validated_forwarding_order": screenshots.ordered_recovered,
                "recovered_spanning_tool_summaries": screenshots.spanning_recovered,
                "bytes": sum(x["bytes"] for x in screenshots.entries.values()),
                "index": "screenshots/index.json", "unresolved": screenshots.unresolved},
            "skills": skill_info,
            "agent_context": CONTEXT.snapshot(trajectory, staging, agent_context),
            "actions": dict(actions), "mcp_tool_calls": dict(tools),
            "total_actions": sum(actions.values()),
            "total_action_sequences": len(sequence_lengths),
            "action_sequences": sequence_stats(sequence_lengths),
            "summed_mcp_call_seconds": tool_seconds, "model_contexts": contexts,
            "walkthrough_urls": list(walkthroughs),
            "source": {"format": "codex-rollout-jsonl", "sha256": usage["source"]["sha256"],
                       "bytes": usage["source"]["bytes"], "records": source_records,
                       "thread_id": thread_id},
            "trajectory": {"path": "trajectory.jsonl", "format": "codex-rollout-external-images-v1",
                "records": source_records, "bytes": trajectory.stat().st_size,
                "sha256": hashlib.sha256(trajectory.read_bytes()).hexdigest()},
            "warnings": usage["warnings"],
            "limitations": ["One conversation; other chats and agents are excluded.",
                "All available rollout records are preserved; hidden reasoning is not decrypted.",
                "Skill bundles are export-time local copies, not verified historical versions; passive catalogs are excluded.",
                "Screenshots are those embedded in the conversation, not every periodic MCP buffer frame.",
                "Wall time includes pauses; summed tool time can overlap across concurrent calls.",
                "Cached input is a subset of input; reasoning is a subset of output."]}
        if gameplay_revisions or local_sessions:
            stats["git"]["agentscumm_gameplay_runtime"] = gameplay_revisions
        if not revisions and not local_revisions:
            stats["limitations"].append("AgentSCUMM's startup commit was not recorded for this run; the exporter checkout is not a substitute.")
        if any(gap.get("scope") == "unforwarded_mcp_result" for gap in screenshots.unresolved):
            stats["limitations"].append("Some unforwarded MCP screenshots survive only as truncated rollout fragments.")
        if progress:
            stats["verified_progress"] = progress
        if local_sequences:
            stats["local_mcp_action_sequences"] = len(local_sequences)
        write_json(staging / "run.json", stats)
        verify_run(staging)
        source.unlink()
        os.rename(staging, destination)
        return destination, stats
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if acquired:
            # Only remove a lock acquired by this process.
            try:
                if lock.read_text() == str(os.getpid()):
                    lock.unlink()
            except FileNotFoundError:
                pass


def verify_run(directory):
    directory = Path(directory)
    require(not directory.is_symlink(), "Linked run directory")
    metadata = directory / "run.json"
    if not metadata.exists() and not metadata.is_symlink():
        metadata = directory / "stats.json"
    require(not metadata.is_symlink(), "Linked run metadata")
    stats = json.loads(metadata.read_text())
    require(stats.get("schema_version") in (1, 2, 3), "Unsupported run metadata schema")
    if stats["schema_version"] >= 2:
        require("agent_context" in stats and "skills" in stats, "Missing version 2 run metadata")
    if stats["schema_version"] >= 3:
        require("api_cost" in stats, "Missing version 3 pricing metadata")
    if "estimated_progress_percent" in stats:
        validate_progress(stats["estimated_progress_percent"])
        require(stats["status"] != "completed" or stats["estimated_progress_percent"] in (None, 100),
                "Completed runs require 100 percent progress")
    verify_run_progress(stats)
    if "total_actions" in stats or "total_action_sequences" in stats:
        for key, expected in (("total_actions", sum(stats["actions"].values())),
                              ("total_action_sequences", stats["action_sequences"]["count"])):
            require(type(stats.get(key)) is int and stats[key] >= 0 and stats[key] == expected,
                    f"{key} count mismatch")
    if "local_mcp_action_sequences" in stats:
        local_count = stats["local_mcp_action_sequences"]
        require(type(local_count) is int and 0 < local_count <= stats.get("total_action_sequences", 0),
                "Invalid local MCP action sequence count")
    trajectory = directory / "trajectory.jsonl"
    require(not trajectory.is_symlink(), "Linked trajectory")
    require(hashlib.sha256(trajectory.read_bytes()).hexdigest() == stats["trajectory"]["sha256"],
            "Trajectory checksum mismatch")
    index = json.loads((directory / "screenshots/index.json").read_text())
    images = {entry["id"]: entry for entry in index["screenshots"]}
    require(len(images) == len(index["screenshots"]) == stats["screenshots"]["unique_files"],
            "Screenshot count/ID mismatch")
    for identity, entry in images.items():
        require(re.fullmatch(r"screenshot-[0-9a-f]{64}", identity), "Invalid screenshot ID")
        require(entry["path"] == f"screenshots/{identity}.{MIMES[entry['mime_type']]}",
                "Invalid screenshot path")
        path = directory / entry["path"]
        require(not path.is_symlink() and not path.parent.is_symlink(), "Linked screenshot")
        data = path.read_bytes()
        require(len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"] == identity[11:],
                "Screenshot checksum mismatch")
    refs = Counter()
    unforwarded_fragments = 0
    def walk(value):
        nonlocal unforwarded_fragments
        if isinstance(value, dict):
            gap = value.get("unavailable_image")
            if isinstance(gap, dict) and gap.get("scope") == "unforwarded_mcp_result":
                fragment = gap.get("recorded_data")
                require(isinstance(fragment, str) and TRUNCATED.fullmatch(fragment),
                        "Missing unforwarded screenshot fragment")
                unforwarded_fragments += 1
            if {"id", "path", "sha256"} <= value.keys() and str(value["id"]).startswith("screenshot-"):
                require(value["id"] in images, "Missing screenshot reference")
                entry = images[value["id"]]
                require(value["path"] == entry["path"] and value["sha256"] == entry["sha256"],
                        "Screenshot reference mismatch")
                refs[value["id"]] += 1
            require(not (value.get("type") == "image" and "data" in value), "Embedded image remains")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            require(not DATA_URL.fullmatch(value), "Embedded image data URL remains")
            if value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    return
                walk(parsed)
    count = 0
    for _, record in records(trajectory):
        count += 1
        walk(record)
    require(count == stats["trajectory"]["records"] == stats["source"]["records"], "Record count mismatch")
    require(sum(refs.values()) == stats["screenshots"]["references_in_trajectory"], "Screenshot reference count mismatch")
    require(all(refs[key] == entry["references"] for key, entry in images.items()), "Unreferenced screenshot or wrong reference count")
    unresolved = stats["screenshots"].get("unresolved", [])
    require(unforwarded_fragments == sum(gap.get("scope") == "unforwarded_mcp_result" for gap in unresolved),
            "Unforwarded screenshot fragment count mismatch")
    require(stats["status"] != "completed" or len(unresolved) == unforwarded_fragments,
            "Completed runs contain unavailable image references")
    if "skills" in stats:
        verify_skills(directory, stats["skills"])
    if "agent_context" in stats:
        CONTEXT.verify(directory, stats["agent_context"])
    if "api_cost" in stats:
        measured = USAGE.snapshot(trajectory, stats["thread_id"])
        require(all(stats["tokens"].get(key) == value for key, value in measured["totals"].items())
                and stats["tokens"]["by_model_and_effort"] == measured["groups"]
                and stats["tokens"]["request_count"] == len(measured["requests"])
                and stats["tokens"]["usage_as_of"] == measured["usage_as_of"],
                "Archived token usage does not match trajectory")
        cost = stats["api_cost"]
        require(cost.get("provider") == measured["provider"] and cost.get("usage_as_of") == measured["usage_as_of"],
                "Pricing provider or usage cutoff mismatch")
        if cost.get("status") == "priced":
            require(cost == PRICING.calculate(USAGE, measured, cost["rates"]),
                    "Archived API price does not match telemetry and rates")
        else:
            require(cost.get("status") == "unavailable" and cost.get("total_api_price_usd") is None
                    and isinstance(cost.get("reason"), str) and cost["reason"],
                    "Invalid unavailable pricing metadata")
    return stats


def verify_skills(directory, summary):
    require(summary["index"] == "skills/index.json", "Invalid skill index path")
    root = directory / "skills"
    require(not root.is_symlink(), "Linked skills directory")
    index_path = root / "index.json"
    require(not index_path.is_symlink(), "Linked skill index")
    data = index_path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == summary["sha256"], "Skill index checksum mismatch")
    index = json.loads(data)
    require(index.get("schema_version") == 1, "Unsupported skill index schema")
    require(index["unresolved"] == summary["unresolved"], "Skill coverage mismatch")
    require(index["provenance"] == summary["provenance"] == "export_time_local_snapshot"
            and index["historical_version_verified"] is summary["historical_version_verified"] is False,
            "Skill provenance mismatch")
    identities, file_count, reference_count = set(), 0, len([r for r in index["unresolved"] if "kind" in r])
    for entry in index["skills"]:
        identity = entry["id"]
        require(re.fullmatch(r"skill-[0-9a-f]{64}", identity) and identity not in identities, "Invalid or duplicate skill ID")
        identities.add(identity)
        require(entry["path"] == f"skills/{identity}", "Invalid skill bundle path")
        require(identity == "skill-" + hashlib.sha256(dump(entry["files"]).encode()).hexdigest(), "Skill manifest checksum mismatch")
        bundle = root / identity
        require(bundle.is_dir() and not bundle.is_symlink(), "Missing or linked skill bundle")
        expected = set()
        for item in entry["files"]:
            relative = item["path"]
            parts = relative.split("/")
            require(relative and not any(part in ("", ".", "..") for part in parts) and "\\" not in relative,
                    "Invalid skill file path")
            require(relative not in expected, "Duplicate skill file")
            expected.add(relative)
            path = bundle
            for part in parts:
                path = path / part
                require(not path.is_symlink(), "Linked skill file or directory")
            require(path.is_file(), "Missing skill file")
            data = path.read_bytes()
            require(len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"],
                    "Skill file checksum mismatch")
            require(type(item["executable"]) is bool, "Invalid skill executable flag")
        require("SKILL.md" in expected, "Missing skill entrypoint")
        actual = set()
        for path in bundle.rglob("*"):
            require(not path.is_symlink(), "Linked skill file or directory")
            if path.is_file():
                actual.add(path.relative_to(bundle).as_posix())
            else:
                require(path.is_dir(), "Special file in skill bundle")
        require(actual == expected, "Unexpected or missing skill files")
        file_count += len(expected)
        reference_count += len(entry["references"])
    require(len(identities) == summary["bundles"] and file_count == summary["files"]
            and reference_count == summary["references"], "Skill counts mismatch")
    require({path.name for path in root.iterdir()} == identities | {"index.json"}, "Unexpected skill bundles")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="Verify an existing run archive instead of exporting")
    parser.add_argument("--rollout", type=Path)
    parser.add_argument("--game-id")
    parser.add_argument("--run-id", help="Defaults to the source conversation UUID")
    parser.add_argument("--status", choices=("in_progress", "completed", "stopped", "blocked"), default="in_progress")
    parser.add_argument("--completion-note", help="Observed ending evidence; required with completed")
    parser.add_argument("--estimated-progress", type=float, help="Declared estimated progress percentage for an unfinished run (0–100)")
    parser.add_argument("--walkthrough-url", action="append", default=[])
    parser.add_argument("--skill", type=Path, action="append", default=[],
                        help="Additional SKILL.md to bundle (repeatable); resolves unavailable named references")
    parser.add_argument("--agent-context", type=Path,
                        help="JSON declaration of initial tool availability and additional injected prompt assets")
    parser.add_argument("--pricing-rates", type=Path,
                        help="Official rates JSON verified today; defaults to a fresh public pricing lookup")
    args = parser.parse_args()
    try:
        if args.verify:
            stats = verify_run(args.verify)
            print(dump({"verified": True, "path": str(args.verify), "status": stats["status"],
                        "screenshots": stats["screenshots"]["unique_files"]}))
        else:
            require(args.rollout and args.game_id, "Provide --rollout and --game-id")
            destination, stats = export_run(args.rollout, args.game_id, run_id=args.run_id,
                status=args.status, completion_note=args.completion_note, walkthroughs=args.walkthrough_url,
                skills=args.skill, agent_context=args.agent_context, estimated_progress=args.estimated_progress,
                pricing_rates=args.pricing_rates)
            print(dump({"path": str(destination), "status": stats["status"], "tokens": stats["tokens"]["total_tokens"],
                        "api_cost_status": stats["api_cost"]["status"],
                        "total_api_price_usd": stats["api_cost"]["total_api_price_usd"],
                        "screenshots": stats["screenshots"]["unique_files"], "verified": True}))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Export failed: {error}\n")


if __name__ == "__main__":
    main()
