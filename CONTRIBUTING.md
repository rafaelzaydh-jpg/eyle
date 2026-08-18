# Development, Feedback, and Contributions

Eyle is currently developed and maintained by its author.

**External code contributions and pull requests are not currently accepted.**

This file exists to make that policy explicit while still documenting how to work with a private fork and how to report useful feedback.

## Personal forks and modifications

Private modifications for personal, non-commercial use are allowed only under the terms of [`LICENSE.md`](LICENSE.md).

The public repository being visible or forkable on GitHub does not grant permission to publish modified versions, redistribute the software, or use it commercially.

## Technical feedback

Bug reports and technical feedback are useful when they contain reproducible evidence. When the repository has an appropriate issue or discussion channel available, include:

- the Eyle app version and revision;
- the operating system and Python version;
- the smallest reproducible request or flow;
- relevant observable job/error codes;
- whether the problem affects Core, Runtime, Memory, a capability provider, Adapter, or UI;
- logs with secrets and protected content removed.

Do not include hidden credentials, private keys, raw protected workspace content, or other sensitive material.

Security vulnerabilities must follow [`SECURITY.md`](SECURITY.md) rather than a public issue.

## Local development

Create an environment:

```bash
python -m venv .venv
```

Install the current development and Adapter dependencies:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install -r server/requirements.txt
```

Run verification:

```bash
make verify
```

Or run the main suites directly:

```bash
python -B -m eyle.devtools.release_identity
python -m pytest -q
python -m pytest -q server/tests
```

## Architectural rules for private modifications

If you maintain a personal fork, the current architecture follows these invariants:

1. **Main owns meaning.**
2. **Core contains Eyle-specific logic.**
3. **Runtime owns physical truth and execution invariants.**
4. **Memory remains an independent sidecar to ECC decisions.**
5. **Capability providers own domain mechanics, not semantic planning.**
6. **Adapter owns provider transport and mechanical wire conformance, not Eyle semantics.**
7. **Current runtime paths remain canonical; historical compatibility belongs in explicit migration tooling.**
8. **Observability must distinguish hypothesis from physical evidence.**
9. **Persistent workspace changes remain confirmable, verifiable, and reversible where the current contract requires it.**

See [`docs/architecture.md`](docs/architecture.md) before making structural changes.

## Documentation language

Public documentation and canonical external contracts are written in English.

Internal agent instructions, tool contracts, state-machine messages, and structured JSON schemas are kept in English for model reliability.

## Future contribution policy

If external code contributions are opened in the future, the repository will publish explicit contribution and intellectual-property terms before accepting them.

Until then, do not submit pull requests containing code, documentation, assets, or patches for incorporation into the official Eyle codebase.
