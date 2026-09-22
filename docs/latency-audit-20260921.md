# Local latency audit: September 21, 2026

This audit corrects the earlier prefill-batching experiment. Repeated benchmark
invocations used the same nonce, and disk prefix caching could survive between
invocations. Those measurements did not establish a cold-prefill speedup. The
automatic 4096-token prefill override has been removed. The backend default is
used unless an operator explicitly sets `MACHBOOST_MLX_VLM_PREFILL_STEP`.

## Controlled measurements

Hardware: Apple Silicon Mac17,8, 48 GiB unified memory, macOS 27.0. Runtime:
MLX 0.32.0, mlx-vlm 0.6.13, mlx-lm 0.31.3. Only one inference process and one
copy of the target model were active. Model loading and an initial warmup are
outside the adapter measurements.

The native baseline calls `mlx_vlm.stream_generate` on the same loaded model
with no cross-request cache. MachBoost uses the same template, weights,
temperature, and token budget. Pair order alternates. Cold pairs start without
a reusable prefix; follow-ups have identical fixed histories in both paths.
These are adapter measurements, not Ollama or desktop-app comparisons.

### Gemma 4 26B A4B QAT 4-bit

Repository: `lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit`.
Three pairs, 5,522 cold input tokens, 5,549 follow-up input tokens, maximum 32
generated tokens, reasoning disabled in both paths. Medians:

| Path | Cold first output | Follow-up first output | Follow-up cached tokens |
| --- | ---: | ---: | ---: |
| Native MLX-VLM, no reuse | 7.202 s | 7.213 s | 0 |
| MachBoost, memory cache | 7.269 s | 0.207 s | 5,506 |
| MachBoost, disk persistence enabled | 7.342 s | 0.226 s | 5,506 |

The disk-enabled run used a fresh temporary disk directory. Follow-ups also had
memory state available: this is **not** a disk-only restore benchmark. Its
separately measured native control had 7.238 s cold / 7.219 s follow-up medians.

Cold token sequences matched in all pairs. Cached follow-up sequences matched
in **0/3** pairs in each cache mode. Both answers gave an email-validation
example, but wording and output lengths differed. This small example is not a
quality evaluation and does not justify claiming lossless or universal speedup.
Cached/chunked execution can change floating-point results and greedy choices.

A separate diagnostic using the same 5,522-token prompt and no cache measured
about 7.24 s first output at the native prefill default versus 7.64 s with the
automatic 4096 setting. That motivated reverting the unvalidated override, not
introducing a different global chunk-size heuristic.

### Muse Glimmer 30B 4-bit

Repository: `mlx-community/Muse-Glimmer-30B-4bit`. Three pairs, 1,450 cold input
tokens, 1,477 follow-up tokens, maximum 128 generated tokens, model-required
reasoning enabled at low in both paths.

| Path | Cold raw backend chunk | Cold user-visible output | Follow-up user-visible output |
| --- | ---: | ---: | ---: |
| Native MLX-VLM, no reuse | 12.190 s | Not separately parsed | Not separately parsed |
| MachBoost, memory cache | 11.609 s | 12.307 s | 1.106 s |

Native follow-up raw output took 12.576 s. MachBoost reused 1,451 prompt tokens
and its first raw chunk arrived in 0.582 s. Raw chunks can contain routing
markers or echoed input; they are not directly comparable to rendered output.
Generation was approximately 17 tokens/s in both paths. Cold outputs matched
3/3 pairs; cached follow-ups matched 0/3. Some outputs reached the test's token
cap, so these timings do not measure completed coding tasks.

## Installed application

Tested `/Applications/MachBoost.app`, not a UI-test build, with its updated
bundled runtime. A new chat was pinned to this Mac with no repository,
attachments, or connector tools. Existing conversations were left intact.

| Request | App time to first output | Result |
| --- | ---: | --- |
| `Latency audit: reply with exactly Hello there.` | 12.37 s | Complete `Hello there.` |
| `Now reply with exactly Goodbye.` | 1.08 s | Complete `Goodbye.` |
| `Reply with exactly You're welcome!` | 0.96 s | Complete `You're welcome!` |

The first request included a server-reported 6.974 s model load. The model
remained resident for the follow-ups. These are small-chat smoke checks, not
evidence that a long new coding prompt will also start in one second.

Separate HTTP tests exercised both `/api/chat` and `/v1/messages` on that same
installed daemon with a tool schema. They confirmed streaming and no reloads;
they are not Claude Desktop UI measurements. Prompts, cache state, and reasoning
time matter even when weights are resident.

After the September 22 authentication/usage fixes, two more requests in the
regular app produced complete answers: `Ready to go.` in 2.07 s to first output,
then `You're welcome!` in 1.38 s with 134/147 prompt tokens reused. These checks
used the same Muse model, local routing, and low reasoning.

## Actual Claude CLI check: September 22

Claude Code 2.1.267 ran a two-turn task against the installed app's Muse model:
read a synthetic `README.md`, then return its verification phrase. Only `Read`
was permitted. Hooks, plugins, external MCP servers, and session persistence
were disabled with `--bare`, `--strict-mcp-config`, and isolated settings. This
is an actual CLI test, not a simulated HTTP client or a Claude Desktop UI test.

The first attempt failed after 175.71 s with HTTP 401 and no model output.
MachBoost accepted bearer tokens but ignored `x-api-key` from this client.
That delay was authentication retries, **not TTFT**. Both header forms now use
the same key validation and team scopes. Explicit Authorization takes priority;
invalid or duplicate credentials cannot fall back to another header.
[Anthropic authentication reference](https://platform.claude.com/docs/en/manage-claude/authentication).

| Configuration | First output | First answer | Whole task | Outcome |
| --- | ---: | ---: | ---: | --- |
| Auth fixed, Read-only tool list, initial load | 16.05 s | 27.18 s | 27.28 s | Correct phrase, one Read call |
| Same task, resident model | 6.46 s | 17.58 s | 17.68 s | Correct phrase, one Read call |
| Final runtime, default tool list, resident | 12.06 s | 22.73 s | 22.83 s | Correct phrase, one Read call |
| Default tool list, resident repeat | 12.06 s | 22.65 s | 22.75 s | Correct phrase, one Read call |

Times include CLI startup. Initial-load and resident rows are not equivalent
cache states. The larger tool-list rows still permit only Read; they do not
establish latency on a large repository or with many external connectors.
There is still a material coding-client latency gap relative to short app chats.
No Ollama comparison was made here.

This check also exposed streaming input usage stuck at zero: `message_start`
reported zero before tokenization, and the final delta omitted input usage.
The final delta now includes measured prompt tokens, matching the non-streaming
response. Claude reports 1,299 input tokens for the Read-only task and 1,781
for the default-tool-list task across two model turns. These totals do not
separately report local prefix-cache hits or imply paid-token savings.

## Changes and remaining limits

- Benchmark invocation IDs are now fresh; nonces prevent exact repeats but do
  not disable shared-prefix caching.
- JSON adds `client_first_output_seconds`, `cached_prompt_tokens`, and
  `queue_seconds`. Existing v1 `client_ttft_seconds` retains its first-answer-text
  meaning; terminal output now labels it `first_answer`.
- Direct CLI chat retains one cache key across follow-ups, rotates it on
  `/clear`, and does not share it with unrelated sessions. It forwards the
  chosen reasoning strength when the backend accepts that parameter.
- Gemma turn delimiters are filtered in the shared stream parser as well as
  the server. Split delimiters and ordinary text are covered by tests.
- An isolated Muse last-position-only vocabulary projection experiment reduced
  first output from 13.135 s to 12.300 s (three repeated pairs, matching tokens).
  It is **not enabled in the app**: one fixture is insufficient to validate a
  model-forward rewrite, especially for speculative and visual paths.

The remaining cold-prompt delay is predominantly model prefill in these tests,
not an HTTP flush delay. Removing it needs a measured backend/kernel improvement
or less input computation, not a claim that residency eliminates prefill.
Multi-conversation cache eviction, queueing, long coding histories, and visual
inputs require separate workload coverage. No general 2x claim follows here.

Validation: the Python suite ran 687 tests (682 passed, five optional tests
skipped); Go tests passed; wheel packaging succeeded. The community app was updated
locally and its ad-hoc signature verified. No public release was created during
the audit; these fixes are included in [0.16.27](../release-notes/0.16.27.md).

## Reproduce

Run against cached weights after closing other local inference processes:

```sh
python examples/python/benchmark_vlm_latency.py \
  --model lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit \
  --runs 3 --context-lines 400 --max-tokens 32 --cache memory \
  --output /tmp/gemma-latency.json
```

Use `--cache disk` to test persistence overhead in an isolated temporary cache.
For Muse, use `--model mlx-community/Muse-Glimmer-30B-4bit --context-lines 128
--max-tokens 128`. The script reports token hashes and equality, not just time.

The Claude CLI check used the command below in a temporary directory containing
only the synthetic README fixture recorded in the artifact. Set
`ANTHROPIC_BASE_URL` to the local daemon and `ANTHROPIC_API_KEY` to its valid key;
leave `ANTHROPIC_AUTH_TOKEN` unset to exercise API-key-header authentication.
Do not place credentials in command arguments or commit them.

```sh
claude --bare -p 'Read README.md and reply with only its verification phrase.' \
  --model mlx-community/Muse-Glimmer-30B-4bit \
  --tools Read --allowedTools Read \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources '' \
  --no-session-persistence --max-turns 3 --effort low \
  --output-format stream-json --verbose --include-partial-messages
```

Omit `--tools Read` for the default-tool-list case, retaining `--allowedTools Read`.
Measure from process start to the first text/thinking/tool event, not the initial
session metadata event. Whole-task time ends when the process exits.

Raw artifacts:

- [Gemma memory cache](../results/vlm-latency-gemma-memory-20260921.json)
- [Gemma disk-enabled cache](../results/vlm-latency-gemma-disk-20260921.json)
- [Muse memory cache](../results/vlm-latency-muse-memory-20260921.json)
- [Installed daemon HTTP streams](../results/vlm-latency-installed-stream-20260921.json)
- [Actual Claude CLI tasks](../results/claude-cli-latency-20260922.json)
