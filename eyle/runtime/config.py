"""Rev5.8 strict configuration boundary. One canonical Core contract."""
from __future__ import annotations

import json

from eyle import __revision__, __schema_version__, __version__
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
    "retry_max_attempts", "retry_base_delay_seconds",
    "retry_max_delay_seconds", "retry_jitter_seconds", "max_concurrent_requests",
    "cooldown_seconds", "retry_read_timeouts", "stream_responses",
    "agent_max_tokens",
}
_AGENT_FIELDS = {
    "max_tree_entries", "max_tree_depth", "max_file_read_lines",
    "task_deadline_seconds", "max_llm_calls",
    "max_prompt_tokens", "max_completion_tokens", "max_total_tokens",
    "max_llm_turns",
    "max_tool_calls", "max_patch_dry_run_failures",
    "context_view", "max_project_scan_entries",
    "max_project_scan_depth", "max_project_file_bytes", "max_inspect_relation_edges",
    "max_git_diff_chars", "max_search_matches", "max_search_ranges", "claims",
    "max_search_range_lines", "sandbox",
}
_CONTEXT_VIEW_FIELDS = {
    "max_source_preview_chars",
    "max_symbol_preview_chars", "max_search_source_chars",
}
_CONTEXT_FIELDS = {
    "safety_margin_tokens", "chars_per_token_fallback", "cached_prompt_weight",
}
_CODAR_FIELDS = {"ativado", "testes"}

_WORKER_FIELDS = {
    "heartbeat_interval_seconds", "queue_error_backoff_seconds",
    "max_invalid_jobs_per_reservation", "max_parallel_jobs", "isolate_jobs",
    "stale_worker_seconds", "head_of_line_blocked_seconds", "multiprocessing_context",
}
_WEB_FIELDS = {"api_token", "rate_limit"}
_WEB_RATE_LIMIT_FIELDS = {"requests", "auth_failures", "window_seconds"}
_CONFIRMATION_FIELDS = {"expiracao_segundos"}
_TELEMETRY_FIELDS = {"enabled", "window_seconds"}
_TEST_FIELDS = {"ativado", "comando_python", "comando_node", "timeout_segundos", "sandbox"}
_SANDBOX_FIELDS = {
    "backend", "bloquear_rede", "comandos_permitidos", "cpu_segundos", "memoria_mb",
    "max_processos", "max_arquivos_abertos", "max_saida_kb", "max_arquivo_mb",
    "copiar_projeto", "max_arquivos_projeto", "max_tamanho_projeto_mb", "cpus",
    "allow_trusted_local", "timeout_segundos", "imagem_docker",
}
_AGENT_POSITIVE_DEFAULTS = {
    "max_llm_turns": 24,
    "max_tool_calls": 64,
    "max_patch_dry_run_failures": 2,
    "max_project_scan_entries": 20000,
    "max_project_scan_depth": 32,
    "max_project_file_bytes": 4194304,
    "max_inspect_relation_edges": 60,
    "max_search_range_lines": 16,
    "max_file_read_lines": 400,
    "max_tree_entries": 200,
    "max_tree_depth": 6,
    "max_git_diff_chars": 6000,
    "max_search_matches": 40,
    "max_search_ranges": 12,
    "task_deadline_seconds": 1800,
    "max_llm_calls": 32,
    "max_prompt_tokens": 90000,
    "max_completion_tokens": 8000,
    "max_total_tokens": 98000,
}
_CONTEXT_VIEW_POSITIVE_DEFAULTS = {
    "max_source_preview_chars": 3500,
    "max_search_source_chars": 600,
    "max_symbol_preview_chars": 2600,
}

_SANDBOX_BACKENDS = {"auto", "docker", "bwrap", "process", "trusted_local"}

def _validate_sandbox_backend(container, prefix):
    backend = container.get("backend", "auto")
    if not isinstance(backend, str) or backend not in _SANDBOX_BACKENDS:
        raise ConfigError(
            f"{prefix}.backend must be one of: " + ", ".join(sorted(_SANDBOX_BACKENDS))
        )



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
    expected_identity = {
        "app_version": __version__,
        "config_schema_version": __schema_version__,
        "revision": __revision__,
    }
    for key, expected in expected_identity.items():
        if config.get(key) != expected:
            raise ConfigError(f"CONFIG_IDENTITY_INCOMPATIBLE:{key}:{config.get(key)!r}")

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
    _validate_int(llm, "context_window_tokens", 32768, minimum=1, prefix="llm")
    if int(llm.get("context_window_tokens", 32768) or 32768) > 32768:
        raise ConfigError("llm.context_window_tokens não pode exceder 32768 na Rev5.8")

    codar = config.get("codar") or {}
    if not isinstance(codar, dict):
        raise ConfigError("codar precisa ser um objeto")
    _reject_unknown(codar, _CODAR_FIELDS, "codar")
    tests = codar.get("testes") or {}
    if not isinstance(tests, dict):
        raise ConfigError("codar.testes precisa ser um objeto")
    _reject_unknown(tests, _TEST_FIELDS, "codar.testes")
    sandbox = tests.get("sandbox") or {}
    if not isinstance(sandbox, dict):
        raise ConfigError("codar.testes.sandbox precisa ser um objeto")
    _reject_unknown(sandbox, _SANDBOX_FIELDS, "codar.testes.sandbox")
    _validate_sandbox_backend(sandbox, "codar.testes.sandbox")

    worker = config.get("worker") or {}
    if not isinstance(worker, dict):
        raise ConfigError("worker precisa ser um objeto")
    _reject_unknown(worker, _WORKER_FIELDS, "worker")

    web = config.get("web") or {}
    if not isinstance(web, dict):
        raise ConfigError("web precisa ser um objeto")
    _reject_unknown(web, _WEB_FIELDS, "web")
    rate_limit = web.get("rate_limit") or {}
    if not isinstance(rate_limit, dict):
        raise ConfigError("web.rate_limit precisa ser um objeto")
    _reject_unknown(rate_limit, _WEB_RATE_LIMIT_FIELDS, "web.rate_limit")

    confirmations = config.get("confirmacoes") or {}
    if not isinstance(confirmations, dict):
        raise ConfigError("confirmacoes precisa ser um objeto")
    _reject_unknown(confirmations, _CONFIRMATION_FIELDS, "confirmacoes")

    telemetry = config.get("telemetry") or {}
    if not isinstance(telemetry, dict):
        raise ConfigError("telemetry precisa ser um objeto")
    _reject_unknown(telemetry, _TELEMETRY_FIELDS, "telemetry")

    agent = config.get("agent") or {}
    if not isinstance(agent, dict):
        raise ConfigError("agent precisa ser um objeto")
    _reject_unknown(agent, _AGENT_FIELDS, "agent")
    for key, default in _AGENT_POSITIVE_DEFAULTS.items():
        _validate_int(agent, key, default, minimum=1, prefix="agent")
    if int(agent.get("max_total_tokens", 98000) or 98000) > 98000:
        raise ConfigError("agent.max_total_tokens não pode exceder 98000 na Rev5.8")
    if int(agent.get("max_prompt_tokens", 90000) or 90000) > 90000:
        raise ConfigError("agent.max_prompt_tokens não pode exceder 90000 na Rev5.8")
    if int(agent.get("max_completion_tokens", 8000) or 8000) > 8000:
        raise ConfigError("agent.max_completion_tokens não pode exceder 8000 na Rev5.8")

    agent_sandbox = agent.get("sandbox") or {}
    if not isinstance(agent_sandbox, dict):
        raise ConfigError("agent.sandbox precisa ser um objeto")
    _reject_unknown(agent_sandbox, _SANDBOX_FIELDS, "agent.sandbox")
    _validate_sandbox_backend(agent_sandbox, "agent.sandbox")

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
