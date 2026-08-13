import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.runtime.service as service_mod
from llm.structured import parse_agent_response, StructuredResponseError
from tests.canonical import agent_final, agent_needs_user, agent_tools, base_config, tool_call


def test_needs_user_contract_is_blocking_object_and_rejects_legacy_string():
    parsed = parse_agent_response({
        "action": {"kind": "needs_user", "question": "Qual porta?", "missing_information": "The server port"},
        "investigation_updates": [],
        "task_updates": [],
    })
    assert parsed["action"] == {"kind": "needs_user", "question": "Qual porta?", "missing_information": "The server port"}

    try:
        parse_agent_response({
            "action": {"kind": "needs_user", "question": "Qual porta?"},
            "investigation_updates": [],
            "task_updates": [],
        })
    except StructuredResponseError as error:
        assert "AGENT_NEEDS_USER_INVALID" in str(error.code)
    else:
        raise AssertionError("legacy string needs_user must be rejected")


def test_resume_clarification_is_canonical_across_main_turns(monkeypatch, tmp_path):
    (tmp_path / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
    cfg = base_config()

    first_prompts = []
    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: first_prompts.append(json.loads(prompt)) or agent_needs_user(
            "Qual classe devo localizar?",
            missing_information="The class name required to perform the requested lookup",
        ),
    )
    status, _, pending, details1 = core_agent.executar_agente(
        "Localize a classe que eu indicar e responda com o arquivo.",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        execution_id="job-1",
        source_job_id=1,
    )
    assert status == "needs_user"
    assert pending["clarification"]["question"] == "Qual classe devo localizar?"
    assert pending["session"]["request"] == "Localize a classe que eu indicar e responda com o arquivo."
    assert details1["turns"] == 1

    resumed_prompts = []
    outputs = iter([
        agent_tools(tool_call("find_symbol", {"symbol": "AgentSession"})),
        agent_final({"answer": "session.py:1", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: resumed_prompts.append(json.loads(prompt)) or next(outputs),
    )

    status, text, pending2, details2 = core_agent.executar_agente(
        pending["session"]["request"],
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retomar=pending,
        resposta_usuario="AgentSession",
        retornar_detalhes=True,
        execution_id="job-2",
        source_job_id=2,
    )
    assert status == "success"
    assert text == "session.py:1"
    assert pending2 is None
    assert len(resumed_prompts) == 2
    canonical = resumed_prompts[0]["request"]
    assert "Localize a classe" in canonical
    assert "Missing information:" in canonical
    assert "The class name required" in canonical
    assert "Question: Qual classe devo localizar?" in canonical
    assert "Answer: AgentSession" in canonical
    assert resumed_prompts[1]["request"] == canonical
    assert all(
        not any(item.get("tool") == "user_response" for item in (prompt.get("latest_capability_results") or []))
        for prompt in resumed_prompts
    )
    # Job #2 metrics are physical-job scoped; cumulative task chronology is separate.
    assert details2["turns"] == 2
    assert details2["tool_calls"] == 1
    assert all(item.get("decision") != "needs_user" for item in details2["decision_history"])
    assert details2["task_totals"]["turns"] == 3
    assert details2["task_totals"]["tool_calls"] == 1


def test_expired_user_input_pending_cannot_capture_new_request(monkeypatch, tmp_path):
    calls = {"resume": 0, "new": 0}
    pending = {
        "pending_schema_version": "1",
        "continuation_kind": "user_input",
        "question": "Which class?",
        "session": {"request": "old request", "execution_id": "job-old"},
        "clarification": {"question": "Which class?", "missing_information": "class name"},
        "id": "ABCD",
        "created_at": "1999-01-01T00:00:00+00:00",
        "expires_at": "2000-01-01T00:00:00+00:00",
        "project_hash": "stale",
    }
    monkeypatch.setattr(service_mod, "carregar_agent_pendente", lambda: pending)
    monkeypatch.setattr(service_mod, "carregar_config", lambda: base_config())
    monkeypatch.setattr(service_mod, "carregar_projeto", lambda: {"caminho_origem": str(tmp_path)})
    monkeypatch.setattr(service_mod, "carregar_conversa", lambda: [])
    monkeypatch.setattr(service_mod, "registrar_mensagem", lambda *args, **kwargs: None)
    cleared = []
    monkeypatch.setattr(service_mod, "limpar_agent_pendente", lambda: cleared.append(True))
    monkeypatch.setattr(service_mod, "_retomar_agente_pendente", lambda *a, **k: calls.__setitem__("resume", calls["resume"] + 1))

    def fake_process(question, config, project, **kwargs):
        calls["new"] += 1
        return {"status": "success", "resposta": question, "avisos": [], "details": {"status": "success"}}
    monkeypatch.setattr(service_mod, "_processar_agente", fake_process)

    result = service_mod.processar("novo pedido real", registrar_pergunta=False, execution_id="job-2", source_job_id=2)
    assert result["resposta"] == "novo pedido real"
    assert calls == {"resume": 0, "new": 1}
    assert cleared == [True]


def test_agent_prompt_treats_conversation_as_valid_without_forcing_work_state():
    from llm.executar import PROMPT_AGENTE
    lower=PROMPT_AGENTE.lower()
    assert "conversation and simple requests may go straight to final" in lower
    assert "empty updates mean no new commitment" in lower
    assert "needs_user" in lower
    assert "ambient workspace state and capability availability are context, not tasks" in lower

def test_execution_context_rejects_canonical_request_identity_drift():
    from eyle.core.execution_context import ExecutionContext
    execution = ExecutionContext.from_config(base_config(), execution_id="job-1", source_job_id=1)
    execution.bind_canonical_request("task A")
    execution.assert_canonical_request("task A")
    try:
        execution.assert_canonical_request("task B")
    except RuntimeError as error:
        assert str(error) == "CANONICAL_REQUEST_IDENTITY_MISMATCH"
    else:
        raise AssertionError("request identity drift must fail")



def test_user_input_pending_cancel_is_control_but_plain_sim_is_clarification(monkeypatch):
    pending = {
        "pending_schema_version": "1",
        "continuation_kind": "user_input",
        "question": "Continue?",
        "session": {"request": "task", "execution_id": "job-1"},
        "clarification": {"question": "Continue?", "missing_information": "user choice"},
        "id": "ABCD",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "project_hash": "project",
    }
    monkeypatch.setattr(service_mod, "carregar_config", lambda: base_config())
    monkeypatch.setattr(service_mod, "carregar_projeto", lambda: {})
    monkeypatch.setattr(service_mod, "registrar_mensagem", lambda *a, **k: None)
    monkeypatch.setattr(service_mod, "carregar_agent_pendente", lambda: pending)
    monkeypatch.setattr(service_mod, "_validar_pendencia", lambda p, project: (True, None))

    resumed = []
    cancelled = []
    monkeypatch.setattr(service_mod, "_retomar_agente_pendente", lambda *a, **k: resumed.append(k.get("resposta_usuario")) or {"resposta": "resumed"})
    monkeypatch.setattr(service_mod, "_cancelar_agente_pendente", lambda p: cancelled.append(True) or {"resposta": "cancelled"})

    result = service_mod.processar("sim", registrar_pergunta=False)
    assert result["resposta"] == "resumed"
    assert resumed == ["sim"] and cancelled == []

    resumed.clear()
    result = service_mod.processar("cancelar ABCD", registrar_pergunta=False)
    assert result["resposta"] == "cancelled"
    assert resumed == [] and cancelled == [True]
