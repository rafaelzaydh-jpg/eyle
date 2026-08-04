#!/usr/bin/env python3
"""Verificacao semantica deterministica e conservadora de conclusoes.

Nao tenta "entender tudo" como uma LLM. Em vez disso, bloqueia afirmacoes com
ancoras objetivas (identificadores, caminhos, numeros e literais citados) que
nao aparecem nas evidencias declaradas. Baixa sobreposicao lexical vira aviso,
nao bloqueio, para evitar falsos negativos em parafrases legitimas.
"""
from __future__ import annotations

import re
import unicodedata

_RE_CITATION = re.compile(
    r"(?P<file>[\w./\\-]+\.(?:py|js|ts|tsx|jsx|json|html|css|md|yml|yaml))"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?",
    re.IGNORECASE,
)
_RE_BACKTICK = re.compile(r"`([^`\n]{1,160})`")
_RE_QUOTED = re.compile(r"(?<!\w)[\"']([^\"'\n]{2,120})[\"']")
_RE_PATH = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\b")
_RE_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:%|ms|s|mb|gb|kb)?\b", re.IGNORECASE)
_RE_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_RE_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

_STOP = {
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e", "ou",
    "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "que", "se", "ao",
    "aos", "como", "isso", "esta", "este", "essa", "esse", "foi", "ser", "tem", "ha",
    "the", "and", "or", "of", "to", "in", "for", "with", "without", "this", "that",
    "is", "are", "was", "were", "be", "has", "have", "it", "from", "on", "by",
}


def _normalize(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def _tokens(text):
    return {
        token for token in re.findall(r"[a-z0-9_]{3,}", _normalize(text))
        if token not in _STOP and not token.isdigit()
    }


def _claims(answer):
    result = []
    for raw in _RE_SENTENCE.split(str(answer or "")):
        claim = raw.strip(" \t-*#>\r")
        if len(claim) < 8:
            continue
        if claim.casefold().startswith(("limitação", "limitacao", "limitações", "limitacoes")):
            continue
        result.append(claim)
    return result


def _evidence_for_claim(claim, evidences):
    citations = list(_RE_CITATION.finditer(claim))
    if not citations:
        return list(evidences)
    selected = []
    for citation in citations:
        filename = citation.group("file").replace("\\", "/").casefold()
        start = int(citation.group("start"))
        end = int(citation.group("end") or start)
        for ev in evidences:
            ev_file = str(ev.get("arquivo") or "").replace("\\", "/").casefold()
            ev_start = int(ev.get("linha_inicio") or 0)
            ev_end = int(ev.get("linha_fim") or 0)
            if ev_file == filename and ev_start <= start and end <= ev_end:
                selected.append(ev)
    # Citacao invalida ja e tratada pelo gate estrutural. Aqui, vazio continua
    # vazio para nao "emprestar" evidencia de outro arquivo.
    return selected


def _anchors(claim):
    anchors = []
    for pattern, kind in (
        (_RE_BACKTICK, "code"),
        (_RE_PATH, "path"),
        (_RE_NUMBER, "number"),
    ):
        for match in pattern.finditer(claim):
            value = match.group(1) if match.lastindex else match.group(0)
            value = value.strip()
            if value:
                anchors.append((kind, value))
    # Literais entre aspas contam apenas se parecem valor tecnico, nao prosa.
    for match in _RE_QUOTED.finditer(claim):
        value = match.group(1).strip()
        if any(ch in value for ch in ("_", "/", ".", "=", "-")) or len(value.split()) <= 3:
            anchors.append(("literal", value))
    # Identificadores com sinais fortes: snake_case, CamelCase ou MAIUSCULAS.
    for match in _RE_IDENTIFIER.finditer(claim):
        value = match.group(0)
        if "_" in value or any(ch.isupper() for ch in value[1:]) or value.isupper():
            anchors.append(("identifier", value))
    deduped = []
    seen = set()
    for kind, value in anchors:
        key = (kind, _normalize(value))
        if key not in seen:
            seen.add(key)
            deduped.append((kind, value))
    return deduped


def verify_conclusion(answer, evidences, config=None):
    """Retorna ``ok``, bloqueios objetivos e avisos de baixa cobertura."""
    cfg = config if isinstance(config, dict) else {}
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return {"ok": True, "enabled": False, "claims": [], "errors": [], "warnings": []}

    min_overlap = float(cfg.get("min_claim_token_overlap", 0.12))
    min_tokens = max(3, int(cfg.get("min_claim_tokens", 5)))
    block_anchors = bool(cfg.get("block_unsupported_anchors", True))
    require_citations = bool(cfg.get("require_inline_citations", False))

    evidences = [ev for ev in (evidences or []) if isinstance(ev, dict)]
    reports = []
    errors = []
    warnings = []
    for claim in _claims(answer):
        selected = _evidence_for_claim(claim, evidences)
        evidence_text = "\n".join(str(ev.get("conteudo") or "") for ev in selected)
        normalized_evidence = _normalize(evidence_text)
        claim_tokens = _tokens(_RE_CITATION.sub(" ", claim))
        evidence_tokens = _tokens(evidence_text)
        overlap = (
            len(claim_tokens & evidence_tokens) / max(1, len(claim_tokens))
            if claim_tokens else 1.0
        )
        unsupported = []
        for kind, anchor in _anchors(_RE_CITATION.sub(" ", claim)):
            normalized_anchor = _normalize(anchor)
            if normalized_anchor and normalized_anchor not in normalized_evidence:
                unsupported.append({"kind": kind, "value": anchor})

        claim_errors = []
        claim_warnings = []
        if require_citations and not _RE_CITATION.search(claim):
            claim_errors.append("claim_without_inline_citation")
        if unsupported and block_anchors:
            claim_errors.append("unsupported_objective_anchor")
        if len(claim_tokens) >= min_tokens and overlap < min_overlap:
            claim_warnings.append("low_lexical_overlap")
        if not selected:
            claim_errors.append("no_matching_evidence")

        report = {
            "claim": claim,
            "evidence_ids": [ev.get("id") for ev in selected if ev.get("id")],
            "token_overlap": round(overlap, 3),
            "unsupported_anchors": unsupported,
            "errors": claim_errors,
            "warnings": claim_warnings,
        }
        reports.append(report)
        if claim_errors:
            errors.append(report)
        if claim_warnings:
            warnings.append(report)

    return {
        "ok": not errors,
        "enabled": True,
        "claims": reports,
        "errors": errors,
        "warnings": warnings,
        "summary": (
            "all objective anchors are supported by the declared evidence"
            if not errors else
            f"{len(errors)} claim(s) contain unsupported or unmapped objective anchors"
        ),
    }
