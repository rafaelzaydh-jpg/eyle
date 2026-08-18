from __future__ import annotations

from eyle.capabilities import build_registry
from eyle.providers.standard.registry import get_provider as get_standard_provider


def standard_registry():
    """Return the same canonical namespaced registry used by production."""
    return build_registry([get_standard_provider()])


def run_agent(module, *args, **kwargs):
    kwargs.setdefault("registry", standard_registry())
    return module.executar_agente(*args, **kwargs)


def base_config(*, tests_enabled=False):
    return {
        "app_version": "2.7.5",
        "config_schema_version": "2.7.5-r3.7.2-ecc",
        "revision": "rev3.7.2-ecc",
        "llm": {
            "base_url": "http://127.0.0.1:8080",
            "model": "deepseek-v4-flash",
            "temperature": 0.0,
            "provider_token_budget_per_message": 150000,
            "context_window_tokens": None,
            "connect_timeout_seconds": 5,
            "read_timeout_seconds": None,
            "adapter_handshake_timeout_seconds": 3,
            "retry_max_attempts": 3,
            "retry_base_delay_seconds": 0.0,
            "retry_max_delay_seconds": 0.0,
            "retry_jitter_seconds": 0.0,
            "max_concurrent_requests": 1,
            "cooldown_seconds": 0.0,
            "retry_read_timeouts": False,
            "stream_responses": True,
            "reasoning_mode": "off",
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "conversation_materialization_tokens": 1200,
            "observation_materialization_tokens": 2200,
        },
        "providers": {
            "standard": {
                "tests": {"enabled": bool(tests_enabled)},
            },
        },
        "worker": {
            "heartbeat_interval_seconds": 5,
            "queue_error_backoff_seconds": 1,
            "max_invalid_jobs_per_reservation": 100,
            "max_parallel_jobs": 1,
            "isolate_jobs": True,
            "stale_worker_seconds": 30,
            "head_of_line_blocked_seconds": 60,
            "multiprocessing_context": "spawn",
        },
        "web": {"api_token": None, "rate_limit": {"requests": 180, "auth_failures": 10, "window_seconds": 60}},
        "confirmacoes": {"expiracao_segundos": 3600},
        "telemetry": {"enabled": True, "window_seconds": 3600},
    }


def select_graph_nodes_for_test(
    storage_dir,
    *,
    world_scope_value,
    query="",
    queries=(),
    ids=(),
    tags=(),
    scope="all",
    include_neighbors=False,
    retention="all",
    natures=(),
    volatilities=(),
    relation_labels=(),
):
    """Materialize an entire recall snapshot for assertions only.

    Production Runtime exposes paging/frontiers; tests that need the complete
    finite ID set consume the snapshot directly instead of keeping a debug
    materializer in Runtime.
    """
    from eyle.runtime.memory_graph import create_recall_snapshot, release_recall_snapshot, memory_db_path
    import sqlite3

    created = create_recall_snapshot(
        storage_dir,
        world_scope_value=world_scope_value,
        query=query,
        queries=queries,
        ids=ids,
        tags=tags,
        scope=scope,
        include_neighbors=include_neighbors,
        retention=retention,
        natures=natures,
        volatilities=volatilities,
        relation_labels=relation_labels,
    )
    snapshot_id = str(created["snapshot_id"])
    try:
        conn = sqlite3.connect(memory_db_path(storage_dir))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT node_id,source_kind,source_from_node,source_via_relation "
                "FROM memory_recall_items WHERE snapshot_id=? ORDER BY ordinal",
                (snapshot_id,),
            ).fetchall()
        finally:
            conn.close()
        return {
            "node_ids": [str(row["node_id"]) for row in rows],
            "reasons": {
                str(row["node_id"]): {
                    "source_kind": str(row["source_kind"]),
                    "from_node": str(row["source_from_node"]) if row["source_from_node"] is not None else None,
                    "via_relation": str(row["source_via_relation"]) if row["source_via_relation"] is not None else None,
                }
                for row in rows
            },
            "selection": created["selection"],
        }
    finally:
        release_recall_snapshot(storage_dir, snapshot_id)
