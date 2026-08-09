"""Single deterministic final gate before semantic Claim Review."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .request_policy import requested_finding_limit


def validate_final(
    final: Any,
    evidence: Dict[str, Any],
    *,
    request: Any = "",
    project_available: bool = False,
    grounding_required: bool = False,
    investigation: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str, str, List[str], List[Dict[str, Any]], Optional[int]]:
    """Validate only deterministic final-answer invariants.

    Semantic truth, Claim atomization, contradictions and omissions belong to
    ``claim_review.py``. This gate owns structure, Evidence identity and the
    fail-closed requirement that project-grounded answers cite runtime Evidence.
    """
    finding_limit = requested_finding_limit(request)
    if isinstance(final, str):
        answer = final
        evidence_ids: List[str] = []
        limitations: List[str] = []
    elif isinstance(final, dict):
        allowed = {"answer", "evidence_ids", "limitations"}
        unknown = sorted(set(final) - allowed)
        if unknown:
            return False, "FINAL_UNKNOWN_FIELDS:" + ",".join(unknown), "", [], [], finding_limit
        answer = str(final.get("answer") or "")
        raw_evidence_ids = final.get("evidence_ids", [])
        raw_limitations = final.get("limitations", [])
        if not isinstance(raw_evidence_ids, list):
            return False, "FINAL_EVIDENCE_IDS_INVALID", "", [], [], finding_limit
        if not isinstance(raw_limitations, list):
            return False, "FINAL_LIMITATIONS_INVALID", "", [], [], finding_limit
        evidence_ids = [str(item) for item in raw_evidence_ids if str(item)]
        evidence_ids = list(dict.fromkeys(evidence_ids))
        limitations = [str(item) for item in raw_limitations]
    else:
        return False, "FINAL_INVALID", "", [], [], finding_limit

    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations, [], finding_limit
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations, [], finding_limit
    missing = [item for item in evidence_ids if item not in evidence]
    if missing:
        return False, "FINAL_UNKNOWN_EVIDENCE:" + ",".join(missing), "", limitations, [], finding_limit

    if grounding_required:
        targets = [item for item in (investigation or []) if isinstance(item, dict)]
        if not targets:
            return False, "FINAL_INVESTIGATION_REQUIRED", "", limitations, [], finding_limit
        open_ids = [str(item.get("id") or "") for item in targets if item.get("status") == "open"]
        if open_ids:
            return False, "FINAL_INVESTIGATION_TARGET_OPEN:" + ",".join(open_ids), "", limitations, [], finding_limit
    if grounding_required and not evidence:
        return False, "FINAL_PROJECT_FACTS_REQUIRE_READ", "", limitations, [], finding_limit
    if grounding_required and not evidence_ids:
        return False, "FINAL_PROJECT_EVIDENCE_IDS_REQUIRED", "", limitations, [], finding_limit

    return True, "ok", answer, limitations, [], finding_limit
