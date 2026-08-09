"""Rev5.2.3 configuration boundary for the canonical AgentSession core."""
from __future__ import annotations

import json

from eyle.core.claim_review import ClaimConfigError, claim_config


class ConfigError(ValueError):
    pass


_TOP_LEVEL_FIELDS = {
    "llm", "context_engine", "web", "codar", "confirmacoes", "agent",
    "worker", "telemetry", "app_version", "config_schema_version", "revision",
}
_LLM_FIELDS = {
    "base_url", "model", "openai_compatible", "temperature", "max_tokens",
    "context_window_tokens", "connect_timeout_seconds", "read_timeout_seconds",
    "model_discovery_timeout_seconds", "model_discovery_negative_ttl_seconds",
    "retry_max_attempts", "agent_retry_max_attempts", "retry_base_delay_seconds",
    "retry_max_delay_seconds", "retry_jitter_seconds", "max_concurrent_requests",
    "cooldown_seconds", "retry_read_timeouts", "stream_responses",
    "truncation_retry_multiplier", "truncation_retry_max_tokens",
    "agent_decision_max_tokens", "agent_patch_max_tokens", "agent_analysis_max_tokens",
}
_AGENT_FIELDS = {
    "max_tree_entries", "max_tree_depth", "max_read_range_lines",
    "task_deadline_seconds", "max_llm_calls", "max_secret_scan_bytes",
    "max_prompt_tokens", "max_completion_tokens", "max_total_tokens",
    "chat_history_token_budget", "final_validation_retries", "max_llm_turns",
    "max_tool_calls", "max_identical_tool_repeats", "max_patch_dry_run_failures",
    "context_view", "max_write_investigation_turns",
    "max_no_progress_turns", "max_phase_violations", "max_project_scan_entries",
    "max_project_scan_depth", "max_project_file_bytes", "max_inspect_relation_edges",
    "max_git_diff_chars", "max_search_matches", "max_search_ranges", "claims",
    "max_search_range_lines", "structured_protocol_retries",
}
_CONTEXT_VIEW_FIELDS = {
    "max_relevant_sources", "max_relevant_source_chars",
    "max_symbol_preview_chars", "max_search_source_chars",
}
_CONTEXT_FIELDS = {
    "safety_margin_tokens", "chars_per_token_fallback", "cached_prompt_weight",
    "working_set_target_tokens",
}
_CODAR_FIELDS = {"ativado", "testes"}
_AGENT_POSITIVE_DEFAULTS = {
    "max_llm_turns": 8,
    "max_tool_calls": 12,
    "max_identical_tool_repeats": 2,
    "max_patch_dry_run_failures": 2,
    "max_write_investigation_turns": 2,
    "max_no_progress_turns": 2,
    "chat_history_token_budget": 700,
    "max_project_scan_entries": 20000,
    "max_project_scan_depth": 32,
    "max_project_file_bytes": 4194304,
    "max_inspect_relation_edges": 60,
    "max_search_range_lines": 16,
    "max_read_range_lines": 400,
    "max_tree_entries": 200,
    "max_tree_depth": 6,
    "max_secret_scan_bytes": 65536,
    "max_git_diff_chars": 6000,
    "max_search_matches": 40,
    "max_search_ranges": 12,
    "task_deadline_seconds": 900,
    "max_llm_calls": 12,
    "max_prompt_tokens": 96000,
    "max_completion_tokens": 9000,
    "max_total_tokens": 105000,
}
_AGENT_NONNEGATIVE_DEFAULTS = {
    "structured_protocol_retries": 1,
    "final_validation_retries": 1,
    "max_phase_violations": 1,
}
_CONTEXT_VIEW_POSITIVE_DEFAULTS = {
    "max_relevant_sources": 4,
    "max_relevant_source_chars": 3500,
    "max_search_source_chars": 600,
    "max_symbol_preview_chars": 2600,
}


def _validate_int(container, key, default, *, minimum, prefix):
    value = container.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        label = "inteiro positivo" if minimum == 1 else "inteiro não negativo"
        raise ConfigError(f"{prefix}.{key} precisa ser {label}")


def _validate_positive_number(container, key, default, prefix):
    value = container.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0:
        raise ConfigError(f"{prefix}.{key} precisa ser numérico positivo")


def _reject_unknown(container, allowed, prefix):
    unknown = sorted(set(container) - set(allowed))
    if unknown:
        raise ConfigError(f"UNKNOWN_CONFIG_FIELD:{prefix}:" + ",".join(unknown))


def validar_config(config):
    if not isinstance(config, dict):
        raise ConfigError("config precisa ser um objeto")
    _reject_unknown(config, _TOP_LEVEL_FIELDS, "root")

    llm = config.get("llm") or {}
    if not isinstance(llm, dict):
        raise ConfigError("llm precisa ser um objeto")
    _reject_unknown(llm, _LLM_FIELDS, "llm")
    if "stream_responses" in llm and not isinstance(llm.get("stream_responses"), bool):
        raise ConfigError("llm.stream_responses precisa ser booleano")
    for key, default in (
        ("connect_timeout_seconds", 5),
        ("read_timeout_seconds", 120),
        ("model_discovery_timeout_seconds", 3),
    ):
        _validate_positive_number(llm, key, default, "llm")

    codar = config.get("codar") or {}
    if not isinstance(codar, dict):
        raise ConfigError("codar precisa ser um objeto")
    _reject_unknown(codar, _CODAR_FIELDS, "codar")

    agent = config.get("agent") or {}
    if not isinstance(agent, dict):
        raise ConfigError("agent precisa ser um objeto")
    _reject_unknown(agent, _AGENT_FIELDS, "agent")
    for key, default in _AGENT_POSITIVE_DEFAULTS.items():
        _validate_int(agent, key, default, minimum=1, prefix="agent")
    for key, default in _AGENT_NONNEGATIVE_DEFAULTS.items():
        _validate_int(agent, key, default, minimum=0, prefix="agent")

    context_view = agent.get("context_view") or {}
    if not isinstance(context_view, dict):
        raise ConfigError("agent.context_view precisa ser um objeto")
    _reject_unknown(context_view, _CONTEXT_VIEW_FIELDS, "agent.context_view")
    for key, default in _CONTEXT_VIEW_POSITIVE_DEFAULTS.items():
        _validate_int(context_view, key, default, minimum=1, prefix="agent.context_view")

    context = config.get("context_engine") or {}
    if not isinstance(context, dict):
        raise ConfigError("context_engine precisa ser um objeto")
    _reject_unknown(context, _CONTEXT_FIELDS, "context_engine")
    cached_weight = context.get("cached_prompt_weight", 0.2)
    if not isinstance(cached_weight, (int, float)) or isinstance(cached_weight, bool) or not 0 <= float(cached_weight) <= 1:
        raise ConfigError("context_engine.cached_prompt_weight precisa estar entre 0 e 1")
    for key, default in (
        ("safety_margin_tokens", 500),
        ("chars_per_token_fallback", 3),
        ("working_set_target_tokens", 12000),
    ):
        _validate_int(context, key, default, minimum=1, prefix="context_engine")

    try:
        claim_config(config)
    except ClaimConfigError as error:
        raise ConfigError(str(error)) from error
    return config


def carregar_config_validada(path):
    with open(path, "r", encoding="utf-8") as handle:
        return validar_config(json.load(handle))
