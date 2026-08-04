#!/usr/bin/env python3
"""
ingest.py
---------
Varre uma pasta de projeto (pode ter dezenas de milhares de tokens) e
constrói a MEMORIA EXTERNA da Eyle:

    memory/projeto.json     -> identidade/resumo do projeto
    memory/estrutura.json   -> mapa de arquivos, funcoes e classes
    memory/chunks.jsonl     -> o conteudo real, dividido em pedacos pequenos
    memory/historico.json   -> log de decisoes (criado vazio se nao existir)

Nada disso entra na LLM de uma vez. E so o "HD" da inteligencia.
A LLM so ve o que o retrieval/buscar.py selecionar depois.

Uso:
    python ingest.py /caminho/do/projeto
    python ingest.py /caminho/do/projeto --nome "MeuProjeto" --out memory/
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.entender import gerar_entendimento_arquivos  # Atualizacao 3 -- Modelo Interno do Projeto
from engine.seguranca import _resolver_caminho_seguro
from engine.persistencia import salvar_json_atomico, salvar_jsonl_atomico
from engine.config_schema import carregar_config_validada

EXTENSOES_TEXTO = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".sql",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".sh", ".bat",
}

PASTAS_IGNORADAS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".idea", ".vscode", "target", ".mypy_cache",
    ".pytest_cache", "eyle-base",
}

# Atualizacao 29. A lista mira arquivos que frequentemente contem segredo
# real. Ela e deliberadamente conservadora com nomes de codigo comuns:
# ``secrets.py`` pode ser modulo legitimo, mas ``secrets.json`` e credencial.
NOMES_SECRETOS = {
    ".env", ".npmrc", ".pypirc", ".netrc", "credentials.json",
    "credential.json", "secrets.json", "secret.json", "tokens.json",
    "token.json", "service-account.json", "service_account.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
SUFIXOS_SECRETOS = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx")
PADROES_SEGREDO_CONTEUDO = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
)

INDEXER_VERSION = "2.0"

# JS/TS continua com o reconhecedor leve; Python usa AST (Atualizacao 24).
RE_DEF_JS = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?class\s+(\w+)|^\s*const\s+(\w+)\s*=\s*(?:async\s*)?\(")


def _glob_gitignore_para_regex(padrao):
    """Traduz o subconjunto completo de glob usado por .gitignore.

    ``*`` nao atravessa ``/``; ``**`` atravessa. Classes ``[abc]`` e ``?``
    tambem sao aceitas. A ancoragem e decidida pelo chamador.
    """
    resultado = []
    i = 0
    while i < len(padrao):
        caractere = padrao[i]
        if caractere == "*":
            if i + 1 < len(padrao) and padrao[i + 1] == "*":
                i += 2
                if i < len(padrao) and padrao[i] == "/":
                    resultado.append("(?:.*/)?")
                    i += 1
                else:
                    resultado.append(".*")
                continue
            resultado.append("[^/]*")
        elif caractere == "?":
            resultado.append("[^/]")
        elif caractere == "[":
            fim = padrao.find("]", i + 1)
            if fim == -1:
                resultado.append(r"\[")
            else:
                classe = padrao[i + 1:fim]
                if classe.startswith("!"):
                    classe = "^" + classe[1:]
                elif classe.startswith("^"):
                    classe = "\\" + classe
                resultado.append("[" + classe + "]")
                i = fim
        else:
            resultado.append(re.escape(caractere))
        i += 1
    return "".join(resultado)


def _carregar_gitignore(caminho_projeto, diretorio_abs, diretorio_rel=""):
    caminho_rel_gitignore = os.path.join(diretorio_rel, ".gitignore") if diretorio_rel else ".gitignore"
    caminho_seguro = _resolver_caminho_seguro(caminho_projeto, caminho_rel_gitignore)
    if caminho_seguro is None or not os.path.isfile(caminho_seguro):
        return []
    try:
        with open(caminho_seguro, "r", encoding="utf-8", errors="replace") as arquivo:
            linhas = arquivo.read(1024 * 1024).splitlines()
    except OSError:
        return []

    regras = []
    base = diretorio_rel.replace(os.sep, "/").strip("/")
    for linha in linhas:
        linha = linha.rstrip()
        if not linha:
            continue
        if linha.startswith(r"\#"):
            linha = linha[1:]
        elif linha.startswith("#"):
            continue

        negada = False
        if linha.startswith(r"\!"):
            linha = linha[1:]
        elif linha.startswith("!"):
            negada = True
            linha = linha[1:]
        if not linha:
            continue

        somente_diretorio = linha.endswith("/")
        linha = linha.rstrip("/")
        ancorada = linha.startswith("/")
        linha = linha.lstrip("/")
        if not linha:
            continue
        tem_barra = ancorada or "/" in linha
        try:
            regex = re.compile("^" + _glob_gitignore_para_regex(linha) + "$")
        except re.error:
            continue
        regras.append({
            "base": base,
            "negada": negada,
            "somente_diretorio": somente_diretorio,
            "tem_barra": tem_barra,
            "regex": regex,
        })
    return regras


def _ignorado_por_gitignore(caminho_relativo, diretorio, regras):
    relativo = caminho_relativo.replace(os.sep, "/").strip("/")
    ignorado = False
    for regra in regras:
        base = regra["base"]
        if base:
            if relativo == base:
                alvo = ""
            elif relativo.startswith(base + "/"):
                alvo = relativo[len(base) + 1:]
            else:
                continue
        else:
            alvo = relativo

        if regra["somente_diretorio"] and not diretorio:
            continue
        candidato = alvo if regra["tem_barra"] else alvo.rsplit("/", 1)[-1]
        if regra["regex"].match(candidato):
            ignorado = not regra["negada"]
    return ignorado


def _caminho_parece_segredo(caminho_relativo):
    nome = os.path.basename(caminho_relativo).lower()
    if nome in NOMES_SECRETOS or nome.startswith(".env."):
        return True
    return nome.endswith(SUFIXOS_SECRETOS)


def _conteudo_parece_segredo(conteudo):
    amostra = conteudo[:512 * 1024]
    return any(padrao.search(amostra) for padrao in PADROES_SEGREDO_CONTEUDO)


def _listar_arquivos_ingestao(caminho_projeto):
    """Lista candidatos sem seguir diretorios symlink e com motivo de rejeicao."""
    ignorados = {"padrao_interno": 0, "gitignore": 0, "segredo": 0, "symlink_externo": 0}

    def visitar(diretorio_abs, diretorio_rel, regras_herdadas):
        regras = regras_herdadas + _carregar_gitignore(
            caminho_projeto, diretorio_abs, diretorio_rel,
        )
        try:
            entradas = sorted(os.scandir(diretorio_abs), key=lambda item: item.name)
        except OSError:
            return

        for entrada in entradas:
            caminho_rel = os.path.join(diretorio_rel, entrada.name) if diretorio_rel else entrada.name
            caminho_seguro = _resolver_caminho_seguro(caminho_projeto, caminho_rel)
            if caminho_seguro is None:
                ignorados["symlink_externo"] += 1
                continue

            try:
                e_symlink = entrada.is_symlink()
                e_diretorio = entrada.is_dir(follow_symlinks=False)
            except OSError:
                continue

            if e_diretorio:
                if entrada.name in PASTAS_IGNORADAS or entrada.name.startswith("."):
                    ignorados["padrao_interno"] += 1
                    continue
                if _ignorado_por_gitignore(caminho_rel, True, regras):
                    ignorados["gitignore"] += 1
                    continue
                yield from visitar(caminho_seguro, caminho_rel, regras)
                continue

            # Nao segue links de diretorio, mesmo internos: evita ciclos e
            # indexacao duplicada. Links de arquivo internos podem ser lidos
            # pelo alvo real ja validado pelo resolvedor compartilhado.
            if e_symlink and os.path.isdir(caminho_seguro):
                ignorados["padrao_interno"] += 1
                continue
            if _ignorado_por_gitignore(caminho_rel, False, regras):
                ignorados["gitignore"] += 1
                continue
            if _caminho_parece_segredo(caminho_rel):
                ignorados["segredo"] += 1
                continue
            yield caminho_rel, caminho_seguro

    return visitar(caminho_projeto, "", []), ignorados


def _coletar_arquivos_indexaveis(caminho_projeto):
    """Le uma vez os candidatos que realmente podem entrar no indice."""
    arquivos = []
    candidatos, ignorados = _listar_arquivos_ingestao(caminho_projeto)
    for caminho_rel, caminho_abs in candidatos:
        ext = os.path.splitext(caminho_rel)[1].lower()
        if ext not in EXTENSOES_TEXTO:
            continue
        try:
            with open(caminho_abs, "r", encoding="utf-8", errors="ignore") as arquivo:
                conteudo = arquivo.read()
        except OSError:
            continue
        if _conteudo_parece_segredo(conteudo):
            ignorados["segredo"] += 1
            continue
        arquivos.append((caminho_rel, caminho_abs, conteudo))
    return arquivos, ignorados


def _config_indexacao(config, chunk_max_tokens, chars_per_token):
    config = config or {}
    cfg_llm = config.get("llm", {})
    cfg_entendimento = config.get("entendimento", {})
    return {
        "indexer_version": INDEXER_VERSION,
        "chunk_max_tokens": int(chunk_max_tokens),
        "chars_per_token": int(chars_per_token),
        "entendimento": {
            "gerar_via_llm": bool(cfg_entendimento.get("gerar_via_llm", True)),
            "max_chars_por_arquivo": cfg_entendimento.get("max_chars_por_arquivo", 20000),
        },
        "llm": {
            "provider": cfg_llm.get("provider", "ollama"),
            "base_url": str(cfg_llm.get("base_url", "http://localhost:11434")).rstrip("/"),
            "model": cfg_llm.get("model", "qwen2.5:7b-instruct-q4_0"),
            "openai_compatible": bool(cfg_llm.get("openai_compatible", False)),
            "temperature": cfg_llm.get("temperature", 0.2),
            "max_tokens": cfg_llm.get("max_tokens", 700),
        },
    }


def _fingerprint_arquivos(arquivos, config, chunk_max_tokens, chars_per_token):
    manifesto = [
        {
            "arquivo": caminho_rel.replace(os.sep, "/"),
            "sha256": hashlib.sha256(conteudo.encode("utf-8")).hexdigest(),
        }
        for caminho_rel, _caminho_abs, conteudo in arquivos
    ]
    manifesto.sort(key=lambda item: item["arquivo"])
    payload = {
        "config": _config_indexacao(config, chunk_max_tokens, chars_per_token),
        "arquivos": manifesto,
    }
    canonico = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonico).hexdigest()


def calcular_index_fingerprint(
    caminho_projeto, config=None, chunk_max_tokens=400, chars_per_token=4,
):
    """Recalcula o fingerprint da fonte atual sem gravar memoria."""
    caminho_projeto = os.path.realpath(os.path.abspath(caminho_projeto))
    arquivos, _ignorados = _coletar_arquivos_indexaveis(caminho_projeto)
    return _fingerprint_arquivos(
        arquivos, config, chunk_max_tokens, chars_per_token,
    )


def indice_esta_atual(projeto, config=None):
    """True/False para indices novos; ``None`` para memoria legada."""
    projeto = projeto or {}
    esperado = projeto.get("index_fingerprint")
    caminho = projeto.get("caminho_origem")
    settings = projeto.get("index_settings") or {}
    if not esperado or not caminho or not os.path.isdir(caminho):
        return None
    atual = calcular_index_fingerprint(
        caminho, config=config,
        chunk_max_tokens=settings.get("chunk_max_tokens", 400),
        chars_per_token=settings.get("chars_per_token", 4),
    )
    return atual == esperado


def estimar_tokens(texto: str, chars_per_token: int = 4) -> int:
    """Estimativa rapida sem depender de nenhuma lib de tokenizacao."""
    return max(1, len(texto) // chars_per_token)


def sha256_arquivo(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


RE_DOCSTRING_PY = re.compile(r'^\s*"""(.*?)"""', re.DOTALL)
RE_DOCSTRING_PY_ALT = re.compile(r"^\s*'''(.*?)'''", re.DOTALL)
RE_COMENTARIO_BLOCO_JS = re.compile(r"^\s*/\*(.*?)\*/", re.DOTALL)


def extrair_resumo_modulo(caminho_abs):
    """
    Extrai um resumo curto (1 frase) do topo de um arquivo:
      - Python: primeira docstring de modulo (\"\"\"...\"\"\" ou '''...''').
      - JS/TS: primeiro comentario de bloco (/* ... */).
    Devolve None se nao encontrar nada aproveitavel. Nao usa LLM --
    so le o que o proprio autor do arquivo ja escreveu.
    """
    try:
        with open(caminho_abs, "r", encoding="utf-8", errors="ignore") as f:
            conteudo = f.read(2000)  # so precisa do topo do arquivo
    except Exception:
        return None

    ext = os.path.splitext(caminho_abs)[1].lower()
    bruto = None

    if ext == ".py":
        # pula linhas de shebang (#!...) e comentarios/vazias antes da docstring
        linhas_topo = conteudo.splitlines(keepends=True)
        i = 0
        while i < len(linhas_topo) and (
            linhas_topo[i].strip() == "" or linhas_topo[i].lstrip().startswith("#")
        ):
            i += 1
        conteudo = "".join(linhas_topo[i:])
        m = RE_DOCSTRING_PY.match(conteudo) or RE_DOCSTRING_PY_ALT.match(conteudo)
        if m:
            bruto = m.group(1)
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        m = RE_COMENTARIO_BLOCO_JS.match(conteudo)
        if m:
            bruto = m.group(1)

    if not bruto:
        return None

    nome_arquivo = os.path.basename(caminho_abs)
    re_so_marcadores = re.compile(r"^[-=*_]+$")

    # pega a primeira linha nao vazia e "substantiva" do bloco: ignora linhas
    # que so repetem o nome do arquivo (padrao comum "engine.py\n---------")
    # e linhas que sao so tracos/marcadores.
    for linha in bruto.strip().splitlines():
        linha = linha.strip().strip("-*# ").strip()
        if not linha or linha == nome_arquivo or re_so_marcadores.match(linha):
            continue
        return linha
    return None


def montar_entendimento(estrutura, caminho_projeto, entendimento_existente=None):
    """
    Agrupa arquivos por componente (pasta de primeiro nivel) e tenta
    preencher a "funcao" de cada componente com o resumo extraido de
    extrair_resumo_modulo() (sem LLM). Se ja existir uma "funcao" escrita
    a mao em memory/entendimento.json, essa versao humana e preservada --
    o ingest so preenche o que ainda esta vazio.
    """
    entendimento_existente = entendimento_existente or {}
    componentes_existentes = entendimento_existente.get("componentes", {})

    componentes = {}

    for rel in estrutura:
        pasta = rel.split(os.sep)[0] if os.sep in rel else "raiz"
        componente = componentes.setdefault(pasta, {"funcao": None, "arquivos": []})
        componente["arquivos"].append(rel)

    for nome, componente in componentes.items():
        funcao_manual = componentes_existentes.get(nome, {}).get("funcao")
        if funcao_manual:
            componente["funcao"] = funcao_manual
            continue
        # tenta extrair de cada arquivo do componente ate achar um resumo
        for rel in sorted(componente["arquivos"]):
            caminho_seguro = _resolver_caminho_seguro(caminho_projeto, rel)
            if caminho_seguro is None:
                continue
            resumo = extrair_resumo_modulo(caminho_seguro)
            if resumo:
                componente["funcao"] = resumo
                break

    return {
        "version": "1.0",
        "componentes": componentes,
    }


def gerar_evidencias(estrutura, evidencias_existente=None):
    """
    Gera memory/evidencias.json a partir dos simbolos (funcoes/classes) ja
    encontrados em estrutura.json: cada simbolo vira uma entidade com
    'entity' + 'defined_in' confirmados (defined_in aponta pro arquivo real,
    entao e sempre validado=true por definicao). 'used_by' e preservado se
    ja tinha sido preenchido antes (isso exige analise de chamadas, fora do
    escopo do ingest); novas entidades comecam com used_by vazio.
    """
    evidencias_existente = evidencias_existente or {}
    existentes_por_entity = {e.get("entity"): e for e in evidencias_existente.get("entidades", [])}

    entidades = []
    for arquivo, info in estrutura.items():
        for nome in info.get("funcoes_classes", []):
            anterior = existentes_por_entity.get(nome, {})
            entidades.append({
                "entity": nome,
                "defined_in": arquivo,
                "used_by": anterior.get("used_by", []),
                "validated_by": "estrutura.json",
            })

    return {
        "version": "1.0",
        "entidades": entidades,
    }


def extrair_definicoes_python(linhas):
    """
    Extrai funcoes, classes e metodos com posicoes reais via AST.

    Metodos recebem nome qualificado (``Classe.run``), inclusive em classes
    aninhadas. Funcoes locais nao viram simbolos independentes: elas fazem
    parte do chunk e do recorte da funcao externa. O inicio inclui
    decorators, evitando que eles desaparecam entre chunks.
    """
    try:
        arvore = ast.parse("\n".join(linhas))
    except (SyntaxError, ValueError, TypeError):
        return []

    definicoes = []

    def visitar_corpo(corpo, prefixo=""):
        for no in corpo:
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue

            nome = f"{prefixo}.{no.name}" if prefixo else no.name
            decorators = getattr(no, "decorator_list", None) or []
            inicio = min([no.lineno] + [d.lineno for d in decorators])
            fim = getattr(no, "end_lineno", None) or no.lineno
            definicoes.append({
                "nome": nome,
                "linha_inicio": inicio,
                "linha_fim": fim,
                "tipo": (
                    "classe" if isinstance(no, ast.ClassDef)
                    else "funcao_assincrona" if isinstance(no, ast.AsyncFunctionDef)
                    else "funcao"
                ),
            })

            # Dentro de classes, metodos e classes aninhadas sao entidades
            # do indice. Dentro de funcoes, defs locais permanecem parte do
            # simbolo externo e nao poluem a estrutura global.
            if isinstance(no, ast.ClassDef):
                visitar_corpo(no.body, nome)

    visitar_corpo(arvore.body)
    definicoes.sort(key=lambda d: (d["linha_inicio"], d["linha_fim"], d["nome"]))
    return definicoes


def extrair_simbolos(linhas, extensao):
    """Retorna lista de (nome, numero_da_linha) de funcoes/classes encontradas."""
    if extensao == ".py":
        return [
            (d["nome"], d["linha_inicio"])
            for d in extrair_definicoes_python(linhas)
        ]

    simbolos = []
    if extensao in (".js", ".ts", ".jsx", ".tsx"):
        for i, linha in enumerate(linhas, start=1):
            m = RE_DEF_JS.match(linha)
            if m:
                nome = next(g for g in m.groups() if g)
                simbolos.append((nome, i))
    return simbolos


def dividir_em_chunks(caminho_relativo, linhas, extensao, chunk_max_tokens, chars_per_token):
    """
    Divide o conteudo de um arquivo em pedacos pequenos (chunks).
    Tenta cortar em fronteiras de funcao/classe quando possivel (py/js),
    senao usa corte por tamanho fixo.
    """
    chunks = []
    simbolos = extrair_simbolos(linhas, extensao)

    if simbolos:
        if extensao == ".py":
            # O reconhecedor antigo comecava no primeiro def/class e perdia
            # imports, constantes, docstring e decorators anteriores. Esse
            # material agora ganha chunk proprio e pesquisavel.
            primeiro_inicio = min(linha for _, linha in simbolos)
            if primeiro_inicio > 1:
                preambulo = "\n".join(linhas[:primeiro_inicio - 1])
                if preambulo.strip():
                    for sub_inicio, sub_fim, sub_texto in _subdividir_por_tamanho(
                        preambulo, 1, chunk_max_tokens, chars_per_token
                    ):
                        chunks.append({
                            "arquivo": caminho_relativo,
                            "simbolo": None,
                            "tipo_chunk": "preambulo",
                            "linha_inicio": sub_inicio,
                            "linha_fim": sub_fim,
                            "texto": sub_texto,
                        })

        # corta um chunk a cada simbolo encontrado, ate o proximo simbolo
        limites = [linha for _, linha in simbolos] + [len(linhas) + 1]
        for idx, (nome, inicio) in enumerate(simbolos):
            fim = limites[idx + 1] - 1
            trecho = "\n".join(linhas[inicio - 1:fim])
            # se o "trecho" de um simbolo for gigante, subdivide por tamanho
            for sub_inicio, sub_fim, sub_texto in _subdividir_por_tamanho(
                trecho, inicio, chunk_max_tokens, chars_per_token
            ):
                chunks.append({
                    "arquivo": caminho_relativo,
                    "simbolo": nome,
                    "tipo_chunk": "simbolo",
                    "linha_inicio": sub_inicio,
                    "linha_fim": sub_fim,
                    "texto": sub_texto,
                })
    else:
        # Sem simbolos reconhecidos, o arquivo inteiro ainda e pesquisavel.
        # Em Python valido isso tambem e o preambulo completo do modulo.
        texto_completo = "\n".join(linhas)
        for sub_inicio, sub_fim, sub_texto in _subdividir_por_tamanho(
            texto_completo, 1, chunk_max_tokens, chars_per_token
        ):
            chunks.append({
                "arquivo": caminho_relativo,
                "simbolo": None,
                "tipo_chunk": "preambulo" if extensao == ".py" else "generico",
                "linha_inicio": sub_inicio,
                "linha_fim": sub_fim,
                "texto": sub_texto,
            })
    return chunks


def _subdividir_por_tamanho(texto, linha_inicial, chunk_max_tokens, chars_per_token):
    max_chars = chunk_max_tokens * chars_per_token
    linhas = texto.split("\n")
    resultado = []
    buf = []
    tam = 0
    inicio_atual = linha_inicial
    linha_num = linha_inicial
    for linha in linhas:
        buf.append(linha)
        tam += len(linha) + 1
        if tam >= max_chars:
            resultado.append((inicio_atual, linha_num, "\n".join(buf)))
            buf = []
            tam = 0
            inicio_atual = linha_num + 1
        linha_num += 1
    if buf:
        resultado.append((inicio_atual, linha_num - 1, "\n".join(buf)))
    return resultado


def ingerir(caminho_projeto, nome_projeto, out_dir, chunk_max_tokens=400, chars_per_token=4, config=None):
    caminho_projeto = os.path.abspath(caminho_projeto)
    if not os.path.isdir(caminho_projeto):
        print(f"Erro: pasta nao encontrada: {caminho_projeto}")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    estrutura = {}
    todos_chunks = []
    total_tokens = 0
    total_arquivos = 0

    arquivos_indexaveis, ignorados = _coletar_arquivos_indexaveis(caminho_projeto)
    index_fingerprint = _fingerprint_arquivos(
        arquivos_indexaveis, config, chunk_max_tokens, chars_per_token,
    )
    for caminho_rel, caminho_abs, conteudo in arquivos_indexaveis:
        ext = os.path.splitext(caminho_rel)[1].lower()

        linhas = conteudo.split("\n")
        tokens_arquivo = estimar_tokens(conteudo, chars_per_token)
        simbolos = extrair_simbolos(linhas, ext)

        estrutura[caminho_rel] = {
            "linhas": len(linhas),
            "tokens_estimados": tokens_arquivo,
            "funcoes_classes": [nome for nome, _ in simbolos],
            "hash": hashlib.sha256(conteudo.encode("utf-8")).hexdigest()[:16],
        }

        chunks = dividir_em_chunks(caminho_rel, linhas, ext, chunk_max_tokens, chars_per_token)
        for c in chunks:
            c["tokens"] = estimar_tokens(c["texto"], chars_per_token)
            c["id"] = hashlib.md5(
                f"{c['arquivo']}:{c['linha_inicio']}:{c['linha_fim']}".encode()
            ).hexdigest()[:12]
        todos_chunks.extend(chunks)

        total_tokens += tokens_arquivo
        total_arquivos += 1

    agora = time.strftime("%Y-%m-%dT%H:%M:%S")

    projeto_json = {
        "projeto": nome_projeto,
        "caminho_origem": caminho_projeto,
        "arquivos": total_arquivos,
        "chunks": len(todos_chunks),
        "tokens_estimados_totais": total_tokens,
        "criado_ou_atualizado_em": agora,
        "version": "1.0",
        "source_path_hash": hashlib.sha256(caminho_projeto.encode()).hexdigest()[:16],
        "index_fingerprint": index_fingerprint,
        "index_settings": {
            "indexer_version": INDEXER_VERSION,
            "chunk_max_tokens": int(chunk_max_tokens),
            "chars_per_token": int(chars_per_token),
        },
        "arquivos_ignorados": ignorados,
    }

    estrutura_json = {
        "version": "1.0",
        "updated": agora,
        "arquivos": estrutura,
    }

    salvar_json_atomico(os.path.join(out_dir, "projeto.json"), projeto_json)
    salvar_json_atomico(os.path.join(out_dir, "estrutura.json"), estrutura_json)
    salvar_jsonl_atomico(os.path.join(out_dir, "chunks.jsonl"), todos_chunks)

    entendimento_path = os.path.join(out_dir, "entendimento.json")
    entendimento_existente = {}
    if os.path.exists(entendimento_path):
        with open(entendimento_path, "r", encoding="utf-8") as f:
            entendimento_existente = json.load(f)
    # "componentes": resumo heuristico por pasta (sem LLM), como ja era -- preservado
    entendimento_json = montar_entendimento(estrutura, caminho_projeto, entendimento_existente)
    # "arquivos": Modelo Interno do Projeto (Atualizacao 3) -- um objeto por
    # arquivo, gerado pela LLM lendo o arquivo inteiro, so regenerando o que
    # mudou (via hash). Ver engine/entender.py.
    entendimento_json["arquivos"] = gerar_entendimento_arquivos(
        estrutura, caminho_projeto, config=config, entendimento_existente=entendimento_existente,
    )
    entendimento_json["version"] = "1.1"
    entendimento_json["updated"] = agora
    salvar_json_atomico(entendimento_path, entendimento_json)

    evidencias_path = os.path.join(out_dir, "evidencias.json")
    evidencias_existente = {}
    if os.path.exists(evidencias_path):
        with open(evidencias_path, "r", encoding="utf-8") as f:
            evidencias_existente = json.load(f)
    evidencias_json = gerar_evidencias(estrutura, evidencias_existente)
    evidencias_json["updated"] = agora
    salvar_json_atomico(evidencias_path, evidencias_json)

    decisoes_path = os.path.join(out_dir, "decisoes.json")
    if not os.path.exists(decisoes_path):
        salvar_json_atomico(
            decisoes_path,
            {"version": "1.0", "updated": None, "decisoes": []},
        )

    historico_path = os.path.join(out_dir, "historico.json")
    if not os.path.exists(historico_path):
        salvar_json_atomico(
            historico_path,
            {"version": "1.0", "updated": agora, "decisoes": []},
        )

    print(f"[ingest] Projeto '{nome_projeto}' indexado com sucesso.")
    print(f"[ingest] Arquivos:          {total_arquivos}")
    print(f"[ingest] Chunks gerados:    {len(todos_chunks)}")
    print(f"[ingest] Tokens estimados:  {total_tokens}  (memoria externa, fora da LLM)")
    print(f"[ingest] Ignorados com seguranca: {sum(ignorados.values())} {ignorados}")
    print(f"[ingest] Componentes (entendimento.json['componentes']): {len(entendimento_json['componentes'])}")
    print(f"[ingest] Arquivos com entendimento (entendimento.json['arquivos']): {len(entendimento_json['arquivos'])}")
    print(f"[ingest] Entidades (evidencias.json):      {len(evidencias_json['entidades'])}")
    print(f"[ingest] Saida em:          {out_dir}")


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Indexa um projeto na memoria externa da Eyle")
    parser.add_argument("caminho", nargs="?", default=os.path.join(base_dir, "workspace"),
                         help="Pasta do projeto a ser indexado (default: workspace/)")
    parser.add_argument("--nome", default=None, help="Nome do projeto (default: nome da pasta)")
    parser.add_argument("--out", default=os.path.join(base_dir, "memory"),
                         help="Pasta de saida da memoria (default: ./memory)")
    parser.add_argument("--chunk-max-tokens", type=int, default=400)
    parser.add_argument("--config", default=os.path.join(base_dir, "config.json"),
                         help="config.json a usar (default: ./config.json) -- controla o endpoint da LLM "
                              "usada para gerar entendimento.json['arquivos'] (Atualizacao 3)")
    parser.add_argument("--pular-entendimento-llm", action="store_true",
                         help="Ignora config.json['entendimento']['gerar_via_llm'] e forca ingest so-heuristico "
                              "(sem chamar a LLM, sem precisar de servidor local rodando). Entradas antigas de "
                              "entendimento.json['arquivos'] sao preservadas.")
    args = parser.parse_args()

    config = carregar_config_validada(args.config)
    if args.pular_entendimento_llm:
        config.setdefault("entendimento", {})["gerar_via_llm"] = False

    nome = args.nome or os.path.basename(os.path.normpath(args.caminho))
    ingerir(
        args.caminho, nome, args.out,
        chunk_max_tokens=args.chunk_max_tokens,
        chars_per_token=config.get("context", {}).get("chars_per_token", 4),
        config=config,
    )


if __name__ == "__main__":
    main()
