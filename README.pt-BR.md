<p align="center"><img src="assets/eyle-banner.svg" alt="Eyle — agente autônoma de programação" width="100%"></p>
<p align="center"><strong>Uma agente de programação com um único cérebro LLM, ferramentas reais e escrita supervisionada.</strong></p>

**Versão:** 2.7.4 · **Schema:** 4.11.2 · **Revisão:** 4.11.2-write-loop-fix

## Arquitetura

```text
Interface
→ runtime service
→ AgentSession
→ LLM
↔ ferramentas
↔ memória externa sob demanda
→ resposta
```

A mesma LLM conversa, interpreta, planeja quando necessário, investiga, escreve código e produz a resposta. Não existe outro agente preparando a missão ou julgando a conclusão.

O runtime não tenta pensar pela LLM. Ele controla somente fatos executáveis:

- caminhos seguros e limites de leitura;
- contratos das ferramentas;
- hashes das evidências;
- dry-run e confirmação antes da escrita;
- alterações atômicas e transações multi-arquivo;
- testes, rollback e releitura;
- prazo, chamadas, tokens, fila, cancelamento e telemetria.

## AgentSession

O estado da tarefa contém apenas:

- pedido original;
- plano opcional criado pela própria LLM;
- último resultado das ferramentas;
- índice compacto das evidências;
- contadores de turnos e ferramentas;
- proposta pendente quando houver escrita.

A repetição protegida é somente a mesma chamada exata várias vezes seguidas. O runtime não tenta decidir se duas investigações diferentes “significam a mesma coisa”.

## Memória externa

A memória nunca entra automaticamente no prompt. A LLM consulta `memory_search` quando isso ajuda e usa `memory_store` somente com evidências atuais. Entradas ligadas a arquivos são descartadas quando o hash deixa de corresponder.

## Edição

```text
pedido
→ investigação e patch pela LLM
→ dry-run
→ confirmação do usuário
→ aplicação
→ testes quando habilitados
→ rollback em falha
→ releitura
→ resposta final
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

- 94 testes passam na suíte de validação empacotada;
- 1 teste opcional da interface foi pulado porque Flask não está instalado no ambiente de empacotamento;
- o smoke real com Qwen ainda precisa ser executado no ambiente final.

Veja [Arquitetura](docs/architecture.md), [Configuração](docs/configuration.md), [Correção do loop de escrita](docs/rev4112-write-loop-fix.md) e [Changelog](CHANGELOG.md).
