"""Canonical runtime decision history for Eyle 2.7.5 Rev1.5.0.

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
    reason: Optional[str] = None, capabilities: Optional[List[str]] = None,
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
    if capabilities:
        item["capabilities"] = [str(name) for name in capabilities[:8]]
    if isinstance(facts, dict) and facts:
        item["facts"] = copy.deepcopy(facts)
    events.append(item)
    return item


def record_rejection(
    ledger: Dict[str, Any], *, turn: int, code: str,
    decision: Optional[str] = None, capabilities: Optional[List[str]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one rejected decision without interpreting repetition."""
    return record(
        ledger,
        turn=turn,
        decision=decision or code,
        outcome="rejected",
        reason=reason or code,
        capabilities=capabilities,
    )


def requested_capability_names(ledger: Dict[str, Any]) -> List[str]:
    """Return capabilities Main actually requested, in first-use order."""
    seen = set()
    result: List[str] = []
    for item in _events(ledger):
        if not isinstance(item, dict) or item.get("outcome") != "requested":
            continue
        if item.get("decision") not in {"capability", "capability_calls"}:
            continue
        for capability in item.get("capabilities") or []:
            name = str(capability or "")
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {"events": [copy.deepcopy(item) for item in _events(ledger)]}

