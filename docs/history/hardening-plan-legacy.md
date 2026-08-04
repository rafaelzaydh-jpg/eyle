# Plano de Hardening — Eyle (Atualizações 16 em diante)

> **Estado atual (número livre, o que já foi feito) vive em
> `ESTADO_ATUAL.md`, não aqui** — este arquivo é o plano detalhado
> (escopo, teste, ordem de dependência) de cada atualização; aquele é o
> ponteiro rápido de 10 linhas que uma sessão nova lê primeiro.

Este plano deriva de `ANALISE_BUGS_PLANO_ATUALIZACAO_EYLE.md` (auditoria
externa contra a base `eyle091-atualizacoes-10-14`), reconciliado com o
código real da Eyle, que hoje já está em **Atualização 15** (teto de
tokens de saída — não existia quando a auditoria foi escrita).

## Conflito encontrado e como foi resolvido

Existe um segundo arquivo, `Plano_Atualizacoes_10_em_diante.md`, que usa os
mesmos números 12–17 para conteúdo **completamente diferente** do que
está implementado no código real (Goal State enxuto, `llm_profiles`,
`planning_mode: fixed`, Context Engine por relevância — nenhuma dessas
peças existe no zip atual). Esse arquivo não foi escrito nesta sessão de
trabalho e nunca chegou a ser aplicado contra o código de verdade.

**Resolução, na ordem certa segundo o próprio princípio da Eyle** ("o
principal primeiro, consolidar, depois implementar coisas novas"):

1. O código real + `Atual_Versão.md` (testado, linha por linha, contra o
   projeto de verdade) é a fonte de verdade. As correções da auditoria
   (segurança, integridade, contratos de estado) são "o principal" —
   ficam com os próximos números, a partir de **16**.
2. As capacidades novas do arquivo divergente (Goal State, `llm_profiles`,
   `planning_mode`, Context Engine por relevância) são "coisas novas" —
   ficam **renumeradas para depois** de todo o hardening P0/P1 fechar
   (a partir de ~38, provisório — números exatos quando chegar a vez,
   e cada uma precisa ser reavaliada contra o código pós-hardening antes
   de implementar, porque o código vai ter mudado por baixo dela).
3. `Plano_Atualizacoes_10_em_diante.md` fica obsoleto pra números 12-17
   especificamente — as ideias nele não se perdem, só a numeração muda.

## Critério de ordenação (mesmo espírito das Atualizações 10-15)

Cada atualização isolada, testável sozinha, com "o que não muda"
explícito. Ordenadas por **o que outras atualizações futuras vão
precisar que já exista** — um resolvedor de caminho seguro compartilhado
(18) vem antes de endurecer ingestão (29) porque a ingestão vai *usar*
esse resolvedor, por exemplo.

---

## Grupo 1 — Fecha as garantias que a Eyle já promete (P0, prioridade máxima)

### Atualização 16 — Circuit breaker conta `{"ok": false}` ✅ **feito**
**Fecha:** EYL-006. **Bug em código desta sessão** (Atualização 11).
`registrar_resultado_tool` só incrementa `erros_consecutivos` quando o
resultado tem chave `"erro"` — mas `apply_patch`/`run_tests`/
`test_patch_dry_run` falham devolvendo `{"ok": false, ...}`, formato
diferente. Uma escrita que falha repetidamente nunca aciona o breaker.
**Escopo:** `registrar_resultado_tool` passa a contar como erro tanto
`"erro" in resultado` quanto `resultado.get("ok") is False`.
**Teste:** 3 falhas seguidas de `apply_patch` (`{"ok": false}`, sem
`"erro"`) → `needs_user` no 3º passo.

### Atualização 17 — Verificador exige `executado=true AND ok=true` ✅ **feito**
**Fecha:** EYL-005. **Bug em código desta sessão** (Atualização 10).
`registrar_testes` aceita `ok=true` mesmo com `executado=false` (testes
desligados/não configurados) — isso esvazia a garantia da Atualização
10: "final" pode ser aceito depois de escrita sem nenhuma verificação
real ter rodado.
**Escopo:** `registrar_testes` só marca `testes_ok_apos_escrita=True`
quando `executado is True and ok is True`. Quando testes estão
desligados, a tarefa com escrita não consegue mais fechar em `"final"`
sozinha — precisa de `needs_user` explícito ou de `max_steps`. Isso é
mudança de comportamento visível (documentar em "o que não muda" o
inverso: o que passa a mudar).
**Teste:** `apply_patch` → `run_tests` com `{"executado": false, "ok":
true}` → `"final"` → recusado (igual não ter rodado `run_tests`).
`apply_patch` → `run_tests` com `{"executado": true, "ok": true}` →
`"final"` → aceito.

### Atualização 18 — Resolvedor de caminho seguro compartilhado ✅ **feito**
**Fecha:** EYL-001 (P0 — crítico mesmo em uso solo local, porque o
caminho vem de decisão da LLM, manipulável por conteúdo de arquivo lido
via prompt injection indireto, não só por atacante externo).
**Escopo:** mover `_resolver_caminho_seguro` de `engine/codar.py` para
um módulo compartilhado (`engine/seguranca.py`); `engine/dicas.py:
ler_codigo_real` (usado por `read_file` e pelo pipeline `dicas`) passa a
usar o mesmo resolvedor que `apply_patch` já usa. Caminho fora da raiz,
absoluto ou symlink externo é rejeitado com erro claro, não lido
silenciosamente.
**Por que vem antes das outras:** ingestão (29) e qualquer tool futura
que leia caminho vão depender deste módulo existir — é a peça mais
"pré-requisito de outras" da lista inteira.
**Teste:** `read_file` com `caminho_relativo="../fora.txt"` → erro,
não conteúdo. Caminho normal dentro do projeto continua funcionando.

**Aplicado:** novo `engine/seguranca.py` concentra
`_resolver_caminho_seguro`; `engine/codar.py` importa essa implementação
e `engine/dicas.py:ler_codigo_real` passou a usá-la antes de qualquer
leitura. Rejeições viram uma entrada `{"erro": ...}` explícita,
propagada por `read_file` e tratada pelo prompt de dicas sem expor
conteúdo. `tests/test_seguranca.py` cobre caminho normal, `../`, caminho
absoluto mesmo apontando para dentro da raiz e symlink externo.

### Atualização 19 — Rollback sempre restaura, com ou sem backup; escrita atômica ✅ **feito**
**Fecha:** EYL-002 (P0). `_reverter()` só restaura `if backup_path:`,
mesmo com `conteudo_atual` já em memória. Com `codar.fazer_backup=false`
(hoje não é o default, mas é possível), uma falha deixa o arquivo real
quebrado enquanto a mensagem diz "revertido automaticamente".
**Escopo:** `_reverter()` usa `conteudo_atual` sempre, independente de
`backup_path` (backup vira histórico adicional, não pré-requisito de
segurança). Escrita passa a ser atômica: arquivo temporário no mesmo
diretório + `os.replace()`, em vez de `open(..., "w")` direto — uma
interrupção no meio da escrita não pode truncar o arquivo real.
**Teste:** simular falha de `ast.parse()` pós-escrita com
`fazer_backup=false` → arquivo real continua com o conteúdo original,
byte a byte.

**Aplicado:** `engine/codar.py` ganhou `_escrever_arquivo_atomico`, que
cria o temporário no diretório do alvo, sincroniza, preserva permissões
e troca com `os.replace()`. `_reverter()` restaura `conteudo_atual`
mantido em memória pelo mesmo caminho atômico; `backup_path` não é mais
lido pelo rollback. `tests/test_codar.py` cobre rollback sem backup,
uso de `os.replace`, preservação de permissões, limpeza do temporário e
falha anterior à troca sem truncar o destino.

---

## Grupo 1.5 — extraído de uma peça maior, priorizada mais cedo (segunda auditoria)

### Atualização 39 — `run_tests` vira permissão `EXEC` ✅ **feito**
**Fecha:** a parte urgente de EYL-012 / P0-03 (segunda auditoria,
`AUDITORIA_TECNICA_EYLE_10_17_1_.md`). `engine/agent_tools.py:290`
classifica `run_tests` como `READ` — mas é execução de subprocess de
verdade. Na auditoria ele ainda usava `subprocess.run(..., shell=True)`;
essa metade já foi corrigida junto da 28. Misturar execução na mesma categoria que
`read_file`/`search_code` esconde que é uma classe de risco diferente:
com `shell=True`, qualquer parte do comando influenciada por
configuração ou argumento vira superfície de injeção de shell — risco
real mesmo em uso solo, não depende de repositório malicioso de
terceiro.
**Por que veio pra cá, fora de ordem numérica:** é uma correção pequena
e isolada (trocar `shell=True` por argv validado + nova categoria de
permissão), do mesmo tamanho das Atualizações 16/17 — não precisa
esperar o sandbox completo (Atualização 28) pra valer a pena. Número
mais alto não significa prioridade mais baixa; quem controla ordem de
execução é a lista no fim deste documento, não a numeração (mesmo
princípio do `ESTADO_ATUAL.md`).
**Estado após a Atualização 28:** `engine/codar.py` já não usa
`shell=True`: o comando é convertido em argv e o processo hospedeiro nasce
com `shell=False` dentro do executor de sandbox. A 39 fecha a parte de
contrato do Agente: permissão `EXEC`, reclassificação de `run_tests` e gate
independente.
**Escopo restante:**
- `engine/agent_tools.py`: nova permissão `EXEC` (além de `READ`/
  `WRITE`); `run_tests` passa de `READ` pra `EXEC`.
- `engine/codar.py`: ✅ entregue pela 28 — `rodar_testes_projeto` monta o comando como lista
  de argv (`shlex.split` do comando configurado, ou lista já
  estruturada no config) e chama `subprocess.run(argv, shell=False,
  ...)` — nunca mais interpola string crua com `shell=True`.
- `engine/agent.py`: decide se `EXEC` precisa do mesmo gate de
  confirmação que `WRITE` hoje tem (`require_confirmation_for_write`)
  — avaliar quando for implementar; rodar teste do próprio projeto é
  bem mais seguro que escrever nele, então pode ficar sem confirmação
  por padrão, mas isolado como decisão própria, não herdada da lógica
  de WRITE.
**Teste:** comando de teste configurado com um caractere de shell
(`;`, `&&`, backtick) não executa nada além do comando literal; `run_tests`
continua funcionando normalmente pro caso comum.
**Entregue depois na Atualização 28:** container/sandbox de verdade, rede
desligada, limites de CPU/memória/tempo e allowlist de comando por projeto.

**Aplicado:** `run_tests` agora é `EXEC`; `agent.require_confirmation_for_exec`
é independente e fica `false` por padrão, pois a execução já passa pelo
sandbox. Se ativado, pausa/persiste/retoma como qualquer ação confirmável. O
Codar normaliza a configuração em argv antes de chamar o sandbox e o teste com
`; touch ...` confirma que metacaracteres continuam argumentos literais.

---

## Grupo 2 — Contrato de estado (o que o sistema diz vs. o que é verdade)

### Atualização 20 — Erro da LLM não vira resposta "válida" ✅ **feito**
**Fecha:** EYL-003 (P0) + a metade de EYL-014 sobre confiança em erro.
`_chamar_llm` devolve string `"[erro] ..."` como se fosse resposta
normal; pode ser salva no histórico, passar pelo Verify e receber
confiança `1.0` (quando não há citação, `confianca = 1.0 if total == 0
else ...`).
**Escopo:** `_chamar_llm` sinaliza erro de um jeito que o chamador não
pode confundir com resposta real (levantar uma exceção própria, ex.
`ErroLLM`, é o caminho mais idiomático em Python — decidir na hora de
implementar). Cada `_processar_*` em `engine/engine.py` captura isso e
retorna `status: "failed"` **sem chamar Verify**. `verify/validar.py`
para de dar `confianca=1.0` quando não há citação — vira `None` ou um
valor baixo explícito.
**Nota:** maior que 16-19 em superfície (toca todo `_chamar_llm` +
todos os `_processar_*`), mas ainda uma peça isolada e testável — não
misturar com o contrato de tools (Atualização 21).
**Teste:** mock de `_chamar_llm` levantando `ErroLLM` → `processar()`
devolve `status: "failed"`, `conversa.json` não ganha uma entrada
"assistant" com o texto do erro, Verify nunca é chamado.

**Aplicado:** `llm/executar.py` define `ErroLLM` e o levanta para erro
HTTP, conexão e falha inesperada; cache legado com prefixo `[erro]`
também é rejeitado. `engine/engine.py` converte a exceção em resultado
`failed` nas fronteiras de chat, consulta, dicas, visão geral, Agente e
engenharia, sem persistir resposta nem chamar Verify. O Entendedor
preserva a entrada anterior quando recebe `ErroLLM` durante o ingest.
`verify/validar.py` devolve `confianca=None` quando não há citação.
Testes cobrem os dois backends de erro, cache legado, fluxo completo,
ausência de Verify/persistência e o novo significado da confiança.

### Atualização 21 — Contrato padronizado de resultado de tools ✅ **feito**
**Fecha:** o resto de EYL-006 (fix completo, além do patch pontual da
Atualização 16) + parte de EYL-007.
**Escopo:** todas as tools em `agent_tools.py` passam a devolver o
mesmo formato (`status`, `ok`, `executed`, `changed`, `error_code`,
`detail`) em vez de cada uma inventar suas próprias chaves. `agent.py`
só chama `registrar_escrita()` quando `changed=True` — hoje qualquer
tool WRITE confirmada marca escrita mesmo que o patch tenha falhado e
nada tenha mudado no arquivo.
**Depende de:** 16 (a versão pontual já vai estar em produção;
21 é a generalização — não é redundante, é a versão "de verdade" do
mesmo problema).
**Teste:** `apply_patch` que falha e faz rollback (`changed=False`) →
`estado.houve_escrita` continua `False` depois dessa chamada.

**Aplicado:** as sete tools de `engine/agent_tools.py` agora devolvem o
mesmo envelope exato: `status`, `ok`, `executed`, `changed`,
`error_code`, `detail`. Sucesso, falha, operação pulada, argumento
inválido e exceção inesperada usam esse contrato; os dados específicos
da tool ficam em `detail`. `AgentState` interpreta `status`/`ok` e
`executed`, e `engine/agent.py` só chama `registrar_escrita()` quando
uma tool `WRITE` informa `changed=true`. Falha com rollback bem-sucedido
fica `changed=false`; se até o rollback falhar, `codar.py` propaga
`changed=true` para não mentir sobre o estado do arquivo.

### Atualização 22 — Confirmação vinculada à tarefa (não mais um arquivo global) ✅ **feito**
**Fecha:** EYL-004 (P0). `proposta_pendente.json`/`agent_pendente.json`
são arquivos globais sem `job_id`, expiração ou hash de projeto — um
"sim" qualquer pode confirmar uma pendência antiga ou errada; a proposta
do Codar é checada antes da do Agente sem aviso se as duas existirem.
**Escopo:** cada pendência ganha `id` curto, `criado_em`,
`expira_em` e o hash do projeto no momento da criação. Confirmação
passa a exigir referência ao id quando houver mais de uma pendência
(ex: "confirmar 7F3A"); com uma só, "sim" continua funcionando como
hoje. Pendência expirada ou com hash de projeto divergente é
rejeitada com mensagem clara, não aplicada silenciosamente.
**Teste:** duas pendências simultâneas (simuladas) → "sim" sem id é
rejeitado pedindo qual das duas; "sim" com id errado é rejeitado.

**Aplicado:** proposta do Codar e tool `WRITE` do Agente recebem ID
hexadecimal curto, `criado_em`, `expira_em`, `tipo_pendencia` e
`projeto_hash` (identidade formada por nome + caminho real do projeto,
sem reutilizar o `source_hash` legado). O TTL default é 3600 segundos,
configurável em `confirmacoes.expiracao_segundos`. Uma pendência mantém
o atalho `sim`; com proposta e Agente simultaneamente pendentes, a Eyle
exige `confirmar ID`/`cancelar ID`. ID desconhecido, pendência expirada,
metadados legados incompletos ou hash de outro projeto são rejeitados
antes de qualquer escrita e a pendência inválida é descartada com uma
mensagem explícita.

---

## Grupo 3 — Retrieval e contexto corretos

### Atualização 23 — Decisão do Analista realmente filtra o contexto ✅ **feito**
**Fecha:** EYL-007. `ler`/`ignorar` são interpretados mas nunca
aplicados sobre `atual["trechos"]` — o Executor pode receber
exatamente o que o Analista mandou ignorar.
**Escopo:** aplicar `ignorar` removendo do conjunto de trechos antes de
montar o prompt do Executor; acumular evidências aprovadas entre
iterações em vez de a rodada nova substituir a anterior por completo.

**Aplicado:** o prompt identifica cada candidato por `arquivo:linhas` e
o ciclo aceita esse ID, arquivo, símbolo ou seletor estruturado. `ignorar`
tem prioridade, apenas trechos aprovados chegam ao Executor e os aprovados
das rodadas anteriores são preservados/deduplicados sem ultrapassar o
`token_budget`. `atual.json`, `tokens_usados` e `arquivos_relevantes` são
reconstruídos a partir desse mesmo conjunto final, evitando divergência
entre disco, Executor e Verify.

### Atualização 24 — Extração de símbolos via AST ✅ **feito**
**Fecha:** EYL-008. Regex trata método como símbolo global,
`dict(simbolos)` apaga duplicata por nome, preâmbulo do módulo (imports,
constantes, decorators antes da primeira função) some do índice.
**Escopo:** usar `ast` pra Python; identificadores qualificados
(`ClasseA.run` vs `ClasseB.run`); chunk dedicado pro preâmbulo do
módulo. JS/TS fica de fora desta atualização (parser de verdade tipo
Tree-sitter é esforço maior, vira atualização própria se/quando a Eyle
precisar indexar JS/TS a sério).

**Aplicado:** `ingest.py` usa AST para funções síncronas/assíncronas,
classes, métodos e classes aninhadas; métodos recebem nome qualificado e
decorators ficam anexados ao símbolo. Imports, docstring e constantes
antes da primeira definição ganham chunk `preambulo`. `codar.py` usa as
posições AST para localizar o recorte real e rejeita nomes ambíguos em
vez de perder duplicatas via `dict(simbolos)`. JS/TS mantém o reconhecedor
anterior, conforme o escopo.

---

## Grupo 4 — Fila, concorrência e API (relevante sobretudo se sair de "uso solo local")

### Atualização 25 — Ordem de mensagens / snapshot de histórico por job ✅ **feito**
**Fecha:** EYL-009. Mensagem B pode entrar na conversa antes da tarefa
A terminar de processar, contaminando o histórico que A "deveria" ver.

**Aplicado:** `POST /enviar` registra a mensagem e captura as seis
mensagens de histórico sob o mesmo lock; esse snapshot segue dentro do
job até o Worker e é a única versão que o pipeline `chat` usa. Uma
mensagem posterior pode aparecer em `conversa.json`, mas nunca invade o
prompt de um job que já estava na fila. CLI e chamadas sem snapshot
mantêm o comportamento anterior.

### Atualização 26 — Fila persistente, falha do Worker não some ✅ **feito**
**Fecha:** EYL-010. Fila é uma `deque` em memória; reiniciar o processo
perde tudo; exceção do Worker só vai pro terminal. Maior esforço do
grupo — migração pra SQLite é honesta sobre ser um passo grande;
mantém-se isolada das outras (não é pré-requisito de nenhuma anterior).

**Aplicado:** `engine/queue.py` usa `context/fila.sqlite3`, reserva jobs
em FIFO com transação SQLite e persiste `pending`, `processing`,
`completed` e `failed`, tentativas, resultado e erro. Na inicialização,
o Worker recoloca em `pending` jobs interrompidos pelo processo anterior.
`POST /enviar`/`DELETE /mensagem` devolvem `job_id`; `/status` expõe as
contagens e a última falha sem depender do terminal. O consumo continua
deliberadamente com um único Worker por fila.

### Atualização 27 — Endurecer API web ✅ **feito**
**Fecha:** EYL-011. Sem auth/token, sem rate limit, `/status` expõe
caminho absoluto. Prioridade sobe pra P0 automaticamente se algum dia o
`host` deixar de ser `127.0.0.1`.

**Aplicado:** endpoints de dados usam token Bearer comparado em tempo
constante; o segredo vem de `EYLE_API_TOKEN`, de `web.api_token` ou é
gerado uma vez em `context/web_api_token.txt` com permissão `0600`. O
painel pede o token e o mantém só na `sessionStorage`. Há janela de rate
limit por IP e teto separado para autenticações inválidas, ambos com
`429`/`Retry-After`. `/status` usa uma allowlist de metadados públicos e
redige caminhos conhecidos dos erros da fila. HTML/CSS/JS continuam
públicos, mas não carregam memória nem segredo; endpoints futuros ficam
protegidos por padrão. Respostas de dados usam `Cache-Control: no-store`
e cabeçalhos básicos anti-sniffing/frame. Host externo emite aviso
explícito sobre HTTPS e firewall.

### Atualização 28 — Sandbox completo de execução ✅ **feito**
**Fecha:** o resto de EYL-012 / P0-03 (segunda auditoria). A permissão
`EXEC` própria continua separada na **Atualização 39**. Como a 28 acabou
sendo aplicada antes dela, também absorveu a remoção de `shell=True`,
pré-requisito direto do executor. O escopo principal continua: isolamento de
verdade (container/processo restrito), CPU/memória/tempo limitados,
rede desligada por padrão, allowlist de comando por projeto. Continua
sem pressa pra uso solo local com projetos confiáveis — sobe de
prioridade se a Eyle passar a rodar teste de repositório não confiável.

**Aplicado:** novo `engine/sandbox.py` aceita somente argv presente numa
allowlist confiável externa ao repositório e executa com `shell=False`.
`backend=auto` usa Bubblewrap no Linux ou Docker quando uma imagem foi
explicitamente configurada; rede é desligada por padrão. Bubblewrap monta
runtime read-only, snapshot gravável do projeto e `/tmp` efêmero; Docker usa rootfs
read-only, capacidades removidas e `no-new-privileges`. CPU, memória,
processos, descritores, tamanho de saída e tempo de parede têm limites.
Por padrão a suíte recebe uma cópia temporária limitada por quantidade de
arquivos/bytes, então efeitos colaterais não alcançam o projeto real.
Sem backend capaz de bloquear rede, a execução falha fechada. Overrides por
projeto são indexados pelo caminho real em `config.json`, nunca por arquivo
controlado pelo próprio projeto. Recusa/falha do sandbox também reprova o
patch e aciona rollback. A remoção de `shell=True` precisou aterrissar junto
para que o sandbox não fosse invocado por uma string crua; a classificação
`EXEC` do Agente continua isolada na 39.

### Atualização 29 — Ingestão segura ✅ **feito**
**Fecha:** EYL-013. Sem respeitar `.gitignore`, sem denylist de
segredos, sem checagem de symlink externo. **Depende de:** 18 (usa o
mesmo resolvedor de caminho seguro).

**Aplicado:** o walker do `ingest.py` carrega regras `.gitignore` da raiz e
de subpastas, incluindo negação, ancoragem e `**`; mantém a denylist de nomes
e extensões de credenciais e detecta marcadores de segredo de alta confiança
no conteúdo. Todo candidato passa pelo resolvedor da 18 antes de abrir;
symlink externo é rejeitado e symlink de diretório não é seguido. O caminho
real validado é usado para conteúdo/hash, e `engine/entender.py` repete a
mesma validação antes de enviar qualquer arquivo à LLM. `projeto.json`
registra contagens por motivo (`gitignore`, `segredo`, `symlink_externo` e
filtro interno) sem armazenar o conteúdo recusado.

---

## Grupo 5 — Qualidade, observabilidade, acabamento (P2/P3 — sem pressa)

| # | Fecha | Resumo |
|---|---|---|
| 30 ✅ | EYL-014 (resto) + P0-05 (2ª auditoria) | Verify separa `citation_validity`/`coverage`/`grounding`; `linha_ini` e `linha_fim` entram na validade; `success` e teste positivo não fabricam confiança `1.0`. `confianca` fica só como alias temporário de métrica medida. |
| 31 ✅ | EYL-015 | O último item do histórico só é removido quando é exatamente a mensagem atual; repetições legítimas de turnos anteriores permanecem. Vale para CLI e snapshot da fila web. |
| 32 ✅ | EYL-016 | Escrita atômica em todos os JSONs de memória e JSONL do índice. |
| 33 ✅ | EYL-017 | Chave de cache inclui fingerprint completo do backend (`base_url`, provider e modo). |
| 34 ✅ | EYL-018 | Validação de config no startup (schema tipado, falha cedo). |
| 35 ✅ | EYL-019 | Versões diretas/transitivas fixadas em locks reproduzíveis. |
| 36 ✅ | EYL-020 | Retenção/rotação de histórico, cache, traces e backups. |
| 37 ✅ | EYL-021 | Interface consulta o estado do `job_id` real; não deduz conclusão pela conversa. |
| 38 ✅ | P1-05 (2ª auditoria) | `source_path_hash` nomeia honestamente o hash do caminho; `index_fingerprint` usa conteúdo, config relevante e versão do indexador; `main.py status` verifica defasagem. |

---

## Grupo 6 — Adiado: capacidades novas (só depois do Grupo 1-4 fechado)

As ideias de `Plano_Atualizacoes_10_em_diante.md` (Goal State enxuto,
`llm_profiles`/tabela de capacidade por tier, `planning_mode: fixed`,
Context Engine por relevância) continuam válidas em espírito, mas
**precisam ser reavaliadas contra o código pós-hardening antes de
implementar** — o contrato de tools (21), o resolvedor de caminho (18)
e o contrato de erro da LLM (20) mudam código que essas features
tocariam. Renumeração exata quando chegar a vez; não reservar números
agora pra não repetir o próprio erro que este documento está
corrigindo.

---

## Ordem de execução desta rodada

```
16 → 17 → 18 → 19 → 20 → 21 → 22 ✅ feito (garantias P0, contratos e confirmação vinculada)
39 ✅ feito              (EXEC própria; shell=False/argv/sandbox)
23 → 24 ✅ feito         (retrieval correto)
25 → 26 ✅ feito         (snapshot por job + fila SQLite persistente)
27 → 28 → 29 ✅ feito     (API, sandbox completo e ingestão segura)
30 → 31 ✅ feito         (Verify honesto + mensagem atual sem duplicação)
32 → 38 ✅ feito         (qualidade, observabilidade e acabamento)
```

Fontes deste plano: `ANALISE_BUGS_PLANO_ATUALIZACAO_EYLE.md` (primeira
auditoria, itens EYL-001 a EYL-021) e
`AUDITORIA_TECNICA_EYLE_10_17_1_.md` (segunda auditoria, contra
`eyle091-atualizacoes-10-17.zip` — trouxe P1-05/`source_hash`, a
validação de `linha_fim` no item 30, e a priorização da 39). Achados que
as duas concordam (18, 19, 21, 22, contrato de erro da LLM) foram
verificados linha por linha contra o código antes de entrar no plano —
ver `ESTADO_ATUAL.md` pra saber o que já foi implementado.

Cada atualização fecha com teste verde antes da próxima começar — mesma
disciplina de `Atual_Versão.md`.
