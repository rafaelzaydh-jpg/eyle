"""Rev4.11.2 configuration boundary for the single AgentSession core."""
from __future__ import annotations

import json


class ConfigError(ValueError):
    pass


_REMOVED = {
    "semantic_grounding", "project_read_finalizer_enabled",
    "project_read_fast_path_enabled", "target_coverage_enabled",
    "project_read_single_repair_enabled", "intent_output_gate_enabled",
    "audit_optional_expansion_enabled", "audit_single_repair_enabled",
    "deterministic_symbol_lookup_enabled", "max_steps",
    "max_no_progress_steps", "max_repeated_action_warnings",
}


def validar_config(config):
    if not isinstance(config, dict):
        raise ConfigError("config precisa ser um objeto")
    llm = config.get("llm") or {}
    if "stream_responses" in llm and not isinstance(llm.get("stream_responses"), bool):
        raise ConfigError("llm.stream_responses precisa ser booleano")
    agent = config.get("agent") or {}
    bad = sorted(set(agent) & _REMOVED)
    if bad:
        raise ConfigError("configuração removida no core AgentSession: " + ", ".join(bad))
    for key, default in (
        ("max_llm_turns", 6), ("max_tool_calls", 12),
        ("max_identical_tool_repeats", 2), ("protocol_parse_retries", 1),
        ("max_patch_dry_run_failures", 2),
        ("max_llm_calls", 8), ("max_prompt_tokens", 12000),
        ("max_completion_tokens", 6000), ("max_total_tokens", 18000),
    ):
        value = agent.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f"agent.{key} precisa ser inteiro positivo")
    context = config.get("context_engine") or {}
    for key, default in (("safety_margin_tokens", 500), ("chars_per_token_fallback", 3)):
        value = context.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f"context_engine.{key} precisa ser inteiro positivo")
    return config


def carregar_config_validada(path):
    with open(path, "r", encoding="utf-8") as handle:
        return validar_config(json.load(handle))
