from __future__ import annotations

import json
from pathlib import Path

import eyle.core.agent as core_agent
from tests.canonical import base_config, agent_tools, agent_final, tool_call


def test_workspace_fact_needs_no_investigation(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, _config):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return agent_tools(tool_call("count_tokens", {}))
        result = payload["latest_capability_results"][0]
        assert result["tool"] == "count_tokens"
        count = result["detail"]["estimated_tokens"]
        return agent_final({
            "answer": f"O projeto tem aproximadamente {count} tokens.",
            "limitations": ["A contagem é estimada."],
            "grounding_ids": list(result.get("grounding_ids") or []),
        })

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    status, text, pending, details = core_agent.executar_agente(
        "quantos tokens tem o projeto?",
        base_config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert pending is None
    assert len(prompts) == 2
    assert details["tool_calls"] == 1
    assert details["investigation"] == []
    assert "tokens" in text
    assert all(item.get("reason") != "INVESTIGATION_REQUIRED" for item in details["decision_history"])


def test_declared_open_investigation_blocks_final_until_resolved(monkeypatch, tmp_path):
    calls={"n":0}
    def fake(prompt,_config):
        calls["n"]+=1
        if calls["n"]==1:
            return agent_final("Prematuro.",investigation=[{"id":"T1","goal":"Inspect runtime flow","status":"open","grounding_ids":[],"conclusion":"","reason":""}])
        payload=json.loads(prompt)
        if calls["n"]==2:
            assert "FINAL_COMMITMENTS_OPEN" in str(payload.get("runtime_feedback") or "")
        return agent_tools(tool_call("read_file", {"path":"note.txt"}), investigation=[{"id":"T1","goal":"Inspect runtime flow","status":"open","grounding_ids":[],"conclusion":"","reason":""}]) if calls["n"]==2 else agent_final({"answer":"Resolvido.","grounding_ids":["mat-0001"]}, investigation=[{"id":"T1","goal":"Inspect runtime flow","status":"established","grounding_ids":["mat-0001"],"conclusion":"note.txt establishes the runtime fact needed by the request.","reason":"Observed note.txt"}])
    (tmp_path / "note.txt").write_text("ok\n",encoding="utf-8")
    monkeypatch.setattr(core_agent,"executar_agente_llm",fake)
    status,text,_,details=core_agent.executar_agente("Isso é legado?",base_config(),projeto={"caminho_origem":str(tmp_path)},retornar_detalhes=True)
    assert status=="success" and text=="Resolvido."
    assert details["investigation"][0]["status"]=="established"
    assert calls["n"]==3

def test_runtime_has_no_semantic_task_router_symbols():
    source = Path(core_agent.__file__).read_text(encoding="utf-8")
    forbidden = [
        "INVESTIGATION_REQUIRED",
        "grounding_mode",
        "_OBVIOUS_CALCULATOR_REQUEST",
        "_OBVIOUS_AGENT_INFO_REQUEST",
        "_FAST_CHAT_HINT",
        "_phase_for_call",
        "max_write_investigation_turns",
        "committed_progress_extension_calls",
    ]
    for token in forbidden:
        assert token not in source
