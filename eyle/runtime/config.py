"""Rev4.12 configuration boundary for the observable AgentSession core."""
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
        ("max_write_investigation_turns", 2), ("max_no_progress_turns", 2),
        ("task_context_token_budget", 500),
        ("max_project_scan_entries", 20000), ("max_project_scan_depth", 32),
        ("max_project_file_bytes", 4194304), ("max_inspect_relation_edges", 60),
        ("max_llm_calls", 8), ("max_prompt_tokens", 12000),
        ("max_completion_tokens", 6000), ("max_total_tokens", 18000),
    ):
        value = agent.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f"agent.{key} precisa ser inteiro positivo")
    phase_violations = agent.get("max_phase_violations", 1)
    if not isinstance(phase_violations, int) or phase_violations < 0:
        raise ConfigError("agent.max_phase_violations precisa ser inteiro não negativo")
    quality = agent.get("response_quality") or {}
    if not isinstance(quality, dict):
        raise ConfigError("agent.response_quality precisa ser um objeto")
    for key in ("enabled", "reject_mid_list_corrections"):
        if key in quality and not isinstance(quality.get(key), bool):
            raise ConfigError(f"agent.response_quality.{key} precisa ser booleano")
    for key, default in (("max_relevant_sources", 4), ("max_relevant_source_chars", 8000)):
        value = quality.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(
                f"agent.response_quality.{key} precisa ser inteiro positivo"
            )
    context = config.get("context_engine") or {}
    cached_weight = context.get("cached_prompt_weight", 0.2)
    if not isinstance(cached_weight, (int, float)) or not 0 <= float(cached_weight) <= 1:
        raise ConfigError("context_engine.cached_prompt_weight precisa estar entre 0 e 1")
    for key, default in (("safety_margin_tokens", 500), ("chars_per_token_fallback", 3)):
        value = context.get(key, default)
        if not isinstance(value, int) or value < 1:
            raise ConfigError(f"context_engine.{key} precisa ser inteiro positivo")
    return config


def carregar_config_validada(path):
    with open(path, "r", encoding="utf-8") as handle:
        return validar_config(json.load(handle))
