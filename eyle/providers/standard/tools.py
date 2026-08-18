#!/usr/bin/env python3
"""Read/search/execute capability handlers for the standard provider."""
import copy
import json
import os
import re
import subprocess

from eyle.contracts.capability import physical_effect
from eyle.contracts.observation import (CoverageContractError, materialize_snapshot_handle, normalize_coverage, register_snapshot_handle)
from eyle.runtime.observation import resolve_frontier, consume_frontier
from eyle.providers.standard.workspace_io import ErroLeituraProjeto, ler_faixa_projeto, listar_arvore_projeto
from eyle.providers.standard import editing as _editing
from eyle.providers.standard.project_inspection import calculate as calculate_expression, count_tokens as count_project_tokens, inspect_project as inspect_project_signals, project_stats as measure_project_stats
from eyle.providers.standard.git_tools import git_status as inspect_git_status, git_diff as inspect_git_diff
from eyle.providers.standard.code_relations import analyze_symbol_relations
from eyle.providers.standard.text_hash import hash_texto
from eyle.providers.standard import sandbox as _sandbox
from eyle.providers.standard.workspace_policy import build_protected_resource_index, is_protected_workspace_resource, protected_resource_info
from eyle.providers.standard.file_scope import FileScopeError, normalize_scope_selectors, resolve_file_scope
from eyle.providers.standard.common import (_standard_context, _standard_config, _standard_tests_config, _sucesso, _falha, _pulado, _source_name, _caminho_fonte, _source_unavailable, _self_runtime_path_blocked, _protected_resource_failure)
from eyle.providers.standard.contracts import _coverage_record, _file_material

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


# Materialization page sizes protect a single model turn; they are not knowledge
# ceilings. Every omitted item remains reachable through Frontier.
_FILE_PAGE_LINES = 400
_TREE_PAGE_ENTRIES = 80
_SEARCH_RANGE_PAGE_LINES = 16
_SEARCH_MATCH_PAGE = 40
_SEARCH_RANGE_PAGE = 12
_GIT_DIFF_PAGE_CHARS = 6000

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
    max_lines = _SEARCH_RANGE_PAGE_LINES
    max_matches = _SEARCH_MATCH_PAGE
    max_ranges = _SEARCH_RANGE_PAGE

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

    ``page_size`` controls only the materialized relation page. The full finite
    structural result is computed first and every omitted row is retained behind
    exact Frontiers.
    """
    root = _caminho_fonte(ctx, arguments)
    if not root:
        return _source_unavailable(arguments)
    path = arguments.get("path")
    if path and _self_runtime_path_blocked(arguments, path):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(path))
    if path and is_protected_workspace_resource(root, str(path), index=build_protected_resource_index(root)):
        return _protected_resource_failure(root, str(path))
    query = str(arguments.get("query") or "relations")
    page_size = max(1, int(arguments.get("page_size") or 60))
    requested_depth = arguments.get("max_depth")
    scan_depth = int(requested_depth) if requested_depth is not None else None
    try:
        detail = analyze_symbol_relations(
            root, arguments["symbol"], path=path, roots=list(arguments.get("roots") or []),
            direction=str(arguments.get("direction") or "both"),
            include_text_references=bool(arguments.get("include_text_references", False)),
            max_depth=scan_depth,
            max_edges=None,
            max_files=None,
            max_file_bytes=None,
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
    max_lines = _SEARCH_RANGE_PAGE_LINES
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
    from eyle.providers.standard.registry import CAPABILITIES
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
        scan = _editing.localizar_simbolo_no_projeto(
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
                    max_linhas=max(1, int(only.get("line_end") or only.get("line_start") or 1) - int(only.get("line_start") or 1) + 1),
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

    result = _editing.localizar_simbolo(root, rel, symbol)
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
            max_linhas=max(1, int(result["line_end"]) - int(result["line_start"]) + 1),
        )
        result.update(reading); result["simbolo"] = symbol
    except ErroLeituraProjeto as erro:
        if erro.error_code == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(root, str(rel or ""))
        return _falha(erro.error_code, {"message": str(erro), "source": source, "path": rel, "symbol": symbol}, executed=True)
    except (OSError, TypeError, ValueError) as erro:
        return _falha("SYMBOL_READ_FAILED", {"message": str(erro), "source": source, "path": rel, "symbol": symbol}, executed=True)
    return _sucesso(result)


def _tool_read_file(arguments, ctx):
    """Read fresh source through exact pages, never through a prompt-sized knowledge ceiling.

    With no explicit range, the declared scope is the whole file and the first
    page is materialized.  With an explicit large range, that whole requested
    range remains reachable, but only one configured page is materialized now;
    the exact remainder becomes an Observation Frontier.
    """
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _source_unavailable(arguments)
    caminho_relativo = arguments["path"]
    if _self_runtime_path_blocked(arguments, caminho_relativo):
        return _falha("SELF_RUNTIME_STATE_READ_BLOCKED", "self analysis cannot read live workspace/memory/context runtime state", retryable=False, failure_scope="resource", failure_resource=str(caminho_relativo))
    default_page = _FILE_PAGE_LINES
    has_start = arguments.get("line_start") is not None
    has_end = arguments.get("line_end") is not None
    if has_start != has_end:
        return _falha("INVALID_ARGUMENT", "line_start e line_end devem ser informados juntos")
    line_start = int(arguments.get("line_start") or 1)
    requested_end = int(arguments.get("line_end")) if has_end else None
    if requested_end is not None and requested_end < line_start:
        return _falha("INVALID_ARGUMENT", "line_end deve ser maior ou igual a line_start", executed=True)

    # One physical page is the materialization budget.  A larger explicit range
    # is preserved as scope and continued exactly; it is never silently cropped.
    first_page_end = line_start + default_page - 1
    if requested_end is not None:
        first_page_end = min(first_page_end, requested_end)
    try:
        leitura = ler_faixa_projeto(
            caminho_projeto, caminho_relativo, line_start, first_page_end,
            max_linhas=max(1, first_page_end-line_start+1),
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {"INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE", "RANGE_OUT_OF_BOUNDS"} else erro.error_code
        if codigo == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(caminho_projeto, caminho_relativo)
        return _falha(codigo, erro.detail, executed=True)

    leitura = dict(leitura)
    physical_end = int(leitura.get("line_end") or first_page_end)
    total = int(leitura.get("total_lines") or physical_end)
    target_end = total if requested_end is None else min(total, requested_end)
    remaining_lines = max(0, target_end - physical_end)
    leitura["requested_line_start"] = line_start
    leitura["requested_line_end"] = target_end
    leitura["truncated"] = remaining_lines > 0
    leitura["file_continues"] = physical_end < total
    leitura["materialization_page_lines"] = default_page

    frontiers=[]
    ledger=(ctx or {}).get("observation_ledger")
    if remaining_lines > 0 and isinstance(ledger,dict):
        ranges=[]
        cursor=physical_end+1
        while cursor<=target_end:
            finish=min(target_end,cursor+default_page-1)
            ranges.append({"line_start":cursor,"line_end":finish})
            cursor=finish+1
        handle=register_snapshot_handle(
            ledger, kind="read_file.ranges",
            payload={"kind":"read_file_ranges","source":_source_name(arguments),"path":caminho_relativo,"items":ranges},
            reality_epoch=int((ctx or {}).get("reality_epoch") or 0), source_capability="read_file",
            description=f"Continue {caminho_relativo} after line {physical_end} through line {target_end}", page_size=1,
        )
        frontiers.append({
            "kind":"material_continuation","at":"file","count":remaining_lines,
            "reason":f"requested file scope continues after line {physical_end} through line {target_end}",
            "handle":handle["id"],
        })
    return _sucesso(leitura, frontiers=frontiers)

def _tool_list_tree(arguments, ctx):
    """List a live tree page. Page size is not a scan/knowledge ceiling."""
    caminho_projeto = _caminho_fonte(ctx, arguments)
    if not caminho_projeto:
        return _source_unavailable(arguments)
    requested_page = max(1, int(arguments.get("limit") or _TREE_PAGE_ENTRIES))
    page_size = requested_page
    profundidade = int(arguments["depth"]) if arguments.get("depth") is not None else None
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
    resultado = _editing.rodar_testes_projeto(caminho_projeto, cfg_testes, scope=arguments.get("scope"))
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
    page_chars = _GIT_DIFF_PAGE_CHARS
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
    result = _sandbox.executar_comando_livre_no_sandbox(
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
        detail = _sandbox.export_active_sandbox_zip(
            eyle_root, arguments["filename"],
            archive_root=arguments.get("archive_root"),
            timeout_seconds=int(arguments.get("timeout_seconds") or 120),
        )
    except _sandbox.ErroSandbox as error:
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
