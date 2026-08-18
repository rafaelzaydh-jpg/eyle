"""Minimal persisted ECC AgentSession for Eyle."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from eyle.runtime.observation import empty_ledger as empty_observation_ledger, persisted_view as persisted_observations
from .evidence import empty_evidence, validate_evidence
from .memory import empty_memory_view

SESSION_SCHEMA_VERSION = "2.7.5-r3.7.2-ecc"


def _validated_memory_view(value: Any) -> Dict[str, Any]:
    if value is None:
        return empty_memory_view()
    if not isinstance(value, dict) or set(value) != {"node_ids", "coverage", "frontiers", "selector", "overview"}:
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    node_ids = value.get("node_ids")
    frontiers = value.get("frontiers")
    if not isinstance(node_ids, list) or any(not isinstance(v, str) for v in node_ids):
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    if not isinstance(frontiers, list) or any(not isinstance(v, str) for v in frontiers):
        raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    for key in ("coverage", "selector", "overview"):
        if not isinstance(value.get(key), dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
    return {
        "node_ids": [str(v) for v in node_ids if str(v).strip()],
        "coverage": dict(value.get("coverage") or {}),
        "frontiers": [str(v) for v in frontiers if str(v).strip()],
        "selector": dict(value.get("selector") or {}),
        "overview": dict(value.get("overview") or {}),
    }


@dataclass
class AgentSession:
    request: str
    execution_id: Optional[str] = None
    turn: int = 0
    reality_epoch: int = 0
    observation_ledger: Dict[str, Any] = field(default_factory=empty_observation_ledger)
    evidence: Dict[str, Dict[str, Any]] = field(default_factory=empty_evidence)
    memory_view: Dict[str, Any] = field(default_factory=empty_memory_view)
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
            "memory_view": _validated_memory_view(self.memory_view),
            "runtime_feedback": [dict(v) for v in self.runtime_feedback if isinstance(v, dict)],
            "pending_operation": dict(self.pending_operation or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        expected = {
            "session_schema_version", "request", "execution_id", "turn", "reality_epoch",
            "observation_ledger", "evidence", "memory_view", "runtime_feedback", "pending_operation",
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
        session.memory_view = _validated_memory_view(data.get("memory_view"))
        value = data.get("runtime_feedback")
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.runtime_feedback = [dict(v) for v in value]
        if not isinstance(data.get("pending_operation"), dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.pending_operation = dict(data.get("pending_operation") or {})
        return session
