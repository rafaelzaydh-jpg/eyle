"""The entire cognitive action vocabulary of Eyle ECC.

ECC exposes exactly three cognitive move types through the structured protocol:
``explorar``, ``construir`` and ``concluir``.  The ``type`` field is the single
source of truth for the family.  Operation names therefore contain *no* family
prefix (``search``, not ``explorar.search``; ``transaction``, not
``construir.transaction``).

Providers register deterministic capabilities. Runtime classifies their declared
physical effect mechanically:
- observe/execute -> Explorar
- mutate          -> Construir

Providers may publish an optional ``ecc_name`` presentation alias. Core never
knows provider identities or domain semantics.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Dict, Iterable, List


def capability_family(capability: str, registry: Any) -> str:
    """Return the mechanical ECC family declared by one registered capability."""
    effect = str(registry.spec(str(capability or "")).get("effect") or "observe")
    return "construir" if effect == "mutate" else "explorar"


def _candidate_name(capability: str, registry: Any) -> tuple[str, str, str]:
    spec = registry.spec(capability)
    provider = str(registry.provider_for(capability) or capability.split(".", 1)[0] or "provider")
    local = str(registry.local_name(capability) or (capability.split(".", 1)[1] if "." in capability else capability))
    family = capability_family(capability, registry)
    alias = str(spec.get("ecc_name") or "").strip()
    short = alias or f"{provider}.{local}"
    fallback = f"{provider}.{local}"
    return short, fallback, family


def _stable_public_names(registry: Any) -> Dict[str, str]:
    """Return provider-stable operation names, collision-safe within each family.

    Because ``type`` already carries the ECC family, an Explore operation and a
    Build operation may legitimately share the same short alias. Collisions are
    only ambiguous inside the same family and are then expanded to
    ``provider.local`` deterministically.
    """
    candidates: Dict[str, tuple[str, str, str]] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for capability in sorted({str(v) for v in registry.names()}):
        try:
            short, fallback, family = _candidate_name(capability, registry)
        except Exception:
            continue
        candidates[capability] = (short, fallback, family)
        counts[(family, short)] += 1
    return {
        capability: (short if counts[(family, short)] == 1 else fallback)
        for capability, (short, fallback, family) in candidates.items()
    }


def public_name(capability: str, registry: Any | None = None) -> str:
    capability = str(capability or "")
    if registry is None:
        return capability
    return _stable_public_names(registry).get(capability, capability)


def operation_map(registry: Any, available: Iterable[str], action_kind: str) -> Dict[str, str]:
    """Map one ECC family's public operation names to canonical capabilities."""
    family = str(action_kind or "")
    if family not in {"explorar", "construir"}:
        return {}
    allowed = {str(v) for v in available}
    stable = _stable_public_names(registry)
    out: Dict[str, str] = {}
    for capability in sorted(allowed):
        if capability_family(capability, registry) != family:
            continue
        if registry.spec(capability).get("ecc_hidden") is True:
            continue
        public = stable.get(capability)
        if public:
            out[public] = capability
    return out


def resolve(operation: str, action_kind: str, registry: Any, available: Iterable[str]) -> str | None:
    return operation_map(registry, available, action_kind).get(str(operation or "").strip())


def catalog(registry: Any, config: Dict[str, Any], available: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    allowed = {str(v) for v in available}
    contracts = {str(item.get("name")): item for item in registry.catalog(config=config, allowed_names=allowed)}
    out: Dict[str, Any] = {"guidance": list(registry.ecc_guidance(allowed)), "explorar": [], "construir": []}
    for family in ("explorar", "construir"):
        mapping = operation_map(registry, allowed, family)
        for public, internal in mapping.items():
            item = contracts.get(internal)
            if not item:
                continue
            clone = copy.deepcopy(item)
            clone["operation"] = public
            clone.pop("name", None)
            clone.pop("provider", None)
            spec = registry.spec(internal)
            if spec.get("ecc_purpose"):
                clone["purpose"] = str(spec.get("ecc_purpose"))
            if spec.get("ecc_returns"):
                clone["returns"] = str(spec.get("ecc_returns"))
            if isinstance(spec.get("ecc_caveats"), (list, tuple)):
                clone["caveats"] = [str(v) for v in spec.get("ecc_caveats") if str(v).strip()]
            if spec.get("ecc_require_explicit_source"):
                inputs = clone.get("inputs") if isinstance(clone.get("inputs"), dict) else {}
                source_contract = inputs.get("source")
                if isinstance(source_contract, str):
                    inputs["source"] = source_contract.replace("workspace|eyle?", "workspace|eyle")
                clone["inputs"] = inputs
            clone.pop("effect", None)
            if not clone.get("caveats"):
                clone.pop("caveats", None)
            if not clone.get("limits"):
                clone.pop("limits", None)
            if str(clone.get("confirmation") or "none") == "none":
                clone.pop("confirmation", None)
            out[family].append(clone)
    out["explorar"].append({
        "operation": "recall",
        "purpose": "Bring exact saved Evidence back into this turn.",
        "inputs": {"evidence_id": "ev-*"},
        "returns": "The saved Evidence and where it came from.",
        "caveats": ["Only Evidence from this run can be recalled."],
    })
    return out
