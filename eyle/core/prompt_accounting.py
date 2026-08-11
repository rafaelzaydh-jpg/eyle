"""Safe prompt-cost accounting for observable Eyle traces.

This module aggregates sizes and provider token counters only. It never exposes
raw prompts, source bodies, model responses, chain-of-thought or semantic
judgments about whether a component was useful.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


_COMPONENT_GROUPS = {
    "fixed_contract": {"capability_index", "active_tools"},
    "request": {"request"},
    "fresh_observation": {"latest_tool_results"},
    "retained_context": {"conversation_background"},
    "source_state": {"observation_map", "source_record_index"},
    "evidence_state": {"investigation", "evidence_index"},
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
                "request_status": "sent" if sent else "preflight_blocked",
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
            for key in ("selected_evidence_count", "evidence_excerpt_chars_per_item", "answer_anchor_count", "request_anchor_count", "investigation_target_count", "prompt_budget_tokens"):
                if snap.get(key) is not None: item[key] = snap.get(key)
            calls.append({key:value for key,value in item.items() if value is not None})

    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    provider_reported_total = usage.get("prompt_tokens_actual")
    if not isinstance(provider_reported_total, (int, float)):
        provider_reported_total = provider_prompt_total if provider_calls_with_usage else None

    fixed_repeat_tokens = int(categories["system"]["estimated_tokens"]) + int(categories["fixed_contract"]["estimated_tokens"])
    claim_snaps = [item for item in prompt_views if "claim" in str(item.get("mode") or "") or "verification" in str(item.get("mode") or "")]
    claim_packet: Dict[str, Any] = {"calls": len(claim_snaps)}
    selected_counts=[int(item.get("selected_evidence_count")) for item in claim_snaps if isinstance(item.get("selected_evidence_count"),(int,float))]
    excerpt_widths=[int(item.get("evidence_excerpt_chars_per_item")) for item in claim_snaps if isinstance(item.get("evidence_excerpt_chars_per_item"),(int,float))]
    anchor_counts=[int(item.get("answer_anchor_count")) for item in claim_snaps if isinstance(item.get("answer_anchor_count"),(int,float))]
    request_anchor_counts=[int(item.get("request_anchor_count")) for item in claim_snaps if isinstance(item.get("request_anchor_count"),(int,float))]
    target_counts=[int(item.get("investigation_target_count")) for item in claim_snaps if isinstance(item.get("investigation_target_count"),(int,float))]
    if selected_counts: claim_packet.update({"selected_evidence_last":selected_counts[-1],"selected_evidence_max":max(selected_counts)})
    if excerpt_widths: claim_packet.update({"evidence_excerpt_chars_last":excerpt_widths[-1],"evidence_excerpt_chars_min":min(excerpt_widths)})
    if anchor_counts: claim_packet.update({"answer_anchors_last":anchor_counts[-1],"answer_anchors_max":max(anchor_counts)})
    if request_anchor_counts: claim_packet.update({"request_anchors_last":request_anchor_counts[-1],"request_anchors_max":max(request_anchor_counts)})
    if target_counts: claim_packet.update({"investigation_targets_last":target_counts[-1],"investigation_targets_max":max(target_counts)})

    observation_count=int(_number(details.get("observation_ledger_size")))
    source_record_count=int(_number(details.get("source_record_count_total")))
    evidence_count=int(_number(details.get("evidence_count_total")))
    replays=int(_number(details.get("observation_replays")))
    tool_requests=len([item for item in (details.get("tool_history") or []) if isinstance(item,dict)])
    evidence_usage=details.get("evidence_usage") if isinstance(details.get("evidence_usage"),dict) else {}
    # Use canonical task-wide values when available; job-level detail remains a fallback.
    source_record_count = int(_number(evidence_usage.get("total_source_record_count"))) or source_record_count
    evidence_count = int(_number(evidence_usage.get("total_evidence_count"))) or evidence_count
    promoted_source_count = int(_number(evidence_usage.get("promoted_source_record_count")))
    diagnostics={
        "observation_count":observation_count, "source_record_count":source_record_count,
        "evidence_count":evidence_count, "observation_replays":replays,
        "tool_requests_observed":tool_requests,
    }
    source_per_observation=_round_ratio(float(source_record_count),float(observation_count))
    if source_per_observation is not None: diagnostics["source_records_per_observation"]=source_per_observation
    admission=_round_ratio(float(promoted_source_count),float(source_record_count))
    if admission is not None: diagnostics["evidence_admission_ratio"]=admission
    replay_rate=_round_ratio(float(replays),float(tool_requests))
    if replay_rate is not None: diagnostics["replay_request_rate"]=replay_rate
    for key in (
        "promoted_source_record_count","unpromoted_source_record_count",
        "target_attached_evidence_count","claim_cited_evidence_count",
        "structurally_unreferenced_evidence_count","tool_actions_with_source_records",
        "tool_actions_with_promoted_sources","tool_actions_with_direct_evidence_refs",
    ):
        if evidence_usage.get(key) is not None: diagnostics[key]=evidence_usage.get(key)


    summary: Dict[str, Any] = {
        "calls_observed": len(logical_calls), "physical_attempts_observed": physical_attempts,
        "retry_attempts_observed": retry_attempts,
        "local_user_estimated_tokens":local_user_estimated,"local_system_estimated_tokens":local_system_estimated,
        "local_total_estimated_tokens":local_total_estimated,
        "provider_prompt_tokens":int(provider_reported_total) if isinstance(provider_reported_total,(int,float)) else None,
        "provider_cached_prompt_tokens":provider_cached_total,"fixed_repeat_tax_estimated_tokens":fixed_repeat_tokens,
        "fresh_observation_estimated_tokens":int(categories["fresh_observation"]["estimated_tokens"]),
        "retained_context_estimated_tokens":int(categories["retained_context"]["estimated_tokens"]),
        "source_state_estimated_tokens":int(categories["source_state"]["estimated_tokens"]),
        "evidence_state_estimated_tokens":int(categories["evidence_state"]["estimated_tokens"]),
    }
    ratio=_round_ratio(float(summary["provider_prompt_tokens"] or 0),float(local_total_estimated))
    if ratio is not None and summary["provider_prompt_tokens"] is not None: summary["provider_to_local_estimate_ratio"]=ratio
    fixed_share=_round_ratio(float(fixed_repeat_tokens),float(local_total_estimated))
    if fixed_share is not None: summary["fixed_repeat_tax_share"]=fixed_share
    summary={k:v for k,v in summary.items() if v is not None}
    return {"summary":summary,"categories":categories,"component_totals":component_totals,"claim_packet":claim_packet,"diagnostics":diagnostics,"calls":calls,"interpretation":"observational_only; token cost is measured, usefulness remains semantic"}
