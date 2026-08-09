"""Safe observable execution traces for Eyle.

The trace contains runtime facts only. It never includes chain-of-thought, raw
prompts, raw model responses, source bodies, patch bodies, hashes, secrets or
stored-memory bodies. Diagnosis remains the LLM's job.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


_TRACE_LIST_KEYS = ("phases", "context", "llm_calls", "decisions", "tools", "committed_progress_history", "tool_extension_history")
_TRACE_SECTIONS = {"all", "summary", "context", "llm", "tools", "decisions", "phases", "validation"}


def _bounded_list(value: Any, limit: int) -> List[Dict[str, Any]]:
    items = [dict(item) for item in (value or []) if isinstance(item, dict)]
    return items[-max(1, int(limit)):]


def _filter_turn(items: Iterable[Dict[str, Any]], turn: Optional[int]) -> List[Dict[str, Any]]:
    values = [dict(item) for item in items if isinstance(item, dict)]
    if turn is None:
        return values
    return [item for item in values if item.get("turn") == int(turn)]


def _safe_llm_calls(details: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    snapshots = details.get("prompt_snapshots") if isinstance(details.get("prompt_snapshots"), list) else []
    responses = details.get("llm_responses") if isinstance(details.get("llm_responses"), list) else []
    sent_requests = max(0, int(usage.get("llm_requests", len(responses)) or 0))
    logical_attempts = max(0, int(usage.get("llm_calls", len(snapshots)) or 0))
    total = max(len(snapshots), len(responses), logical_attempts)
    calls: List[Dict[str, Any]] = []
    for index in range(total):
        snap = snapshots[index] if index < len(snapshots) and isinstance(snapshots[index], dict) else {}
        response = responses[index] if index < len(responses) and isinstance(responses[index], dict) else {}
        prompt_tokens = response.get("prompt_tokens")
        cached_tokens = response.get("cached_prompt_tokens")
        uncached_tokens = None
        if isinstance(prompt_tokens, (int, float)):
            cached = cached_tokens if isinstance(cached_tokens, (int, float)) else 0
            uncached_tokens = max(0, prompt_tokens - cached)
        call = {
            "call": index + 1,
            "turn": snap.get("turn"),
            "phase": snap.get("phase"),
            "request_status": "sent" if index < sent_requests else "preflight_blocked",
            "prompt_estimated_tokens": snap.get("estimated_tokens"),
            "prompt_characters": snap.get("characters"),
            "prompt_budget_tokens": snap.get("prompt_budget_tokens"),
            "output_tokens_reserved": snap.get("output_tokens_reserved"),
            "tool_count_available": snap.get("tool_count"),
            "prompt_tokens": prompt_tokens,
            "cached_prompt_tokens": cached_tokens,
            "uncached_prompt_tokens": uncached_tokens,
            "completion_tokens": response.get("completion_tokens"),
            "reasoning_tokens": response.get("reasoning_tokens"),
            "finish_reason": response.get("finish_reason"),
            "provider_model": response.get("provider_model"),
            "latency_ms": response.get("orchestration_latency_ms", response.get("latency_ms")),
            "streaming": response.get("streaming"),
            "structured_profile": response.get("structured_profile"),
            "structured_mode": response.get("structured_mode"),
            "structured_capability_source": response.get("structured_capability_source"),
            "structured_parse_status": response.get("structured_parse_status"),
            "structured_parse_error": response.get("structured_parse_error"),
            "structured_parse_detail": response.get("structured_parse_detail"),
            "structured_top_level_keys": response.get("structured_top_level_keys"),
            "structured_missing_keys": response.get("structured_missing_keys"),
        }
        calls.append({key: value for key, value in call.items() if value is not None})
    return calls[-max(1, int(limit)):]


def build_execution_trace(
    details: Dict[str, Any], *, job_id: Optional[int] = None, status: Optional[str] = None,
    created_at: Any = None, started_at: Any = None, completed_at: Any = None,
    duration_seconds: Any = None, limit: int = 100,
) -> Dict[str, Any]:
    """Build one sanitized factual trace from AgentSession/runtime details."""
    details = details if isinstance(details, dict) else {}
    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    snapshots = _bounded_list(details.get("prompt_snapshots"), limit)

    context = []
    for item in snapshots:
        context.append({
            key: item.get(key)
            for key in (
                "turn", "phase", "characters", "estimated_tokens", "tool_count",
                "prompt_budget_tokens", "output_tokens_reserved", "system_prompt_characters",
                "system_prompt_estimated_tokens", "pre_crop_characters", "pre_crop_estimated_tokens",
                "crop_applied", "components_before", "components_after",
            )
            if item.get(key) is not None
        })

    decisions = []
    for index, item in enumerate(_bounded_list(details.get("decision_history"), limit)):
        decisions.append({
            "event": index + 1,
            "turn": item.get("turn"),
            "phase": item.get("phase"),
            "decision": item.get("decision"),
            "outcome": item.get("outcome"),
            "reason": item.get("reason"),
            "tools": list(item.get("tools") or [])[:8],
        })

    tools = []
    for index, item in enumerate(_bounded_list(details.get("tool_history"), limit)):
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        tools.append({
            "event": index + 1,
            "tool": item.get("tool") or result.get("tool") or "unknown_tool",
            "turn": item.get("turn"),
            "phase": item.get("phase"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
            "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            "result": result,
        })

    phases = _bounded_list(details.get("phase_history"), limit)
    if not phases and details.get("runtime_phase"):
        phases = [{"turn": details.get("turns"), "from": None, "to": details.get("runtime_phase")}]

    tokens = {
        "prompt_total": usage.get("prompt_tokens_actual"),
        "prompt_cached": usage.get("prompt_tokens_cached"),
        "prompt_new": usage.get("prompt_tokens_uncached"),
        "prompt_effective": usage.get("prompt_tokens_effective"),
        "completion": usage.get("completion_tokens_actual", usage.get("generated_tokens")),
        "completion_remaining": usage.get("completion_tokens_remaining"),
        "reasoning": usage.get("reasoning_tokens_actual"),
        "effective_total": usage.get("total_tokens_effective"),
        "completion_remaining_pre_call": usage.get("completion_tokens_remaining_pre_call"),
        "completion_requested_pre_call": usage.get("completion_tokens_requested_pre_call"),
        "completion_pending_pre_call": usage.get("completion_tokens_pending_pre_call"),
        "downstream_completion_reserve": usage.get("downstream_completion_reserve_tokens"),
        "administrative_calls": usage.get("administrative_llm_calls"),
        "administrative_prompt": usage.get("administrative_prompt_tokens"),
        "administrative_completion": usage.get("administrative_completion_tokens"),
        "administrative_reasoning": usage.get("administrative_reasoning_tokens"),
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
            "current_phase": details.get("runtime_phase"),
            "failure_code": details.get("failure_code"),
            "parse_failures": details.get("parse_failures"),
            "no_progress_turns": details.get("no_progress_turns"),
            "phase_violations": details.get("phase_violations"),
        },
        "tokens": tokens,
        "phases": phases,
        "context": context,
        "llm_calls": _safe_llm_calls(details, limit),
        "decisions": decisions,
        "tools": tools,
        "committed_progress_history": _bounded_list(details.get("committed_progress_history"), limit),
        "tool_extension_history": _bounded_list(details.get("tool_extension_history"), limit),
        "administrative": {
            "structured_capability": details.get("structured_capability") if isinstance(details.get("structured_capability"), dict) else {},
            "llm_history": _bounded_list(details.get("administrative_llm_history"), limit),
        },
        "validation": {
            "write_validation": details.get("write_validation") if isinstance(details.get("write_validation"), dict) else {},
            "write_failure": details.get("write_failure") if isinstance(details.get("write_failure"), dict) else None,
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
    """Return one bounded view of a sanitized trace without diagnosing it."""
    if not isinstance(trace, dict):
        return {}
    section = str(section or "all").strip().lower()
    if section not in _TRACE_SECTIONS:
        raise ValueError("section must be one of: " + ", ".join(sorted(_TRACE_SECTIONS)))
    limit = max(1, min(int(limit or 100), 200))

    base = {
        "summary": dict(trace.get("summary") or {}),
        "privacy": dict(trace.get("privacy") or {}),
    }
    mapping = {
        "summary": [],
        "context": ["context"],
        "llm": ["tokens", "llm_calls"],
        "tools": ["tools"],
        "decisions": ["decisions"],
        "phases": ["phases"],
        "validation": ["administrative", "validation"],
        "all": ["tokens", "phases", "context", "llm_calls", "decisions", "tools", "committed_progress_history", "tool_extension_history", "administrative", "validation"],
    }
    for key in mapping[section]:
        value = trace.get(key)
        if key in _TRACE_LIST_KEYS and isinstance(value, list):
            filtered = _filter_turn(value, turn)
            base[key] = filtered[-limit:]
        else:
            base[key] = value
    if turn is not None:
        base["query"] = {"turn": int(turn), "section": section}
    elif section != "all":
        base["query"] = {"section": section}
    return base
