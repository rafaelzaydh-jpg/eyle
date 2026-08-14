"""Task-scoped cognitive memory for Eyle Main.

Observation owns what the body physically observed. Task Memory owns only the
compact semantic knowledge Main chose to retain from those observations:
EvidenceSpan coordinates, Findings and Conclusions. Runtime validates identity
and references; it never decides what is important or whether a statement is
semantically true.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Tuple

TASK_MEMORY_SCHEMA_VERSION = "1"
_ID_PATTERNS = {
    "evidence": re.compile(r"^ev-[A-Za-z0-9._-]+$"),
    "finding": re.compile(r"^f-[A-Za-z0-9._-]+$"),
    "conclusion": re.compile(r"^c-[A-Za-z0-9._-]+$"),
}


def empty_task_memory() -> Dict[str, Any]:
    return {"evidence": {}, "findings": {}, "conclusions": {}}


def _copy_state(value: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    state = empty_task_memory()
    for key in state:
        bucket = raw.get(key)
        if isinstance(bucket, dict):
            state[key] = {str(item_id): copy.deepcopy(item) for item_id, item in bucket.items() if isinstance(item, dict)}
    return state


def validate_task_memory_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"evidence", "findings", "conclusions"}:
        raise ValueError("TASK_MEMORY_STATE_INVALID")
    state = _copy_state(value)
    if any(not isinstance(value.get(key), dict) for key in state):
        raise ValueError("TASK_MEMORY_STATE_INVALID")

    for evidence_id, item in state["evidence"].items():
        if _ID_PATTERNS["evidence"].fullmatch(evidence_id) is None:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        required = {"id", "material_id", "selector", "locator", "source_capability", "source_version", "content_hash"}
        if set(item) != required or item.get("id") != evidence_id:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("material_id"), str) or not item["material_id"].startswith("mat-"):
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("selector"), dict) or not isinstance(item.get("locator"), dict):
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        for field in ("source_capability", "source_version", "content_hash"):
            if not isinstance(item.get(field), str):
                raise ValueError("TASK_MEMORY_STATE_INVALID")

    for finding_id, item in state["findings"].items():
        if _ID_PATTERNS["finding"].fullmatch(finding_id) is None:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if set(item) != {"id", "statement", "evidence_ids"} or item.get("id") != finding_id:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("evidence_ids"), list) or any(not isinstance(ref, str) for ref in item["evidence_ids"]):
            raise ValueError("TASK_MEMORY_STATE_INVALID")

    for conclusion_id, item in state["conclusions"].items():
        if _ID_PATTERNS["conclusion"].fullmatch(conclusion_id) is None:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if set(item) != {"id", "statement", "evidence_ids", "finding_ids"} or item.get("id") != conclusion_id:
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("evidence_ids"), list) or any(not isinstance(ref, str) for ref in item["evidence_ids"]):
            raise ValueError("TASK_MEMORY_STATE_INVALID")
        if not isinstance(item.get("finding_ids"), list) or any(not isinstance(ref, str) for ref in item["finding_ids"]):
            raise ValueError("TASK_MEMORY_STATE_INVALID")
    return state


def persisted_view(value: Dict[str, Any]) -> Dict[str, Any]:
    return validate_task_memory_state(value)


def project_task_knowledge(value: Dict[str, Any]) -> Dict[str, Any]:
    """Return compact task knowledge; Evidence content itself is never replayed here."""
    state = validate_task_memory_state(value)
    evidence = []
    for item in state["evidence"].values():
        evidence.append({
            "id": item["id"],
            "material_id": item["material_id"],
            "locator": copy.deepcopy(item["locator"]),
            "selector": copy.deepcopy(item["selector"]),
            "source_version": item["source_version"],
            "content_hash": item["content_hash"],
        })
    return {
        "evidence": evidence,
        "findings": [copy.deepcopy(item) for item in state["findings"].values()],
        "conclusions": [copy.deepcopy(item) for item in state["conclusions"].values()],
    }


def apply_task_memory_updates(
    updates: Any,
    *,
    previous: Dict[str, Any],
    materials: Dict[str, Dict[str, Any]],
    select_evidence: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Apply independent Main-owned cognitive-memory deltas.

    Evidence IDs are immutable coordinates. Findings and Conclusions are
    semantic notes owned by Main and may be refined under the same ID.
    Invalid siblings never roll back valid updates.
    """
    state = validate_task_memory_state(previous)
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    if updates is None:
        return state, accepted, rejected
    if not isinstance(updates, dict):
        return state, accepted, [{"kind": "memory", "id": "", "reason": "TASK_MEMORY_UPDATES_INVALID"}]

    # Evidence first so Findings in the same Main turn may refer to it.
    for raw in updates.get("evidence") or []:
        if not isinstance(raw, dict):
            rejected.append({"kind": "evidence", "id": "", "reason": "TASK_MEMORY_EVIDENCE_INVALID"})
            continue
        evidence_id = str(raw.get("id") or "").strip()
        material_id = str(raw.get("material_id") or "").strip()
        selector = copy.deepcopy(raw.get("selector") or {})
        if _ID_PATTERNS["evidence"].fullmatch(evidence_id) is None:
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": "TASK_MEMORY_EVIDENCE_ID_INVALID"})
            continue
        material = materials.get(material_id)
        if not isinstance(material, dict):
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": f"TASK_MEMORY_UNKNOWN_MATERIAL:{material_id}"})
            continue
        if not isinstance(selector, dict):
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": "TASK_MEMORY_SELECTOR_INVALID"})
            continue
        try:
            selected = select_evidence(material, selector)
        except ValueError as exc:
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": str(exc) or "TASK_MEMORY_SELECTOR_INVALID"})
            continue
        if not isinstance(selected, dict):
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": "TASK_MEMORY_SELECTOR_INVALID"})
            continue
        record = {
            "id": evidence_id,
            "material_id": material_id,
            "selector": selector,
            "locator": copy.deepcopy(selected.get("locator") or material.get("locator") or {}),
            "source_capability": str(material.get("source_capability") or ""),
            "source_version": str(material.get("source_version") or ""),
            "content_hash": str(selected.get("content_hash") or material.get("content_hash") or ""),
        }
        existing = state["evidence"].get(evidence_id)
        if existing is not None and existing != record:
            rejected.append({"kind": "evidence", "id": evidence_id, "reason": "TASK_MEMORY_EVIDENCE_ID_IMMUTABLE"})
            continue
        changed = existing is None
        state["evidence"][evidence_id] = record
        accepted.append({"kind": "evidence", "id": evidence_id, "changed": changed})

    for raw in updates.get("findings") or []:
        if not isinstance(raw, dict):
            rejected.append({"kind": "finding", "id": "", "reason": "TASK_MEMORY_FINDING_INVALID"})
            continue
        finding_id = str(raw.get("id") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        evidence_ids = [str(ref) for ref in (raw.get("evidence_ids") or [])]
        if _ID_PATTERNS["finding"].fullmatch(finding_id) is None or not statement:
            rejected.append({"kind": "finding", "id": finding_id, "reason": "TASK_MEMORY_FINDING_INVALID"})
            continue
        unknown = [ref for ref in evidence_ids if ref not in state["evidence"]]
        if unknown:
            rejected.append({"kind": "finding", "id": finding_id, "reason": "TASK_MEMORY_UNKNOWN_EVIDENCE:" + ",".join(unknown)})
            continue
        record = {"id": finding_id, "statement": statement, "evidence_ids": evidence_ids}
        changed = state["findings"].get(finding_id) != record
        state["findings"][finding_id] = record
        accepted.append({"kind": "finding", "id": finding_id, "changed": changed})

    for raw in updates.get("conclusions") or []:
        if not isinstance(raw, dict):
            rejected.append({"kind": "conclusion", "id": "", "reason": "TASK_MEMORY_CONCLUSION_INVALID"})
            continue
        conclusion_id = str(raw.get("id") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        evidence_ids = [str(ref) for ref in (raw.get("evidence_ids") or [])]
        finding_ids = [str(ref) for ref in (raw.get("finding_ids") or [])]
        if _ID_PATTERNS["conclusion"].fullmatch(conclusion_id) is None or not statement:
            rejected.append({"kind": "conclusion", "id": conclusion_id, "reason": "TASK_MEMORY_CONCLUSION_INVALID"})
            continue
        unknown_evidence = [ref for ref in evidence_ids if ref not in state["evidence"]]
        unknown_findings = [ref for ref in finding_ids if ref not in state["findings"]]
        if unknown_evidence or unknown_findings:
            refs = unknown_evidence + unknown_findings
            rejected.append({"kind": "conclusion", "id": conclusion_id, "reason": "TASK_MEMORY_UNKNOWN_SUPPORT:" + ",".join(refs)})
            continue
        record = {
            "id": conclusion_id,
            "statement": statement,
            "evidence_ids": evidence_ids,
            "finding_ids": finding_ids,
        }
        changed = state["conclusions"].get(conclusion_id) != record
        state["conclusions"][conclusion_id] = record
        accepted.append({"kind": "conclusion", "id": conclusion_id, "changed": changed})

    return state, accepted, rejected
