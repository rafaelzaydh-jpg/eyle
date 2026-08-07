"""Deterministic response-quality checks for project/code conclusions.

The LLM still owns the prose. This module only enforces a small contract:
project facts must cite fresh read evidence, finding limits are respected and
an internal claim-to-evidence ledger is retained for diagnostics.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

CLAIM_KINDS = {"fact", "bug", "risk", "recommendation"}
EVIDENCE_REQUIRED_KINDS = {"fact", "bug", "risk"}

_LIMIT_PATTERNS = (
    re.compile(r"\b(?:ate|até|no\s+maximo|no\s+máximo|maximo\s+de|máximo\s+de)\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(?:up\s+to|at\s+most|no\s+more\s+than)\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(?:hasta|como\s+maximo|como\s+máximo)\s+(\d{1,2})\b", re.I),
)
_LIMIT_KIND_PATTERNS = (
    (None, re.compile(r"\b(?:ponto|pontos|item|itens|problema|problemas|issue|issues|finding|findings)\b", re.I)),
    ("bug", re.compile(r"\b(?:bug|bugs|erro|erros|error|errors|falha|falhas|failure|failures)\b", re.I)),
    ("risk", re.compile(r"\b(?:risco|riscos|risk|risks)\b", re.I)),
    ("recommendation", re.compile(
        r"\b(?:recomendacao|recomendacoes|recomendacion|recomendación|recomendaciones|recommendation|recommendations|sugestao|sugestoes|suggestion|suggestions)\b",
        re.I,
    )),
    ("fact", re.compile(r"\b(?:fato|fatos|fact|facts)\b", re.I)),
)

_PROJECT_ANCHORS = (
    "projeto", "project", "codebase", "repositorio", "repositório", "repository",
    "workspace", "arquivo", "file", "runtime", "modulo", "módulo", "module",
    "estrutura", "architecture", "arquitetura", "implementacao", "implementação",
    "implementation", "dependencia", "dependência", "dependency",
    "pasta", "pastas", "diretorio", "diretório", "diretorios", "diretórios",
    "folder", "folders", "directory", "directories", "template", "templates",
)
_ANALYSIS_ACTIONS = (
    "analise", "análise", "analize", "analisar", "analyze", "review", "revisao",
    "revisão", "inspecione", "inspect", "verifique", "verify", "check", "audite",
    "audit", "investigue", "investigate", "encontre", "find",
)
_CODE_ELEMENTS = (
    "codigo", "código", "code", "funcao", "função", "function", "classe", "class",
    "teste", "test", "bug", "erro", "error", "falha", "failure", "rota", "route",
    "metodo", "método", "method", "api",
)
_EVALUATION_TERMS = (
    "correto", "correta", "correct", "funciona", "works", "quebra", "breaks",
    "seguro", "segura", "safe", "problema", "problem",
)

# Conservative imperative detector. Its purpose is not to understand every
# editing request; it prevents direct file-change commands from being accepted
# as prose-only conclusions before the runtime has produced a dry-run proposal.
_WRITE_IMPERATIVE = re.compile(
    r"(?:^|[,.!;:]\s+|\b(?:e|then|and|y)\s+)"
    r"(?:por\s+favor\s+)?"
    r"(?:extraia|mova|crie|adicione|altere|mude|corrija|remova|exclua|apague|"
    r"refatore|implemente|substitua|renomeie|separe|junte|una|mescle|combine|"
    r"consolide|salve|escreva|gere|coloque|atualize|migre|converta|"
    r"traga|leve|incorpore|embuta|inclua|centralize|simplifique|"
    r"extract|move|create|add|change|fix|remove|delete|refactor|implement|"
    r"replace|rename|split|join|merge|combine|consolidate|save|write|generate|update|migrate|convert|"
    r"bring|embed|include|centralize|simplify|"
    r"extrae|mueve|crea|anade|añade|agrega|cambia|corrige|elimina|"
    r"refactoriza|implementa|reemplaza|renombra|separa|guarda|escribe|"
    r"genera|actualiza|migra|convierte)\b",
    re.I,
)
_WRITE_NOUN_COMMAND = re.compile(
    r"\b(?:faca|faça|realize|execute|make|do|haz|haga)\s+"
    r"(?:uma?\s+|a\s+|the\s+|la\s+|el\s+)?"
    r"(?:alteracao|alteração|mudanca|mudança|correcao|correção|refatoracao|"
    r"refatoração|implementacao|implementação|extracao|extração|migracao|"
    r"migração|edicao|edição|change|edit|fix|refactor|implementation|"
    r"extraction|migration|cambio|edicion|edición|correccion|corrección)\b",
    re.I,
)
_ADVISORY_OPENING = re.compile(
    r"^\s*(?:como|how|explique|explain|me\s+diga|tell\s+me|"
    r"qual|quais|what|por\s+que|porque|why)\b",
    re.I,
)


_CORRECTION_MARKERS = re.compile(
    r"\b(?:na\s+verdade|corre[cç][aã]o|corrigindo|melhor\s+dizendo|retiro\s+o\s+que\s+disse|"
    r"actually|correction|to\s+correct\s+that|rather|en\s+realidad|correcci[oó]n)\b",
    re.I,
)
_LIST_LINE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", re.M)
_NEGATION = re.compile(r"\b(?:nao|não|not|never|sem|no|nunca)\b", re.I)
_SOURCE_PATH = re.compile(
    r"(?:^|[\s`'\"])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|pyi|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|hpp|kt|swift|vue|svelte|json|toml|ya?ml)\b",
    re.I,
)


def _fold(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _normalized_words(text: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", _fold(text)))


def requested_finding_constraints(request: Any) -> Dict[str, Any]:
    """Extract overall and per-kind caps from explicit bounded requests."""
    text = str(request or "")
    matches: List[Tuple[int, int, Optional[str]]] = []
    for pattern in _LIMIT_PATTERNS:
        for match in pattern.finditer(text):
            value = int(match.group(1))
            if not 0 <= value <= 99:
                continue
            tail = _fold(text[match.end(): match.end() + 64])
            kind = None
            nearest: Optional[Tuple[int, Optional[str]]] = None
            for candidate_kind, category_pattern in _LIMIT_KIND_PATTERNS:
                category_match = category_pattern.search(tail)
                if category_match is None:
                    continue
                candidate = (category_match.start(), candidate_kind)
                if nearest is None or candidate < nearest:
                    nearest = candidate
            if nearest is not None:
                kind = nearest[1]
            matches.append((match.start(), value, kind))

    if not matches:
        return {"overall": None, "by_kind": {}}
    matches.sort(key=lambda item: item[0])
    by_kind: Dict[str, int] = {}
    generic: List[int] = []
    for _position, value, kind in matches:
        if kind:
            by_kind[kind] = min(value, by_kind.get(kind, value))
        else:
            generic.append(value)

    if generic:
        overall = min(generic)
    elif len(matches) == 1:
        overall = matches[0][1]
    else:
        overall = sum(value for _position, value, _kind in matches)
    return {"overall": overall, "by_kind": by_kind}


def requested_finding_limit(request: Any) -> Optional[int]:
    """Return the deterministic overall cap for compatibility and prompts."""
    return requested_finding_constraints(request)["overall"]


def _phrase_in_words(phrase: str, words: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {words} "


_ALIGNMENT_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
    "em", "essa", "esse", "esta", "este", "o", "os", "para", "por", "que",
    "the", "a", "an", "and", "as", "at", "by", "for", "from", "in", "is",
    "it", "of", "on", "that", "this", "to", "with",
    "el", "la", "las", "los", "con", "de", "del", "en", "es", "para", "por",
    "que", "un", "una", "y",
}
_ANSWER_LINE_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")
_ANSWER_SENTENCE = re.compile(r"(?<=[.!?])(?:\s+|$)")
_CRITICAL_TERM = re.compile(
    r"`[^`]+`|(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|"
    r"[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|hpp|kt|swift|vue|svelte|json|toml|ya?ml)|"
    r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b|"
    r"\b[A-Za-z][A-Za-z0-9]*\.[A-Za-z_][A-Za-z0-9_]*\b|"
    r"\b\d+(?:[.,]\d+)?\b",
    re.I,
)
_CODE_NAMED_TERM = re.compile(
    r"\b(?:funcao|função|function|classe|class|modulo|módulo|module|"
    r"rota|route|endpoint|variavel|variável|variable|metodo|método|method)"
    r"\s+[`'\"]?([A-Za-z_][A-Za-z0-9_.-]*)",
    re.I,
)


def _content_words(text: Any) -> List[str]:
    return [
        token for token in _normalized_words(text).split()
        if token not in _ALIGNMENT_STOPWORDS
    ]


def _critical_terms(text: Any) -> set[str]:
    raw = str(text or "")
    terms = {
        _normalized_words(item) for item in _CRITICAL_TERM.findall(raw)
        if _normalized_words(item)
    }
    terms.update(
        _normalized_words(item) for item in _CODE_NAMED_TERM.findall(raw)
        if _normalized_words(item)
    )
    return terms


def _answer_segments(answer: str) -> List[str]:
    """Return 1-based claimable answer sentences in visible order.

    Markdown headings are presentation, not factual sentences. List markers are
    removed, while the visible sentence text itself is preserved.
    """
    segments: List[str] = []
    for raw_line in str(answer or "").splitlines():
        stripped = raw_line.strip()
        if not stripped or re.match(r"^#{1,6}\s+", stripped):
            continue
        line = _ANSWER_LINE_PREFIX.sub("", raw_line).strip()
        if not line:
            continue
        pieces = _ANSWER_SENTENCE.split(line)
        for piece in pieces:
            candidate = piece.strip()
            if candidate:
                segments.append(candidate)
    return segments


def _align_claim_to_answer(text: str, answer: str) -> Optional[Dict[str, Any]]:
    """Align a small paraphrase to one exact answer sentence.

    This is intentionally conservative. It repairs harmless wording drift while
    preserving polarity, numeric values and code/file identifiers.
    """
    claim_words = _content_words(text)
    if not claim_words:
        return None
    claim_set = set(claim_words)
    claim_critical = _critical_terms(text)
    _claim_skeleton_text, claim_negative = _claim_skeleton(text)
    best: Optional[Dict[str, Any]] = None

    for candidate in _answer_segments(answer):
        normalized_candidate = _normalized_words(candidate)
        if not normalized_candidate:
            continue
        _candidate_skeleton, candidate_negative = _claim_skeleton(candidate)
        if claim_negative != candidate_negative:
            continue
        candidate_critical = _critical_terms(candidate)
        if not claim_critical.issubset(candidate_critical):
            continue
        candidate_words = _content_words(candidate)
        if not candidate_words:
            continue
        candidate_set = set(candidate_words)
        overlap = len(claim_set & candidate_set)
        coverage = overlap / max(1, len(claim_set))
        precision = overlap / max(1, len(candidate_set))
        f1 = (2 * coverage * precision / (coverage + precision)) if coverage + precision else 0.0
        sequence = difflib.SequenceMatcher(
            None, _normalized_words(text), normalized_candidate, autojunk=False,
        ).ratio()

        if len(claim_set) <= 2:
            accepted = coverage == 1.0 and precision >= 0.50 and sequence >= 0.62
        else:
            accepted = coverage == 1.0 and precision >= 0.72 and sequence >= 0.65
        if not accepted:
            continue
        score = (coverage * 0.55) + (f1 * 0.25) + (sequence * 0.20)
        if best is None or score > best["score"]:
            best = {
                "text": candidate,
                "score": round(score, 4),
                "method": "deterministic_answer_segment",
            }
    return best


def request_requires_write(request: Any, project_available: bool = True) -> bool:
    """Return True for direct file/code mutation commands.

    The detector is intentionally conservative: questions about how to edit are
    not treated as commands, while imperative requests such as "extraia o HTML"
    are. The runtime uses this only as a fail-closed guard before accepting a
    prose final response; actual writes still require fresh reads and dry-runs.
    """
    if not project_available:
        return False
    raw = str(request or "").strip()
    if not raw or _ADVISORY_OPENING.search(raw):
        return False
    return bool(_WRITE_IMPERATIVE.search(raw) or _WRITE_NOUN_COMMAND.search(_fold(raw)))


def request_needs_project_evidence(request: Any, project_available: bool) -> bool:
    if not project_available:
        return False
    raw = str(request or "")
    if _SOURCE_PATH.search(raw):
        return True
    words = _normalized_words(raw)
    has_anchor = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in _PROJECT_ANCHORS
    )
    if has_anchor:
        return True
    has_element = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in _CODE_ELEMENTS
    )
    has_action = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in _ANALYSIS_ACTIONS
    )
    has_evaluation = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in _EVALUATION_TERMS
    )
    return has_element and (has_action or has_evaluation)


def quality_contract(
    request: Any, project_available: bool, enabled: bool,
    write_available: bool = True,
) -> Dict[str, Any]:
    constraints = requested_finding_constraints(request)
    return {
        "enabled": bool(enabled),
        "project_evidence_required": bool(enabled and request_needs_project_evidence(request, project_available)),
        "write_action_required": bool(
            write_available and request_requires_write(request, project_available)
        ),
        "requested_finding_limit": constraints["overall"],
        "requested_kind_limits": constraints["by_kind"],
        "claim_kinds": sorted(CLAIM_KINDS),
        "evidence_required_for": sorted(EVIDENCE_REQUIRED_KINDS),
        "claim_reference": "1-based sentence index over non-heading answer sentences",
        "claim_text_legacy_supported": True,
    }


def _claim_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _claim_skeleton(text: str) -> Tuple[str, bool]:
    folded = _fold(text)
    negative = bool(_NEGATION.search(folded))
    folded = _NEGATION.sub(" ", folded)
    return _normalized_words(folded), negative


def _validate_claims(
    claims_raw: Any,
    answer: str,
    evidence: Dict[str, Any],
    finding_limit: Optional[int],
    kind_limits: Optional[Dict[str, int]] = None,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    if not isinstance(claims_raw, list) or not claims_raw:
        return False, "FINAL_PROJECT_CLAIMS_REQUIRED", []
    if finding_limit is not None and len(claims_raw) > finding_limit:
        return False, f"FINAL_FINDING_LIMIT_EXCEEDED:{len(claims_raw)}>{finding_limit}", []
    kind_limits = dict(kind_limits or {})
    kind_counts: Dict[str, int] = {}

    claims: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    skeletons: Dict[str, bool] = {}
    normalized_answer = _normalized_words(answer)
    answer_segments = _answer_segments(answer)

    for index, raw in enumerate(claims_raw, start=1):
        if not isinstance(raw, dict):
            return False, f"FINAL_CLAIM_INVALID:{index}", []
        kind = _fold(raw.get("kind") or "")
        evidence_ids = _claim_ids(raw.get("evidence_ids"))
        sentence_raw = raw.get("sentence", raw.get("sentence_index"))
        text = str(raw.get("text") or "").strip()
        sentence_index: Optional[int] = None
        alignment: Optional[Dict[str, Any]] = None
        original_text: Optional[str] = None

        if sentence_raw is not None:
            if isinstance(sentence_raw, bool):
                return False, f"FINAL_CLAIM_SENTENCE_INVALID:{index}", []
            try:
                sentence_index = int(sentence_raw)
            except (TypeError, ValueError):
                return False, f"FINAL_CLAIM_SENTENCE_INVALID:{index}", []
            if sentence_index < 1 or sentence_index > len(answer_segments):
                return False, (
                    f"FINAL_CLAIM_SENTENCE_OUT_OF_RANGE:{index}:"
                    f"{sentence_index}>{len(answer_segments)}"
                ), []
            text = answer_segments[sentence_index - 1]
        elif not text:
            return False, f"FINAL_CLAIM_REFERENCE_REQUIRED:{index}", []

        if kind not in CLAIM_KINDS:
            return False, f"FINAL_CLAIM_KIND_INVALID:{index}:{kind or 'empty'}", []
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind in kind_limits and kind_counts[kind] > kind_limits[kind]:
            return False, (
                f"FINAL_KIND_LIMIT_EXCEEDED:{kind}:"
                f"{kind_counts[kind]}>{kind_limits[kind]}"
            ), []

        normalized_text = _normalized_words(text)
        if sentence_index is None and (not normalized_text or not _phrase_in_words(normalized_text, normalized_answer)):
            alignment = _align_claim_to_answer(text, answer)
            if alignment is None:
                return False, f"FINAL_CLAIM_NOT_IN_ANSWER:{index}", []
            original_text = text
            text = str(alignment["text"])
            normalized_text = _normalized_words(text)
        if normalized_text in seen_text:
            return False, f"FINAL_DUPLICATE_CLAIM:{index}", []
        seen_text.add(normalized_text)

        if kind in EVIDENCE_REQUIRED_KINDS and not evidence_ids:
            return False, f"FINAL_CLAIM_REQUIRES_EVIDENCE:{index}:{kind}", []
        missing = [item for item in evidence_ids if item not in evidence]
        if missing:
            return False, "FINAL_UNKNOWN_EVIDENCE:" + ",".join(missing), []

        skeleton, negative = _claim_skeleton(text)
        if skeleton and skeleton in skeletons and skeletons[skeleton] != negative:
            return False, f"FINAL_CONTRADICTORY_CLAIM:{index}", []
        if skeleton:
            skeletons[skeleton] = negative

        claim = {"text": text, "kind": kind, "evidence_ids": evidence_ids}
        if sentence_index is not None:
            claim["sentence"] = sentence_index
            claim["reference_method"] = "sentence_index"
        if original_text is not None and alignment is not None:
            claim.update({
                "original_text": original_text,
                "alignment_method": alignment["method"],
                "alignment_score": alignment["score"],
            })
        claims.append(claim)

    return True, "ok", claims


def validate_response_quality(
    final: Any,
    answer: str,
    evidence: Dict[str, Any],
    *,
    request: Any,
    project_available: bool,
    enabled: bool,
    reject_mid_list_corrections: bool = True,
) -> Tuple[bool, str, List[Dict[str, Any]], Optional[int]]:
    """Validate the optional strict quality contract.

    Legacy/general answers remain valid when the feature is disabled or the
    request is not about concrete project/code facts.
    """
    constraints = requested_finding_constraints(request)
    finding_limit = constraints["overall"]
    kind_limits = constraints["by_kind"]
    if not enabled:
        return True, "ok", [], finding_limit

    if reject_mid_list_corrections and len(_LIST_LINE.findall(answer)) >= 2 and _CORRECTION_MARKERS.search(answer):
        return False, "FINAL_MID_LIST_CORRECTION", [], finding_limit

    requires_project_evidence = request_needs_project_evidence(request, project_available)
    claims_raw = final.get("claims") if isinstance(final, dict) else None

    if requires_project_evidence:
        if not evidence:
            return False, "FINAL_PROJECT_FACTS_REQUIRE_READ", [], finding_limit
        ok, reason, claims = _validate_claims(
            claims_raw, answer, evidence, finding_limit, kind_limits,
        )
        return ok, reason, claims, finding_limit

    if isinstance(claims_raw, list) and claims_raw:
        ok, reason, claims = _validate_claims(
            claims_raw, answer, evidence, finding_limit, kind_limits,
        )
        return ok, reason, claims, finding_limit

    return True, "ok", [], finding_limit


def claim_evidence_ledger(claims: Iterable[Dict[str, Any]], evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    ledger: List[Dict[str, Any]] = []
    for claim in claims:
        sources = []
        for evidence_id in claim.get("evidence_ids") or []:
            item = evidence.get(evidence_id) or {}
            source = {
                "evidence_id": evidence_id,
                "file": item.get("arquivo"),
                "lines": [item.get("linha_inicio"), item.get("linha_fim")],
                "file_hash": item.get("file_hash"),
                "content_hash": item.get("content_hash"),
            }
            if item.get("source_type"):
                source.update({
                    "source_type": item.get("source_type"),
                    "stage": item.get("stage"),
                    "error_code": item.get("error_code"),
                })
            sources.append(source)
        entry = {
            "kind": claim.get("kind"),
            "text": claim.get("text"),
            "sources": sources,
        }
        if claim.get("sentence") is not None:
            entry.update({
                "sentence": claim.get("sentence"),
                "reference_method": claim.get("reference_method", "sentence_index"),
            })
        if claim.get("original_text"):
            entry.update({
                "original_text": claim.get("original_text"),
                "alignment_method": claim.get("alignment_method"),
                "alignment_score": claim.get("alignment_score"),
            })
        ledger.append(entry)
    return ledger
