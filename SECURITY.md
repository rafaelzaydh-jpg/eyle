# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Use GitHub's **Security → Report a vulnerability** flow so the report can be
reviewed privately. Include the affected version, reproduction steps, expected
behavior, actual behavior, and any relevant logs with secrets removed.

## Security boundaries

Eyle is designed to fail closed around project writes and command execution:

- project paths are constrained to the configured project root;
- writes require fresh evidence, hashes, a dry run, and explicit confirmation;
- tests run through a configured sandbox and are refused when isolation cannot
  satisfy the requested policy;
- patches are applied atomically and can be rolled back;
- the web API requires a bearer token and rate-limits invalid authentication.

These controls reduce risk, but they do not make an untrusted model or an
untrusted repository harmless. Run Eyle with the least operating-system
privileges possible and review every proposed change. The default configuration enables supervised editing only inside `workspace/`; external paths remain read-only until explicitly trusted.
