import hashlib

import eyle.core.agent as core_agent
import eyle.core.editing as editing
from eyle.core.post_write import (
    expected_outputs_from_patches,
    run_compileall_for_changes,
    verify_expected_outputs,
)
from eyle.core.session import AgentSession


def _config(tests_enabled=False):
    return {
        "agent": {"max_read_range_lines": 400},
        "codar": {
            "ativado": True,
            "testes": {"ativado": tests_enabled, "timeout_segundos": 30},
        },
    }


def _pending_replace_and_create(root, replacement="VALUE = 2\n"):
    original = (root / "app.py").read_text(encoding="utf-8")
    return {
        "continuation_kind": "write_confirmation",
        "write_transaction": {
            "patches": [
                    {
                        "operation": "replace",
                        "path": "app.py",
                        "content": replacement,
                        "file_hash_expected": hashlib.sha256(original.encode()).hexdigest(),
                    },
                    {
                        "operation": "create",
                        "path": "tests/test_created.py",
                        "content": "def test_created():\n    assert True\n",
                    },
                ]
        }
    }


def test_new_pytest_file_is_detected_without_root_marker(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_created.py").write_text("def test_created():\n    assert True\n", encoding="utf-8")

    command = editing._detectar_comando_teste(str(tmp_path), {
        "ativado": True,
        "comando_python": "python -m pytest -q",
    })

    assert command == "python -m pytest -q"


def test_compileall_runs_for_changed_python_files(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = run_compileall_for_changes(str(tmp_path), ["app.py"], timeout_seconds=30)

    assert result["required"] is True
    assert result["executed"] is True
    assert result["ok"] is True
    assert result["files"] == ["app.py"]
    assert not (tmp_path / "__pycache__").exists()


def test_compileall_reports_invalid_written_python(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    result = run_compileall_for_changes(str(tmp_path), ["broken.py"], timeout_seconds=30)

    assert result["executed"] is True
    assert result["ok"] is False
    assert result["error_code"] == "COMPILEALL_FAILED"


def test_failed_tests_roll_back_whole_transaction(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    pending = _pending_replace_and_create(tmp_path)
    monkeypatch.setattr(core_agent, "_run_tests_after_write", lambda *_: {
        "status": "failed", "ok": False, "executed": True,
        "error_code": "TESTS_FAILED", "detail": "1 failed",
    })

    status, text, _, details = core_agent._resume_set(
        AgentSession("mude"), pending, _config(tests_enabled=True),
        {"caminho_origem": str(tmp_path)}, True,
    )

    assert status == "failed"
    assert "restaurados" in text
    assert details["failure_code"] == "TESTS_FAILED_ROLLED_BACK"
    assert app.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (tmp_path / "tests" / "test_created.py").exists()


def test_compileall_failure_rolls_back_whole_transaction(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    pending = _pending_replace_and_create(tmp_path)
    monkeypatch.setattr(core_agent, "_compile_after_write", lambda *_: {
        "required": True, "executed": True, "ok": False,
        "error_code": "COMPILEALL_FAILED", "detail": "syntax error",
    })

    status, _, _, details = core_agent._resume_set(
        AgentSession("mude"), pending, _config(),
        {"caminho_origem": str(tmp_path)}, True,
    )

    assert status == "failed"
    assert details["failure_code"] == "COMPILEALL_FAILED_ROLLED_BACK"
    assert app.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (tmp_path / "tests" / "test_created.py").exists()


def test_no_tests_means_partial_validation_not_verified(tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    original = app.read_text(encoding="utf-8")
    pending = {
        "continuation_kind": "write_confirmation",
        "write_transaction": {"patches": [{
                "operation": "replace",
                "path": "app.py",
                "content": "VALUE = 2\n",
                "file_hash_expected": hashlib.sha256(original.encode()).hexdigest(),
            }]}
    }

    status, text, _, details = core_agent._resume_set(
        AgentSession("mude"), pending, _config(tests_enabled=False),
        {"caminho_origem": str(tmp_path)}, True,
    )

    assert status == "success"
    assert "validação parcial" in text
    assert "Estado: transação verificada" not in text
    assert details["limitations"]
    assert app.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_reread_confirms_created_and_deleted_outputs(tmp_path):
    created = tmp_path / "new.txt"
    created.write_text("ok", encoding="utf-8")
    patches = [
        {"operation": "create", "path": "new.txt", "result_content": "ok"},
        {"operation": "delete", "path": "old.txt", "result_content": None},
    ]

    result = verify_expected_outputs(str(tmp_path), expected_outputs_from_patches(patches))

    assert result["ok"] is True
    assert {item["path"] for item in result["checked"]} == {"new.txt", "old.txt"}


def test_newly_created_pytest_suite_is_actually_invoked(monkeypatch, tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_created.py").write_text("def test_created():\n    assert True\n", encoding="utf-8")
    calls = []

    def fake_sandbox(root, command, config):
        calls.append((root, command, config))
        return {"executado": True, "ok": True, "codigo": 0, "saida": "1 passed", "backend": "fake"}

    monkeypatch.setattr(editing, "executar_no_sandbox", fake_sandbox)
    result = editing.rodar_testes_projeto(str(tmp_path), {
        "ativado": True,
        "comando_python": "python -m pytest -q",
        "sandbox": {"comandos_permitidos": [["python", "-m", "pytest"]]},
    })

    assert result["executado"] is True
    assert result["ok"] is True
    assert calls and calls[0][1] == ["python", "-m", "pytest", "-q"]


def test_generic_pyproject_does_not_fake_a_pytest_suite(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    command = editing._detectar_comando_teste(str(tmp_path), {
        "ativado": True,
        "comando_python": "python -m pytest -q",
    })

    assert command is None
