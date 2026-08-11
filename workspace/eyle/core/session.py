"""One active Rev5.7.5 Eyle agent session.

Rev5.7.5 is a clean break. The session stores only canonical semantic/physical
state that must survive turns or confirmation. Histories and metrics are views
of their owning ledgers, not parallel persisted fields.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .decision import empty_ledger as empty_decision_ledger, persisted_view as persisted_decisions
from .evidence import empty_ledger as empty_evidence_ledger, index_view as evidence_index_view, persisted_view as persisted_evidence
from .observation import empty_ledger as empty_observation_ledger, persisted_view as persisted_observations
from .write_transaction import empty_transaction

SESSION_SCHEMA_VERSION = "5.7.5"


@dataclass
class AgentSession:
    request: str
    task_id: Optional[str] = None
    turn: int = 0
    workspace_epoch: int = 0
    observation_ledger: Dict[str, Any] = field(default_factory=empty_observation_ledger)
    decision_ledger: Dict[str, Any] = field(default_factory=empty_decision_ledger)
    evidence_ledger: Dict[str, Any] = field(default_factory=empty_evidence_ledger)
    investigation: List[Dict[str, Any]] = field(default_factory=list)
    claim_review: Dict[str, Any] = field(default_factory=dict)
    conversation_background: List[Dict[str, Any]] = field(default_factory=list)
    write_transaction: Dict[str, Any] = field(default_factory=empty_transaction)

    def evidence_index(self) -> List[Dict[str, Any]]:
        return evidence_index_view(self.evidence_ledger, self.investigation)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "request": self.request,
            "task_id": self.task_id,
            "turn": int(self.turn),
            "workspace_epoch": int(self.workspace_epoch),
            "observation_ledger": persisted_observations(self.observation_ledger),
            "decision_ledger": persisted_decisions(self.decision_ledger),
            "evidence_ledger": persisted_evidence(self.evidence_ledger),
            "investigation": [dict(item) for item in self.investigation if isinstance(item, dict)],
            "claim_review": dict(self.claim_review or {}),
            "conversation_background": [dict(item) for item in self.conversation_background if isinstance(item, dict)],
            "write_transaction": dict(self.write_transaction or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        expected_top_level = {
            "session_schema_version", "request", "task_id", "turn", "workspace_epoch",
            "observation_ledger", "decision_ledger", "evidence_ledger", "investigation",
            "claim_review", "conversation_background", "write_transaction",
        }
        if not isinstance(data, dict) or data.get("session_schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if set(data) != expected_top_level:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["request"], str):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if data["task_id"] is not None and not isinstance(data["task_id"], str):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["turn"], int) or isinstance(data["turn"], bool) or data["turn"] < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["workspace_epoch"], int) or isinstance(data["workspace_epoch"], bool) or data["workspace_epoch"] < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        obs = data["observation_ledger"]
        if not isinstance(obs, dict) or set(obs) != {"entries", "events", "pending_results", "handles"}:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(obs["entries"], dict) or not all(isinstance(v, dict) for v in obs["entries"].values()):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(obs["events"], list) or not all(isinstance(item, dict) for item in obs["events"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(obs["pending_results"], list) or not all(isinstance(item, dict) for item in obs["pending_results"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(obs["handles"], dict) or not all(isinstance(v, dict) for v in obs["handles"].values()):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        decisions = data["decision_ledger"]
        if not isinstance(decisions, dict) or set(decisions) != {"events"}:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(decisions["events"], list) or not all(isinstance(item, dict) for item in decisions["events"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        evidence = data["evidence_ledger"]
        if not isinstance(evidence, dict) or set(evidence) != {"items"}:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(evidence["items"], dict) or not all(isinstance(v, dict) for v in evidence["items"].values()):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        if not isinstance(data["investigation"], list) or not all(isinstance(item, dict) for item in data["investigation"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["claim_review"], dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["conversation_background"], list) or not all(isinstance(item, dict) for item in data["conversation_background"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["write_transaction"], dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        session = cls(request=data["request"], task_id=data["task_id"])
        session.turn = data["turn"]
        session.workspace_epoch = data["workspace_epoch"]
        session.observation_ledger = {
            "entries": {str(k): dict(v) for k, v in obs["entries"].items()},
            "events": [dict(item) for item in obs["events"]],
            "pending_results": [dict(item) for item in obs["pending_results"]],
            "handles": {str(k): dict(v) for k, v in obs["handles"].items()},
        }
        session.decision_ledger = {"events": [dict(item) for item in decisions["events"]]}
        session.evidence_ledger = {"items": {str(k): dict(v) for k, v in evidence["items"].items()}}
        session.investigation = [dict(item) for item in data["investigation"]]
        session.claim_review = dict(data["claim_review"])
        session.conversation_background = [dict(item) for item in data["conversation_background"]]
        session.write_transaction = dict(data["write_transaction"])
        return session
