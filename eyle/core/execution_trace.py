"""Safe observable execution traces for the canonical Rev5.7 runtime.

Trace is factual telemetry only: no semantic scheduler, no earned-authority
history, no chain-of-thought, raw prompts, raw model responses or source bodies.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .prompt_accounting import build_prompt_cost_accounting

_TRACE_LIST_KEYS = ("context", "llm_calls", "decisions", "tools")
_TRACE_SECTIONS = {"all", "summary", "context", "llm", "tools", "decisions", "validation"}


def _bounded_list(value: Any, limit: int) -> List[Dict[str, Any]]:
    items = [dict(item) for item in (value or []) if isinstance(item, dict)]
    return items[-max(1, int(limit)):]


def _filter_turn(items: Iterable[Dict[str, Any]], turn: Optional[int]) -> List[Dict[str, Any]]:
    values = [dict(item) for item in items if isinstance(item, dict)]
    if turn is None:
        return values
    return [item for item in values if item.get("turn") == int(turn)]


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
                "mode": logical.get("mode"), "request_status": "sent" if attempt else "preflight_blocked",
                "prompt_estimated_tokens": prompt.get("estimated_tokens"), "prompt_characters": prompt.get("characters"),
                "prompt_budget_tokens": prompt.get("prompt_budget_tokens"), "output_tokens_reserved": prompt.get("output_tokens_reserved"),
                "tool_count_available": prompt.get("tool_count"), "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_tokens, "uncached_prompt_tokens": uncached_tokens,
                "completion_tokens": attempt.get("completion_tokens"), "reasoning_tokens": attempt.get("reasoning_tokens"),
                "finish_reason": attempt.get("finish_reason"), "provider_model": attempt.get("provider_model"),
                "latency_ms": attempt.get("orchestration_latency_ms", attempt.get("latency_ms")),
                "streaming": attempt.get("streaming"), "structured_profile": attempt.get("structured_profile"),
                "structured_mode": attempt.get("structured_mode"), "structured_parse_status": attempt.get("structured_parse_status"),
                "structured_parse_error": attempt.get("structured_parse_error"), "structured_parse_detail": attempt.get("structured_parse_detail"),
                "structured_top_level_keys": attempt.get("structured_top_level_keys"), "structured_missing_keys": attempt.get("structured_missing_keys"),
                "selected_evidence_count": prompt.get("selected_evidence_count"),
                "evidence_excerpt_chars_per_item": prompt.get("evidence_excerpt_chars_per_item"),
                "answer_anchor_count": prompt.get("answer_anchor_count"), "investigation_target_count": prompt.get("investigation_target_count"),
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
            "turn", "mode", "characters", "estimated_tokens", "tool_count", "active_tool_count",
            "prompt_budget_tokens", "output_tokens_reserved", "system_prompt_characters",
            "system_prompt_estimated_tokens", "pre_crop_characters", "pre_crop_estimated_tokens",
            "crop_applied", "components_before", "components_after",
            "selected_evidence_count", "evidence_excerpt_chars_per_item",
            "answer_anchor_count", "investigation_target_count",
        )
        if item.get(key) is not None
    } for item in snapshots]

    decisions = [{
        "event": index + 1,
        "turn": item.get("turn"),
        "decision": item.get("decision"),
        "outcome": item.get("outcome"),
        "reason": item.get("reason"),
        "tools": list(item.get("tools") or [])[:8],
    } for index, item in enumerate(_bounded_list(details.get("decision_history"), limit))]

    tools = []
    for index, item in enumerate(_bounded_list(details.get("tool_history"), limit)):
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        tools.append({
            "event": index + 1,
            "tool": item.get("tool") or result.get("tool") or "unknown_tool",
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
        "completion_remaining": usage.get("completion_tokens_remaining"),
        "reasoning": usage.get("reasoning_tokens_actual"),
        "effective_total": usage.get("total_tokens_effective"),
        "physical_estimated_total": usage.get("total_tokens_physical_estimated"),
        "physical_remaining": usage.get("physical_tokens_remaining"),
        "physical_limit": usage.get("physical_tokens_limit"),
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
            "tool_calls": details.get("tool_calls"),
            "tool_budget": details.get("tool_budget") if isinstance(details.get("tool_budget"), dict) else {},
            "failure_code": details.get("failure_code"),
            "repeated_rejected_decisions": details.get("repeated_rejected_decisions"),
            "task_totals": details.get("task_totals") if isinstance(details.get("task_totals"), dict) else {},
        },
        "tokens": tokens,
        "prompt_accounting": build_prompt_cost_accounting(details, limit=limit),
        "context": context,
        "llm_calls": _safe_llm_calls(details, limit),
        "decisions": decisions,
        "tools": tools,
        "validation": {
            "write_transaction": details.get("write_transaction") if isinstance(details.get("write_transaction"), dict) else {},
            "claim_review": details.get("claim_review") if isinstance(details.get("claim_review"), dict) else {},
        },
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


def filter_execution_trace(
    trace: Dict[str, Any], *, section: str = "all", turn: Optional[int] = None, limit: int = 100,
) -> Dict[str, Any]:
    if not isinstance(trace, dict):
        return {}
    section = str(section or "all").strip().lower()
    if section not in _TRACE_SECTIONS:
        raise ValueError("section must be one of: " + ", ".join(sorted(_TRACE_SECTIONS)))
    limit = max(1, min(int(limit or 100), 200))
    base = {"summary": dict(trace.get("summary") or {}), "privacy": dict(trace.get("privacy") or {})}
    mapping = {
        "summary": [],
        "context": ["context", "prompt_accounting"],
        "llm": ["tokens", "llm_calls", "prompt_accounting"],
        "tools": ["tools"],
        "decisions": ["decisions"],
        "validation": ["validation"],
        "all": ["tokens", "prompt_accounting", "context", "llm_calls", "decisions", "tools", "validation"],
    }
    for key in mapping[section]:
        value = trace.get(key)
        if key in _TRACE_LIST_KEYS and isinstance(value, list):
            base[key] = _filter_turn(value, turn)[-limit:]
        else:
            base[key] = value
    if turn is not None:
        base["query"] = {"turn": int(turn), "section": section}
    elif section != "all":
        base["query"] = {"section": section}
    return base
