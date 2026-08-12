"""Compact factual self-observation for Main.

This module does not diagnose loops, prescribe recovery, or create semantic
state.  It projects recent canonical Runtime facts (DecisionLedger,
Observation, Claim outcome and ExecutionContext) so Main can see what it just
tried and decide for itself whether to retry, change approach, or stop.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from .execution_context import current_execution


_PROBLEM_OUTCOMES = {"challenge", "rejected", "failed", "blocked"}
_MEANINGFUL_DECISIONS = {
    "final", "claim_review", "tool", "tool_calls", "tool_validation",
    "tool_preflight", "tool_execution", "patches", "patch_validation",
    "needs_user", "protocol", "runtime",
}
_REPLAY_OUTCOMES = {"replayed", "batch_duplicate"}


def _decision_events(session: Any) -> List[Dict[str, Any]]:
    ledger = getattr(session, "decision_ledger", {}) or {}
    values = ledger.get("events") if isinstance(ledger, dict) else []
    return [item for item in (values or []) if isinstance(item, dict)]


def _observation_events(session: Any) -> List[Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    values = ledger.get("events") if isinstance(ledger, dict) else []
    return [item for item in (values or []) if isinstance(item, dict)]


def _materials(session: Any) -> Dict[str, Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    values = ledger.get("materials") if isinstance(ledger, dict) else {}
    return values if isinstance(values, dict) else {}


def _frontiers(session: Any) -> Dict[str, Dict[str, Any]]:
    ledger = getattr(session, "observation_ledger", {}) or {}
    values = ledger.get("frontiers") if isinstance(ledger, dict) else {}
    return values if isinstance(values, dict) else {}


def _last_problem(decisions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in reversed(decisions):
        if str(item.get("outcome") or "") in _PROBLEM_OUTCOMES:
            return item
    return None


def _last_final(decisions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in reversed(decisions):
        if item.get("decision") == "final" and item.get("outcome") == "provisional":
            return item
    return None


def _compact_decision(item: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "turn": item.get("turn"),
        "decision": item.get("decision"),
        "outcome": item.get("outcome"),
    }
    if item.get("reason"):
        out["reason"] = str(item.get("reason"))[:160]
    if item.get("tools"):
        out["tools"] = [str(value) for value in list(item.get("tools") or [])[:4]]
    facts = item.get("facts")
    if isinstance(facts, dict) and facts:
        allowed = {
            "grounding_ids", "workspace_epoch", "issue_kinds", "new_material_ids",
            "frontier_ids", "error_code",
        }
        bounded = {key: copy.deepcopy(value) for key, value in facts.items() if key in allowed}
        if bounded:
            out["facts"] = bounded
    return {key: value for key, value in out.items() if value not in (None, "", [], {})}


def build_operational_feedback(session: Any, *, recent_limit: int = 8, material_limit: int = 8) -> Dict[str, Any]:
    """Project bounded operational facts for Main without semantic diagnosis."""
    decisions = _decision_events(session)
    observations = _observation_events(session)
    materials = _materials(session)
    frontiers = _frontiers(session)
    execution = current_execution()

    meaningful = [
        item for item in decisions
        if str(item.get("decision") or "") in _MEANINGFUL_DECISIONS
    ]
    recent = [_compact_decision(item) for item in meaningful[-max(1, int(recent_limit)):]]

    problem = _last_problem(decisions)
    problem_turn = int(problem.get("turn") or 0) if problem else 0
    problem_epoch = None
    if problem and isinstance(problem.get("facts"), dict):
        value = problem["facts"].get("workspace_epoch")
        if isinstance(value, int) and not isinstance(value, bool):
            problem_epoch = value

    executed_observations = [item for item in observations if item.get("executed") is True]
    last_executed_turn = max((int(item.get("turn") or 0) for item in executed_observations), default=0)
    anchor_turn = max(problem_turn, last_executed_turn)

    replay_decisions = [
        item for item in decisions
        if int(item.get("turn") or 0) > last_executed_turn
        and item.get("decision") == "tool_preflight"
        and str(item.get("outcome") or "") in _REPLAY_OUTCOMES
    ]
    observations_after_problem = [
        item for item in observations
        if problem and int(item.get("turn") or 0) > problem_turn
    ]
    new_material_after_problem: List[str] = []
    new_frontier_after_problem: List[str] = []
    for item in observations_after_problem:
        for material_id in item.get("grounding_ids") or []:
            material_id = str(material_id or "")
            if material_id and material_id not in new_material_after_problem:
                new_material_after_problem.append(material_id)
        for frontier_id in item.get("frontier_ids") or []:
            frontier_id = str(frontier_id or "")
            if frontier_id and frontier_id not in new_frontier_after_problem:
                new_frontier_after_problem.append(frontier_id)

    open_frontier_ids = [
        str(frontier_id)
        for frontier_id, item in frontiers.items()
        if isinstance(item, dict) and item.get("status") == "open"
    ]
    material_ids = [str(value) for value in materials.keys()]

    physical_state: Dict[str, Any] = {
        "workspace_epoch": int(getattr(session, "workspace_epoch", 0) or 0),
        "last_executed_observation_turn": last_executed_turn or None,
        "turns_since_executed_observation": max(0, int(getattr(session, "turn", 0) or 0) - last_executed_turn) if last_executed_turn else None,
        "replay_preflights_since_executed_observation": len(replay_decisions),
        "replay_only_since_last_executed_observation": bool(replay_decisions),
        "material_count": len(material_ids),
        "available_material_ids": material_ids[-max(1, int(material_limit)):],
        "open_frontier_ids": open_frontier_ids[-8:],
    }
    physical_state = {key: value for key, value in physical_state.items() if value not in (None, "", [], {})}
    if executed_observations:
        latest_observation = executed_observations[-1]
        latest_result = latest_observation.get("result") if isinstance(latest_observation.get("result"), dict) else {}
        observation_view: Dict[str, Any] = {
            "turn": latest_observation.get("turn"),
            "tool": latest_observation.get("tool"),
            "status": latest_observation.get("status"),
            "ok": latest_observation.get("ok"),
            "grounding_ids": list(latest_observation.get("grounding_ids") or []),
            "frontier_ids": list(latest_observation.get("frontier_ids") or []),
        }
        if isinstance(latest_result.get("coverage"), dict):
            coverage = latest_result.get("coverage") or {}
            observation_view["coverage"] = {
                key: copy.deepcopy(coverage.get(key))
                for key in ("scope", "examined", "complete", "boundaries")
                if coverage.get(key) not in (None, "", [], {})
            }
        physical_state["last_executed_observation"] = {
            key: value for key, value in observation_view.items() if value not in (None, "", [], {})
        }

    out: Dict[str, Any] = {
        "recent_operations": recent,
        "physical_state": physical_state,
    }

    if problem:
        problem_view = _compact_decision(problem)
        since_problem: Dict[str, Any] = {
            "turns": max(0, int(getattr(session, "turn", 0) or 0) - problem_turn),
            "executed_observations": sum(1 for item in observations_after_problem if item.get("executed") is True),
            "coverage_records": sum(
                1 for item in observations_after_problem
                if isinstance((item.get("result") or {}).get("coverage"), dict)
            ),
            "new_material_ids": new_material_after_problem[-8:],
            "new_frontier_ids": new_frontier_after_problem[-8:],
            "workspace_changed": (
                int(getattr(session, "workspace_epoch", 0) or 0) != problem_epoch
                if problem_epoch is not None else False
            ),
        }
        replay_after_problem = [
            item for item in decisions
            if int(item.get("turn") or 0) > problem_turn
            and item.get("decision") == "tool_preflight"
            and str(item.get("outcome") or "") in _REPLAY_OUTCOMES
        ]
        since_problem["replay_preflights"] = len(replay_after_problem)
        problem_view["since"] = since_problem
        out["last_problem"] = problem_view

        matching = sum(
            1 for item in meaningful[-24:]
            if item.get("decision") == problem.get("decision")
            and item.get("outcome") == problem.get("outcome")
            and item.get("reason") == problem.get("reason")
        )
        if matching > 1:
            out["last_problem"]["matching_recent_occurrences"] = matching

    final = _last_final(decisions)
    if final:
        facts = final.get("facts") if isinstance(final.get("facts"), dict) else {}
        out["last_provisional_final"] = {
            "turn": final.get("turn"),
            "grounding_ids": list(facts.get("grounding_ids") or []),
        }

    if execution is not None:
        out["physical_budget"] = {
            "used": int(execution.physical_tokens_used),
            "remaining": int(execution.physical_tokens_remaining),
            "limit": int(execution.max_total_tokens or 0),
        }

    # Avoid a permanently noisy empty component on the first conversational turn.
    if not recent and not materials and not open_frontier_ids and not problem:
        return {"physical_budget": out.get("physical_budget", {})} if execution is not None else {}
    return out
