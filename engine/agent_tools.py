#!/usr/bin/env python3
"""
agent_tools.py
--------------
Registro real de ferramentas do Agente da Eyle -- fecha o buraco que
engine/agent.py deixava aberto (import defensivo caindo no stub
"tool indisponivel" porque este arquivo nao existia ainda).

Principio deste modulo: NAO reimplementar nada. Cada tool aqui e um
wrapper fino em cima de uma funcao que ja existe em outro lugar da
Eyle -- este arquivo so traduz `arguments` (o dict que a LLM devolve
num tool_call) para os parametros posicionais que a funcao real espera.

Atualizacao 21: toda tool devolve exatamente o mesmo envelope:
`status`, `ok`, `executed`, `changed`, `error_code`, `detail`. O campo
`detail` carrega o resultado especifico da operacao (texto, lista ou
dict), enquanto os outros cinco campos sempre mantem o mesmo significado.
Assim o loop nao precisa adivinhar se `erro`, `executado`, `resultados`
ou outra chave inventada por uma tool significa sucesso ou falha.

Origem de cada tool (nenhuma logica de negocio nova):
    read_metadata       -> memory/entendimento.json (so leitura de dict,
                            nenhuma funcao dedicada -- e o proprio
                            entendimento carregado pelo chamador)
    list_tree           -> engine/project_reader.py:listar_arvore_projeto()
    search_code         -> retrieval/buscar.py:buscar()
    find_symbol         -> engine/codar.py:localizar_simbolo()
    read_range          -> engine/project_reader.py:ler_faixa_projeto()
    read_file           -> engine/dicas.py:ler_codigo_real()
    test_patch_dry_run  -> engine/codar.py:testar_patch_em_copia()
    run_tests           -> engine/codar.py:rodar_testes_projeto()
    apply_patch         -> engine/codar.py:aplicar_patch()  (permission=WRITE)

Atualizacao 40: este registro e a unica fonte do catalogo entregue ao
modelo. Cada entrada declara nome, descricao, permissao, schema de entrada,
limites e resumo de saida. A validacao central normaliza aliases legados e
rejeita argumentos invalidos antes de qualquer execucao.

Atualizacao 41: list_tree/read_range leem o disco atual; search_code usa o
indice apenas para localizar e rele a faixa fresca antes de devolve-la.

`ctx` (dict passado pelo loop em engine/agent.py) precisa trazer:
    ctx["config"]       -- config.json carregado
    ctx["entendimento"] -- memory/entendimento.json carregado (ou {})
    ctx["projeto"]      -- memory/projeto.json carregado (usa
                            projeto["caminho_origem"] pra achar o
                            codigo real no disco)

As permissoes sao READ, EXEC e WRITE. ``run_tests`` e EXEC porque inicia
subprocesso real no sandbox; ``apply_patch`` e WRITE. Cada categoria tem
gate proprio no loop (EXEC nao pede confirmacao por padrao).

Nenhuma tool aqui decide SE deve rodar -- isso e sempre do loop
principal (guarda de chamada repetida, confirmacao de escrita, limite
de passos). Este modulo so executa o que foi pedido e devolve o
resultado, sem alucinar; qualquer excecao vira o mesmo envelope com
`status="failed"` em vez de derrubar a tarefa inteira.
"""
import os
import re
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from retrieval.buscar import buscar  # noqa: E402
from engine.dicas import ler_codigo_real  # noqa: E402
from engine.project_reader import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)
from engine.codar import (  # noqa: E402
    localizar_simbolo,
    testar_patch_em_copia,
    rodar_testes_projeto,
    aplicar_patch,
    restaurar_snapshot_patch,
)

MEMORY_DIR = os.path.join(BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(BASE_DIR, "context")

_CAMPOS_RESULTADO = ("status", "ok", "executed", "changed", "error_code", "detail")


def _resultado(status, ok, executed, changed=False, error_code=None, detail=None):
    """Monta o contrato unico de retorno das tools (Atualizacao 21)."""
    return {
        "status": status,
        "ok": bool(ok),
        "executed": bool(executed),
        "changed": bool(changed),
        "error_code": error_code,
        "detail": detail,
    }


def _sucesso(detail=None, changed=False):
    return _resultado("success", True, True, changed=changed, detail=detail)


def _falha(error_code, detail, executed=False, changed=False):
    return _resultado(
        "failed", False, executed, changed=changed,
        error_code=error_code, detail=detail,
    )


def _pulado(detail):
    return _resultado("skipped", True, False, detail=detail)


def _caminho_projeto(ctx):
    """Extrai caminho_origem do projeto ativo."""
    projeto = (ctx or {}).get("projeto") or {}
    return projeto.get("caminho_origem")


def _caminho_memoria(ctx):
    """Permite indices isolados por projeto, mantendo compatibilidade global."""
    projeto = (ctx or {}).get("projeto") or {}
    return projeto.get("memory_dir") or MEMORY_DIR


# ---------------------------------------------------------------------------
# Tools READ
# ---------------------------------------------------------------------------

def _tool_read_metadata(arguments, ctx):
    """
    a) da ordem de preferencia do PROMPT_AGENTE: metadados de
    entendimento.json (tipo, responsabilidade, depende_de,
    funcoes_principais, pontos_criticos) ANTES de ler o arquivo inteiro.
    Nao chama nenhuma funcao -- e so o dict ja carregado em ctx['entendimento'].
    """
    entendimento = (ctx or {}).get("entendimento") or {}
    caminho_relativo = arguments["caminho_relativo"]
    info = (entendimento.get("arquivos") or {}).get(caminho_relativo)
    if info is None:
        return _falha(
            "METADATA_NOT_FOUND",
            f"'{caminho_relativo}' nao encontrado em entendimento.json (rode ingest antes?)",
            executed=True,
        )
    return _sucesso(info)


def _tool_search_code(arguments, ctx):
    """
    BM25 sobre memory/chunks.jsonl localiza os candidatos, mas o texto do
    indice nao e devolvido como verdade atual. Cada faixa e relida do disco
    por ler_faixa_projeto e volta numerada, com hash do conteudo fresco.
    """
    pergunta = arguments["pergunta"].strip()
    config = (ctx or {}).get("config") or {}
    atual = buscar(pergunta, memory_dir=_caminho_memoria(ctx), config=config)
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")

    max_linhas = config.get("agent", {}).get("max_read_range_lines", 400)
    resultados = []
    falhas_leitura = []
    for trecho in atual.get("trechos", []):
        try:
            inicio_texto, fim_texto = str(trecho["linhas"]).split("-", 1)
            linha_inicio = int(inicio_texto)
            linha_fim_indexada = int(fim_texto)
            linha_fim = min(linha_fim_indexada, linha_inicio + max_linhas - 1)
            leitura = ler_faixa_projeto(
                caminho_projeto,
                trecho["arquivo"],
                linha_inicio,
                linha_fim,
                max_linhas=max_linhas,
            )
        except ErroLeituraProjeto as erro:
            falhas_leitura.append({
                "arquivo": trecho.get("arquivo"),
                "error_code": erro.error_code,
                "detail": erro.detail,
            })
            continue
        except (KeyError, TypeError, ValueError) as erro:
            falhas_leitura.append({
                "arquivo": trecho.get("arquivo"),
                "error_code": "INVALID_INDEX_RANGE",
                "detail": f"faixa invalida no indice: {erro}",
            })
            continue

        resultados.append({
            "arquivo": leitura["arquivo"],
            "linha_inicio": leitura["linha_inicio"],
            "linha_fim": leitura["linha_fim"],
            "simbolo": trecho.get("simbolo"),
            "score": trecho.get("score"),
            "trecho_numerado": leitura["trecho_numerado"],
            "content_hash": leitura["content_hash"],
            "file_hash": leitura["file_hash"],
            "faixa_indexada": trecho["linhas"],
            "faixa_truncada_pelo_limite": linha_fim < linha_fim_indexada,
            "fim_ajustado_ao_arquivo": leitura["fim_ajustado_ao_arquivo"],
        })
    return _sucesso({
        "resultados": resultados,
        "arquivos_relevantes": atual.get("arquivos_relevantes", []),
        "falhas_leitura": falhas_leitura,
    })


def _tool_find_symbol(arguments, ctx):
    """
    b) find_symbol -- localiza a faixa de linhas exata de uma
    funcao/classe dentro de um arquivo ja identificado (via
    read_metadata ou search_code), lendo o arquivo FRESCO do disco
    (engine/codar.py:localizar_simbolo). Precisa de caminho_relativo E
    simbolo -- nao existe busca de simbolo "no projeto inteiro" aqui,
    igual a funcao de origem.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")
    caminho_relativo = arguments["caminho_relativo"]
    simbolo = arguments["simbolo"]

    resultado = localizar_simbolo(caminho_projeto, caminho_relativo, simbolo)
    if resultado is None:
        return _falha(
            "SYMBOL_NOT_FOUND",
            f"simbolo '{simbolo}' nao encontrado em '{caminho_relativo}' "
            "(pode ter sido renomeado/removido desde o ultimo ingest, ou o arquivo nao existe mais).",
            executed=True,
        )
    resultado = dict(resultado)
    resultado["arquivo"] = caminho_relativo
    resultado["simbolo"] = simbolo
    try:
        leitura = ler_faixa_projeto(
            caminho_projeto,
            caminho_relativo,
            int(resultado["linha_inicio"]),
            int(resultado["linha_fim"]),
            max_linhas=((ctx or {}).get("config") or {}).get("agent", {}).get(
                "max_read_range_lines", 400,
            ),
        )
    except (ErroLeituraProjeto, KeyError, TypeError, ValueError):
        # Compatibilidade com mocks/implementacoes antigas de localizar_simbolo.
        return _sucesso(resultado)
    resultado.update(leitura)
    resultado["simbolo"] = simbolo
    return _sucesso(resultado)


def _tool_read_file(arguments, ctx):
    """Le o inicio do arquivo e, quando possivel, devolve evidencia com hashes.

    ``read_file`` continua compativel com as chaves antigas ``conteudo`` e
    ``truncado``, mas agora tambem usa o mesmo envelope verificavel de
    ``read_range``. Assim uma leitura real nao e descartada pelo gate de
    grounding apenas porque o modelo escolheu o alias de compatibilidade.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")
    caminho_relativo = arguments["caminho_relativo"]

    config = (ctx or {}).get("config") or {}
    cfg_dicas = config.get("dicas", {})
    max_chars = cfg_dicas.get("max_chars_por_arquivo", 20000)
    codigos = ler_codigo_real([caminho_relativo], caminho_projeto, max_chars_por_arquivo=max_chars)
    info = codigos.get(caminho_relativo)
    if info is None:
        return _falha(
            "FILE_NOT_FOUND",
            f"arquivo '{caminho_relativo}' nao encontrado no disco (removido desde o ultimo ingest?)",
            executed=True,
        )
    if info.get("erro"):
        return _falha("FILE_READ_REJECTED", info["erro"], executed=True)

    max_linhas = config.get("agent", {}).get("max_read_range_lines", 400)
    try:
        leitura = ler_faixa_projeto(
            caminho_projeto, caminho_relativo, 1, max_linhas,
            max_linhas=max_linhas,
        )
    except ErroLeituraProjeto:
        # Mantem a compatibilidade de mocks/estados antigos. Em um projeto real
        # legivel, a chamada acima normalmente produz os hashes verificaveis.
        return _sucesso(info)

    detalhe = dict(info)
    detalhe.update(leitura)
    detalhe["truncado"] = bool(
        info.get("truncado")
        or leitura.get("linha_fim", 0) < leitura.get("total_linhas_arquivo", 0)
    )
    return _sucesso(detalhe)


def _tool_list_tree(arguments, ctx):
    """Lista a arvore fresca do projeto com limites e motivos ignorados."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")
    cfg_agente = ((ctx or {}).get("config") or {}).get("agent", {})
    max_entradas = cfg_agente.get("max_tree_entries", 200)
    max_profundidade = cfg_agente.get("max_tree_depth", 6)
    limite = arguments.get("limite", max_entradas)
    profundidade = arguments.get("profundidade", max_profundidade)
    if limite > max_entradas:
        return _falha(
            "INVALID_ARGUMENT",
            f"limite={limite} excede agent.max_tree_entries={max_entradas}",
        )
    if profundidade > max_profundidade:
        return _falha(
            "INVALID_ARGUMENT",
            f"profundidade={profundidade} excede agent.max_tree_depth={max_profundidade}",
        )
    try:
        resultado = listar_arvore_projeto(
            caminho_projeto,
            limite=limite,
            profundidade=profundidade,
            filtro=arguments.get("filtro"),
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {
            "INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE",
            "RANGE_OUT_OF_BOUNDS",
        } else erro.error_code
        return _falha(codigo, erro.detail, executed=True)
    return _sucesso(resultado)


def _tool_read_range(arguments, ctx):
    """Le uma janela fresca e numerada do disco, nunca do indice."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")
    max_linhas = ((ctx or {}).get("config") or {}).get("agent", {}).get(
        "max_read_range_lines", 400,
    )
    try:
        resultado = ler_faixa_projeto(
            caminho_projeto,
            arguments["caminho_relativo"],
            arguments["linha_inicio"],
            arguments["linha_fim"],
            max_linhas=max_linhas,
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {
            "INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE",
            "RANGE_OUT_OF_BOUNDS",
        } else erro.error_code
        return _falha(codigo, erro.detail, executed=True)
    return _sucesso(resultado)


def _tool_test_patch_dry_run(arguments, ctx):
    """
    Testa uma substituicao de linhas NUMA COPIA temporaria -- nunca
    escreve no arquivo real (engine/codar.py:testar_patch_em_copia).
    Usado pelo Agente pra validar uma mudanca ANTES de propor apply_patch
    (que e WRITE e para o loop em needs_user).
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")

    obrigatorios = ("caminho_relativo", "linha_inicio", "linha_fim", "codigo_novo")
    faltando = [c for c in obrigatorios if arguments.get(c) in (None, "")]
    if faltando:
        return _falha("INVALID_ARGUMENT", f"argumentos obrigatorios faltando: {', '.join(faltando)}")

    try:
        linha_inicio = int(arguments["linha_inicio"])
        linha_fim = int(arguments["linha_fim"])
    except (TypeError, ValueError):
        return _falha("INVALID_ARGUMENT", "'linha_inicio' e 'linha_fim' precisam ser numeros inteiros")

    resultado = testar_patch_em_copia(
        caminho_projeto, arguments["caminho_relativo"], linha_inicio, linha_fim,
        arguments["codigo_novo"],
        file_hash_esperado=arguments["file_hash_esperado"],
        range_hash_esperado=arguments["range_hash_esperado"],
    )
    detail = {
        "message": resultado.get("detalhe", ""),
        "conteudo_resultante": resultado.get("conteudo_resultante"),
    }
    if resultado.get("ok") is True:
        return _sucesso(detail)
    return _falha(resultado.get("error_code") or "DRY_RUN_FAILED", detail, executed=True)


def _tool_run_tests(arguments, ctx):
    """
    Roda a suite de teste real do projeto (pytest/npm test), so se
    config['codar']['testes']['ativado'] estiver ligado e o projeto ja
    tiver esse tipo de teste configurado -- engine/codar.py:
    rodar_testes_projeto ja cuida dessa checagem, aqui so repassa
    config['codar']['testes'] igual ao que
    engine/engine.py:_aplicar_proposta_pendente ja faz pro Codar.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")
    cfg_testes = ((ctx or {}).get("config") or {}).get("codar", {}).get("testes", {})
    resultado = rodar_testes_projeto(caminho_projeto, cfg_testes)
    detail = resultado.get("detalhe", "")
    if resultado.get("executado") is not True and resultado.get("ok") is True:
        return _pulado(detail)
    if resultado.get("ok") is True:
        return _sucesso(detail)
    return _falha(
        "TESTS_REFUSED" if resultado.get("recusado") else "TESTS_FAILED",
        detail,
        executed=resultado.get("executado") is True,
    )


# ---------------------------------------------------------------------------
# Tool WRITE -- unica que exige confirmacao (engine/agent.py checa
# permission == "WRITE" antes de chamar executar_tool).
# ---------------------------------------------------------------------------

def _tool_apply_patch(arguments, ctx):
    """
    Escreve de verdade no arquivo real -- so deve rodar depois que o
    loop principal ja passou pela pausa de confirmacao (needs_user).
    Delega 100% pra engine/codar.py:aplicar_patch, que re-le o arquivo
    na hora, aborta se mudou desde que os argumentos foram montados,
    faz backup e reverte sozinho se a checagem pos-escrita falhar --
    nenhuma dessas garantias e reimplementada aqui.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("PROJECT_NOT_INDEXED", "nenhum projeto indexado (memory/projeto.json sem caminho_origem)")

    obrigatorios = (
        "caminho_relativo", "linha_inicio", "linha_fim",
        "codigo_original_esperado", "codigo_novo",
        "file_hash_esperado", "range_hash_esperado",
    )
    faltando = [c for c in obrigatorios if arguments.get(c) in (None, "")]
    if faltando:
        return _falha("INVALID_ARGUMENT", f"argumentos obrigatorios faltando: {', '.join(faltando)}")

    try:
        linha_inicio = int(arguments["linha_inicio"])
        linha_fim = int(arguments["linha_fim"])
    except (TypeError, ValueError):
        return _falha("INVALID_ARGUMENT", "'linha_inicio' e 'linha_fim' precisam ser numeros inteiros")

    config = (ctx or {}).get("config") or {}
    cfg_codar = config.get("codar", {})
    backups_dir = os.path.join(CONTEXT_DIR, "backups") if cfg_codar.get("fazer_backup", True) else None
    cfg_testes = cfg_codar.get("testes", {})

    resultado = aplicar_patch(
        caminho_projeto, arguments["caminho_relativo"], linha_inicio, linha_fim,
        arguments["codigo_original_esperado"], arguments["codigo_novo"],
        backups_dir=backups_dir, cfg_testes=cfg_testes,
        cfg_retention=config.get("retention", {}),
        file_hash_esperado=arguments["file_hash_esperado"],
        range_hash_esperado=arguments["range_hash_esperado"],
        incluir_snapshot=True,
        executar_testes=False,
    )
    detail = {
        "message": resultado.get("detalhe", ""),
        "backup_path": resultado.get("backup_path"),
        "outcome": resultado.get("outcome"),
        "rollback_snapshot": resultado.get("rollback_snapshot"),
        "file_hash_antes": resultado.get("file_hash_antes"),
        "range_hash_antes": resultado.get("range_hash_antes"),
        "file_hash_depois": resultado.get("file_hash_depois"),
        "linha_fim_final": resultado.get("linha_fim_final"),
    }
    if resultado.get("ok") is True:
        return _sucesso(detail, changed=True)
    return _falha(
        resultado.get("error_code") or "PATCH_FAILED", detail, executed=True,
        changed=resultado.get("changed") is True,
    )


def reverter_patch_confirmado(snapshot, ctx):
    """Rollback interno do ciclo 46; nao e uma tool disponivel a LLM."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return {
            "ok": False, "changed": False, "error_code": "PROJECT_NOT_INDEXED",
            "detalhe": "nenhum projeto indexado para restaurar a edicao",
        }
    return restaurar_snapshot_patch(caminho_projeto, snapshot)


# ---------------------------------------------------------------------------
# Registro -- e' isso que engine/agent.py importa. Nome da tool = chave
# que a LLM usa em {"tool": "...", "arguments": {...}}; "permission"
# decide se o loop para em needs_user (WRITE) ou executa direto (READ).
# ---------------------------------------------------------------------------

def _schema_objeto(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_CAMINHO = {
    "type": "string", "minLength": 1,
    "description": "Relative path inside the project root.",
}
_LINHA = {"type": "integer", "minimum": 1}
_CODIGO = {"type": "string", "minLength": 1}
_HASH = {
    "type": "string", "minLength": 64, "maxLength": 64,
    "pattern": "^[0-9a-f]{64}$",
    "description": "Hexadecimal SHA-256 returned by a fresh read.",
}


TOOLS = {
    "read_metadata": {
        "name": "read_metadata",
        "description": "Read indexed understanding metadata for a known file.",
        "permission": "READ",
        "input_schema": _schema_objeto(
            {"caminho_relativo": _CAMINHO}, ["caminho_relativo"],
        ),
        "output_schema": "Standard envelope; detail contains file metadata.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_read_metadata,
    },
    "list_tree": {
        "name": "list_tree",
        "description": "List the fresh project tree with limit, depth, filter, and ignored-item counts.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "limite": {"type": "integer", "minimum": 1},
            "profundidade": {"type": "integer", "minimum": 1},
            "filtro": {"type": "string", "minLength": 1},
        }),
        "output_schema": "Standard envelope; detail contains tree entries, truncation, and ignored_by_reason counts.",
        "compat_aliases": {"max_depth": "profundidade"},
        "fn": _tool_list_tree,
    },
    "search_code": {
        "name": "search_code",
        "description": "Locate code through the index, then freshly re-read each result from disk with numbered lines and hashes.",
        "permission": "READ",
        "input_schema": _schema_objeto(
            {"pergunta": {"type": "string", "minLength": 1}}, ["pergunta"],
        ),
        "output_schema": "Standard envelope; detail.resultados contains file, range, symbol, score, numbered snippet, content_hash, and file_hash.",
        "compat_aliases": {"query": "pergunta"},
        "fn": _tool_search_code,
    },
    "find_symbol": {
        "name": "find_symbol",
        "description": "Locate the fresh line range of a symbol inside a known file.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "simbolo": {"type": "string", "minLength": 1},
        }, ["caminho_relativo", "simbolo"]),
        "output_schema": "Standard envelope; detail contains the range, original code, and total line count.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_find_symbol,
    },
    "read_range": {
        "name": "read_range",
        "description": "Read a small, fresh, numbered range directly from disk; prefer this over read_file.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "linha_inicio": _LINHA,
            "linha_fim": _LINHA,
        }, ["caminho_relativo", "linha_inicio", "linha_fim"]),
        "output_schema": "Standard envelope; detail contains the actual range, numbered snippet, total lines, content_hash, and file_hash.",
        "compat_aliases": {
            "arquivo": "caminho_relativo",
            "linha_inicial": "linha_inicio",
        },
        "fn": _tool_read_range,
    },
    "read_file": {
        "name": "read_file",
        "description": "Read the beginning of a file and return verifiable lines/hashes; read_range remains preferred for exact ranges.",
        "permission": "READ",
        "input_schema": _schema_objeto(
            {"caminho_relativo": _CAMINHO}, ["caminho_relativo"],
        ),
        "output_schema": "Standard envelope; detail preserves content/truncation and, when readable, includes a numbered range, content_hash, and file_hash.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_read_file,
    },
    "test_patch_dry_run": {
        "name": "test_patch_dry_run",
        "description": "Test a range replacement in a temporary copy without writing to the project.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "linha_inicio": _LINHA,
            "linha_fim": _LINHA,
            "codigo_novo": _CODIGO,
            "file_hash_esperado": _HASH,
            "range_hash_esperado": _HASH,
        }, [
            "caminho_relativo", "linha_inicio", "linha_fim", "codigo_novo",
            "file_hash_esperado", "range_hash_esperado",
        ]),
        "output_schema": "Standard envelope; detail contains the dry-run result and resulting content.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_test_patch_dry_run,
    },
    "run_tests": {
        "name": "run_tests",
        "description": "Run the configured test suite inside the sandbox.",
        "permission": "EXEC",
        "input_schema": _schema_objeto(),
        "output_schema": "Standard envelope; executed distinguishes an executed suite from unavailable tests.",
        "compat_aliases": {},
        "fn": _tool_run_tests,
    },
    "apply_patch": {
        "name": "apply_patch",
        "description": "Apply a confirmed range replacement with original-content preconditions, rollback, and tests.",
        "permission": "WRITE",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "linha_inicio": _LINHA,
            "linha_fim": _LINHA,
            "codigo_original_esperado": _CODIGO,
            "codigo_novo": _CODIGO,
            "file_hash_esperado": _HASH,
            "range_hash_esperado": _HASH,
        }, [
            "caminho_relativo", "linha_inicio", "linha_fim",
            "codigo_original_esperado", "codigo_novo",
            "file_hash_esperado", "range_hash_esperado",
        ]),
        "output_schema": "Standard envelope; STALE_PATCH aborts without writing; detail keeps hashes, final range, and the internal rollback snapshot.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_apply_patch,
    },
}

# Limites ficam no proprio registro. O catalogo resolve as chaves de
# configuracao para valores numericos antes de chegar ao modelo.
for _entrada_tool in TOOLS.values():
    _entrada_tool.setdefault("limits", {})
TOOLS["list_tree"]["limits"] = {
    "max_entradas": {"config_key": "agent.max_tree_entries", "default": 200},
    "max_profundidade": {"config_key": "agent.max_tree_depth", "default": 6},
}
TOOLS["search_code"]["limits"] = {
    "max_linhas_por_resultado": {"config_key": "agent.max_read_range_lines", "default": 400},
}
TOOLS["read_range"]["limits"] = {
    "max_linhas": {"config_key": "agent.max_read_range_lines", "default": 400},
}
TOOLS["read_file"]["limits"] = {
    "max_caracteres": {"config_key": "dicas.max_chars_por_arquivo", "default": 20000},
}


def _ler_config_key(config, caminho, default):
    valor = config or {}
    for parte in caminho.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return default
        valor = valor[parte]
    return valor


def gerar_catalogo_tools(registro=None, config=None):
    """Gera o catalogo publico diretamente do registro executavel."""
    catalogo = []
    fonte = TOOLS if registro is None else registro
    for chave, entrada in fonte.items():
        limites = {}
        for nome_limite, origem in (entrada.get("limits") or {}).items():
            limites[nome_limite] = _ler_config_key(
                config, origem["config_key"], origem["default"],
            )
        catalogo.append({
            "name": entrada.get("name", chave),
            "description": entrada.get("description", ""),
            "permission": entrada.get("permission"),
            "input_schema": entrada.get("input_schema", _schema_objeto()),
            "output_schema": entrada.get("output_schema", "Standard tool envelope."),
            "limits": limites,
        })
    return catalogo


def _tipo_json_valido(valor, tipo):
    if tipo == "integer":
        return isinstance(valor, int) and not isinstance(valor, bool)
    if tipo == "number":
        return isinstance(valor, (int, float)) and not isinstance(valor, bool)
    if tipo == "string":
        return isinstance(valor, str)
    if tipo == "boolean":
        return isinstance(valor, bool)
    if tipo == "object":
        return isinstance(valor, dict)
    if tipo == "array":
        return isinstance(valor, list)
    return False


def validar_chamada_tool(nome, arguments, registro=None):
    """Normaliza aliases e valida argumentos antes de qualquer execucao."""
    registro = TOOLS if registro is None else registro
    entrada = registro.get(nome)
    if entrada is None:
        conhecidas = ", ".join(sorted(registro))
        return None, _falha(
            "TOOL_NOT_FOUND",
            f"tool '{nome}' nao existe. Ferramentas disponiveis: {conhecidas}",
        )
    if not isinstance(arguments, dict):
        return None, _falha("INVALID_ARGUMENT", "arguments precisa ser um objeto JSON")

    # Registros minimos usados por integracoes antigas/testes continuam
    # aceitos; o registro real e testado para sempre possuir schema.
    schema = entrada.get("input_schema")
    if not isinstance(schema, dict):
        return dict(arguments), None

    aliases = entrada.get("compat_aliases") or {}
    normalizados = {}
    for chave, valor in arguments.items():
        canonica = aliases.get(chave, chave)
        if canonica in normalizados and normalizados[canonica] != valor:
            return None, _falha(
                "INVALID_ARGUMENT",
                f"argumentos conflitantes para '{canonica}'",
            )
        normalizados[canonica] = valor

    propriedades = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        desconhecidas = sorted(set(normalizados) - set(propriedades))
        if desconhecidas:
            return None, _falha(
                "INVALID_ARGUMENT",
                "argumento(s) desconhecido(s): " + ", ".join(desconhecidas),
            )

    faltando = [nome_campo for nome_campo in schema.get("required", []) if nome_campo not in normalizados]
    if faltando:
        return None, _falha(
            "INVALID_ARGUMENT",
            "argumento(s) obrigatorio(s) faltando: " + ", ".join(faltando),
        )

    for nome_campo, valor in normalizados.items():
        regra = propriedades.get(nome_campo)
        if regra is None:
            continue
        tipo = regra.get("type")
        if not _tipo_json_valido(valor, tipo):
            return None, _falha(
                "INVALID_ARGUMENT",
                f"argumento '{nome_campo}' precisa ser do tipo {tipo}",
            )
        if tipo == "string" and len(valor.strip()) < regra.get("minLength", 0):
            return None, _falha("INVALID_ARGUMENT", f"argumento '{nome_campo}' nao pode ser vazio")
        if tipo == "string" and "maxLength" in regra and len(valor) > regra["maxLength"]:
            return None, _falha(
                "INVALID_ARGUMENT",
                f"argumento '{nome_campo}' precisa ter no maximo {regra['maxLength']} caracteres",
            )
        if tipo == "string" and regra.get("pattern") and not re.fullmatch(regra["pattern"], valor):
            return None, _falha(
                "INVALID_ARGUMENT",
                f"argumento '{nome_campo}' nao corresponde ao formato esperado",
            )
        if tipo in ("integer", "number"):
            if "minimum" in regra and valor < regra["minimum"]:
                return None, _falha(
                    "INVALID_ARGUMENT",
                    f"argumento '{nome_campo}' precisa ser >= {regra['minimum']}",
                )
            if "maximum" in regra and valor > regra["maximum"]:
                return None, _falha(
                    "INVALID_ARGUMENT",
                    f"argumento '{nome_campo}' precisa ser <= {regra['maximum']}",
                )
    if "linha_inicio" in normalizados and "linha_fim" in normalizados:
        if normalizados["linha_fim"] < normalizados["linha_inicio"]:
            return None, _falha(
                "INVALID_ARGUMENT",
                "argumento 'linha_fim' precisa ser >= linha_inicio",
            )
    return normalizados, None


def executar_tool(nome, arguments, ctx):
    """
    Ponto de entrada unico chamado pelo loop principal
    (engine/agent.py:executar_agente). Nunca deixa uma excecao de tool
    derrubar a tarefa inteira -- vira o envelope padrao com
    `error_code="TOOL_EXECUTION_ERROR"` e o loop segue (o Agente ve o
    erro na proxima observacao e decide o que fazer, mesmo espirito de
    _parse_resposta_analista nunca travar o ciclo por causa de uma falha
    isolada).
    """
    arguments, erro_validacao = validar_chamada_tool(nome, arguments, registro=TOOLS)
    if erro_validacao is not None:
        return erro_validacao
    entrada = TOOLS[nome]
    try:
        resultado = entrada["fn"](arguments, ctx or {})
        if not isinstance(resultado, dict) or set(resultado) != set(_CAMPOS_RESULTADO):
            return _falha(
                "INVALID_TOOL_RESULT",
                f"tool '{nome}' devolveu um resultado fora do contrato padrao",
                executed=True,
            )
        return resultado
    except Exception as e:
        return _falha("TOOL_EXECUTION_ERROR", f"tool '{nome}' falhou ao executar: {e}", executed=True)
