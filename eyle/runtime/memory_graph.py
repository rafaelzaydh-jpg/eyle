"""Persistent graph memory mechanics for the ECC general-agent core.

This module is deliberately *not* a capability provider.  It is internal
Runtime infrastructure used before/after every ECC decision.  Main owns the
semantic meaning of nodes/edges and chooses graph deltas; Runtime owns IDs,
SQLite durability, revisions, hashes/freshness checks and graph metrics.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

MEMORY_GRAPH_SCHEMA_VERSION = "2.7.5-r2.5-memory-graph-v2"
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


def _clean_text(value: Any, *, code: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"MEMORY_{code}_EMPTY")
    if len(text) > maximum:
        raise ValueError(f"MEMORY_{code}_TOO_LARGE")
    return text


def _clean_tags(values: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        tag = str(raw or "").strip()
        if not tag:
            continue
        if len(tag) > 96:
            raise ValueError("MEMORY_TAG_TOO_LARGE")
        folded = tag.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(tag)
    return out


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
                    status TEXT NOT NULL CHECK(status IN ('current','archived','superseded')),
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    retrieval_count INTEGER NOT NULL DEFAULT 0 CHECK(retrieval_count >= 0),
                    last_retrieved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_nodes_scope_status_updated ON memory_nodes(scope,status,updated_at DESC);
                CREATE INDEX idx_memory_nodes_kind_status ON memory_nodes(kind,status);
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
                """
            )
            conn.execute("INSERT INTO memory_meta(key,value) VALUES('schema_version',?)", (MEMORY_GRAPH_SCHEMA_VERSION,))
        return
    rows = dict(conn.execute("SELECT key,value FROM memory_meta").fetchall())
    if rows.get("schema_version") != MEMORY_GRAPH_SCHEMA_VERSION:
        raise ValueError("MEMORY_GRAPH_SCHEMA_INCOMPATIBLE")
    required = {
        "memory_meta", "memory_nodes", "memory_tags", "memory_edges", "memory_anchors",
        "memory_changesets", "memory_events",
    }
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables):
        raise ValueError("MEMORY_GRAPH_STORE_INVALID")


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
                    content = _clean_text(op.get("content"), code="CONTENT", maximum=12000)
                    tags = _clean_tags(op.get("tags"))
                    conn.execute(
                        "INSERT INTO memory_nodes(id,scope,kind,content,status,revision,retrieval_count,last_retrieved_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (node_id, scope, node_kind, content, "current", 1, 0, None, now, now),
                    )
                    conn.executemany("INSERT INTO memory_tags(node_id,tag) VALUES(?,?)", [(node_id, tag) for tag in tags])
                    anchor_ids = _insert_anchors(conn, "node", node_id, op.get("anchors") or [], now, entity_revision=1)
                    payload = {"scope": scope, "kind": node_kind, "content": content, "tags": tags, "anchors": anchor_ids}
                    _event(conn, cid, "node", node_id, kind, 1, payload, now)
                    affected.append({"type": "node", "id": node_id, "revision": 1, "action": kind})
                elif kind == "update_node":
                    node_id = _clean_text(op.get("id"), code="NODE_ID", maximum=120)
                    row = _node_row(conn, node_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_NODE_NOT_CURRENT:{node_id}")
                    content = str(op.get("content") or "").strip() or str(row["content"])
                    if len(content) > 12000:
                        raise ValueError("MEMORY_CONTENT_TOO_LARGE")
                    node_kind = str(op.get("kind") or "").strip() or str(row["kind"])
                    if len(node_kind) > 96:
                        raise ValueError("MEMORY_KIND_TOO_LARGE")
                    revision = int(row["revision"]) + 1
                    conn.execute(
                        "UPDATE memory_nodes SET kind=?,content=?,revision=?,updated_at=? WHERE id=?",
                        (node_kind, content, revision, now, node_id),
                    )
                    add_tags = _clean_tags(op.get("add_tags")); remove_tags = _clean_tags(op.get("remove_tags"))
                    conn.executemany("INSERT OR IGNORE INTO memory_tags(node_id,tag) VALUES(?,?)", [(node_id, tag) for tag in add_tags])
                    conn.executemany("DELETE FROM memory_tags WHERE node_id=? AND tag=? COLLATE NOCASE", [(node_id, tag) for tag in remove_tags])
                    # New anchors supplement provenance history; current trust is evaluated from the current revision anchors.
                    anchor_ids = _insert_anchors(conn, "node", node_id, op.get("anchors") or [], now, entity_revision=revision)
                    payload = {"content": content, "kind": node_kind, "add_tags": add_tags, "remove_tags": remove_tags, "anchors": anchor_ids}
                    _event(conn, cid, "node", node_id, kind, revision, payload, now)
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
                            "INSERT INTO memory_edges(id,source,label,target,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            (rel_id, node_id, "superseded_by", replacement, "current", 1, now, now),
                        )
                        _event(conn, cid, "edge", rel_id, "create_edge", 1, {"source": node_id, "label": "superseded_by", "target": replacement}, now)
                        payload["replacement"] = replacement
                    _event(conn, cid, "node", node_id, kind, revision, payload, now)
                    affected.append({"type": "node", "id": node_id, "revision": revision, "action": kind})
                elif kind == "create_edge":
                    edge_id = str(op.get("id") or _new_id("rel"))
                    if _REL_ID_RE.fullmatch(edge_id) is None:
                        raise ValueError("MEMORY_EDGE_ID_INVALID")
                    source = _clean_text(op.get("source"), code="SOURCE", maximum=120)
                    target = _clean_text(op.get("target"), code="TARGET", maximum=120)
                    label = _clean_text(op.get("label"), code="RELATION", maximum=120)
                    _node_row(conn, source); _node_row(conn, target)
                    conn.execute(
                        "INSERT INTO memory_edges(id,source,label,target,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (edge_id, source, label, target, "current", 1, now, now),
                    )
                    anchor_ids = _insert_anchors(conn, "edge", edge_id, op.get("anchors") or [], now, entity_revision=1)
                    _event(conn, cid, "edge", edge_id, kind, 1, {"source": source, "label": label, "target": target, "anchors": anchor_ids}, now)
                    affected.append({"type": "edge", "id": edge_id, "revision": 1, "action": kind})
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
        edges = int(conn.execute("SELECT COUNT(*) FROM memory_edges WHERE status='current'").fetchone()[0])
        cold = int(conn.execute(
            "SELECT COUNT(*) FROM memory_nodes n WHERE n.status='current' AND NOT EXISTS (SELECT 1 FROM memory_edges e WHERE e.status='current' AND (e.source=n.id OR e.target=n.id))"
        ).fetchone()[0])
        return {"nodes": nodes, "edges": edges, "isolated_nodes": cold}
    finally:
        conn.close()


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
        "status": row["status"], "revision": int(row["revision"]), "tags": tags,
        "retrieval_count": int(row["retrieval_count"] or 0), "last_retrieved_at": row["last_retrieved_at"],
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


def edge_record(storage_dir: str, edge_id: str) -> Dict[str, Any]:
    conn = _connect(storage_dir)
    try:
        row = _edge_row(conn, str(edge_id))
        return {
            "id": row["id"], "source": row["source"], "label": row["label"], "target": row["target"],
            "status": row["status"], "revision": int(row["revision"]),
            "anchors": _anchors_for(conn, "edge", str(row["id"]), int(row["revision"])) ,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _graph_topology(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    node_rows = conn.execute("SELECT id,retrieval_count FROM memory_nodes WHERE status='current'").fetchall()
    node_ids = [str(row["id"]) for row in node_rows]
    retrieval = {str(row["id"]): int(row["retrieval_count"] or 0) for row in node_rows}
    edges = conn.execute("SELECT source,target,label FROM memory_edges WHERE status='current'").fetchall()
    adjacency: Dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    in_degree: Dict[str, int] = defaultdict(int); out_degree: Dict[str, int] = defaultdict(int)
    labels: Dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source, target = str(edge["source"]), str(edge["target"])
        if source not in adjacency or target not in adjacency:
            continue
        adjacency[source].add(target); adjacency[target].add(source)
        out_degree[source] += 1; in_degree[target] += 1
        labels[source].add(str(edge["label"])); labels[target].add(str(edge["label"]))

    # Tarjan articulation points, O(V+E). This is a mechanical graph property,
    # never a semantic declaration that a node is important.
    discovery: Dict[str, int] = {}; low: Dict[str, int] = {}; parent: Dict[str, str | None] = {}
    articulation: set[str] = set(); clock = 0

    def dfs(u: str) -> None:
        nonlocal clock
        clock += 1; discovery[u] = low[u] = clock; children = 0
        for v in adjacency.get(u, ()):
            if v not in discovery:
                parent[v] = u; children += 1; dfs(v); low[u] = min(low[u], low[v])
                if parent.get(u) is None and children > 1:
                    articulation.add(u)
                if parent.get(u) is not None and low[v] >= discovery[u]:
                    articulation.add(u)
            elif v != parent.get(u):
                low[u] = min(low[u], discovery[v])

    for node_id in node_ids:
        if node_id not in discovery:
            parent[node_id] = None; dfs(node_id)

    component_size: Dict[str, int] = {}
    visited: set[str] = set()
    for start in node_ids:
        if start in visited:
            continue
        queue = deque([start]); component: list[str] = []; visited.add(start)
        while queue:
            u = queue.popleft(); component.append(u)
            for v in adjacency.get(u, ()):
                if v not in visited:
                    visited.add(v); queue.append(v)
        size = len(component)
        for node_id in component:
            component_size[node_id] = size

    out: Dict[str, Dict[str, Any]] = {}
    for node_id in node_ids:
        degree = len(adjacency.get(node_id, ()))
        connectivity = degree * 4 + len(labels.get(node_id, ())) * 2 + (6 if node_id in articulation else 0)
        hits = retrieval.get(node_id, 0)
        if node_id in articulation or connectivity >= 18 or hits >= 8:
            tier = "hot"
        elif degree > 0 or hits > 0:
            tier = "warm"
        else:
            tier = "cold"
        out[node_id] = {
            "degree": degree, "in_degree": int(in_degree.get(node_id, 0)), "out_degree": int(out_degree.get(node_id, 0)),
            "relation_types": len(labels.get(node_id, ())), "articulation_point": node_id in articulation,
            "component_size": int(component_size.get(node_id, 1)), "connectivity_score": int(connectivity),
            "retrieval_count": hits, "exposure_tier": tier,
        }
    return out


def _terms(values: Iterable[Any]) -> List[str]:
    stop = {"para", "como", "onde", "quando", "qual", "quais", "uma", "uns", "das", "dos", "the", "and", "with", "from", "that", "this", "what", "how"}
    out: list[str] = []; seen: set[str] = set()
    for value in values:
        for token in re.findall(r"[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ0-9_.-]{2,}", str(value or "").casefold()):
            if token in stop or token in seen:
                continue
            seen.add(token); out.append(token)
    return out[:40]


def retrieve_graph(
    storage_dir: str,
    *,
    world_scope_value: str,
    query: str,
    focus: Iterable[str] = (),
    limit: int = 14,
    execution_id: str | None = None,
) -> Dict[str, Any]:
    """Mechanically retrieve a bounded graph view; semantics remain Main-owned."""
    page = max(1, min(int(limit or 14), 30))
    focus_values = [str(item) for item in focus or [] if str(item).strip()][:12]
    terms = _terms([query, *focus_values])
    direct_ids = [item for item in focus_values if _MEM_ID_RE.fullmatch(item)]
    conn = _connect(storage_dir)
    try:
        topology = _graph_topology(conn)
        scores: Dict[str, int] = defaultdict(int)
        reasons: Dict[str, set[str]] = defaultdict(set)

        for node_id in direct_ids:
            row = conn.execute("SELECT id FROM memory_nodes WHERE id=? AND status='current'", (node_id,)).fetchone()
            if row is not None:
                scores[node_id] += 2000; reasons[node_id].add("focus_id")

        scopes = ("user", world_scope_value)
        rows = conn.execute(
            "SELECT id,scope,kind,content,updated_at FROM memory_nodes WHERE status='current' AND scope IN (?,?)",
            scopes,
        ).fetchall()
        tag_map: Dict[str, list[str]] = defaultdict(list)
        for row in conn.execute(
            "SELECT t.node_id,t.tag FROM memory_tags t JOIN memory_nodes n ON n.id=t.node_id WHERE n.status='current' AND n.scope IN (?,?)",
            scopes,
        ):
            tag_map[str(row["node_id"])].append(str(row["tag"]))

        for row in rows:
            node_id = str(row["id"])
            haystack = " ".join([str(row["kind"]), str(row["content"]), *tag_map.get(node_id, [])]).casefold()
            matched = 0
            for term in terms:
                if term in haystack:
                    matched += 1
            if matched:
                scores[node_id] += matched * 120
                reasons[node_id].add("lexical")
            # Global user memory can surface weakly even without lexical match;
            # world-scoped memory needs lexical/focus relevance or topology fallback.
            if row["scope"] == "user" and matched:
                scores[node_id] += 40
            if node_id in topology:
                scores[node_id] += min(80, int(topology[node_id]["connectivity_score"]))

        # If query produced no hit, expose only mechanically central world/user nodes.
        if not scores:
            for row in rows:
                node_id = str(row["id"])
                topo = topology.get(node_id) or {}
                connectivity = int(topo.get("connectivity_score") or 0)
                retrieval_count = int(topo.get("retrieval_count") or 0)
                if connectivity or retrieval_count:
                    scores[node_id] = min(80, connectivity) + min(30, retrieval_count)
                    reasons[node_id].add("topology_fallback")

        # One-hop expansion lets tags/terms activate local graph context.
        seed_ids = sorted(scores, key=lambda n: (scores[n], n), reverse=True)[: max(page, 8)]
        if seed_ids:
            marks = ",".join("?" for _ in seed_ids)
            edge_rows = conn.execute(
                f"SELECT source,target FROM memory_edges WHERE status='current' AND (source IN ({marks}) OR target IN ({marks}))",
                tuple(seed_ids + seed_ids),
            ).fetchall()
            for edge in edge_rows:
                for node_id in (str(edge["source"]), str(edge["target"])):
                    if node_id not in seed_ids:
                        scores[node_id] += 70
                        reasons[node_id].add("neighbor")

        selected_ids = sorted(scores, key=lambda n: (scores[n], int((topology.get(n) or {}).get("connectivity_score") or 0), n), reverse=True)[:page]
        now = utc_now()
        if selected_ids:
            with conn:
                conn.executemany(
                    "UPDATE memory_nodes SET retrieval_count=retrieval_count+1,last_retrieved_at=? WHERE id=?",
                    [(now, node_id) for node_id in selected_ids],
                )
        node_rows = {str(row["id"]): row for row in conn.execute(
            f"SELECT * FROM memory_nodes WHERE id IN ({','.join('?' for _ in selected_ids)})", tuple(selected_ids)
        )} if selected_ids else {}
        nodes = []
        for node_id in selected_ids:
            record = _node_record(conn, node_rows[node_id], include_edges=False)
            record["topology"] = topology.get(node_id) or {}
            record["retrieval"] = {"score": int(scores[node_id]), "reasons": sorted(reasons[node_id])}
            nodes.append(record)

        selected_set = set(selected_ids)
        edges = []
        if selected_ids:
            marks = ",".join("?" for _ in selected_ids)
            for row in conn.execute(
                f"SELECT * FROM memory_edges WHERE status='current' AND source IN ({marks}) AND target IN ({marks}) ORDER BY id",
                tuple(selected_ids + selected_ids),
            ):
                edges.append({
                    "id": row["id"], "source": row["source"], "label": row["label"], "target": row["target"],
                    "revision": int(row["revision"]), "anchors": _anchors_for(conn, "edge", str(row["id"]), int(row["revision"])) ,
                })
        return {
            "nodes": nodes, "edges": edges,
            "retrieval": {
                "terms": terms[:16], "focus": focus_values, "returned_nodes": len(nodes), "returned_edges": len(edges),
                "candidate_nodes": len(scores), "scope": world_scope_value,
            },
        }
    finally:
        conn.close()
