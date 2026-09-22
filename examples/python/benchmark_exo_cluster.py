"""Compare a loaded EXO placement with the same resident model on MachBoost."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from machboost.client import MachBoostAPIError, MachBoostClient, machboost_app_api_token
from machboost.exo import (
    ExoClusterClient,
    ExoClusterError,
    require_placement,
    require_rdma_placement,
)


def _public_result(measurement) -> dict:
    result = measurement.to_dict()
    answer = result.pop("content")
    thought = result.pop("reasoning")
    result["answer_sha256"] = hashlib.sha256(answer.encode()).hexdigest()
    result["reasoning_sha256"] = hashlib.sha256(thought.encode()).hexdigest()
    result["answer_chars"] = len(answer)
    result["reasoning_chars"] = len(thought)
    return result


def _median(rows: list[dict], field: str) -> float | None:
    values = [row[field] for row in rows if row.get(field) is not None]
    return median(values) if values else None


def _resident_baseline(endpoint: str, model: str, token: str | None, timeout: float) -> None:
    client = MachBoostClient(endpoint, api_token=token, timeout=timeout)
    loaded = client.ps()
    matching = [
        row for row in loaded
        if model in {row.get("model"), row.get("name"), row.get("repository")}
    ]
    if not matching:
        raise ExoClusterError(f"MachBoost baseline model {model} is not resident; warm it first")
    if any(row.get("warming") for row in matching):
        raise ExoClusterError(f"MachBoost baseline model {model} is still warming")


def run_benchmark(args: argparse.Namespace) -> dict:
    if args.min_nodes < 2:
        raise ExoClusterError("EXO cluster benchmark requires at least two nodes")
    exo = ExoClusterClient(
        args.exo_url, api_key=os.environ.get("EXO_API_TOKEN"), timeout=args.timeout
    )
    snapshot = exo.snapshot()
    placement = require_placement(
        snapshot, args.model, min_nodes=args.min_nodes, sharding=args.sharding
    )
    if args.require_rdma:
        require_rdma_placement(snapshot, placement)
    baseline = None
    if args.machboost_url:
        parsed = urlsplit(args.machboost_url)
        token = os.environ.get("MACHBOOST_API_TOKEN")
        if token is None and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            token = machboost_app_api_token()
        _resident_baseline(args.machboost_url, args.model, token, args.timeout)
        baseline = ExoClusterClient(args.machboost_url, api_key=token, timeout=args.timeout)

    hosts = [("exo", exo)]
    if baseline is not None:
        hosts.append(("machboost", baseline))
    pairs = []
    all_rounds = args.warmups + args.runs
    for turn in range(all_rounds):
        marker = f"Request ID: {uuid4().hex}\n" if args.fresh else ""
        messages = [{"role": "user", "content": marker + args.prompt}]
        order = list(reversed(hosts)) if turn % 2 else hosts
        measurements = {}
        for name, client in order:
            measurements[name] = client.measure_chat(
                args.model,
                messages,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                seed=args.seed,
            )
        if turn < args.warmups:
            continue
        pair = {
            "run": turn - args.warmups + 1,
            "first": order[0][0],
            "exo": _public_result(measurements["exo"]),
        }
        if baseline is not None:
            pair["machboost"] = _public_result(measurements["machboost"])
            pair["answer_equal"] = (
                measurements["exo"].content == measurements["machboost"].content
            )
            pair["reasoning_equal"] = (
                measurements["exo"].reasoning == measurements["machboost"].reasoning
            )
            pair["total_speedup_exo_vs_machboost"] = (
                measurements["machboost"].total_seconds
                / measurements["exo"].total_seconds
            )
        pairs.append(pair)

    final_snapshot = exo.snapshot()
    final_placement = require_placement(
        final_snapshot, args.model, min_nodes=args.min_nodes, sharding=args.sharding
    )
    if final_placement != placement:
        raise ExoClusterError("EXO placement changed during the benchmark")
    if args.require_rdma:
        require_rdma_placement(final_snapshot, final_placement)

    exo_rows = [pair["exo"] for pair in pairs]
    baseline_rows = [pair["machboost"] for pair in pairs if "machboost" in pair]
    return {
        "schema": "machboost.exo_compare.v1",
        "model": args.model,
        "prompt_sha256": hashlib.sha256(args.prompt.encode()).hexdigest(),
        "prompt_characters": len(args.prompt),
        "fresh_prompt_per_pair": args.fresh,
        "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout,
        "temperature": args.temperature,
        "seed": args.seed,
        "warmups": args.warmups,
        "runs": args.runs,
        "cluster": {
            "nodes": snapshot["nodes"],
            "rdma_links": snapshot["rdma_links"],
            "placement": placement,
        },
        "summary": {
            "exo_median_total_seconds": _median(exo_rows, "total_seconds"),
            "exo_median_first_output_seconds": _median(exo_rows, "first_output_seconds"),
            "exo_median_first_answer_seconds": _median(exo_rows, "first_answer_seconds"),
            "machboost_median_total_seconds": _median(baseline_rows, "total_seconds"),
            "machboost_median_first_output_seconds": _median(baseline_rows, "first_output_seconds"),
            "machboost_median_first_answer_seconds": _median(baseline_rows, "first_answer_seconds"),
            "median_total_speedup_exo_vs_machboost": _median(
                pairs, "total_speedup_exo_vs_machboost"
            ),
            "answer_equal_pairs": sum(pair.get("answer_equal", False) for pair in pairs),
            "reasoning_equal_pairs": sum(pair.get("reasoning_equal", False) for pair in pairs),
        },
        "pairs": pairs,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exo-url", required=True, help="EXO API root, usually http://127.0.0.1:52415")
    parser.add_argument("--machboost-url", help="Optional resident MachBoost API root")
    parser.add_argument("--model", required=True, help="Exact Hugging Face model ID loaded in both runtimes")
    parser.add_argument("--prompt", default="Explain how a hash table resolves collisions in two sentences.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=120, help="Per-request timeout in seconds")
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-nodes", type=int, default=2)
    parser.add_argument("--sharding", choices=("tensor", "pipeline"), default="tensor")
    parser.add_argument("--require-rdma", action="store_true")
    parser.add_argument(
        "--reuse-prompt", dest="fresh", action="store_false",
        help="Repeat the identical prompt across pairs; default adds a fresh leading nonce",
    )
    parser.add_argument("--output", type=Path, help="Write machine-readable results to this JSON file")
    parser.set_defaults(fresh=True)
    args = parser.parse_args(argv)
    if args.runs < 1 or args.warmups < 0 or args.max_tokens < 1 or args.min_nodes < 2 or args.timeout <= 0:
        parser.error("runs, max-tokens, and timeout must be positive; min-nodes must be at least 2; warmups cannot be negative")
    try:
        report = run_benchmark(args)
    except (ExoClusterError, MachBoostAPIError, ValueError) as exc:
        print(f"EXO benchmark: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
