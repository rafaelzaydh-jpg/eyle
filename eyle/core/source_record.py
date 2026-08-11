"""Canonical SourceRecord ledger for objectively materialized tool facts.

SourceRecords are physical/citable materializations produced by capabilities.
They are not Evidence until the Main LLM explicitly selects them in an
Investigation update or Final grounding set.  The Runtime owns identity,
freshness and persistence only; it never promotes SourceRecords by inferred
relevance.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Iterable, List, Optional

from .text_hash import hash_texto
from .workspace_io import ErroLeituraProjeto, ler_faixa_projeto


def empty_ledger() -> Dict[str, Any]:
    return {"items": {}}


def items(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("items", {})
    return value if isinstance(value, dict) else {}


def register_candidates(ledger: Dict[str, Any], candidates: Iterable[Dict[str, Any]]) -> List[str]:
    store = items(ledger)
    ids: List[str] = []
    for candidate in candidates:
        item = dict(candidate or {})
        if not item.get("file") or not item.get("file_hash"):
            continue
        existing = next((
            record_id for record_id, record in store.items()
            if isinstance(record, dict)
            and record.get("file") == item.get("file")
            and record.get("line_start") == item.get("line_start")
            and record.get("line_end") == item.get("line_end")
            and record.get("file_hash") == item.get("file_hash")
            and record.get("content_hash") == item.get("content_hash")
        ), None)
        record_id = existing or f"src-{len(store)+1:04d}"
        item["id"] = record_id
        store[record_id] = item
        ids.append(record_id)
    return ids


def index_view(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for record_id, item in items(ledger).items():
        if not isinstance(item, dict):
            continue
        entry = {
            "id": record_id,
            "file": item.get("file"),
            "lines": [item.get("line_start"), item.get("line_end")],
        }
        if item.get("source_type"):
            entry["source_type"] = item.get("source_type")
        if item.get("query"):
            entry["query"] = item.get("query")
        out.append(entry)
    return out


def freshest_for_path(ledger: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    normalized = str(path or "").replace("\\", "/")
    for item in reversed(list(items(ledger).values())):
        if isinstance(item, dict) and str(item.get("file") or "").replace("\\", "/") == normalized:
            return item
    return None


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {"items": {
        key: {
            field: copy.deepcopy(value)
            for field, value in item.items()
            if field not in {"content", "numbered_content"}
        }
        for key, item in items(ledger).items() if isinstance(item, dict)
    }}


def rehydrate(ledger: Dict[str, Any], project_root: str, *, max_lines: int) -> None:
    if not project_root or not os.path.isdir(project_root):
        return
    for record_id, item in list(items(ledger).items()):
        if not isinstance(item, dict) or item.get("content") or item.get("numbered_content"):
            continue
        path = str(item.get("file") or "").strip()
        start, end = item.get("line_start"), item.get("line_end")
        if path.startswith("<"):
            item["rehydration_error"] = "SOURCE_RECORD_REEXECUTION_REQUIRED"
            continue
        if not path or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            item["rehydration_error"] = "SOURCE_RECORD_REEXECUTION_REQUIRED"
            continue
        try:
            reading = ler_faixa_projeto(
                project_root, path, start, end,
                max_linhas=max(max_lines, end - start + 1),
            )
        except ErroLeituraProjeto as error:
            item["rehydration_error"] = error.error_code
            continue
        if (
            (item.get("file_hash") and str(item.get("file_hash")) != str(reading.get("file_hash") or ""))
            or (item.get("content_hash") and str(item.get("content_hash")) != str(reading.get("content_hash") or ""))
        ):
            item["rehydration_error"] = "SOURCE_RECORD_STALE"
            item["stale"] = True
            continue
        item.update(reading)
        item.pop("rehydration_error", None)
        item.pop("stale", None)


def candidates_from_tool(tool: str, detail: Any) -> List[Dict[str, Any]]:
    """Convert objective tool materializations into SourceRecord candidates.

    This function performs no semantic ranking.  It only preserves objectively
    citable material returned by the originating capability.
    """
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("results") or [] if isinstance(item, dict)]
        if not candidates and detail.get("scope_complete") is True:
            observation = {
                "query": detail.get("query"),
                "matches_observed": detail.get("matches_observed"),
                "matches_materialized": detail.get("matches_materialized"),
                "ranges_observed": detail.get("ranges_observed"),
                "ranges_materialized": detail.get("ranges_materialized"),
                "files_with_matches": detail.get("files_with_matches"),
                "scope_complete": True,
                "coverage_complete": bool(detail.get("coverage_complete")),
                "coverage_scope": detail.get("coverage_scope"),
                "protected_resources_excluded": int(detail.get("protected_resources_excluded") or 0),
                "read_failures": list(detail.get("read_failures") or []),
                "frontiers": list(detail.get("frontiers") or []),
                "projection_complete": bool(detail.get("projection_complete")),
                "backend": detail.get("backend"),
            }
            content = json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            source_hash = hash_texto(content)
            return [{
                "file": "<search-observation>", "line_start": None, "line_end": None,
                "file_hash": source_hash, "content_hash": source_hash, "content": content,
                "source_type": "search_observation", "query": detail.get("query"),
                "scope_complete": True, "coverage_complete": bool(detail.get("coverage_complete")),
                "coverage_scope": detail.get("coverage_scope"),
                "protected_resources_excluded": int(detail.get("protected_resources_excluded") or 0),
                "matches": 0,
            }]
        return candidates
    if tool in {"read_file", "find_symbol"} and isinstance(detail, dict):
        return [detail]
    if tool == "list_tree" and isinstance(detail, dict) and detail.get("inventory_hash"):
        inventory = json.dumps({
            "entries": detail.get("entries") or [],
            "truncated": bool(detail.get("truncated")),
            "varredura_completa": bool(detail.get("varredura_completa")),
            "filter": detail.get("filter"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return [{
            "file": "<workspace-tree>", "line_start": None, "line_end": None,
            "file_hash": detail.get("inventory_hash"), "content_hash": hash_texto(inventory),
            "content": inventory, "source_type": "workspace_tree",
        }]
    if tool in {"project_stats", "count_tokens", "inspect_project", "symbol_relations", "expand_observation"} and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = detail.get("scan_hash") or detail.get("measurement_hash") or detail.get("inspection_hash") or hash_texto(content)
        return [{
            "file": f"<tool:{tool}>", "line_start": None, "line_end": None,
            "file_hash": source_hash, "content_hash": hash_texto(content), "content": content,
            "source_type": tool,
        }]
    if tool == "agent_info" and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        return [{
            "file": "<agent-runtime>", "line_start": None, "line_end": None,
            "file_hash": source_hash, "content_hash": source_hash, "content": content,
            "source_type": "agent_runtime",
        }]
    if tool in {"calculate", "run_tests", "run_command", "git_status", "git_diff", "execution_trace"} and isinstance(detail, dict):
        content = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = hash_texto(content)
        names = {
            "calculate": "<calculator>", "run_tests": "<runtime-tests>",
            "run_command": "<sandbox-command>", "git_status": "<git-status>",
            "git_diff": "<git-diff>", "execution_trace": "<execution-trace>",
        }
        return [{
            "file": names[tool], "line_start": None, "line_end": None,
            "file_hash": source_hash, "content_hash": source_hash, "content": content,
            "source_type": tool,
        }]
    return []


def register_tool_detail(ledger: Dict[str, Any], tool: str, detail: Any) -> List[str]:
    return register_candidates(ledger, candidates_from_tool(tool, detail))
