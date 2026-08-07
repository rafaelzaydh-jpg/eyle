"""Single-session LLM-first programming agent.

There is one reasoning loop. The LLM decides whether to answer, plan, use a
tool, ask a blocking question or propose a patch. The runtime only validates
and executes concrete actions.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from json import JSONDecoder
from typing import Any, Dict, Iterable, List, Optional, Tuple

from llm.executar import ErroLLM, PROMPT_AGENTE, executar_agente as executar_agente_llm

from .session import AgentSession
from .execution_trace import build_execution_trace
from .security import _resolver_caminho_seguro
from .token_budget import available_user_prompt_tokens, estimate_tokens
from .text_hash import extrair_faixa, hash_faixa, hash_texto
from .post_write import (
    expected_outputs_from_patches,
    run_compileall_for_changes,
    verify_expected_outputs,
)
from .tools import (
    executar_tool,
    gerar_catalogo_tools,
    gerar_taxonomia_tools,
    reverter_patch_confirmado,
    reverter_patch_set_confirmado,
    validar_chamada_tool,
)
from .validation import validate_final
from .response_quality import (
    claim_evidence_ledger, quality_contract, request_requires_write,
)

READ_TOOLS = {"list_tree", "search_code", "find_symbol", "read_range", "read_file"}
OBSERVATION_TOOLS = {"project_stats", "count_tokens", "inspect_project"}
UTILITY_TOOLS = {"calculate", "agent_info"}
GIT_TOOLS = {"git_status", "git_diff"}
EXECUTION_TOOLS = {"run_tests"}
TRACE_TOOLS = {"execution_trace"}
EVIDENCE_TOOLS = READ_TOOLS | OBSERVATION_TOOLS | GIT_TOOLS | EXECUTION_TOOLS | TRACE_TOOLS | {"calculate", "agent_info"}
MEMORY_TOOLS = {"memory_search", "memory_store"}
PATCH_TOOLS = {"test_patch_dry_run", "test_patch_set_dry_run"}
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
_TEST_ONLY_REQUEST = re.compile(
    r"^\s*(?:(?:execute|rode|rodar|faça|faca|run)\s+)?"
    r"(?:os\s+|the\s+)?(?:testes|tests|pytest|test suite|su[ií]te de testes)"
    r"(?:\s+(?:do|de|of)\s+(?:projeto|project|workspace))?"
    r"(?:\s+e\s+(?:explique|explain)\b[^.;]*)?[?!.\s]*$",
    re.I,
)


def _is_obvious_calculator_request(request: Any) -> bool:
    return bool(_OBVIOUS_CALCULATOR_REQUEST.fullmatch(str(request or "")))


def _is_obvious_agent_info_request(request: Any) -> bool:
    return bool(_OBVIOUS_AGENT_INFO_REQUEST.fullmatch(str(request or "")))


def _is_test_only_request(request: Any) -> bool:
    """Conservative optimization hint; never routes a compound investigation."""
    return bool(_TEST_ONLY_REQUEST.fullmatch(str(request or "")))



def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _json_candidates(text: str) -> Iterable[Dict[str, Any]]:
    decoder = JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    if text.startswith("```") and "\n" in text:
        body = text.split("\n", 1)[1]
        if body.rstrip().endswith("```"):
            body = body.rsplit("```", 1)[0]
        try:
            value = json.loads(body.strip())
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    candidates = list(_json_candidates(text))
    if not candidates:
        raise ValueError("no JSON object found")
    protocol = {"tool", "tool_call", "tool_calls", "actions", "patches", "needs_user", "final"}
    return next((item for item in candidates if protocol.intersection(item)), candidates[0])


def _normalize_action(action: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(action, dict):
        return None
    if isinstance(action.get("function"), dict):
        function = action["function"]
        action = {"tool": function.get("name"), "arguments": function.get("arguments") or {}}
    tool = action.get("tool") or action.get("name")
    arguments = action.get("arguments") or action.get("args") or action.get("input") or {}
    if not tool:
        return None
    return {"tool": str(tool), "arguments": arguments if isinstance(arguments, dict) else {}}


def _parse_decision(raw: Any) -> Dict[str, Any]:
    value = _json_object(raw)
    if isinstance(value.get("decision"), dict):
        value = value["decision"]
    plan = value.get("plan")
    if isinstance(plan, list):
        plan = [str(item).strip() for item in plan if str(item).strip()]
    else:
        plan = None
    if isinstance(value.get("tool_call"), dict):
        action = _normalize_action(value["tool_call"])
        if action:
            action["plan"] = plan
            return action
    calls = value.get("tool_calls") or value.get("actions")
    if isinstance(calls, list):
        normalized = [item for item in (_normalize_action(call) for call in calls) if item]
        return {"tool_calls": normalized, "plan": plan}
    if isinstance(value.get("patches"), list):
        return {
            "tool": "test_patch_set_dry_run",
            "arguments": {"patches": value["patches"]},
            "plan": plan,
        }
    if "tool" in value or "name" in value:
        action = _normalize_action(value)
        if action:
            action["plan"] = plan
            return action
    if "needs_user" in value:
        return {"needs_user": str(value.get("needs_user") or "").strip(), "plan": plan}
    if "final" in value:
        return {"final": value["final"], "plan": plan}
    raise ValueError("unsupported decision object")


def _trim_history(context: Any, token_budget: int, chars_per_token: int) -> Dict[str, Any]:
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    kept: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        cost = estimate_tokens(item, chars_per_token)
        if used + cost > max(0, token_budget):
            continue
        kept.append(item)
        used += cost
    kept.reverse()
    return {"recent_messages": kept, "omitted_messages": max(0, len(messages) - len(kept))}


def _build_context_anchor(
    context: Any, request: str, token_budget: int, chars_per_token: int,
) -> List[Dict[str, Any]]:
    """Keep a tiny stable task anchor across turns without replaying full chat."""
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    selected: List[Dict[str, Any]] = []
    used = 0
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        compact = {"role": role, "content": content.strip()[:1800]}
        if compact["content"] == str(request or "").strip():
            continue
        cost = estimate_tokens(compact, chars_per_token)
        if used + cost > max(0, token_budget):
            continue
        selected.append(compact)
        used += cost
        if len(selected) >= 4:
            break
    selected.reverse()
    return selected


def _project_descriptor(project: Dict[str, Any]) -> Dict[str, Any]:
    root = (project or {}).get("caminho_origem")
    return {
        "available": bool(root and os.path.isdir(root)),
        "name": os.path.basename(os.path.realpath(root)) if root else None,
    }


def _tests_enabled(config: Dict[str, Any]) -> bool:
    return bool((((config or {}).get("codar") or {}).get("testes") or {}).get("ativado", False))


def _response_quality_config(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = (((config or {}).get("agent") or {}).get("response_quality") or {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "max_relevant_sources": max(1, int(raw.get("max_relevant_sources", 4) or 4)),
        "max_relevant_source_chars": max(500, int(raw.get("max_relevant_source_chars", 8000) or 8000)),
        "reject_mid_list_corrections": bool(raw.get("reject_mid_list_corrections", True)),
    }


def _run_tests_closes_read_only_task(session: AgentSession) -> bool:
    latest_terminal_test = any(
        isinstance(item, dict)
        and item.get("tool") == "run_tests"
        and (
            item.get("executed") is True
            or item.get("error_code") in {"TEST_RUNNER_UNAVAILABLE", "TESTS_NOT_FOUND"}
        )
        for item in session.latest_tool_results
    )
    if not latest_terminal_test:
        return False
    if not _is_test_only_request(session.request):
        return False
    observed_tools = [
        str(item.get("tool") or "")
        for item in session.tool_history
        if isinstance(item, dict) and str(item.get("tool") or "") in EVIDENCE_TOOLS
    ]
    if any(name != "run_tests" for name in observed_tools):
        return False
    if len(session.plan) > 1:
        return False
    return True


def _phase_for_call(
    session: AgentSession, config: Dict[str, Any], project: Dict[str, Any],
) -> str:
    descriptor = _project_descriptor(project)
    if not descriptor["available"]:
        return "chat"

    agent_cfg = (config or {}).get("agent") or {}
    write_required = request_requires_write(session.request, True)
    max_investigation = max(1, int(agent_cfg.get("max_write_investigation_turns", 2) or 2))
    max_no_progress = max(1, int(agent_cfg.get("max_no_progress_turns", 2) or 2))
    max_turns = max(1, int(agent_cfg.get("max_llm_turns", 6) or 6))

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

    # Rev4.12.3 keeps only a cheap fast path for obvious conversation and
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
    if not session.evidence:
        return "analysis_investigate"
    # A test result closes tools only when execution state shows a narrow
    # test-only flow: run_tests was the sole project observation and the model
    # did not declare a multi-step plan. Compound investigations remain open.
    if _run_tests_closes_read_only_task(session):
        return "analysis_answer_only"
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
        return set(PATCH_TOOLS) if project_available and bool(((config or {}).get("codar") or {}).get("ativado", True)) else set()
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
    if phase == "write_prepare" and bool(((config or {}).get("codar") or {}).get("ativado", True)):
        names |= PATCH_TOOLS
    if _tests_enabled(config) and (phase.startswith("analysis") or phase == "write_investigate"):
        names.add("run_tests")
    return names


def _tool_catalog(
    config: Dict[str, Any], project: Dict[str, Any], phase: str, request: Any = "",
) -> Tuple[set[str], List[Dict[str, Any]]]:
    allowed = _allowed_tools(config, project, phase, request)
    catalog = gerar_catalogo_tools(
        config=config, allowed_names=allowed, compact=True, minimal=False,
    ) if allowed else []
    for item in catalog:
        if item.get("name") == "test_patch_set_dry_run":
            item["patch_contract"] = {
                "replace_existing": {"operation": "replace", "path": "app.py", "content": "complete new file"},
                "create": {"operation": "create", "path": "routes.py", "content": "complete file"},
                "delete": {"operation": "delete", "path": "old.py"},
                "range_update": {"operation": "update", "path": "app.py", "line_start": 1, "line_end": 3, "new_code": "replacement"},
            }
            item["note"] = (
                "Hashes are filled only from fresh evidence; read every existing file first. "
                "To remove a directory, delete each contained file in the same transaction; "
                "the runtime prunes empty parent directories after confirmation."
            )
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
        result = {"path": arguments.get("caminho_relativo")}
        if tool == "read_range":
            result.update({
                "line_start": arguments.get("linha_inicio"),
                "line_end": arguments.get("linha_fim"),
            })
        return {k: v for k, v in result.items() if v is not None}
    if tool == "list_tree":
        return {
            k: arguments.get(k) for k in ("limite", "profundidade", "filtro")
            if arguments.get(k) is not None
        }
    if tool == "search_code":
        return {"query": str(arguments.get("query") or "")[:240]}
    if tool == "find_symbol":
        return {
            k: arguments.get(k) for k in ("simbolo", "caminho_relativo")
            if arguments.get(k) is not None
        }
    if tool == "calculate":
        return {"expression": str(arguments.get("expression") or "")[:240]}
    if tool == "count_tokens":
        return {
            k: arguments.get(k) for k in ("caminho_relativo", "tokenizer")
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
    if tool in PATCH_TOOLS:
        patches = arguments.get("patches") if tool == "test_patch_set_dry_run" else [arguments]
        public = []
        for patch in patches or []:
            if not isinstance(patch, dict):
                continue
            public.append({
                key: patch.get(key)
                for key in ("operation", "path", "caminho_relativo", "line_start", "line_end", "linha_inicio", "linha_fim")
                if patch.get(key) is not None
            })
        return {"patches": public[:50]}
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
            "matches": len(detail.get("resultados") or []),
            "files": list(detail.get("arquivos_relevantes") or [])[:20],
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
        for key in ("file_count", "languages", "frameworks", "entrypoints", "test_frameworks", "has_tests", "has_ci"):
            if key in detail:
                public[key] = detail.get(key)
        for key in ("routes", "local_import_edges", "relation_hubs"):
            if isinstance(detail.get(key), list):
                public[f"{key}_count"] = len(detail.get(key) or [])
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
    elif tool in PATCH_TOOLS:
        patches = detail.get("prepared_patches") or detail.get("patches") or []
        public["patches"] = [
            {k: item.get(k) for k in ("operation", "path") if item.get(k) is not None}
            for item in patches[:50] if isinstance(item, dict)
        ]
        if detail.get("detail") is not None:
            public["detail"] = str(detail.get("detail"))[:500]
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


def _register_evidence(session: AgentSession, tool: str, detail: Any) -> List[str]:
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("resultados") or [] if isinstance(item, dict)]
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


def _model_tool_result(session: AgentSession, tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    evidence_worthy = bool(result.get("ok")) or (
        tool == "run_tests"
        and isinstance(result.get("detail"), dict)
        and (result.get("executed") is True or result.get("error_code") == "TEST_RUNNER_UNAVAILABLE")
    )
    evidence_ids = _register_evidence(session, tool, result.get("detail")) if evidence_worthy else []
    if tool in EVIDENCE_TOOLS and isinstance(result.get("detail"), dict):
        detail = result.get("detail")
        if tool == "search_code":
            copied = dict(detail)
            copied_results = []
            for item, evidence_id in zip(copied.get("resultados") or [], evidence_ids):
                clone = dict(item)
                clone["evidence_id"] = evidence_id
                copied_results.append(clone)
            copied["resultados"] = copied_results
            detail = copied
        elif evidence_ids:
            detail = dict(detail)
            detail["evidence_id"] = evidence_ids[0]
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
    quality = _response_quality_config(config)
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
        for key in ("conteudo", "trecho_numerado", "conteudo_raw"):
            value = item.get(key)
            if isinstance(value, str) and value:
                limit = quality["max_relevant_source_chars"]
                compact[key] = value if len(value) <= limit else value[:limit] + "\n...[source cropped]"
        session.relevant_sources = [
            source for source in session.relevant_sources
            if source.get("evidence_id") != evidence_id
        ]
        session.relevant_sources.append(compact)
    del session.relevant_sources[:-quality["max_relevant_sources"]]


def _retained_sources_for_prompt(session: AgentSession) -> List[Dict[str, Any]]:
    """Avoid sending the same raw source in latest results and retention."""
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
    return [
        source for source in session.relevant_sources
        if str(source.get("evidence_id") or "") not in latest_ids
    ]


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

    Rev4.12.4.1 keeps generic context compaction to arbitrary nested tool output.
    The prompt receives a deep-copied, bounded view; session evidence and public
    history keep the original runtime data.
    """
    while estimate_tokens(payload, chars_per_token) > budget:
        history = (payload.get("recent_context") or {}).get("recent_messages") or []
        if history:
            payload["recent_context"]["recent_messages"] = history[1:]
            payload["recent_context"]["omitted_messages"] = int(payload["recent_context"].get("omitted_messages", 0)) + 1
            continue

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
        if len(payload.get("evidence_index") or []) > 8:
            payload["evidence_index"] = payload["evidence_index"][-8:]
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
    """Resolve output reserve from work type, never from source volume.

    Context already becomes more valuable as evidence accumulates. Increasing
    completion reserve in proportion to source text would steal input budget at
    exactly the wrong time. Analysis therefore has a stable reserve, while
    patch-producing phases retain a larger bounded reserve.
    """
    clone = dict(config)
    llm = dict(config.get("llm") or {})
    phase = _phase_for_call(session, config, project)
    decision_limit = max(1, int(llm.get("agent_decision_max_tokens", 1100) or 1100))
    analysis_limit = max(1, int(llm.get("agent_analysis_max_tokens", 1800) or 1800))
    patch_limit = max(1, int(llm.get("agent_patch_max_tokens", 3600) or 3600))

    if phase in {"write_prepare", "write_patch_only", "write_patch_retry"}:
        reserve = patch_limit
    elif phase.startswith("analysis"):
        reserve = analysis_limit
    else:
        reserve = decision_limit
    llm["agent_max_tokens"] = reserve
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
        "tool_history": list(session.tool_history[-50:]),
        "decision_history": list(session.decision_history[-50:]),
        "llm_usage": {key: value for key, value in runtime.items() if key in {
            "llm_calls", "llm_requests", "prompt_tokens_actual", "prompt_tokens_cached",
            "prompt_tokens_uncached", "prompt_tokens_effective", "completion_tokens_actual",
            "generated_tokens", "reasoning_tokens_actual", "total_tokens_effective",
        }},
        "llm_responses": list(runtime.get("llm_responses") or []),
        "runtime_phase": session.phase,
        "prompt_snapshots": list(session.prompt_snapshots[-20:]),
        "phase_history": list(session.phase_history[-50:]),
        "parse_failures": session.parse_failures,
        "no_progress_turns": session.no_progress_turns,
        "phase_violations": session.phase_violations,
        "write_validation": dict(session.write_validation or {}),
    }
    return build_execution_trace(
        details,
        job_id=runtime.get("source_job_id"),
        status="processing",
        limit=100,
    )


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
    history = (
        _trim_history(conversation_context, history_budget, chars_per_token)
        if session.turn <= 1 else {"recent_messages": [], "omitted_messages": 0}
    )
    if not session.context_anchor:
        anchor_budget = int((call_config.get("agent") or {}).get("task_context_token_budget", 500) or 500)
        session.context_anchor = _build_context_anchor(
            conversation_context, session.request, anchor_budget, chars_per_token,
        )
    runtime = call_config.get("_runtime_agent_budget")
    if isinstance(runtime, dict):
        runtime["history_messages_omitted"] = history.get("omitted_messages", 0)
    phase = _phase_for_call(session, call_config, project)
    session.record_phase(phase, turn=session.turn, reason="phase_for_call")
    allowed, tools = _tool_catalog(call_config, project, phase, session.request)
    payload = {
        "request": session.request,
        "turn": session.turn,
        "plan": session.plan,
        "project": _project_descriptor(project),
        "runtime_phase": phase,
        "action_policy": _phase_policy(phase),
        "task_context": session.context_anchor,
        "recent_context": history,
        "latest_tool_results": session.latest_tool_results,
        "relevant_sources": _retained_sources_for_prompt(session),
        "evidence_index": session.evidence_index(),
        "response_quality": quality_contract(
            session.request, _project_descriptor(project)["available"],
            _response_quality_config(call_config)["enabled"],
            write_available=bool(((call_config or {}).get("codar") or {}).get("ativado", True)),
        ),
        "tool_taxonomy": gerar_taxonomia_tools(tools) if tools else {},
        "available_tools": tools,
        "runtime_feedback": feedback or None,
    }
    output_tokens = int((call_config.get("llm") or {}).get("agent_max_tokens", 1400) or 1400)
    prompt_budget = available_user_prompt_tokens(call_config, PROMPT_AGENTE, output_tokens=output_tokens)
    components_before = _trace_prompt_components(payload, chars_per_token)
    pre_crop = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    pre_crop_tokens = estimate_tokens(pre_crop, chars_per_token)
    payload = _crop_payload(copy.deepcopy(payload), prompt_budget, chars_per_token)
    components_after = _trace_prompt_components(payload, chars_per_token)
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    post_crop_tokens = estimate_tokens(prompt, chars_per_token)
    session.record_prompt(
        mode="agent", characters=len(prompt),
        estimated_tokens=post_crop_tokens, tool_count=len(tools),
        phase=phase, turn=session.turn,
        metadata={
            "prompt_budget_tokens": prompt_budget,
            "output_tokens_reserved": output_tokens,
            "system_prompt_characters": len(PROMPT_AGENTE),
            "system_prompt_estimated_tokens": estimate_tokens(PROMPT_AGENTE, chars_per_token),
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
    raw = executar_agente_llm(prompt, call_config)
    return _parse_decision(raw), allowed


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
    )
    return {
        "status": status,
        "plan": session.plan,
        "turns": session.turn,
        "tool_calls": session.tool_calls,
        "tools_used": [
            item.get("tool") for item in session.tool_history
            if (item.get("result") or {}).get("executed") is True
            or ("result" not in item and item.get("status") == "success")
        ],
        "tool_history": list(session.tool_history[-50:]),
        "decision_history": list(session.decision_history[-50:]),
        "evidence": session.evidence_index(),
        "claim_evidence": claim_evidence_ledger(session.final_claims, session.evidence),
        "limitations": list(limitations or []),
        "failure_code": failure_code,
        "write_failure": dict(write_failure or {}) if write_failure else None,
        "llm_usage": {key: runtime.get(key, 0) for key in usage_keys},
        "llm_responses": list(runtime.get("llm_responses") or []),
        "parse_failures": session.parse_failures,
        "runtime_phase": session.phase,
        "no_progress_turns": session.no_progress_turns,
        "phase_violations": session.phase_violations,
        "prompt_snapshots": session.prompt_snapshots,
        "phase_history": list(session.phase_history[-50:]),
        "write_validation": dict(session.write_validation or {}),
    }


def _find_patch_evidence(session: AgentSession, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for item in reversed(list(session.evidence.values())):
        if item.get("arquivo") != args.get("caminho_relativo"):
            continue
        if item.get("file_hash") != args.get("file_hash_esperado"):
            continue
        if (
            item.get("linha_inicio") == args.get("linha_inicio")
            and item.get("linha_fim") == args.get("linha_fim")
            and item.get("content_hash") == args.get("range_hash_esperado")
        ):
            return item
        content = item.get("conteudo")
        whole_file = (
            isinstance(content, str)
            and int(item.get("linha_inicio") or 0) == 1
            and int(item.get("linha_fim") or 0) == int(item.get("total_linhas_arquivo") or -1)
        )
        if not whole_file:
            continue
        start = int(args.get("linha_inicio") or 0)
        end = int(args.get("linha_fim") or 0)
        derived_content = extrair_faixa(content, start, end)
        if derived_content is None or hash_faixa(content, start, end) != args.get("range_hash_esperado"):
            continue
        derived = dict(item)
        derived.update({
            "linha_inicio": start, "linha_fim": end,
            "conteudo": derived_content, "content_hash": args.get("range_hash_esperado"),
        })
        return derived
    return None


def _pending_single_patch(session: AgentSession, args: Dict[str, Any], evidence: Dict[str, Any]):
    original = str(evidence.get("conteudo") or "")
    if original.endswith("\r\n"):
        original = original[:-2]
    elif original.endswith(("\n", "\r")):
        original = original[:-1]
    apply_args = {
        "caminho_relativo": args["caminho_relativo"],
        "linha_inicio": args["linha_inicio"],
        "linha_fim": args["linha_fim"],
        "codigo_original_esperado": original,
        "codigo_novo": args.get("codigo_novo", ""),
        "file_hash_esperado": args["file_hash_esperado"],
        "range_hash_esperado": args["range_hash_esperado"],
    }
    text = (
        f"Proposta pronta para confirmação: {apply_args['caminho_relativo']}:"
        f"{apply_args['linha_inicio']}-{apply_args['linha_fim']}. Dry-run aprovado. "
        "A aplicação exige confirmação do usuário."
    )
    state = session.to_dict()
    state["relevant_sources"] = []
    pending = {
        "continuation_kind": "write_confirmation",
        "pergunta_ao_usuario": text,
        "estado": state,
        "tool_pendente": {"tool": "apply_patch", "arguments": apply_args},
    }
    return text, pending


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
        "tool_pendente": {"tool": "apply_patch_set", "arguments": {"patches": patches}},
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
        result = executar_tool("read_file", {"caminho_relativo": path}, context)
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


def _resume_single(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": session.evidence}
    args, error = validar_chamada_tool("apply_patch", (pending.get("tool_pendente") or {}).get("arguments") or {})
    if error:
        text = "A proposta confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    applied = executar_tool("apply_patch", args, context)
    single_paths = [str(args.get("caminho_relativo") or "")]
    session.write_validation = {"apply": _validation_step(applied, paths=single_paths)}
    if not applied.get("ok"):
        code = applied.get("error_code") or "PATCH_FAILED"
        diagnostic = _diagnostic_text(applied)
        report = {
            "stage": "apply",
            "error_code": code,
            "executed": bool(applied.get("executed")),
            "detail": diagnostic,
            "rollback_confirmed": None,
            "rollback_error_code": None,
            "paths": [args.get("caminho_relativo")],
        }
        text = f"A alteração confirmada não foi aplicada: {code}.\n\nErro real da tentativa:\n{diagnostic}"
        return _return("failed", text, None, _details(
            session, "failed", config, failure_code=code, write_failure=report,
        ), full)

    detail = applied.get("detail") if isinstance(applied.get("detail"), dict) else {}
    snapshot = detail.get("rollback_snapshot")
    compile_result = _compile_after_write(config, project, [args["caminho_relativo"]])
    session.write_validation["compileall"] = _validation_step(compile_result, paths=single_paths)
    if compile_result.get("ok") is not True:
        rollback = reverter_patch_confirmado(snapshot, context) if snapshot else {"ok": False}
        _record_rollback(session, rollback, single_paths)
        text, suffix, report = _write_failure_response(
            "compileall falhou após a escrita.", "compileall", compile_result, rollback,
            "O arquivo foi restaurado automaticamente.", [args["caminho_relativo"]],
        )
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{compile_result.get('error_code') or 'COMPILEALL_FAILED'}_{suffix}",
            limitations=[str(compile_result.get("detail") or "compileall falhou")],
            write_failure=report,
        ), full)

    tests = _run_tests_after_write(config, context)
    session.write_validation["tests"] = _validation_step(tests, paths=single_paths)
    if tests.get("ok") is not True:
        rollback = reverter_patch_confirmado(snapshot, context) if snapshot else {"ok": False}
        _record_rollback(session, rollback, single_paths)
        text, suffix, report = _write_failure_response(
            "A verificação por testes falhou após a escrita.", "tests", tests, rollback,
            "O arquivo foi restaurado automaticamente.", [args["caminho_relativo"]],
        )
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{tests.get('error_code') or 'TESTS_FAILED'}_{suffix}",
            limitations=[str(tests.get("detail") or "testes falharam")],
            write_failure=report,
        ), full)

    expected_outputs = [{
        "path": args["caminho_relativo"],
        "operation": "update",
        "expected_hash": detail.get("file_hash_depois"),
    }]
    tool_reread = _reread_with_tools(context, expected_outputs)
    reread = verify_expected_outputs(project.get("caminho_origem"), expected_outputs)
    session.write_validation["tool_reread"] = _validation_step(tool_reread, paths=single_paths)
    session.write_validation["full_reread"] = _validation_step(reread, paths=single_paths)
    if not tool_reread.get("ok") or not reread.get("ok"):
        rollback = reverter_patch_confirmado(snapshot, context) if snapshot else {"ok": False}
        _record_rollback(session, rollback, single_paths)
        reread_failure = tool_reread if not tool_reread.get("ok") else reread
        reread_failure = dict(reread_failure)
        reread_failure.setdefault("error_code", "POST_WRITE_READ_FAILED")
        text, suffix, report = _write_failure_response(
            "A releitura integral obrigatória falhou após a escrita.", "reread", reread_failure, rollback,
            "O arquivo foi restaurado automaticamente.", [args["caminho_relativo"]],
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
    state_line = (
        "Estado: alteração verificada após escrita."
        if fully_verified else
        "Estado: alteração aplicada com validação parcial; não foi chamada de verificada."
    )
    text = (
        f"Alteração aplicada em {args['caminho_relativo']}.\n\nValidação pós-escrita:\n"
        f"- {compile_line};\n- {test_line};\n"
        f"- releitura pela ferramenta concluída;\n"
        f"- arquivo inteiro relido e hash final confirmado.\n- {state_line}"
    )
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume_set(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": session.evidence}
    args, error = validar_chamada_tool("apply_patch_set", (pending.get("tool_pendente") or {}).get("arguments") or {})
    if error:
        text = "A transação confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    applied = executar_tool("apply_patch_set", args, context)
    attempted_paths = [str(item.get("path") or "") for item in args.get("patches") or [] if isinstance(item, dict)]
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
        rollback = reverter_patch_set_confirmado(applied_patches, context)
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
        rollback = reverter_patch_set_confirmado(applied_patches, context)
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
        rollback = reverter_patch_set_confirmado(applied_patches, context)
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
    text = (
        f"Transação aplicada em {len(paths)} arquivo(s): {', '.join(paths)}.\n\nValidação pós-escrita:\n"
        f"- {compile_line};\n- {test_line};\n"
        f"- releitura de todos os arquivos pela ferramenta concluída;\n"
        f"- todos os arquivos alterados foram relidos integralmente;\n"
        f"- {creation_line};\n- exclusões prometidas foram confirmadas;\n- {state_line}"
    )
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    tool = (pending.get("tool_pendente") or {}).get("tool")
    if tool == "apply_patch_set":
        return _resume_set(session, pending, config, project, full)
    return _resume_single(session, pending, config, project, full)



def _freshest_evidence_for_path(session: AgentSession, path: str) -> Optional[Dict[str, Any]]:
    normalized = str(path or "").replace("\\", "/")
    for item in reversed(list(session.evidence.values())):
        if str(item.get("arquivo") or "").replace("\\", "/") == normalized:
            return item
    return None



def _enrich_single_patch(session: AgentSession, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    patch = dict(arguments or {})
    aliases = {
        "path": "caminho_relativo", "file": "caminho_relativo",
        "line_start": "linha_inicio", "line_end": "linha_fim",
        "new_code": "codigo_novo", "content": "codigo_novo",
        "file_hash_expected": "file_hash_esperado",
        "range_hash_expected": "range_hash_esperado",
    }
    for source, target in aliases.items():
        if target not in patch and source in patch:
            patch[target] = patch[source]
        if source != target:
            patch.pop(source, None)
    path = patch.get("caminho_relativo")
    try:
        start, end = int(patch.get("linha_inicio")), int(patch.get("linha_fim"))
    except (TypeError, ValueError):
        return arguments, "range patch needs linha_inicio and linha_fim"
    if not isinstance(path, str) or not path.strip():
        return arguments, "range patch needs caminho_relativo"
    evidence = _freshest_evidence_for_path(session, path)
    if not evidence or not evidence.get("file_hash"):
        return arguments, f"read the file before updating: {path}"
    patch.setdefault("file_hash_esperado", evidence["file_hash"])
    if not patch.get("range_hash_esperado"):
        if int(evidence.get("linha_inicio") or 0) == start and int(evidence.get("linha_fim") or 0) == end:
            patch["range_hash_esperado"] = evidence.get("content_hash")
        else:
            content = evidence.get("conteudo")
            whole_file = (
                isinstance(content, str)
                and int(evidence.get("linha_inicio") or 0) == 1
                and int(evidence.get("linha_fim") or 0) == int(evidence.get("total_linhas_arquivo") or -1)
            )
            if whole_file:
                patch["range_hash_esperado"] = hash_faixa(content, start, end)
        if not patch.get("range_hash_esperado"):
            return arguments, f"read the exact range before updating {path}:{start}-{end}"
    return patch, None

def _enrich_patch_set(session: AgentSession, project: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    raw_patches = arguments.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        return arguments, "patches must be a non-empty list"
    root = project.get("caminho_origem")
    enriched: List[Dict[str, Any]] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            return arguments, "each patch must be an object"
        patch = dict(raw)
        path = patch.get("path") or patch.get("caminho_relativo") or patch.get("file")
        if not isinstance(path, str) or not path.strip():
            return arguments, "each patch needs path"
        path = path.strip().replace("\\", "/")
        patch["path"] = path
        absolute = _resolver_caminho_seguro(root, path) if root else None
        if absolute is None:
            return arguments, f"unsafe patch path: {path}"
        exists = os.path.isfile(absolute)
        operation = str(patch.get("operation") or patch.get("operacao") or "").strip().lower()
        has_range = any(key in patch for key in ("line_start", "linha_inicio", "line_end", "linha_fim"))
        has_content = any(key in patch for key in ("content", "conteudo", "new_code", "codigo_novo"))
        if not operation:
            operation = "replace" if exists and has_content and not has_range else "create" if not exists and has_content else "update"
        aliases = {"add": "create", "remove": "delete"}
        operation = aliases.get(operation, operation)
        if operation in {"write", "overwrite"}:
            operation = "replace"
        if operation == "modify":
            operation = "replace" if has_content and not has_range else "update"
        if operation == "update" and has_content and not has_range:
            operation = "replace"
        if operation in {"replace", "create", "update"} and not has_content:
            return arguments, f"{operation} needs an explicit content/new_code field: {path}"
        patch["operation"] = operation
        evidence = _freshest_evidence_for_path(session, path)
        if operation in {"replace", "delete"}:
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
            patch.setdefault("file_hash_expected", evidence["file_hash"])
        elif operation == "update":
            start = patch.get("line_start", patch.get("linha_inicio"))
            end = patch.get("line_end", patch.get("linha_fim"))
            try:
                start, end = int(start), int(end)
            except (TypeError, ValueError):
                return arguments, f"range update needs line_start and line_end: {path}"
            patch["line_start"], patch["line_end"] = start, end
            if not evidence or not evidence.get("file_hash"):
                return arguments, f"read the file before updating: {path}"
            patch.setdefault("file_hash_expected", evidence["file_hash"])
            if not patch.get("range_hash_expected"):
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
        elif operation == "create":
            if exists:
                return arguments, f"create cannot overwrite an existing file: {path}; use replace"
        enriched.append(patch)
    return {"patches": enriched}, None


def _preserve_source_for_retry(previous: List[Dict[str, Any]], current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    needs_source = any(
        (item.get("tool") in PATCH_TOOLS and item.get("ok") is False)
        or item.get("error_code") in {"IDENTICAL_READ_BLOCKED", "SEMANTIC_READ_BLOCKED", "READ_PHASE_CLOSED"}
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


def _semantic_read_signature(tool: str, arguments: Dict[str, Any]) -> Optional[str]:
    if tool == "list_tree":
        return "tree:" + json.dumps({
            "filtro": str(arguments.get("filtro") or "").strip().lower(),
            "profundidade": arguments.get("profundidade"),
            "limite": arguments.get("limite"),
        }, sort_keys=True, separators=(",", ":"), default=str)
    if tool == "search_code":
        query = " ".join(str(arguments.get("query") or "").lower().split())
        return f"search:{query}"
    if tool == "find_symbol":
        path = _normalized_path(arguments.get("caminho_relativo"))
        symbol = str(arguments.get("simbolo") or "").strip().lower()
        return f"symbol:{path}:{symbol}"
    if tool == "read_file":
        return f"file:{_normalized_path(arguments.get('caminho_relativo'))}:all"
    if tool == "read_range":
        path = _normalized_path(arguments.get("caminho_relativo"))
        return f"file:{path}:{arguments.get('linha_inicio')}:{arguments.get('linha_fim')}"
    if tool == "project_stats":
        return "project_stats:root"
    if tool == "inspect_project":
        return "inspect_project:root"
    if tool == "count_tokens":
        return "count_tokens:" + json.dumps({
            "path": _normalized_path(arguments.get("caminho_relativo") or "."),
            "tokenizer": str(arguments.get("tokenizer") or "").strip().lower(),
        }, sort_keys=True, separators=(",", ":"))
    if tool == "agent_info":
        return "agent_info:runtime"
    return None


def _read_already_covered(
    session: AgentSession, tool: str, arguments: Dict[str, Any],
) -> bool:
    signature = _semantic_read_signature(tool, arguments)
    if not signature:
        return False
    if tool in {"list_tree", "search_code", "find_symbol"} | OBSERVATION_TOOLS | {"agent_info"}:
        return any(
            item.get("semantic_signature") == signature and item.get("status") == "success"
            for item in session.tool_history
            if isinstance(item, dict)
        )

    path = _normalized_path(arguments.get("caminho_relativo"))
    if not path:
        return False
    requested_start = 1 if tool == "read_file" else int(arguments.get("linha_inicio") or 0)
    requested_end = None if tool == "read_file" else int(arguments.get("linha_fim") or 0)
    for item in session.evidence.values():
        if not isinstance(item, dict) or _normalized_path(item.get("arquivo")) != path:
            continue
        start = int(item.get("linha_inicio") or 0)
        end = int(item.get("linha_fim") or 0)
        total = int(item.get("total_linhas_arquivo") or 0)
        whole = start == 1 and total > 0 and end >= total
        if tool == "read_file" and whole:
            return True
        if tool == "read_range" and start <= requested_start and end >= int(requested_end or 0):
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


def _turn_made_progress(
    evidence_before: int, results: List[Dict[str, Any]], session: AgentSession,
) -> bool:
    if len(session.evidence) > evidence_before:
        return True
    return any(
        isinstance(item, dict)
        and item.get("ok") is True
        and item.get("tool") not in READ_TOOLS
        for item in results
    )


def _action_signature(tool: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _final_validation_feedback(reason: str) -> str:
    if str(reason).startswith((
        "FINAL_CLAIM_SENTENCE_INVALID:",
        "FINAL_CLAIM_SENTENCE_OUT_OF_RANGE:",
        "FINAL_CLAIM_REFERENCE_REQUIRED:",
    )):
        return (
            f"FINAL_VALIDATION_ERROR: {reason}. In each claim use sentence as the "
            "1-based index of a non-heading sentence already present in final.answer."
        )
    if str(reason).startswith("FINAL_CLAIM_NOT_IN_ANSWER:"):
        return (
            f"FINAL_VALIDATION_ERROR: {reason}. Prefer sentence indexes. Legacy "
            "claims[].text must match one sentence already present in final.answer."
        )
    if str(reason).startswith("FINAL_CLAIM_REQUIRES_EVIDENCE:"):
        return (
            f"FINAL_VALIDATION_ERROR: {reason}. Attach real evidence_ids to every "
            "fact, bug, and risk claim; recommendations may remain without evidence."
        )
    return f"FINAL_VALIDATION_ERROR: {reason}. Return a corrected final answer."


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
    max_tool_calls = max(1, int(cfg.get("max_tool_calls", 16) or 16))
    max_identical = max(1, int(cfg.get("max_identical_tool_repeats", 2) or 2))
    parse_retries = max(0, int(cfg.get("protocol_parse_retries", 1) or 1))
    final_retries = max(0, int(cfg.get("final_validation_retries", 1) or 1))
    max_patch_failures = max(1, int(cfg.get("max_patch_dry_run_failures", 2) or 2))
    max_no_progress = max(1, int(cfg.get("max_no_progress_turns", 2) or 2))
    max_phase_violations = max(0, int(cfg.get("max_phase_violations", 1) or 1))
    feedback = ""
    final_failures = 0

    while session.turn < max_turns:
        if _deadline_exceeded(config):
            text = "A tarefa excedeu o prazo de execução."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="TASK_DEADLINE_EXCEEDED"), full)
        session.turn += 1
        evidence_before = len(session.evidence)
        try:
            decision, allowed = _call_agent(session, config, project, conversation_context, feedback)
            session.parse_failures = 0
        except ErroLLM as error:
            text = f"A chamada LLM falhou: {error.error_code or 'LLM_FAILED'}."
            return _return("failed", text, None, _details(session, "failed", config, limitations=[str(error)], failure_code=error.error_code or "LLM_FAILED"), full)
        except Exception as error:
            session.parse_failures += 1
            _record_decision(session, "protocol", "rejected", reason=f"PROTOCOL_ERROR:{type(error).__name__}")
            if session.parse_failures <= parse_retries:
                feedback = f"PROTOCOL_ERROR: {error}. Return exactly one valid JSON decision."
                continue
            text = "A LLM não produziu uma decisão estruturada válida."
            return _return("failed", text, None, _details(session, "failed", config, limitations=[str(error)], failure_code="AGENT_JSON_INVALID"), full)

        if isinstance(decision.get("plan"), list):
            session.plan = decision["plan"][:20]

        if decision.get("needs_user"):
            _record_decision(session, "needs_user", "accepted")
            text = str(decision["needs_user"])
            pending = {
                "continuation_kind": "user_input",
                "pergunta_ao_usuario": text,
                "estado": session.to_dict(),
                "tool_pendente": {"tool": "__user_response__", "arguments": {}},
            }
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if "final" in decision:
            quality = _response_quality_config(config)
            project_available = _project_descriptor(project)["available"]
            write_required = request_requires_write(session.request, project_available)
            write_available = bool(((config or {}).get("codar") or {}).get("ativado", True))
            if write_required and write_available:
                ok = False
                reason = "FINAL_WRITE_ACTION_REQUIRED"
                answer, limitations, claims = "", [], []
            else:
                ok, reason, answer, limitations, claims, _finding_limit = validate_final(
                    decision["final"], session.evidence,
                    request=session.request,
                    project_available=project_available,
                    quality_enabled=quality["enabled"],
                    reject_mid_list_corrections=quality["reject_mid_list_corrections"],
                )
            if ok:
                _record_decision(session, "final", "accepted")
                session.final_claims = claims
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
            feedback = "Choose one available tool, ask a blocking question, or return final."
            continue

        _record_decision(
            session,
            "tool_calls" if len(calls) > 1 else "tool",
            "requested",
            tools=[str(call.get("tool") or "") for call in calls],
        )
        next_results: List[Dict[str, Any]] = []
        for call in calls[:4]:
            if session.tool_calls >= max_tool_calls:
                text = "A tarefa atingiu o limite de ferramentas antes de concluir."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_TOOL_CALLS_EXCEEDED"), full)
            tool = str(call.get("tool") or "")
            arguments = call.get("arguments") or {}
            if tool not in allowed:
                phase_error = None
                phase_detail = "A ferramenta não está disponível neste workspace/configuração."
                if tool in PATCH_TOOLS and session.phase == "write_investigate":
                    phase_error = "WRITE_REQUIRES_SOURCE_READ"
                    phase_detail = "Leia os arquivos existentes necessários antes de propor a transação."
                elif tool in READ_TOOLS and session.phase in {"write_patch_only", "write_patch_retry"}:
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
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=rejected["error_code"], tools=[tool])
                _record_tool_history(session, tool, arguments, rejected, status_override="rejected")
                if (
                    phase_error in {"READ_PHASE_CLOSED", "FINAL_PHASE_REQUIRES_ANSWER"}
                    and session.phase_violations > max_phase_violations
                ):
                    text = "A LLM continuou tentando investigar depois que a fase de leitura foi encerrada."
                    return _return(
                        "failed", text, None,
                        _details(session, "failed", config, failure_code=phase_error), full,
                    )
                continue
            if tool == "test_patch_dry_run":
                arguments, patch_error = _enrich_single_patch(session, arguments)
                if patch_error:
                    session.patch_failures += 1
                    rejected = {
                        "tool": tool, "status": "failed", "ok": False,
                        "executed": False, "changed": False,
                        "error_code": "PATCH_SCHEMA_INVALID", "detail": patch_error,
                    }
                    next_results.append(rejected)
                    _record_decision(session, "tool_validation", "rejected", reason="PATCH_SCHEMA_INVALID", tools=[tool])
                    _record_tool_history(session, tool, arguments, rejected, status_override="rejected")
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {patch_error}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                    continue
            if tool == "test_patch_set_dry_run":
                arguments, patch_error = _enrich_patch_set(session, project, arguments)
                if patch_error:
                    session.patch_failures += 1
                    rejected = {
                        "tool": tool, "status": "failed", "ok": False,
                        "executed": False, "changed": False,
                        "error_code": "PATCH_SCHEMA_INVALID", "detail": patch_error,
                    }
                    next_results.append(rejected)
                    _record_decision(session, "tool_validation", "rejected", reason="PATCH_SCHEMA_INVALID", tools=[tool])
                    _record_tool_history(session, tool, arguments, rejected, status_override="rejected")
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {patch_error}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                    continue
            normalized, error = validar_chamada_tool(tool, arguments)
            if error:
                rejected = _compact_non_read_result(tool, error)
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=error.get("error_code") or "INVALID_ARGUMENT", tools=[tool])
                _record_tool_history(session, tool, arguments, error, status_override="rejected")
                if tool in PATCH_TOOLS:
                    session.patch_failures += 1
                    if session.patch_failures >= max_patch_failures:
                        text = f"A proposta de escrita continuou inválida após {session.patch_failures} tentativa(s): {error.get('detail')}."
                        return _return("failed", text, None, _details(session, "failed", config, failure_code=error.get("error_code") or "PATCH_SCHEMA_INVALID"), full)
                continue
            _record_decision(session, "tool_validation", "validated", tools=[tool])
            semantic_signature = _semantic_read_signature(tool, normalized)
            signature = _action_signature(tool, normalized)
            if signature == session.last_tool_signature:
                session.consecutive_identical_calls += 1
            else:
                session.last_tool_signature = signature
                session.consecutive_identical_calls = 1
            if session.consecutive_identical_calls > max_identical:
                text = "A LLM repetiu exatamente a mesma ferramenta várias vezes sem mudar a ação."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="IDENTICAL_TOOL_LOOP"), full)
            if tool in READ_TOOLS and session.consecutive_identical_calls > 1:
                blocked = {
                    "tool": tool, "status": "skipped", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": "IDENTICAL_READ_BLOCKED",
                    "detail": "A mesma leitura fresca já está disponível. Use o resultado atual, conclua ou escolha outra faixa/arquivo.",
                }
                next_results.append(blocked)
                _record_decision(session, "tool_execution", "skipped", reason="IDENTICAL_READ_BLOCKED", tools=[tool])
                _record_tool_history(session, tool, normalized, blocked, semantic_signature=semantic_signature)
                continue
            if tool in READ_TOOLS and _read_already_covered(session, tool, normalized):
                blocked = {
                    "tool": tool, "status": "skipped", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": "SEMANTIC_READ_BLOCKED",
                    "detail": "Essa fonte ou faixa já está coberta por evidência fresca. Use o conteúdo atual e avance.",
                }
                next_results.append(blocked)
                _record_decision(session, "tool_execution", "skipped", reason="SEMANTIC_READ_BLOCKED", tools=[tool])
                _record_tool_history(session, tool, normalized, blocked, semantic_signature=semantic_signature)
                continue

            context = {
                "config": config, "projeto": project, "evidence": session.evidence,
                "execution_trace": _current_trace_snapshot(session, config),
                "available_tools": sorted(allowed),
            }
            result = executar_tool(tool, normalized, context)
            session.tool_calls += 1
            _record_tool_history(
                session, tool, normalized, result, semantic_signature=semantic_signature,
            )
            if result.get("executed") is True:
                execution_outcome = "executed" if result.get("ok") is True else "failed"
            elif result.get("status") == "skipped":
                execution_outcome = "skipped"
            elif result.get("ok") is True:
                execution_outcome = "completed"
            else:
                execution_outcome = "failed"
            _record_decision(
                session, "tool_execution", execution_outcome,
                reason=result.get("error_code"), tools=[tool],
            )
            model_result = _model_tool_result(session, tool, result)
            _remember_relevant_sources(session, tool, model_result, config)
            next_results.append(model_result)
            if tool in PATCH_TOOLS and result.get("ok") is not True:
                session.patch_failures += 1
                if session.patch_failures >= max_patch_failures:
                    detail = result.get("detail")
                    text = f"O dry-run da escrita falhou {session.patch_failures} vez(es): {result.get('error_code') or 'DRY_RUN_FAILED'} — {detail}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code") or "DRY_RUN_FAILED"), full)

            if tool == "test_patch_dry_run" and result.get("ok"):
                evidence = _find_patch_evidence(session, normalized)
                if evidence is None:
                    next_results.append({"tool": tool, "status": "failed", "error_code": "PATCH_REQUIRES_FRESH_EXACT_READ"})
                    continue
                text, pending = _pending_single_patch(session, normalized, evidence)
                return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

            if tool == "test_patch_set_dry_run" and result.get("ok"):
                detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
                text, pending = _pending_patch_set(session, detail)
                return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

            if not result.get("ok") and result.get("error_code") in TERMINAL_TOOL_ERRORS:
                text = f"A ferramenta encontrou um erro terminal: {result.get('error_code')}."
                return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code")), full)

        session.latest_tool_results = _preserve_source_for_retry(session.latest_tool_results, next_results)
        if session.phase.startswith("write") and any(
            isinstance(item, dict) and item.get("tool") in READ_TOOLS for item in next_results
        ):
            session.investigation_turns += 1

        if _turn_made_progress(evidence_before, next_results, session):
            session.no_progress_turns = 0
            feedback = ""
        else:
            session.no_progress_turns += 1
            if session.no_progress_turns >= max_no_progress:
                if session.evidence and request_requires_write(session.request, _project_descriptor(project)["available"]):
                    feedback = (
                        "NO_PROGRESS_WRITE: investigation is closed. Use retained evidence and "
                        "produce one transactional patch now."
                    )
                elif session.evidence:
                    feedback = "NO_PROGRESS_ANALYSIS: stop using tools and answer from current evidence."
                else:
                    text = "A tarefa não produziu evidência nem progresso após tentativas consecutivas."
                    return _return(
                        "failed", text, None,
                        _details(session, "failed", config, failure_code="AGENT_NO_PROGRESS"), full,
                    )
            else:
                feedback = "The last action added no new evidence. Do not repeat it; advance to the next phase."

    text = "A tarefa atingiu o limite de turnos do agente antes de concluir."
    return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_LLM_TURNS_EXCEEDED"), full)


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
        pending_tool = (retomar.get("tool_pendente") or {}).get("tool")
        if pending_tool == "__user_response__":
            session.latest_tool_results = [{
                "tool": "user_response", "status": "success", "ok": True,
                "detail": str(resposta_usuario or ""),
            }]
            return _run(session, config, project, full, conversation_context=None)
        return _resume(session, retomar, config, project, full)
    session = AgentSession(str(objetivo or ""), task_id=task_id)
    _seed_runtime_failure_evidence(session, conversation_context)
    return _run(session, config, project, full, conversation_context=conversation_context)
