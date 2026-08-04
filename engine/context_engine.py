#!/usr/bin/env python3
"""Orcamento dinamico e selecao de evidencias para o Agente da Eyle.

Atualizacao 42: a janela total do backend deixa de ser confundida com o
``context.token_budget`` do retrieval antigo. A cada passo, este modulo reserva
saida e margem de seguranca, mede o prompt fixo real e usa somente o restante
para evidencias estruturadas. O conteudo completo continua no ``AgentState``;
o prompt recebe uma selecao relevante que cabe na janela.
"""
import json
import re


_RE_TERMO = re.compile(r"[A-Za-zÀ-ÿ0-9_./-]+", re.UNICODE)


def estimar_tokens(texto, chars_per_token=3):
    """Estimativa conservadora e deterministica quando nao ha tokenizador."""
    chars_per_token = max(int(chars_per_token or 3), 1)
    tamanho = len(str(texto or ""))
    return (tamanho + chars_per_token - 1) // chars_per_token


def calcular_orcamento_evidencias(config, prompt_sistema, prompt_sem_evidencias):
    """Calcula quanto da janela atual pode ser ocupado por evidencias."""
    config = config or {}
    cfg_llm = config.get("llm", {})
    cfg_contexto = config.get("context_engine", {})
    janela = int(cfg_llm.get("context_window_tokens", 2048) or 2048)
    resposta = int(cfg_llm.get("max_tokens", 0) or 0)
    margem = int(cfg_contexto.get("safety_margin_tokens", 256) or 0)
    chars_por_token = int(cfg_contexto.get("chars_per_token_fallback", 3) or 3)
    tokens_fixos = estimar_tokens(prompt_sistema, chars_por_token) + estimar_tokens(
        prompt_sem_evidencias, chars_por_token,
    )
    disponivel = max(janela - resposta - margem - tokens_fixos, 0)
    return {
        "context_window_tokens": janela,
        "response_reserved_tokens": resposta,
        "safety_margin_tokens": margem,
        "fixed_prompt_tokens": tokens_fixos,
        "evidence_budget_tokens": disponivel,
        "chars_per_token": chars_por_token,
    }


def _termos(texto):
    return {
        termo.lower()
        for termo in _RE_TERMO.findall(str(texto or ""))
        if len(termo) >= 3
    }


def _pontuar_evidencia(evidencia, objetivo, indice):
    if evidencia.get("estado") != "fresh":
        return -10_000
    termos_objetivo = _termos(objetivo)
    alvo = " ".join((
        str(evidencia.get("arquivo") or ""),
        str(evidencia.get("conteudo") or "")[:4000],
        str(evidencia.get("source_tool") or ""),
    ))
    sobreposicao = len(termos_objetivo & _termos(alvo))
    origem = 2 if evidencia.get("source_tool") == "read_range" else 1
    # Indice maior = evidencia mais recente. A relevancia lexical manda;
    # recencia apenas desempata, sem fazer o passo 1 sumir no passo 6.
    return sobreposicao * 100 + origem * 10 + indice


def _cabecalho_evidencia(evidencia):
    return (
        f"EVIDENCE {evidencia.get('id')} | tool={evidencia.get('source_tool')} | "
        f"{evidencia.get('arquivo')}:{evidencia.get('linha_inicio')}-"
        f"{evidencia.get('linha_fim')} | range_hash={evidencia.get('content_hash')} | "
        f"file_hash={evidencia.get('file_hash')} | "
        f"state={evidencia.get('estado')}"
    )


def _cortar_texto_dinamico(texto, max_chars):
    texto = str(texto or "")
    if len(texto) <= max_chars:
        return texto, False
    marcador = "\n[... content omitted only from this prompt; full evidence remains preserved ...]\n"
    if max_chars <= len(marcador) + 40:
        return texto[:max_chars], True
    disponivel = max_chars - len(marcador)
    inicio = disponivel // 2
    fim = disponivel - inicio
    return (
        texto[:inicio]
        + marcador
        + texto[-fim:],
        True,
    )


def selecionar_evidencias(evidencias, objetivo, budget_tokens, chars_per_token=3):
    """Seleciona/renderiza evidencias frescas dentro do orcamento recebido.

    Nenhum conteudo e removido do estado. Quando nem a evidencia mais relevante
    cabe inteira, o recorte usa todo o saldo dinamico (nao o corte historico de
    500 caracteres) e deixa o ID/faixa/hash disponiveis para nova leitura.
    """
    evidencias = list(evidencias or [])
    budget_tokens = max(int(budget_tokens or 0), 0)
    chars_per_token = max(int(chars_per_token or 3), 1)
    ordenadas = sorted(
        enumerate(evidencias),
        key=lambda item: _pontuar_evidencia(item[1], objetivo, item[0]),
        reverse=True,
    )

    selecionadas = []
    usados = 0
    for _, evidencia in ordenadas:
        if evidencia.get("estado") != "fresh" or usados >= budget_tokens:
            continue
        cabecalho = _cabecalho_evidencia(evidencia)
        conteudo = str(evidencia.get("conteudo") or "")
        disponiveis = budget_tokens - usados
        overhead = estimar_tokens("\n\n" + cabecalho + "\n", chars_per_token)
        if overhead >= disponiveis:
            continue
        max_chars = (disponiveis - overhead) * chars_per_token
        conteudo_prompt, truncada = _cortar_texto_dinamico(conteudo, max_chars)
        bloco = cabecalho + "\n" + conteudo_prompt
        custo = estimar_tokens("\n\n" + bloco, chars_per_token)
        if custo > disponiveis:
            continue
        selecionadas.append({
            "id": evidencia.get("id"),
            "bloco": bloco,
            "tokens_estimados": custo,
            "truncada_no_prompt": truncada,
        })
        usados += custo

    return {
        "selecionadas": selecionadas,
        "tokens_usados": usados,
        "budget_tokens": budget_tokens,
        "ids_selecionados": [item["id"] for item in selecionadas],
    }


def serializar_resumo_orcamento(orcamento, selecao):
    """Resumo compacto para trace/testes, sem copiar o codigo novamente."""
    return json.dumps({
        "janela": orcamento.get("context_window_tokens"),
        "fixos": orcamento.get("fixed_prompt_tokens"),
        "resposta": orcamento.get("response_reserved_tokens"),
        "margem": orcamento.get("safety_margin_tokens"),
        "evidencias_disponiveis": orcamento.get("evidence_budget_tokens"),
        "evidencias_usadas": selecao.get("tokens_usados"),
        "evidence_ids": selecao.get("ids_selecionados"),
    }, ensure_ascii=False, separators=(",", ":"))
