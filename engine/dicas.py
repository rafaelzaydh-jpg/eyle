#!/usr/bin/env python3
"""
dicas.py
--------
Atualizacao 4 -- "Dar dicas reais".

Ate a Atualizacao 3, uma pergunta so tinha um caminho ate o codigo:
retrieval/buscar.py (BM25 sobre chunks.jsonl -- bate contra as PALAVRAS
literais do codigo). Isso funciona bem para "onde fica X", mas nao para
"que sugestao voce tem pra esse projeto" -- a pergunta nao compartilha
vocabulario com nenhum chunk especifico.

Este modulo usa o Modelo Interno do Projeto (memory/entendimento.json
["arquivos"], gerado pela Atualizacao 3 em engine/entender.py) para
responder a pergunta de outro jeito:

    pergunta -> entendimento -> componentes candidatos (via
    tipo/responsabilidade/funcoes_principais/pontos_criticos, expandido
    por depende_de) -> le o CODIGO REAL desses componentes (arquivo
    inteiro, nao chunk) -> so entao a LLM analisa e sugere.

Nao decide nada sozinho: quem orquestra e engine/engine.py
(_processar_dicas), do mesmo jeito que retrieval/buscar.py e chamado por
ciclo_analista/_processar_consulta. Este modulo so sabe escolher
candidatos e ler codigo -- nao chama LLM.
"""
import os
import re

from engine.seguranca import _resolver_caminho_seguro

TOKEN_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _tokenizar(texto):
    return set(t.lower() for t in TOKEN_RE.findall(texto or ""))


def _texto_pesquisavel(caminho_relativo, info):
    """Todo o vocabulario textual disponivel sobre um arquivo no Modelo
    Interno -- o que ele FAZ e onde e fragil, nao o codigo em si. E contra
    isso que os tokens da pergunta sao comparados."""
    partes = [
        caminho_relativo,
        info.get("tipo") or "",
        info.get("responsabilidade") or "",
        " ".join(info.get("funcoes_principais") or []),
        " ".join(info.get("pontos_criticos") or []),
    ]
    return " ".join(p for p in partes if p)


def selecionar_componentes_candidatos(pergunta, entendimento, max_candidatos=5,
                                       profundidade_dependencia=1):
    """
    Escolhe arquivos candidatos a partir do Modelo Interno
    (entendimento.json['arquivos']) -- NAO de BM25 sobre chunks. O match e
    contra tipo/responsabilidade/funcoes_principais/pontos_criticos, ou
    seja: contra o que cada arquivo FAZ e onde ele e fragil, nao contra as
    palavras literais do codigo.

    Pontos criticos pesam em dobro no score: sao exatamente o tipo de coisa
    que vale avisar antes de sugerir mexer em algo (ex: pergunta menciona
    "acoplamento" e o pontos_criticos de engine.py diz "alto acoplamento").

    Depois do ranking direto, expande por 'depende_de' ate
    profundidade_dependencia niveis -- um componente candidato tambem
    precisa que o Executor veja do que ele depende, senao a sugestao ignora
    o resto do fluxo.

    Devolve lista de dicts [{"arquivo", "score", "motivo"}, ...], ordenada
    com os candidatos diretos primeiro (por score desc) e as dependencias
    expandidas no final (score 0, motivo explica de onde vieram).
    """
    arquivos = (entendimento or {}).get("arquivos", {})
    if not arquivos:
        return []

    tokens_pergunta = _tokenizar(pergunta)
    if not tokens_pergunta:
        return []

    pontuados = []
    for caminho_relativo, info in arquivos.items():
        tokens_arquivo = _tokenizar(_texto_pesquisavel(caminho_relativo, info))
        intersecao = tokens_pergunta & tokens_arquivo
        if not intersecao:
            continue
        score = len(intersecao)
        tokens_criticos = _tokenizar(" ".join(info.get("pontos_criticos") or []))
        score += len(tokens_pergunta & tokens_criticos)  # pontos_criticos pesa em dobro
        pontuados.append({
            "arquivo": caminho_relativo,
            "score": score,
            "motivo": "match direto (tipo/responsabilidade/funcoes_principais/pontos_criticos)",
        })

    pontuados.sort(key=lambda item: item["score"], reverse=True)
    diretos = pontuados[:max_candidatos]

    resultado = list(diretos)
    vistos = {item["arquivo"] for item in diretos}
    fronteira = [item["arquivo"] for item in diretos]

    for _ in range(max(0, profundidade_dependencia)):
        proxima_fronteira = []
        for caminho_pai in fronteira:
            info_pai = arquivos.get(caminho_pai, {})
            for dep in info_pai.get("depende_de") or []:
                if dep in vistos or dep not in arquivos:
                    # dep fora do Modelo Interno (ex: biblioteca padrao,
                    # arquivo ainda sem entendimento gerado) -- ignora, nao
                    # ha codigo pra ler de qualquer forma
                    continue
                vistos.add(dep)
                resultado.append({
                    "arquivo": dep,
                    "score": 0,
                    "motivo": f"dependencia de {caminho_pai} (via depende_de)",
                })
                proxima_fronteira.append(dep)
        fronteira = proxima_fronteira

    return resultado


def ler_codigo_real(caminhos, caminho_projeto, max_chars_por_arquivo=20000):
    """
    Le o conteudo REAL (arquivo inteiro, nao chunk/trecho) de cada caminho
    relativo em `caminhos`, a partir de `caminho_projeto` (memory/projeto
    .json['caminho_origem'], resolvido pelo ingest). Arquivo que sumiu do
    disco desde o ultimo ingest e simplesmente pulado -- nunca inventa
    conteudo que nao pode ler de verdade.

    Caminho absoluto, travessia para fora da raiz ou symlink externo e
    rejeitado sem leitura. Nesse caso, a entrada correspondente contem
    somente ``{"erro": ...}``, para o chamador explicar a rejeicao sem
    confundi-la com arquivo removido.

    Devolve dict "caminho_relativo -> {'conteudo', 'truncado'}" (ou
    ``{'erro'}`` para caminho inseguro), na mesma ordem dos candidatos.
    """
    codigos = {}
    for caminho_relativo in caminhos:
        caminho_absoluto = _resolver_caminho_seguro(caminho_projeto, caminho_relativo)
        if caminho_absoluto is None:
            codigos[caminho_relativo] = {
                "erro": (
                    f"caminho inseguro rejeitado: '{caminho_relativo}' deve ser relativo "
                    "e permanecer dentro da raiz do projeto"
                )
            }
            continue
        if not os.path.isfile(caminho_absoluto):
            continue
        try:
            with open(caminho_absoluto, "r", encoding="utf-8", errors="replace") as f:
                conteudo = f.read()
        except OSError:
            continue

        truncado = False
        if len(conteudo) > max_chars_por_arquivo:
            conteudo = conteudo[:max_chars_por_arquivo]
            truncado = True

        codigos[caminho_relativo] = {"conteudo": conteudo, "truncado": truncado}
    return codigos


def preparar_dicas(pergunta, entendimento, caminho_projeto, config=None):
    """
    Funcao de conveniencia que encadeia os dois passos acima usando
    config.json['dicas'] (com defaults sensatos se a secao nao existir).
    Devolve (candidatos, codigos) -- prontos para
    engine/compiler.py:montar_prompt_dicas.
    """
    cfg = (config or {}).get("dicas", {})
    max_candidatos = cfg.get("max_componentes_candidatos", 5)
    profundidade = cfg.get("profundidade_dependencia", 1)
    max_chars = cfg.get("max_chars_por_arquivo", 20000)

    candidatos = selecionar_componentes_candidatos(
        pergunta, entendimento,
        max_candidatos=max_candidatos,
        profundidade_dependencia=profundidade,
    )
    codigos = ler_codigo_real(
        [c["arquivo"] for c in candidatos], caminho_projeto,
        max_chars_por_arquivo=max_chars,
    )
    return candidatos, codigos
