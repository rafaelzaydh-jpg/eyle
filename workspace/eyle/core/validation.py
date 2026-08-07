"""Runtime validation for Eyle responses, evidence and factual claims."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .response_quality import validate_response_quality


def validate_final(
    final: Any,
    evidence: Dict[str, Any],
    *,
    request: Any = "",
    project_available: bool = False,
    quality_enabled: bool = False,
    reject_mid_list_corrections: bool = True,
) -> Tuple[bool, str, str, List[str], List[Dict[str, Any]], Optional[int]]:
    if isinstance(final, str):
        answer = final
        evidence_ids: List[str] = []
        limitations: List[str] = []
    elif isinstance(final, dict):
        answer = str(final.get("answer") or final.get("resposta") or "")
        evidence_ids = [str(item) for item in final.get("evidence_ids") or []]
        limitations = [str(item) for item in final.get("limitations") or final.get("limitacoes") or []]
    else:
        return False, "FINAL_INVALID", "", [], [], None
    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations, [], None
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations, [], None
    missing = [item for item in evidence_ids if item not in evidence]
    if missing:
        return False, "FINAL_UNKNOWN_EVIDENCE:" + ",".join(missing), "", limitations, [], None

    ok, reason, claims, finding_limit = validate_response_quality(
        final,
        answer,
        evidence,
        request=request,
        project_available=project_available,
        enabled=quality_enabled,
        reject_mid_list_corrections=reject_mid_list_corrections,
    )
    if not ok:
        return False, reason, "", limitations, [], finding_limit
    return True, "ok", answer, limitations, claims, finding_limit
