from __future__ import annotations

from pathlib import Path

from eyle.core.memory import (
    apply_memory_sidecar,
    memory_activate_result,
    memory_continue_result,
    materialize_explicit_memory_view,
)
from eyle.core.session import AgentSession
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.runtime.ecc_runtime import dispatch, project_result
from eyle.runtime.observation import material_items
from llm.structured import parse_profile_response, schema_for_profile
from tests.canonical import base_config, standard_registry


def _context(root: Path) -> dict:
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {
            "storage_dir": str(root.parent / (root.name + "_memory")),
            "world_scope_id": f"workspace:{root.resolve()}",
        },
    }


def _runtime_ctx(session: AgentSession, config: dict, provider_context: dict, registry):
    return {
        "config": config,
        "provider_context": provider_context,
        "session": session,
        "grounding": material_items(session.observation_ledger),
        "observation_ledger": session.observation_ledger,
        "reality_epoch": int(session.reality_epoch),
        "registry": registry,
    }


def test_rev283_structured_memory_and_exploration_have_no_semantic_item_ceiling():
    schema = schema_for_profile("ecc")
    memory_delta = schema["properties"]["memory_delta"]
    explore = schema["properties"]["decision"]["oneOf"][0]["properties"]["operations"]
    assert "maxItems" not in memory_delta
    assert "maxItems" not in explore

    memories = [
        {
            "op": "remember",
            "arguments": {
                "scope": "world",
                "retention": "temporary",
                "kind": "atomic_fact",
                "content": f"atomic fact {i}",
                "supports": [{"kind": "request"}],
            },
        }
        for i in range(512)
    ]
    operations = [{"operation": "list_tree", "arguments": {"source": "workspace"}} for _ in range(24)]
    parsed = parse_profile_response(
        {"type": "explorar", "operations": operations, "memory_delta": memories},
        "ecc",
    )
    assert len(parsed["operations"]) == 24
    assert len(parsed["memory_delta"]) == 512


def test_rev283_memory_page_size_is_not_a_reading_limit(tmp_path):
    registry = standard_registry()
    context = _context(tmp_path)
    seed = AgentSession("seed")
    delta = [
        {
            "op": "remember",
            "scope": "world",
            "retention": "persistent",
            "kind": "fact",
            "content": ("knowledge-%03d " % i) + ("x" * 1800 if i == 0 else "atomic"),
            "tags": ["bulk"],
            "supports": [{"kind": "request"}],
        }
        for i in range(120)
    ]
    assert apply_memory_sidecar(seed, delta, registry=registry, provider_context=context)["ok"] is True

    session = AgentSession("recall bulk")
    first = memory_activate_result(
        session,
        arguments={"tags": ["bulk"], "retention": "persistent", "limit": 75},
        registry=registry,
        config=base_config(),
        provider_context=context,
    )
    assert first["ok"] is True
    assert len(session.memory_view["node_ids"]) == 75
    assert session.memory_view["frontiers"]
    frontier = session.memory_view["frontiers"][0]
    second = memory_continue_result(
        session, frontier_id=frontier, registry=registry, config=base_config(), provider_context=context,
    )
    assert second["ok"] is True
    assert len(session.memory_view["node_ids"]) == 120
    assert session.memory_view["frontiers"] == []
    view = materialize_explicit_memory_view(session, registry=registry, config=base_config(), provider_context=context)
    assert len(view["nodes"]) == 120
    # Full content is preserved once the node is materialized; page ordering
    # is intentionally not a semantic ranking guarantee.
    assert any(len(item["content"]) > 1400 for item in view["nodes"])


def test_rev283_tree_page_frontier_preserves_complete_universe(tmp_path):
    for i in range(7):
        (tmp_path / f"f{i}.txt").write_text(str(i), encoding="utf-8")
    config = base_config()
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("tree")
    first = dispatch(
        session,
        action_kind="explorar",
        operation="list_tree",
        arguments={"source": "workspace", "depth": 2, "limit": 2},
        config=config,
        provider_context=context,
        registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION,
        validate_pending=validate_pending_continuation,
    ).result
    assert first["ok"] is True
    assert len(first["detail"]["entries"]) == 2
    assert first["detail"]["complete_scan"] is True
    assert first["frontiers"]

    seen = list(first["detail"]["entries"])
    frontier = first["frontiers"][0]["id"]
    while frontier:
        page = dispatch(
            session,
            action_kind="explorar",
            operation="continue",
            arguments={"frontier": frontier},
            config=config,
            provider_context=context,
            registry=registry,
            pending_schema_version=PENDING_SCHEMA_VERSION,
            validate_pending=validate_pending_continuation,
        ).result
        assert page["ok"] is True
        seen.extend(page["detail"]["materialized"]["entries"])
        frontier = page.get("frontiers", [{}])[0].get("id") if page.get("frontiers") else None
    names = {item.get("path") for item in seen if isinstance(item, dict)}
    assert {f"f{i}.txt" for i in range(7)} <= names


def test_rev283_confirmed_write_materializes_final_artifact_for_memory_provenance(tmp_path):
    registry = standard_registry()
    config = base_config(tests_enabled=False)
    context = _context(tmp_path)
    session = AgentSession("write artifact")
    runtime_ctx = _runtime_ctx(session, config, context, registry)

    prepared = registry.prepare_confirmation(
        "standard.workspace_transaction",
        {"patches": [{"operation": "create", "path": "essay.txt", "content": "Alpha thesis.\nBeta evidence.\n"}]},
        runtime_ctx,
    )
    assert prepared["ok"] is True
    result = registry.confirm("standard.workspace_transaction", prepared["state"], runtime_ctx)
    assert result["ok"] is True
    assert result.get("observations")

    model = project_result(session, "standard.workspace_transaction", result, registry, config)
    assert model["grounding_ids"]
    material_id = model["grounding_ids"][0]
    material = material_items(session.observation_ledger)[material_id]
    session.reality_epoch = 1  # project_result registered a persistent post-write state at the next epoch
    assert material["locator"]["path"] == "essay.txt"
    assert material["content"] == "Alpha thesis.\nBeta evidence.\n"
    artifact = model["detail"]["verified_artifacts"][0]
    assert artifact["material_id"] == material_id
    assert artifact["content"] == "Alpha thesis.\nBeta evidence.\n"
    assert "original_content" not in str(model["detail"])

    learned = apply_memory_sidecar(
        session,
        [
            {
                "op": "remember",
                "scope": "world",
                "retention": "persistent",
                "kind": "thesis",
                "content": "The essay's thesis is Alpha.",
                "supports": [{"kind": "material", "material_id": material_id}],
            },
            {
                "op": "remember",
                "scope": "world",
                "retention": "persistent",
                "kind": "evidence",
                "content": "The essay uses Beta as evidence.",
                "supports": [{"kind": "material", "material_id": material_id}],
            },
        ],
        registry=registry,
        provider_context=context,
    )
    assert learned["ok"] is True
    assert len(learned["affected"]) == 2


def test_rev283_symbol_relations_page_has_no_second_hidden_projection_limit(tmp_path):
    calls = [f"def caller_{i}():\n    return target()\n" for i in range(7)]
    (tmp_path / "many.py").write_text(
        "def target():\n    return 1\n\n" + "\n".join(calls),
        encoding="utf-8",
    )
    config = base_config()
    registry = standard_registry()
    context = _context(tmp_path)
    session = AgentSession("relations")

    first = dispatch(
        session,
        action_kind="explorar",
        operation="symbol_relations",
        arguments={"source": "workspace", "symbol": "target", "direction": "incoming", "page_size": 2},
        config=config,
        provider_context=context,
        registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION,
        validate_pending=validate_pending_continuation,
    ).result
    assert first["ok"] is True
    # page_size is a materialization size.  Every row in that page reaches Main; the rest
    # is represented by one or more exact Frontiers rather than silently sliced.
    assert len(first["detail"]["incoming"]) == 2
    assert len(first["frontiers"]) >= 1
    incoming_frontier = next(item["id"] for item in first["frontiers"] if item.get("at") == "symbol_relations.incoming")

    second = dispatch(
        session,
        action_kind="explorar",
        operation="continue",
        arguments={"frontier": incoming_frontier},
        config=config,
        provider_context=context,
        registry=registry,
        pending_schema_version=PENDING_SCHEMA_VERSION,
        validate_pending=validate_pending_continuation,
    ).result
    assert second["ok"] is True
    assert len(second["detail"]["materialized"]["items"]) == 2


