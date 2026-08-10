#!/usr/bin/env python3
"""Safe factual job history projected from the canonical ExecutionTrace."""
from __future__ import annotations

from eyle.core.execution_trace import build_execution_trace


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
    sent_requests = sum(1 for item in llm_calls if item.get("request_status") == "sent")
    return {
        "job_id": summary.get("job_id"),
        "status": summary.get("status"),
        "created_at": summary.get("created_at"),
        "started_at": summary.get("started_at"),
        "completed_at": summary.get("completed_at"),
        "duration_seconds": summary.get("duration_seconds"),
        "agent": {
            "turns": summary.get("turns"),
            "tool_calls": summary.get("tool_calls"),
            "tool_call_limit": (summary.get("tool_budget") or {}).get("limit"),
            "tool_calls_remaining": (summary.get("tool_budget") or {}).get("remaining"),
            "workspace_epoch": details.get("workspace_epoch"),
            "evidence_count_total": details.get("evidence_count_total"),
            "observation_replays": details.get("observation_replays"),
            "observation_ledger_size": details.get("observation_ledger_size"),
            "repeated_rejected_decisions": summary.get("repeated_rejected_decisions"),
            "failure_code": summary.get("failure_code") or (resultado.get("error_code") if isinstance(resultado, dict) else None),
            "task_totals": summary.get("task_totals") if isinstance(summary.get("task_totals"), dict) else {},
        },
        "tokens": token_summary,
        "prompt_accounting": trace.get("prompt_accounting") or {},
        "llm": {
            "logical_attempts": len(logical_ids),
            "requests_sent": sent_requests,
            "preflight_blocked": sum(1 for item in llm_calls if item.get("request_status") == "preflight_blocked"),
        },
        "llm_calls": llm_calls,
        "decisions": list(trace.get("decisions") or []),
        "tools": list(trace.get("tools") or []),
        "write_transaction": (trace.get("validation") or {}).get("write_transaction") or {},
        "claim_review": (trace.get("validation") or {}).get("claim_review") or {},
        "privacy": dict(trace.get("privacy") or {}),
    }
