"""Minimal persisted ECC AgentSession with internal graph-memory focus."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from eyle.runtime.observation import empty_ledger as empty_observation_ledger, persisted_view as persisted_observations
from .evidence import empty_evidence, validate_evidence
from .memory import empty_memory_focus

SESSION_SCHEMA_VERSION = "2.7.5-r2.5.2-ecc"


def _validated_objective_state(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"summary", "status", "children", "constraints"}:
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    summary, status = value.get("summary"), value.get("status")
    if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 2000:
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    if not isinstance(status, str) or not status.strip() or len(status.strip()) > 96:
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    children = value.get("children")
    constraints = value.get("constraints")
    if not isinstance(children, list) or len(children) > 16 or not isinstance(constraints, list) or len(constraints) > 16:
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    clean_children = []
    seen = set()
    for item in children:
        if not isinstance(item, dict) or set(item) - {"key", "description", "status", "outcome"} or not {"key", "description", "status"}.issubset(item):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        key, description, child_status = item.get("key"), item.get("description"), item.get("status")
        if not isinstance(key, str) or not key or len(key) > 64 or key in seen:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not all(ch.isalnum() or ch in "_-" for ch in key):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 2000:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(child_status, str) or not child_status.strip() or len(child_status.strip()) > 96:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        out = {"key": key, "description": description.strip(), "status": child_status.strip()}
        if "outcome" in item:
            outcome = item.get("outcome")
            if not isinstance(outcome, str) or not outcome.strip() or len(outcome.strip()) > 4000:
                raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
            out["outcome"] = outcome.strip()
        seen.add(key); clean_children.append(out)
    clean_constraints = []
    for item in constraints:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 1000:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        clean_constraints.append(item.strip())
    return {"summary": summary.strip(), "status": status.strip(), "children": clean_children, "constraints": clean_constraints}


@dataclass
class AgentSession:
    request: str
    execution_id: Optional[str] = None
    turn: int = 0
    reality_epoch: int = 0
    observation_ledger: Dict[str, Any] = field(default_factory=empty_observation_ledger)
    evidence: Dict[str, Dict[str, Any]] = field(default_factory=empty_evidence)
    memory_focus: List[str] = field(default_factory=empty_memory_focus)
    objective_state: Optional[Dict[str, Any]] = None
    conversation_background: List[Dict[str, Any]] = field(default_factory=list)
    request_context: List[Dict[str, Any]] = field(default_factory=list)
    runtime_feedback: List[Dict[str, Any]] = field(default_factory=list)
    pending_operation: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "request": self.request,
            "execution_id": self.execution_id,
            "turn": int(self.turn),
            "reality_epoch": int(self.reality_epoch),
            "observation_ledger": persisted_observations(self.observation_ledger),
            "evidence": validate_evidence(self.evidence),
            "memory_focus": [str(v) for v in self.memory_focus[:12] if str(v).strip()],
            "objective_state": _validated_objective_state(self.objective_state),
            "conversation_background": [dict(v) for v in self.conversation_background if isinstance(v, dict)],
            "request_context": [dict(v) for v in self.request_context if isinstance(v, dict)],
            "runtime_feedback": [dict(v) for v in self.runtime_feedback[-20:] if isinstance(v, dict)],
            "pending_operation": dict(self.pending_operation or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        expected = {
            "session_schema_version", "request", "execution_id", "turn", "reality_epoch",
            "observation_ledger", "evidence", "memory_focus", "objective_state", "conversation_background", "request_context",
            "runtime_feedback", "pending_operation",
        }
        if not isinstance(data, dict) or data.get("session_schema_version") != SESSION_SCHEMA_VERSION or set(data) != expected:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data.get("request"), str):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session = cls(data["request"], execution_id=data.get("execution_id"))
        try:
            session.turn = int(data.get("turn", 0)); session.reality_epoch = int(data.get("reality_epoch", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE") from exc
        if session.turn < 0 or session.reality_epoch < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        obs = data.get("observation_ledger")
        required_obs = {"entries", "events", "replay_count", "pending_results", "handles", "snapshots", "frontiers", "materials"}
        if not isinstance(obs, dict) or set(obs) != required_obs:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.observation_ledger = {
            "entries": {str(k): dict(v) for k, v in (obs.get("entries") or {}).items()},
            "events": [dict(v) for v in (obs.get("events") or [])],
            "replay_count": int(obs.get("replay_count") or 0),
            "pending_results": [],
            "handles": {str(k): dict(v) for k, v in (obs.get("handles") or {}).items()},
            "snapshots": {str(k): dict(v) for k, v in (obs.get("snapshots") or {}).items()},
            "frontiers": {str(k): dict(v) for k, v in (obs.get("frontiers") or {}).items()},
            "materials": {str(k): dict(v) for k, v in (obs.get("materials") or {}).items()},
        }
        session.evidence = validate_evidence(data.get("evidence"))
        focus = data.get("memory_focus")
        if not isinstance(focus, list) or any(not isinstance(v, str) for v in focus):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.memory_focus = [str(v) for v in focus[:12] if str(v).strip()]
        session.objective_state = _validated_objective_state(data.get("objective_state"))
        for field_name in ("conversation_background", "request_context", "runtime_feedback"):
            value = data.get(field_name)
            if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
                raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
            setattr(session, field_name, [dict(v) for v in value])
        if not isinstance(data.get("pending_operation"), dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.pending_operation = dict(data.get("pending_operation") or {})
        return session
