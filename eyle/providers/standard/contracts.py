#!/usr/bin/env python3
"""Observation, coverage, projection and material hooks for the standard provider."""
import copy
import json
import os
import re

from eyle.contracts.observation import result_observation_fields
from eyle.providers.standard.text_hash import hash_texto
from eyle.providers.standard.workspace_policy import build_protected_resource_index, protected_resource_info
from eyle.providers.standard.common import _standard_context, _source_name
from eyle.providers.standard.file_scope import normalize_scope_selectors
from eyle.providers.standard.workspace_io import ErroLeituraProjeto, ler_faixa_projeto

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
            # None means exhaustive traversal. page_size is a presentation size
            # only and therefore remains part of the observation identity.
            "max_depth": (int(arguments["max_depth"]) if arguments.get("max_depth") is not None else None),
            "page_size": int(arguments.get("page_size") or 60),
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
        from eyle.providers.standard.registry import CAPABILITIES
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


def _compact_inspect_detail(detail, grounding_ids=None):
    """Small structural map for Main/UI; the complete scan stays in Material.

    ``inspect_project`` is discovery, not source-body delivery. Exposing hundreds
    of import edges and test paths directly in the next cognition turn makes a
    quick map more expensive than reading the implementation that actually
    matters. The current contract keeps objective counts and navigation signals
    while preserving the full result behind canonical Material/Evidence.
    """
    detail = detail if isinstance(detail, dict) else {}
    tests = detail.get("test_signals") if isinstance(detail.get("test_signals"), dict) else {}
    ci = detail.get("ci_signals") if isinstance(detail.get("ci_signals"), dict) else {}
    relations = detail.get("relation_signals") if isinstance(detail.get("relation_signals"), dict) else {}
    frameworks = []
    for item in (detail.get("framework_signals") or [])[:16]:
        if not isinstance(item, dict):
            continue
        sources = [str(v) for v in (item.get("sources") or [])]
        frameworks.append({
            "name": item.get("name"),
            "source_count": len(sources),
            "sources": sources[:4],
        })
    out = {
        "view": "compact_structural_map",
        "structural_only": True,
        "scan_complete": bool(detail.get("scan_complete")),
        "file_count": int(detail.get("file_count") or 0),
        "directory_count": int(detail.get("directory_count") or 0),
        "languages": copy.deepcopy(detail.get("languages") or {}),
        "entrypoint_signals": [copy.deepcopy(item) for item in (detail.get("entrypoint_signals") or [])[:16] if isinstance(item, dict)],
        "dependency_manifests": [str(v) for v in (detail.get("dependency_manifests") or [])[:12]],
        "config_files": [str(v) for v in (detail.get("config_files") or [])[:12]],
        "test_signals": {
            "has_tests": bool(tests.get("has_tests")),
            "count": int(tests.get("count") or 0),
        },
        "ci_signals": {
            "has_ci": bool(ci.get("has_ci")),
            "files": [str(v) for v in (ci.get("files") or [])[:8]],
        },
        "framework_signals": frameworks,
        "relation_signals": {
            "local_import_edge_count": int(relations.get("local_import_edge_count") or 0),
            # This is an objective import-count navigation signal, not semantic
            # importance/relevance ranking.  Keep a small deterministic page.
            "most_imported_files": [copy.deepcopy(item) for item in (relations.get("most_imported_files") or [])[:12] if isinstance(item, dict)],
            "route_files": [copy.deepcopy(item) for item in (relations.get("route_files") or [])[:12] if isinstance(item, dict)],
            "syntax_error_files": [copy.deepcopy(item) for item in (relations.get("syntax_error_files") or [])[:12] if isinstance(item, dict)],
            "full_import_edges_materialized": False,
        },
        "full_detail_available_as_evidence": True,
    }
    ids = [str(v) for v in (grounding_ids or []) if str(v)]
    if ids:
        out["grounding_id"] = ids[0]
    return out


def _public_result_inspect(result):
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return _compact_inspect_detail(detail)


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
    return _compact_inspect_detail(detail, grounding_ids)


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
        normalized.pop("page_size", None)
    return normalized
