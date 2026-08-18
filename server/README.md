# Eyle Adapter — Rev3.7.2

The bundled Adapter is the single deterministic transport path between Eyle and the configured DeepSeek V4-compatible upstream.

```text
Eyle -> localhost:8080 -> Adapter -> UPSTREAM_BASE_URL
```

It does not own ECC, Memory, semantic routing, planning or relevance. It does not discover remote models or guess provider capabilities.

## Environment

Configure `server/.env` with the current names only:

```dotenv
PROVIDER_PROFILE=deepseek_v4
UPSTREAM_BASE_URL=https://api.deepseek.com
UPSTREAM_API_KEY=...
MODEL=deepseek-v4-flash
PORT=8080
```

`UPSTREAM_API_KEY` and `MODEL` are the canonical settings. Removed environment aliases are not read.

## Local request contract

Eyle sends the current local transport fields. Structured cognition uses the configured JSON-object mode and the caller-supplied generic schema.

The local output-cap field is:

```text
max_completion_tokens
```

Incoming `max_tokens` is rejected. The Adapter may emit upstream `max_tokens` internally because that is the provider transport field; it is not a second local API.

`reasoning_mode` is translated mechanically to the configured DeepSeek thinking shape.

## Handshake and readiness

`GET /v1/eyle/handshake` declares the static local protocol/profile. `GET /ready` checks local configuration. Neither endpoint performs a paid generation or remote model discovery.

`GET /v1/models` exposes only the configured `MODEL`.

## Usage

Provider usage is returned to Eyle for the execution ledger. The Adapter does not create a second cognitive budget or reinterpret usage semantically.

## Running

Use the bundled launcher or start the server directly after configuring `.env`. Eyle expects the Adapter on port `8080` unless its current config says otherwise.
