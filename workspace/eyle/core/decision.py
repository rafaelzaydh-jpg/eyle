"""Canonical runtime decision ledger.

One decision event log owns public history and deterministic rejection identity.
No parallel decision history or repeated-rejection counter is persisted.
"""
from __future__ import annotations
import copy, json
from typing import Any, Dict, List, Optional
from .text_hash import hash_texto


def empty_ledger() -> Dict[str, Any]:
    return {"events": []}


def _events(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = ledger.setdefault("events", [])
    return events if isinstance(events, list) else []


def record(ledger: Dict[str, Any], *, turn: int, decision: str, outcome: str,
           reason: Optional[str] = None, tools: Optional[List[str]] = None,
           required_properties: Optional[List[str]] = None,
           rejection_fingerprint: Optional[str] = None,
           rejection_code: Optional[str] = None) -> Dict[str, Any]:
    events = _events(ledger)
    item: Dict[str, Any] = {
        "event_id": f"dec-{len(events)+1:04d}",
        "turn": int(turn), "decision": str(decision), "outcome": str(outcome),
    }
    if reason:
        item["reason"] = str(reason)[:240]
    if tools:
        item["tools"] = [str(tool) for tool in tools[:8]]
    if required_properties:
        item["required_properties"] = [str(value)[:300] for value in required_properties[:4] if str(value).strip()]
    if rejection_fingerprint:
        item["rejection_fingerprint"] = str(rejection_fingerprint)
        item["rejection_code"] = str(rejection_code or reason or decision)
    events.append(item)
    return item


def rejection_fingerprint(*, code: str, payload: Any, objective_state: Dict[str, Any],
                          objective_context: Optional[Dict[str, Any]] = None) -> str:
    canonical = {
        "objective_state": objective_state,
        "code": str(code or ""),
        "payload": payload,
        "objective_context": dict(objective_context or {}),
    }
    return hash_texto(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def record_rejection(ledger: Dict[str, Any], *, turn: int, code: str, payload: Any,
                     objective_state: Dict[str, Any], objective_context: Optional[Dict[str, Any]] = None,
                     decision: Optional[str] = None, tools: Optional[List[str]] = None,
                     reason: Optional[str] = None, repeated_outcome: Optional[str] = None) -> int:
    fingerprint = rejection_fingerprint(
        code=code, payload=payload, objective_state=objective_state,
        objective_context=objective_context,
    )
    prior = sum(
        1 for item in _events(ledger)
        if isinstance(item, dict) and item.get("rejection_fingerprint") == fingerprint
    )
    occurrence = prior + 1
    outcome = str(repeated_outcome) if repeated_outcome and occurrence > 1 else "rejected"
    record(
        ledger, turn=turn, decision=decision or code, outcome=outcome,
        reason=reason or code, tools=tools, rejection_fingerprint=fingerprint, rejection_code=code,
    )
    return occurrence


def repeated_rejection_count(ledger: Dict[str, Any]) -> int:
    counts: Dict[str, int] = {}
    for item in _events(ledger):
        fp = item.get("rejection_fingerprint") if isinstance(item, dict) else None
        if fp:
            counts[str(fp)] = counts.get(str(fp), 0) + 1
    return sum(max(0, count - 1) for count in counts.values())



def requested_tool_names(ledger: Dict[str, Any]) -> List[str]:
    """Return tools the Main LLM has actually requested, in first-use order.

    This is a derived attention view, not persisted activation state. A malformed
    first attempt still counts as a real request so the next prompt can expose
    that tool's expanded contract for correction.
    """
    seen = set()
    result: List[str] = []
    for item in _events(ledger):
        if not isinstance(item, dict) or item.get("outcome") != "requested":
            continue
        if item.get("decision") not in {"tool", "tool_calls"}:
            continue
        for tool in item.get("tools") or []:
            name = str(tool or "")
            if name and name not in seen:
                seen.add(name); result.append(name)
    return result

def history_view(ledger: Dict[str, Any], *, limit: int = 50) -> List[Dict[str, Any]]:
    events = _events(ledger)
    selected = events[-max(1, int(limit)):] if limit else events
    return [
        {k: copy.deepcopy(v) for k, v in item.items() if k not in {"rejection_fingerprint"}}
        for item in selected if isinstance(item, dict)
    ]


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {"events": [copy.deepcopy(item) for item in _events(ledger)]}
