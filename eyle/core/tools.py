#!/usr/bin/env python3
"""Executable tool registry for the LLM-first core.

The model chooses tools; this module validates arguments, executes live workspace
operations, and always returns one standard result envelope. It contains no
semantic routing or alternate reasoning path. READ/EXEC tools run directly.
WRITE tools are invoked by the runtime only after a successful dry-run and an
explicit user confirmation.

``ctx`` supplies the validated config and the live project root. Indexed retrieval is not required.
"""
import json
import os
import re
import sys
import subprocess

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

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
from eyle.core.execution_trace import build_execution_trace, filter_execution_trace  # noqa: E402
from eyle.core.code_relations import analyze_symbol_relations  # noqa: E402
from eyle.core.observation_contract import (  # noqa: E402
    materialize_snapshot_handle, normalize_effect, register_snapshot_handle, result_observation_fields,
)
from eyle.core.sandbox import executar_comando_livre_no_sandbox  # noqa: E402
from eyle.core.workspace_policy import (  # noqa: E402
    build_protected_resource_index, is_protected_workspace_resource, protected_resource_info,
)

PROJECT_BASE_DIR = os.path.dirname(BASE_DIR)
MEMORY_DIR = os.path.join(PROJECT_BASE_DIR, "memory")

_CAMPOS_RESULTADO = ("status", "ok", "executed", "changed", "error_code", "detail", "retryable", "failure_scope", "failure_resource", "observations", "coverage", "frontiers", "handles")


def _resultado(status, ok, executed, changed=False, error_code=None, detail=None, retryable=None,
               failure_scope=None, failure_resource=None, observations=None, coverage=None, frontiers=None, handles=None):
    """Canonical Rev5.7 tool result envelope.

    The physical status fields remain mandatory. Objective observation fields are
    always present but may be empty, so every capability shares one Runtime
    contract without forcing domain-specific payloads onto simple tools.
    """
    observation_fields = result_observation_fields(
        observations=observations, coverage=coverage, frontiers=frontiers, handles=handles,
    )
    return {
        "status": status, "ok": bool(ok), "executed": bool(executed),
        "changed": bool(changed), "error_code": error_code, "detail": detail,
        "retryable": None if retryable is None else bool(retryable),
        "failure_scope": str(failure_scope) if failure_scope else None,
        "failure_resource": str(failure_resource) if failure_resource else None,
        **observation_fields,
    }


def _sucesso(detail=None, changed=False, *, observations=None, coverage=None, frontiers=None, handles=None):
    if isinstance(detail, dict):
        if observations is None: observations = detail.get("observations")
        if coverage is None: coverage = detail.get("coverage")
        if frontiers is None: frontiers = detail.get("frontiers")
        if handles is None: handles = detail.get("handles")
    return _resultado(
        "success", True, True, changed=changed, detail=detail,
        observations=observations, coverage=coverage, frontiers=frontiers, handles=handles,
    )


def _falha(error_code, detail, executed=False, changed=False, retryable=None, *, failure_scope=None, failure_resource=None):
    return _resultado(
        "failed", False, executed, changed=changed,
        error_code=error_code, detail=detail, retryable=retryable,
        failure_scope=failure_scope, failure_resource=failure_resource,
    )


def _pulado(detail, error_code=None):
    return _resultado("skipped", True, False, error_code=error_code, detail=detail)


def _caminho_projeto(ctx):
    """Extrai caminho_origem do projeto ativo."""
    projeto = (ctx or {}).get("projeto") or {}
    return projeto.get("caminho_origem")


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


def _searchable_files(root):
    """Return the canonical readable-file universe plus protected-resource count."""
    ignored_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    files = []
    protected = 0
    protected_index = build_protected_resource_index(root)
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in ignored_dirs)
        for name in sorted(names):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            if not os.path.isfile(path):
                continue
            if is_protected_workspace_resource(root, rel, index=protected_index):
                protected += 1
                continue
            files.append(rel)
    return files, protected


def _canonicalize_search_matches(items, *, root, query):
    """Return the complete canonical match universe for one literal query.

    Backend differences are normalized before any model-facing projection.  No
    semantic relevance ranking occurs here.  The Runtime may later group and
    bound the projection, but the physical match universe is not truncated
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


def _diverse_search_projection(grouped_by_file, file_order, max_ranges):
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
    """Exhaust one literal search objectively, then materialize a diverse bounded projection."""
    query = arguments["query"].strip()
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    config = (ctx or {}).get("config") or {}
    agent_cfg = config.get("agent", {})
    max_lines = max(7, int(agent_cfg.get("max_search_range_lines", 16) or 16))
    max_matches = max(1, int(agent_cfg.get("max_search_matches", 40) or 40))
    max_ranges = max(1, int(agent_cfg.get("max_search_ranges", 12) or 12))

    searchable_files, protected_resources = _searchable_files(root)
    try:
        raw_matches = _search_matches_with_rg(root, query, searchable_files)
        backend = "ripgrep-json"
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        raw_matches = _search_matches_fallback(root, query, searchable_files)
        backend = "python-fallback"

    matches_observed = len(raw_matches)
    grouped_by_file, file_order, ranges_observed = _group_all_search_ranges(raw_matches, max_lines)
    selected_ranges, remaining_ranges = _diverse_search_projection(grouped_by_file, file_order, max_ranges)
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

    handles = []
    frontiers = []
    handle_store = (ctx or {}).get("observation_handles")
    if remaining_ranges:
        payload = {
            "query": query,
            "items": remaining_ranges,
            "kind": "search_range_locator",
        }
        if isinstance(handle_store, dict):
            handle = register_snapshot_handle(
                handle_store,
                kind="search_code.ranges",
                payload=payload,
                workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
                source_tool="search_code",
                description=f"Remaining objective source ranges for literal search {query!r}",
                page_size=max_ranges,
            )
            handles.append(handle)
            frontiers.append({
                "kind": "projection_continuation",
                "at": "workspace_search",
                "count": len(remaining_ranges),
                "reason": "additional objectively matched source ranges remain behind a continuation handle",
                "handle": handle["id"],
            })
        else:
            frontiers.append({
                "kind": "projection_boundary",
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
    # when only a bounded projection is materialized to the Main LLM.
    scope_complete = not read_failures
    coverage_complete = scope_complete and protected_resources == 0
    projection_complete = not remaining_ranges and not read_failures
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
        "results": results,
        "materialized_files": files,
        "matches_observed": matches_observed,
        "matches_materialized": sum(len(item.get("match_lines") or []) for item in results),
        "files_with_matches": len(file_order),
        "file_match_distribution": distribution,
        "distribution_truncated": len(distribution) < len(file_match_counts),
        "ranges_observed": ranges_observed,
        "ranges_materialized": len(results),
        "projection_complete": projection_complete,
        "scope_complete": scope_complete,
        "coverage_complete": coverage_complete,
        "coverage_scope": "all_workspace_files" if protected_resources == 0 else "readable_workspace_files",
        "protected_resources_excluded": protected_resources,
        "backend": backend,
        "read_failures": read_failures,
        "frontiers": frontiers,
        "handles": handles,
    }
    return _sucesso(detail, frontiers=frontiers, handles=handles)

def _tool_symbol_relations(arguments, ctx):
    """Return local relations or a query-shaped structural reachability observation."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    path = arguments.get("path")
    if path and is_protected_workspace_resource(root, str(path), index=build_protected_resource_index(root)):
        return _protected_resource_failure(root, str(path))
    config = (ctx or {}).get("config") or {}
    agent_cfg = config.get("agent") or {}
    query = str(arguments.get("query") or "relations")
    # Reachability depth is Runtime-owned in Rev5.8. The resolved graph is
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
        handle_store = (ctx or {}).get("observation_handles")
        handles = []
        payloads = list(detail.pop("continuation_payloads", []) or [])
        if isinstance(handle_store, dict):
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                handle = register_snapshot_handle(
                    handle_store, kind=f"symbol_relations.{payload.get('frontier_kind') or 'continuation'}",
                    payload=payload, workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
                    source_tool="symbol_relations",
                    description=f"Continuation from symbol_relations for {arguments.get('symbol')}",
                    page_size=12,
                )
                handles.append(handle)
                index = len(handles) - 1
                for frontier in detail.get("frontiers") or []:
                    if isinstance(frontier, dict) and frontier.get("continuation_index") == index:
                        frontier.pop("continuation_index", None)
                        frontier["handle"] = handle["id"]
                        if summary.get("count") is not None:
                            frontier.setdefault("count", summary.get("count"))
        else:
            for frontier in detail.get("frontiers") or []:
                if isinstance(frontier, dict): frontier.pop("continuation_index", None)
        detail["handles"] = handles
        return _sucesso(detail, handles=handles)
    except (OSError, ValueError) as error:
        return _falha("RELATION_SCAN_FAILED", str(error), executed=True)


def _tool_expand_observation(arguments, ctx):
    """Materialize one bounded page behind an opaque observation handle."""
    store = (ctx or {}).get("observation_handles")
    if not isinstance(store, dict):
        return _falha("HANDLE_STORE_UNAVAILABLE", "observation handle store unavailable", executed=False, retryable=False)
    handle_id = str(arguments.get("handle") or "")
    if not handle_id.startswith("handle:"):
        return _falha(
            "INVALID_HANDLE_FORMAT",
            "use the exact opaque handle:* id returned by an observation frontier",
            executed=False, retryable=True,
        )
    materialized, error = materialize_snapshot_handle(
        store, handle_id, workspace_epoch=int((ctx or {}).get("workspace_epoch") or 0),
    )
    if error:
        # HANDLE_NOT_FOUND/HANDLE_STALE invalidate one continuation reference,
        # not the expand_observation capability. A current exact handle may still
        # be used later in the same job.
        return _falha(
            error,
            "this observation handle cannot be materialized; use an exact current handle:* id returned by a frontier",
            executed=True, retryable=True,
        )
    payload = materialized.get("payload") if isinstance(materialized, dict) else None
    observations = []
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        observations = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    elif isinstance(payload, list):
        observations = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        observations = [payload]
    detail = dict(materialized or {})
    detail["observations"] = observations
    return _sucesso(
        detail, observations=observations, coverage=detail.get("coverage"),
        frontiers=detail.get("frontiers"), handles=detail.get("handles"),
    )


def _tool_find_symbol(arguments, ctx):
    """Locate a symbol in a known file or across the live project."""
    root=_caminho_projeto(ctx)
    if not root: return _falha("WORKSPACE_NOT_AVAILABLE","nenhum workspace ativo")
    symbol=arguments["symbol"]
    rel=arguments.get("path")
    protected_index = build_protected_resource_index(root)
    if rel and is_protected_workspace_resource(root, str(rel), index=protected_index):
        return _protected_resource_failure(root, str(rel))
    result=localizar_simbolo(root,rel,symbol) if rel else localizar_simbolo_no_projeto(root,symbol)
    if result is None or (isinstance(result, list) and not result):
        return _falha("SYMBOL_NOT_FOUND",f"símbolo '{symbol}' não encontrado",executed=True)
    if isinstance(result,list): result=result[0] if len(result)==1 else {"matches":result}
    if result.get("matches") is not None:
        safe_matches = [
            item for item in (result.get("matches") or [])
            if isinstance(item, dict) and not is_protected_workspace_resource(root, str(item.get("file") or ""), index=protected_index)
        ]
        if not safe_matches:
            return _falha("SYMBOL_NOT_FOUND",f"símbolo '{symbol}' não encontrado",executed=True)
        clone = dict(result)
        clone["matches"] = safe_matches
        return _sucesso(clone)
    result=dict(result); rel=result.get("file") or rel; result["file"]=rel; result["simbolo"]=symbol
    try:
        reading=ler_faixa_projeto(root,rel,int(result["line_start"]),int(result["line_end"]),max_linhas=((ctx or {}).get("config") or {}).get("agent",{}).get("max_file_read_lines",400))
        result.update(reading); result["simbolo"]=symbol
    except ErroLeituraProjeto as erro:
        if erro.error_code == "PROTECTED_RESOURCE_READ_BLOCKED":
            return _protected_resource_failure(root, str(rel or ""))
    except Exception:
        pass
    return _sucesso(result)


def _tool_read_file(arguments, ctx):
    """Read one fresh bounded file window; omitted range means the initial window."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    caminho_relativo = arguments["path"]
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
    caminho_projeto = _caminho_projeto(ctx)
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
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(measure_project_stats(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_count_tokens(arguments, ctx):
    """Measure project text and convert it to a truthful token estimate."""
    root = _caminho_projeto(ctx)
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
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(inspect_project_signals(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_agent_info(arguments, ctx):
    """Expose full registered capability separately from current-call availability."""
    config = (ctx or {}).get("config") or {}
    available_names = {str(name) for name in ((ctx or {}).get("available_tools") or [])}
    registered = []
    for name, item in sorted(TOOLS.items()):
        registered.append({
            "name": name,
            "category": item.get("category", "READ_ONLY"),
            "effects": list(item.get("effects") or ["NONE"]),
            "description": item.get("description", ""),
        })
    available = [item for item in registered if item.get("name") in available_names]
    return _sucesso({
        "app_version": config.get("app_version"),
        "revision": config.get("revision"),
        "registered_tools": registered,
        "available_tools": available,
        "write_enabled": bool(((config.get("codar") or {}).get("ativado", True))),
        "write_confirmation_required": True,
        "note": (
            "registered_tools is the complete executable registry; available_tools "
            "is only the subset callable in the current Main-LLM call. Workspace writes are supervised."
        ),
    })


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


def _tool_execution_trace(arguments, ctx):
    """Inspect sanitized factual execution history for the current or one persisted job."""
    section = str(arguments.get("section") or "all").strip().lower()
    turn = arguments.get("turn")
    limit = int(arguments.get("limit") or 100)
    requested_job_id = arguments.get("job_id")

    current = (ctx or {}).get("execution_trace")
    current_job_id = None
    if isinstance(current, dict):
        current_job_id = ((current.get("summary") or {}).get("job_id")
                          if isinstance(current.get("summary"), dict) else None)

    if requested_job_id is None or (current_job_id is not None and int(requested_job_id) == int(current_job_id)):
        if not isinstance(current, dict):
            return _falha("EXECUTION_TRACE_UNAVAILABLE", "o trace da sessão atual não está disponível neste contexto")
        trace = current
    else:
        try:
            from eyle.runtime import queue as runtime_queue
        except Exception as error:
            return _falha("EXECUTION_TRACE_UNAVAILABLE", f"não foi possível acessar o histórico persistido: {error}")
        registro = runtime_queue.obter(int(requested_job_id))
        if not isinstance(registro, dict):
            return _falha("JOB_NOT_FOUND", f"job #{int(requested_job_id)} não foi encontrado")
        resultado = registro.get("resultado") if isinstance(registro.get("resultado"), dict) else {}
        details = resultado.get("details") if isinstance(resultado.get("details"), dict) else {}
        if not details:
            return _falha(
                "EXECUTION_TRACE_NOT_READY",
                f"job #{int(requested_job_id)} ainda não possui detalhes de execução persistidos",
            )
        progresso = registro.get("progresso") if isinstance(registro.get("progresso"), dict) else {}
        trace = build_execution_trace(
            details,
            job_id=int(requested_job_id),
            status=registro.get("status"),
            created_at=registro.get("criado_em"),
            started_at=registro.get("iniciado_em"),
            completed_at=registro.get("concluido_em"),
            duration_seconds=progresso.get("elapsed_seconds"),
            limit=max(100, limit),
        )
    try:
        return _sucesso(filter_execution_trace(trace, section=section, turn=turn, limit=limit))
    except (TypeError, ValueError) as error:
        return _falha("INVALID_ARGUMENT", str(error))


def _tool_git_status(arguments, ctx):
    """Inspect Git working-tree state without modifying the repository."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    result = inspect_git_status(root, max_entries=int(arguments.get("max_entries") or 200))
    if result.get("ok"):
        return _sucesso(result)
    return _falha(result.get("error_code") or "GIT_STATUS_FAILED", result.get("detail"), executed=True)


def _tool_git_diff(arguments, ctx):
    """Inspect a bounded Git diff; raw diff is available to the LLM but not public history."""
    root = _caminho_projeto(ctx)
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
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    config = (ctx or {}).get("config") or {}
    sandbox_cfg = dict(((config.get("agent") or {}).get("sandbox") or {}))
    result = executar_comando_livre_no_sandbox(
        root, arguments["command"], sandbox_cfg, cwd=arguments.get("cwd") or ".",
        timeout_segundos=arguments.get("timeout_seconds"),
    )
    if result.get("executado") is not True:
        return _falha("SANDBOX_UNAVAILABLE", result.get("erro") or "sandbox indisponivel", retryable=False)
    detail = {
        "command": arguments["command"], "cwd": result.get("cwd"), "returncode": result.get("codigo"),
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
    """Store one evidence-backed fact outside the source workspace."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    evidence = (ctx or {}).get("evidence") or {}
    evidence_ids = [str(item) for item in arguments.get("evidence_ids") or []]
    if not evidence_ids:
        return _falha("MEMORY_REQUIRES_EVIDENCE", "informe evidence_ids da tarefa atual")
    missing = [item for item in evidence_ids if item not in evidence]
    if missing:
        return _falha("MEMORY_UNKNOWN_EVIDENCE", ", ".join(missing))
    files = []
    for evidence_id in evidence_ids:
        item = evidence.get(evidence_id) or {}
        if item.get("file") and item.get("file_hash"):
            files.append({"path": item["file"], "file_hash": item["file_hash"]})
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

TOOLS = {
    "calculate": {
        "description": "Evaluate one arithmetic expression deterministically with decimal-safe math.",
        "availability": "global",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Decimal result with exact/approximate and precision metadata.",
        "input_schema": _schema_objeto({
            "expression": {"type": "string", "minLength": 1, "maxLength": 500, "description": "Arithmetic expression containing numeric values and supported operators."},
        }, ["expression"]),
        "fn": _tool_calculate,
    },
    "agent_info": {
        "description": "Return Eyle runtime identity, release metadata, executable tool registry and write policy.",
        "availability": "global",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Runtime identity, complete registered_tools, current-call available_tools and write policy.",
        "caveats": ["registered_tools is the full registry; available_tools is only the current-call subset. Runtime metadata does not prove source-level implementation behavior."],
        "input_schema": _schema_objeto(),
        "fn": _tool_agent_info,
    },
    "project_stats": {
        "description": "Measure safe project text: files, directories, lines, characters, bytes, extensions, and languages.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Counts for files, directories, lines, characters, bytes, extensions and languages.",
        "caveats": ["Measurements only; no importance ranking or code-behavior diagnosis."],
        "input_schema": _schema_objeto(),
        "fn": _tool_project_stats,
    },
    "count_tokens": {
        "description": "Measure token count or a truthful token estimate for safe project text.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Token count/estimate, method, exactness, measured characters and scan completeness.",
        "caveats": ["Measures project text, not actual LLM request usage or token waste."],
        "input_schema": _schema_objeto({
            "path": {"type": "string", "minLength": 1, "description": "Optional project-relative file or directory to measure instead of the whole project."},
            "tokenizer": {"type": "string", "minLength": 1, "description": "Optional tokenizer/model identifier; if unavailable, the configured truthful fallback is reported."},
        }),
        "fn": _tool_count_tokens,
    },
    "inspect_project": {
        "description": "Inspect objective project structure and relation signals such as languages, entrypoints, imports, tests, CI, frameworks and manifests.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Languages, entrypoints, imports, tests, CI, frameworks, manifests and relation signals.",
        "caveats": ["Objective static signals only; no importance ranking, runtime confirmation or bug proof."],
        "input_schema": _schema_objeto(),
        "fn": _tool_inspect_project,
    },
    "list_tree": {
        "description": "List the fresh project tree with limit, depth, filter, and ignored-item counts.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Project-relative tree entries plus depth, truncation, ignored-item metadata and protected-resource visibility markers.",
        "input_schema": _schema_objeto({
            "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of tree entries to return before marking the result truncated."},
            "depth": {"type": "integer", "minimum": 1, "description": "Maximum directory depth to traverse from the project root."},
            "filter": {"type": "string", "minLength": 1, "description": "Optional filename/path glob-style filter applied to returned tree entries."},
        }),
        "fn": _tool_list_tree,
    },
    "search_code": {
        "description": "Find exact literal text/code matches in live project files and return fresh verifiable ranges.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Complete literal-match coverage metadata plus a deterministic diverse inline projection of fresh source ranges and continuation handles when more objective ranges exist.",
        "caveats": ["Literal text/code search only; never semantic relevance ranking. Search exhausts the readable scope mechanically; projection_complete describes only the bounded inline projection, while coverage_complete describes the searched scope. Protected credential/private-key resources and their physical aliases are excluded and reported as a coverage boundary."],
        "input_schema": _schema_objeto(
            {"query": {"type": "string", "minLength": 1, "description": "Literal text or code fragment to match exactly in project files."}}, ["query"],
        ),
        "fn": _tool_search_code,
    },
    "symbol_relations": {
        "description": "Inspect structural relationships around a code symbol: calls, registrations/bindings, imports, references and optional root-to-symbol paths.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "AST-aware Python call/binding/registration relations, optional directed projections/root paths, optional literal text references, unresolved dynamic sites and coverage metadata.",
        "caveats": ["Reports structural facts only; it never labels code live/dead/legacy or proves runtime behavior. Static resolution can be incomplete for dynamic dispatch, reflection, plugins or ambiguous names."],
        "input_schema": _schema_objeto({
            "symbol": {"type": "string", "minLength": 1, "description": "Code symbol name to inspect."},
            "query": {"type": "string", "enum": ["relations", "reachability"], "description": "relations returns local structural facts; reachability asks for a root-to-symbol structural path and only material frontiers."},
            "path": _CAMINHO,
            "roots": {"type": "array", "items": {"type": "string", "minLength": 1}, "description": "Optional caller/root symbols, node ids or project-relative files from which to test structural reachability."},
            "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "description": "Project only the requested relation direction; default both."},
            "include_text_references": {"type": "boolean", "description": "Include literal text-reference rows. Default false because structural queries usually do not need them."},
            "max_depth": {"type": "integer", "minimum": 1, "maximum": 32, "description": "Local relations only. Directed reachability ignores/canonicalizes this hint and exhausts the finite resolved graph automatically."},
            "max_edges": {"type": "integer", "minimum": 10, "maximum": 500, "description": "Local relation projection limit. Directed reachability canonicalizes this hint and returns only path/coverage/frontier material."},
        }, ["symbol"]),
        "fn": _tool_symbol_relations,
    },
    "expand_observation": {
        "description": "Materialize one bounded continuation page from an opaque handle returned by a prior objective observation.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "A bounded snapshot continuation with objective observations, coverage, any remaining frontier and a next handle when more is available.",
        "caveats": ["Rev5.7 handles address observation snapshots, not guaranteed-live resources; handles become stale after the Runtime workspace epoch changes."],
        "input_schema": _schema_objeto({
            "handle": {"type": "string", "minLength": 8, "pattern": r"^handle:[A-Za-z0-9._:-]+$", "description": "Exact opaque handle:* continuation id previously returned by a tool observation. Evidence IDs and observation IDs are not handles."},
        }, ["handle"]),
        "fn": _tool_expand_observation,
    },
    "find_symbol": {
        "description": "Locate a symbol in a known file or across the live project.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Fresh symbol definition/location and verifiable source range metadata.",
        "caveats": ["Locates definitions/locations; does not guarantee every runtime reference or call site."],
        "input_schema": _schema_objeto({
            "path": _CAMINHO,
            "symbol": {"type": "string", "minLength": 1, "description": "Exact code symbol name whose definition/location should be found."},
        }, ["symbol"]),
        "fn": _tool_find_symbol,
    },
    "read_file": {
        "description": "Read a bounded beginning portion of one project file with verifiable hashes and line metadata.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded file content, truncation state, line metadata and hashes.",
        "caveats": ["The returned content may be truncated by configured read limits. Protected credential/private-key resources and their physical aliases deny content access; normal files are never blocked by content heuristics."],
        "input_schema": _schema_objeto({
            "path": _CAMINHO,
            "line_start": _LINHA,
            "line_end": _LINHA,
        }, ["path"]),
        "fn": _tool_read_file,
    },
    "run_command": {
        "description": "Run an arbitrary shell command in a writable isolated project snapshot that persists for this job and may access the network.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["SANDBOX_EXEC", "SANDBOX_WRITE", "NETWORK"],
        "returns": "Exit code, bounded combined output, sandbox backend and isolation facts. Sandbox mutations never alter the real workspace.",
        "caveats": ["No command allowlist inside the sandbox. Docker is the recommended backend and may auto-pull the configured/default image; Bubblewrap remains supported. Protected credential/private-key resources and their physical aliases are omitted from the snapshot; normal source is preserved regardless of secret-like content. Docker container state and the writable project snapshot persist for the job. Real workspace writes still require WriteTransaction confirmation."],
        "input_schema": _schema_objeto({
            "command": {"type": "string", "minLength": 1, "maxLength": 8000, "description": "Shell command to execute inside the isolated snapshot."},
            "cwd": {"type": "string", "minLength": 1, "description": "Optional project-relative working directory inside the sandbox snapshot."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Optional command timeout not exceeding the configured sandbox maximum."},
        }, ["command"]),
        "fn": _tool_run_command,
    },
    "memory_search": {
        "description": "Search hash-validated external memory entries associated with the active project.",
        "availability": "workspace",
        "produces_source_records": False,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded hash-validated prior project-memory entries.",
        "caveats": ["Prior memory is context, not proof of current live source state."],
        "input_schema": _schema_objeto({
            "query": {"type": "string", "description": "Text used to match relevant external project-memory entries."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum number of matching memory entries to return."},
        }),
        "fn": _tool_memory_search,
    },
    "memory_store": {
        "description": "Store one useful evidence-backed project fact in external memory.",
        "availability": "workspace",
        "produces_source_records": False,
        "category": "EDIT",
        "effects": ["MEMORY_WRITE"],
        "returns": "The external project-memory entry that was stored.",
        "caveats": ["Persists project memory only and requires current-task evidence references."],
        "input_schema": _schema_objeto({
            "text": {"type": "string", "minLength": 1, "description": "Compact project fact to persist in external project memory."},
            "kind": {"type": "string", "description": "Optional memory category; defaults to fact when omitted."},
            "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "description": "Current-task evidence IDs that substantiate the stored fact."},
        }, ["text", "evidence_ids"]),
        "fn": _tool_memory_store,
    },
    "run_tests": {
        "description": "Run the detected test suite in the sandbox; optionally focus pytest on one safe relative file or directory.",
        "availability": "tests",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["EXEC"],
        "returns": "Runner command, status, return code, concise summary, bounded output and runner diagnostics.",
        "caveats": ["Does not install a missing runner or prove untested behavior; tests may create incidental temporary/cache artifacts."],
        "input_schema": _schema_objeto({
            "scope": {"type": "string", "minLength": 1, "description": "Optional safe project-relative pytest file or directory; omitted means the detected full suite."},
        }),
        "fn": _tool_run_tests,
    },
    "execution_trace": {
        "description": "Read sanitized facts from current or past executions.",
        "availability": "global",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded sanitized execution trace.",
        "caveats": ["Observable facts only: no diagnosis, chain-of-thought, raw prompts, source/patch/memory bodies or secrets."],
        "input_schema": _schema_objeto({
            "job_id": {"type": "integer", "minimum": 1, "description": "Past job id; omit=current."},
            "turn": {"type": "integer", "minimum": 1, "description": "Turn filter."},
            "section": {"type": "string", "minLength": 1, "maxLength": 20, "description": "Trace section."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Event limit."},
        }),
        "fn": _tool_execution_trace,
    },
    "git_status": {
        "description": "Inspect current Git working-tree state without changing files; returns branch and compact modified/added/deleted/untracked entries.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Branch, clean flag, category counts and bounded changed-path entries.",
        "caveats": ["Status metadata only; it does not include patch contents."],
        "input_schema": _schema_objeto({
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of changed-path status entries to return."},
        }),
        "fn": _tool_git_status,
    },
    "git_diff": {
        "description": "Inspect a bounded read-only Git diff for the workspace or one relative path, optionally staged.",
        "availability": "workspace",
        "produces_source_records": True,
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Changed files, added/removed line counts, bounded diff text and truncation state.",
        "caveats": ["Bounded output may omit truncated hunks."],
        "input_schema": _schema_objeto({
            "path": {"type": "string", "minLength": 1, "description": "Optional project-relative path whose Git diff should be inspected."},
            "staged": {"type": "boolean", "description": "When true inspect staged/index changes; otherwise inspect unstaged working-tree changes."},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Number of unchanged context lines around each returned diff hunk."},
        }),
        "fn": _tool_git_diff,
    },
}

# Limites ficam no proprio registro. O catalogo resolve as chaves de
# configuracao para valores numericos antes de chegar ao modelo.
for _entrada_tool in TOOLS.values():
    _entrada_tool.setdefault("limits", {})
    _effects = set(_entrada_tool.get("effects") or [])
    if _entrada_tool.get("category") == "EDIT":
        _entrada_tool["effect"] = "mutate"
    elif any(str(item) not in {"NONE"} for item in _effects):
        _entrada_tool["effect"] = "execute"
    else:
        _entrada_tool["effect"] = "observe"
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
                "category": entrada.get("category", "READ_ONLY"),
                "effect": normalize_effect(entrada.get("effect")),
                "effects": list(entrada.get("effects") or ["NONE"]),
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
    if nome == "symbol_relations" and str(normalizados.get("query") or "relations").strip().lower() == "reachability":
        # Rev5.8 clean contract: graph traversal depth/result-size tuning is
        # physical Runtime work, not a semantic decision for Main. Calls that
        # differ only by these obsolete hints collapse to one observation.
        normalizados.pop("max_depth", None)
        normalizados.pop("max_edges", None)
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
        return resultado
    except Exception as e:
        return _falha("TOOL_EXECUTION_ERROR", f"tool '{nome}' falhou ao executar: {e}", executed=True)
