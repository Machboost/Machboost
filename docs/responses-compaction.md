# Responses compaction

MachBoost supports standalone `POST /v1/responses/compact` and streamed
`POST /v1/responses` with a final `compaction_trigger` input item. Both accept
zstd-compressed JSON. A trigger must be unique and use `stream: true`.

The server summarizes older text with the selected model and retains the last
four items, system/developer instructions, visual inputs, and all tool state
verbatim. Keeping all tool state avoids splitting calls from their results,
but limits reduction for tool-heavy histories. Empty, truncated, or failed
summaries return an error instead of a successful compaction response.

Pass the returned `output` items into the next Responses request. MachBoost
expands its compaction item into summary context and retained items before
rendering the model prompt. Foreign or malformed envelopes are rejected.
The protocol field is called `encrypted_content`, but the MachBoost-owned,
versioned JSON stored there is **not encrypted**. Treat it as sensitive chat
history. No server-side conversation archive is needed to expand it.

Desktop integration leaves the built-in provider selected to preserve account
controls. The loopback relay returns 426 for WebSocket upgrades so compatible
clients can fall back to HTTP SSE. This is HTTP compatibility, not a WebSocket
implementation.

Reference implementation inspected: Ollama commit
`6383a0fa9cbf97494b847226e189f6e36b401a08`, specifically
`server/responses_compact.go`, `openai/responses_compact.go`, and
`cmd/launch/codex_app.go`. MachBoost uses its own conservative retention policy.

Local verification: Gemma 4 26B completed compressed standalone compaction,
recovered a project codename and test command on a subsequent request, and
returned the five streamed compaction events. This is a protocol smoke test,
not an exhaustive summary-quality evaluation or a desktop UI acceptance test.
