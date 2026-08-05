#!/usr/bin/env python3
"""Claims estruturadas e gates de saude para auditorias de projeto.

A revisao 55.20 mantem claims atomicas e diferencia estado atual de registro
historico. Declaracoes sobre releases antigas podem ser citadas como historia
quando estiverem explicitamente atribuidas a documentacao fresca; elas nunca
viram prova do estado operacional atual.
"""
from __future__ import annotations

import re
import unicodedata

ALLOWED_CLAIM_TYPES = {
    "fact", "risk", "inference", "hypothesis", "recommendation", "decision",
}
_TYPE_ALIASES = {
    "fato": "fact",
    "risco": "risk",
    "inferência": "inference",
    "inferencia": "inference",
    "hipótese": "hypothesis",
    "hipotese": "hypothesis",
    "recomendação": "recommendation",
    "recomendacao": "recommendation",
    "decisão": "decision",
    "decisao": "decision",
}
_GROUNDING_TYPE = {
    "fact": "fact",
    "risk": "inference",
    "inference": "inference",
    "hypothesis": "hypothesis",
    "recommendation": "recommendation",
    "decision": "decision",
}

_HEALTH_PATTERNS = (
    re.compile(r"\bnenhum(?:a)?\s+(?:problema|falha|risco)s?\s+criticos?\b"),
    re.compile(r"\b(?:nao|zero)\s+(?:ha|existem?|foram identificados?)\s+(?:problemas?|falhas?|riscos?)\s+criticos?\b"),
    re.compile(r"\bsem\s+(?:problemas?|falhas?|riscos?)\s+criticos?\b"),
    re.compile(r"\bno\s+critical\s+(?:issues?|problems?|risks?)\b"),
    re.compile(r"\ball\s+(?:core\s+)?(?:features?|functionality|functions?)\s+(?:are|is)\s+(?:operational|working)\b"),
    re.compile(r"\btodas?\s+(?:as\s+)?(?:funcionalidades?|funcoes?)\s+(?:principais\s+)?(?:estao|esta)\s+(?:operacionais?|funcionando)\b"),
    re.compile(r"\bsistema\s+(?:esta\s+)?(?:saudavel|estavel|totalmente operacional|pronto para producao)\b"),
    re.compile(r"\b(?:system|project)\s+(?:is\s+)?(?:healthy|stable|fully operational|production ready)\b"),
)
_TEST_PASS_PATTERNS = (
    re.compile(r"\b(?:todos?\s+os\s+)?testes?\s+(?:estao\s+)?(?:passando|passaram|aprovados?)\b"),
    re.compile(r"\b(?:all\s+)?tests?\s+(?:are\s+)?pass(?:ed|ing)?\b"),
    re.compile(r"\b\d+\s+testes?\s+(?:passando|passaram|aprovados?)\b"),
    re.compile(r"\b\d+\s+tests?\s+pass(?:ed|ing)?\b"),
)

_HISTORICAL_ATTRIBUTION_PATTERNS = (
    re.compile(r"\b(?:revisao|versao|release|changelog|readme|documentacao|historico|historica)\b"),
    re.compile(r"\b(?:registrava|registrou|declarava|declarou|informava|informou|relatava|relatou|documentava|documentou)\b"),
    re.compile(r"\b(?:revision|version|release|changelog|readme|documentation|historical)\b"),
    re.compile(r"\b(?:reported|stated|recorded|documented|claimed)\b"),
)


def _documentation_path(path):
    normalized = str(path or "").strip().replace("\\", "/").lower()
    base = normalized.rsplit("/", 1)[-1]
    return (
        normalized.startswith("docs/")
        or "/docs/" in f"/{normalized}/"
        or base.startswith("readme")
        or base.startswith("changelog")
        or base.endswith((".md", ".rst", ".adoc"))
    )


def _historical_document_claim(claim, normalized_text, evidence):
    if not normalized_text or not all(
        pattern.search(normalized_text) for pattern in (
            _HISTORICAL_ATTRIBUTION_PATTERNS[0],
            _HISTORICAL_ATTRIBUTION_PATTERNS[1],
        )
    ) and not all(
        pattern.search(normalized_text) for pattern in (
            _HISTORICAL_ATTRIBUTION_PATTERNS[2],
            _HISTORICAL_ATTRIBUTION_PATTERNS[3],
        )
    ):
        return False
    registry = {
        str(item.get("id")): item
        for item in evidence or []
        if isinstance(item, dict) and item.get("id")
    }
    ids = claim.get("evidence_ids") or []
    if not ids:
        return False
    matched = [registry.get(str(evidence_id)) for evidence_id in ids]
    return bool(matched and all(
        isinstance(item, dict)
        and item.get("estado") == "fresh"
        and _documentation_path(item.get("arquivo"))
        for item in matched
    ))


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold().strip()


def _claim_type(value):
    normalized = _normalize(value)
    normalized = _TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in ALLOWED_CLAIM_TYPES else None


def normalize_structured_claims(value):
    """Normaliza o envelope do Finalizer e devolve erro de contrato objetivo."""
    if not isinstance(value, list) or not value:
        return None, "final.claims precisa ser uma lista nao vazia"

    claims = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            return None, f"final.claims[{index}] precisa ser um objeto"
        claim_type = _claim_type(item.get("type", item.get("tipo")))
        if claim_type is None:
            return None, f"final.claims[{index}].type invalido"
        text = item.get("text", item.get("texto"))
        if not isinstance(text, str) or len(text.strip()) < 8:
            return None, f"final.claims[{index}].text precisa ser texto util"
        text = text.strip()
        # Uma claim atomica nao pode esconder um paragrafo inteiro. O ponto
        # final unico e aceito; duas sentencas assertivas devem virar 2 claims.
        sentence_parts = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        if len(sentence_parts) > 1:
            return None, f"final.claims[{index}] contem mais de uma afirmacao"

        evidence_ids = item.get("evidence_ids", item.get("ids_evidencia", []))
        if evidence_ids is None:
            evidence_ids = []
        if not isinstance(evidence_ids, list) or not all(
            isinstance(evidence_id, str) and evidence_id.strip()
            for evidence_id in evidence_ids
        ):
            return None, f"final.claims[{index}].evidence_ids precisa ser uma lista de IDs"
        evidence_ids = list(dict.fromkeys(evidence_id.strip() for evidence_id in evidence_ids))
        basis = item.get("basis", item.get("base", ""))
        if basis is None:
            basis = ""
        if not isinstance(basis, str):
            return None, f"final.claims[{index}].basis precisa ser texto"
        basis = basis.strip()

        if claim_type in {"fact", "risk", "inference", "hypothesis"} and not evidence_ids:
            return None, f"final.claims[{index}] do tipo {claim_type} exige evidence_ids"
        if claim_type in {"risk", "inference", "hypothesis", "recommendation"} and not basis:
            return None, f"final.claims[{index}] do tipo {claim_type} exige basis"

        claims.append({
            "type": claim_type,
            "text": text,
            "evidence_ids": evidence_ids,
            "basis": basis,
        })
    return claims, None


def render_claims(claims):
    """Monta o texto final deterministicamente sem criar fatos novos."""
    return "\n".join(claim["text"].strip() for claim in claims if claim.get("text")).strip()


def claims_to_annotations(claims):
    """Adapta claims ao verificador tipado legado sem casamento por adivinhacao."""
    return [
        {
            "claim_index": index,
            "claim": claim.get("text"),
            "type": _GROUNDING_TYPE.get(claim.get("type"), "fact"),
            "evidence_ids": list(claim.get("evidence_ids") or []),
            "basis": claim.get("basis") or "",
        }
        for index, claim in enumerate(claims or [], start=1)
    ]


def claim_evidence_ids(claims):
    return list(dict.fromkeys(
        evidence_id
        for claim in claims or []
        for evidence_id in claim.get("evidence_ids") or []
    ))


def coverage_score(coverage):
    criteria = (coverage or {}).get("criteria") or {}
    structural = {
        key: bool(value)
        for key, value in criteria.items()
        if key not in {"coverage_reported", "grounded_answer"}
    }
    if not structural:
        return 0.0
    return sum(1 for value in structural.values() if value) / len(structural)


def successful_test_run(actions):
    """Prova operacional: run_tests executou e retornou ok=True nesta tarefa."""
    return any(
        isinstance(action, dict)
        and action.get("tool") == "run_tests"
        and action.get("executed") is True
        and action.get("ok") is True
        for action in actions or []
    )


def validate_health_claims(
    claims, coverage, actions, *, evidence=None, required_score=1.0,
):
    """Bloqueia saude atual sem prova; permite historia documental explicita."""
    score = coverage_score(coverage)
    tests_ok = successful_test_run(actions)
    for index, claim in enumerate(claims or [], start=1):
        normalized = _normalize(claim.get("text"))
        historical = _historical_document_claim(claim, normalized, evidence)
        if any(pattern.search(normalized) for pattern in _TEST_PASS_PATTERNS):
            if historical:
                continue
            if not tests_ok:
                return {
                    "ok": False,
                    "failure_code": "TEST_STATUS_NOT_VERIFIED",
                    "claim_index": index,
                    "claim": claim.get("text"),
                    "coverage_score": score,
                    "tests_executed": False,
                }
        if any(pattern.search(normalized) for pattern in _HEALTH_PATTERNS):
            if historical:
                continue
            if score < float(required_score):
                return {
                    "ok": False,
                    "failure_code": "UNSUPPORTED_HEALTH_CLAIM",
                    "claim_index": index,
                    "claim": claim.get("text"),
                    "coverage_score": score,
                    "required_score": float(required_score),
                    "tests_executed": tests_ok,
                }
            # Cobertura minima prova que os componentes-alvo foram vistos, nao
            # que todo comportamento do sistema esta saudavel. Exigimos tambem
            # uma execucao atual dos testes para uma declaracao global.
            if not tests_ok:
                return {
                    "ok": False,
                    "failure_code": "UNSUPPORTED_HEALTH_CLAIM",
                    "claim_index": index,
                    "claim": claim.get("text"),
                    "coverage_score": score,
                    "required_score": float(required_score),
                    "tests_executed": False,
                }
    return {
        "ok": True,
        "failure_code": None,
        "coverage_score": score,
        "required_score": float(required_score),
        "tests_executed": tests_ok,
    }
