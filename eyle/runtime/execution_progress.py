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

# First repeated no-progress outcome is surfaced to Main. If the same
# deterministic fixed point repeats once more, execution is terminal. This is
# not a turn ceiling: any genuinely new result/state starts a new episode.
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
    return str(result.get("status") or "") != "already_observed"


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

    def observe(
        self,
        *,
        action_signature: str,
        results: Iterable[Dict[str, Any]],
        physical_progress: bool,
        task_state_progress: bool,
        reality_epoch: int,
    ) -> ProgressState:
        rows = [dict(item) for item in results if isinstance(item, dict)]
        fingerprints = [result_fingerprint(item) for item in rows]

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
            # become novel again merely because the counter reset.
            self.no_progress_counts.clear()
            return ProgressState(True, novel, 0, "", False)

        key = stable_fingerprint({
            "action": str(action_signature or ""),
            "results": fingerprints,
            "reality_epoch": int(reality_epoch or 0),
        })
        repeat = int(self.no_progress_counts.get(key, 0)) + 1
        self.no_progress_counts[key] = repeat
        return ProgressState(
            meaningful_progress=False,
            novel_result=False,
            no_progress_repeat_count=repeat,
            no_progress_key=key,
            terminal=repeat >= NO_PROGRESS_REPEATS_AFTER_WARNING,
        )
