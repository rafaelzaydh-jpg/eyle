import json

import eyle.core.agent as core_agent
from eyle.core.request_policy import (
    request_contract,
    requested_finding_constraints,
)
from eyle.core.validation import validate_final
from tests.canonical import agent_final, agent_tools, base_config, tool_call


def test_final_gate_is_structural_and_requires_evidence_for_workspace_facts():
    evidence = {"ev-1": {"arquivo": "x.py"}}
    ok, reason, answer, _, _, _ = validate_final(
        {"answer": "X está em x.py.", "evidence_ids": ["ev-1"]},
        evidence, request="Onde AgentSession está definido?", project_available=True,
        investigation=[{"id":"T1","goal":"Establish location","status":"established","evidence_ids":["ev-1"],"reason":"cited"}],
        grounding_required=True,
    )
    assert ok is True and reason == "ok" and answer.startswith("X")

    ok, reason, *_ = validate_final(
        {"answer": "X está em x.py.", "evidence_ids": []},
        evidence, request="Onde AgentSession está definido?", project_available=True,
        investigation=[{"id":"T1","goal":"Establish location","status":"established","evidence_ids":["ev-1"],"reason":"cited"}],
        grounding_required=True,
    )
    assert ok is False and reason == "FINAL_PROJECT_EVIDENCE_IDS_REQUIRED"


def test_final_gate_rejects_unknown_fields_instead_of_legacy_claims():
    ok, reason, *_ = validate_final(
        {"answer": "X.", "evidence_ids": [], "claims": []}, {},
    )
    assert ok is False
    assert reason == "FINAL_UNKNOWN_FIELDS:claims"


def test_final_gate_rejects_unknown_evidence_and_unbalanced_fences():
    ok, reason, *_ = validate_final({"answer": "X", "evidence_ids": ["ev-x"]}, {})
    assert ok is False and reason == "FINAL_UNKNOWN_EVIDENCE:ev-x"
    ok, reason, *_ = validate_final("```python\nx=1", {})
    assert ok is False and reason == "FINAL_UNBALANCED_CODE_FENCE"


def test_requested_finding_limits_are_deterministic_not_claim_limits():
    parsed = requested_finding_constraints("Encontre até 5 bugs e no máximo 3 riscos")
    assert parsed["by_kind"]["bug"] == 5
    assert parsed["by_kind"]["risk"] == 3
    assert request_contract("Encontre até 5 bugs", True)["requested_kind_limits"]["bug"] == 5


def test_canonical_grounded_final_is_accepted_by_agent(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\n", encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"})),
        agent_final({"answer": "app.py importa Flask.", "evidence_ids": ["ev-0001"]}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: next(outputs))
    status, text, _, _ = core_agent.executar_agente(
        "Analise app.py", base_config(claims_mode="off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "Flask" in text


def test_relevant_source_survives_non_read_result(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"})),
        agent_tools(tool_call("calculate", {"expression": "1+1"})),
        agent_final({"answer": "app.py define VALUE = 1.", "evidence_ids": ["ev-0001"]}),
    ])
    def fake(prompt, cfg):
        prompts.append(json.loads(prompt))
        return next(outputs)
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, *_ = core_agent.executar_agente(
        "Analise app.py", base_config(claims_mode="off"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert any(item.get("file") == "app.py" for item in prompts[-1]["evidence_index"])
