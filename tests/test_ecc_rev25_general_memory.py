from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from eyle.core.ecc import catalog, operation_map
from eyle.core.session import AgentSession
from eyle.runtime.ecc_runtime import dispatch, exploration_map
from eyle.runtime.observation import material_items
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, standard_registry


def provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {"storage_dir": str(root.parent / f"{root.name}_memory"), "world_scope_id": f"workspace:{root.resolve()}"},
    }


def empty_objective():
    return {"disposition": "unchanged", "state": None}


def empty_memory():
    return {"focus": [], "disposition": "unchanged", "operations": []}


def test_rev25_memory_sidecar_is_transversal_not_fourth_ecc_action():
    schema = schema_for_profile("ecc")
    assert len(schema["oneOf"]) == 3
    for variant in schema["oneOf"]:
        assert "memory" in variant["required"]
        assert "learned" not in variant["properties"]
    with pytest.raises(StructuredResponseError, match="memory"):
        parse_profile_response({"type": "concluir", "response": "ok", "objective": empty_objective()}, "ecc")
    parsed = parse_profile_response({"type": "concluir", "response": "ok", "objective": empty_objective(), "memory": empty_memory()}, "ecc")
    assert parsed["memory"] == empty_memory()
    with pytest.raises(StructuredResponseError):
        parse_profile_response({"type": "concluir", "response": "ok", "objective": empty_objective(), "memory": empty_memory(), "learned": []}, "ecc")


def test_rev25_public_capabilities_do_not_expose_memory_as_tool_or_fourth_family(tmp_path):
    registry = standard_registry()
    config = base_config()
    pc = provider_context(tmp_path)
    available = registry.available_names({"config": config, "provider_context": pc}, terminal=set())
    assert not any(name.startswith("memory.") for name in registry.names())
    assert {"explorar", "construir"}.issubset(catalog(registry, config, available))
    assert "memory" not in catalog(registry, config, available)
    assert "memory" not in operation_map(registry, available, "explorar")
    assert "memory" not in operation_map(registry, available, "construir")


def test_rev25_continue_observation_remains_internal_but_is_not_ecc_operation(tmp_path):
    registry = standard_registry()
    config = base_config()
    pc = provider_context(tmp_path)
    available = registry.available_names({"config": config, "provider_context": pc}, terminal=set())
    assert "standard.continue_observation" in registry.names()
    assert "continue" not in operation_map(registry, available, "explorar")
    assert "continue" not in [item["operation"] for item in catalog(registry, config, available)["explorar"]]


def test_rev25_search_projects_navigation_index_not_broad_source_pages():
    root = Path(__file__).resolve().parents[1]
    registry = standard_registry()
    config = base_config()
    session = AgentSession("find AgentSession")
    out = dispatch(
        session,
        action_kind="explorar",
        operation="search",
        arguments={"source": "eyle", "query": "AgentSession"},
        config=config,
        provider_context=provider_context(root),
        registry=registry,
        pending_schema_version="9-ecc",
        validate_pending=lambda value, persisted=False: value,
    )
    assert out.result["ok"] is True
    assert "frontiers" not in out.result
    detail = out.result["detail"]
    assert detail["results"]
    assert len(json.dumps(out.result, ensure_ascii=False)) < 8000
    for row in detail["results"]:
        assert "content" not in row
        assert "numbered_content" not in row
        assert len(row.get("preview", "")) <= 280
        assert row.get("file")
        assert row.get("grounding_id", "").startswith("mat-")
    materials = material_items(session.observation_ledger)
    assert len(materials) >= len(detail["results"])
    assert any(len(str(item.get("numbered_content") or item.get("content") or "")) > 280 for item in materials.values())


def test_rev25_exploration_map_is_bounded_navigation_metadata():
    root = Path(__file__).resolve().parents[1]
    registry = standard_registry()
    config = base_config()
    session = AgentSession("find ECC")
    dispatch(
        session,
        action_kind="explorar",
        operation="search",
        arguments={"source": "eyle", "query": "ECC"},
        config=config,
        provider_context=provider_context(root),
        registry=registry,
        pending_schema_version="9-ecc",
        validate_pending=lambda value, persisted=False: value,
    )
    nav = exploration_map(session, registry)
    assert len(nav) == 1
    encoded = json.dumps(nav, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) < 1200
    assert "resolved" not in encoded
    assert "frontier" not in encoded
    assert nav[0]["operation"] == "search"
    assert nav[0]["coverage"]["scope"]["source"] == "eyle"


def test_rev25_prompt_places_stable_prefix_before_dynamic_memory_and_hot_state():
    root = Path(__file__).resolve().parents[1]
    registry = standard_registry()
    session = AgentSession("inspect current Eyle")
    prompt, _ = agent._compile_prompt(session, base_config(), provider_context(root), None, registry)
    payload = json.loads(prompt)
    keys = list(payload)
    assert keys[:5] == ["ecc_operations", "runtime_environment", "current_request", "objective_state", "conversation_background"]
    assert "memory_graph" in keys
    assert keys.index("turn") > keys.index("latest_observations")


def test_rev25_prompt_teaches_memory_first_graph_freshness_and_no_ceremonial_updates():
    from llm.executar import PROMPT_ECC

    lower = PROMPT_ECC.lower()
    assert "memory is what is worth knowing again later" in lower
    assert "not a fourth move" in lower
    assert "runtime marks the affected anchor stale/degraded" in lower
    assert "runtime does not understand meaning" in lower
    assert "anything that may be useful again in the future can become memory" in lower
    assert "use memory when it already answers" in lower
    assert "capabilities are eyle's replaceable body" in lower
    assert "not whole raw outputs" in lower


def _run_agent(request, fake, root: Path, storage: Path, monkeypatch):
    from tests.canonical import run_agent
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    pc = {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {"storage_dir": str(storage), "world_scope_id": f"workspace:{root.resolve()}"},
    }
    return run_agent(agent, request, base_config(), provider_context=pc, retornar_detalhes=True)


def test_rev25_project_memory_survives_jobs_and_file_change_only_degrades_anchored_region(monkeypatch, tmp_path):
    project = tmp_path / "project"; project.mkdir()
    storage = tmp_path / "agent-memory"
    source = project / "core.py"; source.write_text("CORE_VALUE = 1\n", encoding="utf-8")
    first_prompts = []

    def first(prompt, _cfg):
        payload = json.loads(prompt); first_prompts.append(payload)
        if len(first_prompts) == 1:
            return {"type": "explorar", "operation": "read_file", "arguments": {"source": "workspace", "path": "core.py", "line_start": 1, "line_end": 1}, "memory": empty_memory()}
        mat = payload["latest_observations"][0]["grounding_ids"][0]
        return {"type": "concluir", "response": "CORE_VALUE is 1", "memory": {"focus": [], "disposition": "updated", "operations": [{
            "op": "remember", "key": "corevalue", "scope": "world", "kind": "code_fact",
            "content": "core.py defines CORE_VALUE = 1", "tags": ["core", "CORE_VALUE"],
            "supports": [{"kind": "material", "material_id": mat, "selector": {"line_start": 1, "line_end": 1}}],
        }]}}

    status, _, _, details = _run_agent("What is CORE_VALUE in the project?", first, project, storage, monkeypatch)
    assert status == "completed" and details["memory_nodes"] == 1 and details["evidence_items"] == 2

    second_prompts = []
    def second(prompt, _cfg):
        payload = json.loads(prompt); second_prompts.append(payload)
        node = next(n for n in payload["memory_graph"]["nodes"] if "CORE_VALUE = 1" in n["content"])
        assert node["freshness"] == "fresh"
        return {"type": "concluir", "response": "from memory", "memory": empty_memory()}

    status2, text2, _, details2 = _run_agent("What is CORE_VALUE in the project?", second, project, storage, monkeypatch)
    assert (status2, text2) == ("completed", "from memory")
    assert details2["physical_capability_calls"] == 0

    source.write_text("CORE_VALUE = 2\n", encoding="utf-8")
    third_prompts = []
    def third(prompt, _cfg):
        payload = json.loads(prompt); third_prompts.append(payload)
        if len(third_prompts) == 1:
            node = next(n for n in payload["memory_graph"]["nodes"] if "CORE_VALUE" in n["content"])
            assert node["freshness"] in {"stale", "degraded"}
            return {"type": "explorar", "operation": "read_file", "arguments": {"source": "workspace", "path": "core.py", "line_start": 1, "line_end": 1}, "memory": {"focus": [node["id"]], "disposition": "unchanged", "operations": []}}
        node = next(n for n in payload["memory_graph"]["nodes"] if "CORE_VALUE" in n["content"])
        mat = payload["latest_observations"][0]["grounding_ids"][0]
        return {"type": "concluir", "response": "CORE_VALUE is now 2", "memory": {"focus": [node["id"]], "disposition": "updated", "operations": [{
            "op": "revise", "id": node["id"], "expected_revision": node["revision"],
            "content": "core.py defines CORE_VALUE = 2", "kind": "code_fact", "add_tags": ["core", "CORE_VALUE"],
            "supports": [{"kind": "material", "material_id": mat, "selector": {"line_start": 1, "line_end": 1}}],
        }]}}

    status3, _, _, details3 = _run_agent("What is CORE_VALUE in the project?", third, project, storage, monkeypatch)
    assert status3 == "completed" and details3["physical_capability_calls"] == 1

    fourth_prompts = []
    def fourth(prompt, _cfg):
        payload = json.loads(prompt); fourth_prompts.append(payload)
        node = next(n for n in payload["memory_graph"]["nodes"] if "CORE_VALUE = 2" in n["content"])
        assert node["freshness"] == "fresh"
        return {"type": "concluir", "response": "2", "memory": empty_memory()}
    status4, text4, _, details4 = _run_agent("What is CORE_VALUE in the project?", fourth, project, storage, monkeypatch)
    assert (status4, text4) == ("completed", "2") and details4["physical_capability_calls"] == 0


def test_rev25_user_memory_is_semantic_cross_project_and_llm_manages_revision(monkeypatch, tmp_path):
    storage = tmp_path / "agent-memory"
    project_a = tmp_path / "a"; project_b = tmp_path / "b"; project_a.mkdir(); project_b.mkdir()

    def remember_dogs(prompt, _cfg):
        payload = json.loads(prompt)
        assert payload["memory_graph"]["nodes"] == []
        return {"type": "concluir", "response": "noted", "memory": {"focus": [], "disposition": "updated", "operations": [{
            "op": "remember", "key": "pets", "scope": "user", "kind": "preference",
            "content": "User likes dogs", "tags": ["pets", "preferences", "dogs"],
            "supports": [{"kind": "request"}],
        }]}}
    assert _run_agent("My pet preference: I like dogs.", remember_dogs, project_a, storage, monkeypatch)[0] == "completed"

    observed = {}
    def retrieve_elsewhere(prompt, _cfg):
        payload = json.loads(prompt)
        node = next(n for n in payload["memory_graph"]["nodes"] if n["content"] == "User likes dogs")
        assert node["scope"] == "user" and node["freshness"] == "semantic"
        observed["id"] = node["id"]
        return {"type": "concluir", "response": "dogs", "memory": empty_memory()}
    status, text, _, details = _run_agent("What are my pet preferences?", retrieve_elsewhere, project_b, storage, monkeypatch)
    assert (status, text) == ("completed", "dogs") and details["physical_capability_calls"] == 0

    def revise_preferences(prompt, _cfg):
        payload = json.loads(prompt)
        node = next(n for n in payload["memory_graph"]["nodes"] if n["id"] == observed["id"])
        return {"type": "concluir", "response": "updated", "memory": {"focus": [node["id"]], "disposition": "updated", "operations": [{
            "op": "revise", "id": node["id"], "expected_revision": node["revision"], "kind": "preference",
            "content": "User likes dogs, cats and roasted fish", "add_tags": ["cats", "roasted-fish"],
            "supports": [{"kind": "request"}],
        }]}}
    assert _run_agent("Update my pet preferences: I also like cats and roasted fish.", revise_preferences, project_b, storage, monkeypatch)[0] == "completed"

    def retrieve_updated(prompt, _cfg):
        payload = json.loads(prompt)
        node = next(n for n in payload["memory_graph"]["nodes"] if "cats" in n["content"])
        assert node["freshness"] == "semantic" and node["revision"] == 2
        return {"type": "concluir", "response": node["content"], "memory": empty_memory()}
    status2, text2, _, _ = _run_agent("What are my pet preferences now?", retrieve_updated, project_a, storage, monkeypatch)
    assert status2 == "completed" and "roasted fish" in text2
