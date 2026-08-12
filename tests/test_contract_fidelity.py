from __future__ import annotations

import json

import eyle.core.tools as tools
from eyle.core.claim_review import build_answer_anchors, compact_grounding, review_prompt
from eyle.core.tools import capability_observation_signature as observation_signature
from tests.canonical import base_config, review, issue


def _ctx(root):
    return {"projeto": {"caminho_origem": str(root)}, "config": base_config(), "observation_ledger": {"handles": {}}}


def test_symbol_relations_observation_identity_covers_result_shaping_arguments():
    base = {"symbol": "x"}
    incoming = observation_signature("symbol_relations", {**base, "direction": "incoming"})
    outgoing = observation_signature("symbol_relations", {**base, "direction": "outgoing"})
    text_refs = observation_signature("symbol_relations", {**base, "direction": "incoming", "include_text_references": True})
    wider = observation_signature("symbol_relations", {**base, "direction": "incoming", "max_edges": 100})
    assert len({incoming, outgoing, text_refs, wider}) == 4
    assert observation_signature("symbol_relations", base) == observation_signature(
        "symbol_relations", {**base, "direction": "both", "include_text_references": False, "max_depth": 6, "max_edges": 60, "roots": []}
    )


def test_tool_validator_rejects_enum_before_execution(tmp_path):
    (tmp_path / "x.py").write_text("def x():\n    return 1\n", encoding="utf-8")
    result = tools.executar_tool("symbol_relations", {"symbol": "x", "direction": "callers"}, _ctx(tmp_path))
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["executed"] is False
    assert "incoming" in str(result["detail"])


def test_tool_validator_rejects_invalid_array_items_before_execution(tmp_path):
    result = tools.executar_tool("symbol_relations", {"symbol": "x", "roots": [123]}, _ctx(tmp_path))
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["executed"] is False


def test_capability_index_exposes_small_enums_without_full_catalog():
    index = tools.gerar_indice_capabilities(allowed_names={"symbol_relations"})
    assert len(index) == 1
    assert "direction?:incoming|outgoing|both" in index[0]


def test_claim_packet_uses_observation_grounding_and_no_evidence_layer():
    anchors = build_answer_anchors("Alpha. Beta.")
    grounding = {"mat-0001": {"locator": {"kind":"file","path":"x.py","line_start":1,"line_end":1}, "content_hash":"h", "content": "x=1"}}
    view = compact_grounding(grounding, ["mat-0001"], max_chars_per_item=200)
    runtime = [{"ref": "runtime:r1", "tool": "run_command", "status": "failed", "ok": False, "executed": False}]
    packet = json.loads(review_prompt(
        "Alpha. Beta.", view, "Do it", answer_anchors=anchors, runtime_facts=runtime,
    ))
    assert packet["answer_anchors"][0]["ref"] == "answer:a1"
    assert packet["observed_material"][0]["ref"] == "observation:mat-0001"
    assert packet["runtime_facts"][0]["ref"] == "runtime:r1"
    assert "investigation" not in packet
    assert "evidence" not in packet


def test_claim_parser_rejects_unprefixed_grounding_ref_locally():
    from llm.structured import StructuredResponseError, parse_claim_review_response
    raw = review(issues=[issue(grounding_refs=["a1"])])
    try:
        parse_claim_review_response(raw)
    except StructuredResponseError as error:
        assert error.code == "CLAIM_REVIEW_GROUNDING_REF_FORMAT_INVALID"
    else:
        raise AssertionError("local Claim parser must reject noncanonical refs")

def test_claim_parser_requires_canonical_answer_refs_and_rejects_removed_target_field():
    from llm.structured import StructuredResponseError, parse_claim_review_response
    base={"kind":"unsupported","answer_ref":"a1","grounding_refs":["request:r1"],"reason":"x"}
    try: parse_claim_review_response({"verdict":"challenge","issues":[base]})
    except StructuredResponseError as error: assert error.code=="CLAIM_REVIEW_ANSWER_REF_INVALID"
    else: raise AssertionError("short answer ref must fail")
    legacy={**base,"answer_ref":"answer:a1","target_id":"investigation:T1"}
    try: parse_claim_review_response({"verdict":"challenge","issues":[legacy]})
    except StructuredResponseError as error: assert error.code=="CLAIM_REVIEW_ISSUE_SHAPE_INVALID"
    else: raise AssertionError("Claim must reject Investigation coordinates")


def test_symbol_relations_model_view_is_bounded_but_preserves_full_counts():
    import eyle.core.agent as core_agent
    from eyle.core.session import AgentSession

    detail = {
        "symbol": "target", "path_filter": None, "direction": "both", "include_text_references": False,
        "backend": "python_ast", "definitions": [{"node": f"def-{i}"} for i in range(20)],
        "incoming": [{"node": f"in-{i}"} for i in range(30)], "outgoing": [{"node": f"out-{i}"} for i in range(30)],
        "structural_references": [{"node": f"ref-{i}"} for i in range(20)],
        "imports": [{"node": f"import-{i}"} for i in range(20)], "text_references": [],
        "root_reachability": [{"root": f"root-{i}", "reachable": False, "path": []} for i in range(20)],
        "unresolved_dynamic": [{"node": f"dyn-{i}"} for i in range(20)], "coverage": {"files_scanned": 72},
    }
    raw = {"status": "success", "ok": True, "executed": True, "changed": False, "detail": detail}
    model = core_agent._model_tool_result(AgentSession("inspect"), "symbol_relations", raw, base_config(), {"symbol": "target"})
    view = model["detail"]
    assert view["counts"] == {"definitions": 20, "incoming": 30, "outgoing": 30, "structural_references": 20, "imports": 20, "text_references": 0, "unresolved_dynamic": 20}
    assert len(view["definitions"]) == 8
    assert len(view["incoming"]) == 12
    assert len(view["outgoing"]) == 12
    assert view["semantics"] == "structural_facts_only"
