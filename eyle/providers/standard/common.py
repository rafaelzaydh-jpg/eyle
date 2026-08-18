#!/usr/bin/env python3
"""Shared plumbing for the bundled standard provider."""
import copy
import json
import os
import re
import subprocess

from eyle.contracts.capability import RESULT_FIELDS, physical_effect, result as capability_result
from eyle.capabilities.registry import Provider
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from eyle.providers.standard.workspace_io import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)
from eyle.providers.standard.editing import (  # noqa: E402
    localizar_simbolo,
    localizar_simbolo_no_projeto,
    rodar_testes_projeto,
)
from eyle.providers.standard.project_inspection import (  # noqa: E402
    calculate as calculate_expression,
    count_tokens as count_project_tokens,
    inspect_project as inspect_project_signals,
    project_stats as measure_project_stats,
)
from eyle.providers.standard.git_tools import git_status as inspect_git_status, git_diff as inspect_git_diff  # noqa: E402
from eyle.providers.standard.code_relations import analyze_symbol_relations  # noqa: E402
from eyle.providers.standard.text_hash import hash_texto  # noqa: E402
from eyle.contracts.observation import (  # noqa: E402
    CoverageContractError, materialize_snapshot_handle, normalize_coverage, normalize_effect, register_snapshot_handle, result_observation_fields,
)
from eyle.runtime.observation import resolve_frontier, consume_frontier  # noqa: E402
from eyle.providers.standard.sandbox import executar_comando_livre_no_sandbox, export_active_sandbox_zip, ErroSandbox  # noqa: E402
from eyle.providers.standard.workspace_policy import (  # noqa: E402
    build_protected_resource_index, is_protected_workspace_resource, protected_resource_info,
)
from eyle.providers.standard.file_scope import (  # noqa: E402
    FileScopeError, normalize_scope_selectors, resolve_file_scope,
)

PROJECT_BASE_DIR = os.path.dirname(BASE_DIR)


def _standard_context(ctx):
    provider_context = (ctx or {}).get("provider_context") or {}
    value = provider_context.get("standard") or {} if isinstance(provider_context, dict) else {}
    return value if isinstance(value, dict) else {}


def _standard_config(config):
    providers = (config or {}).get("providers") or {}
    value = providers.get("standard") or {} if isinstance(providers, dict) else {}
    return value if isinstance(value, dict) else {}


def _standard_tests_config(config):
    value = _standard_config(config).get("tests") or {}
    return value if isinstance(value, dict) else {}

_CAMPOS_RESULTADO = RESULT_FIELDS


def _resultado(status, ok, executed, changed=False, error_code=None, detail=None, retryable=None,
               failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None, physical_effect=None):
    """Small provider convenience wrapper over the universal result contract."""
    return capability_result(
        status, ok, executed, changed=changed, error_code=error_code, detail=detail,
        retryable=retryable, failure_scope=failure_scope, failure_resource=failure_resource,
        observations=observations, coverage=coverage, frontiers=frontiers,
        physical_effect_value=physical_effect,
    )


def _sucesso(detail=None, changed=False, *, observations=None, coverage=None, frontiers=None, physical_effect=None):
    if isinstance(detail, dict):
        if observations is None: observations = detail.get("observations")
        if coverage is None: coverage = detail.get("coverage")
        if frontiers is None: frontiers = detail.get("frontiers")
    return _resultado(
        "success", True, True, changed=changed, detail=detail,
        observations=observations, coverage=coverage, frontiers=frontiers, physical_effect=physical_effect,
    )


def _falha(error_code, detail, executed=False, changed=False, retryable=None, *, failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None, physical_effect=None):
    return _resultado(
        "failed", False, executed, changed=changed,
        error_code=error_code, detail=detail, retryable=retryable,
        failure_scope=failure_scope, failure_resource=failure_resource,
        observations=observations, coverage=coverage, frontiers=frontiers, physical_effect=physical_effect,
    )


def _pulado(detail, error_code=None, *, physical_effect=None):
    return _resultado("skipped", True, False, error_code=error_code, detail=detail, physical_effect=physical_effect)


def _source_name(arguments):
    raw = str((arguments or {}).get("source") or "workspace").strip().lower()
    return raw if raw in {"workspace", "eyle"} else "workspace"


def _caminho_fonte(ctx, arguments):
    """Resolve an observation/sandbox source without granting real self-write authority."""
    projeto = _standard_context(ctx)
    source = _source_name(arguments)
    if source == "eyle":
        root = projeto.get("eyle_root")
    else:
        root = projeto.get("caminho_origem")
    return os.path.realpath(root) if root and os.path.isdir(root) else None


def _source_unavailable(arguments):
    source = _source_name(arguments)
    return _falha(
        "SOURCE_NOT_AVAILABLE",
        {
            "source": source,
            "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
            "message": f"requested physical source '{source}' is unavailable",
        },
        retryable=False, failure_scope="request", failure_resource=source,
    )


def _self_runtime_path_blocked(arguments, relative_path):
    """Keep Eyle self-analysis on source/config, not live user/runtime state."""
    if _source_name(arguments) != "eyle":
        return False
    normalized = str(relative_path or "").replace("\\", "/").strip("/").lower()
    if not normalized:
        return False
    first = normalized.split("/", 1)[0]
    return first in {"workspace", "memory", "context", "agent_memory", ".git"}


def _protected_resource_failure(root, relative_path, *, executed=True):
    info = protected_resource_info(root, relative_path, index=build_protected_resource_index(root))
    return _falha(
        "PROTECTED_RESOURCE_READ_BLOCKED",
        "content access is restricted for this protected resource",
        executed=executed, retryable=False, failure_scope="resource",
        failure_resource=str(relative_path or "").replace("\\", "/"),
    )
