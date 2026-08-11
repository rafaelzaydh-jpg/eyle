"""Low-level editing primitives used by the canonical transaction path.

This module locates symbols, performs atomic writes, substitutes line ranges,
and runs detected test suites. Transaction planning/apply/rollback lives in
``eyle.core.transactions``; transaction execution has one canonical path.
"""
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_THIS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from .workspace_policy import PASTAS_IGNORADAS, build_protected_resource_index, is_protected_workspace_resource
from .symbols import extract_python_definitions, extract_symbols
from .security import _resolver_caminho_seguro
from .sandbox import executar_no_sandbox


def localizar_simbolo(caminho_projeto, caminho_relativo, simbolo):
    """
    Le o ARQUIVO REAL (fresco do disco, NAO um cache antigo -- o
    arquivo pode ter mudado desde a ultima leitura) e localiza a linha
    exata onde `simbolo` (funcao/classe) comeca e termina, reaproveitando
    o extrator de simbolos do workspace. Python usa as posicoes
    do AST (inclusive decorators e metodos qualificados); nas linguagens
    restantes o fim ainda e a linha anterior ao proximo simbolo do arquivo.
    Linhas em branco no limite sao removidas do recorte.

    Devolve {"line_start", "line_end", "codigo_original",
    "total_lines"} ou None se o arquivo nao existe mais, o
    caminho tenta escapar da raiz do projeto (bug 3 do plano de correcao),
    ou o simbolo nao foi encontrado (pode ter sido renomeado/removido
    desde a ultima leitura) -- nunca inventa uma posicao.
    """
    if is_protected_workspace_resource(caminho_projeto, caminho_relativo, index=build_protected_resource_index(caminho_projeto)):
        return None
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
        linha_inicio = encontrados[0]["line_start"]
        linha_fim = encontrados[0]["line_end"]
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
        "line_start": linha_inicio,
        "line_end": linha_fim,
        "codigo_original": codigo_original,
        "total_lines": len(linhas),
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
    protected_index = build_protected_resource_index(raiz)
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
            if is_protected_workspace_resource(raiz, relativo, index=protected_index):
                continue
            seguro = _resolver_caminho_seguro(raiz, relativo)
            if seguro is None or not os.path.isfile(seguro):
                continue
            localizado = localizar_simbolo(raiz, relativo, simbolo)
            if localizado is None:
                continue
            resultados.append({"file": relativo, "simbolo": simbolo, **localizado})
            if len(resultados) >= max(1, int(limite)):
                return resultados
    return resultados


def _substituir_linhas(conteudo, linha_inicio, linha_fim, codigo_novo):
    linhas = conteudo.split("\n")
    if linha_inicio < 1 or linha_fim > len(linhas) or linha_inicio > linha_fim:
        return None
    novas_linhas = linhas[:linha_inicio - 1] + codigo_novo.split("\n") + linhas[linha_fim:]
    return "\n".join(novas_linhas)


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



_MARCADORES_PYTEST_DIRETOS = ("pytest.ini", "conftest.py")
_DIRETORIOS_TESTE_IGNORADOS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".tox", ".nox", "build", "dist",
}

def _arquivo_contem(caminho_projeto, caminho_relativo, marcador):
    protected_index = build_protected_resource_index(caminho_projeto)
    if is_protected_workspace_resource(caminho_projeto, caminho_relativo, index=protected_index):
        return False
    caminho = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho is None:
        return False
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
            return marcador in arquivo.read(256 * 1024)
    except OSError:
        return False


def _tem_testes_pytest(caminho_projeto):
    """Detecta configuração pytest específica ou arquivos de teste reais."""
    if any(os.path.isfile(os.path.join(caminho_projeto, marcador)) for marcador in _MARCADORES_PYTEST_DIRETOS):
        return True
    if _arquivo_contem(caminho_projeto, "pyproject.toml", "[tool.pytest"):
        return True
    if _arquivo_contem(caminho_projeto, "setup.cfg", "[tool:pytest"):
        return True
    if _arquivo_contem(caminho_projeto, "tox.ini", "[pytest]"):
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
        return shlex.join(str(item) for item in comando)
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




def _test_runner_name(argv):
    if not argv:
        return None
    first = os.path.basename(str(argv[0])).lower()
    if first in {"pytest", "pytest.exe"}:
        return "pytest"
    if len(argv) >= 3 and first.startswith("python") and list(argv[1:3]) == ["-m", "pytest"]:
        return "pytest"
    if first in {"npm", "npm.cmd", "npm.exe"}:
        return "npm"
    return first or None


def _runner_unavailable_message(argv, resultado):
    """Return a deterministic unavailable-runner diagnostic when applicable."""
    runner = _test_runner_name(argv)
    combined = "\n".join(
        str(value or "") for value in (resultado.get("erro"), resultado.get("saida"))
    ).lower()
    if runner == "pytest" and any(token in combined for token in (
        "no module named pytest",
        "pytest: command not found",
        "pytest: not found",
        "'pytest' is not recognized",
        '"pytest" is not recognized',
    )):
        return runner, "pytest não está disponível no ambiente Python usado pela Eyle."
    if runner == "npm" and any(token in combined for token in (
        "npm: command not found",
        "npm: not found",
        "'npm' is not recognized",
        '"npm" is not recognized',
        "no such file or directory: 'npm'",
        'no such file or directory: "npm"',
    )):
        return runner, "npm não está disponível no ambiente de execução da Eyle."
    return None, None

def rodar_testes_projeto(caminho_projeto, cfg_testes, scope=None):
    """Run the detected real test suite, optionally narrowed to one safe pytest scope.

    The post-write validator calls this without ``scope`` and keeps the full-suite
    behavior. The agent-facing ``run_tests`` tool may pass a relative file or
    directory so investigation can test a focused area without dumping an entire
    project suite into the model context.
    """
    comando = _detectar_comando_teste(caminho_projeto, cfg_testes)
    if comando is None:
        return {
            "executado": False,
            "ok": True,
            "detalhe": "Nenhum pytest/npm test foi detectado no projeto -- execução não aplicável.",
            "comando": None,
            "codigo": None,
            "saida_resumida": "",
            "backend": None,
            "scope": scope,
            "tests_detected": False,
        }

    cfg_testes = cfg_testes or {}
    try:
        argv = (
            shlex.split(comando, posix=os.name != "nt")
            if isinstance(comando, str) else list(comando)
        )
    except (TypeError, ValueError) as erro:
        return {
            "executado": False, "ok": False, "recusado": True,
            "detalhe": f"Comando de teste invalido: {erro}.",
            "comando": str(comando), "codigo": None, "saida_resumida": "",
            "backend": None, "scope": scope, "tests_detected": True,
        }
    if not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        return {
            "executado": False, "ok": False, "recusado": True,
            "detalhe": "Comando de teste vazio ou com argumento invalido.",
            "comando": str(comando), "codigo": None, "saida_resumida": "",
            "backend": None, "scope": scope, "tests_detected": True,
        }

    normalized_scope = None
    if scope:
        normalized_scope = str(scope).strip().replace("\\", "/")
        safe = _resolver_caminho_seguro(caminho_projeto, normalized_scope)
        if safe is None or not os.path.exists(safe):
            return {
                "executado": False, "ok": False, "recusado": True,
                "detalhe": f"Escopo de teste inválido ou inexistente: {normalized_scope}.",
                "comando": _descricao_comando(argv), "codigo": None,
                "saida_resumida": "", "backend": None,
                "scope": normalized_scope, "tests_detected": True,
            }
        is_pytest = (
            os.path.basename(argv[0]).lower() in {"pytest", "pytest.exe"}
            or (len(argv) >= 3 and os.path.basename(argv[0]).lower().startswith("python") and argv[1:3] == ["-m", "pytest"])
        )
        if not is_pytest:
            return {
                "executado": False, "ok": False, "recusado": True,
                "detalhe": "Escopo seletivo só é suportado para pytest.",
                "comando": _descricao_comando(argv), "codigo": None,
                "saida_resumida": "", "backend": None,
                "scope": normalized_scope, "tests_detected": True,
            }
        argv.append(normalized_scope)

    descricao_comando = _descricao_comando(argv)
    cfg_sandbox = dict(cfg_testes.get("sandbox") or {})
    cfg_sandbox.setdefault("timeout_segundos", cfg_testes.get("timeout_segundos", 60))
    resultado = executar_no_sandbox(caminho_projeto, argv, cfg_sandbox)
    saida_resumida = (resultado.get("saida") or "").strip()[-4000:]
    backend = resultado.get("backend", "sandbox")
    codigo = resultado.get("codigo")
    runner, runner_detail = _runner_unavailable_message(argv, resultado)
    if runner_detail:
        return {
            "executado": False, "ok": False, "recusado": False,
            "error_code": "TEST_RUNNER_UNAVAILABLE",
            "runner": runner, "detalhe": runner_detail,
            "comando": descricao_comando, "codigo": codigo,
            "saida_resumida": saida_resumida, "backend": backend,
            "scope": normalized_scope, "tests_detected": True,
        }

    if resultado.get("executado") is not True:
        detalhe = f"Teste recusado pelo sandbox: {resultado.get('erro') or 'erro desconhecido'}."
        return {
            "executado": False, "ok": False, "recusado": True, "detalhe": detalhe,
            "comando": descricao_comando, "codigo": codigo, "saida_resumida": saida_resumida,
            "backend": backend, "protected_resources_omitted": int(resultado.get("protected_resources_omitted") or 0), "scope": normalized_scope, "tests_detected": True,
        }
    if resultado.get("ok") is True:
        detalhe = f"'{descricao_comando}' passou no sandbox ({backend}).\n{saida_resumida}".rstrip()
        return {
            "executado": True, "ok": True, "detalhe": detalhe,
            "comando": descricao_comando, "codigo": codigo, "saida_resumida": saida_resumida,
            "backend": backend, "protected_resources_omitted": int(resultado.get("protected_resources_omitted") or 0), "scope": normalized_scope, "tests_detected": True,
        }
    erro = resultado.get("erro")
    complemento = f" {erro}." if erro else ""
    detalhe = (
        f"'{descricao_comando}' falhou no sandbox (codigo {codigo})."
        f"{complemento}\n{saida_resumida}"
    ).rstrip()
    return {
        "executado": True, "ok": False, "detalhe": detalhe,
        "comando": descricao_comando, "codigo": codigo, "saida_resumida": saida_resumida,
        "backend": backend, "scope": normalized_scope, "tests_detected": True,
    }


