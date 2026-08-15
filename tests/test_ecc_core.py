from __future__ import annotations

import json
from pathlib import Path

import pytest

import eyle.core.agent as agent
from eyle.capabilities import Provider, build_registry
from eyle.contracts.capability import physical_effect, result
from eyle.core.ecc import catalog, operation_map
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION
from llm.executar import PROMPT_ECC
from llm.structured import StructuredResponseError, parse_profile_response, schema_for_profile
from tests.canonical import base_config, run_agent, standard_registry


def objective(disposition="unchanged", state=None):
    return {"disposition": disposition, "state": state}


def memory(operations=None, focus=None):
    ops = list(operations or [])
    return {"focus": list(focus or []), "disposition": "updated" if ops else "unchanged", "operations": ops}


def explore(operation, arguments=None, memory_sidecar=None):
    return {"type": "explorar", "operation": operation, "arguments": dict(arguments or {}), "objective": objective(), "memory": memory_sidecar or memory()}


def build(operation, arguments=None, memory_sidecar=None):
    return {"type": "construir", "operation": operation, "arguments": dict(arguments or {}), "objective": objective(), "memory": memory_sidecar or memory()}


def conclude(response, memory_sidecar=None):
    return {"type": "concluir", "response": response, "objective": objective(), "memory": memory_sidecar or memory()}


def provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root)},
        "core_memory": {"storage_dir": str(root.parent / (root.name + "_memory")), "world_scope_id": f"workspace:{root.resolve()}"},
    }


def test_ecc_structured_contract_has_exactly_three_cognitive_moves():
    schema = schema_for_profile("ecc")
    kinds = [variant["properties"]["type"]["enum"][0] for variant in schema["oneOf"]]
    assert kinds == ["explorar", "construir", "concluir"]
    text = json.dumps(schema, ensure_ascii=False)
    for dead in ("await_user", "investigation_updates", "task_updates", "memory_updates", "completion_mode"):
        assert dead not in text
    assert parse_profile_response(conclude("ok"), "ecc")["type"] == "concluir"
    with pytest.raises(StructuredResponseError):
        parse_profile_response({"type": "await_user", "question": "x"}, "ecc")


def test_agent_session_is_minimal_ecc_and_clean_break():
    state = AgentSession("x").to_dict()
    assert state["session_schema_version"] == SESSION_SCHEMA_VERSION == "2.7.5-r2.5.2-ecc"
    assert set(state) == {
        "session_schema_version", "request", "execution_id", "turn", "reality_epoch",
        "observation_ledger", "evidence", "memory_focus", "objective_state", "conversation_background", "request_context",
        "runtime_feedback", "pending_operation",
    }
    for dead in ("decision_ledger", "investigation", "tasks", "task_memory", "pending_capability"):
        assert dead not in state
    old = dict(state); old["session_schema_version"] = "2.7.5-r1.5.3"
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(old)
    assert PENDING_SCHEMA_VERSION == "9-ecc"


def test_prompt_draws_cache_evidence_and_authority_boundary():
    lower = PROMPT_ECC.lower()
    assert "three moves" in lower
    assert "you decide meaning" in lower
    assert "memory and conversation background" in lower
    assert "not every message is a task" in lower
    assert "evidence means something was really observed" in lower
    assert "recall" in lower
    assert "what am i still trying to achieve" in lower
    assert "objective says what" in lower
    assert "does not mean it was run" in lower
    for dead in ("write_prepare", "analysis_investigate", "must create a task"):
        assert dead not in lower


def test_direct_conclude_needs_no_phase_or_completion_gate(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: calls.append(json.loads(prompt)) or conclude("4"))
    status, text, pending, details = run_agent(
        agent, "quanto é 2+2?", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True,
    )
    assert (status, text, pending) == ("completed", "4", None)
    assert len(calls) == 1
    assert details["architecture"] == "ECC"
    assert details["physical_capability_calls"] == 0


def test_explore_then_learn_compacts_active_task_knowledge(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 7\n", encoding="utf-8")
    prompts = []
    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        if len(prompts) == 1:
            return explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1})
        mat = payload["latest_observations"][0]["grounding_ids"][0]
        return conclude("VALUE é 7.", memory([{
            "op": "remember", "scope": "world", "kind": "code_fact",
            "content": "app.py define VALUE = 7.", "tags": ["VALUE", "app.py"],
            "supports": [{"kind": "material", "material_id": mat, "selector": {"line_start": 1, "line_end": 1}}],
        }]))
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(agent, "Leia VALUE", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True)
    assert status == "completed" and text == "VALUE é 7."
    assert details["memory_nodes"] == 1 and details["evidence_items"] == 2
    assert details["physical_capability_calls"] == 1


def test_repeated_explore_returns_compact_cache_fact_not_source_body(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("SECRETISH = 'body-once'\n", encoding="utf-8")
    prompts = []
    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        if len(prompts) <= 2:
            return explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1})
        cached = payload["latest_observations"][0]
        assert cached["status"] == "already_observed"
        assert "body-once" not in json.dumps(cached, ensure_ascii=False)
        return conclude("ok")
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, _, _, details = run_agent(agent, "observe", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True)
    assert status == "completed"
    assert details["physical_capability_calls"] == 1
    assert details["operation_replays"] == 1


def test_exact_evidence_recall_is_small_and_addressed(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    prompts = []
    def fake(prompt, cfg):
        payload = json.loads(prompt); prompts.append(payload)
        if len(prompts) == 1:
            return explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 2, "line_end": 2})
        if len(prompts) == 2:
            mat = payload["latest_observations"][0]["grounding_ids"][0]
            return explore("recall", {"evidence_id": "ev-0001"}, memory([{
                "op": "remember", "scope": "world", "kind": "code_fact", "content": "b equals 2",
                "tags": ["b"], "supports": [{"kind": "material", "material_id": mat}],
            }]))
        recalled = payload["latest_observations"][0]
        assert recalled["evidence_id"] == "ev-0001"
        detail = recalled["detail"]
        assert "b = 2" in json.dumps(detail, ensure_ascii=False)
        assert "a = 1" not in json.dumps(detail, ensure_ascii=False)
        return conclude("b = 2")
    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(agent, "confira b", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True)
    assert (status, text) == ("completed", "b = 2")
    assert details["physical_capability_calls"] == 1
    assert details["memory_nodes"] == 1


def test_adjacent_ranges_compose_coverage_without_third_physical_read(monkeypatch, tmp_path):
    lines = "".join(f"line_{i} = {i}\n" for i in range(1, 801))
    (tmp_path / "big.py").write_text(lines, encoding="utf-8")
    decisions = iter([
        explore("read_file", {"source": "workspace", "path": "big.py", "line_start": 1, "line_end": 400}),
        explore("read_file", {"source": "workspace", "path": "big.py", "line_start": 401, "line_end": 800}),
        explore("read_file", {"source": "workspace", "path": "big.py", "line_start": 350, "line_end": 700}),
        conclude("covered"),
    ])
    prompts=[]
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(decisions))
    status, _, _, details = run_agent(agent, "coverage", base_config(), provider_context=provider_context(tmp_path), retornar_detalhes=True)
    assert status == "completed"
    assert details["physical_capability_calls"] == 2
    assert details["operation_replays"] == 1
    assert prompts[-1]["latest_observations"][0]["status"] == "already_observed"


def test_build_is_runtime_confirmed_then_returns_result_to_main(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    outputs = iter([
        explore("read_file", {"source": "workspace", "path": "app.py", "line_start": 1, "line_end": 1}),
        build("transaction", {"patches": [{"operation": "update", "path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 2\n"}]}),
        conclude("alterado"),
    ])
    monkeypatch.setattr(agent, "executar_ecc_llm", lambda prompt, cfg: next(outputs))
    cfg=base_config()
    status, _, pending, details = run_agent(agent, "mude x", cfg, provider_context=provider_context(tmp_path), retornar_detalhes=True)
    assert status == "confirmation_required"
    assert pending["continuation_kind"] == "capability_confirmation"
    assert (tmp_path / "app.py").read_text() == "x = 1\n"
    status2, text2, pending2, details2 = run_agent(
        agent, "mude x", cfg, provider_context=provider_context(tmp_path), retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert (status2, text2, pending2) == ("completed", "alterado", None)
    assert (tmp_path / "app.py").read_text().strip() == "x = 2"
    assert details2["reality_epoch"] == 1


def test_no_progress_is_mechanical_feedback_not_semantic_routing(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    prompts=[]
    def fake(prompt,cfg):
        payload=json.loads(prompt); prompts.append(payload)
        if len(prompts) <= 3:
            return explore("read_file", {"source":"workspace","path":"a.py","line_start":1,"line_end":1})
        assert any(item.get("code") == "NO_PROGRESS" for item in payload["runtime_feedback"])
        return conclude("done")
    monkeypatch.setattr(agent,"executar_ecc_llm",fake)
    status,_,_,details=run_agent(agent,"loop",base_config(),provider_context=provider_context(tmp_path),retornar_detalhes=True)
    assert status=="completed" and details["physical_capability_calls"]==1 and details["operation_replays"]==2


def test_unknown_provider_is_projected_into_ecc_without_core_router():
    def sense(arguments, ctx):
        return result("success", True, True, detail={"value": 63}, observations=[{
            "source_type":"petbot.sense","source_capability":"petbot.sense",
            "locator":{"kind":"sensor","name":"food"},"content":"63","content_hash":"a"*64,
        }])
    def move(arguments, ctx):
        return result("success", True, True, changed=True, physical_effect=physical_effect("petbot.feeder","dispense","persistent",changed=True))
    empty={"type":"object","properties":{},"required":[],"additionalProperties":False}
    provider=Provider("petbot",{
        "sense":{"description":"Sense food.","input_schema":empty,"returns":"level","effect":"observe","confirmation":"none","fn":sense},
        "move":{"description":"Move feeder.","input_schema":empty,"returns":"effect","effect":"mutate","confirmation":"none","fn":move},
    })
    registry=build_registry([provider])
    available=registry.available_names({"config":{"providers":{"petbot":{}}},"provider_context":{"petbot":{}}})
    explore_mapping=operation_map(registry,available,"explorar")
    build_mapping=operation_map(registry,available,"construir")
    assert explore_mapping["petbot.sense"] == "petbot.sense"
    assert build_mapping["petbot.move"] == "petbot.move"
    surface=catalog(registry,{"providers":{"petbot":{}}},available)
    assert {x["operation"] for x in surface["explorar"]} >= {"petbot.sense", "recall"}
    assert {x["operation"] for x in surface["construir"]} == {"petbot.move"}


def test_conversation_background_does_not_become_evidence(monkeypatch, tmp_path):
    prompts=[]
    monkeypatch.setattr(agent,"executar_ecc_llm",lambda prompt,cfg: prompts.append(json.loads(prompt)) or conclude("ok"))
    context={"recent_messages":[{"role":"assistant","content":"app.py used to say x=9"}]}
    status,_,_,details=run_agent(agent,"qual o estado atual?",base_config(),provider_context=provider_context(tmp_path),conversation_context=context,retornar_detalhes=True)
    assert status=="completed"
    assert prompts[0]["conversation_background"]
    assert prompts[0]["memory_graph"]["nodes"] == []
    assert details.get("grounding_count_total",0) == 0


def test_benchmark_can_suppress_background_projection_without_erasing_session_history(monkeypatch, tmp_path):
    prompts=[]
    monkeypatch.setenv("EYLE_BENCHMARK_SUPPRESS_CONVERSATION_BACKGROUND", "1")
    monkeypatch.setattr(agent,"executar_ecc_llm",lambda prompt,cfg: prompts.append(json.loads(prompt)) or conclude("ok"))
    context={"recent_messages":[
        {"role":"user","content":"código anterior CAPIVARA-83917"},
        {"role":"assistant","content":"anotado"},
    ]}
    status,_,_,details=run_agent(
        agent,"qual é o código?",base_config(),provider_context=provider_context(tmp_path),
        conversation_context=context,retornar_detalhes=True,
    )
    assert status=="completed"
    assert prompts[0]["conversation_background"] == []
    assert details.get("grounding_count_total",0) == 0
    calls = details.get("llm_calls") or []
    assert calls
    prompt_meta = (calls[0].get("prompt") or {}) if isinstance(calls[0], dict) else {}
    assert prompt_meta.get("conversation_background_suppressed_for_benchmark") is True
    assert prompt_meta.get("conversation_background_stored_items") == 2
    assert prompt_meta.get("conversation_background_projected_items") == 0


def test_standard_source_contract_distinguishes_user_workspace_from_eyle_self(tmp_path):
    registry = standard_registry()
    cfg = base_config()
    available = registry.available_names({
        "config": cfg,
        "provider_context": {
            "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
            "core_memory": {"storage_dir": str(tmp_path / ".memory"), "world_scope_id": f"workspace:{tmp_path.resolve()}"},
        },
    })
    surface = catalog(registry, cfg, available)
    find = next(item for item in surface["explorar"] if item["operation"] == "find_symbol")
    assert "workspace|eyle" in find["inputs"]["source"].lower()
    guidance = " ".join(surface["guidance"]).lower()
    assert "user's files" in guidance
    assert "eyle's own source code" in guidance
    assert "asks about eyle itself" in guidance


def test_negative_symbol_lookup_is_valid_observation_and_second_request_replays(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) <= 2:
            return explore("find_symbol", {"source": "workspace", "symbol": "AgentSession"})
        cached = payload["latest_observations"][0]
        assert cached["status"] == "already_observed"
        return conclude("não está no workspace")

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, _, details = run_agent(
        agent,
        "onde AgentSession está no workspace?",
        base_config(),
        provider_context=provider_context(tmp_path),
        retornar_detalhes=True,
    )
    assert (status, text) == ("completed", "não está no workspace")
    assert details["physical_capability_calls"] == 1
    assert details["operation_replays"] == 1
    first = prompts[1]["latest_observations"][0]
    assert first["error_code"] == "SYMBOL_NOT_FOUND"
    assert first["detail"]["source"] == "workspace"
    assert first["detail"]["source_scope"] == "dedicated_user_workspace"
