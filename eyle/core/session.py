"""One active Rev5.6 Eyle agent session.

Rev5.6 is a clean break. The session stores only canonical semantic/physical
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

SESSION_SCHEMA_VERSION = "5.6"


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
        if not isinstance(data, dict) or data.get("session_schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session = cls(request=str(data.get("request") or ""), task_id=data.get("task_id"))
        session.turn = max(0, int(data.get("turn") or 0))
        session.workspace_epoch = max(0, int(data.get("workspace_epoch") or 0))
        obs = data.get("observation_ledger")
        if not isinstance(obs, dict) or not isinstance(obs.get("entries"), dict) or not isinstance(obs.get("events"), list):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.observation_ledger = {
            "entries": {str(k): dict(v) for k, v in obs.get("entries", {}).items() if isinstance(v, dict)},
            "events": [dict(item) for item in obs.get("events", []) if isinstance(item, dict)],
            "pending_results": [dict(item) for item in obs.get("pending_results", []) if isinstance(item, dict)],
        }
        decisions = data.get("decision_ledger")
        if not isinstance(decisions, dict) or not isinstance(decisions.get("events"), list):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.decision_ledger = {"events": [dict(item) for item in decisions.get("events", []) if isinstance(item, dict)]}
        evidence = data.get("evidence_ledger")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("items"), dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        session.evidence_ledger = {"items": {str(k): dict(v) for k, v in evidence.get("items", {}).items() if isinstance(v, dict)}}
        session.investigation = [dict(item) for item in data.get("investigation") or [] if isinstance(item, dict)]
        session.claim_review = dict(data.get("claim_review") or {})
        session.conversation_background = [dict(item) for item in data.get("conversation_background") or [] if isinstance(item, dict)]
        session.write_transaction = dict(data.get("write_transaction") or {})
        return session
