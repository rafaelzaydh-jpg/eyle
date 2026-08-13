"""Host assembly for the bundled Eyle distribution.

Core has no provider defaults. A Host chooses the capability body and creates
provider-owned context. Alternative products can construct their own Host with
PetBot, network, IoT or other providers without changing Core.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict

from eyle.capabilities import CapabilityRegistry, build_registry
from eyle.providers.standard import get_provider as get_standard_provider
from eyle.providers.memory import get_provider as get_memory_provider
from eyle.providers.standard_impl.workspace import discover_project


@dataclass(frozen=True)
class Host:
    registry: CapabilityRegistry
    context_factory: Callable[[], Dict[str, Any]]

    def provider_context(self) -> Dict[str, Any]:
        value = self.context_factory()
        if not isinstance(value, dict):
            raise ValueError("HOST_PROVIDER_CONTEXT_INVALID")
        return value


def build_bundled_host(base_dir: str) -> Host:
    root = os.path.realpath(base_dir)
    registry = build_registry([get_standard_provider(), get_memory_provider()])

    def context_factory() -> Dict[str, Any]:
        standard = discover_project(root)
        standard = standard if isinstance(standard, dict) else {}
        scope_root = standard.get("caminho_origem") or root
        return {
            "standard": standard,
            "memory": {
                "storage_dir": os.path.join(root, "memory"),
                "scope_root": scope_root,
            },
        }

    return Host(registry=registry, context_factory=context_factory)
