"""Deterministic composed cognitive-surface measurement for Rev4."""
from __future__ import annotations

import json
from typing import Any, Dict

from eyle.core.ecc import navigation_directory, surface_catalog
from eyle.core.memory import memory_environment
from eyle.runtime.ecc_runtime import available_internal
from eyle.runtime.token_budget import estimate_tokens
from llm.executar import PROMPT_NAVIGATION, PROMPT_EXPLORE, PROMPT_BUILD
from llm.structured import wire_schema_for_profile


def _measure(pieces: Dict[str, Any], chars_per_token: int) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    total_chars = 0
    total_tokens = 0
    for name, value in pieces.items():
        encoded = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), default=str
        )
        chars = len(encoded)
        tokens = estimate_tokens(encoded, chars_per_token)
        metrics[name] = {"characters": chars, "estimated_tokens": tokens}
        total_chars += chars
        total_tokens += tokens
    return {
        "characters": total_chars,
        "estimated_tokens": total_tokens,
        "components": metrics,
    }


def measure_static_cognitive_floor(
    config: Dict[str, Any],
    registry: Any,
    provider_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Measure each Rev4 protocol surface independently.

    Persisted state is not summed into one fictional mega-prompt. The primary
    top-level metric is Navigation, while ``surfaces`` exposes the three exact
    physical prompt contracts for benchmark comparison.
    """
    available = available_internal(registry, config, provider_context)
    memory_enabled = bool((provider_context or {}).get("core_memory"))
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
        "active_task": {},
        "memory_environment": memory_environment(provider_context),
        "memory_view": {"available": memory_enabled, "nodes": [], "edges": []},
        "exploration_map": [],
        "mechanical_coverage": {},
        "execution_convergence": {},
        "latest_observations": [],
        "runtime_effects": [],
        "turn": 0,
        "runtime_feedback": [],
    }
    surfaces = {
        "navigation": {
            "system": PROMPT_NAVIGATION.rstrip(),
            "provider_wire_schema": wire_schema_for_profile("navigation"),
            "capability_surface": navigation_directory(
                registry, config, available, memory_enabled=memory_enabled
            ),
            "runtime_environment": {
                "capabilities_available": bool(available),
                "memory_available": memory_enabled,
            },
            "minimal_runtime_packet": minimal_runtime_packet,
        },
        "explore": {
            "system": PROMPT_EXPLORE.rstrip(),
            "provider_wire_schema": wire_schema_for_profile("explore"),
            "capability_surface": surface_catalog(
                registry, config, available, surface="explore", memory_enabled=memory_enabled
            ),
            "runtime_environment": runtime_environment,
            "minimal_runtime_packet": minimal_runtime_packet,
        },
        "build": {
            "system": PROMPT_BUILD.rstrip(),
            "provider_wire_schema": wire_schema_for_profile("build"),
            "capability_surface": surface_catalog(
                registry, config, available, surface="build", memory_enabled=memory_enabled
            ),
            "runtime_environment": runtime_environment,
            "minimal_runtime_packet": minimal_runtime_packet,
        },
    }
    chars_per_token = max(
        1,
        int(((config or {}).get("context_engine") or {}).get("chars_per_token_fallback", 3) or 3),
    )
    measured = {name: _measure(pieces, chars_per_token) for name, pieces in surfaces.items()}
    primary = dict(measured["navigation"])
    primary["chars_per_token"] = chars_per_token
    primary["surfaces"] = measured
    return primary
