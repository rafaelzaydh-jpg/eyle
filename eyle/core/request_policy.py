"""Deterministic request-contract helpers for the AgentSession core.

Only explicit bounded Finding counts are parsed here. Workspace dependency and
write intent belong to the Main LLM ``workspace_scope`` contract.
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


def _fold(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


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
