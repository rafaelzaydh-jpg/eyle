from __future__ import annotations
from tests.canonical import standard_registry

import json

import eyle.providers.standard as tools
from tests.canonical import base_config


def observation_signature(name, arguments):
    return standard_registry().observation_signature(f"standard.{name}" if "." not in name else name, arguments)


def _ctx(root):
    return {"provider_context": {"standard": {"caminho_origem": str(root)}}, "config": base_config(), "observation_ledger": {"handles": {}}}


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
    result = standard_registry().execute("standard.symbol_relations", {"symbol": "x", "direction": "callers"}, _ctx(tmp_path))
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["executed"] is False
    assert "incoming" in str(result["detail"])


def test_tool_validator_rejects_invalid_array_items_before_execution(tmp_path):
    result = standard_registry().execute("standard.symbol_relations", {"symbol": "x", "roots": [123]}, _ctx(tmp_path))
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["executed"] is False


def test_capability_index_exposes_small_enums_without_full_catalog():
    index = standard_registry().catalog(allowed_names={"standard.symbol_relations"})
    assert len(index) == 1
    assert index[0]["name"] == "standard.symbol_relations"
    assert "incoming|outgoing|both" in index[0]["inputs"]["direction"]








def test_symbol_relations_model_view_exposes_entire_materialized_page():
    import eyle.core.agent as core_agent
    from eyle.runtime.ecc_runtime import project_result
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
    model = project_result(AgentSession("inspect"), "standard.symbol_relations", raw, standard_registry(), base_config())
    view = model["detail"]
    assert view["counts"] == {"definitions": 20, "incoming": 30, "outgoing": 30, "structural_references": 20, "imports": 20, "text_references": 0, "root_reachability": 20, "unresolved_dynamic": 20}
    assert len(view["definitions"]) == 20
    assert len(view["incoming"]) == 30
    assert len(view["outgoing"]) == 30
    assert len(view["root_reachability"]) == 20
    assert view["semantics"] == "structural_facts_only"
