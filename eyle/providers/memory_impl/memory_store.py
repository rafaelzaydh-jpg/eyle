"""SQLite persistence for the Eyle Memory Kernel.

This module owns physical memory state only: schema, revisions, atomic
ChangeSets, append-only events and opaque continuation snapshots. It does not
decide what is worth remembering or what a relation means.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


MEMORY_SCHEMA_VERSION = "2.7.5-r1.3.6-memory-kernel-v1"
_MEMORY_STATUSES = {"current", "archived", "superseded"}
_RELATION_STATUSES = {"current", "retired"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_key(project_root: str) -> str:
    normalized = os.path.realpath(str(project_root or ""))
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:24]


def memory_db_path(base_dir: str, project_root: str) -> str:
    directory = os.path.join(base_dir, "agent_memory")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f"{project_key(project_root)}.sqlite3")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("MEMORY_STORE_INVALID") from error


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"MEMORY_{field.upper()}_EMPTY")
    if len(text) > maximum:
        raise ValueError(f"MEMORY_{field.upper()}_TOO_LARGE")
    return text


def _clean_optional_text(value: Any, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _clean_text(value, field=field, maximum=maximum)


def _clean_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("MEMORY_PROVENANCE_INVALID")
    encoded = _json(value)
    if len(encoded) > 8000:
        raise ValueError("MEMORY_PROVENANCE_TOO_LARGE")
    return json.loads(encoded)


def _clean_tags(values: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        tag = str(value or "").strip()
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


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _connect(base_dir: str, project_root: str) -> sqlite3.Connection:
    path = memory_db_path(base_dir, project_root)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    _ensure_schema(conn, project_root)
    return conn


def _ensure_schema(conn: sqlite3.Connection, project_root: str) -> None:
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_meta'"
    ).fetchone()
    if existing is None:
        with conn:
            conn.executescript(
                """
                CREATE TABLE memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    region TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('current','archived','superseded')),
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    provenance TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX idx_memories_region_status_updated
                    ON memories(region, status, updated_at DESC);
                CREATE TABLE memory_tags (
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    tag TEXT COLLATE NOCASE NOT NULL,
                    PRIMARY KEY(memory_id, tag)
                );
                CREATE INDEX idx_memory_tags_tag ON memory_tags(tag COLLATE NOCASE);
                CREATE TABLE relations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL REFERENCES memories(id),
                    label TEXT NOT NULL,
                    target TEXT NOT NULL REFERENCES memories(id),
                    status TEXT NOT NULL CHECK(status IN ('current','retired')),
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    provenance TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX idx_relations_source_status ON relations(source, status);
                CREATE INDEX idx_relations_target_status ON relations(target, status);
                CREATE TABLE changesets (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE memory_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    changeset_id TEXT NOT NULL REFERENCES changesets(id),
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    revision INTEGER,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX idx_memory_events_entity ON memory_events(entity_id, seq);
                CREATE TABLE memory_continuations (
                    id TEXT PRIMARY KEY,
                    ordered_ids TEXT NOT NULL,
                    next_offset INTEGER NOT NULL,
                    criteria TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute("INSERT INTO memory_meta(key,value) VALUES('schema_version',?)", (MEMORY_SCHEMA_VERSION,))
            conn.execute("INSERT INTO memory_meta(key,value) VALUES('project_root',?)", (os.path.realpath(project_root),))
        return
    rows = dict(conn.execute("SELECT key,value FROM memory_meta").fetchall())
    if rows.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise ValueError("MEMORY_SCHEMA_INCOMPATIBLE")
    if rows.get("project_root") != os.path.realpath(project_root):
        raise ValueError("MEMORY_PROJECT_INCOMPATIBLE")
    required = {
        "memory_meta", "memories", "memory_tags", "relations", "changesets",
        "memory_events", "memory_continuations",
    }
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables):
        raise ValueError("MEMORY_STORE_INVALID")


def _event(
    conn: sqlite3.Connection,
    changeset_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    revision: int | None,
    payload: dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO memory_events(changeset_id,entity_type,entity_id,action,revision,payload,created_at) VALUES(?,?,?,?,?,?,?)",
        (changeset_id, entity_type, entity_id, action, revision, _json(payload), now),
    )


def _memory_row(conn: sqlite3.Connection, memory_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"MEMORY_NOT_FOUND:{memory_id}")
    return row


def _relation_row(conn: sqlite3.Connection, relation_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM relations WHERE id=?", (relation_id,)).fetchone()
    if row is None:
        raise ValueError(f"MEMORY_RELATION_NOT_FOUND:{relation_id}")
    return row


def _expect_revision(row: sqlite3.Row, expected: Any) -> None:
    if expected is None:
        raise ValueError("MEMORY_EXPECTED_REVISION_REQUIRED")
    if int(expected) != int(row["revision"]):
        raise ValueError(f"MEMORY_CONFLICT:{row['id']}:current_revision={row['revision']}")


def apply_operations(
    base_dir: str,
    project_root: str,
    operations: Iterable[dict[str, Any]],
    *,
    changeset_id: str | None = None,
) -> dict[str, Any]:
    ops = [dict(item) for item in operations if isinstance(item, dict)]
    if not ops:
        raise ValueError("MEMORY_CHANGESET_EMPTY")
    cid = str(changeset_id or _new_id("mcs"))
    now = utc_now()
    affected: list[dict[str, Any]] = []
    conn = _connect(base_dir, project_root)
    try:
        with conn:
            conn.execute("INSERT INTO changesets(id,created_at) VALUES(?,?)", (cid, now))
            for op in ops:
                kind = str(op.get("op") or "").strip()
                if kind == "create_memory":
                    memory_id = str(op.get("id") or _new_id("mem"))
                    region = _clean_text(op.get("region"), field="region", maximum=240)
                    content = _clean_text(op.get("content"), field="content", maximum=32000)
                    provenance = _clean_provenance(op.get("provenance"))
                    tags = _clean_tags(op.get("tags"))
                    conn.execute(
                        "INSERT INTO memories(id,region,content,status,revision,provenance,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (memory_id, region, content, "current", 1, _json(provenance), now, now),
                    )
                    conn.executemany(
                        "INSERT INTO memory_tags(memory_id,tag) VALUES(?,?)",
                        [(memory_id, tag) for tag in tags],
                    )
                    _event(conn, cid, "memory", memory_id, kind, 1, {"region": region, "content": content, "tags": tags, "provenance": provenance}, now)
                    affected.append({"type": "memory", "id": memory_id, "revision": 1, "action": kind})
                elif kind == "update_memory":
                    memory_id = _clean_text(op.get("id"), field="id", maximum=120)
                    row = _memory_row(conn, memory_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_NOT_CURRENT:{memory_id}")
                    content = _clean_optional_text(op.get("content"), field="content", maximum=32000) or row["content"]
                    region = _clean_optional_text(op.get("region"), field="region", maximum=240) or row["region"]
                    provenance = _clean_provenance(op.get("provenance")) if "provenance" in op else _decode(row["provenance"], {})
                    revision = int(row["revision"]) + 1
                    conn.execute(
                        "UPDATE memories SET region=?,content=?,revision=?,provenance=?,updated_at=? WHERE id=?",
                        (region, content, revision, _json(provenance), now, memory_id),
                    )
                    add_tags = _clean_tags(op.get("add_tags"))
                    remove_tags = _clean_tags(op.get("remove_tags"))
                    conn.executemany("INSERT OR IGNORE INTO memory_tags(memory_id,tag) VALUES(?,?)", [(memory_id, tag) for tag in add_tags])
                    conn.executemany("DELETE FROM memory_tags WHERE memory_id=? AND tag=? COLLATE NOCASE", [(memory_id, tag) for tag in remove_tags])
                    _event(conn, cid, "memory", memory_id, kind, revision, {"region": region, "content": content, "add_tags": add_tags, "remove_tags": remove_tags, "provenance": provenance}, now)
                    affected.append({"type": "memory", "id": memory_id, "revision": revision, "action": kind})
                elif kind in {"archive_memory", "supersede_memory"}:
                    memory_id = _clean_text(op.get("id"), field="id", maximum=120)
                    row = _memory_row(conn, memory_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_NOT_CURRENT:{memory_id}")
                    status = "archived" if kind == "archive_memory" else "superseded"
                    revision = int(row["revision"]) + 1
                    conn.execute("UPDATE memories SET status=?,revision=?,updated_at=? WHERE id=?", (status, revision, now, memory_id))
                    payload: dict[str, Any] = {"status": status}
                    if kind == "supersede_memory":
                        target = _clean_text(op.get("superseded_by"), field="superseded_by", maximum=120)
                        _memory_row(conn, target)
                        relation_id = str(op.get("relation_id") or _new_id("rel"))
                        conn.execute(
                            "INSERT INTO relations(id,source,label,target,status,revision,provenance,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (relation_id, memory_id, "superseded_by", target, "current", 1, "{}", now, now),
                        )
                        _event(conn, cid, "relation", relation_id, "create_relation", 1, {"source": memory_id, "label": "superseded_by", "target": target}, now)
                        payload["superseded_by"] = target
                    _event(conn, cid, "memory", memory_id, kind, revision, payload, now)
                    affected.append({"type": "memory", "id": memory_id, "revision": revision, "action": kind})
                elif kind == "create_relation":
                    relation_id = str(op.get("id") or _new_id("rel"))
                    source = _clean_text(op.get("source"), field="source", maximum=120)
                    target = _clean_text(op.get("target"), field="target", maximum=120)
                    label = _clean_text(op.get("label"), field="relation", maximum=120)
                    _memory_row(conn, source); _memory_row(conn, target)
                    provenance = _clean_provenance(op.get("provenance"))
                    conn.execute(
                        "INSERT INTO relations(id,source,label,target,status,revision,provenance,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (relation_id, source, label, target, "current", 1, _json(provenance), now, now),
                    )
                    _event(conn, cid, "relation", relation_id, kind, 1, {"source": source, "label": label, "target": target, "provenance": provenance}, now)
                    affected.append({"type": "relation", "id": relation_id, "revision": 1, "action": kind})
                elif kind == "retire_relation":
                    relation_id = _clean_text(op.get("id"), field="id", maximum=120)
                    row = _relation_row(conn, relation_id)
                    _expect_revision(row, op.get("expected_revision"))
                    if row["status"] != "current":
                        raise ValueError(f"MEMORY_RELATION_NOT_CURRENT:{relation_id}")
                    revision = int(row["revision"]) + 1
                    conn.execute("UPDATE relations SET status='retired',revision=?,updated_at=? WHERE id=?", (revision, now, relation_id))
                    _event(conn, cid, "relation", relation_id, kind, revision, {"status": "retired"}, now)
                    affected.append({"type": "relation", "id": relation_id, "revision": revision, "action": kind})
                else:
                    raise ValueError(f"MEMORY_CHANGESET_OPERATION_INVALID:{kind or '<empty>'}")
    except sqlite3.IntegrityError as error:
        message = str(error)
        if "UNIQUE constraint failed" in message:
            raise ValueError("MEMORY_ID_CONFLICT") from error
        raise ValueError("MEMORY_STORE_INVALID") from error
    finally:
        conn.close()
    return {"changeset_id": cid, "affected": affected, "count": len(affected)}


def _record_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    memory_id = str(row["id"])
    tags = [item[0] for item in conn.execute(
        "SELECT tag FROM memory_tags WHERE memory_id=? ORDER BY tag COLLATE NOCASE", (memory_id,)
    )]
    relations = [
        dict(item) for item in conn.execute(
            "SELECT id,source,label,target,status,revision FROM relations WHERE (source=? OR target=?) AND status='current' ORDER BY id",
            (memory_id, memory_id),
        )
    ]
    return {
        "id": row["id"], "region": row["region"], "content": row["content"],
        "status": row["status"], "revision": int(row["revision"]),
        "tags": tags, "relations": relations,
        "provenance": _decode(row["provenance"], {}),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def memory_record(base_dir: str, project_root: str, memory_id: str) -> dict[str, Any]:
    conn = _connect(base_dir, project_root)
    try:
        return _record_from_row(conn, _memory_row(conn, memory_id))
    finally:
        conn.close()


def memory_records(base_dir: str, project_root: str, memory_ids: Iterable[str]) -> list[dict[str, Any]]:
    ids = [str(item) for item in memory_ids]
    if not ids:
        return []
    conn = _connect(base_dir, project_root)
    try:
        rows = {
            str(row["id"]): row for row in conn.execute(
                f"SELECT * FROM memories WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids)
            )
        }
        missing = [memory_id for memory_id in ids if memory_id not in rows]
        if missing:
            raise ValueError(f"MEMORY_NOT_FOUND:{missing[0]}")
        return [_record_from_row(conn, rows[memory_id]) for memory_id in ids]
    finally:
        conn.close()


def current_revision(base_dir: str, project_root: str, memory_id: str) -> int:
    conn = _connect(base_dir, project_root)
    try:
        return int(_memory_row(conn, memory_id)["revision"])
    finally:
        conn.close()


def candidate_ids(
    base_dir: str,
    project_root: str,
    *,
    ids: Iterable[str] = (),
    region: str | None = None,
    tags: Iterable[str] = (),
    text: str = "",
    related_to: Iterable[str] = (),
    include_inactive: bool = False,
) -> tuple[list[str], dict[str, int]]:
    explicit = [str(item) for item in ids or [] if str(item).strip()]
    related = [str(item) for item in related_to or [] if str(item).strip()]
    wanted_tags = _clean_tags(tags)
    terms = [part.casefold() for part in str(text or "").split() if part.strip()]
    conn = _connect(base_dir, project_root)
    try:
        pool: dict[str, int] = {}
        examined = {"explicit_ids": len(explicit), "relation_edges": 0, "filter_candidates": 0}
        for memory_id in explicit:
            row = conn.execute("SELECT id FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row is not None:
                pool[memory_id] = max(pool.get(memory_id, 0), 1000)
        if related:
            marks = ",".join("?" for _ in related)
            rows = conn.execute(
                f"SELECT source,target FROM relations WHERE status='current' AND (source IN ({marks}) OR target IN ({marks}))",
                tuple(related + related),
            ).fetchall()
            examined["relation_edges"] = len(rows)
            for row in rows:
                for memory_id in (row["source"], row["target"]):
                    if memory_id not in related:
                        pool[memory_id] = max(pool.get(memory_id, 0), 500)
            for memory_id in related:
                if conn.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone():
                    pool[memory_id] = max(pool.get(memory_id, 0), 900)
        where = []
        params: list[Any] = []
        if not include_inactive:
            where.append("m.status='current'")
        if region:
            where.append("m.region=?")
            params.append(str(region))
        for term in terms:
            where.append("LOWER(m.content) LIKE ?")
            params.append(f"%{term}%")
        for tag in wanted_tags:
            where.append("EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id=m.id AND t.tag=? COLLATE NOCASE)")
            params.append(tag)
        rows = []
        has_filter_seed = bool(region or wanted_tags or terms)
        if has_filter_seed or (not explicit and not related):
            clause = " AND ".join(where) if where else "1=1"
            rows = conn.execute(
                f"SELECT m.id,m.updated_at FROM memories m WHERE {clause} ORDER BY m.updated_at DESC,m.id",
                tuple(params),
            ).fetchall()
            base_score = 300 if has_filter_seed else 100
            for index, row in enumerate(rows):
                pool[row["id"]] = max(pool.get(row["id"], 0), base_score - min(index, 250))
        examined["filter_candidates"] = len(rows)
        if not pool:
            return [], examined
        status_rows = conn.execute(
            f"SELECT id,status,updated_at FROM memories WHERE id IN ({','.join('?' for _ in pool)})",
            tuple(pool),
        ).fetchall()
        sortable = []
        for row in status_rows:
            if not include_inactive and row["status"] != "current" and row["id"] not in explicit:
                continue
            sortable.append((pool[row["id"]], row["updated_at"], row["id"]))
        sortable.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[2] for item in sortable], examined
    finally:
        conn.close()


def save_continuation(
    base_dir: str,
    project_root: str,
    ordered_ids: list[str],
    next_offset: int,
    criteria: dict[str, Any],
) -> str:
    frontier_id = _new_id("mf")
    conn = _connect(base_dir, project_root)
    try:
        with conn:
            conn.execute(
                "INSERT INTO memory_continuations(id,ordered_ids,next_offset,criteria,created_at) VALUES(?,?,?,?,?)",
                (frontier_id, _json(ordered_ids), int(next_offset), _json(criteria), utc_now()),
            )
        return frontier_id
    finally:
        conn.close()


def load_continuation(base_dir: str, project_root: str, frontier_id: str) -> dict[str, Any]:
    conn = _connect(base_dir, project_root)
    try:
        row = conn.execute("SELECT * FROM memory_continuations WHERE id=?", (frontier_id,)).fetchone()
        if row is None:
            raise ValueError("MEMORY_FRONTIER_NOT_FOUND")
        return {
            "id": row["id"], "ordered_ids": _decode(row["ordered_ids"], []),
            "next_offset": int(row["next_offset"]), "criteria": _decode(row["criteria"], {}),
        }
    finally:
        conn.close()


def advance_continuation(base_dir: str, project_root: str, frontier_id: str, next_offset: int, *, done: bool) -> None:
    conn = _connect(base_dir, project_root)
    try:
        with conn:
            if done:
                conn.execute("DELETE FROM memory_continuations WHERE id=?", (frontier_id,))
            else:
                conn.execute("UPDATE memory_continuations SET next_offset=? WHERE id=?", (int(next_offset), frontier_id))
    finally:
        conn.close()


def history_records(base_dir: str, project_root: str, entity_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    conn = _connect(base_dir, project_root)
    try:
        rows = conn.execute(
            "SELECT seq,changeset_id,entity_type,entity_id,action,revision,payload,created_at FROM memory_events WHERE entity_id=? ORDER BY seq DESC LIMIT ?",
            (str(entity_id), max(1, min(int(limit), 500))),
        ).fetchall()
        return [{**dict(row), "payload": _decode(row["payload"], {})} for row in rows]
    finally:
        conn.close()
