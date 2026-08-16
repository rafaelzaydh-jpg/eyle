#!/usr/bin/env python3
"""Bundled standard capability provider.

This module owns the domain mechanics shipped with the default distribution:
workspace/source observation, isolated execution and related utilities. Eyle Core does not know these domains; it sees only provider-owned
capability contracts through ``eyle.capabilities``.
"""
import copy
import json
import os
import re
import subprocess

from eyle.contracts.capability import RESULT_FIELDS, physical_effect, result as capability_result
from eyle.capabilities.registry import Provider
from eyle.providers import workspace_transaction as _workspace_transaction
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from eyle.providers.standard_impl.workspace_io import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)
from eyle.providers.standard_impl.editing import (  # noqa: E402
    localizar_simbolo,
    localizar_simbolo_no_projeto,
    rodar_testes_projeto,
)
from eyle.providers.standard_impl.project_inspection import (  # noqa: E402
    calculate as calculate_expression,
    count_tokens as count_project_tokens,
    inspect_project as inspect_project_signals,
    project_stats as measure_project_stats,
)
from eyle.providers.standard_impl.git_tools import git_status as inspect_git_status, git_diff as inspect_git_diff  # noqa: E402
from eyle.providers.standard_impl.code_relations import analyze_symbol_relations  # noqa: E402
from eyle.providers.standard_impl.text_hash import hash_texto  # noqa: E402
from eyle.contracts.observation import (  # noqa: E402
    CoverageContractError, materialize_snapshot_handle, normalize_coverage, normalize_effect, register_snapshot_handle, result_observation_fields,
)
from eyle.runtime.observation import resolve_frontier, consume_frontier  # noqa: E402
from eyle.providers.standard_impl.sandbox import executar_comando_livre_no_sandbox, export_active_sandbox_zip, ErroSandbox  # noqa: E402
from eyle.providers.standard_impl.workspace_policy import (  # noqa: E402
    build_protected_resource_index, is_protected_workspace_resource, protected_resource_info,
)
from eyle.providers.standard_impl.file_scope import (  # noqa: E402
    FileScopeError, normalize_scope_selectors, resolve_file_scope,
)

PROJECT_BASE_DIR = os.path.dirname(BASE_DIR)


def _standard_context(ctx):
    provider_context = (ctx or {}).get("provider_context") or {}
    value = provider_context.get("standard") or {} if isinstance(provider_context, dict) else {}
    return value if isinstance(value, dict) else {}


def _standard_config(config):
    providers = (config or {}).get("providers") or {}
    value = providers.get("standard") or {} if isinstance(providers, dict) else {}
    return value if isinstance(value, dict) else {}


def _standard_tests_config(config):
    value = _standard_config(config).get("tests") or {}
    return value if isinstance(value, dict) else {}

_CAMPOS_RESULTADO = RESULT_FIELDS


def _resultado(status, ok, executed, changed=False, error_code=None, detail=None, retryable=None,
               failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None, physical_effect=None):
    """Small provider convenience wrapper over the universal result contract."""
    return capability_result(
        status, ok, executed, changed=changed, error_code=error_code, detail=detail,
        retryable=retryable, failure_scope=failure_scope, failure_resource=failure_resource,
        observations=observations, coverage=coverage, frontiers=frontiers,
        physical_effect_value=physical_effect,
    )


def _sucesso(detail=None, changed=False, *, observations=None, coverage=None, frontiers=None, physical_effect=None):
    if isinstance(detail, dict):
        if observations is None: observations = detail.get("observations")
        if coverage is None: coverage = detail.get("coverage")
        if frontiers is None: frontiers = detail.get("frontiers")
    return _resultado(
        "success", True, True, changed=changed, detail=detail,
        observations=observations, coverage=coverage, frontiers=frontiers, physical_effect=physical_effect,
    )


def _falha(error_code, detail, executed=False, changed=False, retryable=None, *, failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None, physical_effect=None):
    return _resultado(
        "failed", False, executed, changed=changed,
        error_code=error_code, detail=detail, retryable=retryable,
        failure_scope=failure_scope, failure_resource=failure_resource,
        observations=observations, coverage=coverage, frontiers=frontiers, physical_effect=physical_effect,
    )


def _pulado(detail, error_code=None, *, physical_effect=None):
    return _resultado("skipped", True, False, error_code=error_code, detail=detail, physical_effect=physical_effect)


def _source_name(arguments):
    raw = str((arguments or {}).get("source") or "workspace").strip().lower()
    return raw if raw in {"workspace", "eyle"} else "workspace"


def _caminho_fonte(ctx, arguments):
    """Resolve an observation/sandbox source without granting real self-write authority."""
    projeto = _standard_context(ctx)
    source = _source_name(arguments)
    if source == "eyle":
        root = projeto.get("eyle_root")
    else:
        root = projeto.get("caminho_origem")
    return os.path.realpath(root) if root and os.path.isdir(root) else None


def _source_unavailable(arguments):
    source = _source_name(arguments)
    return _falha(
        "SOURCE_NOT_AVAILABLE",
        {
            "source": source,
            "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
            "message": f"requested physical source '{source}' is unavailable",
        },
        retryable=False, failure_scope="request", failure_resource=source,
    )


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


def _source_tree_freshness_token(arguments, ctx):
    """Mechanical source-tree token used only to validate cached observations.

    It intentionally tracks path/size/mtime metadata rather than semantic
    relevance. Provider-specific Material freshness still verifies exact file
    content for grounded reads. The tree token is especially important for
    negative observations, which otherwise have no Material to validate.
    """
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return None
    rows = []
    ignored = set(_SEARCH_IGNORED_DIRS)
    for current, dirs, names in os.walk(root, followlinks=False):
        rel_current = os.path.relpath(current, root).replace("\\", "/")
        if rel_current == ".":
            rel_current = ""
        kept = []
        for name in sorted(dirs):
            if name in ignored:
                continue
            rel = f"{rel_current}/{name}".strip("/")
            if _self_runtime_path_blocked(arguments, rel):
                continue
            kept.append(name)
            try:
                st = os.stat(os.path.join(current, name), follow_symlinks=False)
                rows.append(["d", rel, int(st.st_mtime_ns)])
            except OSError:
                rows.append(["d", rel, None])
        dirs[:] = kept
        for name in sorted(names):
            rel = f"{rel_current}/{name}".strip("/")
            if _self_runtime_path_blocked(arguments, rel):
                continue
            path = os.path.join(current, name)
            try:
                st = os.stat(path, follow_symlinks=False)
            except OSError:
                rows.append(["f", rel, None, None])
                continue
            if not os.path.isfile(path):
                continue
            rows.append(["f", rel, int(st.st_size), int(st.st_mtime_ns)])
    payload = {"source": _source_name(arguments), "root": os.path.realpath(root), "rows": rows}
    return hash_texto(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def _git_freshness_token(arguments, ctx):
    """Mechanical Git/worktree token for retained Git observations."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return None
    tree = _source_tree_freshness_token(arguments, ctx) or ""
    pieces = [tree]
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3, check=False,
        )
        pieces.append(head.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pieces.append("")
    try:
        index = subprocess.run(
            ["git", "-C", root, "rev-parse", "--git-path", "index"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3, check=False,
        )
        index_path = index.stdout.strip()
        if index_path and not os.path.isabs(index_path):
            index_path = os.path.join(root, index_path)
        if index_path and os.path.exists(index_path):
            st = os.stat(index_path, follow_symlinks=False)
            pieces.append(f"{int(st.st_size)}:{int(st.st_mtime_ns)}")
    except (OSError, subprocess.SubprocessError):
        pieces.append("")
    return hash_texto("\n".join(pieces))


def _searchable_files(root, *, include_paths=None, exclude_paths=None):
    """Resolve Objective Scope first, then apply the protected-content boundary."""
    universe, ignored = _search_capability_universe(root)
    include = _normalize_search_selectors(include_paths)
    for selector in include:
        parts = [part for part in selector.replace("\\", "/").split("/") if part]
        blocked = next((part for part in parts if part in _SEARCH_IGNORED_DIRS), None)
        if blocked is not None:
            raise FileScopeError(
                "SEARCH_SCOPE_OUTSIDE_CAPABILITY_BOUNDARY",
                f"objective search include selector targets capability-excluded directory '{blocked}': {selector}",
                selector=selector,
            )
    scoped_files, scope = resolve_file_scope(
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
        return _source_unavailable(arguments)
    config = (ctx or {}).get("config") or {}
    agent_cfg = _standard_config(config)
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
        search_scope = dict(search_scope)
        search_scope["source"] = _source_name(arguments)
        search_scope["source_scope"] = (
            "eyle_application_source" if _source_name(arguments) == "eyle" else "dedicated_user_workspace"
        )
    except FileScopeError as error:
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
                reality_epoch=int((ctx or {}).get("reality_epoch") or 0),
                source_capability="search_code",
                description=f"Remaining objective source ranges for literal search {query!r}",
                page_size=max_ranges,
            )
            frontiers.append({
                "kind": "material_continuation",
                "at": "source_search",
                "count": len(remaining_ranges),
                "reason": "additional objectively matched source ranges remain behind a continuation handle",
                "handle": handle["id"],
            })
        else:
            frontiers.append({
                "kind": "material_boundary",
                "at": "source_search",
                "count": len(remaining_ranges),
                "reason": "additional objectively matched source ranges were not materialized",
            })

    if protected_resources:
        frontiers.append({
            "kind": "protected_resource_boundary", "at": "source_search",
            "count": int(protected_resources),
            "reason": "protected resources were excluded from content search",
        })
    if read_failures:
        frontiers.append({
            "kind": "read_failure_boundary", "at": "source_search",
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
            else ("all_source_files" if protected_resources == 0 else "readable_source_files")
        ),
        "protected_resources_excluded": protected_resources,
        "backend": backend,
        "read_failures": read_failures,
        "frontiers": frontiers,
    }
    return _sucesso(detail, frontiers=frontiers)

def _tool_symbol_relations(arguments, ctx):
    """Inspect symbol structure exhaustively, then page what Main materializes.

    ``max_edges`` is retained as a backwards-compatible page-size name.  It is
    not a scan ceiling: the full finite structural result is computed first and
    every omitted row is retained behind exact Frontiers.
    """
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
    path = arguments.get("path")
    if path and _self_runtime_path_blocked(arguments, path):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(path))
    if path and is_protected_workspace_resource(root, str(path), index=build_protected_resource_index(root)):
        return _protected_resource_failure(root, str(path))
    config = (ctx or {}).get("config") or {}
    agent_cfg = _standard_config(config)
    query = str(arguments.get("query") or "relations")
    default_depth = 6
    page_size = max(1, int(arguments.get("max_edges") or 60))
    raw_scan_limit = agent_cfg.get("max_project_scan_entries")
    scan_limit = None if raw_scan_limit is None else max(1, int(raw_scan_limit))
    try:
        detail = analyze_symbol_relations(
            root, arguments["symbol"], path=path, roots=list(arguments.get("roots") or []),
            direction=str(arguments.get("direction") or "both"),
            include_text_references=bool(arguments.get("include_text_references", False)),
            max_depth=int(arguments.get("max_depth") or default_depth),
            max_edges=None,
            max_files=scan_limit,
            max_file_bytes=max(1024, int(agent_cfg.get("max_project_file_bytes", 4 * 1024 * 1024) or 4 * 1024 * 1024)),
            query=query,
        )

        payloads = list(detail.pop("continuation_payloads", []) or [])
        frontiers = [copy.deepcopy(item) for item in (detail.get("frontiers") or []) if isinstance(item, dict)]

        if query == "relations":
            # Page each objective relation family independently.  This avoids a
            # semantically arbitrary global top-N while preserving exact category
            # identity behind continuation.
            for field in (
                "definitions", "incoming", "outgoing", "structural_references",
                "imports", "text_references", "root_reachability", "unresolved_dynamic",
            ):
                values = [copy.deepcopy(item) for item in (detail.get(field) or []) if isinstance(item, dict)]
                detail[field] = values[:page_size]
                remaining = values[page_size:]
                if not remaining:
                    continue
                payloads.append({
                    "frontier_kind": f"relations.{field}",
                    "items": remaining,
                    "summary": {"count": len(remaining), "field": field},
                })
                frontiers.append({
                    "kind": "material_continuation",
                    "at": f"symbol_relations.{field}",
                    "reason": f"{len(remaining)} additional {field} row(s) remain after this page",
                    "continuation_index": len(payloads) - 1,
                    "count": len(remaining),
                })
            coverage = detail.get("coverage") if isinstance(detail.get("coverage"), dict) else {}
            coverage = dict(coverage)
            coverage["materialization_page_size"] = page_size
            coverage["materialization_frontiers"] = sum(1 for item in frontiers if item.get("continuation_index") is not None)
            detail["coverage"] = coverage

        detail["frontiers"] = frontiers
        ledger = (ctx or {}).get("observation_ledger")
        handle_store = ledger.setdefault("handles", {}) if isinstance(ledger, dict) else None
        if isinstance(handle_store, dict):
            for index, payload in enumerate(payloads):
                if not isinstance(payload, dict):
                    continue
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                handle = register_snapshot_handle(
                    ledger, kind=f"symbol_relations.{payload.get('frontier_kind') or 'continuation'}",
                    payload=payload, reality_epoch=int((ctx or {}).get("reality_epoch") or 0),
                    source_capability="symbol_relations",
                    description=f"Continue symbol_relations for {arguments.get('symbol')}",
                    page_size=page_size,
                )
                for frontier in detail.get("frontiers") or []:
                    if isinstance(frontier, dict) and frontier.get("continuation_index") == index:
                        frontier.pop("continuation_index", None)
                        frontier["handle"] = handle["id"]
                        if summary.get("count") is not None:
                            frontier.setdefault("count", summary.get("count"))
        else:
            for frontier in detail.get("frontiers") or []:
                if isinstance(frontier, dict):
                    frontier.pop("continuation_index", None)
        return _sucesso(detail, frontiers=detail.get("frontiers"))
    except (OSError, ValueError) as error:
        return _falha("RELATION_SCAN_FAILED", str(error), executed=True)


def _continue_read_file_page(payload, ctx):
    """Materialize the exact next file range behind a read_file Frontier."""
    if not isinstance(payload, dict) or payload.get("kind") != "read_file_ranges":
        return {}
    source = str(payload.get("source") or "workspace")
    root = _caminho_fonte(ctx, {"source": source})
    if not root:
        return {}
    projected = []
    materials = []
    failures = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("line_start") or 1); end = int(item.get("line_end") or start)
            reading = ler_faixa_projeto(root, str(payload.get("path") or ""), start, end, max_linhas=max(1, end-start+1))
        except (ErroLeituraProjeto, TypeError, ValueError) as error:
            failures.append({"line_start": item.get("line_start"), "line_end": item.get("line_end"), "error_code": getattr(error, "error_code", "READ_FAILED")})
            continue
        projected.append(dict(reading))
        material = _file_material(reading, source_type="read_file", source=source)
        if material:
            material["source_capability"] = "read_file"
            materials.append(material)
    return {
        "observations": materials,
        "coverage": _coverage_record(
            scope={"kind": "file_range_continuation", "source": source, "path": payload.get("path")},
            examined={"ranges": len(payload.get("items") or []), "materialized": len(projected)},
            complete=not failures,
            boundaries=[{"kind": "read_failure", "count": len(failures)}] if failures else [],
        ),
        "detail": {"source_capability": "read_file", "path": payload.get("path"), "ranges": projected, "read_failures": failures},
    }


def _continue_list_tree_page(payload, ctx):
    """Materialize the exact next tree entries behind a list_tree Frontier."""
    if not isinstance(payload, dict) or payload.get("kind") != "list_tree_entries":
        return {}
    items = [copy.deepcopy(v) for v in payload.get("items") or [] if isinstance(v, dict)]
    source = str(payload.get("source") or "workspace")
    content = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    material = {
        "locator": {"kind": "source_tree_page", "source": source, "depth": payload.get("depth"), "filter": payload.get("filter")},
        "content_hash": hash_texto(content), "content": content,
        "source_type": "list_tree", "source_capability": "list_tree",
    }
    return {
        "observations": [material],
        "coverage": _coverage_record(
            scope={"kind": "source_tree_continuation", "source": source, "depth": payload.get("depth"), "filter": payload.get("filter")},
            examined={"entries": len(items)}, complete=True,
        ),
        "detail": {"source_capability": "list_tree", "entries": items},
    }


def _continue_git_status_page(payload, ctx):
    if not isinstance(payload, dict) or payload.get("kind") != "git_status_entries":
        return {}
    items = [copy.deepcopy(v) for v in payload.get("items") or [] if isinstance(v, dict)]
    return {
        "observations": [{
            "locator": {"kind": "git_status_page", "source": payload.get("source", "workspace")},
            "content_hash": hash_texto(json.dumps(items, ensure_ascii=False, sort_keys=True, default=str)),
            "content": json.dumps(items, ensure_ascii=False, sort_keys=True, default=str),
            "source_type": "git_status", "source_capability": "git_status",
        }],
        "coverage": _coverage_record(scope={"kind":"git_status_continuation","source":payload.get("source","workspace")}, examined={"entries":len(items)}, complete=True),
        "detail": {"source_capability":"git_status","entries":items},
    }


def _continue_git_diff_page(payload, ctx):
    if not isinstance(payload, dict) or payload.get("kind") != "git_diff_chunks":
        return {}
    chunks = [str(v.get("text") or "") for v in payload.get("items") or [] if isinstance(v, dict)]
    text = "".join(chunks)
    return {
        "observations": [{
            "locator": {"kind":"git_diff_page","source":payload.get("source","workspace"),"path":payload.get("path"),"staged":bool(payload.get("staged"))},
            "content_hash": hash_texto(text), "content": text,
            "source_type":"git_diff", "source_capability":"git_diff",
        }],
        "coverage": _coverage_record(scope={"kind":"git_diff_continuation","source":payload.get("source","workspace"),"path":payload.get("path")}, examined={"characters":len(text)}, complete=True),
        "detail": {"source_capability":"git_diff","diff":text},
    }

def _continue_search_code_page(payload, ctx):
    """Materialize search-range locators owned by search_code into file Material candidates."""
    if not isinstance(payload, dict) or payload.get("kind") != "search_range_locator":
        return {}
    source = str(payload.get("source") or "workspace")
    root = _caminho_fonte(ctx, {"source": source})
    if not root:
        return {}
    query = str(payload.get("query") or "")
    cfg = _standard_config((ctx or {}).get("config") or {})
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


def _continue_source_projection(source_capability, payload, ctx):
    entry = CAPABILITIES.get(str(source_capability or "")) or {}
    continuation = entry.get("continue")
    return continuation(payload, ctx or {}) if callable(continuation) else {}

def _tool_continue_observation(arguments, ctx):
    """Continue one Main-visible Frontier while keeping snapshots/handles Runtime-private."""
    ledger = (ctx or {}).get("observation_ledger")
    if not isinstance(ledger, dict):
        return _falha("OBSERVATION_STATE_UNAVAILABLE", "observation state unavailable", executed=False, retryable=False)
    frontier_id = str(arguments.get("frontier") or "")
    handle_id, frontier_error = resolve_frontier(
        ledger, frontier_id, reality_epoch=int((ctx or {}).get("reality_epoch") or 0),
    )
    if frontier_error:
        return _falha(frontier_error, "expected an open fr-* Observation Frontier", executed=False, retryable=True)
    if not isinstance(ledger.get("handles"), dict):
        return _falha("HANDLE_STORE_UNAVAILABLE", "internal continuation store unavailable", executed=False, retryable=False)
    materialized, error = materialize_snapshot_handle(
        ledger, str(handle_id or ""), reality_epoch=int((ctx or {}).get("reality_epoch") or 0),
    )
    if error:
        return _falha(error, "the Runtime continuation behind this Frontier is unavailable", executed=True, retryable=True)

    payload = materialized.get("payload") if isinstance(materialized, dict) else None
    source_capability = str((materialized or {}).get("source_capability") or "")
    projection = _continue_source_projection(source_capability, payload, ctx)
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
    detail["source_capability"] = source_capability
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
        scope={"kind": "frontier_continuation", "frontier": frontier_id, "source_capability": source_capability},
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
        return _source_unavailable(arguments)
    symbol = arguments["symbol"]
    source = _source_name(arguments)
    rel = arguments.get("path")
    if rel and _self_runtime_path_blocked(arguments, rel):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(rel))
    protected_index = build_protected_resource_index(root)
    if rel and is_protected_workspace_resource(root, str(rel), index=protected_index):
        return _protected_resource_failure(root, str(rel))

    if not rel:
        scan = localizar_simbolo_no_projeto(
            root, symbol, limite=None, return_metadata=True,
            excluded_top_level={"workspace", "memory", "context", "agent_memory"} if source == "eyle" else None,
        )
        all_matches = []
        for item in scan.get("all_matches") or []:
            if not isinstance(item, dict):
                continue
            # The search exhausts the selected source, but the model-facing result and
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
            scope={"kind": "symbol_lookup", "source": source, "symbol": symbol, "path": None},
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
                    max_linhas=_standard_config((ctx or {}).get("config") or {}).get("max_file_read_lines", 400),
                )
                only.update(reading)
            except (ErroLeituraProjeto, TypeError, ValueError):
                pass
            only.update({
                "source": source,
                "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
                "simbolo": symbol, "matches_observed": 1, "matches_materialized": 1,
                "files_examined": int(scan.get("files_examined") or 0),
                "protected_resources_excluded": protected,
            })
            return _sucesso(only, coverage=coverage)

        if not all_matches:
            detail = {
                "message": f"símbolo '{symbol}' não encontrado nesta fonte",
                "source": source,
                "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
                "symbol": symbol, "matches": [],
                "matches_observed": 0, "files_examined": int(scan.get("files_examined") or 0),
                "protected_resources_excluded": protected,
            }
            return _falha(
                "SYMBOL_NOT_FOUND", detail, executed=True, coverage=coverage,
            )

        frontiers = []
        ledger = (ctx or {}).get("observation_ledger")
        if remaining and isinstance(ledger, dict):
            handle = register_snapshot_handle(
                ledger, kind="find_symbol.matches",
                payload={"kind": "find_symbol_locator", "symbol": symbol, "items": remaining, "source": _source_name(arguments)},
                reality_epoch=int((ctx or {}).get("reality_epoch") or 0),
                source_capability="find_symbol",
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
            "source": source,
            "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
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
            scope={"kind": "file_symbol_lookup", "source": source, "path": rel, "symbol": symbol},
            examined={"files": 1}, complete=True,
        )
        return _falha(
            "SYMBOL_NOT_FOUND",
            {
                "message": f"símbolo '{symbol}' não encontrado nesta fonte/arquivo",
                "source": source,
                "source_scope": "eyle_application_source" if source == "eyle" else "dedicated_user_workspace",
                "symbol": symbol, "path": rel, "matches": [], "files_examined": 1,
            },
            executed=True, coverage=coverage,
        )
    result = dict(result); result["file"] = result.get("file") or rel; result["simbolo"] = symbol
    result["source"] = source
    result["source_scope"] = "eyle_application_source" if source == "eyle" else "dedicated_user_workspace"
    try:
        reading = ler_faixa_projeto(
            root, rel, int(result["line_start"]), int(result["line_end"]),
            max_linhas=_standard_config((ctx or {}).get("config") or {}).get("max_file_read_lines", 400),
        )
        result.update(reading); result["simbolo"] = symbol
    except ErroLeituraProjeto as erro:
        if erro.error_code == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(root, str(rel or ""))
    except Exception:
        pass
    return _sucesso(result)


def _tool_read_file(arguments, ctx):
    """Read fresh source. Default size is a page; explicit ranges are Main-owned choices."""
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _source_unavailable(arguments)
    caminho_relativo = arguments["path"]
    if _self_runtime_path_blocked(arguments, caminho_relativo):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(caminho_relativo))
    config = (ctx or {}).get("config") or {}
    default_page = max(1, int(_standard_config(config).get("max_file_read_lines", 400) or 400))
    has_start = arguments.get("line_start") is not None
    has_end = arguments.get("line_end") is not None
    if has_start != has_end:
        return _falha("INVALID_ARGUMENT", "line_start e line_end devem ser informados juntos")
    line_start = int(arguments.get("line_start") or 1)
    line_end = int(arguments.get("line_end") or (line_start + default_page - 1))
    requested_lines = max(1, line_end-line_start+1)
    try:
        leitura = ler_faixa_projeto(caminho_projeto, caminho_relativo, line_start, line_end, max_linhas=requested_lines)
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {"INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE", "RANGE_OUT_OF_BOUNDS"} else erro.error_code
        if codigo == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(caminho_projeto, caminho_relativo)
        return _falha(codigo, erro.detail, executed=True)
    leitura = dict(leitura)
    physical_end = int(leitura.get("line_end") or line_end)
    total = int(leitura.get("total_lines") or physical_end)
    leitura["truncated"] = physical_end < total
    frontiers=[]
    ledger=(ctx or {}).get("observation_ledger")
    if physical_end < total and isinstance(ledger,dict):
        ranges=[]
        cursor=physical_end+1
        while cursor<=total:
            finish=min(total,cursor+default_page-1)
            ranges.append({"line_start":cursor,"line_end":finish})
            cursor=finish+1
        handle=register_snapshot_handle(
            ledger, kind="read_file.ranges",
            payload={"kind":"read_file_ranges","source":_source_name(arguments),"path":caminho_relativo,"items":ranges},
            reality_epoch=int((ctx or {}).get("reality_epoch") or 0), source_capability="read_file",
            description=f"Continue {caminho_relativo} after line {physical_end}", page_size=1,
        )
        frontiers.append({"kind":"material_continuation","at":"file","count":total-physical_end,"reason":f"file continues after line {physical_end}","handle":handle["id"]})
    return _sucesso(leitura, frontiers=frontiers)

def _tool_list_tree(arguments, ctx):
    """List a live tree page. Page size is not a scan/knowledge ceiling."""
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _source_unavailable(arguments)
    cfg_agente = _standard_config((ctx or {}).get("config") or {})
    page_size = max(1, int(arguments.get("limit") or cfg_agente.get("max_tree_entries", 200) or 200))
    profundidade = max(1, int(arguments.get("depth") or cfg_agente.get("max_tree_depth", 6) or 6))
    try:
        full = listar_arvore_projeto(caminho_projeto, limite=None, profundidade=profundidade, filtro=arguments.get("filter"))
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {"INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE", "RANGE_OUT_OF_BOUNDS"} else erro.error_code
        return _falha(codigo, erro.detail, executed=True)
    all_entries=list(full.get("entries") or [])
    page=all_entries[:page_size]; remaining=all_entries[page_size:]
    result=dict(full); result["entries"]=page; result["total_retornado"]=len(page); result["truncated"]=bool(remaining); result["varredura_completa"]=True; result["complete_scan"]=True
    frontiers=[]; ledger=(ctx or {}).get("observation_ledger")
    if remaining and isinstance(ledger,dict):
        handle=register_snapshot_handle(
            ledger,kind="list_tree.entries",payload={"kind":"list_tree_entries","source":_source_name(arguments),"depth":profundidade,"filter":arguments.get("filter"),"items":remaining},
            reality_epoch=int((ctx or {}).get("reality_epoch") or 0),source_capability="list_tree",description="Continue exact source tree entries",page_size=page_size,
        )
        frontiers.append({"kind":"material_continuation","at":"source_tree","count":len(remaining),"reason":f"{len(remaining)} tree entries remain after this page","handle":handle["id"]})
    return _sucesso(result, frontiers=frontiers)

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
        return _source_unavailable(arguments)
    try:
        return _sucesso(measure_project_stats(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_count_tokens(arguments, ctx):
    """Measure project text and convert it to a truthful token estimate."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
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
        return _source_unavailable(arguments)
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
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _source_unavailable(arguments)
    cfg_testes = _standard_tests_config((ctx or {}).get("config") or {})
    if not cfg_testes.get("enabled", False):
        return _pulado(
            "Test execution is disabled by the standard provider configuration.",
            error_code="TESTS_DISABLED",
        )
    legacy_tests = {
        "ativado": True,
        "comando_python": cfg_testes.get("command_python", "python -m pytest -q"),
        "comando_node": cfg_testes.get("command_node", "npm test --silent"),
        "timeout_segundos": cfg_testes.get("timeout_seconds", 60),
        "sandbox": copy.deepcopy(cfg_testes.get("sandbox") or {}),
    }
    resultado = rodar_testes_projeto(caminho_projeto, legacy_tests, scope=arguments.get("scope"))
    output = str(resultado.get("saida_resumida") or "")
    detail = {
        "command": resultado.get("comando"),
        "returncode": resultado.get("codigo"),
        "scope": resultado.get("scope"),
        "source": _source_name(arguments),
        "source_scope": "eyle_application_source" if _source_name(arguments) == "eyle" else "dedicated_user_workspace",
        "backend": resultado.get("backend"),
        "runner": resultado.get("runner"),
        "tests_detected": bool(resultado.get("tests_detected")),
        "summary": _pytest_summary(output) or str(resultado.get("detalhe") or "")[:500],
        "output_tail": output[-3000:],
    }
    if resultado.get("executado") is not True and resultado.get("ok") is True:
        return _pulado(detail, error_code="TESTS_NOT_FOUND")
    execution_effect = physical_effect("isolated_test_sandbox", "run_tests", "call")
    if resultado.get("ok") is True:
        return _sucesso(detail, physical_effect=execution_effect)
    error_code = resultado.get("error_code") or (
        "TESTS_REFUSED" if resultado.get("recusado") else "TESTS_FAILED"
    )
    return _falha(
        error_code, detail, executed=resultado.get("executado") is True,
        physical_effect=execution_effect if resultado.get("executado") is True else None,
    )


def _tool_git_status(arguments, ctx):
    """Inspect one page of complete working-tree state; remainder becomes Frontier."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
    page_size=max(1,int(arguments.get("max_entries") or 200))
    result = inspect_git_status(root, max_entries=page_size)
    if not result.get("ok"):
        return _falha(result.get("error_code") or "GIT_STATUS_FAILED", result.get("detail"), executed=True)
    all_entries=list(result.get("entries") or [])
    page=all_entries[:page_size]; remaining=all_entries[page_size:]
    result=dict(result); result["entries"]=page; result["returned_count"]=len(page); result["truncated"]=bool(remaining)
    frontiers=[]; ledger=(ctx or {}).get("observation_ledger")
    if remaining and isinstance(ledger,dict):
        handle=register_snapshot_handle(ledger,kind="git_status.entries",payload={"kind":"git_status_entries","source":_source_name(arguments),"items":remaining},reality_epoch=int((ctx or {}).get("reality_epoch") or 0),source_capability="git_status",description="Continue Git status paths",page_size=page_size)
        frontiers.append({"kind":"material_continuation","at":"git_status","count":len(remaining),"reason":"more changed paths remain","handle":handle["id"]})
    return _sucesso(result,frontiers=frontiers)

def _tool_git_diff(arguments, ctx):
    """Inspect Git diff as a continuable character stream, not a hard truncation."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
    cfg_agent = _standard_config((ctx or {}).get("config") or {})
    page_chars=max(1,int(cfg_agent.get("max_git_diff_chars",6000) or 6000))
    result=inspect_git_diff(root,path=arguments.get("path"),staged=bool(arguments.get("staged",False)),context_lines=int(arguments.get("context_lines") or 3),max_chars=None)
    if not result.get("ok"):
        return _falha(result.get("error_code") or "GIT_DIFF_FAILED",result.get("detail"),executed=True)
    raw=str(result.get("diff") or ""); first=raw[:page_chars]; rest=raw[page_chars:]
    result=dict(result); result["diff"]=first; result["truncated"]=bool(rest); result["diff_characters"]=len(raw)
    frontiers=[]; ledger=(ctx or {}).get("observation_ledger")
    if rest and isinstance(ledger,dict):
        chunks=[{"text":rest[i:i+page_chars]} for i in range(0,len(rest),page_chars)]
        handle=register_snapshot_handle(ledger,kind="git_diff.chunks",payload={"kind":"git_diff_chunks","source":_source_name(arguments),"path":arguments.get("path"),"staged":bool(arguments.get("staged",False)),"items":chunks},reality_epoch=int((ctx or {}).get("reality_epoch") or 0),source_capability="git_diff",description="Continue exact Git diff text",page_size=1)
        frontiers.append({"kind":"material_continuation","at":"git_diff","count":len(rest),"reason":f"{len(rest)} diff characters remain","handle":handle["id"]})
    return _sucesso(result,frontiers=frontiers)

def _tool_run_command(arguments, ctx):
    """Run any shell command inside the isolated, writable per-job sandbox snapshot."""
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
    config = (ctx or {}).get("config") or {}
    sandbox_cfg = dict((_standard_config(config).get("sandbox") or {}))
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
        "output": str(result.get("saida") or ""), "backend": result.get("backend"),
        "network_enabled": bool(result.get("network_enabled")),
        "workspace_isolated": bool(result.get("workspace_isolated")),
        "snapshot_persists_for_job": bool(result.get("snapshot_persists_for_job")),
        "protected_resources_omitted": int(result.get("protected_resources_omitted") or 0),
        "real_workspace_changed": False,
    }
    effect = physical_effect("isolated_snapshot", "run_command", "job")
    if result.get("ok") is True:
        return _sucesso(detail, changed=False, physical_effect=effect)
    detail["error"] = result.get("erro")
    return _falha("SANDBOX_COMMAND_FAILED", detail, executed=True, changed=False, physical_effect=effect)


def _tool_export_sandbox_zip(arguments, ctx):
    """Export the current isolated snapshot as one inert ZIP beside Eyle."""
    projeto = _standard_context(ctx)
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
    return _sucesso(
        detail, changed=True,
        physical_effect=physical_effect("artifact", "export", "persistent", changed=True),
    )


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
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/+", "/", text)
    if text.endswith("/") and text != "/":
        text = text.rstrip("/")
    return text


def _sig_list_tree(arguments):
    return "tree:" + json.dumps({
        "source": _source_name(arguments),
        "filter": str(arguments.get("filter") or "").strip(),
        "depth": arguments.get("depth"), "limit": arguments.get("limit"),
    }, sort_keys=True, separators=(",", ":"), default=str)


def _sig_search_code(arguments):
    return "search:" + json.dumps({
        "source": _source_name(arguments),
        "query": str(arguments.get("query") or ""),
        "include_paths": sorted(normalize_scope_selectors(arguments.get("include_paths"))),
        "exclude_paths": sorted(normalize_scope_selectors(arguments.get("exclude_paths"))),
    }, sort_keys=True, separators=(",", ":"), default=str)


def _sig_find_symbol(arguments):
    return f"symbol:{_source_name(arguments)}:{_norm_capability_path(arguments.get('path'))}:{str(arguments.get('symbol') or '').strip()}"


def _sig_symbol_relations(arguments):
    query = str(arguments.get("query") or "relations").strip().lower()
    identity = {
        "source": _source_name(arguments),
        "symbol": str(arguments.get("symbol") or "").strip(),
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
        "tokenizer": str(arguments.get("tokenizer") or "").strip(),
    }, sort_keys=True, separators=(",", ":"))




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


def _slice_file_material(material, line_start, line_end):
    """Return an exact sub-range from one canonical file Material.

    The provider owns file/range semantics. Core only addresses the Material
    and carries the opaque selector chosen by Main.
    """
    if not isinstance(material, dict):
        raise ValueError("EVIDENCE_SELECTOR_INVALID")
    locator = material.get("locator") if isinstance(material.get("locator"), dict) else {}
    if locator.get("kind") != "file":
        raise ValueError("EVIDENCE_SELECTOR_UNSUPPORTED")
    try:
        parent_start = int(locator.get("line_start"))
        parent_end = int(locator.get("line_end"))
        start = int(line_start)
        end = int(line_end)
    except (TypeError, ValueError) as exc:
        raise ValueError("EVIDENCE_SELECTOR_INVALID") from exc
    if start < parent_start or end > parent_end or start > end:
        raise ValueError("EVIDENCE_SELECTOR_OUT_OF_RANGE")
    text = material.get("content")
    if not isinstance(text, str):
        raise ValueError("EVIDENCE_MATERIAL_NOT_MATERIALIZED")
    lines = text.splitlines(keepends=True)
    offset_start = start - parent_start
    offset_end = end - parent_start + 1
    selected = "".join(lines[offset_start:offset_end])
    numbered = "\n".join(
        f"{number:>6} | {line.rstrip(chr(13) + chr(10))}"
        for number, line in enumerate(lines[offset_start:offset_end], start=start)
    )
    selected_locator = copy.deepcopy(locator)
    selected_locator["line_start"] = start
    selected_locator["line_end"] = end
    return {
        "locator": selected_locator,
        "content": selected,
        "numbered_content": numbered,
        "content_hash": hash_texto(selected),
    }


def _evidence_selector_file(material, selector):
    if not selector:
        return {
            "locator": copy.deepcopy(material.get("locator") or {}),
            "content_hash": str(material.get("content_hash") or ""),
        }
    if set(selector) != {"line_start", "line_end"}:
        raise ValueError("EVIDENCE_SELECTOR_INVALID")
    return _slice_file_material(material, selector.get("line_start"), selector.get("line_end"))


def _json_material(source_type, locator_name, detail, *, source="runtime"):
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
    return _json_material("source_tree", "list_tree", inventory, source=_source_name(arguments))


def _observe_json(name):
    def observe(arguments, result):
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        if not detail:
            return []
        payload = copy.deepcopy(detail)
        if isinstance(result.get("physical_effect"), dict):
            payload["physical_effect"] = copy.deepcopy(result.get("physical_effect"))
        source = _source_name(arguments) if "source" in (arguments or {}) else "runtime"
        return _json_material(name, name, payload, source=source)
    return observe


def _observe_workspace_transaction(arguments, result):
    """Materialize the verified post-write world, never the pre-write blob.

    Memory may later point at these Materials as provenance.  The transaction
    result can carry large rollback snapshots internally, but only final file
    bodies (or an explicit absence record for deletes) become canonical
    Material.
    """
    if not isinstance(result, dict) or result.get("ok") is not True:
        return []
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    patches = detail.get("applied_patches") if isinstance(detail.get("applied_patches"), list) else []
    materials = []
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        path = str(patch.get("path") or "").replace("\\", "/").strip()
        operation = str(patch.get("operation") or "").strip().lower()
        if not path:
            continue
        content = patch.get("result_content")
        if operation != "delete" and isinstance(content, str):
            total_lines = len(content.splitlines())
            material = _file_material({
                "file": path,
                "file_hash": hash_texto(content),
                "content_hash": hash_texto(content),
                "content": content,
                "line_start": 1,
                "line_end": total_lines,
                "total_lines": total_lines,
            }, source_type="workspace_transaction", source="workspace")
            if material:
                material.setdefault("metadata", {})["operation"] = operation
                materials.append(material)
            continue
        if operation == "delete":
            payload = {"path": path, "operation": "delete", "state": "absent"}
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            materials.append({
                "locator": {"kind": "file", "source": "workspace", "path": path, "state": "absent"},
                "content_hash": hash_texto(text),
                "content": text,
                "source_type": "workspace_transaction",
                "metadata": payload,
            })
    return materials


def _model_projection_workspace_transaction(detail, grounding_ids, config):
    """Return verified post-write artifacts to Main with Material provenance.

    Eyle intentionally does NOT hide the final artifact body behind a second
    read.  Main needs the verified body and its ``mat-*`` coordinate in the same
    cognition turn to decompose what it actually created into atomic Memory.
    Pre-write/rollback snapshots remain private and are never projected.
    """
    value = detail if isinstance(detail, dict) else {}
    reread = value.get("reread") if isinstance(value.get("reread"), dict) else {}
    tests = value.get("tests") if isinstance(value.get("tests"), dict) else {}
    compile_result = value.get("compile") if isinstance(value.get("compile"), dict) else {}
    ids = [str(v) for v in (grounding_ids or []) if str(v).strip()]
    verified_artifacts = []
    material_index = 0
    for patch in value.get("applied_patches") or []:
        if not isinstance(patch, dict):
            continue
        path = str(patch.get("path") or "").replace("\\", "/")
        operation = str(patch.get("operation") or "").lower()
        if not path:
            continue
        row = {"path": path, "operation": operation}
        if material_index < len(ids):
            row["material_id"] = ids[material_index]
            material_index += 1
        if operation == "delete":
            row["state"] = "absent"
        elif isinstance(patch.get("result_content"), str):
            content = patch.get("result_content")
            row["content"] = content
            row["content_hash"] = hash_texto(content)
            row["bytes"] = len(content.encode("utf-8"))
        verified_artifacts.append(row)
    return {
        "files": list(value.get("files") or []),
        "verification_state": value.get("verification_state"),
        "limitations": list(value.get("limitations") or []),
        "verified_artifacts": verified_artifacts,
        "compile": {k: compile_result.get(k) for k in ("ok", "status", "executed", "error_code") if compile_result.get(k) is not None},
        "tests": {k: tests.get(k) for k in ("ok", "status", "executed", "error_code", "detail") if tests.get(k) is not None},
        "reread": {
            "ok": reread.get("ok"),
            "checked": [
                {k: item.get(k) for k in ("path", "operation", "state", "file_hash", "bytes") if item.get(k) is not None}
                for item in (reread.get("checked") or []) if isinstance(item, dict)
            ],
            "failures": list(reread.get("failures") or []),
        },
        "grounding_ids": ids,
    }


def _observe_none(arguments, result):
    """Explicit capability-owned declaration that no Material is produced."""
    return []


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
        "kind": "source_tree",
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
        scope={"kind": "source_text", "source": _source_name(arguments)},
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


def _validate_file_material_freshness(material, ctx):
    locator = material.get("locator") if isinstance(material.get("locator"), dict) else {}
    if locator.get("kind") != "file" or not locator.get("path") or not material.get("source_version"):
        return None, "not_applicable"
    if isinstance(ctx, (str, os.PathLike)):
        project_root = os.path.realpath(os.fspath(ctx)) if os.path.isdir(os.fspath(ctx)) else None
    else:
        project = _standard_context(ctx)
        project_root = _material_source_root(material, {
            "workspace": project.get("caminho_origem"),
            "eyle": project.get("eyle_root"),
        })
    if not project_root:
        return False, "source_unavailable"
    try:
        start = int(locator.get("line_start") or 1)
        end = int(locator.get("line_end") or start)
        reading = ler_faixa_projeto(
            project_root, str(locator.get("path") or ""), start, end,
            max_linhas=max(1, end - start + 1),
        )
    except (ErroLeituraProjeto, TypeError, ValueError):
        return False, "stale"
    fresh = str(reading.get("file_hash") or "") == str(material.get("source_version") or "")
    return (True, "ok") if fresh else (False, "stale")


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
        root = roots.get(source)
    else:
        root = roots
    return os.path.realpath(os.fspath(root)) if root and os.path.isdir(os.fspath(root)) else None


def capability_rehydrate_materials(materials, source_roots, *, max_lines):
    """Rehydrate persisted Materials against their recorded physical source."""
    store = materials if isinstance(materials, dict) else {}
    for material in list(store.values()):
        if not isinstance(material, dict) or material.get("content") or material.get("numbered_content"):
            continue
        owner = str(material.get("source_capability") or "")
        rehydrator = (CAPABILITIES.get(owner.split(".", 1)[-1]) or {}).get("rehydrate")
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


def _public_result_fields(*keys):
    def projector(result):
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        return {key: detail.get(key) for key in keys if detail.get(key) is not None}
    return projector


def _public_result_workspace_transaction(result):
    detail = result.get("detail") if isinstance(result, dict) else None
    if isinstance(detail, str):
        return {"detail": detail[:1000]}
    detail = detail if isinstance(detail, dict) else {}
    return {key: detail.get(key) for key in ("files", "verification_state", "limitations") if detail.get(key) is not None}


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
        return {key: value for key, value in {
            "source": detail.get("source"),
            "source_scope": detail.get("source_scope"),
            "matches": len(detail.get("matches") or []),
            "matches_observed": int(detail.get("matches_observed") or len(detail.get("matches") or [])),
            "files_examined": int(detail.get("files_examined") or 0),
        }.items() if value is not None}
    out = _public_result_file(result)
    if detail.get("source") is not None:
        out["source"] = detail.get("source")
    if detail.get("source_scope") is not None:
        out["source_scope"] = detail.get("source_scope")
    return out


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


def _model_projection_default(detail, grounding_ids, config):
    if not isinstance(detail, dict):
        return detail
    clone = copy.deepcopy(detail)
    ids = list(grounding_ids or [])
    if ids:
        clone["grounding_id"] = ids[0]
    return clone


def _model_projection_search(detail, grounding_ids, config):
    """Project literal search as a navigation index, never a broad source replay.

    Full matched ranges remain canonical Material in Observation. Main sees only
    coordinates, small previews and objective counts, then chooses a targeted
    read/find_symbol/search if more source text is semantically useful.
    """
    clone = copy.deepcopy(detail) if isinstance(detail, dict) else {}
    ids = list(grounding_ids or [])
    rows = []
    for index, item in enumerate(clone.get("results") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("numbered_content") or item.get("content") or "")
        preview_lines = [line.strip() for line in text.splitlines() if line.strip()]
        preview = " ".join(preview_lines[:2])[:280]
        row = {
            "file": item.get("file"),
            "line_start": item.get("line_start"),
            "line_end": item.get("line_end"),
            "match_lines": list(item.get("match_lines") or [])[:8],
        }
        if index < len(ids):
            row["grounding_id"] = ids[index]
        if preview:
            row["preview"] = preview
        rows.append({k: v for k, v in row.items() if v not in (None, "", [], {})})

    keep = (
        "query", "search_scope", "materialized_files", "matches_observed",
        "matches_materialized", "files_with_matches", "file_match_distribution",
        "distribution_truncated", "ranges_observed", "ranges_materialized",
        "scope_complete", "coverage_complete", "coverage_scope",
        "protected_resources_excluded", "backend", "read_failures",
    )
    copied = {key: copy.deepcopy(clone.get(key)) for key in keep if clone.get(key) not in (None, "", [], {})}
    copied["results"] = rows
    raw_frontiers = [item for item in (clone.get("frontiers") or []) if isinstance(item, dict)]
    if raw_frontiers:
        copied["more_results_available"] = True
        copied["remaining_result_groups"] = sum(max(0, int(item.get("count") or 0)) for item in raw_frontiers)
        copied["navigation_hint"] = "Use continue on an exposed Frontier to materialize the next exact matched ranges, or refine/read directly if you prefer."
    return copied


def _model_projection_read_file(detail, grounding_ids, config):
    clone = _model_projection_default(detail, grounding_ids, config)
    if isinstance(clone, dict):
        clone.pop("content", None) if clone.get("numbered_content") else None
    return clone


def _model_projection_find_symbol(detail, grounding_ids, config):
    clone = _model_projection_default(detail, grounding_ids, config)
    if not isinstance(clone, dict):
        return clone
    # find_symbol is a navigation capability, so source bodies stay in Material,
    # but every row in the CURRENT materialized page must remain visible to Main.
    # Any additional rows belong behind Frontier; never hide a second top-N here.
    for key in ("content", "numbered_content", "codigo_original"):
        clone.pop(key, None)
    if isinstance(clone.get("matches"), list):
        clone["matches"] = [dict(item) for item in clone.get("matches") if isinstance(item, dict)]
    return clone


def _model_projection_inspect(detail, grounding_ids, config):
    return _model_projection_default(detail, grounding_ids, config)


def _model_projection_relations(detail, grounding_ids, config):
    clone = detail if isinstance(detail, dict) else {}
    ids = list(grounding_ids or [])
    sequence_keys = (
        "definitions", "incoming", "outgoing", "structural_references",
        "imports", "text_references", "root_reachability", "unresolved_dynamic",
    )
    view = {
        key: copy.deepcopy(clone.get(key))
        for key in ("symbol", "path_filter", "query", "direction", "include_text_references", "backend", "coverage")
        if key in clone
    }
    if ids:
        view["grounding_id"] = ids[0]
    # The tool already pages every finite relation family and puts the exact
    # remainder behind Frontier.  A model projection must therefore expose the
    # WHOLE current page; a second hidden slice would make rows unreachable.
    view["counts"] = {key: len(clone.get(key) or []) for key in sequence_keys}
    for key in sequence_keys:
        if isinstance(clone.get(key), list):
            view[key] = copy.deepcopy(clone.get(key))
    view["semantics"] = "structural_facts_only"
    return view


def _model_projection_command(detail, grounding_ids, config):
    return _model_projection_default(detail, grounding_ids, config)


def _covering_read_file(arguments, entries, reality_epoch):
    """Return cached coverage when the union of observed ranges covers the request.

    Coverage is physical interval knowledge. It must compose adjacent/overlapping
    observations instead of requiring one historical read to contain the whole
    requested range.
    """
    if arguments.get("line_start") is None or arguments.get("line_end") is None:
        return None
    path = _norm_capability_path(arguments.get("path"))
    source = _source_name(arguments)
    try:
        requested_start = int(arguments.get("line_start")); requested_end = int(arguments.get("line_end"))
    except (TypeError, ValueError):
        return None

    ranges = []
    for item in (entries or {}).values():
        if not isinstance(item, dict) or int(item.get("reality_epoch", -1)) != int(reality_epoch or 0):
            continue
        if str(item.get("capability") or "") not in {"read_file", "standard.read_file"}:
            continue
        item_args = item.get("arguments") or {}
        if _source_name(item_args) != source or _norm_capability_path(item_args.get("path")) != path:
            continue
        coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
        examined = coverage.get("examined") if isinstance(coverage.get("examined"), dict) else {}
        try:
            start = int(examined.get("line_start")); end = int(examined.get("line_end"))
        except (TypeError, ValueError):
            continue
        if end < requested_start or start > requested_end:
            continue
        ranges.append((start, end, int(item.get("turn") or 0), item))

    if not ranges:
        return None
    ranges.sort(key=lambda row: (row[0], row[1], -row[2]))

    cursor = requested_start
    selected = []
    while cursor <= requested_end:
        candidates = [row for row in ranges if row[0] <= cursor <= row[1]]
        if not candidates:
            return None
        best = max(candidates, key=lambda row: (row[1], row[2]))
        selected.append(best)
        cursor = best[1] + 1

    # Preserve a normal historical entry when one range alone covers everything.
    unique_items = []
    seen = set()
    for _, _, _, item in selected:
        marker = id(item)
        if marker not in seen:
            seen.add(marker); unique_items.append(item)
    if len(unique_items) == 1:
        return copy.deepcopy(unique_items[0])

    grounding_ids = []
    frontier_ids = []
    for item in unique_items:
        for value in item.get("grounding_ids") or []:
            value = str(value)
            if value and value not in grounding_ids:
                grounding_ids.append(value)
        for value in item.get("frontier_ids") or []:
            value = str(value)
            if value and value not in frontier_ids:
                frontier_ids.append(value)
    newest_turn = max(int(item.get("turn") or 0) for item in unique_items)
    tokens = {str(item.get("freshness_token") or "") for item in unique_items}
    composed_freshness_token = next(iter(tokens)) if len(tokens) == 1 else None
    return {
        "observation_signature": None,
        "reality_epoch": int(reality_epoch or 0),
        "capability": "standard.read_file",
        "arguments": copy.deepcopy(arguments),
        "public_arguments": copy.deepcopy(arguments),
        "grounding_ids": grounding_ids,
        "frontier_ids": frontier_ids,
        "freshness_token": composed_freshness_token,
        "coverage": {
            "kind": "file_range_union",
            "examined": {"line_start": requested_start, "line_end": requested_end},
            "complete": True,
            "facts": {"composed_from_observations": len(unique_items)},
        },
        "turn": newest_turn,
        "composed_coverage": True,
    }


def _resource_failure_by_path(owner):
    def find(arguments, entries, reality_epoch):
        path = _norm_capability_path(arguments.get("path"))
        source = _source_name(arguments)
        if not path: return None
        candidates = []
        for item in (entries or {}).values():
            if not isinstance(item, dict) or int(item.get("reality_epoch", -1)) != int(reality_epoch or 0): continue
            if str(item.get("capability") or "") not in {str(owner or ""), f"standard.{owner}"}: continue
            if item.get("failure_scope") != "resource": continue
            item_args = item.get("arguments") or {}
            if _source_name(item_args) != source: continue
            resource = _norm_capability_path(item.get("failure_resource") or item_args.get("path"))
            if resource == path: candidates.append((int(item.get("turn") or 0), item))
        return copy.deepcopy(max(candidates, key=lambda value: value[0])[1]) if candidates else None
    return find


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
            "max_edges": {"type": "integer", "minimum": 1, "description": "Page size for relation rows; remaining rows become Frontier."},
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
        "caveats": ["Default reads are paged; continue the Frontier to read the rest. Protected content is blocked."],
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
    "export_sandbox_zip": "export_sandbox_zip",
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
    public_arguments=_public_arguments_keys("source", "symbol", "query", "path", "roots", "direction", "include_text_references", "max_depth", "max_edges"),
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

# Limites ficam no proprio registro. O catalogo resolve as chaves de
# configuracao para valores numericos antes de chegar ao modelo.
for _entrada_tool in CAPABILITIES.values():
    _entrada_tool.setdefault("limits", {})
    _entrada_tool["effect"] = normalize_effect(_entrada_tool.get("effect"))
CAPABILITIES["workspace_transaction"]["ecc_name"] = "transaction"

CAPABILITIES["list_tree"]["limits"] = {
    "max_entradas": {"config_key": "providers.standard.max_tree_entries", "default": 200},
    "max_profundidade": {"config_key": "providers.standard.max_tree_depth", "default": 6},
}
CAPABILITIES["search_code"]["limits"] = {
    "max_linhas_por_resultado": {"config_key": "providers.standard.max_search_range_lines", "default": 16},
    "max_matches": {"config_key": "providers.standard.max_search_matches", "default": 40},
    "max_ranges": {"config_key": "providers.standard.max_search_ranges", "default": 12},
}
CAPABILITIES["read_file"]["limits"] = {
    "max_linhas": {"config_key": "providers.standard.max_file_read_lines", "default": 400},
}


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
    max_lines = max(1, int(_standard_config(config).get("max_file_read_lines", 400) or 400))
    capability_rehydrate_materials(materials, roots, max_lines=max_lines)


_STANDARD_CONFIG_FIELDS = {
    "max_tree_entries", "max_tree_depth", "max_file_read_lines",
    "max_project_scan_entries", "max_project_scan_depth", "max_project_file_bytes",
    "max_inspect_relation_edges", "max_git_diff_chars", "max_search_matches",
    "max_search_ranges", "max_search_range_lines", "sandbox", "tests",
}
_STANDARD_TEST_FIELDS = {"enabled", "command_python", "command_node", "timeout_seconds", "sandbox"}
_STANDARD_SANDBOX_FIELDS = {
    "backend", "bloquear_rede", "comandos_permitidos", "cpu_segundos", "memoria_mb",
    "max_processos", "max_arquivos_abertos", "max_saida_kb", "max_arquivo_mb",
    "copiar_projeto", "max_arquivos_projeto", "max_tamanho_projeto_mb", "cpus",
    "allow_trusted_local", "timeout_segundos", "imagem_oci",
}
_STANDARD_SANDBOX_BACKENDS = {"auto", "microsandbox", "docker", "bwrap", "process", "trusted_local"}
_STANDARD_LIMIT_DEFAULTS = {
    "max_tree_entries": 200, "max_tree_depth": 6, "max_file_read_lines": 400,
    "max_project_scan_entries": None, "max_project_scan_depth": None,
    "max_project_file_bytes": 4194304, "max_inspect_relation_edges": None,
    "max_git_diff_chars": 6000, "max_search_matches": 40,
    "max_search_ranges": 12, "max_search_range_lines": 16,
}

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
    for key, default in _STANDARD_LIMIT_DEFAULTS.items():
        item = value.get(key, default)
        if key in {"max_project_scan_entries", "max_project_scan_depth", "max_inspect_relation_edges"} and item is None:
            continue
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise ValueError(f"STANDARD_PROVIDER_CONFIG_INVALID:standard.{key}")
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
            "Use source=workspace for the user's files. Use source=eyle for Eyle's own source code. "
            "If the user asks about Eyle itself, look at source=eyle.",
        ),
    )
