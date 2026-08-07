"""Shared token-budget helpers for isolated 10k-context operation.

The runtime uses conservative character estimates only when the provider does
not expose token counts. This module intentionally contains no retrieval or
semantic-selection logic.
"""
from __future__ import annotations

import json
from typing import Any


def estimate_tokens(value: Any, chars_per_token: int = 3) -> int:
    chars_per_token = max(1, int(chars_per_token or 3))
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    return (len(text) + chars_per_token - 1) // chars_per_token


def available_user_prompt_tokens(
    config: dict,
    system_prompt: str,
    *,
    output_tokens: int,
) -> int:
    """Return the safe user-prompt budget for one backend request."""
    llm = (config or {}).get("llm") or {}
    context = (config or {}).get("context_engine") or {}
    chars_per_token = max(1, int(context.get("chars_per_token_fallback", 3) or 3))
    window = max(1, int(llm.get("context_window_tokens", 8192) or 8192))
    margin = max(0, int(context.get("safety_margin_tokens", 500) or 0))
    system_tokens = estimate_tokens(system_prompt, chars_per_token)
    return max(0, window - margin - max(0, int(output_tokens or 0)) - system_tokens)

