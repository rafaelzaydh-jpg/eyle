# Update history — decisões removidas e por que não devem voltar por padrão

Este arquivo não substitui o `CHANGELOG.md`. O changelog registra **o que mudou**. Este documento registra **arquiteturas, mecanismos e proteções que já existiram, por que foram removidos e qual evidência seria necessária para justificar uma reintrodução**.

Objetivo: evitar que uma futura revisão reencontre uma ideia antiga, pareça boa isoladamente e recrie um problema que o projeto já pagou para descobrir.

## Regra de reintrodução

Antes de reimplementar qualquer item abaixo, a proposta deve responder objetivamente:

1. Qual problema atual, reproduzível, não é resolvido pela arquitetura presente?
2. Qual teste ou métrica prova esse problema?
3. Por que a solução antiga falhou?
4. O que mudou desta vez para que a mesma falha não se repita?
5. Qual é o custo em chamadas LLM, tokens, complexidade, latência e pontos de falha?
6. Existe uma ferramenta determinística mais simples que resolve o problema sem controlar o raciocínio da LLM?

Sem respostas concretas, o padrão é **não reintroduzir**.

---

## 1. Pipelines separados por tipo de tarefa

**Removido:** pipelines históricos como `consulta`, `dicas`, `visao_geral`, `engenharia`, além de wrappers separados de Analyst, Executor, Suggestor, Engineer e Understander.

**Por que saiu:** o mesmo pedido podia atravessar caminhos diferentes, produzir contratos diferentes e acumular lógica duplicada. Corrigir um comportamento em um pipeline não corrigia os demais. A arquitetura ficou difícil de prever e testar.

**Substituição atual:** uma única `AgentSession`. A mesma LLM conversa, investiga, escolhe tools, propõe patch e responde.

**Só reconsiderar se:** existir evidência de que um fluxo realmente requer isolamento operacional que não possa ser representado por tools ou fases da mesma sessão.

---

## 2. Roteamento semântico/por palavras-chave como cérebro da tarefa

**Removido:** roteador semântico, keyword intent routing e classificadores determinísticos que escolhiam pipelines.

**Por que saiu:** linguagem natural é ambígua. Pequenas variações ou erros de digitação mudavam o caminho da tarefa. O roteador também passou a competir com a própria LLM para interpretar intenção.

**Substituição atual:** hints estreitos apenas controlam disponibilidade de tools em chat simples; eles não respondem nem decidem o significado completo do pedido.

**Só reconsiderar se:** o roteamento for estritamente operacional, mensurável e não competir com a interpretação da LLM.

---

## 3. TaskContract determinístico, MissionSpec e Mission Interpreter

**Removido:** `TaskContract`, `MissionSpec`, Mission Interpreter e Mission Repair como camadas obrigatórias antes da execução.

**Por que saiu:** o pedido do usuário era reinterpretado várias vezes antes de chegar ao agente. Isso aumentava tokens, criava divergência entre intenção original e missão normalizada e adicionava pontos onde a tarefa podia falhar sem sequer ler o projeto.

**Substituição atual:** o pedido original permanece na `AgentSession`; um plano é opcional e produzido pela mesma LLM.

**Só reconsiderar se:** houver um caso regulatório ou de protocolo que exija formalização externa comprovadamente impossível de manter no runtime/tool contract.

---

## 4. Scouts, Finalizers e agentes auxiliares automáticos

**Removido:** Scouts de leitura, Finalizers especializados, gap-audit agents e outros agentes auxiliares automáticos.

**Por que saiu:** cada camada gerava novas chamadas, repetia contexto e criava o efeito “tribunal”: um modelo fazia, outro reinterpretava, outro julgava. Em tarefas pequenas o overhead era enorme; em tarefas grandes a soma de prompts e reparos podia dominar o custo real do trabalho.

**Substituição atual:** uma LLM + tools + validação determinística somente nas fronteiras necessárias.

**Só reconsiderar se:** um benchmark real demonstrar ganho líquido de qualidade suficiente para pagar chamadas, tokens, latência e novas classes de falha.

---

## 5. Structured-claim court / grounding lexical excessivo

**Removido:** tribunais de claims mais pesados, lexical grounding, information-preservation ledger e múltiplas camadas de reparo da resposta.

**Por que saiu:** mecanismos criados para evitar alucinação começaram a rejeitar respostas corretas por diferenças de redação e a consumir chamadas extras para “consertar” formato. O sistema passou a controlar demais a forma da resposta em vez de validar fatos concretos.

**Substituição atual:** ledger compacto de evidência, tipos `fact/bug/risk/recommendation`, referência por número de frase e validações determinísticas pequenas.

**Só reconsiderar se:** houver uma classe reproduzível de erro factual que o ledger atual não detecte e que não possa ser resolvida com evidência/tool melhor.

---

## 6. Claims duplicando a frase inteira

**Removido:** `claims[].text` como protocolo preferencial, repetindo literalmente cada frase da resposta.

**Por que saiu:** em respostas longas o protocolo duplicava texto e desperdiçava tokens. Também criou `FINAL_CLAIM_NOT_IN_ANSWER` quando a claim era apenas uma paráfrase da frase visível.

**Substituição atual:** `claims[].sentence`, referência numérica às frases visíveis. Compatibilidade textual antiga permanece apenas para não quebrar estados legados.

**Só reconsiderar se:** o índice de frases se provar insuficiente em um formato futuro de resposta e houver alternativa mais compacta que duplicar toda a prosa.

---

## 7. ProjectMemory automático no prompt

**Removido:** injeção automática de `ProjectMemory`, `memory/projeto.json`, memória semântica obrigatória e budgets dedicados de memória no prompt.

**Por que saiu:** contexto antigo entrava mesmo quando não era relevante, aumentava tokens e podia competir com o estado real do workspace.

**Substituição atual:** memória externa é uma tool. A LLM chama `memory_search` ou `memory_store` somente quando faz sentido. Fatos ligados a arquivos carregam hashes para detectar desatualização.

**Só reconsiderar se:** houver medição de recall insuficiente que não possa ser resolvida por busca sob demanda e houver política clara de invalidação.

---

## 8. `entendimento.json` e inventário completo no prompt

**Removido:** geração de `memory/entendimento.json` e listas completas de paths inseridas automaticamente no contexto.

**Por que saiu:** projetos grandes pagavam muitos tokens antes de qualquer ação útil. Estrutura antiga podia ainda ficar desatualizada.

**Substituição atual:** `list_tree`, `project_stats`, `inspect_project`, `search_code` e leituras selecionadas pela tarefa.

**Só reconsiderar se:** for um artefato explicitamente solicitado pelo usuário, nunca como contexto obrigatório.

---

## 9. Ingest/index obrigatório

**Removido:** ingestão/indexação como entrada obrigatória do projeto e código de ingest sem consumidor ativo.

**Por que saiu:** adicionava um estado intermediário que podia ficar velho e duplicava o workspace real como fonte de verdade.

**Substituição atual:** o workspace vivo é a fonte principal; tools inspecionam diretamente os arquivos atuais.

**Só reconsiderar se:** projetos reais ultrapassarem a capacidade de busca direta e um índice demonstrar benefício mensurável com invalidação confiável.

---

## 10. Ciclo complexo de evidência: active/consumed/reactivated

**Removido:** lifecycle de evidência ativa/consumida/reativada, replay amplo de ações e semantic progress pipeline antigo.

**Por que saiu:** a sessão gastava lógica tentando administrar o estado da própria evidência e ainda podia perder a fonte necessária após uma tentativa de patch.

**Substituição atual:** evidência fresca + snippets relevantes limitados + cobertura semântica simples de leituras + fases de execução.

**Só reconsiderar se:** existir bug reproduzível de stale evidence que hashes e retenção limitada não resolvam.

---

## 11. Cache de resposta da LLM / replay de decisão

**Removido:** `llm/cache.py`, LRU/SQLite de respostas do modelo e replay de decisões antigas da AgentSession.

**Por que saiu:** uma decisão de agente depende do workspace e do estado atual. Reutilizar resposta antiga podia reproduzir uma escolha obsoleta mesmo com código alterado. O cache também aumentava a complexidade de invalidação.

**Importante:** isso é diferente do **cache de prompt do provedor**. Tokens cacheados pelo Qwen/OpenAI-compatible continuam sendo medidos; apenas não reusamos a decisão da LLM como se fosse nova.

**Só reconsiderar se:** o cache for limitado a saídas comprovadamente puras e tiver chave/invalidação que inclua todo estado relevante. Nunca para decisões de escrita ou inspeção do workspace.

---

## 12. Recuperação textual/JSON excessiva

**Removido:** JSON Repair personality, retries textuais amplos e recovery layers que pediam novas respostas completas quando o protocolo falhava.

**Por que saiu:** uma falha de formato podia virar várias chamadas grandes, escondendo o erro original e ampliando loops.

**Substituição atual:** parser pequeno, tentativas limitadas e feedback específico. Falhas persistentes terminam com código claro.

**Só reconsiderar se:** um backend específico tiver erro de protocolo mensurável e a correção puder ser curta, limitada e testada.

---

## 13. Guardrails que controlavam o raciocínio em vez da execução

**Removido/reduzido:** múltiplas seguranças sobrepostas que tentavam ditar cada etapa lógica do agente.

**Por que saiu:** mais guardrails não produziram proporcionalmente mais segurança. Alguns entravam em conflito, causavam loops, aumentavam prompts e impediam a LLM de usar ferramentas de forma natural.

**Substituição atual:** liberdade estratégica da LLM + limites fortes nas fronteiras executáveis: path seguro, confirmação, dry-run, testes, rollback, releitura, limites de chamada e validação factual enxuta.

**Só reconsiderar se:** a nova trava proteger uma fronteira concreta e tiver teste mostrando que não cria um segundo sistema de planejamento.

---

## 14. Prompt fixo grande

**Removido:** prompt de agente com aproximadamente 1,3k–1,4k tokens descrevendo hashes, rollback, claims, limites, tools e regras já impostas pelo runtime.

**Por que saiu:** o texto era reenviado a cada chamada. Mesmo com cache do provedor, o orçamento interno somava prompt repetido e tarefas de escrita podiam atingir o limite antes de concluir.

**Substituição atual:** prompt compacto; contratos detalhados ficam nas tools e no runtime. Fases limitam loops de leitura.

**Só reconsiderar se:** uma instrução não puder ser aplicada deterministicamente e houver evidência de que sua ausência causa erro real. Preferir uma frase curta a um novo manual.

---

## 15. Segurança por limite global de turnos como principal anti-loop

**Removido como mecanismo principal:** depender somente de `max_llm_turns`/token budget para encerrar investigação.

**Por que saiu:** o agente podia usar todos os turnos lendo, chegar ao limite e nunca produzir o patch. Uma tarefa simples chegou a consumir mais de 12k tokens em falhas desse tipo.

**Substituição atual:** máquina de fases, no máximo dois turnos comuns de investigação de escrita, patch-only depois disso, bloqueio de leituras equivalentes e detecção de ausência de progresso. Limites globais continuam apenas como última barreira.

**Só reconsiderar se:** nunca substituir as fases por um teto maior. Aumentar combustível não corrige carro andando em círculos.

---

## 16. Tratamento diferente para projetos “pequenos” e “grandes”

**Removido:** thresholds arquiteturais que mudavam o fluxo com base em um número pequeno de arquivos ou em tamanho presumido.

**Por que saiu:** tamanho não define a dificuldade da tarefa. Um projeto pequeno pode exigir investigação profunda; um projeto enorme pode ter uma alteração localizada.

**Substituição atual:** mesma arquitetura para todos; a tarefa determina quais evidências e tools são necessárias.

**Só reconsiderar se:** for apenas otimização de capacidade/scan com comportamento semanticamente equivalente, nunca um cérebro diferente.

---

## 17. Confirmação e estado pendente duplicados no core

**Removido:** IDs de confirmação gerados no core, metadados pendentes duplicados e múltiplos envelopes de resultado (`completion_gate`, `agente_status`, `agente_conclusao`, etc.).

**Por que saiu:** havia duas fontes de verdade para a mesma transação e compatibilidades que podiam divergir.

**Substituição atual:** runtime service é dono de ID, expiração, binding do projeto e envelope público. AgentSession mantém somente o estado necessário para continuar.

**Só reconsiderar se:** existir uma segunda interface que não consiga usar o contrato do runtime atual; mesmo assim, preferir um adaptador externo a duplicar estado no core.

---

## 18. `memory/projeto.json` como fallback de workspace

**Removido:** seleção de workspace por arquivo de memória legado.

**Por que saiu:** permitia que o projeto “lembrado” divergisse do workspace realmente aberto.

**Substituição atual:** discovery do workspace é a única fonte de verdade operacional.

**Só reconsiderar se:** houver suporte explícito a múltiplos workspaces com identificação forte e visível; nunca como fallback silencioso.

---

## 19. Resumo público burocrático de quatro etapas

**Removido:** renderer obrigatório de “Entendimento → Leitura → Análise → Conclusão” e metadados de pipeline mostrados como resposta principal.

**Por que saiu:** respostas simples pareciam relatórios administrativos e o formato podia perder aderência ao pedido real.

**Substituição atual:** resposta natural. A Rev4.12 traz observabilidade separada numa aba expansível, sem poluir a conversa.

**Só reconsiderar se:** o usuário pedir explicitamente um relatório estruturado. Para auditoria técnica, usar o histórico observável, não transformar toda resposta em log.

---

## 20. Exposição de detalhes internos como “transparência”

**Nunca deve voltar:** prompt bruto, resposta bruta do modelo, chain-of-thought, conteúdo integral de fontes ou corpo da memória no painel de histórico.

**Por quê:** observabilidade útil é saber **o que foi executado e qual resultado objetivo ocorreu**, não despejar raciocínio privado ou dados potencialmente sensíveis.

**Substituição atual (Rev4.12):** histórico por job com fases, chamadas LLM, tokens, tools com argumentos sanitizados, resultados resumidos, validação pós-escrita e rollback.

**Condição de mudança:** novos campos só devem ser adicionados se forem observáveis do runtime, limitados e seguros para aparecer na interface.

---

## O que foi mantido de propósito

Algumas proteções não foram removidas porque protegem realidade executável e não tentam substituir a inteligência do modelo:

- containment de paths;
- limites de leitura/scan;
- dry-run antes de escrita;
- confirmação humana;
- transação multi-arquivo;
- `compileall` para Python alterado;
- detecção e execução de testes;
- rollback em falha;
- releitura e verificação da saída final;
- evidência real para fatos sobre código;
- limites globais de deadline/chamadas como última barreira;
- fase patch-only e bloqueio de leitura repetida para impedir loops operacionais.

A regra arquitetural resultante é simples:

> **LLM pensa e escolhe. Tools observam e executam. Runtime protege fronteiras concretas. Histórico mostra o que realmente aconteceu.**

---

## 13. Runtime escrevendo a resposta final de ferramentas determinísticas

**Não adotado / rejeitado:** fazer o runtime responder diretamente ao usuário quando `calculate` ou outra tool já possui o resultado.

**Por que não adotamos:** isso mistura responsabilidades e cria duas vozes. A tool deve garantir o fato objetivo; a LLM deve continuar escolhendo como explicar, formatar e conversar. Também impediria respostas naturais quando o usuário pede explicação do cálculo.

**Substituição atual:** `LLM → tool determinística → LLM final`. Para `calculate`, o resultado vira evidência real para que uma final estruturada não gere uma terceira chamada de reparo desnecessária.

**Só reconsiderar se:** houver um modo explicitamente não conversacional (por exemplo API machine-to-machine) em que o consumidor peça saída determinística e sem linguagem natural.

---

## 14. Saída bruta completa de testes no contexto da LLM

**Removido/evitado:** devolver stdout/stderr inteiro de pytest ou outros runners para o modelo.

**Por que evitamos:** suítes grandes podem produzir milhares de linhas e transformar uma investigação simples em consumo massivo de tokens. O log completo também tende a repetir stack traces e warnings irrelevantes.

**Substituição atual:** `run_tests` devolve comando, código de retorno, resumo conciso e uma cauda limitada da saída. A Eyle pode restringir pytest a um arquivo/diretório quando já sabe o escopo.

**Só reconsiderar se:** um erro real não puder ser diagnosticado com a saída limitada e houver mecanismo de paginação/busca sob demanda, nunca injeção automática do log inteiro.

---

## 15. Git como mecanismo automático de escrita/reversão

**Não adotado:** deixar a Eyle executar `git add`, `commit`, `reset`, `checkout`, `restore` ou outras mutações Git como parte automática do loop.

**Por que não adotamos:** o Git contém trabalho do usuário que pode ser independente da tarefa atual. Usá-lo como rollback automático arriscaria sobrescrever ou misturar alterações preexistentes e duplicaria o rollback transacional já existente.

**Substituição atual:** `git_status` e `git_diff` são tools somente leitura. Elas dão retrovisor para a Eyle distinguir estado e mudanças sem assumir propriedade sobre o histórico Git.

**Só reconsiderar se:** houver uma feature explicitamente solicitada pelo usuário, com escopo/branch isolado, confirmação e testes que provem que mudanças preexistentes nunca são tocadas.
---

## 18. Dumps estruturados completos de tools no prompt

**Removido:** enviar ao turno seguinte listas e mapas estruturados completos de tools grandes, como 100 entradas de `list_tree` ou dezenas de relações/imports de `inspect_project`, apenas porque a tool conseguiu produzi-los.

**Por que saiu:** a tool pode processar muito mais informação do que a LLM precisa receber. Em projeto real, a combinação de árvore + inspeção + README gerou um payload de ~7,3k tokens e foi bloqueada pela janela de contexto antes de chegar ao Qwen.

**Substituição atual:** compactação genérica e progressiva de strings/listas/maps aninhados somente na visão enviada à LLM. O resultado completo permanece na sessão e no histórico recuperável.

**Só reconsiderar se:** um benchmark demonstrar que a perda do detalhe compactado causa erro real e houver aumento de janela/custo claramente justificado. A resposta preferida deve ser busca/leitura focada, não despejar novamente o inventário inteiro.

## 19. `pytest` apenas como dependência de desenvolvimento

**Removido:** manter `pytest` exclusivamente em `requirements-dev.txt` enquanto `run_tests` era anunciado como tool oficial de runtime.

**Por que saiu:** uma instalação normal podia oferecer `run_tests` e falhar antes de executar qualquer teste com `No module named pytest`, classificando incorretamente o caso como falha da suíte.

**Substituição atual:** pytest é dependência de runtime; ausência de runner é diagnosticada como `TEST_RUNNER_UNAVAILABLE`, distinta de `TESTS_FAILED`.

**Só reconsiderar se:** `run_tests` se tornar uma capacidade opcional detectada dinamicamente e a interface deixar explícito que o runner não está instalado.


## Source-available personal-use licensing

**Current decision:** Eyle is public source code under a custom personal-use, non-commercial license. It is not open-source software.

**Why this exists:** the earlier placeholder effectively granted no practical local-use permission while simultaneously telling maintainers to replace it with an OSI license. That contradicted the intended model: people should be able to download and use Eyle privately, while redistribution, publication of modified copies, resale, sublicensing, commercial use, and hosted-service use remain restricted.

**What was removed:** the template instruction telling maintainers to replace `LICENSE.md` with MIT, Apache-2.0, GPL-3.0, or another OSI license. The blanket wording that no copying was permitted was also replaced because installation and personal use necessarily require limited copying.

**Do not reintroduce:**

- an OSI/open-source license merely because the repository is public;
- blanket “no copying” language that conflicts with the personal-use permission;
- contribution rules that leave maintainers unable to use, relicense, or commercialize accepted contributions;
- README wording that describes Eyle as open source when the license has not explicitly changed.

**Reconsider only if:** the project intentionally decides to allow broader redistribution/commercial use, or legal review recommends a different licensing structure. Any such change should be explicit, repository-wide, and documented here before publication.

---

## 20. Palavra-chave como roteador principal de capacidade

**Reduzido na Rev4.12.3:** o classificador lexical não decide mais se uma pergunta normal sobre um workspace merece ferramentas de investigação. Apenas conversa/utilidades óbvias mantêm um fast-path barato; fora disso, a LLM recebe capacidade real de inspeção e escolhe a evidência necessária.

**Por que reduzimos:** palavras isoladas criaram falsos positivos e falsos negativos concretos. `separe` em “separe riscos de bugs” armou fluxo de escrita, enquanto perguntas sobre Git, `AgentSession` e fluxo de mensagens caíram em chat sem tools porque não continham a combinação esperada de palavras.

**O que permanece:** um detector conservador de mutação continua existindo somente como proteção contra conclusão em prosa quando um pedido de escrita é claramente identificável. A segurança real continua nas fronteiras executáveis: dry-run, confirmação, paths, hashes, transação, testes, rollback e releitura.

**Não reintroduzir:** listas crescentes de palavras para mapear cada intenção possível para uma tool/fase. Uma nova regra lexical precisa provar que protege uma fronteira concreta e não apenas tenta pensar no lugar da LLM.

---

## 21. Orientação de tools separada do contrato da própria tool

**Removido na Rev4.12.3:** `_tool_guidance` com dicas específicas como “use X para isso” ou “prefira Y em vez de Z”.

**Por que saiu:** a informação ficava fragmentada entre registro, catálogo minimalista, guidance e fase. Isso tornava a escolha da LLM dependente de regras externas e permitia que uma tool fosse descrita em função de outra.

**Substituição atual:** cada tool model-visible possui contrato compacto próprio: `purpose`, `inputs`, `returns`, `does_not`, `side_effects` e `limits`. A tool descreve somente o que observa/executa e o que sua saída não prova. A combinação entre tools pertence à LLM.

**Não reintroduzir:** instruções por-tool fora do registro para roteamento semântico. Exceções só cabem quando representam uma restrição executável que não pode ser expressa no contrato/validador da própria tool.

---

## 22. `run_tests` encerrando qualquer investigação após um resultado

**Removido na Rev4.12.3:** a regra “houve `run_tests` → próximo turno é answer-only” para toda análise read-only.

**Por que saiu:** funcionava para “execute os testes”, mas cortava tarefas compostas como “analise profundamente, execute testes e proponha correções”. Um runner indisponível virava fim artificial da investigação.

**Substituição atual:** o fechamento é baseado no estado observável da tarefa. `run_tests` só fecha tools quando é a única observação de projeto e não existe plano multi-etapa declarado. Se outras observações já aconteceram, a investigação permanece aberta.

**Só reconsiderar:** com métricas/trace que provem chamadas extras reais em tarefas estreitas e sem voltar a encerrar tarefas compostas.

---

## 23. Medição tratada como diagnóstico

**Regra explicitada na Rev4.12.3:** uma tool não prova algo que ela não observa. Exemplo: `count_tokens` mede texto do projeto; não mede tokens realmente enviados à LLM e não diagnostica desperdício de contexto. `project_stats` mede tamanho/estrutura, não importância. `inspect_project` retorna sinais, não confirma comportamento runtime.

**Motivo:** no teste real, tamanho de `agent.py` foi usado como proxy para “desperdício de tokens”. A conclusão era plausível, mas não estava medida.

**Substituição atual:** contratos declaram explicitamente `does_not`. O futuro mecanismo de self-debug/tracing deve oferecer fatos do caminho real da execução; a conclusão continuará sendo da LLM.

---

## 24. Repetir sufixo de compactação em string já truncada

**Corrigido na Rev4.12.3:** strings próximas do piso de compactação não recebem `...[context cropped]` repetidamente.

**Falha antiga:** ao reduzir uma string para 1000 caracteres e adicionar o marcador, ela continuava maior que 1000; o próximo ciclo podia repetir a mesma operação indefinidamente. O catálogo semântico maior expôs esse loop em regressão de contexto.

**Substituição atual:** o compactador remove logicamente o sufixo antes de medir, só reduz quando há redução real possível e converge para um piso estável.

## 25. Rev4.12.3.1 — hotfix da fundação antes do self-debug

**Corrigido:** fast-paths por presença de palavras (`capacidade`, expressão matemática etc.) podiam esconder tools de projeto em pedidos compostos. Agora apenas pedidos integralmente utilitários usam o caminho barato.

**Corrigido:** `run_tests` podia fechar uma análise composta se a LLM não declarasse `plan`. O fechamento antecipado agora exige que o próprio pedido seja claramente test-only e que não existam outras observações de projeto.

**Corrigido:** a exigência de evidência confundia opinião geral sobre arquitetura com fato do workspace e deixava escapar pedidos como “identifique bugs reais”/“onde AgentSession é definido”. A regra agora mira realidade concreta do workspace; hipóteses, opiniões, recomendações e trade-offs continuam livres quando claramente assumidos.

**Corrigido:** comandos para reescrever a própria resposta não armam mais o fluxo de escrita no workspace. Escrita real continua sendo decidida pela tentativa concreta de patch e protegida por confirmação/validação.

**Corrigido:** contratos de tools agora descrevem todos os argumentos; `search_code` declara busca literal, `memory_store` declara efeito `MEMORY_WRITE`, e `agent_info` separa escrita habilitada da política de confirmação.

**Corrigido:** histórico de tools deixou de registrar “accepted” antes da validação. O fluxo observável passa a distinguir `requested → validated/rejected → executed/skipped/failed/completed`, preservando tentativas rejeitadas. Essa base agora é consumida pela `execution_trace` sem reinterpretar estados antigos.

**Compatibilidade mantida conscientemente:** `claims[].text` e aliases selecionados ainda podem ser aceitos para retomada/compatibilidade de protocolo, mas não são o formato emitido pelo prompt/catalogo atual e não reativam nenhuma arquitetura de raciocínio legada.


## 26. Rev4.12.3.1 — `execution_trace` sem segundo oráculo de diagnóstico

**Adicionado:** uma única tool `execution_trace` consulta fatos sanitizados da execução atual ou de jobs persistidos: fases, composição/tamanho do contexto antes e depois da compactação, metadados de chamadas LLM/tokens, decisões, tools e validações.

**Decisão arquitetural:** o runtime continua apenas registrando realidade observável. A tool não conclui causa, não decide se tokens foram desperdiçados e não substitui leitura de código/testes. A LLM conecta o trace às demais evidências e testa suas próprias hipóteses.

**Sem segundo store:** o trace reutiliza `AgentSession` + detalhes já persistidos do job. Não criar bancos paralelos de tracing enquanto esse registro for suficiente.

**Privacidade:** não expor chain-of-thought, prompt bruto, resposta bruta do modelo, corpos de fonte/patch/memória, hashes sensíveis ou segredos. O registro de composição do contexto guarda apenas nomes de componentes e métricas de tamanho/tokens.

**Não reintroduzir:** tools especializadas como `token_waste`, `why_transition`, `debug_classifier` ou equivalentes que entreguem diagnóstico mastigado. Só reconsiderar se o trace factual não conseguir representar uma evidência concreta necessária.
## 27. Rev4.12.4 — taxonomia compartilhada de tools sem novo roteador

A Rev4.12.4 não cria uma etapa de seleção por categoria. O runtime continua filtrando somente o que é executável na fase atual e envia todas as tools permitidas para a mesma LLM escolher. `READ_ONLY` e `EDIT` são apenas classes de autoridade compartilhadas, não rotas semânticas.

Os efeitos deixam de ser frases repetidas em cada contrato e passam a tags globais: `NONE` (padrão), `EXEC`, `TEMP`, `MEMORY_WRITE`, `WORKSPACE_WRITE`, `VERIFY` e `ROLLBACK`. Contratos individuais preservam somente finalidade, assinatura compacta de argumentos, retorno, caveats específicos e limites numéricos.

Motivo: o catálogo da Rev4.12.3.1 repetia `side_effects: none`, “does not modify files” e explicações equivalentes em quase todas as tools. A primeira tentativa de simplesmente adicionar `category/effects` por tool aumentou o wire-size; ela foi descartada antes do release. A implementação final agrupa nomes na taxonomia uma única vez.

Medição com o mesmo serializador: catálogo completo de 20 tools caiu de 12.492 caracteres para ~10.241 incluindo a taxonomia; `analysis_investigate` com 15 tools caiu de 8.353 para ~7.049. A otimização não justifica esconder tools da LLM nem adicionar uma chamada de roteamento.

Não reintroduzir `_tool_guidance`, seleção lexical por ferramenta ou “LLM escolhe categoria → segunda LLM escolhe tool” sem evidência nova: isso recuperaria exatamente as camadas que a arquitetura removeu.


## 28. Rev4.12.4.1 — janela 32k e orçamento cumulativo separado

A janela por chamada e o orçamento cumulativo do job foram separados explicitamente. O contexto padrão passa a 32.768 tokens por request; o job pode acumular até 96.000 tokens efetivos de prompt ao longo de várias chamadas, sem nunca permitir que uma única chamada ultrapasse a janela do backend. Não restaurar o antigo teto de 12k como mecanismo anti-loop: loops continuam limitados por turnos, ferramentas repetidas, no-progress, deadline e fases.

A reserva de saída deixa de crescer conforme a quantidade de fonte lida. Análise usa reserva estável por fase; apenas fases de patch recebem reserva maior. Fases `analysis_*` também deixam de ganhar dry-run de patch automaticamente. Essa separação evita que evidência crescente reduza o próprio espaço de investigação.

`agent_info` passa a distinguir `registered_tools` (registro completo) de `available_tools` (subconjunto executável na fase atual). Não voltar a inferir capacidade total pelo catálogo local do turno.
