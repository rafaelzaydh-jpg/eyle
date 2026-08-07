<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle — agente autônoma de programação" width="100%"></p>
<p align="center"><strong>Um cérebro LLM. Ferramentas reais. Escrita supervisionada. Execução observável.</strong></p>

**Versão:** 2.7.4 · **Schema:** 4.12.4.1 · **Revisão:** 4.12.4.1-context-budget-hardening

A Eyle é uma agente local de programação construída em torno de uma ideia simples: a LLM decide o que fazer, ferramentas determinísticas medem e executam a realidade, e o runtime protege apenas os limites que não podem depender de adivinhação.

## Por que a Eyle existe

Ela foi pensada para repositórios reais, inclusive projetos grandes demais para caber em um único prompt. A Eyle não despeja o projeto inteiro no contexto e não usa um comitê de agentes. Ela investiga somente o necessário para a tarefa, preserva as evidências úteis e consegue alterar vários arquivos numa única transação supervisionada.

```text
usuário
→ AgentSession
→ decisão da LLM
↔ ferramentas determinísticas / workspace real / memória externa sob demanda
→ dry-run + confirmação para escrita
→ compile/testes/releitura/rollback
→ resposta
```

## Rev4.12.4.1: endurecimento do orçamento de contexto

A Rev4.12.4.1 mantém a taxonomia compartilhada da Rev4.12.4 e eleva a janela padrão por chamada de 10k para 32k. O orçamento cumulativo efetivo de prompt por tarefa passa a 96k, enquanto cada chamada continua obrigada a caber na janela real do modelo. Análises usam reserva de saída estável por tipo de trabalho, fases analíticas não recebem dry-run de patch automaticamente, e `agent_info` separa o registro completo das tools do subconjunto disponível na fase atual. Falhas de pytest em estilo Windows continuam sendo reportadas como falhas reais, e `execution_trace` é testada dentro de uma investigação multi-tool real.

## Rev4.12.4: taxonomia compartilhada de tools

A Rev4.12.4 mantém o modelo de execução da Rev4.12.3.1 e a `execution_trace`, mas compacta como as ferramentas são descritas para a LLM. O runtime continua expondo de uma vez todas as tools permitidas pela fase executável atual e a própria LLM continua escolhendo qual usar; não foi criado roteador de categoria nem chamada extra ao modelo.

A autoridade compartilhada é declarada uma única vez por chamada em duas categorias: `READ_ONLY` (sem alteração persistente intencional em arquivos do projeto ou memória do projeto) e `EDIT` (pode persistir arquivos ou memória). Os efeitos também viraram tags compartilhadas: `NONE` por padrão, além de `EXEC`, `TEMP`, `MEMORY_WRITE`, `WORKSPACE_WRITE`, `VERIFY` e `ROLLBACK` quando aplicável. Cada contrato individual mantém apenas finalidade, assinatura compacta dos argumentos, retorno, ressalvas específicas e limites numéricos configurados.

Isso remove repetições como “does not modify files” e `side_effects: none` sem enfraquecer os limites das tools. No catálogo completo de 20 ferramentas, catálogo + taxonomia caem de 12.492 caracteres na Rev4.12.3.1 para cerca de 10.241; numa investigação normal com 15 tools, de 8.353 para cerca de 7.049 caracteres na mesma medição.

A `execution_trace` continua sendo a única tool de auto-observabilidade: ela expõe fatos sanitizados de fases/contexto/tokens/tools/decisões/validações, não diagnósticos, chain-of-thought, prompts brutos, corpos de fonte/patch/memória ou segredos.

## Raciocínio assistido por ferramentas

A LLM não precisa fazer tudo “de cabeça”. A Eyle pode usar:

- `calculate` — cálculo decimal determinístico;
- `project_stats` — arquivos, linhas, caracteres, bytes e linguagens;
- `count_tokens` — tamanho medido com indicação explícita de contagem exata ou heurística;
- `inspect_project` — sinais objetivos de entrypoints, imports, rotas, testes, CI e frameworks, sem declarar qual arquivo é “importante”;
- `search_code`, `read_file`, `read_range`, `find_symbol`, `list_tree` — inspeção do workspace real;
- `agent_info` — identidade atual e ferramentas realmente disponíveis;
- `run_tests` — execução real em sandbox, com escopo pytest opcional e saída diagnóstica limitada;
- `git_status` — estado do working tree em modo somente leitura;
- `git_diff` — diff somente leitura com tamanho limitado;
- `execution_trace` — fatos sanitizados da execução atual/jobs persistidos para self-debugging;
- memória externa somente quando a própria LLM decide consultar ou armazenar algo.

A ferramenta observa. A Eyle interpreta a observação conforme a tarefa. Resultados determinísticos como `calculate` viram evidência real, mas a resposta final continua sendo escrita pela LLM para preservar tom, explicação e personalidade.

## Escrita supervisionada

```text
pedido
→ ler o código necessário
→ gerar uma transação
→ dry-run
→ confirmação do usuário
→ aplicar
→ compileall dos Python alterados
→ detectar e executar testes
→ rollback se compile/testes/releitura falharem
→ reler e confirmar a saída real
→ informar honestamente se foi verificado ou parcialmente validado
```

Tarefas comuns de escrita usam fases. Depois do orçamento de investigação, leituras são fechadas e o próximo turno fica reservado ao patch. Leituras equivalentes também são bloqueadas quando já existe evidência fresca.

## Qualidade factual

Fatos sobre o projeto, bugs confirmados e riscos contextuais precisam nascer de observações reais. O runtime mantém um ledger compacto entre afirmações e evidências e aplica limites explícitos como “até 3”. As claims apontam para o número da frase visível, sem repetir o texto completo dentro do protocolo.

## Estrutura

```text
eyle/core/       AgentSession, tools, inspeção, memória e edição segura
eyle/runtime/    serviço, fila, worker, persistência, telemetria e histórico público
llm/             transporte, normalização e contabilidade de tokens
web/             chat Flask e histórico expansível
docs/            arquitetura, configuração, releases e notas de engenharia
```

## Uso

```bash
python main.py status
python main.py perguntar "Analise o projeto"
python main.py serve
```

Os endpoints de dados da interface usam Bearer token. O comando `serve` informa no terminal onde obter o token local da API.

## Validação

- 178 testes passam na suíte determinística empacotada;
- 1 teste opcional da interface é pulado quando Flask não está instalado no ambiente de empacotamento;
- o smoke real com Qwen continua sendo executado apenas no ambiente de deploy.

## Licença

A Eyle tem o **código-fonte disponível, mas não é software open source**. A licença permite que pessoas baixem, instalem, executem e modifiquem a Eyle de forma privada para uso pessoal e não comercial. Redistribuição, publicação de cópias ou versões modificadas, venda, sublicenciamento, uso comercial e oferta da Eyle como serviço exigem autorização prévia por escrito.

Consulte [LICENSE.md](LICENSE.md) para os termos que regem o software e [CONTRIBUTING.md](CONTRIBUTING.md) para os termos de contribuição. Os direitos limitados decorrentes do próprio uso do GitHub continuam sujeitos aos Termos de Serviço do GitHub.

## Documentação

- [Arquitetura](docs/architecture.md)
- [Visão técnica](docs/technical-overview.md)
- [Configuração](docs/configuration.md)
- [Benchmark](docs/benchmark.md)
- [Notas da Rev4.12.4.1](docs/releases/2.7.4-rev4.12.4.1.md)
- [Notas da Rev4.12.4](docs/releases/2.7.4-rev4.12.4.md)
- [Notas da Rev4.12.3.1](docs/releases/2.7.4-rev4.12.3.1.md)
- [Notas da Rev4.12.3](docs/releases/2.7.4-rev4.12.3.md)
- [Notas da Rev4.12.2](docs/releases/2.7.4-rev4.12.2.md)
- [Histórico de decisões removidas](UPDATE_HISTORY.md)
- [Changelog](CHANGELOG.md)
