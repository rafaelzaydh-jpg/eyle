from __future__ import annotations

from eyle.core.investigation import (
    apply_investigation_updates,
    open_target_ids,
    reopen_targets_from_review,
    target_evidence_ids,
)


def target(status="open", evidence_ids=None, reason="", goal="Establish X"):
    return {"id": "T1", "goal": goal, "status": status, "evidence_ids": list(evidence_ids or []), "reason": reason}


def test_empty_investigation_is_valid_state():
    state, accepted, rejected = apply_investigation_updates([], previous=[], evidence={})
    assert state == []
    assert accepted == []
    assert rejected == []


def test_declared_target_is_durable_and_goal_immutable():
    state, accepted, rejected = apply_investigation_updates([target()], evidence={})
    assert not rejected and accepted[0]["status"] == "open"
    next_state, accepted2, rejected2 = apply_investigation_updates([], previous=state, evidence={})
    assert next_state == state and not accepted2 and not rejected2
    mutated, _, rejected3 = apply_investigation_updates(
        [target(goal="Different goal")], previous=state, evidence={}
    )
    assert mutated == state
    assert rejected3[0]["reason"] == "INVESTIGATION_TARGET_GOAL_MUTATED:T1"


def test_established_requires_real_evidence_and_attachment_is_additive():
    evidence = {"ev-1": {"id": "ev-1"}, "ev-2": {"id": "ev-2"}}
    state, _, _ = apply_investigation_updates([target()], evidence=evidence)
    unchanged, _, rejected = apply_investigation_updates(
        [target(status="established", reason="Decided")], previous=state, evidence=evidence
    )
    assert unchanged == state
    assert rejected[0]["reason"] == "INVESTIGATION_ESTABLISHED_REQUIRES_EVIDENCE:T1"
    state, _, rejected = apply_investigation_updates(
        [target(status="established", evidence_ids=["ev-1"], reason="Decided")], previous=state, evidence=evidence
    )
    assert not rejected
    state, _, rejected = apply_investigation_updates(
        [target(status="established", evidence_ids=["ev-2"], reason="Still decided")], previous=state, evidence=evidence
    )
    assert not rejected
    assert target_evidence_ids(state) == ["ev-1", "ev-2"]
    assert open_target_ids(state) == []


def test_claim_can_reopen_only_existing_explicit_target():
    state = [target(status="established", evidence_ids=["ev-1"], reason="Decided")]
    review = {
        "claims": [],
        "semantic_gaps": [
            {"type": "scope_gap", "target_id": None, "evidence_ids": [], "reason": "Missing debt"},
            {"type": "insufficient_evidence", "target_id": "T1", "evidence_ids": ["ev-2"], "reason": "Need caller proof"},
            {"type": "scope_gap", "target_id": "T99", "evidence_ids": [], "reason": "Unknown target"},
        ],
    }
    reopened, ids = reopen_targets_from_review(state, review)
    assert ids == ["T1"]
    assert len(reopened) == 1
    assert reopened[0]["status"] == "open"
    assert reopened[0]["evidence_ids"] == ["ev-1", "ev-2"]
