from tests.canonical import run_agent
import json

import eyle.core.agent as core_agent
import eyle.runtime.service as service_mod
from llm.structured import parse_agent_response, StructuredResponseError
from tests.canonical import agent_await_user, agent_complete, agent_tools, base_config, tool_call


def test_await_user_contract_carries_main_authored_choices_and_rejects_needs_user():
    payload = agent_await_user(
        "Qual árvore devo usar?",
        reason="There are two physically available source trees with different authority.",
        options=[
            {"id": "root", "label": "Eyle Root"},
            {"id": "workspace", "label": "Eyle Workspace"},
        ],
    )
    parsed = parse_agent_response(payload)
    assert parsed["action"]["kind"] == "await_user"
    assert parsed["action"]["options"][0] == {"id": "root", "label": "Eyle Root"}

    try:
        parse_agent_response({
            "action": {"kind": "needs_user", "question": "Qual porta?", "missing_information": "port"},
            "investigation_updates": [],
            "task_updates": [],
        })
    except StructuredResponseError as error:
        assert "AGENT_ACTION_KIND_INVALID" in str(error.code)
    else:
        raise AssertionError("Rev1.4.4 must reject the removed needs_user action")


def test_resume_preserves_canonical_request_and_retains_human_resolution(monkeypatch, tmp_path):
    (tmp_path / "session.py").write_text("class AgentSession:\n    pass\n", encoding="utf-8")
    cfg = base_config()

    first_prompts = []
    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: first_prompts.append(json.loads(prompt)) or agent_await_user(
            "Qual classe devo localizar?",
            reason="The requested symbol name must come from the user.",
            options=[{"id": "session", "label": "AgentSession"}],
        ),
    )
    original_request = "Localize a classe que eu indicar e responda com o arquivo."
    status, _, pending, details1 = run_agent(core_agent, 
        original_request,
        cfg,
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        retornar_detalhes=True,
        execution_id="job-1",
        source_job_id=1,
    )
    assert status == "await_user"
    assert pending["continuation_kind"] == "await_user"
    assert pending["session"]["request"] == original_request
    assert details1["turns"] == 1

    resumed_prompts = []
    outputs = iter([
        agent_tools(tool_call("find_symbol", {"symbol": "AgentSession"})),
        agent_complete({"answer": "session.py:1", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(
        core_agent,
        "executar_agente_llm",
        lambda prompt, _cfg: resumed_prompts.append(json.loads(prompt)) or next(outputs),
    )

    status, text, pending2, details2 = run_agent(core_agent, 
        pending["session"]["request"],
        cfg,
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
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
    assert resumed_prompts[0]["request"] == original_request
    assert resumed_prompts[1]["request"] == original_request
    retained = resumed_prompts[0]["request_context"]
    assert any(item.get("answer") == "AgentSession" for item in retained)
    assert any("Qual classe" in item.get("question", "") for item in retained)
    assert all(
        not any(item.get("tool") == "user_response" for item in (prompt.get("latest_capability_results") or []))
        for prompt in resumed_prompts
    )
    assert details2["turns"] == 2
    assert details2["capability_calls"] == 1
    assert any(item.get("decision") == "await_user_resolution" for item in details2["decision_history"])
    assert details2["task_totals"]["turns"] == 3
    assert details2["task_totals"]["capability_calls"] == 1


def test_await_user_pending_does_not_expire_but_old_pending_schema_is_rejected(monkeypatch):
    pending = {
        "pending_schema_version": "4",
        "continuation_kind": "await_user",
        "question": "Which source?",
        "session": {"request": "task", "execution_id": "job-1"},
        "reason": "A user-owned choice is required.",
        "options": [{"id": "root", "label": "Eyle Root"}],
        "id": "ABCD",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "provider_context_hash": None,
    }
    valid, reason = service_mod._validar_pendencia(pending, {}, now=None)
    assert valid is True and reason is None

    old = dict(pending)
    old["pending_schema_version"] = "1"
    valid, reason = service_mod._validar_pendencia(old, {}, now=None)
    assert valid is False and reason == "PENDING_SCHEMA_INCOMPATIBLE"


def test_agent_prompt_treats_conversation_as_valid_without_forcing_work_state():
    from llm.executar import PROMPT_AGENTE
    lower = PROMPT_AGENTE.lower()
    assert "prior_conversation can resolve references" in lower and "request_context" in lower
    assert "do not create either merely because the structures exist" in lower
    assert "await_user" in lower
    assert "needs_user" not in lower
    assert "an available capability is not evidence that it was called" in lower

def test_execution_context_rejects_canonical_request_identity_drift():
    from eyle.runtime.execution_context import ExecutionContext
    execution = ExecutionContext.from_config(base_config(), execution_id="job-1", source_job_id=1)
    execution.bind_canonical_request("task A")
    execution.assert_canonical_request("task A")
    try:
        execution.assert_canonical_request("task B")
    except RuntimeError as error:
        assert str(error) == "CANONICAL_REQUEST_IDENTITY_MISMATCH"
    else:
        raise AssertionError("request identity drift must fail")


def test_await_user_cancel_is_runtime_control_but_plain_sim_is_resolution(monkeypatch):
    pending = {
        "pending_schema_version": "2",
        "continuation_kind": "await_user",
        "question": "Continue?",
        "session": {"request": "task", "execution_id": "job-1"},
        "reason": "User approval is required.",
        "options": [{"id": "continue", "label": "Continuar"}],
        "id": "ABCD",
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": None,
        "provider_context_hash": None,
    }
    monkeypatch.setattr(service_mod, "carregar_config", lambda: base_config())
    monkeypatch.setattr(service_mod, "carregar_provider_context", lambda: {})
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
