"""One active Eyle 2.7.5 Rev1.5.3 agent session.

Observation owns physical history and Material. Investigation records unresolved
epistemic commitments; Tasks record intentional completion commitments. Runtime
persists and validates their physical contracts without semantic inference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .decision import empty_ledger as empty_decision_ledger, persisted_view as persisted_decisions
from eyle.runtime.observation import empty_ledger as empty_observation_ledger, material_index_view, persisted_view as persisted_observations
from .investigation import validate_investigation_state
from .tasks import validate_task_state
from .task_memory import empty_task_memory, persisted_view as persisted_task_memory, validate_task_memory_state

SESSION_SCHEMA_VERSION = "2.7.5-r1.5.3"


@dataclass
class AgentSession:
    request: str
    execution_id: Optional[str] = None
    turn: int = 0
    reality_epoch: int = 0
    observation_ledger: Dict[str, Any] = field(default_factory=empty_observation_ledger)
    decision_ledger: Dict[str, Any] = field(default_factory=empty_decision_ledger)
    investigation: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    conversation_background: List[Dict[str, Any]] = field(default_factory=list)
    request_context: List[Dict[str, Any]] = field(default_factory=list)
    task_memory: Dict[str, Any] = field(default_factory=empty_task_memory)
    pending_capability: Dict[str, Any] = field(default_factory=dict)

    def grounding_index(self) -> List[Dict[str, Any]]:
        return material_index_view(self.observation_ledger)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "request": self.request,
            "execution_id": self.execution_id,
            "turn": int(self.turn),
            "reality_epoch": int(self.reality_epoch),
            "observation_ledger": persisted_observations(self.observation_ledger),
            "decision_ledger": persisted_decisions(self.decision_ledger),
            "investigation": [dict(item) for item in self.investigation if isinstance(item, dict)],
            "tasks": [dict(item) for item in self.tasks if isinstance(item, dict)],
            "conversation_background": [dict(item) for item in self.conversation_background if isinstance(item, dict)],
            "request_context": [dict(item) for item in self.request_context if isinstance(item, dict)],
            "task_memory": persisted_task_memory(self.task_memory),
            "pending_capability": dict(self.pending_capability or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        expected_top_level = {
            "session_schema_version", "request", "execution_id", "turn", "reality_epoch",
            "observation_ledger", "decision_ledger", "investigation", "tasks",
            "conversation_background", "request_context", "task_memory", "pending_capability",
        }
        if not isinstance(data, dict) or data.get("session_schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if set(data) != expected_top_level:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["request"], str):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if data["execution_id"] is not None and not isinstance(data["execution_id"], str):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["turn"], int) or isinstance(data["turn"], bool) or data["turn"] < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["reality_epoch"], int) or isinstance(data["reality_epoch"], bool) or data["reality_epoch"] < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        obs = data["observation_ledger"]
        expected_observation = {"entries", "events", "replay_count", "pending_results", "handles", "snapshots", "frontiers", "materials"}
        if not isinstance(obs, dict) or set(obs) != expected_observation:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        for key in ("entries", "handles", "snapshots", "frontiers", "materials"):
            if not isinstance(obs[key], dict) or not all(isinstance(v, dict) for v in obs[key].values()):
                raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        for key in ("events", "pending_results"):
            if not isinstance(obs[key], list) or not all(isinstance(item, dict) for item in obs[key]):
                raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(obs["replay_count"], int) or isinstance(obs["replay_count"], bool) or obs["replay_count"] < 0:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        decisions = data["decision_ledger"]
        if not isinstance(decisions, dict) or set(decisions) != {"events"}:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(decisions["events"], list) or not all(isinstance(item, dict) for item in decisions["events"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["investigation"], list) or not all(isinstance(item, dict) for item in data["investigation"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["tasks"], list) or not all(isinstance(item, dict) for item in data["tasks"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["conversation_background"], list) or not all(isinstance(item, dict) for item in data["conversation_background"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        if not isinstance(data["request_context"], list) or not all(isinstance(item, dict) for item in data["request_context"]):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")
        try:
            task_memory = validate_task_memory_state(data["task_memory"])
        except ValueError as error:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE") from error
        if not isinstance(data["pending_capability"], dict):
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE")

        session = cls(request=data["request"], execution_id=data["execution_id"])
        session.turn = data["turn"]
        session.reality_epoch = data["reality_epoch"]
        session.observation_ledger = {
            "entries": {str(k): dict(v) for k, v in obs["entries"].items()},
            "events": [dict(item) for item in obs["events"]],
            "replay_count": int(obs["replay_count"]),
            "pending_results": [dict(item) for item in obs["pending_results"]],
            "handles": {str(k): dict(v) for k, v in obs["handles"].items()},
            "snapshots": {str(k): dict(v) for k, v in obs["snapshots"].items()},
            "frontiers": {str(k): dict(v) for k, v in obs["frontiers"].items()},
            "materials": {str(k): dict(v) for k, v in obs["materials"].items()},
        }
        session.decision_ledger = {"events": [dict(item) for item in decisions["events"]]}
        try:
            session.investigation = validate_investigation_state(data["investigation"])
        except ValueError as error:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE") from error
        try:
            session.tasks = validate_task_state(data["tasks"])
        except ValueError as error:
            raise ValueError("SESSION_SCHEMA_INCOMPATIBLE") from error
        session.conversation_background = [dict(item) for item in data["conversation_background"]]
        session.request_context = [dict(item) for item in data["request_context"]]
        session.task_memory = task_memory
        session.pending_capability = dict(data["pending_capability"])
        return session
