"""Same-weight greedy A/B for rotating-cache prefill checkpoints.

Uses cached model weights only. Close other large local models before running.
Baseline keeps MachBoost's existing native LRU. The experimental checkpoints
are off in the app/server and must not be described as lossless: prefill shape
changes have produced different greedy tokens. Both modes clear that LRU
before each pair and receive identical first-turn and follow-up prompts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--context-lines", type=int, default=128)
    parser.add_argument("--output", type=Path, help="Write the generated JSON report")
    args = parser.parse_args()
    if args.runs < 1 or args.context_lines < 1:
        parser.error("runs and context-lines must be positive")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from machboost.adapters.mlx import MLXCausalLMService

    service = MLXCausalLMService.from_pretrained(args.model)
    service.configure_native_prompt_cache(enabled=True)
    tokenizer = service.tokenizer
    rows = []

    def render(messages):
        return tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, enable_thinking=False,
        )

    service.generate_tokens(render([{"role": "user", "content": "Hello"}]), max_tokens=8)
    for run in range(args.runs):
        outcomes = {}
        for enabled in ((False, True) if run % 2 == 0 else (True, False)):
            service.clear_prompt_cache()
            service.experimental_prefill_checkpoints = enabled
            context = "".join(f"Module {n} validates inputs before returning structured results.\n" for n in range(args.context_lines))
            messages = [{"role": "user", "content": context + f"\nCheck {run + 1}: explain input validation briefly."}]
            first_prompt = render(messages)
            first = service.generate_tokens(first_prompt, max_tokens=32)
            cold = dict(service.last_native_metrics)
            messages.extend([
                {"role": "assistant", "content": "Input validation rejects invalid data before processing."},
                {"role": "user", "content": "Give one example of a useful validation check."},
            ])
            second_prompt = render(messages)
            common = next((i for i, (a, b) in enumerate(zip(first_prompt, second_prompt)) if a != b), min(len(first_prompt), len(second_prompt)))
            second = service.generate_tokens(second_prompt, max_tokens=32)
            warm = dict(service.last_native_metrics)
            mode = "checkpoint" if enabled else "baseline"
            outcomes[mode] = (first, second)
            row = {"run": run + 1, "mode": mode, "model": args.model, "common_prompt_tokens": common, "first_turn": cold, "followup": warm}
            rows.append(row)
            print(json.dumps(row), flush=True)
        equality = {
            "run": run + 1,
            "first_turn_exact": outcomes["baseline"][0] == outcomes["checkpoint"][0],
            "followup_exact": outcomes["baseline"][1] == outcomes["checkpoint"][1],
            "exact_token_outputs": outcomes["baseline"] == outcomes["checkpoint"],
        }
        rows.append(equality)
        print(json.dumps(equality), flush=True)
    if args.output:
        args.output.write_text(json.dumps({
            "schema": "machboost.prefill_checkpoint_experiment.v1",
            "model": args.model, "runs": args.runs,
            "context_lines": args.context_lines, "max_tokens": 32,
            "default_enabled": False, "rows": rows,
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
