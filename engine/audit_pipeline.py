#!/usr/bin/env python3
"""Pipeline estruturado de auditoria de projeto da revisao 55.18.

Responsabilidades:
- classificar deterministicamente candidatos do inventario;
- garantir uma base minima de leitura antes de qualquer conclusao;
- validar/normalizar escolhas do Scout sem permitir caminhos inventados;
- relacionar testes aos componentes selecionados;
- manter um estado pequeno e persistente das fases Scout -> leitura -> gaps -> finalizer.
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy

from engine.analysis_coverage import (
    is_documentation_path,
    is_source_path,
    is_test_config_path,
    is_test_path,
)

SCHEMA_VERSION = 1

_ENTRYPOINT_NAMES = {
    "main.py", "app.py", "__main__.py", "run.py", "server.py", "wsgi.py",
    "asgi.py", "manage.py", "cli.py", "index.js", "index.mjs", "index.cjs",
    "index.ts", "index.tsx", "index.jsx", "main.js", "main.ts", "main.go",
    "main.rs", "program.cs",
}
_ORCHESTRATOR_NAMES = {
    "engine.py", "agent.py", "worker.py", "orchestrator.py", "pipeline.py",
    "runner.py", "dispatcher.py", "controller.py", "coordinator.py",
}
_STATE_HINTS = (
    "state", "persist", "storage", "store", "database", "db", "queue",
    "repository", "memory", "context", "session", "checkpoint", "cache",
)
_VALIDATION_HINTS = (
    "ground", "recover", "fallback", "valid", "verify", "guard", "gate",
    "error", "exception", "security", "sandbox", "response_adapter",
)
_VALIDATION_CANONICAL_BONUS = {
    "grounding.py": 60,
    "response_recovery.py": 50,
    "utility_gate.py": 40,
    "response_adapter.py": 35,
    "validar.py": 25,
    "validation.py": 25,
}
_CONFIG_NAMES = {
    "config.json", "pyproject.toml", "setup.cfg", "setup.py", "package.json",
    "requirements.txt", "requirements.lock", "poetry.lock", "pipfile",
    "go.mod", "cargo.toml", "composer.json", ".env.example", "dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "pytest.ini", "tox.ini",
}
_IGNORED_PARTS = {
    ".git", ".github", "node_modules", "vendor", "dist", "build", ".venv",
    "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


def _path(value):
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _base(path):
    return _path(path).lower().rsplit("/", 1)[-1]


def _stem(path):
    name = _base(path)
    for suffix in (".test", ".spec", "_test", "test_"):
        name = name.replace(suffix, "")
    return os.path.splitext(name)[0]


def _inventory_files(inventory):
    files = []
    for item in (inventory or {}).get("entradas") or []:
        if not isinstance(item, dict) or item.get("tipo") != "arquivo":
            continue
        path = _path(item.get("caminho"))
        if not path:
            continue
        parts = {part.lower() for part in path.split("/")}
        if parts & _IGNORED_PARTS:
            continue
        files.append(path)
    return sorted(dict.fromkeys(files))


def _depth_score(path):
    # Raiz e modulos centrais curtos recebem pequena preferencia.
    return max(0, 8 - _path(path).count("/"))


def _score(path, role):
    lower = _path(path).lower()
    base = _base(path)
    score = _depth_score(path)
    if role == "entrypoint":
        score += 120 if base in _ENTRYPOINT_NAMES else 0
        score += 20 if "/" not in lower else 0
    elif role == "orchestrator":
        score += 110 if base in _ORCHESTRATOR_NAMES else 0
        score += 20 if any(part in lower for part in ("engine/", "core/", "src/")) else 0
    elif role == "state_persistence":
        score += 80 + sum(8 for hint in _STATE_HINTS if hint in lower)
    elif role == "grounding_recovery_validation":
        score += 80 + sum(8 for hint in _VALIDATION_HINTS if hint in lower)
        score += _VALIDATION_CANONICAL_BONUS.get(base, 0)
    elif role == "configuration":
        score += 80 if base in _CONFIG_NAMES else 0
        score += 15 if "/" not in lower else 0
    elif role == "test":
        score += 70
    elif role == "core_logic":
        score += 40
        score += 15 if any(lower.startswith(prefix) for prefix in ("engine/", "src/", "app/", "core/", "lib/")) else 0
    return score


def _candidate(path, role, reason):
    return {
        "path": path,
        "role": role,
        "score": _score(path, role),
        "reason": reason,
    }


def _sorted_unique(items):
    best = {}
    for item in items:
        path = item["path"]
        current = best.get(path)
        if current is None or item["score"] > current["score"]:
            best[path] = item
    return sorted(best.values(), key=lambda item: (-item["score"], item["path"]))


def _related_test_score(test_path, component_paths):
    test_lower = _path(test_path).lower()
    test_stem = _stem(test_path)
    score = 0
    for component in component_paths:
        component_stem = _stem(component)
        if not component_stem:
            continue
        if component_stem == test_stem:
            score = max(score, 100)
        elif component_stem in test_lower or test_stem in _path(component).lower():
            score = max(score, 80)
        elif component_stem in {"agent", "engine", "worker", "state", "grounding", "validation", "recovery"}:
            if component_stem in test_lower:
                score = max(score, 70)
    return score


def build_audit_candidate_catalog(inventory, *, max_candidates=48):
    """Classifica o inventario sem pedir a LLM para descobrir arquivos do zero."""
    files = _inventory_files(inventory)
    source = [path for path in files if is_source_path(path)]
    tests = [path for path in files if is_test_path(path)]
    test_configs = [path for path in files if is_test_config_path(path)]
    configs = [path for path in files if _base(path) in _CONFIG_NAMES or is_test_config_path(path)]

    groups = {
        "entrypoints": [],
        "orchestrators": [],
        "state_persistence": [],
        "grounding_recovery_validation": [],
        "core_logic": [],
        "tests": [],
        "configuration": [],
    }

    for path in source:
        lower = path.lower()
        base = _base(path)
        if base in _ENTRYPOINT_NAMES:
            groups["entrypoints"].append(_candidate(path, "entrypoint", "nome canonico de entrypoint"))
        if base in _ORCHESTRATOR_NAMES:
            groups["orchestrators"].append(_candidate(path, "orchestrator", "nome canonico de orquestrador"))
        if any(hint in lower for hint in _STATE_HINTS):
            groups["state_persistence"].append(_candidate(path, "state_persistence", "caminho sugere estado ou persistencia"))
        if any(hint in lower for hint in _VALIDATION_HINTS):
            groups["grounding_recovery_validation"].append(
                _candidate(path, "grounding_recovery_validation", "caminho sugere grounding, recovery, erro ou validacao")
            )
        if any(lower.startswith(prefix) for prefix in ("engine/", "src/", "app/", "core/", "lib/", "server/", "backend/", "api/")):
            groups["core_logic"].append(_candidate(path, "core_logic", "arquivo em diretorio central"))

    # Fallbacks deterministicos para layouts pequenos ou exoticos.
    root_source = [path for path in source if "/" not in path]
    if not groups["entrypoints"]:
        for path in root_source[:4] or source[:2]:
            groups["entrypoints"].append(_candidate(path, "entrypoint", "fallback de arquivo-fonte na raiz"))
    if not groups["orchestrators"]:
        non_entry = [path for path in source if path not in {i["path"] for i in groups["entrypoints"]}]
        for path in non_entry[:8]:
            groups["orchestrators"].append(_candidate(path, "orchestrator", "fallback de nucleo central"))
    if not groups["core_logic"]:
        for path in source[:12]:
            groups["core_logic"].append(_candidate(path, "core_logic", "fallback de codigo-fonte"))

    primary_components = []
    for key in ("entrypoints", "orchestrators", "state_persistence", "grounding_recovery_validation", "core_logic"):
        groups[key] = _sorted_unique(groups[key])
        primary_components.extend(item["path"] for item in groups[key][:8])
    primary_components = list(dict.fromkeys(primary_components))

    for path in tests:
        relation = _related_test_score(path, primary_components)
        reason = "teste relacionado aos componentes candidatos" if relation else "teste do projeto"
        item = _candidate(path, "test", reason)
        item["score"] += relation
        groups["tests"].append(item)
    for path in test_configs:
        item = _candidate(path, "test", "configuracao de testes")
        item["score"] += 30
        groups["tests"].append(item)
    for path in configs:
        groups["configuration"].append(_candidate(path, "configuration", "configuracao principal ou de testes"))

    for key in groups:
        groups[key] = _sorted_unique(groups[key])[:max_candidates]

    flat = []
    roles_by_path = {}
    reasons_by_path = {}
    scores_by_path = {}
    for group, items in groups.items():
        for item in items:
            path = item["path"]
            roles_by_path.setdefault(path, []).append(group)
            reasons_by_path.setdefault(path, []).append(item["reason"])
            scores_by_path[path] = max(scores_by_path.get(path, 0), item["score"])
    for path in sorted(roles_by_path, key=lambda p: (-scores_by_path[p], p)):
        flat.append({
            "path": path,
            "roles": roles_by_path[path],
            "score": scores_by_path[path],
            "reasons": list(dict.fromkeys(reasons_by_path[path])),
        })

    required_slots = []
    for group in (
        "entrypoints", "orchestrators", "tests", "configuration",
    ):
        if groups[group]:
            required_slots.append({"role": group, "path": groups[group][0]["path"]})

    return {
        "schema_version": SCHEMA_VERSION,
        "inventory_hash": (inventory or {}).get("inventory_hash"),
        "inventory_complete": bool((inventory or {}).get("varredura_completa") and not (inventory or {}).get("truncado")),
        "groups": groups,
        "candidates": flat[:max_candidates],
        "required_slots": required_slots,
        "all_candidate_paths": [item["path"] for item in flat[:max_candidates]],
        "counts": {
            "files": len(files),
            "source": len(source),
            "tests": len(tests),
            "test_configs": len(test_configs),
            "configuration": len(configs),
            "candidates": min(len(flat), max_candidates),
        },
    }


def _extract_payload(decision):
    if not isinstance(decision, dict):
        return {}
    final = decision.get("final")
    return final if isinstance(final, dict) else decision


def normalize_scout_selection(decision, catalog, *, already_read=None, limit=6, include_required=True, allow_empty=False):
    """Aceita apenas caminhos existentes no catalogo e preenche slots minimos."""
    payload = _extract_payload(decision)
    allowed = set((catalog or {}).get("all_candidate_paths") or [])
    already = {_path(item) for item in (already_read or []) if _path(item)}
    selected = payload.get("selected_paths") or payload.get("paths") or []
    if isinstance(selected, str):
        selected = [selected]
    normalized = []
    rejected = []
    # Slots do sistema entram primeiro: o Scout pode aprofundar, nao apagar a
    # cobertura basica deterministicamente escolhida.
    if include_required:
        for slot in (catalog or {}).get("required_slots") or []:
            path = _path(slot.get("path")) if isinstance(slot, dict) else ""
            if path and path in allowed and path not in already and path not in normalized:
                normalized.append(path)

    for raw in selected if isinstance(selected, list) else []:
        path = _path(raw)
        if path in allowed and path not in already and path not in normalized:
            normalized.append(path)
        elif path and path not in normalized:
            rejected.append(path)

    # Completa vagas restantes com a ordem deterministica de maior sinal.
    # Isso preserva o baseline mesmo quando o Scout retorna pouco, mas mantem
    # as escolhas validas do Scout antes do preenchimento automatico.
    if not allow_empty:
        groups = (catalog or {}).get("groups") or {}
        fallback_order = []
        for group in (
            "state_persistence", "grounding_recovery_validation",
            "core_logic", "entrypoints", "orchestrators", "tests", "configuration",
        ):
            fallback_order.extend(
                item.get("path") for item in groups.get(group) or [] if isinstance(item, dict)
            )
        fallback_order.extend((catalog or {}).get("all_candidate_paths") or [])
        for path in fallback_order:
            if path and path not in already and path not in normalized:
                normalized.append(path)
            if len(normalized) >= max(0, int(limit)):
                break

    normalized = normalized[:max(0, int(limit))]
    risks = payload.get("risk_hypotheses") or payload.get("risks") or []
    gaps = payload.get("gaps") or payload.get("missing_areas") or []
    if not isinstance(risks, list):
        risks = []
    if not isinstance(gaps, list):
        gaps = []
    return {
        "selected_paths": normalized,
        "rejected_paths": rejected,
        "risk_hypotheses": [str(item)[:500] for item in risks if isinstance(item, str)][:12],
        "gaps": [str(item)[:500] for item in gaps if isinstance(item, str)][:12],
        "rationale": str(payload.get("rationale") or payload.get("answer") or "")[:2000],
    }


def build_deterministic_audit_plan(catalog, *, already_read=None, limit=6):
    """Build the initial high-signal read plan without an LLM call."""
    selection = normalize_scout_selection(
        {}, catalog, already_read=already_read, limit=limit,
        include_required=True, allow_empty=False,
    )
    selection.update({
        "planner": "deterministic",
        "risk_hypotheses": [],
        "gaps": [],
        "rationale": "system-ranked inventory roles and required coverage slots",
    })
    return selection


def build_deterministic_gap_plan(
    catalog, coverage, *, already_read=None, limit=1,
):
    """Choose concrete gap-closing reads from system coverage only."""
    already = {_path(item) for item in (already_read or []) if _path(item)}
    allowed = set((catalog or {}).get("all_candidate_paths") or [])
    selected = []
    for raw in (coverage or {}).get("next_read_candidates") or []:
        path = _path(raw)
        if path and path in allowed and path not in already and path not in selected:
            selected.append(path)
        if len(selected) >= max(0, int(limit)):
            break
    if len(selected) < max(0, int(limit)):
        related = related_test_candidates(
            catalog, list(already), already_read=already,
            limit=max(0, int(limit)) - len(selected),
        )
        for path in related:
            if path not in already and path not in selected:
                selected.append(path)
    return {
        "selected_paths": selected[:max(0, int(limit))],
        "rejected_paths": [],
        "risk_hypotheses": [],
        "gaps": [str(item) for item in (coverage or {}).get("missing") or []],
        "rationale": "system-calculated audit coverage gaps",
        "planner": "deterministic",
    }


def ambiguous_gap_candidates(catalog, coverage, *, already_read=None):
    """Return unresolved candidates only when deterministic coverage has no path."""
    if (coverage or {}).get("next_read_candidates"):
        return []
    missing = [
        item for item in (coverage or {}).get("missing") or []
        if item not in {"coverage_reported", "grounded_answer"}
    ]
    if not missing:
        return []
    already = {_path(item) for item in (already_read or []) if _path(item)}
    candidates = [
        item for item in (catalog or {}).get("candidates") or []
        if isinstance(item, dict) and _path(item.get("path")) not in already
    ]
    if len(candidates) < 2:
        return []
    top_score = int(candidates[0].get("score") or 0)
    tied = [item for item in candidates if int(item.get("score") or 0) == top_score]
    return tied if len(tied) >= 2 else []

def related_test_candidates(catalog, component_paths, *, already_read=None, limit=2):
    already = {_path(item) for item in (already_read or [])}
    components = [_path(item) for item in component_paths if _path(item)]
    candidates = []
    for item in ((catalog or {}).get("groups") or {}).get("tests") or []:
        path = item.get("path")
        if not path or path in already:
            continue
        relation = _related_test_score(path, components)
        candidates.append((relation + int(item.get("score") or 0), path))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return [path for _, path in candidates[:max(0, int(limit))]]


def new_pipeline_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "awaiting_inventory",
        "catalog": {},
        "initial_scout": {},
        "gap_scout": {},
        "optional_expansion": {},
        "optional_expansion_used": False,
        "pending_reads": [],
        "completed_reads": [],
        "failed_reads": [],
        "finalizer_calls": 0,
    }


def normalize_pipeline_state(value):
    base = new_pipeline_state()
    if not isinstance(value, dict):
        return base
    for key in base:
        if key in value:
            base[key] = deepcopy(value[key])
    if not isinstance(base.get("catalog"), dict):
        base["catalog"] = {}
    for key in ("initial_scout", "gap_scout", "optional_expansion"):
        if not isinstance(base.get(key), dict):
            base[key] = {}
    base["optional_expansion_used"] = bool(base.get("optional_expansion_used"))
    for key in ("pending_reads", "completed_reads", "failed_reads"):
        values = base.get(key)
        base[key] = list(dict.fromkeys(_path(item) for item in values or [] if _path(item)))
    try:
        base["finalizer_calls"] = max(0, int(base.get("finalizer_calls") or 0))
    except (TypeError, ValueError):
        base["finalizer_calls"] = 0
    return base


def public_pipeline_state(value):
    state = normalize_pipeline_state(value)
    catalog = state.get("catalog") or {}
    return {
        "schema_version": state["schema_version"],
        "phase": state["phase"],
        "candidate_counts": catalog.get("counts") or {},
        "required_slots": catalog.get("required_slots") or [],
        "initial_scout": state.get("initial_scout") or {},
        "gap_scout": state.get("gap_scout") or {},
        "optional_expansion": state.get("optional_expansion") or {},
        "optional_expansion_used": bool(state.get("optional_expansion_used")),
        "pending_reads": state.get("pending_reads") or [],
        "completed_reads": state.get("completed_reads") or [],
        "failed_reads": state.get("failed_reads") or [],
        "finalizer_calls": state.get("finalizer_calls") or 0,
    }


def catalog_prompt_payload(catalog):
    """Projecao compacta e estavel para o Scout."""
    catalog = catalog or {}
    return json.dumps({
        "schema_version": catalog.get("schema_version"),
        "inventory_hash": catalog.get("inventory_hash"),
        "inventory_complete": catalog.get("inventory_complete"),
        "counts": catalog.get("counts") or {},
        "required_slots": catalog.get("required_slots") or [],
        "candidates": catalog.get("candidates") or [],
    }, ensure_ascii=False, separators=(",", ":"))
