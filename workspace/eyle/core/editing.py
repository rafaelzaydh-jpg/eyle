#!/usr/bin/env python3
"""
codar.py
--------
Atualizacao 5 -- "Codar de verdade" (engenheira assistente).

Ate a Atualizacao 4, a Eyle so EXPLICAVA ou SUGERIA -- nunca escrevia
nada no projeto do usuario. Este modulo e o que torna uma mudanca real
possivel, com seguranca:

    Proposta gerada pelo ciclo LLM-first em eyle/core/agent.py
        -> Impacto        (calcular_impacto: depende_de invertido)
        -> Patch           (localizar_simbolo: recorte exato por linha,
                             lido FRESCO do disco -- nunca da memoria
                             indexada, que pode estar desatualizada)
        -> Teste            (testar_patch_em_copia: NUNCA escreve no
                             arquivo real -- so numa copia temporaria.
                             Verificacao minima viavel: ast.parse() para
                             .py; arquivos web (.js/.html/.css) so
                             confirmam que o recorte de linha e a escrita
                             funcionaram, sem parser de sintaxe real --
                             isso e' deliberado, nao um TODO.)
        -> Aplicar          (aplicar_patch: SO chamado depois que o
                             usuario confirma explicitamente, em
                             eyle/core/agent.py:_resume_single.
                             Confere se o arquivo nao mudou desde a
                             proposta, faz backup, escreve, roda uma
                             segunda checagem no arquivo real -- se
                             falhar, reverte sozinho -- e, se
                             config["codar"]["testes"]["ativado"]=true E
                             o projeto tiver pytest/npm test configurado,
                             roda a suite real no projeto e reverte se
                             ela quebrar.)

Nenhuma funcao aqui decide SE deve aplicar algo -- isso e' sempre
decisão do runtime em eyle/core/agent.py, que só chama apply_patch após confirmação
depois de ver "sim"/"aplica" do usuario numa mensagem separada da
proposta original.
"""
import ast
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from .workspace_policy import PASTAS_IGNORADAS
from .symbols import extract_python_definitions, extract_symbols
from .security import _resolver_caminho_seguro
from .text_hash import hash_faixa as _hash_faixa_canonica, hash_texto as _hash_texto_canonico
from .sandbox import executar_no_sandbox
from .retention import limpar_backups


EXTENSOES_COM_VERIFICACAO_SINTAXE = {".py"}  # so Python tem checagem real de sintaxe por enquanto


def localizar_simbolo(caminho_projeto, caminho_relativo, simbolo):
    """
    Le o ARQUIVO REAL (fresco do disco, NAO um cache antigo -- o
    arquivo pode ter mudado desde a ultima leitura) e localiza a linha
    exata onde `simbolo` (funcao/classe) comeca e termina, reaproveitando
    o extrator de simbolos do workspace. Python usa as posicoes
    do AST (inclusive decorators e metodos qualificados); nas linguagens
    restantes o fim ainda e a linha anterior ao proximo simbolo do arquivo.
    Linhas em branco no limite sao removidas do recorte.

    Devolve {"linha_inicio", "linha_fim", "codigo_original",
    "total_linhas_arquivo"} ou None se o arquivo nao existe mais, o
    caminho tenta escapar da raiz do projeto (bug 3 do plano de correcao),
    ou o simbolo nao foi encontrado (pode ter sido renomeado/removido
    desde a ultima leitura) -- nunca inventa uma posicao.
    """
    caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho_abs is None or not os.path.isfile(caminho_abs):
        return None
    try:
        with open(caminho_abs, "r", encoding="utf-8", errors="replace") as f:
            conteudo = f.read()
    except OSError:
        return None

    linhas = conteudo.split("\n")
    ext = os.path.splitext(caminho_relativo)[1].lower()
    if ext == ".py":
        # AST preserva metodos homonimos como ClasseA.run/ClasseB.run e
        # fornece o fim real do no, sem o dict(nome -> linha) que apagava
        # duplicatas. Decorators fazem parte do recorte do simbolo.
        encontrados = [
            d for d in extract_python_definitions(linhas)
            if d["nome"] == simbolo
        ]
        if len(encontrados) != 1:
            return None
        linha_inicio = encontrados[0]["linha_inicio"]
        linha_fim = encontrados[0]["linha_fim"]
    else:
        simbolos = extract_symbols(linhas, ext)
        encontrados = [linha for nome, linha in simbolos if nome == simbolo]
        if len(encontrados) != 1:
            return None
        linha_inicio = encontrados[0]
        linha_fim = len(linhas)
        for _, linha in sorted(simbolos, key=lambda par: par[1]):
            if linha > linha_inicio:
                linha_fim = linha - 1
                break

    # nao inclui o "respiro" (linhas em branco) entre o simbolo e o proximo
    while linha_fim > linha_inicio and linhas[linha_fim - 1].strip() == "":
        linha_fim -= 1

    codigo_original = "\n".join(linhas[linha_inicio - 1:linha_fim])
    return {
        "linha_inicio": linha_inicio,
        "linha_fim": linha_fim,
        "codigo_original": codigo_original,
        "total_linhas_arquivo": len(linhas),
    }


def localizar_simbolo_no_projeto(caminho_projeto, simbolo, extensoes=None, limite=32):
    """Localiza deterministicamente um símbolo no workspace atual.

    Usa o workspace vivo e varre apenas arquivos-fonte seguros,
    ignora diretórios internos conhecidos e confirma cada candidato usando o
    mesmo localizador fresco empregado por ``find_symbol``.
    """
    raiz = os.path.realpath(os.path.abspath(str(caminho_projeto or "")))
    if not raiz or not os.path.isdir(raiz) or not isinstance(simbolo, str) or not simbolo.strip():
        return []
    permitidas = set(extensoes or {".py", ".js", ".ts", ".jsx", ".tsx"})
    resultados = []
    for diretorio, subdirs, arquivos in os.walk(raiz, followlinks=False):
        subdirs[:] = sorted(
            nome for nome in subdirs
            if nome not in PASTAS_IGNORADAS and not nome.startswith(".")
        )
        for nome in sorted(arquivos):
            if os.path.splitext(nome)[1].lower() not in permitidas:
                continue
            absoluto = os.path.join(diretorio, nome)
            relativo = os.path.relpath(absoluto, raiz).replace(os.sep, "/")
            seguro = _resolver_caminho_seguro(raiz, relativo)
            if seguro is None or not os.path.isfile(seguro):
                continue
            localizado = localizar_simbolo(raiz, relativo, simbolo)
            if localizado is None:
                continue
            resultados.append({"arquivo": relativo, "simbolo": simbolo, **localizado})
            if len(resultados) >= max(1, int(limite)):
                return resultados
    return resultados


def _substituir_linhas(conteudo, linha_inicio, linha_fim, codigo_novo):
    linhas = conteudo.split("\n")
    if linha_inicio < 1 or linha_fim > len(linhas) or linha_inicio > linha_fim:
        return None
    novas_linhas = linhas[:linha_inicio - 1] + codigo_novo.split("\n") + linhas[linha_fim:]
    return "\n".join(novas_linhas)


def _hash_texto(conteudo):
    return _hash_texto_canonico(conteudo)


def _hash_faixa(conteudo, linha_inicio, linha_fim):
    """Hash canonico compativel com ``project_reader.ler_faixa_projeto``."""
    return _hash_faixa_canonica(conteudo, linha_inicio, linha_fim)


def _validar_hashes_patch(conteudo, linha_inicio, linha_fim,
                          file_hash_esperado=None, range_hash_esperado=None):
    file_hash_atual = _hash_texto(conteudo)
    range_hash_atual = _hash_faixa(conteudo, linha_inicio, linha_fim)
    if file_hash_esperado and file_hash_atual != file_hash_esperado:
        return False, file_hash_atual, range_hash_atual
    if range_hash_esperado and range_hash_atual != range_hash_esperado:
        return False, file_hash_atual, range_hash_atual
    return True, file_hash_atual, range_hash_atual


def _escrever_arquivo_atomico(caminho, conteudo):
    """Substitui ``caminho`` sem expor um arquivo parcialmente escrito.

    O temporario nasce no mesmo diretorio do destino, condicao necessaria
    para ``os.replace`` ser atomico no mesmo filesystem. Quando o arquivo ja
    existe, suas permissoes sao copiadas para o temporario antes da troca.
    Qualquer falha anterior ao ``replace`` deixa o destino intacto e remove o
    temporario.
    """
    diretorio = os.path.dirname(os.path.abspath(caminho))
    nome = os.path.basename(caminho)
    modo_original = stat.S_IMODE(os.stat(caminho).st_mode) if os.path.exists(caminho) else None
    fd, caminho_temporario = tempfile.mkstemp(prefix=f".{nome}.eyle-", dir=diretorio)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
        if modo_original is not None:
            try:
                shutil.copymode(caminho, caminho_temporario)
            except OSError:
                # Alguns filesystems nao conseguem representar as mesmas permissoes.
                # A atomicidade da troca continua sendo a garantia principal.
                pass
        os.replace(caminho_temporario, caminho)
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(caminho_temporario):
            os.unlink(caminho_temporario)


def testar_patch_em_copia(caminho_projeto, caminho_relativo, linha_inicio,
                          linha_fim, codigo_novo, file_hash_esperado=None,
                          range_hash_esperado=None):
    """
    NUNCA escreve no arquivo real. Copia o conteudo atual pra um arquivo
    temporario, aplica a substituicao de linhas [linha_inicio, linha_fim]
    (1-indexado, inclusive) por `codigo_novo`, e roda uma verificacao
    minima sobre o RESULTADO:

      - .py          -> ast.parse() inteiro (garante sintaxe Python valida)
      - outras exts  -> so confirma que o arquivo/linha/patch foram
                        recortados e escritos corretamente na copia.
                        Deliberado (Atualizacao 6): nao ha parser de
                        sintaxe real pra .js/.html/.css aqui -- rodar um
                        parser JS/HTML/CSS decente exigiria dependencia
                        nova (node, um parser HTML/CSS em Python etc.)
                        so pra verificacao, o que contradiz a filosofia
                        de "minima viavel". Quem quer verificacao real de
                        JS/HTML/CSS usa o teste opt-in (pytest/npm test)
                        via rodar_testes_projeto(), que roda no PROJETO
                        REAL depois do patch aplicado -- rodar suite de
                        teste numa copia de um arquivo isolado nao faz
                        sentido (o teste precisa do projeto inteiro).

    Devolve {"ok": bool, "detalhe": str, "conteudo_resultante": str|None}.
    """
    caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho_abs is None or not os.path.isfile(caminho_abs):
        return {"ok": False, "detalhe": f"Arquivo '{caminho_relativo}' nao existe mais no disco.",
                "conteudo_resultante": None}

    with open(caminho_abs, "r", encoding="utf-8", errors="replace") as f:
        conteudo_atual = f.read()

    hashes_ok, file_hash_atual, range_hash_atual = _validar_hashes_patch(
        conteudo_atual, linha_inicio, linha_fim,
        file_hash_esperado=file_hash_esperado,
        range_hash_esperado=range_hash_esperado,
    )
    if not hashes_ok:
        return {
            "ok": False,
            "error_code": "STALE_PATCH",
            "detalhe": (
                "O arquivo ou a faixa mudou desde a leitura usada na proposta; "
                "o dry-run foi abortado sem tocar no projeto."
            ),
            "conteudo_resultante": None,
            "file_hash_atual": file_hash_atual,
            "range_hash_atual": range_hash_atual,
        }

    conteudo_resultante = _substituir_linhas(conteudo_atual, linha_inicio, linha_fim, codigo_novo)
    if conteudo_resultante is None:
        total_linhas = len(conteudo_atual.split("\n"))
        return {
            "ok": False,
            "detalhe": f"Faixa de linhas invalida ({linha_inicio}-{linha_fim}) para um arquivo com {total_linhas} linhas.",
            "conteudo_resultante": None,
        }

    tmp_dir = tempfile.mkdtemp(prefix="eyle_teste_patch_")
    try:
        caminho_copia = os.path.join(tmp_dir, os.path.basename(caminho_relativo))
        with open(caminho_copia, "w", encoding="utf-8") as f:
            f.write(conteudo_resultante)

        ext = os.path.splitext(caminho_relativo)[1].lower()
        if ext in EXTENSOES_COM_VERIFICACAO_SINTAXE:
            try:
                ast.parse(conteudo_resultante, filename=caminho_relativo)
            except SyntaxError as e:
                return {
                    "ok": False,
                    "detalhe": f"ast.parse() encontrou erro de sintaxe na versao proposta: {e.msg} (linha {e.lineno}).",
                    "conteudo_resultante": conteudo_resultante,
                }
            return {
                "ok": True,
                "detalhe": "ast.parse() confirmou sintaxe Python valida na copia temporaria (arquivo real nao foi tocado).",
                "conteudo_resultante": conteudo_resultante,
            }

        return {
            "ok": True,
            "detalhe": (
                f"Recorte e escrita testados numa copia temporaria (arquivo real nao foi tocado). "
                f"Arquivos '{ext}' nao tem parser de sintaxe real aqui (verificacao minima viavel) -- "
                f"confirma so que a faixa de linhas e a escrita funcionaram. Se o projeto tiver "
                f"pytest/npm test for detectado e a execução de testes estiver ativada, a suite real roda "
                f"apos aplicar o patch (rodar_testes_projeto)."
            ),
            "conteudo_resultante": conteudo_resultante,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


_MARCADORES_PYTEST_DIRETOS = ("pytest.ini", "conftest.py")
_DIRETORIOS_TESTE_IGNORADOS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".tox", ".nox", "build", "dist",
}


def _arquivo_contem(caminho, marcador):
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
            return marcador in arquivo.read(256 * 1024)
    except OSError:
        return False


def _tem_testes_pytest(caminho_projeto):
    """Detecta configuração pytest específica ou arquivos de teste reais."""
    if any(os.path.isfile(os.path.join(caminho_projeto, marcador)) for marcador in _MARCADORES_PYTEST_DIRETOS):
        return True
    if _arquivo_contem(os.path.join(caminho_projeto, "pyproject.toml"), "[tool.pytest"):
        return True
    if _arquivo_contem(os.path.join(caminho_projeto, "setup.cfg"), "[tool:pytest"):
        return True
    if _arquivo_contem(os.path.join(caminho_projeto, "tox.ini"), "[pytest]"):
        return True
    for raiz, pastas, arquivos in os.walk(caminho_projeto, followlinks=False):
        pastas[:] = [pasta for pasta in pastas if pasta not in _DIRETORIOS_TESTE_IGNORADOS]
        for nome in arquivos:
            nome_lower = nome.lower()
            if nome_lower == "tests.py" or (
                nome_lower.endswith(".py")
                and (nome_lower.startswith("test_") or nome_lower.endswith("_test.py"))
            ):
                return True
    return False


def _descricao_comando(comando):
    if isinstance(comando, (list, tuple)):
        if hasattr(shlex, "join"):
            return shlex.join(str(item) for item in comando)
        return " ".join(shlex.quote(str(item)) for item in comando)
    return str(comando)


def _detectar_comando_teste(caminho_projeto, cfg_testes):
    """
    So devolve um comando se cfg_testes.get("ativado") for True E existir
    evidencia de uma suite. Pytest e detectado tanto por configuracao quanto
    por arquivos ``test_*.py``, ``*_test.py`` ou ``tests.py`` em qualquer
    pasta valida, inclusive quando esses testes acabaram de ser criados pela
    transacao confirmada.

    Prioridade: pytest primeiro, depois package.json com um script "test"
    definido. Se os
    dois existirem, prefere pytest (mesma raiz normalmente so faz sentido
    ter um dos dois como projeto principal). Devolve None se nada aplicavel.
    """
    if not cfg_testes or not cfg_testes.get("ativado", False):
        return None

    if _tem_testes_pytest(caminho_projeto):
        return cfg_testes.get("comando_python", "python -m pytest -q")

    package_json = os.path.join(caminho_projeto, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, json.JSONDecodeError):
            dados = {}
        if isinstance(dados.get("scripts"), dict) and "test" in dados["scripts"]:
            return cfg_testes.get("comando_node", "npm test --silent")

    return None


def rodar_testes_projeto(caminho_projeto, cfg_testes):
    """
    Roda a suite de teste real do projeto (nao numa copia -- teste real
    precisa do projeto inteiro, nao so do arquivo que foi alterado). So
    executa se _detectar_comando_teste achar um comando aplicavel; caso
    contrario devolve {"executado": False, ...} sem tentar rodar nada.

    Chamado depois que o patch ja foi escrito no arquivo real (dentro de
    aplicar_patch), nunca antes -- rodar teste sobre codigo ainda nao
    escrito nao verificaria a mudanca de verdade.

    Devolve {"executado": bool, "ok": bool, "detalhe": str}.
    """
    comando = _detectar_comando_teste(caminho_projeto, cfg_testes)
    if comando is None:
        return {
            "executado": False,
            "ok": True,
            "detalhe": "Nenhum pytest/npm test foi detectado no projeto -- execução não aplicável.",
        }

    cfg_testes = cfg_testes or {}
    descricao_comando = _descricao_comando(comando)
    try:
        argv = (
            shlex.split(comando, posix=os.name != "nt")
            if isinstance(comando, str) else list(comando)
        )
    except (TypeError, ValueError) as erro:
        return {
            "executado": False, "ok": False, "recusado": True,
            "detalhe": f"Comando de teste invalido: {erro}.",
        }
    if not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        return {
            "executado": False, "ok": False, "recusado": True,
            "detalhe": "Comando de teste vazio ou com argumento invalido.",
        }
    cfg_sandbox = dict(cfg_testes.get("sandbox") or {})
    # Mantem compatibilidade com o timeout que ja existia antes da 28. Um
    # valor mais especifico dentro de sandbox continua tendo precedencia.
    cfg_sandbox.setdefault("timeout_segundos", cfg_testes.get("timeout_segundos", 60))
    resultado = executar_no_sandbox(caminho_projeto, argv, cfg_sandbox)
    saida_resumida = (resultado.get("saida") or "").strip()[-1000:]

    if resultado.get("executado") is not True:
        return {
            "executado": False,
            "ok": False,
            "recusado": True,
            "detalhe": f"Teste recusado pelo sandbox: {resultado.get('erro') or 'erro desconhecido'}.",
        }
    if resultado.get("ok") is True:
        backend = resultado.get("backend", "sandbox")
        return {
            "executado": True,
            "ok": True,
            "detalhe": f"'{descricao_comando}' passou no sandbox ({backend}).\n{saida_resumida}",
        }
    erro = resultado.get("erro")
    complemento = f" {erro}." if erro else ""
    return {
        "executado": True,
        "ok": False,
        "detalhe": (
            f"'{descricao_comando}' falhou no sandbox (codigo {resultado.get('codigo')})."
            f"{complemento}\n{saida_resumida}"
        ),
    }


def aplicar_patch(caminho_projeto, caminho_relativo, linha_inicio, linha_fim,
                   codigo_original_esperado, codigo_novo, backups_dir=None,
                   cfg_testes=None, cfg_retention=None,
                   file_hash_esperado=None, range_hash_esperado=None,
                   incluir_snapshot=False, executar_testes=True):
    """
    SO deve ser chamado depois que o usuario confirmou explicitamente a
    proposta numa mensagem separada (nunca na mesma resposta que a gerou).

    Re-le o arquivo NA HORA (nao confia no `codigo_original_esperado`
    guardado na proposta): se as linhas [linha_inicio, linha_fim] nao
    baterem mais com ele, ABORTA sem escrever nada -- o arquivo mudou
    desde a proposta (edicao manual do usuario, outro patch ou edição externa
    etc.), aplicar por cima seria destrutivo as cegas.

    Se `backups_dir` for passado, salva uma copia do arquivo INTEIRO
    original ali antes de escrever qualquer coisa. O arquivo real e'
    substituido atomicamente (temporario no mesmo diretorio + os.replace),
    nunca truncado em escrita direta. Depois da troca, roda uma segunda
    verificacao no ARQUIVO REAL final (.py -> ast.parse): se falhar,
    restaura `conteudo_atual`, que ja esta em memoria, mesmo quando backup
    em disco esta desativado. O backup e' historico adicional, nao um
    pre-requisito do rollback.

    Devolve {"ok": bool, "detalhe": str, "backup_path": str|None}.
    """
    caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho_abs is None or not os.path.isfile(caminho_abs):
        return {"ok": False, "detalhe": f"Arquivo '{caminho_relativo}' nao existe mais no disco.", "backup_path": None}

    with open(caminho_abs, "r", encoding="utf-8", errors="replace") as f:
        conteudo_atual = f.read()
    linhas = conteudo_atual.split("\n")

    hashes_ok, file_hash_atual, range_hash_atual = _validar_hashes_patch(
        conteudo_atual, linha_inicio, linha_fim,
        file_hash_esperado=file_hash_esperado,
        range_hash_esperado=range_hash_esperado,
    )
    if not hashes_ok:
        return {
            "ok": False,
            "changed": False,
            "error_code": "STALE_PATCH",
            "outcome": "blocked",
            "detalhe": (
                f"O arquivo ou a faixa de '{caminho_relativo}':{linha_inicio}-{linha_fim} "
                "mudou desde a proposta. Patch abortado sem escrita."
            ),
            "backup_path": None,
            "file_hash_atual": file_hash_atual,
            "range_hash_atual": range_hash_atual,
        }

    if linha_inicio < 1 or linha_fim > len(linhas) or linha_inicio > linha_fim:
        return {
            "ok": False,
            "detalhe": (
                f"Faixa de linhas invalida ({linha_inicio}-{linha_fim}) para o arquivo atual "
                f"({len(linhas)} linhas) -- o arquivo pode ter mudado desde a proposta."
            ),
            "backup_path": None,
        }

    codigo_atual_no_arquivo = "\n".join(linhas[linha_inicio - 1:linha_fim])
    if codigo_atual_no_arquivo != codigo_original_esperado:
        return {
            "ok": False,
            "changed": False,
            "error_code": "STALE_PATCH",
            "outcome": "blocked",
            "detalhe": (
                f"O conteudo de '{caminho_relativo}':{linha_inicio}-{linha_fim} mudou desde que a proposta "
                f"foi gerada -- aplicar por cima seria destrutivo. Peca a sugestao/mudanca de novo."
            ),
            "backup_path": None,
        }

    backup_path = None
    if backups_dir:
        os.makedirs(backups_dir, exist_ok=True)
        carimbo = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
        nome_seguro = caminho_relativo.replace("/", "__").replace("\\", "__")
        backup_path = os.path.join(backups_dir, f"{carimbo}__{nome_seguro}.bak")
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(conteudo_atual)
        cfg_retention = cfg_retention or {}
        limpar_backups(
            backups_dir,
            max_files=cfg_retention.get("backups_max_files", 50),
            max_age_days=cfg_retention.get("backups_max_age_days", 30),
            max_total_mb=cfg_retention.get("backups_max_total_mb", 256),
        )
        if not os.path.exists(backup_path):
            backup_path = None

    conteudo_final = _substituir_linhas(conteudo_atual, linha_inicio, linha_fim, codigo_novo)
    try:
        _escrever_arquivo_atomico(caminho_abs, conteudo_final)
    except OSError as e:
        return {
            "ok": False,
            "detalhe": f"Nao foi possivel substituir '{caminho_relativo}' atomicamente: {e}.",
            "backup_path": backup_path,
        }

    def _reverter():
        try:
            _escrever_arquivo_atomico(caminho_abs, conteudo_atual)
            return None
        except OSError as e:
            return str(e)

    ext = os.path.splitext(caminho_relativo)[1].lower()
    if ext in EXTENSOES_COM_VERIFICACAO_SINTAXE:
        try:
            ast.parse(conteudo_final, filename=caminho_relativo)
        except SyntaxError as e:
            # segunda checagem falhou no arquivo real (mesmo tendo passado
            # na copia) -- reverte sozinho em vez de deixar quebrado
            erro_rollback = _reverter()
            detalhe_rollback = (
                f" O rollback tambem falhou: {erro_rollback}."
                if erro_rollback else
                " Revertido automaticamente para o conteudo anterior."
            )
            return {
                "ok": False,
                # Se o rollback falhar, o arquivo pode continuar com o
                # conteudo novo. O contrato da tool (Atualizacao 21)
                # precisa refletir isso em `changed`, mesmo com ok=False.
                "changed": erro_rollback is not None,
                "detalhe": (
                    f"ast.parse() falhou no arquivo real apos escrever ({e.msg}, linha {e.lineno})."
                    f"{detalhe_rollback}"
                ),
                "backup_path": backup_path,
                "error_code": "POST_WRITE_SYNTAX_FAILED",
                "outcome": "rollback_failed" if erro_rollback else "reverted",
            }

    # Verificacao de testes (pytest/npm test): so roda se
    # cfg_testes vier com "ativado": true E o projeto ja tiver esse tipo
    # de teste configurado (ver _detectar_comando_teste). Roda no
    # PROJETO REAL, depois da escrita, porque teste precisa do projeto
    # inteiro -- nunca da copia temporaria de testar_patch_em_copia.
    teste = (
        rodar_testes_projeto(caminho_projeto, cfg_testes)
        if executar_testes else
        {
            "executado": False,
            "ok": True,
            "detalhe": "Verificacao separada pendente no ciclo do Agente.",
        }
    )
    if not teste["ok"]:
        erro_rollback = _reverter()
        detalhe_rollback = (
            f" O rollback tambem falhou: {erro_rollback}."
            if erro_rollback else
            " Revertido automaticamente."
        )
        return {
            "ok": False,
            "changed": erro_rollback is not None,
            "detalhe": (
                f"Patch escrito, mas a suite de teste falhou.{detalhe_rollback} {teste['detalhe']}"
            ),
            "backup_path": backup_path,
            "error_code": "TESTS_FAILED",
            "outcome": "rollback_failed" if erro_rollback else "reverted",
            "test_result": teste,
        }

    detalhe = f"Patch aplicado em '{caminho_relativo}':{linha_inicio}-{linha_fim}."
    if teste["executado"]:
        detalhe += f" Teste detectado passou: {teste['detalhe']}"

    file_hash_final = _hash_texto(conteudo_final)
    linha_fim_final = linha_inicio + len(codigo_novo.split("\n")) - 1
    outcome = (
        "verified" if teste.get("executado") is True
        else "applied_without_suite" if executar_testes
        else "applied_pending_verification"
    )
    retorno = {
        "ok": True,
        "detalhe": detalhe,
        "backup_path": backup_path,
        "changed": True,
        "outcome": outcome,
        "test_result": teste,
        "file_hash_antes": file_hash_atual,
        "range_hash_antes": range_hash_atual,
        "file_hash_depois": file_hash_final,
        "linha_fim_final": linha_fim_final,
    }
    if incluir_snapshot:
        retorno["rollback_snapshot"] = {
            "caminho_relativo": caminho_relativo,
            "conteudo_original": conteudo_atual,
            "file_hash_original": file_hash_atual,
            "file_hash_aplicado": file_hash_final,
        }
    return retorno


def restaurar_snapshot_patch(caminho_projeto, snapshot):
    """Restaura uma edicao do Agente sem sobrescrever mudanca externa.

    O rollback so acontece se o arquivo ainda tiver exatamente o hash que a
    escrita confirmada produziu. Se alguem o editou depois, falha fechado.
    """
    snapshot = snapshot or {}
    caminho_relativo = snapshot.get("caminho_relativo")
    conteudo_original = snapshot.get("conteudo_original")
    hash_aplicado = snapshot.get("file_hash_aplicado")
    if not (
        isinstance(caminho_relativo, str) and caminho_relativo
        and isinstance(conteudo_original, str)
        and isinstance(hash_aplicado, str) and hash_aplicado
    ):
        return {
            "ok": False, "changed": False, "error_code": "INVALID_ROLLBACK",
            "detalhe": "Snapshot de rollback ausente ou invalido.",
        }
    caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho_abs is None or not os.path.isfile(caminho_abs):
        return {
            "ok": False, "changed": False, "error_code": "ROLLBACK_TARGET_MISSING",
            "detalhe": "Arquivo da edicao nao existe mais; rollback recusado.",
        }
    with open(caminho_abs, "r", encoding="utf-8", errors="replace") as arquivo:
        conteudo_atual = arquivo.read()
    if _hash_texto(conteudo_atual) != hash_aplicado:
        return {
            "ok": False, "changed": False, "error_code": "STALE_ROLLBACK",
            "detalhe": "O arquivo mudou depois do patch; rollback automatico recusado.",
        }
    try:
        _escrever_arquivo_atomico(caminho_abs, conteudo_original)
    except OSError as erro:
        return {
            "ok": False, "changed": True, "error_code": "ROLLBACK_FAILED",
            "detalhe": f"Falha ao restaurar o arquivo: {erro}.",
        }
    return {
        "ok": True, "changed": False, "error_code": None,
        "detalhe": "Conteudo original restaurado atomicamente apos falha de verificacao.",
        "file_hash_restaurado": _hash_texto(conteudo_original),
    }
