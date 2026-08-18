#!/usr/bin/env python3
"""Capability registry assembly for the standard provider."""
import os

from eyle.capabilities.registry import Provider
from eyle.contracts.observation import normalize_effect
from eyle.providers.standard import workspace_transaction as _workspace_transaction
from eyle.providers.standard import sandbox_promotion as _sandbox_promotion
from eyle.providers.standard.common import _standard_context, _standard_config
from eyle.providers.standard.tools import (
    _FILE_PAGE_LINES,
    _continue_find_symbol_page,
    _continue_git_diff_page,
    _continue_git_status_page,
    _continue_list_tree_page,
    _continue_read_file_page,
    _continue_search_code_page,
    _continue_structured_page,
    _git_freshness_token,
    _source_name,
    _source_tree_freshness_token,
    _standard_tests_config,
    _tool_calculate,
    _tool_continue_observation,
    _tool_count_tokens,
    _tool_export_sandbox_zip,
    _tool_find_symbol,
    _tool_git_diff,
    _tool_git_status,
    _tool_inspect_project,
    _tool_list_tree,
    _tool_project_stats,
    _tool_read_file,
    _tool_run_command,
    _tool_run_tests,
    _tool_search_code,
    _tool_symbol_relations,
)
from eyle.providers.standard.contracts import (
    _coverage_atomic,
    _coverage_continue,
    _coverage_count_tokens,
    _coverage_file,
    _coverage_find_symbol,
    _coverage_git_diff,
    _coverage_git_status,
    _coverage_inspect_project,
    _coverage_project_stats,
    _coverage_relations,
    _coverage_search,
    _coverage_tree,
    _covering_read_file,
    _evidence_selector_file,
    _frontier_passthrough,
    _model_projection_command,
    _model_projection_find_symbol,
    _model_projection_inspect,
    _model_projection_read_file,
    _model_projection_relations,
    _model_projection_search,
    _model_projection_workspace_transaction,
    _normalize_symbol_relations_arguments,
    _observe_continue,
    _observe_file,
    _observe_find_symbol,
    _observe_json,
    _observe_none,
    _observe_search,
    _observe_tree,
    _observe_workspace_transaction,
    _public_arguments_command,
    _public_arguments_keys,
    _public_arguments_read_file,
    _public_arguments_search,
    _public_result_command,
    _public_result_fields,
    _public_result_file,
    _public_result_find_symbol,
    _public_result_git_diff,
    _public_result_git_status,
    _public_result_inspect,
    _public_result_relations,
    _public_result_search,
    _public_result_tree,
    _public_result_workspace_transaction,
    _rehydrate_file_material,
    _resource_failure_by_path,
    _sig_count_tokens,
    _sig_find_symbol,
    _sig_list_tree,
    _sig_read_file,
    _sig_search_code,
    _sig_symbol_relations,
    _validate_file_material_freshness,
    capability_rehydrate_materials,
)

def _schema_objeto(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_CAMINHO = {
    "type": "string", "minLength": 1,
    "description": "Path inside the project.",
}
_LINHA = {"type": "integer", "minimum": 1, "description": "File line, starting at 1."}
_SOURCE = {
    "type": "string", "enum": ["workspace", "eyle"],
    "description": "workspace=the user-selected/open project, even when it is a copy/fork/version of Eyle; eyle=the source tree of the Eyle instance currently running.",
}

CAPABILITIES = {
    "calculate": {
        "description": "Calculate an arithmetic expression.",
        "availability": "global",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Result.",
        "input_schema": _schema_objeto({
            "expression": {"type": "string", "minLength": 1, "maxLength": 500, "description": "Arithmetic expression."},
        }, ["expression"]),
        "fn": _tool_calculate,
    },
    "project_stats": {
        "description": "Count project files, lines, bytes, and languages.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "File, folder, line, byte, and language counts.",
        "caveats": ["Counts do not tell what matters or what runs."],
        "input_schema": _schema_objeto({"source": _SOURCE}),
        "fn": _tool_project_stats,
    },
    "count_tokens": {
        "description": "Count or estimate tokens in project text.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Token count or estimate and what was counted.",
        "caveats": ["Not LLM request usage."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": {"type": "string", "minLength": 1, "description": "Optional file or folder."},
            "tokenizer": {"type": "string", "minLength": 1, "description": "Optional tokenizer or model name."},
        }),
        "fn": _tool_count_tokens,
    },
    "inspect_project": {
        "description": "Get a quick map of the project.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Languages, entry points, imports, tests, CI, frameworks.",
        "caveats": ["Code structure does not prove runtime behavior."],
        "input_schema": _schema_objeto({"source": _SOURCE}),
        "fn": _tool_inspect_project,
    },
    "list_tree": {
        "description": "List files and folders.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Current tree page, complete Coverage, and Frontier when more entries remain.",
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "limit": {"type": "integer", "minimum": 1, "description": "Page size; remaining tree entries become Frontier."},
            "depth": {"type": "integer", "minimum": 1, "description": "Folder levels to scan for this tree request; this is request scope, not a global reading ceiling."},
            "filter": {"type": "string", "minLength": 1, "description": "Optional path filter."},
        }),
        "fn": _tool_list_tree,
    },
    "search_code": {
        "ecc_returns": "Files and lines that contain the text.",
        "ecc_caveats": ["A text match does not explain what the code means; read it when needed."],
        "description": "Search for exact text in files.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Match Material, Coverage and optional Frontier.",
        "caveats": ["Literal only; protected resources are a Coverage boundary."],
        "input_schema": _schema_objeto(
            {
                "source": _SOURCE,
            "query": {"type": "string", "minLength": 1, "description": "Exact text to find."},
                "include_paths": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "description": "Only search these paths."},
                "exclude_paths": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "description": "Skip these paths."},
            }, ["query"],
        ),
        "fn": _tool_search_code,
    },
    "symbol_relations": {
        "description": "See where a code symbol is connected.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Code connections and paths.",
        "caveats": ["Static code links may miss runtime behavior."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "symbol": {"type": "string", "minLength": 1, "description": "Symbol name."},
            "query": {"type": "string", "enum": ["relations", "reachability"], "description": "relations=links; reachability=path from a root."},
            "path": _CAMINHO,
            "roots": {"type": "array", "items": {"type": "string", "minLength": 1}, "description": "Optional starting symbols."},
            "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "description": "Which link direction."},
            "include_text_references": {"type": "boolean", "description": "Also include text mentions."},
            "max_depth": {"type": "integer", "minimum": 1, "description": "Traversal depth for the local relations view; Main chooses it."},
            "page_size": {"type": "integer", "minimum": 1, "description": "Relation rows materialized now; remaining rows become Frontier."},
        }, ["symbol"]),
        "fn": _tool_symbol_relations,
    },
    "continue_observation": {
        "description": "Continue a result that was cut short.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Next part of the result.",
        "caveats": ["Works only while its saved snapshot is valid."],
        "input_schema": _schema_objeto({
            "frontier": {"type": "string", "minLength": 4, "pattern": r"^fr-[0-9]+$", "description": "Continuation id."},
        }, ["frontier"]),
        "fn": _tool_continue_observation,
    },
    "find_symbol": {
        "description": "Find where a code symbol is defined.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "File and lines where the symbol is defined.",
        "caveats": ["A definition does not prove runtime use."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": _CAMINHO,
            "symbol": {"type": "string", "minLength": 1, "description": "Symbol name."},
        }, ["symbol"]),
        "fn": _tool_find_symbol,
    },
    "read_file": {
        "description": "Read a file or part of a file.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Requested file range/page, hashes, and Frontier when the file continues.",
        "caveats": ["File scopes are paged, including large explicit ranges; continue the Frontier for the exact remainder. Protected content is blocked."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": _CAMINHO,
            "line_start": _LINHA,
            "line_end": _LINHA,
        }, ["path"]),
        "fn": _tool_read_file,
    },
    "run_command": {
        "description": "Run a shell command in a safe copy made for this job.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "execute",
        "returns": "Exit code and output from the job copy.",
        "establishes": ["Command result inside the job copy."],
        "does_not_establish": ["Changes to the real workspace."],
        "caveats": ["Runs in a copy; the real workspace stays unchanged."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "command": {"type": "string", "minLength": 1, "maxLength": 8000, "description": "Shell command."},
            "cwd": {"type": "string", "minLength": 1, "description": "Optional folder inside the job copy."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Optional time limit."},
        }, ["command"]),
        "fn": _tool_run_command,
    },
    "export_sandbox_zip": {
        "description": "Save the current job copy as a ZIP file.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "mutate",
        "returns": "ZIP name, size, SHA-256.",
        "caveats": ["A job copy must already exist. An existing ZIP is not overwritten."],
        "input_schema": _schema_objeto({
            "filename": {"type": "string", "minLength": 5, "maxLength": 160, "pattern": r"^[A-Za-z0-9._-]+\.zip$", "description": "ZIP filename."},
            "archive_root": {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[A-Za-z0-9._-]+$", "description": "Optional top folder name in the ZIP."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Time limit."},
        }, ["filename"]),
        "fn": _tool_export_sandbox_zip,
    },
    "promote_sandbox": {
        "description": "Promote an exact tested sandbox subtree into the real workspace after one user confirmation.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "mutate",
        "confirmation": "required",
        "returns": "Exact byte-verified promotion manifest and changed workspace paths.",
        "establishes": ["The confirmed sandbox snapshot bytes were promoted to the declared workspace target."],
        "does_not_establish": ["That tests passed unless test execution was separately observed in the sandbox."],
        "caveats": ["Requires an active run_command sandbox snapshot. merge never deletes unrelated target files; mirror may delete absent target files and therefore must be chosen explicitly."],
        "input_schema": _sandbox_promotion.schema(),
        "prepare": _sandbox_promotion.prepare,
        "confirm": _sandbox_promotion.confirm,
        "cancel": _sandbox_promotion.cancel,
    },
    "run_tests": {
        "description": "Run the project tests in a safe copy.",
        "availability": "tests_source",
        "produces_grounding": True,
        "effect": "execute",
        "returns": "Test status, exit code, summary, output.",
        "caveats": ["Only tests that actually ran count as evidence."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "scope": {"type": "string", "minLength": 1, "description": "Optional test file or folder."},
        }),
        "fn": _tool_run_tests,
    },
    "git_status": {
        "description": "Check Git status.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Branch and changed paths.",
        "caveats": ["This does not include the changed text itself."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "max_entries": {"type": "integer", "minimum": 1, "description": "Page size for changed paths; remaining paths become Frontier."},
        }),
        "fn": _tool_git_status,
    },
    "git_diff": {
        "description": "Read Git changes.",
        "availability": "source",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Changed files and changed text.",
        "caveats": ["Diffs are paged; continue the Frontier to read the rest."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": {"type": "string", "minLength": 1, "description": "Optional path."},
            "staged": {"type": "boolean", "description": "Use staged changes."},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Extra lines around each change."},
        }),
        "fn": _tool_git_diff,
    },
}

# Capability-owned physical observation hooks. Agent/Observation consume only
# this registry contract; adding a new observational capability must not require
# capability-name branches in either core module.
_ECC_NAMES = {
    "calculate": "calculate", "project_stats": "project_stats", "count_tokens": "count_tokens",
    "inspect_project": "inspect_project", "list_tree": "list_tree", "search_code": "search",
    "symbol_relations": "symbol_relations", "continue_observation": "continue", "find_symbol": "find_symbol",
    "read_file": "read_file", "run_command": "run_command", "run_tests": "run_tests",
    "git_status": "git_status", "git_diff": "git_diff", "workspace_transaction": "transaction",
    "export_sandbox_zip": "export_sandbox_zip", "promote_sandbox": "promote_sandbox",
}
for _local_name, _ecc_name in _ECC_NAMES.items():
    if _local_name in CAPABILITIES:
        CAPABILITIES[_local_name]["ecc_name"] = _ecc_name

_ECC_EXPLICIT_SOURCE = {
    "project_stats", "count_tokens", "inspect_project", "list_tree", "search_code",
    "symbol_relations", "find_symbol", "read_file", "run_command", "run_tests",
    "git_status", "git_diff",
}
for _local_name in _ECC_EXPLICIT_SOURCE:
    CAPABILITIES[_local_name]["ecc_require_explicit_source"] = True

CAPABILITIES["list_tree"].update(signature=_sig_list_tree, observe=_observe_tree, coverage=_coverage_tree)
CAPABILITIES["list_tree"]["continue"] = _continue_list_tree_page
CAPABILITIES["search_code"].update(signature=_sig_search_code, observe=_observe_search, coverage=_coverage_search)
CAPABILITIES["search_code"]["continue"] = _continue_search_code_page
CAPABILITIES["find_symbol"].update(signature=_sig_find_symbol, observe=_observe_find_symbol, coverage=_coverage_find_symbol)
CAPABILITIES["find_symbol"]["continue"] = _continue_find_symbol_page
CAPABILITIES["symbol_relations"].update(signature=_sig_symbol_relations, observe=_observe_json("symbol_relations"), coverage=_coverage_relations)
CAPABILITIES["symbol_relations"]["continue"] = _continue_structured_page
CAPABILITIES["read_file"].update(signature=_sig_read_file, observe=_observe_file("read_file"), coverage=_coverage_file)
CAPABILITIES["read_file"]["continue"] = _continue_read_file_page
CAPABILITIES["project_stats"].update(signature=lambda arguments: f"project_stats:{_source_name(arguments)}:root", observe=_observe_json("project_stats"), coverage=_coverage_project_stats)
CAPABILITIES["inspect_project"].update(signature=lambda arguments: f"inspect_project:{_source_name(arguments)}:root", observe=_observe_json("inspect_project"), coverage=_coverage_inspect_project)
CAPABILITIES["count_tokens"].update(signature=_sig_count_tokens, observe=_observe_json("count_tokens"), coverage=_coverage_count_tokens)
CAPABILITIES["run_tests"].update(observe=_observe_json("run_tests"), coverage=_coverage_atomic("test_execution", lambda a, d: {"kind":"test_execution", "scope": d.get("scope") or a.get("scope") or "."}, lambda a, d: {"returncode": d.get("returncode")}))
CAPABILITIES["git_status"].update(observe=_observe_json("git_status"), coverage=_coverage_git_status)
CAPABILITIES["git_status"]["continue"] = _continue_git_status_page
CAPABILITIES["git_diff"].update(observe=_observe_json("git_diff"), coverage=_coverage_git_diff)
CAPABILITIES["git_diff"]["continue"] = _continue_git_diff_page
CAPABILITIES["continue_observation"].update(observe=_observe_continue, coverage=_coverage_continue)
CAPABILITIES["calculate"].update(observe=_observe_json("calculate"), coverage=_coverage_atomic("calculation", lambda a, d: {"kind":"calculation", "expression": a.get("expression")}))
CAPABILITIES["run_command"].update(observe=_observe_json("run_command"), coverage=_coverage_atomic("sandbox_command", lambda a, d: {"kind":"sandbox_command", "source": _source_name(a), "cwd": a.get("cwd") or "."}, lambda a, d: {"returncode": d.get("returncode")}))
CAPABILITIES["export_sandbox_zip"].update(observe=_observe_json("export_sandbox_zip"), coverage=_coverage_atomic("sandbox_export", lambda a, d: {"kind":"sandbox_export", "artifact": d.get("artifact") or a.get("filename")}, lambda a, d: {"bytes": d.get("bytes")}))
CAPABILITIES["promote_sandbox"].update(observe=_observe_json("sandbox_promotion"), coverage=_coverage_atomic("sandbox_promotion", lambda a, d: {"kind":"sandbox_promotion", "sandbox_path": a.get("sandbox_path") or ".", "workspace_path": a.get("workspace_path") or ".", "mode": a.get("mode") or "merge"}, lambda a, d: {"files": len(d.get("files") or [])}))

# Capability-owned presentation, normalization and memoization hooks. Generic
# dispatch functions above never branch on capability names.
CAPABILITIES["read_file"].update(
    public_arguments=_public_arguments_read_file, public_result=_public_result_file,
    model_projection=_model_projection_read_file, covers=_covering_read_file,
    evidence_selector=_evidence_selector_file,
    resource_failure=_resource_failure_by_path("read_file"),
)
CAPABILITIES["list_tree"].update(
    public_arguments=_public_arguments_keys("source", "limit", "depth", "filter"), public_result=_public_result_tree,
)
CAPABILITIES["search_code"].update(
    public_arguments=_public_arguments_search, public_result=_public_result_search,
    model_projection=_model_projection_search, evidence_selector=_evidence_selector_file,
)
CAPABILITIES["find_symbol"].update(
    public_arguments=_public_arguments_keys("source", "symbol", "path"), public_result=_public_result_find_symbol,
    model_projection=_model_projection_find_symbol, evidence_selector=_evidence_selector_file,
    resource_failure=_resource_failure_by_path("find_symbol"),
)
CAPABILITIES["symbol_relations"].update(
    public_arguments=_public_arguments_keys("source", "symbol", "query", "path", "roots", "direction", "include_text_references", "max_depth", "page_size"),
    public_result=_public_result_relations, model_projection=_model_projection_relations,
    normalize=_normalize_symbol_relations_arguments, resource_failure=_resource_failure_by_path("symbol_relations"),
)
CAPABILITIES["continue_observation"].update(public_arguments=lambda arguments: {"frontier": str(arguments.get("frontier") or "")[:80]})
CAPABILITIES["calculate"].update(
    public_arguments=lambda arguments: {"expression": str(arguments.get("expression") or "")[:240]},
    public_result=_public_result_fields("result", "resultado", "exact", "expression"),
)
CAPABILITIES["count_tokens"].update(
    public_arguments=_public_arguments_keys("source", "path", "tokenizer"),
    public_result=_public_result_fields("file_count", "files", "directories", "lines", "characters", "bytes", "estimated_tokens", "tokens", "exact", "method", "characters_per_token", "languages"),
)
CAPABILITIES["project_stats"].update(
    public_arguments=_public_arguments_keys("source"),
    public_result=_public_result_fields("file_count", "files", "directories", "lines", "characters", "bytes", "estimated_tokens", "tokens", "exact", "method", "characters_per_token", "languages"),
)
CAPABILITIES["inspect_project"].update(public_arguments=_public_arguments_keys("source"), public_result=_public_result_inspect, model_projection=_model_projection_inspect)
CAPABILITIES["run_tests"].update(
    public_arguments=_public_arguments_keys("source", "scope"),
    public_result=_public_result_fields("command", "returncode", "scope", "backend", "tests_detected", "summary"),
)
CAPABILITIES["run_command"].update(public_arguments=_public_arguments_command, public_result=_public_result_command, model_projection=_model_projection_command)
CAPABILITIES["export_sandbox_zip"].update(
    public_arguments=_public_arguments_keys("filename", "archive_root", "timeout_seconds"),
    public_result=_public_result_fields("artifact", "bytes", "sha256", "sandbox_source", "real_source_modified"),
)
CAPABILITIES["promote_sandbox"].update(
    public_arguments=_public_arguments_keys("sandbox_path", "workspace_path", "mode"),
    public_result=_public_result_fields("files", "created", "replaced", "deleted", "verification_state", "sandbox_path", "workspace_path", "mode"),
    model_projection=lambda detail, ids, config: {**(detail if isinstance(detail, dict) else {}), **({"material_ids": list(ids)} if ids else {})},
)
CAPABILITIES["git_status"].update(
    public_arguments=_public_arguments_keys("source", "max_entries"),
    public_result=_public_result_git_status,
)
CAPABILITIES["git_diff"].update(public_arguments=_public_arguments_keys("source", "path", "staged", "context_lines"), public_result=_public_result_git_diff, resource_failure=_resource_failure_by_path("git_diff"))


# Workspace mutation is a provider capability, not an Agent action. The generic
# Runtime only sees confirmation=required and delegates prepare/confirm here.
CAPABILITIES["workspace_transaction"] = {
    "description": "Change the real user workspace only after preview and user confirmation; successful writes materialize the verified final artifacts for provenance.",
    "availability": "workspace",
    "produces_grounding": True,
    "effect": "mutate",
    "returns": "Files changed, checks, verified post-write Materials, rollback metadata, lasting effect.",
    "establishes": ["A confirmed successful real-workspace change lasts after the job.", "The verified final file state becomes Material that Memory may cite without copying the whole artifact into a memory node."],
    "does_not_establish": ["Correctness beyond checks that actually ran.", "Semantic knowledge; Main decides what atomic memories to derive from the artifact."],
    "caveats": ["Read existing files fresh. The user must confirm before the real workspace changes.", "After success Main receives the real post-write observation and decides what to learn from it."],
    "confirmation": "required",
    "input_schema": _workspace_transaction.schema(),
    "prepare": _workspace_transaction.prepare,
    "confirm": _workspace_transaction.confirm,
    "observe": _observe_workspace_transaction,
    "model_projection": _model_projection_workspace_transaction,
    "public_result": _public_result_workspace_transaction,
}

for _capability_entry in CAPABILITIES.values():
    # Every capability owns the same physical hook surface. Generic Runtime
    # dispatch never grows capability-name branches; unsupported hooks are
    # explicit ``None`` rather than implicit central behavior.
    _capability_entry.setdefault("signature", None)
    _capability_entry.setdefault("observe", _observe_none)
    _capability_entry.setdefault("coverage", lambda arguments, result: {})
    _capability_entry.setdefault("frontier", _frontier_passthrough)
    for _hook_name in (
        "freshness", "rehydrate", "public_arguments", "public_result",
        "model_projection", "covers", "resource_failure", "normalize", "continue",
        "evidence_selector", "freshness_arguments",
    ):
        _capability_entry.setdefault(_hook_name, None)

for _source_cached_capability in (
    "project_stats", "count_tokens", "inspect_project", "list_tree", "search_code",
    "symbol_relations", "find_symbol", "read_file",
):
    CAPABILITIES[_source_cached_capability]["freshness_token"] = _source_tree_freshness_token
    CAPABILITIES[_source_cached_capability]["freshness_arguments"] = lambda arguments: {"source": arguments.get("source")} if arguments.get("source") is not None else {}

for _execution_source_capability in ("run_command", "run_tests"):
    CAPABILITIES[_execution_source_capability]["freshness_token"] = _source_tree_freshness_token
    CAPABILITIES[_execution_source_capability]["freshness_arguments"] = lambda arguments: {"source": arguments.get("source")} if arguments.get("source") is not None else {}
for _git_capability in ("git_status", "git_diff"):
    CAPABILITIES[_git_capability]["freshness_token"] = _git_freshness_token
    CAPABILITIES[_git_capability]["freshness_arguments"] = lambda arguments: {"source": arguments.get("source")} if arguments.get("source") is not None else {}

for _file_capability in ("read_file", "search_code", "find_symbol"):
    CAPABILITIES[_file_capability]["freshness"] = _validate_file_material_freshness
    CAPABILITIES[_file_capability]["rehydrate"] = _rehydrate_file_material

# Projection paging is provider-owned and reachable; no cognitive ceiling is
# exported as a capability limit.
for _entrada_tool in CAPABILITIES.values():
    _entrada_tool.setdefault("limits", {})
    _entrada_tool["effect"] = normalize_effect(_entrada_tool.get("effect"))
CAPABILITIES["workspace_transaction"]["ecc_name"] = "transaction"


def _provider_available(name, spec, ctx):
    """Provider-owned physical availability; Core never interprets domain labels."""
    availability = str((spec or {}).get("availability") or "workspace")
    project = _standard_context(ctx)
    config = (ctx or {}).get("config") or {}
    workspace_root = project.get("caminho_origem")
    eyle_root = project.get("eyle_root")
    workspace_available = bool(workspace_root and os.path.isdir(workspace_root))
    eyle_available = bool(eyle_root and os.path.isdir(eyle_root))
    if availability == "global":
        return True
    if availability == "workspace":
        return workspace_available
    if availability == "source":
        return workspace_available or eyle_available
    if availability in {"tests", "tests_source"}:
        tests_enabled = bool(_standard_tests_config(config).get("enabled", False))
        return tests_enabled and (workspace_available if availability == "tests" else (workspace_available or eyle_available))
    return False


def _provider_description(ctx):
    project = _standard_context(ctx)
    workspace = project.get("caminho_origem")
    eyle_root = project.get("eyle_root")
    return {
        "connected": True,
        "resources": {
            "workspace": {
                "available": bool(workspace and os.path.isdir(workspace)),
                "kind": "user_workspace",
                "access": "provider_managed",
                "content_state": project.get("content_state"),
            },
            "eyle_source": {
                "available": bool(eyle_root and os.path.isdir(eyle_root)),
                "kind": "running_eyle_source",
                "access": "read_only_or_isolated_snapshot",
            },
        },
    }


def _provider_rehydrate(materials, ctx):
    project = _standard_context(ctx)
    roots = {"workspace": project.get("caminho_origem"), "eyle": project.get("eyle_root")}
    config = (ctx or {}).get("config") or {}
    capability_rehydrate_materials(materials, roots, max_lines=_FILE_PAGE_LINES)


_STANDARD_CONFIG_FIELDS = {"sandbox", "tests"}
_STANDARD_TEST_FIELDS = {"enabled", "command_python", "command_node", "timeout_seconds", "sandbox"}
_STANDARD_SANDBOX_FIELDS = {
    "backend", "bloquear_rede", "comandos_permitidos", "cpu_segundos", "memoria_mb",
    "max_processos", "max_arquivos_abertos", "max_saida_kb", "max_arquivo_mb",
    "copiar_projeto", "max_arquivos_projeto", "max_tamanho_projeto_mb", "cpus",
    "allow_trusted_local", "timeout_segundos", "imagem_oci",
}
_STANDARD_SANDBOX_BACKENDS = {"auto", "microsandbox", "docker", "bwrap", "process", "trusted_local"}

def _reject_provider_unknown(container, allowed, prefix):
    unknown = sorted(set(container) - set(allowed))
    if unknown:
        raise ValueError(f"STANDARD_PROVIDER_CONFIG_UNKNOWN:{prefix}:" + ",".join(unknown))

def _validate_provider_sandbox(value, prefix):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"STANDARD_PROVIDER_CONFIG_INVALID:{prefix}:object_required")
    _reject_provider_unknown(value, _STANDARD_SANDBOX_FIELDS, prefix)
    backend = value.get("backend", "auto")
    if not isinstance(backend, str) or backend not in _STANDARD_SANDBOX_BACKENDS:
        raise ValueError(f"STANDARD_PROVIDER_CONFIG_INVALID:{prefix}.backend")

def _validate_provider_config(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("STANDARD_PROVIDER_CONFIG_INVALID:object_required")
    _reject_provider_unknown(value, _STANDARD_CONFIG_FIELDS, "standard")
    _validate_provider_sandbox(value.get("sandbox") or {}, "standard.sandbox")
    tests = value.get("tests") or {}
    if not isinstance(tests, dict):
        raise ValueError("STANDARD_PROVIDER_CONFIG_INVALID:standard.tests")
    _reject_provider_unknown(tests, _STANDARD_TEST_FIELDS, "standard.tests")
    if "enabled" in tests and not isinstance(tests.get("enabled"), bool):
        raise ValueError("STANDARD_PROVIDER_CONFIG_INVALID:standard.tests.enabled")
    timeout = tests.get("timeout_seconds", 60)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ValueError("STANDARD_PROVIDER_CONFIG_INVALID:standard.tests.timeout_seconds")
    for key in ("command_python", "command_node"):
        if key in tests and not isinstance(tests.get(key), str):
            raise ValueError(f"STANDARD_PROVIDER_CONFIG_INVALID:standard.tests.{key}")
    _validate_provider_sandbox(tests.get("sandbox") or {}, "standard.tests.sandbox")


def get_provider():
    return Provider(
        provider_id="standard", capabilities=CAPABILITIES, available=_provider_available,
        describe=_provider_description, rehydrate=_provider_rehydrate,
        validate_config=_validate_provider_config,
        ecc_guidance=(
            "Use source=workspace for the user's files. Use source=eyle only when the user is asking about Eyle itself, "
            "or when a capability contract remains physically inconsistent after using its explicit error/schema; do not inspect Eyle internals as the first response to an ordinary tool error.",
            "For substantial file/project work, prefer preparing, running, testing, and fixing the candidate inside the persistent run_command sandbox. "
            "When that exact sandbox candidate is ready, use promote_sandbox to stage the file or subtree and request one user confirmation to promote it into the real workspace; do not reconstruct a large tested project as hundreds of textual workspace_transaction patches.",
            "promote_sandbox mode=merge creates/replaces only the staged files and is the default safe promotion. Use mode=mirror only when the intended target must exactly match the staged subtree because mirror may delete target files absent from the snapshot.",
        ),
    )
