<p align="center">
  <img src="assets/eyle-banner.svg" alt="Eyle" width="760">
</p>

# Eyle

**Versão:** 2.7.4 · **Schema:** 5.1 · **Revisão:** rev5.1-context-boundaries-investigation-continuity

A Eyle é um agente de código local-first construído em torno de uma única `AgentSession`, tools determinísticas, Evidence mantida pelo runtime, escrita transacional supervisionada e um Claim Review semântico antes da aceitação de respostas fundamentadas no projeto.

> **A LLM decide semântica. O runtime valida contratos.**

## Por que a Eyle existe

A Eyle permite que a LLM conectada investigue e raciocine sobre um workspace real sem transformar o runtime em um segundo agente escondido. O runtime controla segurança, estrutura, budgets, hashes, freshness, confirmação, persistência e validação. A LLM controla escolhas de investigação, interpretação semântica, redação e intenção de patch.

O core é independente de provider. Qwen, Llama e outros modelos compatíveis usam o mesmo protocolo da Eyle; somente a fronteira `llm/` se adapta à capacidade de structured output que a conexão realmente entrega.

## Arquitetura

```text
interface
→ runtime/service
→ AgentSession
→ handshake estruturado administrativo
→ LLM principal ↔ 16 tools determinísticas + workspace real
→ Evidence Core
→ Final Gate determinístico
→ Claim Review (único 2FA semântico)
   ├─ supported → resposta
   ├─ contradicted → Repair local → Reverify
   └─ insufficient / semantic gap → nova investigação dirigida
```

O handshake administrativo não é uma tool do agente. Ele verifica por comportamento `json_schema`, depois `json_object`, depois JSON guiado por prompt, e salva o modo comprovado por conexão/modelo. A Eyle nunca confia apenas no enforcement do servidor: toda saída estruturada é validada localmente.

## Tools

A LLM principal recebe atualmente 16 tools públicas:

`calculate`, `agent_info`, `project_stats`, `count_tokens`, `inspect_project`, `list_tree`, `search_code`, `find_symbol`, `read_range`, `read_file`, `memory_search`, `memory_store`, `run_tests`, `execution_trace`, `git_status` e `git_diff`.

A escrita não é exposta como patch tools. O modelo emite o protocolo canônico `action=patches` e o runtime executa um único caminho transacional.

## Escrita supervisionada

```text
pedido
→ investigar código
→ action=patches
→ dry-run transacional
→ confirmação do usuário
→ aplicar transação
→ compile/testes/releitura
→ rollback em falha de validação
→ resposta verificada
```

## Evidence e Claim Review

A Evidence completa permanece no runtime. A LLM recebe visões limitadas e pode pedir ranges mais profundos. Claims e Evidence são proporcionais ao conteúdo material da resposta: números como ~6, 12 ou 20+ são orientação, nunca quotas.

Claim Review é o único verificador semântico final. Ele verifica Claims atômicas e Semantic Gaps da conclusão. A recuperação local preserva o Review válido e reavalia somente a Claim ou Semantic Gap malformado; o runtime nunca inventa verdict, tipo de gap, Evidence ou correção semântica.

## Uso

Instale as dependências:

```bash
python -m pip install -r requirements.lock
```

Comandos úteis:

```bash
python main.py status
python main.py perguntar "Analise o projeto"
python main.py serve
```

Para desenvolvimento:

```bash
python -m pip install -r requirements-dev.lock
python -m pytest -q
```

## Configuração

Edite `config.json` para endpoint da LLM, modelo e limites do runtime. Não é necessário configurar manualmente o tipo de structured output por provider: a Eyle testa o comportamento real e salva o resultado local em `context/llm_capabilities.json`, ignorado pelo Git.

Veja [Configuração](docs/configuration.md).

## Estrutura do projeto

```text
eyle/core/       AgentSession, tools, Evidence, Claim Review e edição segura
eyle/runtime/    serviço, fila, worker, persistência, telemetria e histórico
llm/             transporte, capacidades adaptativas e contratos estruturados
web/             interface web local
tests/           suíte de regressão canônica
docs/            arquitetura, configuração, benchmarks e publicação atuais
```

## Validação

A Rev5.1 deve ser publicada somente depois que o artefato extraído passar:

```bash
python -m eyle.devtools.release_identity
python -m compileall -q .
python -m pytest -q
node --check web/static/app.js
```

Veja [Benchmark](docs/benchmark.md) para o cenário real de aceitação da AgentSession.

## Licença

A Eyle tem **código-fonte disponível, mas não é software open source**. Uso pessoal, privado e não comercial é permitido conforme [LICENSE.md](LICENSE.md). Redistribuição, publicação de versões modificadas, uso comercial, sublicenciamento, venda ou oferta da Eyle como serviço exigem autorização prévia por escrito.

Veja também [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentação

- [Arquitetura](docs/architecture.md)
- [Visão técnica](docs/technical-overview.md)
- [Configuração](docs/configuration.md)
- [Benchmark](docs/benchmark.md)
- [Publicação no Git](docs/github-publishing.md)
- [Changelog](CHANGELOG.md)
- [English](README.md)
