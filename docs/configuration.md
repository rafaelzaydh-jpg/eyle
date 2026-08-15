# Configuration

The current application identity is stored in `config.json` and verified at startup.

```text
app_version = 2.7.5
config_schema_version = 2.7.5-r2.5.2-ecc
revision = rev2.5.3-ecc
```

The revision may change without changing the persisted AgentSession schema. This release keeps the existing `2.7.5-r2.5.2-ecc` session/config schema because no persisted shape changed.

## Memory host state

Persistent memory is Host-owned internal state, not a public capability. A Host supplies:

```text
core_memory.storage_dir
core_memory.world_scope_id
```

`world_scope_id` is opaque to Core. A workspace Host may use a workspace identity; another Host may use a robot, device, network, tenant, or other stable identity.

The memory database should stay outside the observed world when a provider could otherwise inspect its own private storage.
