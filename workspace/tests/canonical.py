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


def tool_call(tool, arguments=None):
    return {"tool": tool, "arguments": dict(arguments or {})}


def agent_tools(*calls, investigation=None):
    return {
        "tool_calls": [dict(call) for call in calls],
        "investigation_updates": [dict(item) for item in (investigation or [])],
    }


def agent_patches(patches, investigation=None):
    return {
        "patches": [dict(item) for item in patches],
        "investigation_updates": [dict(item) for item in (investigation or [])],
    }


def agent_final(final, investigation=None):
    if isinstance(final, str):
        final = {"answer": final, "limitations": [], "evidence_ids": []}
    else:
        final = {
            "answer": str((final or {}).get("answer") or ""),
            "limitations": list((final or {}).get("limitations") or []),
            "evidence_ids": list((final or {}).get("evidence_ids") or []),
        }
    return {
        "final": final,
        "investigation_updates": [dict(item) for item in (investigation or [])],
    }


def agent_needs_user(message, investigation=None, *, missing_information="A concrete user-supplied fact is required to continue the active task"):
    return {
        "needs_user": {
            "question": str(message),
            "missing_information": str(missing_information),
        },
        "investigation_updates": [dict(item) for item in (investigation or [])],
    }


def claim(
    *, answer_ref="answer:a1", target_id=None, statement="supported fact",
    evidence_ids=None, grounding_refs=None, verdict="supported", reason="",
):
    refs = list(grounding_refs or [])
    if not refs:
        refs = [f"evidence:{item}" for item in (evidence_ids or [])]
    if not refs:
        refs = ["request"]
    if target_id is not None and not str(target_id).startswith("investigation:"):
        target_id = f"investigation:{target_id}"
    return {
        "answer_ref": answer_ref, "target_id": target_id, "statement": statement,
        "grounding_refs": refs, "verdict": verdict, "reason": reason,
    }


def review(
    claims=None, semantic_gaps=None, material_status="satisfied",
    material_reason="Fixture delivers the requested material result.",
    material_grounding=None, consistency_status="consistent",
    consistency_reason="Fixture answer is internally consistent.", consistency_grounding=None,
):
    return {
        "material_satisfaction": {
            "status": str(material_status),
            "grounding_refs": list(material_grounding or (["runtime:r1"] if material_status == "blocked" else ["request"])),
            "reason": str(material_reason),
        },
        "answer_consistency": {
            "status": str(consistency_status),
            "grounding_refs": list(consistency_grounding or ["answer:a1"]),
            "reason": str(consistency_reason),
        },
        "claims": list(claims or []),
        "semantic_gaps": list(semantic_gaps or []),
    }


def base_config(*, claims_mode="off", tests_enabled=False):
    return {
        "app_version": "2.7.4",
        "config_schema_version": "5.7.5",
        "revision": "rev5.7.5-canonical-boundary-hardening",
        "llm": {
            "context_window_tokens": 32768,
            "agent_max_tokens": 3600,
            "temperature": 0.0,
            "max_tokens": 1500,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 24,
            "max_tool_calls": 64,
            "max_llm_calls": 32,
            "max_prompt_tokens": 90000,
            "max_completion_tokens": 8000,
            "max_total_tokens": 98000,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_file_read_lines": 400,
            "max_search_range_lines": 16,
            "max_search_matches": 40,
            "max_search_ranges": 12,
            "context_view": {
                "max_source_preview_chars": 3500,
                "max_symbol_preview_chars": 2600,
                "max_search_source_chars": 600,
            },
            "claims": {
                "mode": claims_mode,
                "verifier": {"max_tokens": 900, "temperature": 0.0},
                "evidence": {"max_chars_per_item": 2200},
            },
        },
        "codar": {"ativado": True, "testes": {"ativado": bool(tests_enabled)}},

    }
