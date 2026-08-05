#!/usr/bin/env python3
"""Schema tipado e validacao de runtime do ``config.json`` da Eyle."""
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
    stream_responses: bool
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    retry_jitter_seconds: float
    retry_read_timeouts: bool
    max_concurrent_requests: int
    cooldown_seconds: float
    max_tokens: Optional[int]
    agent_max_tokens: Optional[int]
    audit_scout_max_tokens: Optional[int]
    audit_finalizer_max_tokens: Optional[int]
    context_window_tokens: int
    cache: CacheConfig


class ContextEngineConfig(TypedDict, total=False):
    safety_margin_tokens: int
    chars_per_token_fallback: int
    max_recent_observations: int


class AgentConfig(TypedDict, total=False):
    enabled: bool
    rollout_mode: str
    trusted_project_paths: List[str]
    enabled_modes: List[str]
    max_steps: int
    max_no_progress_decisions: int
    cycle_min_repetitions: int
    max_tentativas_parse: int
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
    semantic_repeat_overlap: float
    max_secret_scan_bytes: int
    audit_candidate_limit: int
    audit_initial_read_limit: int
    audit_gap_read_limit: int
    audit_health_claim_required_score: float
    semantic_grounding: Dict[str, Any]
    response_recovery: Dict[str, Any]


class BenchmarkConfig(TypedDict, total=False):
    primary_model: Optional[str]
    baseline_model: Optional[str]


class IngestConfig(TypedDict, total=False):
    max_workers: int
    parallel_threshold: int


class ConfigEyle(TypedDict, total=False):
    llm: LLMConfig
    context_engine: ContextEngineConfig
    agent: AgentConfig
    benchmark: BenchmarkConfig
    context: Dict[str, Any]
    retrieval: Dict[str, Any]
    ingest: IngestConfig
    engine: Dict[str, Any]
    servidor: Dict[str, Any]
    web: Dict[str, Any]
    entendimento: Dict[str, Any]
    dicas: Dict[str, Any]
    codar: Dict[str, Any]
    confirmacoes: Dict[str, Any]
    retention: Dict[str, Any]
    worker: Dict[str, Any]
    app_version: str
    config_schema_version: str
    revision: str
    version: str
    updated: str


class ConfigError(ValueError):
    pass


_SECOES = (
    "llm", "context", "context_engine", "retrieval", "ingest", "engine", "servidor", "web",
    "entendimento", "dicas", "codar", "confirmacoes", "agent", "benchmark",
    "retention", "worker",
    "telemetry",
)


def _valor(config, caminho):
    atual = config
    for parte in caminho.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return False, None
        atual = atual[parte]
    return True, atual


def _tipo_exato(valor, tipo):
    if tipo is int:
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo in (float, (int, float)):
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
    """Valida sem preencher defaults; os defaults historicos seguem nos usos."""
    if not isinstance(config, dict):
        raise ConfigError("a raiz do config.json precisa ser um objeto JSON")

    erros: List[str] = []
    for secao in _SECOES:
        existe, valor = _valor(config, secao)
        if existe and not isinstance(valor, dict):
            erros.append(f"{secao} precisa ser um objeto")

    for caminho in (
        "llm.openai_compatible", "llm.cache.ativado",
        "retrieval.query_cache_ativado",
        "engine.atalho_analista_ativado", "entendimento.gerar_via_llm",
        "codar.ativado", "codar.fazer_backup", "codar.testes.ativado",
        "codar.testes.sandbox.bloquear_rede",
        "codar.testes.sandbox.copiar_projeto", "agent.enabled",
        "agent.usar_json_mode_se_suportado",
        "agent.require_confirmation_for_write",
        "agent.require_confirmation_for_exec",
        "agent.exigir_run_tests_apos_escrita",
        "worker.isolate_jobs",
        "agent.semantic_grounding.enabled",
        "agent.semantic_grounding.block_unsupported_anchors",
        "agent.semantic_grounding.require_inline_citations",
        "agent.semantic_grounding.require_inference_evidence",
        "agent.semantic_grounding.warn_hypothesis_without_evidence",
        "agent.response_recovery.llm_enabled",
        "agent.response_recovery.unstructured_retry",
        "agent.response_recovery.evidence_short_generation",
        "agent.response_recovery.deterministic_fallback",
        "llm.retry_read_timeouts", "llm.stream_responses",
        "telemetry.enabled",
    ):
        _validar_tipo(config, erros, caminho, bool, "booleano")

    caminhos_inteiros = (
        "llm.timeout_seconds", "llm.connect_timeout_seconds",
        "llm.read_timeout_seconds", "llm.agent_timeout_seconds",
        "llm.executor_timeout_seconds", "llm.model_discovery_timeout_seconds",
        "llm.model_discovery_negative_ttl_seconds", "llm.retry_max_attempts",
        "llm.agent_retry_max_attempts",
        "llm.max_concurrent_requests", "llm.context_window_tokens",
        "llm.agent_max_tokens",
        "llm.audit_scout_max_tokens", "llm.audit_finalizer_max_tokens",
        "llm.cache.max_entradas", "llm.cache.memoria_max_entradas",
        "llm.cache.max_age_hours", "llm.cache.hit_flush_interval",
        "context.token_budget", "context.chars_per_token",
        "context.small_project_full_read_max_files",
        "context.small_project_full_read_max_lines",
        "context.small_project_full_read_max_chars",
        "context_engine.safety_margin_tokens",
        "context_engine.chars_per_token_fallback",
        "context_engine.max_recent_observations",
        "retrieval.chunk_max_tokens", "retrieval.max_chunks_no_resultado",
        "retrieval.query_cache_max_entradas",
        "ingest.max_workers", "ingest.parallel_threshold",
        "engine.max_iteracoes_analista", "engine.max_tentativas_executor",
        "engine.task_deadline_seconds",
        "servidor.port", "web.rate_limit.requests",
        "web.rate_limit.auth_failures", "web.rate_limit.window_seconds",
        "entendimento.max_chars_por_arquivo", "dicas.max_componentes_candidatos",
        "dicas.profundidade_dependencia", "dicas.max_chars_por_arquivo",
        "codar.testes.timeout_segundos", "confirmacoes.expiracao_segundos",
        "agent.max_steps", "agent.max_tentativas_parse",
        "agent.max_no_progress_decisions", "agent.cycle_min_repetitions",
        "agent.max_chars_por_observacao", "agent.max_erros_consecutivos",
        "agent.max_fatos_importantes", "agent.max_tree_entries",
        "agent.max_tree_depth", "agent.max_read_range_lines",
        "agent.task_deadline_seconds", "agent.max_llm_calls",
        "agent.max_total_generated_tokens", "agent.max_secret_scan_bytes",
        "agent.audit_candidate_limit", "agent.audit_initial_read_limit",
        "agent.semantic_grounding.min_claim_tokens",
        "worker.heartbeat_interval_seconds", "worker.queue_error_backoff_seconds",
        "worker.max_invalid_jobs_per_reservation",
        "worker.max_parallel_jobs", "worker.job_deadline_seconds",
        "worker.stale_worker_seconds", "worker.head_of_line_blocked_seconds",
        "telemetry.window_seconds", "telemetry.max_entries",
        "retention.historico_max_entradas",
        "retention.trace_max_files", "retention.backups_max_files",
        "retention.backups_max_age_days", "retention.backups_max_total_mb",
    )
    for caminho in caminhos_inteiros:
        existe, valor = _validar_tipo(config, erros, caminho, int, "inteiro")
        if existe and _tipo_exato(valor, int) and valor < 0:
            erros.append(f"{caminho} precisa ser >= 0")

    for caminho in (
        "retrieval.query_cache_max_entradas", "ingest.max_workers",
        "ingest.parallel_threshold",
        "agent.max_tree_entries", "agent.max_tree_depth",
        "agent.cycle_min_repetitions",
        "agent.max_read_range_lines",
        "context.small_project_full_read_max_files",
        "context.small_project_full_read_max_lines",
        "context.small_project_full_read_max_chars",
        "context_engine.chars_per_token_fallback",
        "context_engine.max_recent_observations", "agent.max_no_progress_decisions",
        "llm.timeout_seconds", "llm.connect_timeout_seconds",
        "llm.read_timeout_seconds", "llm.agent_timeout_seconds",
        "llm.executor_timeout_seconds", "llm.model_discovery_timeout_seconds",
        "llm.retry_max_attempts", "llm.agent_retry_max_attempts",
        "llm.max_concurrent_requests",
        "llm.agent_max_tokens",
        "llm.audit_scout_max_tokens", "llm.audit_finalizer_max_tokens",
        "llm.cache.max_age_hours", "llm.cache.hit_flush_interval", "agent.max_steps",
        "agent.max_tentativas_parse", "agent.max_erros_consecutivos",
        "agent.task_deadline_seconds", "agent.max_llm_calls",
        "agent.max_total_generated_tokens", "agent.max_secret_scan_bytes",
        "agent.audit_candidate_limit", "agent.audit_initial_read_limit",
        "agent.semantic_grounding.min_claim_tokens",
        "worker.heartbeat_interval_seconds", "worker.max_invalid_jobs_per_reservation",
        "worker.max_parallel_jobs", "worker.job_deadline_seconds",
        "worker.stale_worker_seconds", "worker.head_of_line_blocked_seconds",
        "engine.task_deadline_seconds",
        "telemetry.window_seconds", "telemetry.max_entries",
    ):
        existe, valor = _valor(config, caminho)
        if existe and _tipo_exato(valor, int) and valor < 1:
            erros.append(f"{caminho} precisa ser >= 1")

    existe, repeticoes_ciclo = _valor(config, "agent.cycle_min_repetitions")
    if existe and _tipo_exato(repeticoes_ciclo, int) and repeticoes_ciclo < 2:
        erros.append("agent.cycle_min_repetitions precisa ser >= 2")

    existe, janela = _valor(config, "llm.context_window_tokens")
    if existe and _tipo_exato(janela, int) and janela < 512:
        erros.append("llm.context_window_tokens precisa ser >= 512")

    for caminho in (
        "codar.testes.sandbox.cpu_segundos",
        "codar.testes.sandbox.memoria_mb",
        "codar.testes.sandbox.max_processos",
        "codar.testes.sandbox.max_arquivos_abertos",
        "codar.testes.sandbox.max_saida_kb",
        "codar.testes.sandbox.max_arquivo_mb",
        "codar.testes.sandbox.max_arquivos_projeto",
        "codar.testes.sandbox.max_tamanho_projeto_mb",
        "codar.testes.sandbox.cpus",
    ):
        _validar_numero(config, erros, caminho, minimo=0.000001)

    _validar_numero(config, erros, "llm.temperature", minimo=0, maximo=2)
    _validar_numero(config, erros, "llm.retry_base_delay_seconds", minimo=0)
    _validar_numero(config, erros, "llm.retry_max_delay_seconds", minimo=0)
    _validar_numero(config, erros, "llm.retry_jitter_seconds", minimo=0)
    _validar_numero(config, erros, "llm.cooldown_seconds", minimo=0)
    _validar_numero(config, erros, "agent.semantic_repeat_overlap", minimo=0.5, maximo=1)
    _validar_numero(config, erros, "agent.audit_health_claim_required_score", minimo=0, maximo=1)
    _validar_numero(config, erros, "agent.semantic_grounding.min_claim_token_overlap", minimo=0, maximo=1)
    _validar_numero(config, erros, "retrieval.bm25_k1", minimo=0)
    _validar_numero(config, erros, "retrieval.bm25_b", minimo=0, maximo=1)
    _validar_numero(config, erros, "ingest.max_workers", minimo=1, maximo=32)
    _validar_numero(config, erros, "engine.atalho_score_minimo", minimo=0)
    _validar_numero(config, erros, "engine.atalho_score_ratio", minimo=1)
    _validar_numero(config, erros, "engine.executor_retry_base_delay_seconds", minimo=0)
    _validar_numero(config, erros, "engine.executor_retry_max_delay_seconds", minimo=0)
    _validar_numero(config, erros, "engine.executor_retry_jitter_seconds", minimo=0)

    for caminho in (
        "llm.provider", "llm.base_url", "llm.model", "servidor.host",
        "app_version", "config_schema_version", "revision", "version",
    ):
        existe, valor = _validar_tipo(config, erros, caminho, str, "texto")
        if existe and isinstance(valor, str) and not valor.strip():
            erros.append(f"{caminho} nao pode ser vazio")

    existe, base_url = _valor(config, "llm.base_url")
    if existe and isinstance(base_url, str) and not base_url.startswith(("http://", "https://")):
        erros.append("llm.base_url precisa comecar com http:// ou https://")

    existe, provider = _valor(config, "llm.provider")
    if existe and isinstance(provider, str) and provider.strip().lower() not in {
        "ollama", "openai", "openai_compatible", "llama.cpp", "lmstudio",
        "text-generation-webui",
    }:
        erros.append("llm.provider invalido")

    existe_openai, openai_compativel = _valor(config, "llm.openai_compatible")
    if (
        existe and isinstance(provider, str)
        and existe_openai and isinstance(openai_compativel, bool)
        and provider.strip().lower() in {"openai", "openai_compatible", "llama.cpp", "lmstudio", "text-generation-webui"}
        and not openai_compativel
    ):
        erros.append("llm.openai_compatible precisa ser true para o provider configurado")

    _, retry_base = _valor(config, "llm.retry_base_delay_seconds")
    _, retry_max = _valor(config, "llm.retry_max_delay_seconds")
    if (
        isinstance(retry_base, (int, float)) and not isinstance(retry_base, bool)
        and isinstance(retry_max, (int, float)) and not isinstance(retry_max, bool)
        and retry_max < retry_base
    ):
        erros.append("llm.retry_max_delay_seconds precisa ser >= llm.retry_base_delay_seconds")

    _, executor_retry_base = _valor(config, "engine.executor_retry_base_delay_seconds")
    _, executor_retry_max = _valor(config, "engine.executor_retry_max_delay_seconds")
    if (
        isinstance(executor_retry_base, (int, float)) and not isinstance(executor_retry_base, bool)
        and isinstance(executor_retry_max, (int, float)) and not isinstance(executor_retry_max, bool)
        and executor_retry_max < executor_retry_base
    ):
        erros.append(
            "engine.executor_retry_max_delay_seconds precisa ser >= "
            "engine.executor_retry_base_delay_seconds"
        )

    existe_schema, schema_version = _valor(config, "config_schema_version")
    existe_legada, version_legada = _valor(config, "version")
    if (
        existe_schema and existe_legada
        and isinstance(schema_version, str) and isinstance(version_legada, str)
        and schema_version.strip() != version_legada.strip()
    ):
        erros.append("version legado precisa ser igual a config_schema_version")

    existe, porta = _valor(config, "servidor.port")
    if existe and _tipo_exato(porta, int) and not 1 <= porta <= 65535:
        erros.append("servidor.port precisa estar entre 1 e 65535")

    existe, token = _valor(config, "web.api_token")
    if existe and token is not None:
        if not isinstance(token, str):
            erros.append("web.api_token precisa ser texto ou null")
        elif token and len(token) < 32:
            erros.append("web.api_token precisa ter pelo menos 32 caracteres")

    for caminho in ("codar.testes.comando_python", "codar.testes.comando_node"):
        existe, comando = _valor(config, caminho)
        if not existe:
            continue
        valido = (
            isinstance(comando, str) and bool(comando.strip())
        ) or (
            isinstance(comando, list) and bool(comando) and
            all(isinstance(item, str) and item for item in comando)
        )
        if not valido:
            erros.append(f"{caminho} precisa ser texto ou argv nao vazio")

    existe, max_tokens = _valor(config, "llm.max_tokens")
    if existe and max_tokens is not None:
        if not _tipo_exato(max_tokens, int):
            erros.append("llm.max_tokens precisa ser inteiro ou null")
        elif max_tokens < 0:
            erros.append("llm.max_tokens precisa ser >= 0")

    _, margem_contexto = _valor(config, "context_engine.safety_margin_tokens")
    if (
        _tipo_exato(janela, int)
        and (max_tokens is None or _tipo_exato(max_tokens, int))
        and _tipo_exato(margem_contexto, int)
        and (max_tokens or 0) + margem_contexto >= janela
    ):
        erros.append(
            "llm.max_tokens + context_engine.safety_margin_tokens precisa ser menor que llm.context_window_tokens"
        )

    existe, backend = _valor(config, "codar.testes.sandbox.backend")
    if existe and backend not in ("auto", "bubblewrap", "docker", "processo"):
        erros.append("codar.testes.sandbox.backend invalido")

    existe, permitidos = _valor(config, "codar.testes.sandbox.comandos_permitidos")
    if existe:
        valido = isinstance(permitidos, list) and all(
            isinstance(argv, list) and argv and
            all(isinstance(item, str) and item for item in argv)
            for argv in permitidos
        )
        if not valido:
            erros.append(
                "codar.testes.sandbox.comandos_permitidos precisa ser lista de argv nao vazios"
            )

    existe, modos = _valor(config, "agent.enabled_modes")
    if existe:
        permitidos = {"analyze", "suggest", "edit"}
        if not isinstance(modos, list) or not modos or not all(
            isinstance(item, str) and item in permitidos for item in modos
        ):
            erros.append(
                "agent.enabled_modes precisa ser uma lista nao vazia de analyze/suggest/edit"
            )
        elif len(modos) != len(set(modos)):
            erros.append("agent.enabled_modes nao pode conter modos duplicados")

    existe, rollout = _valor(config, "agent.rollout_mode")
    if existe and rollout not in ("off", "read_only", "full"):
        erros.append("agent.rollout_mode precisa ser off, read_only ou full")

    existe, confiaveis = _valor(config, "agent.trusted_project_paths")
    if existe and (
        not isinstance(confiaveis, list)
        or not all(isinstance(item, str) and item.strip() for item in confiaveis)
    ):
        erros.append("agent.trusted_project_paths precisa ser uma lista de caminhos nao vazios")
    if rollout == "full" and (not existe or not confiaveis):
        erros.append("agent.rollout_mode=full exige ao menos um caminho em agent.trusted_project_paths")

    existe, mp_context = _valor(config, "worker.multiprocessing_context")
    if existe and mp_context not in ("spawn", "fork", "forkserver"):
        erros.append("worker.multiprocessing_context precisa ser spawn, fork ou forkserver")

    _, job_deadline = _valor(config, "worker.job_deadline_seconds")
    _, task_deadline = _valor(config, "engine.task_deadline_seconds")
    if (
        _tipo_exato(job_deadline, int) and _tipo_exato(task_deadline, int)
        and job_deadline < task_deadline
    ):
        erros.append("worker.job_deadline_seconds precisa ser >= engine.task_deadline_seconds")

    for caminho in ("benchmark.primary_model", "benchmark.baseline_model"):
        existe, modelo = _valor(config, caminho)
        if existe and modelo is not None:
            if not isinstance(modelo, str):
                erros.append(f"{caminho} precisa ser texto ou null")
            elif not modelo.strip():
                erros.append(f"{caminho} nao pode ser vazio")

    if erros:
        raise ConfigError("config.json invalido:\n- " + "\n- ".join(erros))
    config["_config_warnings"] = avisos_config(config)
    return config


def avisos_config(config):
    """Diagnosticos operacionais nao fatais, separados dos erros de schema."""
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
