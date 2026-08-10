"""Canonical write transaction state."""
from __future__ import annotations
import copy
from typing import Any, Dict, List


def empty_transaction() -> Dict[str, Any]:
    return {}


def begin(*, patches: List[Dict[str, Any]], turn: int) -> Dict[str, Any]:
    return {
        "transaction_id": f"tx-{int(turn):04d}", "status": "proposed",
        "patches": copy.deepcopy(patches), "attempts": 0, "validation": {},
    }


def set_status(tx: Dict[str, Any], status: str) -> None:
    tx["status"] = str(status)


def record_validation(tx: Dict[str, Any], stage: str, value: Dict[str, Any]) -> None:
    tx.setdefault("validation", {})[str(stage)] = copy.deepcopy(value)


def increment_attempt(tx: Dict[str, Any]) -> int:
    tx["attempts"] = int(tx.get("attempts") or 0) + 1
    return int(tx["attempts"])


def record_failure(tx: Dict[str, Any], failure: Dict[str, Any]) -> None:
    tx["failure"] = copy.deepcopy(dict(failure or {}))


def clear_failure(tx: Dict[str, Any]) -> None:
    tx.pop("failure", None)


def public_view(tx: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(tx, dict) or not tx: return {}
    return {
        "transaction_id": tx.get("transaction_id"), "status": tx.get("status"),
        "attempts": int(tx.get("attempts") or 0),
        "paths": [str(item.get("path") or "") for item in tx.get("patches") or [] if isinstance(item, dict)],
        "validation": copy.deepcopy(tx.get("validation") or {}),
        "failure": copy.deepcopy(tx.get("failure") or {}) if tx.get("failure") else None,
    }
