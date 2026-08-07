#!/usr/bin/env python3
"""Executable tool registry for the LLM-first core.

The model chooses tools; this module validates arguments, executes live workspace
operations, and always returns one standard result envelope. It contains no
semantic routing or alternate reasoning path. READ/EXEC tools run directly.
WRITE tools are invoked by the runtime only after a successful dry-run and an
explicit user confirmation.

``ctx`` supplies the validated config and the live project root. Indexed retrieval is not required.
"""
import os
import re
import sys
import subprocess

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from eyle.core.workspace_io import (  # noqa: E402
    ErroLeituraProjeto,
    ler_faixa_projeto,
    listar_arvore_projeto,
)
from eyle.core.editing import (  # noqa: E402
    localizar_simbolo,
    localizar_simbolo_no_projeto,
    testar_patch_em_copia,
    rodar_testes_projeto,
    aplicar_patch,
    restaurar_snapshot_patch,
)
from eyle.core.transactions import (  # noqa: E402
    dry_run_patch_set, apply_patch_set, rollback_patch_set,
)
from eyle.core.memory import search_memory, store_memory  # noqa: E402
from eyle.core.project_inspection import (  # noqa: E402
    calculate as calculate_expression,
    count_tokens as count_project_tokens,
    inspect_project as inspect_project_signals,
    project_stats as measure_project_stats,
)
from eyle.core.git_tools import git_status as inspect_git_status, git_diff as inspect_git_diff  # noqa: E402
from eyle.core.execution_trace import build_execution_trace, filter_execution_trace  # noqa: E402

PROJECT_BASE_DIR = os.path.dirname(BASE_DIR)
MEMORY_DIR = os.path.join(PROJECT_BASE_DIR, "memory")
CONTEXT_DIR = os.path.join(PROJECT_BASE_DIR, "context")

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


def _pulado(detail, error_code=None):
    return _resultado("skipped", True, False, error_code=error_code, detail=detail)


def _caminho_projeto(ctx):
    """Extrai caminho_origem do projeto ativo."""
    projeto = (ctx or {}).get("projeto") or {}
    return projeto.get("caminho_origem")



# ---------------------------------------------------------------------------
# Tools READ
# ---------------------------------------------------------------------------

def _tool_search_code(arguments, ctx):
    """Search the live workspace directly and return verifiable source ranges."""
    query = arguments["query"].strip()
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    config = (ctx or {}).get("config") or {}
    max_lines = int(config.get("agent", {}).get("max_read_range_lines", 400))
    matches = []
    try:
        completed = subprocess.run(
            ["rg", "--line-number", "--column", "--no-heading", "--color", "never", "--fixed-strings", query, root],
            capture_output=True, text=True, timeout=20, check=False,
        )
        rows = completed.stdout.splitlines()[:40]
    except (FileNotFoundError, subprocess.SubprocessError):
        rows = []
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {".git","node_modules","__pycache__",".venv","venv"}]
            for name in files:
                path=os.path.join(current,name)
                try:
                    with open(path,"r",encoding="utf-8") as fh:
                        for number,line in enumerate(fh,1):
                            if query in line:
                                rows.append(f"{path}:{number}:1:{line.rstrip()}")
                                if len(rows)>=40: break
                except (OSError,UnicodeError): pass
                if len(rows)>=40: break
            if len(rows)>=40: break
    for row in rows:
        parts=row.split(":",4)
        if len(parts)<4: continue
        path, line_text = parts[0], parts[1]
        try: line=int(line_text)
        except ValueError: continue
        rel=os.path.relpath(path,root).replace("\\","/")
        try:
            reading=ler_faixa_projeto(root,rel,max(1,line-3),line+3,max_linhas=max_lines)
        except ErroLeituraProjeto: continue
        matches.append(reading)
    return _sucesso({"resultados":matches,"arquivos_relevantes":sorted({m.get("arquivo") for m in matches if m.get("arquivo")}),"falhas_leitura":[]})


def _tool_find_symbol(arguments, ctx):
    """Locate a symbol in a known file or across the live project."""
    root=_caminho_projeto(ctx)
    if not root: return _falha("WORKSPACE_NOT_AVAILABLE","nenhum workspace ativo")
    symbol=arguments["simbolo"]
    rel=arguments.get("caminho_relativo")
    result=localizar_simbolo(root,rel,symbol) if rel else localizar_simbolo_no_projeto(root,symbol)
    if result is None:
        return _falha("SYMBOL_NOT_FOUND",f"símbolo '{symbol}' não encontrado",executed=True)
    if isinstance(result,list): result=result[0] if len(result)==1 else {"matches":result}
    if result.get("matches") is not None: return _sucesso(result)
    result=dict(result); rel=result.get("arquivo") or rel; result["arquivo"]=rel; result["simbolo"]=symbol
    try:
        reading=ler_faixa_projeto(root,rel,int(result["linha_inicio"]),int(result["linha_fim"]),max_linhas=((ctx or {}).get("config") or {}).get("agent",{}).get("max_read_range_lines",400))
        result.update(reading); result["simbolo"]=symbol
    except Exception: pass
    return _sucesso(result)


def _tool_read_file(arguments, ctx):
    """Le o inicio do arquivo e, quando possivel, devolve evidencia com hashes.

    ``read_file`` continua compativel com as chaves antigas ``conteudo`` e
    ``truncado``, mas agora tambem usa o mesmo envelope verificavel de
    ``read_range``. Assim uma leitura real nao e descartada pelo gate de
    validação apenas porque o modelo escolheu um alias de leitura.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    caminho_relativo = arguments["caminho_relativo"]

    config = (ctx or {}).get("config") or {}
    max_linhas = config.get("agent", {}).get("max_read_range_lines", 400)
    try:
        leitura = ler_faixa_projeto(
            caminho_projeto, caminho_relativo, 1, max_linhas,
            max_linhas=max_linhas,
        )
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)

    leitura = dict(leitura)
    leitura["conteudo"] = leitura.get("conteudo") or leitura.get("conteudo_raw") or leitura.get("trecho") or ""
    leitura["truncado"] = bool(
        leitura.get("linha_fim", 0) < leitura.get("total_linhas_arquivo", 0)
    )
    return _sucesso(leitura)


def _tool_list_tree(arguments, ctx):
    """Lista a arvore fresca do projeto com limites e motivos ignorados."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_agente = ((ctx or {}).get("config") or {}).get("agent", {})
    max_entradas = cfg_agente.get("max_tree_entries", 200)
    max_profundidade = cfg_agente.get("max_tree_depth", 6)
    max_secret_scan_bytes = cfg_agente.get("max_secret_scan_bytes", 64 * 1024)
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
            max_secret_scan_bytes=max_secret_scan_bytes,
        )
    except ErroLeituraProjeto as erro:
        codigo = "INVALID_ARGUMENT" if erro.error_code in {
            "INVALID_ARGUMENT", "INVALID_RANGE", "RANGE_TOO_LARGE",
            "RANGE_OUT_OF_BOUNDS",
        } else erro.error_code
        return _falha(codigo, erro.detail, executed=True)
    return _sucesso(resultado)


def _tool_calculate(arguments, ctx):
    """Evaluate arithmetic deterministically instead of asking the LLM to do it mentally."""
    try:
        return _sucesso(calculate_expression(arguments["expression"]))
    except (ValueError, SyntaxError) as erro:
        return _falha("INVALID_EXPRESSION", str(erro), executed=True)


def _tool_project_stats(arguments, ctx):
    """Measure objective project size/statistics over the safe text workspace."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(measure_project_stats(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_count_tokens(arguments, ctx):
    """Measure project text and convert it to a truthful token estimate."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        detail = count_project_tokens(
            root, (ctx or {}).get("config") or {},
            path=arguments.get("caminho_relativo"),
            tokenizer=arguments.get("tokenizer"),
        )
        return _sucesso(detail)
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_inspect_project(arguments, ctx):
    """Return objective structural/relation signals without ranking file importance."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    try:
        return _sucesso(inspect_project_signals(root, (ctx or {}).get("config") or {}))
    except ErroLeituraProjeto as erro:
        return _falha(erro.error_code, erro.detail, executed=True)


def _tool_agent_info(arguments, ctx):
    """Expose full registered capability separately from current phase availability."""
    config = (ctx or {}).get("config") or {}
    available_names = {str(name) for name in ((ctx or {}).get("available_tools") or [])}
    registered = []
    for name, item in sorted(TOOLS.items()):
        registered.append({
            "name": item.get("name", name),
            "permission": item.get("permission"),
            "category": item.get("category", "READ_ONLY"),
            "effects": list(item.get("effects") or ["NONE"]),
            "description": item.get("description", ""),
        })
    available = [item for item in registered if item.get("name") in available_names]
    return _sucesso({
        "name": "Eyle",
        "app_version": config.get("app_version"),
        "revision": config.get("revision"),
        "registered_tools": registered,
        "available_tools": available,
        # Compatibility alias for older UI/history consumers. It deliberately
        # means the complete registry now, not the phase-local subset.
        "tools": registered,
        "write_enabled": bool(((config.get("codar") or {}).get("ativado", True))),
        "write_confirmation_required": True,
        "note": (
            "registered_tools is the complete executable registry; available_tools "
            "is only the subset callable in the current phase. Workspace writes are supervised."
        ),
    })


def _tool_read_range(arguments, ctx):
    """Le uma janela fresca e numerada do disco, nunca do indice."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
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
    escreve no arquivo real; usa a camada de edição segura do core.
    Usado pelo Agente pra validar uma mudanca ANTES de propor apply_patch
    (que e WRITE e para o loop em needs_user).
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")

    obrigatorios = ("caminho_relativo", "linha_inicio", "linha_fim", "codigo_novo")
    faltando = [
        campo for campo in obrigatorios
        if campo not in arguments or arguments.get(campo) is None
        or (campo != "codigo_novo" and arguments.get(campo) == "")
    ]
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


def _tool_test_patch_set_dry_run(arguments, ctx):
    """Validate a free-form multi-file transaction without writing."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    patches = arguments.get("patches")
    resultado = dry_run_patch_set(caminho_projeto, patches)
    if resultado.get("ok"):
        return _sucesso({
            "message": resultado.get("message"),
            "prepared_patches": resultado.get("prepared_patches") or [],
            "files": resultado.get("files") or [],
        })
    return _falha(resultado.get("error_code") or "DRY_RUN_FAILED", resultado.get("message"), executed=True)


def _tool_apply_patch_set(arguments, ctx):
    """Apply a previously dry-run transaction after user confirmation."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    resultado = apply_patch_set(caminho_projeto, arguments.get("patches") or [])
    if resultado.get("ok"):
        return _sucesso({
            "message": resultado.get("message"),
            "applied_patches": resultado.get("applied_patches") or [],
            "files": resultado.get("files") or [],
        }, changed=True)
    return _falha(resultado.get("error_code") or "PATCH_TRANSACTION_FAILED", resultado.get("message"), executed=True)


def reverter_patch_set_confirmado(snapshot, ctx):
    return rollback_patch_set(snapshot or [])


def _pytest_summary(output):
    """Return the last concise pytest summary line without exposing huge logs."""
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if any(token in lowered for token in (" passed", " failed", " skipped", " error", " errors")):
            return line[:500]
    return lines[-1][:500] if lines else ""


def _tool_run_tests(arguments, ctx):
    """Run the real suite, optionally focused to a safe pytest file/directory."""
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_testes = ((ctx or {}).get("config") or {}).get("codar", {}).get("testes", {})
    if not cfg_testes.get("ativado", False):
        return _pulado(
            "A execução de testes está desativada em config['codar']['testes']['ativado'].",
            error_code="TESTS_DISABLED",
        )
    resultado = rodar_testes_projeto(caminho_projeto, cfg_testes, scope=arguments.get("scope"))
    output = str(resultado.get("saida_resumida") or "")
    detail = {
        "command": resultado.get("comando"),
        "returncode": resultado.get("codigo"),
        "scope": resultado.get("scope"),
        "backend": resultado.get("backend"),
        "runner": resultado.get("runner"),
        "tests_detected": bool(resultado.get("tests_detected")),
        "summary": _pytest_summary(output) or str(resultado.get("detalhe") or "")[:500],
        "output_tail": output[-3000:],
    }
    if resultado.get("executado") is not True and resultado.get("ok") is True:
        return _pulado(detail, error_code="TESTS_NOT_FOUND")
    if resultado.get("ok") is True:
        return _sucesso(detail)
    error_code = resultado.get("error_code") or (
        "TESTS_REFUSED" if resultado.get("recusado") else "TESTS_FAILED"
    )
    return _falha(
        error_code, detail, executed=resultado.get("executado") is True,
    )


def _tool_execution_trace(arguments, ctx):
    """Inspect sanitized factual execution history for the current or one persisted job."""
    section = str(arguments.get("section") or "all").strip().lower()
    turn = arguments.get("turn")
    limit = int(arguments.get("limit") or 100)
    requested_job_id = arguments.get("job_id")

    current = (ctx or {}).get("execution_trace")
    current_job_id = None
    if isinstance(current, dict):
        current_job_id = ((current.get("summary") or {}).get("job_id")
                          if isinstance(current.get("summary"), dict) else None)

    if requested_job_id is None or (current_job_id is not None and int(requested_job_id) == int(current_job_id)):
        if not isinstance(current, dict):
            return _falha("EXECUTION_TRACE_UNAVAILABLE", "o trace da sessão atual não está disponível neste contexto")
        trace = current
    else:
        try:
            from eyle.runtime import queue as runtime_queue
        except Exception as error:
            return _falha("EXECUTION_TRACE_UNAVAILABLE", f"não foi possível acessar o histórico persistido: {error}")
        registro = runtime_queue.obter(int(requested_job_id))
        if not isinstance(registro, dict):
            return _falha("JOB_NOT_FOUND", f"job #{int(requested_job_id)} não foi encontrado")
        resultado = registro.get("resultado") if isinstance(registro.get("resultado"), dict) else {}
        details = resultado.get("details") if isinstance(resultado.get("details"), dict) else {}
        if not details:
            return _falha(
                "EXECUTION_TRACE_NOT_READY",
                f"job #{int(requested_job_id)} ainda não possui detalhes de execução persistidos",
            )
        progresso = registro.get("progresso") if isinstance(registro.get("progresso"), dict) else {}
        trace = build_execution_trace(
            details,
            job_id=int(requested_job_id),
            status=registro.get("status"),
            created_at=registro.get("criado_em"),
            started_at=registro.get("iniciado_em"),
            completed_at=registro.get("concluido_em"),
            duration_seconds=progresso.get("elapsed_seconds"),
            limit=max(100, limit),
        )
    try:
        return _sucesso(filter_execution_trace(trace, section=section, turn=turn, limit=limit))
    except (TypeError, ValueError) as error:
        return _falha("INVALID_ARGUMENT", str(error))


def _tool_git_status(arguments, ctx):
    """Inspect Git working-tree state without modifying the repository."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    result = inspect_git_status(root, max_entries=int(arguments.get("max_entries") or 200))
    if result.get("ok"):
        return _sucesso(result)
    return _falha(result.get("error_code") or "GIT_STATUS_FAILED", result.get("detail"), executed=True)


def _tool_git_diff(arguments, ctx):
    """Inspect a bounded Git diff; raw diff is available to the LLM but not public history."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    cfg_agent = ((ctx or {}).get("config") or {}).get("agent", {})
    result = inspect_git_diff(
        root,
        path=arguments.get("path"),
        staged=bool(arguments.get("staged", False)),
        context_lines=int(arguments.get("context_lines") or 3),
        max_chars=int(cfg_agent.get("max_git_diff_chars", 6000) or 6000),
    )
    if result.get("ok"):
        return _sucesso(result)
    return _falha(result.get("error_code") or "GIT_DIFF_FAILED", result.get("detail"), executed=True)


def _tool_memory_search(arguments, ctx):
    """Search external project memory only when the agent requests it."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    query = str(arguments.get("query") or "")
    limit = int(arguments.get("limit") or 8)
    try:
        results = search_memory(MEMORY_DIR, root, query=query, limit=limit)
    except (OSError, ValueError) as error:
        return _falha("MEMORY_READ_FAILED", str(error), executed=True)
    return _sucesso({"entries": results, "count": len(results)})


def _tool_memory_store(arguments, ctx):
    """Store one evidence-backed fact outside the source workspace."""
    root = _caminho_projeto(ctx)
    if not root:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")
    evidence = (ctx or {}).get("evidence") or {}
    evidence_ids = [str(item) for item in arguments.get("evidence_ids") or []]
    if not evidence_ids:
        return _falha("MEMORY_REQUIRES_EVIDENCE", "informe evidence_ids da tarefa atual")
    missing = [item for item in evidence_ids if item not in evidence]
    if missing:
        return _falha("MEMORY_UNKNOWN_EVIDENCE", ", ".join(missing))
    files = []
    for evidence_id in evidence_ids:
        item = evidence.get(evidence_id) or {}
        if item.get("arquivo") and item.get("file_hash"):
            files.append({"path": item["arquivo"], "file_hash": item["file_hash"]})
    try:
        entry = store_memory(
            MEMORY_DIR, root, str(arguments.get("text") or ""),
            kind=str(arguments.get("kind") or "fact"), files=files,
        )
    except (OSError, ValueError) as error:
        return _falha("MEMORY_WRITE_FAILED", str(error), executed=True)
    return _sucesso({"entry": entry})


# ---------------------------------------------------------------------------
# Tool WRITE -- invoked only by the core runtime after confirmation.
# ---------------------------------------------------------------------------

def _tool_apply_patch(arguments, ctx):
    """
    Write to the live file only after the core confirmation pause.
    The editing layer rereads preconditions, writes atomically, preserves a
    rollback snapshot, and reports the exact result.
    """
    caminho_projeto = _caminho_projeto(ctx)
    if not caminho_projeto:
        return _falha("WORKSPACE_NOT_AVAILABLE", "nenhum workspace ativo")

    obrigatorios = (
        "caminho_relativo", "linha_inicio", "linha_fim",
        "codigo_original_esperado", "codigo_novo",
        "file_hash_esperado", "range_hash_esperado",
    )
    faltando = [
        campo for campo in obrigatorios
        if campo not in arguments or arguments.get(campo) is None
        or (campo not in {"codigo_original_esperado", "codigo_novo"} and arguments.get(campo) == "")
    ]
    if faltando:
        return _falha("INVALID_ARGUMENT", f"argumentos obrigatorios faltando: {', '.join(faltando)}")
    if not isinstance(arguments.get("codigo_original_esperado"), str):
        return _falha("INVALID_ARGUMENT", "'codigo_original_esperado' precisa ser texto")

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
            "ok": False, "changed": False, "error_code": "WORKSPACE_NOT_AVAILABLE",
            "detalhe": "nenhum workspace ativo para restaurar a edição",
        }
    return restaurar_snapshot_patch(caminho_projeto, snapshot)


# ---------------------------------------------------------------------------
# Registry consumed by eyle.core.agent. Tool names are the public protocol.
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
_LINHA = {"type": "integer", "minimum": 1, "description": "1-based line number inside the selected project file."}
_CODIGO = {"type": "string", "minLength": 1, "description": "Source text supplied for the requested code operation."}
_CODIGO_NOVO = {
    "type": "string", "minLength": 0,
    "description": "Replacement code. Empty string is valid for deletion.",
}
_CODIGO_ORIGINAL = {"type": "string", "minLength": 0, "description": "Exact original source text expected before a confirmed replacement."}
_HASH = {
    "type": "string", "minLength": 64, "maxLength": 64,
    "pattern": "^[0-9a-f]{64}$",
    "description": "Hexadecimal SHA-256 returned by a fresh read.",
}


TOOLS = {
    "calculate": {
        "name": "calculate",
        "description": "Evaluate one arithmetic expression deterministically with decimal-safe math.",
        "permission": "EXEC",
        "input_schema": _schema_objeto({
            "expression": {"type": "string", "minLength": 1, "maxLength": 500, "description": "Arithmetic expression containing numeric values and supported operators."},
        }, ["expression"]),
        "output_schema": "Standard envelope; detail contains expression, deterministic decimal result, exact/approximate status, and precision metadata.",
        "compat_aliases": {"expressao": "expression"},
        "fn": _tool_calculate,
    },
    "agent_info": {
        "name": "agent_info",
        "description": "Return Eyle runtime identity, release metadata, executable tool registry and write policy.",
        "permission": "READ",
        "input_schema": _schema_objeto(),
        "output_schema": "Standard envelope; detail contains name, release identity, tool names/permissions/categories/effects/descriptions, and write policy.",
        "compat_aliases": {},
        "fn": _tool_agent_info,
    },
    "project_stats": {
        "name": "project_stats",
        "description": "Measure safe project text: files, directories, lines, characters, bytes, extensions, and languages.",
        "permission": "READ",
        "input_schema": _schema_objeto(),
        "output_schema": "Standard envelope; detail contains deterministic project measurements and scan completeness.",
        "compat_aliases": {},
        "fn": _tool_project_stats,
    },
    "count_tokens": {
        "name": "count_tokens",
        "description": "Measure token count or a truthful token estimate for safe project text.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "caminho_relativo": {"type": "string", "minLength": 1, "description": "Optional project-relative file or directory to measure instead of the whole project."},
            "tokenizer": {"type": "string", "minLength": 1, "description": "Optional tokenizer/model identifier; if unavailable, the configured truthful fallback is reported."},
        }),
        "output_schema": "Standard envelope; detail includes exact=false when using the configured character/token fallback.",
        "compat_aliases": {"path": "caminho_relativo", "model": "tokenizer"},
        "fn": _tool_count_tokens,
    },
    "inspect_project": {
        "name": "inspect_project",
        "description": "Inspect objective project structure and relation signals such as languages, entrypoints, imports, tests, CI, frameworks and manifests.",
        "permission": "READ",
        "input_schema": _schema_objeto(),
        "output_schema": "Standard envelope; detail contains objective structural and relation signals with hashes and scan completeness.",
        "compat_aliases": {},
        "fn": _tool_inspect_project,
    },
    "list_tree": {
        "name": "list_tree",
        "description": "List the fresh project tree with limit, depth, filter, and ignored-item counts.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "limite": {"type": "integer", "minimum": 1, "description": "Maximum number of tree entries to return before marking the result truncated."},
            "profundidade": {"type": "integer", "minimum": 1, "description": "Maximum directory depth to traverse from the project root."},
            "filtro": {"type": "string", "minLength": 1, "description": "Optional filename/path glob-style filter applied to returned tree entries."},
        }),
        "output_schema": "Standard envelope; detail contains tree entries, truncation, and ignored_by_reason counts.",
        "compat_aliases": {"max_depth": "profundidade"},
        "fn": _tool_list_tree,
    },
    "search_code": {
        "name": "search_code",
        "description": "Find exact literal text/code matches in live project files and return fresh verifiable ranges.",
        "permission": "READ",
        "input_schema": _schema_objeto(
            {"query": {"type": "string", "minLength": 1, "description": "Literal text or code fragment to match exactly in project files."}}, ["query"],
        ),
        "output_schema": "Standard envelope; detail.resultados contains file, range, symbol, score, numbered snippet, content_hash, and file_hash.",
        "compat_aliases": {"pergunta": "query"},
        "fn": _tool_search_code,
    },
    "find_symbol": {
        "name": "find_symbol",
        "description": "Locate a symbol in a known file or across the live project.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "simbolo": {"type": "string", "minLength": 1, "description": "Exact code symbol name whose definition/location should be found."},
        }, ["simbolo"]),
        "output_schema": "Standard envelope; detail contains the range, original code, and total line count.",
        "compat_aliases": {"arquivo": "caminho_relativo"},
        "fn": _tool_find_symbol,
    },
    "read_range": {
        "name": "read_range",
        "description": "Read one fresh numbered line range directly from a project file.",
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
        "description": "Read a bounded beginning portion of one project file with verifiable hashes and line metadata.",
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
            "codigo_novo": _CODIGO_NOVO,
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
    "test_patch_set_dry_run": {
        "name": "test_patch_set_dry_run",
        "description": "Dry-run a transactional set of free-form file updates, creations, or deletions.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "patches": {"type": "array", "description": "Transactional list of create/delete/replace/update patch objects to validate without writing."},
        }, ["patches"]),
        "output_schema": "Standard envelope; detail contains prepared patches and affected files.",
        "compat_aliases": {},
        "fn": _tool_test_patch_set_dry_run,
    },
    "memory_search": {
        "name": "memory_search",
        "description": "Search hash-validated external memory entries associated with the active project.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "query": {"type": "string", "description": "Text used to match relevant external project-memory entries."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum number of matching memory entries to return."},
        }),
        "output_schema": "Standard envelope; detail.entries contains compact, hash-validated project facts.",
        "compat_aliases": {},
        "fn": _tool_memory_search,
    },
    "memory_store": {
        "name": "memory_store",
        "description": "Store one useful evidence-backed project fact in external memory.",
        "permission": "MEMORY_WRITE",
        "input_schema": _schema_objeto({
            "text": {"type": "string", "minLength": 1, "description": "Compact project fact to persist in external project memory."},
            "kind": {"type": "string", "description": "Optional memory category; defaults to fact when omitted."},
            "evidence_ids": {"type": "array", "description": "Current-task evidence IDs that substantiate the stored fact."},
        }, ["text", "evidence_ids"]),
        "output_schema": "Standard envelope containing the stored memory entry.",
        "compat_aliases": {},
        "fn": _tool_memory_store,
    },
    "run_tests": {
        "name": "run_tests",
        "description": "Run the detected test suite in the sandbox; optionally focus pytest on one safe relative file or directory.",
        "permission": "EXEC",
        "input_schema": _schema_objeto({
            "scope": {"type": "string", "minLength": 1, "description": "Optional safe project-relative pytest file or directory; omitted means the detected full suite."},
        }),
        "output_schema": "Standard envelope; detail contains command, return code, concise pytest summary, bounded output tail, scope and execution status.",
        "compat_aliases": {"path": "scope", "caminho_relativo": "scope"},
        "fn": _tool_run_tests,
    },
    "execution_trace": {
        "name": "execution_trace",
        "description": "Read sanitized facts from current or past executions.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "job_id": {"type": "integer", "minimum": 1, "description": "Past job id; omit=current."},
            "turn": {"type": "integer", "minimum": 1, "description": "Turn filter."},
            "section": {"type": "string", "minLength": 1, "maxLength": 20, "description": "Trace section."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Event limit."},
        }),
        "output_schema": "Standard envelope with selected sanitized trace facts.",
        "compat_aliases": {"job": "job_id"},
        "fn": _tool_execution_trace,
    },
    "git_status": {
        "name": "git_status",
        "description": "Inspect current Git working-tree state without changing files; returns branch and compact modified/added/deleted/untracked entries.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum number of changed-path status entries to return."},
        }),
        "output_schema": "Standard envelope; detail contains branch, clean flag, category counts and bounded changed-file entries.",
        "compat_aliases": {},
        "fn": _tool_git_status,
    },
    "git_diff": {
        "name": "git_diff",
        "description": "Inspect a bounded read-only Git diff for the workspace or one relative path, optionally staged.",
        "permission": "READ",
        "input_schema": _schema_objeto({
            "path": {"type": "string", "minLength": 1, "description": "Optional project-relative path whose Git diff should be inspected."},
            "staged": {"type": "boolean", "description": "When true inspect staged/index changes; otherwise inspect unstaged working-tree changes."},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Number of unchanged context lines around each returned diff hunk."},
        }),
        "output_schema": "Standard envelope; detail contains changed files, added/removed line counts, bounded diff text and truncation state.",
        "compat_aliases": {"caminho_relativo": "path", "arquivo": "path"},
        "fn": _tool_git_diff,
    },
    "apply_patch": {
        "name": "apply_patch",
        "description": "Apply a confirmed range replacement with original-content preconditions, rollback, and tests.",
        "permission": "WRITE",
        "input_schema": _schema_objeto({
            "caminho_relativo": _CAMINHO,
            "linha_inicio": _LINHA,
            "linha_fim": _LINHA,
            "codigo_original_esperado": _CODIGO_ORIGINAL,
            "codigo_novo": _CODIGO_NOVO,
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
    "apply_patch_set": {
        "name": "apply_patch_set",
        "description": "Apply a confirmed multi-file transaction with rollback.",
        "permission": "WRITE",
        "input_schema": _schema_objeto({"patches": {"type": "array", "description": "Prepared multi-file transaction previously confirmed by the user."}}, ["patches"]),
        "output_schema": "Standard envelope; detail contains applied patches for rollback and reread.",
        "compat_aliases": {},
        "fn": _tool_apply_patch_set,
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
    "max_linhas": {"config_key": "agent.max_read_range_lines", "default": 400},
}

# Public tool semantics. Shared authority/effect meaning is declared once;
# each tool keeps only its specific purpose/arguments/result/caveats. Nothing
# here encodes preferences between tools or task-routing rules.
_TOOL_TAXONOMY = {
    "categories": {
        "READ_ONLY": (
            "May inspect, calculate, execute checks, or use temporary validation; "
            "does not intentionally persist project-file or project-memory changes."
        ),
        "EDIT": (
            "May persist project-file or project-memory changes; runtime controls still apply."
        ),
    },
    "effects": {
        "NONE": "No persistent side effect.",
        "EXEC": "Executes project code/processes; incidental temporary artifacts may occur.",
        "TEMP": "Uses temporary validation state only; no persistent project change.",
        "MEMORY_WRITE": "Persists external project memory.",
        "WORKSPACE_WRITE": "Persists project-file changes.",
        "VERIFY": "May run post-change verification.",
        "ROLLBACK": "May restore changes if verification fails.",
    },
}

# Tool-specific semantics. Shared authority and side-effect meaning live in the
# taxonomy above, so contracts do not repeat "read-only", "does not modify
# files", or "side_effects: none" for every tool. Caveats are reserved for
# limits that are unique to a tool and matter to the model's conclusion.
_TOOL_CONTRACTS = {
    "calculate": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Decimal result with exact/approximate and precision metadata.",
    },
    "agent_info": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Runtime identity, complete registered_tools, phase-local available_tools and write policy.",
        "caveats": ["registered_tools is the full registry; available_tools is only the current phase subset. Runtime metadata does not prove source-level implementation behavior."],
    },
    "project_stats": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Counts for files, directories, lines, characters, bytes, extensions and languages.",
        "caveats": ["Measurements only; no importance ranking or code-behavior diagnosis."],
    },
    "count_tokens": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Token count/estimate, method, exactness, measured characters and scan completeness.",
        "caveats": ["Measures project text, not actual LLM request usage or token waste."],
    },
    "inspect_project": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Languages, entrypoints, imports, tests, CI, frameworks, manifests and relation signals.",
        "caveats": ["Objective static signals only; no importance ranking, runtime confirmation or bug proof."],
    },
    "list_tree": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Project-relative tree entries plus depth, truncation and ignored-item metadata.",
    },
    "search_code": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Fresh bounded matches with file, line range, numbered snippet and hashes.",
        "caveats": ["Literal text/code search only; not semantic or natural-language search."],
    },
    "find_symbol": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Fresh symbol definition/location and verifiable source range metadata.",
        "caveats": ["Locates definitions/locations; does not guarantee every runtime reference or call site."],
    },
    "read_range": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Numbered source range, total lines, content hash and file hash.",
    },
    "read_file": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded file content, truncation state, line metadata and hashes.",
        "caveats": ["The returned content may be truncated by configured read limits."],
    },
    "test_patch_dry_run": {
        "category": "READ_ONLY",
        "effects": ["TEMP"],
        "returns": "Temporary validation result and resulting content without applying the change.",
        "caveats": ["Fresh-read preconditions still apply; dry-run never confirms a write for the user."],
    },
    "test_patch_set_dry_run": {
        "category": "READ_ONLY",
        "effects": ["TEMP"],
        "returns": "Prepared/validated patch transaction and affected-file metadata without applying it.",
        "caveats": ["Existing files still require fresh evidence; dry-run never confirms a write for the user."],
    },
    "memory_search": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded hash-validated prior project-memory entries.",
        "caveats": ["Prior memory is context, not proof of current live source state."],
    },
    "memory_store": {
        "category": "EDIT",
        "effects": ["MEMORY_WRITE"],
        "returns": "The external project-memory entry that was stored.",
        "caveats": ["Persists project memory only and requires current-task evidence references."],
    },
    "run_tests": {
        "category": "READ_ONLY",
        "effects": ["EXEC"],
        "returns": "Runner command, status, return code, concise summary, bounded output and runner diagnostics.",
        "caveats": ["Does not install a missing runner or prove untested behavior; tests may create incidental temporary/cache artifacts."],
    },
    "execution_trace": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Bounded sanitized execution trace.",
        "caveats": ["Observable facts only: no diagnosis, chain-of-thought, raw prompts, source/patch/memory bodies or secrets."],
    },
    "git_status": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Branch, clean flag, category counts and bounded changed-path entries.",
        "caveats": ["Status metadata only; it does not include patch contents."],
    },
    "git_diff": {
        "category": "READ_ONLY",
        "effects": ["NONE"],
        "returns": "Changed files, added/removed line counts, bounded diff text and truncation state.",
        "caveats": ["Bounded output may omit truncated hunks."],
    },
    "apply_patch": {
        "category": "EDIT",
        "effects": ["WORKSPACE_WRITE", "VERIFY", "ROLLBACK"],
        "returns": "Applied change plus verification, reread and rollback metadata.",
        "caveats": ["Requires runtime confirmation, fresh-content preconditions and a path inside the project root."],
    },
    "apply_patch_set": {
        "category": "EDIT",
        "effects": ["WORKSPACE_WRITE", "VERIFY", "ROLLBACK"],
        "returns": "Applied transaction plus verification, reread and rollback metadata.",
        "caveats": ["Requires runtime confirmation, transaction preconditions and paths inside the project root."],
    },
}

for _tool_name, _contract in _TOOL_CONTRACTS.items():
    if _tool_name in TOOLS:
        TOOLS[_tool_name].update(_contract)


def _ler_config_key(config, caminho, default):
    valor = config or {}
    for parte in caminho.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return default
        valor = valor[parte]
    return valor


def _compact_arg_description(text):
    text = str(text or "").strip()
    replacements = {
        "Relative path inside the project root.": "project-relative path",
        "1-based line number inside the selected project file.": "1-based line",
        "Hexadecimal SHA-256 returned by a fresh read.": "fresh-read SHA-256",
        "Replacement code. Empty string is valid for deletion.": "replacement code; empty=delete",
        "Exact original source text expected before a confirmed replacement.": "exact original source expected before replacement",
    }
    return replacements.get(text, text)[:110]


def _compact_input_contract(schema):
    """Compact JSON-schema arguments into model-readable signatures.

    The executable schema remains authoritative for validation. The model only
    needs type, required/optional state, numeric bounds and the tool-specific
    meaning of each argument.
    """
    schema = schema if isinstance(schema, dict) else _schema_objeto()
    required = set(schema.get("required") or [])
    aliases = {
        "string": "str", "integer": "int", "number": "num",
        "boolean": "bool", "object": "obj", "array": "list",
    }
    inputs = {}
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        kind = aliases.get(spec.get("type", "any"), spec.get("type", "any"))
        if name not in required:
            kind += "?"
        bounds = []
        if spec.get("minimum") is not None:
            bounds.append(f">={spec.get('minimum')}")
        if spec.get("maximum") is not None:
            bounds.append(f"<={spec.get('maximum')}")
        head = kind + ((" " + " ".join(bounds)) if bounds else "")
        description = _compact_arg_description(spec.get("description"))
        inputs[name] = f"{head} | {description}" if description else head
    return inputs


def gerar_catalogo_tools(registro=None, config=None, allowed_names=None, compact=False, minimal=False):
    """Generate the public catalog from the executable registry.

    ``allowed_names`` only filters actions that are impossible in the current
    runtime state. ``compact`` keeps each tool's semantic contract while
    removing implementation-only schema detail. ``minimal`` is retained only
    for compatibility tests/consumers that explicitly request names/arguments.
    """
    catalogo = []
    fonte = TOOLS if registro is None else registro
    allowed = None if allowed_names is None else {str(name) for name in allowed_names}
    for chave, entrada in fonte.items():
        public_name = entrada.get("name", chave)
        if allowed is not None and public_name not in allowed and chave not in allowed:
            continue
        limites = {}
        for nome_limite, origem in (entrada.get("limits") or {}).items():
            limites[nome_limite] = _ler_config_key(
                config, origem["config_key"], origem["default"],
            )
        schema = entrada.get("input_schema", _schema_objeto())
        if minimal:
            catalogo.append({
                "name": public_name,
                "required": list(schema.get("required") or []),
                "arguments": list((schema.get("properties") or {}).keys()),
            })
        elif compact:
            item = {
                "name": public_name,
                "purpose": entrada.get("description", "")[:200],
                "inputs": _compact_input_contract(schema),
                "returns": str(entrada.get("returns") or entrada.get("output_schema") or "")[:220],
            }
            caveats = [str(value)[:150] for value in (entrada.get("caveats") or [])[:4]]
            if caveats:
                item["caveats"] = caveats
            if limites:
                item["limits"] = limites
            catalogo.append(item)
        else:
            item = {
                "name": public_name,
                "description": entrada.get("description", ""),
                "permission": entrada.get("permission"),
                "category": entrada.get("category", "READ_ONLY"),
                "effects": list(entrada.get("effects") or ["NONE"]),
                "input_schema": schema,
                "output_schema": entrada.get("output_schema", "Standard tool envelope."),
                "returns": entrada.get("returns", ""),
                "limits": limites,
            }
            if entrada.get("caveats"):
                item["caveats"] = list(entrada.get("caveats") or [])
            catalogo.append(item)
    return catalogo


def gerar_taxonomia_tools(catalogo):
    """Describe shared authority/effect classes once for the visible tools.

    Tool names are grouped here, so individual contracts do not repeat category
    or side-effect boilerplate. ``NONE`` is the default effect for tools absent
    from the special-effect tag lists.
    """
    items = catalogo if isinstance(catalogo, list) else []
    names = [str(item.get("name")) for item in items if isinstance(item, dict) and item.get("name")]
    categories = {}
    for category, meaning in _TOOL_TAXONOMY["categories"].items():
        members = [name for name in names if (TOOLS.get(name) or {}).get("category") == category]
        if members:
            categories[category] = {"meaning": meaning, "tools": members}

    effect_tags = {}
    used_effects = set()
    for effect in _TOOL_TAXONOMY["effects"]:
        if effect == "NONE":
            continue
        members = [name for name in names if effect in ((TOOLS.get(name) or {}).get("effects") or [])]
        if members:
            effect_tags[effect] = members
            used_effects.add(effect)

    effect_meanings = {
        key: _TOOL_TAXONOMY["effects"][key]
        for key in _TOOL_TAXONOMY["effects"]
        if key == "NONE" or key in used_effects
    }
    return {
        "categories": categories,
        "effects": {
            "default": "NONE",
            "meanings": effect_meanings,
            "tags": effect_tags,
        },
    }


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
    Single execution entry point used by ``eyle.core.agent``. Tool
    exceptions become a standard ``TOOL_EXECUTION_ERROR`` result instead of
    bypassing the task state machine.
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
