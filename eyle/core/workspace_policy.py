"""File policy shared by live workspace discovery and reading."""
from __future__ import annotations

import os
import re

from .security import _resolver_caminho_seguro

EXTENSOES_TEXTO = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".sql", ".md", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sh", ".bat",
}
PASTAS_IGNORADAS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env", "dist", "build",
    ".idea", ".vscode", "target", ".mypy_cache", ".pytest_cache", "eyle-base",
}
NOMES_SECRETOS = {
    ".env", ".npmrc", ".pypirc", ".netrc", "credentials.json", "credential.json",
    "secrets.json", "secret.json", "tokens.json", "token.json", "service-account.json",
    "service_account.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
SUFIXOS_SECRETOS = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx")
PADROES_SEGREDO_CONTEUDO = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
)


def _glob_gitignore_para_regex(padrao):
    result=[]; i=0
    while i < len(padrao):
        c=padrao[i]
        if c == "*":
            if i+1 < len(padrao) and padrao[i+1] == "*":
                i += 2
                if i < len(padrao) and padrao[i] == "/": result.append("(?:.*/)?"); i += 1
                else: result.append(".*")
                continue
            result.append("[^/]*")
        elif c == "?": result.append("[^/]")
        elif c == "[":
            end=padrao.find("]",i+1)
            if end == -1: result.append(r"\[")
            else:
                cls=padrao[i+1:end]
                if cls.startswith("!"): cls="^"+cls[1:]
                elif cls.startswith("^"): cls="\\"+cls
                result.append("["+cls+"]"); i=end
        else: result.append(re.escape(c))
        i += 1
    return "".join(result)


def _carregar_gitignore(caminho_projeto, diretorio_abs, diretorio_rel=""):
    rel=os.path.join(diretorio_rel,".gitignore") if diretorio_rel else ".gitignore"
    safe=_resolver_caminho_seguro(caminho_projeto,rel)
    if safe is None or not os.path.isfile(safe): return []
    try: lines=open(safe,"r",encoding="utf-8",errors="replace").read(1024*1024).splitlines()
    except OSError: return []
    rules=[]; base=diretorio_rel.replace(os.sep,"/").strip("/")
    for line in lines:
        line=line.rstrip()
        if not line: continue
        if line.startswith(r"\#"): line=line[1:]
        elif line.startswith("#"): continue
        neg=False
        if line.startswith(r"\!"): line=line[1:]
        elif line.startswith("!"): neg=True; line=line[1:]
        if not line: continue
        dir_only=line.endswith("/"); line=line.rstrip("/"); anchored=line.startswith("/"); line=line.lstrip("/")
        if not line: continue
        try: regex=re.compile("^"+_glob_gitignore_para_regex(line)+"$")
        except re.error: continue
        rules.append({"base":base,"negada":neg,"somente_diretorio":dir_only,"tem_barra":anchored or "/" in line,"regex":regex})
    return rules


def _ignorado_por_gitignore(caminho_relativo, diretorio, regras):
    rel=caminho_relativo.replace(os.sep,"/").strip("/"); ignored=False
    for rule in regras:
        base=rule["base"]
        if base:
            if rel == base: target=""
            elif rel.startswith(base+"/"): target=rel[len(base)+1:]
            else: continue
        else: target=rel
        if rule["somente_diretorio"] and not diretorio: continue
        candidate=target if rule["tem_barra"] else target.rsplit("/",1)[-1]
        if rule["regex"].match(candidate): ignored=not rule["negada"]
    return ignored


def _caminho_parece_segredo(caminho_relativo):
    name=os.path.basename(caminho_relativo).lower()
    return name in NOMES_SECRETOS or name.startswith(".env.") or name.endswith(SUFIXOS_SECRETOS)


def _conteudo_parece_segredo(conteudo):
    sample=str(conteudo or "")[:512*1024]
    return any(pattern.search(sample) for pattern in PADROES_SEGREDO_CONTEUDO)
