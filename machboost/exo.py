"""Optional, dependency-free client for an externally managed EXO cluster."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import time
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ExoClusterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClusterInstance:
    id: str
    model: str
    nodes: tuple[str, ...]
    sharding: str
    transport: str

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "nodes": list(self.nodes)}


def _instance(instance_id: str, value: Any) -> ClusterInstance:
    if not isinstance(value, dict) or len(value) != 1:
        raise ExoClusterError("EXO returned an unrecognized instance shape")
    transport, body = next(iter(value.items()))
    if transport not in {"MlxRingInstance", "MlxJacclInstance"} or not isinstance(body, dict):
        raise ExoClusterError("EXO returned an unrecognized instance type")
    assignments = body.get("shardAssignments")
    if not isinstance(assignments, dict):
        raise ExoClusterError("EXO instance has no shard assignments")
    nodes = assignments.get("nodeToRunner")
    shards = assignments.get("runnerToShard")
    if not isinstance(nodes, dict) or not isinstance(shards, dict):
        raise ExoClusterError("EXO instance has invalid shard assignments")
    shard_types = {
        next(iter(shard))
        for shard in shards.values()
        if isinstance(shard, dict) and len(shard) == 1
    }
    if not shard_types or not shard_types <= {"TensorShardMetadata", "PipelineShardMetadata"}:
        raise ExoClusterError("EXO instance has unknown sharding metadata")
    if len(shard_types) != 1:
        raise ExoClusterError("EXO instance mixes sharding modes")
    model = assignments.get("modelId")
    if not isinstance(model, str) or not model:
        raise ExoClusterError("EXO instance has no model ID")
    return ClusterInstance(
        id=str(body.get("instanceId") or instance_id),
        model=model,
        nodes=tuple(sorted(str(node) for node in nodes)),
        sharding="tensor" if "TensorShardMetadata" in shard_types else "pipeline",
        transport="jaccl" if transport == "MlxJacclInstance" else "ring",
    )


def parse_cluster_state(state: dict[str, Any]) -> dict[str, Any]:
    topology = state.get("topology")
    if not isinstance(topology, dict) or not isinstance(topology.get("nodes"), list):
        raise ExoClusterError("EXO state has no node topology")
    raw_instances = state.get("instances")
    if not isinstance(raw_instances, dict):
        raise ExoClusterError("EXO state has no instance map")
    instances = [_instance(str(key), value) for key, value in raw_instances.items()]
    connections = topology.get("connections") or {}
    rdma_links: set[tuple[str, str]] = set()
    if isinstance(connections, dict):
        for source, peers in connections.items():
            if not isinstance(peers, dict):
                continue
            for target, edges in peers.items():
                if isinstance(edges, list) and any(
                    isinstance(edge, dict) and "RDMAConnection" in edge for edge in edges
                ):
                    rdma_links.add(tuple(sorted((str(source), str(target)))))
    return {
        "schema": "machboost.exo_cluster.v1",
        "nodes": sorted(str(node) for node in topology["nodes"]),
        "rdma_links": [list(link) for link in sorted(rdma_links)],
        "instances": [item.to_dict() for item in instances],
    }


def require_placement(
    snapshot: dict[str, Any], model: str, *, min_nodes: int = 2, sharding: str = "tensor"
) -> dict[str, Any]:
    candidates = [
        item for item in snapshot.get("instances", [])
        if item.get("model") == model
        and len(item.get("nodes") or []) >= min_nodes
        and item.get("sharding") == sharding
    ]
    if not candidates:
        raise ExoClusterError(
            f"No active {sharding} instance of {model} spans {min_nodes}+ EXO nodes"
        )
    return max(candidates, key=lambda item: len(item["nodes"]))


def _sse_events(lines: Iterator[bytes]) -> Iterator[tuple[str, str]]:
    fields: list[str] = []

    def event() -> tuple[str, str] | None:
        if not fields:
            return None
        comments = [line[1:].strip() for line in fields if line.startswith(":")]
        data = [line[5:].lstrip(" ") for line in fields if line.startswith("data:")]
        if data:
            return "data", "\n".join(data)
        if comments:
            return "comment", "\n".join(comments)
        return None

    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            item = event()
            if item is not None:
                yield item
            fields.clear()
        else:
            fields.append(line)
    item = event()
    if item is not None:
        yield item


@dataclass(frozen=True)
class ChatMeasurement:
    total_seconds: float
    first_output_seconds: float | None
    first_answer_seconds: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    generation_tokens_per_second: float | None
    prefix_cache_hit: str | None
    content: str
    reasoning: str
    tool_call_events: int
    finish_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExoClusterClient:
    def __init__(self, endpoint: str, *, api_key: str | None = None, timeout: float = 300) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("EXO endpoint must be an HTTP(S) server root URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("EXO endpoint cannot contain credentials, query, or fragment")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _open(self, path: str, payload: dict[str, Any] | None = None):
        headers = {"Accept": "application/json" if payload is None else "text/event-stream"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.endpoint + path, data=body, headers=headers)
        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            message = exc.read(512).decode("utf-8", errors="replace")
            raise ExoClusterError(f"EXO HTTP {exc.code}: {message}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ExoClusterError(f"EXO at {self.endpoint} is unavailable") from exc

    def snapshot(self) -> dict[str, Any]:
        with self._open("/state") as response:
            try:
                state = json.load(response)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ExoClusterError("EXO returned invalid cluster state") from exc
        if not isinstance(state, dict):
            raise ExoClusterError("EXO returned invalid cluster state")
        return parse_cluster_state(state)

    def measure_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 128,
        temperature: float = 0,
        seed: int = 0,
    ) -> ChatMeasurement:
        if not model or not messages or max_tokens < 1:
            raise ValueError("model, messages, and a positive max_tokens are required")
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "seed": seed,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        started = time.perf_counter()
        first_output = first_answer = None
        content: list[str] = []
        reasoning: list[str] = []
        tool_call_events = 0
        prompt_tokens = completion_tokens = None
        generation_tps = None
        prefix_cache_hit = None
        finish_reason = None
        completed = False
        with self._open("/v1/chat/completions", payload) as response:
            for kind, raw in _sse_events(iter(response)):
                elapsed = time.perf_counter() - started
                if kind == "comment":
                    if raw.startswith("generation_stats "):
                        try:
                            stats = json.loads(raw.removeprefix("generation_stats "))
                        except ValueError:
                            continue
                        generation_tps = stats.get("generation_tps")
                        prefix_cache_hit = stats.get("prefix_cache_hit")
                    continue
                if raw == "[DONE]":
                    completed = True
                    break
                try:
                    event = json.loads(raw)
                except ValueError as exc:
                    raise ExoClusterError("Upstream sent invalid chat SSE data") from exc
                if not isinstance(event, dict):
                    raise ExoClusterError("Upstream sent invalid chat SSE data")
                if event.get("error"):
                    raise ExoClusterError(f"Upstream chat failed: {event['error']}")
                usage = event.get("usage") or {}
                metadata = event.get("machboost") or {}
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get("completion_tokens", completion_tokens)
                if isinstance(metadata, dict):
                    prompt_tokens = metadata.get("prompt_tokens", prompt_tokens)
                    completion_tokens = metadata.get("generated_tokens", completion_tokens)
                    generation_tps = metadata.get("generation_tokens_per_second", generation_tps)
                    prefix_cache_hit = metadata.get("prefix_cache_hit", prefix_cache_hit)
                choices = event.get("choices") or []
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        continue
                    answer = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    calls = delta.get("tool_calls") or []
                    if answer or thought or calls:
                        first_output = elapsed if first_output is None else first_output
                    if answer:
                        first_answer = elapsed if first_answer is None else first_answer
                        content.append(answer)
                    if thought:
                        reasoning.append(thought)
                    tool_call_events += len(calls)
                    finish_reason = choice.get("finish_reason") or finish_reason
        if not completed:
            raise ExoClusterError("Upstream chat stream ended before [DONE]")
        return ChatMeasurement(
            total_seconds=time.perf_counter() - started,
            first_output_seconds=first_output,
            first_answer_seconds=first_answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            generation_tokens_per_second=generation_tps,
            prefix_cache_hit=prefix_cache_hit,
            content="".join(content),
            reasoning="".join(reasoning),
            tool_call_events=tool_call_events,
            finish_reason=finish_reason,
        )
