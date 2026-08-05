#!/usr/bin/env python3
"""Grounding semantico tipado para conclusoes do Agente.

O projeto e tratado como estado observado, nao como verdade universal. O
verificador separa afirmacoes em tipos epistemicos explícitos, incluindo ausência com escopo:

- fact: descricao objetiva do estado observado; exige evidencia;
- inference: conclusao derivada das evidencias; exige uma base observada;
- hypothesis: possibilidade ainda nao confirmada; pode introduzir novidade,
  mas deve permanecer explicitamente incerta;
- decision: escolha futura do agente; nao precisa preexistir no projeto;
- recommendation: proposta de melhoria; pode introduzir novos valores,
  arquivos ou abordagens.

A verificacao continua deterministica e conservadora. Ela nao tenta provar
entailment completo em linguagem natural; protege fatos objetivos sem impedir
o agente de raciocinar, propor e escolher.
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

CLAIM_TYPES = ("fact", "absence", "inference", "hypothesis", "decision", "recommendation")

_TYPE_ALIASES = {
    "fact": "fact", "fato": "fact", "observed_fact": "fact", "fato_observado": "fact",
    "absence": "absence", "ausência": "absence", "ausencia": "absence",
    "inference": "inference", "inferência": "inference", "inferencia": "inference",
    "hypothesis": "hypothesis", "hipótese": "hypothesis", "hipotese": "hypothesis",
    "decision": "decision", "decisão": "decision", "decisao": "decision",
    "recommendation": "recommendation", "recomendação": "recommendation",
    "recomendacao": "recommendation", "proposal": "recommendation", "proposta": "recommendation",
}

_RE_HYPOTHESIS = re.compile(
    r"\b(?:pode(?:m|ria|riam)?|talvez|possivelmente|provavelmente|aparentemente|"
    r"suspeit[oa](?:mos)?|hip[oó]tese|e possivel|ha chance|may|might|could|possibly|"
    r"probably|likely|appears?|seems?|hypothesis|suspect)\b",
    re.IGNORECASE,
)
_RE_DECISION = re.compile(
    r"\b(?:vou|vamos|decidi(?:mos)?|escolhi(?:mos)?|adotarei|adotaremos|manterei|"
    r"manteremos|implementarei|implementaremos|a decisao e|i will|we will|i chose|"
    r"we chose|the decision is)\b",
    re.IGNORECASE,
)
_RE_RECOMMENDATION = re.compile(
    r"\b(?:recomendo|recomendamos|sugiro|sugerimos|deveria(?:mos)?|ideal e|"
    r"melhor solucao|aconselho|convem|recommend|suggest|should|best approach|"
    r"best solution)\b",
    re.IGNORECASE,
)
_RE_INFERENCE = re.compile(
    r"\b(?:isso indica|isso sugere|portanto|logo|consequentemente|infere-se|"
    r"a partir disso|com base nisso|therefore|thus|this suggests|this indicates|"
    r"implies|based on this)\b",
    re.IGNORECASE,
)


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


def _normalize_claim_type(value):
    return _TYPE_ALIASES.get(_normalize(value).strip())


def classify_claim(claim):
    """Classifica uma afirmacao sem depender da LLM.

    A classificacao explicita fornecida no envelope final tem prioridade. Esta
    heuristica existe para compatibilidade com modelos antigos e para frases
    naturalmente modais. O default e ``fact`` porque afirmacoes assertivas
    devem continuar recebendo o gate mais rigoroso.
    """
    normalized = _normalize(claim)
    if _RE_HYPOTHESIS.search(normalized):
        return "hypothesis"
    if _RE_DECISION.search(normalized):
        return "decision"
    if _RE_RECOMMENDATION.search(normalized):
        return "recommendation"
    if _RE_INFERENCE.search(normalized):
        return "inference"
    return "fact"


def _prepare_annotations(annotations):
    prepared = []
    for item in annotations or []:
        if not isinstance(item, dict):
            continue
        claim_type = _normalize_claim_type(item.get("type", item.get("tipo")))
        if claim_type is None:
            continue
        text = item.get("claim", item.get("text", item.get("statement", item.get("afirmacao"))))
        index = item.get("claim_index", item.get("indice"))
        try:
            index = int(index) if index is not None else None
        except (TypeError, ValueError):
            index = None
        evidence_ids = item.get("evidence_ids", item.get("basis_evidence_ids", []))
        if not isinstance(evidence_ids, list):
            evidence_ids = []
        prepared.append({
            "claim": str(text or "").strip(),
            "claim_normalized": _normalize(text).strip(),
            "claim_index": index,
            "type": claim_type,
            "evidence_ids": [str(value) for value in evidence_ids if isinstance(value, str) and value],
            "basis": str(item.get("basis", item.get("base", "")) or "").strip(),
            "scope": str(item.get("scope", item.get("escopo", "")) or "").strip(),
        })
    return prepared


def _annotation_for_claim(claim, index, annotations):
    normalized = _normalize(claim).strip()
    for item in annotations:
        if item.get("claim_normalized") and item["claim_normalized"] == normalized:
            return item
    for item in annotations:
        if item.get("claim_index") == index + 1:
            return item
    return None


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
    # Citacao invalida ja e tratada como ausencia de evidencia para a claim.
    return selected


def _select_annotation_evidence(selected, evidences, annotation):
    requested = list((annotation or {}).get("evidence_ids") or [])
    if not requested:
        return selected, []
    by_id = {str(ev.get("id")): ev for ev in evidences if ev.get("id")}
    missing = [evidence_id for evidence_id in requested if evidence_id not in by_id]
    requested_items = [by_id[evidence_id] for evidence_id in requested if evidence_id in by_id]
    if selected:
        selected_ids = {str(ev.get("id")) for ev in selected if ev.get("id")}
        requested_items = [ev for ev in requested_items if str(ev.get("id")) in selected_ids]
    return requested_items, missing


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
    for match in _RE_QUOTED.finditer(claim):
        value = match.group(1).strip()
        if any(ch in value for ch in ("_", "/", ".", "=", "-")) or len(value.split()) <= 3:
            anchors.append(("literal", value))
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


def verify_conclusion(answer, evidences, config=None, claim_annotations=None):
    """Valida fatos sem bloquear inferencias, hipoteses e escolhas legitimas."""
    cfg = config if isinstance(config, dict) else {}
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return {
            "ok": True, "enabled": False, "typed": True,
            "claims": [], "errors": [], "warnings": [],
        }

    min_overlap = float(cfg.get("min_claim_token_overlap", 0.12))
    min_tokens = max(3, int(cfg.get("min_claim_tokens", 5)))
    block_anchors = bool(cfg.get("block_unsupported_anchors", True))
    require_citations = bool(cfg.get("require_inline_citations", False))
    require_inference_basis = bool(cfg.get("require_inference_evidence", True))
    warn_hypothesis_without_basis = bool(cfg.get("warn_hypothesis_without_evidence", True))

    evidences = [ev for ev in (evidences or []) if isinstance(ev, dict)]
    annotations = _prepare_annotations(claim_annotations)
    reports = []
    errors = []
    warnings = []

    # Em respostas estruturadas, as claims anotadas sao a unidade canonica.
    # O sistema ja renderizou o texto a partir delas; quebrar o texto outra
    # vez por pontuacao pode perder type/scope/evidence_ids e fazer o
    # verificador rejeitar a propria estrutura que acabou de validar.
    parsed_claims = _claims(answer)
    mapped_annotations = [
        _annotation_for_claim(claim, index, annotations)
        for index, claim in enumerate(parsed_claims)
    ]
    fully_annotated = bool(parsed_claims) and all(mapped_annotations)
    if fully_annotated:
        claim_units = [
            (annotation.get("claim") or claim, annotation)
            for claim, annotation in zip(parsed_claims, mapped_annotations)
        ]
    else:
        # Protocolos antigos podem anotar apenas algumas frases. Nesse caso,
        # todas as frases do texto continuam sendo verificadas; a anotacao
        # parcial nao pode esconder um fato objetivo sem suporte.
        claim_units = [
            (claim, mapped_annotations[index] if index < len(mapped_annotations) else None)
            for index, claim in enumerate(parsed_claims)
        ]

    for index, (claim, explicit_annotation) in enumerate(claim_units):
        annotation = explicit_annotation
        if annotation:
            claim_type = annotation["type"]
            classification_source = "explicit"
        else:
            claim_type = classify_claim(claim)
            classification_source = "heuristic" if claim_type != "fact" else "default"

        selected = _evidence_for_claim(claim, evidences)
        selected, missing_annotation_ids = _select_annotation_evidence(
            selected, evidences, annotation,
        )
        evidence_text = "\n".join(str(ev.get("conteudo") or "") for ev in selected)
        normalized_evidence = _normalize(evidence_text)
        claim_without_citation = _RE_CITATION.sub(" ", claim)
        claim_tokens = _tokens(claim_without_citation)
        evidence_tokens = _tokens(evidence_text)
        overlap = (
            len(claim_tokens & evidence_tokens) / max(1, len(claim_tokens))
            if claim_tokens else 1.0
        )
        unsupported = []
        for kind, anchor in _anchors(claim_without_citation):
            normalized_anchor = _normalize(anchor)
            if normalized_anchor and normalized_anchor not in normalized_evidence:
                unsupported.append({"kind": kind, "value": anchor})

        claim_errors = []
        claim_warnings = []
        has_citation = bool(_RE_CITATION.search(claim))

        if missing_annotation_ids:
            claim_errors.append("annotation_evidence_not_available")

        if claim_type in ("fact", "absence"):
            if require_citations and not has_citation:
                claim_errors.append("fact_without_inline_citation")
            if unsupported and block_anchors:
                claim_errors.append("unsupported_objective_anchor")
            if not selected:
                claim_errors.append("no_matching_evidence")
            if claim_type == "absence" and not (annotation or {}).get("scope"):
                claim_errors.append("absence_without_explicit_scope")
            if len(claim_tokens) >= min_tokens and overlap < min_overlap:
                claim_warnings.append("low_lexical_overlap")

        elif claim_type == "inference":
            if require_inference_basis and not selected:
                claim_errors.append("inference_without_evidence")
            if has_citation and not selected:
                claim_errors.append("citation_without_matching_evidence")
            if unsupported:
                claim_warnings.append("derived_or_unverified_anchor")
            if len(claim_tokens) >= min_tokens and overlap < min_overlap:
                claim_warnings.append("low_inference_overlap")
            if not (annotation or {}).get("basis") and not has_citation:
                claim_warnings.append("inference_without_explicit_basis")

        elif claim_type == "hypothesis":
            if has_citation and not selected:
                claim_errors.append("citation_without_matching_evidence")
            if unsupported:
                claim_warnings.append("hypothesis_contains_unverified_anchor")
            if warn_hypothesis_without_basis and not selected:
                claim_warnings.append("hypothesis_without_observed_basis")

        elif claim_type in ("decision", "recommendation"):
            # Escolhas futuras e propostas podem criar valores, caminhos e
            # identificadores novos. Apenas evidencias/citacoes explicitamente
            # declaradas precisam existir.
            if has_citation and not selected:
                claim_errors.append("citation_without_matching_evidence")
            if unsupported:
                claim_warnings.append("novel_proposed_anchor")

        report = {
            "claim": claim,
            "claim_index": index + 1,
            "claim_type": claim_type,
            "classification_source": classification_source,
            "annotation_basis": (annotation or {}).get("basis") or None,
            "evidence_ids": [ev.get("id") for ev in selected if ev.get("id")],
            "token_overlap": round(overlap, 3),
            "unsupported_anchors": unsupported,
            "errors": list(dict.fromkeys(claim_errors)),
            "warnings": list(dict.fromkeys(claim_warnings)),
        }
        reports.append(report)
        if report["errors"]:
            errors.append(report)
        if report["warnings"]:
            warnings.append(report)

    type_counts = {claim_type: 0 for claim_type in CLAIM_TYPES}
    for report in reports:
        type_counts[report["claim_type"]] += 1

    return {
        "ok": not errors,
        "enabled": True,
        "typed": True,
        "claims": reports,
        "errors": errors,
        "warnings": warnings,
        "type_counts": type_counts,
        "summary": (
            "typed grounding accepted observed facts and preserved legitimate reasoning"
            if not errors else
            f"{len(errors)} claim(s) violate the grounding policy for their declared type"
        ),
    }


def format_grounding_feedback(result, max_claims=4, max_chars=2200):
    """Gera feedback acionavel sem transformar erro interno em pendencia."""
    result = result if isinstance(result, dict) else {}
    limit = max(1, int(max_claims))
    char_limit = max(300, int(max_chars))
    errors = [item for item in (result.get("errors") or []) if isinstance(item, dict)]
    lines = [
        "The previous final answer failed typed semantic grounding.",
        "Observed facts must be supported by fresh evidence. Inferences must have an observed basis. "
        "Hypotheses must remain uncertain. Decisions and recommendations may introduce new choices and do not need to preexist in the project.",
    ]
    for report in errors[:limit]:
        claim = " ".join(str(report.get("claim") or "").split())
        if len(claim) > 420:
            claim = claim[:417] + "..."
        reasons = ", ".join(str(item) for item in (report.get("errors") or [])) or "unsupported_claim"
        anchors = ", ".join(
            f"{item.get('kind')}={item.get('value')}"
            for item in (report.get("unsupported_anchors") or [])
            if isinstance(item, dict) and item.get("value") is not None
        ) or "none"
        evidence_ids = ", ".join(
            str(item) for item in (report.get("evidence_ids") or []) if item
        ) or "none"
        lines.append(
            f'- Rejected claim (type={report.get("claim_type", "fact")}): "{claim}" | '
            f"reasons: {reasons} | unsupported anchors: {anchors} | mapped evidence: {evidence_ids}"
        )
    if len(errors) > limit:
        lines.append(f"- {len(errors) - limit} additional rejected claim(s) omitted from this compact feedback.")
    lines.append(
        "Rewrite or remove only the rejected claims. Preserve valid inferences, hypotheses, decisions, and recommendations. "
        "Do not relabel a factual assertion merely to bypass validation. "
        "Do not ask the user to fix an internal grounding failure."
    )
    feedback = "\n".join(lines)
    if len(feedback) > char_limit:
        feedback = feedback[:char_limit - 3].rstrip() + "..."
    return feedback


def build_safe_grounded_answer(answer, result, evidences, max_claims=8):
    """Remove apenas claims rejeitadas e preserva raciocinio/decisoes validos."""
    del answer
    result = result if isinstance(result, dict) else {}
    reports = [item for item in (result.get("claims") or []) if isinstance(item, dict)]
    supported = []
    rejected_types = []
    for report in reports:
        claim = str(report.get("claim") or "").strip()
        if claim and not report.get("errors"):
            supported.append(claim)
        elif claim:
            rejected_types.append(str(report.get("claim_type") or "fact"))
        if len(supported) >= max(1, int(max_claims)):
            break

    if supported:
        # Retorna somente as claims que ja passaram pelo verificador. Incluir
        # uma explicacao tecnica aqui criaria uma nova claim nao anotada e
        # poderia fazer o proprio reparo falhar no segundo passe de grounding.
        return "\n".join(supported)

    # Sem claim suportada, nao fabrica um recibo tecnico. A camada de
    # recuperacao deve gerar uma nova analise util a partir das evidencias; se
    # isso tambem falhar, o status correto e ``failed``.
    return ""
