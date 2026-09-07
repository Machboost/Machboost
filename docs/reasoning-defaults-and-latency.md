# Reasoning defaults and latency

Measured locally on 2026-09-06 with the cached
`lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit` model, MLX 0.32.2,
MLX-LM 0.31.3, and MLX-VLM 0.6.17. This test used the MLX text backend.

## Corrected defaults

The CLI previously imposed a 128-token budget, including thinking, and disabled
reasoning unless requested. Both behaviors were still present in 0.16.18.
In 0.16.19, reasoning-capable chat models default to Low and native run/chat/code/
completion commands default to `--max-tokens -1`. Positive limits still work.
Uncapped means no separate output ceiling, not infinite context or memory.

Models with a binary thinking switch do not implement distinct effort levels;
Gemma's MLX text path enables thinking but does not impose a Low thinking budget.
Existing explicit desktop Off preferences are retained. Reset settings restores
the model-aware default; non-reasoning models do not expose an effort selector.

## Local verification

In an actual terminal session, the original question about deriving x-y from
x+y=5 completed with both reasoning and an answer: 406 total generated tokens,
3.31 seconds to first output, and 73.1 reported decode tokens/second. Model load
took 8.94 seconds separately. Background warmup was disabled for that measurement.
No desktop UI or Claude session was measured here.

After loading, three HTTP requests used fresh question numbers, temperature zero,
thinking enabled, and no output cap. They all returned complete algebra answers
with two valid examples. These are small synthetic checks, not a broad quality
evaluation. The final measurements ran after the build and test jobs finished.

| Warm measurement | Median | Range |
| --- | --- | --- |
| First streamed reasoning/text | 0.286 s | 0.284-0.684 s |
| First answer text | 7.009 s | 6.973-7.785 s |
| Complete response | 8.257 s | 8.203-9.033 s |
| Total decode rate | 74.5 tokens/s | 74.4-74.7 tokens/s |

All three reported zero model-load time and generated 592-622 tokens. Most of the
wait for an answer was reasoning, not delayed delivery of the first token.
[Raw results](../results/gemma_uncapped_reasoning_20260906.json).

Reproduce against an existing server with the model already downloaded:

```sh
python examples/python/benchmark_reasoning.py \
  --model lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit \
  --runs 3 --output /tmp/reasoning-latency.json
```

Use `MACHBOOST_API_TOKEN` if the server requires authentication. The script records
synthetic answer text, not credentials or reasoning text. Its timeout cancels the
request. It does not download models or run tools.

## Faster inference, separately

This patch does not alter model weights, add a decoder, or establish a speedup
against unwrapped MLX. Removing a cap can increase total response time because
the model can finish instead of stopping mid-thought. Previous
[stream delivery fixes](streaming-latency-verification.md) address buffering,
not the cost of computing new tokens.

Two concrete next experiments remain:

- Gemma's paired MTP assistant proposes several tokens and has the target verify
  them together. MLX-VLM documents a 26B-A4B pairing, but its published sweep uses
  bf16 targets and specific batch sizes. It is not evidence for this QAT 4-bit
  repository. Benchmark the same target, prompts, precision, and greedy decoding
  with/without the compatible assistant, including token equality and memory.
  [Upstream implementation](https://github.com/Blaizzy/mlx-vlm/blob/main/mlx_vlm/speculative/drafters/gemma4_assistant/README.md).
- Continuous batching can reduce head-of-line waiting under concurrent use.
  MachBoost still leases its GPU worker for whole requests; HTTP concurrency is
  not continuous GPU batching. Integration needs per-request cancellation and
  architecture-specific cache validation.
  [MLX-LM batch generator](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/generate.py).

Neither is enabled by this defaults patch. The earlier prefill checkpoint
experiment remains disabled because it failed first-turn token equality checks.
