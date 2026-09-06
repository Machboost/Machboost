# Coding-client latency

Investigation date: 2026-09-05. Source baseline: `4a87f55`.
This is a source audit and implementation proposal, not a benchmark report.
No inference experiments or application tests were run for this investigation.

## Separate computation from delivery

Measure these intervals independently:

- Request arrival, authentication, translation, and host selection.
- Queue wait and model load/compile, when required.
- Cache lookup/restore and uncached prompt processing (prefill).
- First generated token, including model reasoning and protocol tokens.
- First reasoning event, first visible answer text, and first complete tool call.
- Client receipt and rendering of those events.
- Full turn completion, including tool execution and subsequent model requests.

Headers, heartbeats, and empty message-start events are not first tokens. A good
backend TTFT can coexist with a slow user-visible response if delivery buffers
output. Report that as a transport problem, not an inference speedup.

Claude Code resends its context on each request. Its server-side cache behavior
depends on the gateway serving the model. Keeping model weights resident does not
by itself preserve processed conversation state.
[Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching).

## Findings in the current implementation

### 1. Shared-host relay can buffer the stream

`LoopbackRelayHandler._proxy` in `machboost/relay.py` calls
`HTTPResponse.read(64 * 1024)` before writing and flushing downstream. On a
blocking response, this can wait for the requested amount or response completion,
combining small events into a delayed batch. The same code is present in the
installed application's runtime at the time of this audit.

Replace the fill-sized read with `HTTPResponse.read1`, which returns buffered
bytes or performs a limited underlying read. Keep forwarding arbitrary byte
fragments; do not decode each transport chunk independently because UTF-8 and SSE
events can span reads. The existing relay test checks the final combined body,
not whether the first event arrives before the upstream finishes.
[Python buffered I/O semantics](https://docs.python.org/3/library/io.html#io.BufferedIOBase.read1).

This is a concrete delivery defect to address, but its contribution to a particular
reported delay has not been measured. It affects requests traversing the relay,
not direct connections to the native daemon.

### 2. Tool-enabled Anthropic requests suppress answer-text streaming

`handle_anthropic_message` in `machboost/server.py` supplies
`emit=None if prepared.options.get("_tools") else emit`. Consequently, merely
providing tools disables incremental answer text, even if no tool is used.
Reasoning has a separate streaming callback; final text and tool blocks are
emitted after `runtime.chat` returns.

Use a protocol-aware incremental decoder that emits ordered reasoning, prose,
and tool argument deltas. Preserve incomplete protocol prefixes until they can be
classified; never expose raw tool syntax as prose. Close each Anthropic content
block correctly before advancing to another. Keep actual tool execution behind
complete, validated arguments and the client's permission decision.

`ToolAwareTextStream` already offers limited prose filtering elsewhere, but it
reparses accumulated text and does not provide a complete incremental tool-event
contract. Do not simply enable the raw callback and reintroduce missing-text or
tool-markup regressions.

### 3. VLM requests occupy the worker for the whole generation

`MLXVLMService` has a single-worker executor and a class-wide generation lock.
`ReplicaPool` leases resources for entire requests. This is bounded request
queueing, not continuous token batching. Another long generation can delay an
interactive turn even when weights are resident. The title-helper shortcut and
separate agent/utility cache affinity already mitigate some Claude helper traffic.

Investigate a single GPU-owning event loop with per-request state and bounded
prefill work between decode steps. Use supported MLX-VLM batch primitives, with
architecture-specific gates for recurrent, rotating, and multimodal caches.
Removing the lock or increasing threads alone is not a safe implementation.
Queue priority must include aging so background work cannot starve.
[MLX-VLM](https://github.com/Blaizzy/mlx-vlm) and
[Sarathi-Serve](https://arxiv.org/abs/2403.02310) provide relevant designs; their
throughput results are not evidence of a MachBoost speedup.

### 4. Routing does not estimate reusable prompt state

`expected_delay_score` in `machboost/routing.py` considers round-trip time, load,
queue, replica count, and whether weights are loaded. It does not estimate the
requested conversation's cached prefix. An otherwise idle host can be slower
than the current host if moving the request requires substantial prefill.

Proposed scoring should include estimated unmatched tokens and cache-restore
cost, with session affinity and hysteresis. Fall back before emitting output,
not halfway through an answer. Cache ownership must come from authenticated
identity and explicit workspace-sharing policy, independently of client-supplied
routing affinity. Do not share private histories or blindly move KV tensors
between machines, model revisions, quantizations, or runtime versions.

### 5. Existing prompt reduction is not lossless inference

`compact_claude_code_tools` shortens descriptions and strips schema annotations.
`_compact_claude_code_system` substitutes a short coding instruction for recognized
harness text. Tool selection can omit schemas. These transformations change model
input and may change tool choice or behavior. They must be separately disclosed
and evaluated; token savings from them are not same-input acceleration.

Prefer exact, stable prefix reuse first. Upstream tool search offers a way to load
fewer definitions when the client and model support it, but needs an explicit
discovery contract. Existing MachBoost MCP discovery should not be assumed to
implement Claude's native tool-search protocol.
[Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use).

## Actual generation acceleration

Muse Glimmer has a dedicated DFlash assistant. Current MLX-VLM documentation lists
the Muse target/assistant pair for text and image inputs, and the installed runtime
contains its drafter implementation. MachBoost's regular VLM adapter does not pass
a draft model. The separate MachBoost DFlash backend and its Qwen results do not
establish Muse support or speed.
[Muse assistant model card](https://huggingface.co/meta-models/Muse-Glimmer-30B-assistant/blob/main/README.md),
[MLX-VLM speculative usage](https://github.com/Blaizzy/mlx-vlm/blob/main/docs/usage.md).

The next decode experiment should retain the same Muse target weights, tokenizer,
template, request, and sampling policy while adding only the paired drafter. Measure
accepted tokens per verification, verification cost, output correctness, memory,
and full-turn time. Start with greedy generation, then evaluate supported sampling
separately. Quantization and finite-precision dispatch can affect acceptance and
output equality. Use an explicit opt-in and revert to ordinary decoding when
measured draft overhead outweighs saved target steps.

This addresses generation after prefill, including unique outputs. It is not an
answer cache and does not eliminate the first cold prompt pass. Long generations
are a more plausible beneficiary than short greetings.

For genuinely cold, long prompts, investigate compute kernels separately. BaseRT
reports improved prefill using M5 Metal tensor operations, but its measurements
are not portable to every Mac or architecture. Its public bindings are open
source while its engine is separately licensed. Treat it as a reference and
comparison candidate, not code available to copy into MachBoost.
[BaseRT paper](https://arxiv.org/abs/2607.19438),
[BaseRT repository and licensing](https://github.com/basecompute/baseRT).

Non-prefix reuse methods such as CacheBlend selectively recompute attention when
combining cached document state. They are a separate, approximate research track,
not permission to splice arbitrary file KV caches while claiming exact output.
[CacheBlend](https://arxiv.org/abs/2405.16444).

## Implementation and verification order

1. Repair relay buffering; add a gated upstream stream that cannot finish until
   the client has received its first event. Cover SSE, NDJSON, known-length,
   close-delimited, and chunked responses, disconnects, and authentication.
2. Add ordered Anthropic streaming for tool-enabled requests. Cover ordinary prose,
   reasoning, multiple calls, fragmented JSON/UTF-8, cancellation, and tool results.
3. Instrument actual Claude Desktop and Claude Code sessions. Replay the same
   authorized request through the direct daemon and relay; record client-visible
   timestamps, not only backend metrics. Do not save credentials or repository
   content by default.
4. Add cache-aware routing and continuous scheduling as separate changes. Evaluate
   same-session turns, unrelated helper requests, and 1/2/5/10 concurrent clients.
5. Evaluate Muse's native drafter independently before combining it with scheduling.

Use cold-model, warm-model/cold-prefix, and warm-prefix cases separately. Include
short chat, 8K/32K coding inputs, tool loops, and an image task. Preserve the same
target weights and input semantics for algorithm comparisons. Compare p50/p95
client-visible latency, inter-token gaps, complete-task time, valid tool calls,
task success, and memory. Report cache reuse and load/compile intervals explicitly.
Use new user questions at the suffix for warm-prefix trials; randomizing the
system prefix is a deliberate cold-cache control, not a realistic follow-up.

No latency or universal speedup target is claimed as achieved by this audit.
