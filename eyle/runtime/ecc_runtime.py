"""Deterministic physical runtime for the ECC cognitive protocol.

This module never decides what is relevant. It only validates and executes the
operation Main selected, records physical Observation/Material, performs exact
cache checks, handles confirmation, and returns compact factual feedback.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from eyle.core.ecc import public_name, resolve
from eyle.core.evidence import evidence_record, retain_observation_evidence, evidence_ids_for_materials
from eyle.core.memory import memory_activate_result, memory_continue_result, memory_history_result, memory_overview_result, memory_relation_history_result
from eyle.runtime.execution_context import current_execution
from eyle.runtime import telemetry
from eyle.runtime.observation import (
    lookup as lookup_observation,
    record as record_observation,
    record_replay,
    register_material_candidates,
    material_items,
    navigation_view,
    physical_effect_index_view,
    equivalent_result_seen,
    frontier_store,
    frontier_view,
)


def available_internal(registry: Any, config: Dict[str, Any], provider_context: Dict[str, Any]) -> set[str]:
    execution = current_execution()
    terminal = set(execution.terminal_capabilities) if execution is not None else set()
    return registry.available_names({"config": config or {}, "provider_context": provider_context or {}}, terminal=terminal)


def _result_reality_epoch(session: Any, result: Dict[str, Any]) -> int:
    current = int(getattr(session, "reality_epoch", 0) or 0)
    effect = result.get("physical_effect") if isinstance(result, dict) else None
    if (
        result.get("ok") is True and result.get("executed") is True and result.get("changed") is True
        and isinstance(effect, dict) and effect.get("persistence") == "persistent"
    ):
        return current + 1
    return current


def project_result(session: Any, capability: str, result: Dict[str, Any], registry: Any, config: Dict[str, Any], *, freshness_token: str | None = None, freshness_arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
    produces = bool(registry.spec(capability).get("produces_grounding"))
    candidates = []
    if produces:
        for raw in result.get("observations") or []:
            if not isinstance(raw, dict):
                continue
            item = copy.deepcopy(raw)
            # Provenance must use the canonical Registry identity. Provider-local
            # source_type remains descriptive only; Evidence selection needs the
            # exact capability so its selector/freshness hooks can be recovered.
            item["source_capability"] = str(capability)
            candidates.append(item)
    grounding_ids = register_material_candidates(
        session.observation_ledger, candidates,
        reality_epoch=_result_reality_epoch(session, result),
    ) if produces else []
    store = material_items(session.observation_ledger)
    for material_id in grounding_ids:
        material = store.get(str(material_id))
        if not isinstance(material, dict):
            continue
        if freshness_token:
            material["freshness_token"] = str(freshness_token)
        if isinstance(freshness_arguments, dict):
            material["freshness_arguments"] = copy.deepcopy(freshness_arguments)
    # Every physical Material is automatically active-session Evidence. This is
    # mechanical perception bookkeeping; persistent Memory remains Main-owned.
    if grounding_ids:
        session.evidence = retain_observation_evidence(
            session.evidence, materials=store, material_ids=grounding_ids,
            reality_epoch=_result_reality_epoch(session, result),
        )
    evidence_ids = evidence_ids_for_materials(session.evidence, grounding_ids) if grounding_ids else []
    detail = registry.model_detail(capability, result.get("detail"), grounding_ids, config or {})
    out = {
        "operation": public_name(capability, registry),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "error_code": result.get("error_code"),
        "retryable": result.get("retryable"),
        "failure_scope": result.get("failure_scope"),
        "failure_resource": result.get("failure_resource"),
        "detail": detail,
        "grounding_ids": grounding_ids,
        "evidence_ids": evidence_ids,
    }
    if isinstance(result.get("physical_effect"), dict):
        out["physical_effect"] = copy.deepcopy(result["physical_effect"])
    if isinstance(result.get("coverage"), dict) and result.get("coverage"):
        out["coverage"] = copy.deepcopy(result["coverage"])
    if (
        registry.spec(capability).get("ecc_hide_frontiers") is not True
        and isinstance(result.get("frontiers"), list) and result.get("frontiers")
    ):
        out["frontiers"] = copy.deepcopy(result["frontiers"])
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _compact_cached(session: Any, entry: Dict[str, Any], operation: str) -> Dict[str, Any]:
    """Project a cached Observation without dropping recovery coordinates.

    Rev3.7.5.1 compacted away Evidence and Frontier ids. That made the recovery
    instruction self-defeating: Main was told to recall/continue while the exact
    coordinates could disappear from the next hot observation. Rev3.7.6 keeps
    only still-valid public coordinates; source bodies remain compact.
    """
    replay_result = entry.get("replay_result") if isinstance(entry.get("replay_result"), dict) else {}
    evidence_ids = [str(v) for v in replay_result.get("evidence_ids") or [] if str(v)]

    open_frontiers = []
    for item in frontier_view(
        session.observation_ledger,
        [str(v) for v in entry.get("frontier_ids") or [] if str(v)],
    ):
        if not isinstance(item, dict) or item.get("status") != "open":
            continue
        frontier_id = str(item.get("id") or "")
        raw = frontier_store(session.observation_ledger).get(frontier_id)
        if not isinstance(raw, dict):
            continue
        if int(raw.get("reality_epoch") or 0) != int(getattr(session, "reality_epoch", 0) or 0):
            continue
        open_frontiers.append(copy.deepcopy(item))

    if open_frontiers:
        message = (
            "Requested physical scope is already covered. "
            "Runtime has preserved open continuation coordinate(s) in frontiers; operation=continue is available for those coordinates; "
            "Evidence coordinates are also returned when available. Main decides how to proceed."
        )
    else:
        message = (
            "Requested physical scope is already covered and no open Frontier is attached to this cached scope. "
            "Evidence coordinates are returned when available. Main decides whether another physical scope is needed."
        )
    out = {
        "operation": operation,
        "status": "already_observed",
        "ok": True,
        "executed": False,
        "changed": False,
        "grounding_ids": [str(v) for v in entry.get("grounding_ids") or []],
        "evidence_ids": evidence_ids,
        "frontiers": open_frontiers,
        "coverage": copy.deepcopy(entry.get("coverage") or {}),
        "source_turn": entry.get("turn"),
        "message": message,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def _advance_epoch(session: Any, result: Dict[str, Any]) -> None:
    effect = result.get("physical_effect") if isinstance(result, dict) else None
    if (
        result.get("ok") is True and result.get("executed") is True and result.get("changed") is True
        and isinstance(effect, dict) and effect.get("persistence") == "persistent"
    ):
        session.reality_epoch += 1


def _runtime_context(session: Any, config: Dict[str, Any], provider_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "config": config,
        "provider_context": provider_context,
        "session": session,
        "grounding": material_items(session.observation_ledger),
        "observation_ledger": session.observation_ledger,
        "reality_epoch": int(session.reality_epoch),
    }


def recall_evidence(session: Any, evidence_id: str, registry: Any, runtime_ctx: Dict[str, Any] | None = None) -> Dict[str, Any]:
    record = evidence_record(session.evidence, evidence_id)
    if record is None:
        return {
            "operation": "recall", "status": "failed", "ok": False,
            "executed": False, "changed": False, "error_code": "EVIDENCE_NOT_FOUND",
        }
    material_id = str(record.get("material_id") or "")
    material = material_items(session.observation_ledger).get(material_id)
    if not isinstance(material, dict):
        return {
            "operation": "recall", "status": "failed", "ok": False,
            "executed": False, "changed": False, "error_code": "EVIDENCE_MATERIAL_UNAVAILABLE",
            "evidence_id": evidence_id, "material_id": material_id,
        }
    selector = dict(record.get("selector") or {})
    if selector:
        try:
            selected = registry.select_evidence(material, selector)
        except ValueError as exc:
            return {
                "operation": "recall", "status": "failed", "ok": False,
                "executed": False, "changed": False, "error_code": str(exc),
                "evidence_id": evidence_id, "material_id": material_id,
            }
    else:
        selected = {
            "locator": copy.deepcopy(material.get("locator") or {}),
            "content": material.get("content"),
            "numbered_content": material.get("numbered_content"),
            "content_hash": material.get("content_hash"),
        }
    evidence_epoch = int(record.get("reality_epoch") or 0)
    current_epoch = int(getattr(session, "reality_epoch", 0) or 0)
    same_epoch = evidence_epoch == current_epoch
    fresh, freshness_reason = (True, "no_freshness_contract")
    if runtime_ctx is not None:
        fresh, freshness_reason = registry.material_freshness(material, runtime_ctx)
    physical_proofs = {"exact_current", "token_current"}
    revalidated = bool(not same_epoch and fresh is True and str(freshness_reason) in physical_proofs)
    current_valid = bool(fresh is True and (same_epoch or revalidated))
    if not current_valid and fresh is True and not same_epoch:
        freshness_reason = "reality_epoch_changed"
    return {
        "operation": "recall",
        "status": "recalled" if current_valid else "recalled_stale",
        "ok": True, "executed": False, "changed": False,
        "evidence_id": evidence_id, "material_id": material_id,
        "evidence_reality_epoch": evidence_epoch, "current_reality_epoch": current_epoch,
        "current_world_valid": current_valid,
        **({"revalidated_across_epoch": True} if revalidated else {}),
        **({"freshness_reason": str(freshness_reason)} if (not current_valid or revalidated) else {}),
        "detail": {k: v for k, v in selected.items() if v is not None},
    }


@dataclass
class DispatchOutcome:
    result: Dict[str, Any]
    pending: Optional[Dict[str, Any]] = None
    physical_progress: bool = False


def dispatch(
    session: Any,
    *,
    action_kind: str,
    operation: str,
    arguments: Dict[str, Any],
    config: Dict[str, Any],
    provider_context: Dict[str, Any],
    registry: Any,
    pending_schema_version: str,
    validate_pending: Any,
    execution_state: Optional[Dict[str, Any]] = None,
) -> DispatchOutcome:
    """Execute exactly one ECC operation selected by Main."""
    if operation == "recall":
        if action_kind != "explorar" or set(arguments) != {"evidence_id"}:
            return DispatchOutcome({
                "operation": operation, "status": "failed", "ok": False, "executed": False,
                "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID",
            })
        return DispatchOutcome(recall_evidence(
            session, str(arguments.get("evidence_id") or ""), registry,
            _runtime_context(session, config, provider_context),
        ))

    if operation == "memory_overview":
        if action_kind != "explorar" or set(arguments) - {"scope"}:
            return DispatchOutcome({"operation": operation, "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID"})
        if "scope" in arguments and str(arguments.get("scope")) not in {"all", "user", "world", "global"}:
            return DispatchOutcome({"operation": operation, "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID"})
        result = memory_overview_result(session, arguments=arguments, provider_context=provider_context)
        # Memory navigation is cognitive materialization, not physical-world
        # progress. Episode safety decides whether the returned facts are novel.
        return DispatchOutcome(result, physical_progress=False)

    if operation == "memory_history":
        if action_kind != "explorar" or set(arguments) != {"id"} or not isinstance(arguments.get("id"), str):
            return DispatchOutcome({"operation": operation, "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID"})
        result = memory_history_result(session, arguments=arguments, provider_context=provider_context)
        return DispatchOutcome(result, physical_progress=False)

    if operation == "memory_relation_history":
        if action_kind != "explorar" or set(arguments) != {"id"} or not isinstance(arguments.get("id"), str):
            return DispatchOutcome({"operation": operation, "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID"})
        result = memory_relation_history_result(session, arguments=arguments, provider_context=provider_context)
        return DispatchOutcome(result, physical_progress=False)

    if operation == "memory_activate":
        allowed_memory_args = {"query", "queries", "ids", "tags", "natures", "volatilities", "relation_labels", "scope", "retention", "domain", "context_key", "include_neighbors", "limit"}
        valid = action_kind == "explorar" and not (set(arguments) - allowed_memory_args)
        valid = valid and ("query" not in arguments or isinstance(arguments.get("query"), str))
        valid = valid and ("queries" not in arguments or (isinstance(arguments.get("queries"), list) and all(isinstance(v, str) for v in arguments.get("queries") or [])))
        valid = valid and ("ids" not in arguments or (isinstance(arguments.get("ids"), list) and all(isinstance(v, str) for v in arguments.get("ids") or [])))
        valid = valid and ("tags" not in arguments or (isinstance(arguments.get("tags"), list) and all(isinstance(v, str) for v in arguments.get("tags") or [])))
        valid = valid and ("natures" not in arguments or (isinstance(arguments.get("natures"), list) and all(isinstance(v, str) for v in arguments.get("natures") or [])))
        valid = valid and ("volatilities" not in arguments or (isinstance(arguments.get("volatilities"), list) and all(isinstance(v, str) for v in arguments.get("volatilities") or [])))
        valid = valid and ("relation_labels" not in arguments or (isinstance(arguments.get("relation_labels"), list) and all(isinstance(v, str) for v in arguments.get("relation_labels") or [])))
        valid = valid and ("scope" not in arguments or str(arguments.get("scope")) in {"all", "user", "world", "global"})
        valid = valid and ("retention" not in arguments or str(arguments.get("retention")) in {"all", "temporary", "persistent"})
        valid = valid and ("domain" not in arguments or str(arguments.get("domain") or "").strip().lower() in {"all", "chat", "task", "eyle", "knowledge"})
        valid = valid and ("context_key" not in arguments or arguments.get("context_key") is None or isinstance(arguments.get("context_key"), str))
        valid = valid and ("include_neighbors" not in arguments or isinstance(arguments.get("include_neighbors"), bool))
        valid = valid and ("limit" not in arguments or (isinstance(arguments.get("limit"), int) and not isinstance(arguments.get("limit"), bool) and int(arguments.get("limit")) >= 1))
        if not valid:
            return DispatchOutcome({"operation": operation, "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "ECC_OPERATION_ARGUMENTS_INVALID"})
        result = memory_activate_result(
            session, arguments=arguments, registry=registry, config=config, provider_context=provider_context,
        )
        return DispatchOutcome(result, physical_progress=False)

    if operation == "continue" and action_kind == "explorar" and set(arguments) == {"frontier"}:
        frontier_id = str(arguments.get("frontier") or "")
        frontier = frontier_store(session.observation_ledger).get(frontier_id)
        if isinstance(frontier, dict) and str(frontier.get("source_capability") or "") == "core.memory":
            result = memory_continue_result(
                session, frontier_id=frontier_id, registry=registry, config=config, provider_context=provider_context,
            )
            return DispatchOutcome(result, physical_progress=False)

    available = available_internal(registry, config, provider_context)
    capability = resolve(operation, action_kind, registry, available)
    if capability is None:
        return DispatchOutcome({
            "operation": operation, "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "ECC_OPERATION_NOT_AVAILABLE",
        })

    if capability not in available:
        return DispatchOutcome({
            "operation": operation, "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "ECC_OPERATION_NOT_AVAILABLE",
        })

    if registry.spec(capability).get("ecc_require_explicit_source") and "source" not in (arguments or {}):
        return DispatchOutcome({
            "operation": operation, "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "ECC_SOURCE_REQUIRED",
            "detail": "This operation requires an explicit physical source: workspace or eyle.",
        })

    normalized, error = registry.validate(capability, arguments or {})
    if error is not None:
        out = registry.public_result(capability, error)
        return DispatchOutcome({"operation": operation, **out})

    if registry.requires_confirmation(capability):
        prepared = registry.prepare_confirmation(capability, normalized, _runtime_context(session, config, provider_context))
        if prepared.get("ok") is not True:
            err = prepared.get("error") if isinstance(prepared.get("error"), dict) else {}
            return DispatchOutcome({"operation": operation, **registry.public_result(capability, err)})
        confirmation_id = f"ecc-cap-{session.turn:04d}"
        session.pending_operation = {
            "confirmation_id": confirmation_id,
            "provider": prepared.get("provider"),
            "capability": capability,
            "operation": operation,
            "arguments": copy.deepcopy(normalized),
            "state": copy.deepcopy(prepared.get("state") or {}),
        }
        pending = {
            "pending_schema_version": pending_schema_version,
            "continuation_kind": "capability_confirmation",
            "question": str(prepared.get("question") or "").strip(),
            "session": session.to_dict(),
            "execution_state": copy.deepcopy(execution_state),
            "capability": capability,
            "provider": str(prepared.get("provider") or ""),
            "confirmation_id": confirmation_id,
        }
        validate_pending(pending)
        return DispatchOutcome({
            "operation": operation, "status": "confirmation_required", "ok": True,
            "executed": False, "changed": False,
        }, pending=pending)

    signature = registry.observation_signature(capability, normalized)
    if signature:
        previous = lookup_observation(session, signature)
        replay_reason = "EXACT_OBSERVATION_ALREADY_COVERED"
        if previous is None:
            previous = registry.find_covering(
                capability, normalized, (session.observation_ledger or {}).get("entries") or {}, session.reality_epoch,
            )
            replay_reason = "OBSERVATION_SCOPE_ALREADY_COVERED"
        if previous is None:
            previous = registry.find_resource_failure(
                capability, normalized, (session.observation_ledger or {}).get("entries") or {}, session.reality_epoch,
            )
            replay_reason = "RESOURCE_FAILURE_ALREADY_OBSERVED"
        if previous is not None:
            runtime_ctx = _runtime_context(session, config, provider_context)
            fresh, freshness_reason = registry.entry_freshness(
                capability, previous, material_items(session.observation_ledger), runtime_ctx,
            )
            if fresh:
                compact = _compact_cached(session, previous, operation)
                record_replay(session, previous, compact, reason=replay_reason, public_result=compact)
                try:
                    execution = current_execution()
                    telemetry.record(
                        "observation", "replay_avoided", "success", 0.0,
                        execution_id=(execution.execution_id if execution is not None else None),
                        job_id=(execution.source_job_id if execution is not None else None),
                        metadata={
                            "operation": operation,
                            "capability": capability,
                            "reason": replay_reason,
                            "open_frontiers": len(compact.get("frontiers") or []),
                            "evidence_ids": len(compact.get("evidence_ids") or []),
                        },
                    )
                except Exception:
                    pass
                return DispatchOutcome(compact, physical_progress=False)
            previous = None

    execution = current_execution()
    terminal = execution.terminal_capability(capability) if execution is not None else None
    if terminal is not None:
        return DispatchOutcome({
            "operation": operation, "status": "failed", "ok": False, "executed": False,
            "changed": False, "error_code": "CAPABILITY_TERMINALLY_UNAVAILABLE", "detail": terminal,
        })

    material_count_before = len(material_items(session.observation_ledger))
    runtime_ctx = _runtime_context(session, config, provider_context)
    pre_freshness_token = registry.freshness_token(capability, normalized, runtime_ctx)
    result = registry.execute(capability, normalized, runtime_ctx)
    duplicate_result = equivalent_result_seen(session, signature, result)
    if (
        execution is not None and result.get("ok") is False and result.get("retryable") is False
        and result.get("failure_scope") not in {"request", "resource"}
    ):
        execution.mark_terminal_capability(
            capability,
            error_code=str(result.get("error_code") or "CAPABILITY_UNAVAILABLE"),
            detail=result.get("detail"),
        )
    post_freshness_token = registry.freshness_token(capability, normalized, runtime_ctx)
    source_changed_during_operation = bool(
        pre_freshness_token is not None and post_freshness_token is not None
        and pre_freshness_token != post_freshness_token
    )
    freshness_token = None if source_changed_during_operation else post_freshness_token
    model = project_result(
        session, capability, result, registry, config, freshness_token=freshness_token,
        freshness_arguments=registry.freshness_arguments(capability, normalized),
    )
    if source_changed_during_operation:
        model["current_world_valid"] = False
        model["freshness_reason"] = "source_changed_during_operation"
    record_observation(
        session, signature, capability, normalized, result, model,
        public_arguments=registry.public_arguments(capability, normalized),
        public_result=registry.public_result(capability, result),
        freshness_token=freshness_token,
    )
    _advance_epoch(session, result)
    new_material = len(material_items(session.observation_ledger)) > material_count_before
    physical_progress = bool(new_material or (result.get("executed") is True and not duplicate_result))
    return DispatchOutcome(model, physical_progress=physical_progress)


def cancel_pending(session: Any, pending: Dict[str, Any], *, config: Dict[str, Any], provider_context: Dict[str, Any], registry: Any) -> Dict[str, Any]:
    state = session.pending_operation if isinstance(session.pending_operation, dict) else {}
    capability = str(pending.get("capability") or "")
    if (
        not state or capability != str(state.get("capability") or "")
        or str(pending.get("provider") or "") != str(state.get("provider") or "")
        or str(pending.get("confirmation_id") or "") != str(state.get("confirmation_id") or "")
    ):
        return {"ok": False, "error_code": "CAPABILITY_PENDING_MISMATCH"}
    cleaned = registry.cancel_confirmation(
        capability, state.get("state") or {}, _runtime_context(session, config, provider_context),
    )
    session.pending_operation = {}
    return cleaned if isinstance(cleaned, dict) else {"ok": True}


def confirm_pending(session: Any, pending: Dict[str, Any], *, config: Dict[str, Any], provider_context: Dict[str, Any], registry: Any) -> Dict[str, Any]:
    state = session.pending_operation if isinstance(session.pending_operation, dict) else {}
    capability = str(pending.get("capability") or "")
    if (
        not state or capability != str(state.get("capability") or "")
        or str(pending.get("provider") or "") != str(state.get("provider") or "")
        or str(pending.get("confirmation_id") or "") != str(state.get("confirmation_id") or "")
    ):
        return {"operation": state.get("operation"), "status": "failed", "ok": False, "executed": False, "changed": False, "error_code": "CAPABILITY_PENDING_MISMATCH"}
    result = registry.confirm(capability, state.get("state") or {}, _runtime_context(session, config, provider_context))
    model = project_result(session, capability, result, registry, config)
    record_observation(
        session, None, capability, state.get("arguments") or {}, result, model,
        public_arguments=registry.public_arguments(capability, state.get("arguments") or {}),
        public_result=registry.public_result(capability, result),
    )
    _advance_epoch(session, result)
    session.pending_operation = {}
    return model


def _compact_coverage_for_ecc(value: Any) -> Dict[str, Any]:
    """Bound physical Coverage to navigation facts useful to Main.

    Provider-owned full Coverage remains in Observation. ECC receives only the
    declared scope identity, objective examined counts and boundary counts.
    """
    if not isinstance(value, dict):
        return {}
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    compact_scope = {}
    for key in ("kind", "source", "path", "query", "symbol", "requested_lines", "depth", "filter", "direction"):
        item = scope.get(key)
        if item not in (None, "", [], {}):
            compact_scope[key] = copy.deepcopy(item)
    examined = value.get("examined") if isinstance(value.get("examined"), dict) else {}
    compact_examined = {}
    for key, item in examined.items():
        if isinstance(item, (str, int, float, bool)) and len(str(item)) <= 120:
            compact_examined[str(key)] = item
        elif isinstance(item, list) and len(item) <= 4 and all(isinstance(v, (str, int, float, bool)) for v in item):
            compact_examined[str(key)] = copy.deepcopy(item)
    facts = value.get("facts") if isinstance(value.get("facts"), dict) else {}
    compact_facts = {
        str(key): item for key, item in facts.items()
        if isinstance(item, (str, int, float, bool)) and len(str(item)) <= 120
    }
    boundaries = value.get("boundaries") if isinstance(value.get("boundaries"), list) else []
    boundary_counts = []
    for item in boundaries[:8]:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k) for k in ("kind", "count") if item.get(k) not in (None, "")}
        if row:
            boundary_counts.append(row)
    out = {"complete": bool(value.get("complete"))}
    if compact_scope:
        out["scope"] = compact_scope
    if compact_examined:
        out["examined"] = compact_examined
    if compact_facts:
        out["facts"] = compact_facts
    if boundary_counts:
        out["boundaries"] = boundary_counts
    return out


def exploration_map(session: Any, registry: Any | None = None) -> List[Dict[str, Any]]:
    """Small Runtime-built map of physical exploration; never source replay.

    The ledger keeps complete Coverage/Frontier state. Main only needs a bounded
    navigation index showing what physical scopes were touched and whether they
    were complete. Continuation handles are intentionally not exposed here.
    """
    out: List[Dict[str, Any]] = []
    for item in navigation_view(session):
        if not isinstance(item, dict):
            continue
        operation = public_name(str(item.get("capability") or ""), registry)
        row: Dict[str, Any] = {
            "turn": item.get("turn"),
            "operation": operation,
            "materials": len(item.get("grounding_ids") or []),
        }
        coverage = _compact_coverage_for_ecc(item.get("coverage"))
        if coverage:
            row["coverage"] = coverage
        frontier_count = sum(
            1 for frontier in (item.get("frontiers") or [])
            if isinstance(frontier, dict) and frontier.get("consumed") is not True
        )
        if frontier_count:
            row["unmaterialized_boundaries"] = frontier_count
        out.append({k: v for k, v in row.items() if v not in (None, "", [], {})})
    return out


def effects_view(session: Any) -> List[Dict[str, Any]]:
    return physical_effect_index_view(session.observation_ledger)
