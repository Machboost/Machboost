"""Measure reasoning and answer delivery separately on an existing local server."""
import argparse
import json
import statistics
import threading
import time
import uuid
from pathlib import Path

from machboost.client import MachBoostClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11435")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=-1)
    parser.add_argument("--think", choices=["off", "low", "medium", "high"], default="low")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.timeout <= 0 or (args.max_tokens != -1 and args.max_tokens < 1):
        parser.error("runs/timeout must be positive; max-tokens must be -1 or positive")
    # Credentials can come from MACHBOOST_API_TOKEN and are never written to the report.
    client = MachBoostClient(args.endpoint, timeout=args.timeout)
    rows = []
    for run in range(args.runs):
        request_id = str(uuid.uuid4())
        started = time.perf_counter()
        first_output = first_answer = None
        reasoning_chars = 0
        answer = []
        last = {}

        def cancel():
            client.cancel(request_id)

        timer = threading.Timer(args.timeout, cancel)
        timer.daemon = True
        timer.start()
        try:
            for event in client.chat(
                args.model,
                [{"role": "user", "content": (
                    f"Question {run + 1}: Can you determine x-y from x+y=5 alone? "
                    "Answer briefly with two examples."
                )}],
                options={"num_predict": args.max_tokens, "temperature": 0},
                think=False if args.think == "off" else args.think,
                request_id=request_id,
            ):
                message = event.get("message") or {}
                thinking = str(message.get("thinking") or "")
                text = str(message.get("content") or "")
                elapsed = time.perf_counter() - started
                if (thinking or text) and first_output is None:
                    first_output = elapsed
                if text and first_answer is None:
                    first_answer = elapsed
                reasoning_chars += len(thinking)
                answer.append(text)
                if event.get("done"):
                    last = event
        finally:
            timer.cancel()
        count = int(last.get("eval_count") or 0)
        decode_s = float(last.get("eval_duration") or 0) / 1e9
        row = {
            "run": run + 1, "first_output_s": first_output,
            "first_answer_s": first_answer, "wall_s": time.perf_counter() - started,
            "load_s": float(last.get("load_duration") or 0) / 1e9,
            "generated_tokens": count, "decode_s": decode_s,
            "decode_tokens_per_second": count / decode_s if decode_s else None,
            "reasoning_characters": reasoning_chars, "answer": "".join(answer),
            "done_reason": last.get("done_reason"),
        }
        rows.append(row)
        print(json.dumps(row), flush=True)
    summary = {}
    for field in ("first_output_s", "first_answer_s", "wall_s", "decode_tokens_per_second"):
        values = [row[field] for row in rows if row[field] is not None]
        summary[f"median_{field}"] = statistics.median(values) if values else None
    report = {
        "schema": "machboost.reasoning_latency.v1", "model": args.model,
        "reasoning": args.think, "max_tokens": args.max_tokens,
        "measurement": "HTTP client of resident daemon, not desktop UI or Claude",
        "rows": rows, "summary": summary,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
