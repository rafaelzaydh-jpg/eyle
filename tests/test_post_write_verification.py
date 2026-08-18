import hashlib

import eyle.providers.standard.editing as editing
from eyle.providers.standard import workspace_transaction as workspace_tx
from tests.canonical import standard_registry
from eyle.providers.standard.post_write import expected_outputs_from_patches, run_compileall_for_changes, verify_expected_outputs
from tests.canonical import base_config


def _state(root, replacement="VALUE = 2\n"):
    original = (root / "app.py").read_text(encoding="utf-8")
    return {"patches": [
        {"operation":"replace","path":"app.py","content":replacement,"file_hash_expected":hashlib.sha256(original.encode()).hexdigest()},
        {"operation":"create","path":"tests/test_created.py","content":"def test_created():\n    assert True\n"},
    ]}


def _ctx(root, *, tests_enabled=False):
    return {"provider_context":{"standard":{"caminho_origem":str(root)}}, "config":base_config(tests_enabled=tests_enabled), "registry":standard_registry()}


def test_new_pytest_file_is_detected_without_root_marker(tmp_path):
    tests=tmp_path/"tests"; tests.mkdir(); (tests/"test_created.py").write_text("def test_created():\n    assert True\n",encoding="utf-8")
    assert editing._detectar_comando_teste(str(tmp_path), {"enabled":True,"command_python":"python -m pytest -q"}) == "python -m pytest -q"


def test_compileall_runs_for_changed_python_files(tmp_path):
    (tmp_path/"app.py").write_text("VALUE = 1\n",encoding="utf-8")
    result=run_compileall_for_changes(str(tmp_path),["app.py"],timeout_seconds=30)
    assert result["required"] is True and result["executed"] is True and result["ok"] is True
    assert result["files"] == ["app.py"] and not (tmp_path/"__pycache__").exists()


def test_compileall_reports_invalid_written_python(tmp_path):
    (tmp_path/"broken.py").write_text("def broken(:\n",encoding="utf-8")
    result=run_compileall_for_changes(str(tmp_path),["broken.py"],timeout_seconds=30)
    assert result["executed"] is True and result["ok"] is False and result["error_code"] == "COMPILEALL_FAILED"


def test_failed_tests_roll_back_whole_transaction(monkeypatch,tmp_path):
    app=tmp_path/"app.py"; app.write_text("VALUE = 1\n",encoding="utf-8")
    monkeypatch.setattr(workspace_tx,"_run_tests",lambda ctx:{"status":"failed","ok":False,"executed":True,"changed":False,"error_code":"TESTS_FAILED","detail":"1 failed"})
    result=workspace_tx.confirm(_state(tmp_path),_ctx(tmp_path,tests_enabled=True))
    assert result["ok"] is False and result["error_code"] == "TESTS_FAILED"
    assert app.read_text(encoding="utf-8") == "VALUE = 1\n" and not (tmp_path/"tests"/"test_created.py").exists()
    assert result["detail"]["rollback"]["ok"] is True


def test_compileall_failure_rolls_back_whole_transaction(monkeypatch,tmp_path):
    app=tmp_path/"app.py"; app.write_text("VALUE = 1\n",encoding="utf-8")
    monkeypatch.setattr(workspace_tx,"run_compileall_for_changes",lambda *a,**k:{"required":True,"executed":True,"ok":False,"error_code":"COMPILEALL_FAILED","detail":"syntax error"})
    result=workspace_tx.confirm(_state(tmp_path),_ctx(tmp_path))
    assert result["ok"] is False and result["error_code"] == "COMPILEALL_FAILED"
    assert app.read_text(encoding="utf-8") == "VALUE = 1\n" and not (tmp_path/"tests"/"test_created.py").exists()


def test_no_tests_means_partial_validation_not_verified(tmp_path):
    app=tmp_path/"app.py"; app.write_text("VALUE = 1\n",encoding="utf-8")
    from eyle.providers.standard.text_hash import hash_texto
    state={"patches":[{"operation":"replace","path":"app.py","content":"VALUE = 2\n","file_hash_expected":hash_texto(app.read_text(encoding="utf-8"))}]}
    result=workspace_tx.confirm(state,_ctx(tmp_path,tests_enabled=False))
    assert result["ok"] is True and result["detail"]["verification_state"] == "applied_partial"
    assert result["detail"]["limitations"] and app.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_reread_confirms_created_and_deleted_outputs(tmp_path):
    (tmp_path/"new.txt").write_text("ok",encoding="utf-8")
    result=verify_expected_outputs(str(tmp_path),expected_outputs_from_patches([{"operation":"create","path":"new.txt","result_content":"ok"},{"operation":"delete","path":"old.txt","result_content":None}]))
    assert result["ok"] is True and {item["path"] for item in result["checked"]} == {"new.txt","old.txt"}


def test_newly_created_pytest_suite_is_actually_invoked(monkeypatch,tmp_path):
    tests=tmp_path/"tests"; tests.mkdir(); (tests/"test_created.py").write_text("def test_created():\n    assert True\n",encoding="utf-8")
    calls=[]
    monkeypatch.setattr(editing,"executar_no_sandbox",lambda root,command,config:(calls.append((root,command,config)) or {"executado":True,"ok":True,"codigo":0,"saida":"1 passed","backend":"fake"}))
    result=editing.rodar_testes_projeto(str(tmp_path),{"enabled":True,"command_python":"python -m pytest -q","sandbox":{"comandos_permitidos":[["python","-m","pytest"]]}})
    assert result["executado"] is True and result["ok"] is True and calls[0][1] == ["python","-m","pytest","-q"]


def test_generic_pyproject_does_not_fake_a_pytest_suite(tmp_path):
    (tmp_path/"pyproject.toml").write_text("[project]\nname = 'demo'\n",encoding="utf-8")
    assert editing._detectar_comando_teste(str(tmp_path),{"enabled":True,"command_python":"python -m pytest -q"}) is None
