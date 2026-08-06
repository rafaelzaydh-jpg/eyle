<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle — agente autônoma de código" width="100%">
</p>

<p align="center"><strong>Uma agente autônoma de código, um único caminho de execução. Escritas continuam supervisionadas.</strong></p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="docs/architecture.md">Arquitetura</a> ·
  <a href="docs/configuration.md">Configuração</a> ·
  <a href="docs/benchmark.md">Benchmark</a> ·
  <a href="CHANGELOG.md">Histórico</a>
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white">
  <img alt="Versão 2.7.4" src="https://img.shields.io/badge/versão-2.7.4-2563EB">
  <img alt="Testes" src="https://img.shields.io/badge/testes-362%20aprovados-16A34A">
</p>

**Versão:** 2.7.4 · **Schema:** 2.7.4 · **Revisão:** 4.6-token-efficiency

## O que mudou na 2.7.4

A Rev4.6 remove o maior desperdício ativo de tokens sem enfraquecer as garantias de preservação da Rev4.5. `entendimento.json` e inventários completos não entram mais nos prompts, auditorias usam planejamento inicial/de lacunas determinístico, uma auditoria normal gasta apenas a chamada do Finalizer e existe no máximo uma expansão compacta para uma lacuna realmente ambígua. Orçamentos de prompt, resposta e total são verificados antes de cada request; retries são contabilizados; ferramentas e histórico são filtrados pelo estado atual; e `compare-efficiency` detecta regressões de tokens por caso entre releases.

A Eyle agora possui um único pipeline de projeto. Os caminhos históricos Retrieval → Analista → Executor → Verify e seus fallbacks ocultos foram removidos. Um pedido sobre o projeto passa pela agente Eyle ou termina com uma falha específica; nunca é redirecionado silenciosamente para outra arquitetura.

```text
Pedido do usuário
→ agente Eyle
→ ferramentas validadas
→ evidências frescas
→ confirmação para escrita quando necessária
→ testes e releitura
→ resposta validada
```

O BM25 permanece disponível como **ferramenta de busca**, não como pipeline decisório separado. A memória indexada serve apenas como pista de navegação; afirmações atuais ainda exigem leitura fresca.

Na revisão 2, leituras comuns passaram a terminar em `claims[]` estruturadas antes da resposta ser renderizada. Em Windows, testes podem usar o modo opt-in `trusted_local`, limitado à allowlist e executado em cópia temporária do projeto.

Na revisão 3, a Eyle extrai um contrato mínimo de alvos do pedido, bloqueia conclusões incompletas, permite somente um reparo direcionado e finaliza leituras explícitas sem gastar uma chamada intermediária apenas para dizer `ready_to_finalize`.

Na revisão 4, a Eyle assume explicitamente a identidade de uma única agente autônoma de código. Uma intenção determinística escolhe o perfil da resposta (`analyze`, `explain`, `review`, `suggest`, `investigate`, `discuss` ou `edit`), bloqueia recomendações não solicitadas, exige escopo explícito em claims de ausência e monta o recibo final de escrita diretamente do estado verificado de patch/testes/releitura, sem outra chamada ao modelo.

Na revisão 4.1, esse contrato foi endurecido com base nos testes reais: substantivos naturais como “criação” não acionam mais edição, pedidos de melhorias sobre o projeto inteiro mantêm a auditoria completa, quantidades exatas de recomendações são validadas sem exigir uma seção de problemas não solicitada, falhas de ferramenta exibem código/detalhe/política de retry e uma escrita confirmada sem suíte termina aplicada com verificação parcial após releitura fresca.

## Capacidades centrais

- Analisar projetos e explicar arquivos, símbolos, relações, riscos e estrutura.
- Criar ou editar código com ferramentas validadas e confirmação explícita.
- Escrita atômica, hashes, dry-run, testes, releitura e rollback.
- Estado persistente, fila, checkpoints, CLI e interface Flask opcional.
- Backends compatíveis com OpenAI, Ollama, llama.cpp e LM Studio.
- Registro do modelo resolvido, uso de tokens, reasoning e `finish_reason`.
- Cobertura de auditoria e claims estruturadas para conclusões sustentadas por evidência.

## Início rápido

```bash
python ingest.py /caminho/do/projeto --nome "Meu projeto"
python main.py status
python main.py serve
```

Para uma tarefa direta pela CLI:

```bash
python main.py agent "Faça uma análise do projeto"
```

Mesmo com `agent.rollout_mode` em `full`, escritas continuam supervisionadas:

```json
{
  "agent": {
    "rollout_mode": "full",
    "require_confirmation_for_write": true
  }
}
```

## Regra de projeto

O modelo é o cérebro de raciocínio. O código determinístico controla permissões, schemas das ferramentas, limites do workspace, frescor das evidências, confirmação, escrita atômica, testes, rollback, prazos e status terminal.

Veja [Arquitetura](docs/architecture.md) para o fluxo e [Configuração](docs/configuration.md) para as opções suportadas.
