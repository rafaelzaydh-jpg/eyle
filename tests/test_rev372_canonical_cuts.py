from __future__ import annotations

import copy
import inspect
import sqlite3
from pathlib import Path

import pytest

from eyle.core.session import AgentSession, SESSION_SCHEMA_VERSION
from eyle.devtools.migrate_memory_v11_to_v12 import migrate_memory_v11_to_v12
from eyle.providers.standard.registry import CAPABILITIES
from eyle.runtime.config import ConfigError, validar_config
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.runtime.memory_graph import apply_graph_operations, graph_counts, memory_db_path
from tests.canonical import base_config, standard_registry


ROOT = Path(__file__).resolve().parents[1]


def test_rev372_config_accepts_only_current_identity():
    registry = standard_registry()
    current = base_config()
    assert validar_config(copy.deepcopy(current), registry)["revision"] == "rev4.0.0-ecc"

    older = copy.deepcopy(current)
    older["config_schema_version"] = "2.7.5-r3.7.1-ecc"
    older["revision"] = "rev3.7.1-ecc"
    with pytest.raises(ConfigError, match="CONFIG_IDENTITY_INCOMPATIBLE"):
        validar_config(older, registry)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("llm", "generated_token_fuse", 120000),
        ("root", "agent", {"task_deadline_seconds": 600}),
    ],
)
def test_rev372_removed_config_fields_are_rejected(section, key, value):
    registry = standard_registry()
    cfg = base_config()
    if section == "root":
        cfg[key] = value
    else:
        cfg[section][key] = value
    with pytest.raises(ConfigError):
        validar_config(cfg, registry)


def test_rev372_session_and_pending_are_current_schema_only():
    session = AgentSession("x").to_dict()
    assert session["session_schema_version"] == SESSION_SCHEMA_VERSION == "2.7.5-r4.0.0-ecc"
    older = copy.deepcopy(session)
    older["session_schema_version"] = "2.7.5-r3.7.1-ecc"
    with pytest.raises(ValueError, match="SESSION_SCHEMA_INCOMPATIBLE"):
        AgentSession.from_dict(older)

    with pytest.raises(ValueError, match="PENDING_SCHEMA_INCOMPATIBLE"):
        validate_pending_continuation({"pending_schema_version": "12-ecc"})
    assert PENDING_SCHEMA_VERSION == "16-ecc"


def test_rev372_memory_runtime_rejects_v11_and_one_shot_migrator_converts(tmp_path):
    storage = str(tmp_path / "memory")
    apply_graph_operations(
        storage,
        [{"op": "create_node", "id": "mem-old", "scope": "user", "kind": "fact", "content": "keep"}],
    )
    db = memory_db_path(storage)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP INDEX IF EXISTS idx_memory_nodes_domain_context_status_updated")
        conn.execute("ALTER TABLE memory_nodes DROP COLUMN context_key")
        conn.execute("ALTER TABLE memory_nodes DROP COLUMN domain")
        conn.execute(
            "UPDATE memory_meta SET value='2.7.5-r3.6-memory-graph-v11' WHERE key='schema_version'"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="MEMORY_GRAPH_SCHEMA_INCOMPATIBLE"):
        graph_counts(storage)

    result = migrate_memory_v11_to_v12(storage)
    assert result["status"] == "migrated"
    assert graph_counts(storage)["nodes"] == 1


def test_rev372_standard_has_one_canonical_package():
    assert (ROOT / "eyle/providers/standard").is_dir()
    for removed in (
        "eyle/providers/standard.py",
        "eyle/providers/standard_impl",
        "eyle/providers/workspace_transaction.py",
        "eyle/providers/sandbox_promotion.py",
    ):
        assert not (ROOT / removed).exists()


def test_rev372_execution_and_context_removed_aliases_are_physically_absent():
    from eyle.runtime.execution_context import ExecutionContext
    from eyle.runtime import service
    from eyle.core import memory

    cfg = base_config()
    execution = ExecutionContext.from_config(cfg, execution_id="x")
    for name in ("deadline_monotonic", "generated_token_limit", "generated_tokens"):
        assert not hasattr(execution, name)

    assert list(inspect.signature(service.registrar_mensagem_com_snapshot).parameters) == [
        "role", "texto", "metadata"
    ]
    assert not hasattr(memory, "project_memory_view")
    assert hasattr(memory, "materialize_explicit_memory_view")


def test_rev372_standard_public_symbol_paging_uses_page_size_only():
    props = CAPABILITIES["symbol_relations"]["input_schema"]["properties"]
    assert "page_size" in props
    assert "max_edges" not in props


def test_rev372_adapter_and_ui_removed_aliases_are_physically_absent():
    server = (ROOT / "server/server.py").read_text(encoding="utf-8")
    assert "def prepare_upstream(" in server
    assert "def _prepare_upstream(" not in server
    assert 'if "max_tokens" in body:' in server
    assert "DEEPSEEK_API_KEY" not in server
    assert "DEFAULT_MODEL" not in server

    web = (ROOT / "web/static/app.js").read_text(encoding="utf-8")
    assert "data.confirmation" not in web
    assert "msg.confirmation" not in web


def test_rev372_no_removed_runtime_paths_outside_explicit_migrator():
    roots = [ROOT / "eyle", ROOT / "llm", ROOT / "server", ROOT / "web"]
    excluded = {
        (ROOT / "eyle/devtools/migrate_memory_v11_to_v12.py").resolve(),
        (ROOT / "eyle/devtools/release_identity.py").resolve(),
    }
    forbidden = (
        "standard_impl",
        "globals().setdefault",
        "limite_snapshot",
        "project_memory_view",
        "automatic_temporary",
        "generated_token_fuse",
        "generated_token_limit",
        "task_deadline_seconds",
        "deadline_monotonic",
        "LEGACY_AGENT_PENDENTE_PATH",
        "AGENT_PENDENTE_PATH",
        "max_search_matches",
        "max_search_ranges",
        "max_file_read_lines",
        "temporary_graph_records",
    )
    violations = []
    for source_root in roots:
        for path in source_root.rglob("*.py"):
            if path.resolve() in excluded:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in forbidden:
                if marker in text:
                    violations.append((str(path.relative_to(ROOT)), marker))
    assert violations == []
