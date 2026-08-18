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
        short, fallback, family = _candidate_name(capability, registry)
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




def _compact_input_hint(value: Any) -> Any:
    """Return the minimal wire hint Main needs to author an argument.

    Capability schemas remain strict and provider-owned in the Registry.  The ECC
    catalog is a model-facing navigation surface, not a second copy of those
    schemas.  Repeated prose (especially source identity) is already taught once
    in the stable prompt and wastes paid context when duplicated per operation.
    """
    if not isinstance(value, str):
        return copy.deepcopy(value)
    text = value.strip()
    return text.split(" | ", 1)[0].strip() if " | " in text else text


def _compact_catalog_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project a capability contract into a terse model-facing operation card.

    Runtime still validates the full canonical capability schema.  This function
    only removes descriptive duplication that does not change what Main can call.
    """
    out: Dict[str, Any] = {"operation": str(item.get("operation") or "")}
    inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
    if inputs:
        out["inputs"] = {str(k): _compact_input_hint(v) for k, v in inputs.items()}
    # Purpose/returns prose lives in Registry/docs. Only behavior-changing
    # caveats remain on the wire, and are bounded because Runtime enforces the
    # full canonical contract independently.
    caveats = [str(v).strip() for v in (item.get("caveats") or []) if str(v).strip()]
    if caveats:
        out["caveats"] = [v[:180] for v in caveats[:2]]
    confirmation = str(item.get("confirmation") or "none")
    if confirmation != "none":
        out["confirmation"] = confirmation
    return out

def catalog(registry: Any, config: Dict[str, Any], available: Iterable[str], *, memory_enabled: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    allowed = {str(v) for v in available}
    contracts = {str(item.get("name")): item for item in registry.catalog(config=config, allowed_names=allowed)}
    out: Dict[str, Any] = {
        "guidance": [
            "Use source=workspace for user files; source=eyle only for the running Eyle source.",
            "Use sandboxed execution for substantial project work; promote the tested candidate instead of reconstructing it.",
            "Promotion merge preserves unstaged target files; mirror may delete target files absent from the staged subtree.",
        ],
        "explorar": [], "construir": [],
    }
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
    if memory_enabled:
        out["explorar"].extend([
            {
                "operation": "memory_overview",
                "purpose": "See the compact directory of the unified Memory Graph without loading node bodies.",
                "inputs": {"scope": "all|user|world|global?"},
                "returns": "Memory counts plus compact retention/epistemic/kind/tag directory and Coverage.",
            },
            {
                "operation": "memory_history",
                "purpose": "Inspect the complete persisted revision/event history and relations of one mem-* node so temporal change is not erased by the current projection.",
                "inputs": {"id": "mem-*"},
                "returns": "Current node, all persisted node events, relations and Coverage.",
            },
            {
                "operation": "memory_relation_history",
                "purpose": "Inspect the persisted revision/event history of one rel-* relation so relation confidence/context can evolve without losing history.",
                "inputs": {"id": "rel-*"},
                "returns": "Current relation, all persisted relation events and Coverage.",
            },
            {
                "operation": "memory_activate",
                "purpose": "Explicitly activate a Memory Graph region by query, exact mem-* IDs or tags. The requested page is materialized now; any remainder is exposed as Frontier and may be continued as many times as Main chooses.",
                "inputs": {"query": "string?", "queries": "string[]?", "ids": "mem-*[]?", "tags": "string[]?", "domain": "all|chat|task|eyle|knowledge?", "context_key": "physical context id?", "natures": "epistemic nature[]?", "volatilities": "epistemic volatility[]?", "relation_labels": "string[]?", "scope": "all|user|world|global?", "retention": "all|temporary|persistent?", "include_neighbors": "bool?", "limit": "positive page size?"},
                "returns": "Activates exact Memory bodies into memory_view; operation result itself stays compact. Coverage and optional fr-* Frontier are returned.",
                "caveats": ["Use semantic query/IDs/tags or exact physical domain/context_key filters. Runtime never adds topology/importance fallback."],
            },
        ])
        if not any(str(item.get("operation")) == "continue" for item in out["explorar"]):
            out["explorar"].append({
                "operation": "continue",
                "purpose": "Continue any open fr-* Frontier, including Memory or provider observation pages.",
                "inputs": {"frontier": "fr-*"},
                "returns": "The exact next page behind that Frontier plus Coverage and another Frontier if more remains.",
            })
    # The full provider schema stays in Registry; Main receives only the
    # operation name, purpose, terse wire hints and behavior-changing caveats.
    out["explorar"] = [_compact_catalog_item(item) for item in out["explorar"]]
    out["construir"] = [_compact_catalog_item(item) for item in out["construir"]]
    return out
