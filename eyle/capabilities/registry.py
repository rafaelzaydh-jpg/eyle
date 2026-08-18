"""Provider-owned capability registry and generic dispatch.

The registry is mechanical infrastructure. Providers register local capability
names; the registry exposes canonical ``provider.local`` IDs to Main. It owns
schema/effect/result coherence, never semantic routing.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping

from eyle.contracts.observation import CoverageContractError, normalize_coverage, normalize_effect
from eyle.contracts.capability import (
    RESULT_FIELDS, capability_public_contract, failure, normalize_physical_effect,
    validate_schema_value,
)

_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Provider:
    provider_id: str
    capabilities: Mapping[str, Dict[str, Any]]
    available: Callable[[str, Dict[str, Any], Dict[str, Any]], bool] | None = None
    describe: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None
    rehydrate: Callable[[Dict[str, Any], Dict[str, Any]], None] | None = None
    validate_config: Callable[[Dict[str, Any]], None] | None = None
    ecc_guidance: tuple[str, ...] = ()


class CapabilityRegistry:
    def __init__(self) -> None:
        self._providers: Dict[str, Provider] = {}
        # canonical id -> (provider, local id, normalized spec)
        self._index: Dict[str, tuple[Provider, str, Dict[str, Any]]] = {}

    @staticmethod
    def canonical_id(provider_id: str, local_name: str) -> str:
        return f"{provider_id}.{local_name}"

    def register(self, provider: Provider) -> None:
        provider_id = str(provider.provider_id or "").strip()
        if not provider_id or _PROVIDER_ID_RE.fullmatch(provider_id) is None or provider_id in self._providers:
            raise ValueError("CAPABILITY_PROVIDER_INVALID")
        staged: Dict[str, tuple[Provider, str, Dict[str, Any]]] = {}
        for raw_name, raw_spec in provider.capabilities.items():
            local_name = str(raw_name or "").strip()
            if not local_name or _LOCAL_ID_RE.fullmatch(local_name) is None or not isinstance(raw_spec, dict):
                raise ValueError(f"CAPABILITY_LOCAL_ID_INVALID:{local_name}")
            canonical = self.canonical_id(provider_id, local_name)
            if canonical in self._index or canonical in staged:
                raise ValueError(f"CAPABILITY_NAME_COLLISION:{canonical}")
            spec = copy.copy(raw_spec)
            if not str(spec.get("description") or "").strip():
                raise ValueError(f"CAPABILITY_DESCRIPTION_REQUIRED:{canonical}")
            if not isinstance(spec.get("input_schema"), dict):
                raise ValueError(f"CAPABILITY_SCHEMA_REQUIRED:{canonical}")
            if not str(spec.get("returns") or "").strip():
                raise ValueError(f"CAPABILITY_RETURNS_REQUIRED:{canonical}")
            for semantic_field in ("establishes", "does_not_establish"):
                if semantic_field not in spec:
                    continue
                value = spec.get(semantic_field)
                if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                    raise ValueError(f"CAPABILITY_CAUSAL_DESCRIPTION_INVALID:{canonical}:{semantic_field}")
                spec[semantic_field] = [v.strip() for v in value]
            ecc_name = str(spec.get("ecc_name") or "").strip()
            if ecc_name:
                if _LOCAL_ID_RE.fullmatch(ecc_name) is None:
                    raise ValueError(f"CAPABILITY_ECC_NAME_INVALID:{canonical}")
                spec["ecc_name"] = ecc_name
            raw_effect = str(spec.get("effect") or "observe").strip().lower()
            if raw_effect not in {"observe", "execute", "mutate"}:
                raise ValueError(f"CAPABILITY_EFFECT_INVALID:{canonical}")
            spec["effect"] = normalize_effect(raw_effect)
            confirmation = str(spec.get("confirmation") or "none").strip().lower()
            if confirmation not in {"none", "required"}:
                raise ValueError(f"CAPABILITY_CONFIRMATION_INVALID:{canonical}")
            spec["confirmation"] = confirmation
            if confirmation == "required":
                if not callable(spec.get("prepare")) or not callable(spec.get("confirm")):
                    raise ValueError(f"CAPABILITY_CONFIRMATION_HOOKS_REQUIRED:{canonical}")
            elif not callable(spec.get("fn")):
                raise ValueError(f"CAPABILITY_EXECUTOR_REQUIRED:{canonical}")
            staged[canonical] = (provider, local_name, spec)
        self._providers[provider_id] = provider
        self._index.update(staged)

    def validate_host_config(self, config: Dict[str, Any]) -> None:
        provider_cfg = (config or {}).get("providers") or {}
        if not isinstance(provider_cfg, dict):
            raise ValueError("PROVIDERS_CONFIG_INVALID")
        unknown = sorted(set(provider_cfg) - set(self._providers))
        if unknown:
            raise ValueError("PROVIDER_CONFIG_UNKNOWN:" + ",".join(unknown))
        for provider_id, provider in self._providers.items():
            validator = provider.validate_config
            if callable(validator):
                value = provider_cfg.get(provider_id) or {}
                if not isinstance(value, dict):
                    raise ValueError(f"PROVIDER_CONFIG_INVALID:{provider_id}")
                validator(value)

    def providers(self) -> list[str]:
        return list(self._providers)

    def names(self) -> list[str]:
        return list(self._index)

    def ecc_guidance(self, allowed_names: Iterable[str] | None = None) -> list[str]:
        """Return deduplicated provider-owned ECC guidance for the active surface."""
        allowed = None if allowed_names is None else {str(value) for value in allowed_names}
        active_provider_ids = set()
        if allowed is None:
            active_provider_ids.update(self._providers)
        else:
            for name in allowed:
                item = self._item(name)
                if item is not None:
                    active_provider_ids.add(item[0].provider_id)
        out = []
        seen = set()
        for provider_id in self._providers:
            if provider_id not in active_provider_ids:
                continue
            provider = self._providers[provider_id]
            for raw in provider.ecc_guidance or ():
                text = str(raw or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    out.append(text)
        return out

    def environment(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        providers: Dict[str, Any] = {}
        for provider_id, provider in self._providers.items():
            describe = provider.describe
            if not callable(describe):
                providers[provider_id] = {"connected": True}
                continue
            try:
                value = describe(ctx or {})
                providers[provider_id] = copy.deepcopy(value) if isinstance(value, dict) else {"connected": True}
            except Exception as exc:
                providers[provider_id] = {"connected": False, "error": type(exc).__name__}
        return {"providers": providers}

    def _item(self, name: str):
        return self._index.get(str(name or ""))

    def provider_for(self, name: str) -> str | None:
        item = self._item(name)
        return item[0].provider_id if item else None

    def local_name(self, name: str) -> str | None:
        item = self._item(name)
        return item[1] if item else None

    def spec(self, name: str) -> Dict[str, Any]:
        item = self._item(name)
        return item[2] if item else {}

    def available_names(self, ctx: Dict[str, Any], *, terminal: Iterable[str] = ()) -> set[str]:
        blocked = {str(v) for v in terminal}
        names: set[str] = set()
        for canonical, (provider, local_name, spec) in self._index.items():
            if canonical in blocked:
                continue
            predicate = provider.available
            if predicate is None or predicate(local_name, spec, ctx or {}):
                names.add(canonical)
        return names

    def catalog(self, config: Dict[str, Any] | None = None, allowed_names: Iterable[str] | None = None) -> list[Dict[str, Any]]:
        allowed = None if allowed_names is None else {str(v) for v in allowed_names}
        out = []
        for canonical, (provider, _local_name, spec) in self._index.items():
            if allowed is not None and canonical not in allowed:
                continue
            out.append(capability_public_contract(canonical, provider.provider_id, spec, config or {}))
        return out

    def validate(self, name: str, arguments: Any) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
        item = self._item(name)
        if item is None:
            return None, failure("CAPABILITY_NOT_FOUND", f"capability '{name}' is not registered")
        _, _, spec = item
        if not isinstance(arguments, dict):
            return None, failure("INVALID_ARGUMENT", "arguments must be a JSON object")
        schema = spec.get("input_schema")
        if not isinstance(schema, dict):
            return None, failure("INVALID_CAPABILITY_SCHEMA", f"capability '{name}' has no canonical input_schema")
        normalized = dict(arguments)
        error = validate_schema_value(normalized, schema, "arguments")
        if error:
            return None, failure("INVALID_ARGUMENT", error)
        normalizer = spec.get("normalize")
        if callable(normalizer):
            normalized = normalizer(normalized)
            if not isinstance(normalized, dict):
                return None, failure("INVALID_CAPABILITY_NORMALIZATION", f"capability '{name}' returned invalid normalized arguments")
        return normalized, None

    def requires_confirmation(self, name: str) -> bool:
        return str(self.spec(name).get("confirmation") or "none") == "required"

    def prepare_confirmation(self, name: str, arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        normalized, err = self.validate(name, arguments)
        if err is not None:
            return {"ok": False, "error": err}
        provider, _local_name, spec = self._index[str(name)]
        if str(spec.get("confirmation") or "none") != "required":
            return {"ok": False, "error": failure("CONFIRMATION_NOT_REQUIRED", f"capability '{name}' does not require confirmation")}
        prepare = spec.get("prepare")
        if not callable(prepare):
            return {"ok": False, "error": failure("CAPABILITY_PREPARE_MISSING", f"capability '{name}' has no prepare hook")}
        try:
            prepared = prepare(normalized, {**(ctx or {}), "registry": self})
        except Exception as exc:
            return {"ok": False, "error": failure("CAPABILITY_PREPARE_FAILED", f"capability '{name}' preparation failed: {exc}", executed=True)}
        if not isinstance(prepared, dict) or prepared.get("ok") is not True or not isinstance(prepared.get("state"), dict) or not str(prepared.get("question") or "").strip():
            error = prepared.get("error") if isinstance(prepared, dict) else None
            return {"ok": False, "error": error if isinstance(error, dict) else failure("CAPABILITY_PREPARE_INVALID", f"capability '{name}' returned invalid preparation")}
        return {
            "ok": True,
            "provider": provider.provider_id,
            "capability": str(name),
            "arguments": normalized,
            "question": str(prepared["question"]).strip(),
            "state": copy.deepcopy(prepared["state"]),
        }

    def cancel_confirmation(self, name: str, state: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Release capability-owned prepared state without authorizing mutation.

        Cancellation is mechanical cleanup only. A provider may omit the hook
        when preparation created no durable temporary resource.
        """
        item = self._item(name)
        if item is None:
            return {"ok": False, "error_code": "CAPABILITY_NOT_FOUND"}
        _, _, spec = item
        cancel = spec.get("cancel")
        if not callable(cancel):
            return {"ok": True, "cleanup": "not_required"}
        try:
            raw = cancel(copy.deepcopy(state or {}), {**(ctx or {}), "registry": self})
        except Exception as exc:
            return {"ok": False, "error_code": "CAPABILITY_CANCEL_FAILED", "detail": str(exc)[:300]}
        if raw is None:
            return {"ok": True, "cleanup": "completed"}
        if isinstance(raw, dict):
            return copy.deepcopy(raw)
        return {"ok": False, "error_code": "CAPABILITY_CANCEL_INVALID"}

    def confirm(self, name: str, state: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        item = self._item(name)
        if item is None:
            return failure("CAPABILITY_NOT_FOUND", f"capability '{name}' is not registered")
        _, _, spec = item
        confirm = spec.get("confirm")
        if not callable(confirm):
            return failure("CAPABILITY_CONFIRM_MISSING", f"capability '{name}' has no confirm hook")
        try:
            raw = confirm(copy.deepcopy(state or {}), {**(ctx or {}), "registry": self})
        except Exception as exc:
            return failure("CAPABILITY_CONFIRM_FAILED", f"capability '{name}' confirmation failed: {exc}", executed=True)
        return self._finalize_result(str(name), {}, raw, ctx or {})

    def _effect_contract_error(self, name: str, spec: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any] | None:
        effect_class = str(spec.get("effect") or "observe")
        executed = result.get("executed") is True
        changed = result.get("changed") is True
        try:
            physical = normalize_physical_effect(result.get("physical_effect"))
        except ValueError as exc:
            return failure("CAPABILITY_PHYSICAL_EFFECT_INVALID", f"capability '{name}' returned invalid physical_effect: {exc}", executed=executed)
        result["physical_effect"] = physical
        if changed and not executed:
            return failure("CAPABILITY_EFFECT_INCOHERENT", f"capability '{name}' reports changed=true without executed=true", executed=executed)
        if physical is not None and bool(physical.get("changed")) != changed:
            return failure("CAPABILITY_EFFECT_INCOHERENT", f"capability '{name}' changed flag disagrees with physical_effect.changed", executed=executed)
        if effect_class == "observe":
            if changed or physical is not None:
                return failure("CAPABILITY_EFFECT_CONTRACT_VIOLATION", f"observe capability '{name}' cannot report a physical effect or mutation", executed=executed)
        elif effect_class == "execute":
            if changed:
                return failure("CAPABILITY_EFFECT_CONTRACT_VIOLATION", f"execute capability '{name}' cannot report persistent world mutation via changed=true", executed=executed)
        elif effect_class == "mutate":
            if changed and physical is None:
                return failure("CAPABILITY_EFFECT_REQUIRED", f"mutate capability '{name}' changed state without a physical_effect", executed=executed, changed=False)
        return None

    def _finalize_result(self, name: str, normalized: Dict[str, Any], raw_result: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        item = self._item(name)
        if item is None:
            return failure("CAPABILITY_NOT_FOUND", f"capability '{name}' is not registered")
        provider, _local_name, spec = item
        if not isinstance(raw_result, dict) or set(raw_result) != set(RESULT_FIELDS):
            return failure("INVALID_CAPABILITY_RESULT", f"capability '{name}' returned a non-canonical result", executed=True)
        result = copy.deepcopy(raw_result)
        coherence_error = self._effect_contract_error(name, spec, result)
        if coherence_error is not None:
            return coherence_error
        observer = spec.get("observe")
        values = observer(normalized, result) if callable(observer) else []
        observations = []
        for raw in values or []:
            if not isinstance(raw, dict):
                continue
            material = copy.deepcopy(raw)
            source_capability = str(material.get("source_capability") or name).strip()
            if "." not in source_capability:
                source_capability = self.canonical_id(provider.provider_id, source_capability)
            material["source_capability"] = source_capability
            material.setdefault("source_type", source_capability)
            material["source_provider"] = provider.provider_id
            observations.append(material)
        if observations:
            result["observations"] = observations
        coverage_hook = spec.get("coverage")
        try:
            coverage = coverage_hook(normalized, result) if callable(coverage_hook) else result.get("coverage")
            result["coverage"] = normalize_coverage(coverage, allow_empty=True)
        except CoverageContractError as exc:
            return failure(
                "CAPABILITY_COVERAGE_INVALID",
                f"capability '{name}' violated Coverage contract: {exc}",
                executed=bool(result.get("executed")), retryable=False,
            )
        frontier = spec.get("frontier")
        if callable(frontier):
            result["frontiers"] = [copy.deepcopy(v) for v in (frontier(normalized, result) or []) if isinstance(v, dict)]
        return result

    def execute(self, name: str, arguments: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        normalized, err = self.validate(name, arguments)
        if err is not None:
            return err
        _, _, spec = self._index[str(name)]
        if str(spec.get("confirmation") or "none") == "required":
            return failure("CAPABILITY_CONFIRMATION_REQUIRED", f"capability '{name}' must be prepared and confirmed before execution")
        try:
            raw = spec["fn"](normalized, {**(ctx or {}), "registry": self})
        except Exception as exc:
            return failure("CAPABILITY_EXECUTION_ERROR", f"capability '{name}' failed: {exc}", executed=True)
        return self._finalize_result(str(name), normalized, raw, ctx or {})

    def hook(self, name: str, hook_name: str) -> Any:
        return self.spec(name).get(hook_name)

    def observation_signature(self, name: str, arguments: Dict[str, Any]) -> str | None:
        hook = self.hook(name, "signature")
        if callable(hook):
            signature = hook(arguments or {})
            return f"{name}:{signature}" if signature else None
        effect = str(self.spec(name).get("effect") or "observe")
        if effect != "observe":
            return None
        return json.dumps({"capability": str(name), "arguments": arguments or {}}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def public_arguments(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        hook = self.hook(name, "public_arguments")
        if callable(hook):
            value = hook(arguments or {})
            return dict(value) if isinstance(value, dict) else {}
        return copy.deepcopy(arguments or {})

    def public_result(self, name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        result = result if isinstance(result, dict) else {}
        base = {key: result.get(key) for key in ("status", "ok", "executed", "changed", "error_code", "retryable", "failure_scope", "failure_resource", "physical_effect") if result.get(key) is not None}
        detail = result.get("detail")
        hook = self.hook(name, "public_result")
        if callable(hook):
            value = hook(result)
            if isinstance(value, dict):
                base.update(value)
        elif isinstance(detail, str):
            base["detail"] = detail[:500]
        return base

    def model_detail(self, name: str, detail: Any, grounding_ids: list[str], config: Dict[str, Any]) -> Any:
        hook = self.hook(name, "model_projection")
        if callable(hook):
            return hook(detail, grounding_ids, config or {})
        value = copy.deepcopy(detail)
        if isinstance(value, dict) and grounding_ids:
            value["grounding_id"] = grounding_ids[0]
        return value

    def select_evidence(self, material: Dict[str, Any], selector: Dict[str, Any]) -> Dict[str, Any]:
        """Validate one Main-selected EvidenceSpan through its provider contract."""
        if not isinstance(material, dict) or not isinstance(selector, dict):
            raise ValueError("EVIDENCE_SELECTOR_INVALID")
        source_capability = str(material.get("source_capability") or "")
        item = self._item(source_capability)
        if item is None and "." in source_capability:
            item = self._item(source_capability)
        hook = item[2].get("evidence_selector") if item is not None else None
        if callable(hook):
            value = hook(material, selector)
            if not isinstance(value, dict):
                raise ValueError("EVIDENCE_SELECTOR_INVALID")
            return value
        if selector:
            raise ValueError("EVIDENCE_SELECTOR_UNSUPPORTED")
        return {
            "locator": copy.deepcopy(material.get("locator") or {}),
            "content_hash": str(material.get("content_hash") or ""),
        }


    def freshness_arguments(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Return the opaque arguments needed to revalidate a provider token later.

        Providers may narrow this with ``freshness_arguments``. Runtime does not
        interpret keys such as path, device, router, workbook or sensor.
        """
        hook = self.hook(name, "freshness_arguments")
        if callable(hook):
            value = hook(copy.deepcopy(arguments or {}))
            return copy.deepcopy(value) if isinstance(value, dict) else {}
        return {}

    def freshness_token(self, name: str, arguments: Dict[str, Any], ctx: Dict[str, Any]) -> str | None:
        """Return provider-owned physical state token for cache validation.

        This is mechanical identity only. Providers may fingerprint a source tree,
        file, index, device state, etc. A missing hook means no extra token.
        """
        hook = self.hook(name, "freshness_token")
        if not callable(hook):
            return None
        value = hook(arguments or {}, ctx or {})
        text = str(value or "").strip()
        return text or None

    def material_freshness(
        self, material: Dict[str, Any], ctx: Dict[str, Any], *, token_cache: Dict[str, str | None] | None = None,
    ) -> tuple[bool, str]:
        """Validate one retained Material against provider-owned physical state.

        Exact material freshness is preferred over broad source tokens. This lets a
        fact about an unchanged file survive an unrelated write elsewhere while
        aggregate/negative observations still depend on the whole-source token.
        The reason distinguishes an actual physical proof from epoch-only validity.
        """
        if not isinstance(material, dict):
            return False, "material_unavailable"
        name = str(material.get("source_capability") or "")
        if self._item(name) is None:
            return False, "capability_unavailable"

        hook = self.hook(name, "freshness")
        if callable(hook):
            try:
                fresh, reason = hook(material, ctx or {})
            except Exception as exc:
                material["stale"] = True
                return False, f"freshness_error:{type(exc).__name__}"
            if fresh is False:
                material["stale"] = True
                return False, str(reason or "stale")
            if fresh is True:
                material.pop("stale", None)
                return True, "exact_current"
            # None means this exact-material hook does not apply; fall through to
            # a provider source-state token if one exists.

        token_hook = self.hook(name, "freshness_token")
        if callable(token_hook):
            expected = str(material.get("freshness_token") or "").strip()
            if not expected:
                material["stale"] = True
                return False, "freshness_token_missing"
            token_args = material.get("freshness_arguments") if isinstance(material.get("freshness_arguments"), dict) else {}
            cache_key = json.dumps({"capability": name, "arguments": token_args}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            if token_cache is not None and cache_key in token_cache:
                current = token_cache[cache_key]
            else:
                current = self.freshness_token(name, token_args, ctx or {})
                if token_cache is not None:
                    token_cache[cache_key] = current
            if not current or current != expected:
                material["stale"] = True
                return False, "physical_source_changed"
            material.pop("stale", None)
            return True, "token_current"

        material.pop("stale", None)
        return True, "no_freshness_contract"

    def entry_freshness(
        self, name: str, entry: Dict[str, Any], materials: Dict[str, Any], ctx: Dict[str, Any],
    ) -> tuple[bool, str]:
        """Validate cached grounding against provider-owned physical freshness.

        A cache entry with no grounding has no provider-declared freshness proof and
        remains eligible only under the current Runtime reality epoch. Grounded
        entries must pass every available material freshness hook before replay.
        """
        token_hook = self.hook(name, "freshness_token")
        if callable(token_hook):
            expected = str(entry.get("freshness_token") or "").strip()
            if not expected:
                return False, "freshness_token_missing"
            current = self.freshness_token(name, dict(entry.get("arguments") or {}), ctx or {})
            if not current or current != expected:
                return False, "physical_source_changed"

        hook = self.hook(name, "freshness")
        if not callable(hook):
            return True, "token_ok" if callable(token_hook) else "no_hook"
        grounding_ids = [str(v) for v in (entry.get("grounding_ids") or []) if str(v)]
        for material_id in grounding_ids:
            material = materials.get(material_id) if isinstance(materials, dict) else None
            if not isinstance(material, dict):
                return False, "material_unavailable"
            try:
                fresh, reason = hook(material, ctx or {})
            except Exception as exc:
                return False, f"freshness_error:{type(exc).__name__}"
            if fresh is False:
                material["stale"] = True
                return False, str(reason or "stale")
            if fresh is True:
                material.pop("stale", None)
        return True, "ok"

    def find_covering(self, name: str, arguments: Dict[str, Any], entries: Dict[str, Any], reality_epoch: int):
        hook = self.hook(name, "covers")
        return hook(arguments or {}, entries or {}, reality_epoch) if callable(hook) else None

    def find_resource_failure(self, name: str, arguments: Dict[str, Any], entries: Dict[str, Any], reality_epoch: int):
        hook = self.hook(name, "resource_failure")
        return hook(arguments or {}, entries or {}, reality_epoch) if callable(hook) else None

    def rehydrate_materials(self, grounding: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        materials = grounding if isinstance(grounding, dict) else {}
        by_provider: Dict[str, Dict[str, Any]] = {}
        for material_id, material in materials.items():
            if not isinstance(material, dict):
                continue
            provider_id = str(material.get("source_provider") or "")
            if not provider_id:
                provider_id = str(self.provider_for(str(material.get("source_capability") or "")) or "")
            if provider_id:
                by_provider.setdefault(provider_id, {})[str(material_id)] = material
        for provider_id, subset in by_provider.items():
            provider = self._providers.get(provider_id)
            hook = provider.rehydrate if provider is not None else None
            if callable(hook):
                hook(subset, ctx or {})
