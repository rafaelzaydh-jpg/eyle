# Eyle Adapter — Transport Only

The Adapter is Eyle's single provider boundary. Eyle connects locally to port `8080`; the Adapter forwards requests to a configured remote OpenAI-compatible API.

```text
Eyle -> http://127.0.0.1:8080 -> Adapter -> remote provider -> model
```

No local LLM, port `8000`, or Ollama fallback is required.

## Authority boundary

The Adapter does **not** know Eyle semantics. It contains no grammar for ECC moves, Memory operations, epistemic metadata, consolidation, or `on_success`.

Eyle owns:

- tolerant wire canonicalization;
- canonical ECC schema;
- semantic validation;
- Memory meaning.

Adapter owns:

- provider URL/key/model routing;
- authentication/headers/body extras;
- OpenAI-compatible transport;
- structured transport capability selection;
- usage/cache metadata;
- syntactic JSON recovery;
- readiness/handshake endpoints.

## Configuration

Install:

```bash
python -m pip install -r server/requirements.txt
```

Copy `server/.env.example` to `server/.env`:

```dotenv
UPSTREAM_BASE_URL=https://your-provider.example/v1
UPSTREAM_API_KEY=YOUR_KEY
DEFAULT_MODEL=YOUR_MODEL_ID
PORT=8080
UPSTREAM_STRUCTURED_MODE=auto
```

Start:

```bash
python server/server.py
```

Windows launch helpers are also provided in this directory.

## Structured transport policy

`UPSTREAM_STRUCTURED_MODE=auto` prefers:

```text
native_json_schema -> json_object -> prompt_json
```

A mode is downgraded only when the provider technically rejects it. Semantically wrong model content does not change the cached provider capability.

The Adapter performs deterministic syntax-only JSON recovery. When necessary it may attempt one cheap format-only repair in the same accepted transport mode. If semantic content is incomplete, Eyle—not Adapter—handles recovery with the same Main execution.

## Handshake and diagnostics

- `GET /v1/eyle/handshake` — formal `eyle-adapter-handshake-v1` / `eyle-adapter-transport-v1` negotiation;
- `GET /ready` — provider/model readiness without paid generation;
- `GET /health` — Adapter configuration/capability policy;
- `GET /v1/models` — optional model surface, not used to infer Eyle compatibility.

Every response advertises the Adapter protocol/profile headers.
