"""Deterministic fixed-point safety for Eyle execution.

This module belongs to Eyle Runtime because it reasons only about execution
facts already owned by Eyle: observable results, physical progress, Task-state
transitions and reality epochs. Provider transport/wire repair belongs to the
Adapter and is deliberately absent here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Dict, Iterable

# Rev3.7.6: a deterministic fixed point is a local navigation failure, not a
# terminal task failure. The first no-progress outcome blocks that exact action
# signature for the current reality epoch. Main must choose another observable
# path (continue/recall/refine/conclude). Genuine progress clears the block.
#
# Kept as a compatibility/telemetry constant for callers that display the old
# warning threshold; Runtime no longer turns the threshold into task death.
NO_PROGRESS_REPEATS_AFTER_WARNING = 2

_EPHEMERAL_KEYS = {
    "handle",
    "snapshot_id",
    "owner_execution_id",
}


def _stable_value(value: Any, *, parent: str = "") -> Any:
    """Remove navigation identities that do not represent new information."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items(), key=lambda kv: str(kv[0])):
            key = str(raw_key)
            if key in _EPHEMERAL_KEYS:
                continue
            if parent == "frontiers" and key in {"id", "frontier_id"}:
                continue
            out[key] = _stable_value(raw_value, parent=key)
        return out
    if isinstance(value, list):
        return [_stable_value(item, parent=parent) for item in value]
    if isinstance(value, tuple):
        return [_stable_value(item, parent=parent) for item in value]
    return value


def stable_fingerprint(value: Any) -> str:
    raw = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def result_fingerprint(result: Dict[str, Any]) -> str:
    return stable_fingerprint(result if isinstance(result, dict) else {"value": result})


def _result_can_be_new_information(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    # These statuses are Runtime control/navigation facts, not new task facts.
    # Treating them as novel would clear the very recovery episode they describe.
    return str(result.get("status") or "") not in {
        "already_observed",
        "recovery_required",
        "fixed_point_blocked",
    }


@dataclass(frozen=True)
class ProgressState:
    meaningful_progress: bool
    novel_result: bool
    no_progress_repeat_count: int
    no_progress_key: str
    terminal: bool


@dataclass
class ExecutionProgress:
    """Execution-local detector for valid ECC fixed points.

    It never interprets user meaning, protocol syntax, Memory semantics or
    provider behavior. It only answers whether Eyle execution produced a new
    observable fact/state.
    """

    seen_result_fingerprints: set[str] = field(default_factory=set)
    no_progress_counts: Dict[str, int] = field(default_factory=dict)
    blocked_actions: Dict[str, int] = field(default_factory=dict)
    checkpointed_blocks: set[str] = field(default_factory=set)
    operations_since_task_state_progress: int = 0
    provider_tokens_at_last_task_state_progress: int = 0
    provider_tokens_latest: int = 0
    fixed_points_blocked_total: int = 0
    coverage_advanced_since_task_state_progress: bool = False
    physical_mutations_since_task_state_progress: int = 0

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionProgress":
        """Rehydrate only deterministic execution facts.

        This state belongs to the active AgentSession, not persistent semantic
        Memory. Unknown/legacy shapes are rejected by AgentSession validation.
        """
        if value in (None, {}):
            return cls()
        if not isinstance(value, dict):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        expected = {
            "seen_result_fingerprints", "no_progress_counts", "blocked_actions",
            "checkpointed_blocks", "operations_since_task_state_progress",
            "provider_tokens_at_last_task_state_progress", "provider_tokens_latest",
            "fixed_points_blocked_total", "coverage_advanced_since_task_state_progress",
            "physical_mutations_since_task_state_progress",
        }
        if set(value) != expected:
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        seen = value.get("seen_result_fingerprints")
        counts = value.get("no_progress_counts")
        blocked = value.get("blocked_actions")
        checkpointed = value.get("checkpointed_blocks")
        if not isinstance(seen, list) or not all(isinstance(v, str) for v in seen):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        if not isinstance(checkpointed, list) or not all(isinstance(v, str) for v in checkpointed):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        if not isinstance(counts, dict) or not all(isinstance(k, str) and isinstance(v, int) and v >= 0 for k, v in counts.items()):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        if not isinstance(blocked, dict) or not all(isinstance(k, str) and isinstance(v, int) and v >= 0 for k, v in blocked.items()):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        ints = (
            "operations_since_task_state_progress", "provider_tokens_at_last_task_state_progress",
            "provider_tokens_latest", "fixed_points_blocked_total",
            "physical_mutations_since_task_state_progress",
        )
        if any(not isinstance(value.get(k), int) or isinstance(value.get(k), bool) or int(value.get(k)) < 0 for k in ints):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        if not isinstance(value.get("coverage_advanced_since_task_state_progress"), bool):
            raise ValueError("EXECUTION_PROGRESS_SCHEMA_INVALID")
        return cls(
            seen_result_fingerprints=set(seen),
            no_progress_counts={str(k): int(v) for k, v in counts.items()},
            blocked_actions={str(k): int(v) for k, v in blocked.items()},
            checkpointed_blocks=set(checkpointed),
            operations_since_task_state_progress=int(value["operations_since_task_state_progress"]),
            provider_tokens_at_last_task_state_progress=int(value["provider_tokens_at_last_task_state_progress"]),
            provider_tokens_latest=int(value["provider_tokens_latest"]),
            fixed_points_blocked_total=int(value["fixed_points_blocked_total"]),
            coverage_advanced_since_task_state_progress=bool(value["coverage_advanced_since_task_state_progress"]),
            physical_mutations_since_task_state_progress=int(value["physical_mutations_since_task_state_progress"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seen_result_fingerprints": sorted(self.seen_result_fingerprints),
            "no_progress_counts": {str(k): int(v) for k, v in sorted(self.no_progress_counts.items())},
            "blocked_actions": {str(k): int(v) for k, v in sorted(self.blocked_actions.items())},
            "checkpointed_blocks": sorted(self.checkpointed_blocks),
            "operations_since_task_state_progress": int(self.operations_since_task_state_progress),
            "provider_tokens_at_last_task_state_progress": int(self.provider_tokens_at_last_task_state_progress),
            "provider_tokens_latest": int(self.provider_tokens_latest),
            "fixed_points_blocked_total": int(self.fixed_points_blocked_total),
            "coverage_advanced_since_task_state_progress": bool(self.coverage_advanced_since_task_state_progress),
            "physical_mutations_since_task_state_progress": int(self.physical_mutations_since_task_state_progress),
        }

    def convergence_view(self, provider_tokens_total: int | None = None) -> Dict[str, Any]:
        """Expose mechanical pressure signals; Main alone interprets them."""
        latest = int(self.provider_tokens_latest if provider_tokens_total is None else max(0, int(provider_tokens_total)))
        baseline = min(latest, int(self.provider_tokens_at_last_task_state_progress or 0))
        return {
            "operations_since_task_state_progress": int(self.operations_since_task_state_progress),
            "provider_tokens_since_task_state_progress": max(0, latest - baseline),
            "fixed_points_blocked": int(self.fixed_points_blocked_total),
            "coverage_advanced": bool(self.coverage_advanced_since_task_state_progress),
            "physical_mutations": int(self.physical_mutations_since_task_state_progress),
        }

    @staticmethod
    def _block_key(action_signature: str, reality_epoch: int) -> str:
        return stable_fingerprint({"action": str(action_signature or ""), "reality_epoch": int(reality_epoch or 0)})

    def checkpoint_needed_for_block(self, action_signature: str, reality_epoch: int) -> bool:
        key = self._block_key(action_signature, reality_epoch)
        if key in self.checkpointed_blocks:
            return False
        self.checkpointed_blocks.add(key)
        return True

    def is_blocked(self, action_signature: str, reality_epoch: int) -> bool:
        """Return whether this exact action is blocked in the current reality."""
        signature = str(action_signature or "")
        if not signature:
            return False
        return self.blocked_actions.get(signature) == int(reality_epoch or 0)

    def blocked_action_count(self) -> int:
        return len(self.blocked_actions)

    def observe(
        self,
        *,
        action_signature: str,
        results: Iterable[Dict[str, Any]],
        physical_progress: bool,
        task_state_progress: bool,
        reality_epoch: int,
        operation_count: int = 1,
        provider_tokens_total: int = 0,
        coverage_advanced: bool = False,
        physical_mutations: int = 0,
    ) -> ProgressState:
        rows = [dict(item) for item in results if isinstance(item, dict)]
        fingerprints = [result_fingerprint(item) for item in rows]

        self.provider_tokens_latest = max(0, int(provider_tokens_total or 0))
        if task_state_progress:
            self.operations_since_task_state_progress = 0
            self.provider_tokens_at_last_task_state_progress = self.provider_tokens_latest
            self.coverage_advanced_since_task_state_progress = False
            self.physical_mutations_since_task_state_progress = 0
        else:
            self.operations_since_task_state_progress += max(0, int(operation_count or 0))
            self.coverage_advanced_since_task_state_progress = bool(
                self.coverage_advanced_since_task_state_progress or coverage_advanced
            )
            self.physical_mutations_since_task_state_progress += max(0, int(physical_mutations or 0))

        novel = False
        for item, fingerprint in zip(rows, fingerprints):
            if (
                fingerprint not in self.seen_result_fingerprints
                and _result_can_be_new_information(item)
            ):
                novel = True
            self.seen_result_fingerprints.add(fingerprint)

        meaningful = bool(physical_progress or task_state_progress or novel)
        if meaningful:
            # Real Eyle progress starts a fresh fixed-point episode. Historical
            # fingerprints remain execution-wide so old information can never
            # become novel again merely because the counter reset. A changed
            # reality/task path also makes previously blocked actions eligible.
            self.no_progress_counts.clear()
            self.blocked_actions.clear()
            return ProgressState(True, novel, 0, "", False)

        key = stable_fingerprint({
            "action": str(action_signature or ""),
            "results": fingerprints,
            "reality_epoch": int(reality_epoch or 0),
        })
        repeat = int(self.no_progress_counts.get(key, 0)) + 1
        self.no_progress_counts[key] = repeat

        # Block the action immediately after its first proven no-progress state.
        # This prevents repeated physical/replay work while preserving the job.
        signature = str(action_signature or "")
        if signature:
            epoch = int(reality_epoch or 0)
            if self.blocked_actions.get(signature) != epoch:
                self.fixed_points_blocked_total += 1
            self.blocked_actions[signature] = epoch

        return ProgressState(
            meaningful_progress=False,
            novel_result=False,
            no_progress_repeat_count=repeat,
            no_progress_key=key,
            # Compatibility field: Rev3.7.6 fixed points are recoverable.
            terminal=False,
        )
