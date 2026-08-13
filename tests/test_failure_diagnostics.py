import hashlib
import json

import eyle.core.agent as core_agent
from eyle.core.session import AgentSession
from eyle.core.transactions import begin as begin_write_transaction, set_status as set_write_status
from eyle.runtime import service
from tests.canonical import agent_final


def _config(tests_enabled=True):
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_max_tokens": 4200,
        },
        "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3},
        "agent": {
            "max_file_read_lines": 400,
        },
        "codar": {
            "ativado": True,
            "testes": {"ativado": tests_enabled, "timeout_segundos": 30},
        },

    }


def _session_pending_replace_and_create(root):
    original = (root / "routes.py").read_text(encoding="utf-8")
    patches = [
        {"operation":"replace","path":"routes.py","content":"def amor():\n    return render_template('amor.html')\n","file_hash_expected":hashlib.sha256(original.encode()).hexdigest()},
        {"operation":"create","path":"templates/amor.html","content":"<h1>Amor</h1>\n"},
    ]
    session = AgentSession("mova o html")
    session.write_transaction = begin_write_transaction(patches=patches, turn=1)
    set_write_status(session.write_transaction, "awaiting_confirmation")
    return session, {"continuation_kind":"write_confirmation","transaction_id":session.write_transaction["transaction_id"]}


def test_failed_tests_expose_exact_output_and_structured_report(monkeypatch, tmp_path):
    routes = tmp_path / "routes.py"
    routes.write_text("def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8")
    session, pending = _session_pending_replace_and_create(tmp_path)
    diagnostic = (
        "'python -m pytest -q' falhou no sandbox (codigo 1).\n"
        "E   NameError: name 'render_template' is not defined\n"
        "1 failed, 2 passed"
    )
    monkeypatch.setattr(core_agent, "_verify_after_write", lambda *_: {
        "status": "failed",
        "ok": False,
        "executed": True,
        "error_code": "TESTS_FAILED",
        "detail": diagnostic,
    })

    status, text, _, details = core_agent._resume_set(
        session, pending, _config(),
        {"caminho_origem": str(tmp_path)}, True,
    )

    assert status == "failed"
    assert "NameError: name 'render_template' is not defined" in text
    assert "python -m pytest -q" in text
    assert "Todos os arquivos foram restaurados" in text
    report = details["write_failure"]
    assert report["stage"] == "tests"
    assert report["error_code"] == "TESTS_FAILED"
    assert report["rollback_confirmed"] is True
    assert report["paths"] == ["routes.py", "templates/amor.html"]
    assert routes.read_text(encoding="utf-8") == "def amor():\n    return '<h1>Amor</h1>'\n"
    assert not (tmp_path / "templates" / "amor.html").exists()


def test_service_preserves_write_failure_as_message_metadata():
    failure = {
        "stage": "tests",
        "error_code": "TESTS_FAILED",
        "detail": "1 failed",
        "rollback_confirmed": True,
        "paths": ["routes.py"],
    }

    assert service._metadata_resposta_agente("failed", {"write_failure": failure}) == {
        "agent_status": "failed", "write_failure": failure
    }
    assert service._metadata_resposta_agente("success", {}) == {"agent_status": "success"}



def test_service_carries_write_failure_into_agent_conversation_context(monkeypatch):
    failure = {
        "stage": "tests",
        "error_code": "TESTS_FAILED",
        "detail": "NameError: render_template is not defined",
        "rollback_confirmed": True,
        "paths": ["routes.py"],
    }
    captured = {}

    monkeypatch.setattr(service, "carregar_config", lambda: {})
    monkeypatch.setattr(service, "carregar_projeto", lambda: {"caminho_origem": "/tmp/project"})
    monkeypatch.setattr(service, "carregar_agent_pendente", lambda: None)

    def fake_process(question, config, project, **kwargs):
        captured.update(kwargs.get("conversation_context") or {})
        return {"status": "success", "resposta": "ok", "avisos": [], "details": {}}

    monkeypatch.setattr(service, "_processar_agente", fake_process)
    history = [
        {"id": 1, "role": "assistant", "text": "rollback", "agent_status": "failed", "write_failure": failure},
        {"id": 2, "role": "user", "text": "Por que falhou?"},
    ]

    service.processar("Por que falhou?", registrar_pergunta=False, historico_snapshot=history)

    assert captured["recent_messages"] == [{
        "role": "assistant",
        "content": "rollback",
        "write_failure": failure,
    }]

def test_follow_up_can_cite_runtime_failure_instead_of_restored_code(monkeypatch, tmp_path):
    (tmp_path / "routes.py").write_text(
        "def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8"
    )
    prompts = []

    def fake(prompt, _config_value):
        payload = json.loads(prompt)
        prompts.append(payload)
        runtime_sources = [
            item.get("detail") for item in payload["latest_capability_results"]
            if isinstance(item, dict)
            and isinstance(item.get("detail"), dict)
            and item["detail"].get("source_type") == "runtime_validation"
        ]
        assert runtime_sources
        assert runtime_sources[0]["error_code"] == "TESTS_FAILED"
        assert "render_template" in runtime_sources[0]["content"]
        return agent_final(
            {"answer": "Os testes falharam porque render_template não estava definido.", "grounding_ids": ["mat-0001"]},
            investigation=[{
                "id": "T1", "goal": "Establish why the prior project tests failed",
                "status": "established", "grounding_ids": ["mat-0001"],
                "reason": "Runtime validation Evidence records the test failure."
            }],
        )

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    context = {
        "recent_messages": [{
            "role": "assistant",
            "text": "A verificação por testes falhou e houve rollback.",
            "write_failure": {
                "stage": "tests",
                "error_code": "TESTS_FAILED",
                "detail": "NameError: name 'render_template' is not defined",
                "rollback_confirmed": True,
                "paths": ["routes.py", "templates/amor.html"],
            },
        }]
    }

    status, text, _, details = core_agent.executar_agente(
        "Por que os testes falharam no projeto?",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        conversation_context=context,
    )

    assert status == "success"
    assert "render_template" in text
    assert prompts[0]["current_material"][0]["source_type"] == "runtime_validation"
    assert details["grounding"][0]["source_type"] == "runtime_validation"
