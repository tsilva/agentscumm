"""Inventory recorded agent capabilities and initial prompt assets without running them."""

import hashlib
import json
import os
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def fingerprint(value):
    data = encoded(value)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def tool_name(name):
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][\w.:-]*", name), "Invalid tool name")
    if name.startswith("mcp__"):
        return "mcp." + name[5:].replace("__", ".")
    return name.replace("__", ".")


def category(name):
    return "mcp" if name.startswith("mcp.") else "builtin"


def asset_kinds(value, role):
    text = value.get("text", "") if isinstance(value, dict) else str(value)
    kinds = []
    for marker, kind in (("<skills_instructions>", "skill_catalog"), ("### Available skills", "skill_catalog"),
                         ("<environment_context>", "environment"), ("<app-context>", "host_context"),
                         ("<INSTRUCTIONS>", "instructions"), ("# AGENTS.md instructions", "instructions")):
        if marker in text and kind not in kinds:
            kinds.append(kind)
    if isinstance(value, dict) and value.get("type") not in (None, "input_text", "output_text", "text"):
        kinds.append(value["type"])
    return kinds or ["instructions" if role in ("system", "developer") else "user_prompt"]


def snapshot(trajectory, directory, supplied=None):
    assets, initial, observed, declared = [], [], {}, None
    boundary, active = None, True
    seen_initial = set()

    def available(name, provenance, record=None, definition=None):
        name = tool_name(name)
        identity = (name, provenance, record)
        if identity not in seen_initial:
            seen_initial.add(identity)
            entry = {"name": name, "kind": category(name), "provenance": provenance, "record": record}
            if definition is not None:
                entry["definition"] = definition
                if isinstance(definition, dict) and definition.get("kind") in ("mcp", "builtin"):
                    entry["kind"] = definition["kind"]
            initial.append(entry)

    def observe(name, record, evidence):
        name = tool_name(name)
        entry = observed.setdefault(name, {"name": name, "kind": category(name), "records": [], "evidence": []})
        if record not in entry["records"]:
            entry["records"].append(record)
        if evidence not in entry["evidence"]:
            entry["evidence"].append(evidence)

    with trajectory.open() as stream:
        for number, line in enumerate(stream, 1):
            row = json.loads(line)
            payload = row.get("payload", {})
            kind = payload.get("type")
            is_response = row.get("type") == "response_item"
            if active and ((is_response and (kind in ("function_call", "custom_tool_call", "reasoning")
                          or kind == "message" and payload.get("role") == "assistant"))
                          or row.get("type") == "event_msg" and kind in ("agent_reasoning", "agent_message")):
                boundary, active = number, False
            if active:
                values = []
                if row.get("type") == "session_meta" and payload.get("base_instructions"):
                    values.append(("/payload/base_instructions", "system", payload["base_instructions"]))
                if is_response and kind == "message" and payload.get("role") in ("system", "developer", "user"):
                    for i, block in enumerate(payload.get("content", [])):
                        values.append((f"/payload/content/{i}", payload["role"], block))
                for pointer, role, value in values:
                    assets.append({"record": number, "pointer": pointer, "role": role,
                                   "kinds": asset_kinds(value, role), "provenance": "recorded_initial_prompt",
                                   "representation": "archived_json_value", **fingerprint(value)})
                    text = value.get("text", "") if isinstance(value, dict) else str(value)
                    # Only schema declarations count as availability evidence, never prose mentions.
                    sections = re.split(r"^## Namespace: ([\w.]+)\s*$", text, flags=re.M)
                    for i in range(1, len(sections), 2):
                        namespace, body = sections[i:i + 2]
                        for name in re.findall(r"^type (\w+)\s*=", body, re.M):
                            available(f"{namespace}.{name}", "recorded_initial_schema", number)
                if row.get("type") in ("session_meta", "turn_context"):
                    for key in ("tools", "available_tools", "dynamic_tools"):
                        definitions = payload.get(key, [])
                        if not isinstance(definitions, list):
                            continue
                        for definition in definitions:
                            value = definition.get("function", definition) if isinstance(definition, dict) else definition
                            name = value.get("name") if isinstance(value, dict) else value
                            if name:
                                available(name, f"recorded_initial_{key}", number, definition)
            if is_response and kind in ("function_call", "custom_tool_call"):
                if payload.get("name"):
                    observe(payload["name"], number, "recorded_call")
                code = payload.get("input", payload.get("arguments", ""))
                if payload.get("name") in ("exec", "functions.exec") and isinstance(code, str):
                    for name in sorted(set(re.findall(r"\btools\.([A-Za-z_]\w*)\s*\(", code))):
                        observe(name, number, "reference_in_submitted_exec_code")
            if row.get("type") == "event_msg" and kind == "item_completed":
                item = payload.get("item", {})
                if item.get("type") == "McpToolCall" and item.get("server") and item.get("tool"):
                    observe(f"mcp.{item['server']}.{item['tool']}", number, "recorded_mcp_call")

    complete = False
    if supplied is not None:
        supplied = Path(supplied)
        require(supplied.is_file() and not supplied.is_symlink(), "Agent context must be a regular, unlinked JSON file")
        data = supplied.read_bytes()
        declared = json.loads(data)
        require(isinstance(declared, dict) and declared.get("schema_version") == 1,
                "Unsupported supplied agent context schema")
        tools = declared.get("tools", {})
        require(isinstance(tools, dict) and isinstance(tools.get("available_at_start", []), list)
                and isinstance(declared.get("prompt_assets", []), list), "Invalid supplied agent context inventory")
        complete = tools.get("inventory_complete", False)
        require(type(complete) is bool, "Tool inventory_complete must be boolean")
        for definition in tools.get("available_at_start", []):
            require(isinstance(definition, dict) and isinstance(definition.get("name"), str)
                    and definition.get("kind") in ("mcp", "builtin"),
                    "Declared tools require name and kind (mcp or builtin)")
            available(definition["name"], "operator_declared_initial", definition=definition)
        for item in declared.get("prompt_assets", []):
            require(isinstance(item, dict) and isinstance(item.get("name"), str)
                    and isinstance(item.get("kind"), str) and isinstance(item.get("path"), str),
                    "Declared prompt assets require name, kind and path")
            source = Path(item["path"]).expanduser()
            source = source if source.is_absolute() else supplied.parent / source
            require(source.is_file() and not source.is_symlink(), "Prompt asset must be a regular, unlinked file")
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            target = directory / "context" / f"asset-{digest}"
            target.parent.mkdir(mode=0o700, exist_ok=True)
            if not target.exists():
                with target.open("xb") as output:
                    os.chmod(target, 0o600)
                    output.write(data)
            assets.append({"name": item["name"], "kinds": [item["kind"]], "path": f"context/asset-{digest}",
                           "sha256": digest, "bytes": len(data), "provenance": "operator_declared_initial",
                           "snapshot_provenance": "export_time_local_snapshot", "historical_version_verified": False})
    return {"schema_version": 1, "initial_prompt_boundary": {"before_record": boundary,
            "basis": "first_recorded_agent_response" if boundary else "end_of_snapshot"},
            "tools": {"available_at_start": initial, "inventory_complete": complete,
                      "coverage": "operator_declared_complete" if complete else "partial" if initial else "unavailable",
                      "observed_during_run": sorted(observed.values(), key=lambda entry: entry["name"])},
            "prompt_assets": assets,
            "limitations": ["Observed calls and submitted code references do not establish initial availability.",
                            "Initial prompt assets cover recorded pre-response inputs only; omitted host inputs remain unknown.",
                            "Operator-supplied availability and injection claims are declarations, not historical verification.",
                            "Submitted exec code can contain conditional or unexecuted tool references."]}


def verify(directory, context):
    require(context.get("schema_version") == 1, "Unsupported agent context schema")
    expected = snapshot(directory / "trajectory.jsonl", directory)
    tools = context["tools"]
    require(type(tools["inventory_complete"]) is bool, "Invalid tool inventory completeness")
    coverage = ("operator_declared_complete" if tools["inventory_complete"] else
                "partial" if tools["available_at_start"] else "unavailable")
    require(tools["coverage"] == coverage, "Tool inventory coverage mismatch")
    recorded_tools = [t for t in tools["available_at_start"] if t["provenance"] != "operator_declared_initial"]
    require(recorded_tools == expected["tools"]["available_at_start"]
            and tools["observed_during_run"] == expected["tools"]["observed_during_run"],
            "Recorded tool inventory mismatch")
    require(context["initial_prompt_boundary"] == expected["initial_prompt_boundary"], "Initial prompt boundary mismatch")
    recorded_assets = [a for a in context["prompt_assets"] if a["provenance"] == "recorded_initial_prompt"]
    require(recorded_assets == expected["prompt_assets"], "Initial prompt asset checksum or inventory mismatch")
    for asset in context["prompt_assets"]:
        if asset["provenance"] != "recorded_initial_prompt":
            require(asset["provenance"] == "operator_declared_initial", "Invalid prompt asset provenance")
            require(asset["path"] == f"context/asset-{asset['sha256']}"
                    and re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]), "Invalid prompt asset path")
            path = directory / asset["path"]
            require(not path.is_symlink() and not path.parent.is_symlink(), "Linked prompt asset")
            data = path.read_bytes()
            require(hashlib.sha256(data).hexdigest() == asset["sha256"] and len(data) == asset["bytes"],
                    "Prompt asset checksum mismatch")
