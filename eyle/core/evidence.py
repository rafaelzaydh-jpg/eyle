"""Canonical Evidence ledger and lifecycle helpers."""
from __future__ import annotations
import copy, json, os
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
            evidence_id for evidence_id, evidence in store.items()
            if isinstance(evidence, dict)
            and evidence.get("file") == item.get("file")
            and evidence.get("line_start") == item.get("line_start")
            and evidence.get("line_end") == item.get("line_end")
            and evidence.get("file_hash") == item.get("file_hash")
            and evidence.get("content_hash") == item.get("content_hash")
        ), None)
        evidence_id = existing or f"ev-{len(store)+1:04d}"
        item["id"] = evidence_id
        store[evidence_id] = item
        ids.append(evidence_id)
    return ids


def index_view(ledger: Dict[str, Any], investigation: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    store = items(ledger)
    pinned: List[str] = []
    seen = set()
    for target in investigation or []:
        if not isinstance(target, dict):
            continue
        for evidence_id in target.get("evidence_ids") or []:
            evidence_id = str(evidence_id or "").strip()
            if evidence_id and evidence_id in store and evidence_id not in seen:
                pinned.append(evidence_id); seen.add(evidence_id)
    recent = [evidence_id for evidence_id in store if evidence_id not in seen]
    out = []
    for evidence_id in pinned + recent:
        item = store.get(evidence_id)
        if not isinstance(item, dict): continue
        entry = {
            "id": evidence_id, "file": item.get("file"),
            "lines": [item.get("line_start"), item.get("line_end")],
        }
        if evidence_id in seen: entry["pinned"] = True
        if item.get("source_type"):
            entry.update({"source_type": item.get("source_type"), "stage": item.get("stage"), "error_code": item.get("error_code")})
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
        key: {field: copy.deepcopy(value) for field, value in item.items() if field not in {"content", "numbered_content"}}
        for key, item in items(ledger).items() if isinstance(item, dict)
    }}


def rehydrate(ledger: Dict[str, Any], project_root: str, *, max_lines: int) -> None:
    if not project_root or not os.path.isdir(project_root): return
    for evidence_id, item in list(items(ledger).items()):
        if not isinstance(item, dict) or item.get("content") or item.get("numbered_content"): continue
        path = str(item.get("file") or "").strip(); start=item.get("line_start"); end=item.get("line_end")
        if not path or not isinstance(start,int) or not isinstance(end,int) or start < 1 or end < start: continue
        try:
            reading = ler_faixa_projeto(project_root, path, start, end, max_linhas=max(max_lines, end-start+1))
        except ErroLeituraProjeto as error:
            item["rehydration_error"] = error.error_code; continue
        if ((item.get("file_hash") and str(item.get("file_hash")) != str(reading.get("file_hash") or ""))
            or (item.get("content_hash") and str(item.get("content_hash")) != str(reading.get("content_hash") or ""))):
            item["rehydration_error"]="EVIDENCE_STALE"; item["stale"] = True; continue
        item.update(reading); item.pop("rehydration_error",None); item.pop("stale",None)


def candidates_from_tool(tool: str, detail: Any) -> List[Dict[str, Any]]:
    if tool == "search_code" and isinstance(detail, dict):
        candidates = [item for item in detail.get("results") or [] if isinstance(item, dict)]
        if not candidates and detail.get("coverage_complete") is True:
            content = json.dumps({
                "query": detail.get("query"), "matches_observed": detail.get("matches_observed"),
                "matches_returned": detail.get("matches_returned"), "ranges_observed": detail.get("ranges_observed"),
                "ranges_returned": detail.get("ranges_returned"), "coverage_complete": True,
                "truncated": bool(detail.get("truncated")), "backend": detail.get("backend"),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            source_hash = hash_texto(content)
            return [{"file":"<search-observation>","line_start":None,"line_end":None,"file_hash":source_hash,"content_hash":source_hash,"content":content,"source_type":"search_observation","query":detail.get("query"),"coverage_complete":True,"matches":0}]
        return candidates
    if tool in {"read_file", "find_symbol"} and isinstance(detail, dict): return [detail]
    if tool == "list_tree" and isinstance(detail, dict) and detail.get("inventory_hash"):
        inventory=json.dumps({"entries":detail.get("entries") or [],"truncated":bool(detail.get("truncated")),"varredura_completa":bool(detail.get("varredura_completa")),"filter":detail.get("filter")},ensure_ascii=False,sort_keys=True,separators=(",",":"))
        return [{"file":"<workspace-tree>","line_start":None,"line_end":None,"file_hash":detail.get("inventory_hash"),"content_hash":hash_texto(inventory),"content":inventory,"source_type":"workspace_tree"}]
    if tool in {"project_stats","count_tokens","inspect_project","symbol_relations","expand_observation"} and isinstance(detail,dict):
        content=json.dumps(detail,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
        source_hash=detail.get("scan_hash") or detail.get("measurement_hash") or detail.get("inspection_hash") or hash_texto(content)
        return [{"file":f"<tool:{tool}>","line_start":None,"line_end":None,"file_hash":source_hash,"content_hash":hash_texto(content),"content":content,"source_type":tool}]
    if tool == "agent_info" and isinstance(detail,dict):
        content=json.dumps(detail,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str); source_hash=hash_texto(content)
        return [{"file":"<agent-runtime>","line_start":None,"line_end":None,"file_hash":source_hash,"content_hash":source_hash,"content":content,"source_type":"agent_runtime"}]
    if tool in {"calculate","run_tests","run_command","git_status","git_diff","execution_trace"} and isinstance(detail,dict):
        content=json.dumps(detail,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str); source_hash=hash_texto(content)
        names={"calculate":"<calculator>","run_tests":"<runtime-tests>","run_command":"<sandbox-command>","git_status":"<git-status>","git_diff":"<git-diff>","execution_trace":"<execution-trace>"}
        return [{"file":names[tool],"line_start":None,"line_end":None,"file_hash":source_hash,"content_hash":source_hash,"content":content,"source_type":tool}]
    return []


def register_tool_detail(ledger: Dict[str, Any], tool: str, detail: Any) -> List[str]:
    return register_candidates(ledger, candidates_from_tool(tool, detail))


def seed_runtime_failure(ledger: Dict[str, Any], conversation_context: Any) -> List[Dict[str, Any]]:
    """Promote the latest persisted write failure into one citable Evidence item.

    The caller decides whether to expose the returned model-facing observation;
    Evidence identity/lifecycle remains owned here.
    """
    messages = list((conversation_context or {}).get("recent_messages") or []) if isinstance(conversation_context, dict) else []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        failure = message.get("write_failure")
        if not isinstance(failure, dict) or not failure:
            continue
        detail = str(failure.get("detail") or "").strip()
        if not detail:
            continue
        evidence_id = "ev-runtime-0001"
        item = {
            "id": evidence_id, "file": "<runtime-validation>", "line_start": None, "line_end": None,
            "file_hash": None, "content_hash": hash_texto(detail), "content": detail,
            "source_type": "runtime_validation", "stage": failure.get("stage"),
            "error_code": failure.get("error_code"), "paths": list(failure.get("paths") or []),
            "rollback_confirmed": failure.get("rollback_confirmed"),
        }
        items(ledger)[evidence_id] = item
        return [{
            "tool":"runtime_validation","status":"success","ok":True,"executed":False,"changed":False,
            "error_code":None,"detail":{
                "evidence_id":evidence_id,"source_type":item["source_type"],"stage":item["stage"],
                "error_code":item["error_code"],"paths":item["paths"],"rollback_confirmed":item["rollback_confirmed"],
                "content":detail,
            },"evidence_ids":[evidence_id],
        }]
    return []
