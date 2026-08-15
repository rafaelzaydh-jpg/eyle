"""Eyle ECC agent core.

The cognitive core is deliberately small: Main chooses Explorar, Construir or
Concluir. Runtime executes the selected physical operation and returns facts.
There are no phase routers, task/investigation ledgers, completion gates or
model-managed evidence identifiers in this module.
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from llm.executar import ErroLLM, PROMPT_ECC, executar_ecc as executar_ecc_llm
from llm.structured import contract_instruction
from eyle.capabilities.registry import CapabilityRegistry
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation, confirmation_control
from eyle.runtime.ecc_runtime import (
    available_internal,
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
)
from eyle.runtime.token_budget import available_user_prompt_tokens, estimate_tokens
from .ecc import catalog as ecc_catalog, public_name
from .memory import apply_memory_sidecar, memory_environment, memory_graph_view
from .session import AgentSession


def _return(status: str, text: str, pending: Any, details: Dict[str, Any], full: bool):
    return (status, text, pending, details) if full else (status, text, pending)


def _conversation_history(context: Any) -> List[Dict[str, str]]:
    messages = list((context or {}).get("recent_messages") or []) if isinstance(context, dict) else []
    out: List[Dict[str, str]] = []
    for item in messages[-12:]:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            continue
        role = str(item.get("role") or "")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content.strip()})
    return out


def _benchmark_suppress_conversation_background() -> bool:
    """Diagnostic ablation switch; never mutates or deletes stored conversation history."""
    value = str(os.getenv("EYLE_BENCHMARK_SUPPRESS_CONVERSATION_BACKGROUND", "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    room = max(0, max_chars - 80)
    head = room // 2
    return text[:head] + "\n...[ECC context cropped at physical context boundary]...\n" + text[-(room-head):]


def _shrink_payload(payload: Dict[str, Any], budget_tokens: int, chars_per_token: int) -> Dict[str, Any]:
    """Mechanical context fitting; never semantic routing or relevance selection."""
    clone = copy.deepcopy(payload)
    if estimate_tokens(clone, chars_per_token) <= budget_tokens:
        return clone

    # Drop oldest conversation first; current request is never cropped away.
    history = clone.get("conversation_background")
    while isinstance(history, list) and len(history) > 2 and estimate_tokens(clone, chars_per_token) > budget_tokens:
        history.pop(0)

    # Exploration map is physical navigation metadata and can be reduced by age.
    nav = clone.get("exploration_map")
    while isinstance(nav, list) and len(nav) > 8 and estimate_tokens(clone, chars_per_token) > budget_tokens:
        nav.pop(0)

    # Persistent Memory is already a bounded graph view. Crop the least-ranked tail
    # mechanically if a pathological prompt still exceeds the physical budget.
    memory_graph = clone.get("memory_graph")
    if isinstance(memory_graph, dict):
        nodes = memory_graph.get("nodes")
        while isinstance(nodes, list) and len(nodes) > 6 and estimate_tokens(clone, chars_per_token) > budget_tokens:
            nodes.pop()
        if isinstance(nodes, list):
            keep = {str(item.get("id") or "") for item in nodes if isinstance(item, dict)}
            edges = memory_graph.get("edges")
            if isinstance(edges, list):
                memory_graph["edges"] = [
                    item for item in edges if isinstance(item, dict)
                    and str(item.get("source") or "") in keep and str(item.get("target") or "") in keep
                ]

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
        if isinstance(feedback, list) and len(feedback) > 6:
            clone["runtime_feedback"] = feedback[-6:]
    return clone


def _compile_prompt(
    session: AgentSession,
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    conversation_context: Any,
    registry: CapabilityRegistry,
) -> Tuple[str, set[str]]:
    if not session.conversation_background:
        session.conversation_background = _conversation_history(conversation_context)
    execution = current_execution()
    if execution is not None:
        execution.assert_canonical_request(session.request)

    available = available_internal(registry, config, provider_context)
    surface = ecc_catalog(registry, config, available)
    graph_view = memory_graph_view(
        session, query=session.request, registry=registry, config=config, provider_context=provider_context, limit=14,
    )
    # Keep the stable capability/environment prefix first for DeepSeek prefix cache.
    # Memory is dynamic by design and therefore lives after request/background.
    suppress_background = _benchmark_suppress_conversation_background()
    projected_background = [] if suppress_background else session.conversation_background[-10:]
    payload = {
        "ecc_operations": surface,
        "runtime_environment": registry.environment({"config": config or {}, "provider_context": provider_context or {}}),
        "current_request": session.request,
        "objective_state": copy.deepcopy(session.objective_state),
        "conversation_background": projected_background,
        "request_context": session.request_context[-8:],
        "memory_graph": graph_view,
        "exploration_map": exploration_map(session, registry),
        "latest_observations": copy.deepcopy(pending_results(session)),
        "runtime_effects": effects_view(session),
        "runtime_feedback": copy.deepcopy(session.runtime_feedback[-8:]),
        "turn": session.turn,
    }

    llm_cfg = config.get("llm") or {}
    context_cfg = config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    output_tokens = int(llm_cfg.get("agent_max_tokens", 3600) or 3600)
    calibration = execution.prompt_token_calibration if execution is not None else 1.0
    full_system_prompt = PROMPT_ECC.rstrip() + "\n\n" + contract_instruction("ecc")
    prompt_budget = available_user_prompt_tokens(
        config, full_system_prompt, output_tokens=output_tokens, token_estimate_multiplier=calibration,
    )
    pre_tokens = estimate_tokens(payload, chars_per_token)
    fitted = _shrink_payload(payload, prompt_budget, chars_per_token)
    prompt = json.dumps(fitted, ensure_ascii=False, separators=(",", ":"), default=str)
    post_tokens = estimate_tokens(prompt, chars_per_token)
    if execution is not None:
        components = {}
        for name in (
            "current_request", "objective_state", "request_context", "conversation_background", "memory_graph",
            "exploration_map", "latest_observations", "runtime_effects", "runtime_feedback",
            "runtime_environment", "ecc_operations",
        ):
            value = fitted.get(name)
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
            metric = {
                "characters": len(encoded),
                "estimated_tokens": estimate_tokens(encoded, chars_per_token),
            }
            if isinstance(value, (list, dict)):
                metric["items"] = len(value)
            components[name] = metric
        execution.begin_call(mode="ecc", turn=session.turn, prompt={
            "characters": len(prompt),
            "estimated_tokens": post_tokens,
            "pre_crop_estimated_tokens": pre_tokens,
            "crop_applied": post_tokens < pre_tokens,
            "prompt_budget_tokens": prompt_budget,
            "ecc_explore_operations": len(surface.get("explorar") or []),
            "ecc_build_operations": len(surface.get("construir") or []),
            "memory_nodes_projected": len((fitted.get("memory_graph") or {}).get("nodes") or []) if isinstance(fitted.get("memory_graph"), dict) else 0,
            "memory_edges_projected": len((fitted.get("memory_graph") or {}).get("edges") or []) if isinstance(fitted.get("memory_graph"), dict) else 0,
            "latest_observation_items": len(fitted.get("latest_observations") or []),
            "conversation_background_suppressed_for_benchmark": suppress_background,
            "conversation_background_stored_items": len(session.conversation_background),
            "conversation_background_projected_items": len(fitted.get("conversation_background") or []),
            "components_after": components,
            "semantic_packet_fields": list(fitted.keys()),
        })
    return prompt, available


def _structured_error(error: Exception) -> bool:
    return str(getattr(error, "error_code", "") or "").startswith("STRUCTURED_RESPONSE_INVALID:ecc:")


def _feedback(session: AgentSession, code: str, **facts: Any) -> None:
    item = {"code": str(code), **{k: copy.deepcopy(v) for k, v in facts.items() if v is not None}}
    session.runtime_feedback.append(item)
    session.runtime_feedback = session.runtime_feedback[-20:]


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
    graph_view = memory_graph_view(
        session, query=session.request, registry=registry, config=config, provider_context=provider_context or {}, limit=30,
    )
    graph_env = memory_environment(provider_context or {})
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
        "turns": int(session.turn),
        "operations_used": used,
        "operation_history": events,
        "physical_capability_calls": physical_capability_calls(session),
        "operation_replays": replay_count(session),
        "observation_ledger_size": len(events),
        "grounding_count_total": len(materials),
        "memory_nodes": int(graph_env.get("nodes") or 0),
        "memory_edges": int(graph_env.get("edges") or 0),
        "memory_isolated_nodes": int(graph_env.get("isolated_nodes") or 0),
        "memory_projected_nodes": len(projected_nodes),
        "memory_fresh_nodes": sum(1 for item in projected_nodes if item.get("freshness") == "fresh"),
        "memory_degraded_nodes": sum(1 for item in projected_nodes if item.get("freshness") in {"degraded", "stale"}),
        "memory_semantic_nodes": sum(1 for item in projected_nodes if item.get("freshness") in {"semantic", "unbound"}),
        "objective_present": isinstance(session.objective_state, dict),
        "objective_children": len((session.objective_state or {}).get("children") or []) if isinstance(session.objective_state, dict) else 0,
        "evidence_items": len(evidence or {}),
        "memory_rejection_events": len(memory_feedback),
        "memory_rejection_reasons": rejection_reasons[:12],
        "exploration_map_items": len(exploration_map(session, registry)),
        "reality_epoch": int(session.reality_epoch),
        "limitations": list(limitations or []),
        "failure_code": failure_code,
    }
    if execution is not None:
        details["llm_usage"] = execution.usage_view()
        details["llm_calls"] = execution.ledger_view()
    return {k: v for k, v in details.items() if v not in (None, "", [], {})}


def _deadline_exceeded() -> bool:
    execution = current_execution()
    return execution is not None and time.monotonic() >= float(execution.deadline_monotonic)


def _apply_objective_sidecar(session: AgentSession, raw: Any) -> bool:
    """Apply LLM-authored objective state mechanically, without semantic interpretation.

    The Runtime stores/replaces/clears exactly what Main declared. Objective status
    never gates ECC completion or selects operations. Missing sidecars are tolerated
    only for in-process test doubles; the canonical structured contract requires it.
    """
    if not isinstance(raw, dict):
        return False
    disposition = str(raw.get("disposition") or "")
    if disposition == "updated" and isinstance(raw.get("state"), dict):
        new_state = copy.deepcopy(raw["state"])
        changed = new_state != session.objective_state
        session.objective_state = new_state
        return changed
    if disposition == "cleared":
        changed = session.objective_state is not None
        session.objective_state = None
        return changed
    return False


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
    protocol_retry_streak = 0
    last_signature: Optional[str] = None
    stagnant = 0

    while True:
        if _deadline_exceeded():
            return _return(
                "failed", "A tarefa excedeu o prazo de execução.", None,
                _details(session, "failed", config, registry, provider_context, failure_code="TASK_DEADLINE_EXCEEDED"), full,
            )
        session.turn += 1
        if execution is not None:
            execution.agent_turns += 1

        prompt, _available = _compile_prompt(session, config, provider_context, conversation_context, registry)
        try:
            decision = executar_ecc_llm(prompt, config)
        except ErroLLM as error:
            if _structured_error(error) and protocol_retry_streak < 1:
                protocol_retry_streak += 1
                _feedback(session, "ECC_PROTOCOL_RETRY", rejected_code=error.error_code, state_unchanged=True)
                continue
            code = error.error_code or "LLM_FAILED"
            return _return(
                "failed", f"A chamada LLM falhou: {code}.", None,
                _details(session, "failed", config, registry, provider_context, failure_code=code, limitations=[str(error)]), full,
            )
        except Exception as error:
            return _return(
                "failed", "O runtime ECC encontrou um erro interno.", None,
                _details(session, "failed", config, registry, provider_context, failure_code="ECC_RUNTIME_ERROR", limitations=[str(error)]), full,
            )

        # A valid structured decision closes the current protocol-repair episode.
        # A later malformed decision gets its own bounded repair chance instead of
        # inheriting a retry already spent many turns earlier.
        protocol_retry_streak = 0

        # Objective State is transient semantic cognition owned by Main. Runtime
        # only stores/replaces/clears the declared state; it never interprets
        # objective status, turns it into a plan, or uses it as a completion gate.
        _apply_objective_sidecar(session, decision.get("objective"))

        # Persistent Memory is a transversal sidecar of the chosen ECC move.
        # Main owns semantic graph edits; Runtime only validates/persists them.
        memory_outcome = apply_memory_sidecar(
            session, decision.get("memory"), registry=registry, provider_context=provider_context,
        )
        if memory_outcome.get("ok") is not True:
            _feedback(
                session, "MEMORY_DELTA_REJECTED", error_code=memory_outcome.get("error_code"),
                detail=memory_outcome.get("detail"), state_unchanged=True,
            )
            continue
        memory_progress = bool(memory_outcome.get("changed"))

        kind = str(decision.get("type") or "")
        if kind == "concluir":
            clear_pending_results(session)
            return _return(
                "completed", str(decision.get("response") or "").strip(), None,
                _details(session, "completed", config, registry, provider_context), full,
            )

        operation = str(decision.get("operation") or "")
        arguments = dict(decision.get("arguments") or {})
        signature = json.dumps({"type": kind, "operation": operation, "arguments": arguments}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        outcome = dispatch(
            session,
            action_kind=kind,
            operation=operation,
            arguments=arguments,
            config=config,
            provider_context=provider_context,
            registry=registry,
            pending_schema_version=PENDING_SCHEMA_VERSION,
            validate_pending=validate_pending_continuation,
        )
        if outcome.pending is not None:
            return _return(
                "confirmation_required", str(outcome.pending.get("question") or ""), outcome.pending,
                _details(session, "confirmation_required", config, registry, provider_context), full,
            )
        set_pending_results(session, [outcome.result])

        no_new_reality = not outcome.physical_progress and not memory_progress
        if signature == last_signature and no_new_reality:
            stagnant += 1
        elif no_new_reality and str(outcome.result.get("status") or "") == "already_observed":
            stagnant += 1
        else:
            stagnant = 0
        last_signature = signature
        if stagnant >= 2:
            _feedback(
                session, "NO_PROGRESS",
                repeated_operation=operation,
                physical_execution=bool(outcome.result.get("executed") is True),
                new_physical_observation=bool(outcome.physical_progress),
                new_memory=False,
                reality_epoch=session.reality_epoch,
                fact="Equivalent requests are producing no new physical observation or persistent memory change.",
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
            execution.bind_session_baseline(session)
            execution.bind_canonical_request(session.request)
        control = confirmation_control(resposta_usuario)
        if control == "cancelar":
            session.pending_operation = {}
            return _return(
                "cancelled", "Ok, cancelado. A alteração pendente não foi aplicada.", None,
                _details(session, "cancelled", config, registry, provider_context, failure_code="CANCELLED"), full,
            )
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

    session = AgentSession(str(objetivo or ""), execution_id=execution_id)
    if execution is not None:
        execution.bind_session_baseline(session)
        execution.bind_canonical_request(session.request)
    set_pending_results(session, seed_runtime_failure(session.observation_ledger, conversation_context))
    return _run(session, config, provider_context, full, conversation_context=conversation_context, registry=registry)


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
    execution = ExecutionContext.from_config(config, execution_id=execution_id, source_job_id=source_job_id)
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
