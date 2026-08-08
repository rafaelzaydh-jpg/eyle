"""Administrative structured-output capability discovery for Eyle connections.

The core never knows providers.  This module stores only empirically verified
transport capabilities for a connection fingerprint.  A capability is trusted
only after a behavioral probe succeeds and is always paired with local Eyle
validation.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from eyle.runtime.persistence import salvar_json_atomico


CAPABILITY_VERSION = 1
STRUCTURED_MODES = ("json_schema", "json_object", "prompt")
_LOCK = threading.RLock()
_VERIFIED_THIS_PROCESS: dict[str, str] = {}


def connection_fingerprint(*, transport: str, base_url: str, model: str) -> str:
    canonical = json.dumps({
        "transport": str(transport).strip().lower(),
        "base_url": str(base_url).rstrip("/"),
        "model": str(model).strip(),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_path() -> Path:
    override = os.getenv("EYLE_LLM_CAPABILITIES_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = Path(__file__).resolve().parents[1]
    return root / "context" / "llm_capabilities.json"


def _empty_cache() -> dict[str, Any]:
    return {"version": CAPABILITY_VERSION, "connections": {}}


def _load() -> dict[str, Any]:
    path = cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_cache()
    if not isinstance(raw, dict) or raw.get("version") != CAPABILITY_VERSION:
        return _empty_cache()
    connections = raw.get("connections")
    if not isinstance(connections, dict):
        return _empty_cache()
    return {"version": CAPABILITY_VERSION, "connections": dict(connections)}


def _save(cache: dict[str, Any]) -> None:
    salvar_json_atomico(cache_path(), cache, indent=2)


def _entry(cache: dict[str, Any], fingerprint: str) -> dict[str, Any] | None:
    item = (cache.get("connections") or {}).get(fingerprint)
    if not isinstance(item, dict):
        return None
    mode = item.get("structured_output")
    if mode not in STRUCTURED_MODES or item.get("verified") is not True:
        return None
    return dict(item)


def cached_mode(fingerprint: str) -> str | None:
    with _LOCK:
        item = _entry(_load(), fingerprint)
        return str(item["structured_output"]) if item else None


def process_mode(fingerprint: str) -> str | None:
    with _LOCK:
        return _VERIFIED_THIS_PROCESS.get(fingerprint)


def _persist_verified(
    fingerprint: str, mode: str, *, transport: str, base_url: str, model: str,
) -> None:
    _VERIFIED_THIS_PROCESS[fingerprint] = mode
    cache = _load()
    connections = cache.setdefault("connections", {})
    connections[fingerprint] = {
        "structured_output": mode,
        "verified": True,
        "transport": str(transport),
        "model": str(model),
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    try:
        _save(cache)
    except OSError:
        # Persistence is a startup optimization, not an authority requirement.
        # The empirically verified in-process capability remains usable.
        pass


def invalidate(fingerprint: str) -> None:
    with _LOCK:
        _VERIFIED_THIS_PROCESS.pop(fingerprint, None)
        cache = _load()
        connections = cache.setdefault("connections", {})
        if fingerprint in connections:
            connections.pop(fingerprint, None)
            try:
                _save(cache)
            except OSError:
                pass


def reset_process_cache() -> None:
    """Test/dev helper; persisted capabilities are untouched."""
    with _LOCK:
        _VERIFIED_THIS_PROCESS.clear()


def ensure_capability(
    *, transport: str, base_url: str, model: str,
    probe: Callable[[str], bool],
) -> dict[str, str]:
    """Return one verified structured mode for this process/connection.

    On first use after process start, the persisted mode is behaviorally probed
    once.  If that probe fails, it is repeated once before the cache is
    invalidated and a full negotiation begins.
    """
    fingerprint = connection_fingerprint(transport=transport, base_url=base_url, model=model)
    with _LOCK:
        current = _VERIFIED_THIS_PROCESS.get(fingerprint)
        if current in STRUCTURED_MODES:
            return {"mode": current, "source": "process", "fingerprint": fingerprint}

        cache = _load()
        item = _entry(cache, fingerprint)
        failed_cached = None
        if item:
            cached = str(item["structured_output"])
            if probe(cached):
                _VERIFIED_THIS_PROCESS[fingerprint] = cached
                _persist_verified(fingerprint, cached, transport=transport, base_url=base_url, model=model)
                return {"mode": cached, "source": "cache_verified", "fingerprint": fingerprint}
            if probe(cached):
                _VERIFIED_THIS_PROCESS[fingerprint] = cached
                _persist_verified(fingerprint, cached, transport=transport, base_url=base_url, model=model)
                return {"mode": cached, "source": "cache_verified_retry", "fingerprint": fingerprint}
            failed_cached = cached
            connections = cache.setdefault("connections", {})
            connections.pop(fingerprint, None)
            try:
                _save(cache)
            except OSError:
                pass

        for mode in STRUCTURED_MODES:
            if mode == failed_cached:
                continue
            if probe(mode):
                _persist_verified(fingerprint, mode, transport=transport, base_url=base_url, model=model)
                return {"mode": mode, "source": "probe", "fingerprint": fingerprint}

    raise RuntimeError("LLM_STRUCTURED_OUTPUT_UNAVAILABLE")


def revalidate_capability(
    *, transport: str, base_url: str, model: str, current_mode: str,
    probe: Callable[[str], bool],
) -> dict[str, str]:
    """Revalidate a previously working mode after a structural violation.

    One bad model generation never downgrades a connection.  The current mode
    must fail two short behavioral probes before it is invalidated.  Full
    negotiation then tries the remaining official modes.
    """
    fingerprint = connection_fingerprint(transport=transport, base_url=base_url, model=model)
    with _LOCK:
        if probe(current_mode) or probe(current_mode):
            _persist_verified(fingerprint, current_mode, transport=transport, base_url=base_url, model=model)
            return {"mode": current_mode, "source": "revalidated", "fingerprint": fingerprint}

        _VERIFIED_THIS_PROCESS.pop(fingerprint, None)
        cache = _load()
        cache.setdefault("connections", {}).pop(fingerprint, None)
        try:
            _save(cache)
        except OSError:
            pass
        for mode in STRUCTURED_MODES:
            if mode == current_mode:
                continue
            if probe(mode):
                _persist_verified(fingerprint, mode, transport=transport, base_url=base_url, model=model)
                return {"mode": mode, "source": "renegotiated", "fingerprint": fingerprint}

    raise RuntimeError("LLM_STRUCTURED_OUTPUT_UNAVAILABLE")
