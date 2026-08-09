"""Single-session LLM-first programming agent.

There is one reasoning loop. The LLM decides what must be established, whether to answer, use a
tool, ask a blocking question or propose a patch. The runtime only validates
and executes concrete actions.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.executar import (
    ErroLLM, PROMPT_AGENTE, PROMPT_CLAIM_VERIFIER,
    executar_agente as executar_agente_llm, executar_verificador_claims,
)
from llm.structured import StructuredResponseError, retry_instruction

from .session import AgentSession
from .observation import (
    semantic_signature as _observation_signature, lookup as _lookup_observation,
    record as _record_observation,
)
from .investigation import (
    apply_investigation_updates, open_target_ids, reopen_targets_from_review, target_evidence_ids,
    validate_workspace_scope,
)
from .execution_trace import build_execution_trace
from .security import _resolver_caminho_seguro
from .workspace_io import ErroLeituraProjeto, ler_faixa_projeto
from .token_budget import available_user_prompt_tokens, estimate_tokens
from .text_hash import hash_faixa, hash_texto
from .post_write import (
    expected_outputs_from_patches,
    run_compileall_for_changes,
    verify_expected_outputs,
)
from .tools import (
    executar_tool,
    gerar_catalogo_tools,
    gerar_taxonomia_tools,
    validar_chamada_tool,
)
from .transactions import dry_run_patch_set, apply_patch_set, rollback_patch_set
from .validation import validate_final
from .request_policy import request_contract
from .claim_review import (
    claim_config, claim_evidence_ledger, compact_evidence,
    claim_review_output_budget, claim_protocol_recovery_target, semantic_gap_protocol_recovery_target,
    finding_protocol_recovery_target, finding_recovery_prompt,
    review_followup_feedback, normalize_claim_review, problematic_claims, review_prompt,
    validate_file_evidence_freshness, verifier_answer_anchors,
)

READ_TOOLS = {"list_tree", "search_code", "find_symbol", "read_range", "read_file"}
OBSERVATION_TOOLS = {"project_stats", "count_tokens", "inspect_project"}
UTILITY_TOOLS = {"calculate", "agent_info"}
GIT_TOOLS = {"git_status", "git_diff"}
EXECUTION_TOOLS = {"run_tests"}
CACHEABLE_OBSERVATION_TOOLS = OBSERVATION_TOOLS | {"agent_info", "run_tests"}
TRACE_TOOLS = {"execution_trace"}
EVIDENCE_TOOLS = READ_TOOLS | OBSERVATION_TOOLS | GIT_TOOLS | EXECUTION_TOOLS | TRACE_TOOLS | {"calculate", "agent_info"}
MEMORY_TOOLS = {"memory_search", "memory_store"}
TERMINAL_TOOL_ERRORS = {"UNSAFE_PATH", "PATH_OUTSIDE_PROJECT", "PERMISSION_DENIED", "WORKSPACE_NOT_AVAILABLE"}
_OBVIOUS_CALCULATOR_REQUEST = re.compile(
    r"^\s*(?:(?:quanto\s+(?:é|e)|calcule|calcular|calculate|resultado\s+de)\s+)?"
    r"[0-9\s.,()+\-*/%^]+[?!.]?\s*$",
    re.I,
)
_OBVIOUS_AGENT_INFO_REQUEST = re.compile(
    r"^\s*(?:(?:quem|o\s+que)\s+(?:é|e)\s+voc[eê]|who\s+are\s+you|"
    r"(?:qual|quais)\s+(?:é|e|são|sao)?\s*(?:suas?\s+)?(?:ferramentas?|funções|funcoes|capacidades?)|"
    r"(?:que|quais)\s+ferramentas?\s+(?:voc[eê]\s+)?(?:tem|possui)|"
    r"do\s+que\s+voc[eê]\s+(?:é|e)\s+capaz|"
    r"what\s+(?:tools|capabilities)\s+do\s+you\s+have|"
    r"(?:qual\s+(?:é|e)\s+)?(?:seu\s+nome|your\s+name))"
    r"(?:\s+eyle)?[!?.\s]*$",
    re.I,
)
_FAST_CHAT_HINT = re.compile(
    r"^\s*(?:oi|olá|ola|hey|hello|hi|bom dia|boa tarde|boa noite|tudo bem\??|"
    r"como vai\??|valeu|obrigad[oa]|thanks|thank you)(?:\s+eyle)?[!?.\s]*$",
    re.I,
)

def _is_obvious_calculator_request(request: Any) -> bool:
    return bool(_OBVIOUS_CALCULATOR_REQUEST.fullmatch(str(request or "")))


def _is_obvious_agent_info_request(request: Any) -> bool:
    return bool(_OBVIOUS_AGENT_INFO_REQUEST.fullmatch(str(request or "")))


def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _trim_history(context: Any, token_budget: int, chars_per_token: int) -> Dict[str, Any]:
    """Build one bounded conversation background shared by every turn.

    The active task is always ``session.request``. Conversation background may
    carry ongoing user instructions or useful context, but previous tasks are
    not active goals. The runtime does not classify message semantics.
    """
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    normalized: List[Dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = item.get("content") if isinstance(item.get("content"), str) else item.get("text")
        if not isinstance(content, str) or not content.strip():
            continue
        normalized.append({"role": role, "content": content.strip()[:1800]})

    kept: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(normalized):
        cost = estimate_tokens(item, chars_per_token)
        if used + cost > max(0, token_budget):
            continue
        kept.append(item)
        used += cost
    kept.reverse()
    return {"messages": kept, "omitted_messages": max(0, len(normalized) - len(kept))}


def _project_descriptor(project: Dict[str, Any]) -> Dict[str, Any]:
    root = (project or {}).get("caminho_origem")
    return {
        "available": bool(root and os.path.isdir(root)),
        "name": os.path.basename(os.path.realpath(root)) if root else None,
        "discovery": (project or {}).get("discovery"),
    }


def _tests_enabled(config: Dict[str, Any]) -> bool:
    return bool((((config or {}).get("codar") or {}).get("testes") or {}).get("ativado", False))


def _context_view_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = (((config or {}).get("agent") or {}).get("context_view") or {})
    return {
        "max_relevant_sources": max(1, int(raw.get("max_relevant_sources", 4) or 4)),
        "max_relevant_source_chars": max(500, int(raw.get("max_relevant_source_chars", 3500) or 3500)),
        "max_search_source_chars": max(300, int(raw.get("max_search_source_chars", 600) or 600)),
        "max_symbol_preview_chars": max(500, int(raw.get("max_symbol_preview_chars", 2600) or 2600)),
    }



def _phase_for_call(
    session: AgentSession, config: Dict[str, Any], project: Dict[str, Any],
) -> str:
    descriptor = _project_descriptor(project)
    if not descriptor["available"]:
        return "chat"

    agent_cfg = (config or {}).get("agent") or {}
    write_required = str((session.workspace_scope or {}).get("mode") or "") == "write"
    max_investigation = max(1, int(agent_cfg.get("max_write_investigation_turns", 2) or 2))
    max_no_progress = max(1, int(agent_cfg.get("max_no_progress_turns", 2) or 2))
    max_turns = max(1, int(agent_cfg.get("max_llm_turns", 8) or 8))

    if write_required:
        if session.patch_failures and session.evidence:
            return "write_patch_retry"
        if (
            session.turn > max_investigation
            or (session.no_progress_turns >= max_no_progress and bool(session.evidence))
            or session.turn >= max_turns
        ):
            return "write_patch_only"
        if session.turn == 1:
            return "write_investigate"
        return "write_prepare"

    # Keep only a cheap fast path for obvious conversation and
    # deterministic utilities. In a real workspace, every other request gets
    # investigative capability and the LLM chooses the evidence it needs.
    # This prevents a lexical classifier from hiding Git/symbol/read tools just
    # because the user's wording was not anticipated.
    if (
        session.turn == 1
        and not session.evidence
        and (
            _FAST_CHAT_HINT.fullmatch(str(session.request or ""))
            or _is_obvious_calculator_request(session.request)
            or _is_obvious_agent_info_request(session.request)
        )
    ):
        return "chat"
    if session.claim_followup_pending:
        return "analysis_investigate"
    if not session.evidence:
        return "analysis_investigate"
    if session.no_progress_turns >= max_no_progress or session.turn >= max_turns:
        return "analysis_answer_only"
    return "analysis_complete_or_read"


def _phase_policy(phase: str) -> Dict[str, Any]:
    policies = {
        "chat": {"goal": "answer directly", "reads_allowed": False, "patch_required": False},
        "analysis_investigate": {"goal": "read the minimum real source needed", "reads_allowed": True, "patch_required": False},
        "analysis_complete_or_read": {"goal": "answer now unless one clearly missing source is essential", "reads_allowed": True, "patch_required": False},
        "analysis_answer_only": {"goal": "answer from current evidence; no more tools", "reads_allowed": False, "patch_required": False},
        "write_investigate": {"goal": "identify and batch-read every existing file needed for the edit", "reads_allowed": True, "patch_required": False},
        "write_prepare": {"goal": "prefer one transactional patch; read only a genuinely missing file", "reads_allowed": True, "patch_required": True},
        "write_patch_only": {"goal": "produce the transactional patch now; reads are closed", "reads_allowed": False, "patch_required": True},
        "write_patch_retry": {"goal": "correct the rejected patch from the returned error; do not restart investigation", "reads_allowed": False, "patch_required": True},
    }
    return dict(policies.get(phase, policies["chat"]))


def _allowed_tools(
    config: Dict[str, Any], project: Dict[str, Any], phase: str, request: Any = "",
) -> set[str]:
    root = (project or {}).get("caminho_origem")
    project_available = bool(root and os.path.isdir(root))

    # Keep greetings and ordinary chat tool-free. Utilities appear only when
    # the request gives a concrete reason, preserving the cheap chat path.
    if phase == "chat":
        names = set()
        text = str(request or "")
        if _is_obvious_calculator_request(text):
            names.add("calculate")
        if _is_obvious_agent_info_request(text):
            names.add("agent_info")
        return names
    if phase == "analysis_answer_only":
        return set()
    if phase in {"write_patch_only", "write_patch_retry"}:
        return set()
    if not project_available:
        text = str(request or "")
        names = {"calculate"} if _is_obvious_calculator_request(text) else set()
        if _is_obvious_agent_info_request(text):
            names.add("agent_info")
        names |= TRACE_TOOLS
        return names

    names = set(READ_TOOLS) | set(MEMORY_TOOLS) | set(UTILITY_TOOLS) | set(GIT_TOOLS) | set(TRACE_TOOLS)
    if phase.startswith("analysis"):
        # Analysis stays observational. Patch dry-run tools are not introduced
        # merely because evidence exists; they belong to explicit write phases.
        names |= OBSERVATION_TOOLS
    if _tests_enabled(config) and (phase.startswith("analysis") or phase == "write_investigate"):
        names.add("run_tests")
    return names


def _tool_catalog(
    config: Dict[str, Any], project: Dict[str, Any], phase: str, request: Any = "",
) -> Tuple[set[str], List[Dict[str, Any]]]:
    allowed = _allowed_tools(config, project, phase, request)
    catalog = gerar_catalogo_tools(
        config=config, allowed_names=allowed, compact=True,
    ) if allowed else []
    return allowed, catalog


def _compact_non_read_result(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    detail = result.get("detail")
    if isinstance(detail, dict):
        detail = {
            key: value for key, value in detail.items()
            if key not in {"rollback_snapshot", "prepared_patches", "applied_patches", "stdout", "stderr"}
        }
    elif isinstance(detail, str):
        detail = detail[:4000]
    return {
        "tool": tool,
        "status": result.get("status"),
        "ok": result.get("ok"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "error_code": result.get("error_code"),
        "detail": detail,
    }


def _observable_tool_arguments(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return bounded tool arguments safe for the user-visible execution history.

    The history is observability, not a replay surface: source/code bodies, memory
    values and hashes are deliberately excluded.
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    if tool in {"read_file", "read_range"}:
        result = {"path": arguments.get("path")}
        if tool == "read_range":
            result.update({
                "line_start": arguments.get("line_start"),
                "line_end": arguments.get("line_end"),
            })
        return {k: v for k, v in result.items() if v is not None}
    if tool == "list_tree":
        return {
            k: arguments.get(k) for k in ("limit", "depth", "filter")
            if arguments.get(k) is not None
        }
    if tool == "search_code":
        return {"query": str(arguments.get("query") or "")[:240]}
    if tool == "find_symbol":
        return {
            k: arguments.get(k) for k in ("symbol", "path")
            if arguments.get(k) is not None
        }
    if tool == "calculate":
        return {"expression": str(arguments.get("expression") or "")[:240]}
    if tool == "count_tokens":
        return {
            k: arguments.get(k) for k in ("path", "tokenizer")
            if arguments.get(k) is not None
        }
    if tool in {"project_stats", "inspect_project", "agent_info"}:
        return {}
    if tool == "run_tests":
        return {"scope": arguments.get("scope")} if arguments.get("scope") else {}
    if tool == "git_status":
        return {"max_entries": arguments.get("max_entries")} if arguments.get("max_entries") is not None else {}
    if tool == "git_diff":
        return {
            key: arguments.get(key) for key in ("path", "staged", "context_lines")
            if arguments.get(key) is not None
        }
    if tool.startswith("memory_"):
        # Never expose stored memory bodies in the UI history.
        return {
            key: str(arguments.get(key))[:160]
            for key in ("query", "key", "chave", "namespace")
            if arguments.get(key) is not None
        }
    return {
        str(key): (str(value)[:240] if not isinstance(value, (int, float, bool)) else value)
        for key, value in list(arguments.items())[:12]
        if key not in {"content", "conteudo", "codigo_novo", "new_code", "value", "valor", "file_hash_expected", "range_hash_expected", "file_hash_esperado", "range_hash_esperado"}
    }


def _observable_tool_result(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    public: Dict[str, Any] = {
        "status": result.get("status"),
        "ok": bool(result.get("ok")),
        "executed": bool(result.get("executed")),
        "changed": bool(result.get("changed")),
    }
    if result.get("error_code"):
        public["error_code"] = str(result.get("error_code"))[:120]
    detail = result.get("detail")
    if isinstance(detail, str):
        public["detail"] = detail[:500]
        return public
    if not isinstance(detail, dict):
        return public

    if tool in {"read_file", "read_range", "find_symbol"}:
        public.update({
            "file": detail.get("arquivo"),
            "lines": [detail.get("linha_inicio"), detail.get("linha_fim")],
            "total_lines": detail.get("total_linhas_arquivo"),
            "truncated": bool(detail.get("truncado")),
        })
    elif tool == "list_tree":
        public.update({
            "entries": len(detail.get("entradas") or []),
            "truncated": bool(detail.get("truncado")),
            "complete_scan": bool(detail.get("varredura_completa")),
        })
    elif tool == "search_code":
        public.update({
            "matches": detail.get("matches_returned", len(detail.get("resultados") or [])),
            "ranges": detail.get("ranges_returned", len(detail.get("resultados") or [])),
            "files": list(detail.get("arquivos_relevantes") or [])[:20],
            "truncated": bool(detail.get("truncated")),
            "coverage_complete": bool(detail.get("coverage_complete")),
        })
    elif tool == "calculate":
        for key in ("result", "resultado", "exact", "expression"):
            if key in detail:
                public[key] = detail.get(key)
    elif tool in {"project_stats", "count_tokens"}:
        for key in (
            "file_count", "files", "directories", "lines", "characters", "bytes",
            "estimated_tokens", "tokens", "exact", "method", "characters_per_token", "languages",
        ):
            if key in detail:
                public[key] = detail.get(key)
    elif tool == "inspect_project":
        for key in ("file_count", "directory_count", "languages", "scan_complete"):
            if key in detail:
                public[key] = detail.get(key)
        if isinstance(detail.get("entrypoint_signals"), list):
            public["entrypoint_signals"] = [dict(item) for item in detail.get("entrypoint_signals")[:20] if isinstance(item, dict)]
        tests = detail.get("test_signals") if isinstance(detail.get("test_signals"), dict) else {}
        if tests:
            public["test_signals"] = {
                "has_tests": bool(tests.get("has_tests")),
                "count": int(tests.get("count") or 0),
                "files": list(tests.get("files") or [])[:20],
            }
        ci = detail.get("ci_signals") if isinstance(detail.get("ci_signals"), dict) else {}
        if ci:
            public["ci_signals"] = {"has_ci": bool(ci.get("has_ci")), "files": list(ci.get("files") or [])[:20]}
        if isinstance(detail.get("framework_signals"), list):
            public["framework_signals"] = [dict(item) for item in detail.get("framework_signals")[:20] if isinstance(item, dict)]
        relations = detail.get("relation_signals") if isinstance(detail.get("relation_signals"), dict) else {}
        if relations:
            public["relation_signals"] = {
                "local_import_edge_count": int(relations.get("local_import_edge_count") or 0),
                "local_import_edges_truncated": bool(relations.get("local_import_edges_truncated")),
                "most_imported_files": [dict(item) for item in (relations.get("most_imported_files") or [])[:20] if isinstance(item, dict)],
                "route_file_count": len(relations.get("route_files") or []),
                "syntax_error_file_count": len(relations.get("syntax_error_files") or []),
            }
    elif tool == "agent_info":
        for key in ("name", "app_version", "revision", "write_enabled", "write_confirmation_required"):
            if key in detail:
                public[key] = detail.get(key)
        registered = detail.get("registered_tools") if isinstance(detail.get("registered_tools"), list) else detail.get("tools")
        available = detail.get("available_tools") if isinstance(detail.get("available_tools"), list) else []
        if isinstance(registered, list):
            public["registered_tools"] = [item.get("name") for item in registered[:40] if isinstance(item, dict)]
        if isinstance(available, list):
            public["available_tools"] = [item.get("name") for item in available[:40] if isinstance(item, dict)]
    elif tool == "run_tests":
        for key in ("command", "returncode", "scope", "backend", "tests_detected", "summary"):
            if key in detail:
                public[key] = detail.get(key)
    elif tool == "git_status":
        for key in ("branch", "clean", "changed_count", "returned_count", "truncated", "counts"):
            if key in detail:
                public[key] = detail.get(key)
        if isinstance(detail.get("entries"), list):
            public["files"] = [item.get("path") for item in detail.get("entries")[:40] if isinstance(item, dict)]
    elif tool == "git_diff":
        for key in ("staged", "path", "file_count", "added_lines", "removed_lines", "truncated", "diff_characters"):
            if key in detail:
                public[key] = detail.get(key)
        if isinstance(detail.get("files"), list):
            public["files"] = [item.get("path") for item in detail.get("files")[:40] if isinstance(item, dict)]
    elif tool == "execution_trace":
        summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
        public["job_id"] = summary.get("job_id")
        public["trace_status"] = summary.get("status")
        public["current_phase"] = summary.get("current_phase")
        for key in ("phases", "context", "llm_calls", "decisions", "tools"):
            if isinstance(detail.get(key), list):
                public[f"{key}_count"] = len(detail.get(key) or [])
        if isinstance(detail.get("tokens"), dict):
            public["tokens"] = detail.get("tokens")
    return {k: v for k, v in public.items() if v is not None}


def _record_tool_history(
    session: AgentSession, tool: str, arguments: Dict[str, Any], result: Dict[str, Any],
    *, semantic_signature: Optional[str] = None, status_override: Optional[str] = None,
) -> None:
    item = {
        "tool": tool,
        "turn": session.turn,
        "phase": session.phase,
        "status": status_override or result.get("status"),
        "error_code": result.get("error_code"),
        "semantic_signature": semantic_signature,
        "arguments": _observable_tool_arguments(tool, arguments),
        "result": _observable_tool_result(tool, result),
    }
    session.tool_history.append(item)
    del session.tool_history[:-50]


def _investigation_map(session: AgentSession, limit: int = 12) -> List[Dict[str, Any]]:
    """Return compact observable navigation state for the current task only."""
    entries: List[Dict[str, Any]] = []
    for item in session.tool_history:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if tool not in EVIDENCE_TOOLS or result.get("ok") is not True:
            continue
        entry = {
            "turn": item.get("turn"),
            "tool": tool,
            "arguments": dict(item.get("arguments") or {}),
            "result": dict(result),
        }
        if item.get("semantic_signature"):
            entry["semantic_signature"] = item.get("semantic_signature")
        entries.append(entry)
    return entries[-max(1, int(limit or 12)):]


def _register_evidence(session: AgentSession, tool: str, detail: Any) -> List[str]:
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("resultados") or [] if isinstance(item, dict)]
        if not candidates and detail.get("coverage_complete") is True:
            # Negative observations are citable facts about the search itself,
            # not semantic claims about dead code/legacy.
            content = json.dumps({
                "query": detail.get("query"),
                "matches_observed": detail.get("matches_observed"),
                "matches_returned": detail.get("matches_returned"),
                "ranges_observed": detail.get("ranges_observed"),
                "ranges_returned": detail.get("ranges_returned"),
                "coverage_complete": True,
                "truncated": bool(detail.get("truncated")),
                "backend": detail.get("backend"),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            source_hash = hash_texto(content)
            candidates = [{
                "arquivo": "<search-observation>",
                "linha_inicio": None,
                "linha_fim": None,
                "file_hash": source_hash,
                "content_hash": source_hash,
                "conteudo": content,
                "source_type": "search_observation",
                "query": detail.get("query"),
                "coverage_complete": True,
                "matches": 0,
            }]
    elif tool in {"read_file", "read_range", "find_symbol"} and isinstance(detail, dict):
        candidates = [detail]
    elif tool == "list_tree" and isinstance(detail, dict) and detail.get("inventory_hash"):
        inventory = json.dumps({
            "entradas": detail.get("entradas") or [],
            "truncado": bool(detail.get("truncado")),
            "varredura_completa": bool(detail.get("varredura_completa")),
            "filtro": detail.get("filtro"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        candidates = [{
            "arquivo": "<workspace-tree>",
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": detail.get("inventory_hash"),
            "content_hash": hash_texto(inventory),
            "conteudo": inventory,
            "source_type": "workspace_tree",
        }]
    elif tool in OBSERVATION_TOOLS and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = (
            detail.get("scan_hash") or detail.get("measurement_hash")
            or detail.get("inspection_hash") or hash_texto(content)
        )
        candidates = [{
            "arquivo": f"<tool:{tool}>",
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": source_hash,
            "content_hash": hash_texto(content),
            "conteudo": content,
            "source_type": tool,
        }]
    elif tool == "agent_info" and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        candidates = [{
            "arquivo": "<agent-runtime>",
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": source_hash,
            "content_hash": source_hash,
            "conteudo": content,
            "source_type": "agent_runtime",
        }]
    elif tool in {"calculate", "run_tests", "git_status", "git_diff", "execution_trace"} and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        source_names = {
            "calculate": "<calculator>",
            "run_tests": "<runtime-tests>",
            "git_status": "<git-status>",
            "git_diff": "<git-diff>",
            "execution_trace": "<execution-trace>",
        }
        candidates = [{
            "arquivo": source_names[tool],
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": source_hash,
            "content_hash": source_hash,
            "conteudo": content,
            "source_type": tool,
        }]
    else:
        candidates = []
    ids: List[str] = []
    for item in candidates:
        if not item.get("arquivo") or not item.get("file_hash"):
            continue
        existing = next((
            evidence_id for evidence_id, evidence in session.evidence.items()
            if evidence.get("arquivo") == item.get("arquivo")
            and evidence.get("linha_inicio") == item.get("linha_inicio")
            and evidence.get("linha_fim") == item.get("linha_fim")
            and evidence.get("file_hash") == item.get("file_hash")
            and evidence.get("content_hash") == item.get("content_hash")
        ), None)
        evidence_id = existing or f"ev-{len(session.evidence) + 1:04d}"
        clone = dict(item)
        clone["id"] = evidence_id
        session.evidence[evidence_id] = clone
        ids.append(evidence_id)
    return ids


def _seed_runtime_failure_evidence(session: AgentSession, conversation_context: Any) -> None:
    """Promote the latest real post-write failure into citable runtime evidence."""
    messages = list((conversation_context or {}).get("recent_messages") or []) if isinstance(conversation_context, dict) else []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        failure = message.get("write_failure")
        if not isinstance(failure, dict) or not failure:
            continue
        detail = str(failure.get("detail") or "").strip()
        if not detail:
            continue
        evidence_id = "ev-runtime-0001"
        item = {
            "id": evidence_id,
            "arquivo": "<runtime-validation>",
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": None,
            "content_hash": hash_texto(detail),
            "conteudo": detail,
            "source_type": "runtime_validation",
            "stage": failure.get("stage"),
            "error_code": failure.get("error_code"),
            "paths": list(failure.get("paths") or []),
            "rollback_confirmed": failure.get("rollback_confirmed"),
        }
        session.evidence[evidence_id] = item
        session.relevant_sources.append({
            "tool": "runtime_validation",
            "evidence_id": evidence_id,
            "arquivo": item["arquivo"],
            "linha_inicio": None,
            "linha_fim": None,
            "file_hash": None,
            "content_hash": item["content_hash"],
            "conteudo": detail,
            "source_type": item["source_type"],
            "stage": item["stage"],
            "error_code": item["error_code"],
            "paths": item["paths"],
            "rollback_confirmed": item["rollback_confirmed"],
        })
        return


def _bounded_source_text(text: Any, max_chars: int, *, source_span: Optional[Tuple[Any, Any]] = None) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    suffix = "\n...[source preview cropped; use read_range for more]"
    if source_span and source_span[0] is not None and source_span[1] is not None:
        suffix = f"\n...[source span {source_span[0]}-{source_span[1]} cropped; use read_range for more]"
    return value[:max_chars].rstrip() + suffix


def _llm_source_view(item: Dict[str, Any], config: Dict[str, Any], *, tool: str) -> Dict[str, Any]:
    """Return one compact source view; full bytes remain in session.evidence."""
    context_view = _context_view_config(config)
    max_chars = context_view["max_relevant_source_chars"]
    if tool == "search_code":
        max_chars = min(max_chars, max(300, int(context_view.get("max_search_source_chars", 600))))
    elif tool == "find_symbol":
        max_chars = min(max_chars, max(800, int(context_view.get("max_symbol_preview_chars", 2600))))
    keep = {
        key: item.get(key) for key in (
            "arquivo", "simbolo", "linha_inicio", "linha_fim", "total_linhas_arquivo",
            "file_hash", "content_hash", "match_lines", "truncado",
            "coverage_complete", "source_type", "stage", "error_code", "evidence_id",
        ) if item.get(key) is not None
    }
    numbered = item.get("trecho_numerado")
    if not isinstance(numbered, str) or not numbered:
        raw = item.get("conteudo")
        if isinstance(raw, str) and raw:
            numbered = raw
    if isinstance(numbered, str) and numbered:
        keep["trecho_numerado"] = _bounded_source_text(
            numbered, max_chars, source_span=(item.get("linha_inicio"), item.get("linha_fim")),
        )
        keep["source_preview_complete"] = len(numbered) <= max_chars
    return keep



_NUMBERED_SOURCE_LINE = re.compile(r"(?m)^\s*(\d+)\s+\|")


def _merge_source_range(
    target: Dict[str, List[Dict[str, Any]]],
    *,
    path: Any,
    start: int,
    end: int,
    file_hash: Any = None,
    total_lines: Any = None,
) -> None:
    """Merge one observed range into a deterministic coverage map."""
    normalized = _normalized_path(path)
    if not normalized or start <= 0 or end < start:
        return
    entry = {
        "start": int(start),
        "end": int(end),
        "file_hash": str(file_hash or ""),
        "total_lines": int(total_lines or 0),
    }
    existing = [dict(item) for item in target.get(normalized, []) if isinstance(item, dict)]
    existing.append(entry)
    existing.sort(key=lambda item: (str(item.get("file_hash") or ""), int(item.get("start") or 0), int(item.get("end") or 0)))

    merged: List[Dict[str, Any]] = []
    for item in existing:
        if not merged:
            merged.append(item)
            continue
        previous = merged[-1]
        same_hash = str(previous.get("file_hash") or "") == str(item.get("file_hash") or "")
        if same_hash and int(item.get("start") or 0) <= int(previous.get("end") or 0) + 1:
            previous["end"] = max(int(previous.get("end") or 0), int(item.get("end") or 0))
            previous["total_lines"] = max(int(previous.get("total_lines") or 0), int(item.get("total_lines") or 0))
        else:
            merged.append(item)
    target[normalized] = merged[-40:]


def _record_prompt_visible_ranges(session: AgentSession, payload: Any) -> None:
    """Capture CURRENT prompt coverage and separately retain historical telemetry.

    A source that appeared in an older prompt is not necessarily available to
    the stateless Main LLM now. Only ``visible_source_ranges`` may suppress a
    semantic reread. ``historically_seen_source_ranges`` is observability only.
    """
    current: Dict[str, List[Dict[str, Any]]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            numbered = value.get("trecho_numerado")
            path = value.get("arquivo")
            if isinstance(numbered, str) and numbered and path:
                line_numbers = [int(match) for match in _NUMBERED_SOURCE_LINE.findall(numbered)]
                if line_numbers:
                    kwargs = {
                        "path": path,
                        "start": min(line_numbers),
                        "end": max(line_numbers),
                        "file_hash": value.get("file_hash"),
                        "total_lines": value.get("total_linhas_arquivo"),
                    }
                    _merge_source_range(current, **kwargs)
                    _merge_source_range(session.historically_seen_source_ranges, **kwargs)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                if isinstance(nested, (dict, list)):
                    visit(nested)

    visit(payload)
    session.visible_source_ranges = current


def _model_tool_result(session: AgentSession, tool: str, result: Dict[str, Any], config: Optional[Dict[str, Any]] = None, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    evidence_worthy = bool(result.get("ok")) or (
        tool == "run_tests"
        and isinstance(result.get("detail"), dict)
        and (result.get("executed") is True or result.get("error_code") == "TEST_RUNNER_UNAVAILABLE")
    )
    evidence_ids = _register_evidence(session, tool, result.get("detail")) if evidence_worthy else []
    if tool == "find_symbol" and result.get("error_code") == "SYMBOL_NOT_FOUND" and result.get("executed") is True:
        payload = {
            "symbol": str((arguments or {}).get("symbol") or ""),
            "path": (arguments or {}).get("path"),
            "error_code": "SYMBOL_NOT_FOUND",
            "executed": True,
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        evidence_ids = _register_evidence(session, "agent_info", {
            "observation": payload, "source_type": "symbol_observation", "content_hash": source_hash
        })
        # Re-label synthetic runtime Evidence as a symbol observation.
        for evidence_id in evidence_ids:
            if evidence_id in session.evidence:
                session.evidence[evidence_id].update({
                    "arquivo": "<symbol-observation>", "source_type": "symbol_observation",
                    "conteudo": content, "file_hash": source_hash, "content_hash": source_hash,
                })
    if tool in EVIDENCE_TOOLS and isinstance(result.get("detail"), dict):
        detail = result.get("detail")
        if tool == "search_code":
            copied = {
                key: value for key, value in detail.items()
                if key not in {"resultados"}
            }
            copied_results = []
            for item, evidence_id in zip(detail.get("resultados") or [], evidence_ids):
                clone = dict(item)
                clone["evidence_id"] = evidence_id
                copied_results.append(_llm_source_view(clone, config or {}, tool=tool))
            copied["resultados"] = copied_results
            detail = copied
        elif evidence_ids:
            clone = dict(detail)
            clone["evidence_id"] = evidence_ids[0]
            if tool in {"read_file", "read_range", "find_symbol"}:
                detail = _llm_source_view(clone, config or {}, tool=tool)
            else:
                detail = clone
        return {
            "tool": tool,
            "status": result.get("status"),
            "ok": result.get("ok"),
            "executed": result.get("executed"),
            "changed": result.get("changed"),
            "error_code": result.get("error_code"),
            "detail": detail,
            "evidence_ids": evidence_ids,
        }
    compact = _compact_non_read_result(tool, result)
    if evidence_ids:
        compact["evidence_ids"] = evidence_ids
    return compact


def _remember_relevant_sources(
    session: AgentSession,
    tool: str,
    model_result: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    if tool not in EVIDENCE_TOOLS:
        return
    if model_result.get("ok") is not True and not (
        tool == "run_tests"
        and (model_result.get("executed") is True or model_result.get("error_code") == "TEST_RUNNER_UNAVAILABLE")
    ):
        return
    context_view = _context_view_config(config)
    detail = model_result.get("detail")
    candidates: List[Dict[str, Any]] = []
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("resultados") or [] if isinstance(item, dict)]
    elif isinstance(detail, dict):
        candidates = [detail]

    for item in candidates:
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            continue
        compact = {
            "tool": tool,
            "evidence_id": evidence_id,
            "arquivo": item.get("arquivo"),
            "linha_inicio": item.get("linha_inicio"),
            "linha_fim": item.get("linha_fim"),
            "file_hash": item.get("file_hash"),
            "content_hash": item.get("content_hash"),
        }
        source_view = _llm_source_view(item, config, tool=tool)
        if source_view.get("trecho_numerado"):
            compact["trecho_numerado"] = source_view["trecho_numerado"]
            compact["source_preview_complete"] = source_view.get("source_preview_complete")
        session.relevant_sources = [
            source for source in session.relevant_sources
            if source.get("evidence_id") != evidence_id
        ]
        session.relevant_sources.append(compact)
    del session.relevant_sources[:-context_view["max_relevant_sources"]]


def _retained_sources_for_prompt(session: AgentSession, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return retained source bodies, keeping semantic-followup Evidence pinned.

    ``relevant_sources`` is intentionally small and disposable. Evidence named
    by an insufficient Claim/gap or by a reopened target must remain available
    during the follow-up even when the ordinary retention window is cleared.
    """
    latest_ids: set[str] = set()
    for result in session.latest_tool_results:
        if not isinstance(result, dict):
            continue
        latest_ids.update(str(item) for item in result.get("evidence_ids") or [] if item)
        detail = result.get("detail")
        if not isinstance(detail, dict):
            continue
        if detail.get("evidence_id"):
            latest_ids.add(str(detail["evidence_id"]))
        for item in detail.get("resultados") or []:
            if isinstance(item, dict) and item.get("evidence_id"):
                latest_ids.add(str(item["evidence_id"]))

    by_id: Dict[str, Dict[str, Any]] = {}
    for source in session.relevant_sources:
        if not isinstance(source, dict):
            continue
        evidence_id = str(source.get("evidence_id") or "")
        if evidence_id and evidence_id not in latest_ids:
            by_id[evidence_id] = dict(source)

    for evidence_id in session.followup_pinned_evidence_ids:
        evidence_id = str(evidence_id or "")
        if not evidence_id or evidence_id in latest_ids:
            continue
        item = session.evidence.get(evidence_id)
        if not isinstance(item, dict) or item.get("stale"):
            continue
        clone = dict(item)
        clone["evidence_id"] = evidence_id
        source_view = _llm_source_view(clone, config, tool="read_range")
        if source_view.get("trecho_numerado"):
            by_id[evidence_id] = {
                "tool": "semantic_followup_pin",
                "evidence_id": evidence_id,
                "arquivo": source_view.get("arquivo"),
                "linha_inicio": source_view.get("linha_inicio"),
                "linha_fim": source_view.get("linha_fim"),
                "file_hash": source_view.get("file_hash"),
                "content_hash": source_view.get("content_hash"),
                "trecho_numerado": source_view.get("trecho_numerado"),
                "source_preview_complete": source_view.get("source_preview_complete"),
                "pinned": True,
            }
    return list(by_id.values())


def _pin_semantic_followup_evidence(
    session: AgentSession, review: Dict[str, Any], reopened_target_ids: List[str],
) -> None:
    """Pin only Evidence materially named by the semantic follow-up.

    The verifier decides semantic insufficiency and target reopening. Runtime
    merely preserves the referenced Evidence bodies so the stateless Main LLM
    cannot be asked to investigate while simultaneously losing the source that
    motivated the follow-up.
    """
    wanted: List[str] = []

    def add(evidence_id: Any) -> None:
        value = str(evidence_id or "").strip()
        if value and value in session.evidence and value not in wanted:
            wanted.append(value)

    reopened = {str(item or "").strip() for item in reopened_target_ids if str(item or "").strip()}
    for target in session.investigation:
        if not isinstance(target, dict) or str(target.get("id") or "") not in reopened:
            continue
        for evidence_id in target.get("evidence_ids") or []:
            add(evidence_id)

    for claim in (review or {}).get("claims") or []:
        if not isinstance(claim, dict) or str(claim.get("verdict") or "") not in {"insufficient", "contradicted"}:
            continue
        for evidence_id in claim.get("evidence_ids") or []:
            add(evidence_id)

    for gap in (review or {}).get("semantic_gaps") or []:
        if not isinstance(gap, dict):
            continue
        for evidence_id in gap.get("evidence_ids") or []:
            add(evidence_id)

    session.followup_pinned_evidence_ids = wanted


def _shrink_structured_once(value: Any) -> bool:
    """Shrink one large nested value while preserving deterministic summaries.

    Tool results are allowed to inspect a large project, but the LLM should not
    receive every row of that inspection. This reducer is intentionally generic:
    it understands strings, lists and nested mappings instead of hard-coding
    every current tool field name. One reduction is made per call so
    ``_crop_payload`` can stop as soon as the prompt fits.
    """
    if isinstance(value, dict):
        # Prefer the largest list anywhere in this mapping. This covers
        # list_tree entries, inspect_project relation/test signals and future
        # structured tools without adding one special case per schema.
        list_candidates = [
            (len(item), key, item)
            for key, item in value.items()
            if isinstance(item, list) and len(item) > 4
        ]
        if list_candidates:
            _, key, item = max(list_candidates, key=lambda candidate: candidate[0])
            keep = max(4, len(item) // 2)
            value[key] = item[:keep]
            value[f"{key}_context_original_count"] = max(
                int(value.get(f"{key}_context_original_count", 0) or 0), len(item),
            )
            value["context_truncated"] = True
            return True

        # Raw strings can still dominate a prompt (README/read_file/diff tails).
        crop_suffix = "\n...[context cropped]"
        string_candidates = []
        for key, item in value.items():
            if not isinstance(item, str):
                continue
            raw = item[:-len(crop_suffix)] if item.endswith(crop_suffix) else item
            if len(raw) > 1000:
                string_candidates.append((len(raw), key, raw))
        if string_candidates:
            _, key, raw = max(string_candidates, key=lambda candidate: candidate[0])
            keep = max(1000, len(raw) // 2)
            if keep >= len(raw):
                return False
            value[key] = raw[:keep] + crop_suffix
            value[f"{key}_context_original_chars"] = max(
                int(value.get(f"{key}_context_original_chars", 0) or 0), len(raw),
            )
            value["context_truncated"] = True
            return True

        # Then recurse into nested containers. Dicts such as
        # inspect_project.relation_signals may hide the large arrays one level
        # below the top-level detail object.
        nested_candidates = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                try:
                    size = len(json.dumps(item, ensure_ascii=False, default=str))
                except (TypeError, ValueError):
                    size = 0
                nested_candidates.append((size, key, item))
        for _, _, item in sorted(nested_candidates, reverse=True, key=lambda candidate: candidate[0]):
            if _shrink_structured_once(item):
                value["context_truncated"] = True
                return True
        return False

    if isinstance(value, list):
        if len(value) > 4:
            del value[max(4, len(value) // 2):]
            return True
        nested = []
        for item in value:
            if isinstance(item, (dict, list)):
                try:
                    size = len(json.dumps(item, ensure_ascii=False, default=str))
                except (TypeError, ValueError):
                    size = 0
                nested.append((size, item))
        for _, item in sorted(nested, reverse=True, key=lambda candidate: candidate[0]):
            if _shrink_structured_once(item):
                return True
    return False


def _minimal_tool_context(result: Dict[str, Any]) -> Dict[str, Any]:
    """Last-resort bounded representation of one tool result for the model.

    Scalars and evidence IDs are preserved. Structured detail is reduced to a
    small sample, while the full result remains in AgentSession/history.
    """
    compact = {
        key: result.get(key)
        for key in ("tool", "status", "ok", "executed", "changed", "error_code", "evidence_ids")
        if result.get(key) is not None
    }
    detail = copy.deepcopy(result.get("detail"))
    if isinstance(detail, dict):
        for _ in range(64):
            if len(json.dumps(detail, ensure_ascii=False, default=str)) <= 3000:
                break
            if not _shrink_structured_once(detail):
                break
        compact["detail"] = detail
    elif isinstance(detail, str):
        compact["detail"] = detail[:2000] + ("\n...[context cropped]" if len(detail) > 2000 else "")
    else:
        compact["detail"] = detail
    compact["context_compacted"] = True
    return compact


def _crop_payload(payload: Dict[str, Any], budget: int, chars_per_token: int) -> Dict[str, Any]:
    """Fit a prompt without destroying the full tool results kept in session.

    Context compaction remains generic for arbitrary nested tool output.
    The prompt receives a deep-copied, bounded view; session evidence and public
    history keep the original runtime data.
    """
    while estimate_tokens(payload, chars_per_token) > budget:
        results = payload.get("latest_tool_results") or []
        reduced = False
        for result in sorted(
            [item for item in results if isinstance(item, dict)],
            key=lambda item: len(json.dumps(item.get("detail"), ensure_ascii=False, default=str)),
            reverse=True,
        ):
            detail = result.get("detail")
            if isinstance(detail, (dict, list)) and _shrink_structured_once(detail):
                result["context_compacted"] = True
                reduced = True
                break
            if isinstance(detail, str):
                crop_suffix = "\n...[context cropped]"
                raw_detail = detail[:-len(crop_suffix)] if detail.endswith(crop_suffix) else detail
                if len(raw_detail) > 1000:
                    keep = max(1000, len(raw_detail) // 2)
                    if keep < len(raw_detail):
                        result["detail"] = raw_detail[:keep] + crop_suffix
                        result["context_compacted"] = True
                        reduced = True
                        break
        if reduced:
            continue

        relevant = payload.get("relevant_sources") or []
        for source in relevant:
            if isinstance(source, dict) and _shrink_structured_once(source):
                source["context_truncated"] = True
                reduced = True
                break
        if reduced:
            continue
        if len(relevant) > 1:
            payload["relevant_sources"] = relevant[1:]
            continue
        evidence_index = payload.get("evidence_index") or []
        if len(evidence_index) > 8:
            pinned = [item for item in evidence_index if isinstance(item, dict) and item.get("pinned") is True]
            pinned_ids = {str(item.get("id") or "") for item in pinned}
            recent = [item for item in evidence_index if str((item or {}).get("id") or "") not in pinned_ids]
            if len(pinned) >= 8:
                compacted = pinned
            else:
                compacted = pinned + recent[-max(0, 8 - len(pinned)):]
            if compacted != evidence_index:
                payload["evidence_index"] = compacted
                continue

        # Conversation background is stable across turns and lower authority than
        # the active request/current investigation. Drop oldest messages only
        # after bulky task-local material has already been compacted.
        background = payload.get("conversation_background") or []
        if len(background) > 1:
            payload["conversation_background"] = background[1:]
            continue

        # Last resort: keep the observable/evidence envelope but replace bulky
        # result details with bounded structured samples. This is preferable to
        # failing locally after the tools already did useful work.
        compacted_any = False
        for index, result in enumerate(list(results)):
            if not isinstance(result, dict):
                continue
            compact = _minimal_tool_context(result)
            if len(json.dumps(compact, ensure_ascii=False, default=str)) < len(json.dumps(result, ensure_ascii=False, default=str)):
                results[index] = compact
                compacted_any = True
        if compacted_any:
            continue
        break
    return payload


def _agent_config(config: Dict[str, Any], session: AgentSession, project: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a per-call completion ceiling from the current runtime phase.

    Ceilings are authorization limits, not quotas: only provider-reported
    completion usage is charged to the task-wide pool. The main agent never
    reserves Claim Review capacity speculatively because its next decision may
    still be a tool call, user question, or final. Downstream reserve is kept
    only when Claim Review has explicitly returned semantic debt and a later
    verifier pass is already mandatory before acceptance.
    """
    clone = dict(config)
    llm = dict(config.get("llm") or {})
    phase = _phase_for_call(session, config, project)
    decision_limit = max(1, int(llm.get("agent_decision_max_tokens", 1100) or 1100))
    analysis_limit = max(1, int(llm.get("agent_analysis_max_tokens", 1800) or 1800))
    patch_limit = max(1, int(llm.get("agent_patch_max_tokens", 3600) or 3600))

    if phase in {"write_prepare", "write_patch_only", "write_patch_retry"}:
        ceiling = patch_limit
    elif phase == "analysis_investigate":
        # Investigation normally emits a compact tool decision. A larger
        # answer ceiling is preserved for phases that are actually expected to
        # produce the grounded conclusion.
        ceiling = decision_limit
    elif phase.startswith("analysis"):
        ceiling = analysis_limit
    else:
        ceiling = decision_limit
    llm["agent_max_tokens"] = ceiling

    # Ordinary investigation does not reserve a verifier call speculatively.
    # Once Claim Review explicitly sends the Main LLM back for semantic
    # follow-up, however, one later Claim Review is a known mandatory stage
    # before success. Reserve exactly the verifier's configured call ceiling,
    # not a projection based on historical Claims/gaps: already-supported
    # material must not consume the future recovery budget again.
    if session.claim_followup_pending and claim_config(config)["mode"] != "off":
        claims_cfg = claim_config(config)
        llm["downstream_completion_reserve_tokens"] = int(claims_cfg["verifier"]["max_tokens"])
    else:
        llm.pop("downstream_completion_reserve_tokens", None)
    clone["llm"] = llm
    return clone


def _trace_value_metrics(value: Any, chars_per_token: int) -> Dict[str, Any]:
    try:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    metrics: Dict[str, Any] = {
        "characters": len(serialized),
        "estimated_tokens": estimate_tokens(serialized, chars_per_token),
    }
    if isinstance(value, (list, dict)):
        metrics["items"] = len(value)
    return metrics


def _trace_prompt_components(payload: Dict[str, Any], chars_per_token: int) -> Dict[str, Dict[str, Any]]:
    return {
        str(key): _trace_value_metrics(value, chars_per_token)
        for key, value in payload.items()
    }


def _current_trace_snapshot(session: AgentSession, config: Dict[str, Any]) -> Dict[str, Any]:
    runtime = config.get("_runtime_agent_budget") or {}
    details = {
        "status": "processing",
        "turns": session.turn,
        "tool_calls": session.tool_calls,
        "tool_budget": _tool_budget_state(session, config),
        "committed_progress_history": list(session.committed_progress_history[-50:]),
        "progress_credited_evidence_ids": list(session.progress_credited_evidence_ids),
        "tool_extension_history": list(session.tool_extension_history[-50:]),
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_replays": int(session.observation_replays or 0),
        "observation_ledger_size": len(session.observation_ledger or {}),
        "repeated_rejected_decisions": int(session.repeated_rejected_decisions or 0),
        "progress_history": list(session.progress_history[-50:]),
        "tool_history": list(session.tool_history[-50:]),
        "decision_history": list(session.decision_history[-50:]),
        "llm_usage": {key: value for key, value in runtime.items() if key in {
            "llm_calls", "llm_requests", "prompt_tokens_actual", "prompt_tokens_cached",
            "prompt_tokens_uncached", "prompt_tokens_effective", "completion_tokens_actual",
            "generated_tokens", "reasoning_tokens_actual", "total_tokens_effective",
            "completion_tokens_remaining", "completion_tokens_remaining_pre_call",
            "completion_tokens_requested_pre_call", "completion_tokens_pending_pre_call",
            "downstream_completion_reserve_tokens", "administrative_llm_calls",
            "administrative_prompt_tokens", "administrative_completion_tokens",
            "administrative_reasoning_tokens",
        }},
        "llm_responses": list(runtime.get("llm_responses") or []),
        "administrative_llm_history": list(runtime.get("administrative_llm_history") or []),
        "structured_capability": dict(runtime.get("structured_capability") or {}),
        "runtime_phase": session.phase,
        "prompt_snapshots": list(session.prompt_snapshots[-20:]),
        "phase_history": list(session.phase_history[-50:]),
        "parse_failures": session.parse_failures,
        "no_progress_turns": session.no_progress_turns,
        "phase_violations": session.phase_violations,
        "write_validation": dict(session.write_validation or {}),
        "claim_review": {
            "stage": session.claim_review.get("stage"),
            "mode": session.claim_review.get("mode"),
            "summary": dict(session.claim_review.get("summary") or {}),
        } if session.claim_review else {},
    }
    return build_execution_trace(
        details,
        job_id=runtime.get("source_job_id"),
        status="processing",
        limit=100,
    )


def _merged_runtime_feedback(transient: str, persistent: str) -> Any:
    """Preserve semantic follow-up while attaching transient deterministic notices.

    Semantic diagnosis is runtime-owned state until a later Claim Review replaces
    or resolves it. A no-progress/validation notice must not hide that diagnosis.
    """
    transient = str(transient or "").strip()
    persistent = str(persistent or "").strip()
    if not persistent:
        return transient or None
    if not transient or transient == persistent:
        return persistent
    try:
        base = json.loads(persistent)
    except Exception:
        base = {"semantic_followup": persistent}
    if not isinstance(base, dict):
        base = {"semantic_followup": base}
    try:
        notice: Any = json.loads(transient)
    except Exception:
        notice = transient
    base["runtime_notice"] = notice
    return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def _compile_prompt(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    conversation_context: Any,
    feedback: str,
) -> Tuple[str, set[str]]:
    call_config = config
    context_cfg = call_config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    history_budget = int((call_config.get("agent") or {}).get("chat_history_token_budget", 1200) or 1200)
    history_meta = {"messages": session.conversation_background, "omitted_messages": 0}
    if session.turn <= 1 and not session.conversation_background:
        history_meta = _trim_history(conversation_context, history_budget, chars_per_token)
        session.conversation_background = list(history_meta.get("messages") or [])
    runtime = call_config.get("_runtime_agent_budget")
    if isinstance(runtime, dict):
        runtime["history_messages_omitted"] = int(history_meta.get("omitted_messages", 0) or 0)
    phase = _phase_for_call(session, call_config, project)
    session.record_phase(phase, turn=session.turn, reason="phase_for_call")
    allowed, tools = _tool_catalog(call_config, project, phase, session.request)
    tool_budget = _tool_budget_state(session, call_config)
    payload = {
        "request": session.request,
        "turn": session.turn,
        "investigation": session.investigation,
        "project": _project_descriptor(project),
        "runtime_phase": phase,
        "action_policy": _phase_policy(phase),
        "conversation_background": session.conversation_background,
        "investigation_map": _investigation_map(session),
        "latest_tool_results": session.latest_tool_results,
        "relevant_sources": _retained_sources_for_prompt(session, call_config),
        "evidence_index": session.evidence_index(),
        "tool_authority": {
            "earned_extension": tool_budget["earned_extension"],
            "committed_progress_epoch": tool_budget["committed_progress_epoch"],
            "pending_progress_cycles": tool_budget["pending_progress_cycles"],
            "pending_extension_calls": tool_budget["pending_extension_calls"],
            "policy": (
                "Tool authority is runtime-managed. Every runtime-validated fresh committed-progress epoch can fund "
                "the configured +tool step exactly once when the physical fuse is reached; there is no cumulative "
                "earned-extension ceiling. Claim Review never grants authority. Do not recycle old Evidence."
            ),
        },
        "request_contract": request_contract(
            session.request, _project_descriptor(project)["available"],
            write_available=bool(((call_config or {}).get("codar") or {}).get("ativado", True)),
            claims_mode=claim_config(call_config)["mode"],
            workspace_scope=session.workspace_scope,
        ),
        "tool_taxonomy": gerar_taxonomia_tools(tools) if tools else {},
        "available_tools": tools,
        "runtime_feedback": _merged_runtime_feedback(feedback, session.claim_followup_feedback),
    }
    claim_config(call_config)  # validate the active Claims contract
    payload["request_contract"]["semantic_review_separate_from_request_contract"] = True
    payload["request_contract"]["claim_evidence_count_limit"] = None
    payload["request_contract"]["claim_evidence_selection"] = {
        "basis": "material_claims_not_repository_size",
        "guidance": "around 6 can be enough; 12 when materially necessary; 20+ only for genuinely broad material Claims",
        "numbers_are": "guidance_not_quotas_or_limits",
        "exclude": ["duplicate", "irrelevant", "merely_related", "redundant"],
        "requirement": "every selected Evidence ID directly supports at least one material Claim",
    }
    output_tokens = int((call_config.get("llm") or {}).get("agent_max_tokens", 1400) or 1400)
    window_prompt_budget = available_user_prompt_tokens(call_config, PROMPT_AGENTE, output_tokens=output_tokens)
    working_set_target = max(1, int(context_cfg.get("working_set_target_tokens", 12000) or 12000))
    system_tokens = estimate_tokens(PROMPT_AGENTE, chars_per_token)
    working_set_user_budget = max(0, working_set_target - system_tokens)
    prompt_budget = min(window_prompt_budget, working_set_user_budget)
    components_before = _trace_prompt_components(payload, chars_per_token)
    pre_crop = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    pre_crop_tokens = estimate_tokens(pre_crop, chars_per_token)
    payload = _crop_payload(copy.deepcopy(payload), prompt_budget, chars_per_token)
    _record_prompt_visible_ranges(session, {
        "conversation_background": payload.get("conversation_background") or [],
        "investigation_map": payload.get("investigation_map") or [],
        "latest_tool_results": payload.get("latest_tool_results") or [],
        "relevant_sources": payload.get("relevant_sources") or [],
    })
    components_after = _trace_prompt_components(payload, chars_per_token)
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    post_crop_tokens = estimate_tokens(prompt, chars_per_token)
    session.record_prompt(
        mode="agent", characters=len(prompt),
        estimated_tokens=post_crop_tokens, tool_count=len(tools),
        phase=phase, turn=session.turn,
        metadata={
            "prompt_budget_tokens": prompt_budget,
            "working_set_target_tokens": working_set_target,
            "working_set_user_budget_tokens": working_set_user_budget,
            "window_user_prompt_budget_tokens": window_prompt_budget,
            "output_tokens_reserved": output_tokens,
            "system_prompt_characters": len(PROMPT_AGENTE),
            "system_prompt_estimated_tokens": system_tokens,
            "pre_crop_characters": len(pre_crop),
            "pre_crop_estimated_tokens": pre_crop_tokens,
            "crop_applied": len(pre_crop) != len(prompt),
            "components_before": components_before,
            "components_after": components_after,
        },
    )
    return prompt, allowed


def _call_agent(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    conversation_context: Any,
    feedback: str = "",
) -> Tuple[Dict[str, Any], set[str]]:
    call_config = _agent_config(config, session, project)
    prompt, allowed = _compile_prompt(session, call_config, project, conversation_context, feedback)
    decision = executar_agente_llm(prompt, call_config)
    if not isinstance(decision, dict):
        raise ValueError("agent structured response must be an object")
    return decision, allowed


def _claim_llm_config(config: Dict[str, Any], mode: str) -> Dict[str, Any]:
    cfg = claim_config(config)
    clone = dict(config)
    llm = dict((config or {}).get("llm") or {})
    verifier = cfg["verifier"]
    llm["claim_verifier_max_tokens"] = verifier["max_tokens"]
    llm["claim_verifier_truncation_retry_multiplier"] = 1.5
    llm["temperature"] = verifier["temperature"]
    if mode == "verified":
        for key in ("base_url", "model", "openai_compatible"):
            llm[key] = verifier[key]
    clone["llm"] = llm
    return clone


def _record_aux_prompt(
    session: AgentSession, config: Dict[str, Any], *, mode: str, prompt: str,
    system_prompt: str, output_tokens: int, metadata: Optional[Dict[str, Any]] = None,
) -> None:
    chars_per_token = max(1, int(((config or {}).get("context_engine") or {}).get("chars_per_token_fallback", 3) or 3))
    prompt_meta = {
        "output_tokens_reserved": int(output_tokens),
        "system_prompt_characters": len(system_prompt),
        "system_prompt_estimated_tokens": estimate_tokens(system_prompt, chars_per_token),
        "auxiliary_llm_call": True,
    }
    if metadata:
        prompt_meta.update(metadata)
    session.record_prompt(
        mode=mode, characters=len(prompt), estimated_tokens=estimate_tokens(prompt, chars_per_token),
        tool_count=0, phase=mode, turn=session.turn, metadata=prompt_meta,
    )


def _remaining_completion_budget(config: Dict[str, Any]) -> Optional[int]:
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    maximum = int(runtime.get("max_completion_tokens", runtime.get("max_generated_tokens", 0)) or 0)
    used = int(runtime.get("generated_tokens", runtime.get("completion_tokens_actual", 0)) or 0)
    if maximum <= 0:
        maximum = int((((config or {}).get("agent") or {}).get("max_completion_tokens", 0)) or 0)
        used = 0
    if maximum <= 0:
        return None
    return max(0, maximum - used)


def _fit_claim_evidence_view(
    session: AgentSession, config: Dict[str, Any], answer: str, selected_ids: List[str],
    *, output_tokens: int, target_claims: Optional[List[Dict[str, Any]]] = None,
    target_semantic_gaps: Optional[List[Dict[str, Any]]] = None,
    answer_anchors: Optional[List[Dict[str, Any]]] = None,
    scope_only: bool = False,
) -> Tuple[bool, str, List[Dict[str, Any]], Dict[str, int]]:
    """Fit every selected Evidence ID inside the verifier working set.

    Evidence count is never a semantic limit. If the selected set is broad,
    excerpt width shrinks uniformly until the packet fits. The runtime never
    chooses which selected Evidence IDs to keep.
    """
    cfg = claim_config(config)
    verifier_config = _claim_llm_config(config, cfg["mode"])
    context_cfg = (config or {}).get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    working_set_target = max(1, int(context_cfg.get("working_set_target_tokens", 12000) or 12000))
    system_tokens = estimate_tokens(PROMPT_CLAIM_VERIFIER, chars_per_token)
    working_user_budget = max(0, working_set_target - system_tokens)
    window_user_budget = available_user_prompt_tokens(
        verifier_config, PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
    )
    prompt_budget = min(working_user_budget, window_user_budget)

    def build(cap: int) -> Tuple[List[Dict[str, Any]], int]:
        view = compact_evidence(
            session.evidence, selected_ids, max_chars_per_item=max(0, int(cap)),
        )
        prompt = review_prompt(
            answer, view, session.request, target_claims=target_claims,
            target_semantic_gaps=target_semantic_gaps, answer_anchors=answer_anchors,
            investigation=session.investigation, workspace_scope=session.workspace_scope, scope_only=scope_only,
        )
        return view, estimate_tokens(prompt, chars_per_token)

    maximum = int(cfg["evidence"]["max_chars_per_item"])
    full_view, full_tokens = build(maximum)
    if full_tokens <= prompt_budget:
        return True, "ok", full_view, {
            "prompt_budget_tokens": prompt_budget,
            "prompt_estimated_tokens": full_tokens,
            "evidence_excerpt_chars_per_item": maximum,
            "selected_evidence_count": len(selected_ids),
        }

    # Every selected Evidence must carry some source content, not only an ID.
    # This is a physical packet rule, not a semantic count limit.
    minimum_excerpt_chars = min(120, maximum)
    minimum_view, minimum_tokens = build(minimum_excerpt_chars)
    if minimum_tokens > prompt_budget:
        return False, f"CLAIM_REVIEW_WORKING_SET_EXCEEDED:{minimum_tokens}>{prompt_budget}", [], {
            "prompt_budget_tokens": prompt_budget,
            "prompt_estimated_tokens": minimum_tokens,
            "evidence_excerpt_chars_per_item": minimum_excerpt_chars,
            "selected_evidence_count": len(selected_ids),
        }

    best_view: Optional[List[Dict[str, Any]]] = minimum_view
    best_tokens = minimum_tokens
    best_cap = minimum_excerpt_chars
    low, high = minimum_excerpt_chars, maximum
    while low <= high:
        mid = (low + high) // 2
        view, tokens = build(mid)
        if tokens <= prompt_budget:
            best_view, best_tokens, best_cap = view, tokens, mid
            low = mid + 1
        else:
            high = mid - 1

    return True, "ok", best_view, {
        "prompt_budget_tokens": prompt_budget,
        "prompt_estimated_tokens": best_tokens,
        "evidence_excerpt_chars_per_item": best_cap,
        "selected_evidence_count": len(selected_ids),
    }


def _is_structured_response_error(error: Exception, profile: Optional[str] = None) -> bool:
    code = str(getattr(error, "error_code", "") or "")
    prefix = "STRUCTURED_RESPONSE_INVALID:"
    if not code.startswith(prefix):
        return False
    return profile is None or code.startswith(prefix + profile + ":")


def _structured_retry_prompt(prompt: str, error: Exception, profile: str) -> str:
    structured_error = getattr(error, "structured_error", None)
    if not isinstance(structured_error, StructuredResponseError):
        code = str(getattr(error, "error_code", "") or "STRUCTURED_RESPONSE_INVALID")
        structured_error = StructuredResponseError(code, str(error))
    instruction = retry_instruction(
        profile, structured_error, getattr(error, "structured_observed", None),
    )
    return prompt + "\n\n" + instruction


def _structured_retry_limit(config: Dict[str, Any]) -> int:
    return max(0, int(((config or {}).get("agent") or {}).get("structured_protocol_retries", 1) or 0))


def _run_claim_verification(
    session: AgentSession, config: Dict[str, Any], answer: str, evidence_ids: List[str],
    *, project_root: Any = None, target_claims: Optional[List[Dict[str, Any]]] = None,
    target_semantic_gaps: Optional[List[Dict[str, Any]]] = None,
    allow_protocol_recovery: bool = True, scope_only: bool = False,
) -> Tuple[bool, str, Dict[str, Any], List[Dict[str, Any]]]:
    cfg = claim_config(config)
    selected_ids = list(dict.fromkeys(str(item) for item in (evidence_ids or []) if str(item)))

    fresh, freshness_reason = validate_file_evidence_freshness(
        session.evidence, selected_ids, project_root,
    )
    if not fresh:
        return False, freshness_reason, {}, []

    verifier_config = _claim_llm_config(config, cfg["mode"])
    anchors_ok, anchors_reason, answer_anchors = verifier_answer_anchors(answer, target_claims)
    if not anchors_ok:
        return False, anchors_reason, {}, []
    remaining_completion = _remaining_completion_budget(config)
    output_tokens = claim_review_output_budget(
        answer,
        base_tokens=cfg["verifier"]["max_tokens"],
        available_tokens=remaining_completion,
        target_claims=target_claims,
        target_semantic_gaps=target_semantic_gaps,
        answer_anchor_count=len(answer_anchors),
    )
    # Truncation retry may expand only into the same job-level completion pool.
    # This is a physical ceiling, not a Claim-count quota.
    retry_ceiling = max(output_tokens, int(remaining_completion or output_tokens))
    verifier_config["llm"]["claim_verifier_truncation_retry_max_tokens"] = retry_ceiling
    fit_ok, fit_reason, view, fit_meta = _fit_claim_evidence_view(
        session, config, answer, selected_ids, output_tokens=output_tokens, target_claims=target_claims,
        target_semantic_gaps=target_semantic_gaps, answer_anchors=answer_anchors, scope_only=scope_only,
    )
    if not fit_ok:
        return False, fit_reason, {}, []
    visible_ids = [str(item.get("id")) for item in view if item.get("id")]
    if len(visible_ids) != len(selected_ids):
        visible_set = set(visible_ids)
        missing_view = [item for item in selected_ids if item not in visible_set]
        return False, "CLAIM_REVIEW_UNKNOWN_EVIDENCE:" + ",".join(missing_view), {}, []

    verifier_config["llm"].pop("downstream_completion_reserve_tokens", None)
    verifier_config["llm"]["claim_verifier_max_tokens"] = output_tokens
    prompt = review_prompt(
        answer, view, session.request, target_claims=target_claims,
        target_semantic_gaps=target_semantic_gaps, answer_anchors=answer_anchors,
        investigation=session.investigation, workspace_scope=session.workspace_scope, scope_only=scope_only,
    )
    _record_aux_prompt(
        session, verifier_config, mode=("workspace_scope_verification" if scope_only else ("semantic_gap_reverification" if target_semantic_gaps else ("claim_reverification" if target_claims else "claim_verification"))), prompt=prompt,
        system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens, metadata=fit_meta,
    )
    parsed = None
    verifier_prompt = prompt
    for protocol_attempt in range(_structured_retry_limit(config) + 1):
        try:
            parsed = executar_verificador_claims(verifier_prompt, verifier_config)
            break
        except ErroLLM as error:
            if not _is_structured_response_error(error, "claim_verifier") or protocol_attempt >= _structured_retry_limit(config):
                raise
            _record_decision(
                session, "claim_review_protocol", "retry", reason=error.error_code,
            )
            verifier_prompt = _structured_retry_prompt(prompt, error, "claim_verifier")
            _record_aux_prompt(
                session, verifier_config, mode="claim_verification_protocol_retry", prompt=verifier_prompt,
                system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens, metadata={"protocol_retry": protocol_attempt + 1},
            )

    fresh, freshness_reason = validate_file_evidence_freshness(
        session.evidence, selected_ids, project_root,
    )
    if not fresh:
        return False, freshness_reason, {}, view

    if not isinstance(parsed, dict):
        return False, "CLAIM_REVIEW_PROTOCOL_ERROR:STRUCTURED_OBJECT_REQUIRED", {}, view
    expected_ids = [str(item.get("claim_id")) for item in (target_claims or []) if item.get("claim_id")] or None
    ok, reason, review = normalize_claim_review(
        parsed, session.evidence, request=session.request, answer=answer, answer_anchors=answer_anchors,
        visible_evidence_ids=visible_ids, expected_claim_ids=expected_ids, investigation=session.investigation,
        enforce_finding_coverage=not bool(target_claims or target_semantic_gaps or scope_only),
    )
    if ok and scope_only:
        scope_gaps = [dict(item) for item in review.get("semantic_gaps") or [] if isinstance(item, dict)]
        if review.get("claims") or review.get("findings"):
            return False, "WORKSPACE_SCOPE_REVIEW_PROTOCOL_INVALID:CLAIMS_OR_FINDINGS", {}, view
        if len(scope_gaps) > 1 or any(
            item.get("type") != "scope_gap" or item.get("target_id") is not None or item.get("evidence_ids")
            for item in scope_gaps
        ):
            return False, "WORKSPACE_SCOPE_REVIEW_PROTOCOL_INVALID:GAP", {}, view

    recovered_indices = set()
    while not ok and allow_protocol_recovery and not scope_only and not target_claims:
        recoverable, _recover_reason, target, claim_index = claim_protocol_recovery_target(
            parsed, reason, answer_anchors,
        )
        if not recoverable or claim_index in recovered_indices:
            break
        recovered_indices.add(claim_index)
        _record_decision(
            session, "claim_protocol_recovery", "requested", reason=reason,
        )
        try:
            local_ok, local_reason, local_review, _local_view = _run_claim_verification(
                session, config, str(target.get("answer_quote") or ""), selected_ids,
                project_root=project_root, target_claims=[target], allow_protocol_recovery=False,
            )
        except ErroLLM:
            raise
        if not local_ok or len(local_review.get("claims") or []) != 1:
            _record_decision(
                session, "claim_protocol_recovery", "rejected", reason=local_reason,
            )
            return False, f"CLAIM_REVIEW_LOCAL_RECOVERY_FAILED:{reason}:{local_reason}", {}, view
        recovered = dict(local_review["claims"][0])
        parsed_claims = parsed.get("claims") if isinstance(parsed, dict) else None
        if not isinstance(parsed_claims, list) or claim_index < 1 or claim_index > len(parsed_claims):
            return False, "CLAIM_REVIEW_LOCAL_RECOVERY_TARGET_LOST", {}, view
        parsed_claims[claim_index - 1] = {
            "id": recovered.get("id"),
            "answer_ref": recovered.get("answer_ref"),
            "target_id": recovered.get("target_id"),
            "statement": recovered.get("statement"),
            "kind": recovered.get("kind"),
            "evidence_ids": list(recovered.get("evidence_ids") or []),
            "verdict": recovered.get("verdict"),
            "reason": recovered.get("reason", ""),
        }
        _record_decision(
            session, "claim_protocol_recovery", "resolved", reason=str(recovered.get("id") or ""),
        )
        ok, reason, review = normalize_claim_review(
            parsed, session.evidence, request=session.request, answer=answer, answer_anchors=answer_anchors,
            visible_evidence_ids=visible_ids, expected_claim_ids=expected_ids, investigation=session.investigation,
        )

    recovered_gap_ids = set()
    while not ok and allow_protocol_recovery and not scope_only and not target_claims and not target_semantic_gaps:
        recoverable, _recover_reason, target_gap, gap_index = semantic_gap_protocol_recovery_target(
            parsed, reason,
        )
        target_gap_id = str(target_gap.get("id") or "")
        if not recoverable or not target_gap_id or target_gap_id in recovered_gap_ids:
            break
        recovered_gap_ids.add(target_gap_id)
        _record_decision(
            session, "semantic_gap_protocol_recovery", "requested", reason=reason,
        )
        local_ok, local_reason, local_review, _local_view = _run_claim_verification(
            session, config, answer, selected_ids, project_root=project_root,
            target_semantic_gaps=[target_gap], allow_protocol_recovery=False,
        )
        if not local_ok:
            _record_decision(
                session, "semantic_gap_protocol_recovery", "rejected", reason=local_reason,
            )
            return False, f"CLAIM_REVIEW_LOCAL_GAP_RECOVERY_FAILED:{reason}:{local_reason}", {}, view
        local_gaps = [item for item in local_review.get("semantic_gaps") or [] if isinstance(item, dict)]
        if len(local_gaps) > 1:
            return False, "CLAIM_REVIEW_LOCAL_GAP_RECOVERY_MULTIPLE", {}, view
        parsed_gaps = parsed.get("semantic_gaps") if isinstance(parsed, dict) else None
        if not isinstance(parsed_gaps, list) or gap_index < 1 or gap_index > len(parsed_gaps):
            return False, "CLAIM_REVIEW_LOCAL_GAP_RECOVERY_TARGET_LOST", {}, view
        if not local_gaps:
            removed = parsed_gaps.pop(gap_index - 1)
            _record_decision(
                session, "semantic_gap_protocol_recovery", "removed", reason=str((removed or {}).get("id") or ""),
            )
        else:
            recovered_gap = dict(local_gaps[0])
            if str(recovered_gap.get("id") or "") != str(target_gap.get("id") or ""):
                return False, "CLAIM_REVIEW_LOCAL_GAP_RECOVERY_ID_CHANGED", {}, view
            parsed_gaps[gap_index - 1] = {
                "id": recovered_gap.get("id"),
                "type": recovered_gap.get("type"),
                "target_id": recovered_gap.get("target_id"),
                "evidence_ids": list(recovered_gap.get("evidence_ids") or []),
                "reason": recovered_gap.get("reason", ""),
            }
            _record_decision(
                session, "semantic_gap_protocol_recovery", "resolved", reason=str(recovered_gap.get("id") or ""),
            )
        ok, reason, review = normalize_claim_review(
            parsed, session.evidence, request=session.request, answer=answer, answer_anchors=answer_anchors,
            visible_evidence_ids=visible_ids, expected_claim_ids=expected_ids, investigation=session.investigation,
        )

    if not ok and allow_protocol_recovery and not target_claims and not target_semantic_gaps:
        recoverable, _recover_reason, finding_target = finding_protocol_recovery_target(parsed, reason)
        if recoverable:
            _record_decision(session, "finding_protocol_recovery", "requested", reason=reason)
            local_ok, local_reason, recovered_findings = _run_finding_recovery(
                session, config, parsed, finding_target,
            )
            if not local_ok:
                _record_decision(session, "finding_protocol_recovery", "rejected", reason=local_reason)
                return False, f"CLAIM_REVIEW_LOCAL_FINDING_RECOVERY_FAILED:{reason}:{local_reason}", {}, view
            parsed["findings"] = recovered_findings
            ok, reason, review = normalize_claim_review(
                parsed, session.evidence, request=session.request, answer=answer, answer_anchors=answer_anchors,
                visible_evidence_ids=visible_ids, expected_claim_ids=expected_ids, investigation=session.investigation,
            )
            if ok:
                _record_decision(
                    session, "finding_protocol_recovery", "resolved",
                    reason=str(finding_target.get("claim_id") or ""),
                )
            else:
                _record_decision(session, "finding_protocol_recovery", "rejected", reason=reason)

    if ok and target_semantic_gaps:
        if review.get("claims") or review.get("findings"):
            return False, "CLAIM_REVERIFY_GAP_SCOPE_CHANGED", {}, view
        local_gaps = [item for item in review.get("semantic_gaps") or [] if isinstance(item, dict)]
        if len(local_gaps) > 1:
            return False, "CLAIM_REVERIFY_GAP_MULTIPLE", {}, view
        if local_gaps and str(local_gaps[0].get("id") or "") != str(target_semantic_gaps[0].get("id") or ""):
            return False, "CLAIM_REVERIFY_GAP_ID_CHANGED", {}, view

    if ok and target_claims:
        targets = {str(item.get("claim_id")): item for item in target_claims if item.get("claim_id")}
        for claim in review.get("claims") or []:
            target = targets.get(str(claim.get("id"))) or {}
            if claim.get("answer_ref") != target.get("answer_ref"):
                return False, f"CLAIM_REVERIFY_REF_CHANGED:{claim.get('id')}", {}, view
            if claim.get("target_id") != target.get("target_id"):
                return False, f"CLAIM_REVERIFY_TARGET_CHANGED:{claim.get('id')}", {}, view
            if claim.get("answer_quote") != target.get("answer_quote"):
                return False, f"CLAIM_REVERIFY_QUOTE_CHANGED:{claim.get('id')}", {}, view
            expected_kind = str(target.get("kind") or "")
            if expected_kind and claim.get("kind") != expected_kind:
                return False, f"CLAIM_REVERIFY_KIND_CHANGED:{claim.get('id')}", {}, view
    workspace_grounded = str((session.workspace_scope or {}).get("mode") or "") in {"read", "write"}
    if ok and not target_semantic_gaps and (workspace_grounded or bool(selected_ids)) and not review.get("claims"):
        return False, "CLAIM_REVIEW_EMPTY", {}, view
    if ok:
        review["mode"] = cfg["mode"]
        review["evidence_ids"] = visible_ids
    return ok, reason, review, view


def _run_finding_recovery(
    session: AgentSession, config: Dict[str, Any], raw_review: Dict[str, Any], target: Dict[str, Any],
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Regenerate only Findings while preserving Claims and Semantic Gaps."""
    cfg = claim_config(config)
    verifier_config = _claim_llm_config(config, cfg["mode"])
    remaining = _remaining_completion_budget(config)
    output_tokens = min(max(320, int(cfg["verifier"]["max_tokens"])), max(1, int(remaining or cfg["verifier"]["max_tokens"])))
    verifier_config["llm"]["claim_verifier_max_tokens"] = output_tokens
    prompt = finding_recovery_prompt(session.request, raw_review, target)
    _record_aux_prompt(
        session, verifier_config, mode="finding_reverification", prompt=prompt,
        system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
    )
    parsed = None
    call_prompt = prompt
    for protocol_attempt in range(_structured_retry_limit(config) + 1):
        try:
            parsed = executar_verificador_claims(call_prompt, verifier_config)
            break
        except ErroLLM as error:
            if not _is_structured_response_error(error, "claim_verifier") or protocol_attempt >= _structured_retry_limit(config):
                raise
            _record_decision(session, "finding_recovery_protocol", "retry", reason=error.error_code)
            call_prompt = _structured_retry_prompt(prompt, error, "claim_verifier")
            _record_aux_prompt(
                session, verifier_config, mode="finding_reverification_protocol_retry", prompt=call_prompt,
                system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
                metadata={"protocol_retry": protocol_attempt + 1},
            )
    if not isinstance(parsed, dict):
        return False, "CLAIM_REVIEW_FINDING_RECOVERY_OBJECT_REQUIRED", []
    if parsed.get("claims") != [] or parsed.get("semantic_gaps") != []:
        return False, "CLAIM_REVIEW_FINDING_RECOVERY_SCOPE_CHANGED", []
    findings = parsed.get("findings")
    if not isinstance(findings, list):
        return False, "CLAIM_REVIEW_FINDING_RECOVERY_FINDINGS_REQUIRED", []
    return True, "ok", [dict(item) for item in findings if isinstance(item, dict)]


def _append_claim_review(session: AgentSession, review: Dict[str, Any], *, stage: str) -> None:
    snapshot = {
        "stage": stage,
        "turn": session.turn,
        "mode": review.get("mode"),
        "summary": dict(review.get("summary") or {}),
        "claims": [dict(item) for item in review.get("claims") or [] if isinstance(item, dict)],
        "findings": [dict(item) for item in review.get("findings") or [] if isinstance(item, dict)],
        "semantic_gaps": [dict(item) for item in review.get("semantic_gaps") or [] if isinstance(item, dict)],
        "finding_signatures": [
            str(item.get("signature")) for item in review.get("semantic_gaps") or []
            if isinstance(item, dict) and item.get("signature")
        ],
    }
    session.claim_review = snapshot
    session.claim_review_history.append(snapshot)
    del session.claim_review_history[:-10]


def _tool_budget_state(session: AgentSession, config: Dict[str, Any]) -> Dict[str, int]:
    cfg = (config or {}).get("agent") or {}
    base = max(1, int(cfg.get("max_tool_calls", 12) or 12))
    earned = max(0, int(session.earned_tool_extension or 0))
    committed_epoch = max(0, int(session.committed_progress_epoch or 0))
    last_extension_epoch = max(0, int(session.last_extension_progress_epoch or 0))
    per_cycle = max(1, int(cfg.get("committed_progress_extension_calls", 4) or 4))
    pending_cycles = max(0, committed_epoch - last_extension_epoch)
    return {
        "base": base,
        "earned_extension": earned,
        "effective_limit": base + earned,
        "extension_cycles": max(0, int(session.tool_extension_cycles or 0)),
        "committed_progress_epoch": committed_epoch,
        "last_extension_progress_epoch": last_extension_epoch,
        "pending_progress_cycles": pending_cycles,
        "pending_extension_calls": pending_cycles * per_cycle,
    }


def _record_committed_progress(
    session: AgentSession, progress: List[Dict[str, Any]], *, accepted_updates: List[Dict[str, Any]] | None = None,
) -> bool:
    """Deposit one objective contract-progress cycle into the runtime ledger.

    The runtime never judges whether Evidence semantically proves a goal. A
    deposit means only that the canonical Investigation Contract accepted a
    structurally valid change backed by globally fresh runtime Evidence. Each
    Evidence ID may finance physical authority at most once for the whole
    session. Multiple targets changed in the same Main LLM decision still mint
    only one progress epoch, preventing target fragmentation from manufacturing
    authority.
    """
    material = [dict(item) for item in progress or [] if isinstance(item, dict)]
    if not material:
        return False

    # Tool authority is funded only by globally fresh Evidence. A previously
    # credited Evidence ID may still be attached to another Investigation target
    # for legitimate semantics, but it can never mint physical authority again.
    # This durable monotonic set prevents target cloning/reopen/edit cycles from
    # recycling old observations into repeated +tool credit.
    already_credited = set(str(item) for item in session.progress_credited_evidence_ids if str(item))
    target_ids = []
    evidence_ids = []
    for item in material:
        fresh_for_item = []
        for evidence_id in item.get("added_evidence_ids") or []:
            evidence_id = str(evidence_id or "").strip()
            evidence_item = session.evidence.get(evidence_id) if evidence_id else None
            if (
                evidence_id
                and isinstance(evidence_item, dict)
                and not evidence_item.get("stale")
                and evidence_id not in already_credited
                and evidence_id not in evidence_ids
            ):
                evidence_ids.append(evidence_id)
                fresh_for_item.append(evidence_id)
        if fresh_for_item:
            target_id = str(item.get("target_id") or "").strip()
            if target_id and target_id not in target_ids:
                target_ids.append(target_id)
    if not evidence_ids:
        return False

    session.progress_credited_evidence_ids.extend(
        evidence_id for evidence_id in evidence_ids if evidence_id not in already_credited
    )
    session.committed_progress_epoch += 1
    snapshot = {
        "turn": session.turn,
        "epoch": session.committed_progress_epoch,
        "target_ids": target_ids,
        "added_evidence_ids": evidence_ids,
        "accepted_updates": [
            str(item.get("id") or "") for item in accepted_updates or []
            if isinstance(item, dict) and item.get("changed") is True and str(item.get("id") or "")
        ],
    }
    session.committed_progress_history.append(snapshot)
    del session.committed_progress_history[:-50]
    _record_decision(
        session, "committed_progress", "deposited",
        reason=f"epoch={session.committed_progress_epoch};targets=" + ",".join(target_ids),
    )
    return True


def _grant_committed_progress_extension(session: AgentSession, config: Dict[str, Any]) -> int:
    """Convert every unspent objective progress epoch into physical tool authority.

    This is a physical runtime rule, not a semantic reward. Claim Review is not
    consulted. Every runtime-validated committed-progress epoch can fund exactly
    ``committed_progress_extension_calls`` additional physical calls once. There
    is no artificial cumulative +tool ceiling; replay protection comes from the
    immutable progress epoch and globally credit-once Evidence ledger.
    """
    if not open_target_ids(session.investigation):
        return 0

    cfg = (config or {}).get("agent") or {}
    per_cycle = max(1, int(cfg.get("committed_progress_extension_calls", 4) or 4))
    first_epoch = max(0, int(session.last_extension_progress_epoch or 0)) + 1
    last_epoch = max(0, int(session.committed_progress_epoch or 0))
    if first_epoch > last_epoch:
        return 0

    base = max(1, int(cfg.get("max_tool_calls", 12) or 12))
    total_granted = 0
    for progress_epoch in range(first_epoch, last_epoch + 1):
        session.earned_tool_extension = max(0, int(session.earned_tool_extension or 0)) + per_cycle
        session.tool_extension_cycles += 1
        session.last_extension_progress_epoch = progress_epoch
        total_granted += per_cycle
        snapshot = {
            "turn": session.turn,
            "granted": per_cycle,
            "progress_epoch": progress_epoch,
            "earned_total": session.earned_tool_extension,
            "effective_limit": base + session.earned_tool_extension,
        }
        session.tool_extension_history.append(snapshot)
        del session.tool_extension_history[:-50]
        _record_decision(
            session, "tool_authority", "extension_granted",
            reason=(
                f"+{per_cycle};progress_epoch={progress_epoch};"
                f"earned={session.earned_tool_extension};effective={snapshot['effective_limit']}"
            ),
        )
    return total_granted


def _review_followup_payload(review: Dict[str, Any]) -> Dict[str, Any]:
    """Return a canonical, body-free fingerprint payload for Decision Ledger.

    This does not reinterpret reviewer semantics. It preserves only reviewer
    coordinates so an identical semantic debt against the same canonical state
    can be detected without another Agent<->Claim token loop.
    """
    claims = []
    for item in (review or {}).get("claims") or []:
        if not isinstance(item, dict) or item.get("verdict") not in {"contradicted", "insufficient"}:
            continue
        claims.append({
            "answer_ref": item.get("answer_ref"),
            "target_id": item.get("target_id"),
            "statement": str(item.get("statement") or ""),
            "verdict": item.get("verdict"),
            "evidence_ids": sorted(str(x) for x in item.get("evidence_ids") or [] if str(x)),
            "reason": str(item.get("reason") or ""),
        })
    gaps = []
    for item in (review or {}).get("semantic_gaps") or []:
        if not isinstance(item, dict):
            continue
        gaps.append({
            "type": item.get("type"),
            "target_id": item.get("target_id"),
            "evidence_ids": sorted(str(x) for x in item.get("evidence_ids") or [] if str(x)),
            "reason": str(item.get("reason") or ""),
        })
    claims.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    gaps.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    return {"claims": claims, "semantic_gaps": gaps}


def _extend_claim_rework_lane(session: AgentSession, config: Dict[str, Any], current_limit: int) -> int:
    """Expose unused physical LLM-call capacity only after Claim found debt.

    ``max_llm_turns`` remains the normal investigation limit. Claim follow-up is
    not speculative extra exploration: it is directed rework after the second
    brain rejected a provisional final. We therefore allow only the remaining
    task-wide LLM-call capacity, while reserving one future call for Claim Review.
    No new independent budget or semantic policy is introduced.
    """
    runtime = (config or {}).get("_runtime_agent_budget") or {}
    maximum = int(runtime.get("max_llm_calls", 0) or 0)
    if maximum <= 0:
        maximum = int((((config or {}).get("agent") or {}).get("max_llm_calls", 0)) or 0)
    used = int(runtime.get("llm_calls", 0) or 0)
    remaining = max(0, maximum - used) if maximum > 0 else 0
    # One verifier pass is mandatory before a reworked final can be accepted.
    available_agent_calls = max(0, remaining - 1)
    desired = int(session.turn or 0) + available_agent_calls
    if desired > int(current_limit or 0):
        _record_decision(
            session, "claim_rework_lane", "opened",
            reason=f"normal_limit={current_limit};remaining_llm_calls={remaining};rework_limit={desired}",
        )
        return desired
    return int(current_limit or 0)


def _details(
    session: AgentSession,
    status: str,
    config: Dict[str, Any],
    limitations: Optional[List[str]] = None,
    failure_code: Optional[str] = None,
    write_failure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime = config.get("_runtime_agent_budget") or {}
    usage_keys = (
        "llm_calls", "llm_requests", "prompt_tokens_reserved",
        "prompt_tokens_estimated_raw", "prompt_tokens_actual", "prompt_tokens_cached",
        "prompt_tokens_uncached", "prompt_tokens_effective",
        "completion_tokens_actual", "generated_tokens", "reasoning_tokens_actual",
        "total_tokens_effective", "history_messages_omitted",
        "administrative_llm_calls", "administrative_prompt_tokens",
        "administrative_completion_tokens", "administrative_reasoning_tokens",
    )
    return {
        "status": status,
        "workspace_scope": dict(session.workspace_scope or {}),
        "investigation": session.investigation,
        "turns": session.turn,
        "tool_calls": session.tool_calls,
        "tool_budget": _tool_budget_state(session, config),
        "committed_progress_history": list(session.committed_progress_history[-50:]),
        "progress_credited_evidence_ids": list(session.progress_credited_evidence_ids),
        "tool_extension_history": list(session.tool_extension_history[-50:]),
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_replays": int(session.observation_replays or 0),
        "observation_ledger_size": len(session.observation_ledger or {}),
        "repeated_rejected_decisions": int(session.repeated_rejected_decisions or 0),
        "progress_history": list(session.progress_history[-50:]),
        "tools_used": [
            item.get("tool") for item in session.tool_history
            if (item.get("result") or {}).get("executed") is True
            or ("result" not in item and item.get("status") == "success")
        ],
        "tool_history": list(session.tool_history[-50:]),
        "decision_history": list(session.decision_history[-50:]),
        "evidence": session.evidence_index(),
        "claim_evidence": claim_evidence_ledger(session.claim_review, session.evidence) if session.claim_review else [],
        "claim_review": {
            "stage": session.claim_review.get("stage"),
            "mode": session.claim_review.get("mode"),
            "summary": dict(session.claim_review.get("summary") or {}),
            "findings": [dict(item) for item in session.claim_review.get("findings") or [] if isinstance(item, dict)],
            "semantic_gaps": [dict(item) for item in session.claim_review.get("semantic_gaps") or [] if isinstance(item, dict)],
            "finding_signatures": list(session.claim_review.get("finding_signatures") or []),
        } if session.claim_review else {},
        "limitations": list(limitations or []),
        "failure_code": failure_code,
        "write_failure": dict(write_failure or {}) if write_failure else None,
        "llm_usage": {key: runtime.get(key, 0) for key in usage_keys},
        "llm_responses": list(runtime.get("llm_responses") or []),
        "administrative_llm_history": list(runtime.get("administrative_llm_history") or []),
        "structured_capability": dict(runtime.get("structured_capability") or {}),
        "parse_failures": session.parse_failures,
        "runtime_phase": session.phase,
        "no_progress_turns": session.no_progress_turns,
        "phase_violations": session.phase_violations,
        "prompt_snapshots": session.prompt_snapshots,
        "phase_history": list(session.phase_history[-50:]),
        "write_validation": dict(session.write_validation or {}),
    }


def _sanitize_prepared_patches(prepared: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for patch in prepared:
        operation = patch.get("operation")
        item = {"operation": operation, "path": patch.get("path")}
        if operation in {"create", "replace"}:
            item["content"] = patch.get("result_content", patch.get("content", ""))
            if operation == "replace":
                item["file_hash_expected"] = patch.get("file_hash_expected")
        elif operation == "delete":
            item["file_hash_expected"] = patch.get("file_hash_expected")
        else:
            item.update({
                "line_start": patch.get("line_start"),
                "line_end": patch.get("line_end"),
                "new_code": patch.get("new_code", ""),
                "file_hash_expected": patch.get("file_hash_expected"),
                "range_hash_expected": patch.get("range_hash_expected"),
            })
        result.append(item)
    return result


def _transaction_result(raw: Dict[str, Any], *, changed: bool = False) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    ok = raw.get("ok") is True
    detail = {
        key: raw.get(key) for key in ("message", "prepared_patches", "applied_patches", "files")
        if raw.get(key) is not None
    }
    return {
        "status": "success" if ok else "failed",
        "ok": ok,
        "executed": True,
        "changed": bool(changed and ok),
        "error_code": None if ok else str(raw.get("error_code") or "PATCH_TRANSACTION_FAILED"),
        "detail": detail if ok else str(raw.get("message") or "transaction failed"),
    }


def _transaction_rollback_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    ok = raw.get("ok") is True
    return {
        "status": "success" if ok else "failed",
        "ok": ok,
        "executed": True,
        "changed": ok,
        "error_code": None if ok else "ROLLBACK_FAILED",
        "detail": {
            "restored": list(raw.get("restored") or []),
            "failures": list(raw.get("failures") or []),
        },
    }


def _pending_patch_set(session: AgentSession, detail: Dict[str, Any]):
    patches = _sanitize_prepared_patches(detail.get("prepared_patches") or [])
    files = [str(patch.get("path") or "") for patch in patches]
    text = (
        f"Proposta transacional pronta para confirmação: {len(patches)} arquivo(s): "
        f"{', '.join(files)}. Dry-run aprovado para o conjunto completo. "
        "A aplicação exige confirmação do usuário."
    )
    state = session.to_dict()
    state["relevant_sources"] = []
    pending = {
        "continuation_kind": "write_confirmation",
        "pergunta_ao_usuario": text,
        "estado": state,
        "write_transaction": {"patches": patches},
    }
    return text, pending


def _run_tests_after_write(config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    if not _tests_enabled(config):
        return {
            "status": "skipped", "ok": True, "executed": False,
            "error_code": "TESTS_DISABLED", "detail": "Execução de testes desativada explicitamente.",
        }
    return executar_tool("run_tests", {}, context)


def _compile_after_write(config: Dict[str, Any], project: Dict[str, Any], paths: List[str]) -> Dict[str, Any]:
    timeout = int(((((config or {}).get("codar") or {}).get("testes") or {}).get("timeout_segundos", 60)))
    return run_compileall_for_changes(project.get("caminho_origem"), paths, timeout_seconds=timeout)


def _rollback_failure_text(prefix: str, rollback: Dict[str, Any], restored_text: str) -> Tuple[str, str]:
    if rollback.get("ok"):
        return f"{prefix} {restored_text}", "ROLLED_BACK"
    return f"{prefix} O rollback não pôde ser confirmado.", "ROLLBACK_FAILED"


def _diagnostic_text(result: Dict[str, Any], max_chars: int = 4000) -> str:
    """Return a readable bounded diagnostic without hiding the useful tail."""
    detail = (result or {}).get("detail")
    if isinstance(detail, (dict, list)):
        text = json.dumps(detail, ensure_ascii=False, indent=2, default=str)
    else:
        text = str(detail or "").strip()
    if not text:
        return "Nenhum detalhe técnico foi retornado pela etapa de validação."
    max_chars = max(800, int(max_chars))
    if len(text) <= max_chars:
        return text
    head = max_chars // 3
    tail = max_chars - head
    return f"{text[:head]}\n... [diagnóstico truncado] ...\n{text[-tail:]}"


def _write_failure_response(
    prefix: str,
    stage: str,
    result: Dict[str, Any],
    rollback: Dict[str, Any],
    restored_text: str,
    paths: List[str],
) -> Tuple[str, str, Dict[str, Any]]:
    """Build the user-visible and structured report for a failed confirmed write."""
    base, suffix = _rollback_failure_text(prefix, rollback, restored_text)
    diagnostic = _diagnostic_text(result)
    error_code = str((result or {}).get("error_code") or f"{stage.upper()}_FAILED")
    normalized_paths = [str(path) for path in paths or [] if str(path)]
    report = {
        "stage": stage,
        "error_code": error_code,
        "executed": bool((result or {}).get("executed")),
        "detail": diagnostic,
        "rollback_confirmed": bool((rollback or {}).get("ok")),
        "rollback_error_code": (rollback or {}).get("error_code"),
        "paths": normalized_paths,
    }
    text = (
        f"{base}\n\nErro real da tentativa:\n"
        f"- etapa: {stage};\n"
        f"- código: {error_code};\n"
        f"- arquivos envolvidos: {', '.join(normalized_paths) if normalized_paths else 'não informado'}.\n\n"
        f"Saída da validação:\n{diagnostic}"
    )
    return text, suffix, report


def _test_verification_line(tests: Dict[str, Any]) -> Tuple[str, List[str], bool]:
    if tests.get("executed") and tests.get("ok") is True:
        return "testes executados com sucesso", [], True
    detail = str(tests.get("detail") or "Testes não executados.")
    if tests.get("error_code") == "TESTS_NOT_FOUND":
        return "nenhuma suíte de testes detectada", [detail], False
    if tests.get("error_code") == "TESTS_DISABLED":
        return "testes desativados; não houve verificação por testes", [detail], False
    return "testes não executados", [detail], False


def _reread_with_tools(context: Dict[str, Any], outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the public read path in the verification chain before full hashes."""
    failures = []
    for item in outputs or []:
        if item.get("operation") == "delete":
            continue
        path = str(item.get("path") or "")
        result = executar_tool("read_file", {"path": path}, context)
        if not result.get("ok"):
            failures.append(path)
    return {
        "ok": not failures,
        "failures": failures,
        "detail": (
            "Releitura pela ferramenta concluída."
            if not failures else
            "Falha na releitura pela ferramenta: " + ", ".join(failures)
        ),
    }


def _clean_check_line(value: Any) -> str:
    return str(value or "").strip().rstrip(".;")


def _validation_step(result: Dict[str, Any], *, paths: Optional[List[str]] = None) -> Dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    raw_detail = result.get("detail")
    if isinstance(raw_detail, str):
        public_detail = raw_detail[:1200]
    elif isinstance(raw_detail, dict) and isinstance(raw_detail.get("detail"), str):
        public_detail = raw_detail.get("detail")[:1200]
    else:
        public_detail = None
    item = {
        "ok": result.get("ok"),
        "executed": result.get("executed"),
        "error_code": result.get("error_code"),
        "detail": public_detail,
    }
    if paths is not None:
        item["paths"] = [str(path) for path in paths if str(path)]
    detail = result.get("detail")
    if isinstance(detail, dict):
        for key in ("command", "returncode", "tests_detected", "files", "failures", "checked"):
            if key in detail:
                value = detail.get(key)
                if isinstance(value, list):
                    item[key] = value[:30]
                elif isinstance(value, (str, int, float, bool)) or value is None:
                    item[key] = value
    if isinstance(result.get("files"), list):
        item["files"] = list(result.get("files") or [])[:30]
    return {key: value for key, value in item.items() if value is not None}


def _record_rollback(session: AgentSession, rollback: Dict[str, Any], paths: List[str]) -> None:
    session.write_validation["rollback"] = _validation_step(rollback, paths=paths)


def _resume_set(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": session.evidence}
    transaction = pending.get("write_transaction") or {}
    patches = transaction.get("patches") if isinstance(transaction, dict) else None
    if not isinstance(patches, list) or not patches:
        text = "A transação confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    raw_applied = apply_patch_set(project.get("caminho_origem"), patches)
    applied = _transaction_result(raw_applied, changed=bool(raw_applied.get("ok")))
    attempted_paths = [str(item.get("path") or "") for item in patches if isinstance(item, dict)]
    session.write_validation = {"apply": _validation_step(applied, paths=attempted_paths)}
    if not applied.get("ok"):
        code = applied.get("error_code") or "PATCH_TRANSACTION_FAILED"
        diagnostic = _diagnostic_text(applied)
        report = {
            "stage": "apply",
            "error_code": code,
            "executed": bool(applied.get("executed")),
            "detail": diagnostic,
            "rollback_confirmed": None,
            "rollback_error_code": None,
            "paths": [path for path in attempted_paths if path],
        }
        text = f"A transação não foi aplicada: {code}.\n\nErro real da tentativa:\n{diagnostic}"
        return _return("failed", text, None, _details(
            session, "failed", config, failure_code=code, write_failure=report,
        ), full)

    applied_patches = (applied.get("detail") or {}).get("applied_patches") or []
    paths = [str(item.get("path") or "") for item in applied_patches]
    compile_result = _compile_after_write(config, project, paths)
    session.write_validation["compileall"] = _validation_step(compile_result, paths=paths)
    if compile_result.get("ok") is not True:
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        text, suffix, report = _write_failure_response(
            "compileall falhou após a transação.", "compileall", compile_result, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{compile_result.get('error_code') or 'COMPILEALL_FAILED'}_{suffix}",
            limitations=[str(compile_result.get("detail") or "compileall falhou")],
            write_failure=report,
        ), full)

    tests = _run_tests_after_write(config, context)
    session.write_validation["tests"] = _validation_step(tests, paths=paths)
    if tests.get("ok") is not True:
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        text, suffix, report = _write_failure_response(
            "A verificação por testes falhou após a transação.", "tests", tests, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{tests.get('error_code') or 'TESTS_FAILED'}_{suffix}",
            limitations=[str(tests.get("detail") or "testes falharam")],
            write_failure=report,
        ), full)

    expected_outputs = expected_outputs_from_patches(applied_patches)
    tool_reread = _reread_with_tools(context, expected_outputs)
    reread = verify_expected_outputs(project.get("caminho_origem"), expected_outputs)
    session.write_validation["tool_reread"] = _validation_step(tool_reread, paths=paths)
    session.write_validation["full_reread"] = _validation_step(reread, paths=paths)
    if not tool_reread.get("ok") or not reread.get("ok"):
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        reread_failure = tool_reread if not tool_reread.get("ok") else reread
        reread_failure = dict(reread_failure)
        reread_failure.setdefault("error_code", "POST_WRITE_READ_FAILED")
        text, suffix, report = _write_failure_response(
            "A releitura integral da transação falhou.", "reread", reread_failure, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"POST_WRITE_READ_FAILED_{suffix}",
            limitations=[str(reread_failure.get("detail") or "releitura falhou")],
            write_failure=report,
        ), full)

    compile_line = (
        _clean_check_line(compile_result.get("detail"))
        if compile_result.get("executed") else
        "compileall não era aplicável porque nenhum arquivo Python final foi alterado"
    )
    test_line, limitations, fully_verified = _test_verification_line(tests)
    created = [str(item.get("path") or "") for item in applied_patches if item.get("operation") == "create"]
    creation_line = (
        f"arquivos prometidos criados e confirmados: {', '.join(created)}"
        if created else
        "nenhum arquivo novo foi prometido pela transação"
    )
    state_line = (
        "Estado: transação verificada após escrita."
        if fully_verified else
        "Estado: transação aplicada com validação parcial; não foi chamada de verificada."
    )
    session.workspace_epoch += 1

    text = (
        f"Transação aplicada em {len(paths)} arquivo(s): {', '.join(paths)}.\n\nValidação pós-escrita:\n"
        f"- {compile_line};\n- {test_line};\n"
        f"- releitura de todos os arquivos pela ferramenta concluída;\n"
        f"- todos os arquivos alterados foram relidos integralmente;\n"
        f"- {creation_line};\n- exclusões prometidas foram confirmadas;\n- {state_line}"
    )
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    if pending.get("continuation_kind") != "write_confirmation" or not isinstance(pending.get("write_transaction"), dict):
        text = "A pendência não corresponde a uma confirmação transacional válida."
        return _return(
            "failed", text, None,
            _details(session, "failed", config, failure_code="WRITE_PENDING_INVALID"), full,
        )
    # The persisted continuation type objectively proves that this pending
    # operation is a workspace write. This also keeps a 5.2.1 pending proposal
    # compatible with the 5.2.2 workspace-scope contract.
    if not isinstance(session.workspace_scope, dict) or not session.workspace_scope.get("mode"):
        session.workspace_scope = {
            "mode": "write",
            "reason": "Persisted write_confirmation requires workspace mutation.",
        }
    if session.workspace_scope.get("mode") != "write":
        text = "A confirmação pendente não possui autoridade de escrita válida."
        return _return(
            "failed", text, None,
            _details(session, "failed", config, failure_code="WRITE_SCOPE_INVALID"), full,
        )
    pending_open = open_target_ids(session.investigation)
    if pending_open:
        text = "A escrita confirmada foi bloqueada porque a investigação ainda possui dívida aberta."
        return _return(
            "failed", text, None,
            _details(
                session, "failed", config,
                failure_code="WRITE_INVESTIGATION_TARGET_OPEN:" + ",".join(pending_open),
            ), full,
        )
    return _resume_set(session, pending, config, project, full)



def _freshest_evidence_for_path(session: AgentSession, path: str) -> Optional[Dict[str, Any]]:
    normalized = str(path or "").replace("\\", "/")
    for item in reversed(list(session.evidence.values())):
        if str(item.get("arquivo") or "").replace("\\", "/") == normalized:
            return item
    return None



def _enrich_patch_set(session: AgentSession, project: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """Attach deterministic freshness preconditions to canonical patch objects."""
    raw_patches = arguments.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        return arguments, "patches must be a non-empty list"
    root = project.get("caminho_origem")
    enriched: List[Dict[str, Any]] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            return arguments, "each patch must be an object"
        patch = dict(raw)
        path = patch.get("path")
        if not isinstance(path, str) or not path.strip():
            return arguments, "each patch needs canonical path"
        path = path.strip().replace("\\", "/")
        patch["path"] = path
        absolute = _resolver_caminho_seguro(root, path) if root else None
        if absolute is None:
            return arguments, f"unsafe patch path: {path}"
        exists = os.path.isfile(absolute)
        operation = str(patch.get("operation") or "").strip().lower()
        if operation not in {"replace", "create", "delete", "update"}:
            return arguments, f"patch operation must be replace|create|delete|update: {path}"
        patch["operation"] = operation
        evidence = _freshest_evidence_for_path(session, path)

        if operation in {"replace", "create"}:
            if "content" not in patch or not isinstance(patch.get("content"), str):
                return arguments, f"{operation} needs canonical string content: {path}"
        if operation == "update":
            if "new_code" not in patch or not isinstance(patch.get("new_code"), str):
                return arguments, f"update needs canonical string new_code: {path}"
            try:
                start = int(patch.get("line_start"))
                end = int(patch.get("line_end"))
            except (TypeError, ValueError):
                return arguments, f"update needs canonical line_start and line_end: {path}"
            if start < 1 or end < start:
                return arguments, f"invalid update range: {path}:{start}-{end}"
            patch["line_start"], patch["line_end"] = start, end

        if operation in {"replace", "delete", "update"}:
            if not exists:
                return arguments, f"{operation} requires an existing file: {path}"
            if not evidence or not evidence.get("file_hash"):
                return arguments, f"read the existing file before {operation}: {path}"
            if operation == "replace":
                whole_file = (
                    int(evidence.get("linha_inicio") or 0) == 1
                    and int(evidence.get("linha_fim") or 0) == int(evidence.get("total_linhas_arquivo") or -1)
                )
                if not whole_file:
                    return arguments, f"replace requires a fresh whole-file read: {path}"
            patch["file_hash_expected"] = evidence["file_hash"]
        elif operation == "create":
            if exists:
                return arguments, f"create cannot overwrite an existing file: {path}; use replace"

        if operation == "update":
            start, end = patch["line_start"], patch["line_end"]
            if int(evidence.get("linha_inicio") or 0) == start and int(evidence.get("linha_fim") or 0) == end:
                patch["range_hash_expected"] = evidence.get("content_hash")
            else:
                content = evidence.get("conteudo")
                ev_start = int(evidence.get("linha_inicio") or 0)
                ev_end = int(evidence.get("linha_fim") or 0)
                if isinstance(content, str) and ev_start == 1 and ev_end == int(evidence.get("total_linhas_arquivo") or -1):
                    patch["range_hash_expected"] = hash_faixa(content, start, end)
            if not patch.get("range_hash_expected"):
                return arguments, f"read the exact range before updating {path}:{start}-{end}"

        allowed = {"operation", "path"}
        if operation == "replace":
            allowed.update({"content", "file_hash_expected"})
        elif operation == "create":
            allowed.add("content")
        elif operation == "delete":
            allowed.add("file_hash_expected")
        elif operation == "update":
            allowed.update({"line_start", "line_end", "new_code", "file_hash_expected", "range_hash_expected"})
        unknown = sorted(set(patch) - allowed)
        if unknown:
            return arguments, f"unknown canonical patch field(s) for {path}: {', '.join(unknown)}"
        enriched.append(patch)
    return {"patches": enriched}, None


def _preserve_source_for_retry(previous: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    needs_source = any(
        item.get("error_code") in {"READ_PHASE_CLOSED"}
        for item in current if isinstance(item, dict)
    )
    if not needs_source:
        return current
    sources = [
        item for item in previous
        if isinstance(item, dict) and item.get("tool") in EVIDENCE_TOOLS and item.get("ok") is True
    ][-2:]
    return sources + current

def _normalized_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def _source_already_visible(
    session: AgentSession, tool: str, arguments: Dict[str, Any],
) -> bool:
    """Return whether the exact requested source body/range is in the current prompt.

    ObservationLedger owns physical execution identity. This helper owns only
    current Main-LLM context visibility and therefore never suppresses searches
    or structured observations.
    """
    signature = _observation_signature(tool, arguments)
    if not signature:
        return False
    # Physical repeat suppression is owned by ObservationLedger. This helper is
    # only about whether a concrete source body/range is already visible in the
    # current prompt. Structured observations/searches are never blocked here.
    if tool in {"list_tree", "search_code", "find_symbol"} or tool in CACHEABLE_OBSERVATION_TOOLS:
        return False

    path = _normalized_path(arguments.get("path"))
    if not path:
        return False
    ranges = [
        item for item in session.visible_source_ranges.get(path, [])
        if isinstance(item, dict)
    ]
    if not ranges:
        return False

    # Only what actually reached an agent prompt can suppress a later semantic
    # read. Full runtime EvidenceRecords deliberately do not count here.
    groups: Dict[str, List[Tuple[int, int, int]]] = {}
    for item in ranges:
        start = int(item.get("start") or 0)
        end = int(item.get("end") or 0)
        if start <= 0 or end < start:
            continue
        groups.setdefault(str(item.get("file_hash") or ""), []).append((
            start, end, int(item.get("total_lines") or 0),
        ))
    if not groups:
        return False

    requested_start = 1 if tool == "read_file" else int(arguments.get("line_start") or 0)
    requested_end_raw = None if tool == "read_file" else int(arguments.get("line_end") or 0)
    if tool == "read_range" and (requested_start <= 0 or int(requested_end_raw or 0) < requested_start):
        return False

    # Never combine visible ranges from different file hashes into one proof of
    # coverage. A single observed file version must cover the requested range.
    for version_ranges in groups.values():
        normalized_ranges = sorted(version_ranges)
        if tool == "read_file":
            total = max((item[2] for item in normalized_ranges), default=0)
            if total <= 0:
                continue
            requested_end = total
        else:
            requested_end = int(requested_end_raw or 0)

        cursor = requested_start
        for start, end, _total in normalized_ranges:
            if end < cursor:
                continue
            if start > cursor:
                break
            cursor = max(cursor, end + 1)
            if cursor > requested_end:
                return True
    return False


def _record_decision(
    session: AgentSession,
    decision_type: str,
    outcome: str,
    *,
    reason: Optional[str] = None,
    tools: Optional[List[str]] = None,
) -> None:
    """Record only observable protocol decisions, never model reasoning or prose."""
    item: Dict[str, Any] = {
        "turn": session.turn,
        "phase": session.phase,
        "decision": str(decision_type),
        "outcome": str(outcome),
    }
    if reason:
        item["reason"] = str(reason)[:240]
    if tools:
        item["tools"] = [str(tool) for tool in tools[:8]]
    session.decision_history.append(item)
    del session.decision_history[:-50]


def _action_signature(tool: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _objective_runtime_state_payload(session: AgentSession) -> Dict[str, Any]:
    """Return the deterministic state that represents objective runtime progress.

    Free-form Investigation reason/status churn is intentionally excluded. The
    Main LLM may reinterpret Evidence freely, but only new observed reality, new
    Evidence bindings or verified workspace mutation reset runtime stall state.
    """
    evidence = [
        (str(key), str((item or {}).get("content_hash") or ""), str((item or {}).get("file_hash") or ""))
        for key, item in sorted((session.evidence or {}).items()) if isinstance(item, dict)
    ]
    observations = [
        (str(key), str((item or {}).get("result_fingerprint") or ""))
        for key, item in sorted((session.observation_ledger or {}).items()) if isinstance(item, dict)
    ]
    bindings = [
        (str(item.get("id") or ""), tuple(sorted(str(eid) for eid in (item.get("evidence_ids") or []) if str(eid))))
        for item in (session.investigation or []) if isinstance(item, dict)
    ]
    return {
        "workspace_epoch": int(session.workspace_epoch or 0),
        "committed_progress_epoch": int(session.committed_progress_epoch or 0),
        "evidence": evidence,
        "observations": observations,
        "investigation_evidence_bindings": sorted(bindings),
    }


def _runtime_progress_fingerprint(session: AgentSession) -> str:
    return hash_texto(json.dumps(
        _objective_runtime_state_payload(session),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ))


def _record_runtime_cycle_progress(session: AgentSession, progressed: bool) -> None:
    session.progress_history.append({
        "turn": int(session.runtime_cycle_start_turn or session.turn),
        "progressed": bool(progressed),
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_count": len(session.observation_ledger or {}),
        "evidence_count": len(session.evidence or {}),
        "committed_progress_epoch": int(session.committed_progress_epoch or 0),
    })
    del session.progress_history[:-50]


def _decision_rejection_key(
    session: AgentSession, code: str, payload: Any, *, objective_context: Optional[Dict[str, Any]] = None,
) -> str:
    canonical = {
        "objective_state": _objective_runtime_state_payload(session),
        "workspace_scope_mode": str((session.workspace_scope or {}).get("mode") or "none"),
        "phase": str(session.phase or ""),
        "code": str(code or ""),
        "payload": payload,
        "objective_context": dict(objective_context or {}),
    }
    return hash_texto(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def _record_rejected_decision(
    session: AgentSession, code: str, payload: Any, *, objective_context: Optional[Dict[str, Any]] = None,
) -> int:
    key = _decision_rejection_key(session, code, payload, objective_context=objective_context)
    previous = session.decision_ledger.get(key) if isinstance(session.decision_ledger, dict) else None
    count = int((previous or {}).get("count") or 0) + 1
    session.decision_ledger[key] = {"count": count, "turn": session.turn, "code": code}
    if len(session.decision_ledger) > 128:
        oldest = sorted(session.decision_ledger.items(), key=lambda kv: int((kv[1] or {}).get("turn") or 0))
        for old_key, _ in oldest[:len(session.decision_ledger)-128]:
            session.decision_ledger.pop(old_key, None)
    if count > 1:
        session.repeated_rejected_decisions += 1
    return count


def _rehydrate_observation(session: AgentSession, entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    replay = copy.deepcopy(entry.get("replay_result")) if isinstance(entry.get("replay_result"), dict) else None
    if replay is None and isinstance(entry.get("replay_summary"), dict):
        replay = copy.deepcopy(entry.get("replay_summary"))
    evidence_ids = [str(item) for item in entry.get("evidence_ids") or [] if str(item) in session.evidence]
    tool = str(entry.get("tool") or "")
    if replay is None:
        details = []
        for evidence_id in evidence_ids:
            evidence = session.evidence.get(evidence_id) or {}
            clone = dict(evidence)
            clone["evidence_id"] = evidence_id
            if evidence.get("source_type") in {"search_observation", "symbol_observation"}:
                try:
                    details.append(json.loads(str(evidence.get("conteudo") or "{}")))
                except Exception:
                    details.append({"evidence_id": evidence_id})
            else:
                details.append(_llm_source_view(clone, config, tool=tool))
        if tool == "search_code":
            negative = next((item for item in details if isinstance(item, dict) and item.get("coverage_complete") is True and not item.get("arquivo")), None)
            detail = negative or {"resultados": details}
        elif len(details) == 1:
            detail = details[0]
        else:
            detail = {"evidence": details}
        replay = {"tool": tool, "status": "success", "ok": True, "executed": False, "changed": False, "error_code": None, "detail": detail, "evidence_ids": evidence_ids}
    replay["tool"] = tool or replay.get("tool")
    replay["status"] = "replayed"
    replay["executed"] = False
    replay["changed"] = False
    replay["replayed"] = True
    replay["source_turn"] = entry.get("turn")
    replay["evidence_ids"] = evidence_ids or list(replay.get("evidence_ids") or [])
    return replay


def _final_validation_feedback(reason: str) -> str:
    if str(reason).startswith("FINAL_INVESTIGATION_REQUIRED"):
        return (
            "FINAL_VALIDATION_ERROR: FINAL_INVESTIGATION_REQUIRED. "
            "Create the material Investigation targets for this project-grounded request before finalizing."
        )
    if str(reason).startswith("FINAL_INVESTIGATION_TARGET_OPEN:"):
        return (
            f"FINAL_VALIDATION_ERROR: {reason}. Resolve the open targets with Evidence, explicitly dismiss them with reason, "
            "or continue investigation. Do not drop or rename targets."
        )
    if str(reason).startswith("FINAL_PROJECT_EVIDENCE_IDS_REQUIRED"):
        return (
            "FINAL_VALIDATION_ERROR: FINAL_PROJECT_EVIDENCE_IDS_REQUIRED. "
            "Return final as an object with answer plus the evidence_ids actually used. "
            "Do not generate claims; claims are reviewed separately."
        )
    if str(reason).startswith("FINAL_UNKNOWN_EVIDENCE:"):
        return f"FINAL_VALIDATION_ERROR: {reason}. Use only evidence_ids present in evidence_index."
    return f"FINAL_VALIDATION_ERROR: {reason}. Return a corrected final answer."


def _semantic_followup_stalled_feedback(session: AgentSession) -> str:
    """Describe a physical stall without choosing the semantic next action."""
    open_targets = [
        {
            "id": item.get("id"),
            "goal": item.get("goal"),
            "evidence_ids": list(item.get("evidence_ids") or []),
            "reason": item.get("reason", ""),
        }
        for item in session.investigation
        if isinstance(item, dict) and item.get("status") == "open"
    ]
    return json.dumps({
        "code": "SEMANTIC_FOLLOWUP_STALLED",
        "open_targets": open_targets,
        "instruction": (
            "No new Evidence was added. Do not repeat observations already blocked or covered. "
            "Keep semantic control: choose a materially different available observation, update the "
            "Investigation Contract consistently, narrow/remove the unsupported statement, or explicitly "
            "state a limitation when the current Evidence warrants it. Runtime does not choose the tool."
        ),
    }, ensure_ascii=False, separators=(",", ":"))


def _deadline_exceeded(config: Dict[str, Any]) -> bool:
    deadline = (config.get("_runtime_agent_budget") or {}).get("deadline_monotonic")
    return deadline is not None and time.monotonic() >= float(deadline)


def _run(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    full: bool,
    conversation_context: Any = None,
) -> tuple:
    cfg = config.get("agent") or {}
    max_turns = max(1, int(cfg.get("max_llm_turns", 8) or 8))
    claim_config(config)  # validate once at the execution boundary
    run_turn_limit = max_turns
    max_identical = max(1, int(cfg.get("max_identical_tool_repeats", 2) or 2))
    parse_retries = max(0, int(cfg.get("structured_protocol_retries", 1) or 1))
    final_retries = max(0, int(cfg.get("final_validation_retries", 1) or 1))
    max_patch_failures = max(1, int(cfg.get("max_patch_dry_run_failures", 2) or 2))
    max_no_progress = max(1, int(cfg.get("max_no_progress_turns", 2) or 2))
    max_phase_violations = max(0, int(cfg.get("max_phase_violations", 1) or 1))
    feedback = ""
    final_failures = 0

    while session.turn < run_turn_limit:
        if _deadline_exceeded(config):
            text = "A tarefa excedeu o prazo de execução."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="TASK_DEADLINE_EXCEEDED"), full)

        # Unified runtime-cycle epilogue: every prior Main-LLM cycle is observed
        # here, including paths that exited via ``continue`` before the tool
        # epilogue (budget rejects, protocol repair, invalid final, etc.).
        current_runtime_fp = _runtime_progress_fingerprint(session)
        if session.runtime_cycle_start_fingerprint is not None:
            progressed = current_runtime_fp != session.runtime_cycle_start_fingerprint
            _record_runtime_cycle_progress(session, progressed)
            if progressed:
                session.no_progress_turns = 0
            else:
                session.no_progress_turns += 1
                if session.no_progress_turns >= max_no_progress:
                    if session.evidence and str((session.workspace_scope or {}).get("mode") or "") == "write":
                        feedback = "NO_PROGRESS_WRITE: investigation is closed. Use retained evidence and produce one transactional patch now."
                    elif session.evidence and session.claim_followup_pending:
                        feedback = _semantic_followup_stalled_feedback(session)
                    elif session.evidence:
                        feedback = json.dumps({
                            "code": "RUNTIME_CYCLE_STALLED",
                            "instruction": "The canonical runtime state did not advance. Do not repeat the rejected decision or physical observation; reinterpret retained evidence, commit valid Investigation progress, choose a materially different observation, or return a grounded limitation.",
                        }, ensure_ascii=False, separators=(",", ":"))
                    else:
                        text = "A tarefa não produziu evidência nem progresso após tentativas consecutivas."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code="AGENT_NO_PROGRESS"), full)
        session.runtime_cycle_start_fingerprint = current_runtime_fp
        session.runtime_cycle_start_turn = session.turn + 1

        session.turn += 1
        evidence_before = len(session.evidence)
        try:
            decision, allowed = _call_agent(session, config, project, conversation_context, feedback)
            session.parse_failures = 0
        except ErroLLM as error:
            if _is_structured_response_error(error, "agent"):
                session.parse_failures += 1
                _record_decision(session, "protocol", "rejected", reason=error.error_code)
                if session.parse_failures <= parse_retries:
                    feedback = _structured_retry_prompt("", error, "agent").strip()
                    continue
                text = "A LLM não produziu uma decisão estruturada válida."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, limitations=[str(error)], failure_code="AGENT_STRUCTURED_PROTOCOL_INVALID"), full,
                )
            text = f"A chamada LLM falhou: {error.error_code or 'LLM_FAILED'}."
            return _return("failed", text, None, _details(session, "failed", config, limitations=[str(error)], failure_code=error.error_code or "LLM_FAILED"), full)
        except Exception as error:
            _record_decision(session, "runtime", "failed", reason=f"AGENT_RUNTIME_ERROR:{type(error).__name__}")
            text = "O runtime do agente encontrou um erro interno ao processar a decisão estruturada."
            return _return(
                "failed", text, None,
                _details(session, "failed", config, limitations=[str(error)], failure_code="AGENT_RUNTIME_ERROR"), full,
            )

        raw_updates = decision.get("investigation_updates")
        if raw_updates is None:
            raw_updates = []
        prospective_investigation, accepted_updates, rejected_updates, committed_progress = apply_investigation_updates(
            raw_updates, previous=session.investigation, evidence=session.evidence,
        )
        project_available_now = _project_descriptor(project)["available"]
        raw_calls = decision.get("tool_calls") if isinstance(decision.get("tool_calls"), list) else []
        project_action = any(
            isinstance(call, dict)
            and str(call.get("tool") or "")
            and str(call.get("tool") or "") not in UTILITY_TOOLS
            for call in raw_calls
        )
        scope_ok, scope_reason, normalized_scope = validate_workspace_scope(
            decision.get("workspace_scope"),
            previous=session.workspace_scope,
            project_available=project_available_now,
            investigation=prospective_investigation,
            project_action=project_action,
            patches_requested=isinstance(decision.get("patches"), list),
        )
        if not scope_ok:
            _record_decision(session, "workspace_scope", "rejected", reason=scope_reason)
            feedback = (
                f"WORKSPACE_SCOPE_VALIDATION_ERROR: {scope_reason}. "
                "You decide semantic workspace dependency via workspace_scope: none for workspace-independent work, "
                "read for current-workspace facts, write for requested workspace changes. Preserve read/write once declared."
            )
            continue
        session.workspace_scope = normalized_scope
        _record_decision(
            session, "workspace_scope", "accepted",
            reason=str(normalized_scope.get("mode") or "none"),
        )

        # Commit every structurally valid target update independently. Invalid
        # siblings cannot roll back accepted work, and omitted targets remain in
        # the canonical runtime-owned contract unchanged.
        session.investigation = prospective_investigation
        _record_committed_progress(session, committed_progress, accepted_updates=accepted_updates)
        for item in accepted_updates:
            _record_decision(
                session, "investigation_update",
                "committed" if item.get("changed") else "unchanged",
                reason=f"{item.get('id')}={item.get('status')}",
            )
        for item in rejected_updates:
            _record_decision(
                session, "investigation_update", "rejected",
                reason=str(item.get("reason") or "INVESTIGATION_UPDATE_REJECTED"),
            )

        project_grounded_now = normalized_scope.get("mode") in {"read", "write"}
        action_needs_direction = bool(
            isinstance(decision.get("tool_calls"), list)
            or isinstance(decision.get("patches"), list)
            or decision.get("final") is not None
        )
        if project_grounded_now and action_needs_direction and not session.investigation:
            _record_decision(session, "investigation_contract", "rejected", reason="INVESTIGATION_REQUIRED")
            feedback = (
                "INVESTIGATION_VALIDATION_ERROR: INVESTIGATION_REQUIRED. "
                "Create only the material targets needed for this grounded action in investigation_updates."
            )
            continue

        target_state = ",".join(
            f"{item.get('id')}={item.get('status')}"
            for item in session.investigation if isinstance(item, dict)
        )
        _record_decision(
            session, "investigation_contract", "accepted",
            reason=target_state or "empty",
        )

        if rejected_updates:
            feedback = json.dumps({
                "code": "INVESTIGATION_UPDATES_PARTIALLY_REJECTED",
                "accepted_updates": [
                    {"id": item.get("id"), "changed": bool(item.get("changed"))}
                    for item in accepted_updates
                ],
                "rejected_updates": rejected_updates,
                "canonical_investigation": session.investigation,
                "instruction": (
                    "Accepted updates are already committed and must not be reconstructed. "
                    "Correct only the rejected target updates on the next call."
                ),
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        if decision.get("needs_user"):
            _record_decision(session, "needs_user", "accepted")
            text = str(decision["needs_user"])
            pending = {
                "continuation_kind": "user_input",
                "pergunta_ao_usuario": text,
                "estado": session.to_dict(),
            }
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if isinstance(decision.get("patches"), list):
            _record_decision(session, "patches", "requested")
            project_available = _project_descriptor(project)["available"]
            write_enabled = bool(((config.get("codar") or {}).get("ativado", True)))
            write_required = str((session.workspace_scope or {}).get("mode") or "") == "write"
            if not project_available:
                text = "A escrita exige um workspace ativo."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="WORKSPACE_NOT_AVAILABLE"), full)
            if not write_enabled or not write_required:
                _record_decision(session, "patches", "rejected", reason="WRITE_ACTION_NOT_ALLOWED")
                feedback = "WRITE_ACTION_NOT_ALLOWED: only propose patches for an explicit write request."
                continue
            open_ids = open_target_ids(session.investigation)
            if open_ids:
                _record_decision(
                    session, "patches", "rejected",
                    reason="WRITE_INVESTIGATION_TARGET_OPEN:" + ",".join(open_ids),
                )
                feedback = (
                    "WRITE_INVESTIGATION_TARGET_OPEN: resolve or explicitly dismiss every open Investigation target "
                    "before proposing a write transaction. Open: " + ",".join(open_ids)
                )
                continue
            if session.phase == "write_investigate":
                _record_decision(session, "patches", "rejected", reason="WRITE_REQUIRES_SOURCE_READ")
                feedback = "WRITE_REQUIRES_SOURCE_READ: read every existing file needed by the transaction before proposing patches."
                session.phase_violations += 1
                continue
            if session.phase not in {"write_prepare", "write_patch_only", "write_patch_retry"}:
                _record_decision(session, "patches", "rejected", reason="WRITE_PHASE_INVALID")
                feedback = "WRITE_PHASE_INVALID: follow runtime_phase before proposing a transaction."
                continue

            enriched, patch_error = _enrich_patch_set(session, project, {"patches": decision["patches"]})
            if patch_error:
                session.patch_failures += 1
                _record_decision(session, "patch_validation", "rejected", reason="PATCH_SCHEMA_INVALID")
                if session.patch_failures >= max_patch_failures:
                    text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {patch_error}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                feedback = f"PATCH_SCHEMA_INVALID: {patch_error}. Correct the same transaction; do not restart investigation."
                session.phase = "write_patch_retry"
                continue

            raw_dry = dry_run_patch_set(project.get("caminho_origem"), enriched["patches"])
            dry = _transaction_result(raw_dry, changed=False)
            session.write_validation["dry_run"] = _validation_step(
                dry, paths=[str(item.get("path") or "") for item in enriched["patches"]]
            )
            if dry.get("ok") is not True:
                session.patch_failures += 1
                code = str(dry.get("error_code") or "DRY_RUN_FAILED")
                _record_decision(session, "patch_validation", "rejected", reason=code)
                if code in TERMINAL_TOOL_ERRORS:
                    text = f"O dry-run transacional encontrou um erro terminal: {code}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=code), full)
                if session.patch_failures >= max_patch_failures:
                    text = f"O dry-run da escrita falhou {session.patch_failures} vez(es): {code} — {_diagnostic_text(dry)}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=code), full)
                feedback = f"{code}: {_diagnostic_text(dry)}. Correct the same transaction; do not restart investigation."
                session.phase = "write_patch_retry"
                continue

            _record_decision(session, "patch_validation", "validated")
            detail = dry.get("detail") if isinstance(dry.get("detail"), dict) else {}
            text, pending = _pending_patch_set(session, detail)
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if "final" in decision:
            claims_cfg = claim_config(config)
            project_available = _project_descriptor(project)["available"]
            project_root = project.get("caminho_origem")
            workspace_mode = str((session.workspace_scope or {}).get("mode") or "none")
            write_required = workspace_mode == "write"
            write_available = bool(((config or {}).get("codar") or {}).get("ativado", True))
            final_obj = decision.get("final")

            if write_required and write_available:
                ok = False
                reason = "FINAL_WRITE_ACTION_REQUIRED"
                answer, limitations = "", []
            else:
                ok, reason, answer, limitations, _unused_claims, _finding_limit = validate_final(
                    final_obj, session.evidence,
                    request=session.request,
                    project_available=project_available,
                    grounding_required=workspace_mode in {"read", "write"},
                    investigation=session.investigation,
                )

            final_evidence_ids = (
                list(dict.fromkeys(str(item) for item in final_obj.get("evidence_ids") or [] if str(item)))
                if isinstance(final_obj, dict) else []
            )
            review_evidence_ids = list(final_evidence_ids)
            for evidence_id in target_evidence_ids(session.investigation):
                if evidence_id not in review_evidence_ids:
                    review_evidence_ids.append(evidence_id)
            claims_grounded_request = bool(
                claims_cfg["mode"] != "off"
                and workspace_mode in {"read", "write"}
            )
            needs_scope_review = bool(
                ok
                and claims_cfg["mode"] != "off"
                and project_available
                and workspace_mode == "none"
                and session.phase != "chat"
            )
            needs_claim_review = bool(
                ok
                and claims_cfg["mode"] != "off"
                and (claims_grounded_request or bool(final_evidence_ids))
            )

            if ok and needs_scope_review:
                _record_decision(session, "final", "provisional", reason="workspace_scope_review")
                try:
                    scope_ok, scope_reason, scope_review, _scope_view = _run_claim_verification(
                        session, config, answer, [], project_root=project_root,
                        allow_protocol_recovery=False, scope_only=True,
                    )
                except ErroLLM as error:
                    text = f"A verificação semântica de escopo falhou: {error.error_code or 'WORKSPACE_SCOPE_VERIFIER_FAILED'}."
                    return _return(
                        "failed", text, None,
                        _details(session, "failed", config, limitations=[str(error)], failure_code=error.error_code or "WORKSPACE_SCOPE_VERIFIER_FAILED"),
                        full,
                    )
                if not scope_ok:
                    _record_decision(session, "workspace_scope_review", "rejected", reason=scope_reason)
                    text = f"A verificação semântica de escopo ficou inválida: {scope_reason}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=scope_reason), full)
                scope_gaps = [dict(item) for item in scope_review.get("semantic_gaps") or [] if isinstance(item, dict)]
                if scope_gaps:
                    _append_claim_review(session, scope_review, stage="workspace_scope")
                    session.claim_followup_pending = True
                    session.claim_followup_feedback = json.dumps({
                        "code": "WORKSPACE_SCOPE_INSUFFICIENT",
                        "workspace_scope": dict(session.workspace_scope or {}),
                        "semantic_gaps": scope_gaps,
                        "instruction": (
                            "A semantic reviewer found that current workspace facts are material. "
                            "You control semantics: declare read/write workspace_scope as appropriate, create the material "
                            "Investigation targets, gather Evidence, or narrow the answer if the request is actually workspace-independent."
                        ),
                    }, ensure_ascii=False, separators=(",", ":"))
                    feedback = session.claim_followup_feedback
                    session.latest_tool_results = []
                    session.relevant_sources = []
                    _record_decision(session, "workspace_scope_review", "insufficient", reason="WORKSPACE_SCOPE_INSUFFICIENT")
                    continue
                _record_decision(session, "workspace_scope_review", "supported")
                session.claim_followup_pending = False
                session.claim_followup_feedback = ""
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            if ok and not needs_claim_review:
                session.claim_followup_pending = False
                session.claim_followup_feedback = ""
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            if ok and needs_claim_review:
                _record_decision(session, "final", "provisional")
                try:
                    review_ok, review_reason, review, evidence_view = _run_claim_verification(
                        session, config, answer, review_evidence_ids, project_root=project_root,
                    )
                except ErroLLM as error:
                    text = f"A verificação de claims falhou: {error.error_code or 'CLAIM_VERIFIER_LLM_FAILED'}."
                    return _return(
                        "failed", text, None,
                        _details(session, "failed", config, limitations=[str(error)], failure_code=error.error_code or "CLAIM_VERIFIER_LLM_FAILED"),
                        full,
                    )
                if not review_ok:
                    _record_decision(session, "claim_review", "rejected", reason=review_reason)
                    if str(review_reason).startswith("EVIDENCE_STALE:") and session.turn < run_turn_limit:
                        session.claim_followup_pending = True
                        session.claim_followup_feedback = json.dumps({
                            "code": "EVIDENCE_STALE",
                            "detail": review_reason,
                            "instruction": "Selected file evidence changed on disk. Decide whether to reread, search again, narrow the answer, or report the limitation.",
                        }, ensure_ascii=False, separators=(",", ":"))
                        feedback = session.claim_followup_feedback
                        session.latest_tool_results = []
                        session.relevant_sources = []
                        continue
                    text = f"A verificação de claims ficou inválida: {review_reason}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=review_reason), full)

                _append_claim_review(session, review, stage="initial")
                summary = dict(review.get("summary") or {})
                has_contradicted_claims = int(summary.get("contradicted", 0) or 0) > 0
                has_insufficient_claims = int(summary.get("insufficient", 0) or 0) > 0
                has_semantic_gaps = int(summary.get("semantic_gaps", 0) or 0) > 0
                if has_contradicted_claims or has_insufficient_claims or has_semantic_gaps:
                    if has_contradicted_claims:
                        review_reason = "CLAIM_CONTRADICTED"
                        review_outcome = "contradicted"
                    elif has_insufficient_claims:
                        review_reason = "CLAIM_INSUFFICIENT"
                        review_outcome = "insufficient"
                    else:
                        review_reason = "CLAIM_SEMANTIC_GAP"
                        review_outcome = "insufficient"

                    # Decision Ledger is the generic loop fuse. If the reviewer
                    # returns the exact same debt against the exact same
                    # canonical workspace/Investigation state, another
                    # Agent->Claim cycle would spend tokens without changing
                    # reality. A changed state creates a different key and is
                    # allowed normally.
                    repeat_count = _record_rejected_decision(
                        session, "CLAIM_REVIEW_FOLLOWUP", _review_followup_payload(review),
                    )
                    if repeat_count > 1:
                        _record_decision(
                            session, "claim_review", "stalled",
                            reason=f"CLAIM_REVIEW_STALLED:{review_reason}:repeat={repeat_count}",
                        )
                        text = "A mesma dívida semântica reapareceu sem mudança material de estado; o runtime interrompeu o ciclo para evitar repetição de tokens."
                        return _return(
                            "failed", text, None,
                            _details(session, "failed", config, failure_code="CLAIM_REVIEW_STALLED"), full,
                        )

                    session.investigation, reopened_targets = reopen_targets_from_review(
                        session.investigation, review,
                    )
                    if reopened_targets:
                        _record_decision(
                            session, "investigation_contract", "reopened",
                            reason=",".join(reopened_targets),
                        )
                    _pin_semantic_followup_evidence(session, review, reopened_targets)
                    _record_decision(session, "claim_review", review_outcome, reason=review_reason)
                    session.claim_followup_pending = True
                    session.claim_followup_feedback = review_followup_feedback(review)
                    feedback = session.claim_followup_feedback
                    # Claim debt is directed rework, not a free increase in the
                    # normal reasoning limit. Open only unused task-wide LLM
                    # capacity and reserve one later verifier call.
                    run_turn_limit = _extend_claim_rework_lane(session, config, run_turn_limit)
                    if session.turn < run_turn_limit:
                        # Make the already-existing rework lane operationally
                        # explicit so scarce LLM calls are not spent rediscovering
                        # budget state. Runtime reports capacity only; Agent still
                        # chooses whether to investigate, reinterpret or conclude.
                        try:
                            followup_payload = json.loads(session.claim_followup_feedback)
                        except Exception:
                            followup_payload = {"code": "CLAIM_REVIEW_FOLLOWUP", "instruction": session.claim_followup_feedback}
                        tool_budget = _tool_budget_state(session, config)
                        remaining_physical = max(0, int(tool_budget["effective_limit"]) - int(session.tool_calls or 0))
                        agent_calls_left = max(0, int(run_turn_limit) - int(session.turn))
                        followup_payload["runtime_capacity"] = {
                            "agent_calls_before_reserved_verifier": agent_calls_left,
                            "physical_tool_calls_available_now": remaining_physical,
                            "pending_progress_cycles": int(tool_budget.get("pending_progress_cycles", 0)),
                            "pending_extension_calls": int(tool_budget.get("pending_extension_calls", 0)),
                        }
                        instruction = str(followup_payload.get("instruction") or "")
                        if agent_calls_left >= 2:
                            instruction += (
                                " If new Evidence is required, use materially novel tools in the earliest follow-up call; "
                                "preserve the last Agent call for a corrected final before the reserved verifier pass."
                            )
                        followup_payload["instruction"] = instruction.strip()
                        session.claim_followup_feedback = json.dumps(
                            followup_payload, ensure_ascii=False, separators=(",", ":"), default=str,
                        )
                        feedback = session.claim_followup_feedback
                        session.latest_tool_results = []
                        session.relevant_sources = []
                        continue

                    if claims_cfg["require_supported"]:
                        if has_contradicted_claims:
                            text = "A conclusão contém afirmações contraditas pelas evidências e não há capacidade física restante para um novo ciclo Agent→Claim."
                            failure = "CLAIM_REVIEW_CONTRADICTED"
                        elif has_insufficient_claims:
                            text = "A conclusão contém afirmações que não puderam ser confirmadas com a evidência disponível."
                            failure = "CLAIM_REVIEW_INSUFFICIENT"
                        else:
                            text = "A conclusão ainda contém lacunas materiais identificadas pela verificação semântica."
                            failure = "CLAIM_REVIEW_SEMANTIC_GAP"
                        return _return("failed", text, None, _details(session, "failed", config, failure_code=failure), full)
                    limitations = list(limitations) + [
                        "A verificação semântica ainda encontrou dívida material na conclusão."
                    ]

                session.claim_followup_pending = False
                session.claim_followup_feedback = ""
                session.followup_pinned_evidence_ids = []
                _record_decision(session, "claim_review", "supported")
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            _record_decision(session, "final", "rejected", reason=reason)
            final_failures += 1
            if final_failures <= final_retries:
                if reason == "FINAL_WRITE_ACTION_REQUIRED":
                    feedback = (
                        "FINAL_WRITE_ACTION_REQUIRED: the user requested a file change. "
                        "Do not return final prose. Read the necessary existing files and "
                        "produce one patch dry-run for user confirmation."
                    )
                else:
                    feedback = _final_validation_feedback(reason)
                continue
            text = f"A conclusão final ficou inválida: {reason}."
            return _return("failed", text, None, _details(session, "failed", config, failure_code=reason), full)

        calls = decision.get("tool_calls") if isinstance(decision.get("tool_calls"), list) else [decision]
        calls = [call for call in calls if isinstance(call, dict) and call.get("tool")]
        if not calls:
            _record_decision(session, "empty", "rejected", reason="NO_ACTION")
            _record_rejected_decision(session, "NO_ACTION", {})
            feedback = "Choose one available tool, ask a blocking question, or return final."
            continue

        _record_decision(
            session,
            "tool_calls" if len(calls) > 1 else "tool",
            "requested",
            tools=[str(call.get("tool") or "") for call in calls],
        )

        # Unified physical preflight. Semantic freedom is untouched: the model
        # may request any available observation again. Runtime decides only
        # whether that physical observation must be executed for this workspace
        # epoch, or whether retained reality can be replayed.
        next_results: List[Dict[str, Any]] = []
        novel_calls: List[Dict[str, Any]] = []
        seen_batch_observations: set[str] = set()
        preflight_invalid = 0
        preflight_replays = 0
        for call in calls:
            tool = str(call.get("tool") or "")
            arguments = call.get("arguments") or {}
            if tool not in allowed:
                phase_error = None
                phase_detail = "A ferramenta não está disponível neste workspace/configuração."
                if tool in READ_TOOLS and session.phase in {"write_patch_only", "write_patch_retry"}:
                    phase_error = "READ_PHASE_CLOSED"
                    phase_detail = "A fase de leitura terminou. Use as evidências atuais e produza a transação agora."
                elif session.phase == "analysis_answer_only":
                    phase_error = "FINAL_PHASE_REQUIRES_ANSWER"
                    phase_detail = "A investigação terminou. Responda usando as evidências atuais, sem novas ferramentas."
                if phase_error in {"READ_PHASE_CLOSED", "FINAL_PHASE_REQUIRES_ANSWER"}:
                    session.phase_violations += 1
                rejected = {
                    "tool": tool, "status": "failed", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": phase_error or "TOOL_NOT_AVAILABLE",
                    "detail": phase_detail,
                }
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=rejected["error_code"], tools=[tool])
                _record_tool_history(session, tool, arguments, rejected, status_override="rejected")
                continue

            normalized, error = validar_chamada_tool(tool, arguments)
            if error:
                rejected = _compact_non_read_result(tool, error)
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=error.get("error_code") or "INVALID_ARGUMENT", tools=[tool])
                _record_tool_history(session, tool, arguments, error, status_override="rejected")
                continue

            _record_decision(session, "tool_validation", "validated", tools=[tool])
            semantic_signature = _observation_signature(tool, normalized)
            if semantic_signature and semantic_signature in seen_batch_observations:
                duplicate = {
                    "tool": tool, "status": "replayed", "ok": True,
                    "executed": False, "changed": False,
                    "error_code": "BATCH_DUPLICATE_SUPPRESSED",
                    "detail": "Duplicate observation in the same batch was suppressed before physical execution.",
                    "replayed": True,
                }
                preflight_replays += 1
                next_results.append(duplicate)
                _record_decision(session, "tool_preflight", "batch_duplicate", reason="BATCH_DUPLICATE_SUPPRESSED", tools=[tool])
                _record_tool_history(session, tool, normalized, duplicate, semantic_signature=semantic_signature, status_override="replayed")
                continue
            if semantic_signature:
                seen_batch_observations.add(semantic_signature)
                previous = _lookup_observation(session, semantic_signature)
                if previous is not None:
                    replay = _rehydrate_observation(session, previous, config)
                    preflight_replays += 1
                    session.observation_replays += 1
                    next_results.append(replay)
                    _record_decision(session, "tool_preflight", "replayed", reason="OBSERVATION_REHYDRATED", tools=[tool])
                    _record_tool_history(session, tool, normalized, replay, semantic_signature=semantic_signature, status_override="replayed")
                    _remember_relevant_sources(session, tool, replay, config)
                    continue

            if tool in READ_TOOLS and _source_already_visible(session, tool, normalized):
                visible = {
                    "tool": tool, "status": "replayed", "ok": True,
                    "executed": False, "changed": False,
                    "error_code": "SOURCE_ALREADY_VISIBLE",
                    "detail": "Requested source coverage is already present in the current Main-LLM prompt; no physical reread was executed.",
                    "replayed": True,
                }
                preflight_replays += 1
                session.observation_replays += 1
                next_results.append(visible)
                _record_decision(session, "tool_preflight", "replayed", reason="SOURCE_ALREADY_VISIBLE", tools=[tool])
                _record_tool_history(session, tool, normalized, visible, semantic_signature=semantic_signature, status_override="replayed")
                continue

            novel_calls.append({
                "tool": tool,
                "arguments": normalized,
                "semantic_signature": semantic_signature,
                "action_signature": _action_signature(tool, normalized),
            })

        # Phase policy is the outer structural contract. Repeated attempts to
        # investigate after reads are closed fail before lower-level argument
        # handling, so a validation detail cannot mask FINAL_PHASE_REQUIRES_ANSWER.
        if session.phase_violations > max_phase_violations:
            text = "A LLM continuou tentando investigar depois que a fase de leitura foi encerrada."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="FINAL_PHASE_REQUIRES_ANSWER"), full)

        # Invalid calls make the requested batch non-executable. Runtime does
        # not let a later budget gate mask a malformed tool contract. Replays
        # already recovered above remain visible, but no novel call executes.
        if preflight_invalid:
            invalid_results = [
                {
                    "tool": item.get("tool"),
                    "error_code": item.get("error_code"),
                    "detail": item.get("detail"),
                }
                for item in next_results
                if isinstance(item, dict) and item.get("ok") is False
            ]
            repeat_count = _record_rejected_decision(
                session, "TOOL_BATCH_VALIDATION_FAILED", invalid_results,
                objective_context={"invalid_calls": preflight_invalid},
            )
            _record_decision(
                session, "tool_preflight", "batch_rejected",
                reason=f"invalid={preflight_invalid};replayed={preflight_replays};repeat={repeat_count}",
            )
            if repeat_count >= 2:
                text = "A LLM repetiu o mesmo batch de ferramentas inválido sem alterar o estado objetivo."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="ADMINISTRATIVE_LOOP"), full)
            session.latest_tool_results = _preserve_source_for_retry(session.latest_tool_results, next_results)
            feedback = json.dumps({
                "code": "TOOL_BATCH_VALIDATION_FAILED",
                "invalid_calls": invalid_results,
                "replayed_calls": preflight_replays,
                "instruction": "No novel tool from an invalid batch was executed. Correct the rejected call using the canonical available_tools schema; do not retry legacy argument names.",
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        # Phase/contract validation precedes authority. Additional authority is
        # based only on genuinely novel physical work, never on replayed calls.
        physical_cost = len(novel_calls)
        budget_state = _tool_budget_state(session, config)
        remaining_tool_calls = max(0, budget_state["effective_limit"] - session.tool_calls)
        if physical_cost > remaining_tool_calls:
            granted_extension = _grant_committed_progress_extension(session, config)
            if granted_extension > 0:
                budget_state = _tool_budget_state(session, config)
                remaining_tool_calls = max(0, budget_state["effective_limit"] - session.tool_calls)

        if physical_cost > remaining_tool_calls:
            rejection_payload = [
                {"tool": item["tool"], "arguments": item["arguments"]}
                for item in novel_calls
            ]
            repeat_count = _record_rejected_decision(
                session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", rejection_payload,
                objective_context={
                    "tool_calls_used": int(session.tool_calls or 0),
                    "remaining_tool_calls": int(remaining_tool_calls),
                    "effective_tool_limit": int(budget_state["effective_limit"]),
                    "earned_extension": int(budget_state["earned_extension"]),
                },
            )
            _record_decision(
                session, "tool_authority", "batch_rejected",
                reason=f"requested={len(calls)};novel={physical_cost};authorized_now={remaining_tool_calls};effective={budget_state['effective_limit']};repeat={repeat_count}",
            )
            if repeat_count >= 2:
                text = "A LLM repetiu a mesma decisão rejeitada sem alterar o estado canônico."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="ADMINISTRATIVE_LOOP"), full)
            session.latest_tool_results = _preserve_source_for_retry(session.latest_tool_results, next_results)
            feedback = json.dumps({
                "code": "REPEATED_REJECTED_DECISION" if repeat_count > 1 else "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET",
                "requested": len(calls),
                "novel_physical_calls": physical_cost,
                "replayed_calls": preflight_replays,
                "invalid_calls": preflight_invalid,
                "max_novel_batch_size_now": remaining_tool_calls,
                "instruction": "No novel tool from this batch was executed. Replayed observations remain available. Valid Investigation progress remains deposited. Choose a smaller materially novel batch or conclude from retained Evidence.",
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        for item in novel_calls:
            tool = item["tool"]
            normalized = item["arguments"]
            semantic_signature = item["semantic_signature"]
            signature = item["action_signature"]
            projected_identical = session.consecutive_identical_calls + 1 if signature == session.last_tool_signature else 1
            if projected_identical > max_identical:
                text = "A LLM repetiu exatamente a mesma ferramenta executável várias vezes sem mudar a ação."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="CONSECUTIVE_ACTION_REPEAT_FUSE"), full)

            context = {
                "config": config, "projeto": project, "evidence": session.evidence,
                "execution_trace": _current_trace_snapshot(session, config),
                "available_tools": sorted(allowed),
            }
            result = executar_tool(tool, normalized, context)
            session.tool_calls += 1
            if result.get("executed") is True:
                if signature == session.last_tool_signature:
                    session.consecutive_identical_calls += 1
                else:
                    session.last_tool_signature = signature
                    session.consecutive_identical_calls = 1
            _record_tool_history(session, tool, normalized, result, semantic_signature=semantic_signature)
            if result.get("executed") is True:
                execution_outcome = "executed" if result.get("ok") is True else "failed"
            elif result.get("status") == "skipped":
                execution_outcome = "skipped"
            elif result.get("ok") is True:
                execution_outcome = "completed"
            else:
                execution_outcome = "failed"
            _record_decision(session, "tool_execution", execution_outcome, reason=result.get("error_code"), tools=[tool])
            model_result = _model_tool_result(session, tool, result, config, normalized)
            _record_observation(session, semantic_signature, tool, normalized, result, model_result)
            _remember_relevant_sources(session, tool, model_result, config)
            next_results.append(model_result)
            if not result.get("ok") and result.get("error_code") in TERMINAL_TOOL_ERRORS:
                text = f"A ferramenta encontrou um erro terminal: {result.get('error_code')}."
                return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code")), full)

        session.latest_tool_results = _preserve_source_for_retry(session.latest_tool_results, next_results)
        if session.phase.startswith("write") and any(
            isinstance(item, dict) and item.get("tool") in READ_TOOLS for item in next_results
        ):
            session.investigation_turns += 1

        # Runtime-cycle progress is finalized at the top of the next loop so
        # every path, including early ``continue`` branches, is accounted for.
        feedback = ""

    text = "A tarefa atingiu o limite de turnos do agente antes de concluir."
    return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_LLM_TURNS_EXCEEDED"), full)


def _rehydrate_persisted_evidence(
    session: AgentSession, project: Dict[str, Any], config: Dict[str, Any],
) -> None:
    """Restore persisted file Evidence from the live workspace after resume.

    Persistence intentionally omits source bodies. On resume the runtime can
    deterministically reconstruct them from path/range only when both stored
    hashes still match. If reconstruction fails or the file is stale, visible
    coverage for that path is released so the Main LLM may read it again.
    """
    root = (project or {}).get("caminho_origem")
    if not root or not os.path.isdir(root):
        return
    max_lines = max(1, int(((config or {}).get("agent") or {}).get("max_read_range_lines", 400) or 400))
    restored_sources: List[Dict[str, Any]] = []
    for evidence_id, item in list(session.evidence.items()):
        if not isinstance(item, dict):
            continue
        if item.get("conteudo") or item.get("trecho_numerado"):
            continue
        path = str(item.get("arquivo") or "").strip()
        start = item.get("linha_inicio")
        end = item.get("linha_fim")
        if not path or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            continue
        try:
            reading = ler_faixa_projeto(root, path, start, end, max_linhas=max(max_lines, end - start + 1))
        except ErroLeituraProjeto as error:
            item["rehydration_error"] = error.error_code
            session.visible_source_ranges.pop(_normalized_path(path), None)
            session.historically_seen_source_ranges.pop(_normalized_path(path), None)
            continue
        stored_file_hash = str(item.get("file_hash") or "")
        stored_content_hash = str(item.get("content_hash") or "")
        if (
            (stored_file_hash and stored_file_hash != str(reading.get("file_hash") or ""))
            or (stored_content_hash and stored_content_hash != str(reading.get("content_hash") or ""))
        ):
            item["rehydration_error"] = "EVIDENCE_STALE"
            item["stale"] = True
            session.visible_source_ranges.pop(_normalized_path(path), None)
            session.historically_seen_source_ranges.pop(_normalized_path(path), None)
            continue
        item.update(reading)
        item.pop("rehydration_error", None)
        item.pop("stale", None)
        clone = dict(item)
        clone["evidence_id"] = evidence_id
        source_view = _llm_source_view(clone, config, tool="read_range")
        restored_sources.append({
            "tool": "resume_rehydrate",
            "evidence_id": evidence_id,
            "arquivo": source_view.get("arquivo"),
            "linha_inicio": source_view.get("linha_inicio"),
            "linha_fim": source_view.get("linha_fim"),
            "file_hash": source_view.get("file_hash"),
            "content_hash": source_view.get("content_hash"),
            "trecho_numerado": source_view.get("trecho_numerado"),
            "source_preview_complete": source_view.get("source_preview_complete"),
        })

    if restored_sources:
        context_view = _context_view_config(config)
        by_id = {
            str(source.get("evidence_id") or ""): source
            for source in session.relevant_sources if isinstance(source, dict)
        }
        for source in restored_sources:
            by_id[str(source.get("evidence_id") or "")] = source
        session.relevant_sources = list(by_id.values())[-context_view["max_relevant_sources"]:]


def executar_agente(
    objetivo: str,
    config: Dict[str, Any],
    projeto: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None,
    retornar_detalhes: bool = False,
    task_id: Optional[str] = None,
    conversation_context: Any = None,
    resposta_usuario: Optional[str] = None,
):
    """Run or resume the single AgentSession."""
    full = bool(retornar_detalhes)
    project = projeto or {}
    if retomar:
        session = AgentSession.from_dict(retomar.get("estado") or {})
        _rehydrate_persisted_evidence(session, project, config)
        if retomar.get("continuation_kind") == "user_input":
            session.latest_tool_results = [{
                "tool": "user_response", "status": "success", "ok": True,
                "detail": str(resposta_usuario or ""),
            }]
            return _run(session, config, project, full, conversation_context=None)
        return _resume(session, retomar, config, project, full)
    session = AgentSession(str(objetivo or ""), task_id=task_id)
    _seed_runtime_failure_evidence(session, conversation_context)
    return _run(session, config, project, full, conversation_context=conversation_context)
