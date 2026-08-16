from __future__ import annotations

from eyle.capabilities import build_registry
from eyle.providers.standard import get_provider as get_standard_provider


def standard_registry():
    """Return the same canonical namespaced registry used by production."""
    return build_registry([get_standard_provider()])


def run_agent(module, *args, **kwargs):
    kwargs.setdefault("registry", standard_registry())
    return module.executar_agente(*args, **kwargs)


def base_config(*, tests_enabled=False):
    return {
        "app_version": "2.7.5",
        "config_schema_version": "2.7.5-r3-ecc",
        "revision": "rev3-ecc",
        "llm": {
            "context_window_tokens": None,
            "generated_token_fuse": 120000,
            "temperature": 0.0,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
        },
        "agent": {"task_deadline_seconds": 1800},
        "providers": {
            "standard": {
                "max_tree_entries": 200,
                "max_tree_depth": 6,
                "max_file_read_lines": 400,
                "max_project_scan_entries": None,
                "max_project_scan_depth": None,
                "max_project_file_bytes": 4194304,
                "max_inspect_relation_edges": None,
                "max_git_diff_chars": 6000,
                "max_search_matches": 40,
                "max_search_ranges": 12,
                "max_search_range_lines": 16,
                "tests": {"enabled": bool(tests_enabled)},
            },
        },
    }
