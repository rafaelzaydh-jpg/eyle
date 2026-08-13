"""Runtime-owned safe execution history and prompt-cost accounting.

This module projects factual diagnostics only. It never exposes chain-of-thought,
raw prompts, raw model responses, source bodies, patch bodies, secrets or memory
bodies, and it carries no task-semantic authority.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

_COMPONENT_GROUPS = {
    "fixed_contract": {"available_capabilities"},
    "request": {"request"},
    "fresh_observation": {"latest_capability_results"},
    "retained_context": {"prior_conversation"},
    "observation_state": {"runtime_observations", "current_material"},
    "epistemic_state": {"investigation"},
    "intentional_state": {"task_state"},
    "runtime_feedback": {"runtime_feedback"},
}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _round_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _component_metrics(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"characters": 0, "estimated_tokens": 0}
    result: Dict[str, Any] = {
        "characters": int(_number(value.get("characters"))),
        "estimated_tokens": int(_number(value.get("estimated_tokens"))),
    }
    if isinstance(value.get("items"), (int, float)):
        result["items"] = int(value["items"])
    return result


def _aggregate_components(snapshots: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    totals: Dict[str, Dict[str, Any]] = {}
    for snap in snapshots:
        components = snap.get("components_after") if isinstance(snap, dict) else None
        if not isinstance(components, dict):
            continue
        for name, raw in components.items():
            metric = _component_metrics(raw)
            bucket = totals.setdefault(str(name), {
                "characters": 0,
                "estimated_tokens": 0,
                "appearances": 0,
                "items_total": 0,
            })
            bucket["characters"] += metric["characters"]
            bucket["estimated_tokens"] += metric["estimated_tokens"]
            bucket["appearances"] += 1
            bucket["items_total"] += int(metric.get("items", 0) or 0)
    for bucket in totals.values():
        appearances = max(1, int(bucket.get("appearances", 0) or 0))
        bucket["average_estimated_tokens_per_appearance"] = round(
            float(bucket.get("estimated_tokens", 0) or 0) / appearances, 2
        )
    return dict(sorted(totals.items(), key=lambda item: (-int(item[1].get("estimated_tokens", 0)), item[0])))


def _category_totals(
    snapshots: Iterable[Dict[str, Any]], component_totals: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    categories: Dict[str, Dict[str, Any]] = {
        "system": {"estimated_tokens": 0, "characters": 0},
    }
    assigned = set()
    for group, names in _COMPONENT_GROUPS.items():
        token_sum = 0
        char_sum = 0
        for name in names:
            assigned.add(name)
            metric = component_totals.get(name) or {}
            token_sum += int(metric.get("estimated_tokens", 0) or 0)
            char_sum += int(metric.get("characters", 0) or 0)
        categories[group] = {"estimated_tokens": token_sum, "characters": char_sum}

    system_tokens = 0
    system_chars = 0
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        system_tokens += int(_number(snap.get("system_prompt_estimated_tokens")))
        system_chars += int(_number(snap.get("system_prompt_characters")))
    categories["system"] = {"estimated_tokens": system_tokens, "characters": system_chars}

    other_tokens = 0
    other_chars = 0
    for name, metric in component_totals.items():
        if name in assigned:
            continue
        other_tokens += int(metric.get("estimated_tokens", 0) or 0)
        other_chars += int(metric.get("characters", 0) or 0)
    categories["other"] = {"estimated_tokens": other_tokens, "characters": other_chars}
    return categories


def _logical_calls(details: Dict[str, Any], limit: int = 0) -> List[Dict[str, Any]]:
    calls = [dict(item) for item in (details.get("llm_calls") or []) if isinstance(item, dict)]
    return calls[-int(limit):] if limit and len(calls) > int(limit) else calls


def _prompt_views_from_calls(calls: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snapshots: List[Dict[str, Any]] = []
    for call in calls:
        prompt = dict(call.get("prompt") or {})
        prompt.setdefault("logical_call_id", call.get("logical_call_id"))
        prompt.setdefault("mode", call.get("mode"))
        prompt.setdefault("turn", call.get("turn"))
        snapshots.append(prompt)
    return snapshots


def build_prompt_cost_accounting(details: Dict[str, Any], *, limit: int = 20) -> Dict[str, Any]:
    """Return bounded, content-free accounting from the canonical LLMCallLedger."""
    details = details if isinstance(details, dict) else {}
    logical_calls = _logical_calls(details, limit)
    prompt_views = _prompt_views_from_calls(logical_calls)

    component_totals = _aggregate_components(prompt_views)
    categories = _category_totals(prompt_views, component_totals)
    local_user_estimated = sum(int(_number(item.get("estimated_tokens"))) for item in prompt_views)
    local_system_estimated = sum(int(_number(item.get("system_prompt_estimated_tokens"))) for item in prompt_views)
    local_total_estimated = local_user_estimated + local_system_estimated

    provider_prompt_total = 0
    provider_cached_total = 0
    provider_calls_with_usage = 0
    physical_attempts = 0
    retry_attempts = 0
    calls: List[Dict[str, Any]] = []
    ordinal = 0
    for logical in logical_calls:
        snap = dict(logical.get("prompt") or {})
        attempts = [dict(item) for item in (logical.get("attempts") or []) if isinstance(item, dict)]
        if not attempts:
            attempts = [{}]
        for attempt in attempts:
            ordinal += 1
            sent = bool(attempt)
            request_status = str(attempt.get("request_status") or "sent") if sent else "preflight_blocked"
            if sent:
                physical_attempts += 1
                if int(attempt.get("physical_attempt") or 1) > 1:
                    retry_attempts += 1
            actual = attempt.get("prompt_tokens")
            cached = attempt.get("cached_prompt_tokens")
            if isinstance(actual, (int, float)):
                provider_prompt_total += int(actual); provider_calls_with_usage += 1
            if isinstance(cached, (int, float)):
                provider_cached_total += int(cached)
            local_user = int(_number(snap.get("estimated_tokens")))
            local_system = int(_number(snap.get("system_prompt_estimated_tokens")))
            local_total = local_user + local_system
            item: Dict[str, Any] = {
                "call": ordinal,
                "logical_call_id": logical.get("logical_call_id"),
                "physical_attempt": attempt.get("physical_attempt") if sent else None,
                "request_status": request_status,
                "turn": logical.get("turn"), "mode": logical.get("mode"),
                "local_user_estimated_tokens": local_user,
                "local_system_estimated_tokens": local_system,
                "local_total_estimated_tokens": local_total,
                "crop_applied": bool(snap.get("crop_applied", False)),
            }
            if isinstance(actual, (int, float)):
                item["provider_prompt_tokens"] = int(actual)
                ratio = _round_ratio(float(actual), float(local_total))
                if ratio is not None: item["provider_to_local_estimate_ratio"] = ratio
            if isinstance(cached, (int, float)): item["cached_prompt_tokens"] = int(cached)
            components = snap.get("components_after")
            if isinstance(components, dict):
                item["components"] = {str(name): _component_metrics(metric) for name, metric in components.items()}
            for key in (
                "output_tokens_desired", "prompt_budget_tokens",
            ):
                if snap.get(key) is not None: item[key] = snap.get(key)
            calls.append({key:value for key,value in item.items() if value is not None})

    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    provider_reported_total = usage.get("prompt_tokens_actual")
    if not isinstance(provider_reported_total, (int, float)):
        provider_reported_total = provider_prompt_total if provider_calls_with_usage else None

    fixed_repeat_tokens = int(categories["system"]["estimated_tokens"]) + int(categories["fixed_contract"]["estimated_tokens"])

    observation_count=int(_number(details.get("observation_ledger_size")))
    grounding_count=int(_number(details.get("grounding_count_total")))
    replays=int(_number(details.get("observation_replays")))
    capability_requests=len([item for item in (details.get("capability_history") or []) if isinstance(item,dict)])
    grounding_usage=details.get("grounding_usage") if isinstance(details.get("grounding_usage"),dict) else {}
    grounding_count = int(_number(grounding_usage.get("total_grounding_count"))) or grounding_count
    diagnostics={
        "observation_count":observation_count, "grounding_count":grounding_count,
        "observation_replays":replays, "capability_requests_observed":capability_requests,
    }
    grounding_per_observation=_round_ratio(float(grounding_count),float(observation_count))
    if grounding_per_observation is not None: diagnostics["grounding_per_observation"]=grounding_per_observation
    replay_rate=_round_ratio(float(replays),float(capability_requests))
    if replay_rate is not None: diagnostics["replay_capability_rate"]=replay_rate
    for key in (
        "investigation_grounding_count", "task_grounding_count",
        "completion_grounding_count", "unreferenced_grounding_count",
        "capability_actions_with_grounding",
    ):
        if grounding_usage.get(key) is not None: diagnostics[key]=grounding_usage.get(key)


    summary: Dict[str, Any] = {
        "calls_observed": len(logical_calls), "physical_attempts_observed": physical_attempts,
        "retry_attempts_observed": retry_attempts,
        "local_user_estimated_tokens":local_user_estimated,"local_system_estimated_tokens":local_system_estimated,
        "local_total_estimated_tokens":local_total_estimated,
        "provider_prompt_tokens":int(provider_reported_total) if isinstance(provider_reported_total,(int,float)) else None,
        "provider_cached_prompt_tokens":provider_cached_total,"fixed_repeat_tax_estimated_tokens":fixed_repeat_tokens,
        "fresh_observation_estimated_tokens":int(categories["fresh_observation"]["estimated_tokens"]),
        "retained_context_estimated_tokens":int(categories["retained_context"]["estimated_tokens"]),
        "observation_state_estimated_tokens":int(categories["observation_state"]["estimated_tokens"]),
        "epistemic_state_estimated_tokens":int(categories["epistemic_state"]["estimated_tokens"]),
        "intentional_state_estimated_tokens":int(categories["intentional_state"]["estimated_tokens"]),
    }
    ratio=_round_ratio(float(summary["provider_prompt_tokens"] or 0),float(local_total_estimated))
    if ratio is not None and summary["provider_prompt_tokens"] is not None: summary["provider_to_local_estimate_ratio"]=ratio
    fixed_share=_round_ratio(float(fixed_repeat_tokens),float(local_total_estimated))
    if fixed_share is not None: summary["fixed_repeat_tax_share"]=fixed_share
    summary={k:v for k,v in summary.items() if v is not None}
    return {"summary":summary,"categories":categories,"component_totals":component_totals,"diagnostics":diagnostics,"calls":calls,"interpretation":"observational_only; token cost is measured, usefulness remains semantic"}


def _bounded_list(value: Any, limit: int) -> List[Dict[str, Any]]:
    items = [dict(item) for item in (value or []) if isinstance(item, dict)]
    return items[-max(1, int(limit)):]


def _safe_llm_calls(details: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    ordinal = 0
    logical_calls = [dict(item) for item in (details.get("llm_calls") or []) if isinstance(item, dict)]
    for logical in logical_calls:
        prompt = dict(logical.get("prompt") or {})
        attempts = [dict(item) for item in (logical.get("attempts") or []) if isinstance(item, dict)]
        if not attempts:
            attempts = [{}]
        for attempt in attempts:
            ordinal += 1
            prompt_tokens = attempt.get("prompt_tokens")
            cached_tokens = attempt.get("cached_prompt_tokens")
            uncached_tokens = None
            if isinstance(prompt_tokens, (int, float)):
                cached = cached_tokens if isinstance(cached_tokens, (int, float)) else 0
                uncached_tokens = max(0, int(prompt_tokens) - int(cached))
            item = {
                "call": ordinal, "logical_call_id": logical.get("logical_call_id"),
                "physical_attempt": attempt.get("physical_attempt"), "turn": logical.get("turn"),
                "mode": logical.get("mode"),
                "request_status": (str(attempt.get("request_status")) if attempt else "preflight_blocked"),
                "prompt_estimated_tokens": prompt.get("estimated_tokens"), "prompt_characters": prompt.get("characters"),
                "prompt_budget_tokens": prompt.get("prompt_budget_tokens"),
                "output_tokens_reserved": prompt.get("output_tokens_reserved"),
                "capability_count_available": prompt.get("capability_count"), "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_tokens, "uncached_prompt_tokens": uncached_tokens,
                "completion_tokens": attempt.get("completion_tokens"), "reasoning_tokens": attempt.get("reasoning_tokens"),
                "finish_reason": attempt.get("finish_reason"), "provider_model": attempt.get("provider_model"),
                "latency_ms": attempt.get("orchestration_latency_ms", attempt.get("latency_ms")),
                "streaming": attempt.get("streaming"), "structured_profile": attempt.get("structured_profile"),
                "structured_mode": attempt.get("structured_mode"), "structured_parse_status": attempt.get("structured_parse_status"),
                "structured_parse_error": attempt.get("structured_parse_error"), "structured_parse_detail": attempt.get("structured_parse_detail"),
                "structured_top_level_keys": attempt.get("structured_top_level_keys"), "structured_missing_keys": attempt.get("structured_missing_keys"),
                "error_code": attempt.get("error_code"),
                "fresh_call": prompt.get("fresh_call"),
                "semantic_packet_fields": prompt.get("semantic_packet_fields"),
                "prompt_components": prompt.get("components_after") if isinstance(prompt.get("components_after"), dict) else None,
            }
            calls.append({key:value for key,value in item.items() if value is not None})
    return calls[-max(1, int(limit)):]

def build_execution_trace(
    details: Dict[str, Any], *, job_id: Optional[int] = None, status: Optional[str] = None,
    created_at: Any = None, started_at: Any = None, completed_at: Any = None,
    duration_seconds: Any = None, limit: int = 100,
) -> Dict[str, Any]:
    details = details if isinstance(details, dict) else {}
    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    logical_calls = _bounded_list(details.get("llm_calls"), limit)
    snapshots = [dict(item.get("prompt") or {}) | {"turn": item.get("turn"), "mode": item.get("mode")} for item in logical_calls]

    context = [{
        key: item.get(key)
        for key in (
            "turn", "mode", "characters", "estimated_tokens", "capability_count",
            "prompt_budget_tokens",
            "output_tokens_reserved", "system_prompt_characters",
            "system_prompt_estimated_tokens", "pre_crop_characters", "pre_crop_estimated_tokens",
            "crop_applied", "components_before", "components_after",
            "fresh_call", "semantic_packet_fields",
        )
        if item.get(key) is not None
    } for item in snapshots]

    decisions = [{
        "event": index + 1,
        "turn": item.get("turn"),
        "decision": item.get("decision"),
        "outcome": item.get("outcome"),
        "reason": item.get("reason"),
        "capabilities": list(item.get("capabilities") or [])[:8],
    } for index, item in enumerate(_bounded_list(details.get("decision_history"), limit))]

    capabilities = []
    for index, item in enumerate(_bounded_list(details.get("capability_history"), limit)):
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        capabilities.append({
            "event": index + 1,
            "capability": item.get("capability") or result.get("capability") or "unknown_capability",
            "turn": item.get("turn"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
            "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            "result": result,
        })

    tokens = {
        "prompt_total": usage.get("prompt_tokens_actual"),
        "prompt_cached": usage.get("prompt_tokens_cached"),
        "prompt_new": usage.get("prompt_tokens_uncached"),
        "prompt_effective": usage.get("prompt_tokens_effective"),
        "completion": usage.get("completion_tokens_actual", usage.get("generated_tokens")),
        "reasoning": usage.get("reasoning_tokens_actual"),
        "effective_total": usage.get("total_tokens_effective"),
        "physical_estimated_total": usage.get("total_tokens_physical_estimated"),
    }
    tokens = {key: value for key, value in tokens.items() if value is not None}

    trace = {
        "summary": {
            "job_id": job_id,
            "status": status or details.get("status"),
            "created_at": created_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": duration_seconds,
            "turns": details.get("turns"),
            "capability_calls": details.get("capability_calls"),
            "failure_code": details.get("failure_code"),
            "task_totals": details.get("task_totals") if isinstance(details.get("task_totals"), dict) else {},
        },
        "tokens": tokens,
        "prompt_accounting": build_prompt_cost_accounting(details, limit=limit),
        "context": context,
        "llm_calls": _safe_llm_calls(details, limit),
        "decisions": decisions,
        "capabilities": capabilities,
        "privacy": {
            "chain_of_thought_exposed": False,
            "raw_prompts_exposed": False,
            "raw_model_responses_exposed": False,
            "source_contents_exposed": False,
            "patch_bodies_exposed": False,
            "secrets_exposed": False,
            "memory_bodies_exposed": False,
        },
    }
    trace["summary"] = {key: value for key, value in trace["summary"].items() if value is not None}
    return trace


def build_public_job_history(registro):
    if not isinstance(registro, dict):
        return None
    resultado = registro.get("resultado")
    details = resultado.get("details") if isinstance(resultado, dict) else None
    details = details if isinstance(details, dict) else {}
    trace = build_execution_trace(
        details,
        job_id=registro.get("id"), status=registro.get("status"),
        created_at=registro.get("criado_em"), started_at=registro.get("iniciado_em"),
        completed_at=registro.get("concluido_em"),
        duration_seconds=(registro.get("progresso") or {}).get("elapsed_seconds") if isinstance(registro.get("progresso"), dict) else None,
        limit=200,
    )
    summary = dict(trace.get("summary") or {})
    token_summary = dict(trace.get("tokens") or {})
    llm_calls = list(trace.get("llm_calls") or [])
    logical_ids = {str(item.get("logical_call_id")) for item in llm_calls if item.get("logical_call_id") is not None}
    sent_requests = sum(1 for item in llm_calls if item.get("request_status") != "preflight_blocked")
    return {
        "job_id": summary.get("job_id"),
        "status": summary.get("status"),
        "created_at": summary.get("created_at"),
        "started_at": summary.get("started_at"),
        "completed_at": summary.get("completed_at"),
        "duration_seconds": summary.get("duration_seconds"),
        "agent": {
            "turns": summary.get("turns"),
            "capability_calls": summary.get("capability_calls"),
            "reality_epoch": details.get("reality_epoch"),
            "grounding_count_total": details.get("grounding_count_total"),
            "observation_replays": details.get("observation_replays"),
            "observation_ledger_size": details.get("observation_ledger_size"),
            "failure_code": summary.get("failure_code") or (resultado.get("error_code") if isinstance(resultado, dict) else None),
            "task_totals": summary.get("task_totals") if isinstance(summary.get("task_totals"), dict) else {},
        },
        "tokens": token_summary,
        "prompt_accounting": trace.get("prompt_accounting") or {},
        "llm": {
            "logical_attempts": len(logical_ids),
            "requests_sent": sent_requests,
            "preflight_blocked": sum(1 for item in llm_calls if item.get("request_status") == "preflight_blocked"),
            "failed_requests": sum(
                1 for item in llm_calls
                if item.get("request_status") not in {"sent", "started", "preflight_blocked"}
            ),
        },
        "llm_calls": llm_calls,
        "decisions": list(trace.get("decisions") or []),
        "capabilities": list(trace.get("capabilities") or []),
        "privacy": dict(trace.get("privacy") or {}),
    }
