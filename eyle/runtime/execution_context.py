"""Run-scoped physical execution state.

Configuration is immutable input. This context owns deadlines and the canonical
LLM call ledger for one ECC execution/resume. Token usage is accounting only;
physical containment is the provider/model context window, wall-clock deadline
and capability-specific limits.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import copy, hashlib, time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionContext:
    started_monotonic: float
    deadline_monotonic: float
    execution_id: Optional[str]
    source_job_id: Optional[int]
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt_hashes: List[str] = field(default_factory=list)
    history_messages_omitted: int = 0
    agent_turns: int = 0
    session_turn_start: int = 0
    observation_event_start: int = 0
    observation_replay_start: int = 0
    grounding_ids_start: List[str] = field(default_factory=list)
    canonical_request_hash: Optional[str] = None
    prompt_tokens_budgeted_physical: int = 0
    prompt_tokens_estimated_raw: int = 0
    prompt_tokens_actual: int = 0
    prompt_tokens_cached: int = 0
    prompt_tokens_uncached: int = 0
    prompt_tokens_effective: int = 0
    completion_tokens_actual: int = 0
    reasoning_tokens_actual: int = 0
    provider_state: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False, compare=False)
    provider_cleanup_callbacks: List[Any] = field(default_factory=list, repr=False, compare=False)
    terminal_capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Dict[str, Any], *, execution_id: Optional[str] = None,
                    source_job_id: Optional[int] = None) -> "ExecutionContext":
        agent = (config or {}).get("agent") or {}
        deadline = max(1, int(agent.get("task_deadline_seconds", 900) or 900))
        now = time.monotonic()
        return cls(
            started_monotonic=now, deadline_monotonic=now + deadline,
            execution_id=execution_id, source_job_id=source_job_id,
        )

    def bind_session_baseline(self, session: Any) -> None:
        """Capture task-cumulative state at the start of this physical job."""
        self.session_turn_start = int(getattr(session, "turn", 0) or 0)
        observations = getattr(session, "observation_ledger", {}) or {}
        self.observation_event_start = len(observations.get("events") or []) if isinstance(observations, dict) else 0
        self.observation_replay_start = max(0, int(observations.get("replay_count") or 0)) if isinstance(observations, dict) else 0
        materials = observations.get("materials") if isinstance(observations, dict) else {}
        self.grounding_ids_start = sorted(str(key) for key in (materials or {}).keys()) if isinstance(materials, dict) else []
        self.agent_turns = 0

    @staticmethod
    def _request_hash(request: Any) -> str:
        return hashlib.sha256(str(request or "").encode("utf-8")).hexdigest()

    def bind_canonical_request(self, request: Any) -> None:
        self.canonical_request_hash = self._request_hash(request)

    def assert_canonical_request(self, request: Any) -> None:
        current = self._request_hash(request)
        if self.canonical_request_hash is None:
            self.canonical_request_hash = current
            return
        if current != self.canonical_request_hash:
            raise RuntimeError("CANONICAL_REQUEST_IDENTITY_MISMATCH")


    def mark_terminal_capability(self, tool: str, *, error_code: str, detail: Any = None) -> None:
        name = str(tool or "").strip()
        if not name:
            return
        self.terminal_capabilities[name] = {
            "error_code": str(error_code or "CAPABILITY_UNAVAILABLE"),
            "retryable": False,
            "detail": str(detail or "")[:500],
        }

    def terminal_capability(self, tool: str) -> Optional[Dict[str, Any]]:
        value = self.terminal_capabilities.get(str(tool or ""))
        return copy.deepcopy(value) if isinstance(value, dict) else None

    def terminal_capabilities_view(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self.terminal_capabilities)

    def begin_call(self, *, mode: str, turn: int, prompt: Dict[str, Any]) -> Dict[str, Any]:
        call = {
            "logical_call_id": len(self.llm_calls) + 1,
            "mode": str(mode), "turn": int(turn),
            "prompt": copy.deepcopy(prompt), "attempts": [],
        }
        self.llm_calls.append(call)
        return call

    def latest_call(self) -> Optional[Dict[str, Any]]:
        return self.llm_calls[-1] if self.llm_calls else None

    def add_attempt(self, call: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        attempts = call.setdefault("attempts", [])
        item = {k: copy.deepcopy(v) for k, v in dict(metadata or {}).items() if v is not None}
        item["physical_attempt"] = len(attempts) + 1
        attempts.append(item)
        return item

    @property
    def generated_tokens(self) -> int:
        return int(self.completion_tokens_actual or 0)

    @property
    def llm_request_count(self) -> int:
        return sum(len(call.get("attempts") or []) for call in self.llm_calls if isinstance(call, dict))

    @property
    def prompt_token_calibration(self) -> float:
        """Calibrate future reservations against provider-reported prompt usage.

        Calibration exists only to protect the per-call physical context window when
        provider usage differs from the local tokenizer estimate. It no longer
        steers a separate cumulative prompt budget.
        """
        estimated = int(self.prompt_tokens_estimated_raw or 0)
        actual = int(self.prompt_tokens_actual or 0)
        if estimated <= 0 or actual <= 0:
            return 1.0
        ratio = float(actual) / float(estimated)
        return min(4.0, max(0.75, ratio))

    @property
    def physical_tokens_used(self) -> int:
        # Full prompt attempts count even when cached. Once provider usage is
        # known, never let a lower local estimate understate physical spending.
        prompt_used = max(int(self.prompt_tokens_budgeted_physical or 0), int(self.prompt_tokens_actual or 0))
        return prompt_used + int(self.completion_tokens_actual or 0)

    def usage_view(self) -> Dict[str, Any]:
        effective_total = int(self.prompt_tokens_effective or 0) + int(self.completion_tokens_actual or 0)
        return {
            "llm_calls": len(self.llm_calls),
            "llm_requests": self.llm_request_count,
            "prompt_tokens_budgeted_physical": int(self.prompt_tokens_budgeted_physical or 0),
            "prompt_tokens_estimated_raw": int(self.prompt_tokens_estimated_raw or 0),
            "prompt_tokens_actual": int(self.prompt_tokens_actual or 0),
            "prompt_tokens_cached": int(self.prompt_tokens_cached or 0),
            "prompt_tokens_uncached": int(self.prompt_tokens_uncached or 0),
            "prompt_tokens_effective": int(self.prompt_tokens_effective or 0),
            "completion_tokens_actual": int(self.completion_tokens_actual or 0),
            "generated_tokens": int(self.completion_tokens_actual or 0),
            "reasoning_tokens_actual": int(self.reasoning_tokens_actual or 0),
            "total_tokens_effective": effective_total,
            "total_tokens_physical_estimated": self.physical_tokens_used,
            "prompt_token_calibration": round(self.prompt_token_calibration, 4),
            "history_messages_omitted": int(self.history_messages_omitted or 0),
        }

    def ledger_view(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.llm_calls)

    def provider_state_for(self, provider_key: str) -> Dict[str, Any]:
        key = str(provider_key or "").strip()
        if not key:
            raise ValueError("PROVIDER_STATE_KEY_REQUIRED")
        value = self.provider_state.setdefault(key, {})
        if not isinstance(value, dict):
            raise RuntimeError("PROVIDER_STATE_INVALID")
        return value

    def register_provider_cleanup(self, callback: Any) -> None:
        if callable(callback) and callback not in self.provider_cleanup_callbacks:
            self.provider_cleanup_callbacks.append(callback)

    def cleanup(self) -> None:
        callbacks = list(reversed(self.provider_cleanup_callbacks))
        self.provider_cleanup_callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass
        self.provider_state.clear()


_CURRENT_EXECUTION: ContextVar[ExecutionContext | None] = ContextVar("eyle_execution_context", default=None)

def bind_execution(execution: ExecutionContext):
    return _CURRENT_EXECUTION.set(execution)

def reset_execution(token) -> None:
    _CURRENT_EXECUTION.reset(token)

def current_execution() -> ExecutionContext | None:
    return _CURRENT_EXECUTION.get()
