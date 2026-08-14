"""Single-session domain-neutral LLM-first agent.

There is one reasoning loop. Main decides what must be established, whether to call a capability, suspend for user input, or complete. Runtime only validates contracts and executes concrete actions.
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.executar import ErroLLM, PROMPT_AGENTE, executar_agente as executar_agente_llm
from .session import AgentSession
from .continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution, current_execution
from .decision import (
    record as _decision_record, record_rejection as _decision_record_rejection,
    requested_capability_names as _requested_capability_names,
)
from eyle.runtime.observation import (
    lookup as _lookup_observation, record as _record_observation,
    record_replay as _record_observation_replay, navigation_view as _observation_map,
    pending_results as _pending_observation_results, set_pending_results as _set_pending_observation_results,
    clear_pending_results as _clear_pending_observation_results, event_history as _capability_history_view,
    physical_capability_calls as _physical_capability_calls, replay_count as _observation_replay_count,
    material_items as _grounding_items, register_material_candidates as _grounding_register,
    physical_effect_items as _physical_effect_items, physical_effect_index_view as _physical_effect_index,
    freshest_material_for_locator as _grounding_freshest_for_locator,
    seed_runtime_failure as _seed_runtime_failure,
)
from .investigation import (
    apply_investigation_updates, established_investigation_grounding_ids, investigation_grounding_ids, open_investigation_grounding_ids,
)
from .tasks import apply_task_updates, task_grounding_ids, task_state_view
from .task_memory import apply_task_memory_updates, project_task_knowledge
from .token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.capabilities.registry import CapabilityRegistry
from eyle.contracts.capability import physical_effect as _physical_effect
from .validation import validate_complete

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


def _await_user_resolution_text(pending: Dict[str, Any], response: str) -> str:
    """Return compact retained context for a resolved human supervision gate."""
    question = str((pending or {}).get("question") or "").strip()
    answer = str(response or "").strip()
    if not question or not answer:
        raise ValueError("PENDING_USER_RESPONSE_INVALID")
    selected = None
    for option in list((pending or {}).get("options") or []):
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or "").strip()
        label = str(option.get("label") or "").strip()
        if answer.casefold() in {option_id.casefold(), label.casefold()}:
            selected = option_id or None
            break
    selected_text = f" [option={selected}]" if selected else ""
    return f"Response to suspended question: {question} -> {answer}{selected_text}"


def _record_user_resolution(session: AgentSession, pending: Dict[str, Any], response: str) -> None:
    """Retain authoritative refinement of the active Request without rewriting its origin."""
    question = str((pending or {}).get("question") or "").strip()
    answer = str(response or "").strip()
    if not question or not answer:
        raise ValueError("PENDING_USER_RESPONSE_INVALID")
    selected = None
    for option in list((pending or {}).get("options") or []):
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or "").strip()
        label = str(option.get("label") or "").strip()
        if answer.casefold() in {option_id.casefold(), label.casefold()}:
            selected = option_id or None
            break
    item = {
        "question": question,
        "answer": answer,
        "reason": str((pending or {}).get("reason") or "").strip(),
    }
    if selected:
        item["selected_option"] = selected
    session.request_context.append(item)
    session.request_context = session.request_context[-12:]
    _record_decision(
        session, "await_user_resolution", "accepted",
        reason=str((pending or {}).get("reason") or "").strip() or None,
        facts={"response": str(response or "").strip()[:500]},
    )


def _available_capabilities(config: Dict[str, Any], provider_context: Dict[str, Any], registry: CapabilityRegistry) -> set[str]:
    """Return capabilities physically exposed by their providers.

    Core never interprets provider domains or request meaning. Each provider
    owns availability; Runtime contributes only generic terminal-capability
    suppression for the current execution.
    """
    execution = current_execution()
    terminal = set(execution.terminal_capabilities) if execution is not None else set()
    context = {"config": config or {}, "provider_context": provider_context or {}}
    return registry.available_names(context, terminal=terminal)


def _capability_view(
    session: AgentSession, config: Dict[str, Any], provider_context: Dict[str, Any], registry: CapabilityRegistry,
) -> Tuple[set[str], List[Dict[str, Any]]]:
    """Expose complete model-readable contracts for every physically available capability.

    Rev1.5.3 does not hide semantics behind first-use activation or
    tiny signatures. The executable registry remains authoritative, but Main sees
    purpose, effect class, inputs, returns, caveats and physical limits before it
    chooses. This spends prompt tokens for comprehension rather than routing.
    """
    allowed = _available_capabilities(config, provider_context, registry)
    if not allowed:
        return set(), []
    catalog = registry.catalog(config=config, allowed_names=allowed)
    return allowed, catalog

def _project_grounding_index(
    session: AgentSession, *, recent_limit: int = 2, pending_limit: int = 6,
) -> List[Dict[str, Any]]:
    """Project only currently useful Material coordinates to Main.

    The canonical Material directory can grow for the whole task, but the model
    does not need that directory replayed on every turn.  Investigation-pinned
    Material is always retained, fresh pending Material is surfaced in a small
    window, and a tiny recency tail preserves navigation continuity.  Full
    Material remains canonical in Observation and Main can still address it by
    selected ``mat-*`` id.
    """
    full = [item for item in session.grounding_index() if isinstance(item, dict)]
    pinned_ids = set(open_investigation_grounding_ids(session.investigation))
    pending_ids: List[str] = []
    for result in _pending_observation_results(session):
        if not isinstance(result, dict):
            continue
        for grounding_id in result.get("grounding_ids") or []:
            value = str(grounding_id or "")
            if value and value not in pending_ids:
                pending_ids.append(value)
    pending_ids = pending_ids[-max(0, int(pending_limit)):]

    recent_ids = [
        str(item.get("id") or "") for item in full
        if str(item.get("id") or "") not in pinned_ids and str(item.get("id") or "") not in pending_ids
    ][-max(0, int(recent_limit)):]
    wanted = pinned_ids | set(pending_ids) | set(recent_ids)
    projected: List[Dict[str, Any]] = []
    for item in full:
        material_id = str(item.get("id") or "")
        if material_id not in wanted:
            continue
        clone = copy.deepcopy(item)
        if material_id in pinned_ids:
            clone["pinned"] = True
        elif material_id not in pending_ids:
            clone["reference_only"] = True
        projected.append(clone)
    return projected


def _project_observation_map(session: AgentSession) -> List[Dict[str, Any]]:
    """Project Observation as a delta plus durable coordinates.

    Fresh rows from the immediately preceding Main action remain detailed.
    Older rows are not replayed merely because they exist: only rows pinned by
    Investigation survive, and those are reduced to navigation coordinates.
    Every still-open Frontier remains visible independently of row recency.
    """
    full = [item for item in _observation_map(session) if isinstance(item, dict)]
    pinned_ids = set(open_investigation_grounding_ids(session.investigation))
    fresh_turn = max(0, int(getattr(session, "turn", 0) or 0) - 1)

    def key(item: Dict[str, Any]) -> Tuple[Any, Any, Any]:
        return (item.get("turn"), item.get("observation_signature"), item.get("capability"))

    pinned: List[Dict[str, Any]] = []
    for item in full:
        if int(item.get("turn") or 0) >= fresh_turn:
            continue
        if pinned_ids.intersection(str(gid) for gid in item.get("grounding_ids") or []):
            clone = {
                "turn": item.get("turn"),
                "capability": item.get("capability"),
                "grounding_ids": list(item.get("grounding_ids") or []),
                "observation_signature": item.get("observation_signature"),
                "retained_for": "investigation_grounding",
            }
            if isinstance(item.get("coverage"), dict):
                clone["coverage"] = copy.deepcopy(item.get("coverage"))
            pinned.append({k: v for k, v in clone.items() if v not in (None, "", [], {})})
    pinned_keys = {key(item) for item in pinned}
    candidates = [item for item in full if key(item) not in pinned_keys]
    fresh = [copy.deepcopy(item) for item in candidates if int(item.get("turn") or 0) >= fresh_turn]
    fresh_keys = {key(item) for item in fresh}

    open_frontiers: List[Dict[str, Any]] = []
    seen_frontiers: set[str] = set()
    for item in candidates:
        if key(item) in fresh_keys:
            continue
        for frontier in item.get("frontiers") or []:
            if not isinstance(frontier, dict) or frontier.get("status") != "open":
                continue
            frontier_id = str(frontier.get("id") or "")
            if not frontier_id or frontier_id in seen_frontiers:
                continue
            seen_frontiers.add(frontier_id)
            clone = copy.deepcopy(frontier)
            clone.setdefault("source_capability", item.get("capability"))
            open_frontiers.append(clone)

    retained: List[Dict[str, Any]] = []
    if open_frontiers:
        retained.append({"retained_for": "open_frontiers", "frontiers": open_frontiers})
    return pinned + retained + fresh

def _project_pending_results(session: AgentSession, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Project the fresh capability delta without cost-driven token pressure.

    Capability implementations already bind their own physical outputs. Rev1.5.0
    keeps the fresh semantic result intact and only permits the final per-call
    context-window fitter to crop if the provider's real context ceiling requires it.
    """
    return [copy.deepcopy(item) for item in _pending_observation_results(session) if isinstance(item, dict)]

def _compact_non_read_result(capability: str, result: Dict[str, Any]) -> Dict[str, Any]:
    detail = result.get("detail")
    if isinstance(detail, dict):
        detail = {
            key: value for key, value in detail.items()
            if key not in {"rollback_snapshot", "prepared_patches", "applied_patches", "stdout", "stderr"}
        }
    elif isinstance(detail, str):
        detail = detail[:4000]
    return {
        "capability": capability,
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


def _model_capability_result(session: AgentSession, capability: str, result: Dict[str, Any], registry: CapabilityRegistry, config: Optional[Dict[str, Any]] = None, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Project one capability result without interpreting the capability in Agent."""
    produces_grounding = bool(registry.spec(capability).get("produces_grounding"))
    grounding_ids = _grounding_register(
        session.observation_ledger, result.get("observations") or []
    ) if produces_grounding else []
    detail = registry.model_detail(capability, result.get("detail"), grounding_ids, config or {})
    model_result = {
        "capability": capability, "status": result.get("status"), "ok": result.get("ok"),
        "executed": result.get("executed"), "changed": result.get("changed"),
        "error_code": result.get("error_code"), "retryable": result.get("retryable"),
        "failure_scope": result.get("failure_scope"), "failure_resource": result.get("failure_resource"),
        "physical_effect": copy.deepcopy(result.get("physical_effect")) if isinstance(result.get("physical_effect"), dict) else None,
        "detail": detail, "grounding_ids": grounding_ids,
    }
    for field in ("coverage", "frontiers"):
        value = result.get(field)
        if value:
            model_result[field] = copy.deepcopy(value)
    return {k: v for k, v in model_result.items() if v is not None}


def _shrink_structured_once(value: Any) -> bool:
    """Shrink one large nested value while preserving deterministic summaries.

    Tool results are allowed to inspect a large provider_context, but the LLM should not
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


def _minimal_capability_context(result: Dict[str, Any], *, detail_char_limit: int = 3000) -> Dict[str, Any]:
    """Last-resort bounded model view while preserving canonical grounding refs."""
    compact = {
        key: result.get(key)
        for key in (
            "capability", "status", "ok", "executed", "changed", "error_code", "retryable",
            "failure_scope", "failure_resource", "physical_effect", "grounding_ids", "frontiers",
            "replayed", "coverage_replayed", "source_observation_capability", "rematerialized",
        )
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
        results = payload.get("latest_capability_results") or []
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

        observation_map = payload.get("runtime_observations") or []
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
                payload["runtime_observations"] = compacted
                continue

        grounding_index = payload.get("current_material") or []
        if len(grounding_index) > 8:
            pinned = [item for item in grounding_index if isinstance(item, dict) and item.get("pinned") is True]
            pinned_ids = {str(item.get("id") or "") for item in pinned}
            recent = [item for item in grounding_index if str((item or {}).get("id") or "") not in pinned_ids]
            compacted = pinned if len(pinned) >= 8 else pinned + recent[-max(0, 8 - len(pinned)):]
            if compacted != grounding_index:
                payload["current_material"] = compacted
                continue

        background = payload.get("prior_conversation") or []
        if len(background) > 1:
            payload["prior_conversation"] = background[1:]
            continue
        if background:
            payload["prior_conversation"] = []
            continue

        compacted_any = False
        for index, result in enumerate(list(results)):
            if not isinstance(result, dict):
                continue
            compact = _minimal_capability_context(result)
            if len(json.dumps(compact, ensure_ascii=False, default=str)) < len(json.dumps(result, ensure_ascii=False, default=str)):
                results[index] = compact
                compacted_any = True
        if compacted_any:
            continue

        # Finally drop unpinned Material directory rows. Fresh result payloads
        # still carry their grounding ids and pinned Investigation material is
        # never removed by this fallback.
        grounding_index = payload.get("current_material") or []
        unpinned = [item for item in grounding_index if not (isinstance(item, dict) and item.get("pinned") is True)]
        if unpinned:
            payload["current_material"] = [
                item for item in grounding_index if isinstance(item, dict) and item.get("pinned") is True
            ]
            continue
        break
    return payload


def _agent_config(config: Dict[str, Any], session: AgentSession, provider_context: Dict[str, Any]) -> Dict[str, Any]:
    """Return Main's physical LLM configuration without downstream token hostage-taking."""
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
    """Merge independent Runtime notices without assigning semantic meaning."""
    transient = str(transient or "").strip()
    persistent = str(persistent or "").strip()
    if not persistent:
        return transient or None
    if not transient or transient == persistent:
        return persistent
    try:
        base = json.loads(persistent)
    except Exception:
        base = {"runtime_notice": persistent}
    if not isinstance(base, dict):
        base = {"runtime_notice": base}
    try:
        notice: Any = json.loads(transient)
    except Exception:
        notice = transient
    base["runtime_notice_secondary"] = notice
    return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def _project_request_context(session: AgentSession) -> List[Dict[str, Any]]:
    """Project authoritative user refinements of the still-active immutable request."""
    raw = [item for item in (session.request_context or []) if isinstance(item, dict)]
    projected: List[Dict[str, Any]] = []
    for item in raw[-6:]:
        clone = copy.deepcopy(item)
        for field in ("question", "answer", "reason"):
            if isinstance(clone.get(field), str):
                clone[field] = _bounded_context_text(clone[field], 900)
        projected.append(clone)
    return projected


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
    provider_context: Dict[str, Any],
    conversation_context: Any,
    feedback: str,
    registry: CapabilityRegistry,
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

    allowed, capability_index = _capability_view(session, config, provider_context, registry)
    payload = {
        "request": session.request,
        "request_context": _project_request_context(session),
        "turn": session.turn,
        "investigation": session.investigation,
        "environment": registry.environment({"config": config or {}, "provider_context": provider_context or {}}),
        "prior_conversation": _project_conversation_background(session),
        "runtime_observations": _project_observation_map(session),
        "latest_capability_results": _project_pending_results(session, config),
        "current_material": _project_grounding_index(session),
        "task_knowledge": project_task_knowledge(session.task_memory),
        "runtime_effects": _physical_effect_index(session.observation_ledger),
        "physical_limits": {
            "context_window_tokens": int(((config.get("llm") or {}).get("context_window_tokens", 38000) or 38000)),
            "terminal_capabilities": execution.terminal_capabilities_view() if execution is not None else {},
        },
        "available_capabilities": capability_index,
        "runtime_feedback": str(feedback or "").strip() or None,
    }
    if session.tasks:
        payload["task_state"] = task_state_view(session.tasks)
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
            "characters": len(prompt), "estimated_tokens": post_crop_tokens, "capability_count": len(allowed),
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
    provider_context: Dict[str, Any],
    conversation_context: Any,
    feedback: str = "",
    registry: CapabilityRegistry = None,
) -> Tuple[Dict[str, Any], set[str]]:
    call_config = _agent_config(config, session, provider_context)
    prompt, allowed = _compile_prompt(session, call_config, provider_context, conversation_context, feedback, registry)
    decision = executar_agente_llm(prompt, call_config)
    if not isinstance(decision, dict):
        raise ValueError("agent structured response must be an object")
    return decision, allowed


def _is_structured_response_error(error: Exception, profile: Optional[str] = None) -> bool:
    code = str(getattr(error, "error_code", "") or "")
    prefix = "STRUCTURED_RESPONSE_INVALID:"
    if not code.startswith(prefix):
        return False
    return profile is None or code.startswith(prefix + profile + ":")


def _grounding_usage_metrics(session: AgentSession) -> Dict[str, int]:
    """Small operational accounting for one canonical Material store."""
    grounding = _grounding_items(session.observation_ledger)
    all_ids = {str(item) for item in grounding if str(item)}
    investigation_ids = set(investigation_grounding_ids(session.investigation))
    task_ids = set(task_grounding_ids(session.tasks))
    target_ids = investigation_ids | task_ids
    actions_with_grounding = sum(
        1 for item in _capability_history_view(session, limit=200)
        if isinstance(item, dict) and item.get("executed") is True and item.get("grounding_ids")
    )
    return {
        "total_grounding_count": len(all_ids),
        "investigation_grounding_count": len(investigation_ids & all_ids),
        "task_grounding_count": len(task_ids & all_ids),
        "completion_grounding_count": len(target_ids & all_ids),
        "unreferenced_grounding_count": len(all_ids - target_ids),
        "capability_actions_with_grounding": actions_with_grounding,
    }


def _details(
    session: AgentSession, status: str, config: Dict[str, Any],
    limitations: Optional[List[str]] = None, failure_code: Optional[str] = None,
) -> Dict[str, Any]:
    execution = current_execution()
    all_capability_events = list((session.observation_ledger or {}).get("events") or [])
    all_decision_events = list((session.decision_ledger or {}).get("events") or [])
    obs_start = int(execution.observation_event_start or 0) if execution is not None else 0
    dec_start = int(execution.decision_event_start or 0) if execution is not None else 0
    job_capability_events = all_capability_events[obs_start:]
    job_decision_events = all_decision_events[dec_start:]
    capability_history = [{
        "turn": item.get("turn"), "capability": item.get("capability"), "status": item.get("status"),
        "error_code": item.get("error_code"), "observation_signature": item.get("observation_signature"),
        "arguments": copy.deepcopy(item.get("arguments") or {}),
        "result": copy.deepcopy(item.get("result") or {}),
        "grounding_ids": list(item.get("grounding_ids") or []),
        "effect_id": item.get("effect_id"),
        "frontier_ids": list(item.get("frontier_ids") or []),
        "replay_reason": item.get("replay_reason"),
    } for item in job_capability_events[-50:] if isinstance(item, dict)]
    decision_history = [copy.deepcopy(item) for item in job_decision_events[-50:] if isinstance(item, dict)]
    job_capability_calls = sum(1 for item in job_capability_events if isinstance(item, dict) and item.get("executed") is True)
    total_replays = int(_observation_replay_count(session) or 0)
    replay_start = int(execution.observation_replay_start or 0) if execution is not None else 0
    job_replays = max(0, total_replays - replay_start)
    grounding = _grounding_items(session.observation_ledger)
    start_grounding = set(execution.grounding_ids_start or []) if execution is not None else set()
    job_grounding_count = len(set(grounding) - start_grounding)
    return {
        "status": status, "execution_id": session.execution_id, "investigation": session.investigation, "tasks": session.tasks,
        "task_memory": project_task_knowledge(session.task_memory),
        "turns": int(execution.agent_turns if execution is not None else session.turn),
        "capability_calls": job_capability_calls,
        "reality_epoch": int(session.reality_epoch or 0),
        "observation_replays": job_replays,
        "observation_ledger_size": len(job_capability_events),
        "grounding_count_total": job_grounding_count,
        "grounding_usage": _grounding_usage_metrics(session),
        "task_totals": {
            "turns": int(session.turn), "capability_calls": _physical_capability_calls(session),
            "observation_replays": int(_observation_replay_count(session) or 0),
            "observation_events": len(all_capability_events), "grounding_count": len(grounding),
            "decision_events": len(all_decision_events),
        },
        "capabilities_used": [item.get("capability") for item in capability_history if (item.get("result") or {}).get("executed") is True],
        "capability_history": capability_history, "decision_history": decision_history,
        "grounding": session.grounding_index(),
        "physical_effects": _physical_effect_index(session.observation_ledger),
        "limitations": list(limitations or []), "failure_code": failure_code,
        "llm_usage": execution.usage_view() if execution else {},
        "llm_calls": execution.ledger_view() if execution else [],
        "pending_capability": {k: session.pending_capability.get(k) for k in ("confirmation_id", "provider", "capability") if session.pending_capability.get(k)} if isinstance(session.pending_capability, dict) else {},
    }


def _advance_reality_epoch_if_needed(session: AgentSession, result: Dict[str, Any]) -> None:
    """Invalidate reusable observations after a confirmed persistent mutation.

    This is intentionally domain-neutral. Providers describe the physical effect;
    Runtime only reacts to the universal changed+persistent contract.
    """
    effect = result.get("physical_effect") if isinstance(result, dict) else None
    if (result.get("ok") is True and result.get("executed") is True and result.get("changed") is True
            and isinstance(effect, dict) and effect.get("persistence") == "persistent"):
        session.reality_epoch += 1


def _resume(session: AgentSession, pending: Dict[str, Any], config: Dict[str, Any], provider_context: Dict[str, Any], full: bool, registry: CapabilityRegistry):
    if pending.get("continuation_kind") != "capability_confirmation":
        return _return("failed", "A pendência não corresponde a uma confirmação de capability válida.", None, _details(session, "failed", config, failure_code="CAPABILITY_PENDING_INVALID"), full)
    state = session.pending_capability if isinstance(session.pending_capability, dict) else {}
    capability = str(pending.get("capability") or "")
    if (not state or capability != str(state.get("capability") or "")
            or str(pending.get("provider") or "") != str(state.get("provider") or "")
            or str(pending.get("confirmation_id") or "") != str(state.get("confirmation_id") or "")):
        return _return("failed", "A pendência não corresponde ao estado preparado da capability.", None, _details(session, "failed", config, failure_code="CAPABILITY_PENDING_MISMATCH"), full)
    context = {
        "config": config, "provider_context": provider_context, "session": session,
        "grounding": _grounding_items(session.observation_ledger),
        "observation_ledger": session.observation_ledger,
        "reality_epoch": int(session.reality_epoch),
    }
    result = registry.confirm(capability, state.get("state") or {}, context)
    model_result = _model_capability_result(session, capability, result, registry, config, state.get("arguments") or {})
    _record_observation(
        session, None, capability, state.get("arguments") or {}, result, model_result,
        public_arguments=registry.public_arguments(capability, state.get("arguments") or {}),
        public_result=registry.public_result(capability, result),
    )
    _record_decision(session, "capability_confirmation", "executed" if result.get("ok") is True else "failed", reason=result.get("error_code"), capabilities=[capability])
    _advance_reality_epoch_if_needed(session, result)
    session.pending_capability = {}
    _set_pending_observation_results(session, [model_result])
    # Confirmation is mechanical supervision, not semantic completion. The
    # confirmed observation/effect returns to Main, which alone decides whether
    # the task is complete, needs another capability, or should suspend again.
    return _run(session, config, provider_context, full, conversation_context=None, registry=registry)


def _normalized_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def _record_decision(
    session: AgentSession, decision_type: str, outcome: str, *,
    reason: Optional[str] = None, capabilities: Optional[List[str]] = None,
    facts: Optional[Dict[str, Any]] = None,
) -> None:
    _decision_record(
        session.decision_ledger, turn=session.turn, decision=decision_type,
        outcome=outcome, reason=reason, capabilities=capabilities, facts=facts,
    )

def _action_signature(capability: str, arguments: Dict[str, Any]) -> str:
    return json.dumps({"capability": capability, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def _record_rejected_decision(
    session: AgentSession, code: str, payload: Any = None, *,
    decision: Optional[str] = None, capabilities: Optional[List[str]] = None, reason: Optional[str] = None,
) -> None:
    _decision_record_rejection(
        session.decision_ledger, turn=session.turn, code=code,
        decision=decision, capabilities=capabilities, reason=reason,
    )


def _rehydrate_observation(
    session: AgentSession,
    entry: Dict[str, Any],
    config: Dict[str, Any],
    *,
    registry: CapabilityRegistry | None = None,
    requested_capability: str | None = None,
    requested_arguments: Dict[str, Any] | None = None,
) -> Optional[Dict[str, Any]]:
    replay = copy.deepcopy(entry.get("replay_result")) if isinstance(entry.get("replay_result"), dict) else None
    grounding = _grounding_items(session.observation_ledger)
    grounding_ids = [str(item) for item in entry.get("grounding_ids") or [] if str(item) in grounding]
    frontier_ids = [str(item) for item in entry.get("frontier_ids") or [] if str(item)]
    tool = str(entry.get("capability") or "")
    if replay is None and grounding_ids and any((grounding.get(gid) or {}).get("rehydration_error") for gid in grounding_ids):
        return None
    if replay is None and entry.get("failure_scope") in {"request", "resource"}:
        replay = {
            "capability": tool, "status": "failed", "ok": False, "executed": False, "changed": False,
            "error_code": entry.get("failure_error_code") or "STABLE_PHYSICAL_FAILURE",
            "retryable": False, "failure_scope": entry.get("failure_scope"),
            "failure_resource": entry.get("failure_resource"),
            "detail": entry.get("failure_detail") or "stable physical failure",
            "grounding_ids": grounding_ids, "frontiers": frontier_ids,
        }
    if replay is None:
        replay = {
            "capability": tool, "status": "success", "ok": True, "executed": False,
            "changed": False, "error_code": None,
            "grounding_ids": grounding_ids, "frontiers": frontier_ids,
        }

    # Physical memoization must not become cognitive amnesia. If the capability
    # knows how to rematerialize the exact requested view from canonical
    # Observation, serve that view without touching the external source again.
    rematerialized = None
    if (
        grounding_ids
        and registry is not None
        and requested_capability
        and isinstance(requested_arguments, dict)
        and replay.get("ok") is not False
    ):
        rematerialized = registry.rematerialize(
            requested_capability,
            requested_arguments,
            entry,
            grounding,
            config,
        )
    if rematerialized is not None:
        replay["detail"] = rematerialized
        replay["rematerialized"] = True
        replay["context_compacted"] = False
    # Generic cached observations still return compact coordinates. Their
    # provider may not define a rematerializable sub-view.
    elif grounding_ids:
        materials: List[Dict[str, Any]] = []
        for grounding_id in grounding_ids[:3]:
            item = dict(grounding.get(grounding_id) or {})
            text = str(item.get("numbered_content") or item.get("content") or "")
            material = {
                "grounding_id": grounding_id,
                "locator": copy.deepcopy(item.get("locator") or {}),
                "source_type": item.get("source_type"),
            }
            if text:
                material["excerpt"] = _bounded_source_text(text, 700)
                material["excerpt_complete"] = len(text) <= 700
            materials.append({k: v for k, v in material.items() if v not in (None, "", {}, [])})
        replay["detail"] = {
            "cached_observation": True,
            "materials": materials,
            "material_count": len(grounding_ids),
            "omitted_materials": max(0, len(grounding_ids) - len(materials)),
        }
        replay["context_compacted"] = True
    else:
        replay = _minimal_capability_context(replay, detail_char_limit=700)
        replay["cached_observation"] = True
    replay["capability"] = tool or replay.get("capability")
    replay["status"] = "replayed"
    replay["executed"] = False
    replay["changed"] = False
    replay["replayed"] = True
    replay["source_turn"] = entry.get("turn")
    replay["grounding_ids"] = grounding_ids or list(replay.get("grounding_ids") or [])
    return replay


def _complete_validation_feedback(reason: str) -> str:
    """Return factual coordinate-contract feedback without semantic steering."""
    detail = str(reason or "COMPLETE_INVALID")
    code = detail.split(":", 1)[0]
    explanations = {
        "COMPLETE_UNKNOWN_GROUNDING": "Every grounding_id must name a current mat-* coordinate present in current_material.",
        "COMPLETE_UNKNOWN_EFFECT": "Every effect_id must name an eff-* coordinate present in runtime_effects.",
        "COMPLETE_REQUIRED_GROUNDING_MISSING": "Material previously committed by Main through Investigation/Tasks must remain cited at completion.",
    }
    payload = {"code": "COMPLETE_VALIDATION_ERROR", "detail": detail, "state_unchanged": True}
    if code in explanations:
        payload["contract"] = explanations[code]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _deadline_exceeded(config: Dict[str, Any]) -> bool:
    execution = current_execution()
    return execution is not None and time.monotonic() >= float(execution.deadline_monotonic)


def _run(
    session: AgentSession,
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    full: bool,
    conversation_context: Any = None,
    registry: CapabilityRegistry = None,
) -> tuple:
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
                decision, allowed = _call_agent(session, config, provider_context, conversation_context, call_feedback, registry=registry)
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
                            "state_unchanged": True,
                            "expected": "one valid canonical decision envelope",
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

        if "investigation_updates" in decision:
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
            by_investigation_id = {
                str(item.get("id") or ""): item
                for item in session.investigation if isinstance(item, dict) and str(item.get("id") or "")
            }
            for item in accepted_updates:
                current_target = by_investigation_id.get(str(item.get("id") or ""), {})
                _record_decision(
                    session, "investigation_update",
                    "committed" if item.get("changed") else "unchanged",
                    reason=f"{item.get('id')}={item.get('status')}",
                    facts={
                        "investigation_id": item.get("id"),
                        "goal": current_target.get("goal"),
                        "status": current_target.get("status"),
                        "conclusion": current_target.get("conclusion"),
                        "grounding_ids": list(current_target.get("grounding_ids") or []),
                    },
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

        if "task_updates" in decision:
            raw_task_updates = decision.get("task_updates")
            if raw_task_updates is None:
                raw_task_updates = []
            prospective_tasks, accepted_task_updates, rejected_task_updates = apply_task_updates(
                raw_task_updates, previous=session.tasks,
                grounding=_grounding_items(session.observation_ledger),
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
                        "completion_criteria": current_task.get("completion_criteria"),
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

        if "memory_updates" in decision:
            raw_memory_updates = decision.get("memory_updates")
            prospective_memory, accepted_memory_updates, rejected_memory_updates = apply_task_memory_updates(
                raw_memory_updates,
                previous=session.task_memory,
                materials=_grounding_items(session.observation_ledger),
                select_evidence=lambda material, selector: registry.select_evidence(material, selector),
            )
            session.task_memory = prospective_memory
            for item in accepted_memory_updates:
                _record_decision(
                    session,
                    "task_memory_update",
                    "committed" if item.get("changed") else "unchanged",
                    reason=f"{item.get('kind')}:{item.get('id')}",
                    facts={"kind": item.get("kind"), "id": item.get("id")},
                )
            for item in rejected_memory_updates:
                reason = str(item.get("reason") or "TASK_MEMORY_UPDATE_REJECTED")
                _record_rejected_decision(
                    session,
                    reason.split(":", 1)[0],
                    decision="task_memory_update",
                    reason=reason,
                )
            if rejected_memory_updates:
                memory_feedback = {
                    "code": "TASK_MEMORY_UPDATES_PARTIALLY_REJECTED",
                    "accepted_updates": accepted_memory_updates,
                    "rejected_updates": rejected_memory_updates,
                    "task_knowledge": project_task_knowledge(session.task_memory),
                }
                feedback = _merged_runtime_feedback(
                    feedback,
                    json.dumps(memory_feedback, ensure_ascii=False, separators=(",", ":")),
                )

        action = decision.get("action") if isinstance(decision.get("action"), dict) else {}
        action_kind = str(action.get("kind") or "")

        if action_kind == "await_user":
            question = str(action.get("question") or "").strip()
            reason = str(action.get("reason") or "").strip()
            options = [dict(item) for item in list(action.get("options") or []) if isinstance(item, dict)]
            if not question or not reason:
                text = "A LLM produziu uma suspensão de usuário incompleta."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code="AGENT_AWAIT_USER_INVALID"), full,
                )
            _record_decision(
                session, "await_user", "accepted", reason=reason,
                facts={"question": question, "options": options},
            )
            pending = {
                "pending_schema_version": PENDING_SCHEMA_VERSION,
                "continuation_kind": "await_user",
                "question": question,
                "session": session.to_dict(),
                "reason": reason,
                "options": options,
            }
            validate_pending_continuation(pending)
            return _return("await_user", question, pending, _details(session, "await_user", config), full)

        if action_kind == "complete":
            open_investigation = [
                str(item.get("id") or "") for item in session.investigation
                if isinstance(item, dict) and item.get("status") == "open"
            ]
            open_tasks = [
                str(item.get("id") or "") for item in session.tasks
                if isinstance(item, dict) and item.get("status") == "open"
            ]
            blockers: Dict[str, Any] = {}
            if open_investigation:
                blockers["open_investigation"] = open_investigation
            if open_tasks:
                blockers["open_tasks"] = open_tasks
            if blockers:
                _record_decision(
                    session, "complete", "rejected", reason="COMPLETE_COMMITMENTS_OPEN", facts=blockers,
                )
                feedback = json.dumps(
                    {
                        "code": "COMPLETE_COMMITMENTS_OPEN",
                        **blockers,
                        "complete_committed": False,
                    },
                    ensure_ascii=False, separators=(",", ":"),
                )
                continue

            required_grounding_ids = list(dict.fromkeys(
                established_investigation_grounding_ids(session.investigation)
                + task_grounding_ids(session.tasks)
            ))
            complete_obj = {
                "answer": action.get("answer"),
                "limitations": list(action.get("limitations") or []),
                "grounding_ids": list(action.get("grounding_ids") or []),
                "effect_ids": list(action.get("effect_ids") or []),
            }
            ok, reason, answer, limitations = validate_complete(
                complete_obj,
                _grounding_items(session.observation_ledger),
                _physical_effect_items(session.observation_ledger),
                required_grounding_ids=required_grounding_ids,
            )
            if ok:
                _record_decision(
                    session, "complete", "accepted",
                    facts={
                        "grounding_ids": list(dict.fromkeys(complete_obj.get("grounding_ids") or [])),
                        "effect_ids": list(dict.fromkeys(complete_obj.get("effect_ids") or [])),
                        "required_grounding_ids": required_grounding_ids,
                        "reality_epoch": int(session.reality_epoch or 0),
                    },
                )
                return _return(
                    "success", answer, None,
                    _details(session, "success", config, limitations=limitations), full,
                )

            _record_rejected_decision(
                session, "COMPLETE_VALIDATION_REJECTED", {"reason": reason, "complete": complete_obj},
                decision="complete", reason=reason,
            )
            feedback = _complete_validation_feedback(reason)
            continue

        calls = list(action.get("calls") or []) if action_kind == "capability_calls" else []
        calls = [call for call in calls if isinstance(call, dict) and call.get("capability")]
        if not calls:
            _record_rejected_decision(session, "NO_ACTION", {}, decision="empty")
            feedback = json.dumps({"code": "NO_ACTION", "state_unchanged": True, "valid_action_kinds": ["capability_calls", "await_user", "complete"]}, separators=(",", ":"))
            continue

        _record_decision(
            session,
            "capability_calls" if len(calls) > 1 else "capability",
            "requested",
            capabilities=[str(call.get("capability") or "") for call in calls],
        )

        # Unified physical preflight. Semantic freedom is untouched: the model
        # may request any available observation again. Runtime decides only
        # whether that physical observation must be executed for the current reality
        # epoch, or whether retained reality can be replayed.
        next_results: List[Dict[str, Any]] = []
        novel_calls: List[Dict[str, Any]] = []
        seen_batch_observations: set[str] = set()
        preflight_invalid = 0
        preflight_replays = 0
        replay_requests: List[Dict[str, Any]] = []
        for call in calls:
            tool = str(call.get("capability") or "")
            arguments = call.get("arguments") or {}
            if tool not in allowed:
                rejected = {
                    "capability": tool, "status": "failed", "ok": False,
                    "executed": False, "changed": False,
                    "error_code": "CAPABILITY_NOT_AVAILABLE",
                    "detail": "A capability não está disponível no ambiente atual.",
                }
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "capability_validation", "rejected", reason=rejected["error_code"], capabilities=[tool])
                continue

            normalized, error = registry.validate(tool, arguments)
            if error:
                rejected = _compact_non_read_result(tool, error)
                preflight_invalid += 1
                next_results.append(rejected)
                _record_decision(session, "capability_validation", "rejected", reason=error.get("error_code") or "INVALID_ARGUMENT", capabilities=[tool])
                continue

            _record_decision(session, "capability_validation", "validated", capabilities=[tool])
            if registry.requires_confirmation(tool):
                if len(calls) != 1:
                    preflight_invalid += 1
                    rejected = {
                        "capability": tool, "status": "failed", "ok": False, "executed": False, "changed": False,
                        "error_code": "CONFIRMATION_CAPABILITY_MUST_BE_SINGLE",
                        "detail": "A capability que exige confirmação deve ser chamada sozinha neste turno.",
                    }
                    next_results.append(rejected)
                    _record_decision(session, "capability_confirmation", "rejected", reason=rejected["error_code"], capabilities=[tool])
                    continue
                context = {
                    "config": config, "provider_context": provider_context, "session": session,
                    "grounding": _grounding_items(session.observation_ledger),
                    "observation_ledger": session.observation_ledger,
                    "reality_epoch": int(session.reality_epoch),
                }
                prepared = registry.prepare_confirmation(tool, normalized, context)
                if prepared.get("ok") is not True:
                    error_result = prepared.get("error") if isinstance(prepared.get("error"), dict) else {
                        "status": "failed", "ok": False, "executed": False, "changed": False,
                        "error_code": "CAPABILITY_PREPARE_FAILED", "retryable": False,
                        "failure_scope": None, "failure_resource": None, "detail": "Capability preparation failed.",
                        "physical_effect": None, "observations": [], "coverage": {}, "frontiers": [],
                    }
                    model_result = _model_capability_result(session, tool, error_result, registry, config, normalized)
                    _record_observation(
                        session, None, tool, normalized, error_result, model_result,
                        public_arguments=registry.public_arguments(tool, normalized),
                        public_result=registry.public_result(tool, error_result),
                    )
                    next_results.append(model_result)
                    preflight_invalid += 1
                    _record_decision(session, "capability_confirmation", "rejected", reason=error_result.get("error_code") or "CAPABILITY_PREPARE_FAILED", capabilities=[tool])
                    continue
                confirmation_id = f"cap-{session.turn:04d}"
                session.pending_capability = {
                    "confirmation_id": confirmation_id,
                    "provider": prepared.get("provider"),
                    "capability": tool,
                    "arguments": copy.deepcopy(normalized),
                    "state": copy.deepcopy(prepared.get("state") or {}),
                }
                question = str(prepared.get("question") or "").strip()
                pending = {
                    "pending_schema_version": PENDING_SCHEMA_VERSION,
                    "continuation_kind": "capability_confirmation",
                    "question": question,
                    "session": session.to_dict(),
                    "capability": tool,
                    "provider": str(prepared.get("provider") or ""),
                    "confirmation_id": confirmation_id,
                }
                validate_pending_continuation(pending)
                _record_decision(session, "capability_confirmation", "prepared", capabilities=[tool], facts={"provider": prepared.get("provider")})
                return _return("await_user", question, pending, _details(session, "await_user", config), full)
            observation_signature = registry.observation_signature(tool, normalized)
            if observation_signature and observation_signature in seen_batch_observations:
                duplicate = {
                    "capability": tool, "status": "replayed", "ok": True,
                    "executed": False, "changed": False,
                    "error_code": "BATCH_DUPLICATE_SUPPRESSED",
                    "detail": "Duplicate observation in the same batch was suppressed before physical execution.",
                    "replayed": True,
                }
                preflight_replays += 1
                next_results.append(duplicate)
                _record_decision(session, "capability_preflight", "batch_duplicate", reason="BATCH_DUPLICATE_SUPPRESSED", capabilities=[tool])
                _record_observation_replay(session, {"capability": tool, "arguments": normalized, "public_arguments": registry.public_arguments(tool, normalized), "observation_signature": observation_signature}, duplicate, reason="BATCH_DUPLICATE_SUPPRESSED", public_result={"status":"replayed","ok":True,"executed":False,"changed":False})
                continue
            if observation_signature:
                seen_batch_observations.add(observation_signature)
                previous = _lookup_observation(session, observation_signature)
                replay_reason = "OBSERVATION_REHYDRATED"
                if previous is None:
                    previous = registry.find_covering(
                        tool, normalized, (session.observation_ledger or {}).get("entries") or {}, session.reality_epoch
                    )
                    if previous is not None:
                        replay_reason = "OBSERVATION_COVERAGE_REPLAYED"
                if previous is None:
                    previous = registry.find_resource_failure(
                        tool, normalized, (session.observation_ledger or {}).get("entries") or {}, session.reality_epoch
                    )
                    if previous is not None:
                        replay_reason = "RESOURCE_FAILURE_REHYDRATED"
                if previous is not None:
                    replay = _rehydrate_observation(
                        session,
                        previous,
                        config,
                        registry=registry,
                        requested_capability=tool,
                        requested_arguments=normalized,
                    )
                    if replay is not None:
                        replay["capability"] = tool
                        replay["replayed"] = True
                        if replay_reason == "OBSERVATION_COVERAGE_REPLAYED":
                            replay["coverage_replayed"] = True
                            replay["source_observation_capability"] = previous.get("capability")
                        preflight_replays += 1
                        _record_observation_replay(session, previous, replay, reason=replay_reason, public_result={"status":"replayed","ok":True,"executed":False,"changed":False})
                        replay_requests.append({"capability": tool, "arguments": normalized})
                        next_results.append(replay)
                        _record_decision(session, "capability_preflight", "replayed", reason=replay_reason, capabilities=[tool])
                        continue


            novel_calls.append({
                "capability": tool,
                "arguments": normalized,
                "observation_signature": observation_signature,
                "action_signature": _action_signature(tool, normalized),
            })

        # Capability calls are independent observations. A malformed sibling is
        # returned as a physical validation result but cannot cancel valid
        # siblings in the same batch. This keeps Runtime authoritative over each
        # effect without turning validation into strategy steering.
        if preflight_invalid:
            invalid_results = [
                {
                    "capability": item.get("capability"),
                    "error_code": item.get("error_code"),
                    "detail": item.get("detail"),
                }
                for item in next_results
                if isinstance(item, dict) and item.get("ok") is False
            ]
            _record_rejected_decision(
                session, "CAPABILITY_CALL_VALIDATION_FAILED", invalid_results,
                decision="capability_preflight",
                reason=f"invalid={preflight_invalid};replayed={preflight_replays}",
            )

        # Cached reality is a memoization hit, not a semantic event or loop verdict.
        # Return the retained Observation view and let Main decide what it means.
        if calls and not novel_calls and preflight_replays == len(calls):
            _record_decision(
                session, "capability_preflight", "cached", reason="OBSERVATION_CACHE_HIT",
                capabilities=[str(item.get("capability") or "") for item in calls],
            )
            _set_pending_observation_results(session, next_results)
            feedback = ""
            continue

        physical_cost = len(novel_calls)

        for item in novel_calls:
            tool = item["capability"]
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
                _record_decision(session, "capability_execution", "blocked", reason=result["error_code"], capabilities=[tool])
                model_result = _model_capability_result(session, tool, result, registry, config, normalized)
                _record_observation(session, observation_signature, tool, normalized, result, model_result, public_arguments=registry.public_arguments(tool, normalized), public_result=registry.public_result(tool, result))
                next_results.append(model_result)
                continue
            context = {
                "config": config, "provider_context": provider_context, "session": session,
                "grounding": _grounding_items(session.observation_ledger),
                "observation_ledger": session.observation_ledger,
                "reality_epoch": int(session.reality_epoch),
            }
            result = registry.execute(tool, normalized, context)
            if result.get("executed") is True:
                execution_outcome = "executed" if result.get("ok") is True else "failed"
            elif result.get("status") == "skipped":
                execution_outcome = "skipped"
            elif result.get("ok") is True:
                execution_outcome = "completed"
            else:
                execution_outcome = "failed"
            _record_decision(session, "capability_execution", execution_outcome, reason=result.get("error_code"), capabilities=[tool])
            execution = current_execution()
            if (execution is not None and result.get("ok") is False and result.get("retryable") is False
                    and result.get("failure_scope") not in {"request", "resource"}):
                execution.mark_terminal_capability(tool, error_code=str(result.get("error_code") or "CAPABILITY_UNAVAILABLE"), detail=result.get("detail"))
            model_result = _model_capability_result(session, tool, result, registry, config, normalized)
            _record_observation(session, observation_signature, tool, normalized, result, model_result, public_arguments=registry.public_arguments(tool, normalized), public_result=registry.public_result(tool, result))
            _advance_reality_epoch_if_needed(session, result)
            next_results.append(model_result)

        _set_pending_observation_results(session, next_results)

        feedback = ""


def _executar_agente_bound(
    objetivo: str,
    config: Dict[str, Any],
    provider_context: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None,
    retornar_detalhes: bool = False,
    execution_id: Optional[str] = None,
    conversation_context: Any = None,
    resposta_usuario: Optional[str] = None,
    registry: CapabilityRegistry = None,
):
    """Run or resume the single AgentSession with an explicitly injected capability body."""
    if registry is None:
        raise ValueError("CAPABILITY_REGISTRY_REQUIRED")
    full = bool(retornar_detalhes)
    provider_context = provider_context or {}
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
                    "limitations": ["Eyle 2.7.5 Rev1.5.3 does not migrate or adapt pending continuations from older shapes."],
                }
                return _return("failed", text, None, details, full)
            if code == "SESSION_SCHEMA_INCOMPATIBLE":
                text = "The persisted session belongs to a different contract and cannot be resumed in Eyle 2.7.5 Rev1.5.3."
                details = {
                    "status": "failed",
                    "failure_code": "SESSION_SCHEMA_INCOMPATIBLE",
                    "limitations": ["Eyle 2.7.5 Rev1.5.3 does not migrate or adapt sessions from earlier revisions."],
                }
                return _return("failed", text, None, details, full)
            raise
        execution = current_execution()
        if execution is not None:
            execution.bind_session_baseline(session)
        registry.rehydrate_materials(_grounding_items(session.observation_ledger), {"config": config or {}, "provider_context": provider_context or {}})
        if retomar.get("continuation_kind") == "await_user":
            try:
                _record_user_resolution(session, retomar, str(resposta_usuario or ""))
            except ValueError as error:
                text = "A resposta à suspensão do usuário não possui um contrato canônico válido."
                return _return(
                    "failed", text, None,
                    _details(session, "failed", config, failure_code=str(error)), full,
                )
            # Human supervision refines the same immutable Request through
            # request_context. Runtime persists facts; Main interprets their meaning.
            _clear_pending_observation_results(session)
            if execution is not None:
                execution.bind_canonical_request(session.request)
            return _run(session, config, provider_context, full, conversation_context=None, registry=registry)
        if execution is not None:
            execution.bind_canonical_request(session.request)
        return _resume(session, retomar, config, provider_context, full, registry)
    session = AgentSession(str(objetivo or ""), execution_id=execution_id)
    execution = current_execution()
    if execution is not None:
        execution.bind_session_baseline(session)
        execution.bind_canonical_request(session.request)
    _set_pending_observation_results(session, _seed_runtime_failure(session.observation_ledger, conversation_context))
    return _run(session, config, provider_context, full, conversation_context=conversation_context, registry=registry)


def executar_agente(
    objetivo: str, config: Dict[str, Any], provider_context: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None, retornar_detalhes: bool = False,
    execution_id: Optional[str] = None, conversation_context: Any = None,
    resposta_usuario: Optional[str] = None, source_job_id: Optional[int] = None,
    registry: CapabilityRegistry = None,
):
    """Run one canonical AgentSession inside one run-scoped ExecutionContext."""
    if registry is None:
        raise ValueError("CAPABILITY_REGISTRY_REQUIRED")
    execution = ExecutionContext.from_config(config, execution_id=execution_id, source_job_id=source_job_id)
    token = bind_execution(execution)
    try:
        return _executar_agente_bound(
            objetivo, config, provider_context=provider_context, retomar=retomar,
            retornar_detalhes=retornar_detalhes, execution_id=execution_id,
            conversation_context=conversation_context, resposta_usuario=resposta_usuario, registry=registry,
        )
    finally:
        execution.cleanup()
        reset_execution(token)
