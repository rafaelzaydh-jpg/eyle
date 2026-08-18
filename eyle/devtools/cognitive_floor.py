"""Deterministic composed prompt-floor measurement for Rev3.7.1."""
from __future__ import annotations

import json
from typing import Any, Dict

from eyle.core.ecc import catalog as ecc_catalog
from eyle.core.memory import memory_environment
from eyle.runtime.ecc_runtime import available_internal
from eyle.runtime.token_budget import estimate_tokens
from llm.executar import PROMPT_ECC
from llm.structured import contract_instruction


def measure_static_cognitive_floor(
    config: Dict[str, Any],
    registry: Any,
    provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Measure the composed floor, not isolated component budgets."""
    available = available_internal(registry, config, provider_context)
    memory_enabled = bool((provider_context or {}).get("core_memory"))
    capabilities = ecc_catalog(registry, config, available, memory_enabled=memory_enabled)
    runtime_environment = registry.environment({
        "config": config or {},
        "provider_context": provider_context or {},
    })
    minimal_runtime_packet = {
        "current_request": "",
        "conversation": {
            "conversation_id": None,
            "messages": [],
            "history_messages_materialized": 0,
            "history_messages_omitted": 0,
        },
        "memory_environment": memory_environment(provider_context),
        "memory_view": {"available": memory_enabled, "nodes": [], "edges": []},
        "exploration_map": [],
        "latest_observations": [],
        "runtime_effects": [],
        "turn": 0,
        "runtime_feedback": [],
    }
    pieces = {
        "system": PROMPT_ECC.rstrip(),
        "contract": contract_instruction("ecc"),
        "capability_surface": capabilities,
        "runtime_environment": runtime_environment,
        "minimal_runtime_packet": minimal_runtime_packet,
    }
    chars_per_token = max(1, int(((config or {}).get("context_engine") or {}).get("chars_per_token_fallback", 3) or 3))
    metrics = {}
    total_chars = 0
    total_tokens = 0
    for name, value in pieces.items():
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        chars = len(encoded)
        tokens = estimate_tokens(encoded, chars_per_token)
        metrics[name] = {"characters": chars, "estimated_tokens": tokens}
        total_chars += chars
        total_tokens += tokens
    return {
        "characters": total_chars,
        "estimated_tokens": total_tokens,
        "chars_per_token": chars_per_token,
        "components": metrics,
    }
