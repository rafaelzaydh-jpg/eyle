#!/usr/bin/env python3
"""Gate deterministico de utilidade para respostas finais da Eyle."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ_][A-Za-zÀ-ÿ0-9_\-]{1,}")
_CITATION_RE = re.compile(
    r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|ya?ml):\d+(?:-\d+)?",
    re.IGNORECASE,
)
_GENERIC_OBJECTIVE = {
    "analise", "análise", "analisar", "projeto", "app", "aplicacao", "aplicação",
    "codigo", "código", "entender", "entendimento", "faca", "faça", "meu", "minha",
    "verifique", "veja", "sistema", "eyle", "do", "da", "de", "o", "a", "um", "uma",
}
_STOPWORDS = _GENERIC_OBJECTIVE | {
    "para", "com", "sem", "que", "isso", "esta", "está", "ele", "ela", "nos", "nas",
    "os", "as", "por", "como", "mais", "menos", "sobre", "seu", "sua", "the", "and",
    "this", "that", "with", "from", "into", "project", "code", "analysis", "analyze",
}
_RECEIPT_PREFIXES = (
    "evidências verificadas", "evidencias verificadas", "evidências coletadas",
    "evidencias coletadas", "arquivo lido", "arquivos lidos", "linhas",
    "leitura completa", "ferramentas utilizadas", "status", "fallback utilizado",
    "validação", "validacao", "limitações", "limitacoes", "evidence ids",
)
_ACTION_MARKERS = re.compile(
    r"\b(é|são|usa|utiliza|cria|define|inicia|executa|carrega|lê|le|recebe|retorna|"
    r"permite|depende|concentra|expõe|expoe|configura|importa|chama|contém|contem|"
    r"soma|calcula|processa|valida|normaliza|analisa|gera|grava|salva|"
    r"recomendo|deve|pode|indica|sugere|parece|provavelmente|risco|problema|"
    r"encontra|encontrado|encontrada|detecta|detectado|detectada|is|are|uses|creates|defines|starts|loads|returns|allows|imports|calls|contains|"
    r"should|recommend|suggests|indicates|may|might)\b",
    re.IGNORECASE,
)


def _tokens(text):
    return [match.group(0).lower() for match in _WORD_RE.finditer(str(text or ""))]


def _meaningful_lines(answer):
    lines = []
    for raw in str(answer or "").splitlines():
        line = raw.strip(" \t-*#>•")
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(_RECEIPT_PREFIXES):
            continue
        # Uma linha formada apenas por uma citação/faixa não é conclusão.
        stripped = _CITATION_RE.sub("", line)
        stripped = re.sub(r"[\s:;,.()\[\]-]+", "", stripped)
        if not stripped:
            continue
        lines.append(line)
    return lines


def _evidence_tokens(evidence):
    tokens = set()
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for key in ("arquivo", "conteudo_raw", "conteudo"):
            value = item.get(key)
            for token in _tokens(value):
                if token not in _STOPWORDS and len(token) >= 3:
                    tokens.add(token)
    return tokens


def validate_response_utility(answer, objective, *, task_type="project_read", evidence=None):
    """Retorna um gate estruturado; nunca altera a resposta.

    ``success`` exige conteúdo útil. Para leitura de projeto, recibos técnicos,
    listas de arquivos e faixas isoladas não contam como conclusão.
    """
    text = str(answer or "").strip()
    errors = []
    warnings = []
    meaningful = _meaningful_lines(text)
    meaningful_text = " ".join(meaningful).strip()
    tokens = [token for token in _tokens(meaningful_text) if token not in _STOPWORDS]

    if not text:
        errors.append("empty_answer")
    if task_type in ("project_read", "project_write"):
        if not meaningful:
            errors.append("technical_receipt_only")
        if len(meaningful_text) < 24 or len(tokens) < 2:
            errors.append("insufficient_conclusion")
        if meaningful and not _ACTION_MARKERS.search(meaningful_text):
            errors.append("no_observation_inference_or_recommendation")

        objective_tokens = {
            token for token in _tokens(objective)
            if token not in _STOPWORDS and len(token) >= 3
        }
        answer_tokens = set(tokens)
        evidence_tokens = _evidence_tokens(evidence)
        objective_overlap = answer_tokens & objective_tokens
        evidence_overlap = answer_tokens & evidence_tokens
        if objective_tokens and not objective_overlap and not evidence_overlap:
            errors.append("unrelated_to_request")
        elif not objective_tokens and evidence_tokens and not evidence_overlap:
            warnings.append("weak_project_specificity")

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return {
        "ok": not errors,
        "code": "useful_response" if not errors else errors[0],
        "errors": errors,
        "warnings": warnings,
        "meaningful_lines": len(meaningful),
        "meaningful_chars": len(meaningful_text),
        "content_tokens": len(tokens),
        "summary": (
            "response contains a real, request-related conclusion"
            if not errors else "response is empty, receipt-only, too thin, or unrelated"
        ),
    }
