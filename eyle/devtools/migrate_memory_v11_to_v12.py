"""One-shot Memory Graph v11 -> v12 migration.

This module is intentionally outside Runtime. Rev3.7.4 accepts only the current
v12 store during normal operation; operators with a Rev3.6.1/v11 database must
run this explicit migration before starting Eyle.
"""
from __future__ import annotations

import argparse
import os
import sqlite3

V11_SCHEMA_VERSION = "2.7.5-r3.6-memory-graph-v11"
V12_SCHEMA_VERSION = "2.7.5-r3.7.1-memory-graph-v12"


def migrate_memory_v11_to_v12(storage_dir: str) -> dict[str, int | str]:
    root = os.path.realpath(str(storage_dir or ""))
    if not root:
        raise ValueError("MEMORY_STORAGE_UNAVAILABLE")
    db_path = os.path.join(root, "core_memory.sqlite3")
    if not os.path.isfile(db_path):
        raise ValueError("MEMORY_GRAPH_STORE_NOT_FOUND")

    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        meta = conn.execute(
            "SELECT value FROM memory_meta WHERE key='schema_version'"
        ).fetchone()
        observed = str(meta[0]) if meta else ""
        if observed == V12_SCHEMA_VERSION:
            return {"status": "already_current", "schema_version": V12_SCHEMA_VERSION, "task_nodes": 0}
        if observed != V11_SCHEMA_VERSION:
            raise ValueError(f"MEMORY_GRAPH_MIGRATION_UNSUPPORTED:{observed or 'missing'}")

        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memory_nodes)")}
        with conn:
            if "domain" not in columns:
                conn.execute(
                    "ALTER TABLE memory_nodes ADD COLUMN domain TEXT NOT NULL DEFAULT 'knowledge'"
                )
            if "context_key" not in columns:
                conn.execute("ALTER TABLE memory_nodes ADD COLUMN context_key TEXT")
            conn.execute(
                "UPDATE memory_nodes SET domain='knowledge' "
                "WHERE domain IS NULL OR TRIM(domain)=''"
            )
            conn.execute(
                "UPDATE memory_nodes SET domain='task', context_key=id "
                "WHERE id IN (SELECT task_id FROM memory_tasks)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_nodes_domain_context_status_updated "
                "ON memory_nodes(domain,context_key,status,updated_at DESC)"
            )
            conn.execute(
                "UPDATE memory_meta SET value=? WHERE key='schema_version'",
                (V12_SCHEMA_VERSION,),
            )
        task_nodes = int(
            conn.execute("SELECT COUNT(*) FROM memory_nodes WHERE domain='task'").fetchone()[0]
        )
        return {"status": "migrated", "schema_version": V12_SCHEMA_VERSION, "task_nodes": task_nodes}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate one Eyle Memory Graph from v11 to v12.")
    parser.add_argument("storage_dir", help="Directory containing core_memory.sqlite3")
    args = parser.parse_args()
    result = migrate_memory_v11_to_v12(args.storage_dir)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
