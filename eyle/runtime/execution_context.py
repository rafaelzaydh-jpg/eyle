"""Run-scoped physical execution state with durable logical continuity.

Configuration is immutable input. This context owns the physical deadline and
canonical LLM accounting for one *logical* ECC execution, even when Runtime
pauses for human confirmation and later resumes in another process/job.

Execution continuity persists only serializable mechanical state. Provider sockets,
semaphores/callbacks and other process-local resources are intentionally rebuilt
on resume.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import copy, hashlib, time
from contextvars import ContextVar
from typing import Any, Dict, List, Optional


EXECUTION_CONTINUITY_SCHEMA_VERSION = "execution-continuity-v1"


def _finite_number(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if out != out or out in (float("inf"), float("-inf")):
        return float(default)
    return out


def validate_execution_continuity_state(value: Any) -> Dict[str, Any]:
    """Strictly validate the persisted mechanical execution snapshot.

    This contract deliberately contains no provider/ECC semantics. It only
    carries identity, wall-clock deadline and accounting needed to reconstruct
    the same logical execution after a confirmation boundary.
    """
    if not isinstance(value, dict):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    required = {
        "schema_version", "execution_id", "started_wall_time", "deadline_wall_time",
        "generated_token_limit", "llm_calls", "system_prompt_hashes",
        "history_messages_omitted", "agent_turns", "canonical_request_hash",
        "prompt_tokens_budgeted_physical", "prompt_tokens_estimated_raw",
        "prompt_tokens_actual", "prompt_tokens_cached", "prompt_tokens_uncached",
        "prompt_tokens_effective", "completion_tokens_actual", "reasoning_tokens_actual",
        "terminal_capabilities", "resume_count",
    }
    if set(value) != required or value.get("schema_version") != EXECUTION_CONTINUITY_SCHEMA_VERSION:
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    execution_id = value.get("execution_id")
    if execution_id is not None and (not isinstance(execution_id, str) or not execution_id.strip()):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    started = _finite_number(value.get("started_wall_time"), -1)
    deadline = _finite_number(value.get("deadline_wall_time"), -1)
    if started <= 0 or deadline <= started:
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    ints = (
        "generated_token_limit", "history_messages_omitted", "agent_turns",
        "prompt_tokens_budgeted_physical", "prompt_tokens_estimated_raw",
        "prompt_tokens_actual", "prompt_tokens_cached", "prompt_tokens_uncached",
        "prompt_tokens_effective", "completion_tokens_actual", "reasoning_tokens_actual",
        "resume_count",
    )
    for key in ints:
        item = value.get(key)
        minimum = 1 if key == "generated_token_limit" else 0
        if not isinstance(item, int) or isinstance(item, bool) or item < minimum:
            raise ValueError("EXECUTION_CONTINUITY_INVALID")
    request_hash = value.get("canonical_request_hash")
    if request_hash is not None and (not isinstance(request_hash, str) or len(request_hash) != 64):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    calls = value.get("llm_calls")
    hashes = value.get("system_prompt_hashes")
    terminal = value.get("terminal_capabilities")
    if not isinstance(calls, list) or not isinstance(hashes, list) or not all(isinstance(v, str) for v in hashes):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    if not isinstance(terminal, dict):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    # Cached cannot exceed provider-reported prompt accounting.
    if int(value.get("prompt_tokens_cached") or 0) > int(value.get("prompt_tokens_actual") or 0):
        raise ValueError("EXECUTION_CONTINUITY_INVALID")
    return value


@dataclass
class ExecutionContext:
    started_monotonic: float
    deadline_monotonic: float
    execution_id: Optional[str]
    source_job_id: Optional[int]
    started_wall_time: float = 0.0
    deadline_wall_time: float = 0.0
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
    generated_token_limit: int = 120000
    resume_count: int = 0
    provider_state: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False, compare=False)
    provider_cleanup_callbacks: List[Any] = field(default_factory=list, repr=False, compare=False)
    terminal_capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep direct/test construction compatible with earlier revisions while
        # giving every context a durable wall-clock coordinate.
        now_wall = time.time()
        now_mono = time.monotonic()
        if float(self.started_wall_time or 0) <= 0 or float(self.deadline_wall_time or 0) <= float(self.started_wall_time or 0):
            remaining = max(0.0, float(self.deadline_monotonic) - now_mono)
            elapsed = max(0.0, now_mono - float(self.started_monotonic)) if float(self.started_monotonic) > 0 else 0.0
            self.started_wall_time = now_wall - elapsed
            self.deadline_wall_time = now_wall + remaining

    @classmethod
    def from_config(cls, config: Dict[str, Any], *, execution_id: Optional[str] = None,
                    source_job_id: Optional[int] = None) -> "ExecutionContext":
        agent = (config or {}).get("agent") or {}
        deadline = max(1, int(agent.get("task_deadline_seconds", 900) or 900))
        now_mono = time.monotonic()
        now_wall = time.time()
        llm = (config or {}).get("llm") or {}
        try:
            generated_limit = max(1, int(llm.get("generated_token_fuse", 120000) or 120000))
        except (TypeError, ValueError):
            generated_limit = 120000
        return cls(
            started_monotonic=now_mono,
            deadline_monotonic=now_mono + deadline,
            execution_id=execution_id,
            source_job_id=source_job_id,
            started_wall_time=now_wall,
            deadline_wall_time=now_wall + deadline,
            generated_token_limit=generated_limit,
        )

    @classmethod
    def from_continuation_state(
        cls, config: Dict[str, Any], state: Dict[str, Any], *, source_job_id: Optional[int] = None,
    ) -> "ExecutionContext":
        """Rehydrate the same logical execution in a new physical process/job."""
        validate_execution_continuity_state(state)
        now_mono = time.monotonic()
        now_wall = time.time()
        remaining = max(0.0, float(state["deadline_wall_time"]) - now_wall)
        execution = cls(
            started_monotonic=now_mono - max(0.0, now_wall - float(state["started_wall_time"])),
            deadline_monotonic=now_mono + remaining,
            execution_id=state.get("execution_id"),
            source_job_id=source_job_id,
            started_wall_time=float(state["started_wall_time"]),
            deadline_wall_time=float(state["deadline_wall_time"]),
            llm_calls=copy.deepcopy(state.get("llm_calls") or []),
            system_prompt_hashes=list(state.get("system_prompt_hashes") or []),
            history_messages_omitted=int(state.get("history_messages_omitted") or 0),
            agent_turns=int(state.get("agent_turns") or 0),
            canonical_request_hash=state.get("canonical_request_hash"),
            prompt_tokens_budgeted_physical=int(state.get("prompt_tokens_budgeted_physical") or 0),
            prompt_tokens_estimated_raw=int(state.get("prompt_tokens_estimated_raw") or 0),
            prompt_tokens_actual=int(state.get("prompt_tokens_actual") or 0),
            prompt_tokens_cached=int(state.get("prompt_tokens_cached") or 0),
            prompt_tokens_uncached=int(state.get("prompt_tokens_uncached") or 0),
            prompt_tokens_effective=int(state.get("prompt_tokens_effective") or 0),
            completion_tokens_actual=int(state.get("completion_tokens_actual") or 0),
            reasoning_tokens_actual=int(state.get("reasoning_tokens_actual") or 0),
            generated_token_limit=int(state.get("generated_token_limit") or 120000),
            resume_count=int(state.get("resume_count") or 0) + 1,
            terminal_capabilities=copy.deepcopy(state.get("terminal_capabilities") or {}),
        )
        # Configuration changes while awaiting confirmation must not silently
        # reset/expand the already-created logical deadline or token fuse.
        return execution

    def continuation_state(self) -> Dict[str, Any]:
        state = {
            "schema_version": EXECUTION_CONTINUITY_SCHEMA_VERSION,
            "execution_id": self.execution_id,
            "started_wall_time": float(self.started_wall_time),
            "deadline_wall_time": float(self.deadline_wall_time),
            "generated_token_limit": int(self.generated_token_limit or 0),
            "llm_calls": copy.deepcopy(self.llm_calls),
            "system_prompt_hashes": list(self.system_prompt_hashes),
            "history_messages_omitted": int(self.history_messages_omitted or 0),
            "agent_turns": int(self.agent_turns or 0),
            "canonical_request_hash": self.canonical_request_hash,
            "prompt_tokens_budgeted_physical": int(self.prompt_tokens_budgeted_physical or 0),
            "prompt_tokens_estimated_raw": int(self.prompt_tokens_estimated_raw or 0),
            "prompt_tokens_actual": int(self.prompt_tokens_actual or 0),
            "prompt_tokens_cached": int(self.prompt_tokens_cached or 0),
            "prompt_tokens_uncached": int(self.prompt_tokens_uncached or 0),
            "prompt_tokens_effective": int(self.prompt_tokens_effective or 0),
            "completion_tokens_actual": int(self.completion_tokens_actual or 0),
            "reasoning_tokens_actual": int(self.reasoning_tokens_actual or 0),
            "terminal_capabilities": copy.deepcopy(self.terminal_capabilities),
            "resume_count": int(self.resume_count or 0),
        }
        validate_execution_continuity_state(state)
        return state

    @property
    def deadline_remaining_seconds(self) -> float:
        return max(0.0, float(self.deadline_wall_time) - time.time())

    def bind_session_baseline(self, session: Any, *, reset_agent_turns: bool = True) -> None:
        """Capture physical-job baselines without resetting logical accounting."""
        self.session_turn_start = int(getattr(session, "turn", 0) or 0)
        observations = getattr(session, "observation_ledger", {}) or {}
        self.observation_event_start = len(observations.get("events") or []) if isinstance(observations, dict) else 0
        self.observation_replay_start = max(0, int(observations.get("replay_count") or 0)) if isinstance(observations, dict) else 0
        materials = observations.get("materials") if isinstance(observations, dict) else {}
        self.grounding_ids_start = sorted(str(key) for key in (materials or {}).keys()) if isinstance(materials, dict) else []
        if reset_agent_turns:
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
    def generated_tokens_remaining(self) -> int:
        return max(0, int(self.generated_token_limit or 0) - int(self.completion_tokens_actual or 0))

    @property
    def llm_request_count(self) -> int:
        return sum(len(call.get("attempts") or []) for call in self.llm_calls if isinstance(call, dict))

    @property
    def prompt_token_calibration(self) -> float:
        estimated = int(self.prompt_tokens_estimated_raw or 0)
        actual = int(self.prompt_tokens_actual or 0)
        if estimated <= 0 or actual <= 0:
            return 1.0
        ratio = float(actual) / float(estimated)
        return min(4.0, max(0.75, ratio))

    @property
    def physical_tokens_used(self) -> int:
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
            "generated_token_limit": int(self.generated_token_limit or 0),
            "generated_tokens_remaining": self.generated_tokens_remaining,
            "reasoning_tokens_actual": int(self.reasoning_tokens_actual or 0),
            "total_tokens_effective": effective_total,
            "total_tokens_physical_estimated": self.physical_tokens_used,
            "prompt_token_calibration": round(self.prompt_token_calibration, 4),
            "history_messages_omitted": int(self.history_messages_omitted or 0),
            "execution_resume_count": int(self.resume_count or 0),
            "deadline_remaining_seconds": round(self.deadline_remaining_seconds, 3),
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
