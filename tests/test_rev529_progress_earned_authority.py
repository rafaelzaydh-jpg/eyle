from __future__ import annotations

import json

import pytest

from eyle.core import agent as core_agent
from eyle.core.investigation import apply_investigation_updates
from eyle.core.session import AgentSession
from eyle.runtime.config import ConfigError, validar_config
from llm.executar import PROMPT_AGENTE
from tests.canonical import base_config, investigation_target


def test_investigation_evidence_ids_are_additive_delta_and_never_drop_committed_ids():
    previous = [
        investigation_target(
            "T1", goal="Establish usage", status="open",
            evidence_ids=["ev-1", "ev-2"], reason="partial",
        )
    ]
    evidence = {"ev-1": {}, "ev-2": {}, "ev-3": {}}

    canonical, accepted, rejected, progress = apply_investigation_updates(
        [investigation_target(
            "T1", goal="Establish usage", status="established",
            evidence_ids=["ev-3"], reason="complete",
        )],
        previous=previous,
        evidence=evidence,
    )

    assert rejected == []
    assert accepted[0]["changed"] is True
    assert canonical[0]["evidence_ids"] == ["ev-1", "ev-2", "ev-3"]
    assert progress == [{
        "target_id": "T1",
        "added_evidence_ids": ["ev-3"],
        "established_transition": True,
    }]

    # An empty Evidence delta can change semantic status/reason without asking
    # the Agent to resend the monotonic Evidence snapshot.
    reopened, accepted2, rejected2, progress2 = apply_investigation_updates(
        [investigation_target(
            "T1", goal="Establish usage", status="open",
            evidence_ids=[], reason="review reopened",
        )],
        previous=canonical,
        evidence=evidence,
    )
    assert rejected2 == [] and accepted2
    assert reopened[0]["evidence_ids"] == ["ev-1", "ev-2", "ev-3"]
    assert progress2 == []


def test_same_evidence_can_never_mint_committed_progress_twice_even_on_other_target():
    session = AgentSession("audit")
    session.evidence = {"ev-1": {}, "ev-2": {}}
    first = [{"target_id": "T1", "added_evidence_ids": ["ev-1"]}]
    second = [{"target_id": "T2", "added_evidence_ids": ["ev-1"]}]

    assert core_agent._record_committed_progress(session, first) is True
    assert session.committed_progress_epoch == 1
    assert session.progress_credited_evidence_ids == ["ev-1"]

    # Semantic remapping is allowed; physical authority farming is not.
    assert core_agent._record_committed_progress(session, second) is False
    assert session.committed_progress_epoch == 1
    assert len(session.committed_progress_history) == 1

    assert core_agent._record_committed_progress(
        session, [{"target_id": "T2", "added_evidence_ids": ["ev-1", "ev-2"]}]
    ) is True
    assert session.committed_progress_epoch == 2
    assert session.committed_progress_history[-1]["added_evidence_ids"] == ["ev-2"]
    assert session.progress_credited_evidence_ids == ["ev-1", "ev-2"]


def test_every_unspent_progress_epoch_grants_four_tools_without_cumulative_cap():
    cfg = base_config(claims_mode="off")
    cfg["agent"]["committed_progress_extension_calls"] = 4
    session = AgentSession("audit")
    session.investigation = [investigation_target("T1", goal="Establish usage")]
    session.committed_progress_epoch = 3

    # Three independently deposited epochs were not yet converted: all three
    # must keep their earned authority instead of being collapsed to one +4 or
    # clipped at the old +8 ceiling.
    assert core_agent._grant_committed_progress_extension(session, cfg) == 12
    assert session.earned_tool_extension == 12
    assert session.tool_extension_cycles == 3
    assert session.last_extension_progress_epoch == 3
    assert [item["progress_epoch"] for item in session.tool_extension_history] == [1, 2, 3]
    assert [item["granted"] for item in session.tool_extension_history] == [4, 4, 4]
    assert core_agent._tool_budget_state(session, cfg)["effective_limit"] == 24

    # Already converted epochs are immutable/spent and cannot be granted again.
    assert core_agent._grant_committed_progress_extension(session, cfg) == 0
    session.committed_progress_epoch = 4
    assert core_agent._grant_committed_progress_extension(session, cfg) == 4
    assert session.earned_tool_extension == 16
    assert core_agent._tool_budget_state(session, cfg)["effective_limit"] == 28


def test_old_session_backfills_credit_once_evidence_from_progress_history():
    restored = AgentSession.from_dict({
        "request": "audit",
        "committed_progress_epoch": 2,
        "last_extension_progress_epoch": 2,
        "committed_progress_history": [
            {"epoch": 1, "added_evidence_ids": ["ev-1"]},
            {"epoch": 2, "added_evidence_ids": ["ev-2", "ev-1"]},
        ],
    })
    assert restored.progress_credited_evidence_ids == ["ev-1", "ev-2"]
    restored.evidence = {"ev-1": {}, "ev-2": {}}
    assert core_agent._record_committed_progress(
        restored, [{"target_id": "T3", "added_evidence_ids": ["ev-1"]}]
    ) is False


def test_removed_max_earned_extension_config_is_not_kept_as_legacy_route():
    assert validar_config({"agent": {"committed_progress_extension_calls": 4}})
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD:agent:max_earned_tool_extension"):
        validar_config({"agent": {"max_earned_tool_extension": 8}})


def test_agent_prompt_exposes_true_additive_delta_and_credit_once_rule():
    assert "evidence_ids in investigation_updates are additive delta" in PROMPT_AGENTE
    assert "send only newly material IDs" in PROMPT_AGENTE
    assert "Evidence that already funded committed progress cannot fund tool authority again" in PROMPT_AGENTE


def test_stale_or_missing_evidence_cannot_mint_committed_progress():
    session = AgentSession("audit")
    session.evidence = {"ev-stale": {"stale": True}}
    assert core_agent._record_committed_progress(
        session, [{"target_id": "T1", "added_evidence_ids": ["ev-stale", "ev-missing"]}]
    ) is False
    assert session.committed_progress_epoch == 0
    assert session.progress_credited_evidence_ids == []
