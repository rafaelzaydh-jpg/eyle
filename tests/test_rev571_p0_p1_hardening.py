from __future__ import annotations

import json

import eyle.core.agent as core_agent
from eyle.core.code_relations import analyze_symbol_relations
from eyle.core.session import AgentSession
from tests.canonical import base_config, review, claim


def test_reachability_resolves_import_binding_before_global_duplicate_name(tmp_path):
    (tmp_path / "left.py").write_text("def bridge():\n    return 'left'\n", encoding="utf-8")
    (tmp_path / "right.py").write_text("def bridge():\n    return 'right'\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from left import bridge\n\n"
        "def main():\n"
        "    bridge()\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    result = analyze_symbol_relations(
        str(tmp_path), "bridge", query="reachability", max_depth=6, max_edges=20,
    )

    assert result["coverage"]["objective_complete"] is True
    assert result["coverage"]["objective_result"] == "reachable"
    path = result["observations"][0]["path"]
    assert path[-1] == "left.py::bridge"
    assert "right.py::bridge" not in path
    assert result["frontiers"] == []


def test_prompt_projection_keeps_canonical_state_but_bounds_hot_indexes():
    session = AgentSession("inspect")
    for index in range(1, 25):
        evidence_id = f"ev-{index:04d}"
        session.evidence_ledger["items"][evidence_id] = {
            "id": evidence_id,
            "file": f"f{index}.py",
            "line_start": 1,
            "line_end": 1,
            "file_hash": f"h{index}",
            "content_hash": f"c{index}",
        }
        session.observation_ledger["entries"][f"w0:obs:{index}"] = {
            "turn": index,
            "tool": "read_file",
            "observation_signature": f"obs:{index}",
            "workspace_epoch": 0,
            "evidence_ids": [evidence_id],
        }
    session.investigation = [{
        "id": "T1", "goal": "Establish one fact", "status": "established",
        "evidence_ids": ["ev-0001"], "reason": "selected by Main",
    }]

    evidence_view = core_agent._project_evidence_index(session)
    observation_view = core_agent._project_observation_map(session)

    assert len(session.evidence_ledger["items"]) == 24
    assert len(session.observation_ledger["entries"]) == 24
    assert [item["id"] for item in evidence_view][:1] == ["ev-0001"]
    assert len(evidence_view) <= 9  # one pinned + eight recent
    assert any("ev-0001" in item.get("evidence_ids", []) for item in observation_view)
    assert len(observation_view) <= 7  # one pinned + six recent


def test_claim_receives_only_main_selected_final_evidence(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    agent_prompts = []
    claim_prompts = []

    def fake_agent(prompt, _config):
        payload = json.loads(prompt)
        agent_prompts.append(payload)
        if len(agent_prompts) == 1:
            return {
                "tool_calls": [
                    {"tool": "count_tokens", "arguments": {}},
                    {"tool": "inspect_project", "arguments": {}},
                ],
                "investigation_updates": [],
            }
        token_result = next(item for item in payload["latest_tool_results"] if item["tool"] == "count_tokens")
        selected = token_result["evidence_ids"][0]
        return {
            "final": {
                "answer": "O projeto foi medido em tokens.",
                "limitations": [],
                "evidence_ids": [selected],
            },
            "investigation_updates": [],
        }

    def fake_claim(prompt, _config):
        payload = json.loads(prompt)
        claim_prompts.append(payload)
        assert len(payload["evidence"]) == 1
        ref = payload["evidence"][0]["ref"]
        return review(claims=[claim(
            statement="The project token measurement supports the answer.",
            grounding_refs=[ref], verdict="supported", reason="Selected measurement Evidence supports it.",
        )])

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_agent)
    monkeypatch.setattr(core_agent, "executar_verificador_claims", fake_claim)

    status, _, _, details = core_agent.executar_agente(
        "Meça o projeto em tokens.", base_config(claims_mode="self_check"),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert details["evidence_count_total"] == 2
    assert len(claim_prompts) == 1
