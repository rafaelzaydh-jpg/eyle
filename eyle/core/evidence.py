"""Canonical Evidence ledger and explicit SourceRecord admission helpers.

Rev5.8 cleanly separates physical materialization from semantic admission:
capabilities create SourceRecords; the Main LLM selects SourceRecord ids in
Investigation/Final; Runtime deterministically promotes only those selected
records into Evidence. Runtime never promotes by inferred relevance.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .workspace_io import ErroLeituraProjeto, ler_faixa_projeto


def empty_ledger() -> Dict[str, Any]:
    return {"items": {}}


def items(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = ledger.setdefault("items", {})
    return value if isinstance(value, dict) else {}


def _ids(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def evidence_id_for_source_record(source_record_id: str) -> str:
    value = str(source_record_id or "").strip()
    if not value.startswith("src-"):
        raise ValueError("SOURCE_RECORD_ID_INVALID")
    return "ev-" + value


def promote_source_record_items(
    evidence_store: Dict[str, Dict[str, Any]], source_store: Dict[str, Dict[str, Any]], refs: Iterable[Any],
    *, admitted_by: str = "main",
) -> Tuple[List[str], List[str]]:
    """Resolve existing Evidence ids and promote explicitly selected SourceRecords.

    ``refs`` may contain canonical Evidence ids already admitted or SourceRecord
    ids visible to the Main LLM.  Unknown refs are returned to the caller.  The
    mapping ``src-N -> ev-src-N`` is deterministic, so promotion order never
    changes Evidence identity.
    """
    store = evidence_store if isinstance(evidence_store, dict) else {}
    source_store = source_store if isinstance(source_store, dict) else {}
    resolved: List[str] = []
    missing: List[str] = []
    for ref in _ids(refs):
        if ref.startswith("ev-"):
            if ref in store:
                resolved.append(ref)
            else:
                missing.append(ref)
            continue
        if not ref.startswith("src-") or ref not in source_store:
            missing.append(ref)
            continue
        source = source_store.get(ref)
        if (
            not isinstance(source, dict)
            or source.get("stale") is True
            or source.get("rehydration_error")
        ):
            missing.append(ref)
            continue
        evidence_id = evidence_id_for_source_record(ref)
        if evidence_id not in store:
            item = copy.deepcopy(source)
            item["id"] = evidence_id
            item["source_record_id"] = ref
            item["admitted_by"] = str(admitted_by or "main")
            store[evidence_id] = item
        resolved.append(evidence_id)
    return resolved, missing



def promote_source_records(
    ledger: Dict[str, Any], source_records: Dict[str, Any], refs: Iterable[Any],
    *, admitted_by: str = "main",
) -> Tuple[List[str], List[str]]:
    source_store = source_records.get("items") if isinstance(source_records, dict) else {}
    return promote_source_record_items(
        items(ledger), source_store if isinstance(source_store, dict) else {}, refs,
        admitted_by=admitted_by,
    )

def register_runtime_evidence(ledger: Dict[str, Any], evidence_id: str, item: Dict[str, Any]) -> str:
    """Register one Runtime-owned canonical fact that is already Evidence.

    This path is intentionally narrow for physical validation facts such as a
    persisted write failure. It is not used for ordinary capability output.
    """
    value = dict(item or {})
    value["id"] = str(evidence_id)
    value["admitted_by"] = "runtime_fact"
    items(ledger)[str(evidence_id)] = value
    return str(evidence_id)


def index_view(ledger: Dict[str, Any], investigation: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    store = items(ledger)
    pinned: List[str] = []
    seen = set()
    for target in investigation or []:
        if not isinstance(target, dict):
            continue
        for evidence_id in target.get("evidence_ids") or []:
            evidence_id = str(evidence_id or "").strip()
            if evidence_id and evidence_id in store and evidence_id not in seen:
                pinned.append(evidence_id)
                seen.add(evidence_id)
    recent = [evidence_id for evidence_id in store if evidence_id not in seen]
    out = []
    for evidence_id in pinned + recent:
        item = store.get(evidence_id)
        if not isinstance(item, dict):
            continue
        entry = {
            "id": evidence_id, "file": item.get("file"),
            "lines": [item.get("line_start"), item.get("line_end")],
        }
        if evidence_id in seen:
            entry["pinned"] = True
        if item.get("source_record_id"):
            entry["source_record_id"] = item.get("source_record_id")
        if item.get("source_type"):
            entry.update({
                "source_type": item.get("source_type"), "stage": item.get("stage"),
                "error_code": item.get("error_code"),
            })
        out.append(entry)
    return out


def freshest_for_path(ledger: Dict[str, Any], path: str) -> Optional[Dict[str, Any]]:
    normalized = str(path or "").replace("\\", "/")
    for item in reversed(list(items(ledger).values())):
        if isinstance(item, dict) and str(item.get("file") or "").replace("\\", "/") == normalized:
            return item
    return None


def persisted_view(ledger: Dict[str, Any]) -> Dict[str, Any]:
    return {"items": {
        key: {
            field: copy.deepcopy(value)
            for field, value in item.items()
            if field not in {"content", "numbered_content"}
        }
        for key, item in items(ledger).items() if isinstance(item, dict)
    }}


def rehydrate(ledger: Dict[str, Any], project_root: str, *, max_lines: int) -> None:
    if not project_root or not os.path.isdir(project_root):
        return
    for evidence_id, item in list(items(ledger).items()):
        if not isinstance(item, dict) or item.get("content") or item.get("numbered_content"):
            continue
        path = str(item.get("file") or "").strip()
        start, end = item.get("line_start"), item.get("line_end")
        if not path or path.startswith("<") or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            continue
        try:
            reading = ler_faixa_projeto(
                project_root, path, start, end,
                max_linhas=max(max_lines, end - start + 1),
            )
        except ErroLeituraProjeto as error:
            item["rehydration_error"] = error.error_code
            continue
        if (
            (item.get("file_hash") and str(item.get("file_hash")) != str(reading.get("file_hash") or ""))
            or (item.get("content_hash") and str(item.get("content_hash")) != str(reading.get("content_hash") or ""))
        ):
            item["rehydration_error"] = "EVIDENCE_STALE"
            item["stale"] = True
            continue
        item.update(reading)
        item.pop("rehydration_error", None)
        item.pop("stale", None)


def seed_runtime_failure(ledger: Dict[str, Any], conversation_context: Any) -> List[Dict[str, Any]]:
    """Expose the latest persisted write failure as one Runtime-owned Evidence fact."""
    messages = list((conversation_context or {}).get("recent_messages") or []) if isinstance(conversation_context, dict) else []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        failure = message.get("write_failure")
        if not isinstance(failure, dict) or not failure:
            continue
        detail = str(failure.get("detail") or "").strip()
        if not detail:
            continue
        from .text_hash import hash_texto
        evidence_id = "ev-runtime-0001"
        item = {
            "file": "<runtime-validation>", "line_start": None, "line_end": None,
            "file_hash": None, "content_hash": hash_texto(detail), "content": detail,
            "source_type": "runtime_validation", "stage": failure.get("stage"),
            "error_code": failure.get("error_code"), "paths": list(failure.get("paths") or []),
            "rollback_confirmed": failure.get("rollback_confirmed"),
        }
        register_runtime_evidence(ledger, evidence_id, item)
        return [{
            "tool": "runtime_validation", "status": "success", "ok": True,
            "executed": False, "changed": False, "error_code": None,
            "detail": {"evidence_id": evidence_id, "source_type": "runtime_validation", "stage": failure.get("stage"), "error_code": failure.get("error_code"), "content": detail[:700]},
            "evidence_ids": [evidence_id],
        }]
    return []
