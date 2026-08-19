# Eyle Adapter — Rev4.0.0

The bundled Adapter is Eyle's local connection boundary for the configured DeepSeek model.

```text
Eyle
  │
  │ local OpenAI-style request
  ▼
127.0.0.1:8080
  │
  ▼
Eyle Adapter
  │
  │ DeepSeek transport
  ▼
configured upstream
```

The Adapter is intentionally small in responsibility.

## What the Adapter owns

- local HTTP/authentication boundary;
- configured DeepSeek endpoint/model;
- request translation;
- streaming/non-streaming transport;
- caller-supplied JSON Schema delivery;
- safe mechanical JSON representation recovery;
- validation against the same schema;
- exactly one isolated format-only repair when required;
- upstream usage and transport telemetry.

## What the Adapter does not own

- ECC meaning;
- Memory semantics;
- Task state;
- tools/capabilities;
- planning;
- semantic relevance;
- Eyle execution progress;
- Eyle's global per-message provider-token ledger;
- capability discovery/negotiation;
- model discovery.

The Adapter should teach the provider **how to satisfy the connection contract**, not how to be Eyle.

## Requirements

Install Adapter dependencies:

```bash
python -m pip install -r server/requirements.txt
```

## Environment

Copy the example:

```bash
cp server/.env.example server/.env
```

Current variables:

```dotenv
PROVIDER_PROFILE=deepseek_v4
UPSTREAM_BASE_URL=https://api.deepseek.com
UPSTREAM_API_KEY=
MODEL=deepseek-v4-flash

HOST=127.0.0.1
PORT=8080
REQUEST_TIMEOUT_SECONDS=1800
MAX_REQUEST_BYTES=10485760
LOG_LEVEL=INFO

PROXY_API_KEY=
PROXY_ALLOW_LOOPBACK_NO_AUTH=true
```

### Required provider identity

This revision implements one explicit profile:

```text
PROVIDER_PROFILE=deepseek_v4
```

There is no remote model discovery or automatic provider selection.

`MODEL` is the configured upstream model. Incoming request model IDs do not trigger provider discovery.

## Running

From the repository root:

```bash
python server/server.py
```

The default local base URL is:

```text
http://127.0.0.1:8080
```

Eyle's `config.json` points its `llm.base_url` at this Adapter.

## Health and readiness

### `GET /health`

Proves that the local Adapter process is alive and reports its current transport/profile identity.

It does **not** prove that the remote provider is reachable.

### `GET /ready`

Proves that required local configuration is present.

It intentionally does not make a paid provider generation.

### Actual provider connectivity

Only a real completion request proves that the configured upstream provider was reachable for that request.

## Local request contract

Eyle sends the current local request to the Adapter.

The public local output-cap field is:

```text
max_completion_tokens
```

The removed local `max_tokens` alias is rejected.

The Adapter may translate `max_completion_tokens` to the upstream provider's `max_tokens` field internally. That translation is transport mechanics, not a compatibility alias in the Eyle contract.

Eyle's global provider-token ledger does not enter the Adapter.

## Structured-output path

For a caller `json_schema` request:

```text
Eyle caller schema
      │
      ▼
Adapter serializes schema once
      │
      ▼
provider-facing representation instruction
      │
      ▼
DeepSeek candidate
      │
      ├─ safe JSON extraction when needed
      ▼
validate same schema
      │
      ├─ valid -> return
      │
      └─ invalid -> one isolated format repair
```

The Adapter does not special-case `explorar`, `construir`, `concluir`, Memory operations, or old Eyle aliases.

## Mechanical recovery

Safe recovery is representation-only.

Current examples include:

- stripping an outer JSON code fence;
- extracting the first balanced JSON object when there is one unambiguous object.

Recovery must not change the intended Eyle decision or translate semantic aliases.

## Format repair

If a candidate is JSON-recoverable but schema-invalid, the Adapter may make **one** provider repair generation.

The repair context is isolated to:

```text
required schema
+ previous candidate
+ validation errors
```

It does not replay:

- conversation;
- Memory;
- tools/capability packet;
- Task state;
- Runtime observations;
- full Eyle system context.

The repair instruction asks the provider to correct representation only, not reconsider the user's task.

## Truncation

If the upstream returns:

```text
finish_reason=length
```

the Adapter reports model-output truncation.

It does not start a format repair because truncation is not a representation-validation defect.

## Invalid after repair

If the repaired candidate still fails the caller schema, the Adapter returns the last candidate plus structured validation/usage telemetry to Eyle.

Eyle decides whether to request one fresh cognition while preserving its existing Session/observations. The Adapter does not make that semantic/execution decision.

## Usage accounting

Adapter responses/headers expose upstream facts such as:

- upstream attempt count;
- structured repair count;
- prompt/completion/total usage;
- cached prompt usage when reported;
- schema-enforcement result;
- structured-contract character count;
- repair context mode;
- uncertainty when provider usage cannot be known after a transport failure.

Provider-reported usage is preserved rather than estimated inside the Adapter.

## Authentication

`PROXY_API_KEY` can protect the local Adapter from non-loopback clients.

With:

```dotenv
PROXY_ALLOW_LOOPBACK_NO_AUTH=true
```

loopback clients can use the local Adapter without the proxy key while remote clients still require it when a proxy key is configured.

Keep the Adapter on loopback unless you have a concrete reason to expose it.

## Tests

Run Adapter tests:

```bash
python -m pytest -q server/tests
```

Run the full repository verification:

```bash
make verify
```

## Design rule

When considering new Adapter behavior, ask:

> Is this required to connect to the provider or mechanically satisfy the caller's representation contract?

If not, it probably belongs somewhere else in Eyle.
