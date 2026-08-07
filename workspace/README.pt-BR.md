<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle — agente autônoma de programação" width="100%"></p>
<p align="center"><strong>Uma agente de programação com um único cérebro LLM, ferramentas reais e escrita supervisionada.</strong></p>

**Versão:** 2.7.4 · **Schema:** 4.11.7 · **Revisão:** 4.11.7-sentence-markdown-directory-flow

## Arquitetura

```text
Interface
→ runtime service
→ AgentSession
→ LLM
↔ ferramentas
↔ memória externa sob demanda
→ validação factual
→ resposta
```

A mesma LLM conversa, interpreta, planeja quando necessário, investiga, escreve código e produz a resposta. Não existe outro agente preparando a missão ou julgando a conclusão.

O runtime não tenta pensar pela LLM. Ele controla somente fatos executáveis:

- caminhos seguros e limites de leitura;
- contratos das ferramentas;
- hashes das evidências;
- dry-run e confirmação antes da escrita;
- alterações atômicas e transações multi-arquivo;
- compileall pós-escrita, testes detectados, rollback transacional, releitura integral e diagnóstico exato das falhas;
- validação factual por referência numérica às frases, separação entre bug/risco/recomendação e limites “até N”;
- Markdown seguro na interface e evidência estrutural fresca para perguntas sobre pastas;
- prazo, chamadas, tokens totais/cacheados/efetivos, fila, cancelamento e telemetria.

## AgentSession

O estado da tarefa contém apenas:

- pedido original;
- plano opcional criado pela própria LLM;
- último resultado das ferramentas;
- últimos trechos de código relevantes, com limite de tamanho;
- índice compacto das evidências;
- mapa interno entre afirmações finais e evidências, usando o número da frase visível sem duplicar seu texto; claims textuais antigas continuam compatíveis;
- relatório estruturado da última escrita confirmada que falhou, quando existir;
- fase atual, progresso semântico e contadores de turnos/ferramentas;
- proposta pendente quando houver escrita.

A sessão usa fases explícitas. Em escrita comum, a LLM pode investigar por até dois turnos; o próximo fica restrito ao patch. Leituras já cobertas por uma leitura integral ou faixa maior são bloqueadas mesmo quando a ferramenta ou a faixa muda. Dois turnos sem nova evidência encerram a investigação em vez de alimentar o loop.

## Memória externa

A memória nunca entra automaticamente no prompt. A LLM consulta `memory_search` quando isso ajuda e usa `memory_store` somente com evidências atuais. Entradas ligadas a arquivos são descartadas quando o hash deixa de corresponder.

## Edição

```text
pedido
→ investigação e patch pela LLM
→ dry-run
→ confirmação do usuário
→ aplicação da transação
→ compileall dos arquivos Python alterados
→ detecção e execução de testes existentes ou recém-criados
→ quando falhar, exibição da saída real e rollback completo
→ preservação do relatório para perguntas posteriores
→ releitura de todos os arquivos e confirmação de criações/exclusões
→ resposta final com estado honesto de verificação
```

Depois da confirmação, nenhuma chamada LLM é necessária.

## Estrutura

```text
eyle/core/       AgentSession, ferramentas, memória e edição segura
eyle/runtime/    serviço, fila, worker, persistência e telemetria
llm/             transporte e adaptação do backend
web/             interface Flask
```

## Uso

```bash
python main.py status
python main.py perguntar "Analise o projeto"
python main.py serve
```

## Validação

- 136 testes passam na suíte de validação empacotada;
- 1 teste opcional da interface foi pulado porque Flask não está instalado no ambiente de empacotamento;
- o smoke real com Qwen ainda precisa ser executado no ambiente final.

Veja [Arquitetura](docs/architecture.md), [Configuração](docs/configuration.md), [Qualidade factual](docs/rev4114-factual-response-quality.md), [Verificação pós-escrita](docs/rev4113-post-write-verification.md) e [Changelog](CHANGELOG.md).
