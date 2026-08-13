#!/usr/bin/env python3
"""Executable tool registry for the LLM-first core.

The model chooses tools; this module validates arguments, executes live workspace
operations, and always returns one standard result envelope. It contains no
semantic routing or alternate reasoning path. READ/EXEC tools run directly.
WRITE tools are invoked by the runtime only after a successful dry-run and an
explicit user confirmation.

``ctx`` supplies the validated config and the live project root. Indexed retrieval is not required.
"""
import copy
import json
import os
import re
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from eyle.core.workspace_io import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)
from eyle.core.editing import (  # noqa: E402
    localizar_simbolo,
    localizar_simbolo_no_projeto,
    rodar_testes_projeto,
)
from eyle.core.memory import search_memory, store_memory  # noqa: E402
from eyle.core.project_inspection import (  # noqa: E402
    calculate as calculate_expression,
    count_tokens as count_project_tokens,
    inspect_project as inspect_project_signals,
    project_stats as measure_project_stats,
)
from eyle.core.git_tools import git_status as inspect_git_status, git_diff as inspect_git_diff  # noqa: E402
from eyle.core.code_relations import analyze_symbol_relations  # noqa: E402
from eyle.core.text_hash import hash_texto  # noqa: E402
from eyle.core.observation_contract import (  # noqa: E402
    CoverageContractError, materialize_snapshot_handle, normalize_coverage, normalize_effect, register_snapshot_handle, result_observation_fields,
)
from eyle.core.observation import resolve_frontier, consume_frontier  # noqa: E402
from eyle.core.sandbox import executar_comando_livre_no_sandbox, export_active_sandbox_zip, ErroSandbox  # noqa: E402
from eyle.core.workspace_policy import (  # noqa: E402
    build_protected_resource_index, is_protected_workspace_resource, protected_resource_info,
)
from eyle.core.objective_scope import (  # noqa: E402
    ObjectiveScopeError, normalize_scope_selectors, resolve_objective_file_scope,
)

PROJECT_BASE_DIR = os.path.dirname(BASE_DIR)
MEMORY_DIR = os.path.join(PROJECT_BASE_DIR, "memory")

_CAMPOS_RESULTADO = ("status", "ok", "executed", "changed", "error_code", "detail", "retryable", "failure_scope", "failure_resource", "observations", "coverage", "frontiers")


def _resultado(status, ok, executed, changed=False, error_code=None, detail=None, retryable=None,
               failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None):
    """Canonical current tool result envelope.

    The physical status fields remain mandatory. Objective observation fields are
    always present but may be empty, so every capability shares one Runtime
    contract without forcing domain-specific payloads onto simple tools.
    """
    observation_fields = result_observation_fields(
        observations=observations, coverage=coverage, frontiers=frontiers,
    )
    return {
        "status": status, "ok": bool(ok), "executed": bool(executed),
        "changed": bool(changed), "error_code": error_code, "detail": detail,
        "retryable": None if retryable is None else bool(retryable),
        "failure_scope": str(failure_scope) if failure_scope else None,
        "failure_resource": str(failure_resource) if failure_resource else None,
        **observation_fields,
    }


def _sucesso(detail=None, changed=False, *, observations=None, coverage=None, frontiers=None):
    if isinstance(detail, dict):
        if observations is None: observations = detail.get("observations")
        if coverage is None: coverage = detail.get("coverage")
        if frontiers is None: frontiers = detail.get("frontiers")
    return _resultado(
        "success", True, True, changed=changed, detail=detail,
        observations=observations, coverage=coverage, frontiers=frontiers,
    )


def _falha(error_code, detail, executed=False, changed=False, retryable=None, *, failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None):
    return _resultado(
        "failed", False, executed, changed=changed,
        error_code=error_code, detail=detail, retryable=retryable,
        failure_scope=failure_scope, failure_resource=failure_resource,
        observations=observations, coverage=coverage, frontiers=frontiers,
    )


def _pulado(detail, error_code=None):
    return _resultado("skipped", True, False, error_code=error_code, detail=detail)


def _caminho_projeto(ctx):
    """Return the dedicated user-workspace root. Writes always use this root."""
    projeto = (ctx or {}).get("projeto") or {}
    return projeto.get("caminho_origem")


def _source_name(arguments):
    raw = str((arguments or {}).get("source") or "workspace").strip().lower()
    return raw if raw in {"workspace", "eyle"} else "workspace"


def _caminho_fonte(ctx, arguments):
    """Resolve an observation/sandbox source without granting real self-write authority."""
    projeto = (ctx or {}).get("projeto") or {}
    source = _source_name(arguments)
    if source == "eyle":
        root = projeto.get("eyle_root")
    else:
        root = projeto.get("caminho_origem")
    return os.path.realpath(root) if root and os.path.isdir(root) else None


def _self_runtime_path_blocked(arguments, relative_path):
    """Keep Eyle self-analysis on source/config, not live user/runtime state."""
    if _source_name(arguments) != "eyle":
        return False
    normalized = str(relative_path or "").replace("\\", "/").strip("/").lower()
    if not normalized:
        return False
    first = normalized.split("/", 1)[0]
    return first in {"workspace", "memory", "context", "agent_memory", ".git"}


def _protected_resource_failure(root, relative_path, *, executed=True):
    info = protected_resource_info(root, relative_path, index=build_protected_resource_index(root))
    return _falha(
        "PROTECTED_RESOURCE_READ_BLOCKED",
        "content access is restricted for this protected resource",
        executed=executed, retryable=False, failure_scope="resource",
        failure_resource=str(relative_path or "").replace("\\", "/"),
    )



# ---------------------------------------------------------------------------
# Tools READ
# ---------------------------------------------------------------------------

def _parse_rg_json(stdout):
    parsed = []
    for row in str(stdout or "").splitlines():
        try:
            event = json.loads(row)
        except (TypeError, json.JSONDecodeError):
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_data = data.get("path") or {}
        path = path_data.get("text") if isinstance(path_data, dict) else None
        line = data.get("line_number")
        if not path or not isinstance(line, int):
            continue
        submatches = data.get("submatches") or []
        column = None
        if submatches and isinstance(submatches[0], dict):
            start = submatches[0].get("start")
            if isinstance(start, int):
                column = start + 1
        rel = str(path).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        parsed.append({"file": rel, "linha": line, "coluna": column})
    return parsed


_SEARCH_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

_CODE_SEARCH_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt",
    ".sql", ".html", ".css", ".sh", ".bat",
}


def _search_match_priority(item):
    path = str(item.get("file") or "").replace("\\", "/").lower()
    parts = set(path.split("/"))
    extension = os.path.splitext(path)[1]
    is_code = extension in _CODE_SEARCH_EXTENSIONS
    if "tests" in parts or path.startswith("test_") or "/test_" in path:
        group = 3
    elif "devtools" in parts or "benchmarks" in parts or "examples" in parts:
        group = 2
    elif not is_code:
        group = 1
    else:
        group = 0
    return (group, path, int(item.get("linha") or 0), int(item.get("coluna") or 0))


def _normalize_search_selectors(value):
    return normalize_scope_selectors(value)


def _search_capability_universe(root):
    """Return the physical file universe owned by literal code search.

    These exclusions are capability/operational boundaries, not semantic
    relevance choices. Objective Scope is resolved over this universe before
    protected-content access is applied.
    """
    ignored_dirs = set(_SEARCH_IGNORED_DIRS)
    files = []
    ignored_counts = {name: 0 for name in sorted(ignored_dirs)}
    for current, dirs, names in os.walk(root, followlinks=False):
        kept = []
        for name in sorted(dirs):
            if name in ignored_dirs:
                ignored_counts[name] += 1
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = os.path.join(current, name)
            if not os.path.isfile(path):
                continue
            files.append(os.path.relpath(path, root).replace("\\", "/"))
    return sorted(files), {key: value for key, value in ignored_counts.items() if value}


def _searchable_files(root, *, include_paths=None, exclude_paths=None):
    """Resolve Objective Scope first, then apply the protected-content boundary."""
    universe, ignored = _search_capability_universe(root)
    include = _normalize_search_selectors(include_paths)
    for selector in include:
        parts = [part for part in selector.replace("\\", "/").split("/") if part]
        blocked = next((part for part in parts if part in _SEARCH_IGNORED_DIRS), None)
        if blocked is not None:
            raise ObjectiveScopeError(
                "SEARCH_SCOPE_OUTSIDE_CAPABILITY_BOUNDARY",
                f"objective search include selector targets capability-excluded directory '{blocked}': {selector}",
                selector=selector,
            )
    scoped_files, scope = resolve_objective_file_scope(
        root, universe, include_paths=include, exclude_paths=exclude_paths,
    )
    protected_index = build_protected_resource_index(root)
    readable = []
    protected = 0
    for rel in scoped_files:
        if is_protected_workspace_resource(root, rel, index=protected_index):
            protected += 1
            continue
        readable.append(rel)
    scope = dict(scope)
    scope["files_scanned"] = len(readable)
    scope["protected_files"] = int(protected)
    scope["capability_policy_excluded_directories"] = ignored
    return readable, protected, scope


def _canonicalize_search_matches(items, *, root, query):
    """Return the complete canonical match universe for one literal query.

    Backend differences are normalized before any model-facing view.  No
    semantic relevance ranking occurs here.  The Runtime may later group and
    bound the materialization, but the physical match universe is not truncated
    before diversity/coverage are computed.
    """
    unique = {}
    line_cache = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("file") or "").replace("\\", "/")
        line = item.get("linha")
        if not path or not isinstance(line, int):
            continue
        if path not in line_cache:
            try:
                with open(os.path.join(root, *path.split("/")), "r", encoding="utf-8", errors="replace") as fh:
                    line_cache[path] = fh.read().splitlines()
            except OSError:
                line_cache[path] = []
        source_lines = line_cache[path]
        if line < 1 or line > len(source_lines):
            continue
        column = source_lines[line - 1].find(query) + 1
        if column <= 0:
            continue
        key = (path, line, column)
        unique[key] = {"file": path, "linha": line, "coluna": column}
    return sorted(unique.values(), key=_search_match_priority)


def _run_rg_json_files(root, query, files):
    """Search the exact canonical file universe with ripgrep in bounded batches."""
    matches = []
    batch_size = 120
    for offset in range(0, len(files), batch_size):
        batch = files[offset:offset + batch_size]
        if not batch:
            continue
        command = [
            "rg", "--json", "--fixed-strings", "--color", "never",
            "--text", "--no-config", "--no-ignore", "--hidden", "--", query, *batch,
        ]
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=20, check=False,
        )
        if completed.returncode not in {0, 1}:
            raise OSError(f"ripgrep failed with exit code {completed.returncode}")
        matches.extend(_parse_rg_json(completed.stdout))
    return matches


def _search_matches_with_rg(root, query, files):
    return _canonicalize_search_matches(_run_rg_json_files(root, query, files), root=root, query=query)


def _search_matches_fallback(root, query, files):
    """Portable matcher with the same complete universe/order as ripgrep."""
    raw = []
    for rel in files:
        path = os.path.join(root, *rel.split("/"))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for number, line in enumerate(fh, 1):
                    if query in line:
                        raw.append({"file": rel, "linha": number, "coluna": line.find(query) + 1})
        except OSError:
            continue
    return _canonicalize_search_matches(raw, root=root, query=query)


def _group_all_search_ranges(raw_matches, max_lines):
    """Group the complete physical match universe into deterministic source ranges."""
    by_file = {}
    file_order = []
    for item in raw_matches:
        path = str(item.get("file") or "")
        line = item.get("linha")
        if not path or not isinstance(line, int):
            continue
        if path not in by_file:
            by_file[path] = set()
            file_order.append(path)
        by_file[path].add(line)

    grouped_by_file = {}
    total_ranges = 0
    for path in file_order:
        file_ranges = []
        current = None
        for line in sorted(by_file[path]):
            start, end = max(1, line - 3), line + 3
            if current is None:
                current = {"file": path, "line_start": start, "line_end": end, "match_lines": [line]}
                continue
            merged_end = max(current["line_end"], end)
            overlaps = start <= current["line_end"] + 1
            fits = merged_end - current["line_start"] + 1 <= max_lines
            if overlaps and fits:
                current["line_end"] = merged_end
                current["match_lines"].append(line)
            else:
                file_ranges.append(current)
                current = {"file": path, "line_start": start, "line_end": end, "match_lines": [line]}
        if current is not None:
            file_ranges.append(current)
        grouped_by_file[path] = file_ranges
        total_ranges += len(file_ranges)
    return grouped_by_file, file_order, total_ranges


def _diverse_search_materialization(grouped_by_file, file_order, max_ranges):
    """Project ranges round-robin across files without semantic ranking."""
    selected = []
    selected_keys = set()
    depth = 0
    while len(selected) < max_ranges:
        added = False
        for path in file_order:
            ranges = grouped_by_file.get(path) or []
            if depth < len(ranges):
                item = dict(ranges[depth])
                selected.append(item)
                selected_keys.add((path, item["line_start"], item["line_end"]))
                added = True
                if len(selected) >= max_ranges:
                    break
        if not added:
            break
        depth += 1
    remaining = []
    for path in file_order:
        for item in grouped_by_file.get(path) or []:
            key = (path, item["line_start"], item["line_end"])
            if key not in selected_keys:
                remaining.append(dict(item))
    return selected, remaining


def _bound_projected_match_lines(ranges, max_matches):
    """Bound only model-facing match coordinates after diversity is established."""
    remaining = max(1, int(max_matches or 1))
    result = []
    # First preserve at least one coordinate per selected range when possible.
    for item in ranges:
        if remaining <= 0:
            break
        clone = dict(item)
        lines = list(clone.get("match_lines") or [])
        clone["match_lines"] = lines[:1]
        if clone["match_lines"]:
            remaining -= 1
        result.append(clone)
    # Then fill additional coordinates in round-robin order.
    depth = 1
    while remaining > 0:
        added = False
        for index, original in enumerate(ranges[:len(result)]):
            lines = list(original.get("match_lines") or [])
            if depth < len(lines):
                result[index]["match_lines"].append(lines[depth])
                remaining -= 1
                added = True
                if remaining <= 0:
                    break
        if not added:
            break
        depth += 1
    return result


def _tool_search_code(arguments, ctx):
    """Exhaust one literal search objectively, then materialize a diverse bounded materialization."""
    query = arguments["query"].strip()
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    config = (ctx or {}).get("config") or {}
    agent_cfg = config.get("agent", {})
    max_lines = max(7, int(agent_cfg.get("max_search_range_lines", 16) or 16))
    max_matches = max(1, int(agent_cfg.get("max_search_matches", 40) or 40))
    max_ranges = max(1, int(agent_cfg.get("max_search_ranges", 12) or 12))

    include_paths = _normalize_search_selectors(arguments.get("include_paths"))
    exclude_paths = _normalize_search_selectors(arguments.get("exclude_paths"))
    try:
        searchable_files, protected_resources, search_scope = _searchable_files(
            root, include_paths=include_paths, exclude_paths=exclude_paths,
        )
        if _source_name(arguments) == "eyle":
            before = len(searchable_files)
            searchable_files = [
                rel for rel in searchable_files
                if rel.split("/", 1)[0].lower() not in {"workspace", "memory", "context", "agent_memory", ".git"}
            ]
            search_scope = dict(search_scope)
            search_scope["self_runtime_files_excluded"] = before - len(searchable_files)
            search_scope["files_scanned"] = len(searchable_files)
    except ObjectiveScopeError as error:
        return _falha(
            error.code, error.detail, executed=False, retryable=False,
            failure_scope="request", failure_resource=error.selector,
        )
    try:
        raw_matches = _search_matches_with_rg(root, query, searchable_files)
        backend = "ripgrep-json"
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        raw_matches = _search_matches_fallback(root, query, searchable_files)
        backend = "python-fallback"

    matches_observed = len(raw_matches)
    grouped_by_file, file_order, ranges_observed = _group_all_search_ranges(raw_matches, max_lines)
    selected_ranges, remaining_ranges = _diverse_search_materialization(grouped_by_file, file_order, max_ranges)
    selected_ranges = _bound_projected_match_lines(selected_ranges, max_matches)

    results = []
    read_failures = []
    for item in selected_ranges:
        try:
            reading = ler_faixa_projeto(
                root, item["file"], item["line_start"], item["line_end"],
                max_linhas=max_lines,
            )
        except ErroLeituraProjeto as error:
            read_failures.append({"file": item["file"], "error_code": error.error_code})
            continue
        reading = dict(reading)
        reading["match_lines"] = [
            line for line in item["match_lines"]
            if reading["line_start"] <= line <= reading["line_end"]
        ]
        results.append(reading)

    frontiers = []
    ledger = (ctx or {}).get("observation_ledger")
    handle_store = ledger.setdefault("handles", {}) if isinstance(ledger, dict) else None
    if remaining_ranges:
        payload = {
            "query": query,
            "items": remaining_ranges,
            "kind": "search_range_locator",
            "source": _source_name(arguments),
        }
        if isinstance(handle_store, dict):
            handle = register_snapshot_handle(
                ledger,
                kind="search_code.ranges",
                payload=payload,
                workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
                source_tool="search_code",
                description=f"Remaining objective source ranges for literal search {query!r}",
                page_size=max_ranges,
            )
            frontiers.append({
                "kind": "material_continuation",
                "at": "workspace_search",
                "count": len(remaining_ranges),
                "reason": "additional objectively matched source ranges remain behind a continuation handle",
                "handle": handle["id"],
            })
        else:
            frontiers.append({
                "kind": "material_boundary",
                "at": "workspace_search",
                "count": len(remaining_ranges),
                "reason": "additional objectively matched source ranges were not materialized",
            })

    if protected_resources:
        frontiers.append({
            "kind": "protected_resource_boundary", "at": "workspace_search",
            "count": int(protected_resources),
            "reason": "protected resources were excluded from content search",
        })
    if read_failures:
        frontiers.append({
            "kind": "read_failure_boundary", "at": "workspace_search",
            "count": len(read_failures),
            "reason": "one or more readable candidates could not be materialized",
        })

    # Search execution is complete over the declared readable file universe even
    # when only a bounded materialization is materialized to the Main LLM.
    scope_complete = not read_failures
    coverage_complete = scope_complete and protected_resources == 0
    files = sorted({item.get("file") for item in results if item.get("file")})
    match_lines_by_file = {}
    for match in raw_matches:
        path = str(match.get("file") or "")
        line = match.get("linha")
        if path and isinstance(line, int):
            match_lines_by_file.setdefault(path, set()).add(line)
    file_match_counts = [
        {"file": path, "matches": len(match_lines_by_file.get(path) or ())}
        for path in file_order
    ]
    # File counts are objective navigation metadata. Bound the inline list and
    # preserve the complete range universe behind the continuation handle.
    distribution = file_match_counts[:max(12, max_ranges)]
    detail = {
        "query": query,
        "search_scope": search_scope,
        "results": results,
        "materialized_files": files,
        "matches_observed": matches_observed,
        "matches_materialized": sum(len(item.get("match_lines") or []) for item in results),
        "files_with_matches": len(file_order),
        "file_match_distribution": distribution,
        "distribution_truncated": len(distribution) < len(file_match_counts),
        "ranges_observed": ranges_observed,
        "ranges_materialized": len(results),
        "scope_complete": scope_complete,
        "coverage_complete": coverage_complete,
        "coverage_scope": (
            ("declared_search_scope" if protected_resources == 0 else "readable_declared_search_scope")
            if (include_paths or exclude_paths)
            else ("all_workspace_files" if protected_resources == 0 else "readable_workspace_files")
        ),
        "protected_resources_excluded": protected_resources,
        "backend": backend,
        "read_failures": read_failures,
        "frontiers": frontiers,
    }
    return _sucesso(detail, frontiers=frontiers)

def _tool_symbol_relations(arguments, ctx):
    """Return local relations or a query-shaped structural reachability observation."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    path = arguments.get("path")
    if path and _self_runtime_path_blocked(arguments, path):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(path))
    if path and is_protected_workspace_resource(root, str(path), index=build_protected_resource_index(root)):
        return _protected_resource_failure(root, str(path))
    config = (ctx or {}).get("config") or {}
    agent_cfg = config.get("agent") or {}
    query = str(arguments.get("query") or "relations")
    # Reachability depth is Runtime-owned in Eyle 2.7.5 Rev1.3.4. The resolved graph is
    # exhausted mechanically; only local relation queries honor max_depth.
    default_depth = 6
    try:
        detail = analyze_symbol_relations(
            root, arguments["symbol"], path=path, roots=list(arguments.get("roots") or []),
            direction=str(arguments.get("direction") or "both"),
            include_text_references=bool(arguments.get("include_text_references", False)),
            max_depth=int(arguments.get("max_depth") or default_depth),
            max_edges=int(arguments.get("max_edges") or 60),
            max_files=max(1, int(agent_cfg.get("max_project_scan_entries", 20000) or 20000)),
            max_file_bytes=max(1024, int(agent_cfg.get("max_project_file_bytes", 4 * 1024 * 1024) or 4 * 1024 * 1024)),
            query=query,
        )
        ledger = (ctx or {}).get("observation_ledger")
        handle_store = ledger.setdefault("handles", {}) if isinstance(ledger, dict) else None
        payloads = list(detail.pop("continuation_payloads", []) or [])
        if isinstance(handle_store, dict):
            for index, payload in enumerate(payloads):
                if not isinstance(payload, dict):
                    continue
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                handle = register_snapshot_handle(
                    ledger, kind=f"symbol_relations.{payload.get('frontier_kind') or 'continuation'}",
                    payload=payload, workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
                    source_tool="symbol_relations",
                    description=f"Continuation from symbol_relations for {arguments.get('symbol')}",
                    page_size=12,
                )
                for frontier in detail.get("frontiers") or []:
                    if isinstance(frontier, dict) and frontier.get("continuation_index") == index:
                        frontier.pop("continuation_index", None)
                        frontier["handle"] = handle["id"]
                        if summary.get("count") is not None:
                            frontier.setdefault("count", summary.get("count"))
        else:
            for frontier in detail.get("frontiers") or []:
                if isinstance(frontier, dict): frontier.pop("continuation_index", None)
        return _sucesso(detail, frontiers=detail.get("frontiers"))
    except (OSError, ValueError) as error:
        return _falha("RELATION_SCAN_FAILED", str(error), executed=True)



def _continue_search_code_page(payload, ctx):
    """Materialize search-range locators owned by search_code into file Material candidates."""
    if not isinstance(payload, dict) or payload.get("kind") != "search_range_locator":
        return {}
    source = str(payload.get("source") or "workspace")
    root = _caminho_fonte(ctx, {"source": source})
    if not root:
        return {}
    query = str(payload.get("query") or "")
    cfg = ((ctx or {}).get("config") or {}).get("agent", {})
    max_lines = max(7, int(cfg.get("max_search_range_lines", 16) or 16))
    material_candidates = []
    projected = []
    failures = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            reading = ler_faixa_projeto(
                root, str(item.get("file") or ""), int(item.get("line_start") or 1),
                int(item.get("line_end") or int(item.get("line_start") or 1)), max_linhas=max_lines,
            )
        except (ErroLeituraProjeto, TypeError, ValueError) as error:
            failures.append({"file": item.get("file"), "error_code": getattr(error, "error_code", "READ_FAILED")})
            continue
        reading = dict(reading)
        reading["match_lines"] = [
            line for line in (item.get("match_lines") or [])
            if reading.get("line_start", 1) <= line <= reading.get("line_end", 0)
        ]
        material = _file_material(reading, source_type="search_code", source=source)
        if material:
            material["query"] = query
            material["source_capability"] = "search_code"
            material_candidates.append(material)
        projected.append(reading)
    coverage = _coverage_record(
        scope={"kind": "search_range_materialization", "query": query, "source": source},
        examined={"ranges_attempted": len(payload.get("items") or []), "ranges_materialized": len(projected)},
        complete=not bool(failures),
        boundaries=[{"kind": "read_failure", "count": len(failures)}] if failures else [],
        facts={"source_materialization_complete": not bool(failures)},
    )
    return {
        "observations": material_candidates, "coverage": coverage,
        "detail": {"source_capability": "search_code", "query": query, "results": projected, "read_failures": failures},
    }


def _continue_find_symbol_page(payload, ctx):
    """Materialize bounded symbol-location facts from the retained snapshot."""
    if not isinstance(payload, dict) or payload.get("kind") != "find_symbol_locator":
        return {}
    source = str(payload.get("source") or "workspace")
    items = [copy.deepcopy(item) for item in (payload.get("items") or []) if isinstance(item, dict)]
    materials = []
    for item in items:
        content = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        materials.append({
            "locator": {
                "kind": "symbol_location", "source": source, "symbol": payload.get("symbol"),
                "path": item.get("file"), "line_start": item.get("line_start"), "line_end": item.get("line_end"),
            },
            "content_hash": hash_texto(content), "content": content,
            "source_type": "find_symbol", "source_capability": "find_symbol",
        })
    coverage = _coverage_record(
        scope={"kind": "symbol_location_materialization", "source": source, "symbol": payload.get("symbol")},
        examined={"locations": len(items), "materialized": len(materials)},
        complete=True,
    )
    return {
        "observations": materials,
        "coverage": coverage,
        "detail": {"source_capability": "find_symbol", "symbol": payload.get("symbol"), "matches": items},
    }


def _continue_structured_page(payload, ctx):
    """Materialize structured continuation items without teaching Observation their semantics."""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = [copy.deepcopy(item) for item in payload.get("items") or [] if isinstance(item, dict)]
        kind = str(payload.get("frontier_kind") or payload.get("kind") or "structured_continuation")
        materials = []
        for index, item in enumerate(items):
            content = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            materials.append({
                "locator": {"kind": "capability", "name": "symbol_relations", "continuation_kind": kind, "index": index},
                "content_hash": hash_texto(content), "content": content,
                "source_type": "symbol_relations", "source_capability": "symbol_relations",
            })
        return {
            "observations": materials,
            "coverage": _coverage_record(
                scope={"kind": "structured_materialization", "source_capability": "symbol_relations", "continuation_kind": kind},
                examined={"items": len(items), "materialized": len(materials)}, complete=True,
            ),
            "detail": {"source_capability": "symbol_relations", "kind": kind, "items": items},
        }
    return {}


def _continue_source_projection(source_tool, payload, ctx):
    entry = TOOLS.get(str(source_tool or "")) or {}
    continuation = entry.get("continue")
    return continuation(payload, ctx or {}) if callable(continuation) else {}

def _tool_continue_observation(arguments, ctx):
    """Continue one Main-visible Frontier while keeping snapshots/handles Runtime-private."""
    ledger = (ctx or {}).get("observation_ledger")
    if not isinstance(ledger, dict):
        return _falha("OBSERVATION_STATE_UNAVAILABLE", "observation state unavailable", executed=False, retryable=False)
    frontier_id = str(arguments.get("frontier") or "")
    handle_id, frontier_error = resolve_frontier(
        ledger, frontier_id, workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
    )
    if frontier_error:
        return _falha(frontier_error, "use an open fr-* Frontier returned by Observation", executed=False, retryable=True)
    if not isinstance(ledger.get("handles"), dict):
        return _falha("HANDLE_STORE_UNAVAILABLE", "internal continuation store unavailable", executed=False, retryable=False)
    materialized, error = materialize_snapshot_handle(
        ledger, str(handle_id or ""), workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
    )
    if error:
        return _falha(error, "the Runtime continuation behind this Frontier is unavailable", executed=True, retryable=True)

    payload = materialized.get("payload") if isinstance(materialized, dict) else None
    source_tool = str((materialized or {}).get("source_tool") or "")
    projection = _continue_source_projection(source_tool, payload, ctx)
    observations = [copy.deepcopy(item) for item in (projection.get("observations") or []) if isinstance(item, dict)] if isinstance(projection, dict) else []
    if not observations:
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            observations = [copy.deepcopy(item) for item in payload.get("items") or [] if isinstance(item, dict)]
        elif isinstance(payload, list):
            observations = [copy.deepcopy(item) for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            observations = [copy.deepcopy(payload)]

    consume_frontier(ledger, frontier_id)
    detail = dict(materialized or {})
    detail.pop("handle", None)
    detail.pop("payload", None)
    detail["continued_frontier"] = frontier_id
    detail["source_capability"] = source_tool
    if isinstance(projection, dict) and isinstance(projection.get("detail"), dict):
        detail["materialized"] = copy.deepcopy(projection.get("detail"))
    else:
        detail["materialized"] = copy.deepcopy(payload)
    detail["observations"] = observations

    snapshot_coverage = normalize_coverage((materialized or {}).get("coverage"), allow_empty=False)
    projection_coverage = normalize_coverage(
        projection.get("coverage") if isinstance(projection, dict) else {}, allow_empty=True,
    )
    snapshot_facts = snapshot_coverage.get("facts") if isinstance(snapshot_coverage.get("facts"), dict) else {}
    snapshot_exhausted = bool(snapshot_facts.get("snapshot_exhausted", snapshot_coverage.get("complete")))
    source_complete = bool(projection_coverage.get("complete")) if projection_coverage else True
    combined_boundaries = list(snapshot_coverage.get("boundaries") or [])
    if projection_coverage:
        combined_boundaries.extend(copy.deepcopy(projection_coverage.get("boundaries") or []))
    combined_coverage = _coverage_record(
        scope={"kind": "frontier_continuation", "frontier": frontier_id, "source_capability": source_tool},
        examined={
            **copy.deepcopy(snapshot_coverage.get("examined") or {}),
            **({f"source_{key}": copy.deepcopy(value) for key, value in (projection_coverage.get("examined") or {}).items()} if projection_coverage else {}),
        },
        complete=bool(snapshot_exhausted and source_complete),
        boundaries=combined_boundaries,
        facts={
            "snapshot_exhausted": snapshot_exhausted,
            "source_materialization_complete": source_complete,
            "snapshot": copy.deepcopy(snapshot_facts),
            **({"source_coverage": copy.deepcopy(projection_coverage)} if projection_coverage else {}),
        },
    )
    detail["coverage"] = combined_coverage
    return _sucesso(
        detail, observations=observations, coverage=combined_coverage,
        frontiers=detail.get("frontiers"),
    )

def _tool_find_symbol(arguments, ctx):
    """Locate a symbol while separating exhaustive scan Coverage from bounded materialization."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    symbol = arguments["symbol"]
    rel = arguments.get("path")
    if rel and _self_runtime_path_blocked(arguments, rel):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(rel))
    protected_index = build_protected_resource_index(root)
    if rel and is_protected_workspace_resource(root, str(rel), index=protected_index):
        return _protected_resource_failure(root, str(rel))

    if not rel:
        scan = localizar_simbolo_no_projeto(root, symbol, limite=None, return_metadata=True)
        all_matches = []
        for item in scan.get("all_matches") or []:
            if not isinstance(item, dict):
                continue
            # The search exhausts the workspace, but the model-facing result and
            # continuation snapshot retain only objective locators, not duplicate
            # source bodies. Source bytes can be read explicitly when needed.
            all_matches.append({
                key: copy.deepcopy(value) for key, value in item.items()
                if key not in {"codigo_original", "content", "numbered_content"}
            })
        page_size = 32
        selected = all_matches[:page_size]
        remaining = all_matches[page_size:]
        boundaries = []
        protected = int(scan.get("protected_resources_excluded") or 0)
        if protected:
            boundaries.append({"kind": "protected_resource", "count": protected})
        coverage = _coverage_record(
            scope={"kind": "symbol_lookup", "symbol": symbol, "path": None},
            examined={
                "files": int(scan.get("files_examined") or 0),
                "matches": int(scan.get("matches_observed") or len(all_matches)),
                "materialized_matches": len(selected),
            },
            complete=bool(scan.get("complete_scan")) and protected == 0,
            boundaries=boundaries,
            facts={
                "scan_complete": bool(scan.get("complete_scan")),
                "materialization_complete": not bool(remaining),
                "result_page_size": page_size,
            },
        )
        if len(all_matches) == 1:
            only = dict(all_matches[0])
            try:
                reading = ler_faixa_projeto(
                    root, str(only.get("file") or ""), int(only.get("line_start") or 1), int(only.get("line_end") or int(only.get("line_start") or 1)),
                    max_linhas=((ctx or {}).get("config") or {}).get("agent", {}).get("max_file_read_lines", 400),
                )
                only.update(reading)
            except (ErroLeituraProjeto, TypeError, ValueError):
                pass
            only.update({
                "simbolo": symbol, "matches_observed": 1, "matches_materialized": 1,
                "files_examined": int(scan.get("files_examined") or 0),
                "protected_resources_excluded": protected,
            })
            return _sucesso(only, coverage=coverage)

        if not all_matches:
            detail = {
                "symbol": symbol, "matches": [],
                "matches_observed": 0, "files_examined": int(scan.get("files_examined") or 0),
                "protected_resources_excluded": protected,
            }
            return _falha(
                "SYMBOL_NOT_FOUND", f"símbolo '{symbol}' não encontrado", executed=True,
                coverage=coverage, detail=detail,
            )

        frontiers = []
        ledger = (ctx or {}).get("observation_ledger")
        if remaining and isinstance(ledger, dict):
            handle = register_snapshot_handle(
                ledger, kind="find_symbol.matches",
                payload={"kind": "find_symbol_locator", "symbol": symbol, "items": remaining, "source": _source_name(arguments)},
                workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
                source_tool="find_symbol",
                description=f"Remaining objective locations for symbol {symbol!r}",
                page_size=page_size,
            )
            frontiers.append({
                "kind": "material_continuation", "at": "symbol_lookup",
                "count": len(remaining),
                "reason": "additional objectively located symbol definitions remain behind a continuation handle",
                "handle": handle["id"],
            })
        elif remaining:
            frontiers.append({
                "kind": "material_boundary", "at": "symbol_lookup",
                "count": len(remaining),
                "reason": "additional objectively located symbol definitions were not materialized",
            })
        detail = {
            "symbol": symbol, "matches": selected,
            "matches_observed": int(scan.get("matches_observed") or len(all_matches)),
            "matches_materialized": len(selected),
            "files_examined": int(scan.get("files_examined") or 0),
            "protected_resources_excluded": protected,
            "frontiers": frontiers,
        }
        return _sucesso(detail, coverage=coverage, frontiers=frontiers)

    result = localizar_simbolo(root, rel, symbol)
    if result is None:
        coverage = _coverage_record(
            scope={"kind": "file_symbol_lookup", "path": rel, "symbol": symbol},
            examined={"files": 1}, complete=True,
        )
        return _falha("SYMBOL_NOT_FOUND", f"símbolo '{symbol}' não encontrado", executed=True, coverage=coverage)
    result = dict(result); result["file"] = result.get("file") or rel; result["simbolo"] = symbol
    try:
        reading = ler_faixa_projeto(
            root, rel, int(result["line_start"]), int(result["line_end"]),
            max_linhas=((ctx or {}).get("config") or {}).get("agent", {}).get("max_file_read_lines", 400),
        )
        result.update(reading); result["simbolo"] = symbol
    except ErroLeituraProjeto as erro:
        if erro.error_code == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(root, str(rel or ""))
    except Exception:
        pass
    return _sucesso(result)


def _tool_read_file(arguments, ctx):
    """Read one fresh bounded file window; omitted range means the initial window."""
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    caminho_relativo = arguments["path"]
    if _self_runtime_path_blocked(arguments, caminho_relativo):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(caminho_relativo))
    config = (ctx or {}).get("config") or {}
    max_linhas = config.get("agent", {}).get("max_file_read_lines", 400)
    has_start = arguments.get("line_start") is not None
    has_end = arguments.get("line_end") is not None
    if has_start != has_end:
        return _falha("INVALID_ARGUMENT", "line_start e line_end devem ser informados juntos")
    line_start = int(arguments.get("line_start") or 1)
    line_end = int(arguments.get("line_end") or max_linhas)
    try:
        leitura = ler_faixa_projeto(
            caminho_projeto, caminho_relativo, line_start, line_end,
            max_linhas=max_linhas,
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {
            "INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE",
            "RANGE_OUT_OF_BOUNDS",
        } else erro.error_code
        if codigo == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(caminho_projeto, caminho_relativo)
        return _falha(codigo, erro.detail, executed=True)
    leitura = dict(leitura)
    leitura["truncated"] = bool(
        leitura.get("line_start", 1) > 1
        or leitura.get("line_end", 0) < leitura.get("total_lines", 0)
    )
    return _sucesso(leitura)


def _tool_list_tree(arguments, ctx):
    """Lista a arvore fresca do projeto com limites e motivos ignorados."""
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_agente = ((ctx or {}).get("config") or {}).get("agent", {})
    max_entradas = cfg_agente.get("max_tree_entries", 200)
    max_profundidade = cfg_agente.get("max_tree_depth", 6)
    limite = arguments.get("limit", max_entradas)
    profundidade = arguments.get("depth", max_profundidade)
    if limite > max_entradas:
        return _falha(
            "INVALID_ARGUMENT",
            f"limite={limite} excede agent.max_tree_entries={max_entradas}",
        )
    if profundidade > max_profundidade:
        return _falha(
            "INVALID_ARGUMENT",
            f"profundidade={profundidade} excede agent.max_tree_depth={max_profundidade}",
        )
    try:
        resultado = listar_arvore_projeto(
            caminho_projeto,
            limite=limite,
            profundidade=profundidade,
            filtro=arguments.get("filter"),
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {
            "INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE",
            "RANGE_OUT_OF_BOUNDS",
        } else erro.error_code
        return _falha(codigo, erro.detail, executed=True)
    return _sucesso(resultado)


def _tool_calculate(arguments, ctx):
    """Evaluate arithmetic deterministically instead of asking the LLM to do it mentally."""
    try:
        return _sucesso(calculate_expression(arguments["expression"]))
    except (ValueError, SyntaxError) as erro:
        return _falha("INVALID_EXPRESSION", str(erro), executed=True)


def _tool_project_stats(arguments, ctx):
    """Measure objective project size/statistics over the safe text workspace."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(measure_project_stats(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_count_tokens(arguments, ctx):
    """Measure project text and convert it to a truthful token estimate."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        detail = count_project_tokens(
            root, (ctx or {}).get("config") or {},
            path=arguments.get("path"),
            tokenizer=arguments.get("tokenizer"),
        )
        return _sucesso(detail)
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_inspect_project(arguments, ctx):
    """Return objective structural/relation signals without ranking file importance."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(inspect_project_signals(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)




def _pytest_summary(output):
    """Return the last concise pytest summary line without exposing huge logs."""
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(token in lowered for token in (" passed", " failed", " skipped", " error", " errors")):
            return line[:500]
    return lines[-1][:500] if lines else ""


def _tool_run_tests(arguments, ctx):
    """Run the real suite, optionally focused to a safe pytest file/directory."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_testes = ((ctx or {}).get("config") or {}).get("codar", {}).get("testes", {})
    if not cfg_testes.get("ativado", False):
        return _pulado(
            "A execução de testes está desativada em config['codar']['testes']['ativado'].",
            error_code="TESTS_DISABLED",
        )
    resultado = rodar_testes_projeto(caminho_projeto, cfg_testes, scope=arguments.get("scope"))
    output = str(resultado.get("saida_resumida") or "")
    detail = {
        "command": resultado.get("comando"),
        "returncode": resultado.get("codigo"),
        "scope": resultado.get("scope"),
        "backend": resultado.get("backend"),
        "runner": resultado.get("runner"),
        "tests_detected": bool(resultado.get("tests_detected")),
        "summary": _pytest_summary(output) or str(resultado.get("detalhe") or "")[:500],
        "output_tail": output[-3000:],
    }
    if resultado.get("executado") is not True and resultado.get("ok") is True:
        return _pulado(detail, error_code="TESTS_NOT_FOUND")
    if resultado.get("ok") is True:
        return _sucesso(detail)
    error_code = resultado.get("error_code") or (
        "TESTS_REFUSED" if resultado.get("recusado") else "TESTS_FAILED"
    )
    return _falha(
        error_code, detail, executed=resultado.get("executado") is True,
    )




def _tool_git_status(arguments, ctx):
    """Inspect Git working-tree state without modifying the repository."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    result = inspect_git_status(root, max_entries=int(arguments.get("max_entries") or 200))
    if result.get("ok"):
        return _sucesso(result)
    return _falha(result.get("error_code") or "GIT_STATUS_FAILED", result.get("detail"), executed=True)


def _tool_git_diff(arguments, ctx):
    """Inspect a bounded Git diff; raw diff is available to the LLM but not public history."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_agent = ((ctx or {}).get("config") or {}).get("agent", {})
    result = inspect_git_diff(
        root,
        path=arguments.get("path"),
        staged=bool(arguments.get("staged", False)),
        context_lines=int(arguments.get("context_lines") or 3),
        max_chars=int(cfg_agent.get("max_git_diff_chars", 6000) or 6000),
    )
    if result.get("ok"):
        return _sucesso(result)
    return _falha(result.get("error_code") or "GIT_DIFF_FAILED", result.get("detail"), executed=True)


def _tool_run_command(arguments, ctx):
    """Run any shell command inside the isolated, writable per-job sandbox snapshot."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    config = (ctx or {}).get("config") or {}
    sandbox_cfg = dict(((config.get("agent") or {}).get("sandbox") or {}))
    result = executar_comando_livre_no_sandbox(
        root, arguments["command"], sandbox_cfg, cwd=arguments.get("cwd") or ".",
        source_kind=_source_name(arguments),
        timeout_segundos=arguments.get("timeout_seconds"),
    )
    if result.get("executado") is not True:
        error = str(result.get("erro") or "sandbox indisponivel")
        if error.startswith("SANDBOX_SOURCE_CONFLICT"):
            return _falha("SANDBOX_SOURCE_CONFLICT", error, retryable=False, failure_scope="request")
        return _falha("SANDBOX_UNAVAILABLE", error, retryable=False)
    detail = {
        "command": arguments["command"], "source": _source_name(arguments), "cwd": result.get("cwd"), "returncode": result.get("codigo"),
        "output": str(result.get("saida") or "")[-12000:], "backend": result.get("backend"),
        "network_enabled": bool(result.get("network_enabled")),
        "workspace_isolated": bool(result.get("workspace_isolated")),
        "snapshot_persists_for_job": bool(result.get("snapshot_persists_for_job")),
        "protected_resources_omitted": int(result.get("protected_resources_omitted") or 0),
        "real_workspace_changed": False,
    }
    if result.get("ok") is True:
        return _sucesso(detail, changed=False)
    detail["error"] = result.get("erro")
    return _falha("SANDBOX_COMMAND_FAILED", detail, executed=True, changed=False)


def _tool_export_sandbox_zip(arguments, ctx):
    """Export the current isolated snapshot as one inert ZIP beside Eyle."""
    projeto = (ctx or {}).get("projeto") or {}
    eyle_root = projeto.get("eyle_root")
    if not eyle_root or not os.path.isdir(eyle_root):
        return _falha("EYLE_ROOT_UNAVAILABLE", "raiz fisica da Eyle indisponivel", retryable=False)
    try:
        detail = export_active_sandbox_zip(
            eyle_root, arguments["filename"],
            archive_root=arguments.get("archive_root"),
            timeout_seconds=int(arguments.get("timeout_seconds") or 120),
        )
    except ErroSandbox as error:
        detail = str(error)
        if detail.startswith("SANDBOX_NOT_INITIALIZED"):
            return _falha("SANDBOX_NOT_INITIALIZED", detail, retryable=False, failure_scope="request")
        if detail.startswith("ARTIFACT_ALREADY_EXISTS"):
            return _falha("ARTIFACT_ALREADY_EXISTS", detail, retryable=False, failure_scope="resource", failure_resource=arguments.get("filename"))
        return _falha("SANDBOX_EXPORT_FAILED", detail, retryable=False)
    return _sucesso(detail, changed=True)


def _tool_memory_search(arguments, ctx):
    """Search external project memory only when the agent requests it."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    query = str(arguments.get("query") or "")
    limit = int(arguments.get("limit") or 8)
    try:
        results = search_memory(MEMORY_DIR, root, query=query, limit=limit)
    except (OSError, ValueError) as error:
        return _falha("MEMORY_READ_FAILED", str(error), executed=True)
    return _sucesso({"entries": results, "count": len(results)})


def _tool_memory_store(arguments, ctx):
    """Store one observation-grounded fact outside the source workspace."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    grounding = (ctx or {}).get("grounding") or {}
    grounding_ids = [str(item) for item in arguments.get("grounding_ids") or []]
    if not grounding_ids:
        return _falha("MEMORY_REQUIRES_GROUNDING", "informe grounding_ids da tarefa atual")
    missing = [item for item in grounding_ids if item not in grounding]
    if missing:
        return _falha("MEMORY_UNKNOWN_GROUNDING", ", ".join(missing))
    files = []
    for grounding_id in grounding_ids:
        item = grounding.get(grounding_id) or {}
        locator = item.get("locator") if isinstance(item.get("locator"), dict) else {}
        if locator.get("kind") == "file" and locator.get("path") and item.get("source_version"):
            files.append({"path": locator["path"], "file_hash": item["source_version"]})
    try:
        entry = store_memory(
            MEMORY_DIR, root, str(arguments.get("text") or ""),
            kind=str(arguments.get("kind") or "fact"), files=files,
        )
    except (OSError, ValueError) as error:
        return _falha("MEMORY_WRITE_FAILED", str(error), executed=True)
    return _sucesso({"entry": entry})


# ---------------------------------------------------------------------------
# Tool WRITE -- invoked only by the core runtime after confirmation.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registry consumed by eyle.core.agent. Tool names are the public protocol.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Capability-owned observation mechanics.
#
# Observation/Agent never interpret individual tool names. Tool-specific
# identity, materialization and presentation live next to the capability
# registry that defines those tools.
# ---------------------------------------------------------------------------

def _norm_capability_path(value):
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def _sig_list_tree(arguments):
    return "tree:" + json.dumps({
        "source": _source_name(arguments),
        "filter": str(arguments.get("filter") or "").strip().lower(),
        "depth": arguments.get("depth"), "limit": arguments.get("limit"),
    }, sort_keys=True, separators=(",", ":"), default=str)


def _sig_search_code(arguments):
    return "search:" + json.dumps({
        "source": _source_name(arguments),
        "query": " ".join(str(arguments.get("query") or "").lower().split()),
        "include_paths": sorted(normalize_scope_selectors(arguments.get("include_paths"))),
        "exclude_paths": sorted(normalize_scope_selectors(arguments.get("exclude_paths"))),
    }, sort_keys=True, separators=(",", ":"), default=str)


def _sig_find_symbol(arguments):
    return f"symbol:{_source_name(arguments)}:{_norm_capability_path(arguments.get('path'))}:{str(arguments.get('symbol') or '').strip().lower()}"


def _sig_symbol_relations(arguments):
    query = str(arguments.get("query") or "relations").strip().lower()
    identity = {
        "source": _source_name(arguments),
        "symbol": str(arguments.get("symbol") or "").strip().lower(),
        "path": _norm_capability_path(arguments.get("path")),
        "roots": [str(x) for x in (arguments.get("roots") or [])],
        "include_text_references": bool(arguments.get("include_text_references", False)),
        "query": query,
    }
    if query != "reachability":
        identity.update({
            "direction": str(arguments.get("direction") or "both").strip().lower(),
            "max_depth": int(arguments.get("max_depth") or 6),
            "max_edges": int(arguments.get("max_edges") or 60),
        })
    return "relations:" + json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)


def _sig_read_file(arguments):
    source = _source_name(arguments)
    path = _norm_capability_path(arguments.get("path"))
    if arguments.get("line_start") is not None and arguments.get("line_end") is not None:
        return f"file:{source}:{path}:{arguments.get('line_start')}:{arguments.get('line_end')}"
    return f"file:{source}:{path}:default"


def _sig_count_tokens(arguments):
    return "count_tokens:" + json.dumps({
        "source": _source_name(arguments),
        "path": _norm_capability_path(arguments.get("path") or "."),
        "tokenizer": str(arguments.get("tokenizer") or "").strip().lower(),
    }, sort_keys=True, separators=(",", ":"))


def _sig_run_tests(arguments):
    return "run_tests:" + json.dumps({"scope": _norm_capability_path(arguments.get("scope") or ".")}, sort_keys=True, separators=(",", ":"))


def _sig_git_diff(arguments):
    return "git_diff:" + json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)


def capability_observation_signature(name, arguments):
    entry = TOOLS.get(str(name or "")) or {}
    fn = entry.get("signature")
    return fn(arguments or {}) if callable(fn) else None


def _file_material(detail, *, source_type, source="workspace"):
    if not isinstance(detail, dict):
        return None
    path = str(detail.get("file") or "").replace("\\", "/").strip()
    source_version = str(detail.get("file_hash") or "").strip()
    content = detail.get("content")
    numbered_content = detail.get("numbered_content")
    content_hash = str(detail.get("content_hash") or "").strip()
    if not path or not source_version or content is None:
        return None
    if not content_hash:
        content_hash = hash_texto(str(content))
    locator = {"kind": "file", "source": str(source or "workspace"), "path": path}
    for key in ("line_start", "line_end", "total_lines"):
        if detail.get(key) is not None:
            locator[key] = detail.get(key)
    metadata = {
        key: copy.deepcopy(detail.get(key)) for key in (
            "simbolo", "match_lines", "truncated", "query", "scope_complete", "coverage_complete"
        ) if detail.get(key) is not None
    }
    material = {
        "locator": locator, "source_version": source_version,
        "content_hash": content_hash, "content": str(content),
        "source_type": source_type,
    }
    if isinstance(numbered_content, str) and numbered_content:
        material["numbered_content"] = numbered_content
    if metadata:
        material["metadata"] = metadata
    return material


def _json_material(source_type, locator_name, detail, *, source="workspace"):
    if not isinstance(detail, dict):
        return []
    content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return [{
        "locator": {"kind": "capability", "name": str(locator_name), "source": str(source or "workspace")},
        "content_hash": hash_texto(content), "content": content,
        "source_type": str(source_type),
    }]


def _observe_search(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    materials = []
    for item in detail.get("results") or []:
        material = _file_material(item, source_type="search_code", source=_source_name(arguments))
        if material:
            material["query"] = detail.get("query")
            materials.append(material)
    if not materials and detail.get("scope_complete") is True:
        summary = {
            key: copy.deepcopy(detail.get(key)) for key in (
                "query", "matches_observed", "matches_materialized", "ranges_observed",
                "ranges_materialized", "files_with_matches", "scope_complete", "coverage_complete",
                "coverage_scope", "search_scope", "protected_resources_excluded", "read_failures",
                "backend",
            ) if detail.get(key) is not None
        }
        materials.extend(_json_material("search_observation", "search_code", summary, source=_source_name(arguments)))
    return materials


def _observe_file(source_type):
    def observe(arguments, result):
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        material = _file_material(detail, source_type=source_type, source=_source_name(arguments))
        return [material] if material else []
    return observe


def _observe_find_symbol(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    material = _file_material(detail, source_type="find_symbol", source=_source_name(arguments))
    if material:
        return [material]
    if result.get("ok") is True and detail:
        # Ambiguous/multi-match symbol lookup is still objective observed material;
        # keep it capability-owned instead of teaching Observation about symbols.
        return _json_material("symbol_observation", "find_symbol", detail, source=_source_name(arguments))
    if result.get("error_code") == "SYMBOL_NOT_FOUND" and result.get("executed") is True:
        payload = {
            "symbol": str(arguments.get("symbol") or ""),
            "path": arguments.get("path"),
            "error_code": "SYMBOL_NOT_FOUND", "executed": True,
        }
        return _json_material("symbol_observation", "find_symbol", payload, source=_source_name(arguments))
    return []


def _observe_tree(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    if not detail:
        return []
    inventory = {
        "entries": detail.get("entries") or [],
        "truncated": bool(detail.get("truncated")),
        "complete_scan": bool(detail.get("varredura_completa")),
        "filter": detail.get("filter"),
    }
    return _json_material("workspace_tree", "list_tree", inventory, source=_source_name(arguments))


def _observe_json(name):
    def observe(arguments, result):
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        return _json_material(name, name, detail, source=_source_name(arguments)) if detail else []
    return observe




def _observe_none(arguments, result):
    """Explicit capability-owned declaration that no Material is produced."""
    return []


def _coverage_memory_search(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    if result.get("executed") is not True:
        return {}
    limit = max(1, min(int(arguments.get("limit") or 8), 20))
    returned = int(detail.get("count") or 0)
    return _coverage_record(
        scope={"kind": "project_memory_search", "query": str(arguments.get("query") or "")},
        examined={"entries_returned": returned, "limit": limit},
        # search_memory stops when limit is reached; equality therefore cannot
        # prove exhaustion of the physical memory set. Fail conservative.
        complete=bool(result.get("ok") is True and returned < limit),
        boundaries=[{"kind": "result_limit", "limit": limit}] if returned >= limit else [],
    )


def _coverage_memory_store(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    entry = detail.get("entry") if isinstance(detail.get("entry"), dict) else {}
    if result.get("executed") is not True:
        return {}
    return _coverage_record(
        scope={"kind": "project_memory_write"},
        examined={"entries_written": 1 if entry else 0},
        complete=bool(result.get("ok") is True),
    )

def _observe_passthrough(arguments, result):
    values = result.get("observations") if isinstance(result, dict) else []
    return [copy.deepcopy(item) for item in (values or []) if isinstance(item, dict)]


def _observe_continue(arguments, result):
    # continue_observation may already contain source-capability Material
    # candidates. Preserve those instead of wrapping the whole page as one
    # synthetic blob.
    values = _observe_passthrough(arguments, result)
    if values:
        return values
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return _json_material("continue_observation", "continue_observation", detail) if detail else []


def _capability_observations(name, arguments, result):
    entry = TOOLS.get(str(name or "")) or {}
    observer = entry.get("observe")
    values = observer(arguments or {}, result or {}) if callable(observer) else []
    out = []
    for raw in values or []:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        item.setdefault("source_capability", str(name or ""))
        item.setdefault("source_type", str(name or "capability"))
        out.append(item)
    return out


def _coverage_record(*, scope, examined=None, complete=False, boundaries=None, facts=None):
    """Canonical capability-owned Coverage shape.

    Coverage is physical, never a relevance score. ``complete`` means the
    declared physical scope was exhausted under the capability's own boundary.
    """
    value = {
        "scope": copy.deepcopy(scope) if isinstance(scope, dict) else {"kind": str(scope or "capability")},
        "examined": copy.deepcopy(examined) if isinstance(examined, dict) else {},
        "complete": bool(complete),
        "boundaries": [copy.deepcopy(item) for item in (boundaries or []) if isinstance(item, dict)],
    }
    if isinstance(facts, dict) and facts:
        value["facts"] = copy.deepcopy(facts)
    return value


def _capability_coverage(name, arguments, result):
    entry = TOOLS.get(str(name or "")) or {}
    coverage = entry.get("coverage")
    if callable(coverage):
        value = coverage(arguments or {}, result or {})
    else:
        value = result.get("coverage") if isinstance(result, dict) else None
    return normalize_coverage(value, allow_empty=True)


def _capability_frontiers(name, arguments, result):
    entry = TOOLS.get(str(name or "")) or {}
    projector = entry.get("frontier")
    if callable(projector):
        value = projector(arguments or {}, result or {})
        return [copy.deepcopy(item) for item in (value or []) if isinstance(item, dict)]
    return [copy.deepcopy(item) for item in (result.get("frontiers") or []) if isinstance(item, dict)] if isinstance(result, dict) else []


def _frontier_passthrough(arguments, result):
    return [copy.deepcopy(item) for item in (result.get("frontiers") or []) if isinstance(item, dict)] if isinstance(result, dict) else []


def _coverage_atomic(kind, scope_builder=None, examined_builder=None):
    def coverage(arguments, result):
        if result.get("executed") is not True:
            return {}
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        scope = scope_builder(arguments, detail) if callable(scope_builder) else {"kind": kind}
        examined = examined_builder(arguments, detail) if callable(examined_builder) else {}
        return _coverage_record(scope=scope, examined=examined, complete=result.get("ok") is True)
    return coverage


def _coverage_file(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    path = str(detail.get("file") or arguments.get("path") or "").replace("\\", "/")
    if not path:
        return _coverage_record(scope={"kind": "file"}, complete=False)
    scope = {"kind": "file", "source": _source_name(arguments), "path": path}
    if arguments.get("line_start") is not None and arguments.get("line_end") is not None:
        scope["requested_lines"] = [int(arguments["line_start"]), int(arguments["line_end"])]
    examined = {
        key: detail.get(key) for key in ("line_start", "line_end", "total_lines")
        if detail.get(key) is not None
    }
    complete = bool(result.get("ok") is True and not detail.get("truncated"))
    return _coverage_record(
        scope=scope, examined=examined, complete=complete,
        facts={"truncated": bool(detail.get("truncated"))},
    )


def _coverage_find_symbol(arguments, result):
    if arguments.get("path"):
        # Explicit single-file lookup retains the file-window Coverage contract.
        value = result.get("coverage") if isinstance(result, dict) else None
        if isinstance(value, dict) and value:
            return value
        return _coverage_file(arguments, result)
    value = result.get("coverage") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else {}


def _coverage_search(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    boundaries = []
    if int(detail.get("protected_resources_excluded") or 0):
        boundaries.append({"kind": "protected_resource", "count": int(detail.get("protected_resources_excluded") or 0)})
    if detail.get("read_failures"):
        boundaries.append({"kind": "read_failure", "count": len(detail.get("read_failures") or [])})
    scope = {
        "kind": "literal_search",
        "source": _source_name(arguments),
        "query": str(arguments.get("query") or ""),
        "resolved": copy.deepcopy(detail.get("search_scope") or {}),
    }
    examined = {
        "files_with_matches": int(detail.get("files_with_matches") or 0),
        "matches": int(detail.get("matches_observed") or 0),
        "ranges": int(detail.get("ranges_observed") or 0),
        "materialized_ranges": int(detail.get("ranges_materialized") or 0),
    }
    facts = {
        "materialization_complete": not any(
            isinstance(item, dict) and item.get("handle")
            for item in (result.get("frontiers") or [])
        ),
        "coverage_scope": detail.get("coverage_scope"),
    }
    return _coverage_record(
        scope=scope, examined=examined, complete=bool(detail.get("coverage_complete")),
        boundaries=boundaries, facts={k: v for k, v in facts.items() if v is not None},
    )


def _coverage_tree(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    scope = {
        "kind": "workspace_tree",
        "source": _source_name(arguments),
        "depth": arguments.get("depth"), "filter": arguments.get("filter"),
    }
    scope = {k: v for k, v in scope.items() if v is not None}
    complete = bool(detail.get("varredura_completa")) and not bool(detail.get("truncated"))
    return _coverage_record(
        scope=scope,
        examined={"entries": len(detail.get("entries") or [])},
        complete=complete,
        facts={"truncated": bool(detail.get("truncated"))},
    )


def _coverage_project_stats(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    skipped = detail.get("skipped") if isinstance(detail.get("skipped"), dict) else {}
    boundaries = [{"kind": key, "count": int(value or 0)} for key, value in skipped.items() if int(value or 0)]
    return _coverage_record(
        scope={"kind": "workspace_text", "source": _source_name(arguments)},
        examined={"files": int(detail.get("measured_files") or 0), "directories": int(detail.get("directories") or 0)},
        complete=bool(detail.get("scan_complete")), boundaries=boundaries,
        facts={"coverage_scope": detail.get("coverage_scope")},
    )


def _coverage_count_tokens(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    skipped = detail.get("skipped") if isinstance(detail.get("skipped"), dict) else {}
    boundaries = [{"kind": key, "count": int(value or 0)} for key, value in skipped.items() if int(value or 0)]
    considered = int(detail.get("files_considered") or 0)
    measured = int(detail.get("files_measured") or 0)
    return _coverage_record(
        scope={"kind": "token_measurement", "source": _source_name(arguments), "path": detail.get("path") or arguments.get("path") or "."},
        examined={"files_considered": considered, "files_measured": measured, "characters": int(detail.get("characters") or 0)},
        complete=bool(result.get("ok") is True and not boundaries and measured == considered),
        boundaries=boundaries,
    )


def _coverage_inspect_project(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return _coverage_record(
        scope={"kind": "project_structure", "source": _source_name(arguments)},
        examined={"files": int(detail.get("file_count") or 0), "directories": int(detail.get("directory_count") or 0)},
        complete=bool(detail.get("scan_complete")),
    )


def _coverage_relations(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    raw = detail.get("coverage") if isinstance(detail.get("coverage"), dict) else {}
    complete = bool(raw.get("objective_complete", raw.get("complete", not bool(result.get("frontiers")))))
    return _coverage_record(
        scope={"kind": "symbol_relations", "source": _source_name(arguments), "symbol": arguments.get("symbol"), "query": arguments.get("query") or "relations"},
        examined={k: copy.deepcopy(v) for k, v in raw.items() if k.endswith("_count") or k in {"files_scanned", "nodes", "edges"}},
        complete=complete,
        boundaries=[{k: copy.deepcopy(v) for k, v in item.items() if k != "handle"} for item in (result.get("frontiers") or []) if isinstance(item, dict)],
        facts=raw,
    )


def _coverage_git_status(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return _coverage_record(
        scope={"kind": "git_status", "source": _source_name(arguments)}, examined={"returned_entries": int(detail.get("returned_count") or 0)},
        complete=bool(result.get("ok") is True and not detail.get("truncated")),
        facts={"changed_count": detail.get("changed_count"), "truncated": bool(detail.get("truncated"))},
    )


def _coverage_git_diff(arguments, result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return _coverage_record(
        scope={"kind": "git_diff", "source": _source_name(arguments), "path": arguments.get("path"), "staged": bool(arguments.get("staged", False))},
        examined={"files": int(detail.get("file_count") or 0), "characters": int(detail.get("diff_characters") or 0)},
        complete=bool(result.get("ok") is True and not detail.get("truncated")),
        facts={"truncated": bool(detail.get("truncated"))},
    )


def _coverage_continue(arguments, result):
    value = result.get("coverage") if isinstance(result, dict) else None
    if isinstance(value, dict) and {"scope", "examined", "complete"}.issubset(value):
        return copy.deepcopy(value)
    return _coverage_record(scope={"kind": "frontier_continuation", "frontier": arguments.get("frontier")}, complete=result.get("ok") is True)



def _validate_file_material_freshness(material, project_root):
    locator = material.get("locator") if isinstance(material.get("locator"), dict) else {}
    if locator.get("kind") != "file" or not locator.get("path") or not material.get("source_version"):
        return True, "ok"
    try:
        start = int(locator.get("line_start") or 1)
        end = int(locator.get("line_end") or start)
        reading = ler_faixa_projeto(
            project_root, str(locator.get("path") or ""), start, end,
            max_linhas=max(1, end - start + 1),
        )
    except (ErroLeituraProjeto, TypeError, ValueError):
        return False, "stale"
    return (str(reading.get("file_hash") or "") == str(material.get("source_version") or ""), "ok")


def _rehydrate_file_material(material, project_root, max_lines):
    locator = material.get("locator") if isinstance(material.get("locator"), dict) else {}
    if locator.get("kind") != "file":
        return False, "OBSERVATION_REEXECUTION_REQUIRED"
    path = str(locator.get("path") or "").strip()
    start, end = locator.get("line_start"), locator.get("line_end")
    if not path or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return False, "OBSERVATION_REEXECUTION_REQUIRED"
    try:
        reading = ler_faixa_projeto(project_root, path, start, end, max_linhas=max(max_lines, end - start + 1))
    except ErroLeituraProjeto as error:
        return False, error.error_code
    if (
        (material.get("source_version") and str(material.get("source_version")) != str(reading.get("file_hash") or ""))
        or (material.get("content_hash") and str(material.get("content_hash")) != str(reading.get("content_hash") or ""))
    ):
        material["stale"] = True
        return False, "OBSERVATION_STALE"
    material["content"] = reading.get("content")
    material["numbered_content"] = reading.get("numbered_content")
    material.pop("rehydration_error", None)
    material.pop("stale", None)
    return True, "ok"


def _material_source_root(material, roots):
    locator = material.get("locator") if isinstance(material, dict) and isinstance(material.get("locator"), dict) else {}
    source = str(locator.get("source") or "workspace")
    if isinstance(roots, dict):
        root = roots.get(source) or roots.get("workspace")
    else:
        root = roots
    return os.path.realpath(os.fspath(root)) if root and os.path.isdir(os.fspath(root)) else None


def capability_validate_material_freshness(materials, material_ids, source_roots):
    """Dispatch freshness against the physical source recorded by each Material."""
    store = materials if isinstance(materials, dict) else {}
    for material_id in [str(value) for value in (material_ids or []) if str(value)]:
        material = store.get(material_id)
        if not isinstance(material, dict):
            continue
        owner = str(material.get("source_capability") or "")
        checker = (TOOLS.get(owner) or {}).get("freshness")
        if not callable(checker):
            continue
        root = _material_source_root(material, source_roots)
        if root is None:
            return False, f"GROUNDING_STALE:{material_id}:source_unavailable"
        ok, reason = checker(material, root)
        if not ok:
            return False, f"GROUNDING_STALE:{material_id}:{reason}"
    return True, "ok"


def capability_rehydrate_materials(materials, source_roots, *, max_lines):
    """Rehydrate persisted Materials against their recorded physical source."""
    store = materials if isinstance(materials, dict) else {}
    for material in list(store.values()):
        if not isinstance(material, dict) or material.get("content") or material.get("numbered_content"):
            continue
        owner = str(material.get("source_capability") or "")
        rehydrator = (TOOLS.get(owner) or {}).get("rehydrate")
        if not callable(rehydrator):
            material["rehydration_error"] = "OBSERVATION_REEXECUTION_REQUIRED"
            continue
        root = _material_source_root(material, source_roots)
        if root is None:
            material["rehydration_error"] = "OBSERVATION_SOURCE_UNAVAILABLE"
            continue
        ok, reason = rehydrator(material, root, int(max_lines or 1))
        if not ok:
            material["rehydration_error"] = str(reason or "OBSERVATION_REEXECUTION_REQUIRED")


def _public_arguments_default(arguments):
    arguments = arguments if isinstance(arguments, dict) else {}
    return {
        str(key): (str(value)[:240] if not isinstance(value, (int, float, bool)) else value)
        for key, value in list(arguments.items())[:12]
        if key not in {"content", "new_code", "file_hash_expected", "range_hash_expected"}
    }


def _public_arguments_keys(*keys):
    return lambda arguments: {key: arguments.get(key) for key in keys if arguments.get(key) is not None}


def _public_arguments_read_file(arguments):
    out = {"source": _source_name(arguments), "path": arguments.get("path")}
    if arguments.get("line_start") is not None:
        out.update({"line_start": arguments.get("line_start"), "line_end": arguments.get("line_end")})
    return {key: value for key, value in out.items() if value is not None}


def _public_arguments_search(arguments):
    out = {"source": _source_name(arguments), "query": str(arguments.get("query") or "")[:240]}
    for key in ("include_paths", "exclude_paths"):
        if isinstance(arguments.get(key), list):
            out[key] = [str(item)[:240] for item in arguments.get(key)[:20]]
    return out


def _public_arguments_command(arguments):
    out = {"source": _source_name(arguments), "command": str(arguments.get("command") or "")[:500]}
    for key in ("cwd", "timeout_seconds"):
        if arguments.get(key) is not None:
            out[key] = arguments.get(key)
    return out


def _public_arguments_memory(arguments):
    return {
        key: str(arguments.get(key))[:160]
        for key in ("query", "key", "chave", "namespace")
        if arguments.get(key) is not None
    }


def capability_public_arguments(name, arguments):
    """User-visible bounded arguments projected by the capability registry."""
    entry = TOOLS.get(str(name or "")) or {}
    projector = entry.get("public_arguments")
    return projector(arguments or {}) if callable(projector) else _public_arguments_default(arguments or {})


def _public_result_base(result):
    result = result if isinstance(result, dict) else {}
    public = {
        "status": result.get("status"), "ok": bool(result.get("ok")),
        "executed": bool(result.get("executed")), "changed": bool(result.get("changed")),
    }
    for key, limit in (("error_code", 120), ("failure_scope", 40), ("failure_resource", 240)):
        if result.get(key):
            public[key] = str(result.get(key))[:limit]
    if result.get("retryable") is not None:
        public["retryable"] = bool(result.get("retryable"))
    if result.get("coverage"):
        public["coverage"] = copy.deepcopy(result.get("coverage"))
    if result.get("frontiers"):
        public["frontiers"] = [
            {key: value for key, value in item.items() if key != "handle"}
            for item in list(result.get("frontiers") or [])[:12] if isinstance(item, dict)
        ]
    return public


def _public_result_fields(*keys):
    def projector(result):
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        return {key: detail.get(key) for key in keys if detail.get(key) is not None}
    return projector


def _public_result_file(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return {key: value for key, value in {
        "file": detail.get("file"),
        "lines": [detail.get("line_start"), detail.get("line_end")] if detail.get("line_start") is not None else None,
        "total_lines": detail.get("total_lines"), "truncated": bool(detail.get("truncated")),
    }.items() if value is not None}


def _public_result_tree(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return {"entries": len(detail.get("entries") or []), "truncated": bool(detail.get("truncated")), "complete_scan": bool(detail.get("varredura_completa"))}


def _public_result_search(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    out = {key: int(detail.get(key) or 0) for key in ("matches_observed", "matches_materialized", "ranges_observed", "ranges_materialized", "files_with_matches")}
    out.update({"materialized_files": list(detail.get("materialized_files") or [])[:20], "coverage_complete": bool(detail.get("coverage_complete")), "search_scope": dict(detail.get("search_scope") or {})})
    return out


def _public_result_find_symbol(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    if isinstance(detail.get("matches"), list):
        return {"matches": len(detail.get("matches") or []), "matches_observed": int(detail.get("matches_observed") or len(detail.get("matches") or [])), "files_examined": int(detail.get("files_examined") or 0)}
    return _public_result_file(result)


def _public_result_relations(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return {"symbol": detail.get("symbol"), "definitions": len(detail.get("definitions") or []), "incoming": len(detail.get("incoming") or []), "outgoing": len(detail.get("outgoing") or []), "text_references": len(detail.get("text_references") or []), "root_reachability": detail.get("root_reachability") or []}


def _public_result_command(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    out = {key: detail.get(key) for key in ("command", "cwd", "returncode", "backend", "network_enabled", "workspace_isolated", "snapshot_persists_for_job", "real_workspace_changed") if key in detail}
    if detail.get("output"):
        out["output_tail"] = str(detail.get("output"))[-1200:]
    return out


def _public_result_inspect(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    out = {key: detail.get(key) for key in ("file_count", "directory_count", "languages", "scan_complete") if key in detail}
    if isinstance(detail.get("entrypoint_signals"), list): out["entrypoint_signals"] = [dict(item) for item in detail.get("entrypoint_signals")[:20] if isinstance(item, dict)]
    for key in ("test_signals", "ci_signals", "relation_signals"):
        if isinstance(detail.get(key), dict): out[key] = copy.deepcopy(detail.get(key))
    if isinstance(detail.get("framework_signals"), list): out["framework_signals"] = [dict(item) for item in detail.get("framework_signals")[:20] if isinstance(item, dict)]
    return out


def _public_result_git_status(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    out = {key: detail.get(key) for key in ("branch", "clean", "changed_count", "returned_count", "truncated", "counts") if key in detail}
    if isinstance(detail.get("entries"), list): out["files"] = [item.get("path") for item in detail.get("entries")[:40] if isinstance(item, dict)]
    return out


def _public_result_git_diff(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    out = {key: detail.get(key) for key in ("staged", "path", "file_count", "added_lines", "removed_lines", "truncated", "diff_characters") if key in detail}
    if isinstance(detail.get("files"), list): out["files"] = [item.get("path") for item in detail.get("files")[:40] if isinstance(item, dict)]
    return out


def capability_public_result(name, result):
    """User-visible result summary projected by the capability registry."""
    result = result if isinstance(result, dict) else {}
    public = _public_result_base(result)
    detail = result.get("detail")
    if isinstance(detail, str):
        public["detail"] = detail[:500]
        return public
    entry = TOOLS.get(str(name or "")) or {}
    projector = entry.get("public_result")
    if callable(projector):
        extra = projector(result)
        if isinstance(extra, dict):
            public.update(extra)
    return {key: value for key, value in public.items() if value is not None}


def _model_projection_default(detail, grounding_ids, config):
    if not isinstance(detail, dict):
        return detail
    clone = copy.deepcopy(detail)
    ids = list(grounding_ids or [])
    if ids:
        clone["grounding_id"] = ids[0]
    return clone


def _model_projection_search(detail, grounding_ids, config):
    clone = copy.deepcopy(detail) if isinstance(detail, dict) else {}
    ids = list(grounding_ids or [])
    copied = {key: value for key, value in clone.items() if key != "results"}
    rows = []
    for index, item in enumerate(clone.get("results") or []):
        row = dict(item)
        if index < len(ids): row["grounding_id"] = ids[index]
        text = str(row.get("numbered_content") or row.get("content") or "")
        if text:
            row["numbered_content"] = text[:1200]; row.pop("content", None)
        rows.append(row)
    copied["results"] = rows
    return copied


def _model_projection_read_file(detail, grounding_ids, config):
    clone = _model_projection_default(detail, grounding_ids, config)
    if not isinstance(clone, dict): return clone
    text = str(clone.get("numbered_content") or clone.get("content") or "")
    if text:
        clone["numbered_content"] = text[:6000]; clone.pop("content", None)
    return clone


def _model_projection_find_symbol(detail, grounding_ids, config):
    clone = _model_projection_default(detail, grounding_ids, config)
    if not isinstance(clone, dict): return clone
    for key in ("content", "numbered_content", "codigo_original"): clone.pop(key, None)
    if isinstance(clone.get("matches"), list): clone["matches"] = [dict(item) for item in clone.get("matches")[:20] if isinstance(item, dict)]
    return clone


def _model_projection_inspect(detail, grounding_ids, config):
    clone = detail if isinstance(detail, dict) else {}
    ids = list(grounding_ids or [])
    view = {key: copy.deepcopy(clone.get(key)) for key in ("file_count", "directory_count", "languages", "scan_complete") if clone.get(key) is not None}
    if ids: view["grounding_id"] = ids[0]
    if isinstance(clone.get("entrypoint_signals"), list): view["entrypoint_signals"] = [dict(item) for item in clone.get("entrypoint_signals")[:12] if isinstance(item, dict)]
    if isinstance(clone.get("framework_signals"), list): view["framework_signals"] = [dict(item) for item in clone.get("framework_signals")[:12] if isinstance(item, dict)]
    tests = clone.get("test_signals") if isinstance(clone.get("test_signals"), dict) else {}
    if tests: view["test_signals"] = {key: tests.get(key) for key in ("has_tests", "count") if key in tests}
    ci = clone.get("ci_signals") if isinstance(clone.get("ci_signals"), dict) else {}
    if ci: view["ci_signals"] = {"has_ci": ci.get("has_ci"), "files": list(ci.get("files") or [])[:8]}
    rel = clone.get("relation_signals") if isinstance(clone.get("relation_signals"), dict) else {}
    if rel:
        view["relation_signals"] = {key: copy.deepcopy(rel.get(key)) for key in ("local_import_edge_count", "local_import_edges_truncated", "route_file_count", "syntax_error_file_count") if key in rel}
        if isinstance(rel.get("most_imported_files"), list): view["relation_signals"]["most_imported_files"] = [dict(item) for item in rel.get("most_imported_files")[:12] if isinstance(item, dict)]
    return view


def _model_projection_relations(detail, grounding_ids, config):
    clone = detail if isinstance(detail, dict) else {}
    ids = list(grounding_ids or [])
    sequence_keys = ("definitions", "incoming", "outgoing", "structural_references", "imports", "text_references", "unresolved_dynamic")
    view = {key: copy.deepcopy(clone.get(key)) for key in ("symbol", "path_filter", "query", "direction", "include_text_references", "backend", "coverage") if key in clone}
    if ids: view["grounding_id"] = ids[0]
    view["counts"] = {key: len(clone.get(key) or []) for key in sequence_keys}
    limits = {"definitions": 8, "incoming": 12, "outgoing": 12, "structural_references": 8, "imports": 8, "text_references": 8, "unresolved_dynamic": 8}
    for key, limit in limits.items():
        if isinstance(clone.get(key), list): view[key] = copy.deepcopy(clone.get(key)[:limit])
    if isinstance(clone.get("root_reachability"), list): view["root_reachability"] = copy.deepcopy(clone.get("root_reachability")[:12])
    view["semantics"] = "structural_facts_only"
    return view


def _model_projection_command(detail, grounding_ids, config):
    clone = _model_projection_default(detail, grounding_ids, config)
    if isinstance(clone, dict) and clone.get("output") is not None:
        clone["output"] = str(clone.get("output") or "")[-5000:]
    return clone


def capability_model_detail(name, detail, grounding_ids, config):
    """Compact model projection dispatched only through capability-owned hooks."""
    entry = TOOLS.get(str(name or "")) or {}
    projector = entry.get("model_projection")
    return projector(detail, grounding_ids, config or {}) if callable(projector) else _model_projection_default(detail, grounding_ids, config or {})


def _covering_read_file(arguments, entries, workspace_epoch):
    if arguments.get("line_start") is None or arguments.get("line_end") is None:
        return None
    path = _norm_capability_path(arguments.get("path"))
    source = _source_name(arguments)
    try:
        requested_start = int(arguments.get("line_start")); requested_end = int(arguments.get("line_end"))
    except (TypeError, ValueError):
        return None
    candidates = []
    for item in (entries or {}).values():
        if not isinstance(item, dict) or int(item.get("workspace_epoch", -1)) != int(workspace_epoch or 0): continue
        if str(item.get("tool") or "") != "read_file": continue
        item_args = item.get("arguments") or {}
        if _source_name(item_args) != source: continue
        if _norm_capability_path(item_args.get("path")) != path: continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        examined = coverage.get("examined") if isinstance(coverage.get("examined"), dict) else {}
        try: start = int(examined.get("line_start")); end = int(examined.get("line_end"))
        except (TypeError, ValueError): continue
        if start <= requested_start and end >= requested_end: candidates.append((end - start, -int(item.get("turn") or 0), item))
    return copy.deepcopy(min(candidates, key=lambda value: (value[0], value[1]))[2]) if candidates else None


def _resource_failure_by_path(owner):
    def find(arguments, entries, workspace_epoch):
        path = _norm_capability_path(arguments.get("path"))
        source = _source_name(arguments)
        if not path: return None
        candidates = []
        for item in (entries or {}).values():
            if not isinstance(item, dict) or int(item.get("workspace_epoch", -1)) != int(workspace_epoch or 0): continue
            if str(item.get("tool") or "") != str(owner or ""): continue
            if item.get("failure_scope") != "resource": continue
            item_args = item.get("arguments") or {}
            if _source_name(item_args) != source: continue
            resource = _norm_capability_path(item.get("failure_resource") or item_args.get("path"))
            if resource == path: candidates.append((int(item.get("turn") or 0), item))
        return copy.deepcopy(max(candidates, key=lambda value: value[0])[1]) if candidates else None
    return find


def capability_find_covering(name, arguments, entries, workspace_epoch):
    entry = TOOLS.get(str(name or "")) or {}
    hook = entry.get("covers")
    return hook(arguments or {}, entries or {}, workspace_epoch) if callable(hook) else None


def capability_find_resource_failure(name, arguments, entries, workspace_epoch):
    entry = TOOLS.get(str(name or "")) or {}
    hook = entry.get("resource_failure")
    return hook(arguments or {}, entries or {}, workspace_epoch) if callable(hook) else None

def _normalize_symbol_relations_arguments(arguments):
    normalized = dict(arguments or {})
    if str(normalized.get("query") or "relations").strip().lower() == "reachability":
        normalized.pop("max_depth", None)
        normalized.pop("max_edges", None)
    return normalized


def _schema_objeto(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_CAMINHO = {
    "type": "string", "minLength": 1,
    "description": "Relative path inside the project root.",
}
_LINHA = {"type": "integer", "minimum": 1, "description": "1-based line number inside the selected project file."}
_SOURCE = {
    "type": "string", "enum": ["workspace", "eyle"],
    "description": "Physical source to observe; workspace is the user work plane, eyle is Eyle's own read-only source. run_command may use eyle only inside an isolated writable snapshot.",
}

TOOLS = {
    "calculate": {
        "description": "Evaluate one arithmetic expression deterministically with decimal-safe math.",
        "availability": "global",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Decimal result with exact/approximate and precision metadata.",
        "input_schema": _schema_objeto({
            "expression": {"type": "string", "minLength": 1, "maxLength": 500, "description": "Arithmetic expression containing numeric values and supported operators."},
        }, ["expression"]),
        "fn": _tool_calculate,
    },
    "project_stats": {
        "description": "Measure safe text in the user workspace or Eyle's read-only self source.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Counts for files, directories, lines, characters, bytes, extensions and languages.",
        "caveats": ["Measurements only; no importance ranking or code-behavior diagnosis."],
        "input_schema": _schema_objeto({"source": _SOURCE}),
        "fn": _tool_project_stats,
    },
    "count_tokens": {
        "description": "Measure token count or a truthful token estimate for safe project text.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Token count/estimate, method, exactness, measured characters and scan completeness.",
        "caveats": ["Measures project text, not actual LLM request usage or token waste."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": {"type": "string", "minLength": 1, "description": "Optional project-relative file or directory to measure instead of the whole project."},
            "tokenizer": {"type": "string", "minLength": 1, "description": "Optional tokenizer/model identifier; if unavailable, the configured truthful fallback is reported."},
        }),
        "fn": _tool_count_tokens,
    },
    "inspect_project": {
        "description": "Inspect an existing workspace or Eyle self source when its structure, languages, entrypoints, imports, tests, CI, frameworks or manifests matter. It is not a prerequisite for an empty/already-understood workspace.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Languages, entrypoints, imports, tests, CI, frameworks, manifests and relation signals.",
        "caveats": ["Objective static signals only; no importance ranking, runtime confirmation or bug proof. source=eyle is read-only real source."],
        "input_schema": _schema_objeto({"source": _SOURCE}),
        "fn": _tool_inspect_project,
    },
    "list_tree": {
        "description": "List the fresh project tree with limit, depth, filter, and ignored-item counts.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Project-relative tree entries plus depth, truncation, ignored-item metadata and protected-resource visibility markers.",
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of tree entries to return before marking the result truncated."},
            "depth": {"type": "integer", "minimum": 1, "description": "Maximum directory depth to traverse from the project root."},
            "filter": {"type": "string", "minLength": 1, "description": "Optional filename/path glob-style filter applied to returned tree entries."},
        }),
        "fn": _tool_list_tree,
    },
    "search_code": {
        "description": "Find exact literal text/code matches in live project files and return fresh verifiable ranges.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Complete literal-match Coverage plus deterministic bounded fresh material and Frontiers when more objective ranges exist.",
        "caveats": ["Literal text/code search only; never semantic relevance ranking. Objective Scope is resolved before Coverage: literal files are exact, literal directories are recursive, wildcard-bearing selectors are explicit globs, and missing/unsafe literal include paths fail closed. Search exhausts the readable resolved scope mechanically; bounded materialization is represented by the returned material plus any unresolved Frontier. Protected credential/private-key resources and their physical aliases are counted in resolved scope but excluded from content access and reported as a coverage boundary."],
        "input_schema": _schema_objeto(
            {
                "source": _SOURCE,
            "query": {"type": "string", "minLength": 1, "description": "Literal text or code fragment to match exactly in project files."},
                "include_paths": {"type": "array", "maxItems": 16, "items": {"type": "string", "minLength": 1, "maxLength": 300}, "description": "Optional Main-declared project-relative paths. A literal file selects that file; a literal directory selects its recursive subtree; selectors containing *, ? or [ are explicit full-path glob patterns."},
                "exclude_paths": {"type": "array", "maxItems": 16, "items": {"type": "string", "minLength": 1, "maxLength": 300}, "description": "Optional Main-declared project-relative exclusions with the same canonical file/directory/explicit-glob semantics as include_paths."},
            }, ["query"],
        ),
        "fn": _tool_search_code,
    },
    "symbol_relations": {
        "description": "Inspect structural relationships around a code symbol: calls, registrations/bindings, imports, references and optional root-to-symbol paths.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "AST-aware Python call/binding/registration relations, optional directed root paths, optional literal text references, unresolved dynamic sites and coverage metadata.",
        "caveats": ["Reports structural facts only; it never labels code live/dead/legacy or proves runtime behavior. Static resolution can be incomplete for dynamic dispatch, reflection, plugins or ambiguous names."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "symbol": {"type": "string", "minLength": 1, "description": "Code symbol name to inspect."},
            "query": {"type": "string", "enum": ["relations", "reachability"], "description": "relations returns local structural facts; reachability asks for a root-to-symbol structural path and only material frontiers."},
            "path": _CAMINHO,
            "roots": {"type": "array", "items": {"type": "string", "minLength": 1}, "description": "Optional caller/root symbols, node ids or project-relative files from which to test structural reachability."},
            "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "description": "Project only the requested relation direction; default both."},
            "include_text_references": {"type": "boolean", "description": "Include literal text-reference rows. Default false because structural queries usually do not need them."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "description": "Local relations only. Directed reachability ignores/canonicalizes this hint and exhausts the finite resolved graph automatically."},
            "max_edges": {"type": "integer", "minimum": 10, "maximum": 500, "description": "Local relation output limit. Directed reachability canonicalizes this hint and returns only path/coverage/frontier material."},
        }, ["symbol"]),
        "fn": _tool_symbol_relations,
    },
    "continue_observation": {
        "description": "Continue one open Observation Frontier without exposing Runtime continuation handles.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "A bounded continuation with objective observations, coverage and a new Frontier when more reality remains accessible.",
        "caveats": ["Frontiers address observation snapshots and become stale after the Runtime workspace epoch changes."],
        "input_schema": _schema_objeto({
            "frontier": {"type": "string", "minLength": 4, "pattern": r"^fr-[0-9]+$", "description": "Open Frontier id previously returned by Observation."},
        }, ["frontier"]),
        "fn": _tool_continue_observation,
    },
    "find_symbol": {
        "description": "Locate a symbol in a known file or across the live project.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Fresh symbol definition/location and verifiable source range metadata.",
        "caveats": ["Locates definitions/locations; does not guarantee every runtime reference or call site."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": _CAMINHO,
            "symbol": {"type": "string", "minLength": 1, "description": "Exact code symbol name whose definition/location should be found."},
        }, ["symbol"]),
        "fn": _tool_find_symbol,
    },
    "read_file": {
        "description": "Read a bounded beginning portion of one project file with verifiable hashes and line metadata.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Bounded file content, truncation state, line metadata and hashes.",
        "caveats": ["The returned content may be truncated by configured read limits. Protected credential/private-key resources and their physical aliases deny content access; normal files are never blocked by content heuristics."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": _CAMINHO,
            "line_start": _LINHA,
            "line_end": _LINHA,
        }, ["path"]),
        "fn": _tool_read_file,
    },
    "run_command": {
        "description": "Run a shell command in one writable isolated snapshot. source=workspace snapshots the user work plane; source=eyle snapshots Eyle itself for safe self-experiments without changing the installed source.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "execute",
        "returns": "Exit code, bounded combined output, sandbox backend and isolation facts. Sandbox mutations never alter the real workspace.",
        "caveats": ["One sandbox source persists per job; source switching is refused rather than silently discarding state. Protected credentials are omitted. source=eyle also omits live workspace/memory/context state. Real Eyle source is never writable; self changes exist only in the sandbox until explicitly exported as a ZIP artifact."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "command": {"type": "string", "minLength": 1, "maxLength": 8000, "description": "Shell command to execute inside the isolated snapshot."},
            "cwd": {"type": "string", "minLength": 1, "description": "Optional project-relative working directory inside the sandbox snapshot."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Optional command timeout not exceeding the configured sandbox maximum."},
        }, ["command"]),
        "fn": _tool_run_command,
    },
    "export_sandbox_zip": {
        "description": "Package the current persistent sandbox snapshot into a ZIP and export only that inert artifact beside the Eyle installation. It never copies modified source files back.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "mutate",
        "returns": "Artifact filename, byte size, SHA-256, sandbox source and proof that real source was not modified.",
        "caveats": ["Requires an initialized run_command sandbox. Existing artifact names are never overwritten. Packaging does not itself prove tests or release identity passed."],
        "input_schema": _schema_objeto({
            "filename": {"type": "string", "minLength": 5, "maxLength": 160, "pattern": r"^[A-Za-z0-9._-]+\.zip$", "description": "ZIP basename to export beside Eyle."},
            "archive_root": {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[A-Za-z0-9._-]+$", "description": "Optional top-level directory name inside the ZIP."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Packaging/export timeout."},
        }, ["filename"]),
        "fn": _tool_export_sandbox_zip,
    },
    "memory_search": {
        "description": "Search hash-validated external memory entries associated with the active project.",
        "availability": "workspace",
        "produces_grounding": False,
        "effect": "observe",
        "returns": "Bounded hash-validated prior project-memory entries.",
        "caveats": ["Prior memory is context, not proof of current live source state."],
        "input_schema": _schema_objeto({
            "query": {"type": "string", "description": "Text used to match relevant external project-memory entries."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum number of matching memory entries to return."},
        }),
        "fn": _tool_memory_search,
    },
    "memory_store": {
        "description": "Store one useful observation-grounded project fact in external memory.",
        "availability": "workspace",
        "produces_grounding": False,
        "effect": "mutate",
        "returns": "The external project-memory entry that was stored.",
        "caveats": ["Persists project memory only and requires current-task Observation grounding references."],
        "input_schema": _schema_objeto({
            "text": {"type": "string", "minLength": 1, "description": "Compact project fact to persist in external project memory."},
            "kind": {"type": "string", "description": "Optional memory category; defaults to fact when omitted."},
            "grounding_ids": {"type": "array", "items": {"type": "string", "pattern": r"^mat-[0-9]+$"}, "description": "Current-task Observation material IDs that substantiate the stored fact."},
        }, ["text", "grounding_ids"]),
        "fn": _tool_memory_store,
    },
    "run_tests": {
        "description": "Run the detected test suite in the sandbox; optionally focus pytest on one safe relative file or directory.",
        "availability": "tests",
        "produces_grounding": True,
        "effect": "execute",
        "returns": "Runner command, status, return code, concise summary, bounded output and runner diagnostics.",
        "caveats": ["Does not install a missing runner or prove untested behavior; tests may create incidental temporary/cache artifacts."],
        "input_schema": _schema_objeto({
            "scope": {"type": "string", "minLength": 1, "description": "Optional safe project-relative pytest file or directory; omitted means the detected full suite."},
        }),
        "fn": _tool_run_tests,
    },
    "git_status": {
        "description": "Inspect current Git working-tree state without changing files; returns branch and compact modified/added/deleted/untracked entries.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Branch, clean flag, category counts and bounded changed-path entries.",
        "caveats": ["Status metadata only; it does not include patch contents."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of changed-path status entries to return."},
        }),
        "fn": _tool_git_status,
    },
    "git_diff": {
        "description": "Inspect a bounded read-only Git diff for the workspace or one relative path, optionally staged.",
        "availability": "workspace",
        "produces_grounding": True,
        "effect": "observe",
        "returns": "Changed files, added/removed line counts, bounded diff text and truncation state.",
        "caveats": ["Bounded output may omit truncated hunks."],
        "input_schema": _schema_objeto({
            "source": _SOURCE,
            "path": {"type": "string", "minLength": 1, "description": "Optional project-relative path whose Git diff should be inspected."},
            "staged": {"type": "boolean", "description": "When true inspect staged/index changes; otherwise inspect unstaged working-tree changes."},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Number of unchanged context lines around each returned diff hunk."},
        }),
        "fn": _tool_git_diff,
    },
}

# Capability-owned physical observation hooks. Agent/Observation consume only
# this registry contract; adding a new observational capability must not require
# capability-name branches in either core module.
TOOLS["list_tree"].update(signature=_sig_list_tree, observe=_observe_tree, coverage=_coverage_tree)
TOOLS["search_code"].update(signature=_sig_search_code, observe=_observe_search, coverage=_coverage_search)
TOOLS["search_code"]["continue"] = _continue_search_code_page
TOOLS["find_symbol"].update(signature=_sig_find_symbol, observe=_observe_find_symbol, coverage=_coverage_find_symbol)
TOOLS["find_symbol"]["continue"] = _continue_find_symbol_page
TOOLS["symbol_relations"].update(signature=_sig_symbol_relations, observe=_observe_json("symbol_relations"), coverage=_coverage_relations)
TOOLS["symbol_relations"]["continue"] = _continue_structured_page
TOOLS["read_file"].update(signature=_sig_read_file, observe=_observe_file("read_file"), coverage=_coverage_file)
TOOLS["project_stats"].update(signature=lambda arguments: f"project_stats:{_source_name(arguments)}:root", observe=_observe_json("project_stats"), coverage=_coverage_project_stats)
TOOLS["inspect_project"].update(signature=lambda arguments: f"inspect_project:{_source_name(arguments)}:root", observe=_observe_json("inspect_project"), coverage=_coverage_inspect_project)
TOOLS["count_tokens"].update(signature=_sig_count_tokens, observe=_observe_json("count_tokens"), coverage=_coverage_count_tokens)
TOOLS["run_tests"].update(signature=_sig_run_tests, observe=_observe_json("run_tests"), coverage=_coverage_atomic("test_execution", lambda a, d: {"kind":"test_execution", "scope": d.get("scope") or a.get("scope") or "."}, lambda a, d: {"returncode": d.get("returncode")}))
TOOLS["git_status"].update(signature=lambda arguments: f"git_status:{_source_name(arguments)}:root", observe=_observe_json("git_status"), coverage=_coverage_git_status)
TOOLS["git_diff"].update(signature=_sig_git_diff, observe=_observe_json("git_diff"), coverage=_coverage_git_diff)
TOOLS["continue_observation"].update(observe=_observe_continue, coverage=_coverage_continue)
TOOLS["calculate"].update(observe=_observe_json("calculate"), coverage=_coverage_atomic("calculation", lambda a, d: {"kind":"calculation", "expression": a.get("expression")}))
TOOLS["run_command"].update(observe=_observe_json("run_command"), coverage=_coverage_atomic("sandbox_command", lambda a, d: {"kind":"sandbox_command", "source": _source_name(a), "cwd": a.get("cwd") or "."}, lambda a, d: {"returncode": d.get("returncode")}))
TOOLS["export_sandbox_zip"].update(observe=_observe_json("export_sandbox_zip"), coverage=_coverage_atomic("sandbox_export", lambda a, d: {"kind":"sandbox_export", "artifact": d.get("artifact") or a.get("filename")}, lambda a, d: {"bytes": d.get("bytes")}))
TOOLS["memory_search"].update(observe=_observe_none, coverage=_coverage_memory_search)
TOOLS["memory_store"].update(observe=_observe_none, coverage=_coverage_memory_store)

# Capability-owned presentation, normalization and memoization hooks. Generic
# dispatch functions above never branch on capability names.
TOOLS["read_file"].update(
    public_arguments=_public_arguments_read_file, public_result=_public_result_file,
    model_projection=_model_projection_read_file, covers=_covering_read_file,
    resource_failure=_resource_failure_by_path("read_file"),
)
TOOLS["list_tree"].update(
    public_arguments=_public_arguments_keys("source", "limit", "depth", "filter"), public_result=_public_result_tree,
)
TOOLS["search_code"].update(
    public_arguments=_public_arguments_search, public_result=_public_result_search,
    model_projection=_model_projection_search,
)
TOOLS["find_symbol"].update(
    public_arguments=_public_arguments_keys("source", "symbol", "path"), public_result=_public_result_find_symbol,
    model_projection=_model_projection_find_symbol, resource_failure=_resource_failure_by_path("find_symbol"),
)
TOOLS["symbol_relations"].update(
    public_arguments=_public_arguments_keys("source", "symbol", "query", "path", "roots", "direction", "include_text_references", "max_depth", "max_edges"),
    public_result=_public_result_relations, model_projection=_model_projection_relations,
    normalize=_normalize_symbol_relations_arguments, resource_failure=_resource_failure_by_path("symbol_relations"),
)
TOOLS["continue_observation"].update(public_arguments=lambda arguments: {"frontier": str(arguments.get("frontier") or "")[:80]})
TOOLS["calculate"].update(
    public_arguments=lambda arguments: {"expression": str(arguments.get("expression") or "")[:240]},
    public_result=_public_result_fields("result", "resultado", "exact", "expression"),
)
TOOLS["count_tokens"].update(
    public_arguments=_public_arguments_keys("source", "path", "tokenizer"),
    public_result=_public_result_fields("file_count", "files", "directories", "lines", "characters", "bytes", "estimated_tokens", "tokens", "exact", "method", "characters_per_token", "languages"),
)
TOOLS["project_stats"].update(
    public_arguments=_public_arguments_keys("source"),
    public_result=_public_result_fields("file_count", "files", "directories", "lines", "characters", "bytes", "estimated_tokens", "tokens", "exact", "method", "characters_per_token", "languages"),
)
TOOLS["inspect_project"].update(public_arguments=_public_arguments_keys("source"), public_result=_public_result_inspect, model_projection=_model_projection_inspect)
TOOLS["run_tests"].update(
    public_arguments=lambda arguments: {"scope": arguments.get("scope")} if arguments.get("scope") else {},
    public_result=_public_result_fields("command", "returncode", "scope", "backend", "tests_detected", "summary"),
)
TOOLS["run_command"].update(public_arguments=_public_arguments_command, public_result=_public_result_command, model_projection=_model_projection_command)
TOOLS["export_sandbox_zip"].update(
    public_arguments=_public_arguments_keys("filename", "archive_root", "timeout_seconds"),
    public_result=_public_result_fields("artifact", "bytes", "sha256", "sandbox_source", "real_source_modified"),
)
TOOLS["git_status"].update(
    public_arguments=_public_arguments_keys("source", "max_entries"),
    public_result=_public_result_git_status,
)
TOOLS["git_diff"].update(public_arguments=_public_arguments_keys("source", "path", "staged", "context_lines"), public_result=_public_result_git_diff, resource_failure=_resource_failure_by_path("git_diff"))
TOOLS["memory_search"].update(public_arguments=_public_arguments_memory)
TOOLS["memory_store"].update(public_arguments=_public_arguments_memory)

for _capability_entry in TOOLS.values():
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
    ):
        _capability_entry.setdefault(_hook_name, None)

for _file_capability in ("read_file", "search_code", "find_symbol"):
    TOOLS[_file_capability]["freshness"] = _validate_file_material_freshness
    TOOLS[_file_capability]["rehydrate"] = _rehydrate_file_material

# Limites ficam no proprio registro. O catalogo resolve as chaves de
# configuracao para valores numericos antes de chegar ao modelo.
for _entrada_tool in TOOLS.values():
    _entrada_tool.setdefault("limits", {})
    _entrada_tool["effect"] = normalize_effect(_entrada_tool.get("effect"))
TOOLS["list_tree"]["limits"] = {
    "max_entradas": {"config_key": "agent.max_tree_entries", "default": 200},
    "max_profundidade": {"config_key": "agent.max_tree_depth", "default": 6},
}
TOOLS["search_code"]["limits"] = {
    "max_linhas_por_resultado": {"config_key": "agent.max_search_range_lines", "default": 16},
    "max_matches": {"config_key": "agent.max_search_matches", "default": 40},
    "max_ranges": {"config_key": "agent.max_search_ranges", "default": 12},
}
TOOLS["read_file"]["limits"] = {
    "max_linhas": {"config_key": "agent.max_file_read_lines", "default": 400},
}

def _ler_config_key(config, caminho, default):
    valor = config or {}
    for parte in caminho.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return default
        valor = valor[parte]
    return valor


def _compact_arg_description(text):
    text = str(text or "").strip()
    replacements = {
        "Relative path inside the project root.": "project-relative path",
        "1-based line number inside the selected project file.": "1-based line",
        "Hexadecimal SHA-256 returned by a fresh read.": "fresh-read SHA-256",
        "Replacement code. Empty string is valid for deletion.": "replacement code; empty=delete",
        "Exact original source text expected before a confirmed replacement.": "exact original source expected before replacement",
    }
    return replacements.get(text, text)[:110]


def _compact_input_contract(schema):
    """Compact JSON-schema arguments into model-readable signatures.

    The executable schema remains authoritative for validation. The model only
    needs type, required/optional state, numeric bounds and the tool-specific
    meaning of each argument.
    """
    schema = schema if isinstance(schema, dict) else _schema_objeto()
    required = set(schema.get("required") or [])
    type_labels = {
        "string": "str", "integer": "int", "number": "num",
        "boolean": "bool", "object": "obj", "array": "list",
    }
    inputs = {}
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind = type_labels.get(spec.get("type", "any"), spec.get("type", "any"))
        if name not in required:
            kind += "?"
        bounds = []
        if spec.get("minimum") is not None:
            bounds.append(f">={spec.get('minimum')}")
        if spec.get("maximum") is not None:
            bounds.append(f"<={spec.get('maximum')}")
        enum_values = [str(value) for value in (spec.get("enum") or [])]
        if enum_values and len(enum_values) <= 6:
            head = "|".join(enum_values) + ("?" if name not in required else "")
        else:
            head = kind + ((" " + " ".join(bounds)) if bounds else "")
        description = _compact_arg_description(spec.get("description"))
        inputs[name] = f"{head} | {description}" if description else head
    return inputs


def _minimal_tool_signature(name, schema):
    """Return one compact model-facing capability signature from the canonical schema."""
    schema = schema if isinstance(schema, dict) else _schema_objeto()
    required = set(schema.get("required") or [])
    type_labels = {
        "string": "str", "integer": "int", "number": "num",
        "boolean": "bool", "object": "obj", "array": "list",
    }
    args = []
    for arg_name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        enum_values = [str(value) for value in (spec.get("enum") or [])]
        optional = "" if arg_name in required else "?"
        kind = "|".join(enum_values) if enum_values and len(enum_values) <= 6 else type_labels.get(spec.get("type", "any"), spec.get("type", "any"))
        args.append(f"{arg_name}{optional}:{kind}")
    return f"{name}({','.join(args)})"


def gerar_indice_capabilities(config=None, allowed_names=None):
    """Project the executable registry into the tiny index shown on every Agent call.

    The index is discovery, not a second schema. The Runtime still validates the
    first use against TOOLS[name].input_schema. Expanded contracts are shown only
    after the Main LLM has actually requested that tool.
    """
    allowed = None if allowed_names is None else {str(name) for name in allowed_names}
    result = []
    for name, entry in TOOLS.items():
        if allowed is not None and name not in allowed:
            continue
        signature = _minimal_tool_signature(name, entry.get("input_schema", _schema_objeto()))
        purpose = " ".join(str(entry.get("description") or "").split())
        if len(purpose) > 46:
            purpose = purpose[:43].rstrip() + "..."
        result.append(f"{signature} — {purpose}" if purpose else signature)
    return result


def gerar_catalogo_tools(config=None, allowed_names=None, compact=False):
    """Generate the public catalog from the executable registry.

    ``allowed_names`` only filters actions that are impossible in the current
    runtime state. ``compact`` keeps each tool's canonical semantic contract
    while removing implementation-only schema detail.
    """
    catalogo = []
    fonte = TOOLS
    allowed = None if allowed_names is None else {str(name) for name in allowed_names}
    for chave, entrada in fonte.items():
        public_name = chave
        if allowed is not None and public_name not in allowed:
            continue
        limites = {}
        for nome_limite, origem in (entrada.get("limits") or {}).items():
            limites[nome_limite] = _ler_config_key(
                config, origem["config_key"], origem["default"],
            )
        schema = entrada.get("input_schema", _schema_objeto())
        if compact:
            item = {
                "name": public_name,
                "purpose": entrada.get("description", "")[:200],
                "effect": normalize_effect(entrada.get("effect")),
                "inputs": _compact_input_contract(schema),
                "returns": str(entrada.get("returns") or "")[:220],
            }
            caveats = [str(value)[:150] for value in (entrada.get("caveats") or [])[:4]]
            if caveats:
                item["caveats"] = caveats
            if limites:
                item["limits"] = limites
            catalogo.append(item)
        else:
            item = {
                "name": public_name,
                "description": entrada.get("description", ""),
                "effect": normalize_effect(entrada.get("effect")),
                "input_schema": schema,
                "returns": entrada.get("returns", ""),
                "limits": limites,
            }
            if entrada.get("caveats"):
                item["caveats"] = list(entrada.get("caveats") or [])
            catalogo.append(item)
    return catalogo


def _tipo_json_valido(valor, tipo):
    if tipo == "integer":
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo == "number":
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    if tipo == "string":
        return isinstance(valor, str)
    if tipo == "boolean":
        return isinstance(valor, bool)
    if tipo == "object":
        return isinstance(valor, dict)
    if tipo == "array":
        return isinstance(valor, list)
    return False


def _validar_valor_schema(valor, regra, caminho):
    """Validate the JSON-Schema subset used by canonical tool inputs."""
    regra = regra if isinstance(regra, dict) else {}
    tipo = regra.get("type")
    if tipo and not _tipo_json_valido(valor, tipo):
        return f"argumento '{caminho}' precisa ser do tipo {tipo}"
    if "enum" in regra and valor not in list(regra.get("enum") or []):
        permitidos = ", ".join(str(item) for item in (regra.get("enum") or []))
        return f"argumento '{caminho}' precisa ser um de: {permitidos}"
    if tipo == "string":
        if len(valor.strip()) < int(regra.get("minLength", 0) or 0):
            return f"argumento '{caminho}' nao pode ser vazio"
        if "maxLength" in regra and len(valor) > int(regra["maxLength"]):
            return f"argumento '{caminho}' precisa ter no maximo {regra['maxLength']} caracteres"
        if regra.get("pattern") and not re.fullmatch(str(regra["pattern"]), valor):
            return f"argumento '{caminho}' nao corresponde ao formato esperado"
    if tipo in ("integer", "number"):
        if "minimum" in regra and valor < regra["minimum"]:
            return f"argumento '{caminho}' precisa ser >= {regra['minimum']}"
        if "maximum" in regra and valor > regra["maximum"]:
            return f"argumento '{caminho}' precisa ser <= {regra['maximum']}"
    if tipo == "array":
        if "minItems" in regra and len(valor) < int(regra["minItems"]):
            return f"argumento '{caminho}' precisa ter pelo menos {regra['minItems']} item(ns)"
        if "maxItems" in regra and len(valor) > int(regra["maxItems"]):
            return f"argumento '{caminho}' precisa ter no maximo {regra['maxItems']} item(ns)"
        item_schema = regra.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(valor):
                error = _validar_valor_schema(item, item_schema, f"{caminho}[{index}]")
                if error:
                    return error
    return None


def validar_chamada_tool(nome, arguments):
    """Validate one canonical tool call before execution; aliases are not accepted."""
    entrada = TOOLS.get(nome)
    if entrada is None:
        conhecidas = ", ".join(sorted(TOOLS))
        return None, _falha(
            "TOOL_NOT_FOUND",
            f"tool '{nome}' nao existe. Ferramentas disponiveis: {conhecidas}",
        )
    if not isinstance(arguments, dict):
        return None, _falha("INVALID_ARGUMENT", "arguments precisa ser um objeto JSON")

    schema = entrada.get("input_schema")
    if not isinstance(schema, dict):
        return None, _falha("INVALID_TOOL_SCHEMA", f"tool '{nome}' nao possui input_schema canonico")
    normalizados = dict(arguments)

    propriedades = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        desconhecidas = sorted(set(normalizados) - set(propriedades))
        if desconhecidas:
            return None, _falha(
                "INVALID_ARGUMENT",
                "argumento(s) desconhecido(s): " + ", ".join(desconhecidas),
            )

    faltando = [nome_campo for nome_campo in schema.get("required", []) if nome_campo not in normalizados]
    if faltando:
        return None, _falha(
            "INVALID_ARGUMENT",
            "argumento(s) obrigatorio(s) faltando: " + ", ".join(faltando),
        )

    for nome_campo, valor in normalizados.items():
        regra = propriedades.get(nome_campo)
        if regra is None:
            continue
        erro = _validar_valor_schema(valor, regra, nome_campo)
        if erro:
            return None, _falha("INVALID_ARGUMENT", erro)
    if "line_start" in normalizados and "line_end" in normalizados:
        if normalizados["line_end"] < normalizados["line_start"]:
            return None, _falha(
                "INVALID_ARGUMENT",
                "argument 'line_end' must be >= line_start",
            )
    normalizer = (TOOLS.get(nome) or {}).get("normalize")
    if callable(normalizer):
        normalized_by_capability = normalizer(normalizados)
        if not isinstance(normalized_by_capability, dict):
            return None, _falha("INVALID_CAPABILITY_NORMALIZATION", f"capability '{nome}' returned invalid normalized arguments")
        normalizados = normalized_by_capability
    return normalizados, None


def executar_tool(nome, arguments, ctx):
    """
    Single execution entry point used by ``eyle.core.agent``. Tool
    exceptions become a standard ``TOOL_EXECUTION_ERROR`` result instead of
    bypassing the task state machine.
    """
    arguments, erro_validacao = validar_chamada_tool(nome, arguments)
    if erro_validacao is not None:
        return erro_validacao
    entrada = TOOLS[nome]
    try:
        resultado = entrada["fn"](arguments, ctx or {})
        if not isinstance(resultado, dict) or set(resultado) != set(_CAMPOS_RESULTADO):
            return _falha(
                "INVALID_TOOL_RESULT",
                f"tool '{nome}' devolveu um resultado fora do contrato padrao",
                executed=True,
            )
        observations = _capability_observations(nome, arguments, resultado)
        if observations:
            resultado["observations"] = observations
        try:
            coverage = _capability_coverage(nome, arguments, resultado)
        except CoverageContractError as error:
            return _falha(
                "CAPABILITY_COVERAGE_INVALID",
                f"capability '{nome}' violated Coverage contract: {error}",
                executed=bool(resultado.get("executed")), changed=bool(resultado.get("changed")),
                retryable=False,
            )
        resultado["coverage"] = coverage
        resultado["frontiers"] = _capability_frontiers(nome, arguments, resultado)
        return resultado
    except Exception as e:
        return _falha("TOOL_EXECUTION_ERROR", f"tool '{nome}' falhou ao executar: {e}", executed=True)
