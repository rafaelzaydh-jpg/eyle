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

from .session import AgentSession
from .execution_context import ExecutionContext, bind_execution, reset_execution, current_execution
from .decision import (
    record as _decision_record, record_rejection as _decision_record_rejection,
    history_view as _decision_history_view, repeated_rejection_count as _repeated_rejection_count,
    requested_tool_names as _requested_tool_names,
)
from .evidence import items as _evidence_items, register_candidates as _evidence_register, register_tool_detail as _register_evidence, freshest_for_path as _evidence_freshest_for_path, rehydrate as _rehydrate_evidence, seed_runtime_failure as _seed_failure_evidence
from .write_transaction import begin as _begin_write_transaction, set_status as _set_write_status, record_validation as _record_write_validation, increment_attempt as _increment_write_attempt, record_failure as _record_write_failure, clear_failure as _clear_write_failure, public_view as _write_transaction_view
from .observation import (
    semantic_signature as _observation_signature, lookup as _lookup_observation,
    lookup_covering as _lookup_covering_observation, record as _record_observation,
    record_replay as _record_observation_replay, navigation_view as _observation_map,
    pending_results as _pending_observation_results, set_pending_results as _set_pending_observation_results,
    clear_pending_results as _clear_pending_observation_results, event_history as _tool_history_view,
    physical_tool_calls as _physical_tool_calls, replay_count as _observation_replay_count,
)
from .investigation import (
    apply_investigation_updates, open_target_ids, reopen_targets_from_review, target_evidence_ids,
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
    TOOLS, executar_tool,
    gerar_catalogo_tools, gerar_indice_capabilities,
    validar_chamada_tool,
)
from .transactions import dry_run_patch_set, apply_patch_set, rollback_patch_set
from .validation import validate_final
from .claim_review import (
    claim_config, claim_evidence_ledger, compact_evidence,
    claim_review_output_budget,
    review_followup_feedback, normalize_claim_review, review_prompt,
    validate_file_evidence_freshness, build_answer_anchors, compact_runtime_facts,
)

TERMINAL_TOOL_ERRORS = {"UNSAFE_PATH", "PATH_OUTSIDE_PROJECT", "PERMISSION_DENIED", "WORKSPACE_NOT_AVAILABLE"}
def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _conversation_history(context: Any) -> Dict[str, Any]:
    """Return current conversation background without a pre-emptive token cap.

    The physical model window is the only context-size authority. ``_crop_payload``
    may remove oldest background messages only when the compiled prompt truly
    does not fit.
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
        normalized.append({"role": role, "content": content.strip()})
    return {"messages": normalized, "omitted_messages": 0}

def _append_user_clarification(request: str, pending: Dict[str, Any], response: str) -> str:
    """Evolve the one canonical task request with a blocking user clarification.

    The clarification is task input, never a tool observation. Keeping it inside
    session.request guarantees the Main LLM, later turns and Claim review see
    the same task even after pending tool results are replaced.
    """
    clarification = pending.get("clarification") if isinstance(pending, dict) else None
    if not isinstance(clarification, dict):
        raise ValueError("PENDING_CLARIFICATION_INVALID")
    question = str(clarification.get("question") or "").strip()
    missing = str(clarification.get("missing_information") or "").strip()
    answer = str(response or "").strip()
    if not question or not missing or not answer:
        raise ValueError("PENDING_CLARIFICATION_INVALID")
    base = str(request or "").rstrip()
    block = (
        "User clarification for the active task:\n"
        f"Blocking information requested: {missing}\n"
        f"Eyle asked: {question}\n"
        f"User answered: {answer}"
    )
    return f"{base}\n\n{block}" if base else block


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
        "max_source_preview_chars": max(500, int(raw.get("max_source_preview_chars", 3500) or 3500)),
        "max_search_source_chars": max(300, int(raw.get("max_search_source_chars", 600) or 600)),
        "max_symbol_preview_chars": max(500, int(raw.get("max_symbol_preview_chars", 2600) or 2600)),
    }



def _allowed_tools(config: Dict[str, Any], project: Dict[str, Any]) -> set[str]:
    """Return physical capabilities only; never classify the user request.

    The Main LLM sees every capability that is objectively available in the
    current environment and decides whether to use it.
    """
    root = (project or {}).get("caminho_origem")
    project_available = bool(root and os.path.isdir(root))
    tests_enabled = project_available and _tests_enabled(config)
    names: set[str] = set()
    execution = current_execution()
    terminal = set(execution.terminal_capabilities) if execution is not None else set()
    for name, spec in TOOLS.items():
        if name in terminal:
            continue
        availability = str(spec.get("availability") or "workspace")
        if availability == "global":
            names.add(name)
        elif availability == "workspace" and project_available:
            names.add(name)
        elif availability == "tests" and tests_enabled:
            names.add(name)
    return names


def _tool_views(
    session: AgentSession, config: Dict[str, Any], project: Dict[str, Any],
) -> Tuple[set[str], List[str], List[Dict[str, Any]]]:
    """Return physical authority plus progressive model-facing capability views.

    Every physically available tool is callable from the compact index on first
    use. Expanded contracts are derived only for tools the Main LLM has actually
    requested before; no selector, router or persisted activation state exists.
    """
    allowed = _allowed_tools(config, project)
    if not allowed:
        return set(), [], []
    requested = [name for name in _requested_tool_names(session.decision_ledger) if name in allowed]
    active_set = set(requested)
    index = gerar_indice_capabilities(config=config, allowed_names=allowed - active_set)
    active = gerar_catalogo_tools(config=config, allowed_names=requested, compact=True) if requested else []
    return allowed, index, active


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
        "retryable": result.get("retryable"),
        "detail": detail,
    }


def _observable_tool_arguments(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return bounded tool arguments safe for the user-visible execution history.

    The history is observability, not a replay surface: source/code bodies, memory
    values and hashes are deliberately excluded.
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    if tool == "read_file":
        result = {"path": arguments.get("path")}
        if arguments.get("line_start") is not None:
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
    if tool in {"find_symbol", "symbol_relations"}:
        keys = ("symbol", "path", "roots", "direction", "include_text_references", "max_depth", "max_edges") if tool == "symbol_relations" else ("symbol", "path")
        return {k: arguments.get(k) for k in keys if arguments.get(k) is not None}
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
    if tool == "run_command":
        return {
            "command": str(arguments.get("command") or "")[:500],
            **({"cwd": arguments.get("cwd")} if arguments.get("cwd") else {}),
            **({"timeout_seconds": arguments.get("timeout_seconds")} if arguments.get("timeout_seconds") is not None else {}),
        }
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
        if key not in {"content", "new_code", "file_hash_expected", "range_hash_expected"}
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
    if result.get("retryable") is not None:
        public["retryable"] = bool(result.get("retryable"))
    detail = result.get("detail")
    if isinstance(detail, str):
        public["detail"] = detail[:500]
        return public
    if not isinstance(detail, dict):
        return public

    if tool in {"read_file", "find_symbol"}:
        public.update({
            "file": detail.get("file"),
            "lines": [detail.get("line_start"), detail.get("line_end")],
            "total_lines": detail.get("total_lines"),
            "truncated": bool(detail.get("truncated")),
        })
    elif tool == "list_tree":
        public.update({
            "entries": len(detail.get("entries") or []),
            "truncated": bool(detail.get("truncated")),
            "complete_scan": bool(detail.get("varredura_completa")),
        })
    elif tool == "search_code":
        public.update({
            "matches": detail.get("matches_returned", len(detail.get("results") or [])),
            "ranges": detail.get("ranges_returned", len(detail.get("results") or [])),
            "files": list(detail.get("relevant_files") or [])[:20],
            "truncated": bool(detail.get("truncated")),
            "coverage_complete": bool(detail.get("coverage_complete")),
        })
    elif tool == "symbol_relations":
        public.update({
            "symbol": detail.get("symbol"),
            "definitions": len(detail.get("definitions") or []),
            "incoming": len(detail.get("incoming") or []),
            "outgoing": len(detail.get("outgoing") or []),
            "text_references": len(detail.get("text_references") or []),
            "root_reachability": detail.get("root_reachability") or [],
            "coverage": detail.get("coverage") or {},
        })
    elif tool == "run_command":
        for key in ("command", "cwd", "returncode", "backend", "network_enabled", "workspace_isolated", "snapshot_persists_for_job", "real_workspace_changed"):
            if key in detail:
                public[key] = detail.get(key)
        if detail.get("output"):
            public["output_tail"] = str(detail.get("output"))[-1200:]
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
        for key in ("context", "llm_calls", "decisions", "tools"):
            if isinstance(detail.get(key), list):
                public[f"{key}_count"] = len(detail.get(key) or [])
        if isinstance(detail.get("tokens"), dict):
            public["tokens"] = detail.get("tokens")
    return {k: v for k, v in public.items() if v is not None}


def _bounded_source_text(text: Any, max_chars: int, *, source_span: Optional[Tuple[Any, Any]] = None) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    suffix = "\n...[source preview cropped; use read_file with line_start/line_end for more]"
    if source_span and source_span[0] is not None and source_span[1] is not None:
        suffix = f"\n...[source span {source_span[0]}-{source_span[1]} cropped; use read_file with line_start/line_end for more]"
    return value[:max_chars].rstrip() + suffix


def _llm_source_view(item: Dict[str, Any], config: Dict[str, Any], *, tool: str) -> Dict[str, Any]:
    """Return one compact source view; full bytes remain in session.evidence."""
    context_view = _context_view_config(config)
    max_chars = context_view["max_source_preview_chars"]
    if tool == "search_code":
        max_chars = min(max_chars, max(300, int(context_view.get("max_search_source_chars", 600))))
    elif tool == "find_symbol":
        max_chars = min(max_chars, max(800, int(context_view.get("max_symbol_preview_chars", 2600))))
    keep = {
        key: item.get(key) for key in (
            "file", "simbolo", "line_start", "line_end", "total_lines",
            "file_hash", "content_hash", "match_lines", "truncated",
            "coverage_complete", "source_type", "stage", "error_code", "evidence_id",
        ) if item.get(key) is not None
    }
    numbered = item.get("numbered_content")
    if not isinstance(numbered, str) or not numbered:
        raw = item.get("content")
        if isinstance(raw, str) and raw:
            numbered = raw
    if tool != "find_symbol" and isinstance(numbered, str) and numbered:
        keep["numbered_content"] = _bounded_source_text(
            numbered, max_chars, source_span=(item.get("line_start"), item.get("line_end")),
        )
        keep["source_preview_complete"] = len(numbered) <= max_chars
    return keep



def _model_tool_result(session: AgentSession, tool: str, result: Dict[str, Any], config: Optional[Dict[str, Any]] = None, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    evidence_worthy = bool(result.get("ok")) or (
        tool == "run_tests"
        and isinstance(result.get("detail"), dict)
        and (result.get("executed") is True or result.get("error_code") == "TEST_RUNNER_UNAVAILABLE")
    )
    evidence_ids = _register_evidence(session.evidence_ledger, tool, result.get("detail")) if evidence_worthy else []
    if tool == "find_symbol" and result.get("error_code") == "SYMBOL_NOT_FOUND" and result.get("executed") is True:
        payload = {
            "symbol": str((arguments or {}).get("symbol") or ""),
            "path": (arguments or {}).get("path"),
            "error_code": "SYMBOL_NOT_FOUND",
            "executed": True,
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        evidence_ids = _register_evidence(session.evidence_ledger, "agent_info", {
            "observation": payload, "source_type": "symbol_observation", "content_hash": source_hash
        })
        # Re-label synthetic runtime Evidence as a symbol observation.
        for evidence_id in evidence_ids:
            if evidence_id in _evidence_items(session.evidence_ledger):
                _evidence_items(session.evidence_ledger)[evidence_id].update({
                    "file": "<symbol-observation>", "source_type": "symbol_observation",
                    "content": content, "file_hash": source_hash, "content_hash": source_hash,
                })
    if bool((TOOLS.get(tool) or {}).get("produces_evidence")) and isinstance(result.get("detail"), dict):
        detail = result.get("detail")
        if tool == "search_code":
            copied = {
                key: value for key, value in detail.items()
                if key not in {"results"}
            }
            copied_results = []
            for item, evidence_id in zip(detail.get("results") or [], evidence_ids):
                clone = dict(item)
                clone["evidence_id"] = evidence_id
                copied_results.append(_llm_source_view(clone, config or {}, tool=tool))
            copied["results"] = copied_results
            detail = copied
        elif evidence_ids:
            clone = dict(detail)
            clone["evidence_id"] = evidence_ids[0]
            if tool in {"read_file", "find_symbol"}:
                detail = _llm_source_view(clone, config or {}, tool=tool)
            elif tool == "inspect_project":
                relations = clone.get("relation_signals") if isinstance(clone.get("relation_signals"), dict) else {}
                tests = clone.get("test_signals") if isinstance(clone.get("test_signals"), dict) else {}
                ci = clone.get("ci_signals") if isinstance(clone.get("ci_signals"), dict) else {}
                detail = {
                    "evidence_id": clone.get("evidence_id"),
                    "file_count": clone.get("file_count"), "directory_count": clone.get("directory_count"),
                    "languages": clone.get("languages") or {}, "scan_complete": clone.get("scan_complete"),
                    "entrypoint_signals": list(clone.get("entrypoint_signals") or [])[:12],
                    "framework_signals": list(clone.get("framework_signals") or [])[:12],
                    "test_signals": {"has_tests": bool(tests.get("has_tests")), "count": int(tests.get("count") or 0)},
                    "ci_signals": {"has_ci": bool(ci.get("has_ci")), "files": list(ci.get("files") or [])[:8]},
                    "relation_signals": {
                        "local_import_edge_count": int(relations.get("local_import_edge_count") or 0),
                        "local_import_edges_truncated": bool(relations.get("local_import_edges_truncated")),
                        "most_imported_files": list(relations.get("most_imported_files") or [])[:12],
                        "route_file_count": len(relations.get("route_files") or []),
                        "syntax_error_file_count": len(relations.get("syntax_error_files") or []),
                    },
                }
            elif tool == "symbol_relations":
                detail = {
                    "symbol": clone.get("symbol"), "path_filter": clone.get("path_filter"),
                    "direction": clone.get("direction"), "include_text_references": clone.get("include_text_references"),
                    "backend": clone.get("backend"), "evidence_id": clone.get("evidence_id"),
                    "definitions": list(clone.get("definitions") or [])[:24],
                    "incoming": list(clone.get("incoming") or [])[:32],
                    "outgoing": list(clone.get("outgoing") or [])[:32],
                    "structural_references": list(clone.get("structural_references") or [])[:24],
                    "imports": list(clone.get("imports") or [])[:24],
                    "root_reachability": list(clone.get("root_reachability") or [])[:24],
                    "unresolved_dynamic": list(clone.get("unresolved_dynamic") or [])[:16],
                    "coverage": clone.get("coverage") or {},
                    "semantics": "structural_facts_only",
                }
            elif tool == "run_command":
                detail = dict(clone)
                if detail.get("output") is not None:
                    detail["output"] = str(detail.get("output") or "")[-5000:]
            else:
                detail = clone
        return {
            "tool": tool,
            "status": result.get("status"),
            "ok": result.get("ok"),
            "executed": result.get("executed"),
            "changed": result.get("changed"),
            "error_code": result.get("error_code"),
            "retryable": result.get("retryable"),
            "detail": detail,
            "evidence_ids": evidence_ids,
        }
    compact = _compact_non_read_result(tool, result)
    if evidence_ids:
        compact["evidence_ids"] = evidence_ids
    return compact


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


def _claim_review_has_debt(review: Dict[str, Any]) -> bool:
    summary = dict((review or {}).get("summary") or {})
    return any(int(summary.get(key, 0) or 0) > 0 for key in (
        "contradicted", "insufficient", "semantic_gaps",
        "material_satisfaction_gap", "answer_consistency_conflict",
    ))


def _persistent_claim_feedback(session: AgentSession, config: Dict[str, Any]) -> str:
    if claim_config(config)["mode"] == "off" or not _claim_review_has_debt(session.claim_review):
        return ""
    return review_followup_feedback(session.claim_review)


def _has_grounded_runtime_state(session: AgentSession) -> bool:
    """Whether an independent Claim pass has objective task state to audit.

    This is not intent classification. It is a factual view over state that exists:
    observations, Evidence, declared Investigation or a write transaction.
    """
    return bool(
        _evidence_items(session.evidence_ledger)
        or session.investigation
        or ((session.observation_ledger or {}).get("events") or [])
        or session.write_transaction
    )


def _claim_required(session: AgentSession, config: Dict[str, Any]) -> bool:
    mode = claim_config(config)["mode"]
    if mode == "off":
        return False
    if mode == "verified":
        return True
    return _has_grounded_runtime_state(session)


def _agent_config(config: Dict[str, Any], session: AgentSession, project: Dict[str, Any]) -> Dict[str, Any]:
    """Authorize one final-capable Main-LLM response without semantic phases."""
    clone = dict(config)
    llm = dict(config.get("llm") or {})
    llm["agent_max_tokens"] = max(1, int(llm.get("agent_max_tokens", 3600) or 3600))
    if _claim_required(session, config) or _claim_review_has_debt(session.claim_review):
        llm["downstream_completion_reserve_tokens"] = int(claim_config(config)["verifier"]["max_tokens"])
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
    execution = current_execution()
    details = {
        "status": "processing", "turns": session.turn,
        "tool_calls": _physical_tool_calls(session), "tool_budget": _tool_budget_state(session, config),
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_replays": int(_observation_replay_count(session) or 0),
        "observation_ledger_size": len((session.observation_ledger or {}).get("entries") or {}),
        "evidence_count_total": len(_evidence_items(session.evidence_ledger)),
        "evidence_usage": _evidence_usage_metrics(session),
        "repeated_rejected_decisions": int(_repeated_rejection_count(session.decision_ledger) or 0),
        "tool_history": _tool_history_view(session, limit=50),
        "decision_history": _decision_history_view(session.decision_ledger, limit=50),
        "llm_usage": execution.usage_view() if execution else {},
        "llm_calls": execution.ledger_view() if execution else [],
        "write_transaction": _write_transaction_view(session.write_transaction),
        "claim_review": {
            "material_satisfaction": dict(session.claim_review.get("material_satisfaction") or {}),
            "answer_consistency": dict(session.claim_review.get("answer_consistency") or {}),
        } if session.claim_review else {},
    }
    return build_execution_trace(details, job_id=(execution.source_job_id if execution else None), status="processing", limit=100)


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
    context_cfg = config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    history_meta = {"messages": session.conversation_background, "omitted_messages": 0}
    if session.turn <= 1 and not session.conversation_background:
        history_meta = _conversation_history(conversation_context)
        session.conversation_background = list(history_meta.get("messages") or [])
    execution = current_execution()
    if execution is not None:
        execution.history_messages_omitted = int(history_meta.get("omitted_messages", 0) or 0)
        execution.assert_canonical_request(session.request)

    allowed, capability_index, active_tools = _tool_views(session, config, project)
    tool_budget = _tool_budget_state(session, config)
    execution = current_execution()
    token_remaining = None
    if execution is not None:
        token_remaining = execution.physical_tokens_remaining
    payload = {
        "request": session.request,
        "turn": session.turn,
        "investigation": session.investigation,
        "project": _project_descriptor(project),
        "conversation_background": session.conversation_background,
        "observation_map": _observation_map(session),
        "latest_tool_results": _pending_observation_results(session),
        "evidence_index": session.evidence_index(),
        "physical_limits": {
            "tool_calls_remaining": tool_budget["remaining"],
            "llm_turns_remaining_after_this_call": max(
                0,
                int(((config.get("agent") or {}).get("max_llm_turns", 24) or 24))
                - int(execution.agent_turns if execution is not None else session.turn),
            ),
            "physical_tokens_remaining": token_remaining,
            "terminal_capabilities": execution.terminal_capabilities_view() if execution is not None else {},
        },
        "capability_index": capability_index,
        "active_tools": active_tools,
        "runtime_feedback": _merged_runtime_feedback(feedback, _persistent_claim_feedback(session, config)),
    }
    claim_config(config)
    output_tokens = int((config.get("llm") or {}).get("agent_max_tokens", 3600) or 3600)
    calibration = execution.prompt_token_calibration if execution is not None else 1.0
    window_prompt_budget = available_user_prompt_tokens(
        config, PROMPT_AGENTE, output_tokens=output_tokens,
        token_estimate_multiplier=calibration,
    )
    system_tokens = estimate_tokens(PROMPT_AGENTE, chars_per_token)
    prompt_budget = window_prompt_budget
    components_before = _trace_prompt_components(payload, chars_per_token)
    pre_crop = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    pre_crop_tokens = estimate_tokens(pre_crop, chars_per_token)
    payload = _crop_payload(copy.deepcopy(payload), prompt_budget, chars_per_token)
    components_after = _trace_prompt_components(payload, chars_per_token)
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    post_crop_tokens = estimate_tokens(prompt, chars_per_token)
    execution = current_execution()
    if execution is not None:
        execution.begin_call(mode="agent", turn=session.turn, prompt={
            "characters": len(prompt), "estimated_tokens": post_crop_tokens, "tool_count": len(allowed),
            "active_tool_count": len(active_tools),
            "prompt_budget_tokens": prompt_budget, "window_user_prompt_budget_tokens": window_prompt_budget,
            "output_tokens_reserved": output_tokens, "system_prompt_characters": len(PROMPT_AGENTE),
            "system_prompt_estimated_tokens": system_tokens, "pre_crop_characters": len(pre_crop),
            "pre_crop_estimated_tokens": pre_crop_tokens, "crop_applied": len(pre_crop) != len(prompt),
            "components_before": components_before, "components_after": components_after,
        })
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
    # Auxiliary semantic-review prompts are JSON packets. Record only bounded
    # component sizes so Claim cost can be audited without exposing the packet.
    try:
        payload = json.loads(prompt)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        prompt_meta["components_after"] = _trace_prompt_components(payload, chars_per_token)
    if metadata:
        prompt_meta.update(metadata)
    execution = current_execution()
    if execution is not None:
        execution.begin_call(mode=mode, turn=session.turn, prompt={
            "characters": len(prompt), "estimated_tokens": estimate_tokens(prompt, chars_per_token),
            "tool_count": 0, **prompt_meta,
        })


def _remaining_completion_budget(config: Dict[str, Any]) -> Optional[int]:
    execution = current_execution()
    if execution is None:
        maximum = int((((config or {}).get("agent") or {}).get("max_completion_tokens", 0)) or 0)
        return maximum if maximum > 0 else None
    return max(0, int(execution.max_completion_tokens) - int(execution.completion_tokens_actual or 0))


def _fit_claim_evidence_view(
    session: AgentSession, config: Dict[str, Any], answer: str, selected_ids: List[str],
    *, output_tokens: int, answer_anchors: Optional[List[Dict[str, Any]]] = None,
    runtime_facts: Optional[List[Dict[str, Any]]] = None,
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
    execution = current_execution()
    window_user_budget = available_user_prompt_tokens(
        verifier_config, PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
        token_estimate_multiplier=(execution.prompt_token_calibration if execution is not None else 1.0),
    )
    prompt_budget = window_user_budget

    def build(cap: int) -> Tuple[List[Dict[str, Any]], int]:
        view = compact_evidence(
            _evidence_items(session.evidence_ledger), selected_ids, max_chars_per_item=max(0, int(cap)),
        )
        prompt = review_prompt(
            answer, view, session.request, answer_anchors=answer_anchors,
            investigation=session.investigation, runtime_facts=runtime_facts,
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


def _run_claim_verification(
    session: AgentSession, config: Dict[str, Any], answer: str, evidence_ids: List[str],
    *, project_root: Any = None,
) -> Tuple[bool, str, Dict[str, Any], List[Dict[str, Any]]]:
    """Run the single canonical Claim Review path.

    Rev5.6 has one strict Claim pass. Structured transport is canonical JSON
    Schema; invalid structured output fails instead of entering a repair lane.
    """
    cfg = claim_config(config)
    execution = current_execution()
    if execution is not None:
        execution.assert_canonical_request(session.request)
    selected_ids = list(dict.fromkeys(str(item) for item in (evidence_ids or []) if str(item)))

    fresh, freshness_reason = validate_file_evidence_freshness(
        _evidence_items(session.evidence_ledger), selected_ids, project_root,
    )
    if not fresh:
        return False, freshness_reason, {}, []

    verifier_config = _claim_llm_config(config, cfg["mode"])
    answer_anchors = build_answer_anchors(answer)
    runtime_facts = compact_runtime_facts(session.observation_ledger)
    remaining_completion = _remaining_completion_budget(config)
    output_tokens = claim_review_output_budget(
        answer,
        base_tokens=cfg["verifier"]["max_tokens"],
        available_tokens=remaining_completion,
        answer_anchor_count=len(answer_anchors),
    )
    fit_ok, fit_reason, view, fit_meta = _fit_claim_evidence_view(
        session, config, answer, selected_ids, output_tokens=output_tokens,
        answer_anchors=answer_anchors, runtime_facts=runtime_facts,
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
        answer, view, session.request, answer_anchors=answer_anchors,
        investigation=session.investigation, runtime_facts=runtime_facts,
    )
    fit_meta = dict(fit_meta)
    fit_meta.update({
        "answer_anchor_count": len(answer_anchors),
        "target_evidence_count": len(target_evidence_ids(session.investigation)),
        "investigation_target_count": len(session.investigation or []),
        "runtime_fact_count": len(runtime_facts),
    })
    _record_aux_prompt(
        session, verifier_config, mode="claim_verification", prompt=prompt,
        system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens, metadata=fit_meta,
    )

    parsed = executar_verificador_claims(prompt, verifier_config)

    fresh, freshness_reason = validate_file_evidence_freshness(
        _evidence_items(session.evidence_ledger), selected_ids, project_root,
    )
    if not fresh:
        return False, freshness_reason, {}, view
    if not isinstance(parsed, dict):
        return False, "CLAIM_REVIEW_PROTOCOL_ERROR:STRUCTURED_OBJECT_REQUIRED", {}, view

    ok, reason, review = normalize_claim_review(
        parsed, _evidence_items(session.evidence_ledger), answer=answer,
        answer_anchors=answer_anchors, visible_evidence_ids=visible_ids,
        investigation=session.investigation, runtime_facts=runtime_facts,
    )
    return ok, reason, review, view

def _append_claim_review(session: AgentSession, review: Dict[str, Any]) -> None:
    session.claim_review = {
        "turn": session.turn,
        "material_satisfaction": dict(review.get("material_satisfaction") or {}),
        "answer_consistency": dict(review.get("answer_consistency") or {}),
        "claims": [dict(item) for item in review.get("claims") or [] if isinstance(item, dict)],
        "semantic_gaps": [dict(item) for item in review.get("semantic_gaps") or [] if isinstance(item, dict)],
    }

def _tool_budget_state(session: AgentSession, config: Dict[str, Any]) -> Dict[str, int]:
    """Physical tool fuse for the current execution/job, never cumulative task history."""
    maximum = max(1, int(((config or {}).get("agent") or {}).get("max_tool_calls", 64) or 64))
    execution = current_execution()
    events = list((session.observation_ledger or {}).get("events") or [])
    start = int(execution.observation_event_start or 0) if execution is not None else 0
    current_events = events[start:]
    used = sum(1 for item in current_events if isinstance(item, dict) and item.get("executed") is True)
    return {"limit": maximum, "used": used, "remaining": max(0, maximum - used)}

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
            "grounding_refs": sorted(str(x) for x in item.get("grounding_refs") or [] if str(x)),
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
            "grounding_refs": sorted(str(x) for x in item.get("grounding_refs") or [] if str(x)),
            "evidence_ids": sorted(str(x) for x in item.get("evidence_ids") or [] if str(x)),
            "required_property": str(item.get("required_property") or ""),
            "reason": str(item.get("reason") or ""),
        })
    claims.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    gaps.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))
    satisfaction = dict((review or {}).get("material_satisfaction") or {})
    consistency = dict((review or {}).get("answer_consistency") or {})
    return {"material_satisfaction": satisfaction, "answer_consistency": consistency, "claims": claims, "semantic_gaps": gaps}


def _evidence_usage_metrics(session: AgentSession) -> Dict[str, int]:
    all_ids = {str(item) for item in _evidence_items(session.evidence_ledger).keys() if str(item)}
    target_ids = set(target_evidence_ids(session.investigation))
    claim_ids: set[str] = set()
    for claim in (session.claim_review or {}).get("claims") or []:
        if isinstance(claim, dict):
            claim_ids.update(str(item) for item in claim.get("evidence_ids") or [] if str(item))
    for gap in (session.claim_review or {}).get("semantic_gaps") or []:
        if isinstance(gap, dict):
            claim_ids.update(str(item) for item in gap.get("evidence_ids") or [] if str(item))
    referenced = target_ids | claim_ids
    unreferenced = all_ids - referenced
    unreferenced_tool_actions = 0
    for item in _tool_history_view(session, limit=50):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("executed") is not True:
            continue
        ids = {str(eid) for eid in item.get("evidence_ids") or [] if str(eid)}
        if ids and ids.issubset(unreferenced):
            unreferenced_tool_actions += 1
    return {
        "total_evidence_count": len(all_ids),
        "target_attached_evidence_count": len(target_ids & all_ids),
        "claim_cited_evidence_count": len(claim_ids & all_ids),
        "structurally_unreferenced_evidence_count": len(unreferenced),
        "structurally_unreferenced_tool_actions": unreferenced_tool_actions,
    }

def _details(
    session: AgentSession, status: str, config: Dict[str, Any],
    limitations: Optional[List[str]] = None, failure_code: Optional[str] = None,
) -> Dict[str, Any]:
    execution = current_execution()
    all_tool_events = list((session.observation_ledger or {}).get("events") or [])
    all_decision_events = list((session.decision_ledger or {}).get("events") or [])
    obs_start = int(execution.observation_event_start or 0) if execution is not None else 0
    dec_start = int(execution.decision_event_start or 0) if execution is not None else 0
    job_tool_events = all_tool_events[obs_start:]
    job_decision_events = all_decision_events[dec_start:]
    tool_history = [{
        "turn": item.get("turn"), "tool": item.get("tool"), "status": item.get("status"),
        "error_code": item.get("error_code"), "semantic_signature": item.get("semantic_signature"),
        "arguments": copy.deepcopy(item.get("arguments") or {}),
        "result": copy.deepcopy(item.get("result") or {}),
        "evidence_ids": list(item.get("evidence_ids") or []), "replay_reason": item.get("replay_reason"),
    } for item in job_tool_events[-50:] if isinstance(item, dict)]
    decision_history = [{k: copy.deepcopy(v) for k, v in item.items() if k not in {"rejection_fingerprint"}}
                        for item in job_decision_events[-50:] if isinstance(item, dict)]
    job_tool_calls = sum(1 for item in job_tool_events if isinstance(item, dict) and item.get("executed") is True)
    job_replays = sum(1 for item in job_tool_events if isinstance(item, dict) and item.get("status") == "replayed")
    task_evidence_count = len(_evidence_items(session.evidence_ledger))
    start_evidence = set(execution.evidence_ids_start or []) if execution is not None else set()
    job_evidence_count = len(set(_evidence_items(session.evidence_ledger)) - start_evidence)
    return {
        "status": status, "investigation": session.investigation,
        "turns": int(execution.agent_turns if execution is not None else session.turn),
        "tool_calls": job_tool_calls, "tool_budget": _tool_budget_state(session, config),
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_replays": job_replays,
        "observation_ledger_size": len(job_tool_events),
        "evidence_count_total": job_evidence_count,
        "evidence_usage": _evidence_usage_metrics(session),
        "repeated_rejected_decisions": sum(1 for item in job_decision_events if isinstance(item, dict) and item.get("outcome") == "stalled"),
        "task_totals": {
            "turns": int(session.turn),
            "tool_calls": _physical_tool_calls(session),
            "observation_replays": int(_observation_replay_count(session) or 0),
            "observation_events": len(all_tool_events),
            "evidence_count": task_evidence_count,
            "decision_events": len(all_decision_events),
        },
        "tools_used": [item.get("tool") for item in tool_history if (item.get("result") or {}).get("executed") is True],
        "tool_history": tool_history,
        "decision_history": decision_history,
        "evidence": session.evidence_index(),
        "claim_evidence": claim_evidence_ledger(session.claim_review, _evidence_items(session.evidence_ledger)) if session.claim_review else [],
        "claim_review": {
            "material_satisfaction": dict(session.claim_review.get("material_satisfaction") or {}),
            "answer_consistency": dict(session.claim_review.get("answer_consistency") or {}),
            "semantic_gaps": [dict(item) for item in session.claim_review.get("semantic_gaps") or [] if isinstance(item, dict)],
        } if session.claim_review else {},
        "limitations": list(limitations or []), "failure_code": failure_code,
        "write_failure": dict(session.write_transaction.get("failure") or {}) if isinstance(session.write_transaction, dict) and session.write_transaction.get("failure") else None,
        "llm_usage": execution.usage_view() if execution else {},
        "llm_calls": execution.ledger_view() if execution else [],
        "write_transaction": _write_transaction_view(session.write_transaction),
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


def _pending_patch_set(session: AgentSession):
    tx = session.write_transaction
    patches = tx.get("patches") if isinstance(tx, dict) else None
    if not isinstance(patches, list) or not patches:
        raise ValueError("WRITE_TRANSACTION_MISSING")
    files = [str(patch.get("path") or "") for patch in patches]
    text = (
        f"Proposta transacional pronta para confirmação: {len(patches)} arquivo(s): "
        f"{', '.join(files)}. Dry-run aprovado para o conjunto completo. "
        "A aplicação exige confirmação do usuário."
    )
    _set_write_status(tx, "awaiting_confirmation")
    pending = {
        "continuation_kind": "write_confirmation", "pergunta_ao_usuario": text,
        "estado": session.to_dict(), "transaction_id": tx.get("transaction_id"),
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
    _record_write_validation(session.write_transaction, "rollback", _validation_step(rollback, paths=paths))
    _set_write_status(session.write_transaction, "rolled_back" if rollback.get("ok") else "rollback_failed")


def _resume_set(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    context = {"config": config, "projeto": project, "evidence": _evidence_items(session.evidence_ledger)}
    transaction = session.write_transaction
    _clear_write_failure(transaction)
    patches = transaction.get("patches") if isinstance(transaction, dict) else None
    if not isinstance(patches, list) or not patches:
        text = "A transação confirmada ficou inválida."
        return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_RESPONSE_INVALID"), full)
    raw_applied = apply_patch_set(project.get("caminho_origem"), patches)
    applied = _transaction_result(raw_applied, changed=bool(raw_applied.get("ok")))
    attempted_paths = [str(item.get("path") or "") for item in patches if isinstance(item, dict)]
    _record_write_validation(transaction, "apply", _validation_step(applied, paths=attempted_paths))
    _set_write_status(transaction, "applied" if applied.get("ok") else "apply_failed")
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
        _record_write_failure(transaction, report)
        return _return("failed", text, None, _details(
            session, "failed", config, failure_code=code,
        ), full)

    applied_patches = (applied.get("detail") or {}).get("applied_patches") or []
    paths = [str(item.get("path") or "") for item in applied_patches]
    compile_result = _compile_after_write(config, project, paths)
    _record_write_validation(transaction, "compileall", _validation_step(compile_result, paths=paths))
    if compile_result.get("ok") is not True:
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        text, suffix, report = _write_failure_response(
            "compileall falhou após a transação.", "compileall", compile_result, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        _record_write_failure(transaction, report)
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{compile_result.get('error_code') or 'COMPILEALL_FAILED'}_{suffix}",
            limitations=[str(compile_result.get("detail") or "compileall falhou")],
        ), full)

    tests = _run_tests_after_write(config, context)
    _record_write_validation(transaction, "tests", _validation_step(tests, paths=paths))
    if tests.get("ok") is not True:
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        text, suffix, report = _write_failure_response(
            "A verificação por testes falhou após a transação.", "tests", tests, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        _record_write_failure(transaction, report)
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"{tests.get('error_code') or 'TESTS_FAILED'}_{suffix}",
            limitations=[str(tests.get("detail") or "testes falharam")],
        ), full)

    expected_outputs = expected_outputs_from_patches(applied_patches)
    reread = verify_expected_outputs(project.get("caminho_origem"), expected_outputs)
    _record_write_validation(transaction, "full_reread", _validation_step(reread, paths=paths))
    if not reread.get("ok"):
        rollback = _transaction_rollback_result(rollback_patch_set(applied_patches))
        _record_rollback(session, rollback, paths)
        reread_failure = dict(reread)
        reread_failure.setdefault("error_code", "POST_WRITE_READ_FAILED")
        text, suffix, report = _write_failure_response(
            "A releitura integral da transação falhou.", "reread", reread_failure, rollback,
            "Todos os arquivos foram restaurados.", paths,
        )
        _record_write_failure(transaction, report)
        return _return("failed", text, None, _details(
            session, "failed", config,
            failure_code=f"POST_WRITE_READ_FAILED_{suffix}",
            limitations=[str(reread_failure.get("detail") or "releitura falhou")],
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
    _set_write_status(transaction, "verified" if fully_verified else "applied_partial")

    text = (
        f"Transação aplicada em {len(paths)} arquivo(s): {', '.join(paths)}.\n\nValidação pós-escrita:\n"
        f"- {compile_line};\n- {test_line};\n"
        f"- todos os arquivos alterados foram relidos integralmente;\n"
        f"- {creation_line};\n- exclusões prometidas foram confirmadas;\n- {state_line}"
    )
    return _return("success", text, None, _details(session, "success", config, limitations=limitations), full)


def _resume(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], project: Dict[str, Any], full: bool):
    if pending.get("continuation_kind") != "write_confirmation" or not session.write_transaction or pending.get("transaction_id") != session.write_transaction.get("transaction_id"):
        text = "A pendência não corresponde a uma confirmação transacional válida."
        return _return(
            "failed", text, None,
            _details(session, "failed", config, failure_code="WRITE_PENDING_INVALID"), full,
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
        evidence = _evidence_freshest_for_path(session.evidence_ledger, path)

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
                    int(evidence.get("line_start") or 0) == 1
                    and int(evidence.get("line_end") or 0) == int(evidence.get("total_lines") or -1)
                )
                if not whole_file:
                    return arguments, f"replace requires a fresh whole-file read: {path}"
            patch["file_hash_expected"] = evidence["file_hash"]
        elif operation == "create":
            if exists:
                return arguments, f"create cannot overwrite an existing file: {path}; use replace"

        if operation == "update":
            start, end = patch["line_start"], patch["line_end"]
            if int(evidence.get("line_start") or 0) == start and int(evidence.get("line_end") or 0) == end:
                patch["range_hash_expected"] = evidence.get("content_hash")
            else:
                content = evidence.get("content")
                ev_start = int(evidence.get("line_start") or 0)
                ev_end = int(evidence.get("line_end") or 0)
                if isinstance(content, str) and ev_start == 1 and ev_end == int(evidence.get("total_lines") or -1):
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


def _normalized_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def _record_decision(
    session: AgentSession, decision_type: str, outcome: str, *,
    reason: Optional[str] = None, tools: Optional[List[str]] = None,
) -> None:
    _decision_record(
        session.decision_ledger, turn=session.turn, decision=decision_type,
        outcome=outcome, reason=reason, tools=tools,
    )

def _action_signature(tool: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def _objective_runtime_state_payload(session: AgentSession) -> Dict[str, Any]:
    evidence = [
        (str(key), str((item or {}).get("content_hash") or ""), str((item or {}).get("file_hash") or ""))
        for key, item in sorted(_evidence_items(session.evidence_ledger).items()) if isinstance(item, dict)
    ]
    entries = (session.observation_ledger or {}).get("entries") if isinstance(session.observation_ledger, dict) else {}
    observations = [
        (str(key), str((item or {}).get("result_fingerprint") or ""))
        for key, item in sorted((entries or {}).items()) if isinstance(item, dict)
    ]
    bindings = [
        (str(item.get("id") or ""), str(item.get("status") or ""), tuple(sorted(str(eid) for eid in (item.get("evidence_ids") or []) if str(eid))))
        for item in (session.investigation or []) if isinstance(item, dict)
    ]
    return {"workspace_epoch": int(session.workspace_epoch or 0), "evidence": evidence, "observations": observations, "investigation": sorted(bindings)}

def _record_rejected_decision(
    session: AgentSession, code: str, payload: Any, *, objective_context: Optional[Dict[str, Any]] = None,
    decision: Optional[str] = None, tools: Optional[List[str]] = None, reason: Optional[str] = None,
    repeated_outcome: Optional[str] = None,
) -> int:
    return _decision_record_rejection(
        session.decision_ledger, turn=session.turn, code=code, payload=payload,
        objective_state=_objective_runtime_state_payload(session), objective_context=objective_context,
        decision=decision, tools=tools, reason=reason, repeated_outcome=repeated_outcome,
    )


def _rehydrate_observation(session: AgentSession, entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    replay = copy.deepcopy(entry.get("replay_result")) if isinstance(entry.get("replay_result"), dict) else None
    if replay is None and isinstance(entry.get("replay_summary"), dict):
        replay = copy.deepcopy(entry.get("replay_summary"))
    evidence_ids = [str(item) for item in entry.get("evidence_ids") or [] if str(item) in _evidence_items(session.evidence_ledger)]
    tool = str(entry.get("tool") or "")
    if replay is None:
        details = []
        for evidence_id in evidence_ids:
            evidence = _evidence_items(session.evidence_ledger).get(evidence_id) or {}
            clone = dict(evidence)
            clone["evidence_id"] = evidence_id
            if evidence.get("source_type") in {"search_observation", "symbol_observation"}:
                try:
                    details.append(json.loads(str(evidence.get("content") or "{}")))
                except Exception:
                    details.append({"evidence_id": evidence_id})
            else:
                details.append(_llm_source_view(clone, config, tool=tool))
        if tool == "search_code":
            negative = next((item for item in details if isinstance(item, dict) and item.get("coverage_complete") is True and not item.get("file")), None)
            detail = negative or {"results": details}
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
    if str(reason).startswith("FINAL_INVESTIGATION_TARGET_OPEN:"):
        return (
            f"FINAL_VALIDATION_ERROR: {reason}. Resolve the open targets with Evidence, explicitly dismiss them with reason, "
            "or continue investigation. Do not drop or rename targets."
        )
    if str(reason).startswith("FINAL_INVESTIGATION_UNKNOWN_EVIDENCE:"):
        return f"FINAL_VALIDATION_ERROR: {reason}. Keep canonical target Evidence IDs valid and present in evidence_index."
    return f"FINAL_VALIDATION_ERROR: {reason}. Return a corrected final answer."


def _deadline_exceeded(config: Dict[str, Any]) -> bool:
    execution = current_execution()
    return execution is not None and time.monotonic() >= float(execution.deadline_monotonic)


def _run(
    session: AgentSession,
    config: Dict[str, Any],
    project: Dict[str, Any],
    full: bool,
    conversation_context: Any = None,
) -> tuple:
    cfg = config.get("agent") or {}
    max_turns = max(1, int(cfg.get("max_llm_turns", 24) or 24))
    claim_config(config)  # validate once at the execution boundary
    max_patch_failures = max(1, int(cfg.get("max_patch_dry_run_failures", 2) or 2))
    feedback = ""

    execution = current_execution()
    run_turns = 0
    while run_turns < max_turns:
        if _deadline_exceeded(config):
            text = "A tarefa excedeu o prazo de execução."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="TASK_DEADLINE_EXCEEDED"), full)

        session.turn += 1
        run_turns += 1
        if execution is not None:
            execution.agent_turns = run_turns
        evidence_before = len(_evidence_items(session.evidence_ledger))
        try:
            decision, allowed = _call_agent(session, config, project, conversation_context, feedback)
        except ErroLLM as error:
            if _is_structured_response_error(error, "agent"):
                _record_decision(session, "protocol", "rejected", reason=error.error_code)
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
        prospective_investigation, accepted_updates, rejected_updates = apply_investigation_updates(
            raw_updates, previous=session.investigation, evidence=_evidence_items(session.evidence_ledger),
        )
        # Commit every structurally valid target update independently. Invalid
        # siblings cannot roll back accepted work, and omitted targets remain in
        # the canonical runtime-owned contract unchanged.
        session.investigation = prospective_investigation
        for item in accepted_updates:
            _record_decision(
                session, "investigation_update",
                "committed" if item.get("changed") else "unchanged",
                reason=f"{item.get('id')}={item.get('status')}",
            )
        repeated_investigation_rejection = 0
        for item in rejected_updates:
            position = max(1, int(item.get("position") or 1))
            attempted = raw_updates[position - 1] if position <= len(raw_updates) else item
            reason = str(item.get("reason") or "INVESTIGATION_UPDATE_REJECTED")
            code = reason.split(":", 1)[0]
            occurrence = _record_rejected_decision(
                session, code, attempted, decision="investigation_update", reason=reason,
                objective_context={"target_id": item.get("id")}, repeated_outcome="stalled",
            )
            repeated_investigation_rejection = max(repeated_investigation_rejection, occurrence)

        target_state = ",".join(
            f"{item.get('id')}={item.get('status')}"
            for item in session.investigation if isinstance(item, dict)
        )
        _record_decision(
            session, "investigation_contract", "accepted",
            reason=target_state or "empty",
        )

        if rejected_updates:
            if repeated_investigation_rejection >= 2:
                text = "A LLM repetiu a mesma transição estrutural inválida de Investigation sem qualquer mudança objetiva de estado."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="INVESTIGATION_UPDATE_DECISION_LOOP"), full,
                )
            feedback = json.dumps({
                "code": "INVESTIGATION_UPDATES_PARTIALLY_REJECTED",
                "accepted_updates": [
                    {"id": item.get("id"), "changed": bool(item.get("changed"))}
                    for item in accepted_updates
                ],
                "rejected_updates": rejected_updates,
                "canonical_investigation": session.investigation,
                "available_evidence_ids": sorted(_evidence_items(session.evidence_ledger)),
                "instruction": (
                    "Accepted updates are already committed and must not be reconstructed. Correct only the rejected target update. "
                    "If established was rejected for missing Evidence, choose the material IDs yourself from evidence_index/available_evidence_ids and include them explicitly; Runtime will not choose or attach Evidence for you. "
                    "Repeating the same invalid transition without an objective state change terminates the task."
                ),
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        if decision.get("needs_user"):
            request_for_user = decision["needs_user"]
            if not isinstance(request_for_user, dict):
                text = "A LLM produziu um pedido de informação inválido."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="AGENT_NEEDS_USER_INVALID"), full,
                )
            question = str(request_for_user.get("question") or "").strip()
            missing = str(request_for_user.get("missing_information") or "").strip()
            if not question or not missing:
                text = "A LLM produziu um pedido de informação incompleto."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="AGENT_NEEDS_USER_INVALID"), full,
                )
            _record_decision(session, "needs_user", "accepted", reason=missing)
            pending = {
                "continuation_kind": "user_input",
                "pergunta_ao_usuario": question,
                "clarification": {"question": question, "missing_information": missing},
                "estado": session.to_dict(),
            }
            return _return("needs_user", question, pending, _details(session, "needs_user", config), full)

        if isinstance(decision.get("patches"), list):
            _record_decision(session, "patches", "requested")
            project_available = _project_descriptor(project)["available"]
            write_enabled = bool(((config.get("codar") or {}).get("ativado", True)))
            if not project_available:
                text = "A escrita exige um workspace ativo."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="WORKSPACE_NOT_AVAILABLE"), full)
            if not write_enabled:
                _record_decision(session, "patches", "rejected", reason="WRITE_ACTION_NOT_ALLOWED")
                feedback = "WRITE_ACTION_NOT_ALLOWED: workspace mutation is disabled by runtime configuration."
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
            enriched, patch_error = _enrich_patch_set(session, project, {"patches": decision["patches"]})
            if patch_error:
                _record_decision(session, "patch_validation", "rejected", reason="PATCH_SCHEMA_INVALID")
                patch_failures = sum(1 for item in _decision_history_view(session.decision_ledger, limit=200) if item.get("decision") == "patch_validation" and item.get("outcome") == "rejected")
                if patch_failures >= max_patch_failures:
                    text = f"A proposta de escrita continuou inválida após {patch_failures} tentativa(s): {patch_error}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code="PATCH_SCHEMA_INVALID"), full)
                feedback = f"PATCH_SCHEMA_INVALID: {patch_error}. Correct the same transaction; do not restart investigation."
                continue

            if not session.write_transaction or str(session.write_transaction.get("status") or "") in {"verified", "applied_partial", "rolled_back", "rollback_failed"}:
                session.write_transaction = _begin_write_transaction(patches=enriched["patches"], turn=session.turn)
            else:
                session.write_transaction["patches"] = copy.deepcopy(enriched["patches"])
            _increment_write_attempt(session.write_transaction)
            raw_dry = dry_run_patch_set(project.get("caminho_origem"), enriched["patches"])
            dry = _transaction_result(raw_dry, changed=False)
            _record_write_validation(session.write_transaction, "dry_run", _validation_step(
                dry, paths=[str(item.get("path") or "") for item in enriched["patches"]]
            ))
            if dry.get("ok") is not True:
                code = str(dry.get("error_code") or "DRY_RUN_FAILED")
                _set_write_status(session.write_transaction, "dry_run_failed")
                _record_decision(session, "patch_validation", "rejected", reason=code)
                if code in TERMINAL_TOOL_ERRORS:
                    text = f"O dry-run transacional encontrou um erro terminal: {code}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=code), full)
                patch_failures = sum(1 for item in _decision_history_view(session.decision_ledger, limit=200) if item.get("decision") == "patch_validation" and item.get("outcome") == "rejected")
                if patch_failures >= max_patch_failures:
                    text = f"O dry-run da escrita falhou {patch_failures} vez(es): {code} — {_diagnostic_text(dry)}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=code), full)
                feedback = f"{code}: {_diagnostic_text(dry)}. Correct the same transaction; do not restart investigation."
                continue

            _record_decision(session, "patch_validation", "validated")
            _set_write_status(session.write_transaction, "dry_run_valid")
            text, pending = _pending_patch_set(session)
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if "final" in decision:
            claims_cfg = claim_config(config)
            project_root = project.get("caminho_origem")
            final_obj = decision.get("final")
            ok, reason, answer, limitations = validate_final(
                final_obj, _evidence_items(session.evidence_ledger), investigation=session.investigation,
            )

            if ok and not _claim_required(session, config):
                if claims_cfg["mode"] != "off":
                    _record_decision(session, "claim_review", "skipped", reason="NO_GROUNDED_STATE")
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            if ok:
                # Claim is the one semantic challenger. Runtime does not classify
                # the request and does not decide which Evidence is semantically
                # relevant. The verifier sees the task's complete Evidence set.
                review_evidence_ids = list(_evidence_items(session.evidence_ledger).keys())
                _record_decision(session, "final", "provisional")
                try:
                    review_ok, review_reason, review, _evidence_view = _run_claim_verification(
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
                    if str(review_reason).startswith("EVIDENCE_STALE:") and session.turn < max_turns:
                        feedback = json.dumps({
                            "code": "EVIDENCE_STALE",
                            "detail": review_reason,
                            "instruction": (
                                "Selected file Evidence changed on disk. Decide whether to observe current reality again, "
                                "narrow the answer, or state a limitation. Runtime does not choose the semantic action."
                            ),
                        }, ensure_ascii=False, separators=(",", ":"))
                        _clear_pending_observation_results(session)
                        continue
                    text = f"A verificação de claims ficou inválida: {review_reason}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=review_reason), full)

                _append_claim_review(session, review)
                summary = dict(review.get("summary") or {})
                has_contradicted_claims = int(summary.get("contradicted", 0) or 0) > 0
                has_insufficient_claims = int(summary.get("insufficient", 0) or 0) > 0
                has_semantic_gaps = int(summary.get("semantic_gaps", 0) or 0) > 0
                has_material_satisfaction_gap = int(summary.get("material_satisfaction_gap", 0) or 0) > 0
                has_answer_consistency_conflict = int(summary.get("answer_consistency_conflict", 0) or 0) > 0
                has_debt = (
                    has_contradicted_claims or has_insufficient_claims or has_semantic_gaps
                    or has_material_satisfaction_gap or has_answer_consistency_conflict
                )

                if has_debt:
                    if has_contradicted_claims:
                        review_reason = "CLAIM_CONTRADICTED"
                        review_outcome = "contradicted"
                    elif has_insufficient_claims:
                        review_reason = "CLAIM_INSUFFICIENT"
                        review_outcome = "insufficient"
                    elif has_answer_consistency_conflict:
                        review_reason = "CLAIM_ANSWER_CONSISTENCY_CONFLICT"
                        review_outcome = "inconsistent"
                    elif has_material_satisfaction_gap:
                        review_reason = "CLAIM_MATERIAL_SATISFACTION_GAP"
                        review_outcome = "insufficient"
                    else:
                        review_reason = "CLAIM_SEMANTIC_GAP"
                        review_outcome = "insufficient"

                    repeat_count = _record_rejected_decision(
                        session, "CLAIM_REVIEW_FOLLOWUP", _review_followup_payload(review),
                        decision="claim_review", reason=review_reason, repeated_outcome="stalled",
                    )
                    if repeat_count > 1:
                        text = (
                            "A mesma dívida semântica reapareceu sem mudança material de estado; "
                            "o runtime interrompeu o ciclo físico repetido."
                        )
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
                    _record_decision(session, "claim_review", review_outcome, reason=review_reason)
                    feedback = review_followup_feedback(review)

                    # Claim can contest omitted debt (target_id=null), but Runtime
                    # never creates a target. The same Main LLM decides whether
                    # to declare/reopen Investigation on the next ordinary turn.
                    if session.turn < max_turns:
                        _clear_pending_observation_results(session)
                        continue

                    if has_contradicted_claims:
                        failure = "CLAIM_REVIEW_CONTRADICTED"
                        text = "A conclusão contém afirmações contraditas pela revisão semântica."
                    elif has_insufficient_claims:
                        failure = "CLAIM_REVIEW_INSUFFICIENT"
                        text = "A conclusão contém afirmações sem suporte suficiente."
                    elif has_answer_consistency_conflict:
                        failure = "CLAIM_REVIEW_ANSWER_CONSISTENCY_CONFLICT"
                        text = "A conclusão contém vereditos materiais incompatíveis entre si."
                    elif has_material_satisfaction_gap:
                        failure = "CLAIM_REVIEW_MATERIAL_SATISFACTION_GAP"
                        text = "A conclusão não entregou materialmente o resultado solicitado."
                    else:
                        failure = "CLAIM_REVIEW_SEMANTIC_GAP"
                        text = "A revisão semântica encontrou dívida material ainda não resolvida."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=failure), full)

                _record_decision(session, "claim_review", "supported")
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            rejection_count = _record_rejected_decision(
                session, "FINAL_VALIDATION_REJECTED", {"reason": reason, "final": final_obj},
                decision="final", reason=reason, repeated_outcome="stalled",
            )
            if rejection_count > 1:
                text = f"A mesma conclusão inválida foi repetida sem mudança material: {reason}."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="FINAL_VALIDATION_STALLED"), full,
                )
            if session.turn < max_turns:
                feedback = _final_validation_feedback(reason)
                continue
            text = f"A conclusão final ficou inválida: {reason}."
            return _return("failed", text, None, _details(session, "failed", config, failure_code=reason), full)

        calls = decision.get("tool_calls") if isinstance(decision.get("tool_calls"), list) else [decision]
        calls = [call for call in calls if isinstance(call, dict) and call.get("tool")]
        if not calls:
            _record_rejected_decision(session, "NO_ACTION", {}, decision="empty")
            feedback = "Choose one capability from capability_index, ask a blocking question, or return final."
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
        replay_requests: List[Dict[str, Any]] = []
        for call in calls:
            tool = str(call.get("tool") or "")
            arguments = call.get("arguments") or {}
            if tool not in allowed:
                rejected = {
                    "tool": tool, "status": "failed", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": "TOOL_NOT_AVAILABLE",
                    "detail": "A ferramenta não está disponível neste workspace/configuração.",
                }
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=rejected["error_code"], tools=[tool])
                continue

            normalized, error = validar_chamada_tool(tool, arguments)
            if error:
                rejected = _compact_non_read_result(tool, error)
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "tool_validation", "rejected", reason=error.get("error_code") or "INVALID_ARGUMENT", tools=[tool])
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
                _record_observation_replay(session, {"tool": tool, "arguments": normalized, "public_arguments": _observable_tool_arguments(tool, normalized), "semantic_signature": semantic_signature}, duplicate, reason="BATCH_DUPLICATE_SUPPRESSED", public_result={"status":"replayed","ok":True,"executed":False,"changed":False})
                continue
            if semantic_signature:
                seen_batch_observations.add(semantic_signature)
                previous = _lookup_observation(session, semantic_signature)
                replay_reason = "OBSERVATION_REHYDRATED"
                if previous is None:
                    previous = _lookup_covering_observation(session, tool, normalized)
                    if previous is not None:
                        replay_reason = "OBSERVATION_COVERAGE_REPLAYED"
                if previous is not None:
                    replay = _rehydrate_observation(session, previous, config)
                    replay["tool"] = tool
                    replay["replayed"] = True
                    if replay_reason == "OBSERVATION_COVERAGE_REPLAYED":
                        replay["coverage_replayed"] = True
                        replay["source_observation_tool"] = previous.get("tool")
                    preflight_replays += 1
                    _record_observation_replay(session, previous, replay, reason=replay_reason, public_result={"status":"replayed","ok":True,"executed":False,"changed":False})
                    replay_requests.append({"tool": tool, "arguments": normalized})
                    next_results.append(replay)
                    _record_decision(session, "tool_preflight", "replayed", reason=replay_reason, tools=[tool])
                    continue


            novel_calls.append({
                "tool": tool,
                "arguments": normalized,
                "semantic_signature": semantic_signature,
                "action_signature": _action_signature(tool, normalized),
            })

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
                objective_context={"invalid_calls": preflight_invalid}, decision="tool_preflight",
                reason=f"invalid={preflight_invalid};replayed={preflight_replays}", repeated_outcome="stalled",
            )
            if repeat_count >= 2:
                text = "A LLM repetiu o mesmo batch de ferramentas inválido sem alterar o estado objetivo."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="TOOL_BUDGET_DECISION_LOOP"), full)
            _set_pending_observation_results(session, next_results)
            feedback = json.dumps({
                "code": "TOOL_BATCH_VALIDATION_FAILED",
                "invalid_calls": invalid_results,
                "replayed_calls": preflight_replays,
                "instruction": "No novel tool from an invalid batch was executed. Correct the rejected call using the canonical capability_index signature or active_tools contract; do not retry noncanonical argument names.",
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        # Replaying retained reality is useful once because it restores the requested
        # observation to the active context. Repeating the exact replay-only batch
        # against the same objective state is a physical loop, not a semantic
        # judgment about whether the model is making cognitive progress.
        if calls and not novel_calls and preflight_replays == len(calls):
            repeat_count = _record_rejected_decision(
                session, "REPLAY_ONLY_BATCH", replay_requests,
                objective_context={"replayed_calls": preflight_replays}, decision="tool_preflight",
                reason="OBSERVATION_REPLAY", tools=[str(item.get("tool") or "") for item in calls],
                repeated_outcome="stalled",
            )
            if repeat_count >= 2:
                text = "A LLM repetiu o mesmo batch de observações já reidratadas sem alterar o estado objetivo."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="OBSERVATION_REPLAY_LOOP"), full,
                )
            _set_pending_observation_results(session, next_results)
            feedback = ""
            continue

        physical_cost = len(novel_calls)
        budget_state = _tool_budget_state(session, config)
        remaining_tool_calls = int(budget_state["remaining"])

        if physical_cost > remaining_tool_calls:
            rejection_payload = [
                {"tool": item["tool"], "arguments": item["arguments"]}
                for item in novel_calls
            ]
            repeat_count = _record_rejected_decision(
                session, "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET", rejection_payload,
                objective_context={
                    "tool_calls_used": int(_physical_tool_calls(session) or 0),
                    "remaining_tool_calls": int(remaining_tool_calls),
                    "tool_call_limit": int(budget_state["limit"]),
                }, decision="physical_tool_limit",
                reason=f"requested={len(calls)};novel={physical_cost};remaining={remaining_tool_calls};limit={budget_state['limit']}",
                repeated_outcome="stalled",
            )
            if repeat_count >= 2:
                text = "A LLM repetiu a mesma decisão rejeitada sem alterar o estado canônico."
                return _return("failed", text, None, _details(session, "failed", config, failure_code="TOOL_BUDGET_DECISION_LOOP"), full)
            _set_pending_observation_results(session, next_results)
            feedback = json.dumps({
                "code": "REPEATED_REJECTED_DECISION" if repeat_count > 1 else "TOOL_BATCH_EXCEEDS_AUTHORIZED_BUDGET",
                "requested": len(calls),
                "novel_physical_calls": physical_cost,
                "replayed_calls": preflight_replays,
                "invalid_calls": preflight_invalid,
                "max_novel_batch_size_now": remaining_tool_calls,
                "instruction": "No novel tool from this batch was executed. Replayed observations remain available. Choose a smaller batch, use retained Evidence, or conclude.",
            }, ensure_ascii=False, separators=(",", ":"))
            continue

        for item in novel_calls:
            tool = item["tool"]
            normalized = item["arguments"]
            semantic_signature = item["semantic_signature"]
            execution = current_execution()
            terminal_failure = execution.terminal_capability(tool) if execution is not None else None
            if terminal_failure is not None:
                result = {
                    "status": "failed", "ok": False, "executed": False, "changed": False,
                    "error_code": "CAPABILITY_TERMINALLY_UNAVAILABLE", "retryable": False,
                    "detail": terminal_failure,
                }
                _record_decision(session, "tool_execution", "blocked", reason=result["error_code"], tools=[tool])
                model_result = _model_tool_result(session, tool, result, config, normalized)
                _record_observation(session, semantic_signature, tool, normalized, result, model_result, public_arguments=_observable_tool_arguments(tool, normalized), public_result=_observable_tool_result(tool, result))
                next_results.append(model_result)
                continue
            context = {
                "config": config, "projeto": project, "evidence": _evidence_items(session.evidence_ledger),
                "execution_trace": _current_trace_snapshot(session, config),
                "available_tools": sorted(allowed),
            }
            result = executar_tool(tool, normalized, context)
            if result.get("executed") is True:
                execution_outcome = "executed" if result.get("ok") is True else "failed"
            elif result.get("status") == "skipped":
                execution_outcome = "skipped"
            elif result.get("ok") is True:
                execution_outcome = "completed"
            else:
                execution_outcome = "failed"
            _record_decision(session, "tool_execution", execution_outcome, reason=result.get("error_code"), tools=[tool])
            execution = current_execution()
            if execution is not None and result.get("ok") is False and result.get("retryable") is False:
                execution.mark_terminal_capability(tool, error_code=str(result.get("error_code") or "CAPABILITY_UNAVAILABLE"), detail=result.get("detail"))
            model_result = _model_tool_result(session, tool, result, config, normalized)
            _record_observation(session, semantic_signature, tool, normalized, result, model_result, public_arguments=_observable_tool_arguments(tool, normalized), public_result=_observable_tool_result(tool, result))
            next_results.append(model_result)
            if not result.get("ok") and result.get("error_code") in TERMINAL_TOOL_ERRORS:
                text = f"A ferramenta encontrou um erro terminal: {result.get('error_code')}."
                return _return("failed", text, None, _details(session, "failed", config, failure_code=result.get("error_code")), full)

        _set_pending_observation_results(session, next_results)

        feedback = ""

    text = "A tarefa atingiu o limite de turnos do agente antes de concluir."
    return _return("failed", text, None, _details(session, "failed", config, failure_code="MAX_LLM_TURNS_EXCEEDED"), full)


def _executar_agente_bound(
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
        try:
            session = AgentSession.from_dict(retomar.get("estado") or {})
        except ValueError as error:
            if str(error) == "SESSION_SCHEMA_INCOMPATIBLE":
                text = "O estado persistido pertence a outro contrato de sessão e não pode ser retomado na Rev5.6."
                details = {
                    "status": "failed",
                    "failure_code": "SESSION_SCHEMA_INCOMPATIBLE",
                    "limitations": ["Rev5.6 não migra nem adapta sessões de revisões anteriores."],
                }
                return _return("failed", text, None, details, full)
            raise
        execution = current_execution()
        if execution is not None:
            execution.bind_session_baseline(session)
        _rehydrate_evidence(session.evidence_ledger, project.get("caminho_origem"), max_lines=max(1, int(((config or {}).get("agent") or {}).get("max_file_read_lines", 400) or 400)))
        if retomar.get("continuation_kind") == "user_input":
            try:
                session.request = _append_user_clarification(session.request, retomar, str(resposta_usuario or ""))
            except ValueError as error:
                text = "A pendência de clarificação não possui um contrato canônico válido."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code=str(error)), full,
                )
            # A clarification is canonical task input, not a transient observation.
            _clear_pending_observation_results(session)
            if execution is not None:
                execution.bind_canonical_request(session.request)
            return _run(session, config, project, full, conversation_context=None)
        if execution is not None:
            execution.bind_canonical_request(session.request)
        return _resume(session, retomar, config, project, full)
    session = AgentSession(str(objetivo or ""), task_id=task_id)
    execution = current_execution()
    if execution is not None:
        execution.bind_session_baseline(session)
        execution.bind_canonical_request(session.request)
    _set_pending_observation_results(session, _seed_failure_evidence(session.evidence_ledger, conversation_context))
    return _run(session, config, project, full, conversation_context=conversation_context)


def executar_agente(
    objetivo: str, config: Dict[str, Any], projeto: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None, retornar_detalhes: bool = False,
    task_id: Optional[str] = None, conversation_context: Any = None,
    resposta_usuario: Optional[str] = None, source_job_id: Optional[int] = None,
):
    """Run one canonical AgentSession inside one run-scoped ExecutionContext."""
    execution = ExecutionContext.from_config(config, task_id=task_id, source_job_id=source_job_id)
    token = bind_execution(execution)
    try:
        return _executar_agente_bound(
            objetivo, config, projeto=projeto, retomar=retomar,
            retornar_detalhes=retornar_detalhes, task_id=task_id,
            conversation_context=conversation_context, resposta_usuario=resposta_usuario,
        )
    finally:
        execution.cleanup_sandbox()
        reset_execution(token)
