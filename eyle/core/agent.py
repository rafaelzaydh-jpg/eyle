"""Single-session LLM-first programming agent.

There is one reasoning loop. The LLM decides what must be established, whether to answer, use a
tool, ask a blocking question or propose a patch. The runtime only validates
and executes concrete actions.
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.executar import (
    ErroLLM, PROMPT_AGENTE, PROMPT_CLAIM_VERIFIER,
    executar_agente as executar_agente_llm, executar_verificador_claims,
)
from llm.structured import claim_review_output_budget

from .session import AgentSession
from .continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from .execution_context import ExecutionContext, bind_execution, reset_execution, current_execution
from .decision import (
    record as _decision_record, record_rejection as _decision_record_rejection,
    history_view as _decision_history_view, requested_tool_names as _requested_tool_names,
)
from .operational_feedback import build_operational_feedback as _operational_feedback
from .write_transaction import begin as _begin_write_transaction, set_status as _set_write_status, record_validation as _record_write_validation, increment_attempt as _increment_write_attempt, record_failure as _record_write_failure, clear_failure as _clear_write_failure, public_view as _write_transaction_view
from .observation import (
    lookup as _lookup_observation, record as _record_observation,
    record_replay as _record_observation_replay, navigation_view as _observation_map,
    pending_results as _pending_observation_results, set_pending_results as _set_pending_observation_results,
    clear_pending_results as _clear_pending_observation_results, event_history as _tool_history_view,
    physical_tool_calls as _physical_tool_calls, replay_count as _observation_replay_count,
    material_items as _grounding_items, register_material_candidates as _grounding_register,
    freshest_material_for_locator as _grounding_freshest_for_locator,
    seed_runtime_failure as _seed_runtime_failure,
)
from .investigation import (
    apply_investigation_updates, investigation_grounding_ids,
)
from .tasks import apply_task_updates, task_state_view
from .security import _resolver_caminho_seguro
from .token_budget import available_user_prompt_tokens, estimate_tokens
from .text_hash import hash_faixa, hash_texto
from .post_write import (
    expected_outputs_from_patches,
    run_compileall_for_changes,
    verify_expected_outputs,
    verify_after_write as _verify_after_write,
)
from .tools import (
    TOOLS, executar_tool,
    gerar_catalogo_tools, gerar_indice_capabilities, validar_chamada_tool,
    capability_observation_signature as _observation_signature,
    capability_public_arguments as _observable_tool_arguments,
    capability_public_result as _observable_tool_result,
    capability_model_detail as _capability_model_detail,
    capability_find_covering as _capability_find_covering,
    capability_find_resource_failure as _capability_find_resource_failure,
    capability_validate_material_freshness as _validate_material_freshness,
    capability_rehydrate_materials as _rehydrate_grounding,
)
from .transactions import dry_run_patch_set, apply_patch_set, rollback_patch_set
from .validation import validate_final
from .claim_review import (
    claim_config, claim_grounding_ledger, compact_grounding,
    review_followup_feedback, normalize_claim_review, review_prompt,
    build_answer_anchors, build_request_anchors, compact_runtime_facts,
)

def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _conversation_history(context: Any) -> Dict[str, Any]:
    """Return canonical conversation background received from the Runtime boundary."""
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    normalized: List[Dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            continue
        role = str(item.get("role") or "")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
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
    # Expanded contracts are a hot working-set view, not
    # permanent prompt baggage. Keep only the most recently requested distinct
    # capabilities expanded; older capabilities return to the compact index and
    # remain fully callable. This is recency, not semantic routing.
    recent_limit = 2
    recent: List[str] = []
    for name in reversed(requested):
        if name not in recent:
            recent.append(name)
        if len(recent) >= recent_limit:
            break
    recent.reverse()
    active_set = set(recent)
    index = gerar_indice_capabilities(config=config, allowed_names=allowed - active_set)
    active = gerar_catalogo_tools(config=config, allowed_names=recent, compact=True) if recent else []
    return allowed, index, active


def _project_grounding_index(session: AgentSession, *, recent_limit: int = 8) -> List[Dict[str, Any]]:
    """Bound Main-visible Observation material without creating a second grounding copy."""
    full = session.grounding_index()
    pinned_ids = set(investigation_grounding_ids(session.investigation))
    pinned = [item for item in full if isinstance(item, dict) and str(item.get("id") or "") in pinned_ids]
    recent = [item for item in full if isinstance(item, dict) and str(item.get("id") or "") not in pinned_ids]
    return pinned + recent[-max(0, int(recent_limit)):]


def _project_observation_map(session: AgentSession, *, recent_limit: int = 5) -> List[Dict[str, Any]]:
    """Return bounded navigation without losing still-open Frontiers.

    Investigation-grounded entries and all open Frontier coordinates survive
    recency compaction. Older Frontier-bearing observations collapse into one
    tiny bundle instead of keeping full historical navigation rows.
    """
    full = [item for item in _observation_map(session) if isinstance(item, dict)]
    pinned_ids = set(investigation_grounding_ids(session.investigation))

    def key(item: Dict[str, Any]) -> Tuple[Any, Any, Any]:
        return (item.get("turn"), item.get("observation_signature"), item.get("tool"))

    pinned: List[Dict[str, Any]] = []
    for item in full:
        if pinned_ids.intersection(str(gid) for gid in item.get("grounding_ids") or []):
            clone = copy.deepcopy(item)
            clone["retained_for"] = "investigation_grounding"
            pinned.append(clone)
    pinned_keys = {key(item) for item in pinned}
    candidates = [item for item in full if key(item) not in pinned_keys]
    recent = [copy.deepcopy(item) for item in candidates[-max(0, int(recent_limit)):]]
    recent_keys = {key(item) for item in recent}

    open_frontiers: List[Dict[str, Any]] = []
    seen_frontiers: set[str] = set()
    for item in candidates:
        if key(item) in recent_keys:
            continue
        for frontier in item.get("frontiers") or []:
            if not isinstance(frontier, dict) or frontier.get("status") != "open":
                continue
            frontier_id = str(frontier.get("id") or "")
            if not frontier_id or frontier_id in seen_frontiers:
                continue
            seen_frontiers.add(frontier_id)
            clone = copy.deepcopy(frontier)
            clone.setdefault("source_tool", item.get("tool"))
            open_frontiers.append(clone)

    retained: List[Dict[str, Any]] = []
    if open_frontiers:
        retained.append({"retained_for": "open_frontiers", "frontiers": open_frontiers})
    return pinned + retained + recent

def _project_pending_results(session: AgentSession, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Project one bounded fresh delta; canonical bytes stay in ObservationLedger.

    Raw tool output is a bounded working set, not permission to
    spend the remaining job budget.  Bounds tighten as the job gets older or
    physical token headroom shrinks, while every result keeps status, grounding
    refs and Frontier coordinates.
    """
    turn = max(1, int(getattr(session, "turn", 1) or 1))
    if turn <= 3:
        total_limit, per_result_limit = 14000, 5000
    elif turn <= 6:
        total_limit, per_result_limit = 10000, 3800
    else:
        total_limit, per_result_limit = 7000, 2600
    execution = current_execution()
    remaining = execution.physical_tokens_remaining if execution is not None else None
    if remaining is not None and remaining < 30000:
        total_limit, per_result_limit = min(total_limit, 6000), min(per_result_limit, 2200)
    if remaining is not None and remaining < 15000:
        total_limit, per_result_limit = min(total_limit, 4200), min(per_result_limit, 1600)

    projected: List[Dict[str, Any]] = []
    for raw in _pending_observation_results(session):
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        for _ in range(64):
            if len(json.dumps(item, ensure_ascii=False, default=str)) <= per_result_limit:
                break
            detail = item.get("detail")
            if isinstance(detail, (dict, list)) and _shrink_structured_once(detail):
                item["context_compacted"] = True
                continue
            if isinstance(detail, str) and len(detail) > 900:
                item["detail"] = _bounded_context_text(detail, max(900, per_result_limit // 2))
                item["context_compacted"] = True
                continue
            compact = _minimal_tool_context(item, detail_char_limit=max(700, per_result_limit // 2))
            if len(json.dumps(compact, ensure_ascii=False, default=str)) < len(json.dumps(item, ensure_ascii=False, default=str)):
                item = compact
                continue
            break
        projected.append(item)

    def size(value: Dict[str, Any]) -> int:
        return len(json.dumps(value, ensure_ascii=False, default=str))

    while projected and sum(size(item) for item in projected) > total_limit:
        index = max(range(len(projected)), key=lambda idx: size(projected[idx]))
        current = projected[index]
        compact = _minimal_tool_context(current, detail_char_limit=900)
        if size(compact) < size(current):
            projected[index] = compact
            continue
        detail = current.get("detail")
        if detail not in (None, {}, [], ""):
            current["detail"] = {"context_compacted": True, "note": "full fresh result retained in Observation"}
            current["context_compacted"] = True
            continue
        break
    return projected


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
        "failure_scope": result.get("failure_scope"),
        "failure_resource": result.get("failure_resource"),
        "detail": detail,
    }


def _bounded_context_text(text: Any, max_chars: int, *, marker: str = "...[context cropped]...") -> str:
    """Keep both ends of long text so diagnostics/tails are not silently lost."""
    value = str(text or "")
    max_chars = max(200, int(max_chars or 0))
    if len(value) <= max_chars:
        return value
    marker = "\n" + str(marker or "...[context cropped]...") + "\n"
    room = max(0, max_chars - len(marker))
    head = max(1, room // 2)
    tail = max(1, room - head)
    return value[:head].rstrip() + marker + value[-tail:].lstrip()


def _bounded_source_text(text: Any, max_chars: int, *, source_span: Optional[Tuple[Any, Any]] = None) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    marker = "...[source preview cropped; request a narrower physical source range for more]..."
    if source_span and source_span[0] is not None and source_span[1] is not None:
        marker = f"...[source span {source_span[0]}-{source_span[1]} cropped; request a narrower physical source range for more]..."
    return _bounded_context_text(value, max_chars, marker=marker)


def _material_model_view(item: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Generic compact view of one canonical Material."""
    context_view = _context_view_config(config)
    max_chars = max(300, int(context_view.get("max_source_preview_chars", 6000) or 6000))
    text = str(item.get("numbered_content") or item.get("content") or "")
    entry = {
        "grounding_id": item.get("id"),
        "locator": copy.deepcopy(item.get("locator") or {}),
        "source_type": item.get("source_type"),
        "content_hash": item.get("content_hash"),
    }
    if text:
        entry["excerpt"] = _bounded_source_text(text, max_chars)
        entry["excerpt_complete"] = len(text) <= max_chars
    if isinstance(item.get("metadata"), dict) and item.get("metadata"):
        entry["metadata"] = copy.deepcopy(item.get("metadata"))
    return {k: v for k, v in entry.items() if v not in (None, "", {}, [])}


def _model_tool_result(session: AgentSession, tool: str, result: Dict[str, Any], config: Optional[Dict[str, Any]] = None, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Project one capability result without interpreting the capability in Agent."""
    produces_grounding = bool((TOOLS.get(tool) or {}).get("produces_grounding"))
    grounding_ids = _grounding_register(
        session.observation_ledger, result.get("observations") or []
    ) if produces_grounding else []
    detail = _capability_model_detail(tool, result.get("detail"), grounding_ids, config or {})
    model_result = {
        "tool": tool, "status": result.get("status"), "ok": result.get("ok"),
        "executed": result.get("executed"), "changed": result.get("changed"),
        "error_code": result.get("error_code"), "retryable": result.get("retryable"),
        "failure_scope": result.get("failure_scope"), "failure_resource": result.get("failure_resource"),
        "detail": detail, "grounding_ids": grounding_ids,
    }
    for field in ("coverage", "frontiers"):
        value = result.get(field)
        if value:
            model_result[field] = copy.deepcopy(value)
    if result.get("handles"):
        model_result["handles"] = copy.deepcopy(result.get("handles"))
    return {k: v for k, v in model_result.items() if v is not None}


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
        # Structured capability results may contain large lists; reduce them generically.
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

        # Raw strings can still dominate a prompt regardless of capability.
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
            value[key] = _bounded_context_text(raw, keep + len(crop_suffix))
            value[f"{key}_context_original_chars"] = max(
                int(value.get(f"{key}_context_original_chars", 0) or 0), len(raw),
            )
            value["context_truncated"] = True
            return True

        # Then recurse into nested containers; large arrays may hide below the top level.
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


def _minimal_tool_context(result: Dict[str, Any], *, detail_char_limit: int = 3000) -> Dict[str, Any]:
    """Last-resort bounded model view while preserving canonical grounding refs."""
    compact = {
        key: result.get(key)
        for key in ("tool", "status", "ok", "executed", "changed", "error_code", "grounding_ids", "frontiers")
        if result.get(key) is not None
    }
    detail = copy.deepcopy(result.get("detail"))
    if isinstance(detail, dict):
        for _ in range(64):
            if len(json.dumps(detail, ensure_ascii=False, default=str)) <= max(700, int(detail_char_limit or 3000)):
                break
            if not _shrink_structured_once(detail):
                break
        compact["detail"] = detail
    elif isinstance(detail, str):
        compact["detail"] = _bounded_context_text(detail, max(500, int(detail_char_limit or 3000)))
    else:
        compact["detail"] = detail
    compact["context_compacted"] = True
    return compact


def _crop_payload(payload: Dict[str, Any], budget: int, chars_per_token: int) -> Dict[str, Any]:
    """Fit the model view without altering canonical Runtime state."""
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
                        result["detail"] = _bounded_context_text(raw_detail, keep + len(crop_suffix))
                        result["context_compacted"] = True
                        reduced = True
                        break
        if reduced:
            continue

        observation_map = payload.get("observation_map") or []
        if len(observation_map) > 5:
            retained = [
                item for item in observation_map
                if isinstance(item, dict) and item.get("retained_for") in {"open_frontiers", "investigation_grounding"}
            ]
            retained_ids = {id(item) for item in retained}
            recent = [item for item in observation_map if id(item) not in retained_ids]
            slots = max(0, 5 - len(retained))
            compacted = retained + (recent[-slots:] if slots else [])
            if compacted != observation_map:
                payload["observation_map"] = compacted
                continue

        grounding_index = payload.get("grounding_index") or []
        if len(grounding_index) > 8:
            pinned = [item for item in grounding_index if isinstance(item, dict) and item.get("pinned") is True]
            pinned_ids = {str(item.get("id") or "") for item in pinned}
            recent = [item for item in grounding_index if str((item or {}).get("id") or "") not in pinned_ids]
            compacted = pinned if len(pinned) >= 8 else pinned + recent[-max(0, 8 - len(pinned)):]
            if compacted != grounding_index:
                payload["grounding_index"] = compacted
                continue

        background = payload.get("conversation_background") or []
        if len(background) > 1:
            payload["conversation_background"] = background[1:]
            continue

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
    return str((review or {}).get("verdict") or "") == "challenge"


def _persistent_claim_feedback(session: AgentSession, config: Dict[str, Any]) -> str:
    if claim_config(config)["mode"] == "off" or not _claim_review_has_debt(session.claim_review):
        return ""
    return review_followup_feedback(session.claim_review)


def _agent_config(config: Dict[str, Any], session: AgentSession, project: Dict[str, Any]) -> Dict[str, Any]:
    """Return the physical LLM configuration without semantic phase steering."""
    clone = dict(config)
    llm = dict(config.get("llm") or {})
    configured_ceiling = max(1, int(llm.get("agent_max_tokens", 3600) or 3600))
    llm["agent_max_tokens"] = configured_ceiling
    llm["agent_max_tokens_configured"] = configured_ceiling
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


def _project_conversation_background(session: AgentSession) -> List[Dict[str, Any]]:
    """Keep enough prior conversation for continuity without replaying it forever."""
    raw = [item for item in (session.conversation_background or []) if isinstance(item, dict)]
    keep = raw[-3:] if int(session.turn or 1) <= 1 else raw[-2:]
    projected: List[Dict[str, Any]] = []
    for item in keep:
        clone = copy.deepcopy(item)
        if isinstance(clone.get("content"), str):
            clone["content"] = _bounded_context_text(clone["content"], 1200)
        projected.append(clone)
    return projected



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
    token_remaining = execution.physical_tokens_remaining if execution is not None else None
    payload = {
        "request": session.request,
        "turn": session.turn,
        "investigation": session.investigation,
        "task_state": task_state_view(session.tasks),
        "project": _project_descriptor(project),
        "conversation_background": _project_conversation_background(session),
        "observation_map": _project_observation_map(session),
        "latest_tool_results": _project_pending_results(session, config),
        "grounding_index": _project_grounding_index(session),
        "physical_limits": {
            "physical_tokens_remaining": token_remaining,
            "terminal_capabilities": execution.terminal_capabilities_view() if execution is not None else {},
        },
        "capability_index": capability_index,
        "active_tools": active_tools,
        "operational_feedback": _operational_feedback(session),
        "runtime_feedback": _merged_runtime_feedback(feedback, _persistent_claim_feedback(session, config)),
    }
    claim_config(config)
    llm_cfg = config.get("llm") or {}
    output_tokens = int(llm_cfg.get("agent_max_tokens", 3600) or 3600)
    output_tokens_configured = int(llm_cfg.get("agent_max_tokens_configured", output_tokens) or output_tokens)
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
    if execution is not None:
        execution.begin_call(mode="agent", turn=session.turn, prompt={
            "characters": len(prompt), "estimated_tokens": post_crop_tokens, "tool_count": len(allowed),
            "active_tool_count": len(active_tools),
            "prompt_budget_tokens": prompt_budget, "window_user_prompt_budget_tokens": window_prompt_budget,
            "output_tokens_requested": output_tokens_configured, "output_tokens_reserved": output_tokens,
            "completion_ceiling_clamped": output_tokens < output_tokens_configured,
            "system_prompt_characters": len(PROMPT_AGENTE),
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


def _fit_claim_grounding_view(
    session: AgentSession, config: Dict[str, Any], answer: str, selected_ids: List[str],
    *, output_tokens: int, answer_anchors: Optional[List[Dict[str, Any]]] = None,
    request_anchors: Optional[List[Dict[str, Any]]] = None,
    runtime_facts: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str, List[Dict[str, Any]], Dict[str, int]]:
    """Fit all Main-selected observed material into the Claim working set."""
    cfg = claim_config(config)
    verifier_config = _claim_llm_config(config, cfg["mode"])
    context_cfg = (config or {}).get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    execution = current_execution()
    prompt_budget = available_user_prompt_tokens(
        verifier_config, PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
        token_estimate_multiplier=(execution.prompt_token_calibration if execution is not None else 1.0),
    )

    def build(cap: int) -> Tuple[List[Dict[str, Any]], int]:
        view = compact_grounding(
            _grounding_items(session.observation_ledger), selected_ids, max_chars_per_item=max(0, int(cap)),
        )
        prompt = review_prompt(
            answer, view, session.request, answer_anchors=answer_anchors, request_anchors=request_anchors,
            runtime_facts=runtime_facts,
        )
        return view, estimate_tokens(prompt, chars_per_token)

    maximum = int(cfg["grounding"]["max_chars_per_item"])
    full_view, full_tokens = build(maximum)
    if full_tokens <= prompt_budget:
        return True, "ok", full_view, {
            "prompt_budget_tokens": prompt_budget,
            "prompt_estimated_tokens": full_tokens,
            "grounding_excerpt_chars_per_item": maximum,
            "selected_grounding_count": len(selected_ids),
        }

    minimum = min(120, maximum)
    minimum_view, minimum_tokens = build(minimum)
    if minimum_tokens > prompt_budget:
        return False, f"CLAIM_REVIEW_WORKING_SET_EXCEEDED:{minimum_tokens}>{prompt_budget}", [], {
            "prompt_budget_tokens": prompt_budget,
            "prompt_estimated_tokens": minimum_tokens,
            "grounding_excerpt_chars_per_item": minimum,
            "selected_grounding_count": len(selected_ids),
        }

    best_view, best_tokens, best_cap = minimum_view, minimum_tokens, minimum
    low, high = minimum, maximum
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
        "grounding_excerpt_chars_per_item": best_cap,
        "selected_grounding_count": len(selected_ids),
    }


def _is_structured_response_error(error: Exception, profile: Optional[str] = None) -> bool:
    code = str(getattr(error, "error_code", "") or "")
    prefix = "STRUCTURED_RESPONSE_INVALID:"
    if not code.startswith(prefix):
        return False
    return profile is None or code.startswith(prefix + profile + ":")


def _run_claim_verification(
    session: AgentSession, config: Dict[str, Any], answer: str, grounding_ids: List[str],
    *, project_root: Any = None,
) -> Tuple[bool, str, Dict[str, Any], List[Dict[str, Any]]]:
    """Run Claim against Main-selected Observation material."""
    cfg = claim_config(config)
    execution = current_execution()
    if execution is not None:
        execution.assert_canonical_request(session.request)
    selected_ids = list(dict.fromkeys(str(item) for item in (grounding_ids or []) if str(item)))
    grounding = _grounding_items(session.observation_ledger)

    fresh, freshness_reason = _validate_material_freshness(_grounding_items(session.observation_ledger), selected_ids, project_root)
    if not fresh:
        return False, freshness_reason, {}, []

    verifier_config = _claim_llm_config(config, cfg["mode"])
    answer_anchors = build_answer_anchors(answer)
    request_anchors = build_request_anchors(session.request)
    runtime_facts = compact_runtime_facts(session.observation_ledger)
    output_tokens = claim_review_output_budget()
    fit_ok, fit_reason, view, fit_meta = _fit_claim_grounding_view(
        session, config, answer, selected_ids, output_tokens=output_tokens,
        answer_anchors=answer_anchors, request_anchors=request_anchors, runtime_facts=runtime_facts,
    )
    if not fit_ok:
        return False, fit_reason, {}, []
    visible_ids = [str(item.get("ref") or "").split(":", 1)[1] for item in view if str(item.get("ref") or "").startswith("observation:")]
    visible_set = set(visible_ids)
    if any(item not in visible_set for item in selected_ids):
        missing = [item for item in selected_ids if item not in visible_set]
        return False, "CLAIM_REVIEW_UNKNOWN_GROUNDING:" + ",".join(missing), {}, []

    verifier_config["llm"].pop("downstream_completion_reserve_tokens", None)
    verifier_config["llm"]["claim_verifier_max_tokens"] = output_tokens
    prompt = review_prompt(
        answer, view, session.request, answer_anchors=answer_anchors, request_anchors=request_anchors,
        runtime_facts=runtime_facts,
    )
    fit_meta = dict(fit_meta)
    fit_meta.update({
        "answer_anchor_count": len(answer_anchors),
        "request_anchor_count": len(request_anchors),
        "runtime_fact_count": len(runtime_facts),
    })
    _record_aux_prompt(
        session, verifier_config, mode="claim_verification", prompt=prompt,
        system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens, metadata=fit_meta,
    )
    try:
        parsed = executar_verificador_claims(prompt, verifier_config)
    except ErroLLM as error:
        recoverable_protocol_error = (
            _is_structured_response_error(error, "claim_verifier")
            or str(getattr(error, "error_code", "") or "") == "MODEL_OUTPUT_TRUNCATED"
        )
        if not recoverable_protocol_error:
            raise
        _record_decision(session, "claim_protocol", "rejected", reason=error.error_code)
        retry_payload = json.loads(prompt)
        retry_payload["protocol_feedback"] = {
            "code": "CANONICAL_CLAIM_RECOVERY",
            "instruction": (
                "Return only the canonical verdict and the smallest sufficient blocker set. "
                "Use at most 3 issues, at most 4 grounding refs per issue, and one concise reason sentence. "
                "Do not enumerate secondary defects."
            ),
        }
        retry_prompt = json.dumps(retry_payload, ensure_ascii=False, separators=(",", ":"), default=str)
        _record_aux_prompt(
            session, verifier_config, mode="claim_verification", prompt=retry_prompt,
            system_prompt=PROMPT_CLAIM_VERIFIER, output_tokens=output_tokens,
            metadata={**fit_meta, "protocol_retry": True, "protocol_retry_cause": error.error_code},
        )
        _record_decision(session, "claim_protocol", "retry", reason="CANONICAL_CLAIM_RECOVERY")
        try:
            parsed = executar_verificador_claims(retry_prompt, verifier_config)
        except ErroLLM as retry_error:
            _record_decision(session, "claim_protocol", "failed", reason=retry_error.error_code)
            raise

    fresh, freshness_reason = _validate_material_freshness(_grounding_items(session.observation_ledger), selected_ids, project_root)
    if not fresh:
        return False, freshness_reason, {}, view
    if not isinstance(parsed, dict):
        return False, "CLAIM_REVIEW_PROTOCOL_ERROR:STRUCTURED_OBJECT_REQUIRED", {}, view
    ok, reason, review = normalize_claim_review(
        parsed, grounding, answer=answer, answer_anchors=answer_anchors,
        request_anchors=request_anchors, visible_grounding_ids=visible_ids,
        runtime_facts=runtime_facts,
    )
    return ok, reason, review, view

def _append_claim_review(session: AgentSession, review: Dict[str, Any]) -> None:
    session.claim_review = {
        "turn": session.turn,
        "verdict": str(review.get("verdict") or ""),
        "issues": [dict(item) for item in review.get("issues") or [] if isinstance(item, dict)],
    }


def _grounding_usage_metrics(session: AgentSession) -> Dict[str, int]:
    """Small operational accounting for one canonical material store."""
    grounding = _grounding_items(session.observation_ledger)
    all_ids = {str(item) for item in grounding if str(item)}
    target_ids = set(investigation_grounding_ids(session.investigation))
    claim_ids: set[str] = set()
    for issue in (session.claim_review or {}).get("issues") or []:
        if isinstance(issue, dict):
            claim_ids.update(str(item) for item in issue.get("grounding_ids") or [] if str(item))
    actions_with_grounding = sum(
        1 for item in _tool_history_view(session, limit=200)
        if isinstance(item, dict) and item.get("executed") is True and item.get("grounding_ids")
    )
    return {
        "total_grounding_count": len(all_ids),
        "investigation_grounding_count": len(target_ids & all_ids),
        "claim_grounding_count": len(claim_ids & all_ids),
        "unreferenced_grounding_count": len(all_ids - target_ids - claim_ids),
        "tool_actions_with_grounding": actions_with_grounding,
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
        "error_code": item.get("error_code"), "observation_signature": item.get("observation_signature"),
        "arguments": copy.deepcopy(item.get("arguments") or {}),
        "result": copy.deepcopy(item.get("result") or {}),
        "grounding_ids": list(item.get("grounding_ids") or []),
        "frontier_ids": list(item.get("frontier_ids") or []),
        "replay_reason": item.get("replay_reason"),
    } for item in job_tool_events[-50:] if isinstance(item, dict)]
    decision_history = [copy.deepcopy(item) for item in job_decision_events[-50:] if isinstance(item, dict)]
    job_tool_calls = sum(1 for item in job_tool_events if isinstance(item, dict) and item.get("executed") is True)
    total_replays = int(_observation_replay_count(session) or 0)
    replay_start = int(execution.observation_replay_start or 0) if execution is not None else 0
    job_replays = max(0, total_replays - replay_start)
    grounding = _grounding_items(session.observation_ledger)
    start_grounding = set(execution.grounding_ids_start or []) if execution is not None else set()
    job_grounding_count = len(set(grounding) - start_grounding)
    return {
        "status": status, "execution_id": session.execution_id, "investigation": session.investigation, "tasks": session.tasks,
        "turns": int(execution.agent_turns if execution is not None else session.turn),
        "tool_calls": job_tool_calls,
        "workspace_epoch": int(session.workspace_epoch or 0),
        "observation_replays": job_replays,
        "observation_ledger_size": len(job_tool_events),
        "grounding_count_total": job_grounding_count,
        "grounding_usage": _grounding_usage_metrics(session),
        "task_totals": {
            "turns": int(session.turn), "tool_calls": _physical_tool_calls(session),
            "observation_replays": int(_observation_replay_count(session) or 0),
            "observation_events": len(all_tool_events), "grounding_count": len(grounding),
            "decision_events": len(all_decision_events),
        },
        "tools_used": [item.get("tool") for item in tool_history if (item.get("result") or {}).get("executed") is True],
        "tool_history": tool_history, "decision_history": decision_history,
        "grounding": session.grounding_index(),
        "claim_grounding": claim_grounding_ledger(session.claim_review, grounding) if session.claim_review else [],
        "claim_review": {
            "verdict": session.claim_review.get("verdict"),
            "issues": [dict(item) for item in session.claim_review.get("issues") or [] if isinstance(item, dict)],
        } if session.claim_review else {},
        "operational_feedback": _operational_feedback(session),
        "limitations": list(limitations or []), "failure_code": failure_code,
        "write_failure": dict(session.write_transaction.get("failure") or {}) if isinstance(session.write_transaction, dict) and session.write_transaction.get("failure") else None,
        "llm_usage": execution.usage_view() if execution else {},
        "llm_calls": execution.ledger_view() if execution else [],
        "write_transaction": _write_transaction_view(session.write_transaction),
    }


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
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "write_confirmation",
        "question": text,
        "session": session.to_dict(),
        "transaction_id": tx.get("transaction_id"),
    }
    validate_pending_continuation(pending)
    return text, pending


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
    context = {"config": config, "projeto": project, "grounding": _grounding_items(session.observation_ledger), "observation_ledger": session.observation_ledger}
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

    tests = _verify_after_write(config, context)
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
        material = _grounding_freshest_for_locator(
            session.observation_ledger, {"kind": "file", "path": path}, match_fields=("kind", "path")
        )

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

        locator = dict(material.get("locator") or {}) if isinstance(material, dict) and isinstance(material.get("locator"), dict) else {}
        if operation in {"replace", "delete", "update"}:
            if not exists:
                return arguments, f"{operation} requires an existing file: {path}"
            if not material or locator.get("kind") != "file" or not material.get("source_version"):
                return arguments, f"read the existing file before {operation}: {path}"
            if operation == "replace":
                whole_file = (
                    int(locator.get("line_start") or 0) == 1
                    and int(locator.get("line_end") or 0) == int(locator.get("total_lines") or -1)
                )
                if not whole_file:
                    return arguments, f"replace requires a fresh whole-file read: {path}"
            patch["file_hash_expected"] = material["source_version"]
        elif operation == "create":
            if exists:
                return arguments, f"create cannot overwrite an existing file: {path}; use replace"

        if operation == "update":
            start, end = patch["line_start"], patch["line_end"]
            if int(locator.get("line_start") or 0) == start and int(locator.get("line_end") or 0) == end:
                patch["range_hash_expected"] = material.get("content_hash")
            else:
                content = material.get("content")
                ev_start = int(locator.get("line_start") or 0)
                ev_end = int(locator.get("line_end") or 0)
                if isinstance(content, str) and ev_start == 1 and ev_end == int(locator.get("total_lines") or -1):
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
    facts: Optional[Dict[str, Any]] = None,
) -> None:
    _decision_record(
        session.decision_ledger, turn=session.turn, decision=decision_type,
        outcome=outcome, reason=reason, tools=tools, facts=facts,
    )

def _action_signature(tool: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def _record_rejected_decision(
    session: AgentSession, code: str, payload: Any = None, *,
    decision: Optional[str] = None, tools: Optional[List[str]] = None, reason: Optional[str] = None,
) -> None:
    _decision_record_rejection(
        session.decision_ledger, turn=session.turn, code=code,
        decision=decision, tools=tools, reason=reason,
    )


def _rehydrate_observation(session: AgentSession, entry: Dict[str, Any], config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    replay = copy.deepcopy(entry.get("replay_result")) if isinstance(entry.get("replay_result"), dict) else None
    grounding = _grounding_items(session.observation_ledger)
    grounding_ids = [str(item) for item in entry.get("grounding_ids") or [] if str(item) in grounding]
    frontier_ids = [str(item) for item in entry.get("frontier_ids") or [] if str(item)]
    tool = str(entry.get("tool") or "")
    if replay is None and grounding_ids and any((grounding.get(gid) or {}).get("rehydration_error") for gid in grounding_ids):
        return None
    if replay is None and entry.get("failure_scope") in {"request", "resource"}:
        replay = {
            "tool": tool, "status": "failed", "ok": False, "executed": False, "changed": False,
            "error_code": entry.get("failure_error_code") or "STABLE_PHYSICAL_FAILURE",
            "retryable": False, "failure_scope": entry.get("failure_scope"),
            "failure_resource": entry.get("failure_resource"),
            "detail": entry.get("failure_detail") or "stable physical failure",
            "grounding_ids": grounding_ids, "frontiers": frontier_ids,
        }
    if replay is None:
        materials = []
        for grounding_id in grounding_ids:
            item = dict(grounding.get(grounding_id) or {})
            item["id"] = grounding_id
            materials.append(_material_model_view(item, config))
        detail: Any = materials[0] if len(materials) == 1 else {"materials": materials}
        replay = {
            "tool": tool, "status": "success", "ok": True, "executed": False,
            "changed": False, "error_code": None, "detail": detail,
            "grounding_ids": grounding_ids, "frontiers": frontier_ids,
        }
    replay["tool"] = tool or replay.get("tool")
    replay["status"] = "replayed"
    replay["executed"] = False
    replay["changed"] = False
    replay["replayed"] = True
    replay["source_turn"] = entry.get("turn")
    replay["grounding_ids"] = grounding_ids or list(replay.get("grounding_ids") or [])
    return replay


def _final_validation_feedback(reason: str) -> str:
    return json.dumps({"code": "FINAL_VALIDATION_ERROR", "detail": str(reason)}, ensure_ascii=False, separators=(",", ":"))


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
    claim_config(config)  # validate once at the execution boundary
    feedback = ""

    execution = current_execution()
    run_turns = 0
    protocol_retry_used = False
    while True:
        if _deadline_exceeded(config):
            text = "A tarefa excedeu o prazo de execução."
            return _return("failed", text, None, _details(session, "failed", config, failure_code="TASK_DEADLINE_EXCEEDED"), full)

        session.turn += 1
        run_turns += 1
        if execution is not None:
            execution.agent_turns = run_turns
        call_feedback = feedback
        while True:
            try:
                decision, allowed = _call_agent(session, config, project, conversation_context, call_feedback)
                break
            except ErroLLM as error:
                if _is_structured_response_error(error, "agent"):
                    observed = getattr(error, "structured_observed", None)
                    action_kind = None
                    if isinstance(observed, dict) and isinstance(observed.get("action"), dict):
                        action_kind = str((observed.get("action") or {}).get("kind") or "") or None
                    _record_decision(session, "protocol", "rejected", reason=error.error_code)
                    if not protocol_retry_used:
                        protocol_retry_used = True
                        _record_decision(session, "protocol", "retry", reason="CANONICAL_DECISION_RETRY")
                        retry_feedback = json.dumps({
                            "code": "CANONICAL_DECISION_RETRY",
                            "rejected_code": error.error_code,
                            "instruction": (
                                "Your previous decision envelope was rejected before any action executed. "
                                "Decide again from the unchanged canonical state and return exactly one valid action object. "
                                "Do not reconstruct or repair the rejected JSON."
                            ),
                        }, ensure_ascii=False, separators=(",", ":"))
                        call_feedback = _merged_runtime_feedback(feedback, retry_feedback)
                        continue
                    text = "A LLM não produziu uma decisão estruturada válida após a única nova tentativa canônica permitida."
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
            raw_updates, previous=session.investigation,
            grounding=_grounding_items(session.observation_ledger),
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
        for item in rejected_updates:
            reason = str(item.get("reason") or "INVESTIGATION_UPDATE_REJECTED")
            code = reason.split(":", 1)[0]
            _record_rejected_decision(session, code, decision="investigation_update", reason=reason)

        target_state = ",".join(
            f"{item.get('id')}={item.get('status')}"
            for item in session.investigation if isinstance(item, dict)
        )
        _record_decision(
            session, "investigation_contract", "accepted",
            reason=target_state or "empty",
        )

        if rejected_updates:
            # Investigation is Main-owned epistemic notebook state. Invalid notebook
            # deltas are rejected independently and never cancel a valid action from
            # the same turn. If another Agent turn occurs, the facts are available
            # as protocol feedback; Runtime does not prescribe a correction.
            feedback = json.dumps({
                "code": "INVESTIGATION_UPDATES_PARTIALLY_REJECTED",
                "accepted_updates": [
                    {"id": item.get("id"), "changed": bool(item.get("changed"))}
                    for item in accepted_updates
                ],
                "rejected_updates": rejected_updates,
                "canonical_investigation": session.investigation,
                "available_grounding_ids": sorted(_grounding_items(session.observation_ledger)),
            }, ensure_ascii=False, separators=(",", ":"))

        raw_task_updates = decision.get("task_updates")
        if raw_task_updates is None:
            raw_task_updates = []
        prospective_tasks, accepted_task_updates, rejected_task_updates = apply_task_updates(
            raw_task_updates, previous=session.tasks,
        )
        session.tasks = prospective_tasks
        by_task_id = {
            str(item.get("id") or ""): item
            for item in session.tasks if isinstance(item, dict) and str(item.get("id") or "")
        }
        for item in accepted_task_updates:
            current_task = by_task_id.get(str(item.get("id") or ""), {})
            _record_decision(
                session, "task_update",
                "committed" if item.get("changed") else "unchanged",
                reason=f"{item.get('id')}={item.get('status')}",
                facts={
                    "task_id": item.get("id"),
                    "parent_id": current_task.get("parent_id"),
                    "description": current_task.get("description"),
                    "status": current_task.get("status"),
                    "result": current_task.get("result"),
                },
            )
        for item in rejected_task_updates:
            reason = str(item.get("reason") or "TASK_UPDATE_REJECTED")
            code = reason.split(":", 1)[0]
            _record_rejected_decision(session, code, decision="task_update", reason=reason)

        task_contract_state = ",".join(
            f"{item.get('id')}={item.get('status')}"
            for item in session.tasks if isinstance(item, dict)
        )
        _record_decision(
            session, "task_contract", "accepted",
            reason=task_contract_state or "empty",
        )

        if rejected_task_updates:
            task_feedback = {
                "code": "TASK_UPDATES_PARTIALLY_REJECTED",
                "accepted_updates": [
                    {"id": item.get("id"), "changed": bool(item.get("changed"))}
                    for item in accepted_task_updates
                ],
                "rejected_updates": rejected_task_updates,
                "canonical_task_state": task_state_view(session.tasks),
            }
            feedback = _merged_runtime_feedback(
                feedback,
                json.dumps(task_feedback, ensure_ascii=False, separators=(",", ":")),
            )

        action = decision.get("action") if isinstance(decision.get("action"), dict) else {}
        action_kind = str(action.get("kind") or "")

        if action_kind == "needs_user":
            question = str(action.get("question") or "").strip()
            missing = str(action.get("missing_information") or "").strip()
            if not question or not missing:
                text = "A LLM produziu um pedido de informação incompleto."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="AGENT_NEEDS_USER_INVALID"), full,
                )
            _record_decision(session, "needs_user", "accepted", reason=missing)
            pending = {
                "pending_schema_version": PENDING_SCHEMA_VERSION,
                "continuation_kind": "user_input",
                "question": question,
                "session": session.to_dict(),
                "clarification": {"question": question, "missing_information": missing},
            }
            validate_pending_continuation(pending)
            return _return("needs_user", question, pending, _details(session, "needs_user", config), full)

        if action_kind == "patches":
            patches = list(action.get("patches") or [])
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
            enriched, patch_error = _enrich_patch_set(session, project, {"patches": patches})
            if patch_error:
                _record_decision(session, "patch_validation", "rejected", reason="PATCH_SCHEMA_INVALID")
                feedback = json.dumps({"code": "PATCH_SCHEMA_INVALID", "detail": str(patch_error)}, ensure_ascii=False, separators=(",", ":"))
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
                feedback = json.dumps({"code": code, "detail": _diagnostic_text(dry)}, ensure_ascii=False, separators=(",", ":"))
                continue

            _record_decision(session, "patch_validation", "validated")
            _set_write_status(session.write_transaction, "dry_run_valid")
            text, pending = _pending_patch_set(session)
            return _return("needs_user", text, pending, _details(session, "needs_user", config), full)

        if action_kind == "final":
            claims_cfg = claim_config(config)
            project_root = project.get("caminho_origem")
            final_obj = {
                "answer": action.get("answer"),
                "limitations": list(action.get("limitations") or []),
                "grounding_ids": list(action.get("grounding_ids") or []),
            }
            ok, reason, answer, limitations = validate_final(
                final_obj, _grounding_items(session.observation_ledger),
            )

            if ok and claims_cfg["mode"] == "off":
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            if ok:
                review_grounding_ids = list(dict.fromkeys(
                    str(item) for item in final_obj.get("grounding_ids") or [] if str(item)
                ))
                _record_decision(
                    session, "final", "provisional",
                    facts={
                        "grounding_ids": review_grounding_ids,
                        "workspace_epoch": int(session.workspace_epoch or 0),
                    },
                )
                try:
                    review_ok, review_reason, review, _grounding_view = _run_claim_verification(
                        session, config, answer, review_grounding_ids, project_root=project_root,
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
                    if str(review_reason).startswith("GROUNDING_STALE:"):
                        feedback = json.dumps({
                            "code": "GROUNDING_STALE",
                            "detail": review_reason,
                        }, ensure_ascii=False, separators=(",", ":"))
                        _clear_pending_observation_results(session)
                        continue
                    text = f"A verificação de claims ficou inválida: {review_reason}."
                    return _return("failed", text, None, _details(session, "failed", config, failure_code=review_reason), full)

                _append_claim_review(session, review)
                if str(review.get("verdict") or "") == "challenge":
                    issue_kinds = sorted({
                        str(item.get("kind") or "") for item in review.get("issues") or []
                        if isinstance(item, dict) and str(item.get("kind") or "")
                    })
                    _record_decision(
                        session, "claim_review", "challenge",
                        reason=",".join(issue_kinds) or "CLAIM_CHALLENGE",
                        facts={
                            "issue_kinds": issue_kinds,
                            "workspace_epoch": int(session.workspace_epoch or 0),
                        },
                    )
                    feedback = review_followup_feedback(review)
                    _clear_pending_observation_results(session)
                    continue

                _record_decision(session, "claim_review", "accepted")
                _record_decision(session, "final", "accepted")
                return _return("success", answer, None, _details(session, "success", config, limitations=limitations), full)

            _record_rejected_decision(
                session, "FINAL_VALIDATION_REJECTED", {"reason": reason, "final": final_obj},
                decision="final", reason=reason,
            )
            feedback = _final_validation_feedback(reason)
            continue

        calls = list(action.get("calls") or []) if action_kind == "tool_calls" else []
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
            observation_signature = _observation_signature(tool, normalized)
            if observation_signature and observation_signature in seen_batch_observations:
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
                _record_observation_replay(session, {"tool": tool, "arguments": normalized, "public_arguments": _observable_tool_arguments(tool, normalized), "observation_signature": observation_signature}, duplicate, reason="BATCH_DUPLICATE_SUPPRESSED", public_result={"status":"replayed","ok":True,"executed":False,"changed":False})
                continue
            if observation_signature:
                seen_batch_observations.add(observation_signature)
                previous = _lookup_observation(session, observation_signature)
                replay_reason = "OBSERVATION_REHYDRATED"
                if previous is None:
                    previous = _capability_find_covering(
                        tool, normalized, (session.observation_ledger or {}).get("entries") or {}, session.workspace_epoch
                    )
                    if previous is not None:
                        replay_reason = "OBSERVATION_COVERAGE_REPLAYED"
                if previous is None:
                    previous = _capability_find_resource_failure(
                        tool, normalized, (session.observation_ledger or {}).get("entries") or {}, session.workspace_epoch
                    )
                    if previous is not None:
                        replay_reason = "RESOURCE_FAILURE_REHYDRATED"
                if previous is not None:
                    replay = _rehydrate_observation(session, previous, config)
                    if replay is not None:
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
                "observation_signature": observation_signature,
                "action_signature": _action_signature(tool, normalized),
            })

        # Tool calls are independent observations. A malformed sibling is
        # returned as a physical validation result but cannot cancel valid
        # siblings in the same batch. This keeps Runtime authoritative over each
        # effect without turning validation into strategy steering.
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
            _record_rejected_decision(
                session, "TOOL_CALL_VALIDATION_FAILED", invalid_results,
                objective_context={"invalid_calls": preflight_invalid}, decision="tool_preflight",
                reason=f"invalid={preflight_invalid};replayed={preflight_replays}",
            )

        # Cached reality is a memoization hit, not a semantic event or loop verdict.
        # Return the retained Observation view and let Main decide what it means.
        if calls and not novel_calls and preflight_replays == len(calls):
            _record_decision(
                session, "tool_preflight", "cached", reason="OBSERVATION_CACHE_HIT",
                tools=[str(item.get("tool") or "") for item in calls],
            )
            _set_pending_observation_results(session, next_results)
            feedback = ""
            continue

        physical_cost = len(novel_calls)

        for item in novel_calls:
            tool = item["tool"]
            normalized = item["arguments"]
            observation_signature = item["observation_signature"]
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
                _record_observation(session, observation_signature, tool, normalized, result, model_result, public_arguments=_observable_tool_arguments(tool, normalized), public_result=_observable_tool_result(tool, result))
                next_results.append(model_result)
                continue
            context = {
                "config": config, "projeto": project,
                "grounding": _grounding_items(session.observation_ledger),
                "observation_ledger": session.observation_ledger,
                "workspace_epoch": int(session.workspace_epoch),
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
            if (execution is not None and result.get("ok") is False and result.get("retryable") is False
                    and result.get("failure_scope") not in {"request", "resource"}):
                execution.mark_terminal_capability(tool, error_code=str(result.get("error_code") or "CAPABILITY_UNAVAILABLE"), detail=result.get("detail"))
            model_result = _model_tool_result(session, tool, result, config, normalized)
            _record_observation(session, observation_signature, tool, normalized, result, model_result, public_arguments=_observable_tool_arguments(tool, normalized), public_result=_observable_tool_result(tool, result))
            next_results.append(model_result)

        _set_pending_observation_results(session, next_results)

        feedback = ""


def _executar_agente_bound(
    objetivo: str,
    config: Dict[str, Any],
    projeto: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None,
    retornar_detalhes: bool = False,
    execution_id: Optional[str] = None,
    conversation_context: Any = None,
    resposta_usuario: Optional[str] = None,
):
    """Run or resume the single AgentSession."""
    full = bool(retornar_detalhes)
    project = projeto or {}
    if retomar:
        try:
            validate_pending_continuation(retomar, persisted=bool("id" in retomar))
            session = AgentSession.from_dict(retomar.get("session") or {})
        except ValueError as error:
            code = str(error)
            if code in {"PENDING_SCHEMA_INVALID", "PENDING_SCHEMA_INCOMPATIBLE"}:
                text = "The persisted continuation does not match the current canonical pending schema."
                details = {
                    "status": "failed",
                    "failure_code": code,
                    "limitations": ["Eyle 2.7.5 Rev1.3 does not migrate or adapt pending continuations from older shapes."],
                }
                return _return("failed", text, None, details, full)
            if code == "SESSION_SCHEMA_INCOMPATIBLE":
                text = "The persisted session belongs to a different contract and cannot be resumed in Eyle 2.7.5 Rev1.3."
                details = {
                    "status": "failed",
                    "failure_code": "SESSION_SCHEMA_INCOMPATIBLE",
                    "limitations": ["Eyle 2.7.5 Rev1.3 does not migrate or adapt sessions from earlier revisions."],
                }
                return _return("failed", text, None, details, full)
            raise
        execution = current_execution()
        if execution is not None:
            execution.bind_session_baseline(session)
        _rehydrate_grounding(_grounding_items(session.observation_ledger), project.get("caminho_origem"), max_lines=max(1, int(((config or {}).get("agent") or {}).get("max_file_read_lines", 400) or 400)))
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
    session = AgentSession(str(objetivo or ""), execution_id=execution_id)
    execution = current_execution()
    if execution is not None:
        execution.bind_session_baseline(session)
        execution.bind_canonical_request(session.request)
    _set_pending_observation_results(session, _seed_runtime_failure(session.observation_ledger, conversation_context))
    return _run(session, config, project, full, conversation_context=conversation_context)


def executar_agente(
    objetivo: str, config: Dict[str, Any], projeto: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None, retornar_detalhes: bool = False,
    execution_id: Optional[str] = None, conversation_context: Any = None,
    resposta_usuario: Optional[str] = None, source_job_id: Optional[int] = None,
):
    """Run one canonical AgentSession inside one run-scoped ExecutionContext."""
    execution = ExecutionContext.from_config(config, execution_id=execution_id, source_job_id=source_job_id)
    token = bind_execution(execution)
    try:
        return _executar_agente_bound(
            objetivo, config, projeto=projeto, retomar=retomar,
            retornar_detalhes=retornar_detalhes, execution_id=execution_id,
            conversation_context=conversation_context, resposta_usuario=resposta_usuario,
        )
    finally:
        execution.cleanup_sandbox()
        reset_execution(token)
