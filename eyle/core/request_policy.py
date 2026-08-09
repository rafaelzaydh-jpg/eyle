"""Deterministic request helpers for the AgentSession core.

Explicit numeric Finding limits remain deterministic. Legacy lexical helpers
are retained for compatibility/tests, but production workspace authority is
declared by the Main LLM through ``workspace_scope`` and administered by the
runtime.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

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
    "workspace", "código-fonte", "codigo-fonte", "source code",
    "neste arquivo", "nesse arquivo", "este arquivo", "esse arquivo",
    "neste módulo", "nesse módulo", "este módulo", "esse módulo",
    "neste modulo", "nesse modulo", "este modulo", "esse modulo",
)
_ANALYSIS_ACTIONS = (
    "analise", "análise", "analize", "analisar", "analyze", "review", "revisao",
    "revisão", "inspecione", "inspect", "verifique", "verify", "check", "audite",
    "audit", "investigue", "investigate", "encontre", "find", "identifique",
    "identify", "localize", "locate", "definido", "definida", "defined",
    "utilizado", "utilizada", "usado", "usada", "used", "referencias",
    "referências", "references", "chama", "calls", "onde", "where",
    "deletou", "removeu", "apagou", "deleted", "removed", "existe", "exists",
)

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
_WRITE_TARGETS = (
    "arquivo", "arquivos", "file", "files", "codigo", "código", "code",
    "funcao", "função", "function", "classe", "class", "modulo", "módulo",
    "module", "rota", "route", "endpoint", "template", "html", "css",
    "javascript", "python", "teste", "test", "config", "configuracao",
    "configuração", "readme", "documentacao", "documentação", "pasta",
    "diretorio", "diretório", "folder", "directory", "projeto", "project",
    "workspace", "dependencia", "dependência", "dependency",
)

_SOURCE_PATH = re.compile(
    r"(?:^|[\s`'\"])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|pyi|js|jsx|ts|tsx|java|go|rs|rb|php|cs|cpp|c|h|hpp|kt|swift|vue|svelte|json|toml|ya?ml)\b",
    re.I,
)
_CODE_IDENTIFIER = re.compile(
    r"(?:`[^`]{1,120}`|\b[A-Z][A-Za-z0-9]+[A-Z][A-Za-z0-9]*\b|\b[a-z][a-z0-9]+_[a-z0-9_]+\b)"
)
_LOCATION_USE_HINT = re.compile(
    r"\b(?:onde|where|definid[oa]|defined|declarad[oa]|declared|utilizad[oa]|usad[oa]|used|"
    r"refer[eê]ncias?|references?|quem\s+chama|who\s+calls|fluxo|flow)\b", re.I,
)
_IMPLEMENTATION_FLOW_HINT = re.compile(
    r"(?=.*\b(?:mensagem|message|request|prompt)\b)(?=.*\b(?:llm|modelo|model)\b)"
    r"(?=.*\b(?:resposta|response|fluxo|flow|caminho|path|chega|reach|travessa|passa)\b)",
    re.I,
)
_NON_WORKSPACE_TARGET = re.compile(
    r"\b(?:n[aã]o|not|sem|without)\s+(?:(?:o|a|os|as|the)\s+)?"
    r"(?:c[oó]digo|code|arquivo|files?|projeto|project|workspace)\b", re.I,
)
_BARE_CODE_MUTATION = re.compile(
    r"\b(?:mude|altere|corrija|substitua|renomeie|change|fix|replace|rename)\s+"
    r"[A-Za-z_][A-Za-z0-9_.]*\s+(?:para|por|to|as)\b", re.I,
)
_CODE_NAMED_TERM = re.compile(
    r"\b(?:funcao|função|function|classe|class|modulo|módulo|module|"
    r"rota|route|endpoint|variavel|variável|variable|metodo|método|method)"
    r"\s+[`'\"]?([A-Za-z_][A-Za-z0-9_.-]*)",
    re.I,
)


def _fold(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _normalized_words(text: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", _fold(text)))


def _phrase_in_words(phrase: str, words: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {words} "


def requested_finding_constraints(request: Any) -> Dict[str, Any]:
    """Extract overall and per-kind caps from explicit bounded requests."""
    text = str(request or "")
    matches: list[Tuple[int, int, Optional[str]]] = []
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
    generic: list[int] = []
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
    return requested_finding_constraints(request)["overall"]


def request_requires_write(request: Any, project_available: bool = True) -> bool:
    """Return True for direct file/code mutation commands."""
    if not project_available:
        return False
    raw = str(request or "").strip()
    if not raw or _ADVISORY_OPENING.search(raw):
        return False
    if _WRITE_NOUN_COMMAND.search(_fold(raw)):
        return True
    if not _WRITE_IMPERATIVE.search(raw):
        return False
    if _SOURCE_PATH.search(raw) or _BARE_CODE_MUTATION.search(raw):
        return True
    target_text = _NON_WORKSPACE_TARGET.sub(" ", raw)
    words = _normalized_words(target_text)
    return any(_phrase_in_words(_normalized_words(term), words) for term in _WRITE_TARGETS)


def request_needs_project_evidence(request: Any, project_available: bool) -> bool:
    """Require grounding only for concrete workspace-dependent requests."""
    if not project_available:
        return False
    raw = str(request or "")
    if _SOURCE_PATH.search(raw):
        return True
    words = _normalized_words(raw)
    if any(_phrase_in_words(_normalized_words(term), words) for term in _PROJECT_ANCHORS):
        return True
    if _CODE_IDENTIFIER.search(raw) and _LOCATION_USE_HINT.search(raw):
        return True
    if _IMPLEMENTATION_FLOW_HINT.search(raw):
        return True

    conceptual = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in ("vantagem", "vantagens", "desvantagem", "desvantagens", "conceito", "conceitual", "em geral", "generico", "genérico")
    )
    has_named_code_element = bool(_CODE_NAMED_TERM.search(raw))
    has_analysis_action = any(_phrase_in_words(_normalized_words(term), words) for term in _ANALYSIS_ACTIONS)
    if has_analysis_action and any(
        _phrase_in_words(_normalized_words(term), words)
        for term in ("codigo", "código", "codebase", "source code")
    ) and not conceptual:
        return True
    if has_named_code_element and has_analysis_action and not conceptual:
        return True

    state_terms = ("pasta", "pastas", "diretorio", "diretório", "directory", "folder", "template", "templates")
    state_actions = ("deletou", "removeu", "apagou", "deleted", "removed", "existe", "exists")
    if any(_phrase_in_words(_normalized_words(term), words) for term in state_terms) and any(
        _phrase_in_words(_normalized_words(term), words) for term in state_actions
    ):
        return True

    has_bug_or_risk = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in ("bug", "bugs", "erro", "erros", "falha", "falhas", "risk", "risks", "risco", "riscos")
    )
    has_hunt = any(
        _phrase_in_words(_normalized_words(term), words)
        for term in ("identifique", "identify", "encontre", "find", "investigue", "investigate", "analise", "análise", "analyze", "audit", "audite", "procure", "search")
    )
    return bool(has_bug_or_risk and has_hunt)


def request_contract(
    request: Any, project_available: bool,
    write_available: bool = True, claims_mode: str = "self_check",
    workspace_scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    constraints = requested_finding_constraints(request)
    declared = dict(workspace_scope) if isinstance(workspace_scope, dict) else {}
    return {
        "workspace_scope_authority": "main_llm",
        "workspace_available": bool(project_available),
        "write_available": bool(write_available),
        "declared_workspace_scope": declared or None,
        "requested_finding_limit": constraints["overall"],
        "requested_kind_limits": constraints["by_kind"],
        "claims_mode": str(claims_mode or "self_check"),
        "claims_generated_after_answer": str(claims_mode or "self_check") != "off",
        "claim_verdicts": ["supported", "contradicted", "insufficient"],
    }
