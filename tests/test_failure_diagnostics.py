import hashlib
import json

import eyle.core.agent as core_agent
from eyle.core.session import AgentSession
from eyle.runtime import service


def _config(tests_enabled=True):
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1400,
            "agent_patch_max_tokens": 4200,
        },
        "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3},
        "agent": {
            "max_llm_turns": 4,
            "max_tool_calls": 8,
            "max_identical_tool_repeats": 2,
            "structured_protocol_retries": 1,
            "final_validation_retries": 1,
            "chat_history_token_budget": 2400,
            "max_read_range_lines": 400,
            "claims": {"mode": "off"},
            "context_view": {"max_relevant_sources": 4, "max_relevant_source_chars": 3500, "max_symbol_preview_chars": 2600, "max_search_source_chars": 600},
        },
        "codar": {
            "ativado": True,
            "testes": {"ativado": tests_enabled, "timeout_segundos": 30},
        },
        "_runtime_agent_budget": {
            "max_llm_calls": 10,
            "max_prompt_tokens": 12000,
            "max_completion_tokens": 6000,
            "max_total_tokens": 18000,
            "llm_calls": 0,
            "llm_requests": 0,
        },
    }


def _pending_replace_and_create(root):
    original = (root / "routes.py").read_text(encoding="utf-8")
    return {
        "continuation_kind": "write_confirmation",
        "write_transaction": {
            "patches": [
                    {
                        "operation": "replace",
                        "path": "routes.py",
                        "content": "def amor():\n    return render_template('amor.html')\n",
                        "file_hash_expected": hashlib.sha256(original.encode()).hexdigest(),
                    },
                    {
                        "operation": "create",
                        "path": "templates/amor.html",
                        "content": "<h1>Amor</h1>\n",
                    },
                ]
        }
    }


def test_failed_tests_expose_exact_output_and_structured_report(monkeypatch, tmp_path):
    routes = tmp_path / "routes.py"
    routes.write_text("def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8")
    pending = _pending_replace_and_create(tmp_path)
    diagnostic = (
        "'python -m pytest -q' falhou no sandbox (codigo 1).\n"
        "E   NameError: name 'render_template' is not defined\n"
        "1 failed, 2 passed"
    )
    monkeypatch.setattr(core_agent, "_run_tests_after_write", lambda *_: {
        "status": "failed",
        "ok": False,
        "executed": True,
        "error_code": "TESTS_FAILED",
        "detail": diagnostic,
    })

    status, text, _, details = core_agent._resume_set(
        AgentSession("mova o html"), pending, _config(),
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


def test_follow_up_can_cite_runtime_failure_instead_of_restored_code(monkeypatch, tmp_path):
    (tmp_path / "routes.py").write_text(
        "def amor():\n    return '<h1>Amor</h1>'\n", encoding="utf-8"
    )
    prompts = []

    def fake(prompt, _config_value):
        payload = json.loads(prompt)
        prompts.append(payload)
        runtime_sources = [
            source for source in payload["relevant_sources"]
            if source.get("source_type") == "runtime_validation"
        ]
        assert runtime_sources
        assert runtime_sources[0]["error_code"] == "TESTS_FAILED"
        return {
            "final": {
                "answer": "Os testes falharam porque render_template não estava definido.",
                "evidence_ids": ["ev-runtime-0001"],
            },
            "workspace_scope": {"mode": "read", "reason": "The answer depends on persisted runtime validation Evidence."},
            "investigation": [{
                "id": "T1", "goal": "Establish why the prior project tests failed",
                "status": "established", "evidence_ids": ["ev-runtime-0001"],
                "reason": "Runtime validation Evidence records the test failure."
            }],
        }

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
    assert prompts[0]["evidence_index"][0]["source_type"] == "runtime_validation"
    assert details["evidence"][0]["source_type"] == "runtime_validation"
