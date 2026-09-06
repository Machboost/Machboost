"""Measure client-visible delivery through native and Anthropic HTTP streams.

Use an already running MachBoost daemon. This does not download models, execute
tools, or alter server settings. Credentials and response text are not printed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def measure(endpoint, token, model, protocol, max_tokens, prompt):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": 0,
    }
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    if protocol == "native":
        path = "/api/chat"
        payload.update(
            think=False,
            options={"num_predict": max_tokens, "temperature": 0},
            tools=[{"type": "function", "function": {"name": "read_file", "parameters": parameters}}],
        )
    else:
        path = "/v1/messages"
        payload.update(
            max_tokens=max_tokens,
            thinking={"type": "disabled"},
            tools=[{"name": "read_file", "input_schema": parameters}],
        )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.perf_counter()
    row = {"protocol": protocol, "first_output_s": None, "first_text_s": None, "text_characters": 0}
    with urlopen(Request(endpoint.rstrip("/") + path, data=json.dumps(payload).encode(), headers=headers), timeout=180) as response:
        row["headers_s"] = time.perf_counter() - started
        for line in response:
            if protocol == "anthropic":
                if not line.startswith(b"data: {"):
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "error":
                    raise RuntimeError(event["error"].get("message", "stream error"))
                delta = event.get("delta") or {}
                text = delta.get("text", "")
                output = text or delta.get("thinking") or delta.get("partial_json")
                if event.get("type") == "message_delta":
                    row["generated_tokens"] = event.get("usage", {}).get("output_tokens")
            else:
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(event["error"])
                message = event.get("message") or {}
                text = message.get("content", "")
                output = text or message.get("thinking") or message.get("tool_calls")
                if event.get("done"):
                    meta = event.get("machboost") or {}
                    stats = meta.get("stats") or {}
                    row.update(
                        load_s=event.get("load_duration", 0) / 1e9,
                        queue_s=(meta.get("scheduler") or {}).get("queue_wait_seconds"),
                        prompt_tokens=event.get("prompt_eval_count"),
                        cached_tokens=stats.get("prompt_cache_prefix_tokens", stats.get("cached_prompt_tokens")),
                        generated_tokens=event.get("eval_count"),
                    )
            elapsed = time.perf_counter() - started
            if output and row["first_output_s"] is None:
                row["first_output_s"] = elapsed
            if text and row["first_text_s"] is None:
                row["first_text_s"] = elapsed
            row["text_characters"] += len(text)
    row["wall_s"] = time.perf_counter() - started
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11435")
    parser.add_argument("--app-token", action="store_true", help="Read this Mac's installed app credential for a loopback endpoint")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--context-lines", type=int, default=0, help="Add stable synthetic context to exercise prefix reuse")
    parser.add_argument("--clients", type=int, default=1, help="Concurrent clients per round; 1 alternates protocols sequentially")
    parser.add_argument("--output", type=Path, help="Write the generated JSON report")
    args = parser.parse_args()
    if args.runs < 1 or args.max_tokens < 1 or args.context_lines < 0 or not 1 <= args.clients <= 16:
        parser.error("runs/max-tokens must be positive; context-lines nonnegative; clients 1-16")
    token = os.environ.get("MACHBOOST_API_TOKEN", "")
    if args.app_token:
        if urlparse(args.endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("--app-token can only be used on loopback")
        from machboost.client import machboost_app_api_token
        token = machboost_app_api_token()
    context = "".join(f"Module {n} validates inputs before returning structured results.\n" for n in range(args.context_lines))
    rows = []
    for run in range(args.runs):
        prompt = context + f"\nRequest {run + 1}: In one paragraph, explain why automated tests are useful. Do not call any tools."
        protocols = ("native", "anthropic") if run % 2 == 0 else ("anthropic", "native")
        def execute(protocol):
            return measure(args.endpoint, token, args.model, protocol, args.max_tokens, prompt)
        if args.clients == 1:
            outcomes = map(execute, protocols)
        else:
            with ThreadPoolExecutor(max_workers=args.clients) as executor:
                outcomes = list(executor.map(execute, [protocols[n % 2] for n in range(args.clients)]))
        for row in outcomes:
            record = {"run": run + 1, "model": args.model, **row}
            rows.append(record)
            print(json.dumps(record), flush=True)
    if args.output:
        args.output.write_text(json.dumps({
            "schema": "machboost.stream_delivery.v1", "model": args.model,
            "runs": args.runs, "clients": args.clients,
            "max_tokens": args.max_tokens, "context_lines": args.context_lines,
            "measurement": "HTTP client, not Claude Desktop UI", "rows": rows,
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
