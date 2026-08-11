<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Eyle é uma agente de código source-available construída sobre autoridade semântica explícita, controles determinísticos de Runtime, Evidence grounded e mutação supervisionada de projetos.**

**Versão:** 2.7.4 · **Schema:** 5.7.1 · **Revisão:** rev5.7.1-directed-observation-context-projection

A Eyle foi projetada para análise de repositórios, investigação de código, execução de comandos em sandbox isolado, respostas fundamentadas em Evidence e alterações de código com confirmação. A Main LLM interpreta e escolhe a estratégia; o Runtime controla execução física, estado, segurança e budgets; o Claim Review contesta a entrega grounded sem virar um segundo planner.

```text
USUÁRIO
 ↓
Main LLM                 autoridade semântica
 ↓
Capabilities             observação/execução determinística
 ↓
Observation → Evidence   estado factual canônico
 ↓
Main LLM → Final
 ↓
Claim Review             contestação semântica
 ↓
USUÁRIO
```

## Princípios de design

- **Uma autoridade semântica.** A Main LLM decide o significado do pedido, o que precisa ser estabelecido, quais tools usar e quando a investigação é suficiente.
- **O Runtime não inventa semântica.** Ele valida schemas, executa capabilities, impõe limites físicos, preserva estado canônico e rejeita operações inválidas deterministicamente.
- **Estado do mundo não é contexto do modelo.** Os ledgers podem permanecer completos enquanto cada chamada recebe apenas uma projeção limitada do estado necessário naquele turno.
- **Evidence continua grounded.** Observações de fonte, Runtime Facts e resultados de escrita permanecem endereçáveis em vez de virarem apenas memória textual da LLM.
- **Writes possuem um único caminho controlado.** Mutação real do projeto usa `WriteTransaction` com dry-run, confirmação, verificação e rollback.
- **Sem camada escondida de compatibilidade.** A Rev5.7.1 aceita somente os schemas atuais de config/session/queue/project-memory.

## Observação dirigida de código

A Eyle pode fazer perguntas estruturais sobre um projeto Python sem obrigar a Main LLM a reconstruir o repositório símbolo por símbolo.

`symbol_relations(query="reachability")` pesquisa a partir de roots explícitos ou sinais objetivos de entrypoint Python e pode devolver o caminho completo até um símbolo quando ele é estruturalmente estabelecido.

```text
main.py::<module>
→ main.py::main
→ ...
→ llm/structured.py::parse_claim_review_response
```

Resultados de capabilities usam um envelope comum de observação:

```text
status / ok / executed / changed / error_code / retryable
observations[]
coverage
frontiers[]
handles[]
detail
```

- `coverage` descreve o escopo/completude objetiva reportado pela capability.
- `frontier` identifica uma borda objetiva de continuação ainda não materializada na observação atual.
- `handle` é uma referência opaca para continuar a materialização sem repetir toda a observação.

Esses campos são opcionais para capabilities simples. Eles não transformam toda tool em uma ferramenta de grafo e não dizem à Main LLM se uma continuação é semanticamente necessária.

## Investigation e entrega grounded

Dívida semântica persistente é representada por um Investigation Contract opcional criado pela Main LLM:

```json
{
  "id": "T1",
  "goal": "Establish whether the module participates in active runtime flow",
  "status": "open",
  "evidence_ids": [],
  "reason": ""
}
```

Os estados são `open`, `established` e `dismissed`. O Runtime preserva identidade e invariantes estruturais, mas nunca cria um target sozinho.

A entrega Final declara quais Evidence a sustentam:

```json
{
  "answer": "...",
  "limitations": [],
  "evidence_ids": ["ev-..."]
}
```

O Claim Review pode fundamentar seu veredito em coordenadas tipadas:

```text
request
answer:<anchor>
evidence:<id>
runtime:<fact>
investigation:<target>
```

O Runtime valida existência e freshness quando necessário. Suficiência semântica continua sendo julgamento do modelo.

## Projeção de contexto

O Runtime preserva o estado canônico enquanto limita material repetido no prompt.

A Main recebe, entre outros dados do turno atual:

- request e estado da Investigation;
- Evidence fixada pela Investigation + janela recente;
- navegação de Observations fixada + recente;
- delta limitado dos resultados atuais de tools;
- contratos completos somente das duas tools distintas solicitadas mais recentemente;
- `capability_index` compacto para as demais tools chamáveis.

Tools antigas continuam chamáveis. Não existe Tool Selector, router semântico, classificador de tarefa nem estado persistido de ativação.

## Donos canônicos de estado

```text
ObservationLedger  → realidade física de tools, replay e coverage
EvidenceLedger     → ciclo de vida e freshness de Evidence citável
DecisionLedger     → decisões do Runtime e rejeições determinísticas
LLMCallLedger      → chamadas lógicas e tentativas de provider
WriteTransaction   → ciclo de mutação, validação e rollback
Investigation      → dívida semântica declarada pela Main LLM
ClaimReview        → auditoria semântica
```

Históricos, contadores, views de prompt e resumos de UI são projeções desses donos, não fontes paralelas de verdade.

## Tools

A Eyle expõe 18 tools públicas determinísticas:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `symbol_relations`, `expand_observation`, `find_symbol`, `read_file`, `run_command`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status`, `git_diff`.

Writes não são tools públicas. A Main LLM emite a ação canônica `patches` e o Runtime controla dry-run, confirmação, aplicação, verificação e rollback.

## Sandbox e segurança do projeto

`run_command` executa dentro de um snapshot forte e gravável do projeto. `backend=auto` prefere Docker e usa Bubblewrap como fallback. O workspace real nunca é montado read-write no ambiente irrestrito de comandos.

A sandbox pode usar rede, instalar pacotes/toolchains, compilar código e modificar seu snapshot descartável. Isso protege o workspace real de mutação direta; **não** torna confidencial o código-fonte que estiver visível dentro de uma sandbox com rede. Consulte [SECURITY.md](SECURITY.md) para os limites completos.

Sem backend forte disponível, a execução irrestrita falha de forma fechada com `SANDBOX_UNAVAILABLE` em vez de cair para processo local confiável.

## Limites físicos de inferência

Defaults por job:

```text
max_llm_turns          24
max_tool_calls         64
max_llm_calls          32
max_prompt_tokens      90000
max_completion_tokens  8000
max_total_tokens       98000
task_deadline_seconds  1800
backend context window <= 32768
```

São limites físicos de contenção. Eles não decidem quando uma investigação está semanticamente completa.

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

Verificação de release:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q eyle llm main.py
python -m pytest -q
node --check web/static/app.js
```

## Documentação

- [Arquitetura](docs/architecture.md) — contratos e fronteiras atuais do Runtime.
- [Visão técnica](docs/technical-overview.md) — loop, ledgers, projeção e grounding.
- [Direção arquitetural](docs/architectural-direction.md) — objetivos futuros; não descreve capabilities já entregues.
- [Configuração](docs/configuration.md) — configuração atual e fusíveis físicos.
- [Benchmarks](docs/benchmark.md) — regressões e baselines de eficiência.
- [Publicação](docs/github-publishing.md) — checks de empacotamento.
- [Changelog](CHANGELOG.md) — histórico de releases.
- [English](README.md)

## Licença

Eyle é **source-available, não open-source**. Consulte [LICENSE.md](LICENSE.md).
