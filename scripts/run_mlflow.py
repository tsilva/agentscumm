"""Project a verified AgentSCUMM archive into an MLflow tracking experiment."""

import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit


CONFIG = Path(".local/mlflow.json")
TOKEN_METRICS = (
    "total_tokens", "input_tokens", "output_tokens", "cached_input_tokens", "uncached_input_tokens",
    "reasoning_output_tokens", "cache_write_input_tokens", "uncached_input_plus_output_tokens", "request_count",
)
ACTION_TYPES = frozenset(("click", "move", "key", "wait", "pause", "resume", "wait_ticks",
                          "save", "load", "screenshot", "drag"))


class TrackingError(ValueError):
    pass


class TrackingUnavailable(TrackingError):
    pass


def load_config(repo):
    path = repo / CONFIG
    settings = {}
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise TrackingError("MLflow configuration must be an unlinked file")
        try:
            settings = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise TrackingError("Invalid local MLflow configuration") from error
        if (not isinstance(settings, dict)
                or settings.keys() - {"tracking_uri", "ssh_host", "experiment"}
                or not all(isinstance(value, str) for value in settings.values())):
            raise TrackingError("Invalid local MLflow configuration fields")
    uri = os.environ.get("MLFLOW_TRACKING_URI", settings.get("tracking_uri", "")).rstrip("/")
    if not uri:
        return None
    host = os.environ.get("AGENTSCUMM_MLFLOW_SSH_HOST", settings.get("ssh_host", ""))
    experiment = os.environ.get("MLFLOW_EXPERIMENT_NAME", settings.get("experiment", "AgentSCUMM"))
    try:
        parsed = urlsplit(uri)
        valid_url = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                     and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                     and not re.search(r"[\s\\]", uri))
    except ValueError:
        valid_url = False
    if (not valid_url or not experiment.strip()
            or (host and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host))
            or ((host or parsed.scheme == "http") and parsed.hostname not in {"127.0.0.1", "::1", "localhost"})):
        raise TrackingError("Invalid MLflow endpoint, SSH host, or experiment")
    return uri, host, experiment


class Tracker:
    def __init__(self, config):
        self.uri, self.host, self.experiment = config

    def request(self, method, route, *, query=None, body=None, artifact=None, allowed=()):
        url = self.uri + "/api/2.0/" + route
        if query:
            url += "?" + urlencode(query)
        command = ["curl", "-q", "--silent", "--show-error", "--max-time", "10", "--globoff",
                   "--noproxy", "*", "--request", method, "--url", url, "--write-out", "\n%{http_code}"]
        payload = None
        if body is not None or artifact is not None:
            content_type = "application/json" if artifact is None else "application/octet-stream"
            command += ["--header", f"Content-Type: {content_type}", "--data-binary", "@-"]
            payload = json.dumps(body if artifact is None else artifact).encode()
        if self.host:
            command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ControlMaster=no",
                       "-o", "ConnectTimeout=5", self.host, shlex.join(command)]
        try:
            result = subprocess.run(command, input=payload if payload is not None else b"",
                                    capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TrackingUnavailable("MLflow transport failed") from error
        if result.returncode:
            raise TrackingUnavailable("MLflow transport failed")
        data, _, status = result.stdout.rpartition(b"\n")
        if status not in {b"200", b"201", b"204", *allowed}:
            code = status.decode() if len(status) == 3 and status.isdigit() else "unknown"
            error_type = TrackingUnavailable if code == "429" or code.startswith("5") else TrackingError
            raise error_type(f"MLflow {route.split('/')[-1]} returned HTTP {code}")
        if status in allowed:
            return None
        try:
            return json.loads(data) if data else {}
        except ValueError as error:
            raise TrackingError("MLflow returned an invalid response") from error

    def experiment_id(self):
        query = {"experiment_name": self.experiment}
        route = "mlflow/experiments/get-by-name"
        found = self.request("GET", route, query=query, allowed=(b"404",))
        if found is None:
            created = self.request("POST", "mlflow/experiments/create",
                                   body={"name": self.experiment}, allowed=(b"409",))
            if created is not None:
                return created["experiment_id"]
            found = self.request("GET", route, query=query)
        return found["experiment"]["experiment_id"]

    def run(self, experiment_id, key, stats):
        found = self.request("POST", "mlflow/runs/search", body={
            "experiment_ids": [experiment_id], "filter": f"tags.`agentscumm.archive_key` = '{key}'",
            "max_results": 2,
        }).get("runs", [])
        if len(found) > 1:
            raise TrackingError("MLflow contains duplicate runs for this archive")
        if found:
            return found[0]
        tags = {"agentscumm.archive_key": key, "agentscumm.status": stats["status"]}
        return self.request("POST", "mlflow/runs/create", body={
            "experiment_id": experiment_id, "start_time": millis(stats["started_at"]),
            "run_name": f"{stats['game_id']} / {stats['run_id']}",
            "tags": [{"key": name, "value": value} for name, value in tags.items()],
        })["run"]


def millis(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def projection(stats):
    end = millis(stats["recorded_through"])
    metrics = []

    def metric(key, value, *, step=0, timestamp=end):
        if value is not None:
            metrics.append({"key": key, "value": value, "step": step, "timestamp": timestamp})

    metric("total_actions", stats.get("total_actions", sum(stats["actions"].values())))
    sizes = stats["action_sequences"]
    metric("total_action_sequences", sizes["count"])
    for name in ("min_length", "max_length", "mean_length", "std_length", "median_length"):
        metric(f"action_sequences/{name}", sizes.get(name))
    for name in TOKEN_METRICS:
        metric(name, stats["tokens"].get(name))
    metric("wall_clock_seconds", stats.get("wall_clock_seconds"))
    cost = stats.get("api_cost", {}).get("total_api_price_usd")
    if cost is not None:
        metric("api_equivalent_cost_usd", float(cost))

    params = {"game": stats["game_id"], "models": ", ".join(stats["models"]),
              "reasoning_efforts": ", ".join(stats["reasoning_levels"]),
              "walkthrough": "declared" if stats["walkthrough_urls"] else "unspecified"}
    progress = stats.get("verified_progress")
    curve = stats.get("progress_curve")
    if progress:
        metric("verified_progress_percent", progress["percent"], timestamp=millis(progress["recorded_at"]))
        if progress.get("map_revision"):
            params["map_revision"] = progress["map_revision"]
    if curve:
        params["map_revision"] = curve["map_revision"]
        params["baseline_progress_percent"] = str(curve["points"][0]["percent"])
        metric("max_verified_progress_percent", curve["points"][-1]["percent"])
        for point in curve["points"]:
            when = millis(point["recorded_at"])
            metric("actions_at_progress_percent", point["actions"], step=point["percent"], timestamp=when)
            metric("action_sequences_at_progress_percent", point["action_sequences"],
                   step=point["percent"], timestamp=when)

    artifact = []
    for index, sequence in enumerate(stats.get("timed_action_sequences") or [], start=1):
        kinds = [kind if kind in ACTION_TYPES else "other" for kind in sequence["types"]]
        metric("actions_in_sequence", len(kinds), step=index, timestamp=millis(sequence["submitted_at"]))
        artifact.append({"sequence": index, "types": kinds})
    return metrics, [{"key": name, "value": value} for name, value in params.items()], artifact


def publish(config, stats):
    tracker = Tracker(config)
    key = hashlib.sha256(json.dumps([
        stats["game_id"], stats["run_id"], stats["source"]["sha256"],
    ]).encode()).hexdigest()
    try:
        experiment = tracker.experiment_id()
        run = tracker.run(experiment, key, stats)
        run_id = run["info"]["run_id"]
        metrics, params, artifact = projection(stats)
        if artifact:
            location = urlsplit(run["info"]["artifact_uri"])
            parts = location.path.strip("/").split("/")
            if (location.scheme != "mlflow-artifacts" or location.netloc or location.query
                    or location.fragment or any(part in ("", ".", "..") for part in parts)):
                raise TrackingError("MLflow experiment must use server-proxied artifacts")
            route = "mlflow-artifacts/artifacts/" + "/".join(quote(part, safe="") for part in parts)
            tracker.request("PUT", route + "/action-sequences.json", artifact=artifact)
        for index in range(0, len(metrics), 500):
            tracker.request("POST", "mlflow/runs/log-batch", body={
                "run_id": run_id, "metrics": metrics[index:index + 500],
                "params": params if index == 0 else [],
            })
        if stats["status"] != "in_progress":
            statuses = {"completed": "FINISHED", "stopped": "KILLED", "blocked": "FAILED"}
            tracker.request("POST", "mlflow/runs/update", body={
                "run_id": run_id, "status": statuses[stats["status"]],
                "end_time": millis(stats["recorded_through"]),
            })
        return {"status": "published", "experiment_id": experiment, "run_id": run_id}
    except (KeyError, TypeError, AttributeError) as error:
        raise TrackingError("MLflow response or archive is missing required tracking data") from error
