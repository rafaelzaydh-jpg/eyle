from __future__ import annotations

from eyle.core.investigation import apply_investigation_updates, investigation_grounding_ids


def target(status="open", grounding_ids=None, conclusion="", reason="", goal="Establish X"):
    return {"id": "T1", "goal": goal, "status": status, "grounding_ids": list(grounding_ids or []), "conclusion": conclusion, "reason": reason}


def test_empty_investigation_is_valid_state():
    state, accepted, rejected = apply_investigation_updates([], previous=[], grounding={})
    assert state == [] and accepted == [] and rejected == []


def test_declared_target_is_durable_and_main_can_revise_goal():
    state,accepted,rejected=apply_investigation_updates([target()],grounding={})
    assert not rejected and accepted[0]["status"]=="open"
    same,accepted2,rejected2=apply_investigation_updates([],previous=state,grounding={})
    assert same==state and not accepted2 and not rejected2
    revised,accepted3,rejected3=apply_investigation_updates([target(goal="Different goal")],previous=state,grounding={})
    assert not rejected3 and accepted3[0]["changed"] is True
    assert revised[0]["goal"]=="Different goal"

def test_established_requires_real_grounding_and_grounding_remains_additive():
    grounding={"mat-0001":{"id":"mat-0001"},"mat-0002":{"id":"mat-0002"}}
    state,_,_=apply_investigation_updates([target()],grounding=grounding)
    unchanged,_,rejected=apply_investigation_updates([target(status="established",reason="Decided")],previous=state,grounding=grounding)
    assert unchanged == state
    assert rejected[0]["reason"].startswith("INVESTIGATION_ESTABLISHED_GROUNDING_REQUIRED")
    state,_,rejected=apply_investigation_updates([target(status="established",grounding_ids=["mat-0001"],conclusion="X is established",reason="Decided")],previous=state,grounding=grounding)
    assert not rejected
    state,_,rejected=apply_investigation_updates([target(status="established",grounding_ids=["mat-0002"],conclusion="X remains established",reason="Still decided")],previous=state,grounding=grounding)
    assert not rejected and investigation_grounding_ids(state)==["mat-0001","mat-0002"]

def test_unknown_grounding_is_rejected_without_promotion_or_aliases():
    state, _, _ = apply_investigation_updates([target()], grounding={})
    unchanged, _, rejected = apply_investigation_updates(
        [target(status="established", grounding_ids=["ev-src-0027"], reason="fake")],
        previous=state, grounding={"mat-0001": {}},
    )
    assert unchanged == state
    assert rejected[0]["reason"].startswith("INVESTIGATION_UNKNOWN_GROUNDING:T1:")
