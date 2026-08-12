"""Canonical runtime decision history for Eyle 2.7.5 Rev1.3.

DecisionLedger is observability only. It records what Main requested and what
Runtime accepted/rejected/executed. It does not fingerprint behaviour, count
semantic repetitions, or prescribe what Main should do next.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


def empty_ledger() -> Dict[str, Any]:
    return {"events": []}


def _events(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = ledger.setdefault("events", [])
    return events if isinstance(events, list) else []


def record(
    ledger: Dict[str, Any], *, turn: int, decision: str, outcome: str,
    reason: Optional[str] = None, tools: Optional[List[str]] = None,
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    events = _events(ledger)
    item: Dict[str, Any] = {
        "event_id": f"dec-{len(events)+1:04d}",
        "turn": int(turn),
        "decision": str(decision),
        "outcome": str(outcome),
    }
    if reason:
        item["reason"] = str(reason)[:240]
    if tools:
        item["tools"] = [str(tool) for tool in tools[:8]]
    if isinstance(facts, dict) and facts:
        item["facts"] = copy.deepcopy(facts)
    events.append(item)
    return item


def record_rejection(
    ledger: Dict[str, Any], *, turn: int, code: str,
    decision: Optional[str] = None, tools: Optional[List[str]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one rejected decision without interpreting repetition."""
    return record(
        ledger,
        turn=turn,
        decision=decision or code,
        outcome="rejected",
        reason=reason or code,
        tools=tools,
    )


def requested_tool_names(ledger: Dict[str, Any]) -> List[str]:
    """Return tools Main actually requested, in first-use order."""
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
                seen.add(name)
                result.append(name)
    return result


def history_view(ledger: Dict[str, Any], *, limit: int = 50) -> List[Dict[str, Any]]:
    events = _events(ledger)
    selected = events[-max(1, int(limit)):] if limit else events
    return [copy.deepcopy(item) for item in selected if isinstance(item, dict)]


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {"events": [copy.deepcopy(item) for item in _events(ledger)]}
