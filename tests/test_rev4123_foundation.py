#!/usr/bin/env python3
"""Rev4.12.3 foundation regressions: tool contracts, routing and state-aware tests."""
from __future__ import annotations

import json

from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from eyle.core.response_quality import request_needs_project_evidence, request_requires_write
from eyle.core.session import AgentSession
from eyle.runtime.history import build_public_job_history
from llm.executar import PROMPT_AGENTE


BASE = Path(__file__).resolve().parents[1]


def _config():
    return {
        "agent": {
            "max_llm_turns": 6,
            "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "max_git_diff_chars": 6000,
            "response_quality": {"enabled": True},
        },
        "codar": {"ativado": True, "testes": {"ativado": True}},
    }


def test_analysis_presentation_word_does_not_arm_write_gate():
    assert request_requires_write(
        "Identifique até 5 riscos e separe claramente de bugs.", True,
    ) is False
    assert request_requires_write("Mude a variável x no código para 2", True) is True
    assert request_requires_write("No routes.py, separe o HTML em um template", True) is True



def test_named_greeting_keeps_workspace_fast_path_tool_free(tmp_path):
    session = AgentSession("Oi Eyle")
    session.turn = 1
    phase = core_agent._phase_for_call(
        session, _config(), {"caminho_origem": str(tmp_path)},
    )
    assert phase == "chat"
    _, catalog = core_agent._tool_catalog(
        _config(), {"caminho_origem": str(tmp_path)}, phase, session.request,
    )
    assert catalog == []

def test_non_fast_workspace_request_gets_general_investigation_tools(tmp_path):
    session = AgentSession("Onde AgentSession é definido e onde ele é utilizado?")
    session.turn = 1
    phase = core_agent._phase_for_call(
        session, _config(), {"caminho_origem": str(tmp_path)},
    )
    assert phase == "analysis_investigate"
    _, catalog = core_agent._tool_catalog(
        _config(), {"caminho_origem": str(tmp_path)}, phase, session.request,
    )
    names = {item["name"] for item in catalog}
    assert {"find_symbol", "search_code", "git_status", "git_diff", "run_tests"} <= names
    # Initial investigation requires evidence before any dry-run patch tool.
    assert not ({"test_patch_dry_run", "test_patch_set_dry_run"} & names)


def test_analysis_after_evidence_stays_observational_without_patch_tools(tmp_path):
    session = AgentSession("Faça uma análise profunda desse trecho")
    session.turn = 2
    session.evidence["ev-0001"] = {"arquivo": "app.py", "linha_inicio": 1, "linha_fim": 1}
    phase = core_agent._phase_for_call(
        session, _config(), {"caminho_origem": str(tmp_path)},
    )
    assert phase == "analysis_complete_or_read"
    _, catalog = core_agent._tool_catalog(
        _config(), {"caminho_origem": str(tmp_path)}, phase, session.request,
    )
    names = {item["name"] for item in catalog}
    assert not ({"test_patch_dry_run", "test_patch_set_dry_run"} & names)


def test_compact_tool_catalog_uses_shared_taxonomy_without_repeated_side_effect_boilerplate():
    catalog = tools.gerar_catalogo_tools(compact=True)
    taxonomy = tools.gerar_taxonomia_tools(catalog)
    assert catalog and len(catalog) == 20
    assert set(taxonomy["categories"]) == {"READ_ONLY", "EDIT"}
    assert taxonomy["effects"]["default"] == "NONE"
    assert taxonomy["effects"]["tags"]["EXEC"] == ["run_tests"]
    assert set(taxonomy["effects"]["tags"]["TEMP"]) == {"test_patch_dry_run", "test_patch_set_dry_run"}
    assert taxonomy["effects"]["tags"]["MEMORY_WRITE"] == ["memory_store"]
    assert set(taxonomy["effects"]["tags"]["WORKSPACE_WRITE"]) == {"apply_patch", "apply_patch_set"}
    for item in catalog:
        assert item["name"]
        assert item["purpose"]
        assert isinstance(item["inputs"], dict)
        assert item["returns"]
        assert "side_effects" not in item
        assert "does_not" not in item
        assert "category" not in item
        assert "effects" not in item
    count = next(item for item in catalog if item["name"] == "count_tokens")
    assert "actual llm request usage" in " ".join(count["caveats"]).lower()
    inspect = next(item for item in catalog if item["name"] == "inspect_project")
    assert "importance ranking" in " ".join(inspect["caveats"]).lower()


def test_shared_tool_taxonomy_reduces_full_catalog_wire_size():
    catalog = tools.gerar_catalogo_tools(compact=True)
    taxonomy = tools.gerar_taxonomia_tools(catalog)
    serialized = json.dumps({"tool_taxonomy": taxonomy, "available_tools": catalog}, ensure_ascii=False, separators=(",", ":"))
    # Rev4.12.3.1 used 12,492 chars for the 20 compact tool contracts alone.
    assert len(serialized) < 11_000


def test_run_tests_closes_only_narrow_test_state():
    session = AgentSession("Execute os testes")
    session.latest_tool_results = [{
        "tool": "run_tests", "executed": False,
        "error_code": "TEST_RUNNER_UNAVAILABLE",
    }]
    session.tool_history = [{"tool": "run_tests", "status": "failed"}]
    assert core_agent._run_tests_closes_read_only_task(session) is True

    session.tool_history.insert(0, {"tool": "project_stats", "status": "success"})
    assert core_agent._run_tests_closes_read_only_task(session) is False

    session.tool_history = [{"tool": "run_tests", "status": "failed"}]
    session.plan = ["run tests", "inspect failing module"]
    assert core_agent._run_tests_closes_read_only_task(session) is False


def test_cropped_string_reaches_stable_floor_instead_of_looping():
    value = {"text": "x" * 5000}
    steps = 0
    while core_agent._shrink_structured_once(value):
        steps += 1
        assert steps < 20
    assert value["text"].endswith("...[context cropped]")
    assert len(value["text"]) < 1100


def test_history_always_surfaces_a_tool_name_and_ui_has_visible_tool_line():
    history = build_public_job_history({
        "id": 7,
        "status": "completed",
        "resultado": {"details": {
            "tool_history": [{
                "turn": 1, "phase": "analysis_investigate", "status": "success",
                "result": {"tool": "project_stats", "ok": True},
            }],
        }},
    })
    assert history["tools"][0]["tool"] == "project_stats"
    app_js = (BASE / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'historyLine("ferramenta", call.tool' in app_js


def test_pytest_remains_a_runtime_dependency_and_prompt_preserves_free_reasoning():
    requirements = (BASE / "requirements.txt").read_text(encoding="utf-8")
    lock = (BASE / "requirements.lock").read_text(encoding="utf-8")
    assert "pytest==8.2.2" in requirements
    assert "pytest==8.2.2" in lock
    assert "Project-specific facts, confirmed bugs and contextual risks require real evidence" in PROMPT_AGENTE
    assert "hypotheses, opinions, tradeoffs and recommendations may be reasoned" in PROMPT_AGENTE


def test_compound_requests_never_get_utility_fast_path(tmp_path):
    project = {"caminho_origem": str(tmp_path)}
    for request in (
        "Por que o teste 2+2 está falhando neste projeto?",
        "Analise a capacidade do módulo de contexto.",
        "Quem é você e onde AgentSession é definido?",
    ):
        session = AgentSession(request)
        session.turn = 1
        assert core_agent._phase_for_call(session, _config(), project) == "analysis_investigate"

    for request, expected in (
        ("93847 * 7283 / 17.4 + 918", "calculate"),
        ("Quem é você?", "agent_info"),
        ("Quais suas ferramentas?", "agent_info"),
    ):
        session = AgentSession(request)
        session.turn = 1
        phase = core_agent._phase_for_call(session, _config(), project)
        assert phase == "chat"
        _, catalog = core_agent._tool_catalog(_config(), project, phase, request)
        assert {item["name"] for item in catalog} == {expected}


def test_run_tests_only_closes_when_request_itself_is_test_only():
    terminal = [{"tool": "run_tests", "executed": True, "ok": True}]
    history = [{"tool": "run_tests", "status": "success"}]

    simple = AgentSession("Execute os testes do projeto e explique qualquer falha")
    simple.latest_tool_results = terminal
    simple.tool_history = history
    assert core_agent._run_tests_closes_read_only_task(simple) is True

    compound = AgentSession(
        "Faça uma análise profunda, encontre os 3 problemas mais importantes e execute os testes necessários"
    )
    compound.latest_tool_results = terminal
    compound.tool_history = history
    compound.plan = []
    assert core_agent._run_tests_closes_read_only_task(compound) is False


def test_project_evidence_gate_distinguishes_workspace_facts_from_general_opinion():
    assert request_needs_project_evidence("Identifique até 5 bugs reais.", True) is True
    assert request_needs_project_evidence("Onde AgentSession é definido?", True) is True
    assert request_needs_project_evidence("Faça uma análise completa do projeto.", True) is True
    assert request_needs_project_evidence("Explique o caminho de uma mensagem minha até a resposta da LLM.", True) is True
    assert request_needs_project_evidence("Qual sua opinião sobre arquitetura hexagonal?", True) is False
    assert request_needs_project_evidence("Quais trade-offs existem em arquitetura hexagonal?", True) is False


def test_response_rewrite_is_not_mistaken_for_workspace_write():
    assert request_requires_write("Corrija sua resposta anterior.", True) is False
    assert request_requires_write("Mude sua explicação, não o código.", True) is False
    assert request_requires_write("Corrija o código em app.py.", True) is True
    assert request_requires_write("Remova o arquivo old.py.", True) is True


def test_all_tool_inputs_have_self_contained_descriptions_and_search_is_literal():
    for name, item in tools.TOOLS.items():
        props = (item.get("input_schema") or {}).get("properties") or {}
        for arg, spec in props.items():
            assert spec.get("description"), f"{name}.{arg} missing description"
    search = tools.TOOLS["search_code"]
    assert "query" in search["input_schema"]["properties"]
    assert "literal" in search["input_schema"]["properties"]["query"]["description"].lower()
    assert search["compat_aliases"].get("pergunta") == "query"
    assert tools.TOOLS["memory_store"]["permission"] == "MEMORY_WRITE"


def test_agent_info_reports_write_state_without_equating_disabled_with_no_confirmation(tmp_path):
    config = _config()
    config["app_version"] = "2.7.4"
    config["revision"] = "4.12.3.1-foundation-hardening"
    config["codar"]["ativado"] = False
    result = tools.executar_tool("agent_info", {}, {"config": config, "projeto": {"caminho_origem": str(tmp_path)}})
    detail = result["detail"]
    assert detail["write_enabled"] is False
    assert detail["write_confirmation_required"] is True


def test_rejected_tool_request_is_recorded_as_rejected_not_accepted(monkeypatch, tmp_path):
    responses = iter([
        '{"tool":"not_a_tool","arguments":{}}',
        '{"final":"Não usei uma ferramenta inexistente."}',
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda *a, **k: next(responses))
    status, _text, _pending, details = core_agent.executar_agente(
        "Oi Eyle", _config(), {"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    requested = [d for d in details["decision_history"] if d.get("decision") == "tool"]
    assert requested and requested[0]["outcome"] == "requested"
    rejected = [d for d in details["decision_history"] if d.get("decision") == "tool_validation"]
    assert rejected and rejected[0]["outcome"] == "rejected"
    assert details["tool_history"][0]["tool"] == "not_a_tool"
    assert details["tool_history"][0]["status"] == "rejected"
