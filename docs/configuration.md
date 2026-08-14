# Configuration — Eyle 2.7.5 Rev1.5.3

Canonical identity:

```json
{
  "app_version": "2.7.5",
  "config_schema_version": "2.7.5-r1.5.3",
  "revision": "rev1.5.3-cognitive-task-memory"
}
```

Unknown universal fields and mismatched identity are errors. Provider configuration is validated by the Registry/Provider selected by the Host.

## Universal host configuration

Top-level universal namespaces remain `llm`, `context_engine`, `web`, `confirmacoes`, `agent`, `worker`, `telemetry`, and `providers`.

Runtime does not know provider-specific search, sandbox, device or network fields.

## Bundled Host

The bundled distribution installs:

```json
"providers": {
  "standard": {"...": "workspace/Git/sandbox/test mechanics"},
  "memory": {}
}
```

An alternative Host may define a different provider set and config schema. Provider IDs in config must correspond to Providers actually registered by that Host.

## Physical limits

Rev1.5.3 keeps per-call context/output containment, task deadline and provider-owned physical limits. There is no task-wide economic `max_total_tokens` fuse yet; Task Memory is intentionally being validated before budget/no-progress changes are added.

## Persistence schemas

- configuration: `2.7.5-r1.5.3`;
- persisted Session: `2.7.5-r1.5.3`;
- Queue: `2.7.5-r1.4.3`;
- pending continuation: `4`.

This is a clean break; old session/config shapes are not silently migrated.
