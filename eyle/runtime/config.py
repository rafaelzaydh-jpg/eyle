"""Eyle ECC host configuration boundary.

Core/Runtime validate only universal host mechanics. Domain configuration is
opaque here and delegated to the capability provider that owns that domain.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from eyle import __revision__, __schema_version__, __version__
from eyle.capabilities.registry import CapabilityRegistry


class ConfigError(ValueError):
    pass


_TOP_LEVEL_FIELDS = {
    "llm", "context_engine", "web", "confirmacoes", "providers",
    "worker", "telemetry", "app_version", "config_schema_version", "revision",
}
_LLM_FIELDS = {
    "base_url", "model", "temperature", "provider_token_budget_per_message",
    "context_window_tokens", "connect_timeout_seconds", "read_timeout_seconds",
    "adapter_status_timeout_seconds",
    "retry_max_attempts", "retry_base_delay_seconds", "retry_max_delay_seconds",
    "retry_jitter_seconds", "max_concurrent_requests", "cooldown_seconds",
    "retry_read_timeouts", "stream_responses",
    "reasoning_mode",
}
_CONTEXT_FIELDS = {"safety_margin_tokens", "chars_per_token_fallback", "conversation_materialization_tokens", "observation_materialization_tokens", "runtime_feedback_materialization_tokens"}
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

def _validate_nonnegative_number(container, key, default, prefix):
    value = container.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) < 0:
        raise ConfigError(f"{prefix}.{key} precisa ser numérico não negativo")

def _validate_bool(container, key, default, prefix):
    value = container.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{prefix}.{key} precisa ser booleano")

def _validate_string(container, key, default, prefix, *, nonempty=False):
    value = container.get(key, default)
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ConfigError(f"{prefix}.{key} precisa ser texto" + (" não vazio" if nonempty else ""))


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
    base_url = str(llm.get("base_url") or "http://127.0.0.1:8080").strip()
    try:
        parsed_adapter = urlparse(base_url)
        adapter_port = parsed_adapter.port
    except ValueError as exc:
        raise ConfigError("llm.base_url precisa apontar para o Adapter local em 127.0.0.1:8080") from exc
    adapter_host = str(parsed_adapter.hostname or "").lower()
    adapter_path = str(parsed_adapter.path or "").rstrip("/")
    if (
        parsed_adapter.scheme not in {"http", "https"}
        or adapter_host not in {"127.0.0.1", "localhost", "::1"}
        or adapter_port != 8080
        or adapter_path not in {"", "/v1"}
        or parsed_adapter.username is not None
        or parsed_adapter.password is not None
        or bool(parsed_adapter.query)
        or bool(parsed_adapter.fragment)
    ):
        raise ConfigError("llm.base_url precisa apontar para o Adapter local em 127.0.0.1:8080 (ou localhost/[::1], com /v1 opcional)")
    _validate_string(llm, "model", "deepseek-v4-flash", "llm", nonempty=True)
    _validate_nonnegative_number(llm, "temperature", 0.2, "llm")
    _validate_bool(llm, "stream_responses", True, "llm")
    _validate_bool(llm, "retry_read_timeouts", False, "llm")
    if str(llm.get("reasoning_mode") or "off") not in {"off", "on", "provider_default"}:
        raise ConfigError("llm.reasoning_mode inválido")
    for key, default in (("connect_timeout_seconds", 5), ("adapter_status_timeout_seconds", 3)):
        _validate_positive_number(llm, key, default, "llm")
    if llm.get("read_timeout_seconds") is not None:
        _validate_positive_number(llm, "read_timeout_seconds", 1, "llm")
    if llm.get("context_window_tokens") is not None:
        _validate_int(llm, "context_window_tokens", 50000, minimum=1, prefix="llm")
    _validate_int(llm, "provider_token_budget_per_message", 150000, minimum=1, prefix="llm")
    for key, default in (("retry_max_attempts", 3), ("max_concurrent_requests", 1)):
        _validate_int(llm, key, default, minimum=1, prefix="llm")
    for key, default in (("retry_base_delay_seconds", 0.5), ("retry_max_delay_seconds", 2.0), ("retry_jitter_seconds", 0.2), ("cooldown_seconds", 2.0)):
        _validate_nonnegative_number(llm, key, default, "llm")


    worker = config.get("worker") or {}
    if not isinstance(worker, dict):
        raise ConfigError("worker precisa ser um objeto")
    _reject_unknown(worker, _WORKER_FIELDS, "worker")
    for key, default in (("heartbeat_interval_seconds", 5), ("queue_error_backoff_seconds", 1), ("max_invalid_jobs_per_reservation", 100), ("max_parallel_jobs", 1), ("stale_worker_seconds", 30), ("head_of_line_blocked_seconds", 60)):
        _validate_int(worker, key, default, minimum=1, prefix="worker")
    _validate_bool(worker, "isolate_jobs", True, "worker")
    if str(worker.get("multiprocessing_context") or "spawn") not in {"spawn", "fork", "forkserver"}:
        raise ConfigError("worker.multiprocessing_context inválido")

    web = config.get("web") or {}
    if not isinstance(web, dict):
        raise ConfigError("web precisa ser um objeto")
    _reject_unknown(web, _WEB_FIELDS, "web")
    rate_limit = web.get("rate_limit") or {}
    if not isinstance(rate_limit, dict):
        raise ConfigError("web.rate_limit precisa ser um objeto")
    _reject_unknown(rate_limit, _WEB_RATE_LIMIT_FIELDS, "web.rate_limit")
    for key, default in (("requests", 180), ("auth_failures", 10), ("window_seconds", 60)):
        _validate_int(rate_limit, key, default, minimum=1, prefix="web.rate_limit")
    if web.get("api_token") is not None and not isinstance(web.get("api_token"), str):
        raise ConfigError("web.api_token precisa ser texto ou null")

    confirmations = config.get("confirmacoes") or {}
    if not isinstance(confirmations, dict):
        raise ConfigError("confirmacoes precisa ser um objeto")
    _reject_unknown(confirmations, _CONFIRMATION_FIELDS, "confirmacoes")
    _validate_int(confirmations, "expiracao_segundos", 3600, minimum=1, prefix="confirmacoes")

    telemetry = config.get("telemetry") or {}
    if not isinstance(telemetry, dict):
        raise ConfigError("telemetry precisa ser um objeto")
    _reject_unknown(telemetry, _TELEMETRY_FIELDS, "telemetry")
    _validate_bool(telemetry, "enabled", True, "telemetry")
    _validate_int(telemetry, "window_seconds", 3600, minimum=1, prefix="telemetry")

    context = config.get("context_engine") or {}
    if not isinstance(context, dict):
        raise ConfigError("context_engine precisa ser um objeto")
    _reject_unknown(context, _CONTEXT_FIELDS, "context_engine")
    for key, default in (
        ("safety_margin_tokens", 500),
        ("chars_per_token_fallback", 3),
        ("conversation_materialization_tokens", 1200),
        ("observation_materialization_tokens", 2200),
        ("runtime_feedback_materialization_tokens", 320),
    ):
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
