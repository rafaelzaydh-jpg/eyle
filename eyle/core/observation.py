"""Canonical runtime observation ledger.

The ledger owns physical tool observations, replay identity, source coverage,
public tool history and the pending model-facing result batch. Indexes and views
are derived from this one state.
"""
from __future__ import annotations
import copy, hashlib, json
from typing import Any, Dict, List, Optional
from .observation_contract import persisted_handles


def empty_ledger() -> Dict[str, Any]:
    return {"entries": {}, "events": [], "pending_results": [], "handles": {}}


def _entries(session: Any) -> Dict[str, Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    value = ledger.setdefault("entries", {})
    return value if isinstance(value, dict) else {}


def _events(session: Any) -> List[Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    value = ledger.setdefault("events", [])
    return value if isinstance(value, list) else []


def pending_results(session: Any) -> List[Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    value = ledger.setdefault("pending_results", [])
    return value if isinstance(value, list) else []


def set_pending_results(session: Any, results: List[Dict[str, Any]]) -> None:
    session.observation_ledger["pending_results"] = copy.deepcopy(list(results or []))


def clear_pending_results(session: Any) -> None:
    session.observation_ledger["pending_results"] = []


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def observation_signature(tool: str, arguments: Dict[str, Any]) -> Optional[str]:
    if tool == "list_tree":
        return "tree:" + json.dumps({"filter": str(arguments.get("filter") or "").strip().lower(), "depth": arguments.get("depth"), "limit": arguments.get("limit")}, sort_keys=True, separators=(",", ":"), default=str)
    if tool == "search_code": return "search:" + " ".join(str(arguments.get("query") or "").lower().split())
    if tool == "find_symbol": return f"symbol:{_norm_path(arguments.get('path'))}:{str(arguments.get('symbol') or '').strip().lower()}"
    if tool == "symbol_relations":
        query = str(arguments.get("query") or "relations").strip().lower()
        identity = {
            "symbol": str(arguments.get("symbol") or "").strip().lower(),
            "path": _norm_path(arguments.get("path")),
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
    if tool == "read_file":
        if arguments.get("line_start") is not None and arguments.get("line_end") is not None:
            return f"file:{_norm_path(arguments.get('path'))}:{arguments.get('line_start')}:{arguments.get('line_end')}"
        return f"file:{_norm_path(arguments.get('path'))}:default"
    if tool == "project_stats": return "project_stats:root"
    if tool == "inspect_project": return "inspect_project:root"
    if tool == "count_tokens": return "count_tokens:" + json.dumps({"path": _norm_path(arguments.get("path") or "."), "tokenizer": str(arguments.get("tokenizer") or "").strip().lower()}, sort_keys=True, separators=(",", ":"))
    if tool == "agent_info": return "agent_info:runtime"
    if tool == "run_tests": return "run_tests:" + json.dumps({"scope": _norm_path(arguments.get("scope") or ".")}, sort_keys=True, separators=(",", ":"))
    if tool == "git_status": return "git_status:root"
    if tool == "git_diff": return "git_diff:" + json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    if tool == "execution_trace": return "execution_trace:session"
    return None


def ledger_key(signature: str, workspace_epoch: int) -> str:
    return f"w{int(workspace_epoch)}:{signature}"


def lookup(session: Any, signature: Optional[str]) -> Optional[Dict[str, Any]]:
    if not signature: return None
    item = _entries(session).get(ledger_key(signature, getattr(session, "workspace_epoch", 0)))
    return copy.deepcopy(item) if isinstance(item, dict) else None


def lookup_covering(session: Any, tool: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if tool != "read_file" or arguments.get("line_start") is None or arguments.get("line_end") is None:
        return None
    path = _norm_path(arguments.get("path"))
    try:
        requested_start = int(arguments.get("line_start")); requested_end = int(arguments.get("line_end"))
    except (TypeError, ValueError): return None
    epoch = int(getattr(session, "workspace_epoch", 0)); candidates=[]
    for item in _entries(session).values():
        if not isinstance(item, dict) or int(item.get("workspace_epoch", -1)) != epoch: continue
        if item.get("tool") != "read_file": continue
        if _norm_path((item.get("arguments") or {}).get("path")) != path: continue
        coverage = item.get("source_coverage") if isinstance(item.get("source_coverage"), dict) else {}
        try:
            observed_start=int(coverage.get("line_start")); observed_end=int(coverage.get("line_end"))
        except (TypeError, ValueError): continue
        if observed_start <= requested_start and observed_end >= requested_end:
            candidates.append((observed_end-observed_start, -int(item.get("turn") or 0), item))
    return copy.deepcopy(min(candidates, key=lambda value:(value[0],value[1]))[2]) if candidates else None




def lookup_resource_failure(session: Any, tool: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return one resource-scoped stable failure for the same tool/path in this epoch."""
    path = _norm_path(arguments.get("path"))
    if not path:
        return None
    epoch = int(getattr(session, "workspace_epoch", 0))
    candidates = []
    for item in _entries(session).values():
        if not isinstance(item, dict) or int(item.get("workspace_epoch", -1)) != epoch:
            continue
        if item.get("tool") != tool or item.get("failure_scope") != "resource":
            continue
        resource = _norm_path(item.get("failure_resource") or (item.get("arguments") or {}).get("path"))
        if resource != path:
            continue
        candidates.append((int(item.get("turn") or 0), item))
    if not candidates:
        return None
    return copy.deepcopy(max(candidates, key=lambda value: value[0])[1])

def result_fingerprint(result: Dict[str, Any]) -> str:
    payload=json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _append_event(session: Any, *, tool: str, arguments: Dict[str, Any], result: Dict[str, Any],
                  model_result: Dict[str, Any], observation_signature: Optional[str], status: str,
                  replay_reason: Optional[str] = None, public_arguments: Optional[Dict[str, Any]] = None,
                  public_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    events=_events(session)
    event={
        "event_id":f"obs-{len(events)+1:04d}", "turn":int(getattr(session,"turn",0)),
        "workspace_epoch":int(getattr(session,"workspace_epoch",0)), "tool":str(tool),
        "arguments":copy.deepcopy(public_arguments if public_arguments is not None else arguments), "status":str(status),
        "executed":result.get("executed") is True, "ok":result.get("ok") is True,
        "error_code":result.get("error_code"), "retryable":result.get("retryable"),
        "failure_scope":result.get("failure_scope"), "failure_resource":result.get("failure_resource"),
        "observation_signature":observation_signature,
        "source_record_ids":list(model_result.get("source_record_ids") or []),
        "evidence_ids":list(model_result.get("evidence_ids") or []),
        "result":copy.deepcopy(public_result if public_result is not None else result),
    }
    if replay_reason: event["replay_reason"]=str(replay_reason)
    events.append(event)
    return event


def record(session: Any, signature: Optional[str], tool: str, arguments: Dict[str, Any], result: Dict[str, Any],
           model_result: Dict[str, Any], *, public_arguments: Optional[Dict[str, Any]] = None,
           public_result: Optional[Dict[str, Any]] = None) -> None:
    """Record one physical tool outcome and reusable identity when applicable."""
    _append_event(session, tool=tool, arguments=arguments, result=result, model_result=model_result,
                  observation_signature=signature, status=str(result.get("status") or ("success" if result.get("ok") else "failed")),
                  public_arguments=public_arguments, public_result=public_result)
    if not signature or result.get("executed") is not True: return
    reusable = (
        result.get("ok") is True
        or result.get("error_code") in {"SYMBOL_NOT_FOUND", "TEST_RUNNER_UNAVAILABLE"}
        or (tool == "run_tests" and result.get("executed") is True)
        or (result.get("retryable") is False and result.get("failure_scope") in {"request", "resource"})
    )
    if not reusable: return
    key=ledger_key(signature,getattr(session,"workspace_epoch",0))
    detail=model_result.get("detail") if isinstance(model_result.get("detail"),dict) else None
    replay_summary=None
    if tool=="search_code" and isinstance(detail,dict) and not (detail.get("results") or []): replay_summary=copy.deepcopy(model_result)
    elif tool in {"project_stats","count_tokens","inspect_project","symbol_relations","agent_info","run_tests","git_status","git_diff"}: replay_summary=copy.deepcopy(model_result)
    elif tool=="find_symbol" and result.get("error_code")=="SYMBOL_NOT_FOUND": replay_summary=copy.deepcopy(model_result)
    raw_detail=result.get("detail") if isinstance(result.get("detail"),dict) else {}
    source_coverage=None
    if tool=="read_file":
        try:
            source_coverage={"file":_norm_path(raw_detail.get("file") or arguments.get("path")),"line_start":int(raw_detail.get("line_start")),"line_end":int(raw_detail.get("line_end")),"total_lines":int(raw_detail.get("total_lines")) if raw_detail.get("total_lines") is not None else None,"file_hash":raw_detail.get("file_hash")}
        except (TypeError,ValueError): source_coverage=None
    _entries(session)[key]={
        "observation_signature":signature,"workspace_epoch":int(getattr(session,"workspace_epoch",0)),"tool":tool,
        "arguments":copy.deepcopy(arguments),"public_arguments":copy.deepcopy(public_arguments if public_arguments is not None else arguments),
        "result_fingerprint":result_fingerprint(result),
        "source_record_ids":list(model_result.get("source_record_ids") or []),
        "evidence_ids":list(model_result.get("evidence_ids") or []),"coverage_complete":bool(raw_detail.get("coverage_complete")),
        "source_coverage":source_coverage,"failure_scope":result.get("failure_scope"),
        "failure_resource":result.get("failure_resource"),
        "failure_error_code":result.get("error_code") if result.get("failure_scope") else None,
        "failure_detail":str(result.get("detail") or "")[:500] if result.get("failure_scope") else None,
        "replay_result":copy.deepcopy(model_result),"replay_summary":replay_summary,
        "turn":int(getattr(session,"turn",0)),
    }
def record_replay(session: Any, entry: Dict[str, Any], model_result: Dict[str, Any], *, reason: str,
                  public_result: Optional[Dict[str, Any]] = None) -> None:
    result={"status":"replayed","ok":True,"executed":False,"changed":False,"error_code":None}
    _append_event(session, tool=str(entry.get("tool") or ""), arguments=dict(entry.get("arguments") or {}),
                  result=result, model_result=model_result, observation_signature=entry.get("observation_signature"),
                  status="replayed", replay_reason=reason,
                  public_arguments=dict(entry.get("public_arguments") or entry.get("arguments") or {}),
                  public_result=public_result or {"status":"replayed","ok":True,"executed":False,"changed":False})


def event_history(session: Any, *, limit: int=50) -> List[Dict[str, Any]]:
    events=_events(session); selected=events[-max(1,int(limit)):] if limit else events
    out=[]
    for event in selected:
        if not isinstance(event,dict): continue
        out.append({
            "turn":event.get("turn"),"tool":event.get("tool"),"status":event.get("status"),"error_code":event.get("error_code"),"retryable":event.get("retryable"),
            "failure_scope":event.get("failure_scope"),"failure_resource":event.get("failure_resource"),
            "observation_signature":event.get("observation_signature"),"arguments":copy.deepcopy(event.get("arguments") or {}),
            "result":copy.deepcopy(event.get("result") or {}),
            "source_record_ids":list(event.get("source_record_ids") or []),
            "evidence_ids":list(event.get("evidence_ids") or []),"replay_reason":event.get("replay_reason"),
        })
    return out


def navigation_view(session: Any) -> List[Dict[str, Any]]:
    ordered=sorted((item for item in _entries(session).values() if isinstance(item,dict)),key=lambda item:(int(item.get("turn") or 0),str(item.get("observation_signature") or "")))
    out=[]
    for item in ordered:
        entry={
            "turn":item.get("turn"),"tool":item.get("tool"),
            "source_record_ids":list(item.get("source_record_ids") or []),
            "evidence_ids":list(item.get("evidence_ids") or []),
        }
        if item.get("observation_signature"):
            entry["observation_signature"]=item.get("observation_signature")
        coverage=item.get("source_coverage") if isinstance(item.get("source_coverage"),dict) else None
        if coverage:
            entry["source_coverage"]={
                key:coverage.get(key) for key in ("file","line_start","line_end","total_lines")
                if coverage.get(key) is not None
            }
        if item.get("coverage_complete"): entry["coverage_complete"]=True
        out.append(entry)
    return out


def physical_tool_calls(session: Any) -> int:
    return sum(1 for event in _events(session) if isinstance(event,dict) and event.get("executed") is True)


def replay_count(session: Any) -> int:
    return sum(1 for event in _events(session) if isinstance(event,dict) and event.get("status")=="replayed")


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Persist identity/coverage and public events, never hot source bodies.

    Model-facing pending results are turn-ephemeral. Rehydration payloads are
    intentionally not serialized; after resume the agent can observe reality
    again if it needs the source.
    """
    entries=ledger.get("entries") if isinstance(ledger,dict) and isinstance(ledger.get("entries"),dict) else {}
    events=ledger.get("events") if isinstance(ledger,dict) and isinstance(ledger.get("events"),list) else []
    safe_entries={}
    for key,value in entries.items():
        if not isinstance(value,dict):
            continue
        safe_entries[str(key)]={
            field:copy.deepcopy(value.get(field))
            for field in (
                "observation_signature","workspace_epoch","tool","arguments","public_arguments",
                "result_fingerprint","source_record_ids","evidence_ids","coverage_complete","source_coverage",
                "failure_scope","failure_resource","failure_error_code","failure_detail","turn"
            )
            if value.get(field) is not None
        }
    safe_events=[]
    for item in events:
        if not isinstance(item,dict):
            continue
        safe_events.append({
            field:copy.deepcopy(item.get(field))
            for field in (
                "event_id","turn","workspace_epoch","tool","arguments","status","executed",
                "ok","error_code","failure_scope","failure_resource","observation_signature",
                "source_record_ids","evidence_ids","result","replay_reason"
            )
            if item.get(field) is not None
        })
    return {"entries":safe_entries,"events":safe_events,"pending_results":[],"handles":persisted_handles(ledger)}
