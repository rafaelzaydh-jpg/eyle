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
    plan: List[str] = field(default_factory=list)
    latest_tool_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    parse_failures: int = 0
    patch_failures: int = 0
    last_tool_signature: Optional[str] = None
    consecutive_identical_calls: int = 0
    prompt_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def evidence_index(self) -> List[Dict[str, Any]]:
        index: List[Dict[str, Any]] = []
        for evidence_id, item in list(self.evidence.items())[-40:]:
            if not isinstance(item, dict):
                continue
            index.append({
                "id": evidence_id,
                "file": item.get("arquivo"),
                "lines": [item.get("linha_inicio"), item.get("linha_fim")],
                "file_hash": item.get("file_hash"),
                "content_hash": item.get("content_hash"),
            })
        return index

    def record_prompt(self, mode: str, characters: int, estimated_tokens: int, tool_count: int) -> None:
        self.prompt_snapshots.append({
            "mode": mode,
            "characters": int(characters),
            "estimated_tokens": int(estimated_tokens),
            "tool_count": int(tool_count),
        })
        del self.prompt_snapshots[:-20]

    def to_dict(self) -> Dict[str, Any]:
        evidence = {
            key: {
                field: value for field, value in item.items()
                if field not in {"conteudo", "conteudo_raw", "trecho_numerado"}
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
                    if key not in {"conteudo", "conteudo_raw", "trecho_numerado", "resultados"}
                }
            latest_results.append(clone)
        return {
            "request": self.request,
            "task_id": self.task_id,
            "turn": self.turn,
            "tool_calls": self.tool_calls,
            "plan": self.plan,
            "latest_tool_results": latest_results,
            "evidence": evidence,
            "tool_history": self.tool_history[-30:],
            "parse_failures": self.parse_failures,
            "patch_failures": self.patch_failures,
            "prompt_snapshots": self.prompt_snapshots,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentSession":
        session = cls(
            request=str(data.get("request") or ""),
            task_id=data.get("task_id"),
        )
        session.turn = int(data.get("turn") or 0)
        session.tool_calls = int(data.get("tool_calls") or 0)
        session.plan = [str(item) for item in data.get("plan") or []]
        session.latest_tool_results = list(data.get("latest_tool_results") or [])
        session.evidence = dict(data.get("evidence") or {})
        session.tool_history = list(data.get("tool_history") or [])
        session.parse_failures = int(data.get("parse_failures") or 0)
        session.patch_failures = int(data.get("patch_failures") or 0)
        session.prompt_snapshots = list(data.get("prompt_snapshots") or [])
        return session
