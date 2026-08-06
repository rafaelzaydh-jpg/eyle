#!/usr/bin/env python3
"""Schema enxuto do ``config.json`` da Eyle 2.7.4."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, TypedDict


class CacheConfig(TypedDict, total=False):
    ativado: bool
    max_entradas: int
    memoria_max_entradas: int
    max_age_days: int
    max_age_hours: int
    hit_flush_interval: int


class LLMConfig(TypedDict, total=False):
    provider: str
    base_url: str
    model: str
    openai_compatible: bool
    temperature: float
    timeout_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int
    agent_timeout_seconds: int
    executor_timeout_seconds: int
    model_discovery_timeout_seconds: int
    model_discovery_negative_ttl_seconds: int
    retry_max_attempts: int
    agent_retry_max_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    retry_jitter_seconds: float
    retry_read_timeouts: bool
    stream_responses: bool
    max_concurrent_requests: int
    cooldown_seconds: float
    max_tokens: Optional[int]
    agent_max_tokens: Optional[int]
    audit_scout_max_tokens: Optional[int]
    audit_finalizer_max_tokens: Optional[int]
    project_read_finalizer_max_tokens: Optional[int]
    truncation_retry_multiplier: float
    truncation_retry_max_tokens: Optional[int]
    context_window_tokens: int
    cache: CacheConfig


class AgentConfig(TypedDict, total=False):
    rollout_mode: str
    enabled_modes: List[str]
    max_steps: int
    max_no_progress_decisions: int
    cycle_min_repetitions: int
    max_tentativas_parse: int
    usar_json_mode_se_suportado: bool
    require_confirmation_for_write: bool
    require_confirmation_for_exec: bool
    max_chars_por_observacao: int
    max_erros_consecutivos: int
    exigir_run_tests_apos_escrita: bool
    max_fatos_importantes: int
    max_tree_entries: int
    max_tree_depth: int
    max_read_range_lines: int
    task_deadline_seconds: int
    max_llm_calls: int
    max_total_generated_tokens: int
    max_prompt_tokens: int
    max_completion_tokens: int
    max_total_tokens: int
    chat_history_token_budget: int
    audit_optional_expansion_enabled: bool
    semantic_repeat_overlap: float
    max_secret_scan_bytes: int
    audit_candidate_limit: int
    audit_initial_read_limit: int
    audit_gap_read_limit: int
    audit_health_claim_required_score: float
    project_read_finalizer_enabled: bool
    project_read_fast_path_enabled: bool
    target_coverage_enabled: bool
    project_read_single_repair_enabled: bool
    deterministic_post_write_enabled: bool
    deterministic_symbol_lookup_enabled: bool
    intent_output_gate_enabled: bool
    deterministic_write_receipt_enabled: bool
    semantic_grounding: Dict[str, Any]


class ConfigEyle(TypedDict, total=False):
    llm: LLMConfig
    context: Dict[str, Any]
    context_engine: Dict[str, Any]
    retrieval: Dict[str, Any]
    ingest: Dict[str, Any]
    servidor: Dict[str, Any]
    web: Dict[str, Any]
    codar: Dict[str, Any]
    confirmacoes: Dict[str, Any]
    retention: Dict[str, Any]
    benchmark: Dict[str, Any]
    agent: AgentConfig
    worker: Dict[str, Any]
    telemetry: Dict[str, Any]
    app_version: str
    config_schema_version: str
    revision: str
    version: str
    updated: str


class ConfigError(ValueError):
    pass


_SECOES = (
    "llm", "context", "context_engine", "retrieval", "ingest", "servidor",
    "web", "codar", "confirmacoes", "retention", "benchmark", "agent",
    "worker", "telemetry",
)


def _valor(config: Dict[str, Any], caminho: str):
    atual: Any = config
    for parte in caminho.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return False, None
        atual = atual[parte]
    return True, atual


def _tipo_exato(valor: Any, tipo: type) -> bool:
    if tipo is int:
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo is float:
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    return isinstance(valor, tipo)


def _validar_tipo(config, erros, caminho, tipo, descricao):
    existe, valor = _valor(config, caminho)
    if existe and not _tipo_exato(valor, tipo):
        erros.append(f"{caminho} precisa ser {descricao}")
    return existe, valor


def _validar_numero(config, erros, caminho, minimo=None, maximo=None, aceitar_none=False):
    existe, valor = _valor(config, caminho)
    if not existe or (aceitar_none and valor is None):
        return
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        erros.append(f"{caminho} precisa ser numero")
        return
    if minimo is not None and valor < minimo:
        erros.append(f"{caminho} precisa ser >= {minimo}")
    if maximo is not None and valor > maximo:
        erros.append(f"{caminho} precisa ser <= {maximo}")


def validar_config(config) -> ConfigEyle:
    if not isinstance(config, dict):
        raise ConfigError("a raiz do config.json precisa ser um objeto JSON")

    erros: List[str] = []
    for secao in _SECOES:
        existe, valor = _valor(config, secao)
        if existe and not isinstance(valor, dict):
            erros.append(f"{secao} precisa ser um objeto")

    for caminho in (
        "llm.openai_compatible", "llm.cache.ativado", "llm.retry_read_timeouts",
        "retrieval.query_cache_ativado",
        "agent.enabled",
        "llm.stream_responses", "agent.usar_json_mode_se_suportado",
        "agent.require_confirmation_for_write", "agent.require_confirmation_for_exec",
        "agent.exigir_run_tests_apos_escrita", "agent.project_read_finalizer_enabled",
        "agent.project_read_fast_path_enabled",
        "agent.target_coverage_enabled", "agent.project_read_single_repair_enabled",
        "agent.deterministic_post_write_enabled", "agent.deterministic_symbol_lookup_enabled",
        "agent.intent_output_gate_enabled", "agent.deterministic_write_receipt_enabled",
        "agent.semantic_grounding.enabled", "agent.semantic_grounding.block_unsupported_anchors",
        "agent.semantic_grounding.require_inline_citations",
        "agent.semantic_grounding.require_inference_evidence",
        "agent.semantic_grounding.warn_hypothesis_without_evidence",
        "codar.fazer_backup", "codar.testes.ativado",
        "codar.testes.sandbox.bloquear_rede", "codar.testes.sandbox.copiar_projeto",
        "codar.testes.sandbox.allow_trusted_local",
        "worker.isolate_jobs", "telemetry.enabled",
        "agent.audit_optional_expansion_enabled",
    ):
        _validar_tipo(config, erros, caminho, bool, "booleano")

    inteiros_positivos = (
        "llm.timeout_seconds", "llm.connect_timeout_seconds", "llm.read_timeout_seconds",
        "llm.agent_timeout_seconds", "llm.executor_timeout_seconds",
        "llm.model_discovery_timeout_seconds", "llm.retry_max_attempts",
        "llm.agent_retry_max_attempts", "llm.max_concurrent_requests",
        "llm.context_window_tokens", "llm.agent_max_tokens",
        "llm.audit_scout_max_tokens", "llm.audit_finalizer_max_tokens",
        "llm.project_read_finalizer_max_tokens", "llm.truncation_retry_max_tokens",
        "context.token_budget", "context.chars_per_token",
        "context_engine.safety_margin_tokens", "context_engine.chars_per_token_fallback",
        "context_engine.max_recent_observations", "retrieval.chunk_max_tokens",
        "retrieval.max_chunks_no_resultado", "ingest.max_workers", "ingest.parallel_threshold",
        "servidor.port", "confirmacoes.expiracao_segundos", "agent.max_steps",
        "agent.max_no_progress_decisions", "agent.cycle_min_repetitions",
        "agent.max_tentativas_parse", "agent.max_chars_por_observacao",
        "agent.max_erros_consecutivos", "agent.max_fatos_importantes",
        "agent.max_tree_entries", "agent.max_tree_depth", "agent.max_read_range_lines",
        "agent.task_deadline_seconds", "agent.max_llm_calls",
        "agent.max_total_generated_tokens", "agent.max_prompt_tokens",
        "agent.max_completion_tokens", "agent.max_total_tokens",
        "agent.chat_history_token_budget", "agent.max_secret_scan_bytes",
        "agent.audit_candidate_limit", "agent.audit_initial_read_limit",
        "agent.audit_gap_read_limit", "worker.heartbeat_interval_seconds",
        "worker.queue_error_backoff_seconds", "worker.max_invalid_jobs_per_reservation",
        "worker.max_parallel_jobs", "worker.job_deadline_seconds",
        "worker.stale_worker_seconds", "worker.head_of_line_blocked_seconds",
        "telemetry.window_seconds", "telemetry.max_entries",
    )
    for caminho in inteiros_positivos:
        existe, valor = _validar_tipo(config, erros, caminho, int, "inteiro")
        if existe and _tipo_exato(valor, int) and valor < 1:
            erros.append(f"{caminho} precisa ser >= 1")

    for caminho in (
        "llm.temperature", "llm.retry_base_delay_seconds", "llm.retry_max_delay_seconds",
        "llm.retry_jitter_seconds", "llm.cooldown_seconds",
        "agent.semantic_repeat_overlap", "agent.audit_health_claim_required_score",
        "llm.truncation_retry_multiplier", "agent.semantic_grounding.min_claim_token_overlap",
        "retrieval.bm25_k1", "retrieval.bm25_b",
    ):
        _validar_numero(config, erros, caminho, minimo=0)
    _validar_numero(config, erros, "llm.temperature", minimo=0, maximo=2)
    _validar_numero(config, erros, "agent.semantic_repeat_overlap", minimo=0.5, maximo=1)
    _validar_numero(config, erros, "agent.audit_health_claim_required_score", minimo=0, maximo=1)
    _validar_numero(config, erros, "retrieval.bm25_b", minimo=0, maximo=1)

    for caminho in (
        "llm.provider", "llm.base_url", "llm.model", "servidor.host",
        "app_version", "config_schema_version", "revision", "version",
    ):
        existe, valor = _validar_tipo(config, erros, caminho, str, "texto")
        if existe and not valor.strip():
            erros.append(f"{caminho} nao pode ser vazio")

    _, provider = _valor(config, "llm.provider")
    if isinstance(provider, str) and provider.lower() not in {
        "ollama", "openai", "openai_compatible", "llama.cpp", "lmstudio",
        "text-generation-webui",
    }:
        erros.append("llm.provider invalido")
    _, base_url = _valor(config, "llm.base_url")
    if isinstance(base_url, str) and not base_url.startswith(("http://", "https://")):
        erros.append("llm.base_url precisa comecar com http:// ou https://")

    recovery_enabled, recovery_value = _valor(config, "agent.response_recovery.llm_enabled")
    if recovery_enabled and recovery_value is True:
        erros.append("agent.response_recovery.llm_enabled foi removido; recovery LLM legado esta desativado")

    existe_rollout, rollout = _valor(config, "agent.rollout_mode")
    if existe_rollout and rollout not in ("read_only", "full"):
        erros.append("agent.rollout_mode precisa ser read_only ou full")
    existe_modos, modos = _valor(config, "agent.enabled_modes")
    if existe_modos and (
        not isinstance(modos, list) or not modos
        or not all(m in {"analyze", "suggest", "edit"} for m in modos)
    ):
        erros.append("agent.enabled_modes precisa conter analyze, suggest e/ou edit")


    existe_ciclo, ciclo = _valor(config, "agent.cycle_min_repetitions")
    if existe_ciclo and _tipo_exato(ciclo, int) and ciclo < 2:
        erros.append("agent.cycle_min_repetitions precisa ser >= 2")


    existe_workers, workers = _valor(config, "ingest.max_workers")
    if existe_workers and _tipo_exato(workers, int) and workers > 32:
        erros.append("ingest.max_workers precisa ser <= 32")

    existe_porta, porta = _valor(config, "servidor.port")
    if existe_porta and _tipo_exato(porta, int) and porta > 65535:
        erros.append("servidor.port precisa ser <= 65535")

    existe_janela, janela = _valor(config, "llm.context_window_tokens")
    if existe_janela and _tipo_exato(janela, int) and janela < 512:
        erros.append("llm.context_window_tokens precisa ser >= 512")
    _, max_tokens = _valor(config, "llm.max_tokens")
    _, margem = _valor(config, "context_engine.safety_margin_tokens")
    if (
        _tipo_exato(janela, int)
        and isinstance(max_tokens, (int, float)) and not isinstance(max_tokens, bool)
        and isinstance(margem, (int, float)) and not isinstance(margem, bool)
        and max_tokens + margem >= janela
    ):
        erros.append("llm.max_tokens + context_engine.safety_margin_tokens precisa ser menor que llm.context_window_tokens")

    _, app_version = _valor(config, "app_version")
    _, schema_version = _valor(config, "config_schema_version")
    _, version = _valor(config, "version")
    if isinstance(app_version, str) and isinstance(schema_version, str) and app_version != schema_version:
        erros.append("config_schema_version precisa ser igual a app_version")
    if isinstance(app_version, str) and isinstance(version, str) and app_version != version:
        erros.append("version precisa ser igual a app_version")

    _, mp_context = _valor(config, "worker.multiprocessing_context")
    if mp_context not in (None, "spawn", "fork", "forkserver"):
        erros.append("worker.multiprocessing_context precisa ser spawn, fork ou forkserver")

    if erros:
        raise ConfigError("config.json invalido:\n- " + "\n- ".join(erros))
    config["_config_warnings"] = avisos_config(config)
    return config


def avisos_config(config):
    avisos = []
    _, read_timeout = _valor(config, "llm.read_timeout_seconds")
    if isinstance(read_timeout, (int, float)) and read_timeout > 300:
        avisos.append({
            "code": "LLM_READ_TIMEOUT_HIGH",
            "detail": "llm.read_timeout_seconds acima de 300s pode parecer congelamento",
        })
    _, parallel = _valor(config, "worker.max_parallel_jobs")
    _, llm_parallel = _valor(config, "llm.max_concurrent_requests")
    if (
        _tipo_exato(parallel, int) and _tipo_exato(llm_parallel, int)
        and parallel > llm_parallel
    ):
        avisos.append({
            "code": "WORKER_PARALLELISM_CAPPED",
            "detail": "worker.max_parallel_jobs sera limitado por llm.max_concurrent_requests",
        })
    _, tests_enabled = _valor(config, "codar.testes.ativado")
    if tests_enabled is False:
        avisos.append({
            "code": "PROJECT_TESTS_DISABLED",
            "detail": "codar.testes.ativado=false: patches podem ficar sem suite real do projeto",
        })
    _, api_token = _valor(config, "web.api_token")
    if api_token is None:
        avisos.append({
            "code": "WEB_TOKEN_FILE_MODE",
            "detail": "web.api_token ausente: a Eyle usara segredo local gerado em arquivo 0600",
        })
    return avisos


def carregar_config_validada(caminho) -> ConfigEyle:
    caminho = os.fspath(caminho)
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            config = json.load(arquivo)
    except FileNotFoundError as erro:
        raise ConfigError(f"config.json nao encontrado: {caminho}") from erro
    except json.JSONDecodeError as erro:
        raise ConfigError(
            f"config.json malformado em linha {erro.lineno}, coluna {erro.colno}: {erro.msg}"
        ) from erro
    except OSError as erro:
        raise ConfigError(f"nao foi possivel ler config.json: {erro}") from erro
    return validar_config(config)
