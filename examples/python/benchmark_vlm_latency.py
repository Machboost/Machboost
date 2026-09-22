"""Audit native MLX-VLM versus MachBoost with one loaded model.

Uses cached weights only. Close other inference processes before running.
Cold rows have no reusable prefix; follow-ups reuse only their own pair's state.
Disk tests use a temporary cache, never the application's cache. This is an
adapter benchmark, not a native-app or Claude/Codex UI benchmark.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import platform
import tempfile
import time
import uuid
from importlib.metadata import version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--context-lines", type=int, default=400)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--cache", choices=("memory", "disk"), default="memory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.context_lines < 0 or args.max_tokens < 1:
        parser.error("runs/max-tokens must be positive; context-lines nonnegative")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["MACHBOOST_MLX_APC_DISK"] = "1" if args.cache == "disk" else "0"
    os.environ.pop("MACHBOOST_MLX_VLM_PREFILL_STEP", None)
    from machboost.adapters.mlx_vlm import MLXVLMAccelerator, _effective_reasoning, _thinking_budget

    with tempfile.TemporaryDirectory(prefix="machboost-latency-") as cache_dir:
        os.environ["MACHBOOST_MLX_APC_DISK_PATH"] = cache_dir
        loaded_at = time.perf_counter()
        accelerator = MLXVLMAccelerator.from_pretrained(args.model)
        load_seconds = time.perf_counter() - loaded_at
        native_stream = accelerator._stream_generate
        observed = {}
        started = 0.0

        @functools.wraps(native_stream)
        def observe(*positional, **kwargs):
            seen = set()
            for item in native_stream(*positional, **kwargs):
                if getattr(item, "text", ""):
                    observed.setdefault("first_native_chunk_seconds", time.perf_counter() - started)
                step = getattr(item, "generation_tokens", 0)
                token = getattr(item, "token", None)
                if token is not None and step not in seen:
                    seen.add(step)
                    observed.setdefault("token_ids", []).append(int(token.item() if hasattr(token, "item") else token))
                observed["last"] = item
                yield item

        accelerator._stream_generate = observe
        thinking, strength = _effective_reasoning(accelerator.model.config, False, None)

        def run_native(messages):
            accelerator._bind_thread_local_stream()
            prompt = accelerator._format_chat_prompt(
                messages, image_count=0, enable_thinking=thinking, reasoning_strength=strength,
            )
            kwargs = {"max_tokens": args.max_tokens, "temperature": 0.0, "enable_thinking": thinking}
            if thinking:
                kwargs["thinking_budget"] = _thinking_budget(strength)
                if getattr(accelerator.model.config, "model_type", "") == "muse_glimmer":
                    kwargs.update(thinking_start_token="<|eot|>", thinking_end_token="<|eom|>")
            for _ in observe(accelerator.model, accelerator.processor, prompt, **kwargs):
                pass

        def emit(text):
            if text:
                observed.setdefault("first_output_seconds", time.perf_counter() - started)

        rows = []
        try:
            accelerator.generate_chat([{"role": "user", "content": "Say hello."}], max_tokens=8)
            context = "".join(
                f"Module {n} validates input fields before returning structured records.\n"
                for n in range(args.context_lines)
            )
            for run in range(args.runs):
                accelerator.reset_cache()
                key = f"audit:{uuid.uuid4().hex}"
                messages = [
                    {"role": "system", "content": "Answer concisely using the supplied notes."},
                    {"role": "user", "content": context + "\nWhat do these modules do?"},
                ]
                following = messages + [
                    {"role": "assistant", "content": "They validate inputs and return structured records."},
                    {"role": "user", "content": "Give one example of a useful validation check."},
                ]
                tokens_by_mode = {}
                modes = ("native", "machboost") if run % 2 == 0 else ("machboost", "native")
                for mode in modes:
                    for phase, prompt_messages in (("cold", messages), ("followup", following)):
                        observed = {}
                        started = time.perf_counter()
                        stats = None
                        if mode == "native":
                            accelerator._executor.submit(run_native, prompt_messages).result()
                        else:
                            _, stats = accelerator.generate_chat(
                                prompt_messages, max_tokens=args.max_tokens, cache_key=key,
                                on_text=emit, on_thinking=emit,
                            )
                        wall = time.perf_counter() - started
                        token_ids = observed.pop("token_ids", [])
                        tokens_by_mode[(mode, phase)] = token_ids
                        last = observed.pop("last")
                        row = {
                            "run": run + 1, "mode": mode, "phase": phase, "wall_seconds": wall,
                            "prompt_tokens": last.prompt_tokens,
                            "generated_tokens": last.generation_tokens,
                            "generation_tokens_per_second": last.generation_tps,
                            "cached_prompt_tokens": stats.prompt_cache_prefix_tokens if stats else 0,
                            "token_sha256": hashlib.sha256(json.dumps(token_ids).encode()).hexdigest(),
                            **observed,
                        }
                        if mode == "native":
                            row["first_output_seconds"] = row.get("first_native_chunk_seconds")
                        tokenizer = getattr(accelerator.processor, "tokenizer", accelerator.processor)
                        row["generated_text"] = tokenizer.decode(token_ids, skip_special_tokens=False)
                        if phase == "cold" and row["cached_prompt_tokens"]:
                            raise RuntimeError("Cold measurement unexpectedly reused a prefix")
                        rows.append(row)
                        print(json.dumps(row), flush=True)
                equality = {"run": run + 1, "exact_token_outputs": {
                    phase: tokens_by_mode[("native", phase)] == tokens_by_mode[("machboost", phase)]
                    for phase in ("cold", "followup")
                }}
                rows.append(equality)
                print(json.dumps(equality), flush=True)
        finally:
            accelerator.close()

    artifact = {
        "schema": "machboost.vlm_latency_audit.v1",
        "model": args.model, "load_seconds": load_seconds, "cache": args.cache,
        "context_lines": args.context_lines, "max_tokens": args.max_tokens,
        "reasoning_enabled": thinking, "reasoning_strength": strength,
        "platform": platform.platform(), "machine": platform.machine(),
        "versions": {name: version(name) for name in ("mlx", "mlx-vlm", "mlx-lm")},
        "measurement": "Same loaded weights, template, reasoning settings, and greedy sampling; adapter only.",
        "notes": [
            "Native baseline has no cross-request prompt cache; MachBoost uses its normal prefix cache.",
            "Cold rows compare wrapper overhead. Follow-ups measure cache reuse, not faster cold inference.",
            "Follow-up history is fixed to keep both inputs identical regardless of first-turn output.",
            "Chunked/cached prefill can change greedy tokens through floating-point differences; equality is measured, not assumed.",
            "Warmup/load is excluded; disk cache is temporary and unique to this invocation.",
        ],
        "rows": rows,
    }
    if args.output:
        args.output.write_text(json.dumps(artifact, indent=2) + "\n")


if __name__ == "__main__":
    main()
