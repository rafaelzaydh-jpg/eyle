# Technical overview — Eyle 2.7.4

**Versão:** 2.7.4 · **Schema:** 2.7.4 · **Revisão:** 4.3-human-readable-code-analysis

## Single-agent core

Every project request is represented by one persisted Eyle task. The same task moves through planning, reading, optional writing, testing, verification, and finalization. Names such as scout or finalizer describe internal prompt profiles, not independent agents or alternate pipelines.

## Ingest

`ingest.py` deterministically writes:

- `projeto.json`: project identity and fingerprint;
- `estrutura.json`: inventory and structural metadata;
- `entendimento.json`: deterministic navigation hints and manually preserved component notes;
- `chunks.jsonl`: bounded BM25 search chunks.

Ingest does not call an LLM. Indexed text is not accepted as current source evidence; the agent rereads the file from disk before using it in a conclusion.

## Task intent, evidence, and answers

A compact deterministic task intent records the code-domain intent, response profile, requested outputs, write permission, and whether recommendations were actually requested. It guides the same Eyle agent; it does not create separate agents.

Fresh reads create evidence IDs with file/range hashes. Structured claims and project-audit coverage are validated before publication. `absence` claims require an explicit reviewed scope, and inferences require an observed basis. A failure stays a failure; there is no legacy response path that can convert it into a success.

## Editing

A write request follows this fixed sequence:

```text
read target
→ build patch
→ dry-run
→ wait for confirmation
→ atomic replace
→ run configured tests
→ reread changed range
→ deterministic verified write receipt
```

Atomic replacement uses a temporary file in the destination directory, `fsync`, best-effort permission copying, and `os.replace`. The implementation does not depend on `os.fchmod`, so it works on Windows as well as POSIX systems.

## Interfaces

CLI, Flask UI, queue, and worker are adapters around the same `engine.processar()` entry point. They do not implement separate reasoning pipelines.
