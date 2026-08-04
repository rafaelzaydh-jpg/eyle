# Publishing on GitHub

## Recommended repository metadata

**Repository name**

```text
eyle
```

**Description**

```text
Local-first supervised coding agent for LFM2.5-8B-A1B with external memory, BM25 retrieval, confirmed patches, tests and rollback.
```

**Topics**

```text
local-llm
coding-agent
ai-agent
code-assistant
developer-tools
offline-ai
privacy
rag
retrieval
bm25
llama-cpp
ollama
lm-studio
python
agentic-ai
lfm2-5
liquid-ai
tool-use
function-calling
```

## Before publishing

1. Review `config.json` for local paths, model names, and secrets.
2. Confirm that `memory/`, `context/`, and `workspace/` contain only `.gitkeep`.
3. Choose a license. The repository currently defaults to all rights reserved.
4. Create the repository without an auto-generated README or `.gitignore`.
5. Push this folder, then enable Issues and private vulnerability reporting.
6. Upload `assets/eyle-social-preview.png` under **Settings → General → Social preview**.
7. Add the description and topics above.
8. Pin the repository on your profile and mention “Minimum recommended model: LFM2.5-8B-A1B” in the About section if space allows.

## Initial Git commands

```bash
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/eyle.git
git push -u origin main
```
