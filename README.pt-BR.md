<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Versão:** 2.7.4 · **Schema:** 5.4 · **Revisão:** rev5.2.9-progress-earned-authority

## Rev5.2.9 — Autoridade Conquistada por Progresso

A Rev5.2.9 mantém a arquitetura da Rev5.2.8 e remove um teto artificial de autoridade em vez de adicionar outro subsistema. O fusível-base continua em 12 tools físicas, mas cada epoch de `committed_progress` validado pelo Runtime pode liberar +4 tools exatamente uma vez quando o gate físico precisar; não existe mais teto cumulativo de +8. Um conjunto persistente global de Evidence já creditada impede que Evidence antiga seja remapeada ou reaberta para comprar autoridade novamente. `investigation_updates.evidence_ids` agora é delta aditivo de verdade: o Agent envia somente Evidence nova que julgou material, e o Runtime preserva automaticamente toda Evidence já commitada. O follow-up do Claim também recebe capacidade restante determinística para gastar as poucas chamadas LLM de forma deliberada. Os limites normais de 8 turnos e 12 chamadas LLM permanecem iguais.

## Rev5.2.8 — Canonical Runtime Cleanup

A Rev5.2.8 não adiciona agente, tool, ledger nem budget. Ela corrige os contratos já existentes depois que o benchmark de código legado expôs um falso `ADMINISTRATIVE_LOOP`: a identidade do Decision Ledger agora inclui estado objetivo observado e autoridade física, enquanto o progresso do Runtime ignora churn livre de `reason/status` da Investigation. Batches com chamada inválida são rejeitados atomicamente antes de tool authority, e o ABI público das tools usa um único vocabulário canônico (`path`, `line_start`, `line_end`, `symbol`, `limit`, `depth`, `filter`) sem aliases legados. Targets `open` podem acumular Evidence incrementalmente quando o Agent julgar material. Os classificadores lexicais aposentados de workspace/write e o wrapper antigo de assinatura semântica de leitura foram apagados. Os limites físicos permanecem iguais.

## Rev5.2.7 — Two-Brain Claim Follow-up & Loop Control

A Rev5.2.7 removeu o perfil semântico `claim_repair` e passou a devolver `contradicted`, `insufficient` e semantic gaps ao Agent principal pelo mesmo follow-up determinístico de reopen/pin/feedback. Somente `agent` produz semântica da tarefa e somente `claim_verifier` a julga de forma independente.

## Rev5.2.5 — Transactional Contract Authority

A Rev5.2.5 mantém o fusível-base de 12 tools, mas move a liberação progressiva de autoridade para o administrador de contratos do Runtime. A Main LLM passa a enviar somente `investigation_updates`; o Runtime mantém o Investigation Contract canônico, commita updates estruturalmente válidos de forma independente, preserva os itens aceitos quando outro falha e deposita `committed_progress` objetivo quando Evidence real é vinculada ou um target é estabelecido validamente. Esse depósito vira autoridade dormente: somente quando o gate físico impedir um lote atômico, ainda existir dívida aberta e houver novo progresso commitado desde a última extensão o Runtime pode liberar +4 tools, limitado a +8 nesta release. O Claim Review volta a ser apenas o segundo cérebro verificador da conclusão provisória. O histórico mantém **expandir tudo / recolher tudo** e agora mostra committed progress e extensões conquistadas.

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
→ LLM principal ↔ updates transacionais de Investigation
→ administrador de contratos do Runtime ↔ 16 tools determinísticas + workspace real
→ Final Gate determinístico
→ Claim Review (único 2FA semântico)
   ├─ supported → resposta
   ├─ contradicted → Runtime reabre a dívida mapeada → Main Agent
   └─ insufficient / semantic gap → mesmo follow-up dirigido
```

## Investigation Contract

A Rev5.2 substitui o antigo `plan` livre por um ledger semântico persistente. Na Rev5.2.5 o Runtime mantém esse ledger canônico e a Main LLM envia somente deltas em `investigation_updates`. Targets omitidos permanecem exatamente como estavam e Evidence já commitada não pode desaparecer silenciosamente. A Main LLM continua decidindo toda a semântica e declara apenas alvos materialmente necessários:

```json
{
  "id": "T3",
  "goal": "Establish AgentSession's role in the real execution path",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Os estados são `open`, `established` e `dismissed`. Um target existente não pode desaparecer silenciosamente nem mudar de `goal`. `established` exige Evidence real e motivo; `dismissed` exige motivo. Updates válidos são commitados individualmente, então um sibling inválido não apaga trabalho aceito. O runtime valida somente essas propriedades mecânicas — ele nunca decide se a Evidence realmente prova o target.

Uma resposta fundamentada no projeto não passa pelo Final Gate enquanto houver target `open`. O Claim Review recebe o mesmo contrato e pode contestar target `established`/`dismissed` usando `target_id`, fazendo o runtime reabrir exatamente aquela dívida. Um escopo material ausente do contrato usa `target_id=null`; cabe à Main LLM decidir se cria novo target, reformula a investigação, restringe a resposta ou informa limitação.

`investigation` e `investigation_map` permanecem separados: um guarda **propósito**, o outro **histórico de navegação**.

## Tools

A Main LLM continua recebendo exatamente 16 tools públicas:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status` e `git_diff`.

A Rev5.2 não adiciona Planner, ResearchManager, tools `callers/callees/references`, ranking semântico de arquivos ou outro sistema de coverage de leitura. O benchmark mostrou falta de direção, não falta de capacidade de descoberta.

A escrita continua usando um único protocolo: `action=patches` → dry-run → confirmação → apply → compile/testes/releitura → rollback em falha.

## Evidence e Claim Review

A Evidence completa permanece no runtime. Evidence associada a targets fica pinned somente como índice compacto (`ID`, arquivo, linhas e hashes), evitando que uma fonte importante do começo da tarefa desapareça do índice depois de muitas observações.

Claim Review continua sendo o único verificador semântico independente. Ele verifica Claims materiais e cobertura dos targets depois do final provisório. Ele **não** concede autoridade de tools, não define `committed_progress`, não reescreve a resposta e não escolhe tools. Recovery local de Claim, Semantic Gap e Finding corrige somente saída estruturada inválida do próprio revisor; dívida semântica `contradicted`, `insufficient` ou gap volta para a Main Agent pelo estado de follow-up do Runtime.

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

A Rev5.2.9 deve ser publicada somente depois que o artefato extraído passar:

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
