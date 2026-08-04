# Eyle — memória externa + retrieval seletivo para LLMs locais pequenas

**Versão:** 2.7.3 · **Schema:** 2.7.3 · **Revisão:** 53.0-speed-cycle-hardening

Implementação funcional da ideia:

```
Projeto de 30k-100k+ tokens
        |
        v
Memória externa (memory/, sem limite prático)
        |
        v
Retrieval seletivo (BM25, 100% offline)
        |
        v
Contexto virtual enviado à LLM (saldo calculado por chamada)
        |
        v
Resposta + verificação contra a memória
```

A LLM nunca vê o projeto inteiro. Ela recebe só os pedaços mais relevantes
para a pergunta feita. No Agente, o saldo para código é calculado a partir da
janela real do backend; a configuração atual usa **8192 tokens** no total. O
projeto pode ter 30k, 100k ou mais tokens porque conteúdo lido fica como
evidência externa e apenas a seleção que cabe entra em cada passo.

O projeto real a ser analisado entra pela pasta `workspace/` (veja
"Usando com o SEU projeto" abaixo) — ela funciona como um container:
tudo o que for colocado lá dentro, em qualquer nível de subpasta, é
lido e indexado.

## Estrutura

```
eyle-base/
├── config.json              # endpoint da LLM local + orçamento de tokens
├── main.py                  # CLI principal (ingest / perguntar / agente / status / serve)
├── ingest.py                # varre o projeto; Python usa AST e preserva o preâmbulo
├── workspace/                # CONTAINER — coloque aqui a raiz do projeto real
├── memory/                  # MEMÓRIA EXTERNA — fica fora da LLM
│   ├── projeto.json         # identidade/resumo do projeto indexado
│   ├── estrutura.json       # mapa de arquivos, funções e classes
│   ├── chunks.jsonl         # conteúdo real, dividido em pedaços pequenos
│   ├── entendimento.json    # Modelo Interno do Projeto: componentes (heurístico) + arquivos (via LLM)
│   ├── evidencias.json      # entidades (função/classe) -> onde é definida/usada
│   ├── decisoes.json        # decisões arquiteturais registradas manualmente
│   ├── conversa.json        # histórico de mensagens (persistente, independe do navegador)
│   └── historico.json       # log de decisões/interações (com versão/data/hash)
├── context/
│   ├── atual.json           # o que a LLM efetivamente recebe agora (retrieval)
│   ├── cache_llm.sqlite3    # cache indexado por hash; migra cache_llm.json legado
│   ├── proposta_pendente.json  # proposta com ID/expiração aguardando confirmação
│   ├── agent_pendente.json     # compatibilidade: pendência legada anterior à versão 2.6
│   ├── fila.sqlite3            # jobs, tarefas, heartbeats e auditoria
│   ├── telemetry.sqlite3       # latência/erros de LLM, tools e jobs (P50/P95/P99)
│   ├── llm_limiter.sqlite3     # limite de concorrência entre processos
│   ├── web_api_token.txt        # segredo aleatório da API (gerado localmente, modo 0600)
│   └── backups/              # cópia do arquivo original antes de cada patch aplicado de verdade
├── retrieval/
│   └── buscar.py            # BM25 puro em Python (sem dependências)
├── engine/
│   ├── engine.py             # orquestra o ciclo completo + roteamento + Atualização 5
│   ├── roteador.py           # separa conversa livre do Agente e define analyze/suggest/edit
│   ├── compiler.py           # monta todos os prompts a partir da memória
│   ├── context_engine.py     # orçamento dinâmico + seleção de evidências por ID
│   ├── entender.py           # gera entendimento.json['arquivos'] via LLM, uma vez por arquivo (cache por hash)
│   ├── dicas.py              # usa entendimento.json['arquivos'] pra escolher componentes e ler o codigo real deles (sugestoes)
│   ├── seguranca.py          # resolve caminhos dentro da raiz e bloqueia traversal/symlink externo
│   ├── agent_tools.py        # registro executável, schemas, permissões e validação central
│   ├── project_reader.py     # árvore fresca + leitura segura/numerada por faixa
│   ├── sandbox.py            # isola testes, bloqueia rede e aplica allowlist/limites
│   ├── config_schema.py      # erros fatais + avisos operacionais separados
│   ├── grounding.py          # prova objetiva de afirmações contra evidências
│   ├── process_limiter.py    # semáforo LLM entre processos com lease recuperável
│   ├── telemetry.py          # métricas persistentes e percentis
│   ├── persistencia.py       # publicação atômica de JSON/JSONL/texto
│   ├── retencao.py           # limites de histórico, cache, traces e backups
│   ├── codar.py              # localiza/testa/aplica patch com troca atômica e rollback independente de backup
│   ├── queue.py               # fila FIFO persistente em SQLite
│   └── worker.py              # consumidores paralelos + processo filho terminável
├── llm/
│   ├── executar.py          # chama Ollama / LM Studio / llama.cpp server
│   └── cache.py              # cache de resposta por hash do prompt completo
├── web/
│   ├── routes.py             # Flask: API autenticada, rate limit e painel de chat
│   ├── templates/index.html  # painel de chat single-user
│   └── static/                # CSS/JS do painel
└── verify/
    └── validar.py           # detecta alucinação, registra no histórico
```

Cada peça faz uma coisa só, exatamente como no desenho original:
`workspace` é o container onde o projeto real é colocado antes de
indexar, `memory` guarda tudo depois de indexado, `context` é só o que
a LLM vê agora, `retrieval` decide o que entra em `context`, `llm` só
executa, `verify` confere se a resposta é confiável antes de virar
"verdade" no histórico.

## Proteções operacionais das revisões 52–53

Além do hardening estrutural da revisão 52, a revisão 53 fecha os caminhos que
ainda podiam desperdiçar tempo ou repetir estado:

- respostas ambíguas com duas decisões JSON válidas são rejeitadas;
- respostas de erro estruturadas não sobrevivem no cache;
- o Agente detecta ciclos curtos pelo estado/resultados, não só por argumentos;
- a reserva da fila possui teto mesmo em conflito permanente;
- o Analista encerra lacunas/buscas repetidas e reutiliza retrieval idêntico;
- retries do Executor reprovados pelo Verify usam backoff exponencial;
- falhas de permissão do token web entram na telemetria.


- cada tarefa tem deadline global e orçamento central de chamadas/tokens, inclusive nos pipelines legados;
- cada job roda, por padrão, em processo filho terminável; o supervisor encerra código que ignorar timeouts;
- dois consumidores evitam que um único job lento paralise toda a fila;
- o limite da LLM funciona entre threads e processos, protegendo GPU local;
- conclusões são confrontadas com evidências e âncoras objetivas inventadas são bloqueadas;
- cache, fila, limitador e telemetria usam SQLite; cache JSON antigo é migrado automaticamente;
- `/health`, `status` e a CLI expõem workers, bloqueio de fila, avisos e P50/P95/P99.

## Pré-requisitos

- Python 3.8+ (não precisa instalar **nenhuma** biblioteca — tudo aqui
  usa só a biblioteca padrão do Python: `json`, `re`, `urllib`, etc.)
- Para o painel web, instale as versões reproduzíveis com
  `python -m pip install -r requirements.lock`. Para desenvolver e rodar a
  suíte: `python -m pip install -r requirements-dev.lock`.
- Para executar testes de projeto com rede bloqueada: Bubblewrap + `prlimit`
  no Linux, ou Docker com uma imagem local explicitamente configurada. Sem
  backend seguro, a Eyle recusa a execução em vez de cair no host.
- Um servidor de LLM local rodando, por exemplo:
  - **[Ollama](https://ollama.com)** (mais simples): `ollama pull qwen2.5:7b-instruct-q4_0` e depois `ollama serve` (geralmente já fica rodando sozinho em `http://localhost:11434`)
  - **LM Studio**, **llama.cpp server** ou **text-generation-webui** com a API compatível com OpenAI ativada

## Atualização segura

O ZIP revisado pode ser extraído sobre a instalação existente. Ele não carrega
arquivos mutáveis de `memory/` nem `context/`, então preserva o projeto
indexado, conversa, histórico, fila/checkpoints, confirmações, token web e
backups. Não apague essas duas pastas antes da atualização.

Em uma instalação nova, a ausência inicial desses arquivos é normal: o primeiro
`python3 main.py ingest` cria a memória do projeto e os demais dados aparecem
quando cada recurso é usado. Se uma versão anterior já sobrescreveu
`estrutura.json`/`entendimento.json` com conteúdo vazio, rode o `ingest` uma vez
para reconstruir o índice.

## Configuração

Abra `config.json` e ajuste:

```json
{
  "llm": {
    "provider": "ollama",
    "base_url": "http://localhost:11434",
    "model": "qwen2.5:7b-instruct-q4_0",
    "openai_compatible": false,
    "max_tokens": 1500,
    "context_window_tokens": 8192
  },
  "context": {
    "token_budget": 1500
  },
  "context_engine": {
    "safety_margin_tokens": 500,
    "chars_per_token_fallback": 3,
    "max_recent_observations": 4
  },
  "agent": {
    "rollout_mode": "read_only",
    "trusted_project_paths": [],
    "enabled_modes": ["analyze", "suggest", "edit"],
    "max_steps": 8,
    "max_no_progress_decisions": 3
  }
}
```

- Use `"openai_compatible": true` se estiver usando LM Studio / llama.cpp
  server / text-generation-webui (eles expõem `/v1/chat/completions`).
- `model` precisa bater exatamente com o nome do modelo carregado no seu
  servidor.
- `llm.context_window_tokens` é a janela **total** liberada no servidor local,
  somando entrada e saída. Está configurada em `8192`.
- `llm.max_tokens` reserva o teto da resposta. O Context Engine também desconta
  margem, prompt de sistema, catálogo, objetivo, ações e observações; só o saldo
  vira evidência de código.
- `context.token_budget` continua sendo apenas o orçamento do retrieval antigo.
  Ele não representa a janela do modelo e não dimensiona o Agente.
- `chars_per_token_fallback: 3` é a estimativa conservadora usada quando o
  tokenizador exato do backend não está disponível.
- `agent.rollout_mode` controla a ativação inteira por uma única chave:
  `off` volta aos pipelines anteriores, `read_only` roteia projetos para o
  Agente mas bloqueia `WRITE`/`EXEC`, e `full` libera o ciclo completo somente
  para raízes incluídas em `agent.trusted_project_paths`. O pacote vem em
  `read_only` até o benchmark real do LFM2 permanecer verde.
- A CLI explícita `python main.py agente "..."` continua disponível com trace;
  quando o rollout está `off`, ela abre apenas em `read_only` para diagnóstico.
- `agent.enabled_modes` declara `analyze`, `suggest` e `edit`. O modo `edit`
  exige leitura fresca, hashes, dry-run, confirmação, escrita atômica, teste e
  releitura; não usa mais o fallback legado de engenharia.
- `agent.max_steps` conta ferramentas realmente executadas. Decisões sem ação
  são limitadas separadamente por `max_no_progress_decisions`.

## Usando com o SEU projeto

O `workspace/` é um container: qualquer coisa colocada lá dentro —
pastas, subpastas, arquivos dentro de pastas, em qualquer nível — é
reconhecida e lida pelo `ingest`. Por enquanto, a forma de uso é
colocar a raiz inteira do projeto que você quer analisar dentro de
`workspace/`.

```bash
# 1. Copie (ou clone) a raiz do seu projeto inteira para dentro de workspace/
cp -r /caminho/para/seu/projeto eyle-base/workspace/

# 2. Indexa o que estiver em workspace/ (pode ter 30k, 100k, 1 milhão de
#    tokens — não importa; sem --nome, o Eyle usa o nome da subpasta
#    que está dentro de workspace/ como nome do projeto)
python3 main.py ingest

# (equivalente, se preferir apontar para outra pasta fora do workspace)
python3 main.py ingest /caminho/para/seu/projeto --nome "MeuProjeto"

# 3. Pergunta o que quiser — o sistema busca só o que é relevante
python3 main.py perguntar "aumente o limite de upload para 10MB"

# 4. Veja estatísticas da memória indexada
python3 main.py status
```

Para usar o painel persistente:

```bash
python3 main.py serve
```

O terminal mostra o token da API. Abra `http://127.0.0.1:5000`, cole esse
token quando o painel pedir e use normalmente. O valor fica só na
`sessionStorage` daquela aba. Também é possível definir um segredo próprio
de pelo menos 32 caracteres em `EYLE_API_TOKEN` ou
`config.json -> web.api_token`.

Você também pode testar só o retrieval, sem nenhuma LLM rodando:

```bash
python3 retrieval/buscar.py "sua pergunta aqui"
```

Cada `ingest` reconstrói `memory/projeto.json`, `estrutura.json` e
`chunks.jsonl` (o `historico.json` é preservado entre indexações).
Em Python, funções/classes são extraídas pela AST: métodos têm nomes
qualificados (`ClasseA.run`/`ClasseB.run`), `async def` é reconhecida e
imports, docstring e constantes anteriores à primeira definição ficam
num chunk próprio de preâmbulo. JS/TS ainda usa o reconhecedor por regex.
`projeto.json` guarda `source_path_hash` (identidade do caminho) separado de
`index_fingerprint` (conteúdo + configuração relevante + versão do indexador).
`python3 main.py status` recalcula o segundo e avisa se a fonte mudou.

### Segurança da ingestão e da leitura do projeto real

Toda leitura feita por `read_file` e pelo pipeline de dicas passa pelo
mesmo resolvedor usado pelo Codar. Só caminhos relativos que continuam
dentro da raiz do projeto depois de resolver `..` e symlinks são
aceitos. Caminhos absolutos, travessia para fora da raiz e symlinks
externos são rejeitados sem ler conteúdo.

O ingest também respeita `.gitignore` da raiz e de subpastas, não segue
diretórios symlink e bloqueia nomes/extensões de credenciais e marcadores de
segredo de alta confiança antes de gerar chunks ou chamar a LLM. Leitura,
verificação de conteúdo, hashes, AST/símbolos e chunks podem usar até quatro
threads, mantendo a mesma ordem determinística do modo serial. O resumo em
`memory/projeto.json -> arquivos_ignorados` guarda só contagens por motivo.

### Sandbox de testes

Quando `codar.testes.ativado=true`, a suíte só roda se o comando estiver em
`codar.testes.sandbox.comandos_permitidos`. A política vive no `config.json`
da Eyle, nunca dentro do repositório analisado. Rede é bloqueada por padrão;
CPU, memória, processos, arquivos, saída e tempo têm limites.

Por padrão, a suíte recebe uma cópia temporária gravável do projeto, limitada
por quantidade de arquivos e bytes. Caches/builds e até alterações maliciosas
morrem com essa cópia; o projeto real não é montado para o comando.

`backend=auto` prefere Bubblewrap no Linux. Docker só é usado quando
`imagem_docker` foi definida e deve apontar para uma imagem local que já
contenha as dependências. Overrides em `sandbox.projetos` usam o caminho
absoluto real como chave. O backend `processo` exige
`bloquear_rede=false` e serve apenas para projeto local confiável.

Uma recusa do sandbox é falha de teste: se ocorreu após um patch, o Codar
reverte automaticamente. O processo hospedeiro sempre nasce com
`shell=False`.

### Segurança ao escrever e ao chamar a LLM

Um patch confirmado nunca é gravado diretamente sobre o arquivo real:
a Eyle escreve um temporário no mesmo diretório e usa `os.replace()`.
Se a checagem de sintaxe ou a suíte falhar, restaura o conteúdo original
que já estava em memória — inclusive com `codar.fazer_backup=false`.
O backup em `context/backups/` é histórico adicional, não requisito do
rollback.

Falha de conexão, timeout ou rejeição do backend local levanta `ErroLLM`.
O pipeline termina com `status: "failed"`, sem passar o erro pelo Verify,
sem cacheá-lo e sem salvá-lo em `conversa.json` como se fosse uma fala da
assistente.

Toda tool do Agente devolve o mesmo contrato:
`status`, `ok`, `executed`, `changed`, `error_code`, `detail`. Uma tool
`WRITE` que falha e reverte não é contabilizada como escrita; só
`changed=true` invalida a verificação anterior e exige novos testes.
`run_tests` tem permissão `EXEC`, distinta de `READ` e `WRITE`. O gate
`agent.require_confirmation_for_exec` é independente e fica desligado por
padrão porque a execução já é isolada pelo sandbox.

O catálogo entregue ao modelo nasce do próprio registro `TOOLS`: nome,
descrição, permissão, schema de entrada, limites configurados e resumo da
saída. Antes de qualquer execução, uma validação central normaliza apenas os
sinônimos legados conhecidos e rejeita campo ausente, tipo incorreto ou chave
desconhecida com `INVALID_ARGUMENT`. Não existe uma segunda lista manual de
ferramentas para ficar velha em silêncio.

Para caber na janela pequena, o prompt usa uma projeção compacta desse mesmo
registro: conserva nomes, descrições, permissões, argumentos/tipos/obrigatórios,
limites e saída, removendo apenas a verbosidade repetitiva do JSON Schema. Com
os `4080` atuais, um primeiro passo típico deixa cerca de **1114 tokens** para
código real.

Para enxergar o projeto atual, o Agente possui `list_tree`, `search_code` com
trecho real numerado e hash, e `read_range`. A busca ainda usa o índice para
localizar candidatos, mas relê cada faixa do disco antes de devolvê-la. O
`read_file` continua só por compatibilidade; o caminho preferido é ler janelas
pequenas. Os tetos ficam em `agent.max_tree_entries`,
`agent.max_tree_depth` e `agent.max_read_range_lines`.

Cada faixa real lida vira uma evidência estruturada com `evidence_id`, tool de
origem, arquivo, linhas, conteúdo numerado, SHA-256 e estado `fresh`/`stale`.
`AgentState` separa objetivo, evidências, ações e observações recentes. O código
completo permanece fora do prompt; o Context Engine escolhe as evidências
relevantes que cabem no saldo de cada chamada. Pausa/retomada preserva IDs e
hashes.

Tarefas são classificadas em `chat`, `project_read` ou `project_write`. As duas
classes de projeto recusam `final` sem código fresco: árvore, metadados e
`fatos_importantes` não substituem evidência. Antes de concluir, a Eyle relê a
faixa, confere arquivo/linhas/hash e valida se citações `arquivo:linha` estão
cobertas pelos IDs declarados. Escrita real ou mudança externa deixa a evidência
`stale`; a mesma faixa pode então ser relida para gerar um novo hash.

No roteamento de alto nível, conversa sem relação com o projeto continua em
`chat`; com rollout `read_only` ou `full`, todo pedido reconhecido como relativo
ao projeto entra no Agente Eyle. Em `off`, a resposta volta ao pipeline anterior
com `fallback_cause=agent_rollout_off`, sem queda silenciosa para chat genérico.
Dentro dele, `analyze` explica, `suggest` propõe sem escrever e `edit` executa o
ciclo protegido de mudança. Os dois primeiros aceitam somente tools `READ`;
`edit` exige leitura fresca da faixa, hashes, dry-run, confirmação, aplicação
atômica, teste/rollback e releitura final.

Cada tarefa do Agente carrega um `GoalState` pequeno: objetivo, modo, critérios,
restrições, plano de até cinco passos, passo atual, bloqueios e evidências ainda
necessárias. Arquivo explícito gera plano de até dois passos; análise geral
começa por `list_tree`. Falha de ferramenta ou mudança de hash replaneja pelo
sistema; a LLM só pode replanejar quando evidência fresca negar uma hipótese.
O trace registra esse estado em cada evento. Cada execução também recebe um
`task_id` e grava em SQLite o `GoalState`, evidências/hashes, ações, orçamento
restante, pergunta e ação pendentes. O resultado publica, sem depender do texto
da LLM, as tools chamadas, evidências usadas, gate de conclusão, estado de
leitura e causa de fallback.

As gravações de memória JSON/JSONL usam temporário, `fsync` e `os.replace`.
`config.json -> retention` limita histórico, idade/LRU do cache, arquivos de
trace e backups. Interrupção no meio da serialização preserva a versão anterior.

Propostas, tools confirmáveis e qualquer pergunta `needs_user` recebem um ID
curto, criação,
expiração e hash da identidade do projeto. Com uma só, `sim` continua
funcionando. Se houver duas, confirme a correta com `confirmar ID` (ou
descarte com `cancelar ID`). O prazo padrão é 3600 segundos e pode ser
alterado em `config.json -> confirmacoes.expiracao_segundos`. Pendência
expirada ou criada para outro projeto nunca é aplicada. Cancelar/expirar limpa
a ação executável, mas mantém o snapshot e a auditoria. Após reinício, somente
ações idempotentes são retomadas; uma `WRITE` fica aguardando revalidação e, se
o código novo já estiver no disco, não é executada pela segunda vez.

### Segurança da API web

O HTML/CSS/JS do painel é público, mas não contém conversa, memória ou
segredo. `POST /enviar`, `GET /conversa`, `DELETE /mensagem/<id>`,
`GET /jobs/<id>` e `GET /status` exigem `Authorization: Bearer TOKEN` (clientes não web
também podem usar `X-API-Token`). Rotas de dados adicionadas no futuro
nascem protegidas por padrão.

O limite padrão é 180 requisições por minuto por IP, com teto separado de
10 tokens inválidos; bloqueios devolvem `429` e `Retry-After`. Esses valores
ficam em `config.json -> web.rate_limit`. O status publica somente nome e
contagens do projeto, nunca `caminho_origem`, `source_path_hash` ou
`index_fingerprint`, e redige
caminhos internos presentes no último erro da fila.

Mantenha o host em `127.0.0.1`. Se expuser a Eyle na rede, restrinja o
firewall e use um proxy HTTPS: o servidor Flask não cifra o token sozinho.

### Ordem das mensagens e fila persistente

Cada `POST /enviar` devolve `job_id` e `mensagem_id`. A mensagem e as
seis entradas de histórico que existiam naquele instante são capturadas
sob o mesmo lock; o Worker usa esse snapshot, então uma mensagem B
enviada depois nunca contamina o processamento da mensagem A.

Os jobs ficam em `context/fila.sqlite3`, em ordem FIFO, com estado
`pending`, `processing`, `completed` ou `failed`. Reiniciar a Eyle
preserva a fila e recoloca jobs interrompidos para processamento. Uma
exceção do Worker conserva o erro no próprio job; `GET /status` mostra
as contagens e a última falha registrada. A fila usa um único Worker
consumidor, mesmo quando Flask e Worker rodam em processos separados.
O painel acompanha cada `job_id` por `GET /jobs/<id>`; o indicador deixa de
deduzir conclusão pela ordem das mensagens ou pelo tamanho aproximado da fila.

## Como funciona o retrieval (`retrieval/buscar.py`)

Usa **BM25** — o mesmo tipo de algoritmo usado por motores de busca de
texto — implementado em Python puro, sem nenhuma dependência externa.
Ele:

1. Tokeniza os chunks uma vez e constrói postings invertidos por termo
2. Para cada pergunta, pontua somente os chunks que contêm os termos consultados
3. Reutiliza seleções lexicalmente equivalentes em um LRU limitado e invalidado quando o índice muda
4. Seleciona o Top-K exato por heap, respeitando o `token_budget` sem ordenar todos os candidatos
5. Também recupera decisões antigas do `historico.json` relacionadas aos mesmos arquivos, sempre relendo o histórico

No pipeline de engenharia, o Analista recebe cada candidato identificado
por `arquivo:linhas`. Os itens em `ignorar` são removidos de verdade, e
os trechos aprovados em `ler` são acumulados entre rodadas antes de montar
o contexto do Executor. `context/atual.json` registra exatamente esse
conjunto final filtrado.

Isso é o "raciocínio" descrito no documento original: um programador
não lê 100 mil linhas antes de corrigir uma função — ele procura o
erro, abre o arquivo certo, entende as dependências, e só então altera.
O `buscar.py` faz esse "procurar o arquivo certo" automaticamente.

> **Nota sobre qualidade da busca:** BM25 é busca por palavras-chave —
> ótimo, rápido e 100% offline, mas não entende sinônimos/semântica
> como uma busca por embeddings entenderia. Se seu servidor local
> expõe um endpoint de embeddings (Ollama suporta `/api/embeddings`
> com modelos como `nomic-embed-text`), dá para trocar o `buscar.py`
> por uma versão vetorial no futuro sem mudar o resto do sistema — a
> interface (`buscar(pergunta) -> atual.json`) continua igual.

## Como funciona a verificação (`verify/validar.py`)

Depois que a LLM responde, o `validar.py`:

1. Encontra menções a arquivos (`algo.py:43-61`) na resposta
2. Confere se esses arquivos **realmente existem** em `estrutura.json`
3. Confere se as linhas citadas fazem sentido para o tamanho real do
   arquivo
4. Publica três medidas separadas: `citation_validity` (arquivo/faixa
   existem), `coverage` (quanto do contexto relevante foi citado) e
   `grounding` (quanto das citações veio do contexto mostrado ao modelo)
5. Sem citação, as métricas ficam `None`; status `success`, teste positivo
   ou patch aplicado não fabricam confiança `1.0`
6. Registra tudo em `memory/historico.json`, com data — porque memória
   sem histórico vira memória falsa

Isso é o que separa esse sistema de um RAG comum: sem essa etapa, a
LLM pode inventar que uma função existe em um arquivo que ela nunca
viu. Com a etapa, você sabe quando desconfiar da resposta.

## Limitações honestas

- BM25 é busca por palavras-chave, não por significado — perguntas
  muito indiretas (que não usam nenhum termo parecido com o código)
  podem não recuperar o trecho certo. Ajuste `token_budget` e
  `max_chunks_no_resultado` em `config.json` conforme necessário.
- A verificação (`validar.py`) confere **citações de arquivo/linha**,
  não confere se a lógica da resposta está correta — é uma rede de
  segurança contra alucinação de referências, não um revisor de código.
  Métricas `None` significam "não havia o que medir", não aprovação nem
  reprovação automática. `confianca` continua temporariamente no retorno
  apenas como alias compatível de uma métrica efetivamente medida.
- Estimativa de tokens é aproximada: o retrieval histórico usa
  `context.chars_per_token` (padrão 4) e o Agente usa o fallback mais
  conservador de `context_engine.chars_per_token_fallback` (atualmente 3).
  Não substitui o tokenizador exato, por isso existe margem de segurança.
