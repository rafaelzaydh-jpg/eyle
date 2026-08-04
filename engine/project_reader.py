#!/usr/bin/env python3
"""Leituras frescas e seguras do projeto usadas pelas tools do Agente.

Atualizacao 41: concentra a arvore atual do projeto e a leitura numerada
por faixa. Nenhuma funcao usa o indice como fonte do conteudo: o indice
serve apenas para localizar candidatos; os bytes sao relidos do disco.
"""
import fnmatch
import os

from engine.seguranca import _resolver_caminho_seguro
from engine.text_hash import hash_faixa, hash_texto, normalizar_quebras
from ingest import (
    EXTENSOES_TEXTO,
    PASTAS_IGNORADAS,
    _carregar_gitignore,
    _caminho_parece_segredo,
    _conteudo_parece_segredo,
    _ignorado_por_gitignore,
)


class ErroLeituraProjeto(ValueError):
    """Erro esperado de leitura, com codigo estavel para o contrato da tool."""

    def __init__(self, error_code, detail):
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


def ler_faixa_projeto(caminho_projeto, caminho_relativo, linha_inicio,
                      linha_fim, max_linhas=400):
    """Le uma faixa 1-based fresca, numerada e com hashes canonicos.

    ``content_hash`` protege exatamente a faixa proposta para edicao;
    ``file_hash`` protege o conteudo logico do arquivo inteiro. Quebras CRLF,
    CR e LF sao tratadas como LF para evitar falso ``STALE_PATCH`` entre
    Windows, WSL, Docker e Linux.
    """
    if not isinstance(linha_inicio, int) or isinstance(linha_inicio, bool):
        raise ErroLeituraProjeto("INVALID_ARGUMENT", "linha_inicio precisa ser inteiro")
    if not isinstance(linha_fim, int) or isinstance(linha_fim, bool):
        raise ErroLeituraProjeto("INVALID_ARGUMENT", "linha_fim precisa ser inteiro")
    if linha_inicio < 1 or linha_fim < linha_inicio:
        raise ErroLeituraProjeto(
            "INVALID_RANGE",
            "a faixa precisa satisfazer 1 <= linha_inicio <= linha_fim",
        )
    quantidade = linha_fim - linha_inicio + 1
    if quantidade > max_linhas:
        raise ErroLeituraProjeto(
            "RANGE_TOO_LARGE",
            f"a faixa pediu {quantidade} linhas; o limite configurado e {max_linhas}",
        )

    caminho_abs = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
    if caminho_abs is None:
        raise ErroLeituraProjeto(
            "UNSAFE_PATH",
            f"caminho inseguro rejeitado: '{caminho_relativo}' deve permanecer dentro do projeto",
        )
    if not os.path.isfile(caminho_abs):
        raise ErroLeituraProjeto(
            "FILE_NOT_FOUND",
            f"arquivo '{caminho_relativo}' nao encontrado no disco",
        )

    try:
        with open(caminho_abs, "r", encoding="utf-8", errors="replace") as arquivo:
            conteudo = normalizar_quebras(arquivo.read())
    except OSError as erro:
        raise ErroLeituraProjeto(
            "FILE_READ_ERROR",
            f"nao foi possivel ler '{caminho_relativo}': {erro}",
        ) from erro

    linhas = conteudo.splitlines(keepends=True)
    total_linhas = len(linhas)
    if linha_inicio > total_linhas:
        raise ErroLeituraProjeto(
            "RANGE_OUT_OF_BOUNDS",
            f"linha_inicio={linha_inicio} excede as {total_linhas} linha(s) atuais do arquivo",
        )

    linha_fim_real = min(linha_fim, total_linhas)
    selecionadas = linhas[linha_inicio - 1:linha_fim_real]
    conteudo_lido = "".join(selecionadas)
    trecho_numerado = "\n".join(
        f"{numero:>6} | {linha.rstrip(chr(13) + chr(10))}"
        for numero, linha in enumerate(selecionadas, start=linha_inicio)
    )
    return {
        "arquivo": caminho_relativo,
        "linha_inicio": linha_inicio,
        "linha_fim": linha_fim_real,
        "linha_fim_solicitada": linha_fim,
        "total_linhas_arquivo": total_linhas,
        "trecho_numerado": trecho_numerado,
        "conteudo": conteudo_lido,
        "content_hash": hash_faixa(conteudo, linha_inicio, linha_fim_real),
        "file_hash": hash_texto(conteudo),
        "fim_ajustado_ao_arquivo": linha_fim_real != linha_fim,
    }


def _corresponde_filtro(caminho_relativo, filtro):
    if not filtro:
        return True
    caminho = caminho_relativo.replace(os.sep, "/").lower()
    padrao = filtro.strip().lower()
    if not padrao:
        return True
    if any(caractere in padrao for caractere in "*?["):
        return fnmatch.fnmatch(caminho, padrao)
    return padrao in caminho


def listar_arvore_projeto(caminho_projeto, limite=200, profundidade=6,
                          filtro=None, max_secret_scan_bytes=64 * 1024):
    """Lista a arvore atual respeitando filtros de ingestao e limites.

    Os motivos ignorados sao contagens, nunca nomes de arquivos secretos.
    Isso preserva a disciplina da Atualizacao 29.
    """
    raiz = os.path.realpath(os.fspath(caminho_projeto))
    if not os.path.isdir(raiz):
        raise ErroLeituraProjeto("PROJECT_NOT_FOUND", "a raiz atual do projeto nao existe")

    entradas = []
    ignorados = {
        "padrao_interno": 0,
        "gitignore": 0,
        "segredo": 0,
        "symlink_externo": 0,
        "extensao_nao_suportada": 0,
        "filtro": 0,
        "profundidade": 0,
        "erro_leitura": 0,
    }
    truncado = False

    def adicionar(caminho_rel, tipo, nivel):
        nonlocal truncado
        if len(entradas) >= limite:
            truncado = True
            return False
        entradas.append({
            "caminho": caminho_rel.replace(os.sep, "/"),
            "tipo": tipo,
            "profundidade": nivel,
        })
        return True

    def visitar(diretorio_abs, diretorio_rel, regras_herdadas, nivel):
        nonlocal truncado
        regras = regras_herdadas + _carregar_gitignore(
            raiz, diretorio_abs, diretorio_rel,
        )
        try:
            itens = sorted(os.scandir(diretorio_abs), key=lambda item: item.name.lower())
        except OSError:
            ignorados["erro_leitura"] += 1
            return

        for entrada in itens:
            if truncado:
                return
            caminho_rel = os.path.join(diretorio_rel, entrada.name) if diretorio_rel else entrada.name
            caminho_seguro = _resolver_caminho_seguro(raiz, caminho_rel)
            if caminho_seguro is None:
                ignorados["symlink_externo"] += 1
                continue
            try:
                e_symlink = entrada.is_symlink()
                e_diretorio = entrada.is_dir(follow_symlinks=False)
            except OSError:
                ignorados["erro_leitura"] += 1
                continue

            if e_diretorio:
                if entrada.name in PASTAS_IGNORADAS or entrada.name.startswith("."):
                    ignorados["padrao_interno"] += 1
                    continue
                if _ignorado_por_gitignore(caminho_rel, True, regras):
                    ignorados["gitignore"] += 1
                    continue
                if _corresponde_filtro(caminho_rel, filtro):
                    if not adicionar(caminho_rel, "diretorio", nivel):
                        return
                if nivel >= profundidade:
                    ignorados["profundidade"] += 1
                    continue
                visitar(caminho_seguro, caminho_rel, regras, nivel + 1)
                continue

            if e_symlink and os.path.isdir(caminho_seguro):
                ignorados["padrao_interno"] += 1
                continue
            if _ignorado_por_gitignore(caminho_rel, False, regras):
                ignorados["gitignore"] += 1
                continue
            if _caminho_parece_segredo(caminho_rel):
                ignorados["segredo"] += 1
                continue
            if os.path.splitext(caminho_rel)[1].lower() not in EXTENSOES_TEXTO:
                ignorados["extensao_nao_suportada"] += 1
                continue
            try:
                with open(caminho_seguro, "r", encoding="utf-8", errors="replace") as arquivo:
                    amostra = arquivo.read(max(1024, int(max_secret_scan_bytes)))
            except OSError:
                ignorados["erro_leitura"] += 1
                continue
            if _conteudo_parece_segredo(amostra):
                ignorados["segredo"] += 1
                continue
            if not _corresponde_filtro(caminho_rel, filtro):
                ignorados["filtro"] += 1
                continue
            if not adicionar(caminho_rel, "arquivo", nivel):
                return

    visitar(raiz, "", [], 1)
    return {
        "entradas": entradas,
        "total_retornado": len(entradas),
        "limite": limite,
        "profundidade_maxima": profundidade,
        "filtro": filtro,
        "truncado": truncado,
        "varredura_completa": not truncado,
        "ignorados_por_motivo": ignorados,
    }
