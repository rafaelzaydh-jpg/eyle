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

