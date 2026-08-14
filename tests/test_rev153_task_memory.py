from __future__ import annotations

import json

import eyle.core.agent as core_agent
from eyle.core.task_memory import apply_task_memory_updates, empty_task_memory, project_task_knowledge
from eyle.runtime.observation import register_material_candidates
from eyle.core.session import AgentSession
from tests.canonical import agent_complete, agent_tools, base_config, run_agent, standard_registry, tool_call


def _numbered_file(lines: int = 100) -> str:
    return "".join(f"line {index:03d} = {'x' * 40}\n" for index in range(1, lines + 1))


def test_task_memory_selects_exact_provider_owned_evidence_span_and_compacts_knowledge():
    session = AgentSession("inspect")
    text = _numbered_file(100)
    material_id = register_material_candidates(session.observation_ledger, [{
        "locator": {"kind": "file", "source": "workspace", "path": "agent.py", "line_start": 1, "line_end": 100, "total_lines": 100},
        "content": text,
        "content_hash": "parent-range-hash",
        "source_version": "file-version-A",
        "source_type": "standard.read_file",
        "source_capability": "standard.read_file",
        "source_provider": "standard",
    }])[0]
    registry = standard_registry()
    updates = {
        "evidence": [{"id": "ev-core", "material_id": material_id, "selector": {"line_start": 28, "line_end": 37}}],
        "findings": [{"id": "f-core", "statement": "The relevant logic is concentrated in the selected span.", "evidence_ids": ["ev-core"]}],
        "conclusions": [{"id": "c-core", "statement": "The task can retain the learned fact without retaining the whole source body in prompt memory.", "evidence_ids": [], "finding_ids": ["f-core"]}],
    }
    state, accepted, rejected = apply_task_memory_updates(
        updates,
        previous=empty_task_memory(),
        materials=session.observation_ledger["materials"],
        select_evidence=lambda material, selector: registry.select_evidence(material, selector),
    )
    assert not rejected
    assert {item["kind"] for item in accepted} == {"evidence", "finding", "conclusion"}
    evidence = state["evidence"]["ev-core"]
    assert evidence["material_id"] == material_id
    assert evidence["locator"]["line_start"] == 28
    assert evidence["locator"]["line_end"] == 37
    projected = project_task_knowledge(state)
    assert projected["findings"][0]["statement"].startswith("The relevant logic")
    assert "content" not in projected["evidence"][0]


def test_coverage_replay_rematerializes_exact_requested_range_instead_of_small_excerpt(monkeypatch, tmp_path):
    (tmp_path / "large.py").write_text(_numbered_file(320), encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return agent_tools(tool_call("read_file", {"path": "large.py", "line_start": 1, "line_end": 300}))
        if len(prompts) == 2:
            detail = payload["latest_capability_results"][0]["detail"]
            assert detail["presentation"]["complete"] is False
            assert detail["presentation"]["line_end"] < 300
            return agent_tools(tool_call("read_file", {"path": "large.py", "line_start": 250, "line_end": 260}))
        replay = payload["latest_capability_results"][0]
        assert replay["replayed"] is True
        assert replay["coverage_replayed"] is True
        assert replay["rematerialized"] is True
        assert replay["executed"] is False
        detail = replay["detail"]
        assert detail["presentation"]["complete"] is True
        assert detail["line_start"] == 250 and detail["line_end"] == 260
        assert "250 |" in detail["numbered_content"] and "260 |" in detail["numbered_content"]
        return agent_complete({"answer": "Verified the requested range.", "grounding_ids": ["mat-0001"]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = run_agent(
        core_agent,
        "Inspect the relevant section of large.py",
        base_config(),
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        retornar_detalhes=True,
    )
    assert status == "success" and text == "Verified the requested range."
    assert details["capability_calls"] == 1
    assert details["observation_replays"] == 1


def test_main_can_metabolize_raw_material_into_task_knowledge_for_later_turn(monkeypatch, tmp_path):
    (tmp_path / "agent.py").write_text(_numbered_file(80), encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return agent_tools(tool_call("read_file", {"path": "agent.py", "line_start": 1, "line_end": 80}))
        if len(prompts) == 2:
            return {
                "action": {"kind": "capability_calls", "calls": [tool_call("calculate", {"expression": "1+1"})]},
                "memory_updates": {
                    "evidence": [{"id": "ev-memory", "material_id": "mat-0001", "selector": {"line_start": 28, "line_end": 37}}],
                    "findings": [{"id": "f-memory", "statement": "Lines 28-37 contain the task-relevant mechanism.", "evidence_ids": ["ev-memory"]}],
                    "conclusions": [{"id": "c-memory", "statement": "The mechanism can be reasoned about from compact retained knowledge.", "evidence_ids": [], "finding_ids": ["f-memory"]}],
                },
            }
        knowledge = payload["task_knowledge"]
        assert knowledge["findings"][0]["id"] == "f-memory"
        assert knowledge["conclusions"][0]["id"] == "c-memory"
        evidence = knowledge["evidence"][0]
        assert evidence["locator"]["line_start"] == 28 and evidence["locator"]["line_end"] == 37
        assert "content" not in evidence and "numbered_content" not in evidence
        return agent_complete({"answer": "Task knowledge retained.", "grounding_ids": ["mat-0001"]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = run_agent(
        core_agent,
        "Inspect agent.py and retain only the useful knowledge",
        base_config(),
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        retornar_detalhes=True,
    )
    assert status == "success" and text == "Task knowledge retained."
    assert details["task_memory"]["findings"][0]["id"] == "f-memory"


def test_task_memory_survives_await_user_roundtrip_and_source_can_be_rematerialized(monkeypatch, tmp_path):
    from tests.canonical import agent_await_user

    (tmp_path / "agent.py").write_text(_numbered_file(120), encoding="utf-8")
    cfg = base_config()
    prompts = []
    first_outputs = iter([
        agent_tools(tool_call("read_file", {"path": "agent.py", "line_start": 1, "line_end": 120})),
        {
            **agent_await_user("Continuar a verificação?", reason="The user controls whether the active audit continues."),
            "memory_updates": {
                "evidence": [{"id": "ev-resume", "material_id": "mat-0001", "selector": {"line_start": 28, "line_end": 37}}],
                "findings": [{"id": "f-resume", "statement": "The selected mechanism is in lines 28-37.", "evidence_ids": ["ev-resume"]}],
                "conclusions": [],
            },
        },
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, _cfg: prompts.append(json.loads(prompt)) or next(first_outputs))
    status, _, pending, _ = run_agent(
        core_agent,
        "Inspect the mechanism and remember the exact supporting span",
        cfg,
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        retornar_detalhes=True,
    )
    assert status == "await_user"
    assert pending["session"]["task_memory"]["findings"]["f-resume"]["evidence_ids"] == ["ev-resume"]
    # Hot source bodies are intentionally absent from persisted Observation state.
    assert "content" not in pending["session"]["observation_ledger"]["materials"]["mat-0001"]

    resumed_prompts = []
    resumed_outputs = iter([
        agent_tools(tool_call("read_file", {"path": "agent.py", "line_start": 28, "line_end": 37})),
        agent_complete({"answer": "Evidence verified after resume.", "grounding_ids": ["mat-0001"]}),
    ])

    def resumed_fake(prompt, _cfg):
        payload = json.loads(prompt)
        resumed_prompts.append(payload)
        if len(resumed_prompts) == 1:
            assert payload["task_knowledge"]["findings"][0]["id"] == "f-resume"
            return next(resumed_outputs)
        replay = payload["latest_capability_results"][0]
        assert replay["coverage_replayed"] is True
        assert replay["rematerialized"] is True
        assert replay["executed"] is False
        detail = replay["detail"]
        assert detail["line_start"] == 28 and detail["line_end"] == 37
        assert "28 |" in detail["numbered_content"] and "37 |" in detail["numbered_content"]
        return next(resumed_outputs)

    monkeypatch.setattr(core_agent, "executar_agente_llm", resumed_fake)
    status, text, pending2, details = run_agent(
        core_agent,
        pending["session"]["request"],
        cfg,
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        retomar=pending,
        resposta_usuario="Sim",
        retornar_detalhes=True,
    )
    assert status == "success" and text == "Evidence verified after resume." and pending2 is None
    assert details["capability_calls"] == 0  # resume performed no new physical capability execution
    assert details["observation_replays"] >= 1
