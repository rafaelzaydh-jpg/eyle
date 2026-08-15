"""Eyle ECC host configuration boundary.

Core/Runtime validate only universal host mechanics. Domain configuration is
opaque here and delegated to the capability provider that owns that domain.
"""
from __future__ import annotations

import json

from eyle import __revision__, __schema_version__, __version__
from eyle.capabilities.registry import CapabilityRegistry


class ConfigError(ValueError):
    pass


_TOP_LEVEL_FIELDS = {
    "llm", "context_engine", "web", "confirmacoes", "agent", "providers",
    "worker", "telemetry", "app_version", "config_schema_version", "revision",
}
_LLM_FIELDS = {
    "base_url", "model", "openai_compatible", "temperature", "max_tokens",
    "context_window_tokens", "connect_timeout_seconds", "read_timeout_seconds",
    "model_discovery_timeout_seconds", "model_discovery_negative_ttl_seconds",
    "retry_max_attempts", "retry_base_delay_seconds", "retry_max_delay_seconds",
    "retry_jitter_seconds", "max_concurrent_requests", "cooldown_seconds",
    "retry_read_timeouts", "stream_responses", "agent_max_tokens",
}
_AGENT_FIELDS = {"task_deadline_seconds"}
_CONTEXT_FIELDS = {"safety_margin_tokens", "chars_per_token_fallback", "cached_prompt_weight"}
_WORKER_FIELDS = {
    "heartbeat_interval_seconds", "queue_error_backoff_seconds",
    "max_invalid_jobs_per_reservation", "max_parallel_jobs", "isolate_jobs",
    "stale_worker_seconds", "head_of_line_blocked_seconds", "multiprocessing_context",
}
_WEB_FIELDS = {"api_token", "rate_limit"}
_WEB_RATE_LIMIT_FIELDS = {"requests", "auth_failures", "window_seconds"}
_CONFIRMATION_FIELDS = {"expiracao_segundos"}
_TELEMETRY_FIELDS = {"enabled", "window_seconds"}


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


def validar_config(config, registry: CapabilityRegistry):
    if registry is None:
        raise ConfigError("CAPABILITY_REGISTRY_REQUIRED")
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
    _validate_int(llm, "context_window_tokens", 38000, minimum=1, prefix="llm")

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
    _validate_int(agent, "task_deadline_seconds", 1800, minimum=1, prefix="agent")

    context = config.get("context_engine") or {}
    if not isinstance(context, dict):
        raise ConfigError("context_engine precisa ser um objeto")
    _reject_unknown(context, _CONTEXT_FIELDS, "context_engine")
    cached_weight = context.get("cached_prompt_weight", 0.2)
    if not isinstance(cached_weight, (int, float)) or isinstance(cached_weight, bool) or not 0 <= float(cached_weight) <= 1:
        raise ConfigError("context_engine.cached_prompt_weight precisa estar entre 0 e 1")
    for key, default in (("safety_margin_tokens", 500), ("chars_per_token_fallback", 3)):
        _validate_int(context, key, default, minimum=1, prefix="context_engine")

    providers = config.get("providers") or {}
    if not isinstance(providers, dict):
        raise ConfigError("providers precisa ser um objeto")
    try:
        registry.validate_host_config(config)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    return config


def carregar_config_validada(path, registry: CapabilityRegistry):
    with open(path, "r", encoding="utf-8") as handle:
        return validar_config(json.load(handle), registry)
