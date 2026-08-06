"""Minimal runtime validation for Eyle responses and evidence references."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate_final(final: Any, evidence: Dict[str, Any]) -> Tuple[bool, str, str, List[str]]:
    if isinstance(final, str):
        answer = final
        evidence_ids: List[str] = []
        limitations: List[str] = []
    elif isinstance(final, dict):
        answer = str(final.get("answer") or final.get("resposta") or "")
        evidence_ids = [str(item) for item in final.get("evidence_ids") or []]
        limitations = [str(item) for item in final.get("limitations") or final.get("limitacoes") or []]
    else:
        return False, "FINAL_INVALID", "", []
    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations
    missing = [item for item in evidence_ids if item not in evidence]
    if missing:
        return False, "FINAL_UNKNOWN_EVIDENCE:" + ",".join(missing), "", limitations
    return True, "ok", answer, limitations
