<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Versão:** 2.7.4 · **Schema:** 5.6 · **Revisão:** rev5.6-grounded-outcomes-docker-backend

## Rev5.6 — Grounded Outcomes & Docker Backend

A Rev5.6 preserva a entrada canônica da tarefa e a arquitetura dirigida por propriedade da Rev5.5.5 e corrige a fronteira de verificação/execução exposta pelos benchmarks seguintes: grounding do Claim deixa de significar apenas IDs do EvidenceLedger, falhas físicas não-retryable viram fatos terminais da capability no job atual, `symbol_relations` passa a reconhecer bindings/registries comuns e pode projetar apenas a direção estrutural pedida, e `run_command` usa por padrão um sandbox Docker persistente quando Docker está disponível.

> **A Main LLM decide o que precisa ser feito. O Runtime decide o que pode acontecer fisicamente. O Claim contesta o resultado de forma independente.**

### Autoridade semântica

```text
USUÁRIO
 ↓
Main LLM
 ├─ escolhe tools
 ├─ decide se existe dívida semântica
 └─ cria Investigation somente quando precisa
        ↓
      Tools → Observation → Evidence
        ↓
      Main LLM → Final
        ↓
      Claim Review
        ├─ aceito → usuário
        └─ dívida semântica → Main LLM
```

`Investigation=[]` é um estado válido: nenhuma dívida semântica persistente foi declarada. Ler ou escrever o workspace não obriga Investigation.

Quando a Main LLM declara um target, o Runtime torna o compromisso estruturalmente rígido: o target não desaparece, o `goal` não muda escondido, `established` exige Evidence real e target `open` bloqueia o Final. O Runtime nunca inventa um target.

O Claim possui um único caminho semântico global. Se detectar escopo material omitido, pode retornar `target_id=null`; somente a Main LLM decide se cria nova Investigation.

### Grounded outcomes

O Claim verifica a resposta provisória usando coordenadas de grounding tipadas, em vez de forçar toda conclusão pelo EvidenceLedger:

```text
request                     → tarefa canônica do usuário
answer:<anchor>             → trecho delimitado da resposta
evidence:<id>               → Evidence factual citável
runtime:<fact>              → fato físico observado pelo Runtime
investigation:<target>      → dívida semântica declarada
```

Uma omissão material pode ser fundamentada por request + answer; um fato externo sobre código normalmente exige Evidence; uma impossibilidade física pode ser fundamentada por Runtime Facts. `blocked` é um resultado material válido quando a realidade física impede a execução. O Runtime só valida se as coordenadas citadas existem; não decide sua suficiência semântica.

Falhas de tool marcadas como não-retryable entram em `ExecutionContext.terminal_capabilities` e a capability deixa de ser oferecida no restante daquele job.

### Capabilities progressivas e tools gerais

A primeira chamada do Agent não recebe mais os 15 contratos completos. `capability_index` mostra somente assinatura compacta + função das tools ainda não usadas. A Main LLM pode chamar qualquer uma imediatamente; não existe Tool Selector nem chamada de ativação. Depois do primeiro uso real, a tool passa para `active_tools` com contrato expandido nas chamadas seguintes. Esse estado é derivado do DecisionLedger.

### Budget de treinamento

```text
janela Llama Server por chamada  <= 32768
prompt por mensagem/job          <= 90000
saída por mensagem/job           <= 8000
total físico por mensagem/job    <= 98000
```

Cada tentativa de backend cobra o prompt completo para o budget, mesmo quando há cache. Cache continua apenas como telemetria. Turns, tools, calls e deadline permanecem fusíveis independentes. Se o budget acabar, a tarefa falha; a LLM não ganha extensão.

No `self_check` padrão, Final sem Observation, Evidence, Investigation ou WriteTransaction não chama Claim: não existe estado grounded para o verifier auditar. O modo explícito `verified` continua verificando todo Final. Isso é derivado de estado real, não de classificação semântica da tarefa.

### O que foi removido

- `workspace_scope`; leitura/escrita são fatos observáveis de tools e patches;
- `final.evidence_ids` / `answer_evidence_ids`; Evidence de target pertence à Investigation;
- `request_policy` lexical e o subsistema paralelo `findings[]` do Claim;
- `AGENT_NO_PROGRESS`; só repetição determinística de decisão rejeitada/replay é fundida;
- `relevant_sources` / `visible_source_ranges`; ObservationLedger é o dono de identidade/cobertura/replay;
- cópias persistidas de feedback do Claim e releitura pós-write duplicada;
- tool pública `read_range`; `read_file` possui range opcional;
- conjuntos paralelos de tools no Agent e `_TOOL_CONTRACTS`; `TOOLS` é o registry operacional único;
- campos duplicados do registry (`name`, `permission`, `output_schema`) e injeção de registry alternativo;
- `INVESTIGATION_REQUIRED`, router semântico, fast paths lexicais e scheduler `analysis_*`/`write_*`;
- Progress Earned Authority e extensões de `+4 tools`;
- recoveries especializados de Claim/Gaps/Findings e seus IDs/signatures administrativos;
- Final como string, APIs antigas de Investigation, bridges/migrações e aliases históricos;
- capability negotiation/cache estruturada, downgrade `json_object`/prompt e retries de repair estrutural;
- retry automático após `finish_reason=length` e retry de transporte especial do Agent;
- limites artificiais de working-set/chat/history/contagem de Evidence/observações;
- telemetria/UI administrativa órfã de mecanismos já apagados;
- testes de revisão que só mantinham APIs removidas.

### Sem compatibilidade retroativa

A Rev5.6 possui um único contrato canônico. Estado persistido anterior de sessão, fila, memória de projeto ou configuração não é migrado nem adaptado. Estado incompatível falha explicitamente.

```text
config/session/queue/project-memory → schema 5.6
```

Portabilidade atual não é camada de compatibilidade: chamadas estruturadas de Agent/Claim exigem JSON Schema strict. Os transports OpenAI-compatible e Ollama só são válidos quando suportam esse mecanismo canônico. O fallback Python quando `rg` não existe continua sendo portabilidade operacional atual.

## Runtime

Responsabilidades:

- validação/execução determinística das tools;
- identidade, hashes e freshness da Evidence;
- Observation Ledger e replay físico;
- workspace epoch;
- segurança de paths e segredos;
- dry-run, confirmação, transação, verificação e rollback;
- schemas persistentes atuais;
- fusíveis físicos de turn/tools/tokens/deadline;
- contexto limitado pela janela física real do modelo, sem working-set artificial;
- trace e telemetria sanitizados.

Fusíveis físicos não decidem quando uma investigação semântica terminou.

## Main LLM

Responsabilidades:

- compreender o pedido;
- decidir o que precisa ser observado;
- escolher tools;
- decidir se existe dívida persistente;
- criar/atualizar Investigation;
- interpretar Evidence;
- decidir suficiência e parada;
- propor writes;
- entregar Final para o usuário.

## Investigation Contract

```json
{
  "id": "T1",
  "goal": "Establish whether the module participates in active runtime flow",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Estados: `open`, `established`, `dismissed`.

`reason` é argumento semântico da Main LLM, nunca autoridade factual.

## Tools

17 tools públicas determinísticas:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, `git_diff`.

Writes usam somente o contrato `patches`; Runtime controla dry-run, confirmação, aplicação, verificação e rollback.

## Sandbox Docker-first

`run_command` pode criar/apagar arquivos, instalar pacotes, baixar dependências, compilar e executar comandos arbitrários dentro do sandbox descartável do job. `backend=auto` prefere Docker e usa Bubblewrap como fallback. No Docker há um único container persistente por job (imagem padrão `python:3.12-slim`, com pull automático quando ausente), portanto instalações e mudanças no rootfs sobrevivem entre chamadas `run_command` do mesmo trabalho.

O workspace real nunca é montado em modo read-write. O Runtime cria primeiro um snapshot sanitizado e monta apenas essa cópia em `/workspace`; secrets protegidos ficam de fora. Sem backend forte, `run_command` retorna `SANDBOX_UNAVAILABLE` com `retryable=false`, e a Main LLM pode finalizar honestamente como bloqueada em vez de insistir.

## Executar

```bash
python -m pip install -r requirements.lock
python main.py status
python main.py perguntar "Analise o projeto"
python main.py serve
```

Desenvolvimento:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

Validação de release:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

## Documentação

- [Arquitetura](docs/architecture.md)
- [Visão técnica](docs/technical-overview.md)
- [Configuração](docs/configuration.md)
- [Benchmark](docs/benchmark.md)
- [Publicação](docs/github-publishing.md)
- [Histórico](CHANGELOG.md)

## Licença

Eyle é **source-available, não open source**. Veja [LICENSE.md](LICENSE.md).
