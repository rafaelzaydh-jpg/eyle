from __future__ import annotations

from pathlib import Path
import pytest

from eyle.core import memory as project_memory, tools
from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.core.validation import validate_final
from eyle.runtime import queue
from eyle.runtime.config import ConfigError, validar_config
from llm.structured import StructuredResponseError, parse_agent_response
from tests.canonical import base_config


def test_rev1_session_schema_is_exact_and_old_state_is_not_migrated():
    current = AgentSession("x").to_dict()
    assert current["session_schema_version"] == SESSION_SCHEMA_VERSION == "2.7.5-r1.4"
    assert set(current) == {
        "session_schema_version", "request", "execution_id", "turn", "workspace_epoch",
        "observation_ledger", "decision_ledger", "investigation", "tasks",
        "conversation_background", "write_transaction",
    }
    for old_version in ("5.9.1", "5.9", "5.5", "5.4"):
        old = dict(current); old["session_schema_version"] = old_version
        with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
            AgentSession.from_dict(old)


def test_rev1_session_has_no_source_or_evidence_ledgers():
    current = AgentSession("x").to_dict()
    assert "source_record_ledger" not in current
    assert "evidence_ledger" not in current
    assert set(current["observation_ledger"]) == {"entries", "events", "pending_results", "handles", "snapshots", "frontiers", "materials", "replay_count"}


def test_removed_config_keys_are_errors_not_aliases():
    for key in (
        "committed_progress_extension_calls", "max_write_investigation_turns",
        "max_identical_tool_repeats", "max_phase_violations",
    ):
        cfg = base_config(); cfg["agent"][key] = 1
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)


def test_removed_context_and_output_ceiling_keys_are_not_aliases():
    cfg = base_config(); cfg["agent"]["context_view"] = {"max_source_preview_chars": 3500}
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)
    cfg = base_config(); cfg["context_engine"]["working_set_target_tokens"] = 12000
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)
    for key in ("agent_decision_max_tokens", "agent_analysis_max_tokens", "agent_patch_max_tokens"):
        cfg = base_config(); cfg["llm"][key] = 1000
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)


def test_final_has_one_canonical_grounding_shape_and_rejects_evidence_alias():
    ok, reason, *_ = validate_final("legacy string final", {})
    assert not ok and reason == "FINAL_INVALID"
    ok, reason, answer, limitations = validate_final(
        {"answer": "ok", "limitations": [], "grounding_ids": []}, {}
    )
    assert ok and reason == "ok" and answer == "ok" and limitations == []
    ok, reason, *_ = validate_final(
        {"answer": "old", "limitations": [], "evidence_ids": []}, {}
    )
    assert not ok and reason.startswith("FINAL_UNKNOWN_FIELDS")


def test_deleted_architecture_is_physically_absent_from_runtime_source():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "eyle/core/source_record.py").exists()
    assert not (root / "eyle/core/evidence.py").exists()
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in list((root / "eyle").rglob("*.py")) + list((root / "llm").rglob("*.py"))
    )
    forbidden = [
        "source_record_ledger", "evidence_ledger", "promote_source_records",
        '"expand_observation"', '"agent_info"',
        "committed_progress_epoch", "progress_credited_evidence_ids",
        "claim_review_history", "historically_seen_source_ranges",
        "max_write_investigation_turns", "SECRET_CONTENT_BLOCKED", "SECRET_PATH_BLOCKED",
    ]
    for token in forbidden:
        assert token not in source


def test_old_config_identity_is_rejected():
    for schema, revision in (
        ("5.9.1", "rev5.9.1-scope-investigation-contract-hardening"),
        ("5.9", "rev5.9-decision-integrity-epistemic-completion"),
        ("5.4", "rev5.4-grounding-unification"),
    ):
        cfg = base_config(); cfg["config_schema_version"] = schema; cfg["revision"] = revision
        with pytest.raises(ConfigError, match="CONFIG_IDENTITY_INCOMPATIBLE"):
            validar_config(cfg)


def test_old_project_memory_and_queue_are_not_migrated(monkeypatch, tmp_path):
    assert not hasattr(project_memory, "_load")
    assert not hasattr(project_memory, "search_memory")
    assert not hasattr(project_memory, "store_memory")

    import sqlite3
    db = tmp_path / "fila.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL, iniciado_em TEXT, concluido_em TEXT, resultado TEXT, erro TEXT)")
        conn.execute("CREATE TABLE runtime_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
        conn.execute("INSERT INTO runtime_meta (chave, valor) VALUES ('queue_instance_id', 'old')")
        conn.execute("CREATE TABLE worker_heartbeat (worker_id TEXT PRIMARY KEY, status TEXT NOT NULL, job_id INTEGER, atualizado_em TEXT NOT NULL, detalhe TEXT)")
    monkeypatch.setattr(queue, "DB_PATH", str(db)); queue._schemas_prontos.clear()
    with pytest.raises(RuntimeError, match="QUEUE_SCHEMA_INCOMPATIBLE"):
        queue.database_instance_id()


def test_agent_contract_uses_grounding_ids_and_rejects_evidence_ids():
    payload = {"action": {"kind": "final", "answer": "ok", "limitations": [], "grounding_ids": []}, "investigation_updates": [], "task_updates": []}
    assert parse_agent_response(payload)["action"]["answer"] == "ok"
    with pytest.raises(StructuredResponseError):
        parse_agent_response({"action": {"kind": "final", "answer": "old", "limitations": [], "evidence_ids": []}, "investigation_updates": [], "task_updates": []})
    with pytest.raises(StructuredResponseError):
        parse_agent_response({**payload, "workspace_scope": {"mode": "read"}})



def test_tool_registry_has_one_contract_source_and_rev1_cuts():
    assert "read_file" in tools.TOOLS and "read_range" not in tools.TOOLS
    assert "continue_observation" in tools.TOOLS and "expand_observation" not in tools.TOOLS
    assert "agent_info" not in tools.TOOLS and "execution_trace" not in tools.TOOLS
    assert len(tools.TOOLS) == 17
    for entry in tools.TOOLS.values():
        assert "category" not in entry
        assert "effects" not in entry
        assert entry.get("effect") in {"observe", "execute", "mutate"}
        assert entry.get("availability")
        assert isinstance(entry.get("produces_grounding"), bool)
        assert "produces_source_records" not in entry
        assert "produces_evidence" not in entry


