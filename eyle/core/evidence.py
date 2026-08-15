"""Active-task Evidence records, separate from persistent graph Memory."""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable


def empty_evidence() -> Dict[str, Dict[str, Any]]:
    return {}


def validate_evidence(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        raise ValueError("EVIDENCE_SCHEMA_INVALID")
    return copy.deepcopy(value)


def _next_id(existing: Iterable[str]) -> str:
    maximum = 0
    for raw in existing:
        text = str(raw or "")
        if text.startswith("ev-"):
            try:
                maximum = max(maximum, int(text[3:]))
            except ValueError:
                pass
    return f"ev-{maximum + 1:04d}"


def retain_evidence(
    evidence: Dict[str, Dict[str, Any]],
    *,
    material_id: str,
    material: Dict[str, Any],
    selector: Dict[str, Any],
    selected: Dict[str, Any],
    reality_epoch: int,
) -> tuple[Dict[str, Dict[str, Any]], str, bool]:
    state = validate_evidence(evidence)
    for evidence_id, record in state.items():
        if (
            str(record.get("material_id") or "") == str(material_id)
            and dict(record.get("selector") or {}) == dict(selector or {})
            and str(record.get("content_hash") or "") == str(material.get("content_hash") or "")
        ):
            return state, evidence_id, False
    evidence_id = _next_id(state.keys())
    state[evidence_id] = {
        "id": evidence_id,
        "material_id": str(material_id),
        "selector": copy.deepcopy(selector or {}),
        "selected": copy.deepcopy(selected or {}),
        "content_hash": material.get("content_hash"),
        "reality_epoch": int(reality_epoch or 0),
    }
    return state, evidence_id, True


def evidence_record(value: Dict[str, Dict[str, Any]], evidence_id: str) -> Dict[str, Any] | None:
    state = validate_evidence(value)
    item = state.get(str(evidence_id or ""))
    return copy.deepcopy(item) if isinstance(item, dict) else None


def retain_observation_evidence(
    evidence: Dict[str, Dict[str, Any]],
    *,
    materials: Dict[str, Dict[str, Any]],
    material_ids: Iterable[str],
    reality_epoch: int,
) -> Dict[str, Dict[str, Any]]:
    """Mechanically retain one evidence pointer for every physical Material.

    Evidence is perception bookkeeping, not a semantic memory decision. The
    complete Material remains in the Runtime ledger; Evidence stores a compact
    exact pointer/hash so Main may recall it during the active AgentSession.
    """
    state = validate_evidence(evidence)
    for raw_id in material_ids or []:
        material_id = str(raw_id or "")
        material = materials.get(material_id)
        if not isinstance(material, dict):
            continue
        selected = {
            "locator": copy.deepcopy(material.get("locator") or {}),
            "content_hash": material.get("content_hash"),
        }
        state, _evidence_id, _created = retain_evidence(
            state, material_id=material_id, material=material, selector={},
            selected=selected, reality_epoch=reality_epoch,
        )
    return state


def evidence_ids_for_materials(
    evidence: Dict[str, Dict[str, Any]], material_ids: Iterable[str], *, whole_material_only: bool = True,
) -> list[str]:
    wanted = {str(v) for v in material_ids or []}
    state = validate_evidence(evidence)
    out = []
    for evidence_id, record in state.items():
        if str(record.get("material_id") or "") not in wanted:
            continue
        if whole_material_only and dict(record.get("selector") or {}):
            continue
        out.append(str(evidence_id))
    return sorted(out)
