from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

from eyle.core.memory import apply_memory_sidecar
from eyle.core.session import AgentSession
from eyle.providers.standard import sandbox_promotion
from eyle.providers.standard.editing import _escrever_arquivo_atomico
from eyle.runtime.execution_context import ExecutionContext, bind_execution, reset_execution
from tests.canonical import base_config, standard_registry


def _provider_ctx(workspace: Path, eyle_root: Path) -> dict:
    return {
        "provider_context": {
            "standard": {"caminho_origem": str(workspace), "eyle_root": str(eyle_root)},
        },
        "config": base_config(),
    }


def _memory_ctx(workspace: Path) -> dict:
    return {
        "standard": {"caminho_origem": str(workspace), "eyle_root": str(workspace.parent)},
        "core_memory": {"storage_dir": str(workspace.parent / "memdb"), "world_scope_id": f"workspace:{workspace.resolve()}"},
    }


def test_rev32_atomic_writer_is_byte_stable_for_crlf(tmp_path):
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"old\r\n")
    content = "one\r\ntwo\r\n"
    _escrever_arquivo_atomico(str(target), content)
    assert target.read_bytes() == content.encode("utf-8")
    assert b"\r\r\n" not in target.read_bytes()


def test_rev32_sandbox_project_promotes_once_from_durable_stage(tmp_path):
    eyle_root = tmp_path / "eyle"
    workspace = tmp_path / "workspace"
    snapshot = tmp_path / "snapshot"
    for p in (eyle_root, workspace, snapshot): p.mkdir()
    (workspace / "app.py").write_text("old\n", encoding="utf-8")
    candidate = snapshot / "candidate"
    candidate.mkdir()
    (candidate / "app.py").write_text("new\n", encoding="utf-8")
    (candidate / "data.bin").write_bytes(b"\x00\x01binary\xff")

    execution = ExecutionContext.from_config(base_config(), execution_id="promote")
    execution.provider_state_for("standard.sandbox").update({"workspace_path": str(snapshot), "source_kind": "workspace", "backend": "docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path": "candidate", "workspace_path": ".", "mode": "merge"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is True
    state = prepared["state"]
    assert state["kind"] == "project"
    assert state["summary"]["replaces"] == 1
    assert state["summary"]["creates"] == 1
    assert "staged_files" not in state and "expected_workspace_hashes" not in state

    # Confirmation no longer depends on the live sandbox/provider state.
    shutil.rmtree(snapshot)
    applied = sandbox_promotion.confirm(state, _provider_ctx(workspace, eyle_root))
    assert applied["ok"] is True
    assert applied["detail"]["verification_state"] == "promoted_exact"
    assert (workspace / "app.py").read_text(encoding="utf-8") == "new\n"
    assert (workspace / "data.bin").read_bytes() == b"\x00\x01binary\xff"


def test_rev32_sandbox_single_file_can_be_promoted_to_named_workspace_path(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    (snapshot / "report.bin").write_bytes(b"report\x00bytes")
    execution = ExecutionContext.from_config(base_config(), execution_id="single")
    execution.provider_state_for("standard.sandbox").update({"workspace_path": str(snapshot), "source_kind": "workspace", "backend": "docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path": "report.bin", "workspace_path": "artifacts/final.bin"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is True and prepared["state"]["kind"] == "file"
    applied = sandbox_promotion.confirm(prepared["state"], _provider_ctx(workspace, eyle_root))
    assert applied["ok"] is True
    assert (workspace / "artifacts" / "final.bin").read_bytes() == b"report\x00bytes"


def test_rev32_sandbox_promotion_fails_closed_if_workspace_changed_after_prepare(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    (workspace / "a.txt").write_text("old", encoding="utf-8")
    (snapshot / "a.txt").write_text("candidate", encoding="utf-8")
    execution = ExecutionContext.from_config(base_config(), execution_id="stale")
    execution.provider_state_for("standard.sandbox").update({"workspace_path": str(snapshot), "source_kind": "workspace", "backend": "docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path": ".", "workspace_path": "."}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    (workspace / "a.txt").write_text("human change", encoding="utf-8")
    applied = sandbox_promotion.confirm(prepared["state"], _provider_ctx(workspace, eyle_root))
    assert applied["ok"] is False
    assert applied["error_code"] == "SANDBOX_PROMOTION_FAILED"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "human change"


def test_rev32_delete_button_has_real_client_implementation():
    source = (Path(__file__).parents[1] / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "async function deleteMessage(messageId)" in source
    assert 'apiFetch(`/mensagem/${id}`, { method: "DELETE" })' in source


def test_rev32_sandbox_mirror_deletes_only_absent_files_inside_target(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    target = workspace / "app"; target.mkdir()
    (target / "keep.txt").write_text("old", encoding="utf-8")
    (target / "remove.txt").write_text("remove", encoding="utf-8")
    (workspace / "outside.txt").write_text("outside", encoding="utf-8")
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    candidate = snapshot / "candidate"; candidate.mkdir()
    (candidate / "keep.txt").write_text("new", encoding="utf-8")
    execution = ExecutionContext.from_config(base_config(), execution_id="mirror")
    execution.provider_state_for("standard.sandbox").update({"workspace_path": str(snapshot), "source_kind":"workspace", "backend":"docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path":"candidate", "workspace_path":"app", "mode":"mirror"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is True
    assert prepared["state"]["summary"]["deletes"] == 1
    assert "app/remove.txt" in prepared["state"]["summary"]["preview"]
    applied = sandbox_promotion.confirm(prepared["state"], _provider_ctx(workspace, eyle_root))
    assert applied["ok"] is True
    assert (target / "keep.txt").read_text(encoding="utf-8") == "new"
    assert not (target / "remove.txt").exists()
    assert (workspace / "outside.txt").read_text(encoding="utf-8") == "outside"


def test_rev32_eyle_rejects_adapter_without_completion_ceiling():
    import llm.executar as llm_mod
    body = {
        "handshake_schema": llm_mod.ADAPTER_HANDSHAKE_SCHEMA,
        "adapter_protocol": llm_mod.ADAPTER_TRANSPORT_PROTOCOL,
        "authority":"transport-only", "semantic_protocol":"client-owned",
        "capabilities": {
            "chat_completions":True, "client_json_schema_hint":True,
            "json_candidate_passthrough":True, "syntactic_json_recovery":True,
        },
        "endpoints":{"chat_completions":"/v1/chat/completions", "readiness":"/ready"},
    }
    import pytest
    with pytest.raises(ValueError, match="ADAPTER_REQUIRED_CAPABILITY_MISSING"):
        llm_mod._validate_adapter_handshake(body)


def test_rev32_standard_guidance_prefers_contract_then_sandbox_promotion():
    from eyle.providers.standard.registry import get_provider
    guidance = "\n".join(get_provider().ecc_guidance).lower()
    assert "do not inspect eyle internals as the first response" in guidance
    assert "promote_sandbox" in guidance
    assert "one user confirmation" in guidance


def test_rev32_transaction_preserves_exact_crlf_bytes(tmp_path):
    from eyle.providers.standard import transactions
    from eyle.providers.standard.text_hash import hash_texto
    target = tmp_path / "calc.py"
    before = "a = 1\r\n"
    after = "a = 2\r\nb = 3\r\n"
    target.write_bytes(before.encode("utf-8"))
    result = transactions.apply_patch_set(str(tmp_path), [{
        "operation":"replace", "path":"calc.py", "content":after,
        "file_hash_expected":hash_texto(before),
    }])
    assert result["ok"] is True
    assert target.read_bytes() == after.encode("utf-8")
    assert b"\r\r\n" not in target.read_bytes()


def test_rev32_large_project_pending_state_stays_compact(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    candidate = snapshot / "candidate"; candidate.mkdir()
    for index in range(250):
        path = candidate / f"pkg/file_{index:04d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"value {index}\n", encoding="utf-8")
    execution = ExecutionContext.from_config(base_config(), execution_id="large-stage")
    execution.provider_state_for("standard.sandbox").update({"workspace_path": str(snapshot), "source_kind":"workspace", "backend":"docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path":"candidate", "workspace_path":".", "mode":"merge"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is True
    state = prepared["state"]
    assert state["summary"]["files"] == 250
    assert len(json.dumps(state, ensure_ascii=False)) < 3000
    assert "staged_files" not in state and "expected_workspace_hashes" not in state


def test_rev32_rejecting_promotion_releases_private_stage(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    (snapshot / "candidate.txt").write_text("candidate", encoding="utf-8")
    execution = ExecutionContext.from_config(base_config(), execution_id="cancel-stage")
    execution.provider_state_for("standard.sandbox").update({"workspace_path":str(snapshot), "source_kind":"workspace", "backend":"docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path":"candidate.txt", "workspace_path":"candidate.txt"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is True
    state = prepared["state"]
    manifest = eyle_root / Path(state["stage_manifest"])
    assert manifest.is_file()
    registry = standard_registry()
    cleaned = registry.cancel_confirmation(
        "standard.promote_sandbox", state,
        {"provider_context": _provider_ctx(workspace, eyle_root)["provider_context"], "config": base_config()},
    )
    assert cleaned["ok"] is True
    assert not manifest.exists()
    assert not any((eyle_root / "context" / "sandbox_promotions").glob("promotion-*"))


def test_rev32_promotion_refuses_protected_workspace_resource_and_cleans_stage(tmp_path):
    eyle_root = tmp_path / "eyle"; eyle_root.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir()
    snapshot = tmp_path / "snapshot"; snapshot.mkdir()
    (snapshot / ".env").write_text("SECRET=value", encoding="utf-8")
    execution = ExecutionContext.from_config(base_config(), execution_id="protected-stage")
    execution.provider_state_for("standard.sandbox").update({"workspace_path":str(snapshot), "source_kind":"workspace", "backend":"docker"})
    token = bind_execution(execution)
    try:
        prepared = sandbox_promotion.prepare({"sandbox_path":".env", "workspace_path":".env"}, _provider_ctx(workspace, eyle_root))
    finally:
        reset_execution(token)
    assert prepared["ok"] is False
    assert prepared["error"]["error_code"] == "SANDBOX_PROMOTION_PREPARE_FAILED"
    staging = eyle_root / "context" / "sandbox_promotions"
    assert not list(staging.glob("promotion-*"))
    assert not (workspace / ".env").exists()
