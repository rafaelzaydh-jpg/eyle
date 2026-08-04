# Plano de Consolidação da Eyle Base como Agente — Atualizações 40 em diante

## Veredito

As Atualizações 28-39 deixaram a Eyle **mais segura e previsível para executar
testes e ler repositórios**, mas não a transformam, sozinhas, em agente. A 39
já separou a permissão `EXEC`. Isso é fundação de segurança, não ganho de
inteligência.

Hoje a Eyle já possui partes de um agente — loop, estado, ferramentas,
confirmação, rollback e testes — mas ainda falha nos três pontos que definem a
utilidade real:

1. não enxerga código suficiente nas observações do loop;
2. pode concluir uma tarefa de projeto sem provar que leu código;
3. pedidos comuns sobre o projeto continuam fora do loop do Agente.

O marco honesto será:

- após a **43**, a Eyle pode ser chamada de agente de leitura/análise;
- após a **46**, pode ser chamada de agente de programação com ação verificada;
- após a **48**, o agente pode ser ativado como caminho padrão com segurança;
- após a **49**, ele fica resiliente a pausas e reinícios.

## Objetivo do plano

Manter a **Eyle Base simples e consolidada**, usando o **LFM2 8B-A1B como único
modelo principal**, mas fazer a Eyle operar com um ciclo real:

`entender → localizar → ler → guardar evidência → agir → verificar → responder`

Não criar “outra Eyle”, nem vários agentes/personagens concorrendo entre si.
`visao_geral`, `consulta`, `dicas` e `engenharia` passam a ser intenções ou
modos internos do mesmo agente.

## Princípios obrigatórios

- **Um cérebro:** LFM2 8B-A1B como modelo-base. O Q4 4B fica como referência de
  compatibilidade, não como segundo pipeline.
- **Um loop:** todo trabalho sobre projeto usa o mesmo estado e as mesmas
  ferramentas.
- **Ferramentas antes de opinião:** fatos do projeto vêm de leitura real, nunca
  apenas de nomes de arquivos ou memória antiga.
- **Evidência estruturada:** conteúdo lido não pode virar um resumo cego de 500
  caracteres e desaparecer quatro passos depois.
- **Conclusão objetiva:** a LLM propõe que terminou; o sistema decide se os
  critérios foram cumpridos.
- **Escrita confirmada:** nenhuma alteração real sem confirmação explícita.
- **Simplicidade para modelo pequeno:** planos curtos, uma ação por passo,
  formatos pequenos e determinísticos.
- **Ativação gradual:** primeiro leitura, depois escrita, sempre com retorno
  fácil ao modo anterior.

## Relação com as Atualizações 28–39

Este plano não cancela o hardening já numerado.

| Atualização anterior | Relação com o Agente 40+ |
|---|---|
| 39 — permissão `EXEC` | ✅ Concluída; libera o pré-requisito da 46. |
| 38 — `index_fingerprint` real | ✅ Concluída; `main.py status` detecta fonte alterada. |
| 30 — confiança/grounding honestos | ✅ Concluída; alimenta o gate de conclusão da 43 e já removeu `success = 1.0`. |
| 34 — validação de configuração | ✅ Concluída; libera o pré-requisito da 48. |
| 28–29 — sandbox e ingestão segura | ✅ Concluídas; fecham o gate para repositórios não confiáveis, sem substituir os gates de agente das 39–49. |

O hardening 28–39 está concluído. A sequência de utilidade agora começa na 40.

---

## Atualização 40 — Catálogo real de ferramentas e argumentos ✅ **feito**

### Problema

O prompt atual cita nomes de ferramentas, mas não entrega ao modelo seus
argumentos, tipos, limites e retorno esperado. Um modelo pequeno precisa
adivinhar o contrato.

### Escopo

- Cada entrada de `TOOLS` passa a declarar:
  - `name`;
  - `description` curta;
  - `permission` (`READ`, `EXEC` ou `WRITE`);
  - `input_schema`;
  - resumo do `output_schema`.
- `montar_prompt_agente` inclui o catálogo gerado do próprio registro, sem uma
  segunda lista manual que possa ficar desatualizada.
- Validação central rejeita argumento ausente, tipo errado e chave desconhecida
  antes de executar a ferramenta.
- Sinônimos antigos (`arquivo`/`caminho_relativo`, por exemplo) ficam somente
  num adaptador de compatibilidade e são normalizados para uma forma canônica.

### Critérios de aceite

- 100% das ferramentas registradas possuem esquema testado.
- Toda chamada inválida devolve `INVALID_ARGUMENT` determinístico.
- O prompt e o registro nunca divergem sobre ferramentas disponíveis.
- LFM2 consegue chamar cada ferramenta com JSON válido em teste isolado.

**Aplicado:** as nove tools registradas declaram `name`, descrição, permissão,
`input_schema`, resumo de saída e limites. O catálogo público é derivado desse
mesmo registro a cada passo e recebe os limites atuais do `config.json`.
`validar_chamada_tool` normaliza somente aliases legados declarados, rejeita
campo ausente, tipo errado, faixa invertida e chave desconhecida antes do gate
de confirmação/execução, sempre com `INVALID_ARGUMENT`. Os wrappers recebem
apenas argumentos canônicos. Testes percorrem 100% dos schemas, validam uma
chamada JSON canônica por tool e confirmam que validação inválida não chama a
função real.

## Atualização 41 — Olhos reais: árvore, busca com trecho e leitura por faixa ✅ **feito**

### Problema

`search_code` devolve localização e score, mas não o código. `read_file` lê até
20 mil caracteres, porém a observação seguinte guarda cerca de 500. Para um
arquivo de 14 linhas isso já foi suficiente para a Eyle responder sem enxergar
o conteúdo.

### Escopo

- Criar `list_tree` para listar a árvore atual do projeto no disco, com limite,
  profundidade, filtro e motivos de arquivos ignorados.
- Alterar `search_code` para devolver, por resultado:
  - arquivo;
  - faixa de linhas;
  - símbolo;
  - score;
  - trecho real numerado;
  - hash do conteúdo lido.
- Criar `read_range(caminho_relativo, linha_inicio, linha_fim)` para ler uma
  janela fresca e numerada direto do disco.
- Manter `read_file` como compatibilidade, mas orientar o Agente a preferir
  faixas pequenas e relevantes.
- Toda leitura usa o resolvedor seguro da Atualização 18.

### Critérios de aceite

- No caso real de um `audio.py` com 14 linhas, “analise este arquivo” faz a
  Eyle abrir e receber as 14 linhas antes de responder.
- Resultado de busca contém código, não apenas `arquivo:linhas + score`.
- Nenhuma janela pode ultrapassar o limite configurado sem erro claro.
- Hash e linhas retornados correspondem ao arquivo fresco do disco.

**Aplicado:** `engine/project_reader.py` concentra `list_tree` e a leitura
1-based por faixa usando o resolvedor seguro. A árvore respeita
profundidade/limite/filtro e publica somente contagens dos motivos ignorados,
sem revelar nomes de credenciais. `search_code` usa BM25 só para localizar,
reabre cada faixa no disco e devolve arquivo, linhas reais, símbolo, score,
trecho numerado e SHA-256 do conteúdo lido. `read_range` aplica o teto
`agent.max_read_range_lines`; `read_file` permanece como compatibilidade. O
teste de integração com `audio.py` confirma que as 14 linhas chegam ao segundo
passo do Agente antes do `final`.

## Atualização 42 — Context Engine de evidências estruturadas ✅ **feito**

### Problema

`AgentState` guarda observações em texto truncado e só reenvia as quatro mais
recentes. Código relevante pode sumir enquanto a tarefa ainda depende dele. Os
`fatos_importantes` atuais são escolhidos pela própria LLM, então não substituem
evidência objetiva.

### Escopo

- Separar o estado em quatro blocos:
  - `goal_state`;
  - `evidence`;
  - `actions`;
  - `recent_observations`.
- Cada evidência recebe `id`, ferramenta de origem, arquivo, linhas, conteúdo,
  hash e estado (`fresh` ou `stale`).
- O compilador monta o próximo prompt por orçamento de relevância, preservando
  evidências ligadas ao passo atual, em vez de cortar tudo por quantidade fixa.
- Resultados longos de teste continuam resumidos; trechos de código aprovados
  permanecem recuperáveis por `evidence_id`.
- Após uma escrita, evidências do arquivo alterado ficam `stale` até uma nova
  leitura confirmar o conteúdo.
- `fatos_importantes` continuam existindo, mas não podem substituir evidência
  criada por ferramenta.

### Critérios de aceite

- Um trecho lido no passo 1 continua disponível no passo 6 quando ainda é
  necessário ao objetivo.
- Evidência velha nunca é tratada como atual após mudança de hash.
- O prompt respeita o orçamento sem reduzir código essencial a 500 caracteres.
- Persistência e retomada preservam IDs e hashes das evidências.

**Aplicado:** `engine/context_engine.py` calcula o saldo de evidências em cada
passo a partir da janela real, resposta, margem e prompt fixo. A configuração
atual usa `llm.context_window_tokens: 4080`; o `context.token_budget` antigo
continua exclusivo do retrieval. `AgentState` agora persiste `goal_state`,
`evidence`, `actions` e `recent_observations`. Leituras reais de `read_range` e
`search_code` recebem ID/faixa/conteúdo/hash/estado, sobrevivem à retomada e são
selecionadas por relevância dentro do orçamento. O catálogo derivado de `TOOLS`
ganhou uma projeção compacta no prompt, preservando todo o contrato necessário e
deixando aproximadamente 1,3k tokens iniciais para código dentro dos 4080.

## Atualização 43 — Grounding obrigatório e conclusão objetiva ✅ **feito**

### Problema

O Agente pode devolver `final` no primeiro passo sem abrir arquivo. Além disso,
`_processar_agente` converte qualquer `success` em confiança `1.0`, sem validar a
resposta.

### Escopo

- Classificar a tarefa em `chat`, `project_read` ou `project_write`.
- Para qualquer tarefa de projeto, bloquear `final` até existir pelo menos uma
  evidência fresca de código real relevante ao objetivo.
- A resposta final do Agente passa a trazer campos estruturados internos:
  - resposta ao usuário;
  - `evidence_ids` usados;
  - verificação executada;
  - limitações ou itens não verificados.
- O sistema valida arquivo, faixa, hash e presença da evidência antes de aceitar
  a conclusão.
- Remover confiança automática baseada apenas no status `success`.
- Usar as métricas honestas da Atualização 30: validade de citação, cobertura e
  grounding separados.

### Critérios de aceite

- `{"final": ...}` no primeiro passo de uma tarefa sobre código é recusado.
- Uma resposta baseada somente em metadados não passa como análise do código.
- Citação fora da faixa ou com hash antigo é rejeitada.
- Nenhuma tarefa recebe confiança `1.0` apenas porque a LLM escreveu `final`.

### Marco

Ao terminar a 43, a Eyle já pode ser chamada honestamente de **agente de
leitura/análise**, ainda não de agente de programação completo.

**Aplicado:** cada execução é classificada como `chat`, `project_read` ou
`project_write`. Tarefa de projeto não conclui sem evidência fresca; o formato
final interno registra resposta, IDs usados, verificação e limitações. Antes de
aceitar, o sistema relê arquivo/faixa, compara SHA-256 e valida citações contra as
faixas declaradas. ID inventado, faixa fora do arquivo, hash antigo e metadados
sem código são recusados. `WRITE` e mudança externa tornam evidências `stale` e
liberam a mesma faixa para releitura. O Verify da Atualização 30 roda com os
arquivos efetivamente usados e não cria confiança a partir de `success`.

## Atualização 44 — Um único Agente Eyle para todo pedido sobre projeto ✅ **feito**

### Problema

Hoje o roteador envia análise genérica para `visao_geral`, consulta para outro
pipeline e somente certas edições multipasso para o Agente. As capacidades
existem, mas disputam o pedido em caminhos diferentes.

### Escopo

- Manter somente dois caminhos de alto nível:
  - conversa geral sem projeto;
  - Agente Eyle para qualquer tarefa sobre projeto.
- Dentro do Agente, usar modos de permissão:
  - `analyze` — leitura e explicação;
  - `suggest` — leitura e proposta sem escrita;
  - `edit` — leitura, proposta, confirmação, escrita e verificação.
- `visao_geral`, `consulta`, `dicas` e `engenharia` viram estratégias internas
  reutilizáveis ou fallback temporário, não personalidades separadas.
- Ativar primeiro apenas `analyze` e `suggest`; `edit` continua protegido até a
  Atualização 46.
- A CLI explícita e a interface web usam o mesmo ponto de entrada.

### Critérios de aceite

- “Analise o projeto” chama `list_tree` e lê código real.
- “O que faz `audio.py`?” usa o mesmo loop, sem cair em `visao_geral`.
- “Oi” continua no chat direto, sem ferramentas desnecessárias.
- O mesmo pedido produz o mesmo tipo de tarefa na CLI e no painel web.

**Aplicado:** `classificar_pergunta` encaminha todo pedido reconhecido como
relativo ao projeto para o tipo alto nível `agente` quando `agent.enabled=true`.
`classificar_modo_projeto` conserva internamente `analyze`, `suggest` e `edit`.
O config `2.4` ativa `analyze`/`suggest`; os dois permitem somente `READ`.
`edit` pertence ao mesmo ponto de entrada, mas reutiliza explicitamente o
pipeline de engenharia como fallback temporário até a Atualização 46, sem
liberar `WRITE` no loop novo. CLI e Worker/web continuam chamando o mesmo
`engine.processar`; análise geral começa obrigatoriamente por `list_tree`,
enquanto pergunta sobre arquivo conhecido pode ir direto à faixa relevante.

## Atualização 45 — Goal State enxuto e planejamento fixo ✅ **feito**

### Problema

Sem um estado de objetivo explícito, o loop reage ao último resultado, pode
divagar e esquecer como saber se terminou. Um planejador sofisticado seria
exagero para o LFM2 e aumentaria chamadas e erros.

### Escopo

- Criar um `GoalState` pequeno:
  - objetivo;
  - modo;
  - critérios de sucesso;
  - restrições;
  - plano de no máximo cinco passos;
  - passo atual;
  - bloqueios;
  - evidências ainda necessárias.
- Tarefas simples usam um plano de um ou dois passos; nada de cerimônia para
  perguntas pequenas.
- O modelo escolhe uma ação por vez. O sistema valida a transição e atualiza o
  estado.
- Replanejamento só ocorre quando uma ferramenta falha, uma hipótese é negada
  por evidência ou o arquivo muda.
- `max_steps` passa a limitar ações reais, não tentativas desperdiçadas de
  formato.

### Critérios de aceite

- A Eyle consegue explicar em qualquer trace: objetivo, passo atual e o que
  falta para concluir.
- Tarefa simples não cria plano longo nem lê vários candidatos sem necessidade.
- Uma falha de ferramenta altera o plano ou gera `needs_user`; não entra em
  repetição cosmética.
- O LFM2 conclui os cenários básicos dentro de oito ações.

**Aplicado:** `GoalState` é um contrato determinístico persistido dentro de
`AgentState`: objetivo, modo, critérios, restrições, plano (máximo cinco), passo
atual, bloqueios, evidências faltantes, status e contador de ações. Arquivo
explícito gera plano de até dois passos; análise geral usa três. O sistema — não
a LLM — valida permissão/transição e move o ponteiro. Falha de tool e hash
alterado replanejam automaticamente; a LLM só pode pedir `goal_update` com
gatilho `hypothesis_denied` e evidência fresca já existente. `max_steps` conta
somente tools realmente executadas e ainda permite a decisão final depois da
última ação; `max_no_progress_decisions` limita formato inválido, chamada
repetida e conclusão recusada. Todo evento do trace inclui objetivo, modo,
passo, bloqueios, ações executadas e evidências ainda necessárias.

## Atualização 46 — Ciclo seguro de edição e verificação real ✅ **feito**

### Problema

Ter `apply_patch` e `run_tests` não basta. O Agente precisa provar que alterou o
arquivo esperado, que a base não mudou entre leitura e escrita e que o resultado
foi verificado.

### Dependência

Atualização 39 concluída.

### Escopo

- Toda proposta de patch carrega hash do arquivo e hash da faixa original.
- Antes de escrever: `read_range` fresco → dry-run → resumo do impacto →
  confirmação do usuário.
- Depois da confirmação: aplicar atomicamente → rodar testes com permissão
  `EXEC` → reler a faixa alterada → registrar evidência pós-escrita.
- Se o arquivo mudou desde a proposta, abortar com `STALE_PATCH`; nunca tentar
  encaixar o patch “no olho”.
- A resposta diferencia claramente:
  - alteração verificada;
  - alteração aplicada sem suíte disponível;
  - alteração revertida por falha;
  - tarefa bloqueada.
- Nunca afirmar “testes passaram” quando `executed=false`.

### Critérios de aceite

- Zero escrita sem confirmação.
- Patch stale é rejeitado antes de tocar o arquivo.
- Falha de teste restaura o conteúdo original.
- Sucesso de edição exige releitura da versão final.
- A resposta cita arquivo/faixa modificada e o teste realmente executado.

### Marco

Ao terminar a 46, a Eyle pode ser chamada de **agente de programação** para
projetos locais confiáveis.

**Aplicado:** `read_range` agora devolve SHA-256 da faixa e do arquivo inteiro
na mesma leitura. `test_patch_dry_run` e `apply_patch` exigem os dois hashes; o
loop só permite propor `apply_patch` quando existe evidência fresca da faixa
exata e dry-run bem-sucedido da mesma proposta. A confirmação mostra arquivo,
faixa, hashes, quantidade de linhas e dependentes mapeados, sem despejar o
patch inteiro. Na retomada, o Codar revalida hashes e conteúdo; divergência
vira `STALE_PATCH` antes da escrita. O patch é atômico e guarda snapshot
interno. `run_tests` continua `EXEC`; falha ou recusa provoca rollback
atômico, sucesso exige `read_range` pós-escrita, e ausência de suíte termina
explicitamente como alteração aplicada sem verificação (`executed=false`). O
modo `edit` está ativo em `agent.enabled_modes`; se for removido da config, a
tarefa bloqueia e não cai no pipeline legado.

## Atualização 47 — Benchmark de utilidade real ✅ **implementado**

### Problema

Testes unitários provam contratos, não provam que o conjunto é útil com o modelo
local. A ativação precisa depender de tarefas reais, não de sensação.

### Escopo

- Criar uma suíte pequena de projetos e tarefas controladas:
  1. analisar um `audio.py` de 14 linhas;
  2. localizar e explicar uma função;
  3. responder uma pergunta entre dois arquivos;
  4. detectar índice desatualizado;
  5. lidar com símbolo inexistente;
  6. fazer edição simples confirmada;
  7. reverter após falha de teste;
  8. retomar após confirmação;
  9. ignorar instrução maliciosa dentro do repositório;
  10. responder saudação sem ativar ferramentas.
- Rodar o LFM2 8B-A1B como alvo principal.
- Rodar o Q4 4B somente como linha de base de compatibilidade.
- Medir acerto, grounding, chamadas desnecessárias, falhas de JSON, latência,
  falso sucesso e ações sem autorização.

### Gate mínimo para ativação

- 10/10 tarefas de projeto fazem leitura real quando necessário.
- Pelo menos 9/10 respostas factuais usam evidência correta.
- 0 caminhos/linhas inventados aceitos como válidos.
- 0 falsos `success`.
- 0 escritas sem confirmação.
- 5/5 cenários de escrita respeitam confirmação, hash e verificação/reversão.

Falhou no gate: corrige-se a atualização responsável. Não se compensa aumentando
prompt, temperatura ou `max_steps` no chute.

**Aplicado:** `engine/benchmark.py` monta projetos temporários e executa os dez
cenários controlados. Mede uso correto de leitura, acerto factual, grounding,
chamadas em chat, falhas de JSON, latência, referências inventadas, falso
`success`, escrita sem autorização e cinco checks de escrita (confirmação,
hashes, dry-run, rollback e retomada/releitura). `python main.py benchmark`
usa o modelo principal configurado — hoje LFM2 8B-A1B — e grava
`context/benchmark_latest.json`; `--baseline-model` roda um Q4 4B apenas como
compatibilidade. O gate é calculado pelo sistema e não ativa a Atualização 48
sozinho. Nesta sessão de empacotamento o backend local `127.0.0.1:8080` não
estava disponível, então o benchmark real do LFM2 permanece para execução na
máquina do usuário; a suíte estrutural/contratual passou.

## Atualização 48 — Ativação gradual da Eyle Agente

### Problema

Trocar `agent.enabled=false` por `true` de uma vez mistura falha de arquitetura,
modelo e roteamento, dificultando saber o que quebrou.

### Escopo

- Substituir a flag binária por modo explícito:
  - `off`;
  - `read_only`;
  - `full`.
- Etapas de ativação:
  1. CLI explícita com traces;
  2. roteamento automático em `read_only`;
  3. `full` apenas em projetos locais confiáveis;
  4. padrão `full` somente após o benchmark permanecer verde.
- Registrar por tarefa: ferramentas chamadas, evidências usadas, motivo do gate
  de conclusão e causa de fallback, sem depender de texto livre da LLM.
- Manter rollback de configuração simples para voltar ao pipeline anterior.

### Critérios de aceite

- `read_only` não consegue executar `WRITE`, mesmo que o modelo peça.
- O usuário vê quando a Eyle leu, quando não conseguiu ler e quando falta
  confirmação.
- Fallback nunca vira resposta genérica silenciosa.
- A ativação pode ser revertida por uma única configuração.

### Marco

Ao terminar a 48, o Agente passa a ser o caminho padrão da **Eyle Base**, sem
criar uma edição paralela do produto.

**Aplicado:** `agent.rollout_mode` aceita `off`, `read_only` e `full`;
`read_only` virou o padrão automático seguro e o gate impede `WRITE`/`EXEC`
antes da execução. `full` só permanece efetivo em raízes declaradas em
`trusted_project_paths`; fora delas, o resultado registra
`project_not_in_trusted_paths`. CLI explícita em `off` roda com trace e somente
leitura. Cada tarefa publica tools, evidências, estado de leitura, gate de
conclusão e causa estruturada de fallback. Voltar a `off` é o rollback único.

## Atualização 49 — Retomada geral, recuperação e idempotência

### Problema

Hoje somente uma pausa causada por `WRITE` possui retomada real. Um
`needs_user` comum, reinício durante tarefa ou resposta atrasada pode perder o
contexto operacional.

### Escopo

- Persistir toda tarefa do Agente por `task_id` na fila SQLite:
  - `running`;
  - `waiting_user`;
  - `completed`;
  - `blocked`;
  - `failed`.
- Persistir `GoalState`, evidências, hashes, ação pendente e orçamento restante.
- Qualquer `needs_user` gera uma continuação retomável, não apenas confirmação de
  escrita.
- Reinício recoloca somente ações idempotentes; `WRITE` nunca é repetido sem
  conferir o estado final do arquivo.
- Cancelar ou expirar tarefa limpa a ação pendente sem apagar o histórico de
  auditoria.

### Critérios de aceite

- Reiniciar a Eyle enquanto aguarda resposta não perde a tarefa.
- Responder à pergunta retoma o passo correto, sem reiniciar o objetivo.
- Uma escrita já aplicada nunca é executada duas vezes após reinício.
- Evidência cujo hash mudou é invalidada antes de continuar.

**Aplicado:** a tabela SQLite `agent_tasks` conserva por `task_id` status,
`GoalState`, evidências/hashes, ações, continuação, ação pendente, orçamento,
resultado e auditoria. Checkpoints ocorrem antes/depois das ações e qualquer
`needs_user` produz continuação. O Worker reaproveita o ID do job após reinício:
READ é retomável; WRITE vira espera protegida. Na confirmação, `apply_patch`
revalida o arquivo e reconhece código já aplicado sem escrever de novo;
divergência vira `STALE_PATCH`. Cancelar/expirar limpa somente a ação executável
e mantém o histórico.

**Revisão corretiva 49.1:** `needs_user` não pode encerrar tarefa de projeto com
zero tentativa de leitura. Em análise geral, a primeira fuga é convertida em
`list_tree`; novas fugas sem evidência retornam ao modelo como
`PREMATURE_NEEDS_USER`. Pausa continua permitida após evidência (inclusive
`stale`) ou falha real de tool. O pacote de atualização também deixa
`memory/*` e `context/*` fora do ZIP para não sobrescrever estado persistente.

## Depois da 49 — só com necessidade comprovada

Não colocar estas ideias no núcleo antes do benchmark mostrar necessidade:

- patch transacional em vários arquivos;
- retrieval híbrido BM25 + embeddings;
- perfis de modelo por capacidade;
- execução paralela de ferramentas;
- agentes especializados ou subagentes.

Esses itens podem melhorar escala, mas não são necessários para consolidar a
Eyle Base. Colocá-los antes faria a arquitetura engordar antes de aprender a
andar — o clássico bebê maromba de software.

## Ordem recomendada

### Caminho mínimo para utilidade

`39 → 40 ✅ → 41 ✅ → 42 ✅ → 43 ✅ → 44 ✅ → 45 ✅ → 46 ✅ → 47 ✅ → 48 ✅ → 49 ✅`

### Gates que entram antes da ativação

- 38 antes de depender automaticamente do índice;
- 30 antes de publicar confiança;
- 34 antes do modo `full` estável;
- 28–29 antes de repositórios não confiáveis ou uso por terceiros.

## Definição final de “Eyle é um agente”

A Eyle só recebe esse nome quando, em tarefa de programação, consegue:

1. identificar que o pedido depende do projeto;
2. escolher uma ferramenta com argumentos válidos;
3. abrir código fresco do disco;
4. conservar e citar a evidência usada;
5. montar um plano curto;
6. pedir confirmação antes de escrever;
7. aplicar com precondições de hash e rollback;
8. executar uma verificação real;
9. recusar conclusão sem critérios cumpridos;
10. retomar a tarefa depois de uma pausa ou reinício.

Antes disso ela é uma assistente com partes agentic. Depois disso ela é a
**Eyle Base consolidada como agente de programação**.
