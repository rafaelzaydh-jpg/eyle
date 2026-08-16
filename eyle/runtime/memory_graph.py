"""Persistent graph memory mechanics for the ECC general-agent core.

This module is deliberately *not* a capability provider. It is internal
Runtime infrastructure for persistent semantic knowledge. Main owns meaning and
chooses graph deltas/navigation. Runtime owns IDs, SQLite durability, revisions,
source anchors/freshness and bounded mechanical selection. No hot/cold tier,
retrieval counter or graph-centrality signal participates in cognition.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

MEMORY_GRAPH_SCHEMA_VERSION = "2.7.5-r2.9-memory-graph-v8"
_PRE_ASSOCIATIVE_MEMORY_GRAPH_SCHEMA_VERSION = "2.7.5-r2.8.7-memory-graph-v7"
_PRE_SCALABLE_MEMORY_GRAPH_SCHEMA_VERSION = "2.7.5-r2.8.5-memory-graph-v6"
_PRE_EPISTEMIC_MEMORY_GRAPH_SCHEMA_VERSION = "2.7.5-r2.8-memory-graph-v5"
_LEGACY_MEMORY_GRAPH_SCHEMA_VERSIONS = {
    "2.7.5-r2.7.1-memory-graph-v4", "2.7.5-r2.7-memory-graph-v3", "2.7.5-r2.5-memory-graph-v2"
}
_RETENTIONS = {"temporary", "persistent"}
_NODE_STATUSES = {"current", "archived", "superseded"}
_EDGE_STATUSES = {"current", "retired"}
_MEM_ID_RE = re.compile(r"^mem-[A-Za-z0-9._-]+$")
_REL_ID_RE = re.compile(r"^rel-[A-Za-z0-9._-]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decode(raw: str | None, default: Any) -> Any:
    if not raw:
        return copy.deepcopy(default)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MEMORY_GRAPH_STORE_INVALID") from exc


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _clean_text(value: Any, *, code: str, maximum: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"MEMORY_{code}_EMPTY")
    if maximum is not None and len(text) > maximum:
        raise ValueError(f"MEMORY_{code}_TOO_LARGE")
    return text


def _clean_json_object(value: Any, *, code: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"MEMORY_{code}_INVALID")
    try:
        encoded = _json(value)
        decoded = json.loads(encoded)
    except Exception as exc:
        raise ValueError(f"MEMORY_{code}_INVALID") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"MEMORY_{code}_INVALID")
    return decoded


def _clean_epistemic(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    nature = _clean_text(raw.get("nature") or "unclassified", code="EPISTEMIC_NATURE", maximum=96)
    confidence = raw.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("MEMORY_EPISTEMIC_CONFIDENCE_INVALID")
        confidence = float(confidence)
    volatility = _clean_text(raw.get("volatility") or "unknown", code="EPISTEMIC_VOLATILITY", maximum=96)
    temporal = _clean_json_object(raw.get("temporal"), code="EPISTEMIC_TEMPORAL")
    context = _clean_json_object(raw.get("context"), code="EPISTEMIC_CONTEXT")
    return {"nature": nature, "confidence": confidence, "volatility": volatility, "temporal": temporal, "context": context}


def _clean_tags(values: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = str(raw or "").strip()
        if not tag:
            continue
        folded = tag.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(tag)
    return out


def _clean_recall_metadata(value: Any) -> dict[str, list[str]]:
    """Normalize Main-authored associative retrieval cues without inventing any.

    These strings are not evidence, ranking weights or Runtime ontology. They are
    simply semantic handles authored by Main so future lexical recall can bridge
    different wording. Runtime deduplicates/serializes them mechanically.
    """
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"aliases", "concepts", "cues"}:
        raise ValueError("MEMORY_RECALL_METADATA_INVALID")
    out: dict[str, list[str]] = {}
    for key in ("aliases", "concepts", "cues"):
        raw_values = value.get(key)
        if raw_values is None:
            continue
        if not isinstance(raw_values, list):
            raise ValueError("MEMORY_RECALL_METADATA_INVALID")
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("MEMORY_RECALL_METADATA_INVALID")
            text = raw.strip(); folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded); cleaned.append(text)
        if cleaned:
            out[key] = cleaned
    return out


def _merge_recall_metadata(
    current: dict[str, list[str]],
    *,
    replacement: Any = None,
    additions: Any = None,
    removals: Any = None,
) -> dict[str, list[str]]:
    base = _clean_recall_metadata(replacement) if replacement is not None else _clean_recall_metadata(current)
    add = _clean_recall_metadata(additions)
    remove = _clean_recall_metadata(removals)
    for key in ("aliases", "concepts", "cues"):
        values = list(base.get(key) or [])
        remove_set = {v.casefold() for v in remove.get(key) or []}
        values = [v for v in values if v.casefold() not in remove_set]
        seen = {v.casefold() for v in values}
        for value in add.get(key) or []:
            if value.casefold() not in seen:
                seen.add(value.casefold()); values.append(value)
        if values:
            base[key] = values
        else:
            base.pop(key, None)
    return base


def memory_db_path(storage_dir: str) -> str:
    root = os.path.realpath(str(storage_dir or ""))
    if not root:
        raise ValueError("MEMORY_STORAGE_UNAVAILABLE")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "core_memory.sqlite3")


def world_scope(world_identity: str) -> str:
    """Return an opaque Runtime scope for one host-defined world/body.

    Core does not know whether the identity names a filesystem workspace, robot,
    network, cloud tenant or another body. The Host owns that meaning; Memory only
    needs a stable opaque identity boundary.
    """
    identity = str(world_identity or "").strip()
    if not identity:
        raise ValueError("MEMORY_WORLD_SCOPE_REQUIRED")
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"world:{digest}"


def _connect(storage_dir: str) -> sqlite3.Connection:
    conn = sqlite3.connect(memory_db_path(storage_dir), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_meta'").fetchone()
    if exists is None:
        with conn:
            conn.executescript(
                """
                CREATE TABLE memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE memory_nodes (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    retention TEXT NOT NULL DEFAULT 'persistent' CHECK(retention IN ('temporary','persistent')),
                    epistemic_nature TEXT NOT NULL DEFAULT 'unclassified',
                    epistemic_confidence REAL CHECK(epistemic_confidence IS NULL OR (epistemic_confidence >= 0.0 AND epistemic_confidence <= 1.0)),
                    epistemic_volatility TEXT NOT NULL DEFAULT 'unknown',
                    epistemic_temporal TEXT NOT NULL DEFAULT '{}',
                    epistemic_context TEXT NOT NULL DEFAULT '{}',
                    associative_recall TEXT NOT NULL DEFAULT '{}',
                    last_evidenced_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('current','archived','superseded')),
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_nodes_scope_status_updated ON memory_nodes(scope,status,updated_at DESC);
                CREATE INDEX idx_memory_nodes_retention_status_updated ON memory_nodes(retention,status,updated_at DESC);
                CREATE INDEX idx_memory_nodes_kind_status ON memory_nodes(kind,status);
                CREATE INDEX idx_memory_nodes_epistemic_nature_status ON memory_nodes(epistemic_nature,status);
                CREATE INDEX idx_memory_nodes_epistemic_volatility_status ON memory_nodes(epistemic_volatility,status);
                CREATE TABLE memory_tags (
                    node_id TEXT NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
                    tag TEXT COLLATE NOCASE NOT NULL,
                    PRIMARY KEY(node_id,tag)
                );
                CREATE INDEX idx_memory_tags_tag ON memory_tags(tag COLLATE NOCASE);
                CREATE TABLE memory_edges (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL REFERENCES memory_nodes(id),
                    label TEXT NOT NULL,
                    target TEXT NOT NULL REFERENCES memory_nodes(id),
                    epistemic_nature TEXT NOT NULL DEFAULT 'relation',
                    epistemic_confidence REAL CHECK(epistemic_confidence IS NULL OR (epistemic_confidence >= 0.0 AND epistemic_confidence <= 1.0)),
                    epistemic_volatility TEXT NOT NULL DEFAULT 'unknown',
                    epistemic_temporal TEXT NOT NULL DEFAULT '{}',
                    epistemic_context TEXT NOT NULL DEFAULT '{}',
                    last_evidenced_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('current','retired')),
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_edges_source_status ON memory_edges(source,status);
                CREATE INDEX idx_memory_edges_target_status ON memory_edges(target,status);
                CREATE INDEX idx_memory_edges_label_status ON memory_edges(label,status);
                CREATE TABLE memory_anchors (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL CHECK(entity_type IN ('node','edge')),
                    entity_id TEXT NOT NULL,
                    anchor_kind TEXT NOT NULL CHECK(anchor_kind IN ('material','request','memory')),
                    source_capability TEXT,
                    locator TEXT NOT NULL,
                    source_version TEXT,
                    content_hash TEXT,
                    freshness_token TEXT,
                    freshness_arguments TEXT,
                    source_ref TEXT,
                    entity_revision INTEGER NOT NULL CHECK(entity_revision >= 1),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_anchors_entity ON memory_anchors(entity_type,entity_id);
                CREATE INDEX idx_memory_anchors_ref ON memory_anchors(source_ref);
                CREATE TABLE memory_changesets (
                    id TEXT PRIMARY KEY,
                    execution_id TEXT,
                    turn INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE memory_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    changeset_id TEXT NOT NULL REFERENCES memory_changesets(id),
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    revision INTEGER,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_events_entity ON memory_events(entity_type,entity_id,seq);
                CREATE INDEX idx_memory_nodes_recall_scope ON memory_nodes(status,scope,retention,id);
                CREATE INDEX idx_memory_nodes_recall_nature ON memory_nodes(status,scope,epistemic_nature,id);
                CREATE INDEX idx_memory_nodes_recall_volatility ON memory_nodes(status,scope,epistemic_volatility,id);
                CREATE TABLE memory_recall_snapshots (
                    id TEXT PRIMARY KEY,
                    selector TEXT NOT NULL,
                    scoped_nodes INTEGER NOT NULL DEFAULT 0,
                    matched_nodes INTEGER NOT NULL DEFAULT 0,
                    selected_nodes INTEGER NOT NULL DEFAULT 0,
                    backend TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE memory_recall_items (
                    snapshot_id TEXT NOT NULL REFERENCES memory_recall_snapshots(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
                    node_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'match',
                    PRIMARY KEY(snapshot_id,ordinal),
                    UNIQUE(snapshot_id,node_id)
                );
                CREATE INDEX idx_memory_recall_items_cursor ON memory_recall_items(snapshot_id,ordinal);
                """
            )
            _ensure_fts_schema(conn, rebuild=True)
            conn.execute("INSERT INTO memory_meta(key,value) VALUES('schema_version',?)", (MEMORY_GRAPH_SCHEMA_VERSION,))
        return
    rows = dict(conn.execute("SELECT key,value FROM memory_meta").fetchall())
    schema_version = rows.get("schema_version")
    if schema_version in _LEGACY_MEMORY_GRAPH_SCHEMA_VERSIONS:
        # Migration: lifecycle vocabulary changed from conversation-bound ``context``
        # to general-purpose ``temporary`` memory. Existing temporary nodes keep
        # their meaning and become temporary; older stores without retention stay
        # persistent. The rebuild is mechanical and preserves IDs/relations.
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        if "retention" not in columns:
            with conn:
                conn.execute("ALTER TABLE memory_nodes ADD COLUMN retention TEXT NOT NULL DEFAULT 'persistent'")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE memory_nodes_v5 (
                        id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                        retention TEXT NOT NULL DEFAULT 'persistent' CHECK(retention IN ('temporary','persistent')),
                        status TEXT NOT NULL CHECK(status IN ('current','archived','superseded')),
                        revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT INTO memory_nodes_v5(id,scope,kind,content,retention,status,revision,created_at,updated_at)
                    SELECT id,scope,kind,content,CASE WHEN retention IN ('context','temporary') THEN 'temporary' ELSE 'persistent' END,status,revision,created_at,updated_at
                    FROM memory_nodes
                """)
                conn.execute("DROP TABLE memory_nodes")
                conn.execute("ALTER TABLE memory_nodes_v5 RENAME TO memory_nodes")
                conn.execute("CREATE INDEX idx_memory_nodes_scope_status_updated ON memory_nodes(scope,status,updated_at DESC)")
                conn.execute("CREATE INDEX idx_memory_nodes_retention_status_updated ON memory_nodes(retention,status,updated_at DESC)")
                conn.execute("CREATE INDEX idx_memory_nodes_kind_status ON memory_nodes(kind,status)")
                conn.execute("UPDATE memory_meta SET value=? WHERE key='schema_version'", (_PRE_EPISTEMIC_MEMORY_GRAPH_SCHEMA_VERSION,))
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        schema_version = _PRE_EPISTEMIC_MEMORY_GRAPH_SCHEMA_VERSION
    if schema_version == _PRE_EPISTEMIC_MEMORY_GRAPH_SCHEMA_VERSION:
        # Migration: add epistemic metadata without rewriting semantic history.
        # Existing nodes become explicitly unclassified/unknown; Main may later
        # reassess them when they are relevant instead of Runtime inventing meaning.
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        with conn:
            additions = [
                ("epistemic_nature", "TEXT NOT NULL DEFAULT 'unclassified'"),
                ("epistemic_confidence", "REAL"),
                ("epistemic_volatility", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("epistemic_temporal", "TEXT NOT NULL DEFAULT '{}'"),
                ("epistemic_context", "TEXT NOT NULL DEFAULT '{}'"),
                ("last_evidenced_at", "TEXT"),
            ]
            for name, ddl in additions:
                if name not in columns:
                    conn.execute(f"ALTER TABLE memory_nodes ADD COLUMN {name} {ddl}")
            edge_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_edges)")}
            edge_additions = [
                ("epistemic_nature", "TEXT NOT NULL DEFAULT 'relation'"),
                ("epistemic_confidence", "REAL"),
                ("epistemic_volatility", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("epistemic_temporal", "TEXT NOT NULL DEFAULT '{}'"),
                ("epistemic_context", "TEXT NOT NULL DEFAULT '{}'"),
            ]
            for name, ddl in edge_additions:
                if name not in edge_columns:
                    conn.execute(f"ALTER TABLE memory_edges ADD COLUMN {name} {ddl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_epistemic_nature_status ON memory_nodes(epistemic_nature,status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_epistemic_volatility_status ON memory_nodes(epistemic_volatility,status)")
            conn.execute("UPDATE memory_meta SET value=? WHERE key='schema_version'", (_PRE_SCALABLE_MEMORY_GRAPH_SCHEMA_VERSION,))
        schema_version = _PRE_SCALABLE_MEMORY_GRAPH_SCHEMA_VERSION
    if schema_version == _PRE_SCALABLE_MEMORY_GRAPH_SCHEMA_VERSION:
        # Migration: add scalable literal recall infrastructure. This migration
        # adds only mechanical indexes/search/navigation state; semantic node and
        # edge contents are unchanged.
        with conn:
            edge_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_edges)")}
            if "last_evidenced_at" not in edge_columns:
                conn.execute("ALTER TABLE memory_edges ADD COLUMN last_evidenced_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_recall_scope ON memory_nodes(status,scope,retention,id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_recall_nature ON memory_nodes(status,scope,epistemic_nature,id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_nodes_recall_volatility ON memory_nodes(status,scope,epistemic_volatility,id)")
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_recall_snapshots (
                id TEXT PRIMARY KEY, selector TEXT NOT NULL, scoped_nodes INTEGER NOT NULL DEFAULT 0,
                matched_nodes INTEGER NOT NULL DEFAULT 0, selected_nodes INTEGER NOT NULL DEFAULT 0,
                backend TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_recall_items (
                snapshot_id TEXT NOT NULL REFERENCES memory_recall_snapshots(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 1), node_id TEXT NOT NULL,
                source_kind TEXT NOT NULL DEFAULT 'match', PRIMARY KEY(snapshot_id,ordinal), UNIQUE(snapshot_id,node_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_recall_items_cursor ON memory_recall_items(snapshot_id,ordinal)")
            # FTS is rebuilt after the associative-recall column exists.
            conn.execute("UPDATE memory_meta SET value=? WHERE key='schema_version'", (_PRE_ASSOCIATIVE_MEMORY_GRAPH_SCHEMA_VERSION,))
        schema_version = _PRE_ASSOCIATIVE_MEMORY_GRAPH_SCHEMA_VERSION
    if schema_version == _PRE_ASSOCIATIVE_MEMORY_GRAPH_SCHEMA_VERSION:
        # Migration: add Main-authored associative recall metadata. Existing
        # memories remain semantically unchanged; Runtime adds only an empty
        # retrieval-cue object and rebuilds the mechanical FTS index.
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        with conn:
            if "associative_recall" not in columns:
                conn.execute("ALTER TABLE memory_nodes ADD COLUMN associative_recall TEXT NOT NULL DEFAULT '{}'")
            _ensure_fts_schema(conn, rebuild=True)
            conn.execute("UPDATE memory_meta SET value=? WHERE key='schema_version'", (MEMORY_GRAPH_SCHEMA_VERSION,))
        schema_version = MEMORY_GRAPH_SCHEMA_VERSION
    if schema_version != MEMORY_GRAPH_SCHEMA_VERSION:
        raise ValueError("MEMORY_GRAPH_SCHEMA_INCOMPATIBLE")
    required = {
        "memory_meta", "memory_nodes", "memory_tags", "memory_edges", "memory_anchors",
        "memory_changesets", "memory_events", "memory_recall_snapshots", "memory_recall_items",
    }
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables):
        raise ValueError("MEMORY_GRAPH_STORE_INVALID")



def _fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM memory_meta WHERE key='fts5_available'").fetchone()
    return bool(row and str(row[0]) == "1")


def _ensure_fts_schema(conn: sqlite3.Connection, *, rebuild: bool = False) -> bool:
    """Create the optional SQLite FTS5 index used for literal scalable recall.

    FTS is a mechanical text index, never a semantic ranker. If the host SQLite
    lacks FTS5, Memory remains functional using SQL LIKE fallback; Runtime does
    not silently substitute embeddings or another semantic authority.
    """
    try:
        existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'").fetchone()
        if existing is not None:
            cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_fts)")}
            if "recall" not in cols:
                conn.execute("DROP TABLE memory_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "node_id UNINDEXED, content, kind, nature, volatility, temporal, context, tags, recall, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
    except sqlite3.OperationalError:
        conn.execute("INSERT OR REPLACE INTO memory_meta(key,value) VALUES('fts5_available','0')")
        return False
    conn.execute("INSERT OR REPLACE INTO memory_meta(key,value) VALUES('fts5_available','1')")
    if rebuild:
        conn.execute("DELETE FROM memory_fts")
        rows = conn.execute(
            "SELECT id,content,kind,epistemic_nature,epistemic_volatility,epistemic_temporal,epistemic_context,associative_recall "
            "FROM memory_nodes WHERE status='current'"
        ).fetchall()
        for row in rows:
            tags = " ".join(str(item[0]) for item in conn.execute(
                "SELECT tag FROM memory_tags WHERE node_id=? ORDER BY tag COLLATE NOCASE", (str(row["id"]),)
            ))
            conn.execute(
                "INSERT INTO memory_fts(node_id,content,kind,nature,volatility,temporal,context,tags,recall) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(row["id"]), str(row["content"] or ""), str(row["kind"] or ""),
                 str(row["epistemic_nature"] or ""), str(row["epistemic_volatility"] or ""),
                 str(row["epistemic_temporal"] or ""), str(row["epistemic_context"] or ""), tags,
                 str(row["associative_recall"] or "{}")),
            )
    return True


def _refresh_fts_nodes(conn: sqlite3.Connection, node_ids: Iterable[str], *, delete_existing: bool = True) -> None:
    """Refresh FTS rows for a changeset in batches.

    Eyle may accept thousands of Main-authored memory changes in one turn.
    Per-node SELECT/DELETE/INSERT cycles made indexing the dominant write cost,
    so the physical index is maintained once per atomic changeset instead.
    This changes no semantic decision and remains inside the same transaction.
    """
    if not _fts_available(conn):
        return
    ids = list(dict.fromkeys(str(v) for v in node_ids if str(v).strip()))
    if not ids:
        return
    chunk_size = 400
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        marks = ",".join("?" for _ in chunk)
        if delete_existing:
            conn.execute(f"DELETE FROM memory_fts WHERE node_id IN ({marks})", tuple(chunk))
        rows = conn.execute(
            f"SELECT id,content,kind,epistemic_nature,epistemic_volatility,epistemic_temporal,epistemic_context,associative_recall,status "
            f"FROM memory_nodes WHERE id IN ({marks})", tuple(chunk)
        ).fetchall()
        tag_map: dict[str, list[str]] = {}
        for tag_row in conn.execute(
            f"SELECT node_id,tag FROM memory_tags WHERE node_id IN ({marks}) ORDER BY node_id,tag COLLATE NOCASE", tuple(chunk)
        ):
            tag_map.setdefault(str(tag_row["node_id"]), []).append(str(tag_row["tag"]))
        inserts = []
        for row in rows:
            if str(row["status"]) != "current":
                continue
            node_id = str(row["id"])
            inserts.append((
                node_id, str(row["content"] or ""), str(row["kind"] or ""),
                str(row["epistemic_nature"] or ""), str(row["epistemic_volatility"] or ""),
                str(row["epistemic_temporal"] or ""), str(row["epistemic_context"] or ""),
                " ".join(tag_map.get(node_id, [])), str(row["associative_recall"] or "{}"),
            ))
        if inserts:
            conn.executemany(
                "INSERT INTO memory_fts(node_id,content,kind,nature,volatility,temporal,context,tags,recall) VALUES(?,?,?,?,?,?,?,?,?)",
                inserts,
            )


def _refresh_fts_node(conn: sqlite3.Connection, node_id: str) -> None:
    # Compatibility helper for call sites outside changeset application.
    _refresh_fts_nodes(conn, [node_id])

def _node_row(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        raise ValueError(f"MEMORY_NODE_NOT_FOUND:{node_id}")
    return row


def _edge_row(conn: sqlite3.Connection, edge_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM memory_edges WHERE id=?", (edge_id,)).fetchone()
    if row is None:
        raise ValueError(f"MEMORY_EDGE_NOT_FOUND:{edge_id}")
    return row


def _expect_revision(row: sqlite3.Row, expected: Any) -> None:
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ValueError("MEMORY_EXPECTED_REVISION_REQUIRED")
    if int(row["revision"]) != int(expected):
        raise ValueError(f"MEMORY_CONFLICT:{row['id']}:current_revision={row['revision']}")


def _event(conn: sqlite3.Connection, cid: str, entity_type: str, entity_id: str, action: str, revision: int | None, payload: dict[str, Any], now: str) -> None:
    conn.execute(
        "INSERT INTO memory_events(changeset_id,entity_type,entity_id,action,revision,payload,created_at) VALUES(?,?,?,?,?,?,?)",
        (cid, entity_type, entity_id, action, revision, _json(payload), now),
    )


def _insert_anchors(conn: sqlite3.Connection, entity_type: str, entity_id: str, anchors: Iterable[Dict[str, Any]], now: str, *, entity_revision: int) -> list[str]:
    ids: list[str] = []
    for raw in anchors or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("anchor_kind") or "").strip()
        if kind not in {"material", "request", "memory"}:
            raise ValueError("MEMORY_ANCHOR_KIND_INVALID")
        anchor_id = _new_id("ma")
        locator = raw.get("locator") if isinstance(raw.get("locator"), dict) else {}
        source_ref = str(raw.get("source_ref") or "").strip() or None
        if kind == "memory" and (not source_ref or _MEM_ID_RE.fullmatch(source_ref) is None):
            raise ValueError("MEMORY_ANCHOR_MEMORY_REF_INVALID")
        conn.execute(
            "INSERT INTO memory_anchors(id,entity_type,entity_id,anchor_kind,source_capability,locator,source_version,content_hash,freshness_token,freshness_arguments,source_ref,entity_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                anchor_id, entity_type, entity_id, kind,
                str(raw.get("source_capability") or "").strip() or None,
                _json(locator), str(raw.get("source_version") or "").strip() or None,
                str(raw.get("content_hash") or "").strip() or None,
                str(raw.get("freshness_token") or "").strip() or None,
                _json(raw.get("freshness_arguments") or {}),
                source_ref, int(entity_revision), now,
            ),
        )
        ids.append(anchor_id)
    return ids


def apply_graph_operations(
    storage_dir: str,
    operations: Iterable[Dict[str, Any]],
    *,
    execution_id: str | None = None,
    turn: int | None = None,
) -> Dict[str, Any]:
    """Apply one LLM-authored semantic graph delta atomically."""
    ops = [copy.deepcopy(item) for item in operations or [] if isinstance(item, dict)]
    if not ops:
        return {"changeset_id": None, "affected": [], "count": 0}
    cid = _new_id("mcs")
    now = utc_now()
    affected: list[dict[str, Any]] = []
    fts_new: set[str] = set()
    fts_dirty: set[str] = set()
    conn = _connect(storage_dir)
    try:
        with conn:
            conn.execute(
                "INSERT INTO memory_changesets(id,execution_id,turn,created_at) VALUES(?,?,?,?)",
                (cid, str(execution_id or "") or None, int(turn) if turn is not None else None, now),
            )
            for op in ops:
                kind = str(op.get("op") or "").strip()
                if kind == "create_node":
                    node_id = str(op.get("id") or _new_id("mem"))
                    if _MEM_ID_RE.fullmatch(node_id) is None:
                        raise ValueError("MEMORY_NODE_ID_INVALID")
                    scope = _clean_text(op.get("scope"), code="SCOPE", maximum=160)
                    node_kind = _clean_text(op.get("kind"), code="KIND", maximum=96)
                    content = _clean_text(op.get("content"), code="CONTENT")
                    epistemic = _clean_epistemic(op.get("epistemic"))
                    retention = str(op.get("retention") or "persistent").strip().lower()
                    if retention not in _RETENTIONS:
                        raise ValueError("MEMORY_RETENTION_INVALID")
                    tags = _clean_tags(op.get("tags"))
                    recall = _clean_recall_metadata(op.get("recall"))
                    conn.execute(
                        "INSERT INTO memory_nodes(id,scope,kind,content,retention,epistemic_nature,epistemic_confidence,epistemic_volatility,epistemic_temporal,epistemic_context,associative_recall,last_evidenced_at,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (node_id, scope, node_kind, content, retention, epistemic["nature"], epistemic["confidence"], epistemic["volatility"], _json(epistemic["temporal"]), _json(epistemic["context"]), _json(recall), now if op.get("anchors") else None, "current", 1, now, now),
                    )
                    conn.executemany("INSERT INTO memory_tags(node_id,tag) VALUES(?,?)", [(node_id, tag) for tag in tags])
                    anchor_ids = _insert_anchors(conn, "node", node_id, op.get("anchors") or [], now, entity_revision=1)
                    payload = {"scope": scope, "kind": node_kind, "content": content, "retention": retention, "epistemic": epistemic, "recall": recall, "tags": tags, "anchors": anchor_ids}
                    _event(conn, cid, "node", node_id, kind, 1, payload, now)
                    fts_new.add(node_id)
                    affected.append({"type": "node", "id": node_id, "revision": 1, "action": kind})
                elif kind == "update_node":
                    node_id = _clean_text(op.get("id"), code="NODE_ID", maximum=120)
                    row = _node_row(conn, node_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_NODE_NOT_CURRENT:{node_id}")
                    content = str(op.get("content") or "").strip() or str(row["content"])
                    node_kind = str(op.get("kind") or "").strip() or str(row["kind"])
                    if len(node_kind) > 96:
                        raise ValueError("MEMORY_KIND_TOO_LARGE")
                    retention = str(op.get("retention") or row["retention"] or "persistent").strip().lower()
                    current_epistemic = {
                        "nature": row["epistemic_nature"] or "unclassified",
                        "confidence": row["epistemic_confidence"],
                        "volatility": row["epistemic_volatility"] or "unknown",
                        "temporal": _decode(row["epistemic_temporal"], {}),
                        "context": _decode(row["epistemic_context"], {}),
                    }
                    epistemic = _clean_epistemic(op.get("epistemic") if op.get("epistemic") is not None else current_epistemic)
                    current_recall = _decode(row["associative_recall"], {}) if "associative_recall" in row.keys() else {}
                    recall = _merge_recall_metadata(
                        current_recall, replacement=op.get("recall"), additions=op.get("add_recall"), removals=op.get("remove_recall")
                    )
                    if retention not in _RETENTIONS:
                        raise ValueError("MEMORY_RETENTION_INVALID")
                    revision = int(row["revision"]) + 1
                    conn.execute(
                        "UPDATE memory_nodes SET kind=?,content=?,retention=?,epistemic_nature=?,epistemic_confidence=?,epistemic_volatility=?,epistemic_temporal=?,epistemic_context=?,associative_recall=?,last_evidenced_at=CASE WHEN ? THEN ? ELSE last_evidenced_at END,revision=?,updated_at=? WHERE id=?",
                        (node_kind, content, retention, epistemic["nature"], epistemic["confidence"], epistemic["volatility"], _json(epistemic["temporal"]), _json(epistemic["context"]), _json(recall), 1 if op.get("anchors") else 0, now, revision, now, node_id),
                    )
                    add_tags = _clean_tags(op.get("add_tags")); remove_tags = _clean_tags(op.get("remove_tags"))
                    conn.executemany("INSERT OR IGNORE INTO memory_tags(node_id,tag) VALUES(?,?)", [(node_id, tag) for tag in add_tags])
                    conn.executemany("DELETE FROM memory_tags WHERE node_id=? AND tag=? COLLATE NOCASE", [(node_id, tag) for tag in remove_tags])
                    # New anchors supplement provenance history; current trust is evaluated from the current revision anchors.
                    anchor_ids = _insert_anchors(conn, "node", node_id, op.get("anchors") or [], now, entity_revision=revision)
                    payload = {"content": content, "kind": node_kind, "retention": retention, "epistemic": epistemic, "recall": recall, "add_recall": _clean_recall_metadata(op.get("add_recall")), "remove_recall": _clean_recall_metadata(op.get("remove_recall")), "add_tags": add_tags, "remove_tags": remove_tags, "anchors": anchor_ids}
                    _event(conn, cid, "node", node_id, kind, revision, payload, now)
                    fts_dirty.add(node_id)
                    affected.append({"type": "node", "id": node_id, "revision": revision, "action": kind})
                elif kind in {"archive_node", "supersede_node"}:
                    node_id = _clean_text(op.get("id"), code="NODE_ID", maximum=120)
                    row = _node_row(conn, node_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_NODE_NOT_CURRENT:{node_id}")
                    status = "archived" if kind == "archive_node" else "superseded"
                    revision = int(row["revision"]) + 1
                    conn.execute("UPDATE memory_nodes SET status=?,revision=?,updated_at=? WHERE id=?", (status, revision, now, node_id))
                    payload: dict[str, Any] = {"status": status}
                    if kind == "supersede_node":
                        replacement = _clean_text(op.get("replacement"), code="REPLACEMENT", maximum=120)
                        _node_row(conn, replacement)
                        rel_id = _new_id("rel")
                        conn.execute(
                            "INSERT INTO memory_edges(id,source,label,target,epistemic_nature,epistemic_confidence,epistemic_volatility,epistemic_temporal,epistemic_context,last_evidenced_at,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (rel_id, node_id, "superseded_by", replacement, "lifecycle_relation", 1.0, "low", "{}", "{}", None, "current", 1, now, now),
                        )
                        _event(conn, cid, "edge", rel_id, "create_edge", 1, {"source": node_id, "label": "superseded_by", "target": replacement}, now)
                        payload["replacement"] = replacement
                    _event(conn, cid, "node", node_id, kind, revision, payload, now)
                    fts_dirty.add(node_id)
                    affected.append({"type": "node", "id": node_id, "revision": revision, "action": kind})
                elif kind == "create_edge":
                    edge_id = str(op.get("id") or _new_id("rel"))
                    if _REL_ID_RE.fullmatch(edge_id) is None:
                        raise ValueError("MEMORY_EDGE_ID_INVALID")
                    source = _clean_text(op.get("source"), code="SOURCE", maximum=120)
                    target = _clean_text(op.get("target"), code="TARGET", maximum=120)
                    label = _clean_text(op.get("label"), code="RELATION", maximum=120)
                    epistemic = _clean_epistemic(op.get("epistemic") or {"nature": "relation"})
                    _node_row(conn, source); _node_row(conn, target)
                    conn.execute(
                        "INSERT INTO memory_edges(id,source,label,target,epistemic_nature,epistemic_confidence,epistemic_volatility,epistemic_temporal,epistemic_context,last_evidenced_at,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (edge_id, source, label, target, epistemic["nature"], epistemic["confidence"], epistemic["volatility"], _json(epistemic["temporal"]), _json(epistemic["context"]), now if op.get("anchors") else None, "current", 1, now, now),
                    )
                    anchor_ids = _insert_anchors(conn, "edge", edge_id, op.get("anchors") or [], now, entity_revision=1)
                    _event(conn, cid, "edge", edge_id, kind, 1, {"source": source, "label": label, "target": target, "epistemic": epistemic, "anchors": anchor_ids}, now)
                    affected.append({"type": "edge", "id": edge_id, "revision": 1, "action": kind})
                elif kind == "update_edge":
                    edge_id = _clean_text(op.get("id"), code="EDGE_ID", maximum=120)
                    row = _edge_row(conn, edge_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_EDGE_NOT_CURRENT:{edge_id}")
                    label = str(op.get("label") or "").strip() or str(row["label"])
                    if len(label) > 120:
                        raise ValueError("MEMORY_RELATION_TOO_LARGE")
                    current_epistemic = {
                        "nature": row["epistemic_nature"] or "relation",
                        "confidence": row["epistemic_confidence"],
                        "volatility": row["epistemic_volatility"] or "unknown",
                        "temporal": _decode(row["epistemic_temporal"], {}),
                        "context": _decode(row["epistemic_context"], {}),
                    }
                    epistemic = _clean_epistemic(op.get("epistemic") if op.get("epistemic") is not None else current_epistemic)
                    revision = int(row["revision"]) + 1
                    conn.execute(
                        "UPDATE memory_edges SET label=?,epistemic_nature=?,epistemic_confidence=?,epistemic_volatility=?,epistemic_temporal=?,epistemic_context=?,last_evidenced_at=CASE WHEN ? THEN ? ELSE last_evidenced_at END,revision=?,updated_at=? WHERE id=?",
                        (label, epistemic["nature"], epistemic["confidence"], epistemic["volatility"], _json(epistemic["temporal"]), _json(epistemic["context"]), 1 if op.get("anchors") else 0, now, revision, now, edge_id),
                    )
                    anchor_ids = _insert_anchors(conn, "edge", edge_id, op.get("anchors") or [], now, entity_revision=revision)
                    payload = {"label": label, "epistemic": epistemic, "anchors": anchor_ids}
                    _event(conn, cid, "edge", edge_id, kind, revision, payload, now)
                    affected.append({"type": "edge", "id": edge_id, "revision": revision, "action": kind})
                elif kind == "retire_edge":
                    edge_id = _clean_text(op.get("id"), code="EDGE_ID", maximum=120)
                    row = _edge_row(conn, edge_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_EDGE_NOT_CURRENT:{edge_id}")
                    revision = int(row["revision"]) + 1
                    conn.execute("UPDATE memory_edges SET status='retired',revision=?,updated_at=? WHERE id=?", (revision, now, edge_id))
                    _event(conn, cid, "edge", edge_id, kind, revision, {"status": "retired"}, now)
                    affected.append({"type": "edge", "id": edge_id, "revision": revision, "action": kind})
                else:
                    raise ValueError(f"MEMORY_GRAPH_OPERATION_INVALID:{kind or '<empty>'}")
            # New rows never need the expensive delete path. If a node was
            # created and revised in the same changeset, index its final state once.
            fts_dirty.difference_update(fts_new)
            _refresh_fts_nodes(conn, fts_new, delete_existing=False)
            _refresh_fts_nodes(conn, fts_dirty, delete_existing=True)
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("MEMORY_GRAPH_ID_CONFLICT") from exc
        raise ValueError("MEMORY_GRAPH_STORE_INVALID") from exc
    finally:
        conn.close()
    return {"changeset_id": cid, "affected": affected, "count": len(affected)}


def graph_counts(storage_dir: str) -> Dict[str, int]:
    conn = _connect(storage_dir)
    try:
        nodes = int(conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE status='current'").fetchone()[0])
        persistent = int(conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND retention='persistent'").fetchone()[0])
        temporary = int(conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND retention='temporary'").fetchone()[0])
        edges = int(conn.execute(
            "SELECT COUNT(*) FROM memory_edges e JOIN memory_nodes s ON s.id=e.source JOIN memory_nodes t ON t.id=e.target "
            "WHERE e.status='current' AND s.status='current' AND t.status='current'"
        ).fetchone()[0])
        isolated = int(conn.execute(
            "SELECT COUNT(*) FROM memory_nodes n WHERE n.status='current' AND NOT EXISTS "
            "(SELECT 1 FROM memory_edges e JOIN memory_nodes s ON s.id=e.source JOIN memory_nodes t ON t.id=e.target "
            "WHERE e.status='current' AND s.status='current' AND t.status='current' AND (e.source=n.id OR e.target=n.id))"
        ).fetchone()[0])
        return {"nodes": nodes, "persistent_nodes": persistent, "temporary_nodes": temporary, "edges": edges, "isolated_nodes": isolated}
    finally:
        conn.close()


def temporary_graph_records(
    storage_dir: str, *, world_scope_value: str, limit: int = 16,
    include_persistent_neighbors: bool = True, create_cursor: bool = False,
) -> Dict[str, Any]:
    """Materialize only the first automatic temporary-memory page.

    Counting and first-page selection stay inside SQLite. If ``create_cursor`` is
    requested and more nodes exist, a DB-backed exact recall snapshot is created
    for continuation; otherwise no navigation artifact is allocated.
    """
    cap = max(1, int(limit or 16))
    conn = _connect(storage_dir)
    try:
        total = int(conn.execute(
            "SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND retention='temporary' AND scope IN (?,?)",
            ("user", world_scope_value),
        ).fetchone()[0])
        temporary_ids = [str(row["id"]) for row in conn.execute(
            "SELECT id FROM memory_nodes WHERE status='current' AND retention='temporary' AND scope IN (?,?) "
            "ORDER BY updated_at DESC,id DESC LIMIT ?", ("user", world_scope_value, cap)
        )]
        neighbor_ids: list[str] = []
        if include_persistent_neighbors and temporary_ids:
            marks = ",".join("?" for _ in temporary_ids)
            candidates: list[str] = []
            for row in conn.execute(
                f"SELECT e.source,e.target FROM memory_edges e JOIN memory_nodes s ON s.id=e.source JOIN memory_nodes t ON t.id=e.target "
                f"WHERE e.status='current' AND s.status='current' AND t.status='current' AND (e.source IN ({marks}) OR e.target IN ({marks})) ORDER BY e.updated_at DESC,e.id",
                tuple(temporary_ids + temporary_ids),
            ):
                for node_id in (str(row["source"]), str(row["target"])):
                    if node_id not in temporary_ids and node_id not in candidates:
                        candidates.append(node_id)
            if candidates:
                marks2 = ",".join("?" for _ in candidates)
                durable = {str(row["id"]) for row in conn.execute(
                    f"SELECT id FROM memory_nodes WHERE status='current' AND retention='persistent' AND id IN ({marks2})", tuple(candidates)
                )}
                neighbor_ids = [node_id for node_id in candidates if node_id in durable][:cap]
    finally:
        conn.close()
    snapshot_id = ""
    if create_cursor and total > len(temporary_ids):
        created = create_recall_snapshot(
            storage_dir, world_scope_value=world_scope_value, scope="all", retention="temporary",
            select_all=True, include_neighbors=False, order_mode="updated",
        )
        snapshot_id = str(created["snapshot_id"])
    records = graph_records(storage_dir, [*temporary_ids, *neighbor_ids])
    return {
        **records,
        "temporary_node_ids": temporary_ids,
        "linked_persistent_node_ids": neighbor_ids,
        "recall_snapshot_id": snapshot_id or None,
        "recall_after_ordinal": len(temporary_ids),
        "total_temporary_nodes": total,
        "remaining_temporary_nodes": max(0, total - len(temporary_ids)),
        "search_backend": memory_search_backend(storage_dir),
        "complete": len(temporary_ids) >= total,
    }


def _anchors_for(conn: sqlite3.Connection, entity_type: str, entity_id: str, entity_revision: int | None = None) -> List[Dict[str, Any]]:
    if entity_revision is None:
        rows = conn.execute(
            "SELECT * FROM memory_anchors WHERE entity_type=? AND entity_id=? ORDER BY entity_revision,created_at,id",
            (entity_type, entity_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memory_anchors WHERE entity_type=? AND entity_id=? AND entity_revision=? ORDER BY created_at,id",
            (entity_type, entity_id, int(entity_revision)),
        ).fetchall()
        # A metadata-only revision may intentionally inherit the latest earlier provenance.
        if not rows:
            rows = conn.execute(
                "SELECT * FROM memory_anchors WHERE entity_type=? AND entity_id=? AND entity_revision=(SELECT MAX(entity_revision) FROM memory_anchors WHERE entity_type=? AND entity_id=? AND entity_revision<?) ORDER BY created_at,id",
                (entity_type, entity_id, entity_type, entity_id, int(entity_revision)),
            ).fetchall()
    out = []
    for row in rows:
        out.append({
            "id": row["id"], "anchor_kind": row["anchor_kind"],
            "source_capability": row["source_capability"], "locator": _decode(row["locator"], {}),
            "source_version": row["source_version"], "content_hash": row["content_hash"],
            "freshness_token": row["freshness_token"], "freshness_arguments": _decode(row["freshness_arguments"], {}), "source_ref": row["source_ref"],
            "entity_revision": int(row["entity_revision"]),
        })
    return out


def _node_record(conn: sqlite3.Connection, row: sqlite3.Row, *, include_edges: bool = True) -> Dict[str, Any]:
    node_id = str(row["id"])
    tags = [item[0] for item in conn.execute("SELECT tag FROM memory_tags WHERE node_id=? ORDER BY tag COLLATE NOCASE", (node_id,))]
    out = {
        "id": node_id, "scope": row["scope"], "kind": row["kind"], "content": row["content"],
        "retention": row["retention"], "status": row["status"], "revision": int(row["revision"]), "tags": tags,
        "recall": _decode(row["associative_recall"], {}) if "associative_recall" in row.keys() else {},
        "epistemic": {
            "nature": row["epistemic_nature"] or "unclassified",
            "confidence": row["epistemic_confidence"],
            "volatility": row["epistemic_volatility"] or "unknown",
            "temporal": _decode(row["epistemic_temporal"], {}),
            "context": _decode(row["epistemic_context"], {}),
            "last_evidenced_at": row["last_evidenced_at"],
        },
        "anchors": _anchors_for(conn, "node", node_id, int(row["revision"])),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if include_edges:
        out["edges"] = [dict(item) for item in conn.execute(
            "SELECT id,source,label,target,status,revision FROM memory_edges WHERE status='current' AND (source=? OR target=?) ORDER BY id",
            (node_id, node_id),
        )]
    return out


def node_record(storage_dir: str, node_id: str) -> Dict[str, Any]:
    conn = _connect(storage_dir)
    try:
        return _node_record(conn, _node_row(conn, str(node_id)))
    finally:
        conn.close()



def node_history(storage_dir: str, node_id: str) -> Dict[str, Any]:
    """Return the complete persisted revision/event history for one memory node.

    This is mechanical history, not semantic consolidation. Main decides what a
    change means. No fixed event-count ceiling is imposed here; callers may add
    physical pagination later without changing the cognitive contract.
    """
    conn = _connect(storage_dir)
    try:
        current = _node_record(conn, _node_row(conn, str(node_id)))
        rows = conn.execute(
            "SELECT seq,changeset_id,action,revision,payload,created_at FROM memory_events "
            "WHERE entity_type='node' AND entity_id=? ORDER BY seq",
            (str(node_id),),
        ).fetchall()
        events = [
            {
                "seq": int(row["seq"]),
                "changeset_id": row["changeset_id"],
                "action": row["action"],
                "revision": row["revision"],
                "payload": _decode(row["payload"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        relations = [dict(row) for row in conn.execute(
            "SELECT * FROM memory_edges WHERE source=? OR target=? ORDER BY created_at,id", (str(node_id), str(node_id))
        )]
        for relation in relations:
            relation["epistemic"] = {
                "nature": relation.pop("epistemic_nature", None) or "relation",
                "confidence": relation.pop("epistemic_confidence", None),
                "volatility": relation.pop("epistemic_volatility", None) or "unknown",
                "temporal": _decode(relation.pop("epistemic_temporal", None), {}),
                "context": _decode(relation.pop("epistemic_context", None), {}),
            }
        return {"node": current, "events": events, "relations": relations}
    finally:
        conn.close()

def edge_record(storage_dir: str, edge_id: str) -> Dict[str, Any]:
    conn = _connect(storage_dir)
    try:
        row = _edge_row(conn, str(edge_id))
        return {
            "id": row["id"], "source": row["source"], "label": row["label"], "target": row["target"],
            "epistemic": {"nature": row["epistemic_nature"] or "relation", "confidence": row["epistemic_confidence"], "volatility": row["epistemic_volatility"] or "unknown", "temporal": _decode(row["epistemic_temporal"], {}), "context": _decode(row["epistemic_context"], {})},
            "status": row["status"], "revision": int(row["revision"]), "last_evidenced_at": row["last_evidenced_at"],
            "anchors": _anchors_for(conn, "edge", str(row["id"]), int(row["revision"])) ,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
    finally:
        conn.close()



def edge_history(storage_dir: str, edge_id: str) -> Dict[str, Any]:
    """Return the persisted revision/event history for one relation."""
    conn = _connect(storage_dir)
    try:
        current = edge_record(storage_dir, edge_id)
        rows = conn.execute(
            "SELECT seq,changeset_id,action,revision,payload,created_at FROM memory_events "
            "WHERE entity_type='edge' AND entity_id=? ORDER BY seq", (str(edge_id),)
        ).fetchall()
        events = [{
            "seq": int(row["seq"]), "changeset_id": row["changeset_id"], "action": row["action"],
            "revision": row["revision"], "payload": _decode(row["payload"], {}), "created_at": row["created_at"],
        } for row in rows]
        return {"relation": current, "events": events}
    finally:
        conn.close()

def _scope_values(world_scope_value: str, scope: str) -> tuple[str, ...]:
    selected = str(scope or "all").strip().lower()
    if selected == "user":
        return ("user",)
    if selected == "world":
        return (world_scope_value,)
    if selected != "all":
        raise ValueError("MEMORY_SCOPE_INVALID")
    return ("user", world_scope_value)


def graph_overview(
    storage_dir: str,
    *,
    world_scope_value: str,
    scope: str = "all",
    tag_limit: int | None = None,
    kind_limit: int | None = None,
) -> Dict[str, Any]:
    """Return a compact read-only directory of Memory without materializing bodies."""
    scopes = _scope_values(world_scope_value, scope)
    marks = ",".join("?" for _ in scopes)
    conn = _connect(storage_dir)
    try:
        nodes = int(conn.execute(
            f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks})", scopes,
        ).fetchone()[0])
        edges = int(conn.execute(
            f"SELECT COUNT(*) FROM memory_edges e JOIN memory_nodes s ON s.id=e.source "
            f"JOIN memory_nodes t ON t.id=e.target WHERE e.status='current' AND s.status='current' "
            f"AND t.status='current' AND s.scope IN ({marks}) AND t.scope IN ({marks})",
            tuple(scopes + scopes),
        ).fetchone()[0])
        kind_sql = f"SELECT kind,COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) GROUP BY kind ORDER BY COUNT(*) DESC,kind"
        kind_args = tuple(scopes)
        if kind_limit is not None:
            kind_sql += " LIMIT ?"; kind_args += (max(1, int(kind_limit)),)
        kinds = [{"kind": str(row[0]), "count": int(row[1])} for row in conn.execute(kind_sql, kind_args)]
        tag_sql = f"SELECT t.tag,COUNT(*) FROM memory_tags t JOIN memory_nodes n ON n.id=t.node_id WHERE n.status='current' AND n.scope IN ({marks}) GROUP BY t.tag ORDER BY COUNT(*) DESC,t.tag COLLATE NOCASE"
        tag_args = tuple(scopes)
        if tag_limit is not None:
            tag_sql += " LIMIT ?"; tag_args += (max(1, int(tag_limit)),)
        tags = [{"tag": str(row[0]), "count": int(row[1])} for row in conn.execute(tag_sql, tag_args)]
        retentions = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                f"SELECT retention,COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) GROUP BY retention", scopes,
            )
        }
        epistemic_natures = [{"nature": str(row[0]), "count": int(row[1])} for row in conn.execute(
            f"SELECT epistemic_nature,COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) GROUP BY epistemic_nature ORDER BY COUNT(*) DESC,epistemic_nature", scopes,
        )]
        volatilities = [{"volatility": str(row[0]), "count": int(row[1])} for row in conn.execute(
            f"SELECT epistemic_volatility,COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) GROUP BY epistemic_volatility ORDER BY COUNT(*) DESC,epistemic_volatility", scopes,
        )]
        confidence = {
            "classified": int(conn.execute(f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) AND epistemic_confidence IS NOT NULL", scopes).fetchone()[0]),
            "unclassified": int(conn.execute(f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) AND epistemic_confidence IS NULL", scopes).fetchone()[0]),
        }
        relation_labels = [{"relation": str(row[0]), "count": int(row[1])} for row in conn.execute(
            f"SELECT e.label,COUNT(*) FROM memory_edges e JOIN memory_nodes s ON s.id=e.source JOIN memory_nodes t ON t.id=e.target "
            f"WHERE e.status='current' AND s.status='current' AND t.status='current' AND s.scope IN ({marks}) AND t.scope IN ({marks}) "
            "GROUP BY e.label ORDER BY COUNT(*) DESC,e.label COLLATE NOCASE", tuple(scopes + scopes),
        )]
        consolidation = {
            "isolated_nodes": int(conn.execute(
                f"SELECT COUNT(*) FROM memory_nodes n WHERE n.status='current' AND n.scope IN ({marks}) AND NOT EXISTS ("
                "SELECT 1 FROM memory_edges e JOIN memory_nodes s ON s.id=e.source JOIN memory_nodes t ON t.id=e.target "
                "WHERE e.status='current' AND s.status='current' AND t.status='current' AND (e.source=n.id OR e.target=n.id))", scopes,
            ).fetchone()[0]),
            "revised_nodes": int(conn.execute(
                f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) AND revision>1", scopes,
            ).fetchone()[0]),
            "evidenced_nodes": int(conn.execute(
                f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) AND last_evidenced_at IS NOT NULL", scopes,
            ).fetchone()[0]),
            "associatively_described_nodes": int(conn.execute(
                f"SELECT COUNT(*) FROM memory_nodes WHERE status='current' AND scope IN ({marks}) AND associative_recall<>'{{}}'", scopes,
            ).fetchone()[0]),
        }
        return {"scope": scope, "nodes": nodes, "edges": edges, "retention": {"temporary": retentions.get("temporary", 0), "persistent": retentions.get("persistent", 0)}, "epistemic_natures": epistemic_natures, "volatility": volatilities, "confidence": confidence, "kinds": kinds, "tags": tags, "relation_labels": relation_labels, "consolidation": consolidation, "search_backend": "fts5" if _fts_available(conn) else "sql_like"}
    finally:
        conn.close()


def _terms(values: Iterable[Any]) -> List[str]:
    stop = {
        "para", "como", "onde", "quando", "qual", "quais", "uma", "uns", "das", "dos",
        "the", "and", "with", "from", "that", "this", "what", "how",
    }
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in re.findall(r"[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ0-9_.-]{1,}", str(value or "").casefold()):
            if token in stop or token in seen:
                continue
            seen.add(token)
            out.append(token)
    return out


def memory_search_backend(storage_dir: str) -> str:
    conn = _connect(storage_dir)
    try:
        return "fts5" if _fts_available(conn) else "sql_like"
    finally:
        conn.close()


def _fts_expression(terms: Iterable[str]) -> str:
    # Terms are already mechanically tokenized; quoting prevents FTS operators
    # from becoming a second query language owned by Runtime.
    return " OR ".join('"' + str(term).replace('"', '""') + '"' for term in terms if str(term))


def _base_recall_where(
    *, scopes: tuple[str, ...], retention: str, natures: Iterable[str], volatilities: Iterable[str],
    include_epistemic: bool = True,
) -> tuple[str, list[Any]]:
    marks = ",".join("?" for _ in scopes)
    clauses = ["n.status='current'", f"n.scope IN ({marks})"]
    params: list[Any] = list(scopes)
    if retention != "all":
        clauses.append("n.retention=?")
        params.append(retention)
    if include_epistemic:
        nature_values = [str(v).strip() for v in natures or [] if str(v).strip()]
        if nature_values:
            marks2 = ",".join("?" for _ in nature_values)
            clauses.append(f"n.epistemic_nature COLLATE NOCASE IN ({marks2})")
            params.extend(nature_values)
        volatility_values = [str(v).strip() for v in volatilities or [] if str(v).strip()]
        if volatility_values:
            marks3 = ",".join("?" for _ in volatility_values)
            clauses.append(f"n.epistemic_volatility COLLATE NOCASE IN ({marks3})")
            params.extend(volatility_values)
    return " AND ".join(clauses), params


def create_recall_snapshot(
    storage_dir: str,
    *,
    world_scope_value: str,
    query: str = "",
    queries: Iterable[str] = (),
    ids: Iterable[str] = (),
    tags: Iterable[str] = (),
    scope: str = "all",
    include_neighbors: bool = False,
    retention: str = "all",
    natures: Iterable[str] = (),
    volatilities: Iterable[str] = (),
    relation_labels: Iterable[str] = (),
    select_all: bool = False,
    order_mode: str = "relevance",
) -> Dict[str, Any]:
    """Persist an exact DB-backed recall selection without loading all IDs in Python.

    The snapshot stores only node IDs + ordinal in SQLite. Frontier continuation
    therefore remains exact even for very large match sets while Session carries
    only a small cursor. FTS5 is used when available; otherwise SQL performs a
    literal LIKE fallback. Neither path performs embeddings or semantic ranking.
    """
    scopes = _scope_values(world_scope_value, scope)
    retention_value = str(retention or "all").strip().lower()
    if retention_value not in {"all", "temporary", "persistent"}:
        raise ValueError("MEMORY_RETENTION_INVALID")
    wanted_ids = [str(v).strip() for v in ids or [] if str(v).strip()]
    wanted_tags = [str(v).strip() for v in tags or [] if str(v).strip()]
    wanted_natures = [str(v).strip() for v in natures or [] if str(v).strip()]
    wanted_volatilities = [str(v).strip() for v in volatilities or [] if str(v).strip()]
    wanted_relation_labels = [str(v).strip() for v in relation_labels or [] if str(v).strip()]
    query_variants = [str(v).strip() for v in queries or [] if str(v).strip()]
    terms = _terms([query, *query_variants])
    epistemic_only = bool(wanted_natures or wanted_volatilities or wanted_relation_labels) and not (terms or wanted_ids or wanted_tags)
    if not (select_all or epistemic_only or terms or wanted_ids or wanted_tags):
        raise ValueError("MEMORY_SELECTOR_REQUIRED")

    snapshot_id = _new_id("mrs")
    selector = {
        "query": str(query or "")[:4000], "queries": query_variants, "ids": wanted_ids, "tags": wanted_tags,
        "scope": scope, "retention": retention_value, "natures": wanted_natures,
        "volatilities": wanted_volatilities, "relation_labels": wanted_relation_labels, "include_neighbors": bool(include_neighbors),
        "terms": terms, "select_all": bool(select_all), "order_mode": str(order_mode or "relevance"),
    }
    conn = _connect(storage_dir)
    try:
        backend = "fts5" if _fts_available(conn) else "sql_like"
        scope_where, scope_params = _base_recall_where(
            scopes=scopes, retention=retention_value, natures=(), volatilities=(), include_epistemic=False,
        )
        base_where, base_params = _base_recall_where(
            scopes=scopes, retention=retention_value, natures=wanted_natures, volatilities=wanted_volatilities,
        )
        if wanted_relation_labels:
            rel_marks = ",".join("?" for _ in wanted_relation_labels)
            base_where += (
                " AND EXISTS (SELECT 1 FROM memory_edges re WHERE re.status='current' "
                f"AND (re.source=n.id OR re.target=n.id) AND re.label COLLATE NOCASE IN ({rel_marks}))"
            )
            base_params.extend(wanted_relation_labels)
        scoped_nodes = int(conn.execute(f"SELECT COUNT(*) FROM memory_nodes n WHERE {scope_where}", tuple(scope_params)).fetchone()[0])

        tag_cte = "tag_hits AS (SELECT NULL AS node_id,0 AS hits WHERE 0)"
        tag_params: list[Any] = []
        if wanted_tags:
            tag_marks = ",".join("?" for _ in wanted_tags)
            tag_cte = (
                "tag_hits AS (SELECT t.node_id,COUNT(*) AS hits FROM memory_tags t "
                "JOIN base b ON b.id=t.node_id WHERE t.tag COLLATE NOCASE IN (" + tag_marks + ") GROUP BY t.node_id)"
            )
            tag_params.extend(wanted_tags)

        query_cte = "query_hits AS (SELECT NULL AS node_id,0.0 AS rank WHERE 0)"
        query_params: list[Any] = []
        if terms and backend == "fts5":
            query_cte = (
                "query_hits AS (SELECT f.node_id,bm25(memory_fts) AS rank FROM memory_fts f "
                "JOIN base b ON b.id=f.node_id WHERE memory_fts MATCH ?)"
            )
            query_params.append(_fts_expression(terms))
        elif terms:
            pieces = []
            for term in terms:
                pieces.append(
                    "(LOWER(b.content || ' ' || b.kind || ' ' || b.epistemic_nature || ' ' || "
                    "b.epistemic_volatility || ' ' || b.epistemic_temporal || ' ' || b.epistemic_context || ' ' || b.associative_recall) LIKE ? "
                    "OR EXISTS (SELECT 1 FROM memory_tags qt WHERE qt.node_id=b.id AND LOWER(qt.tag) LIKE ?))"
                )
                like = f"%{term.casefold()}%"
                query_params.extend([like, like])
            query_cte = "query_hits AS (SELECT b.id AS node_id,0.0 AS rank FROM base b WHERE " + " OR ".join(pieces) + ")"

        direct_expr = "0"
        direct_params: list[Any] = []
        if wanted_ids:
            id_marks = ",".join("?" for _ in wanted_ids)
            direct_expr = f"CASE WHEN b.id IN ({id_marks}) THEN 1 ELSE 0 END"
            direct_params.extend(wanted_ids)
        include_all_flag = 1 if (select_all or epistemic_only) else 0
        raw_cte = (
            "raw AS (SELECT b.id,b.updated_at," + direct_expr + " AS direct_hit,"
            "COALESCE(th.hits,0) AS tag_hits,CASE WHEN qh.node_id IS NULL THEN 0 ELSE 1 END AS query_hit,"
            "COALESCE(qh.rank,0.0) AS query_rank FROM base b LEFT JOIN tag_hits th ON th.node_id=b.id "
            "LEFT JOIN query_hits qh ON qh.node_id=b.id),"
            "candidates AS (SELECT * FROM raw WHERE direct_hit=1 OR tag_hits>0 OR query_hit=1 OR ?=1)"
        )
        if str(order_mode or "relevance") == "updated":
            order_clause = "updated_at DESC,id DESC"
        else:
            order_clause = "direct_hit DESC,tag_hits DESC,query_hit DESC,query_rank ASC,updated_at DESC,id DESC"
        sql = (
            "WITH base AS (SELECT n.id,n.updated_at,n.content,n.kind,n.epistemic_nature,n.epistemic_volatility,"
            f"n.epistemic_temporal,n.epistemic_context,n.associative_recall FROM memory_nodes n WHERE {base_where}),"
            + tag_cte + "," + query_cte + "," + raw_cte +
            " INSERT INTO memory_recall_items(snapshot_id,ordinal,node_id,source_kind) "
            "SELECT ?,ROW_NUMBER() OVER (ORDER BY " + order_clause + "),id,'match' FROM candidates"
        )
        params = [*base_params, *tag_params, *query_params, *direct_params, include_all_flag, snapshot_id]
        now = utc_now()
        with conn:
            conn.execute(
                "INSERT INTO memory_recall_snapshots(id,selector,scoped_nodes,matched_nodes,selected_nodes,backend,created_at) VALUES(?,?,?,?,?,?,?)",
                (snapshot_id, _json(selector), scoped_nodes, 0, 0, backend, now),
            )
            conn.execute(sql, tuple(params))
            matched = int(conn.execute("SELECT COUNT(*) FROM memory_recall_items WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0])
            if include_neighbors and matched:
                # Neighbours are a mechanical one-hop expansion of the exact match
                # set. Epistemic filters apply to direct matches; neighbours retain
                # only scope/retention boundaries, matching the historical contract.
                neighbor_where, neighbor_params = _base_recall_where(
                    scopes=scopes, retention=retention_value, natures=(), volatilities=(), include_epistemic=False,
                )
                conn.execute(
                    "WITH matched AS (SELECT node_id FROM memory_recall_items WHERE snapshot_id=? AND source_kind='match'),"
                    "neighbors AS (SELECT DISTINCT CASE WHEN e.source=m.node_id THEN e.target ELSE e.source END AS node_id "
                    "FROM memory_edges e JOIN matched m ON (e.source=m.node_id OR e.target=m.node_id) WHERE e.status='current'),"
                    "allowed AS (SELECT n.id FROM memory_nodes n JOIN neighbors x ON x.node_id=n.id WHERE " + neighbor_where + "),"
                    "new_neighbors AS (SELECT a.id FROM allowed a WHERE NOT EXISTS (SELECT 1 FROM memory_recall_items i WHERE i.snapshot_id=? AND i.node_id=a.id)) "
                    "INSERT INTO memory_recall_items(snapshot_id,ordinal,node_id,source_kind) "
                    "SELECT ?,? + ROW_NUMBER() OVER (ORDER BY id),id,'neighbor' FROM new_neighbors",
                    tuple([snapshot_id, *neighbor_params, snapshot_id, snapshot_id, matched]),
                )
            selected = int(conn.execute("SELECT COUNT(*) FROM memory_recall_items WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0])
            conn.execute(
                "UPDATE memory_recall_snapshots SET matched_nodes=?,selected_nodes=? WHERE id=?",
                (matched, selected, snapshot_id),
            )
        return {
            "snapshot_id": snapshot_id,
            "selection": {
                "scope": scope, "retention": retention_value, "query": str(query or "")[:1000], "queries": query_variants,
                "ids": wanted_ids, "tags": wanted_tags, "natures": wanted_natures,
                "volatilities": wanted_volatilities, "relation_labels": wanted_relation_labels, "include_neighbors": bool(include_neighbors),
                "terms": terms, "scoped_nodes": scoped_nodes, "matched_nodes": matched,
                "selected_nodes": selected, "backend": backend, "db_cursor": True,
            },
        }
    except Exception:
        try:
            with conn:
                conn.execute("DELETE FROM memory_recall_snapshots WHERE id=?", (snapshot_id,))
        except Exception:
            pass
        raise
    finally:
        conn.close()


def recall_snapshot_page(storage_dir: str, snapshot_id: str, *, after_ordinal: int = 0, limit: int = 30) -> Dict[str, Any]:
    cap = max(1, int(limit or 30))
    cursor = max(0, int(after_ordinal or 0))
    conn = _connect(storage_dir)
    try:
        meta = conn.execute("SELECT * FROM memory_recall_snapshots WHERE id=?", (str(snapshot_id),)).fetchone()
        if meta is None:
            raise ValueError("MEMORY_RECALL_SNAPSHOT_NOT_FOUND")
        rows = conn.execute(
            "SELECT ordinal,node_id,source_kind FROM memory_recall_items WHERE snapshot_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
            (str(snapshot_id), cursor, cap),
        ).fetchall()
        ids = [str(row["node_id"]) for row in rows]
        last_ordinal = int(rows[-1]["ordinal"]) if rows else cursor
        total = int(meta["selected_nodes"])
        remaining = max(0, total - last_ordinal)
        return {
            "snapshot_id": str(snapshot_id), "node_ids": ids, "after_ordinal": cursor,
            "last_ordinal": last_ordinal, "remaining": remaining, "complete": remaining == 0,
            "selection": {
                **(_decode(meta["selector"], {}) if isinstance(meta["selector"], str) else {}),
                "scoped_nodes": int(meta["scoped_nodes"]), "matched_nodes": int(meta["matched_nodes"]),
                "selected_nodes": total, "backend": str(meta["backend"]), "db_cursor": True,
            },
        }
    finally:
        conn.close()


def release_recall_snapshot(storage_dir: str, snapshot_id: str) -> None:
    conn = _connect(storage_dir)
    try:
        with conn:
            conn.execute("DELETE FROM memory_recall_snapshots WHERE id=?", (str(snapshot_id),))
    finally:
        conn.close()


def select_graph_nodes(
    storage_dir: str,
    *,
    world_scope_value: str,
    query: str = "",
    queries: Iterable[str] = (),
    ids: Iterable[str] = (),
    tags: Iterable[str] = (),
    scope: str = "all",
    include_neighbors: bool = False,
    retention: str = "all",
    natures: Iterable[str] = (),
    volatilities: Iterable[str] = (),
    relation_labels: Iterable[str] = (),
) -> Dict[str, Any]:
    """Compatibility/debug materialization of the scalable DB-backed selector.

    Core recall does not use this function for pagination anymore; it exists for
    tests/tools that explicitly request the complete ID set.
    """
    created = create_recall_snapshot(
        storage_dir, world_scope_value=world_scope_value, query=query, queries=queries, ids=ids, tags=tags,
        scope=scope, include_neighbors=include_neighbors, retention=retention,
        natures=natures, volatilities=volatilities, relation_labels=relation_labels,
    )
    snapshot_id = str(created["snapshot_id"])
    try:
        conn = _connect(storage_dir)
        try:
            all_ids = [str(row[0]) for row in conn.execute(
                "SELECT node_id FROM memory_recall_items WHERE snapshot_id=? ORDER BY ordinal", (snapshot_id,)
            )]
        finally:
            conn.close()
        return {"node_ids": all_ids, "reasons": {}, "selection": created["selection"]}
    finally:
        release_recall_snapshot(storage_dir, snapshot_id)


def graph_records(storage_dir: str, node_ids: Iterable[str], *, include_inactive: bool = False) -> Dict[str, Any]:
    """Materialize exact Memory node IDs and only edges between those IDs.

    DB-backed recall snapshots may continue after Main has revised/archived a
    selected node. ``include_inactive=True`` preserves that exact Frontier item
    and exposes its current lifecycle status instead of silently dropping it.
    """
    ids = []
    seen: set[str] = set()
    for raw in node_ids or []:
        node_id = str(raw or "").strip()
        if node_id and node_id not in seen:
            seen.add(node_id); ids.append(node_id)
    if not ids:
        return {"nodes": [], "edges": []}
    conn = _connect(storage_dir)
    try:
        marks = ",".join("?" for _ in ids)
        status_clause = "" if include_inactive else "status='current' AND "
        row_map = {str(row["id"]): row for row in conn.execute(
            f"SELECT * FROM memory_nodes WHERE {status_clause}id IN ({marks})", tuple(ids),
        )}
        nodes = [_node_record(conn, row_map[node_id], include_edges=False) for node_id in ids if node_id in row_map]
        present = [str(node["id"]) for node in nodes]
        edges: list[dict[str, Any]] = []
        if present:
            edge_marks = ",".join("?" for _ in present)
            for row in conn.execute(
                f"SELECT * FROM memory_edges WHERE status='current' AND source IN ({edge_marks}) AND target IN ({edge_marks}) ORDER BY id",
                tuple(present + present),
            ):
                edges.append({
                    "id": row["id"], "source": row["source"], "label": row["label"], "target": row["target"],
                    "epistemic": {
                        "nature": row["epistemic_nature"] or "relation",
                        "confidence": row["epistemic_confidence"],
                        "volatility": row["epistemic_volatility"] or "unknown",
                        "temporal": _decode(row["epistemic_temporal"], {}),
                        "context": _decode(row["epistemic_context"], {}),
                    },
                    "revision": int(row["revision"]),
                    "anchors": _anchors_for(conn, "edge", str(row["id"]), int(row["revision"])),
                })
        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()

