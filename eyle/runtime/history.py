#!/usr/bin/env python3
"""Safe observable job history for Eyle.

This module exposes only runtime actions and measurements. It deliberately does
not expose chain-of-thought, raw prompts, raw model responses, source contents,
or memory bodies.
"""
from __future__ import annotations


def build_public_job_history(registro):
    """Build a bounded observable execution history without chain-of-thought.

    Only runtime facts are exposed: phases, LLM usage metadata, tool calls with
    redacted arguments/results, and deterministic post-write validation.
    Prompts, model raw responses, source contents, hashes and memory bodies stay
    private.
    """
    if not isinstance(registro, dict):
        return None
    resultado = registro.get("resultado")
    details = resultado.get("details") if isinstance(resultado, dict) else None
    details = details if isinstance(details, dict) else {}
    usage = details.get("llm_usage") if isinstance(details.get("llm_usage"), dict) else {}
    snapshots = details.get("prompt_snapshots") if isinstance(details.get("prompt_snapshots"), list) else []
    responses = details.get("llm_responses") if isinstance(details.get("llm_responses"), list) else []

    llm_calls = []
    sent_requests = max(0, int(usage.get("llm_requests", len(responses)) or 0))
    logical_attempts = max(0, int(usage.get("llm_calls", len(snapshots)) or 0))
    total_calls = max(len(snapshots), len(responses), logical_attempts)
    for index in range(total_calls):
        snap = snapshots[index] if index < len(snapshots) and isinstance(snapshots[index], dict) else {}
        response = responses[index] if index < len(responses) and isinstance(responses[index], dict) else {}
        request_status = "sent" if index < sent_requests else "preflight_blocked"
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
            "request_status": request_status,
            "prompt_estimated_tokens": snap.get("estimated_tokens"),
            "prompt_characters": snap.get("characters"),
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
        }
        llm_calls.append({key: value for key, value in call.items() if value is not None})

    decisions = []
    for index, item in enumerate(details.get("decision_history") or []):
        if not isinstance(item, dict):
            continue
        decisions.append({
            "call": index + 1,
            "turn": item.get("turn"),
            "phase": item.get("phase"),
            "decision": item.get("decision"),
            "outcome": item.get("outcome"),
            "reason": item.get("reason"),
            "tools": list(item.get("tools") or [])[:8],
        })

    tools = []
    for index, item in enumerate(details.get("tool_history") or []):
        if not isinstance(item, dict):
            continue
        tools.append({
            "call": index + 1,
            "tool": item.get("tool"),
            "turn": item.get("turn"),
            "phase": item.get("phase"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
            "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            "result": item.get("result") if isinstance(item.get("result"), dict) else {},
        })

    total_prompt = usage.get("prompt_tokens_actual")
    total_cached = usage.get("prompt_tokens_cached")
    total_uncached = usage.get("prompt_tokens_uncached")
    token_summary = {
        "prompt_total": total_prompt,
        "prompt_cached": total_cached,
        "prompt_new": total_uncached,
        "prompt_effective": usage.get("prompt_tokens_effective"),
        "completion": usage.get("completion_tokens_actual", usage.get("generated_tokens")),
        "reasoning": usage.get("reasoning_tokens_actual"),
        "effective_total": usage.get("total_tokens_effective"),
    }

    write_validation = details.get("write_validation")
    if not isinstance(write_validation, dict):
        write_validation = {}

    return {
        "job_id": registro.get("id"),
        "status": registro.get("status"),
        "created_at": registro.get("criado_em"),
        "started_at": registro.get("iniciado_em"),
        "completed_at": registro.get("concluido_em"),
        "duration_seconds": (registro.get("progresso") or {}).get("elapsed_seconds") if isinstance(registro.get("progresso"), dict) else None,
        "agent": {
            "turns": details.get("turns"),
            "tool_calls": details.get("tool_calls"),
            "final_phase": details.get("runtime_phase"),
            "failure_code": details.get("failure_code") or (resultado.get("error_code") if isinstance(resultado, dict) else None),
            "parse_failures": details.get("parse_failures"),
            "no_progress_turns": details.get("no_progress_turns"),
            "phase_violations": details.get("phase_violations"),
        },
        "tokens": {key: value for key, value in token_summary.items() if value is not None},
        "llm": {
            "logical_attempts": logical_attempts,
            "requests_sent": sent_requests,
            "preflight_blocked": max(0, logical_attempts - sent_requests),
        },
        "llm_calls": llm_calls,
        "decisions": decisions,
        "tools": tools,
        "write_validation": write_validation,
        "write_failure": details.get("write_failure") if isinstance(details.get("write_failure"), dict) else None,
        "privacy": {
            "chain_of_thought_exposed": False,
            "raw_prompts_exposed": False,
            "raw_model_responses_exposed": False,
            "source_contents_exposed": False,
        },
    }


