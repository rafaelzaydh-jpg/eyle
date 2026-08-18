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

from llm.executar import ErroLLM, PROMPT_ECC, executar_ecc as executar_ecc_llm
from llm.structured import contract_instruction
from llm.protocol import CanonicalPrompt
from eyle.capabilities.registry import CapabilityRegistry
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation, confirmation_control, resolve_semantic_choice
from eyle.runtime.ecc_runtime import (
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
)
from eyle.runtime.token_budget import available_user_prompt_tokens, estimate_tokens
from eyle.runtime.memory_graph import graph_counts
from eyle.runtime.context_materializer import materialize_conversation, materialize_latest_observations, component_metrics
from .ecc import catalog as ecc_catalog, public_name
from .memory import (
    apply_memory_sidecar, materialize_explicit_memory_view, memory_available, memory_environment,
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
    execution = current_execution()
    if execution is not None:
        execution.assert_canonical_request(session.request)

    available = available_internal(registry, config, provider_context)
    memory_enabled = memory_available(provider_context)
    surface = ecc_catalog(registry, config, available, memory_enabled=memory_enabled)
    # Only Main-explicit Memory activation is materialized automatically.
    # Global/temporary Memory remains reachable through recall but no longer
    # inflates every cognition merely because the Graph is large.
    active_memory = materialize_explicit_memory_view(
        session, registry=registry, config=config, provider_context=provider_context,
    )
    repairing_protocol = any(
        isinstance(item, dict) and item.get("code") == "ECC_PROTOCOL_RECOVERY"
        for item in session.runtime_feedback
    )
    conversation = materialize_conversation(conversation_context, config)
    if repairing_protocol:
        conversation = {
            "conversation_id": conversation.get("conversation_id"),
            "messages": [],
            "history_messages_materialized": 0,
            "history_messages_omitted": int(
                conversation.get("history_messages_materialized") or 0
            ) + int(conversation.get("history_messages_omitted") or 0),
        }
    if execution is not None:
        execution.history_messages_omitted = int(conversation.get("history_messages_omitted") or 0)
    latest = materialize_latest_observations(
        pending_results(session), config, repair=repairing_protocol,
    )
    # Stable provider/body material is physically before all per-turn state so
    # provider prefix caching can reuse it without provider-specific Core logic.
    stable_packet = {
        "ecc_operations": surface,
        "runtime_environment": registry.environment({"config": config or {}, "provider_context": provider_context or {}}),
    }
    dynamic_packet = {
        "current_request": session.request,
        "conversation": conversation,
        "memory_environment": memory_environment(provider_context),
        "memory_view": {"available": active_memory.get("available", False), "nodes": [], "edges": []} if repairing_protocol else active_memory,
        "exploration_map": [] if repairing_protocol else exploration_map(session, registry),
        "latest_observations": latest,
        "runtime_effects": [] if repairing_protocol else effects_view(session),
        "turn": session.turn,
        "runtime_feedback": copy.deepcopy(session.runtime_feedback),
    }
    payload = {**stable_packet, **dynamic_packet}

    context_cfg = config.get("context_engine") or {}
    chars_per_token = max(1, int(context_cfg.get("chars_per_token_fallback", 3) or 3))
    calibration = execution.prompt_token_calibration if execution is not None else 1.0
    full_system_prompt = PROMPT_ECC.rstrip() + "\n\n" + contract_instruction("ecc")
    prompt_budget = available_user_prompt_tokens(
        config, full_system_prompt, output_tokens=0, token_estimate_multiplier=calibration,
    )
    pre_tokens = estimate_tokens(payload, chars_per_token)
    fitted = _shrink_payload(payload, prompt_budget, chars_per_token)
    fitted_stable = {name: fitted[name] for name in ("ecc_operations", "runtime_environment") if name in fitted}
    fitted_dynamic = {name: fitted[name] for name in (
         "current_request", "conversation", "memory_environment", "memory_view", "exploration_map",
        "latest_observations", "runtime_effects", "turn", "runtime_feedback",
    ) if name in fitted}
    prompt = CanonicalPrompt(stable=fitted_stable, dynamic=fitted_dynamic)
    post_tokens = estimate_tokens(prompt.wire_text, chars_per_token)
    if execution is not None:
        components = component_metrics({
            name: fitted.get(name) for name in (
                "current_request", "conversation", "memory_view", "memory_environment", "exploration_map",
                "latest_observations", "runtime_effects", "runtime_feedback", "runtime_environment", "ecc_operations",
            )
        }, config)
        execution.begin_call(mode="ecc", turn=session.turn, prompt={
            "characters": len(prompt.wire_text),
            "stable_prefix_characters": len(prompt.stable_text),
            "stable_prefix_hash": prompt.stable_hash,
            "estimated_tokens": post_tokens,
            "pre_crop_estimated_tokens": pre_tokens,
            "crop_applied": post_tokens < pre_tokens,
            "prompt_budget_tokens": prompt_budget,
            "local_context_limit_enabled": prompt_budget is not None,
            "cognition_reason": "protocol_repair" if repairing_protocol else ("continuation" if session.turn > 0 else "normal"),
            "conversation_messages_materialized": int((conversation or {}).get("history_messages_materialized") or 0),
            "conversation_messages_omitted": int((conversation or {}).get("history_messages_omitted") or 0),
            "ecc_explore_operations": len(surface.get("explorar") or []),
            "ecc_build_operations": len(surface.get("construir") or []),
            "memory_nodes_projected": len((fitted.get("memory_view") or {}).get("nodes") or []) if isinstance(fitted.get("memory_view"), dict) else 0,
            "memory_edges_projected": len((fitted.get("memory_view") or {}).get("edges") or []) if isinstance(fitted.get("memory_view"), dict) else 0,
            "latest_observation_items": len(fitted.get("latest_observations") or []),
            "components_after": components,
            "estimated_static_tokens": int(components.get("runtime_environment", {}).get("estimated_tokens", 0)) + int(components.get("ecc_operations", {}).get("estimated_tokens", 0)),
            "estimated_conversation_tokens": int(components.get("conversation", {}).get("estimated_tokens", 0)),
            "estimated_memory_tokens": int(components.get("memory_view", {}).get("estimated_tokens", 0)) + int(components.get("memory_environment", {}).get("estimated_tokens", 0)),
            "estimated_observation_tokens": int(components.get("latest_observations", {}).get("estimated_tokens", 0)),
            "estimated_feedback_tokens": int(components.get("runtime_feedback", {}).get("estimated_tokens", 0)),
            "estimated_capability_tokens": int(components.get("ecc_operations", {}).get("estimated_tokens", 0)),
            "semantic_packet_fields": list(fitted.keys()),
        })
    return prompt, available


def _structured_error(error: Exception) -> bool:
    code = str(getattr(error, "error_code", "") or "")
    return code.startswith("STRUCTURED_RESPONSE_INVALID:ecc:") or code == "LLM_STRUCTURED_RESPONSE_UNSATISFIED"


def _structured_fingerprint(error: Exception) -> str:
    observed = getattr(error, "structured_observed", None)
    try:
        observed_text = json.dumps(observed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        observed_text = str(observed or "")
    return f"{getattr(error, 'error_code', '')}|{observed_text[:4000]}"


def _structured_observed_summary(observed: Any) -> Dict[str, Any] | None:
    """Return shape-only repair facts; never echo giant rejected payloads.

    A malformed memory sidecar used to repeat source code/patch content inside
    runtime_feedback, making the next prompt much larger than the error itself.
    Main only needs the rejected envelope's structure to re-serialize it.
    """
    if not isinstance(observed, dict):
        return None
    out: Dict[str, Any] = {"top_level_keys": sorted(str(k) for k in observed.keys())[:24]}
    decision = observed.get("decision") if isinstance(observed.get("decision"), dict) else observed
    if isinstance(decision, dict):
        if decision.get("type") is not None:
            out["decision_type"] = str(decision.get("type"))[:80]
        if isinstance(decision.get("operation"), str):
            out["operation"] = decision.get("operation")[:120]
    memory = observed.get("memory_delta")
    if isinstance(memory, dict):
        memory = [memory]
    if isinstance(memory, list):
        out["memory_delta_count"] = len(memory)
        shapes = []
        for item in memory[:8]:
            if isinstance(item, dict):
                shape = {"op": str(item.get("op") or item.get("action") or "")[:80], "keys": sorted(str(k) for k in item.keys())[:20]}
                args = item.get("arguments")
                if isinstance(args, dict):
                    shape["argument_keys"] = sorted(str(k) for k in args.keys())[:30]
                shapes.append(shape)
        if shapes:
            out["memory_shapes"] = shapes
    return out


def _feedback(session: AgentSession, code: str, **facts: Any) -> None:
    # runtime_feedback is an *active repair surface*, not an append-only log.
    # Repeating the same unresolved code replaces its prior payload so protocol
    # recovery cannot snowball prompt size turn after turn. Historical telemetry
    # remains in the execution/job history.
    code = str(code)
    item = {"code": code, **{k: copy.deepcopy(v) for k, v in facts.items() if v is not None}}
    replaceable = {"ECC_PROTOCOL_RECOVERY", "MEMORY_DELTA_REJECTED", "NO_PROGRESS", "CONFIRMATION_EXECUTION_FAILED", "USER_CHOICE"}
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
        "reality_epoch": int(session.reality_epoch),
        "limitations": list(limitations or []),
        "failure_code": failure_code,
    }
    if execution is not None:
        details["llm_usage"] = execution.usage_view()
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
    protocol_failures: Dict[str, int] = {}
    last_signature: Optional[str] = None
    stagnant = 0

    while True:
        session.turn += 1
        if execution is not None:
            execution.agent_turns += 1

        prompt, _available = _compile_prompt(session, config, provider_context, conversation_context, registry)
        try:
            decision = executar_ecc_llm(prompt, config)
        except ErroLLM as error:
            if _structured_error(error):
                # A malformed cognition envelope is a recoverable
                # serialization event, not task death. The same Main receives
                # feedback on the next turn with the same Session, ExecutionContext,
                # provider-token ledger. Mechanical context/cost/safety limits
                # remain the bounded stop conditions.
                fingerprint = _structured_fingerprint(error)
                repeat_count = protocol_failures.get(fingerprint, 0) + 1
                protocol_failures[fingerprint] = repeat_count
                observed = getattr(error, "structured_observed", None)
                detail = str(getattr(getattr(error, "structured_error", None), "detail", "") or str(error))[:900]
                guidance = (
                    "Re-emit the same intended decision as the simplest valid wire JSON; do not repeat capabilities merely because serialization failed."
                    if repeat_count < 3 else
                    "The same envelope error repeated. Simplify aggressively: flat top-level type + required fields + memory_delta, preserving semantics."
                )
                _feedback(
                    session, "ECC_PROTOCOL_RECOVERY", rejected_code=error.error_code,
                    detail=detail, observed_shape=_structured_observed_summary(observed),
                    repeat_count=repeat_count, state_unchanged=True, guidance=guidance,
                )
                # Rev3.7 bounds only repeated mechanical protocol repair, not
                # legitimate cognition. The initial malformed response may be
                # followed by at most two repairs for the same fingerprint.
                if repeat_count > 2:
                    return _terminal_return(
                        session, "failed", "A resposta estruturada permaneceu inválida após reparos de protocolo.",
                        _details(
                            session, "failed", config, registry, provider_context,
                            failure_code="ECC_PROTOCOL_UNRECOVERABLE",
                            limitations=[detail],
                        ),
                        full, provider_context=provider_context,
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

        # A valid structured decision closes the current serialization episode
        # and has consumed any one-turn USER_CHOICE fact.
        protocol_failures.clear()
        _resolve_feedback(session, "ECC_PROTOCOL_RECOVERY", "USER_CHOICE")

        # Persistent learning is transversal to the chosen ECC move, but it is
        # a true sidecar in Rev3.7: parser/storage failure never vetoes a valid
        # ECC decision and never causes another LLM call by itself.
        memory_parse_error = decision.get("memory_error") if isinstance(decision.get("memory_error"), dict) else None
        if memory_parse_error is not None:
            memory_outcome = {
                "ok": False,
                "changed": False,
                "task_state_changed": False,
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
        else:
            _resolve_feedback(session, "MEMORY_DELTA_REJECTED")
            memory_changed = bool(memory_outcome.get("changed"))
            task_state_progress = bool(memory_outcome.get("task_state_changed"))

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

        selected = decision.get("operations") if kind == "explorar" else [{
            "operation": decision.get("operation"), "arguments": decision.get("arguments") or {},
        }]
        selected = [item for item in (selected or []) if isinstance(item, dict)]
        signature = json.dumps({"type": kind, "operations": selected}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        outcomes = []
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
                return _return(
                    "confirmation_required", str(outcome.pending.get("question") or ""), outcome.pending,
                    _details(session, "confirmation_required", config, registry, provider_context), full,
                )
        set_pending_results(session, [outcome.result for outcome in outcomes])

        physical_progress = any(outcome.physical_progress for outcome in outcomes)
        if physical_progress:
            _resolve_feedback(session, "NO_PROGRESS", "CONFIRMATION_EXECUTION_FAILED")
        # Every successful Build observation returns to Main.
        # This lets the same brain learn from the real post-write Material/effect,
        # attach provenance, and only then decide whether to conclude.

        # General Memory learning is valuable but cannot by itself keep a task
        # execution alive indefinitely. Only a real physical/navigation result or
        # an explicit Task Memory lifecycle transition resets task stagnation.
        no_new_reality = not physical_progress and not task_state_progress
        statuses = [str(outcome.result.get("status") or "") for outcome in outcomes]
        if signature == last_signature and no_new_reality:
            stagnant += 1
        elif no_new_reality and statuses and all(status == "already_observed" for status in statuses):
            stagnant += 1
        else:
            stagnant = 0
        last_signature = signature
        if stagnant >= 2:
            _feedback(
                session, "NO_PROGRESS",
                repeated_operations=[str(item.get("operation") or "") for item in selected],
                physical_execution=any(bool(outcome.result.get("executed") is True) for outcome in outcomes),
                new_physical_observation=bool(physical_progress), new_memory=bool(memory_changed),
                task_state_transition=bool(task_state_progress), reality_epoch=session.reality_epoch,
                fact="Equivalent requests are producing no new physical/navigation result or Task Memory lifecycle transition. General Memory edits alone do not count as task progress.",
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
