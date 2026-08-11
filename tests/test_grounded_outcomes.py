from __future__ import annotations

import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.sandbox as sandbox_mod
import eyle.core.tools as tools
from eyle.core.claim_review import build_answer_anchors, normalize_claim_review
from eyle.core.code_relations import analyze_symbol_relations
from eyle.core.execution_context import ExecutionContext, bind_execution, reset_execution
from llm.structured import StructuredResponseError, parse_claim_review_response
from tests.canonical import agent_final, agent_tools, base_config, review, claim, tool_call


def test_material_omission_can_be_grounded_by_request_and_answer_without_source_evidence():
    raw = {
        "material_satisfaction": {
            "status": "gap", "grounding_refs": ["request", "answer:a1"],
            "reason": "The visible answer omits a requested result.",
        },
        "answer_consistency": {
            "status": "consistent", "grounding_refs": ["answer:a1"],
            "reason": "No internal conflict is visible.",
        },
        "claims": [],
        "semantic_gaps": [{
            "type": "material_omission", "target_id": None,
            "grounding_refs": ["request", "answer:a1"],
            "required_property": "The omitted requested result must be delivered or explicitly limited.",
            "reason": "The request asks for two outputs while the answer contains one.",
        }],
    }
    parsed = parse_claim_review_response(raw)
    ok, reason, normalized = normalize_claim_review(
        parsed, {}, answer="Only A.", answer_anchors=build_answer_anchors("Only A."),
        visible_evidence_ids=[], investigation=[], runtime_facts=[],
    )
    assert ok is True and reason == "ok"
    assert normalized["semantic_gaps"][0]["evidence_ids"] == []
    assert normalized["semantic_gaps"][0]["grounding_refs"] == ["request", "answer:a1"]


def test_runtime_fact_can_ground_blocked_outcome_without_evidence_ledger():
    raw = review(
        claims=[claim(
            statement="run_command is physically unavailable in this job",
            grounding_refs=["runtime:r1"], verdict="supported",
            reason="The runtime fact records SANDBOX_UNAVAILABLE.",
        )],
        material_status="blocked",
        material_grounding=["runtime:r1", "request", "answer:a1"],
        material_reason="The requested sandbox action cannot execute in this job.",
    )
    runtime_facts = [{
        "ref": "runtime:r1", "tool": "run_command", "status": "failed", "ok": False,
        "executed": False, "error_code": "SANDBOX_UNAVAILABLE",
    }]
    ok, reason, normalized = normalize_claim_review(
        raw, {}, answer="Não consegui executar: sandbox indisponível.",
        visible_evidence_ids=[], investigation=[], runtime_facts=runtime_facts,
    )
    assert ok is True and reason == "ok"
    assert normalized["material_satisfaction"]["status"] == "blocked"
    assert normalized["claims"][0]["evidence_ids"] == []


def test_claim_schema_requires_grounding_coordinates_up_front():
    raw = {
        "material_satisfaction": {"status": "satisfied", "grounding_refs": [], "reason": "x"},
        "answer_consistency": {"status": "consistent", "grounding_refs": ["answer:a1"], "reason": "x"},
        "claims": [], "semantic_gaps": [],
    }
    try:
        parse_claim_review_response(raw)
    except StructuredResponseError as error:
        assert "GROUNDING_REFS_REQUIRED" in str(error.code)
    else:
        raise AssertionError("empty grounding must be rejected by the structured boundary")


def test_nonretryable_tool_failure_becomes_runtime_fact_and_final_can_be_blocked(monkeypatch, tmp_path):
    prompts = []
    outputs = iter([
        agent_tools(tool_call("run_command", {"command": "echo ok"})),
        agent_final("Não consegui executar porque o sandbox forte está indisponível."),
    ])

    def fake_agent(prompt, _config):
        prompts.append(json.loads(prompt))
        return next(outputs)

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)

    def fake_tool(name, arguments, context):
        assert name == "run_command"
        return {
            "status": "failed", "ok": False, "executed": False, "changed": False,
            "error_code": "SANDBOX_UNAVAILABLE", "retryable": False,
            "detail": "Docker/Bubblewrap unavailable",
        }

    monkeypatch.setattr(core_agent, "executar_tool", fake_tool)
    claim_packets = []

    def fake_claim(prompt, _config):
        packet = json.loads(prompt)
        claim_packets.append(packet)
        runtime_ref = packet["runtime_facts"][0]["ref"]
        return review(
            claims=[claim(
                statement="The sandbox capability is unavailable in this job",
                grounding_refs=[runtime_ref], verdict="supported",
                reason="Runtime recorded the non-retryable capability failure.",
            )],
            material_status="blocked",
            material_grounding=[runtime_ref, "request", "answer:a1"],
            material_reason="The requested action is physically blocked in this execution.",
        )

    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)
    status, text, _, details = core_agent.executar_agente(
        "Execute no sandbox.", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "sandbox" in text.lower()
    assert details["tool_calls"] == 0  # failure was non-executed physical availability check
    assert len(prompts) == 2
    assert "run_command" in prompts[0]["capability_index"][-1] or any("run_command(" in x for x in prompts[0]["capability_index"])
    terminal = prompts[1]["physical_limits"]["terminal_capabilities"]
    assert terminal["run_command"]["error_code"] == "SANDBOX_UNAVAILABLE"
    assert not any(item.startswith("run_command(") for item in prompts[1]["capability_index"])
    assert claim_packets[0]["runtime_facts"][0]["error_code"] == "SANDBOX_UNAVAILABLE"


def test_symbol_relations_reports_registry_binding_and_root_reachability(tmp_path):
    (tmp_path / "tools.py").write_text(
        "def target():\n    return 1\n\nTOOLS = {'x': {'fn': target}}\n",
        encoding="utf-8",
    )
    result = tools.executar_tool(
        "symbol_relations",
        {"symbol": "target", "roots": ["tools.py"], "direction": "incoming", "include_text_references": False},
        {"projeto": {"caminho_origem": str(tmp_path)}, "config": base_config()},
    )
    assert result["ok"] is True
    detail = result["detail"]
    assert any(edge["kind"] == "registry_binding" for edge in detail["incoming"])
    assert detail["outgoing"] == []
    assert detail["text_references"] == []
    assert detail["root_reachability"][0]["reachable"] is True


def test_docker_backend_reuses_one_container_per_job(monkeypatch, tmp_path):
    cfg = {
        "backend": "docker", "imagem_docker": "python:3.12-slim",
        "timeout_segundos": 30, "cpu_segundos": 30, "memoria_mb": 256,
        "max_processos": 32, "max_arquivos_abertos": 64, "max_saida_kb": 64,
        "max_arquivo_mb": 64, "max_arquivos_projeto": 1000, "max_tamanho_projeto_mb": 64,
        "cpus": 1.0,
    }
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    calls = []

    class Completed:
        returncode = 0
        stdout = "container-id\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Completed()

    monkeypatch.setattr(sandbox_mod.subprocess, "run", fake_run)
    execution = ExecutionContext.from_config(base_config())
    token = bind_execution(execution)
    try:
        limits = sandbox_mod._limites(cfg)
        first, cleanup1 = sandbox_mod._comando_docker(str(tmp_path), "apt-get update", ".", cfg, limits)
        second, cleanup2 = sandbox_mod._comando_docker(str(tmp_path), "python -V", ".", cfg, limits)
        assert cleanup1 is None and cleanup2 is None
        assert first[0:2] == ["/usr/bin/docker", "exec"]
        assert second[0:2] == ["/usr/bin/docker", "exec"]
        assert first[4] == second[4] == execution.sandbox_container_name
        docker_runs = [call for call in calls if call[:2] == ["/usr/bin/docker", "run"]]
        assert len(docker_runs) == 1
        assert "--pull" in docker_runs[0] and "missing" in docker_runs[0]
        assert "--read-only" not in docker_runs[0]
        assert "--network" in docker_runs[0] and "bridge" in docker_runs[0]
    finally:
        execution.cleanup_sandbox()
        reset_execution(token)


def test_symbol_relations_reports_python_main_guard_even_with_ambiguous_main_names(tmp_path):
    (tmp_path / "a.py").write_text(
        "def main():\n    return 1\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    detail = analyze_symbol_relations(
        str(tmp_path), "main", path="a.py", direction="incoming",
        include_text_references=False, max_depth=4, max_edges=20,
    )
    assert len(detail["definitions"]) == 1
    assert any(
        edge["kind"] == "python_main_guard"
        and edge["from"] == "a.py::<module>"
        and edge["to"] == "a.py::main"
        for edge in detail["incoming"]
    )
    assert "python_main_guard" in detail["reachability_edge_kinds"]
