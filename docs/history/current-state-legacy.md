## Estado após a revisão 50.1

O Agente usa schema JSON explícito no llama-server, tenta desligar thinking nas
decisões, ignora cache estrutural e aceita `reasoning_content` quando necessário.
Configuração padrão: 8192 de janela, 1500 de saída, timeout 600 e modelo `auto`.

# Estado Atual — Eyle

(Arquivo curto de propósito. Para histórico detalhado, ver `Atual_Versão.md`.)

**Próximo número livre de Atualização: 51**

**Últimas atualizações aplicadas:** **48-50 concluídas no código.** O rollout agora é `off`/`read_only`/`full`; o pacote fica em
`read_only` até o benchmark real do LFM2 permanecer verde. `full` exige raiz em
`trusted_project_paths`. Uma única configuração `off` restaura os pipelines
anteriores.

**Atualização 50:** compatibilidade básica automática para llama-server e outros
backends OpenAI-compatible. A Eyle detecta o único modelo exposto em
`/v1/models`, faz fallback de `response_format` e de `role=system`, guarda as
capacidades em memória e limpa blocos de raciocínio apenas nas respostas JSON
do Agente. Nenhum perfil por família nem tool calling nativo foi adicionado.

**Correção 49.1:** tarefa de projeto não aceita `needs_user` antes de tentar
READ. Análise geral força `list_tree`; falta de contexto sem leitura vira
`PREMATURE_NEEDS_USER` e o ciclo continua até ler código ou encontrar bloqueio
real. O ZIP de atualização exclui `memory/*` e `context/*`, preservando índice,
conversa, fila, pendências, token e backups ao extrair sobre a instalação.

**Persistência/retomada:** toda tarefa recebe `task_id` e checkpoint em
`context/fila.sqlite3` (`agent_tasks`) com GoalState, evidências/hashes, ação
pendente e orçamento. Todo `needs_user` é retomável. Reinício retoma somente
ações idempotentes; `WRITE` é revalidada e nunca repetida se o patch já estiver
no disco. Cancelamento/expiração preservam auditoria.

**Benchmark:** `python main.py benchmark` roda os 10 cenários e calcula o gate
com métricas de leitura, grounding, JSON, latência, falso sucesso, autorização
e 5 checks de escrita. `--baseline-model` aceita o nome exato do Q4 4B. O
backend local não estava disponível nesta sessão; portanto o gate real do LFM2
ainda deve ser executado na máquina do usuário antes de promover o rollout para
`full` por padrão.

**Configuração:** `config.json` está em `2.6`, com
`agent.rollout_mode: "read_only"`, `trusted_project_paths: []`,
`enabled_modes: ["analyze", "suggest", "edit"]` e
`llm.context_window_tokens: 8192`. O catálogo/contrato 46 deixa cerca de **aproximadamente 4k
tokens** iniciais para código; `context.token_budget: 1500` continua exclusivo
do retrieval legado.

**Validação:** **148/148 testes passaram**; `compileall`, `config.json`, CLI e
JavaScript válidos. Depois da 49 não há atualização de núcleo agendada: só entra
novo mecanismo quando o benchmark demonstrar necessidade.
