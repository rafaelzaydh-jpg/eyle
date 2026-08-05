#!/usr/bin/env python3
"""Cobertura deterministica para auditorias gerais de projeto.

A revisao 55.21 separa cobertura minima de cobertura real. Este modulo
nao pergunta a LLM se a investigacao foi suficiente: classifica o inventario e
as evidencias frescas, calcula os sete gates minimos, mede o alcance observado
e gera uma divulgacao honesta que nao pode ser sobrescrita pelo modelo.
"""
from __future__ import annotations

import os
import re
from copy import deepcopy

from engine.test_execution import latest_test_execution

PROJECT_AUDIT_CRITERIA = [
    "inventory_complete",
    "entrypoint_read",
    "core_logic_read",
    "error_paths_read",
    "tests_or_test_config_checked",
    "coverage_reported",
    "grounded_answer",
]

_SOURCE_EXTENSIONS = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".swift", ".kt", ".kts", ".scala", ".sh",
    ".bash", ".zsh", ".ps1", ".lua", ".ex", ".exs", ".dart", ".vue",
    ".svelte",
}

_ENTRYPOINT_BASENAMES = {
    "app.py", "main.py", "__main__.py", "run.py", "server.py", "wsgi.py",
    "asgi.py", "manage.py", "cli.py", "index.js", "index.mjs", "index.cjs",
    "index.ts", "index.tsx", "index.jsx", "server.js", "server.ts",
    "main.js", "main.ts", "main.go", "main.rs", "program.cs",
}

_TEST_CONFIG_BASENAMES = {
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "jest.config.js",
    "jest.config.ts", "vitest.config.js", "vitest.config.ts", "karma.conf.js",
    "playwright.config.js", "playwright.config.ts", "cypress.config.js",
    "cypress.config.ts", ".mocharc", ".mocharc.json", "package.json",
}

_ERROR_PATH_HINTS = (
    "error", "exception", "recovery", "fallback", "retry", "rollback",
    "failure", "validator", "validation", "guard", "handler", "worker",
    "queue", "sandbox", "security", "middleware",
)

_ERROR_PATTERN = re.compile(
    r"\b(?:try|except|catch|throw|raise|error|exception|failed|failure|"
    r"fallback|rollback|retry|invalid|validate|validation)\b",
    re.IGNORECASE,
)

_CRITICAL_PIPELINE_ROLES = {
    "entrypoints", "orchestrators", "state_persistence",
    "grounding_recovery_validation", "core_logic",
}

_PORTUGUESE_STRONG_WORDS = {
    "analise", "análise", "projeto", "arquivo", "codigo", "código",
    "testes", "faça", "faca", "revise", "verifique", "sistema",
    "componente", "componentes", "leitura", "cobertura", "riscos",
}
_ENGLISH_STRONG_WORDS = {
    "analyze", "analyse", "review", "inspect", "project", "code",
    "source", "tests", "system", "component", "components", "coverage",
    "risks", "file", "files", "audit",
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)



def _path(path):
    return str(path or "").strip().replace("\\", "/").lstrip("./")


def is_documentation_path(path):
    """Documentacao nunca satisfaz leitura de codigo em ``project_audit``."""
    normalized = _path(path)
    lower = normalized.lower()
    base = lower.rsplit("/", 1)[-1]
    if lower == "docs" or lower.startswith("docs/") or "/docs/" in f"/{lower}/":
        return True
    if base.startswith("readme") or base.startswith("changelog"):
        return True
    if base in {
        "license", "license.md", "license.txt", "contributing.md",
        "code_of_conduct.md", "security.md", "authors", "authors.md",
    }:
        return True
    return os.path.splitext(base)[1] in {".md", ".rst", ".adoc"}


def is_test_path(path):
    normalized = _path(path).lower()
    base = normalized.rsplit("/", 1)[-1]
    parts = [part for part in normalized.split("/") if part]
    return (
        any(part in {"test", "tests", "spec", "specs", "__tests__"} for part in parts[:-1])
        or base.startswith("test_")
        or base.endswith("_test.py")
        or ".test." in base
        or ".spec." in base
    )


def is_test_config_path(path):
    return _path(path).lower().rsplit("/", 1)[-1] in _TEST_CONFIG_BASENAMES


def is_source_path(path):
    normalized = _path(path)
    if not normalized or is_documentation_path(normalized) or is_test_path(normalized):
        return False
    return os.path.splitext(normalized.lower())[1] in _SOURCE_EXTENSIONS


def _inventory_files(inventory):
    return sorted({
        _path(item.get("caminho"))
        for item in (inventory or {}).get("entradas") or []
        if isinstance(item, dict)
        and item.get("tipo") == "arquivo"
        and _path(item.get("caminho"))
    })


def _entrypoint_candidates(source_files):
    explicit = [
        path for path in source_files
        if path.lower().rsplit("/", 1)[-1] in _ENTRYPOINT_BASENAMES
    ]
    if explicit:
        return explicit
    root = [path for path in source_files if "/" not in path]
    if root:
        return root
    return source_files[:1]


def _core_candidates(source_files, entrypoints):
    preferred_dirs = ("src/", "engine/", "app/", "core/", "lib/", "server/", "backend/", "api/")
    non_entry = [path for path in source_files if path not in set(entrypoints)]
    preferred = [path for path in non_entry if path.lower().startswith(preferred_dirs)]
    if preferred:
        return preferred + [path for path in non_entry if path not in preferred]
    return non_entry or list(entrypoints)


def _error_candidates(source_files):
    hinted = [
        path for path in source_files
        if any(hint in path.lower() for hint in _ERROR_PATH_HINTS)
    ]
    return hinted


def _fresh_evidence(evidence):
    return [
        item for item in evidence or []
        if isinstance(item, dict) and item.get("estado") == "fresh"
    ]


def _evidence_paths(evidence):
    return sorted({_path(item.get("arquivo")) for item in evidence if _path(item.get("arquivo"))})


def _full_read_paths(evidence):
    return sorted({
        _path(item.get("arquivo"))
        for item in evidence
        if _path(item.get("arquivo")) and item.get("leitura_completa") is True
    })


def _content(item):
    raw = item.get("conteudo_raw")
    if isinstance(raw, str):
        return raw
    numbered = item.get("conteudo")
    return numbered if isinstance(numbered, str) else ""





def _successful_test_execution(actions):
    return latest_test_execution(actions)


def _pipeline_critical_paths(audit_pipeline, source_files, entrypoints, cores, error_candidates):
    pipeline = audit_pipeline if isinstance(audit_pipeline, dict) else {}
    catalog = pipeline.get("catalog") if isinstance(pipeline.get("catalog"), dict) else {}
    candidates = catalog.get("candidates") if isinstance(catalog.get("candidates"), list) else []
    critical = []
    roles_by_path = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        path = _path(item.get("path"))
        if not path:
            continue
        roles_by_path[path] = set(item.get("roles") or [])

    # So conta como critico o que o catalogo classificou em papel critico.
    # Arquivos auxiliares escolhidos pelo Scout continuam sendo lidos, mas nao
    # inflam a metrica de componentes criticos.
    for scout_key in ("initial_scout", "gap_scout"):
        scout = pipeline.get(scout_key) if isinstance(pipeline.get(scout_key), dict) else {}
        for path in scout.get("selected_paths") or []:
            path = _path(path)
            roles = roles_by_path.get(path, set())
            if path in source_files and (_CRITICAL_PIPELINE_ROLES & roles):
                critical.append(path)
    for slot in catalog.get("required_slots") or []:
        if not isinstance(slot, dict):
            continue
        path = _path(slot.get("path"))
        role = str(slot.get("role") or "")
        roles = roles_by_path.get(path, set()) | ({role} if role else set())
        if path in source_files and (_CRITICAL_PIPELINE_ROLES & roles):
            critical.append(path)

    if not critical:
        # Checkpoints antigos nao possuem Scout. Escolhemos no maximo um
        # candidato por papel critico, mantendo a contagem pequena e auditavel.
        by_role = {}
        for item in candidates:
            if not isinstance(item, dict):
                continue
            path = _path(item.get("path"))
            roles = set(item.get("roles") or [])
            if path not in source_files:
                continue
            for role in _CRITICAL_PIPELINE_ROLES & roles:
                by_role.setdefault(role, path)
        critical.extend(by_role.values())

    if not critical:
        fallback = list(entrypoints[:1])
        fallback.extend(path for path in cores if path not in fallback)
        fallback.extend(path for path in error_candidates if path not in fallback)
        critical.extend(fallback[:5])
    return list(dict.fromkeys(critical))


def _coverage_level(
    *, inventory_complete, source_files, source_read, full_source_read,
    structural_passed,
):
    if not source_read:
        return "none"
    if inventory_complete and source_files and set(source_files).issubset(set(full_source_read)):
        return "complete"
    if inventory_complete and structural_passed:
        return "targeted"
    return "partial"


def detect_response_language(language_sample):
    """Detecta PT/EN sem usar artigos isolados como sinal de idioma."""
    text = str(language_sample or "")
    tokens = {token.casefold() for token in _WORD_RE.findall(text)}
    pt_score = sum(2 for token in tokens if token in _PORTUGUESE_STRONG_WORDS)
    en_score = sum(2 for token in tokens if token in _ENGLISH_STRONG_WORDS)
    if re.search(r"[áàâãéêíóôõúç]", text, re.IGNORECASE):
        pt_score += 3
    # Contracoes e preposicoes compostas sao sinais melhores que "a/o".
    if re.search(r"\b(?:do|da|dos|das|não|nao|para o|para a|no|na)\b", text, re.IGNORECASE):
        pt_score += 1
    if re.search(r"\b(?:the|this|that|with|without|within|from)\b", text, re.IGNORECASE):
        en_score += 1
    return "pt" if pt_score > en_score else "en"


def render_coverage_disclosure(coverage, language_sample=""):
    """Gera um cabecalho factual a partir de metricas calculadas pelo sistema."""
    metrics = (coverage or {}).get("coverage") or {}
    if not metrics:
        return ""
    portuguese = detect_response_language(language_sample) == "pt"
    level = metrics.get("level") or "partial"
    critical = int(metrics.get("critical_components_read") or 0)
    code_read = int(metrics.get("code_files_read") or 0)
    code_total = int(metrics.get("code_files_total") or 0)
    tests_executed = metrics.get("tests_executed") is True
    tests_passed = metrics.get("tests_passed") is True

    if portuguese:
        opening = {
            "complete": "Cobertura integral dos arquivos de código inventariados concluída.",
            "targeted": "Análise direcionada concluída.",
            "partial": "Análise parcial concluída.",
            "none": "Nenhuma análise de código foi concluída.",
        }.get(level, "Análise parcial concluída.")
        critical_line = (
            "Foi revisado 1 componente crítico."
            if critical == 1 else f"Foram revisados {critical} componentes críticos."
        )
        code_line = (
            f"Foi lido {code_read} de {code_total} arquivo de código inventariado."
            if code_total == 1 else
            f"Foram lidos {code_read} de {code_total} arquivos de código inventariados."
        )
        lines = [opening, critical_line, code_line]
        if tests_executed and tests_passed:
            lines.append("Os testes foram executados e passaram na tarefa atual.")
        elif tests_executed:
            lines.append("Os testes foram executados, mas não passaram integralmente na tarefa atual.")
        else:
            lines.append("Os testes não foram executados.")
        lines.append("Não é possível afirmar ausência total de bugs.")
        return "\n".join(lines)

    opening = {
        "complete": "Full coverage of the inventoried source files was completed.",
        "targeted": "Targeted analysis completed.",
        "partial": "Partial analysis completed.",
        "none": "No source-code analysis was completed.",
    }.get(level, "Partial analysis completed.")
    critical_line = (
        "1 critical component was reviewed."
        if critical == 1 else f"{critical} critical components were reviewed."
    )
    code_line = (
        f"{code_read} of 1 inventoried source file was read."
        if code_total == 1 else f"{code_read} of {code_total} inventoried source files were read."
    )
    lines = [opening, critical_line, code_line]
    if tests_executed and tests_passed:
        lines.append("Tests were executed and passed in the current task.")
    elif tests_executed:
        lines.append("Tests were executed but did not fully pass in the current task.")
    else:
        lines.append("Tests were not executed.")
    lines.append("It is not possible to claim that the project is completely free of bugs.")
    return "\n".join(lines)


def evaluate_project_audit_coverage(
    inventory,
    evidence,
    *,
    coverage_reported=False,
    grounded_answer=False,
    audit_pipeline=None,
    actions=None,
    selected_evidence_ids=None,
):
    """Calcula os gates minimos e os candidatos de proxima leitura."""
    inventory = inventory if isinstance(inventory, dict) else {}
    fresh = _fresh_evidence(evidence)
    files = _inventory_files(inventory)
    source_files = [path for path in files if is_source_path(path)]
    documentation_files = [path for path in files if is_documentation_path(path)]
    test_files = [path for path in files if is_test_path(path)]
    test_configs = [path for path in files if is_test_config_path(path)]
    entrypoints = _entrypoint_candidates(source_files)
    cores = _core_candidates(source_files, entrypoints)
    error_candidates = _error_candidates(source_files)

    read_paths = _evidence_paths(fresh)
    source_read = [path for path in read_paths if is_source_path(path)]
    docs_read = [path for path in read_paths if is_documentation_path(path)]
    tests_read = [path for path in read_paths if is_test_path(path)]
    test_configs_read = [path for path in read_paths if is_test_config_path(path)]
    full_read = set(_full_read_paths(fresh))
    full_source_read = sorted(path for path in source_read if path in full_read)
    selected_ids = {str(item) for item in selected_evidence_ids or [] if str(item)}
    selected_fresh = [item for item in fresh if item.get("id") in selected_ids]

    inventory_complete = bool(
        inventory.get("varredura_completa") and not inventory.get("truncado")
    )
    entrypoint_read = any(path in source_read for path in entrypoints)

    # Em projetos com mais de um arquivo-fonte, o entrypoint sozinho nao pode
    # contar também como nucleo: sao exigidos pelo menos dois arquivos de codigo.
    minimum_code_files_required = 1 if len(source_files) <= 1 else 2
    enough_code_files = len(set(source_read)) >= minimum_code_files_required
    distinct_core_candidates = [path for path in cores if path not in set(entrypoints)]
    if distinct_core_candidates:
        core_file_read = any(path in source_read for path in distinct_core_candidates)
    else:
        core_file_read = any(path in source_read for path in cores)
    core_logic_read = bool(core_file_read and enough_code_files)

    error_evidence_paths = []
    for item in fresh:
        path = _path(item.get("arquivo"))
        if not is_source_path(path):
            continue
        if path in error_candidates or _ERROR_PATTERN.search(_content(item)):
            error_evidence_paths.append(path)
    # Uma leitura completa do nucleo também representa uma verificacao real de
    # caminhos de erro, mesmo quando nenhum handler explicito existe.
    fully_inspected_core = [path for path in source_read if path in full_read and path in cores]
    error_paths_read = bool(error_evidence_paths or fully_inspected_core)
    if len(source_files) > 1:
        error_paths_read = bool(error_paths_read and enough_code_files)

    if test_files or test_configs:
        tests_or_test_config_checked = bool(tests_read or test_configs_read)
    else:
        # Ausencia so pode ser afirmada quando a arvore foi varrida por completo.
        tests_or_test_config_checked = inventory_complete

    criteria = {
        "inventory_complete": inventory_complete,
        "entrypoint_read": entrypoint_read,
        "core_logic_read": core_logic_read,
        "error_paths_read": error_paths_read,
        "tests_or_test_config_checked": tests_or_test_config_checked,
        "coverage_reported": bool(coverage_reported),
        "grounded_answer": bool(grounded_answer),
    }
    missing = [name for name in PROJECT_AUDIT_CRITERIA if not criteria[name]]
    evidence_only_contains_docs = bool(fresh and docs_read and not source_read)

    next_candidates = []
    if not entrypoint_read:
        next_candidates.extend(path for path in entrypoints if path not in read_paths)
    if not core_logic_read:
        next_candidates.extend(path for path in cores if path not in read_paths)
    if not error_paths_read:
        next_candidates.extend(path for path in error_candidates if path not in read_paths)
        next_candidates.extend(path for path in cores if path not in read_paths)
    if not tests_or_test_config_checked:
        next_candidates.extend(path for path in test_files + test_configs if path not in read_paths)
    next_candidates = list(dict.fromkeys(next_candidates))

    structural_passed = all(
        criteria[name]
        for name in PROJECT_AUDIT_CRITERIA
        if name not in {"coverage_reported", "grounded_answer"}
    )
    critical_paths = _pipeline_critical_paths(
        audit_pipeline, source_files, entrypoints, cores, error_candidates,
    )
    critical_read_paths = [path for path in critical_paths if path in source_read]
    docs_used_paths = sorted({
        _path(item.get("arquivo"))
        for item in selected_fresh
        if is_documentation_path(item.get("arquivo"))
    })
    test_execution = _successful_test_execution(actions)
    real_coverage = {
        "inventory_complete": inventory_complete,
        "code_files_total": len(source_files),
        "code_files_read": len(set(source_read)),
        "code_files_fully_read": len(set(full_source_read)),
        "critical_components_total": len(critical_paths),
        "critical_components_read": len(critical_read_paths),
        "tests_executed": test_execution["executed"],
        "tests_passed": test_execution["passed"],
        "test_run_attempts": test_execution["attempts"],
        "docs_used": len(docs_used_paths),
        "level": _coverage_level(
            inventory_complete=inventory_complete,
            source_files=source_files,
            source_read=source_read,
            full_source_read=full_source_read,
            structural_passed=structural_passed,
        ),
    }

    return {
        "schema_version": 2,
        "task_type": "project_audit",
        "required_criteria": list(PROJECT_AUDIT_CRITERIA),
        "criteria": criteria,
        "missing": missing,
        "passed": not missing,
        "coverage": real_coverage,
        "critical_components": {
            "candidates": critical_paths,
            "read": critical_read_paths,
        },
        "docs_used_paths": docs_used_paths,
        "test_execution": test_execution,
        "failure_code": (
            "SOURCE_CODE_NOT_ANALYZED"
            if evidence_only_contains_docs
            else "PROJECT_AUDIT_COVERAGE_INCOMPLETE"
            if missing else None
        ),
        "evidence_only_contains_docs": evidence_only_contains_docs,
        "inventory": {
            "complete": inventory_complete,
            "hash": inventory.get("inventory_hash"),
            "total_files": len(files),
            "source_files": len(source_files),
            "test_files": len(test_files),
            "test_configs": len(test_configs),
            "documentation_files": len(documentation_files),
        },
        "reads": {
            "all": read_paths,
            "source": source_read,
            "documentation": docs_read,
            "tests": tests_read,
            "test_configs": test_configs_read,
            "full_files": sorted(full_read),
        },
        "roles": {
            "entrypoint_candidates": entrypoints,
            "core_candidates": cores,
            "error_candidates": error_candidates,
        },
        "minimum_code_files_required": minimum_code_files_required,
        "next_read_candidates": next_candidates,
    }


def next_project_audit_action(coverage):
    """Devolve uma leitura deterministica para o primeiro gate pendente."""
    if not isinstance(coverage, dict):
        return None
    for path in coverage.get("next_read_candidates") or []:
        if isinstance(path, str) and path.strip():
            return {"tool": "read_file", "arguments": {"caminho_relativo": path}}
    return None


def public_coverage_report(coverage):
    """Remove dados internos desnecessarios antes de publicar/persistir detalhes."""
    if not isinstance(coverage, dict):
        return {}
    return deepcopy({
        key: coverage.get(key)
        for key in (
            "schema_version", "task_type", "required_criteria", "criteria",
            "missing", "passed", "failure_code", "evidence_only_contains_docs",
            "inventory", "reads", "roles", "minimum_code_files_required",
            "coverage", "critical_components", "docs_used_paths", "test_execution",
        )
    })
