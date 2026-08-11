from __future__ import annotations

import json

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from eyle.core.claim_review import (
    build_answer_anchors,
    build_request_anchors,
    claim_review_output_budget,
    normalize_claim_review,
)
from eyle.core.evidence import items as evidence_items, promote_source_records
from eyle.core.investigation import apply_investigation_updates
from eyle.core.session import AgentSession
from eyle.core.source_record import items as source_items
from llm.structured import contract_instruction
from tests.canonical import base_config, claim, review


def _ctx(root, *, max_ranges=3, max_matches=3, handles=None):
    cfg = base_config()
    cfg["agent"]["max_search_ranges"] = max_ranges
    cfg["agent"]["max_search_matches"] = max_matches
    return {
        "projeto": {"caminho_origem": str(root)},
        "config": cfg,
        "observation_handles": handles if handles is not None else {},
        "workspace_epoch": 0,
    }


def test_search_diversifies_files_before_inline_projection_limit(tmp_path):
    # A noisy first file must not monopolize the bounded model-facing projection.
    (tmp_path / "a.py").write_text("\n".join("needle" if i % 20 == 0 else "x" for i in range(120)) + "\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("needle\n", encoding="utf-8")

    result = tools.executar_tool("search_code", {"query": "needle"}, _ctx(tmp_path))
    detail = result["detail"]

    assert detail["matches_observed"] >= 8
    assert detail["files_with_matches"] == 3
    assert detail["ranges_materialized"] == 3
    assert set(detail["materialized_files"]) == {"a.py", "b.py", "c.py"}
    assert detail["scope_complete"] is True
    assert detail["coverage_complete"] is True
    assert detail["projection_complete"] is False


def test_search_projection_continuation_does_not_downgrade_physical_coverage(tmp_path):
    for index in range(5):
        (tmp_path / f"f{index}.py").write_text("needle\n", encoding="utf-8")
    handles = {}
    context = _ctx(tmp_path, max_ranges=2, max_matches=2, handles=handles)

    result = tools.executar_tool("search_code", {"query": "needle"}, context)
    detail = result["detail"]
    assert detail["scope_complete"] is True
    assert detail["coverage_complete"] is True
    assert detail["projection_complete"] is False
    frontier = next(item for item in result["frontiers"] if item["kind"] == "projection_continuation")
    assert frontier["handle"].startswith("handle:")
    assert frontier["handle"] in handles

    expanded = tools.executar_tool("expand_observation", {"handle": frontier["handle"]}, context)
    assert expanded["ok"] is True
    assert expanded["observations"]


def test_tool_materialization_creates_source_record_not_evidence(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = AgentSession("inspect")
    raw = tools.executar_tool("read_file", {"path": "app.py"}, _ctx(tmp_path))
    projected = core_agent._model_tool_result(session, "read_file", raw, base_config(), {"path": "app.py"})

    assert projected["source_record_ids"] == ["src-0001"]
    assert list(source_items(session.source_record_ledger)) == ["src-0001"]
    assert evidence_items(session.evidence_ledger) == {}


def test_only_explicit_source_selection_is_promoted_to_evidence(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("B = 2\n", encoding="utf-8")
    session = AgentSession("inspect")
    for path in ("a.py", "b.py"):
        raw = tools.executar_tool("read_file", {"path": path}, _ctx(tmp_path))
        core_agent._model_tool_result(session, "read_file", raw, base_config(), {"path": path})

    promoted, missing = promote_source_records(
        session.evidence_ledger, session.source_record_ledger, ["src-0002"], admitted_by="test",
    )
    assert missing == []
    assert promoted == ["ev-src-0002"]
    assert set(evidence_items(session.evidence_ledger)) == {"ev-src-0002"}
    assert evidence_items(session.evidence_ledger)["ev-src-0002"]["source_record_id"] == "src-0002"


def test_investigation_explicit_source_ref_promotes_and_stores_canonical_evidence(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = AgentSession("inspect")
    raw = tools.executar_tool("read_file", {"path": "app.py"}, _ctx(tmp_path))
    core_agent._model_tool_result(session, "read_file", raw, base_config(), {"path": "app.py"})

    updated, accepted, rejected = apply_investigation_updates(
        [{"id": "T1", "goal": "Establish VALUE", "status": "established", "evidence_ids": ["src-0001"], "reason": "Observed source."}],
        previous=[], evidence=evidence_items(session.evidence_ledger), source_records=source_items(session.source_record_ledger),
    )
    assert rejected == []
    assert accepted[0]["evidence_ids"] == ["ev-src-0001"]
    assert updated[0]["evidence_ids"] == ["ev-src-0001"]


def test_request_anchors_are_literal_coordinates_not_requirements():
    request = "Give me five examples. If only three are established, say so."
    anchors = build_request_anchors(request, max_chars=24)
    assert anchors
    assert [item["ref"] for item in anchors] == [f"request:r{i}" for i in range(1, len(anchors) + 1)]
    assert all(item["text"] in request for item in anchors)
    # No semantic fields such as required/count/obligation are invented.
    assert all(set(item) == {"id", "ref", "text", "start", "end"} for item in anchors)


def test_claim_contract_instruction_matches_current_schema_shape():
    instruction = contract_instruction("claim_verifier")
    assert "{status,grounding_refs,reason}" in instruction
    assert "satisfied|gap|blocked" in instruction
    assert "request:rN" in instruction
    assert "exactly {status,reason}" not in instruction


def test_claim_protocol_rejects_satisfied_with_material_omission():
    answer = "Only one part."
    answer_anchors = build_answer_anchors(answer)
    request_anchors = build_request_anchors("Answer A and B.")
    raw = review(
        semantic_gaps=[{
            "type": "material_omission", "target_id": None,
            "grounding_refs": ["request:r1", "answer:a1"],
            "required_property": "B", "reason": "B is omitted.",
        }],
        material_status="satisfied", material_grounding=["request:r1", "answer:a1"],
    )
    ok, reason, _ = normalize_claim_review(
        raw, {}, answer=answer, answer_anchors=answer_anchors, request_anchors=request_anchors,
    )
    assert ok is False
    assert reason == "CLAIM_REVIEW_SATISFIED_WITH_SEMANTIC_GAP"


def test_claim_protocol_rejects_gap_without_concrete_semantic_debt():
    answer = "Partial."
    raw = review(material_status="gap", material_grounding=["request"])
    ok, reason, _ = normalize_claim_review(raw, {}, answer=answer, answer_anchors=build_answer_anchors(answer))
    assert ok is False
    assert reason == "CLAIM_REVIEW_GAP_REQUIRES_SEMANTIC_DEBT"


def test_claim_blocked_requires_runtime_grounding():
    answer = "Cannot complete."
    raw = review(material_status="blocked", material_grounding=["request"])
    ok, reason, _ = normalize_claim_review(raw, {}, answer=answer, answer_anchors=build_answer_anchors(answer))
    assert ok is False
    assert reason == "CLAIM_REVIEW_BLOCKED_REQUIRES_RUNTIME_GROUNDING"


def test_claim_budget_scales_with_request_even_when_answer_is_equally_short():
    answer = "Done."
    short = "Check A."
    long = " ".join(f"Constraint {i}." for i in range(40))
    short_budget = claim_review_output_budget(
        answer, request=short, base_tokens=128,
        answer_anchor_count=len(build_answer_anchors(answer)), request_anchor_count=len(build_request_anchors(short)),
    )
    long_budget = claim_review_output_budget(
        answer, request=long, base_tokens=128,
        answer_anchor_count=len(build_answer_anchors(answer)), request_anchor_count=len(build_request_anchors(long)),
    )
    assert long_budget > short_budget


def test_agent_contract_states_objective_projection_and_truth_priority():
    from llm.executar import PROMPT_AGENTE
    assert "src-* is observed/citable material, NOT admitted Evidence" in PROMPT_AGENTE
    assert "YOU decide relevance" in PROMPT_AGENTE
    assert "Requested quantity never licenses invention" in PROMPT_AGENTE
    assert "projection_complete=false" in PROMPT_AGENTE

def test_persisted_synthetic_source_record_requires_reexecution_before_admission(tmp_path):
    session = AgentSession("inspect")
    raw = tools.executar_tool("inspect_project", {}, _ctx(tmp_path))
    core_agent._model_tool_result(session, "inspect_project", raw, base_config(), {})
    assert "src-0001" in source_items(session.source_record_ledger)

    restored = AgentSession.from_dict(session.to_dict())
    from eyle.core.source_record import rehydrate as rehydrate_sources
    rehydrate_sources(restored.source_record_ledger, str(tmp_path), max_lines=400)
    record = source_items(restored.source_record_ledger)["src-0001"]
    assert record["rehydration_error"] == "SOURCE_RECORD_REEXECUTION_REQUIRED"
    promoted, missing = promote_source_records(
        restored.evidence_ledger, restored.source_record_ledger, ["src-0001"], admitted_by="test",
    )
    assert promoted == []
    assert missing == ["src-0001"]
