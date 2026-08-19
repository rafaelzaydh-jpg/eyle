
from __future__ import annotations

import copy
from pathlib import Path

import pytest

import eyle.runtime.service as service
from eyle.capabilities import build_registry
from eyle.core.session import AgentSession
from eyle.providers.standard.registry import get_provider as get_standard_provider
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.runtime.ecc_runtime import dispatch
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.observation import mechanical_coverage_state
from tests.canonical import base_config


def _provider_context(root: Path):
    return {
        "standard": {"caminho_origem": str(root), "eyle_root": str(root)},
        "core_memory": {
            "storage_dir": str(root / "memory"),
            "world_scope_id": f"workspace:{root.resolve()}",
        },
    }


def _dispatch(session, root, operation, arguments):
    return dispatch(
        session,
        action_kind="explorar",
        operation=operation,
        arguments=arguments,
        config=base_config(),
        provider_context=_provider_context(root),
        registry=build_registry([get_standard_provider()]),
        pending_schema_version=PENDING_SCHEMA_VERSION,
        validate_pending=validate_pending_continuation,
    )


def _checkpoint(execution_id: str):
    config = base_config()
    session = AgentSession("recover", execution_id=execution_id)
    execution = ExecutionContext.from_config(config, execution_id=execution_id)
    return {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "recoverable_execution",
        "question": "Recoverable execution checkpoint.",
        "session": session.to_checkpoint_dict(),
        "execution_state": execution.continuation_state(),
        "checkpoint_reason": "stalled_recoverable",
        "resume_hint": "Resume the same logical execution.",
    }


def test_rev378_read_file_frontier_is_bound_to_source_revision_and_never_mixes_versions(tmp_path):
    path = tmp_path / "large.py"
    path.write_text("\n".join(f"old_{i}" for i in range(1, 901)), encoding="utf-8")
    session = AgentSession("read")

    first = _dispatch(
        session, tmp_path, "read_file",
        {"source": "workspace", "path": "large.py"},
    )
    assert first.result["ok"] is True
    frontier = first.result["frontiers"][0]["id"]
    materials_before = copy.deepcopy(session.observation_ledger["materials"])
    snapshot = next(iter(session.observation_ledger["snapshots"].values()))
    expected_revision = snapshot["payload"]["resource_revision"]
    assert expected_revision

    path.write_text("\n".join(["new_1"] + [f"old_{i}" for i in range(2, 901)]), encoding="utf-8")

    continued = _dispatch(session, tmp_path, "continue", {"frontier": frontier})
    assert continued.result["ok"] is False
    assert continued.result["error_code"] == "FRONTIER_SOURCE_REVISED"
    assert continued.result["detail"]["expected_revision"] == expected_revision
    assert continued.result["detail"]["current_revision"] != expected_revision
    assert session.observation_ledger["frontiers"][frontier]["status"] == "stale"
    assert session.observation_ledger["frontiers"][frontier]["stale_reason"] == "source_revision_changed"

    # The original Material/Evidence lineage remains intact. A failure fact may
    # be added, but no page from the new source revision replaces old Material.
    for material_id, material in materials_before.items():
        assert session.observation_ledger["materials"][material_id]["source_version"] == material["source_version"]


def test_rev378_new_read_after_external_change_executes_against_new_revision(tmp_path):
    path = tmp_path / "large.py"
    path.write_text("\n".join(f"a_{i}" for i in range(1, 501)), encoding="utf-8")
    session = AgentSession("read")

    first = _dispatch(session, tmp_path, "read_file", {"source": "workspace", "path": "large.py"})
    old_revision = first.result["detail"]["file_hash"]
    path.write_text("\n".join(["changed"] + [f"a_{i}" for i in range(2, 501)]), encoding="utf-8")

    second = _dispatch(session, tmp_path, "read_file", {"source": "workspace", "path": "large.py"})
    assert second.result["ok"] is True
    assert second.result["status"] == "success"
    assert second.result["detail"]["file_hash"] != old_revision
    assert second.physical_progress is True


def test_rev378_search_frontier_is_bound_to_each_live_file_revision(tmp_path):
    path = tmp_path / "many.py"
    lines = [f"needle_{i}" if i % 20 == 0 else f"x_{i}" for i in range(1, 601)]
    path.write_text("\n".join(lines), encoding="utf-8")
    session = AgentSession("search")

    first = _dispatch(session, tmp_path, "search", {"source": "workspace", "query": "needle"})
    assert first.result["ok"] is True
    frontier = first.result["frontiers"][0]["id"]
    snapshot = next(iter(session.observation_ledger["snapshots"].values()))
    assert all(item.get("resource_revision") for item in snapshot["payload"]["items"])

    path.write_text("changed\n" + "\n".join(lines[1:]), encoding="utf-8")
    continued = _dispatch(session, tmp_path, "continue", {"frontier": frontier})
    assert continued.result["error_code"] == "FRONTIER_SOURCE_REVISED"
    mismatches = continued.result["detail"]["revision_mismatches"]
    assert len(mismatches) == 1
    assert mismatches[0]["path"] == "many.py"


def test_rev378_mechanical_coverage_never_merges_different_source_revisions():
    session = AgentSession("coverage")
    session.observation_ledger["entries"] = {
        "w0:a": {
            "reality_epoch": 0, "turn": 1, "capability": "standard.read_file",
            "frontier_ids": [],
            "coverage": {
                "scope": {"kind": "file", "source": "workspace", "path": "main.py", "source_revision": "rev-A"},
                "examined": {"line_start": 1, "line_end": 400, "total_lines": 800},
                "complete": False, "boundaries": [],
            },
        },
        "w0:b": {
            "reality_epoch": 0, "turn": 2, "capability": "standard.read_file",
            "frontier_ids": [],
            "coverage": {
                "scope": {"kind": "file", "source": "workspace", "path": "main.py", "source_revision": "rev-B"},
                "examined": {"line_start": 401, "line_end": 800, "total_lines": 800},
                "complete": False, "boundaries": [],
            },
        },
    }
    rows = mechanical_coverage_state(session)["files"]
    assert len(rows) == 2
    assert {row["source_revision"] for row in rows} == {"rev-A", "rev-B"}
    assert sorted(row["materialized_lines"] for row in rows) == [400, 400]


def test_rev378_provider_identity_ignores_mutable_provider_context_state(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(tmp_path / "pending"))
    identity = {"standard": {"workspace_root": str(tmp_path.resolve())}}
    first_context = {"standard": {"caminho_origem": str(tmp_path), "content_state": "empty"}}
    second_context = {"standard": {"caminho_origem": str(tmp_path), "content_state": "nonempty"}}

    saved = service.salvar_agent_pendente(
        _checkpoint("job-identity"),
        provider_context=first_context,
        provider_identity=identity,
        config=base_config(),
    )
    assert "provider_context_hash" not in saved
    valid, reason = service._validar_pendencia(saved, identity)
    assert (valid, reason) == (True, None)
    # Mutable context may differ entirely; validity is bound only to stable identity.
    assert first_context != second_context


def test_rev378_provider_identity_mismatch_rejects_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(tmp_path / "pending"))
    saved = service.salvar_agent_pendente(
        _checkpoint("job-mismatch"),
        provider_identity={"standard": {"workspace_root": "/workspace/A"}},
        config=base_config(),
    )
    valid, reason = service._validar_pendencia(
        saved, {"standard": {"workspace_root": "/workspace/B"}},
    )
    assert valid is False
    assert reason == "PENDING_PROVIDER_IDENTITY_MISMATCH"


def test_rev378_recoverable_checkpoint_is_singular_and_generation_is_monotonic(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(tmp_path / "pending"))
    identity = {"standard": {"workspace_root": str(tmp_path.resolve())}}
    first = service.salvar_agent_pendente(
        _checkpoint("job-singular"), provider_identity=identity, config=base_config(),
    )
    second = service.salvar_agent_pendente(
        _checkpoint("job-singular"), provider_identity=identity, config=base_config(),
    )
    third = service.salvar_agent_pendente(
        _checkpoint("job-singular"), provider_identity=identity, config=base_config(),
    )

    assert [first["checkpoint_generation"], second["checkpoint_generation"], third["checkpoint_generation"]] == [1, 2, 3]
    matching = [
        item for item in service.listar_agent_pendentes()
        if service._pending_execution_id(item) == "job-singular"
    ]
    assert len(matching) == 1
    assert matching[0]["id"] == third["id"]
    assert matching[0]["checkpoint_generation"] == 3
    recovery_files = list((tmp_path / "pending" / "recovery").glob("*.json"))
    assert len(recovery_files) == 1


def test_rev378_failed_checkpoint_publish_preserves_previous_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(tmp_path / "pending"))
    identity = {"standard": {"workspace_root": str(tmp_path.resolve())}}
    first = service.salvar_agent_pendente(
        _checkpoint("job-atomic"), provider_identity=identity, config=base_config(),
    )
    path = Path(service._pending_storage_path(first))
    before = path.read_bytes()

    def fail_publish(_path, _data):
        raise OSError("simulated publish failure")

    monkeypatch.setattr(service, "_salvar_json", fail_publish)
    with pytest.raises(OSError, match="simulated publish failure"):
        service.salvar_agent_pendente(
            _checkpoint("job-atomic"), provider_identity=identity, config=base_config(),
        )

    assert path.read_bytes() == before
