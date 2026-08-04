# Revisão 53.0-speed-cycle-hardening — velocidade, cache e ciclos

- `engine/agent.py`: parser rejeita mais de uma decisão JSON válida e integra
  detecção de ciclos curtos após cada tool realmente executada.
- `engine/agent_state.py`: fingerprint estável combina resultado, evidências,
  estado de edição, blockers e evidências ainda necessárias.
- `llm/cache.py` e `llm/executar.py`: envelopes estruturados de falha são
  invalidados; respostas só entram no cache após o orçamento aceitar os tokens.
- `engine/queue.py`: a reserva de jobs possui teto de ciclos sob conflito.
- `engine/engine.py`: retrieval repetido é reutilizado, lacunas/buscas repetidas
  encerram o Analista e retries recusados pelo Verify usam backoff exponencial.
- `engine/config_schema.py`: valida os novos limites de backoff do Executor.
- `web/routes.py`: falhas de permissão do token web são observáveis na telemetria.
- `tests/test_hardening_53.py`: nove regressões específicas.
- Validação local: `compileall` e **202/202 testes executáveis** aprovados; um
  teste web ignorado por ausência de Flask. Benchmark real continua dependente
  do endpoint/modelo da instalação final.

---

# Revisão 52.0-complete-hardening — fechamento da auditoria

- `engine/grounding.py`: valida cada afirmação contra a evidência declarada e
  bloqueia identificadores, caminhos, números e literais objetivos sem suporte.
- `engine/worker.py`: executa jobs em processos filhos termináveis, aplica
  deadline de parede e mantém consumidores paralelos configuráveis.
- `engine/process_limiter.py`: serializa a LLM entre processos com SQLite, lease
  recuperável e remoção de slots pertencentes a processos mortos.
- `llm/cache.py`: cache indexado em SQLite com migração automática do JSON legado.
- `engine/telemetry.py`: registra jobs, tools e LLM e calcula P50/P95/P99.
- `engine/queue.py`: heartbeat com PID, recuperação seletiva e detecção de fila bloqueada.
- `llm/executar.py`: orçamento LLM central, métricas, reset/timeout transitórios e
  fallback de descoberta de modelo observável.
- `engine/engine.py`: fallbacks legados estruturados e early exit em reprovação repetida.
- `web/routes.py` e `main.py`: health/status com fila, workers, avisos e métricas.
- Validação local: `compileall` e **193/193 testes executáveis** aprovados; um
  teste web ignorado por ausência de Flask. O benchmark LLM real continua
  dependente do endpoint/modelo da instalação final.

---

# Revisão 51.0-hardening — limites operacionais e desempenho

- `engine/agent.py`: deadline, orçamento de chamadas/tokens e parser JSON estrito.
- `llm/executar.py`: timeouts por perfil, retry transitório, backoff/jitter,
  cooldown, semáforo e cache negativo da descoberta de modelos.
- `llm/cache.py`: rejeição/invalidação de vazios e `[erro]`; hits em lote.
- `engine/agent_state.py`: equivalência semântica de tools repetidas.
- `retrieval/buscar.py`: BM25 em memória com invalidação por fingerprint.
- `engine/queue.py` e `engine/worker.py`: heartbeat, schema único e ciclo resiliente.
- `engine/config_schema.py`: limites operacionais e consistência de provider.
- `engine/release_identity.py`: build falha se config, manifesto e README divergirem.
- Validação: 179/179 testes não-web e `compileall` aprovados.

Limites: testes web e benchmark real não executados; entailment semântico completo
ainda não é verificado deterministicamente.

---

# Revisão 50.1 — agente estruturado confiável no llama-server

- `llm/executar.py`: schema JSON explícito, controles de thinking com fallback,
  leitura de `reasoning_content` quando `content` vier vazio e cache desativado
  somente para decisões estruturadas do Agente.
- `engine/agent.py`: parser incremental substitui o regex guloso.
- `config.json`: `model=auto`, 8192 tokens de janela, 1500 de saída, timeout 600
  e três tentativas de parsing.
- Regressões novas cobrem schema, thinking separado, cache envenenado e JSON
  misturado com texto.

---

# Atualização 50 — compatibilidade básica de modelos no llama-server

## Problema

A Eyle dependia de um nome de modelo fixo no `config.json` e sempre enviava
`response_format` ao Agente quando o JSON mode estava ligado. Um GGUF novo, um
llama-server antigo ou um template que rejeitasse `role=system` podia responder
HTTP 400 e interromper a tarefa sem necessidade.

## Implementação mínima

- `llm/executar.py` consulta `/v1/models` em backends OpenAI-compatible. Se o
  servidor expõe apenas um modelo, esse ID é usado automaticamente quando o
  nome configurado ficou antigo ou está como `auto`.
- O Agente tenta JSON mode nativo uma vez. Em HTTP 400/404/422, repete sem
  `response_format`, mantendo o contrato JSON pelo próprio prompt.
- Se o pedido ainda for rejeitado, repete sem `role=system`, incorporando as
  instruções do sistema à mensagem `user`.
- As capacidades detectadas ficam apenas em memória por `base_url + modelo`;
  nenhuma nova persistência ou perfil de modelo foi criado.
- Respostas estruturadas removem blocos `<think>`, `<analysis>` e `<reasoning>`
  e cercas Markdown antes do parser do Agente. Chat comum não é alterado.
- `tests/test_llm_executar.py` cobre seleção do modelo carregado, fallback de
  JSON mode, cache da capacidade, fallback do papel system e limpeza de
  raciocínio visível.

## Limites intencionais

A detecção não tenta adivinhar família, template ou parâmetros ideais do modelo.
Com vários modelos servidos ao mesmo tempo, o alias exato continua vindo de
`config.json`. A Eyle preserva seu protocolo JSON próprio em vez de depender do
tool calling nativo de Qwen, Mistral, LFM ou outras famílias.

---

# Atual Versão — Eyle (changelog completo)

**Leitura opcional.** Pra saber o estado atual antes de propor uma
atualização nova, leia `ESTADO_ATUAL.md` (10-15 linhas, sempre
atualizado). Este arquivo aqui é o histórico rico — cada função, cada
arquivo alterado, cada motivo — útil pra entender *por que* algo foi
feito de um jeito, caro em tokens pra carregar inteiro, por isso não é
mais obrigatório em toda sessão nova (foi o próprio custo de manter
isso que fez o arquivo ficar desatualizado por um tempo — ver decisão
registrada em `ESTADO_ATUAL.md`/conversa: os dois arquivos existem
justamente pra não repetir esse problema).

Últimas atualizações aplicadas: **Atualizações 48-49 + revisão corretiva 49.1 —
rollout gradual, retomada geral, idempotência e leitura obrigatória**.
(base: eyle091-fase4-agente-corrigido-2 + Atualizações 10-49)

Este arquivo lista todas as funções e funcionalidades criadas ou
alteradas até agora, por arquivo. As Atualizações 16-39 aplicam o plano de
hardening (`Plano_Hardening_Eyle.md`, derivado de
uma auditoria externa). As 16-17 corrigem **bugs em código das próprias
Atualizações 10 e 11**; a 18 prende leituras à raiz; a 19 garante escrita
atômica e rollback sem depender de backup; a 20 separa falha da LLM de
resposta real; a 21 padroniza tools; a 22 prende confirmações à pendência
e ao projeto; a 23 filtra/acumula o contexto do Analista; a 24 indexa
Python via AST; a 25 congela o histórico do job; a 26 persiste a fila e
as falhas; a 27 protege a API web; a 28 isola execução; a 29 filtra a
ingestão; a 30 separa métricas reais de grounding; a 31 remove a mensagem
atual do histórico enviado ao chat; as 32-39 fecham o acabamento do hardening.
As 40-47 aplicam `Plano_Eyle_Agente_40_em_diante.md`: contrato de tools, olhos
reais, contexto virtual, conclusão objetiva, roteamento unificado, plano curto,
edição segura e benchmark controlado. As 48-49 fecham a ativação gradual e a
persistência completa do ciclo do Agente. Ideias posteriores continuam fora do
núcleo até haver necessidade medida.

---

## Revisão corretiva 49.1 — análise não pode desistir antes de ler

O gate das Atualizações 42-45 já recusava `final` sem evidência fresca, mas
`needs_user` ainda podia escapar antes de qualquer tool. Isso permitia que a
LLM respondesse “nenhum contexto do projeto está disponível” com zero leituras,
mesmo quando `list_tree`/`read_range` estavam disponíveis.

- `engine/agent.py` agora recusa `needs_user` de projeto enquanto não houver
  tentativa real de leitura. Uma análise geral que tente desistir no primeiro
  passo é convertida deterministicamente em `list_tree` pelo mesmo fluxo de
  schema, gate, trace e checkpoint das demais ações.
- Depois da árvore, uma nova fuga sem evidência é devolvida ao modelo como
  `PREMATURE_NEEDS_USER`; a tarefa só pode pausar após leitura/evidência ou
  bloqueio real de tool. Evidência que ficou `stale` continua sendo tratada
  como tentativa real, preservando a retomada da Atualização 49.
- `llm/executar.py` explicita que falta de contexto no prompt não é bloqueio:
  o Agente deve usar READ, começar análise geral por `list_tree` e seguir com
  `search_code`/`read_range`.
- O pacote revisado não contém arquivos mutáveis de `memory/` nem `context/`.
  Assim, extrair a atualização sobre uma instalação existente não zera índice,
  conversa, fila, pendências, token ou backups. Instalação nova cria esses
  dados normalmente no primeiro `ingest`/uso.
- Três regressões cobrem análise geral, arquivo explícito e bloqueio real.
  **148/148 testes passaram**, além de `compileall` e validação do ZIP.

---

## O que as Atualizações 48-49 entregam

### Atualização 48 — rollout explícito e observável

- `agent.rollout_mode` substitui a ativação binária por `off`, `read_only` e
  `full`. Uma única alteração para `off` devolve todo o roteamento automático
  aos pipelines anteriores.
- `read_only` é o padrão do pacote: pedidos sobre projeto usam o Agente, mas o
  gate do sistema bloqueia `WRITE` e `EXEC` antes da tool. A CLI explícita segue
  disponível com trace mesmo em `off`, também limitada a leitura.
- `full` exige que a raiz real do projeto esteja sob uma entrada de
  `agent.trusted_project_paths`. Projeto fora da allowlist cai de forma visível
  para `read_only` com `fallback_cause=project_not_in_trusted_paths`.
- Cada resultado expõe `task_id`, tools chamadas, IDs de evidência,
  `read_status`, gate de conclusão e causa determinística de fallback. O
  fallback legado em `off` também recebe causa estruturada; não vira chat
  genérico silencioso.
- Como o benchmark real do LFM2 não pôde rodar sem o backend local, o pacote não
  promove `full` por padrão. A promoção continua condicionada ao gate verde.

### Atualização 49 — tarefa durável, retomada geral e escrita idempotente

- `engine/queue.py` cria a tabela `agent_tasks` na mesma base SQLite da fila.
  Toda tarefa persiste `running`, `waiting_user`, `completed`, `blocked` ou
  `failed`, além de `GoalState`, evidências/hashes, ações, continuação, ação
  pendente, orçamento restante, resultado e auditoria.
- O loop grava checkpoint antes e depois de cada ação. Qualquer `needs_user`
  produz continuação serializável; uma resposta livre volta ao mesmo objetivo e
  ao mesmo passo sem consumir o orçamento de tools.
- O Worker associa tarefas ao ID durável do job. Após reinício, ações `READ`
  podem continuar do checkpoint; `WRITE` nunca é recolocada automaticamente e
  exige revalidação do estado final.
- Na retomada de `apply_patch`, o sistema distingue arquivo ainda original,
  patch já aplicado e estado divergente. Código novo já presente é registrado
  como recuperação (`ALREADY_APPLIED_RECOVERED`) sem executar a escrita outra
  vez; divergência termina em `STALE_PATCH`.
- Cancelamento e expiração removem continuação/ação executável, preservando
  snapshot e trilha de auditoria. Antes de continuar, hashes de evidência são
  relidos e qualquer diferença marca o ID como `stale`.

### Validação 48-49

- **145/145 testes passaram**: os 136 anteriores mais nove cenários de rollout,
  confiança de projeto, continuação livre, SQLite/auditoria, recuperação
  idempotente, retomada pós-checkpoint sem repetição, não repetição de `WRITE`
  e invalidação de evidência por hash.
- `compileall`, schema/configuração `2.6`, importação do CLI e JavaScript
  passaram. O benchmark estrutural permanece disponível; o benchmark real do
  LFM2 continua dependente do servidor local do usuário.

---

## O que as Atualizações 46-47 entregam

### Atualização 46 — edição segura no Agente real

- O modo `edit` deixou o fallback legado e entrou em `agent.enabled_modes`.
- Toda proposta nasce de `read_range` fresco e carrega SHA-256 do arquivo e da
  faixa original. `test_patch_dry_run` precisa aprovar exatamente a mesma
  proposta antes de a confirmação ser criada.
- A confirmação mostra alvo, faixa, hashes, tamanho e impacto. Na retomada, os
  hashes são validados novamente; divergência encerra como `STALE_PATCH` sem
  tocar no arquivo.
- A aplicação é atômica e conserva snapshot interno para rollback. `run_tests`
  continua sendo `EXEC`: falha reverte o arquivo, sucesso exige releitura final
  e ausência de suíte termina como `applied_without_suite`, nunca como teste
  aprovado.
- O resultado distingue `tests_passed`, `applied_without_suite`, `reverted`,
  `rollback_failed` e `blocked`.

### Atualização 47 — benchmark com gate automático

- Novo `engine/benchmark.py` monta projetos temporários e executa dez cenários:
  leitura simples, símbolo, relação entre arquivos, índice stale, símbolo
  ausente, edição confirmada, rollback, retomada, instrução maliciosa e chat.
- `python main.py benchmark` mede leitura real, acerto factual, grounding,
  referências inventadas, falhas de JSON, latência, falso sucesso, autorização
  e cinco garantias do ciclo de escrita.
- O modelo principal configurado é o alvo; `--baseline-model` aceita um Q4 4B
  apenas como linha de base. O relatório é salvo em
  `context/benchmark_latest.json`.
- O gate não ativa a Atualização 48 sozinho. Como o backend local não estava
  disponível nesta sessão, o benchmark real do LFM2 deve ser executado na
  máquina do usuário antes da ativação gradual.

### Validação

- **136/136 testes passaram**, incluindo confirmação obrigatória,
  `STALE_PATCH`, dry-run exato, releitura, rollback e `executed=false` honesto.
- `compileall`, schema/configuração, CLI do benchmark e JavaScript passaram.
- A próxima atualização numerada é a **48**, dependente do relatório real do
  LFM2 permanecer verde.

---

## O que as Atualizações 44-45 entregam

### Atualização 44 — um único Agente para pedidos sobre projeto

- `engine/roteador.py:classificar_modo_projeto` separa as intenções internas
  `analyze`, `suggest` e `edit`; com `agent.enabled=true`, qualquer mensagem
  reconhecida como relativa ao projeto recebe o tipo alto nível `agente`.
- “Analise o projeto”, consulta de arquivo/símbolo e pedido de sugestão não
  disputam mais `visao_geral`, `consulta` e `dicas`: entram no mesmo loop.
  “Oi” continua em `chat`, sem tool.
- `config.json` sobe para `2.4`, ativa `agent.enabled` e declara
  `enabled_modes: ["analyze", "suggest"]`. Esses modos recusam qualquer
  transição que não seja `READ`.
- Na entrega 44-45, `edit` também passou pelo ponto de entrada unificado, mas
  ainda usava o pipeline legado de engenharia. A Atualização 46 substituiu esse
  fallback pelo ciclo novo de escrita protegido.
- CLI (`main.py perguntar`/`main.py agente`) e painel/Worker usam o mesmo
  `engine.processar`; o resultado expõe modo e fallback quando aplicável.
- Análise geral deve começar por `list_tree`; pergunta sobre arquivo específico
  evita cerimônia e pode abrir diretamente a faixa relevante. Em ambos os
  casos, o gate 43 continua exigindo código fresco antes do `final`.

### Atualização 45 — Goal State e transições objetivas

- Novo `GoalState` em `engine/agent_state.py` cria e normaliza: objetivo, modo,
  `task_type`, critérios de sucesso, restrições, plano de no máximo cinco
  passos, passo atual, bloqueios, evidências faltantes, status, motivo do
  replanejamento e contador de ações.
- Tarefa simples com arquivo explícito recebe plano de até dois passos;
  análise/sugestão geral usa no máximo três e `edit` no máximo quatro.
- O sistema valida a próxima tool contra o modo e move o passo somente depois
  de uma execução real. Uma decisão continua contendo no máximo uma ação.
- Replanejamento automático só ocorre em `tool_failure` ou `file_changed`.
  A LLM pode enviar `goal_update` apenas para `hypothesis_denied`, com evidência
  fresca já presente e novo plano válido de uma a cinco etapas.
- `max_steps` agora conta tools realmente executadas, inclusive falhas reais,
  mas não parse retry, argumento inválido, chamada repetida ou `final` recusado.
  Depois da última ação ainda existe uma decisão para concluir. A nova guarda
  `max_no_progress_decisions: 3` pausa repetição cosmética sem criar loop.
- Todo evento de `agent_trace.jsonl` registra objetivo, modo, passo atual,
  ações executadas, bloqueios e evidências ainda necessárias. Pausa/retomada
  conserva o contrato e os contadores antigos continuam compatíveis.
- O prompt de sistema foi compactado para absorver o Goal State. Com janela de
  4080, resposta de 700, margem de 500 e catálogo atual, o primeiro passo
  típico ainda deixa cerca de **1114 tokens** para código real.

### Validação

- **131/131 testes passaram.** Os 11 cenários novos cobrem roteamento dos três
  modos, saudação em chat, paridade CLI/Worker, fallback de `edit`, plano curto,
  permissão por modo, contagem de ação real, replanejamento, trace, persistência
  e schema de configuração.
- `compileall`, validação tipada de `config.json` e sintaxe do JavaScript
  passaram.
- Este marco preparou a **46**, que depois substituiu o fallback de `edit` pelo
  ciclo de escrita com hash, confirmação, teste e releitura final.

---

## O que as Atualizações 42-43 entregam

### Atualização 42 — Context Engine de evidências estruturadas

- Novo `engine/context_engine.py` separa a janela real do modelo do
  `context.token_budget` usado pelo retrieval antigo. Em cada passo calcula:
  `janela - resposta - margem - prompt de sistema - objetivo/catálogo/estado`.
- `config.json` sobe para `2.3`, adiciona
  `llm.context_window_tokens: 4080` (valor temporário solicitado) e
  `context_engine` com margem de 500 tokens, fallback conservador de 3
  caracteres/token e quatro observações recentes.
- `AgentState` passa a persistir quatro blocos: `goal_state`, `evidence`,
  `actions` e `recent_observations`. O alias `observacoes` e a leitura do formato
  antigo permanecem compatíveis.
- `read_range` e cada resultado fresco de `search_code` geram evidência com ID
  estável, ferramenta de origem, arquivo, linhas, conteúdo numerado, SHA-256 e
  estado `fresh`/`stale`. Duplicata exata de faixa/hash reutiliza o ID.
- Código completo fica no estado externo; o prompt recebe evidências frescas
  ordenadas por relevância que cabem no saldo dinâmico. Se uma evidência for
  maior, o recorte usa o saldo disponível — não o corte fixo de 500 caracteres.
- O catálogo continua nascendo de `TOOLS`, mas o prompt usa uma projeção compacta
  que conserva nome, descrição, permissão, argumentos/tipos/obrigatórios,
  limites e saída. Com 4080, o primeiro passo típico reserva cerca de 1,3k tokens
  para código real, contra aproximadamente 182 com o JSON Schema verboso.
- Pausa e retomada preservam IDs, hashes, contadores, objetivo, ações e estado
  das evidências.

### Atualização 43 — Grounding obrigatório e conclusão objetiva

- O sistema classifica cada execução em `chat`, `project_read` ou
  `project_write`. A presença de um projeto é objetiva; intenção de mudança
  separa leitura de escrita.
- `project_read`/`project_write` recusam `final` sem pelo menos uma evidência
  fresca de código. Árvore, metadados e fatos escolhidos pela LLM não contam.
- O formato final de projeto traz `resposta`, `evidence_ids`, `verificacao` e
  `limitacoes`. Respostas antigas em string são normalizadas internamente para
  compatibilidade, mas passam pelo mesmo gate de evidência.
- Antes de cada prompt e antes do `final`, a Eyle relê as faixas declaradas no
  disco. ID inexistente, arquivo removido, faixa ajustada/fora do arquivo, hash
  diferente ou estado `stale` recusam a conclusão.
- Uma escrita real marca todas as evidências do arquivo como `stale`. Mudança
  externa detectada por hash faz o mesmo. A assinatura da faixa é liberada para
  que `read_range` possa reler exatamente os mesmos argumentos e criar evidência
  nova.
- Citações `arquivo:linha`/`arquivo:inicio-fim` na resposta só são aceitas quando
  estão cobertas pelas evidências declaradas.
- `_processar_agente` e a retomada executam o Verify honesto da Atualização 30
  usando somente os arquivos realmente empregados. `success` não fabrica
  confiança `1.0`; validade de citação, cobertura e grounding continuam campos
  separados.

### Validação

- **120/120 testes passaram.** Os novos cenários cobrem janela total de 4080,
  catálogo real dentro do orçamento, evidência do passo 1 no passo 6,
  persistência de ID/hash, `final` precoce, metadados sem código, ID inventado,
  hash antigo, releitura da mesma faixa e citação fora da faixa.
- `compileall`, validação tipada de `config.json` e sintaxe do JavaScript
  passaram.
- `agent.enabled` continua `false`; a Atualização 44 é quem começa a unificar os
  pedidos sobre projeto no loop, e a ativação padrão continua gradual até a 48.

---

## O que as Atualizações 40-41 entregam

### Atualização 40 — catálogo e validação derivados do registro

- As nove entradas de `engine/agent_tools.py:TOOLS` declaram nome, descrição,
  permissão `READ`/`EXEC`/`WRITE`, JSON schema de entrada, resumo da saída e
  limites.
- `gerar_catalogo_tools` deriva o catálogo do registro executável e resolve os
  limites atuais da configuração. `engine/agent.py` envia esse catálogo a
  `montar_prompt_agente`; a lista manual de ferramentas saiu do prompt de
  sistema.
- `validar_chamada_tool` normaliza somente aliases declarados (como
  `arquivo -> caminho_relativo`) e rejeita antes da confirmação/execução:
  argumento ausente, tipo incorreto, chave desconhecida, conflito entre alias
  e nome canônico e faixa invertida. Todos usam `INVALID_ARGUMENT`.
- O executor repete a mesma validação na fronteira da tool, inclusive em
  retomada de confirmação persistida.

### Atualização 41 — árvore e código fresco

- Novo `engine/project_reader.py`: `listar_arvore_projeto` e
  `ler_faixa_projeto` usam o resolvedor seguro compartilhado.
- Nova tool `list_tree`: lista o disco atual com limite, profundidade e filtro;
  aplica `.gitignore`, filtros internos e proteção de segredos. Motivos
  ignorados aparecem só como contagens.
- `search_code` continua usando BM25 para localizar candidatos, mas descarta o
  texto potencialmente velho do índice na resposta e relê cada faixa no disco.
  Cada resultado traz arquivo, linhas reais, símbolo, score, trecho numerado e
  SHA-256 do conteúdo efetivamente lido.
- Nova tool `read_range`: janela 1-based fresca, numerada, com hash e teto
  `agent.max_read_range_lines`. `read_file` fica como compatibilidade.
- `AgentState` preserva o código visível nos resumos de `search_code` e
  `read_range`, além de formatar a árvore de forma compacta.
- `config.json` sobe para `2.2` e adiciona `max_tree_entries`,
  `max_tree_depth` e `max_read_range_lines`.

### Validação

- **111/111 testes passaram**, incluindo o cenário de `audio.py` com 14 linhas,
  validação de 100% dos schemas, hash fresco, travessia, limites, filtros e
  motivos ignorados.
- `compileall`, `config.json` e sintaxe do JavaScript passaram.
- `agent.enabled` continua `false`; a Atualização 42 ainda é necessária para
  conservar evidência estruturada por vários passos.

---

## O que as Atualizações 32-39 entregam

- **32 — persistência atômica:** `engine/persistencia.py` publica JSON, JSONL
  e contexto de texto via temporário + `fsync` + `os.replace`. Falha no meio
  preserva o arquivo anterior e limpa o temporário.
- **33 — cache por backend:** a chave inclui provider, URL normalizada, modo
  OpenAI/Ollama, modelo, temperatura, teto e JSON mode. Servidores diferentes
  nunca compartilham resposta só porque usam o mesmo nome de modelo.
- **34 — config tipada:** `engine/config_schema.py` valida o contrato em todos
  os entrypoints e recusa arquivo ausente, JSON malformado, tipo/range inválido,
  backend desconhecido ou allowlist defeituosa antes de reservar trabalho.
- **35 — dependências fixadas:** requirements diretos têm versão exata e
  `requirements.lock`/`requirements-dev.lock` fixam também as transitivas,
  preservando Python 3.8+.
- **36 — retenção:** histórico tem teto; cache aplica idade + LRU; traces são
  rotacionados; backups saem por idade, quantidade e tamanho total.
- **37 — estado real da interface:** `/jobs/<id>` expõe somente metadados
  seguros do job. O navegador persiste e consulta o `job_id`; não deduz mais
  conclusão comparando IDs de mensagens nem anima etapas fictícias.
- **38 — índice verificável:** o hash antigo virou `source_path_hash` e o novo
  `index_fingerprint` usa hashes completos dos arquivos aceitos, configuração
  relevante e versão do indexador. `python main.py status` recalcula e avisa
  quando a fonte mudou.
- **39 — permissão EXEC:** `run_tests` saiu de READ. EXEC possui gate próprio
  (`require_confirmation_for_exec=false` por padrão), continua no sandbox e
  recebe argv antes da execução; metacaracteres de shell são literais.

### Validação

- **97/97 testes passaram**, incluindo API Flask e os testes novos 32-39.
- `compileall`, `config.json` e `node --check web/static/app.js` passaram.
- `agent.enabled` continua `false`; estas atualizações fecham hardening, não o
  Contexto de Evidências planejado para 40+.

---

## O que as Atualizações 30-31 entregam

### Atualização 30 — Verify honesto

- `verify/validar.py` agora publica `citation_validity`, `coverage` e
  `grounding` separadamente. O campo `confianca` permanece apenas como alias
  temporário para consumidores antigos e nunca nasce de `status="success"`.
- Uma citação só conta como válida quando arquivo, linha inicial e linha final
  existem. Faixa invertida e basename ambíguo também são rejeitados.
- `verificacao_aprovada` é o gate objetivo usado pelo retry do Executor;
  cobertura continua uma medida informativa e não força a LLM a citar todo
  chunk recuperado.
- Agente, proposta e aplicação de patch não recebem mais `1.0` automático:
  sucesso operacional fica no status/teste correspondente, não em grounding.
- `memory/historico.json` passa a guardar as três métricas separadas.
- `main.py` mostra as três métricas no CLI.

### Atualização 31 — mensagem atual uma única vez

- `engine/engine.py:_historico_sem_mensagem_atual` remove apenas o último item
  quando ele é exatamente a pergunta corrente já registrada.
- Funciona tanto na CLI quanto no snapshot imutável da fila web.
- Uma pergunta igual feita em turno anterior permanece no histórico; só a
  ocorrência corrente sai antes de `executar_chat` adicionar `MENSAGEM ATUAL`.

### Validação

- 75/75 testes do núcleo passaram no executor local compatível.
- A suíte total agora tem 81 testes; os 6 testes Flask não puderam ser
  repetidos nesta sessão porque Flask/pytest não estão instalados.
- `compileall` e JSON de configuração/memória válidos.

---

## O que as Atualizações 10-13 entregam

Quatro correções pontuais, cada uma isolada e testável em separado —
nenhuma depende das outras para funcionar, e nenhuma muda o
comportamento por padrão além do que está descrito (`agent.enabled`
continua `false`; quem já testava o Agente via `main.py agente "..."`
ganha as quatro automaticamente).

### Atualização 10 — Verificador de conclusão objetivo

**Problema**: `if "final" in decisao: return "success", ...` aceitava a
palavra da LLM sem checar nada — mesmo depois de uma escrita real no
projeto (`apply_patch`), sem `run_tests` ter rodado.

**Correção**: `executar_agente` agora recusa `{"final": ...}` quando a
tarefa usou uma tool `WRITE` e `run_tests` ainda não rodou com sucesso
depois dela — devolve uma observação (`AgentState.observar_final_sem_verificacao`)
pedindo para rodar `run_tests` primeiro, e dá mais um passo em vez de
encerrar a tarefa. Flag `config["agent"]["exigir_run_tests_apos_escrita"]`
(default `true`) desliga isso se precisar, sem tirar o código.

- `engine/agent_state.py`: novos campos `houve_escrita`,
  `testes_ok_apos_escrita`; novos métodos `registrar_escrita()`,
  `registrar_testes(resultado_run_tests)`,
  `observar_final_sem_verificacao()`. Persistidos em `to_dict()`/`from_dict()`
  (retrocompatível — `agent_pendente.json` salvo antes desta atualização
  ainda carrega, com os campos novos assumindo `False`).
- `engine/agent.py`: marca `registrar_escrita()` depois de qualquer tool
  `WRITE` executada (tanto no branch `retomar` quanto, se um dia houver
  outra tool WRITE, no loop principal); marca `registrar_testes(...)`
  depois de `run_tests`; checa as duas flags antes de aceitar `"final"`.
- `llm/executar.py`: `PROMPT_AGENTE` ganha a regra 9, avisando a LLM
  sobre essa exigência (evita gastar uma tentativa "no escuro").
- `config.json`: `agent.max_erros_consecutivos` (ver Atualização 11)
  e `agent.exigir_run_tests_apos_escrita` (default `true`).

### Atualização 11 — Circuit breaker de erro consecutivo

**Problema**: a guarda de repetição (Atualização 3) só barra a MESMA
`(tool, arguments)` — um modelo pequeno que varia levemente o argumento
numa tentativa quebrada escapa dela e pode insistir indefinidamente
(até `max_steps`) num caminho que não funciona.

**Correção**: `AgentState.erros_consecutivos` conta qualquer erro de
tool (resultado com chave `"erro"`) em sequência, **independente de
qual tool ou argumentos** — zera assim que uma tool roda sem erro. Ao
atingir `config["agent"]["max_erros_consecutivos"]` (default `3`), o
loop para em `needs_user` em vez de continuar tentando.

- `engine/agent_state.py`: novo campo `erros_consecutivos`, novo método
  `registrar_resultado_tool(resultado)`. Persistido em `to_dict()`/`from_dict()`.
- `engine/agent.py`: chama `registrar_resultado_tool(...)` depois de
  toda execução de tool (`retomar` e loop principal); checa o limite
  logo em seguida e devolve `needs_user` se estourou.
- `config.json`: `agent.max_erros_consecutivos` (default `3`).

### Atualização 12 — `fatos_importantes` em `AgentState`

**Problema**: `AgentState` só guardava `observacoes` (resumos de tool,
cortados às últimas `max_entradas` no prompt) — um fato descoberto no
passo 1 (ex: "o projeto usa pytest") desaparecia do prompt a partir do
passo 5 numa tarefa de 8 passos.

**Correção**: lista separada, alimentada por uma chave opcional
`"fato_importante"` que a LLM pode incluir em qualquer decisão
(`tool_call`, `final` ou `needs_user`). Ao contrário de `observacoes`,
**nunca é cortada** por `max_entradas` em
`compiler.py:montar_prompt_agente` — sempre entra inteira no próximo
prompt, num bloco `FATOS IMPORTANTES` separado do `HISTORICO RECENTE`.
Teto `max_fatos_importantes` (FIFO, default `10`) só para não crescer
sem limite numa tarefa muito longa.

- `engine/agent_state.py`: novo campo `fatos_importantes`, novo método
  `registrar_fato(fato)`. Persistido em `to_dict()`/`from_dict()`.
- `engine/agent.py`: chama `estado.registrar_fato(decisao.get("fato_importante"))`
  logo após parsear qualquer decisão válida; passa
  `fatos_importantes=estado.fatos_importantes` para `montar_prompt_agente`.
- `engine/compiler.py`: `montar_prompt_agente` ganha o parâmetro
  `fatos_importantes` e o bloco correspondente no prompt.
- `llm/executar.py`: `PROMPT_AGENTE` ganha a regra 10, explicando o
  campo opcional para a LLM.
- `config.json`: `agent.max_fatos_importantes` (default `10`).

### Atualização 13 — roteador não deixa pergunta sobre o projeto cair em `chat` sem contexto

**Problema real que motivou esta correção** (sessão de revisão, mensagem
real do usuário): *"Como melhorar o projeto? Me dê 3 caminhos"* não batia
em `PALAVRAS_DICAS` nem em nenhuma outra categoria de
`classificar_pergunta`, e caía no fallback final — `"chat"`, que roda
sem NENHUM contexto do projeto (`Executor direto`). A resposta saiu
genérica e sem relação real com a Eyle, com a mesma confiança de uma
resposta com contexto de verdade — o usuário não tinha como distinguir
uma da outra sem já conhecer o código.

**Correção**: antes do fallback final para `"chat"`, `classificar_pergunta`
agora confere se a mensagem menciona algum `SUBSTANTIVOS_PROJETO` (o
mesmo vocabulário que `_pede_inspecao_projeto` já usa, com a mesma
tolerância a erro de digitação) — se sim, cai em `"visao_geral"` em vez
de `"chat"`, mesmo sem verbo de inspeção reconhecido. Mensagens
realmente sem relação com o projeto (`"oi, tudo bem?"`) continuam caindo
em `"chat"` normalmente.

- `engine/roteador.py`: nova checagem entre `_pede_inspecao_projeto(...)`
  e o `return "chat", ...` final de `classificar_pergunta`.

### Arquivos de teste (`tests/test_agent.py`)

Sete testes novos, mesmo padrão de mock dos cinco já existentes (LLM e
tools sempre mockadas, nada de modelo local nem projeto indexado de
verdade): dois para o verificador de conclusão (aceita depois de
`run_tests` ok; recusa e dá um passo extra sem ele), dois para o circuit
breaker (para em `needs_user` após N erros seguidos; zera o contador num
sucesso no meio), um para `fatos_importantes` (sobrevive ao corte de
`max_entradas`), e dois para o roteador (mensagem sobre o projeto cai em
`visao_geral`; mensagem sem relação nenhuma continua em `chat`, sem
regressão). O teste 4 existente (`tool WRITE -> pausa/retoma`) foi
ajustado para incluir uma chamada a `run_tests` antes do `final` — o
comportamento antigo (aceitar `final` direto após a escrita) era
justamente o bug que a Atualização 10 corrige, então o teste que o
validava tinha que mudar junto.

---

## O que **não** mudou nas Atualizações 10-13

- Nenhuma tool nova, nenhuma mudança em `agent_tools.py` — as quatro
  atualizações só mudam COMO o loop e o roteador decidem, nunca O QUE
  as tools fazem.
- `agent.enabled` continua `false` por padrão — nada aqui muda a
  exposição do Agente a usuário real via chat automático.
- `planning_mode`, `llm_profiles`/roteamento por personalidade e
  qualquer decomposição multi-passo continuam fora de escopo — eram
  discutidos como próximo passo, mas dependem de ter um segundo modelo
  rodando de verdade para valer a pena implementar (ver decisão
  registrada em conversa: não construir isso especulativamente).
- Os pipelines `chat`, `consulta`, `dicas`, `engenharia` continuam
  funcionando exatamente como antes, exceto pela única mudança de
  classificação da Atualização 13 (mensagem que menciona o projeto sem
  bater em categoria específica).

---

## O que a Atualização 14 entrega

**Caso real que motivou esta correção** (sessão de revisão, transcript
real do usuário): o servidor local da LLM falhou duas vezes seguidas
(timeout, depois conexão recusada ao reiniciar) — cada falha gerou uma
mensagem `"[erro] ..."` mostrada normalmente na conversa. Na mensagem
seguinte ("olá", uma simples saudação), a resposta veio comentando
*"parece que houve uma interrupção na conexão... posso sugerir
diagnosticar"* — um comportamento sem nenhum sentido pra quem só disse
"olá".

**Diagnóstico**: `llm/executar.py:_chamar_llm` já tem a convenção
`"[erro] ..."` pras três falhas possíveis (HTTP recusado, conexão
recusada, exceção genérica) — inclusive já usa esse prefixo pra **não
cachear** erro (`if cache_ativado and not resposta.startswith("[erro]")`).
Só que `engine/engine.py:_processar_chat` salvava essa string em
`memory/conversa.json` com `registrar_mensagem("assistant", resposta)`
exatamente como qualquer resposta real — e carregava as últimas 6
mensagens **cruas** (`carregar_conversa()[-6:]`) pra montar o HISTÓRICO
RECENTE da próxima chamada. O modelo via seu próprio erro anterior como
se fosse uma fala real do assistente na conversa, e reagia a ele.

**Correção**: nova função `_historico_sem_erros_llm(mensagens)` filtra
qualquer mensagem com prefixo `"[erro]"` **antes** de cortar as últimas
6 — assim uma sequência de falhas não empurra conteúdo real pra fora da
janela nem contamina o contexto da próxima chamada.

- `engine/engine.py`: nova função `_historico_sem_erros_llm(mensagens)`;
  `_processar_chat` usa `_historico_sem_erros_llm(carregar_conversa())[-6:]`
  em vez de `carregar_conversa()[-6:]` direto.

**Escopo desta correção**: só `_processar_chat` monta histórico de
conversa pra mandar de volta à LLM (conferido — nenhum outro pipeline
faz isso hoje). As mensagens de erro continuam sendo salvas em
`conversa.json` e visíveis na transcrição pro usuário (isso é
informação útil, não é o bug) — só param de ser **repassadas como
contexto** pra próxima chamada.

> **Estado atual:** essa era a regra nas Atualizações 14-19. A
> Atualização 20 substituiu a string de erro por `ErroLLM`; falhas novas
> não são mais salvas como mensagens. O filtro permanece para limpar o
> histórico legado já existente.

### Arquivos de teste (`tests/test_engine.py`, novo)

Primeiro arquivo de teste de `engine/engine.py` (até aqui só
`engine/agent.py`/`agent_state.py` tinham cobertura). Três testes, mesmo
padrão de mock: `_historico_sem_erros_llm` isolada (remove só erro,
preserva ordem; não quebra com lista vazia ou sem erro nenhum) e
`_processar_chat` de ponta a ponta (com `carregar_conversa`,
`registrar_mensagem` e `executar_chat` mockados) confirmando que o
`historico` que chega em `executar_chat` nunca contém as mensagens de
erro, mesmo com elas presentes e dentro da janela das últimas 6.

---

## O que **não** mudou na Atualização 14

- Nada em `llm/executar.py` — o prefixo `"[erro]"` já existia e já era
  usado (pra não cachear); esta atualização só passou a fazer mais uma
  coisa com ele, sem mudar o formato nem o texto das mensagens de erro.
- O usuário continua vendo a mensagem de erro normalmente quando o
  servidor local falha — isso é informação real e útil, não foi
  removido. Só parou de ser reenviado pra LLM como se fosse conversa.
- Nenhum outro pipeline (`consulta`, `dicas`, `visao_geral`,
  `engenharia`, `agente`) foi alterado — nenhum deles monta histórico de
  conversa pra LLM hoje, então nenhum tinha esse bug.

Esses três itens descrevem o escopo histórico da Atualização 14; o
contrato global foi deliberadamente alterado depois pela Atualização 20.

---

## O que a Atualização 15 entrega

**Caso real que motivou esta correção** (log real do servidor local,
colado em sessão de revisão): uma mensagem trivial ("oi") gerou uma
resposta que passou de 600 tokens (ainda incompleta, ~7 tokens/s no
hardware do usuário) até a chamada ser cancelada. Logo depois, um
pedido de análise do projeto também terminou em `[erro] Falha ao chamar
a LLM local: timed out`.

**Diagnóstico**: nenhuma chamada em `llm/executar.py` (`_chamar_ollama`,
`_chamar_openai_compatible`) limitava quantos tokens o modelo podia
gerar por resposta — sem teto, uma resposta trivial pode consumir o
orçamento inteiro de `timeout_seconds` só com verbosidade, em vez de
parar quando já respondeu o que precisava. Isso não é o mesmo bug da
Atualização 14 (aquele era sobre o QUE acontece depois de um erro; este
é sobre o erro acontecer com mais frequência do que precisava).

**Correção**: `_chamar_llm` agora lê `config["llm"]["max_tokens"]`
(default `700`) e repassa como `num_predict` (dentro de `options`,
formato do Ollama nativo) ou `max_tokens` (campo padrão do formato
OpenAI-compatible — LM Studio/llama.cpp server/text-generation-webui).
`0`/`null` desliga o teto, preservando o comportamento anterior a esta
atualização pra quem preferir sem limite.

- `llm/executar.py`: `_chamar_ollama` e `_chamar_openai_compatible`
  ganham o parâmetro `max_tokens=None`; `_chamar_llm` lê
  `cfg_llm.get("max_tokens", 700)` e repassa pras duas.
- `config.json`: nova chave `llm.max_tokens` (default `700`) — o
  comentário explica a conta pra escolher o valor certo pro hardware de
  cada um: `max_tokens * (tokens/s do log do servidor)` deveria ficar
  bem abaixo de `timeout_seconds`.

**Nota sobre a causa raiz, fora do escopo desta correção**: ~7 tokens/s
é lento — provavelmente CPU-only ou sem offload completo pra GPU no
servidor local. Isso não é algo que o código da Eyle resolve; o teto de
tokens só limita o *dano* de uma geração longa, não acelera a geração
em si. Vale investigar a configuração do servidor de inferência
(Ollama/llama.cpp/LM Studio) separadamente se o throughput continuar
baixo mesmo com o teto.

### Arquivos de teste (`tests/test_llm_executar.py`, novo)

Primeiro arquivo de teste de `llm/executar.py` (até aqui sem nenhuma
cobertura). Cinco testes, `urllib.request.urlopen` sempre mockado
(nenhum precisa de servidor local rodando): `num_predict` aparece no
payload do Ollama quando `max_tokens` está configurado, e some quando
está desligado; mesma checagem para `max_tokens` no payload do backend
OpenAI-compatible; e o default `700` é usado quando a chave nem existe
no config.

---

## O que **não** mudou na Atualização 15

- O valor default (`700`) é uma estimativa conservadora pra ~7
  tokens/s — hardware mais rápido pode (e deve) usar um valor maior;
  isso é ajuste de config, não mudança de código.
- Nenhuma personalidade (Analista/Executor/Sugestor/Engenheiro/Agente)
  ganhou um teto diferente entre si — é um valor único em
  `config["llm"]`, mesmo padrão de `timeout_seconds`/`temperature` hoje
  (roteamento por personalidade continua fora de escopo, mesma decisão
  já registrada nas Atualizações 10-14).
- O cache (`llm/cache.py`) não muda — a chave de cache já inclui
  modelo+temperatura+prompts; `max_tokens` não entra na chave porque
  não é uma escolha de conteúdo da resposta, é um limite de segurança.

---

## O que as Atualizações 16-17 entregam

Primeiras duas peças do `Plano_Hardening_Eyle.md` — derivadas de uma
auditoria externa que revisou o zip das Atualizações 10-14. Diferente
de todas as anteriores, essas duas corrigem **bugs em código escrito
nas próprias Atualizações 10 e 11**, não em código mais antigo — vale
ler com atenção porque muda garantias que já tínhamos declarado
fechadas.

### Atualização 16 — circuit breaker conta `{"ok": false}`, não só `{"erro": ...}`

**Bug**: `AgentState.registrar_resultado_tool` (Atualização 11) só
incrementava `erros_consecutivos` quando o resultado tinha chave
`"erro"` — mas `apply_patch`/`run_tests`/`test_patch_dry_run` reportam
falha de negócio como `{"ok": false, ...}`, um formato diferente
(`"erro"` é reservado pra falha de *execução* da tool, ex: argumento
faltando). Uma escrita que falhasse repetidamente (ex: patch não
aplica, `ast.parse` inválido, rollback) nunca acionava o breaker.

**Correção**: `registrar_resultado_tool` agora conta como erro tanto
`"erro" in resultado` quanto `resultado.get("ok") is False`.

- `engine/agent_state.py`: `registrar_resultado_tool` atualizado.
- Teste novo: 3 tentativas de `apply_patch` (cada uma com seu ciclo
  `needs_user`/`retomar`, já que toda tool WRITE exige confirmação
  própria) devolvendo `{"ok": False}` sem chave `"erro"` → circuit
  breaker dispara na 3ª, `needs_user`.

### Atualização 17 — verificador exige `executado=True E ok=True`

**Bug**: `AgentState.registrar_testes` (Atualização 10) aceitava
`ok=true` mesmo com `executado=false` (testes desligados ou não
configurados no projeto) — isso esvaziava a garantia inteira da
Atualização 10: "final" podia ser aceito depois de uma escrita real sem
**nenhuma** verificação de fato ter rodado.

**Correção**: `registrar_testes` só marca `testes_ok_apos_escrita=True`
quando `executado is True and ok is True`.

**Mudança de comportamento visível, intencional**: um projeto sem
testes configurados, depois de uma escrita, não fecha mais sozinho em
`{"final": ...}` — precisa de `needs_user` explícito da LLM ou esgota
`max_steps`. Isso é exatamente o que "verificador objetivo" deveria
significar; antes desta correção, não significava isso de verdade.

- `engine/agent_state.py`: `registrar_testes` atualizado.
- Teste novo: `run_tests` com `{"executado": False, "ok": True}` depois
  de uma escrita → `final` recusado igual a não ter rodado `run_tests`
  nenhuma; só fecha quando a LLM reconhece isso via `needs_user`
  explícito.

### O que **não** mudou nas Atualizações 16-17

- Nenhuma tool nova, nenhuma mudança em `agent_tools.py`/`codar.py` —
  as duas atualizações só mudam como `AgentState` interpreta resultados
  que as tools já devolviam.
- O restante do `Plano_Hardening_Eyle.md` (21 em diante — contrato
  padronizado de tools, confirmação vinculada à tarefa, e o resto)
  continua pendente — ver esse arquivo pra ordem e escopo de cada uma.

---

## O que a Atualização 18 entrega

**Problema**: `engine/codar.py` já rejeitava `../` e symlink externo
antes de localizar/testar/aplicar um patch, mas
`engine/dicas.py:ler_codigo_real` ainda montava o caminho com
`os.path.join(caminho_projeto, caminho_relativo)` e abria diretamente.
Essa função alimenta tanto o pipeline `dicas` quanto a tool `read_file`;
logo, uma decisão manipulada da LLM podia pedir um arquivo fora da raiz
e receber o conteúdo real dele.

**Correção**: a validação saiu do Codar e virou uma primitiva
compartilhada. Toda leitura de `ler_codigo_real` passa por ela antes de
`os.path.isfile`/`open`.

- `engine/seguranca.py` (novo): `_resolver_caminho_seguro` exige caminho
  relativo e confirma, após `realpath`, que o alvo continua dentro da
  raiz. Rejeita caminho absoluto POSIX/Windows, drive explícito,
  travessia para fora e symlink externo. Entradas inválidas falham
  fechadas com `None`.
- `engine/codar.py`: remove a cópia local do resolvedor e importa a
  implementação compartilhada. O comportamento dos caminhos legítimos
  permanece igual.
- `engine/dicas.py`: usa o resolvedor antes de qualquer leitura. Caminho
  inseguro produz `{"erro": ...}` explícito, sem conteúdo; arquivo
  legítimo que apenas sumiu continua sendo pulado como antes.
- `engine/agent_tools.py`: `read_file` propaga o erro de segurança em
  vez de tratá-lo como conteúdo ou como arquivo removido genérico.
- `engine/compiler.py`: o prompt de dicas trata a entrada de erro sem
  tentar acessar `conteudo` e informa que o código não foi lido por
  segurança.
- `tests/test_seguranca.py` (novo): quatro testes cobrem caminho normal,
  `../fora.txt` sem vazamento, caminho absoluto apontando até para dentro
  da raiz e symlink que aponta para fora.

### O que **não** mudou na Atualização 18

- Nenhuma regra de proposta, confirmação, patch, rollback ou execução de
  testes mudou; isso continua nas atualizações próprias do plano.
- Arquivo normal dentro da raiz continua sendo lido com o mesmo formato
  `{"conteudo", "truncado"}`.
- `config.json` continua na versão `1.9`: não há flag nova nem mudança de
  configuração nesta atualização.

---

## O que a Atualização 19 entrega

**Problema**: `engine/codar.py:aplicar_patch` mantinha o conteúdo
original em `conteudo_atual`, mas `_reverter()` só fazia algo quando
existia `backup_path`. Com `codar.fazer_backup=false`, uma falha de
`ast.parse` ou da suíte deixava o arquivo alterado enquanto a resposta
afirmava que ele tinha sido revertido. A escrita real também usava
`open(..., "w")`, expondo o destino a truncamento se o processo fosse
interrompido durante a gravação.

**Correção**: rollback e backup foram separados. O conteúdo original em
memória é a fonte da restauração; o `.bak` é apenas histórico opcional.
Toda substituição do arquivo real — patch e rollback — passa por um
temporário criado no mesmo diretório e por `os.replace()`.

- `engine/codar.py`: nova `_escrever_arquivo_atomico(caminho, conteudo)`
  cria o temporário no mesmo filesystem, copia o modo de permissão,
  grava, faz `flush`/`fsync` e troca atomicamente. O `finally` remove
  qualquer temporário remanescente.
- `engine/codar.py:aplicar_patch`: a escrita inicial retorna erro claro
  se a troca atômica falhar; `_reverter()` sempre usa `conteudo_atual`,
  também atomicamente, e não lê mais o backup. Se até o rollback falhar,
  a mensagem informa isso em vez de mentir que restaurou.
- `config.json`: comentário de `codar` atualizado —
  `fazer_backup=true` guarda histórico, não habilita a segurança.
- `tests/test_codar.py` (novo): rollback após falha de `ast.parse` com
  backup desligado preserva os bytes originais; escrita usa
  `os.replace`, mantém permissões e limpa o temporário; falha antes do
  replace não trunca o destino.

### O que **não** mudou na Atualização 19

- A confirmação explícita antes de qualquer patch real continua
  obrigatória.
- O formato de retorno de `aplicar_patch` continua
  `{"ok", "detalhe", "backup_path"}`; a padronização das tools é a 21.
- Backups continuam sendo criados quando configurados e podem ser usados
  manualmente como histórico, mesmo sem participar do rollback automático.

---

## O que a Atualização 20 entrega

**Problema**: `_chamar_llm` devolvia `"[erro] ..."` no mesmo tipo
(`str`) de uma resposta real. Dependendo do pipeline, essa string podia
ser gravada em `conversa.json`, enviada ao Verify e receber confiança
`1.0` só porque não continha nenhuma citação. A Atualização 14 filtrava
o erro no próximo chat, mas não corrigia o contrato falso na origem.

**Correção**: falha de transporte/backend agora é estado, não conteúdo.

- `llm/executar.py`: nova exceção `ErroLLM`. `_chamar_llm` a levanta em
  HTTP recusado (preservando código e até 500 caracteres do corpo),
  conexão recusada/timeout e exceção inesperada. A exceção nunca entra no
  cache; uma entrada legada que ainda comece com `[erro]` também vira
  `ErroLLM`, não resposta.
- `engine/engine.py`: `_resultado_falha_llm` monta `status: "failed"`,
  `confianca: None` e detalhe para o chamador sem registrar mensagem de
  assistente, sem gravar `ultima_resposta.txt`, sem chamar Verify e sem
  registrar uma decisão como se houvesse resposta. Chat, consulta,
  dicas, visão geral, Agente/retomada e engenharia usam essa fronteira.
  O pipeline completo ganhou `_processar_engenharia_impl`, envolvido por
  `_processar_engenharia` para capturar falhas do Analista, Engenheiro ou
  Executor num único ponto.
- `engine/entender.py`: no ingest, `ErroLLM` conta como falha daquele
  arquivo e preserva o entendimento anterior, em vez de derrubar toda a
  indexação ou tentar parsear o erro como JSON.
- `verify/validar.py`: quando não existe citação verificável,
  `confianca` agora é `None`. `1.0` só existe quando havia ao menos uma
  citação e todas as verificadas foram confirmadas. O retry de engenharia
  trata `None` como ausência de evidência, não como comparação numérica.
- `tests/test_llm_executar.py`: cobre `ErroLLM` em HTTP, conexão e cache
  legado.
- `tests/test_engine.py`: cobre `failed` sem persistência, consulta sem
  Verify, fronteira de engenharia e `processar()` de ponta a ponta.
- `tests/test_validar.py` (novo): cobre confiança `None` sem citação e
  preservação de `1.0` com citação real confirmada.

### O que **não** mudou na Atualização 20

- Respostas reais continuam usando `str` e o cache normal.
- O filtro da Atualização 14 continua removendo `[erro]` de conversas
  antigas; compatibilidade histórica não foi apagada.
- `confianca=None` não afirma que a resposta está certa nem errada — só
  diz que o Verify de citações não tinha evidência para medir.
- O contrato das tools era heterogêneo até a Atualização 21.

---

## O que as Atualizações 21-22 entregam

### Atualização 21 — contrato padronizado de resultado de tools

**Problema:** cada wrapper de `engine/agent_tools.py` usava chaves
diferentes (`erro`, `executado`, `resultados`, `conteudo`, `detalhe`). O
loop precisava inferir o significado do resultado e marcava
`houve_escrita=true` só porque uma tool `WRITE` tinha sido confirmada e
chamada — mesmo quando `apply_patch` falhava, revertia tudo e não deixava
nenhuma alteração real.

**Correção:** todas as sete tools devolvem exatamente o envelope
`status`, `ok`, `executed`, `changed`, `error_code`, `detail`.

- `engine/agent_tools.py`: helpers únicos montam sucesso, falha e
  operação pulada; resultado específico vai em `detail`; tool
  desconhecida, argumento inválido e exceção inesperada obedecem ao
  mesmo contrato.
- `engine/agent_state.py`: resumo, circuit breaker e registro de testes
  leem o envelope novo (`executed`, não `executado`). Leitura dos
  formatos antigos permanece só como compatibilidade defensiva.
- `engine/agent.py`: `registrar_escrita()` só roda quando a tool tem
  permissão `WRITE` **e** o resultado informa `changed=true`, tanto no
  fluxo direto quanto depois de retomar uma confirmação.
- `engine/codar.py`: se a validação pós-escrita falhar e até o rollback
  falhar, o resultado interno informa `changed=true`; rollback concluído
  informa `changed=false` no envelope da tool.
- `tests/test_agent_tools.py` (novo) e `tests/test_agent.py`: cobrem o
  envelope, teste pulado, patch aplicado, falha com rollback e a garantia
  de que uma WRITE com `changed=false` não marca escrita.

### Atualização 22 — confirmação vinculada à pendência e ao projeto

**Problema:** `context/proposta_pendente.json` e
`context/agent_pendente.json` não tinham identidade, prazo nem vínculo
com o projeto. Se os dois existissem, um `sim` confirmava a proposta do
Codar primeiro sem avisar; uma confirmação antiga também podia alcançar
uma tarefa criada para outro projeto.

**Correção:** toda pendência criada agora recebe:

- `id` hexadecimal curto (ex.: `7F3A`);
- `tipo_pendencia`, `criado_em` e `expira_em` em UTC;
- `projeto_hash`, calculado a partir de nome + caminho real do projeto;
- TTL configurável em `config.json -> confirmacoes.expiracao_segundos`
  (default 3600 segundos).

Com uma única pendência, `sim`/`não` continuam funcionando. Com proposta
e Agente pendentes ao mesmo tempo, a resposta precisa identificar o
alvo (`confirmar 7F3A` ou `cancelar 7F3A`). ID inexistente, prazo vencido,
metadados de versão antiga ou hash diferente são rejeitados antes de
aplicar/retomar qualquer escrita, com mensagem clara; pendência inválida
é descartada para não bloquear o fluxo para sempre.

`tests/test_engine.py` cobre as duas pendências simultâneas, `sim` sem
ID, ID errado, seleção correta, expiração, troca de projeto e persistência
dos metadados. A suíte completa agora tem **50 testes verdes**.

### O que **não** mudou nas Atualizações 21-22

- `run_tests` ainda está classificada como permissão `READ` e usa
  `shell=True`; a correção isolada continua sendo a Atualização 39.
- Os dois arquivos de pendência continuam efêmeros em `context/`; a
  mudança é o contrato seguro de identidade/seleção, não uma fila
  persistente (isso pertence às Atualizações 25-26).
- Uma confirmação nunca aplica duas pendências: cada mensagem resolve no
  máximo um ID.

---

## O que as Atualizações 23-24 entregam

### Atualização 23 — decisão do Analista filtra o contexto real

**Problema:** o Analista devolvia `ler` e `ignorar`, mas
`ciclo_analista` retornava o `atual` bruto da última busca. Um trecho
marcado para ignorar ainda aparecia no prompt do Executor; numa segunda
rodada, os bons trechos da primeira também eram substituídos pelo novo
resultado do retrieval.

**Correção:** cada candidato recebe no prompt um ID estável
`arquivo:linhas`. O ciclo aceita esse ID, arquivo, símbolo ou seletor
estruturado, aplica `ignorar` com prioridade e acumula/deduplica os
trechos aprovados em todas as rodadas, respeitando o `token_budget`. No fim, recompõe
`trechos`, `tokens_usados`, `arquivos_relevantes` e
`historico_relacionado`, salva o mesmo conjunto em `context/atual.json`
e só então o entrega ao Executor/Verify.

- `engine/engine.py`: novos helpers de normalização, correspondência,
  filtro, deduplicação e reconstrução do contexto; `ciclo_analista`
  acumula evidências aprovadas.
- `engine/compiler.py`: candidatos do Analista exibem ID
  `[arquivo:linhas]` e o JSON solicitado referencia esses IDs.
- `tests/test_engine.py`: cobre duas rodadas, descarte dos ignorados,
  preservação da primeira rodada, persistência do contexto filtrado e
  seletor estruturado.

### Atualização 24 — símbolos Python via AST

**Problema:** o regex tratava todos os métodos `run` como um símbolo
global, não reconhecia `async def`, descartava o conteúdo anterior à
primeira definição e `dict(simbolos)` no Codar escolhia silenciosamente
uma duplicata.

**Correção:** Python passa por `ast`. Classes e métodos recebem nomes
qualificados (`ClasseA.run`, `ClasseB.run`), funções assíncronas entram
no índice, decorators ficam anexados ao símbolo e imports/docstring/
constantes anteriores à primeira definição ganham chunk próprio de
preâmbulo. O Codar usa os limites reais do nó AST e rejeita nomes
ambíguos, sem sobrescrever duplicatas.

- `ingest.py`: nova `extrair_definicoes_python`; `extrair_simbolos` e
  `dividir_em_chunks` usam AST para `.py`.
- `engine/codar.py`: `localizar_simbolo` usa posição inicial/final do
  AST para Python; JS/TS mantém o reconhecedor anterior, conforme o
  escopo.
- `tests/test_ingest.py` (novo): cobre métodos homônimos qualificados,
  `async def`, preâmbulo, decorator e localização independente de cada
  método.

A suíte completa agora tem **57 testes verdes**.

### O que **não** mudou nas Atualizações 23-24

- BM25 continua sendo o mecanismo de busca; a 23 corrige o que acontece
  depois da busca, não troca o retrieval.
- JS/TS continua com regex. Parser real para essas linguagens permanece
  uma atualização futura separada.
- Naquele corte, `run_tests` ainda era `READ`/`shell=True`; a 28 removeu o
  shell e a 39 criou `EXEC` depois.

---

## O que as Atualizações 25-27 entregam

### Atualização 25 — snapshot de histórico por job

**Problema:** `POST /enviar` gravava a mensagem na conversa e colocava
só o texto numa `deque`. Quando o Worker finalmente processava A, o
pipeline `chat` relia `conversa.json`; se B tivesse sido enviada nesse
intervalo, A recebia B no próprio histórico — informação do futuro.

**Correção:** `registrar_mensagem_com_snapshot` grava a mensagem e
captura as últimas seis entradas válidas sob o mesmo lock. O snapshot é
persistido no payload do job e percorre `routes.py` → `worker.py` →
`engine.processar(..., historico_snapshot=...)`. `_processar_chat` usa
essa cópia em vez de reler a conversa atual. Chamadas da CLI, que não
recebem snapshot, continuam usando o comportamento anterior.

### Atualização 26 — fila SQLite e falha observável

**Problema:** a `deque` desaparecia em qualquer reinício e só existia
no processo que a importou. Uma exceção do Worker era impressa e perdida,
sem ID, estado ou resultado consultável.

**Correção:** `engine/queue.py` agora mantém `context/fila.sqlite3` com
reserva FIFO transacional e estados `pending`, `processing`, `completed`
e `failed`. Cada job guarda timestamps, tentativas, payload, resultado e
erro. Ao iniciar, o único Worker consumidor devolve jobs deixados em
`processing` para `pending`; uma exceção fica marcada como `failed`.

- `web/routes.py`: envio e remoção devolvem `job_id`; `/status` inclui
  contagens persistentes e a última falha.
- `engine/worker.py`: `processar_proximo` persiste conclusão/erro e torna
  o ciclo testável sem entrar num loop infinito.
- `tests/test_queue_worker.py` (novo): cobre FIFO, persistência, resultado,
  recuperação após interrupção, falha registrada e isolamento do snapshot.

A suíte completa chegou aqui a **62 testes verdes**.

### O que **não** mudou nas Atualizações 25-26

- Continua existindo um único Worker consumidor por fila. Coordenação de
  múltiplos Workers não entrou neste escopo.
- O painel ainda usa `eventos_na_fila` como aproximação visual; refletir
  o estado exato de cada job na interface continua sendo a Atualização 37.
- Autenticação, rate limit e ocultação de caminho absoluto entram na 27,
  documentada logo abaixo.

### Atualização 27 — API web autenticada e limitada

**Problema:** qualquer cliente que alcançasse o Flask podia ler a conversa,
enviar/remover mensagens e consultar o status. Não havia limite de
requisições, e o objeto bruto de `memory/projeto.json` revelava
`caminho_origem` absoluto.

**Correção:**

- `web/routes.py`: todas as rotas além do shell visual/arquivos estáticos
  exigem `Authorization: Bearer TOKEN` (ou `X-API-Token`). A comparação usa
  `secrets.compare_digest`; rota futura nasce protegida por padrão.
- O token vem de `EYLE_API_TOKEN`, `config.json -> web.api_token` ou é
  gerado com `secrets.token_urlsafe(32)` e persistido em
  `context/web_api_token.txt` com modo `0600`. Segredos configurados têm
  mínimo de 32 caracteres e nunca entram no HTML nem em resposta da API.
- Rate limit usa janela por IP, com padrão de 180 requisições/minuto e
  teto separado de 10 autenticações inválidas/minuto. Bloqueio responde
  `429`, `error_code=RATE_LIMITED` e `Retry-After`.
- Respostas da API recebem `Cache-Control: no-store`; todas as rotas usam
  `nosniff`, `X-Frame-Options: DENY` e política de referrer restrita.
- `/status` usa allowlist de campos públicos do projeto, omitindo
  `caminho_origem`/`source_hash`, e redige a raiz do projeto/Eyle em erros
  persistidos da fila sem perder o restante do diagnóstico.
- `web/static/app.js`: pede o token ao abrir o painel, envia Bearer em cada
  `fetch` e guarda o valor só na `sessionStorage` da aba.
- `main.py serve`: mostra o token no terminal; host externo recebe aviso
  explícito para restringir rede/firewall e colocar HTTPS na frente do
  Flask, que por si só não cifra tráfego.
- `config.json`: novo bloco `web` com origem do token e limites.
- `tests/test_web_security.py`: cobre shell público/API privada, ausência
  de mutação sem autenticação, redaction, `429`/`Retry-After`, teto de
  tentativas inválidas e persistência/permissão do segredo.

A suíte completa agora tem **68 testes verdes**.

---

## O que as Atualizações 28-29 entregam

### Atualização 28 — sandbox completo de execução

**Problema:** a suíte configurada rodava diretamente no host, sem isolamento
de filesystem/rede e sem limites de CPU, memória, processos ou saída. Um
repositório analisado controla o próprio código de teste; portanto, mesmo um
comando confiável como `pytest` pode executar conteúdo hostil.

**Correção:**

- Novo `engine/sandbox.py`: recebe argv, valida uma allowlist guardada no
  `config.json` da Eyle (fora do repositório) e nunca chama shell no host.
- Linux usa Bubblewrap: namespaces separados, rede desligada por padrão,
  runtime read-only, projeto gravável, `/tmp` efêmero e sessão/processos
  presos ao executor.
- Docker é alternativa explícita para outras plataformas: imagem definida
  pelo usuário, rootfs read-only, `--cap-drop ALL`, `no-new-privileges`, rede
  `none`, limites e volume exclusivo do projeto.
- Backend `processo` só existe para projeto confiável e exige
  `bloquear_rede=false`; nunca finge fornecer isolamento de rede.
- Por padrão, a suíte recebe uma cópia temporária gravável do projeto. Uma
  pré-varredura limita itens/bytes e recusa arquivos especiais; alterações
  feitas pelos testes desaparecem com o sandbox e não atingem a fonte real.
- `prlimit`/Docker limitam CPU, memória, processos, descritores, arquivo/saída
  e o executor aplica timeout de parede com encerramento do grupo.
- Sem backend compatível, comando fora da allowlist ou configuração inválida,
  a execução é recusada. No Codar, recusa também reprova o teste e reverte o
  patch — ela não vira “teste pulado”.
- `config.json -> codar.testes.sandbox` documenta backend, rede, allowlist,
  limites e overrides confiáveis por caminho real do projeto.

Como a 28 precisava iniciar o sandbox sem interpolar uma string, ela também
removeu o `shell=True` de `rodar_testes_projeto`: strings configuradas viram
argv e o processo hospedeiro nasce com `shell=False`. A Atualização 39 ainda
tem uma tarefa própria: classificar `run_tests` como permissão `EXEC` no
Agente e decidir seu gate, em vez de deixá-la como `READ`.

### Atualização 29 — ingestão segura

**Problema:** `os.walk` ignorava pastas fixas, mas não respeitava
`.gitignore`, não tinha denylist de credenciais e podia abrir um symlink de
arquivo apontado para fora da raiz. O estágio posterior de entendimento via
LLM também remontava caminhos diretamente.

**Correção:**

- `ingest.py` usa walker próprio, carrega `.gitignore` por diretório e aplica
  ordem de regras, negação, padrões ancorados, `*`, `?`, classes e `**`.
- Arquivos como `.env`, `credentials.json`, chaves privadas e formatos de
  keystore são recusados por nome/extensão; marcadores de alta confiança
  (private key, AWS/GitHub/Slack/OpenAI token) também são filtrados no
  conteúdo antes de gerar qualquer chunk.
- Todo caminho passa por `_resolver_caminho_seguro`; symlink externo é
  rejeitado e diretório symlink não é seguido para evitar ciclo/duplicação.
- Hash e conteúdo usam o caminho real já validado. `montar_entendimento` e
  `engine/entender.py` repetem a resolução segura antes da leitura, fechando
  troca de estágio.
- `memory/projeto.json -> arquivos_ignorados` registra somente contagens por
  motivo, sem guardar caminho/conteúdo sensível.

Foram adicionados 7 testes (6 de sandbox e 1 cenário de ingestão que cobre
`.gitignore` raiz/aninhado, negação, segredo por nome/conteúdo e symlink
externo). A suíte agora contém **75 testes**. Nesta aplicação, os 69 testes
do núcleo passaram; os 6 testes web existentes não puderam ser repetidos
porque Flask/pytest não estão instalados no ambiente. `python -m compileall`
passou sem erro.

### O que **não** mudou nas Atualizações 28-29

- `agent.enabled` continua `false`.
- No estado 28-29, `run_tests` ainda estava classificada como `READ`; a 39
  posterior concluiu a migração para `EXEC`.
- A allowlist e os limites vêm da configuração confiável da Eyle; nenhum
  arquivo dentro do projeto pode liberar o próprio comando.
- A cópia temporária do projeto fica gravável dentro do sandbox para permitir
  cache/build/teste; rollback do Codar continua sendo a garantia do arquivo
  real após falha.

---


## O que a Atualização 6 entrega

Fecha a lacuna apontada em `Atualizacao_Agente.md`: até aqui existia um
**Agente mínimo** completo (`engine/agent.py`, `engine/agent_state.py`,
`engine/agent_tools.py`) que nunca tinha sido documentado neste rastreador
oficial — e, mais importante, **nunca era chamado a partir de uma mensagem
real de usuário** (nem pelo roteador, nem por `main.py`, nem pela web). O
plano descreve isso como "buraco 2"; esta atualização fecha exatamente ele
(Fase 2 do plano do Agente — Fases 1 e 3/4 tratadas nas seções
"Módulos do Agente já existentes" e "Próximas atualizações" abaixo).

### Módulos do Agente já existentes (não rastreados até agora)

Antes desta atualização o repositório já continha, sem menção neste
arquivo:

- **`engine/agent_tools.py`**: registro `TOOLS`/`executar_tool` com 7
  ferramentas, cada uma um wrapper fino sobre uma função que já existia em
  outro módulo (`read_metadata` → `entendimento.json`; `search_code` →
  `retrieval/buscar.py:buscar`; `find_symbol` → `engine/codar.py:
  localizar_simbolo`; `read_file` → `engine/dicas.py:ler_codigo_real`;
  `test_patch_dry_run` → `engine/codar.py:testar_patch_em_copia`;
  `run_tests` → `engine/codar.py:rodar_testes_projeto`; `apply_patch` →
  `engine/codar.py:aplicar_patch`, única com `permission="WRITE"`).
- **`engine/agent_state.py`**: `AgentState`, guarda observações já
  resumidas (nunca o resultado cru da tool) e a guarda de chamada repetida
  (mesma `(tool, arguments)` não roda duas vezes na mesma tarefa).
- **`engine/agent.py`**: `decidir_passo` (chama a LLM, tenta parsear a
  decisão, com retry curto em formato inválido) e `executar_agente`
  (loop principal: até `config["agent"]["max_steps"]` passos, para em
  `needs_user` antes de qualquer tool `WRITE` se
  `config["agent"]["require_confirmation_for_write"]`, grava rastro de
  depuração em `context/agent_trace.jsonl`). Devolve
  `(status, texto)` com `status` em `"success" | "needs_user" | "failed" |
  "max_steps"`.

Nada nesses três arquivos foi alterado nesta atualização — só passaram a
ser **chamados**, o que é justamente o que faltava.

### Arquivos alterados nesta atualização (Fase 2)

#### `engine/roteador.py`
- Novo vocabulário `PALAVRAS_MULTIPASSO` (`"e depois"`, `"e então"`,
  `"até passar"`, `"e roda os testes"`, etc.) e `_pede_tarefa_multipasso(...)`.
- `classificar_pergunta(...)` ganha um 6º tipo, `"agente"`, e um novo
  parâmetro `agent_habilitado=False`. Quando a mensagem bate em
  `PALAVRAS_ENGENHARIA` **e** também em `PALAVRAS_MULTIPASSO` **e**
  `agent_habilitado` é `True`, o tipo vira `"agente"` em vez de
  `"engenharia"`. Com a flag desligada (default), o comportamento é
  **idêntico** ao de antes — nenhuma mensagem muda de classificação só
  por causa desta atualização.

#### `config.json`
- Nova chave `agent.enabled` (default `false`) — feature flag que
  controla se `classificar_pergunta` pode devolver `"agente"`. Fica
  desligada até a Fase 3 (persistência de `needs_user` entre turnos) e a
  Fase 4 (testes automatizados) fecharem, para não expor o Agente a
  usuário real via chat antes de estar completo.
- Versão do config sobe de `"1.6"` para `"1.7"`.

#### `engine/engine.py`
- Import de `executar_agente` (`engine/agent.py`).
- `processar(pergunta, registrar_pergunta=True, forcar_tipo=None)`: novo
  parâmetro `forcar_tipo` — quando informado, pula
  `classificar_pergunta` (e a checagem de proposta pendente) e roda
  direto o pipeline daquele tipo. Usado por `main.py agente "..."` para
  garantir que a tarefa chega em `executar_agente()` mesmo com
  `agent.enabled=false` ou sem o vocabulário multi-passo. Sem
  `forcar_tipo` (uso normal, via worker/`main.py perguntar`), o
  comportamento é o de sempre: `agent_habilitado =
  config["agent"]["enabled"]` é passado para `classificar_pergunta`, e
  a nova ramificação `if tipo == "agente": return _processar_agente(...)`
  entra no mesmo lugar que `consulta`/`dicas`/`visao_geral`.
- Nova função `_processar_agente(pergunta, config, projeto, entendimento,
  motivo_roteador)`: chama `executar_agente(...)` e traduz
  `(status, texto)` para o mesmo contrato de dict que os outros
  `_processar_*` já devolvem (`resposta`, `roteador`, `iteracoes_analista`,
  `decisoes_analista`, `confianca`, `avisos`), mais um campo extra
  `agente_status`. Historicamente, `confianca` era `1.0` em `"success"` e
  `0.0` em `"failed"`/`"max_steps"`; desde a Atualização 30 ela fica
  `None` em todos os status, pois êxito do loop não prova grounding. O
  resultado operacional continua explícito em `agente_status`. Registra a
  mensagem do assistente e o histórico igual aos demais pipelines.

#### `main.py`
- Novo subcomando `python main.py agente "objetivo"`
  (`cmd_agente`, espelhando `cmd_perguntar`): chama
  `processar(objetivo, forcar_tipo="agente")` — ignora deliberadamente a
  flag `agent.enabled` e o vocabulário multi-passo do roteador, porque é
  uma chamada explícita do usuário/desenvolvedor, não roteamento
  automático de uma mensagem de chat qualquer (que continua respeitando a
  flag normalmente). Avisa no terminal se `agent.enabled` está `false` ou
  se não há projeto indexado, mas roda mesmo assim.

### O que **não** mudou nesta atualização

- `web/routes.py` e `web/static/app.js`: **nenhuma mudança** — o
  navegador já chamava `engine/queue.py` → `engine/worker.py:
  processar_evento` → `engine.engine.processar(...)` de forma genérica
  (sem hardcodar tipos de pipeline), então a nova ramificação `"agente"`
  já funciona pela web automaticamente assim que `agent.enabled=true`
  for ligado em `config.json` — nenhum código do painel precisou saber
  que o tipo existe.
- `engine/agent.py`, `engine/agent_state.py`, `engine/agent_tools.py`:
  zero alterações — só passaram a ser chamados (ver acima).
- `needs_user` do Agente por causa de tool WRITE **passou a persistir**
  entre turnos em `context/agent_pendente.json` -- isso e' a Atualização 8
  (Fase 3), ver secao propria logo abaixo.

---

## O que a Atualização 5 entrega

Até a Atualização 4, a Eyle só **explicava** (Executor) ou **sugeria**
(Sugestor) — nunca escrevia nada no projeto do usuário, mesmo quando o
pedido era claramente uma mudança de código (pipeline `engenharia`).

Agora, dentro do pipeline `engenharia`, quando o ciclo
Retrieval→Analista converge num **único alvo claro** (um arquivo + um
símbolo/função/classe — nada ambíguo), a Eyle tenta ir além do texto:
gera uma **proposta de patch de verdade**, com este ciclo:

```
Proposta                    LLM Engenheiro le o codigo REAL do simbolo
                             (lido fresco do disco, nao da memoria
                             indexada) e devolve o codigo novo completo

  -> Impacto                 depende_de INVERTIDO em entendimento.json:
                             quem declara depender do arquivo alvo

  -> Patch                   recorte exato por linha_inicio/linha_fim,
                             localizado no arquivo REAL agora (nao no
                             chunk indexado, que pode estar desatualizado)

  -> Teste                   aplicado numa COPIA TEMPORARIA -- o arquivo
                             real nunca e tocado nesta etapa. Para .py:
                             ast.parse() confere sintaxe valida.

  -> [pausa aqui]             a proposta e a RESPOSTA da Eyle. Nada foi
                             escrito ainda. Fica pendente em
                             context/proposta_pendente.json

  -> Aplicar                  SO na proxima mensagem do usuario, e SO se
                             for uma confirmacao explicita ("sim"/
                             "aplica"). Reconfirma que o arquivo nao
                             mudou desde a proposta, faz backup, escreve,
                             roda ast.parse() de novo no arquivo real
                             (rollback automatico se falhar).
```

Se o alvo **não** for único e claro (0 ou 2+ arquivos, símbolos
diferentes no meio dos trechos, chunk sem símbolo reconhecido) — ou
qualquer etapa da geração da proposta falhar (símbolo sumiu do arquivo
desde o último ingest, LLM não devolveu JSON válido) — cai
silenciosamente no comportamento de sempre: o Executor só explica em
texto, sem propor patch. É um "opt-in" automático, nunca um requisito
pra responder.

### Arquivos criados

#### `engine/codar.py` (novo)
Toda a mecânica de ler/testar/aplicar patch — nada aqui decide **se**
deve aplicar algo, isso é sempre de quem chama (`engine/engine.py`).

- `localizar_simbolo(caminho_projeto, caminho_relativo, simbolo)`
  Lê o arquivo **real, fresco do disco** (nunca a memória indexada, que
  pode estar desatualizada) e localiza a linha exata onde o símbolo
  começa e termina, reaproveitando `extrair_simbolos` de `ingest.py`. O
  fim de um símbolo é a linha anterior ao próximo símbolo do arquivo (ou
  o fim do arquivo), com linhas em branco no limite removidas do
  recorte. Devolve `None` se o arquivo sumiu ou o símbolo não existe
  mais ali (pode ter sido renomeado/removido desde o último ingest) —
  nunca inventa uma posição.
- `calcular_impacto(arquivo_alvo, entendimento)`
  "`depende_de` invertido": varre `entendimento.json['arquivos']` e
  devolve quem declara depender do arquivo alvo — sem precisar de
  índice persistido, o Modelo Interno inteiro já cabe em memória.
- `testar_patch_em_copia(caminho_projeto, caminho_relativo, linha_inicio, linha_fim, codigo_novo)`
  **Nunca** escreve no arquivo real. Copia o conteúdo pra um arquivo
  temporário, aplica a substituição de linhas, e roda uma verificação
  mínima: `.py` → `ast.parse()` no resultado inteiro; outras extensões
  → só confirma que o recorte/escrita funcionaram (verificação de
  sintaxe real pra `.js`/`.html`/`.css` é a Atualização 6).
- `aplicar_patch(caminho_projeto, caminho_relativo, linha_inicio, linha_fim, codigo_original_esperado, codigo_novo, backups_dir=None)`
  Só deve ser chamado depois da confirmação explícita do usuário.
  **Re-lê o arquivo na hora**: se as linhas não baterem mais com
  `codigo_original_esperado`, aborta sem escrever nada (o arquivo mudou
  desde a proposta — aplicar por cima seria destrutivo às cegas). Faz
  backup do arquivo inteiro antes de escrever (se `backups_dir`
  passado), depois roda `ast.parse()` de novo no **arquivo real final**
  como segunda checagem — se falhar, restaura o backup automaticamente
  (rollback) em vez de deixar o arquivo real quebrado.

### Arquivos alterados

#### `engine/roteador.py`
- Novo vocabulário `PALAVRAS_CONFIRMACAO_PATCH` / `PALAVRAS_CANCELAMENTO_PATCH`.
- `detectar_resposta_proposta(pergunta)`: só chamado quando já existe
  uma proposta pendente — decide se a mensagem atual é confirmação
  (`"sim"`/`"aplica"`) ou cancelamento (`"não"`/`"cancela"`) dela.
  Cancelamento é checado primeiro (`"não aplica"`/`"ainda não"` contêm a
  palavra "aplica" mas são negações). Devolve `None` se a mensagem não
  parecer resposta à proposta — nesse caso ela continua pendente e o
  fluxo normal roda por cima.
- **Não é** um tipo novo em `classificar_pergunta` — é um mecanismo
  separado, checado por `engine/engine.py:processar()` **antes** do
  roteador de tipo rodar.

#### `engine/compiler.py`
- `montar_prompt_engenheiro(pergunta, arquivo, simbolo, alvo, entendimento=None, decisoes=None, impacto=None)`:
  monta o prompt do Engenheiro — objetivo, Modelo Interno do arquivo
  alvo, quem depende dele (impacto), decisões conhecidas, e o código
  real atual do símbolo (já localizado por linha). Pede um **recorte
  completo** de código novo, nunca um diff (mais fácil de aplicar por
  substituição direta de linhas, e mais fácil de um modelo pequeno
  gerar corretamente).
- `montar_texto_proposta(proposta)`: formata Proposta + Impacto + Patch
  (código atual vs proposto) + resultado do Teste num texto legível,
  terminando com o pedido de confirmação explícita (se o teste passou)
  ou explicando por que não pode ser aplicada como está (se falhou).

#### `llm/executar.py`
- Novo prompt de sistema `PROMPT_ENGENHEIRO` (quinta personalidade, ao
  lado de Analista/Executor/Entendedor/Sugestor): só escreve o código
  novo completo de UM símbolo já localizado por linha, nunca aplica
  nada sozinho.
- Nova função `executar_engenheiro(prompt_usuario, config)`.

#### `engine/engine.py`
- Imports de `engine/codar.py` (`localizar_simbolo`, `calcular_impacto`,
  `testar_patch_em_copia`, `aplicar_patch`), `engine/compiler.py`
  (`montar_prompt_engenheiro`, `montar_texto_proposta`),
  `engine/roteador.py` (`detectar_resposta_proposta`) e `llm/executar.py`
  (`executar_engenheiro`).
- Novo estado efêmero em `context/proposta_pendente.json` (fica em
  `context/`, não `memory/` — é o estado de UMA interação em andamento,
  igual `atual.json`, não conhecimento persistente): `carregar_proposta_pendente()`,
  `salvar_proposta_pendente(proposta)`, `limpar_proposta_pendente()`.
- `processar(...)`: **antes** de rotear a mensagem normalmente, confere
  se já existe uma proposta pendente e se a mensagem atual é uma
  resposta a ela (`detectar_resposta_proposta`). Se for confirmação ou
  cancelamento, isso tem prioridade sobre o roteador de tipo — evita que
  um "sim" solto vire uma pergunta de chat aleatória.
- `_identificar_alvo_unico(atual)`: a partir dos trechos finais do
  `ciclo_analista`, decide se há um único `(arquivo, símbolo)` alvo
  claro (todos os trechos do mesmo arquivo E do mesmo símbolo — funções
  grandes viram vários sub-chunks mas com o mesmo nome, então ainda
  contam como um alvo só). Qualquer ambiguidade devolve `None`.
- `_parse_resposta_engenheiro(texto)`: extrai e valida o JSON do
  Engenheiro; exige `codigo_novo` não vazio.
- `_tentar_gerar_proposta(pergunta, config, projeto, atual, entendimento, decisoes)`:
  o ciclo completo Proposta→Impacto→Patch→Teste. Devolve o resultado
  pronto (mesmo que o teste tenha falhado — ainda é informativo) ou
  `None` se não deu pra tentar (config desativado, alvo ambíguo, símbolo
  não localizado, LLM sem JSON válido) — nesse caso quem chamou cai no
  fallback de sempre.
- `_aplicar_proposta_pendente(proposta, config)`: chamado quando o
  usuário confirma. Chama `aplicar_patch`, registra em `historico.json`
  se aplicado com sucesso, sempre limpa a proposta pendente ao final
  (aplicada ou não).
- `_cancelar_proposta_pendente(proposta)`: chamado quando o usuário
  recusa. Só limpa a proposta pendente, não toca em nada.
- `_processar_engenharia(...)`: depois do `ciclo_analista` de sempre,
  chama `_tentar_gerar_proposta` — se devolver algo, essa proposta
  **vira** a resposta (não roda o Executor de texto livre por cima). Se
  devolver `None`, segue exatamente como antes da Atualização 5.

#### `config.json`
- Nova seção `codar`: `ativado` (default `true`), `fazer_backup`
  (default `true`, salva o arquivo original em `context/backups/`
  antes de qualquer escrita real).
- Versão do config sobe de `"1.3"` para `"1.4"`.

#### `README.md`
- Estrutura do projeto atualizada: `engine/codar.py`,
  `context/proposta_pendente.json`, `context/backups/`.

---

## O que **não** mudou nesta atualização

- `retrieval/buscar.py`, `verify/validar.py`, `engine/entender.py`,
  `engine/dicas.py`, `web/routes.py`, `web/static/*`, `web/templates/*`
  — nenhuma função existente foi alterada. Os pipelines `chat`,
  `consulta`, `dicas` e `visao_geral` continuam funcionando exatamente
  como antes.
- O painel web (`web/`) **não precisou de nenhuma mudança** — a
  confirmação/cancelamento da proposta acontece só trocando mensagens
  de chat normais ("sim"/"não"), o mesmo contrato de sempre
  (`POST /enviar`, `GET /conversa`).
- O pipeline `engenharia` continua rodando o ciclo
  Retrieval→Analista→(investigação)→Verify exatamente como antes — a
  Atualização 5 só entra DEPOIS que esse ciclo já convergiu, como uma
  tentativa adicional antes do Executor de texto livre.
- Nenhum patch é aplicado sem uma mensagem separada de confirmação
  explícita do usuário — a proposta nunca aplica nada na mesma resposta
  em que é gerada.
- Verificação de sintaxe real para arquivos web (`.js`/`.html`/`.css`)
  e `pytest`/`npm test` opt-in continuam **não implementados** — isso é
  a Atualização 7 (ver "Próximas atualizações" no final deste arquivo).

---

## O que a Atualização 8 (Fase 3 do plano do Agente) entrega

Fecha o "buraco 3" apontado em `Atualizacao_Agente.md`: até aqui, quando
`executar_agente` pausava em `needs_user` por causa de uma tool `WRITE`
(hoje só `apply_patch`), a resposta era devolvida mas a tarefa não tinha
como retomar — o usuário precisaria refazer o pedido do zero. Agora
persiste, no mesmo padrão já usado por `context/proposta_pendente.json`
(Atualização 5).

#### `engine/agent_state.py`
- `AgentState.to_dict()`/`AgentState.from_dict(dados, config=None)`:
  serializam/reconstroem só o que a classe já guardava (`observacoes` e
  `assinaturas_chamadas`, cada uma virando lista de 2 elementos em JSON e
  voltando a tupla em `from_dict`) — zero lógica nova, zero reescrita da
  classe, como o plano pedia.

#### `engine/agent.py`
- `executar_agente(..., retomar=None)`: novo parâmetro opcional. Quando
  `None` (tarefa nova), comportamento idêntico a antes. Quando presente
  (dict vindo de `context/agent_pendente.json`), reidrata o `AgentState`
  via `from_dict`, executa a `tool_pendente` (já confirmada pelo
  usuário — pula a checagem de WRITE/chamada-repetida só para essa
  chamada) e continua o loop normal a partir de `step_atual`.
- Retorno passou de `(status, texto)` para `(status, texto,
  estado_pendente)`. `estado_pendente` é `None` em todos os casos exceto
  quando uma tool `WRITE` acabou de pausar o loop — aí é o dict pronto
  para salvar em `context/agent_pendente.json` (`objetivo`, `step_atual`,
  `estado`, `tool_pendente`, `pergunta_ao_usuario`). Um `needs_user`
  explícito da LLM (pergunta livre, sem tool) continua devolvendo
  `estado_pendente=None` — não muda de comportamento.
- `_continuar_trace()`: nova função irmã de `_iniciar_trace()` — ao
  retomar, não trunca `context/agent_trace.jsonl` (é a mesma tarefa que
  pausou, o rastro anterior continua útil).

#### `engine/engine.py`
- Novo bloco `AGENT_PENDENTE_PATH`/`carregar_agent_pendente`/
  `salvar_agent_pendente`/`limpar_agent_pendente`, espelhando
  `PROPOSTA_PENDENTE_PATH` linha por linha.
- `processar()`: logo depois da checagem de `proposta_pendente`, confere
  `context/agent_pendente.json` da mesma forma — reaproveita
  `detectar_resposta_proposta` (nenhum vocabulário novo de
  confirmação/cancelamento) para decidir `_retomar_agente_pendente` ou
  `_cancelar_agente_pendente`.
- `_processar_agente`: agora desempacota a 3-tupla e salva
  `estado_pendente` quando o status é `needs_user` com tool pendente.
- Novas funções `_retomar_agente_pendente(agente_pendente, config)`
  (chama `executar_agente(..., retomar=agente_pendente)`, salva de novo
  se encadear outra tool `WRITE`, limpa se concluir) e
  `_cancelar_agente_pendente(agente_pendente)` (descarta a tarefa
  inteira — não há como retomar só a leitura sem a escrita pendente).

### O que **não** mudou nesta atualização

- `engine/agent_tools.py`: zero alterações — as tools continuam as
  mesmas 6, nenhuma lógica de negócio nova.
- Um `needs_user` explícito da LLM (sem tool pendente) continua sem
  persistir — só o caso de tool `WRITE` pendente ganha retomada, como o
  plano especificava.
- `web/routes.py`/`web/static/app.js`: nenhuma mudança — a
  confirmação/cancelamento continua sendo só uma mensagem de chat normal
  ("sim"/"não"), mesmo contrato de sempre.
- Nenhum teste automatizado foi escrito nesta atualização — isso
  continua sendo a Fase 4 (ver "Próximas atualizações" abaixo).

---

## O que a Atualização 9 (Fase 4 do plano do Agente) entrega

Fecha o plano do Agente inteiro: primeiro arquivo de teste automatizado
do projeto (`tests/test_agent.py`), cobrindo exatamente os 5 critérios
de pronto que as atualizações anteriores definiam mas nunca tinham
ganhado automação — sempre com a LLM e as tools mockadas via
`monkeypatch`, nenhum modelo local precisa estar rodando:

1. resposta malformada da LLM → `decidir_passo` tenta de novo e recupera
   (e desiste de forma limpa, sem exceção, se todas as tentativas
   falharem);
2. tarefa que nunca devolve `"final"` → `max_steps` interrompe exatamente
   no passo configurado, nem um a mais;
3. mesma `(tool, arguments)` pedida duas vezes seguidas → a guarda de
   repetição barra a segunda (a tool só roda de verdade uma vez);
4. tool `WRITE` (`apply_patch`) → pausa em `needs_user` com um
   `estado_pendente` que sobrevive a uma ida/volta real por
   `json.dumps`/`json.loads`; `executar_agente(..., retomar=...)`
   executa a tool só então e continua do passo certo (Fase 3, agora com
   cobertura automatizada);
5. `engine/agent_tools.py` bloqueado via `importlib.reload` +
   `builtins.__import__` patchado (regressão simulada de verdade, não um
   substituto escrito só pro teste) → `engine/agent.py` cai no stub
   (`TOOLS = {}`, `executar_tool` devolve `{"erro": ...}`) sem lançar
   `ImportError` pra cima; o módulo é restaurado ao normal no `finally`
   do próprio teste.

#### `tests/test_agent.py` (novo)
- 6 funções de teste (o critério 1 vira 2: recupera do retry e desiste
  de forma limpa quando esgota as tentativas). Helpers `_config()` e
  `_sequencia_llm()` no topo do arquivo — nenhuma lógica de produto
  nova, só fábricas de mock.

#### `pytest.ini` (novo)
- `testpaths = tests` — única opção, sem nada exótico.

#### `requirements-dev.txt` (novo) / `requirements.txt`
- `pytest` isolado num arquivo de dependência só de desenvolvimento —
  continua não sendo preciso instalar nada pra usar a Eyle normalmente
  (`ingest`/`perguntar`/`serve`), só pra rodar `tests/`.

### O que **não** mudou nesta atualização

- `engine/agent.py`, `engine/agent_state.py`, `engine/agent_tools.py`,
  `engine/engine.py`: **zero alterações de comportamento** — a Fase 4 só
  adiciona testes sobre o que já existia (Atualizações 1-5 e Fase 3).
- `config.json["agent"]["enabled"]` continua `false` por padrão — o
  plano do Agente definia isso como um passo deliberado, não automático
  só porque os testes fecharam; ligar em produção é decisão separada.

---

---

## Como testar (quando for a hora)

```bash
# ingest completo, com LLM gerando entendimento por arquivo (necessario
# pro calculo de impacto via depende_de)
python main.py ingest workspace/ --nome MeuProjeto

# pede uma mudanca especifica o suficiente pra apontar 1 arquivo + 1 funcao
python main.py perguntar "corrija o bug X na funcao processar_pagamento"
# -> se o Analista convergir num alvo unico, a resposta vem como
#    PROPOSTA DE MUDANCA (com Impacto e resultado do Teste), nao texto livre

# confirma a proposta pendente numa mensagem SEPARADA
python main.py perguntar "sim"
# -> so agora o arquivo real e escrito (com backup em context/backups/)

# ou cancela
python main.py perguntar "nao"

# proposta pendente fica em context/proposta_pendente.json ate ser
# confirmada, cancelada, ou substituida por uma proposta nova
cat context/proposta_pendente.json
```

---

---

## O que a Atualização 4 entrega

`engine/roteador.py:_contem_frase` fazia `frase in texto_norm` (substring
crua): frases curtas de `PALAVRAS_ENGENHARIA`/`PALAVRAS_CONSULTA` (ex:
`"cria"`, `"app"`) batiam dentro de qualquer palavra que as contivesse
como pedaço — `"isso tem a ver com criatividade"` era classificado como
`"engenharia"` só por causa de `"cria"` dentro de `"criatividade"`.
Corrigido para casar a frase inteira com fronteira de palavra (`\b...\b`
via `re.search`), sem mudar o comportamento para os casos que já
funcionavam certo (frases de uma palavra continuam batendo normalmente;
frases de várias palavras como `"resolva esse bug"` continuam inteiras).

---

## O que a Atualização 4 entrega

Até a Atualização 3, uma pergunta só tinha um caminho até o código:
`retrieval/buscar.py` (BM25 sobre `chunks.jsonl` — bate contra as
palavras literais do código). Isso funciona bem para "onde fica X", mas
não para "que sugestão você tem pra esse projeto" — a pergunta não
compartilha vocabulário com nenhum chunk específico.

Fluxo novo (**"dicas"**, um tipo novo no roteador):

```
pergunta -> entendimento.json["arquivos"] -> componentes candidatos
(via tipo/responsabilidade/funcoes_principais/pontos_criticos, expandido
por depende_de) -> lê o CÓDIGO REAL desses componentes (arquivo inteiro,
não chunk) -> LLM Sugestor analisa e sugere
```

### Arquivos criados

#### `engine/dicas.py` (novo)
- `selecionar_componentes_candidatos(pergunta, entendimento, max_candidatos=5, profundidade_dependencia=1)`
  Casa os tokens da pergunta contra `tipo`/`responsabilidade`/
  `funcoes_principais`/`pontos_criticos` de cada arquivo em
  `entendimento.json["arquivos"]` (não contra o código, contra o que o
  Modelo Interno já sabe sobre ele). `pontos_criticos` pesa em dobro no
  score. Depois expande a lista seguindo `depende_de` por
  `profundidade_dependencia` nível(is), para o Sugestor também ver do que
  os candidatos diretos dependem.
- `ler_codigo_real(caminhos, caminho_projeto, max_chars_por_arquivo=20000)`
  Lê o conteúdo real (arquivo inteiro) de cada candidato a partir de
  `memory/projeto.json["caminho_origem"]`. Arquivo removido do disco
  desde o último ingest é pulado, nunca inventado.
- `preparar_dicas(pergunta, entendimento, caminho_projeto, config=None)`
  Encadeia as duas funções acima usando `config.json["dicas"]`.

### Arquivos alterados

#### `engine/roteador.py`
- Novo vocabulário `PALAVRAS_DICAS` (dica/dicas/sugestão/sugestões/
  sugira/recomende/etc.).
- `classificar_pergunta(...)`: novo tipo `"dicas"`, prioridade entre
  `"engenharia"` e `"consulta"` — pede opinião/sugestão (não aplica
  nada), mas precisa ler código real, então fica acima de uma consulta
  simples.

#### `engine/compiler.py`
- Nova função `montar_prompt_dicas(pergunta, candidatos, codigos, projeto=None, entendimento=None)`:
  monta o prompt do Sugestor com o objetivo, o Modelo Interno de cada
  candidato (tipo/responsabilidade/depende_de/pontos_criticos, e o
  motivo dele ter sido escolhido) e o código real lido do disco.

#### `llm/executar.py`
- Novo prompt de sistema `PROMPT_SUGESTOR` (quarta personalidade,
  ao lado de Analista/Executor/Entendedor): só sugere a partir do código
  real mostrado, nunca aplica nada, nunca gera patch.
- Nova função `executar_sugestor(prompt_usuario, config)`.

#### `engine/engine.py`
- Import de `preparar_dicas` (engine/dicas.py), `montar_prompt_dicas`
  (engine/compiler.py) e `executar_sugestor` (llm/executar.py).
- Nova função `_processar_dicas(pergunta, config, projeto, entendimento, motivo_roteador)`:
  pipeline "dicas" — sem retrieval BM25, sem Analista, sem retry (é uma
  opinião fundamentada no código, não um fato verificável linha a linha
  como no Executor de engenharia). Se `entendimento.json["arquivos"]`
  ainda estiver vazio (projeto nunca rodou com a Atualização 3 ativa),
  avisa objetivamente em vez de tentar adivinhar.
- `processar(...)`: nova ramificação `if tipo == "dicas": return _processar_dicas(...)`.

#### `config.json`
- Nova seção `dicas`: `max_componentes_candidatos` (default `5`),
  `profundidade_dependencia` (default `1`), `max_chars_por_arquivo`
  (default `20000`).
- Versão do config sobe de `"1.2"` para `"1.3"`.

---

---

## O que a Atualização 3 entrega

`memory/entendimento.json` deixa de responder só "o que cada **pasta**
faz" (bloco `componentes`, heurístico, sem LLM) e passa a responder
também "o que cada **arquivo** faz", em detalhe, gerado pela LLM:

```json
{
  "arquivos": {
    "engine/engine.py": {
      "tipo": "orquestrador",
      "responsabilidade": "orquestrar fluxo principal",
      "entrada": ["mensagem", "estado"],
      "saida": ["resposta"],
      "depende_de": ["retrieval/buscar.py", "llm/executar.py", "verify/validar.py"],
      "funcoes_principais": ["processar"],
      "pontos_criticos": ["controle do pipeline", "alto acoplamento"],
      "hash": "a1b2c3d4e5f6a1b2"
    }
  }
}
```

O bloco `componentes` (resumo por pasta, heurístico, sem LLM) **continua
existindo do mesmo jeito que antes** — nada foi removido, só adicionado.

Regenera só o que mudou: cada entrada em `arquivos` guarda o `hash` do
arquivo (o mesmo hash sha256 curto já calculado em `estrutura.json`). No
próximo `ingest`, um arquivo cujo hash não mudou reaproveita a entrada
anterior sem gastar uma chamada de LLM.

---

## Arquivos criados

### `engine/entender.py` (novo)
Orquestra a geração do Modelo Interno do Projeto.

- `gerar_entendimento_arquivos(estrutura, caminho_projeto, config, entendimento_existente, log)`
  Para cada arquivo em `estrutura` (saída do ingest): se o hash não mudou
  desde a última execução, reaproveita a entrada anterior; senão lê o
  arquivo inteiro, monta o prompt (`montar_prompt_entendedor`), chama a
  LLM (`executar_entendedor`) e grava o resultado com o novo hash. Se
  `config['entendimento']['gerar_via_llm']` for `false`, não chama a LLM
  e só preserva o que já existia. Devolve o dict pronto para
  `entendimento.json["arquivos"]`.
- `_parse_resposta_entendedor(texto)`
  Extrai e valida o JSON devolvido pela LLM (tipo, responsabilidade,
  entrada, saida, depende_de, funcoes_principais, pontos_criticos).
  Devolve `None` se a resposta não vier em JSON válido — quem chama
  decide manter a entrada anterior em vez de sobrescrever com algo vazio
  ou inventado.

### `Atual_Versão.md` (novo)
Este arquivo.

---

## Arquivos alterados

### `ingest.py`
- Importa `gerar_entendimento_arquivos` de `engine/entender.py`.
- `ingerir(...)`: novo parâmetro `config=None`. Depois de montar
  `estrutura` e o bloco `componentes` de sempre, chama
  `gerar_entendimento_arquivos(...)` e grava o resultado em
  `entendimento_json["arquivos"]`. Versão do arquivo passa de `"1.0"`
  para `"1.1"`.
- Resumo final do `ingest` no terminal agora mostra também quantos
  arquivos ganharam entendimento (`entendimento.json['arquivos']`).
- `main()`: novo argumento `--config` (default `./config.json`, carrega
  o endpoint/modelo da LLM usado pelo Entendedor) e novo argumento
  `--pular-entendimento-llm` (ingest só-heurístico, sem chamar a LLM,
  útil sem servidor local rodando — preserva as entradas antigas).

### `main.py`
- Nova função `carregar_config()` (lê `config.json`).
- `cmd_ingest(args)`: agora carrega a config e repassa para `ingerir(...)`;
  respeita a nova flag `--pular-entendimento-llm`.
- Novo argumento `--pular-entendimento-llm` no subcomando `ingest`.

### `engine/compiler.py`
- Nova função `montar_prompt_entendedor(caminho_relativo, conteudo, max_chars=20000)`:
  monta o prompt que pede para a LLM ler um arquivo inteiro (ingestão,
  uma vez por arquivo) e devolver tipo/responsabilidade/entrada/saída/
  depende_de/funcoes_principais/pontos_criticos em JSON. Trunca arquivos
  maiores que `max_chars` (configurável) para não estourar o contexto.

### `llm/executar.py`
- Novo prompt de sistema `PROMPT_ENTENDEDOR` (terceira personalidade da
  mesma LLM local, ao lado de Analista e Executor): só lê e descreve o
  arquivo, nunca gera código, nunca responde ao usuário.
- Nova função `executar_entendedor(prompt_usuario, config)`: chama a LLM
  com `PROMPT_ENTENDEDOR` (reaproveita `_chamar_llm` e o cache já
  existente por hash do prompt completo).

### `config.json`
- Nova seção `entendimento`:
  - `gerar_via_llm` (default `true`): liga/desliga a geração de
    `entendimento.json['arquivos']` via LLM no ingest.
  - `max_chars_por_arquivo` (default `20000`): limite de caracteres do
    conteúdo do arquivo mandado para a LLM no Entendedor.
- Versão do config sobe de `"1.1"` para `"1.2"`.

### `README.md`
- Estrutura do projeto atualizada: menciona `engine/entender.py` e o que
  `memory/entendimento.json` guarda agora (`componentes` + `arquivos`).

---

## O que **não** mudou nesta atualização

- `retrieval/buscar.py`, `verify/validar.py`, `engine/entender.py`,
  `web/routes.py`, `web/static/*`, `web/templates/*` — nenhuma função
  existente foi alterada. `entendimento.json["componentes"]` continua
  sendo montado e usado exatamente como antes (bloco `RESUMO DO PROJETO`
  em `engine/compiler.py:bloco_entendimento`). Os pipelines `chat`,
  `consulta`, `visao_geral` e `engenharia` continuam funcionando
  exatamente como antes — a única mudança de comportamento em
  `engine/roteador.py` além do novo tipo `dicas` é a correção do bug de
  substring matching descrita no topo deste arquivo.
- `entendimento.json["arquivos"]` continua **só sendo escrito** no
  ingest (Atualização 3) — a Atualização 4 é o primeiro lugar que **lê**
  isso para decidir algo em tempo de pergunta/resposta.
- Nesta atualização (4), nenhum patch ainda era aplicado no código do
  projeto indexado — o Sugestor só apontava, nunca editava nada. Isso
  passou a existir na Atualização 5 (ver topo deste arquivo).

---
