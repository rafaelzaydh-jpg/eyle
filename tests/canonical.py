from __future__ import annotations


def investigation_target(
    target_id="T1", *, goal="Establish the material project fact needed by the request",
    status="open", evidence_ids=None, reason="",
):
    return {
        "id": str(target_id),
        "goal": str(goal),
        "status": str(status),
        "evidence_ids": list(evidence_ids or []),
        "reason": str(reason),
    }


def workspace_scope(mode="read", reason=None):
    if reason is None:
        reason = {
            "none": "The fixture is independent of the live workspace.",
            "read": "The fixture depends on current workspace facts.",
            "write": "The fixture requests a workspace mutation.",
        }[mode]
    return {"mode": str(mode), "reason": str(reason)}


def _default_final_investigation(final):
    if isinstance(final, dict):
        ids = [str(item) for item in final.get("evidence_ids") or [] if str(item)]
        if ids:
            return [investigation_target(
                status="established", evidence_ids=ids, reason="Grounded by the cited fixture Evidence.",
            )]
    return []


def agent_tools(*calls, investigation=None, scope=None):
    if investigation is None:
        investigation = [investigation_target()]
    return {"tool_calls": [dict(call) for call in calls], "workspace_scope": dict(scope or workspace_scope("read")), "investigation_updates": [dict(item) for item in investigation]}


def tool_call(tool, arguments=None):
    return {"tool": tool, "arguments": dict(arguments or {})}


def agent_patches(patches, investigation=None, scope=None):
    if investigation is None:
        investigation = [investigation_target(
            status="established", evidence_ids=["ev-0001"],
            reason="The fixture has read the source required for the write.",
        )]
    return {"patches": [dict(item) for item in patches], "workspace_scope": dict(scope or workspace_scope("write")), "investigation_updates": [dict(item) for item in investigation]}


def agent_final(final, investigation=None, scope=None):
    if investigation is None:
        investigation = _default_final_investigation(final)
    if scope is None:
        mode = "read" if isinstance(final, dict) and final.get("evidence_ids") else "none"
        scope = workspace_scope(mode)
    return {"final": final, "workspace_scope": dict(scope), "investigation_updates": [dict(item) for item in investigation]}


def agent_needs_user(message, investigation=None, scope=None):
    items = [dict(item) for item in (investigation or [])]
    if scope is None:
        scope = workspace_scope("read" if items else "none")
    return {"needs_user": str(message), "workspace_scope": dict(scope), "investigation_updates": items}


def claim(
    claim_id="claim-1", *, answer_ref="a1", target_id=None, statement="supported fact",
    kind="fact", evidence_ids=None, verdict="supported", reason="",
):
    return {
        "id": claim_id,
        "answer_ref": answer_ref,
        "target_id": target_id,
        "statement": statement,
        "kind": kind,
        "evidence_ids": list(evidence_ids or []),
        "verdict": verdict,
        "reason": reason,
    }


def review(claims=None, findings=None, semantic_gaps=None):
    return {
        "claims": list(claims or []),
        "findings": list(findings or []),
        "semantic_gaps": list(semantic_gaps or []),
    }



def base_config(*, claims_mode="off", tests_enabled=False):
    return {
        "llm": {
            "context_window_tokens": 32768,
            "agent_decision_max_tokens": 1100,
            "agent_analysis_max_tokens": 1800,
            "agent_patch_max_tokens": 3600,
            "temperature": 0.0,
            "max_tokens": 1500,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "working_set_target_tokens": 12000,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 8,
            "max_tool_calls": 12,
            "max_identical_tool_repeats": 2,
            "structured_protocol_retries": 1,
            "final_validation_retries": 1,
            "chat_history_token_budget": 700,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "max_search_range_lines": 16,
            "max_search_matches": 40,
            "max_search_ranges": 12,
            "max_no_progress_turns": 2,
            "max_phase_violations": 1,
            "context_view": {
                "max_relevant_sources": 4,
                "max_relevant_source_chars": 3500,
                "max_symbol_preview_chars": 2600,
                "max_search_source_chars": 600,
            },
            "claims": {
                "mode": claims_mode,
                "require_supported": True,
                "verifier": {"max_tokens": 900, "temperature": 0.0},
                "evidence": {"max_chars_per_item": 2200},
            },
        },
        "codar": {"ativado": True, "testes": {"ativado": bool(tests_enabled)}},
        "_runtime_agent_budget": {
            "max_llm_calls": 12,
            "max_prompt_tokens": 96000,
            "max_completion_tokens": 9000,
            "max_total_tokens": 105000,
            "llm_calls": 0,
            "llm_requests": 0,
        },
    }
