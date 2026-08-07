import json
import shutil
import subprocess

import pytest

import eyle.core.agent as core_agent
import eyle.core.editing as editing
import eyle.core.tools as tools
from eyle.core.git_tools import git_diff, git_status
from eyle.runtime.history import build_public_job_history


def _config(tests_enabled=True):
    return {
        "app_version": "2.7.4",
        "revision": "4.12.1-runtime-tools-observability",
        "llm": {
            "model": "auto",
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1100,
            "agent_patch_max_tokens": 3600,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 6,
            "max_tool_calls": 12,
            "max_identical_tool_repeats": 2,
            "protocol_parse_retries": 1,
            "final_validation_retries": 1,
            "max_patch_dry_run_failures": 2,
            "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2,
            "max_phase_violations": 1,
            "chat_history_token_budget": 700,
            "task_context_token_budget": 500,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "max_git_diff_chars": 6000,
            "response_quality": {"enabled": True},
        },
        "codar": {
            "ativado": True,
            "fazer_backup": False,
            "testes": {
                "ativado": bool(tests_enabled),
                "comando_python": "python -m pytest -q",
                "timeout_segundos": 60,
                "sandbox": {
                    "backend": "auto",
                    "bloquear_rede": True,
                    "comandos_permitidos": [["pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"]],
                    "allow_trusted_local": True,
                },
            },
        },
        "_runtime_agent_budget": {
            "max_llm_calls": 8,
            "max_prompt_tokens": 12000,
            "max_completion_tokens": 6000,
            "max_total_tokens": 18000,
            "llm_calls": 0,
            "llm_requests": 0,
            "prompt_tokens_reserved": 0,
            "prompt_tokens_actual": 0,
            "prompt_tokens_effective": 0,
            "generated_tokens": 0,
        },
    }


def test_calculate_structured_final_finishes_in_two_calls(monkeypatch):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"calculate","arguments":{"expression":"200-12+2"}}'
        result = payload["latest_tool_results"][0]
        assert result["tool"] == "calculate"
        assert result["detail"]["result"] == "190"
        assert result["detail"]["evidence_id"] == "ev-0001"
        return json.dumps({"final": {
            "answer": "200 - 12 + 2 é igual a 190. 😎",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Quanto é 200 - 12 + 2?", _config(), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "190" in text
    assert len(prompts) == 2
    assert details["turns"] == 2
    assert [item["decision"] for item in details["decision_history"]] == ["tool", "final"]
    assert all(item["outcome"] == "accepted" for item in details["decision_history"])
    assert details["evidence"][0]["source_type"] == "calculate"


def test_history_exposes_decision_rejection_reason_without_raw_content():
    registro = {
        "id": 9,
        "status": "completed",
        "resultado": {"details": {
            "turns": 3,
            "tool_calls": 1,
            "runtime_phase": "chat",
            "decision_history": [
                {"turn": 1, "phase": "chat", "decision": "tool", "outcome": "accepted", "tools": ["calculate"]},
                {"turn": 2, "phase": "chat", "decision": "final", "outcome": "rejected", "reason": "FINAL_UNKNOWN_EVIDENCE:ev-9999"},
                {"turn": 3, "phase": "chat", "decision": "final", "outcome": "accepted"},
            ],
        }},
    }
    history = build_public_job_history(registro)
    assert [item["outcome"] for item in history["decisions"]] == ["accepted", "rejected", "accepted"]
    assert history["decisions"][1]["reason"] == "FINAL_UNKNOWN_EVIDENCE:ev-9999"
    assert history["privacy"]["raw_prompts_exposed"] is False
    assert history["privacy"]["raw_model_responses_exposed"] is False


def test_scoped_run_tests_appends_safe_pytest_scope(monkeypatch, tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_one.py").write_text("def test_one():\n    assert True\n", encoding="utf-8")

    monkeypatch.setattr(editing, "_detectar_comando_teste", lambda root, cfg: "python -m pytest -q")
    captured = {}

    def fake_sandbox(root, argv, cfg):
        captured["argv"] = list(argv)
        return {"executado": True, "ok": True, "codigo": 0, "saida": "1 passed in 0.01s", "backend": "test"}

    monkeypatch.setattr(editing, "executar_no_sandbox", fake_sandbox)
    result = editing.rodar_testes_projeto(str(tmp_path), _config()["codar"]["testes"], scope="tests/test_one.py")
    assert result["ok"] is True
    assert captured["argv"][-1] == "tests/test_one.py"
    assert result["scope"] == "tests/test_one.py"


def test_run_tests_tool_returns_compact_structured_result(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "rodar_testes_projeto", lambda root, cfg, scope=None: {
        "executado": True,
        "ok": False,
        "detalhe": "falhou",
        "comando": "python -m pytest -q tests/test_x.py",
        "codigo": 1,
        "saida_resumida": "FAILED tests/test_x.py::test_x - AssertionError\n1 failed in 0.03s",
        "backend": "sandbox",
        "scope": scope,
        "tests_detected": True,
    })
    result = tools.executar_tool(
        "run_tests", {"scope": "tests/test_x.py"},
        {"projeto": {"caminho_origem": str(tmp_path)}, "config": _config()},
    )
    assert result["ok"] is False
    assert result["error_code"] == "TESTS_FAILED"
    assert result["detail"]["summary"] == "1 failed in 0.03s"
    assert len(result["detail"]["output_tail"]) < 3100


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_git_status_and_diff_are_read_only_and_compact(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Eyle Test"], cwd=tmp_path, check=True)
    app = tmp_path / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    app.write_text("x = 2\ny = 3\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("local\n", encoding="utf-8")

    status = git_status(str(tmp_path))
    assert status["ok"] is True
    assert status["clean"] is False
    assert any(item["path"] == "app.py" and item["category"] == "modified" for item in status["entries"])
    assert any(item["path"] == "notes.txt" and item["category"] == "untracked" for item in status["entries"])

    diff = git_diff(str(tmp_path), path="app.py", max_chars=2000)
    assert diff["ok"] is True
    assert diff["file_count"] == 1
    assert diff["added_lines"] == 2
    assert diff["removed_lines"] == 1
    assert "+y = 3" in diff["diff"]


def test_analysis_catalog_exposes_tests_and_git_when_available(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    seen = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        seen.append(payload)
        names = {item["name"] for item in payload["available_tools"]}
        assert {"run_tests", "git_status", "git_diff"} <= names
        return '{"final":"Sem necessidade de executar ferramentas neste teste."}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, *_ = core_agent.executar_agente(
        "Analise o projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    # A resposta sem leitura deve ser rejeitada pela qualidade; o ponto aqui é o catálogo.
    assert seen
    assert status in {"failed", "success"}


def test_failed_run_tests_becomes_runtime_evidence(monkeypatch, tmp_path):
    prompts = []

    def fake_tool(name, arguments, ctx):
        if name == "run_tests":
            return {
                "status": "failed", "ok": False, "executed": True, "changed": False,
                "error_code": "TESTS_FAILED",
                "detail": {
                    "command": "python -m pytest -q", "returncode": 1,
                    "scope": None, "backend": "sandbox", "tests_detected": True,
                    "summary": "1 failed in 0.03s", "output_tail": "AssertionError: expected 2 got 1",
                },
            }
        return tools.executar_tool(name, arguments, ctx)

    def fake_llm(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"run_tests","arguments":{}}'
        result = payload["latest_tool_results"][0]
        assert result["evidence_ids"] == ["ev-0001"]
        assert result["detail"]["evidence_id"] == "ev-0001"
        return json.dumps({"final": {
            "answer": "A suíte executada falhou com 1 teste.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_tool", fake_tool)
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake_llm)
    status, text, _, details = core_agent.executar_agente(
        "Execute os testes e diga se passam", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "falhou" in text
    assert details["evidence"][0]["source_type"] == "run_tests"
