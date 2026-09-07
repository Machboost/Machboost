"""Translate an uncapped request without imposing a short response budget."""
from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any


def output_token_limit(requested: int, *, model: Any, tokenizer: Any, prompt_tokens: int) -> int:
    if requested >= 0:
        return requested
    if requested not in {-1, -2}:
        raise ValueError("max_tokens must be nonnegative or -1 (no output cap)")

    def value(source: Any, name: str) -> Any:
        return source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)

    sources = [value(model, "config"), value(model, "args"), tokenizer]
    for source in tuple(sources):
        sources.extend(value(source, name) for name in ("text_config", "language_config"))
    limits = []
    for source in sources:
        for name in ("max_position_embeddings", "model_max_length", "max_sequence_length"):
            limit = value(source, name)
            # HF uses extremely large integers to mean an unspecified context length.
            if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit < 100_000_000:
                limits.append(limit)
    if not limits:
        return sys.maxsize
    remaining = min(limits) - prompt_tokens
    if remaining < 1:
        raise ValueError("The prompt fills the model context window; shorten or summarize it.")
    return remaining
