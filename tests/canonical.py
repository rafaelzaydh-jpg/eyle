from __future__ import annotations

from eyle.capabilities import build_registry
from eyle.providers.standard import get_provider as get_standard_provider
from eyle.providers.memory import get_provider as get_memory_provider


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



class LegacyStandardRegistryAdapter:
    """Test-only adapter for legacy mechanics tests. Production registry stays canonical/namespaced."""
    def __init__(self, registry):
        self._registry = registry

    def _canonical(self, name):
        raw = str(name)
        if "." in raw:
            return raw
        aliases = {"memory_search": "memory.search", "memory_store": "memory.store"}
        if raw in aliases:
            return aliases[raw]
        standard = f"standard.{raw}"
        if standard in self._registry.names():
            return standard
        matches = [item for item in self._registry.names() if item.endswith("." + raw)]
        if len(matches) == 1:
            return matches[0]
        return raw

    def names(self):
        return self._registry.names()

    def register(self, provider):
        return self._registry.register(provider)

    def validate_host_config(self, config):
        return self._registry.validate_host_config(config)

    def catalog(self, *args, **kwargs):
        if kwargs.get("allowed_names") is not None:
            kwargs["allowed_names"] = {self._canonical(item) for item in kwargs["allowed_names"]}
        return self._registry.catalog(*args, **kwargs)

    def provider_descriptions(self, *args, **kwargs):
        return self._registry.provider_descriptions(*args, **kwargs)

    def cleanup(self, *args, **kwargs):
        return self._registry.cleanup(*args, **kwargs)

    def __getattr__(self, attr):
        target = getattr(self._registry, attr)
        if not callable(target) or attr not in {
            "spec", "validate", "execute", "requires_confirmation",
            "prepare_confirmation", "confirm", "observation_signature",
            "find_covering", "find_resource_failure", "public_arguments",
            "public_result", "model_detail", "provider_id",
        }:
            return target

        def wrapped(name, *args, **kwargs):
            return target(self._canonical(name), *args, **kwargs)
        return wrapped


def standard_registry():
    return LegacyStandardRegistryAdapter(build_registry([get_standard_provider(), get_memory_provider()]))


def run_agent(module, *args, **kwargs):
    kwargs.setdefault("registry", standard_registry())
    return module.executar_agente(*args, **kwargs)


def tool_call(tool, arguments=None):
    name = str(tool)
    if "." not in name:
        name = "standard." + name
    return {"capability": name, "arguments": dict(arguments or {})}


def agent_tools(*calls, investigation=None, tasks=None):
    out = {"action": {"kind": "capability_calls", "calls": [dict(call) for call in calls]}}
    if investigation:
        out["investigation_updates"] = [dict(item) for item in investigation]
    if tasks:
        out["task_updates"] = [dict(item) for item in tasks]
    return out


def agent_patches(patches, investigation=None, tasks=None):
    # Compatibility helper for old tests: Rev1.5 exposes workspace mutation as a normal capability.
    return agent_tools(
        tool_call("workspace_transaction", {"patches": [dict(item) for item in patches]}),
        investigation=investigation, tasks=tasks,
    )


def agent_complete(complete, investigation=None, tasks=None):
    if isinstance(complete, str):
        complete = {
            "answer": complete, "limitations": [],
            "grounding_ids": [], "effect_ids": [],
        }
    else:
        raw = complete or {}
        grounding_ids = list(raw.get("grounding_ids") or [])
        effect_ids = list(raw.get("effect_ids") or [])
        complete = {
            "answer": str(raw.get("answer") or ""),
            "limitations": list(raw.get("limitations") or []),
            "grounding_ids": grounding_ids,
            "effect_ids": effect_ids,
        }
    out = {"action": {"kind": "complete", **complete}}
    if investigation:
        out["investigation_updates"] = [dict(item) for item in investigation]
    if tasks:
        out["task_updates"] = [dict(item) for item in tasks]
    return out


def agent_await_user(
    message, investigation=None, tasks=None, *,
    reason="A user decision or input is required to continue the active work",
    options=None,
):
    out = {
        "action": {
            "kind": "await_user",
            "question": str(message),
            "reason": str(reason),
            "options": [dict(item) for item in (options or [])],
        }
    }
    if investigation:
        out["investigation_updates"] = [dict(item) for item in investigation]
    if tasks:
        out["task_updates"] = [dict(item) for item in tasks]
    return out


def base_config(*, tests_enabled=False):
    return {
        "app_version": "2.7.5",
        "config_schema_version": "2.7.5-r1.5.1",
        "revision": "rev1.5.1-host-injected-universal-capabilities",
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
        "agent": {"task_deadline_seconds": 1800},
        "providers": {
            "standard": {
                "max_tree_entries": 200,
                "max_tree_depth": 6,
                "max_file_read_lines": 400,
                "max_project_scan_entries": 20000,
                "max_project_scan_depth": 32,
                "max_project_file_bytes": 4194304,
                "max_inspect_relation_edges": 60,
                "max_git_diff_chars": 6000,
                "max_search_matches": 40,
                "max_search_ranges": 12,
                "max_search_range_lines": 16,
                "tests": {"enabled": bool(tests_enabled)},
            },
            "memory": {},
        },
    }
