<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Versão:** 2.7.4 · **Schema:** 5.2 · **Revisão:** rev5.2.3-investigation-memory-progress

## Rev5.2.3 — Investigation Memory & Progress Semantics

A Rev5.2.3 preserva todo o hardening da Rev5.2.2 e corrige dois P0 de convergência expostos pelas auditorias hostis. O bloqueio de releitura agora usa somente o que está visível no **prompt compilado atual**, enquanto ranges vistos no passado ficam apenas como telemetria. Evidence citada por Claim/Semantic Gap insuficiente ou ligada a target reaberto fica pinned durante o follow-up semântico, impedindo o caso absurdo de mandar a Main LLM investigar depois de remover a própria fonte. Progresso também passa a significar mudança observável de conhecimento/estado: `ok=true` sozinho não zera mais o fusível de no-progress, observações idênticas de projeto/runtime são suprimidas e `run_tests` repetido no mesmo scope é reutilizado até uma ação com mudança observável invalidar a execução anterior. As 16 tools públicas e os limites de 8 turnos / 12 tools / 9k completion permanecem iguais.

A Eyle é um agente de código local-first construído em torno de uma única `AgentSession`, tools determinísticas, Evidence mantida pelo runtime, escrita transacional supervisionada e um único Claim Review semântico antes da aceitação de respostas fundamentadas no projeto.

> **A LLM decide semântica. O runtime valida contratos.**

## Por que a Eyle existe

A Eyle permite que a LLM conectada investigue um workspace real sem transformar o runtime em um segundo agente escondido. A LLM decide o que precisa ser estabelecido, como investigar, quais Evidence sustentam suas conclusões e como responder. O runtime controla estrutura, estado, execução de tools, hashes, freshness, segurança, budgets, confirmação e validações determinísticas.

O core é independente de provider. Qwen, Llama e outros modelos compatíveis usam o mesmo protocolo; somente `llm/` se adapta ao structured output realmente entregue pela conexão.

## Arquitetura

```text
interface
→ runtime/service
→ AgentSession
   ├─ request atual + conversation_background
   ├─ Investigation Contract (o que ainda falta estabelecer)
   ├─ investigation_map (onde a agente já navegou)
   └─ Evidence + estado do runtime
→ handshake estruturado administrativo
→ LLM principal ↔ 16 tools determinísticas + workspace real
→ Final Gate determinístico
→ Claim Review (único 2FA semântico)
   ├─ supported → resposta
   ├─ contradicted → Repair local → Reverify
   └─ insufficient / target gap → reabrir investigação dirigida
```

## Investigation Contract

A Rev5.2 substitui o antigo `plan` livre por um ledger semântico persistente. A própria Main LLM declara apenas alvos materialmente necessários:

```json
{
  "id": "T3",
  "goal": "Establish AgentSession's role in the real execution path",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Os estados são `open`, `established` e `dismissed`. Um target existente não pode desaparecer silenciosamente nem mudar de `goal`. `established` exige Evidence real e motivo; `dismissed` exige motivo. O runtime valida somente essas propriedades mecânicas — ele nunca decide se a Evidence realmente prova o target.

Uma resposta fundamentada no projeto não passa pelo Final Gate enquanto houver target `open`. O Claim Review recebe o mesmo contrato e pode contestar target `established`/`dismissed` usando `target_id`, fazendo o runtime reabrir exatamente aquela dívida. Um escopo material ausente do contrato usa `target_id=null`; cabe à Main LLM decidir se cria novo target, reformula a investigação, restringe a resposta ou informa limitação.

`investigation` e `investigation_map` permanecem separados: um guarda **propósito**, o outro **histórico de navegação**.

## Tools

A Main LLM continua recebendo exatamente 16 tools públicas:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status` e `git_diff`.

A Rev5.2 não adiciona Planner, ResearchManager, tools `callers/callees/references`, ranking semântico de arquivos ou outro sistema de coverage de leitura. O benchmark mostrou falta de direção, não falta de capacidade de descoberta.

A escrita continua usando um único protocolo: `action=patches` → dry-run → confirmação → apply → compile/testes/releitura → rollback em falha.

## Evidence e Claim Review

A Evidence completa permanece no runtime. Evidence associada a targets fica pinned somente como índice compacto (`ID`, arquivo, linhas e hashes), evitando que uma fonte importante do começo da tarefa desapareça do índice depois de muitas observações.

Claim Review continua sendo o único verificador semântico. Ele verifica Claims materiais e cobertura dos targets. Claim, Semantic Gap e Finding Recovery preservam o conteúdo válido; o runtime nunca inventa verdict, Evidence, tipo de gap ou reparo semântico.

## Fronteiras de contexto

`request` é a única tarefa ativa. `conversation_background` permanece estável e não autoritativo ao longo do job. `investigation_map` preserva descobertas observáveis da tarefa atual entre follow-ups semânticos. Reads bloqueados por cobertura/repetição não contam como execução idêntica.

## Uso

```bash
python -m pip install -r requirements.lock
python main.py status
python main.py perguntar "Analise o projeto"
python main.py serve
```

Para desenvolvimento:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

## Configuração

Edite `config.json` para endpoint, modelo e limites do runtime. A capacidade de structured output é testada por comportamento e salva localmente em `context/llm_capabilities.json`, ignorado pelo Git.

Veja [Configuração](docs/configuration.md).

## Validação

A Rev5.2.3 deve ser publicada somente depois que o artefato extraído passar:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

Veja [Benchmark](docs/benchmark.md) para o cenário real de aceitação da AgentSession.

## Licença

A Eyle tem **código-fonte disponível, mas não é software open source**. Uso pessoal, privado e não comercial é permitido conforme [LICENSE.md](LICENSE.md). Redistribuição, publicação de versões modificadas, uso comercial, sublicenciamento, venda ou oferta da Eyle como serviço exigem autorização prévia por escrito.

## Documentação

- [Arquitetura](docs/architecture.md)
- [Visão técnica](docs/technical-overview.md)
- [Configuração](docs/configuration.md)
- [Benchmark](docs/benchmark.md)
- [Publicação no Git](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
- [English](README.md)
