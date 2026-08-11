from __future__ import annotations

from pathlib import Path

import pytest

from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.core import memory as project_memory
from eyle.runtime import queue
from eyle.core.validation import validate_final
from eyle.runtime.config import ConfigError, validar_config
from tests.canonical import base_config
from llm.structured import StructuredResponseError, parse_agent_response, parse_claim_review_response
from eyle.core import tools


def test_session_schema_is_exact_and_old_state_is_not_migrated():
    current = AgentSession("x").to_dict()
    assert current["session_schema_version"] == SESSION_SCHEMA_VERSION == "5.7.5"
    assert AgentSession.from_dict(current).request == "x"
    old = dict(current)
    old["session_schema_version"] = "5.4"
    old["committed_progress_epoch"] = 3
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(old)


def test_removed_config_keys_are_errors_not_aliases():
    for key in (
        "committed_progress_extension_calls",
        "max_write_investigation_turns",
        "max_identical_tool_repeats",
        "max_phase_violations",
    ):
        cfg = base_config()
        cfg["agent"][key] = 1
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)


def test_removed_relevant_source_count_cap_is_not_a_compatibility_key():
    cfg = base_config()
    cfg["agent"]["context_view"]["max_relevant_sources"] = 4
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)


def test_removed_working_set_target_is_not_a_compatibility_key():
    cfg = base_config()
    cfg["context_engine"]["working_set_target_tokens"] = 12000
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)


def test_removed_agent_output_ceiling_keys_are_not_aliases():
    for key in (
        "agent_decision_max_tokens",
        "agent_analysis_max_tokens",
        "agent_patch_max_tokens",
    ):
        cfg = base_config()
        cfg["llm"][key] = 1000
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)


def test_final_has_one_canonical_object_shape():
    ok, reason, *_ = validate_final("legacy string final", {}, investigation=[])
    assert not ok and reason == "FINAL_INVALID"
    ok, reason, answer, limitations = validate_final(
        {"answer": "ok", "limitations": [], "evidence_ids": []},
        {}, investigation=[]
    )
    assert ok and reason == "ok" and answer == "ok" and limitations == []


def test_deleted_legacy_symbols_do_not_exist_in_runtime_source():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in list((root / "eyle").rglob("*.py")) + list((root / "llm").rglob("*.py"))
    )
    forbidden = [
        "def validate_investigation(",
        "def reopen_targets_from_semantic_gaps(",
        "committed_progress_epoch",
        "progress_credited_evidence_ids",
        "claim_review_history",
        "historically_seen_source_ranges",
        "max_write_investigation_turns",
        "claim_protocol_recovery_target",
        "semantic_gap_protocol_recovery_target",
        "finding_protocol_recovery_target",
    ]
    for token in forbidden:
        assert token not in source


def test_old_config_identity_is_rejected_even_if_fields_are_otherwise_valid():
    cfg = base_config()
    cfg["config_schema_version"] = "5.4"
    cfg["revision"] = "rev5.4-grounding-unification"
    with pytest.raises(ConfigError, match="CONFIG_IDENTITY_INCOMPATIBLE"):
        validar_config(cfg)


def test_old_project_memory_is_not_migrated(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text('{"entries":[{"id":"old"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="MEMORY_SCHEMA_INCOMPATIBLE"):
        project_memory._load(str(path))


def test_old_queue_schema_is_not_migrated(monkeypatch, tmp_path):
    import sqlite3

    db = tmp_path / "fila.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL, iniciado_em TEXT, concluido_em TEXT, resultado TEXT, erro TEXT)")
        conn.execute("CREATE TABLE runtime_meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
        conn.execute("INSERT INTO runtime_meta (chave, valor) VALUES ('queue_instance_id', 'old')")
        conn.execute("CREATE TABLE worker_heartbeat (worker_id TEXT PRIMARY KEY, status TEXT NOT NULL, job_id INTEGER, atualizado_em TEXT NOT NULL, detalhe TEXT)")
    monkeypatch.setattr(queue, "DB_PATH", str(db))
    queue._schemas_prontos.clear()
    with pytest.raises(RuntimeError, match="QUEUE_SCHEMA_INCOMPATIBLE"):
        queue.database_instance_id()


def test_rev551_rejects_rev55_session_identity():
    current = AgentSession("x").to_dict()
    current["session_schema_version"] = "5.5"
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(current)


def test_agent_contract_has_no_workspace_scope_and_final_evidence_ids_are_canonical():
    payload = {
        "tool_calls": None, "patches": None, "needs_user": None,
        "final": {"answer": "ok", "limitations": [], "evidence_ids": []},
        "investigation_updates": [],
    }
    assert parse_agent_response(payload)["final"]["answer"] == "ok"
    with pytest.raises(StructuredResponseError):
        parse_agent_response({**payload, "workspace_scope": {"mode": "read"}})
    bad = dict(payload)
    bad["final"] = {"answer": "ok", "limitations": []}
    with pytest.raises(StructuredResponseError):
        parse_agent_response(bad)


def test_claim_contract_has_no_findings_lane():
    payload = {
        "material_satisfaction": {"status": "satisfied", "grounding_refs": ["request"], "reason": "complete"},
        "answer_consistency": {"status": "consistent", "grounding_refs": ["answer:a1"], "reason": "no conflict"},
        "claims": [], "semantic_gaps": [],
    }
    assert parse_claim_review_response(payload)["claims"] == []
    with pytest.raises(StructuredResponseError):
        parse_claim_review_response({**payload, "findings": []})


def test_read_file_is_the_only_public_file_read_tool():
    assert "read_file" in tools.TOOLS
    assert "read_range" not in tools.TOOLS
    schema = tools.TOOLS["read_file"]["input_schema"]
    assert set(schema["properties"]) >= {"path", "line_start", "line_end"}


def test_second_cut_config_names_are_not_accepted_as_aliases():
    for key in ("max_read_range_lines", "chat_history_token_budget", "max_no_progress_turns"):
        cfg = base_config()
        cfg["agent"][key] = 1
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)
    cfg = base_config()
    cfg["agent"]["context_view"]["max_relevant_source_chars"] = 1000
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)
    cfg = base_config()
    cfg["worker"] = {"job_deadline_seconds": 100}
    with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
        validar_config(cfg)


def test_structured_output_has_one_json_schema_path_and_no_repair_config():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "llm" / "capabilities.py").exists()
    production = (root / "llm" / "executar.py").read_text(encoding="utf-8")
    assert '"json_object"' not in production
    assert "_ensure_structured_capability" not in production
    assert "_revalidate_structured_capability" not in production
    for key in (
        "structured_protocol_retries",
        "truncation_retry_multiplier",
        "truncation_retry_max_tokens",
        "agent_retry_max_attempts",
    ):
        cfg = base_config()
        target = cfg["agent"] if key == "structured_protocol_retries" else cfg["llm"]
        target[key] = 2
        with pytest.raises(ConfigError, match="UNKNOWN_CONFIG_FIELD"):
            validar_config(cfg)


def test_claim_atomic_contract_has_no_recovery_identity_fields():
    payload = {
        "material_satisfaction": {"status": "satisfied", "grounding_refs": ["request"], "reason": "complete"},
        "answer_consistency": {"status": "consistent", "grounding_refs": ["answer:a1"], "reason": "consistent"},
        "claims": [{
            "answer_ref": "answer:a1", "target_id": None, "statement": "fact",
            "grounding_refs": ["evidence:ev-1"], "verdict": "supported", "reason": "observed",
        }],
        "semantic_gaps": [],
    }
    assert parse_claim_review_response(payload)["claims"][0]["statement"] == "fact"
    bad_claim = dict(payload)
    bad_claim["claims"] = [{**payload["claims"][0], "id": "claim-1"}]
    with pytest.raises(StructuredResponseError):
        parse_claim_review_response(bad_claim)
    bad_kind = dict(payload)
    bad_kind["claims"] = [{**payload["claims"][0], "kind": "fact"}]
    with pytest.raises(StructuredResponseError):
        parse_claim_review_response(bad_kind)
    bad_gap = dict(payload)
    bad_gap["semantic_gaps"] = [{
        "id": "gap-1", "type": "scope_gap", "target_id": None,
        "grounding_refs": ["request"], "required_property": "missing scope", "reason": "missing scope",
    }]
    with pytest.raises(StructuredResponseError):
        parse_claim_review_response(bad_gap)


def test_tool_registry_has_one_identity_and_contract_source():
    root = Path(__file__).resolve().parents[1]
    source = (root / "eyle" / "core" / "tools.py").read_text(encoding="utf-8")
    assert "_TOOL_CONTRACTS" not in source
    for name, entry in tools.TOOLS.items():
        assert "name" not in entry
        assert "permission" not in entry
        assert "output_schema" not in entry
        assert entry.get("category")
        assert entry.get("effects")
        assert entry.get("effect") in {"observe", "execute", "mutate"}
        assert entry.get("availability")
        assert isinstance(entry.get("produces_evidence"), bool)
