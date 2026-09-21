"""MachBoost-owned Responses compaction envelopes (not encrypted)."""

import json
from typing import Any


def expand_items(items: Any) -> list[dict[str, Any]]:
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
        raise ValueError("compaction input must contain Responses items")
    expanded = []
    for item in items:
        if item.get("type") != "compaction":
            expanded.append(item)
            continue
        try:
            state = json.loads(item["encrypted_content"])
            if state.get("type") != "machboost_compaction" or state.get("version") != 1:
                raise ValueError("unsupported compaction envelope")
            summary, retained = state["summary"], state["retained"]
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("empty compaction summary")
            if not isinstance(retained, list) or not all(isinstance(x, dict) for x in retained):
                raise ValueError("invalid retained items")
            if any(x.get("type") in {"compaction", "compaction_trigger"} for x in retained):
                raise ValueError("nested compaction state is not supported")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                "Cannot restore this chat's compacted history: it is encrypted by another "
                "provider, unsupported, or damaged. Start a new chat with MachBoost selected "
                "and paste a plain-text summary, or resume this chat with its original provider. "
                "No history has been discarded."
            ) from exc
        expanded.append({"type": "message", "role": "assistant",
                         "content": "Conversation summary (historical context):\n" + summary})
        expanded.extend(retained)
    return expanded


def triggered(payload: dict[str, Any]) -> bool:
    items = payload.get("input")
    if not isinstance(items, list):
        return False
    positions = [i for i, x in enumerate(items) if isinstance(x, dict) and x.get("type") == "compaction_trigger"]
    if not positions:
        return False
    if positions != [len(items) - 1] or payload.get("stream") is not True:
        raise ValueError("compaction_trigger must be unique, final, and use stream=true")
    return True


def prepare(payload: dict[str, Any], *, stream: bool) -> tuple[list, list]:
    raw = payload.get("input", [])
    if stream:
        raw = raw[:-1]
    items = expand_items(raw)
    if payload.get("instructions"):
        items.insert(0, {"type": "message", "role": "system", "content": str(payload["instructions"])})
    if not items or any(x.get("type") == "compaction_trigger" for x in items):
        raise ValueError("invalid or empty compaction input")
    # Preserve recent context, instructions, visuals, and all tool state verbatim.
    # Conservative retention avoids orphaning calls, including unfinished calls.
    retained, older = [], []
    for i, item in enumerate(items):
        kind = item.get("type", "message")
        content = item.get("content")
        visual = isinstance(content, list) and any(
            isinstance(x, dict) and x.get("type") not in {"input_text", "output_text", "text"}
            for x in content)
        keep = (i >= len(items) - 4 or kind != "message" or
                item.get("role") in {"system", "developer"} or visual)
        (retained if keep else older).append(item)
    return older, retained


def envelope(summary: str, retained: list) -> dict[str, Any]:
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("compaction failed: empty summary; original conversation is unchanged")
    return {"type": "compaction", "encrypted_content": json.dumps({
        "type": "machboost_compaction", "version": 1,
        "summary": summary.strip(), "retained": retained,
    }, ensure_ascii=False)}
