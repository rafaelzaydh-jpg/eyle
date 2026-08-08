from __future__ import annotations


def agent_tools(*calls, plan=None):
    return {"tool_calls": [dict(call) for call in calls], "plan": list(plan or [])}


def tool_call(tool, arguments=None):
    return {"tool": tool, "arguments": dict(arguments or {})}


def agent_patches(patches, plan=None):
    return {"patches": [dict(item) for item in patches], "plan": list(plan or [])}


def agent_final(final, plan=None):
    return {"final": final, "plan": list(plan or [])}


def agent_needs_user(message, plan=None):
    return {"needs_user": str(message), "plan": list(plan or [])}


def claim(
    claim_id="claim-1", *, answer_ref="a1", statement="supported fact",
    kind="fact", evidence_ids=None, verdict="supported", reason="",
):
    return {
        "id": claim_id,
        "answer_ref": answer_ref,
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


def repair(repairs=None):
    return {"repairs": list(repairs or [])}


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
                "repair": {"enabled": True, "max_attempts": 1},
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
