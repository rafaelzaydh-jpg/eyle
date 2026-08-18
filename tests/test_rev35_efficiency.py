from __future__ import annotations

import copy
import json

from eyle.core.ecc import catalog
from eyle.core.session import AgentSession
from eyle.runtime.config import validar_config
from eyle.runtime.ecc_runtime import project_result
from llm.executar import PROMPT_ECC
from llm.structured import contract_instruction
from tests.canonical import base_config, standard_registry


def _provider_context(root):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {
            "storage_dir": str(root / "memory"),
            "world_scope_id": f"workspace:{root.resolve()}",
        },
    }


def test_rev35_stable_prompt_and_wire_reminder_are_compact_but_keep_depth_rule():
    lower = PROMPT_ECC.lower()
    assert len(PROMPT_ECC) <= 12000
    assert len(contract_instruction("ecc")) <= 900
    assert "do not confuse an inventory with an analysis" in lower
    assert "inspect representative implementation before concluding" in lower
    assert "prefer a few targeted reads over a huge structural dump" in lower
    assert "frontier is not a limit" in lower
    assert "memory_view is a materialized view" in lower


def test_rev35_ecc_operation_surface_deduplicates_full_provider_schema():
    registry = standard_registry()
    cfg = base_config()
    surface = catalog(registry, cfg, registry.names(), memory_enabled=True)
    encoded = json.dumps(surface, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) <= 7500
    read_file = next(item for item in surface["explorar"] if item["operation"] == "read_file")
    assert read_file["inputs"]["source"] == "workspace|eyle"
    assert read_file["inputs"]["line_start"] == "int? >=1"
    assert "returns" not in read_file
    assert "limits" not in read_file


def test_rev35_inspect_project_keeps_full_material_but_projects_compact_map(tmp_path):
    # Create enough deterministic import relations that exposing the full graph
    # would be obviously more expensive than the orientation view.
    (tmp_path / "main.py").write_text("import m00\nimport m01\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    for index in range(24):
        imports = "".join(f"import m{j:02d}\n" for j in range(index))
        (tmp_path / f"m{index:02d}.py").write_text(imports + f"VALUE = {index}\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for index in range(12):
        (tests_dir / f"test_{index:02d}.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    registry = standard_registry()
    cfg = base_config()
    session = AgentSession("inspect")
    ctx = {
        "config": cfg,
        "provider_context": _provider_context(tmp_path),
        "observation_ledger": session.observation_ledger,
        "reality_epoch": 0,
    }
    capability = "standard.inspect_project"
    result = registry.execute(capability, {"source": "workspace"}, ctx)
    assert result["ok"] is True
    full_detail = json.dumps(result["detail"], ensure_ascii=False, separators=(",", ":"))
    assert "local_import_edges" in full_detail
    assert len(result.get("observations") or []) == 1
    # Material contains the complete objective scan, not the compact projection.
    material_content = str((result["observations"][0] or {}).get("content") or "")
    assert "local_import_edges" in material_content

    model = project_result(session, capability, result, registry, cfg)
    projected = json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    assert model["detail"]["view"] == "compact_structural_map"
    assert model["detail"]["structural_only"] is True
    assert model["detail"]["full_detail_available_as_evidence"] is True
    assert model["detail"]["relation_signals"]["local_import_edge_count"] > 100
    assert "local_import_edges" not in projected
    assert "test_00.py" not in projected
    assert model.get("evidence_ids")
    assert len(projected) < len(full_detail) * 0.35


def test_rev35_global_memory_scope_reaches_ecc_dispatch_contract(tmp_path):
    from eyle.runtime.ecc_runtime import dispatch
    from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
    from eyle.core.memory import apply_memory_sidecar

    registry = standard_registry()
    cfg = base_config()
    context = _provider_context(tmp_path)
    seed = AgentSession("seed")
    learned = apply_memory_sidecar(
        seed,
        [{"op": "remember", "scope": "world", "retention": "persistent", "kind": "fact", "content": "global-dispatch-proof", "support": "request"}],
        registry=registry,
        provider_context=context,
    )
    assert learned["ok"] is True

    session = AgentSession("inspect global")
    overview = dispatch(
        session, action_kind="explorar", operation="memory_overview", arguments={"scope": "global"},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert overview.result["ok"] is True
    activated = dispatch(
        session, action_kind="explorar", operation="memory_activate", arguments={"query": "global-dispatch-proof", "scope": "global"},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert activated.result["ok"] is True


def test_rev35_tree_page_budget_is_physical_not_knowledge_limit(tmp_path):
    from eyle.runtime.ecc_runtime import dispatch
    from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation

    for index in range(140):
        (tmp_path / f"f{index:03d}.txt").write_text("x", encoding="utf-8")
    registry = standard_registry()
    cfg = base_config()
    context = _provider_context(tmp_path)
    session = AgentSession("tree")
    outcome = dispatch(
        session, action_kind="explorar", operation="list_tree",
        arguments={"source": "workspace", "depth": 1, "limit": 200},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert outcome.result["ok"] is True
    # Rev3.7.1 does not fossilize the old 80-entry working-set ceiling.
    # The explicit caller limit is physical; reachability is preserved when it truncates.
    assert len(outcome.result["detail"]["entries"]) == 141
    assert outcome.result["detail"]["truncated"] is False
    assert not outcome.result.get("frontiers")


def test_rev35_frontier_accepts_natural_numeric_alias_and_continues_exact_file_page(tmp_path):
    from eyle.runtime.ecc_runtime import dispatch
    from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation

    (tmp_path / "large.py").write_text("\n".join(f"line_{index}" for index in range(1, 901)), encoding="utf-8")
    registry = standard_registry()
    cfg = base_config()
    context = _provider_context(tmp_path)
    session = AgentSession("read large file")

    first = dispatch(
        session, action_kind="explorar", operation="read_file",
        arguments={"source": "workspace", "path": "large.py"},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert first.result["ok"] is True
    assert first.result["detail"]["line_start"] == 1
    assert first.result["detail"]["line_end"] == 400
    assert first.result["detail"]["truncated"] is True
    assert first.result["frontiers"][0]["id"] == "fr-0001"

    # Models commonly simplify fr-0001 to fr-1. It is the same mechanical
    # coordinate and must not become FRONTIER_NOT_FOUND.
    second = dispatch(
        session, action_kind="explorar", operation="continue",
        arguments={"frontier": "fr-1"},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert second.result["ok"] is True
    materialized = second.result["detail"]["materialized"]
    assert materialized["ranges"][0]["line_start"] == 401
    assert materialized["ranges"][0]["line_end"] == 800
    assert second.result.get("frontiers")
    assert second.result["frontiers"][0]["id"] == "fr-0002"


def test_rev35_explicit_large_file_scope_is_paged_not_dumped_or_lost(tmp_path):
    from eyle.runtime.ecc_runtime import dispatch
    from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation

    (tmp_path / "huge.py").write_text("\n".join(f"value_{index} = {index}" for index in range(1, 2001)), encoding="utf-8")
    registry = standard_registry()
    cfg = base_config()
    context = _provider_context(tmp_path)
    session = AgentSession("explicit huge range")

    first = dispatch(
        session, action_kind="explorar", operation="read_file",
        arguments={"source": "workspace", "path": "huge.py", "line_start": 1, "line_end": 1800},
        config=cfg, provider_context=context, registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
    )
    assert first.result["ok"] is True
    detail = first.result["detail"]
    assert detail["line_start"] == 1
    assert detail["line_end"] == 400
    assert detail["requested_line_end"] == 1800
    assert detail["truncated"] is True
    assert detail["file_continues"] is True
    assert first.result["frontiers"][0]["count"] == 1400

    # The rest is exact navigation state, not discarded context.
    seen = [(1, 400)]
    frontier = first.result["frontiers"][0]["id"]
    while frontier:
        nxt = dispatch(
            session, action_kind="explorar", operation="continue",
            arguments={"frontier": frontier},
            config=cfg, provider_context=context, registry=registry,
            pending_schema_version=PENDING_SCHEMA_VERSION, validate_pending=validate_pending_continuation,
        )
        assert nxt.result["ok"] is True
        ranges = nxt.result["detail"]["materialized"]["ranges"]
        seen.extend((row["line_start"], row["line_end"]) for row in ranges)
        frontiers = nxt.result.get("frontiers") or []
        frontier = frontiers[0]["id"] if frontiers else ""
    assert seen == [(1, 400), (401, 800), (801, 1200), (1201, 1600), (1601, 1800)]


def test_rev35_memory_write_count_has_no_semantic_ceiling(tmp_path):
    from eyle.core.memory import apply_memory_sidecar
    from eyle.runtime.memory_graph import graph_counts

    registry = standard_registry()
    context = _provider_context(tmp_path)
    session = AgentSession("learn many useful facts")
    operations = [
        {
            "op": "remember", "scope": "world", "retention": "persistent",
            "kind": "code_fact", "content": f"project fact {index}",
        }
        for index in range(35)
    ]
    outcome = apply_memory_sidecar(session, operations, registry=registry, provider_context=context)
    assert outcome["ok"] is True
    assert len(outcome["affected"]) == 35
    counts = graph_counts(context["core_memory"]["storage_dir"])
    assert counts["nodes"] == 35
