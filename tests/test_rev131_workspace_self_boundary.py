import os
import zipfile
from pathlib import Path

import pytest

from eyle.core.execution_context import ExecutionContext, bind_execution, reset_execution
from eyle.core.sandbox import _copiar_projeto, export_active_sandbox_zip, ErroSandbox
from eyle.core.tools import (
    TOOLS,
    capability_observation_signature,
    capability_validate_material_freshness,
)
from eyle.core.workspace import discover_project
from llm.executar import PROMPT_AGENTE


def _ctx(workspace: Path, eyle_root: Path):
    return {
        "projeto": {
            "caminho_origem": str(workspace),
            "eyle_root": str(eyle_root),
            "content_state": "empty",
        },
        "config": {"agent": {"max_file_read_lines": 400}},
        "workspace_epoch": 0,
        "observation_ledger": {"handles": {}},
    }


def test_empty_dedicated_workspace_never_falls_back_to_eyle_installation(tmp_path):
    (tmp_path / "eyle").mkdir()
    (tmp_path / "main.py").write_text("print('control-plane')\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".gitkeep").write_text("", encoding="utf-8")

    project = discover_project(str(tmp_path))

    assert project["caminho_origem"] == os.path.realpath(workspace)
    assert project["eyle_root"] == os.path.realpath(tmp_path)
    assert project["discovery"] == "workspace"
    assert project["content_state"] == "empty"


def test_nonempty_workspace_remains_the_only_real_work_plane(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('user')\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('eyle')\n", encoding="utf-8")

    project = discover_project(str(tmp_path))

    assert project["caminho_origem"] == os.path.realpath(workspace)
    assert project["content_state"] == "nonempty"
    assert project["caminho_origem"] != project["eyle_root"]


def test_read_file_can_observe_eyle_without_confusing_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "main.py").write_text("print('eyle')\n", encoding="utf-8")
    ctx = _ctx(workspace, tmp_path)

    from_self = TOOLS["read_file"]["fn"]({"source": "eyle", "path": "main.py"}, ctx)
    from_workspace = TOOLS["read_file"]["fn"]({"source": "workspace", "path": "main.py"}, ctx)

    assert from_self["ok"] is True
    assert "eyle" in from_self["detail"]["content"]
    assert from_workspace["ok"] is False


def test_self_analysis_blocks_live_runtime_state_content(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "conversation.json").write_text('{"secret":"live"}', encoding="utf-8")
    ctx = _ctx(workspace, tmp_path)

    result = TOOLS["read_file"]["fn"]({"source": "eyle", "path": "memory/conversation.json"}, ctx)

    assert result["ok"] is False
    assert result["error_code"] == "SELF_RUNTIME_STATE_READ_BLOCKED"


def test_observation_identity_distinguishes_workspace_from_self_source():
    a = capability_observation_signature("read_file", {"source": "workspace", "path": "main.py", "line_start": 1, "line_end": 2})
    b = capability_observation_signature("read_file", {"source": "eyle", "path": "main.py", "line_start": 1, "line_end": 2})
    assert a != b
    assert "workspace" in a and "eyle" in b


def test_self_material_freshness_uses_recorded_source_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("workspace\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("self\n", encoding="utf-8")
    ctx = _ctx(workspace, tmp_path)
    raw = TOOLS["read_file"]["fn"]({"source": "eyle", "path": "main.py"}, ctx)
    material = TOOLS["read_file"]["observe"]({"source": "eyle", "path": "main.py"}, raw)[0]
    material["id"] = "mat-1"
    material["source_capability"] = "read_file"

    ok, reason = capability_validate_material_freshness(
        {"mat-1": material}, ["mat-1"],
        {"workspace": str(workspace), "eyle": str(tmp_path)},
    )
    assert (ok, reason) == (True, "ok")

    (tmp_path / "main.py").write_text("self changed\n", encoding="utf-8")
    ok, reason = capability_validate_material_freshness(
        {"mat-1": material}, ["mat-1"],
        {"workspace": str(workspace), "eyle": str(tmp_path)},
    )
    assert ok is False and reason.startswith("GROUNDING_STALE:mat-1")


def test_self_sandbox_snapshot_omits_live_runtime_state_but_keeps_source(tmp_path):
    root = tmp_path / "eyle-root"
    root.mkdir()
    (root / "main.py").write_text("print('source')\n", encoding="utf-8")
    for dirname in ("workspace", "memory", "context", "agent_memory"):
        directory = root / dirname
        directory.mkdir()
        (directory / ".gitkeep").write_text("", encoding="utf-8")
        (directory / "live.txt").write_text("do not copy", encoding="utf-8")
    git = root / ".git"
    git.mkdir()
    (git / "config").write_text("secret-ish", encoding="utf-8")

    snapshot, temp = _copiar_projeto(
        str(root),
        {"arquivos_projeto": 200, "tamanho_projeto_mb": 10},
        source_kind="eyle",
    )
    try:
        snap = Path(snapshot)
        assert (snap / "main.py").read_text(encoding="utf-8") == "print('source')\n"
        assert not (snap / ".git").exists()
        for dirname in ("workspace", "memory", "context", "agent_memory"):
            assert (snap / dirname / ".gitkeep").exists()
            assert not (snap / dirname / "live.txt").exists()
    finally:
        temp.cleanup()


def test_export_packages_only_active_snapshot_and_never_overwrites(tmp_path):
    eyle_root = tmp_path / "eyle-root"
    eyle_root.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "main.py").write_text("print('candidate')\n", encoding="utf-8")
    cache = snapshot / "__pycache__"
    cache.mkdir()
    (cache / "main.pyc").write_bytes(b"junk")

    execution = ExecutionContext.from_config({}, execution_id="test")
    execution.sandbox_workspace_path = str(snapshot)
    execution.sandbox_source_kind = "eyle"
    execution.sandbox_backend = "docker"
    token = bind_execution(execution)
    try:
        result = export_active_sandbox_zip(str(eyle_root), "candidate.zip", archive_root="Candidate")
        assert result["real_source_modified"] is False
        artifact = eyle_root / "candidate.zip"
        assert artifact.exists()
        with zipfile.ZipFile(artifact) as zf:
            names = zf.namelist()
        assert "Candidate/main.py" in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        with pytest.raises(ErroSandbox, match="ARTIFACT_ALREADY_EXISTS"):
            export_active_sandbox_zip(str(eyle_root), "candidate.zip")
    finally:
        reset_execution(token)


def test_prompt_keeps_self_boundary_without_turning_workspace_into_a_task():
    lowered = PROMPT_AGENTE.lower()
    assert "source=eyle" in lowered
    assert "isolated sandbox" in lowered
    assert "real workspace writes use patch transactions" in lowered
    assert "workspace state is context, not a task" in lowered
    assert "trivial single-step" not in lowered


def test_run_command_source_conflict_is_request_scoped_not_terminal(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = _ctx(workspace, tmp_path)

    monkeypatch.setattr(
        "eyle.core.tools.executar_comando_livre_no_sandbox",
        lambda *args, **kwargs: {
            "executado": False,
            "ok": False,
            "erro": "SANDBOX_SOURCE_CONFLICT: active=workspace; requested=eyle",
        },
    )
    result = TOOLS["run_command"]["fn"](
        {"source": "eyle", "command": "echo test"}, ctx
    )
    assert result["error_code"] == "SANDBOX_SOURCE_CONFLICT"
    assert result["failure_scope"] == "request"


def test_export_before_sandbox_is_recoverable_ordering_error(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = _ctx(workspace, tmp_path)
    execution = ExecutionContext.from_config({}, execution_id="test")
    token = bind_execution(execution)
    try:
        result = TOOLS["export_sandbox_zip"]["fn"]({"filename": "candidate.zip"}, ctx)
    finally:
        reset_execution(token)
    assert result["ok"] is False
    assert result["error_code"] == "SANDBOX_NOT_INITIALIZED"
    assert result["failure_scope"] == "request"
