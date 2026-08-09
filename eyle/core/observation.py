"""Runtime-owned observation identity and replay ledger."""
from __future__ import annotations
import copy, hashlib, json
from typing import Any, Dict, Optional

def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()

def semantic_signature(tool: str, arguments: Dict[str, Any]) -> Optional[str]:
    if tool == "list_tree":
        return "tree:" + json.dumps({"filter": str(arguments.get("filter") or "").strip().lower(), "depth": arguments.get("depth"), "limit": arguments.get("limit")}, sort_keys=True, separators=(",", ":"), default=str)
    if tool == "search_code": return "search:" + " ".join(str(arguments.get("query") or "").lower().split())
    if tool == "find_symbol": return f"symbol:{_norm_path(arguments.get('path'))}:{str(arguments.get('symbol') or '').strip().lower()}"
    if tool == "read_file": return f"file:{_norm_path(arguments.get('path'))}:all"
    if tool == "read_range": return f"file:{_norm_path(arguments.get('path'))}:{arguments.get('line_start')}:{arguments.get('line_end')}"
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
    item = (getattr(session, "observation_ledger", {}) or {}).get(ledger_key(signature, getattr(session, "workspace_epoch", 0)))
    return copy.deepcopy(item) if isinstance(item, dict) else None

def result_fingerprint(result: Dict[str, Any]) -> str:
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def record(session: Any, signature: Optional[str], tool: str, arguments: Dict[str, Any], result: Dict[str, Any], model_result: Dict[str, Any]) -> None:
    if not signature or result.get("executed") is not True: return
    reusable = result.get("ok") is True or result.get("error_code") in {"SYMBOL_NOT_FOUND", "TEST_RUNNER_UNAVAILABLE"} or (tool == "run_tests" and result.get("executed") is True)
    if not reusable: return
    key = ledger_key(signature, getattr(session, "workspace_epoch", 0))
    detail = model_result.get("detail") if isinstance(model_result.get("detail"), dict) else None
    replay_summary = None
    if tool == "search_code" and isinstance(detail, dict) and not (detail.get("resultados") or []):
        replay_summary = copy.deepcopy(model_result)
    elif tool in {"project_stats", "count_tokens", "inspect_project", "agent_info", "run_tests", "git_status", "git_diff"}:
        replay_summary = copy.deepcopy(model_result)
    elif tool == "find_symbol" and result.get("error_code") == "SYMBOL_NOT_FOUND":
        replay_summary = copy.deepcopy(model_result)
    session.observation_ledger[key] = {
        "semantic_signature": signature,
        "workspace_epoch": int(getattr(session, "workspace_epoch", 0)),
        "tool": tool,
        "arguments": copy.deepcopy(arguments),
        "result_fingerprint": result_fingerprint(result),
        "evidence_ids": list(model_result.get("evidence_ids") or []),
        "coverage_complete": bool(((result.get("detail") or {}) if isinstance(result.get("detail"), dict) else {}).get("coverage_complete")),
        "replay_result": copy.deepcopy(model_result),
        "replay_summary": replay_summary,
        "turn": int(getattr(session, "turn", 0)),
    }
    # Keep identity indefinitely within the session. To bound hot-memory source
    # bodies, strip only replay payloads from older entries; never forget keys.
    if len(session.observation_ledger) > 256:
        ordered = sorted(session.observation_ledger.items(), key=lambda kv: int((kv[1] or {}).get("turn") or 0))
        for _, old in ordered[:len(session.observation_ledger)-256]:
            if isinstance(old, dict):
                old.pop("replay_result", None)

def persisted_view(ledger: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for key, value in (ledger or {}).items():
        if isinstance(value, dict): out[str(key)] = {k: copy.deepcopy(v) for k, v in value.items() if k != "replay_result"}
    return out
