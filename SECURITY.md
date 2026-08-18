# Security Policy

Eyle separates semantic authority from physical security enforcement.

Main may interpret user intent and choose actions, but it cannot waive Runtime-owned protections through natural language.

## Reporting a vulnerability

Do **not** publish exploitable vulnerability details in a public issue.

Use GitHub's private **Security → Report a vulnerability** flow when available, or another private contact channel exposed by the repository owner.

A useful report includes:

- affected Eyle version/revision;
- affected component;
- minimal reproduction;
- security impact;
- relevant observable error/failure codes;
- environment details needed to reproduce the issue.

Remove API keys, credentials, private keys, protected source, and unrelated private data from reports.

## Security boundaries

### Workspace confinement

User-project operations are confined to the dedicated configured workspace boundary.

Eyle's own source is a separate `source="eyle"` inspection surface and is not the default persistent write target.

### Protected resources

Credential/private-key resources and other protected physical identities are excluded from normal readable/sandbox content surfaces.

Protection is based on path/physical identity and policy, not merely on suspicious words appearing in a source file. Normal code is not blocked just because it contains strings such as `token`, `password`, or `api_key`.

### Persistent writes

Real workspace mutations require the current Runtime path:

```text
fresh observation/state
      ↓
exact proposal / dry-run
      ↓
confirmation when required
      ↓
transaction
      ↓
post-write observation
```

Runtime can reject a mutation because of stale state, protected resources, failed verification, or transaction error even if Main wants the change.

### Sandbox execution

Unrestricted `run_command` executes against an isolated copy rather than a read-write mount of the real workspace.

The sandbox protects host/workspace integrity according to the configured backend and limits.

If network access is enabled, source visible to a process inside the sandbox should not be treated as confidential from that process. A process that can both read source and access the network can potentially transmit it.

### Git inspection

Bundled Git status/diff capabilities are inspection surfaces. They do not grant unrestricted persistent Git mutation authority.

## Web/API exposure

The bundled web application has independent API-token and rate-limit controls.

By default the local service should remain on loopback.

If you expose Eyle on a non-loopback interface:

- restrict access with firewall/network policy;
- place it behind an HTTPS reverse proxy;
- protect the API token;
- do not rely on the Flask development server for transport encryption.

## Adapter boundary

Eyle connects to the local Adapter, normally on port `8080`.

Remote provider credentials and provider-specific configuration belong in `server/.env` or the Adapter environment and must not be committed.

The Adapter's `/health` endpoint proves only that the local Adapter process/protocol is alive.

`/ready` proves local mandatory configuration is present.

Actual remote-provider reachability is established only by a real provider request.

If `PROXY_API_KEY` is configured, non-loopback clients must authenticate according to the current Adapter policy.

## Provider data boundary

Requests sent to the configured upstream provider can contain Eyle instructions, materialized conversation, relevant Runtime context, and content Main chose to inspect/materialize.

Treat the configured provider as part of the data-processing boundary.

Do not place secrets in the workspace expecting the model or sandbox to keep them confidential merely because they are not semantically relevant.

## Semantic authority is not a security bypass

Main cannot reason around deterministic restrictions.

Natural-language instructions cannot waive:

- path confinement;
- protected-resource policy;
- schema validation;
- confirmation;
- provider/context budgets;
- sandbox limits;
- transaction rollback;
- post-write verification;
- API authentication/rate limiting.

## Memory, Material, and provenance

`mat-*` Material represents exact physical observations.

Memory contains Main-authored learned interpretations and may reference request, Material, or Memory supports.

Runtime preserves identity, revision, freshness, and provenance mechanically. It does not certify semantic truth.

Domain-specific validation belongs in the relevant capability/policy layer and must return findings through normal Observation rather than become a hidden universal semantic authority.

## Diagnostic privacy

Normal user-facing diagnostics must not expose:

- hidden chain-of-thought;
- raw provider prompts;
- raw provider responses;
- protected credential content;
- patch/write bodies that are not already exposed through the normal task surface;
- private Memory bodies outside their intended user-facing projection.

Execution history should expose facts needed to debug behavior without becoming a second secret-bearing transcript.

## Dependency and third-party risk

Eyle depends on third-party Python packages and, depending on configuration, external sandbox/container infrastructure and an upstream LLM provider.

Operators are responsible for:

- keeping dependencies patched;
- reviewing third-party licenses;
- protecting provider credentials;
- choosing an appropriate sandbox/network policy;
- controlling who can access the local web/API service.

## Supported security contract

Security documentation describes the current Rev3.7.5.1 runtime.

Historical behavior should not be assumed to have the same guarantees. Use the current release identity and verification gates when evaluating a deployment.
