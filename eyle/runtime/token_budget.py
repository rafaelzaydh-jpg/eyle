"""Shared token-accounting helpers for Eyle.

Local estimates exist for telemetry and for an OPTIONAL operator-declared model
context window. Eyle deliberately has no default prompt-size fuse: when no
local context window is configured, Eyle forwards the complete canonical packet
and lets the Adapter/provider enforce its real model window.
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


def configured_context_window(config: dict) -> int | None:
    raw = ((config or {}).get("llm") or {}).get("context_window_tokens")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def available_user_prompt_tokens(
    config: dict,
    system_prompt: str,
    *,
    output_tokens: int = 0,
    token_estimate_multiplier: float = 1.0,
) -> int | None:
    """Return a local prompt budget only when the operator declared a window.

    ``None`` means "no local context fuse". This is the default.
    Tests/special deployments may still declare ``context_window_tokens`` to
    exercise deterministic context fitting.
    """
    window = configured_context_window(config)
    if window is None:
        return None
    context = (config or {}).get("context_engine") or {}
    chars_per_token = max(1, int(context.get("chars_per_token_fallback", 3) or 3))
    margin = max(0, int(context.get("safety_margin_tokens", 500) or 0))
    system_tokens = estimate_tokens(system_prompt, chars_per_token)
    try:
        multiplier = min(4.0, max(0.75, float(token_estimate_multiplier or 1.0)))
    except (TypeError, ValueError):
        multiplier = 1.0
    capacity = int(max(0, window - margin - max(0, int(output_tokens or 0))) / multiplier)
    return max(0, capacity - system_tokens)
