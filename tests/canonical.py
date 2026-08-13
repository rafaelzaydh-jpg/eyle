from __future__ import annotations


def investigation_target(
    target_id="T1", *, goal="Establish the material project fact needed by the request",
    status="open", grounding_ids=None, conclusion=None, reason="",
):
    if status == "established" and conclusion is None:
        conclusion = "The grounded Material establishes the Investigation goal."
    if conclusion is None:
        conclusion = ""
    return {
        "id": str(target_id),
        "goal": str(goal),
        "status": str(status),
        "grounding_ids": list(grounding_ids or []),
        "conclusion": str(conclusion),
        "reason": str(reason),
    }


def task_item(
    task_id="task-1", *, parent_id=None, description="Complete the required work",
    completion_criteria=None, status="open", result="", grounding_ids=None,
):
    return {
        "id": str(task_id),
        "parent_id": None if parent_id is None else str(parent_id),
        "description": str(description),
        "completion_criteria": list(completion_criteria or ["The described work is complete"]),
        "status": str(status),
        "result": str(result),
        "grounding_ids": list(grounding_ids or []),
    }


def tool_call(tool, arguments=None):
    return {"tool": tool, "arguments": dict(arguments or {})}


def agent_tools(*calls, investigation=None, tasks=None):
    return {
        "action": {"kind": "tool_calls", "calls": [dict(call) for call in calls]},
        "investigation_updates": [dict(item) for item in (investigation or [])],
        "task_updates": [dict(item) for item in (tasks or [])],
    }


def agent_patches(patches, investigation=None, tasks=None):
    return {
        "action": {"kind": "patches", "patches": [dict(item) for item in patches]},
        "investigation_updates": [dict(item) for item in (investigation or [])],
        "task_updates": [dict(item) for item in (tasks or [])],
    }


def agent_final(final, investigation=None, tasks=None):
    if isinstance(final, str):
        final = {"answer": final, "limitations": [], "grounding_ids": []}
    else:
        final = {
            "answer": str((final or {}).get("answer") or ""),
            "limitations": list((final or {}).get("limitations") or []),
            "grounding_ids": list((final or {}).get("grounding_ids") or []),
        }
    return {
        "action": {"kind": "final", **final},
        "investigation_updates": [dict(item) for item in (investigation or [])],
        "task_updates": [dict(item) for item in (tasks or [])],
    }


def agent_needs_user(message, investigation=None, tasks=None, *, missing_information="A concrete user-supplied fact is required to continue the active task"):
    return {
        "action": {
            "kind": "needs_user",
            "question": str(message),
            "missing_information": str(missing_information),
        },
        "investigation_updates": [dict(item) for item in (investigation or [])],
        "task_updates": [dict(item) for item in (tasks or [])],
    }


def base_config(*, tests_enabled=False):
    return {
        "app_version": "2.7.5",
        "config_schema_version": "2.7.5-r1.4.3",
        "revision": "rev1.4.3-semantic-completion",
        "llm": {
            "context_window_tokens": 38000,
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
            "max_total_tokens": 90000,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_file_read_lines": 400,
            "max_search_range_lines": 16,
            "max_search_matches": 40,
            "max_search_ranges": 12,
        },
        "codar": {"ativado": True, "testes": {"ativado": bool(tests_enabled)}},
    }
