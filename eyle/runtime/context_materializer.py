"""Deterministic physical context materialization for Rev3.7.1.

This module does not rank semantic relevance. It only serializes facts identified
by Runtime identities and budgets: current conversation, explicit Memory
activation, latest observations/effects and bounded runtime feedback.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict

from eyle.runtime.token_budget import estimate_tokens


DEFAULT_CONVERSATION_BUDGET_TOKENS = 1200
DEFAULT_OBSERVATION_BUDGET_TOKENS = 2200
DEFAULT_FEEDBACK_BUDGET_TOKENS = 320


def _budget(config: Dict[str, Any], name: str, default: int) -> int:
    context = (config or {}).get("context_engine") or {}
    try:
        return max(0, int(context.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _chars_per_token(config: Dict[str, Any]) -> int:
    context = (config or {}).get("context_engine") or {}
    try:
        return max(1, int(context.get("chars_per_token_fallback", 3) or 3))
    except (TypeError, ValueError):
        return 3


def materialize_conversation(conversation_context: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    """Materialize the newest physically fitting messages, never a fixed count."""
    raw = conversation_context if isinstance(conversation_context, dict) else {}
    messages = [copy.deepcopy(v) for v in raw.get("recent_messages") or [] if isinstance(v, dict)]
    total = int(raw.get("total_messages") or len(messages))
    conversation_id = str(raw.get("conversation_id") or "").strip() or None
    chars_per_token = _chars_per_token(config)
    budget = _budget(config, "conversation_materialization_tokens", DEFAULT_CONVERSATION_BUDGET_TOKENS)

    selected_reversed = []
    used = 0
    for item in reversed(messages):
        compact = {
            "role": str(item.get("role") or ""),
            "content": str(item.get("content") or ""),
        }
        if isinstance(item.get("execution_failure"), dict) and item["execution_failure"]:
            compact["execution_failure"] = copy.deepcopy(item["execution_failure"])
        cost = estimate_tokens(compact, chars_per_token)
        if selected_reversed and used + cost > budget:
            break
        if not selected_reversed and cost > budget and budget > 0:
            # Preserve the most recent message identity/content even when it is
            # individually larger than the conversation slice budget. The outer
            # prompt fitter remains the physical provider-window authority.
            compact["content"] = compact["content"][: max(256, budget * chars_per_token)]
            cost = estimate_tokens(compact, chars_per_token)
        if budget == 0:
            break
        selected_reversed.append(compact)
        used += cost

    selected = list(reversed(selected_reversed))
    materialized = len(selected)
    omitted = max(0, total - materialized)
    out: Dict[str, Any] = {
        "conversation_id": conversation_id,
        "messages": selected,
        "history_messages_materialized": materialized,
        "history_messages_omitted": omitted,
        "estimated_tokens": used,
    }
    if omitted:
        # Older chat is persisted in Memory Graph domain=chat and remains
        # mechanically reachable through explicit memory_activate/recall.
        out["older_history"] = {
            "available": True,
            "count": omitted,
            "continuation": "memory_activate",
            "domain": "chat",
            "context_key": conversation_id,
        }
    return out


def materialize_latest_observations(results: Any, config: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Bound observation bodies by physical token budget, newest first.

    Provider-format repair belongs to the Adapter. If Eyle asks Main for one
    fresh current decision after a wire failure, existing observations remain
    available so task progress is preserved.
    """
    items = [copy.deepcopy(v) for v in results or [] if isinstance(v, dict)]
    if not items:
        return []
    chars_per_token = _chars_per_token(config)
    budget = _budget(config, "observation_materialization_tokens", DEFAULT_OBSERVATION_BUDGET_TOKENS)
    selected_reversed = []
    used = 0
    for item in reversed(items):
        cost = estimate_tokens(item, chars_per_token)
        if selected_reversed and used + cost > budget:
            break
        selected_reversed.append(item)
        used += cost
        if used >= budget:
            break
    return list(reversed(selected_reversed))



def materialize_runtime_feedback(feedback: Any, config: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Materialize newest active Runtime feedback under a physical token budget."""
    items = [copy.deepcopy(v) for v in feedback or [] if isinstance(v, dict)]
    if not items:
        return []
    chars_per_token = _chars_per_token(config)
    budget = _budget(config, "runtime_feedback_materialization_tokens", DEFAULT_FEEDBACK_BUDGET_TOKENS)
    if budget <= 0:
        return []
    selected_reversed = []
    used = 0
    for item in reversed(items):
        cost = estimate_tokens(item, chars_per_token)
        if selected_reversed and used + cost > budget:
            break
        if not selected_reversed and cost > budget:
            # Preserve the newest feedback identity/code; crop only verbose detail.
            compact = copy.deepcopy(item)
            for key in ("detail", "guidance"):
                if isinstance(compact.get(key), str):
                    compact[key] = compact[key][: max(120, budget * chars_per_token // 2)]
            item = compact
            cost = estimate_tokens(item, chars_per_token)
        selected_reversed.append(item)
        used += cost
        if used >= budget:
            break
    return list(reversed(selected_reversed))

def component_metrics(packet: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    chars_per_token = _chars_per_token(config)
    out: Dict[str, Dict[str, int]] = {}
    for name, value in packet.items():
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        metric = {"characters": len(encoded), "estimated_tokens": estimate_tokens(encoded, chars_per_token)}
        if isinstance(value, (list, dict)):
            metric["items"] = len(value)
        out[name] = metric
    return out
