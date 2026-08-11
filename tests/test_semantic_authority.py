from __future__ import annotations

import json
from pathlib import Path

import eyle.core.agent as core_agent
from tests.canonical import base_config


def test_workspace_fact_needs_no_investigation(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, _config):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return {
                "tool_calls": [{"tool": "count_tokens", "arguments": {}}],
                "investigation_updates": [],
            }
        result = payload["latest_tool_results"][0]
        assert result["tool"] == "count_tokens"
        count = result["detail"]["estimated_tokens"]
        return {
            "final": {
                "answer": f"O projeto tem aproximadamente {count} tokens.",
                "limitations": ["A contagem é estimada."],
                "evidence_ids": list(result.get("evidence_ids") or []),
            },
            "investigation_updates": [],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    status, text, pending, details = core_agent.executar_agente(
        "quantos tokens tem o projeto?",
        base_config(claims_mode="off"),
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


def test_declared_open_debt_blocks_final(monkeypatch, tmp_path):
    calls = 0

    def fake(_prompt, _config):
        nonlocal calls
        calls += 1
        return {
            "final": {"answer": "Prematuro.", "limitations": [], "evidence_ids": []},
            "investigation_updates": [{
                "id": "T1",
                "goal": "Establish whether the module participates in active runtime flow",
                "status": "open",
                "evidence_ids": [],
                "reason": "",
            }],
        }

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    cfg = base_config(claims_mode="off")
    cfg["agent"]["max_llm_turns"] = 2
    status, text, _, details = core_agent.executar_agente(
        "Isso é legado?", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert calls == 2
    assert any(
        item.get("decision") == "final" and "FINAL_INVESTIGATION_TARGET_OPEN:T1" in str(item.get("reason"))
        for item in details["decision_history"]
    )


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
