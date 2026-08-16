# Model Surface

Eyle deliberately exposes a tolerant wire surface to Main and keeps the strict canonical ECC contract inside Eyle.

> **Main does the semantic work; Eyle handles safe serialization details.**

## Preferred decision wire

```json
{"type":"explorar","operations":[{"operation":"read_file","arguments":{"path":"calc.py","source":"workspace"}}],"memory_delta":[]}
```

```json
{"type":"construir","operation":"transaction","arguments":{},"memory_delta":[]}
```

```json
{"type":"concluir","response":"Final answer","memory_delta":[]}
```

The nested `{"decision":{...},"memory_delta":[]}` envelope is also accepted.

Explore batches have no semantic item-count ceiling. A successful Build always returns the verified physical result to Main before completion.

## Deterministic wire canonicalization

Before strict ECC validation, Eyle may mechanically recover or normalize:

- Markdown fences or surrounding prose containing one balanced object;
- safe Python-literal dictionaries/lists using `ast.literal_eval` (never `eval`);
- `output`, `result`, or `ecc` wrappers;
- flat top-level decisions;
- safe move aliases such as `explore`, `build`, and `final`;
- operation aliases such as `name/tool` and `args/input`;
- flat Memory operations;
- `memory` / `memories` aliases;
- temporary/persistent retention aliases;
- flat epistemic fields;
- unambiguous supports such as `request`, `mat-*`, `mem-*`, and `@key`;
- retired `on_success`, which is dropped because post-Build cognition is mandatory.

These transformations may change representation but **never invent missing semantic content**. A Conclude decision without an answer remains invalid.

## Memory wire

Preferred learning wire may be flat:

```json
{
  "op": "remember",
  "scope": "world",
  "retention": "temporary",
  "kind": "weak_signal",
  "content": "Port 443 showed unusual resets",
  "nature": "observation",
  "confidence": 0.98,
  "volatility": "medium",
  "temporal": {"as_of": "current scan"},
  "support": "mat-0007",
  "recall": {
    "concepts": ["network reset anomaly"],
    "cues": ["when diagnosing intermittent TLS failures"]
  }
}
```

Eyle canonicalizes this into strict internal `arguments`, `epistemic`, recall, and support-object structures before atomic Memory Graph application.

## Structured failure recovery

The Adapter only needs to return a recoverable JSON candidate. Eyle canonicalizes and validates it locally.

If required semantics are still missing, the error becomes `ECC_PROTOCOL_RECOVERY` feedback on the **same Main execution**. Session, observations, Memory, generated-token fuse, and deadline remain intact; capabilities are not repeated merely because serialization failed.

There is no fixed semantic structured-retry count. Physical deadline, generated-token fuse, cancellation, and provider availability remain the real stop conditions.

## Adapter transport policy

The Adapter chooses the strongest upstream JSON transport mode the provider technically accepts:

```text
native_json_schema -> json_object -> prompt_json
```

It only degrades after a technical provider rejection of the stronger mode. A model producing semantically wrong JSON does not teach the Adapter that a transport capability is unsupported.

Adapter does not implement Eyle semantic grammar.

## Frontier semantics

Page sizes are materialization choices, not knowledge limits. If finite remainder exists, Runtime publishes an exact `fr-*` Frontier. Main may continue as many times as needed.
