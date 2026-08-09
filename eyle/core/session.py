"""One active Eyle agent session.

The session stores only what is needed to continue the current task. The LLM
controls strategy and prose; the runtime controls tools, writes and evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentSession:
    request: str
    task_id: Optional[str] = None
    turn: int = 0
    tool_calls: int = 0
    workspace_scope: Dict[str, str] = field(default_factory=dict)
    investigation: List[Dict[str, Any]] = field(default_factory=list)
    latest_tool_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    decision_history: List[Dict[str, Any]] = field(default_factory=list)
    parse_failures: int = 0
    patch_failures: int = 0
    last_tool_signature: Optional[str] = None
    consecutive_identical_calls: int = 0
    prompt_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    phase_history: List[Dict[str, Any]] = field(default_factory=list)
    relevant_sources: List[Dict[str, Any]] = field(default_factory=list)
    # Source ranges visible in the CURRENT compiled prompt only.
    visible_source_ranges: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # Historical coverage is observability only; it must never suppress a reread by itself.
    historically_seen_source_ranges: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # Evidence explicitly kept available during semantic follow-up.
    followup_pinned_evidence_ids: List[str] = field(default_factory=list)
    claim_review: Dict[str, Any] = field(default_factory=dict)
    claim_review_history: List[Dict[str, Any]] = field(default_factory=list)
    claim_repair_attempts: int = 0
    claim_followup_pending: bool = False
    claim_followup_feedback: str = ""
    phase: str = "start"
    investigation_turns: int = 0
    no_progress_turns: int = 0
    phase_violations: int = 0
    conversation_background: List[Dict[str, Any]] = field(default_factory=list)
    write_validation: Dict[str, Any] = field(default_factory=dict)

    def evidence_index(self) -> List[Dict[str, Any]]:
        index: List[Dict[str, Any]] = []
        pinned = []
        seen = set()
        for target in self.investigation:
            if not isinstance(target, dict):
                continue
            for evidence_id in target.get("evidence_ids") or []:
                evidence_id = str(evidence_id or "").strip()
                if evidence_id and evidence_id in self.evidence and evidence_id not in seen:
                    pinned.append(evidence_id)
                    seen.add(evidence_id)
        recent = [evidence_id for evidence_id in list(self.evidence.keys())[-40:] if evidence_id not in seen]
        ordered_ids = pinned + recent
        for evidence_id in ordered_ids:
            item = self.evidence.get(evidence_id)
            if not isinstance(item, dict):
                continue
            entry = {
                "id": evidence_id,
                "file": item.get("arquivo"),
                "lines": [item.get("linha_inicio"), item.get("linha_fim")],
                "file_hash": item.get("file_hash"),
                "content_hash": item.get("content_hash"),
            }
            if evidence_id in seen:
                entry["pinned"] = True
            if item.get("source_type"):
                entry.update({
                    "source_type": item.get("source_type"),
                    "stage": item.get("stage"),
                    "error_code": item.get("error_code"),
                })
            index.append(entry)
        return index

    def record_prompt(self, mode: str, characters: int, estimated_tokens: int, tool_count: int, *, phase: str | None = None, turn: int | None = None, metadata: Dict[str, Any] | None = None) -> None:
        snapshot = {
            "mode": mode,
            "characters": int(characters),
            "estimated_tokens": int(estimated_tokens),
            "tool_count": int(tool_count),
            "phase": phase or self.phase,
            "turn": int(self.turn if turn is None else turn),
        }
        if isinstance(metadata, dict):
            snapshot.update({key: value for key, value in metadata.items() if value is not None})
        self.prompt_snapshots.append(snapshot)
        del self.prompt_snapshots[:-20]

    def record_phase(self, phase: str, *, turn: int | None = None, reason: str = "runtime_state") -> None:
        phase = str(phase or "start")
        previous = str(self.phase or "start")
        if previous == phase and self.phase_history:
            self.phase = phase
            return
        self.phase_history.append({
            "turn": int(self.turn if turn is None else turn),
            "from": previous,
            "to": phase,
            "reason": str(reason or "runtime_state"),
        })
        del self.phase_history[:-50]
        self.phase = phase

    def to_dict(self) -> Dict[str, Any]:
        evidence = {
            key: {
                field: value for field, value in item.items()
                if field not in {"conteudo", "trecho_numerado"}
            }
            for key, item in self.evidence.items() if isinstance(item, dict)
        }
        latest_results = []
        for result in self.latest_tool_results:
            if not isinstance(result, dict):
                continue
            clone = dict(result)
            detail = clone.get("detail")
            if isinstance(detail, dict):
                clone["detail"] = {
                    key: value for key, value in detail.items()
                    if key not in {"conteudo", "trecho_numerado", "resultados"}
                }
            latest_results.append(clone)
        return {
            "request": self.request,
            "task_id": self.task_id,
            "turn": self.turn,
            "tool_calls": self.tool_calls,
            "workspace_scope": self.workspace_scope,
            "investigation": self.investigation,
            "latest_tool_results": latest_results,
            "evidence": evidence,
            "tool_history": self.tool_history[-30:],
            "decision_history": self.decision_history[-30:],
            "parse_failures": self.parse_failures,
            "patch_failures": self.patch_failures,
            "prompt_snapshots": self.prompt_snapshots,
            "phase_history": self.phase_history,
            "relevant_sources": self.relevant_sources,
            "visible_source_ranges": self.visible_source_ranges,
            "historically_seen_source_ranges": self.historically_seen_source_ranges,
            "followup_pinned_evidence_ids": self.followup_pinned_evidence_ids,
            "claim_review": self.claim_review,
            "claim_review_history": self.claim_review_history[-10:],
            "claim_repair_attempts": self.claim_repair_attempts,
            "claim_followup_pending": self.claim_followup_pending,
            "claim_followup_feedback": self.claim_followup_feedback,
            "phase": self.phase,
            "investigation_turns": self.investigation_turns,
            "no_progress_turns": self.no_progress_turns,
            "phase_violations": self.phase_violations,
            "conversation_background": self.conversation_background,
            "write_validation": self.write_validation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        session = cls(
            request=str(data.get("request") or ""),
            task_id=data.get("task_id"),
        )
        session.turn = int(data.get("turn") or 0)
        session.tool_calls = int(data.get("tool_calls") or 0)
        raw_scope = data.get("workspace_scope")
        session.workspace_scope = dict(raw_scope) if isinstance(raw_scope, dict) else {}
        session.investigation = [dict(item) for item in data.get("investigation") or [] if isinstance(item, dict)]
        session.latest_tool_results = list(data.get("latest_tool_results") or [])
        session.evidence = dict(data.get("evidence") or {})
        session.tool_history = list(data.get("tool_history") or [])
        session.decision_history = list(data.get("decision_history") or [])
        session.parse_failures = int(data.get("parse_failures") or 0)
        session.patch_failures = int(data.get("patch_failures") or 0)
        session.prompt_snapshots = list(data.get("prompt_snapshots") or [])
        session.phase_history = list(data.get("phase_history") or [])
        session.relevant_sources = list(data.get("relevant_sources") or [])
        session.visible_source_ranges = dict(data.get("visible_source_ranges") or {})
        session.historically_seen_source_ranges = dict(data.get("historically_seen_source_ranges") or {})
        session.followup_pinned_evidence_ids = [str(item) for item in data.get("followup_pinned_evidence_ids") or [] if str(item)]
        session.claim_review = dict(data.get("claim_review") or {})
        session.claim_review_history = list(data.get("claim_review_history") or [])
        session.claim_repair_attempts = int(data.get("claim_repair_attempts") or 0)
        session.claim_followup_pending = bool(data.get("claim_followup_pending", False))
        session.claim_followup_feedback = str(data.get("claim_followup_feedback") or "")
        session.phase = str(data.get("phase") or "start")
        session.investigation_turns = int(data.get("investigation_turns") or 0)
        session.no_progress_turns = int(data.get("no_progress_turns") or 0)
        session.phase_violations = int(data.get("phase_violations") or 0)
        session.conversation_background = list(data.get("conversation_background") or [])
        session.write_validation = dict(data.get("write_validation") or {})
        return session
