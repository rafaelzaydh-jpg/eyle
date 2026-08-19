"""Eyle ECC agent core.

The cognitive core is deliberately small: Main chooses Explorar, Construir or
Concluir. Runtime executes the selected physical operation and returns facts.
There are no phase routers, task/investigation ledgers, completion gates or
model-managed evidence identifiers in this module.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.executar import (
    ErroLLM, PROMPT_NAVIGATION, PROMPT_EXPLORE, PROMPT_BUILD,
    executar_navigation as executar_navigation_llm,
    executar_explore as executar_explore_llm,
    executar_build as executar_build_llm,
)
from llm.protocol import CanonicalPrompt
from eyle.capabilities.registry import CapabilityRegistry
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation, confirmation_control, resolve_semantic_choice
from eyle.runtime.ecc_runtime import (
    DispatchOutcome,
    available_internal,
    cancel_pending,
    confirm_pending,
    dispatch,
    effects_view,
    exploration_map,
)
from eyle.runtime.execution_context import ExecutionContext, bind_execution, current_execution, reset_execution
from eyle.runtime.observation import (
    clear_pending_results,
    material_items,
    pending_results,
    physical_capability_calls,
    replay_count,
    event_history,
    seed_runtime_failure,
    set_pending_results,
    frontier_view,
    mechanical_coverage_state,
)
from eyle.runtime.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.memory_graph import graph_counts
from eyle.runtime.context_materializer import materialize_conversation, materialize_latest_observations, materialize_runtime_feedback, component_metrics
from eyle.runtime.execution_progress import ExecutionProgress
from .ecc import catalog as ecc_catalog, navigation_directory, surface_catalog, public_name
from .memory import (
    apply_memory_sidecar, apply_task_binding, materialize_active_task,
    materialize_explicit_memory_view, memory_available, memory_environment,
    release_memory_navigation, sync_memory_lifecycle,
)
from .session import AgentSession


def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _terminal_return(
    session: AgentSession, status: str, text: str, details: Dict[str, Any], full: bool,
    *, provider_context: Dict[str, Any],
):
    """Finish one logical task and release navigation-only Memory snapshots.

    Learned Memory survives. DB recall selections exist only to continue this
    Session, so they are released when the logical task can no longer resume.
    """
    release_memory_navigation(session, provider_context)
    return _return(status, text, None, details, full)


def _recoverable_pending(
    session: AgentSession,
    execution: Optional[ExecutionContext],
    *,
    reason: str,
    resume_hint: str,
) -> Dict[str, Any]:
    """Build one canonical Runtime-owned checkpoint envelope.

    This is not a user interaction and carries no semantic decision. It is a
    durable physical continuation of the same logical AgentSession.
    """
    if execution is None:
        raise RuntimeError("RECOVERABLE_CONTINUATION_REQUIRES_EXECUTION_CONTEXT")
    pending = {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "recoverable_execution",
        "question": "Recoverable execution checkpoint.",
        "session": session.to_checkpoint_dict(),
        "execution_state": execution.continuation_state(),
        "checkpoint_reason": str(reason),
        "resume_hint": str(resume_hint),
    }
    validate_pending_continuation(pending)
    return pending


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    room = max(0, max_chars - 80)
    head = room // 2
    return text[:head] + "\n...[ECC context cropped at physical context boundary]...\n" + text[-(room-head):]


def _shrink_payload(payload: Dict[str, Any], budget_tokens: int | None, chars_per_token: int) -> Dict[str, Any]:
    """Mechanical context fitting; never semantic routing or relevance selection."""
    clone = copy.deepcopy(payload)
    if budget_tokens is None:
        return clone
    if estimate_tokens(clone, chars_per_token) <= budget_tokens:
        return clone

    # Exploration map is physical navigation metadata and can be reduced by age.
    nav = clone.get("exploration_map")
    while isinstance(nav, list) and len(nav) > 8 and estimate_tokens(clone, chars_per_token) > budget_tokens:
        nav.pop(0)

    # Active Memory View was explicitly chosen by Main. A configured physical
    # provider context boundary may force serialization fitting, but there is no
    # semantic node-count ceiling and Runtime never substitutes hidden retrieval.
    memory_view = clone.get("memory_view")
    if isinstance(memory_view, dict):
        nodes = memory_view.get("nodes")
        while isinstance(nodes, list) and len(nodes) > 6 and estimate_tokens(clone, chars_per_token) > budget_tokens:
            nodes.pop(0)
        if isinstance(nodes, list):
            keep = {str(item.get("id") or "") for item in nodes if isinstance(item, dict)}
            edges = memory_view.get("edges")
            if isinstance(edges, list):
                memory_view["edges"] = [item for item in edges if isinstance(item, dict) and str(item.get("source") or "") in keep and str(item.get("target") or "") in keep]

    # Raw latest observations are the only source body allowed to remain hot.
    latest = clone.get("latest_observations")
    if isinstance(latest, list):
        for item in latest:
            if estimate_tokens(clone, chars_per_token) <= budget_tokens:
                break
            if not isinstance(item, dict):
                continue
            detail = item.get("detail")
            if isinstance(detail, str):
                item["detail"] = _bounded_text(detail, max(600, len(detail)//2))
            elif isinstance(detail, dict):
                for key in ("content", "numbered_content", "output", "stdout", "stderr"):
                    if isinstance(detail.get(key), str) and len(detail[key]) > 1000:
                        detail[key] = _bounded_text(detail[key], 1000)

    # Last-resort bounded serialization for pathological provider diagnostics.
    if estimate_tokens(clone, chars_per_token) > budget_tokens:
        feedback = clone.get("runtime_feedback")
        while isinstance(feedback, list) and feedback and estimate_tokens(clone, chars_per_token) > budget_tokens:
            feedback.pop(0)
    return clone


def _compile_prompt(
    session: AgentSession,
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    conversation_context: Any,
    registry: CapabilityRegistry,
) -> Tuple[CanonicalPrompt, set[str]]:
    """Materialize the Rev4 protocol surface selected by explicit ECC state.

    The Runtime never infers a surface from request meaning. ``cognitive_surface``
    is either the initial Navigation protocol or the direct consequence of Main's
    previous ECC choice.
    """
    execution = current_execution()
    if execution is not None:
        execution.assert_canonical_request(session.request)

    surface_name = str(session.cognitive_surface or "navigation")
    if surface_name not in {"navigation", "explore", "build"}:
        raise ValueError("COGNITIVE_SURFACE_INVALID")

    available = available_internal(registry, config, provider_context)
    memory_enabled = memory_available(provider_context)
    active_memory = materialize_explicit_memory_view(
        session, registry=registry, config=config, provider_context=provider_context,
    )
    active_task = materialize_active_task(session, provider_context)
    wire_retry = any(
        isinstance(item, dict) and item.get("code") == "ECC_WIRE_RETRY"
        for item in session.runtime_feedback
    )
    conversation = materialize_conversation(conversation_context, config)
    if execution is not None:
        execution.history_messages_omitted = int(conversation.get("history_messages_omitted") or 0)
    latest = materialize_latest_observations(pending_results(session), config)

    if surface_name == "navigation":
        catalog_key = "ecc_navigation"
        capability_surface = navigation_directory(
            registry, config, available, memory_enabled=memory_enabled,
        )
        # Navigation only needs physical availability, not provider tool schemas.
        runtime_environment = {
            "capabilities_available": len(available),
            "memory_available": bool(memory_enabled),
        }
        system_prompt = PROMPT_NAVIGATION
    elif surface_name == "explore":
        catalog_key = "explore_operations"
        capability_surface = surface_catalog(
            registry, config, available, "explore", memory_enabled=memory_enabled,
        )
        runtime_environment = registry.environment(
            {"config": config or {}, "provider_context": provider_context or {}}
        )
        system_prompt = PROMPT_EXPLORE
    else:
        catalog_key = "build_operations"
        capability_surface = surface_catalog(
            registry, config, available, "build", memory_enabled=memory_enabled,
        )
        runtime_environment = registry.environment(
            {"config": config or {}, "provider_context": provider_context or {}}
        )
        system_prompt = PROMPT_BUILD

    stable_packet = {
        catalog_key: capability_surface,
        "runtime_environment": runtime_environment,
    }
    dynamic_packet = {
        "current_request": session.request,
        "conversation": conversation,
        "active_task": active_task,
        "memory_environment": memory_environment(provider_context),
        "memory_view": active_memory,
        "exploration_map": exploration_map(session, registry),
        "mechanical_coverage": mechanical_coverage_state(session),
        "execution_convergence": ExecutionProgress.from_dict(session.execution_progress).convergence_view(
            execution.provider_total_tokens_actual if execution is not None else 0
        ),
        "latest_observations": latest,
        "runtime_effects": effects_view(session),
        "turn": session.turn,
        "runtime_feedback": materialize_runtime_feedback(session.runtime_feedback, config),
        "cognitive_surface": surface_name,
    }
    payload = {**stable_packet, **dynamic_packet}

    context_cfg = config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    calibration = execution.prompt_token_calibration if execution is not None else 1.0
    prompt_budget = available_user_prompt_tokens(
        config, system_prompt.rstrip(), output_tokens=0, token_estimate_multiplier=calibration,
    )
    pre_tokens = estimate_tokens(payload, chars_per_token)
    fitted = _shrink_payload(payload, prompt_budget, chars_per_token)
    fitted_stable = {name: fitted[name] for name in stable_packet if name in fitted}
    fitted_dynamic = {name: fitted[name] for name in dynamic_packet if name in fitted}
    prompt = CanonicalPrompt(stable=fitted_stable, dynamic=fitted_dynamic)
    post_tokens = estimate_tokens(prompt.wire_text, chars_per_token)

    if execution is not None:
        component_names = list(dynamic_packet) + list(stable_packet)
        components = component_metrics(
            {name: fitted.get(name) for name in component_names}, config
        )
        if surface_name == "navigation":
            explore_count = len(capability_surface.get("explorar") or [])
            build_count = len(capability_surface.get("construir") or [])
        else:
            ops = capability_surface.get("operations") if isinstance(capability_surface, dict) else []
            explore_count = len(ops or []) if surface_name == "explore" else 0
            build_count = len(ops or []) if surface_name == "build" else 0
        execution.begin_call(mode=surface_name, turn=session.turn, prompt={
            "surface": surface_name,
            "characters": len(prompt.wire_text),
            "stable_prefix_characters": len(prompt.stable_text),
            "stable_prefix_hash": prompt.stable_hash,
            "estimated_tokens": post_tokens,
            "pre_crop_estimated_tokens": pre_tokens,
            "crop_applied": post_tokens < pre_tokens,
            "prompt_budget_tokens": prompt_budget,
            "local_context_limit_enabled": prompt_budget is not None,
            "cognition_reason": "wire_retry" if wire_retry else ("continuation" if session.turn > 1 else "normal"),
            "conversation_messages_materialized": int((conversation or {}).get("history_messages_materialized") or 0),
            "conversation_messages_omitted": int((conversation or {}).get("history_messages_omitted") or 0),
            "ecc_explore_operations": explore_count,
            "ecc_build_operations": build_count,
            "active_task_present": bool(active_task),
            "memory_nodes_projected": len((fitted.get("memory_view") or {}).get("nodes") or []) if isinstance(fitted.get("memory_view"), dict) else 0,
            "memory_edges_projected": len((fitted.get("memory_view") or {}).get("edges") or []) if isinstance(fitted.get("memory_view"), dict) else 0,
            "latest_observation_items": len(fitted.get("latest_observations") or []),
            "components_after": components,
            "estimated_static_tokens": int(components.get("runtime_environment", {}).get("estimated_tokens", 0)) + int(components.get(catalog_key, {}).get("estimated_tokens", 0)),
            "estimated_conversation_tokens": int(components.get("conversation", {}).get("estimated_tokens", 0)),
            "estimated_memory_tokens": int(components.get("memory_view", {}).get("estimated_tokens", 0)) + int(components.get("memory_environment", {}).get("estimated_tokens", 0)) + int(components.get("active_task", {}).get("estimated_tokens", 0)),
            "estimated_observation_tokens": int(components.get("latest_observations", {}).get("estimated_tokens", 0)),
            "estimated_feedback_tokens": int(components.get("runtime_feedback", {}).get("estimated_tokens", 0)),
            "estimated_capability_tokens": int(components.get(catalog_key, {}).get("estimated_tokens", 0)),
            "semantic_packet_fields": list(fitted.keys()),
        })
    return prompt, available

def _structured_error(error: Exception) -> bool:
    code = str(getattr(error, "error_code", "") or "")
    return (
        code.startswith("STRUCTURED_RESPONSE_INVALID:navigation:")
        or code.startswith("STRUCTURED_RESPONSE_INVALID:explore:")
        or code.startswith("STRUCTURED_RESPONSE_INVALID:build:")
        or code == "LLM_STRUCTURED_RESPONSE_UNSATISFIED"
    )


def _feedback(session: AgentSession, code: str, **facts: Any) -> None:
    # runtime_feedback is an active execution surface, not an append-only log.
    # Repeating the same unresolved condition replaces its prior payload so the
    # next cognition receives bounded current facts. Historical telemetry remains
    # in execution/job history.
    code = str(code)
    item = {"code": code, **{k: copy.deepcopy(v) for k, v in facts.items() if v is not None}}
    replaceable = {"ECC_WIRE_RETRY", "MEMORY_DELTA_REJECTED", "TASK_BINDING_REJECTED", "NO_PROGRESS", "CONFIRMATION_EXECUTION_FAILED", "USER_CHOICE", "BUDGET_SALVAGE"}
    if code in replaceable:
        session.runtime_feedback = [v for v in session.runtime_feedback if not (isinstance(v, dict) and v.get("code") == code)]
    session.runtime_feedback.append(item)


def _resolve_feedback(session: AgentSession, *codes: str) -> None:
    wanted = {str(code) for code in codes}
    if not wanted:
        return
    session.runtime_feedback = [
        item for item in session.runtime_feedback
        if not (isinstance(item, dict) and str(item.get("code") or "") in wanted)
    ]


def _set_surface(session: AgentSession, surface: str, execution: Optional[ExecutionContext]) -> None:
    """Transition protocol surface only from explicit Main/runtime protocol state."""
    if surface not in {"navigation", "explore", "build"}:
        raise ValueError("COGNITIVE_SURFACE_INVALID")
    previous = str(session.cognitive_surface or "navigation")
    if previous != surface:
        session.cognitive_surface = surface
        if execution is not None:
            execution.surface_transitions += 1


def _call_surface_llm(surface: str, prompt: CanonicalPrompt, config: Dict[str, Any]) -> Dict[str, Any]:
    if surface == "navigation":
        return executar_navigation_llm(prompt, config)
    if surface == "explore":
        return executar_explore_llm(prompt, config)
    if surface == "build":
        return executar_build_llm(prompt, config)
    raise ValueError("COGNITIVE_SURFACE_INVALID")


def _apply_semantic_sidecars(
    session: AgentSession,
    decision: Dict[str, Any],
    *,
    registry: CapabilityRegistry,
    provider_context: Dict[str, Any],
    execution: Optional[ExecutionContext],
) -> tuple[bool, bool]:
    """Apply Memory and exact Task binding without vetoing primary cognition."""
    memory_parse_error = decision.get("memory_error") if isinstance(decision.get("memory_error"), dict) else None
    if memory_parse_error is not None:
        memory_outcome = {
            "ok": False,
            "changed": False,
            "task_state_changed": False,
            "aliases": {},
            "error_code": memory_parse_error.get("code") or "MEMORY_DELTA_INVALID",
            "detail": memory_parse_error.get("detail"),
        }
    else:
        memory_outcome = apply_memory_sidecar(
            session, decision.get("memory_delta"), registry=registry, provider_context=provider_context,
        )

    if memory_outcome.get("ok") is not True:
        _feedback(
            session, "MEMORY_DELTA_REJECTED", error_code=memory_outcome.get("error_code"),
            detail=memory_outcome.get("detail"), state_unchanged=True,
        )
        memory_changed = False
        task_state_progress = False
        aliases = {}
    else:
        _resolve_feedback(session, "MEMORY_DELTA_REJECTED")
        memory_changed = bool(memory_outcome.get("changed"))
        # General Memory edits alone do not count as task progress.
        task_state_progress = bool(memory_outcome.get("task_state_changed"))
        aliases = dict(memory_outcome.get("aliases") or {})

    binding_parse_error = decision.get("task_binding_error") if isinstance(decision.get("task_binding_error"), dict) else None
    if binding_parse_error is not None:
        binding_outcome = {
            "ok": False,
            "changed": False,
            "error_code": binding_parse_error.get("code") or "TASK_BINDING_INVALID",
            "detail": binding_parse_error.get("detail"),
        }
    else:
        binding_outcome = apply_task_binding(
            session, decision.get("task_binding"), aliases=aliases, provider_context=provider_context,
        )
    if binding_outcome.get("ok") is not True:
        _feedback(
            session, "TASK_BINDING_REJECTED",
            error_code=binding_outcome.get("error_code"),
            detail=binding_outcome.get("detail"),
            state_unchanged=True,
        )
    else:
        _resolve_feedback(session, "TASK_BINDING_REJECTED")
        if binding_outcome.get("changed") and execution is not None:
            execution.task_bind_count += 1
    return memory_changed, task_state_progress


def _details(
    session: AgentSession, status: str, config: Dict[str, Any], registry: CapabilityRegistry,
    provider_context: Optional[Dict[str, Any]] = None, *, failure_code: Optional[str] = None,
    limitations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    execution = current_execution()
    events = event_history(session, limit=200)
    used = []
    for event in events:
        name = public_name(str(event.get("capability") or ""), registry)
        if name and name not in used:
            used.append(name)
    materials = material_items(session.observation_ledger)
    evidence = session.evidence if isinstance(session.evidence, dict) else {}
    graph_view = materialize_explicit_memory_view(
        session, registry=registry, config=config, provider_context=provider_context or {},
    )
    memory_ctx = (provider_context or {}).get("core_memory")
    storage_dir = str(memory_ctx.get("storage_dir") or "").strip() if isinstance(memory_ctx, dict) else ""
    try:
        graph_counts_view = graph_counts(storage_dir) if storage_dir else {}
    except (OSError, ValueError):
        graph_counts_view = {}
    memory_feedback = [
        item for item in session.runtime_feedback
        if isinstance(item, dict) and item.get("code") == "MEMORY_DELTA_REJECTED"
    ]
    rejection_reasons = []
    for feedback in memory_feedback:
        reason = str(feedback.get("error_code") or "")
        if reason and reason not in rejection_reasons:
            rejection_reasons.append(reason)
    projected_nodes = [item for item in graph_view.get("nodes") or [] if isinstance(item, dict)]
    details = {
        "status": status,
        "architecture": "ECC",
        "execution_id": session.execution_id,
        "active_task_id": session.active_task_id,
        "cognitive_surface": session.cognitive_surface,
        "turns": int(session.turn),
        "operations_used": used,
        "operation_history": events,
        "physical_capability_calls": physical_capability_calls(session),
        "operation_replays": replay_count(session),
        "observation_ledger_size": len(events),
        "grounding_count_total": len(materials),
        "memory_nodes": int(graph_counts_view.get("nodes") or 0),
        "memory_temporary_nodes": int(graph_counts_view.get("temporary_nodes") or 0),
        "memory_persistent_nodes": int(graph_counts_view.get("persistent_nodes") or 0),
        "memory_edges": int(graph_counts_view.get("edges") or 0),
        "memory_isolated_nodes": int(graph_counts_view.get("isolated_nodes") or 0),
        "memory_projected_nodes": len(projected_nodes),
        "memory_fresh_nodes": sum(1 for item in projected_nodes if item.get("freshness") == "fresh"),
        "memory_degraded_nodes": sum(1 for item in projected_nodes if item.get("freshness") in {"degraded", "stale"}),
        "memory_semantic_nodes": sum(1 for item in projected_nodes if item.get("freshness") in {None, "semantic", "unbound"}),
        "evidence_items": len(evidence or {}),
        "memory_rejection_events": len(memory_feedback),
        "memory_rejections": len(memory_feedback),
        "memory_rejection_reasons": rejection_reasons[:12],
        "exploration_map_items": len(exploration_map(session, registry)),
        "mechanical_coverage": mechanical_coverage_state(session),
        "execution_convergence": ExecutionProgress.from_dict(session.execution_progress).convergence_view(
            execution.provider_total_tokens_actual if execution is not None else 0
        ),
        "reality_epoch": int(session.reality_epoch),
        "limitations": list(limitations or []),
        "failure_code": failure_code,
    }
    if execution is not None:
        usage = execution.usage_view()
        details["llm_usage"] = usage
        details["conversation_messages_materialized"] = int(usage.get("conversation_messages_materialized") or 0)
        details["conversation_messages_omitted"] = int(usage.get("conversation_messages_omitted") or 0)
        details["older_history_available"] = bool(usage.get("conversation_messages_omitted"))
        details["llm_calls"] = execution.ledger_view()
    return {k: v for k, v in details.items() if v not in (None, "", [], {})}


def _run(
    session: AgentSession,
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    full: bool,
    *,
    conversation_context: Any,
    registry: CapabilityRegistry,
) -> tuple:
    execution = current_execution()
    progress_tracker = ExecutionProgress.from_dict(session.execution_progress)
    wire_retry_surface: Optional[str] = None

    while True:
        if execution is not None:
            remaining = int(execution.provider_tokens_remaining)
            limit = max(1, int(execution.provider_token_limit or 1))
            in_salvage_band = bool(
                int(execution.provider_total_tokens_actual or 0) > 0
                and remaining * 100 <= limit * 15
            )
            if in_salvage_band:
                _feedback(
                    session, "BUDGET_SALVAGE",
                    provider_tokens_remaining=remaining,
                    provider_token_limit=limit,
                    fact=(
                        "The logical execution is inside the final 15% of its provider token budget. "
                        "Existing Session, Evidence and Observations remain valid."
                    ),
                    guidance=(
                        "Interpret this budget fact together with the available Evidence. "
                        "Main decides whether to consolidate, continue exploring, or conclude."
                    ),
                )
            if (
                in_salvage_band
                and not execution.salvage_checkpoint_emitted
                and bool(execution.execution_id)
            ):
                execution.salvage_checkpoint_emitted = True
                session.execution_progress = progress_tracker.to_dict()
                pending = _recoverable_pending(
                    session, execution,
                    reason="budget_salvage",
                    resume_hint="Resume the same AgentSession, active Task, cognitive surface, Evidence, Frontiers and budget ledger.",
                )
                return _return(
                    "recoverable_checkpoint", "Recoverable budget checkpoint persisted.", pending,
                    _details(session, "recoverable_checkpoint", config, registry, provider_context), full,
                )

        session.turn += 1
        if execution is not None:
            execution.agent_turns += 1

        surface = str(session.cognitive_surface or "navigation")
        prompt, _available = _compile_prompt(
            session, config, provider_context, conversation_context, registry,
        )
        try:
            decision = _call_surface_llm(surface, prompt, config)
        except ErroLLM as error:
            if _structured_error(error):
                detail = str(getattr(getattr(error, "structured_error", None), "detail", "") or str(error))[:900]
                if wire_retry_surface == surface:
                    return _terminal_return(
                        session, "failed", "A resposta permaneceu incompatível com o wire cognitivo atual.",
                        _details(
                            session, "failed", config, registry, provider_context,
                            failure_code="ECC_WIRE_INVALID",
                            limitations=[detail],
                        ),
                        full, provider_context=provider_context,
                    )
                wire_retry_surface = surface
                _feedback(
                    session, "ECC_WIRE_RETRY",
                    rejected_code=error.error_code,
                    surface=surface,
                    detail=detail,
                    state_unchanged=True,
                    guidance="Emit a fresh decision using the current cognitive-surface wire. Preserve Task and Runtime facts.",
                )
                continue
            code = error.error_code or "LLM_FAILED"
            return _terminal_return(
                session, "failed", f"A chamada LLM falhou: {code}.",
                _details(session, "failed", config, registry, provider_context, failure_code=code, limitations=[str(error)]), full,
                provider_context=provider_context,
            )
        except Exception as error:
            return _terminal_return(
                session, "failed", "O runtime ECC encontrou um erro interno.",
                _details(session, "failed", config, registry, provider_context, failure_code="ECC_RUNTIME_ERROR", limitations=[str(error)]), full,
                provider_context=provider_context,
            )

        _resolve_feedback(session, "USER_CHOICE")
        memory_changed, task_state_progress = _apply_semantic_sidecars(
            session, decision, registry=registry, provider_context=provider_context, execution=execution,
        )

        # Navigation owns the only semantic ECC choice. Selecting Explore/Build
        # changes only the next physical protocol surface; no operation is
        # executed until Main authors it under that family's schema.
        if surface == "navigation":
            kind = str(decision.get("type") or "")
            if kind == "concluir":
                response = str(decision.get("response") or "").strip()
                choices = decision.get("choices")
                if isinstance(choices, list) and len(choices) >= 2:
                    interaction_id = f"ecc-choice-{session.turn:04d}"
                    pending = {
                        "pending_schema_version": PENDING_SCHEMA_VERSION,
                        "continuation_kind": "semantic_choice",
                        "question": response,
                        "session": session.to_dict(),
                        "execution_state": (execution.continuation_state() if execution is not None else None),
                        "interaction_id": interaction_id,
                        "options": [str(label) for label in choices],
                        "allow_free_text": bool(decision.get("allow_free_text", True)),
                    }
                    validate_pending_continuation(pending)
                    return _return(
                        "choice_required", response, pending,
                        _details(session, "choice_required", config, registry, provider_context), full,
                    )
                clear_pending_results(session)
                return _terminal_return(
                    session, "completed", response,
                    _details(session, "completed", config, registry, provider_context), full,
                    provider_context=provider_context,
                )
            if kind == "explorar":
                _set_surface(session, "explore", execution)
                continue
            if kind == "construir":
                _set_surface(session, "build", execution)
                continue
            return _terminal_return(
                session, "failed", "A Navigation Surface retornou uma decisão ECC inválida.",
                _details(session, "failed", config, registry, provider_context, failure_code="ECC_NAVIGATION_INVALID"), full,
                provider_context=provider_context,
            )

        if decision.get("return_to_ecc") is True:
            _set_surface(session, "navigation", execution)
            continue

        if surface == "explore":
            kind = "explorar"
            selected = [item for item in (decision.get("operations") or []) if isinstance(item, dict)]
        elif surface == "build":
            kind = "construir"
            selected = [{
                "operation": decision.get("operation"),
                "arguments": decision.get("arguments") or {},
            }]
        else:
            raise RuntimeError("COGNITIVE_SURFACE_INVALID")

        signature = json.dumps(
            {"type": kind, "operations": selected},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        )
        outcomes = []
        action_blocked = progress_tracker.is_blocked(signature, session.reality_epoch)
        if action_blocked:
            recovery_frontiers = [
                item for item in frontier_view(session.observation_ledger)
                if isinstance(item, dict) and item.get("status") == "open"
            ]
            recovery_evidence = [str(v) for v in (session.evidence or {}).keys() if str(v)][-12:]
            for selected_op in selected:
                outcomes.append(DispatchOutcome({
                    "operation": str(selected_op.get("operation") or ""),
                    "status": "recovery_required",
                    "ok": True,
                    "executed": False,
                    "changed": False,
                    "error_code": "ECC_FIXED_POINT_BLOCKED",
                    "evidence_ids": recovery_evidence,
                    "frontiers": recovery_frontiers,
                    "message": (
                        "This exact action is blocked because it already reached a deterministic "
                        "no-progress state in the current reality. Runtime exposes physical recovery "
                        "coordinates; Main remains responsible for the next semantic path."
                    ),
                }, physical_progress=False))
        else:
            for selected_op in selected:
                operation = str(selected_op.get("operation") or "")
                arguments = dict(selected_op.get("arguments") or {})
                outcome = dispatch(
                    session, action_kind=kind, operation=operation, arguments=arguments,
                    config=config, provider_context=provider_context, registry=registry,
                    pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
                    execution_state=(execution.continuation_state() if execution is not None else None),
                )
                outcomes.append(outcome)
                if outcome.pending is not None:
                    # A Build selection has already left the Build Surface. After
                    # confirmed physical execution Main returns to Navigation.
                    if kind == "construir":
                        _set_surface(session, "navigation", execution)
                        outcome.pending["session"] = session.to_dict()
                    return _return(
                        "confirmation_required", str(outcome.pending.get("question") or ""), outcome.pending,
                        _details(session, "confirmation_required", config, registry, provider_context), full,
                    )

        set_pending_results(session, [outcome.result for outcome in outcomes])

        physical_progress = any(outcome.physical_progress for outcome in outcomes)
        progress = progress_tracker.observe(
            action_signature=signature,
            results=[outcome.result for outcome in outcomes],
            physical_progress=physical_progress,
            task_state_progress=task_state_progress,
            reality_epoch=session.reality_epoch,
            operation_count=len(selected),
            provider_tokens_total=(execution.provider_total_tokens_actual if execution is not None else 0),
            coverage_advanced=any(
                bool(outcome.result.get("executed") is True and isinstance(outcome.result.get("coverage"), dict) and outcome.result.get("coverage"))
                for outcome in outcomes
            ),
            physical_mutations=sum(
                1 for outcome in outcomes
                if outcome.result.get("changed") is True
                and isinstance(outcome.result.get("physical_effect"), dict)
            ),
        )
        session.execution_progress = progress_tracker.to_dict()

        # Build is deliberately one mutation attempt per surface. Main must
        # navigate again after the resulting physical facts.
        if kind == "construir":
            _set_surface(session, "navigation", execution)

        if progress.meaningful_progress:
            wire_retry_surface = None
            _resolve_feedback(
                session, "ECC_WIRE_RETRY", "NO_PROGRESS",
                "CONFIRMATION_EXECUTION_FAILED",
            )
        else:
            coverage = mechanical_coverage_state(session)
            open_frontiers = list(coverage.get("open_frontiers") or [])
            _feedback(
                session, "NO_PROGRESS",
                repeated_operations=[str(item.get("operation") or "") for item in selected],
                physical_execution=any(bool(outcome.result.get("executed") is True) for outcome in outcomes),
                new_physical_observation=bool(physical_progress),
                new_runtime_result=False,
                new_memory=bool(memory_changed),
                task_state_transition=bool(task_state_progress),
                reality_epoch=session.reality_epoch,
                repeat_count=progress.no_progress_repeat_count,
                action_blocked=True,
                blocked_action_count=progress_tracker.blocked_action_count(),
                open_frontier_ids=[str(v.get("id")) for v in open_frontiers if isinstance(v, dict) and v.get("id")],
                fact=(
                    "This deterministic action/result state produced no new observable Runtime information. "
                    "The exact action is blocked for the current reality epoch."
                ),
                guidance=(
                    "Runtime exposes open Frontier/Evidence/coverage coordinates when available. "
                    "Main alone decides whether to continue one, recall Evidence, choose another operation, return to ECC, or conclude."
                ),
            )
            session.execution_progress = progress_tracker.to_dict()
            if (
                execution is not None
                and bool(execution.execution_id)
                and progress_tracker.checkpoint_needed_for_block(signature, session.reality_epoch)
            ):
                # checkpoint_needed_for_block mutates deterministic progress
                # state. Persist that mark before creating the checkpoint so a
                # restart cannot emit the same fixed-point checkpoint again.
                session.execution_progress = progress_tracker.to_dict()
                pending = _recoverable_pending(
                    session, execution,
                    reason="stalled_recoverable",
                    resume_hint="Resume the same Task-anchored cognitive surface with its Session, Evidence, Frontiers and execution-progress state.",
                )
                return _return(
                    "recoverable_checkpoint", "Recoverable fixed-point checkpoint persisted.", pending,
                    _details(session, "recoverable_checkpoint", config, registry, provider_context), full,
                )


def _resume_confirmation(
    session: AgentSession,
    pending: Dict[str, Any],
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    full: bool,
    registry: CapabilityRegistry,
) -> tuple:
    result = confirm_pending(session, pending, config=config, provider_context=provider_context, registry=registry)
    set_pending_results(session, [result])
    if result.get("ok") is False:
        _feedback(session, "CONFIRMATION_EXECUTION_FAILED", error_code=result.get("error_code"))
    return _run(session, config, provider_context, full, conversation_context=None, registry=registry)


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
    if registry is None:
        raise ValueError("CAPABILITY_REGISTRY_REQUIRED")
    full = bool(retornar_detalhes)
    provider_context = provider_context or {}
    execution = current_execution()

    if retomar:
        try:
            validate_pending_continuation(retomar, persisted=bool("id" in retomar))
            session = AgentSession.from_dict(retomar.get("session") or {})
        except ValueError as error:
            code = str(error)
            return _return(
                "failed", "A continuação pertence a um contrato incompatível com ECC.", None,
                {"status": "failed", "failure_code": code, "architecture": "ECC"}, full,
            )
        if execution is not None:
            execution.bind_session_baseline(session, reset_agent_turns=False)
            execution.assert_canonical_request(session.request)
        if retomar.get("continuation_kind") == "recoverable_execution":
            registry.rehydrate_materials(
                material_items(session.observation_ledger),
                {"config": config or {}, "provider_context": provider_context or {}},
            )
            _feedback(
                session, "RECOVERED_EXECUTION",
                checkpoint_reason=str(retomar.get("checkpoint_reason") or ""),
                fact="Runtime rehydrated the same logical AgentSession from a canonical recoverable checkpoint.",
            )
            return _run(session, config, provider_context, full, conversation_context=None, registry=registry)

        control = confirmation_control(resposta_usuario)
        # Cancellation is always safe to honor: it performs no deferred mutation
        # and releases logical-task navigation state immediately.
        if control == "cancelar":
            if retomar.get("continuation_kind") == "capability_confirmation":
                cancel_pending(
                    session, retomar, config=config, provider_context=provider_context, registry=registry,
                )
            else:
                session.pending_operation = {}
            return _terminal_return(
                session, "cancelled", "Ok, cancelado. Nenhuma alteração pendente foi aplicada.",
                _details(session, "cancelled", config, registry, provider_context, failure_code="CANCELLED"), full,
                provider_context=provider_context,
            )
        if retomar.get("continuation_kind") == "semantic_choice":
            selected = resolve_semantic_choice(resposta_usuario, retomar)
            if selected is None:
                return _return(
                    "choice_required", str(retomar.get("question") or "Escolha como continuar."), retomar,
                    _details(session, "choice_required", config, registry, provider_context, failure_code="EXPLICIT_CHOICE_REQUIRED"), full,
                )
            _feedback(
                session, "USER_CHOICE", selected=selected,
                interaction_id=str(retomar.get("interaction_id") or ""),
                fact="The user selected this semantic continuation path.",
            )
            return _run(session, config, provider_context, full, conversation_context=None, registry=registry)
        if control != "aplicar":
            return _return(
                "confirmation_required", str(retomar.get("question") or "Confirmação explícita necessária."), retomar,
                _details(session, "confirmation_required", config, registry, provider_context, failure_code="EXPLICIT_CONFIRMATION_REQUIRED"), full,
            )
        registry.rehydrate_materials(
            material_items(session.observation_ledger),
            {"config": config or {}, "provider_context": provider_context or {}},
        )
        return _resume_confirmation(session, retomar, config, provider_context, full, registry)

    # The host supplies only a physical conversation-boundary signal. Runtime
    # never projects the raw transcript. temporary memory is not chat-local,
    # so a conversation boundary does not erase the Memory Graph.
    sync_memory_lifecycle(provider_context, conversation_context, execution_id=execution_id)
    session = AgentSession(str(objetivo or ""), execution_id=execution_id)
    if execution is not None:
        execution.bind_session_baseline(session)
        execution.bind_canonical_request(session.request)
    set_pending_results(session, seed_runtime_failure(session.observation_ledger, conversation_context))
    return _run(session, config, provider_context, full, conversation_context=conversation_context, registry=registry)



def compile_cache_warmup_prompt(
    config: Dict[str, Any], provider_context: Dict[str, Any], registry: CapabilityRegistry,
) -> CanonicalPrompt:
    """Build the stable prefix with a disposable dynamic suffix.

    This is provider-neutral. Hosts may call it before serving traffic only when
    their configured provider/cache policy makes prewarming worthwhile. No
    decision returned by the warmup call is executed and no memory delta is
    committed.
    """
    session = AgentSession(
        "Provider cache warmup only. Conclude briefly and emit no memory_delta.",
        execution_id="cache-warmup",
    )
    prompt, _ = _compile_prompt(session, config, provider_context, {"recent_messages": []}, registry)
    return prompt

def executar_agente(
    objetivo: str,
    config: Dict[str, Any],
    provider_context: Optional[Dict[str, Any]] = None,
    retomar: Optional[Dict[str, Any]] = None,
    retornar_detalhes: bool = False,
    execution_id: Optional[str] = None,
    conversation_context: Any = None,
    resposta_usuario: Optional[str] = None,
    source_job_id: Optional[int] = None,
    registry: CapabilityRegistry = None,
):
    """Public ECC entry point kept under the product-level agent name."""
    if registry is None:
        raise ValueError("CAPABILITY_REGISTRY_REQUIRED")
    if retomar and isinstance(retomar, dict) and isinstance(retomar.get("execution_state"), dict):
        try:
            execution = ExecutionContext.from_continuation_state(
                config, retomar["execution_state"], source_job_id=source_job_id,
            )
        except ValueError:
            # The pending-continuation validator will return the canonical public
            # incompatibility result; never silently start a fresh budget here.
            execution = ExecutionContext.from_config(
                config, execution_id=execution_id, source_job_id=source_job_id,
            )
    else:
        execution = ExecutionContext.from_config(
            config, execution_id=execution_id, source_job_id=source_job_id,
        )
    token = bind_execution(execution)
    try:
        return _executar_agente_bound(
            objetivo,
            config,
            provider_context=provider_context,
            retomar=retomar,
            retornar_detalhes=retornar_detalhes,
            execution_id=execution_id,
            conversation_context=conversation_context,
            resposta_usuario=resposta_usuario,
            registry=registry,
        )
    finally:
        execution.cleanup()
        reset_execution(token)
