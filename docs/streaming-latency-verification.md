# Streaming latency verification

Measured locally on 2026-09-05: Apple M5 Pro, 48 GiB unified memory, macOS
26.6.2, bundled Python 3.13, MLX 0.32.0, MLX-LM 0.31.3, and MLX-VLM 0.6.13.
This follows the [coding-client source audit](coding-client-latency.md).

## Changes

- The shared-host loopback relay forwards available bytes using `read1` instead
  of waiting for a 64 KiB read to fill. Byte fragments are not independently
  decoded, preserving split UTF-8 and protocol events.
- Supplying tools no longer suppresses ordinary answer-text streaming on the
  Anthropic, OpenAI chat, and Responses routes. The native chat route shares the
  incremental prose/tool parser.
- Native and Anthropic streams emit complete parsed tool calls as they become
  available. Anthropic reasoning, text, and tool blocks close before the next
  block starts. Incomplete tool arguments are withheld; execution still belongs
  to the client and its permission checks. OpenAI function items remain final.
- Already-emitted prose is no longer reparsed on each fragment. Ambiguous bare
  JSON and JSON fences are still withheld until final parsing.
- The macOS chat view limits intermediate text persistence to approximately
  30 updates per second, with immediate first output and forced final, tool,
  error, and cancellation updates. TTFT is recorded from the start of the chat
  generation loop to first received reasoning, prose, or tool output, including
  queue/load time inside that interval. It is not time to the first final-answer
  word and excludes preparation before that loop.

These are delivery and presentation fixes, not a new decoder or universal
inference speedup. No prompt shortening, lower precision, or disabled reasoning
was added to the app defaults by this change.

## HTTP measurements

Model: `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit`. Requests supplied one
tool definition but asked for ordinary prose, with temperature zero, reasoning
disabled explicitly in the benchmark, and a 64-token output limit. Protocol order
alternated. Native and Anthropic request translation need not produce identical
model inputs; this is a delivery comparison, not an exactness benchmark.

| Path | State | Client first text | Full response |
| --- | --- | --- | --- |
| Native `/api/chat` | First request, model load 2.803 s | 3.365 s | 4.196 s |
| Native `/api/chat` | Two subsequent requests | 0.240-0.316 s | 1.070-1.145 s |
| Anthropic `/v1/messages` | Three warm requests | 0.242-0.322 s | 1.070-1.151 s |

[Raw single-client results](../results/gemma_stream_delivery_20260905.json).
These are HTTP measurements of the Claude-compatible endpoint, **not measurements
inside Claude Desktop or Claude Code**. The installed Claude Desktop was not
switched away from its existing provider session.

Before the streaming fix, two exploratory warm Anthropic requests delivered first
text only at completion (1.143 and 1.232 s). Those observations were not collected
as a controlled paired benchmark, so no precise before/after speedup is claimed.
A gated regression test independently proves that the relay delivers the first
event before upstream completion; the pre-fix relay timed out under that test.

Two concurrent clients also completed all four measured requests. With one GPU
worker, the first client received text in 0.251-0.441 s, while the queued client
received it in 1.323-1.532 s. Full responses took 1.300-2.369 s.
[Raw concurrent results](../results/gemma_concurrent_delivery_20260905.json).
This is bounded queueing, **not continuous GPU batching**. Increasing HTTP clients
does not establish concurrent model execution or improved throughput.

## Installed native app checks

The rebuilt `/Applications/MachBoost.app` was exercised through its actual UI,
using local inference rather than the Mac Studio or an XCTest-only interface.
These are individual smoke checks, not distributions or cross-model comparisons.
Reasoning remained enabled for the chat checks.

| Model and interaction | Displayed first-output time | Outcome |
| --- | --- | --- |
| Gemma 26B, exact greeting | 0.68 s | Complete `You're welcome!`, including first word |
| Gemma 26B, read-only repository request | 1.19 s | Reasoning, one `list_files` call, reasoning, final three names |
| Muse Glimmer 30B 4-bit, cold greeting | 50.37 s | Complete greeting; cold startup remains slow |
| Muse Glimmer 30B 4-bit, warm follow-up | 1.95 s | Complete `Hello again!` |

The repository check used Plan mode and did not modify files or execute shell
commands. Its model produced a final answer after the tool result without raw
tool markup or a duplicate call. A single successful tool loop does not prove
every model or long coding session is correct. The Muse cold result is an
unresolved load/compile/startup problem; these fixes do not make it disappear.

## Prefill snapshot experiment: disabled

Gemma's rotating prompt state could not always rewind far enough to reuse a
conversation prefix. An opt-in `experimental_prefill_checkpoints` flag on the MLX
text service retains earlier immutable prefill snapshots within the existing
namespace and bounded LRU. Normal app, CLI, and server paths leave this false.

Three paired runs used one loaded Gemma model, identical tokenized prompts,
greedy generation, a 32-token limit, alternating mode order, and a cleared cache
before each mode. Both modes used the same canonical assistant history for the
follow-up, preventing different first-turn outputs from changing its input.

| Follow-up measurement | Default | Experimental |
| --- | --- | --- |
| Median client callback first token | 2.075 s | 0.345 s |
| Reused prompt tokens | 0 | 1,512 of 1,603 |
| Follow-up token equality | Reference | 3/3 pairs |
| First-turn token equality | Reference | 1/3 pairs |

The approximately 6x follow-up latency result is **not enabled for users**.
First-turn outputs differed in two pairs when prefill shapes changed. First-turn
latency also increased slightly. Follow-up equality alone is not sufficient to
call the workflow exact, and no broad quality evaluation was performed.
[Raw timings and equality checks](../results/gemma_prefill_checkpoints_20260905.json).

## Reproduction and regression coverage

With the model already cached and the app running:

```sh
python3 examples/python/stream_delivery_benchmark.py --app-token \
  --model lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit \
  --runs 3 --max-tokens 64 --output /tmp/stream-delivery.json

python3 examples/python/stream_delivery_benchmark.py --app-token \
  --model lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit \
  --runs 2 --clients 2 --max-tokens 64 --output /tmp/concurrent-delivery.json
```

The scripts send synthetic prompts, do not execute model tools, and do not record
credentials or response text. They can cause the selected cached model to load.
They do not unload an already resident model to manufacture a cold result.

For the research-only A/B experiment, run with MLX dependencies installed and
weights already cached; it does not use the resident daemon:

```sh
python3 examples/python/prefix_checkpoint_benchmark.py \
  --model lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit \
  --runs 3 --output /tmp/prefill-checkpoints.json
```

Verification completed:

- Python: 605 tests run, 19 skipped, no failures. Tests cover authentication,
  SSE/NDJSON relay delivery, split UTF-8, protocol fragments, multiple tool calls,
  first-word preservation, ordered Anthropic blocks, cancellation, resource reuse,
  and simultaneous native/Anthropic requests with mock replicas.
- Swift: 96 tests, no failures, including presentation throttling and client
  timing preservation. Native Release build and local ad-hoc signature validation
  also passed. This is not Apple notarization or a public DMG release.

Still unverified or unimplemented in this change: actual Claude Desktop/Code
end-to-end timings, remote-host performance, continuous batching, cache-aware host
selection, Muse draft-model decoding, and large-context quality/latency sweeps.
Resident weights alone do not remove uncached prefill, reasoning, or queue costs.
