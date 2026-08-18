"""Host assembly for the bundled Eyle distribution.

Core has no provider defaults. A Host chooses the capability body and creates
provider-owned context. Alternative products can construct their own Host with
PetBot, network, IoT or other providers without changing Core.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from eyle.capabilities import CapabilityRegistry, build_registry
from eyle.providers.standard.registry import get_provider as get_standard_provider
from eyle.providers.standard.workspace import discover_project


@dataclass(frozen=True)
class Host:
    registry: CapabilityRegistry
    context_factory: Callable[[], Dict[str, Any]]
    describe_factory: Optional[Callable[[], Dict[str, Any]]] = None

    def provider_context(self) -> Dict[str, Any]:
        value = self.context_factory()
        if not isinstance(value, dict):
            raise ValueError("HOST_PROVIDER_CONTEXT_INVALID")
        return value

    def describe(self) -> Dict[str, Any]:
        """Return host-owned presentation/status data, never provider execution context.

        Runtime treats this as an opaque host description. Product shells may
        use fields that their own Host chooses to expose (for example the
        bundled workspace UI). Alternative Hosts can expose entirely different
        metadata without changing Runtime or Core.
        """
        if self.describe_factory is None:
            return {}
        value = self.describe_factory()
        if not isinstance(value, dict):
            raise ValueError("HOST_DESCRIPTION_INVALID")
        return value


def build_bundled_host(base_dir: str) -> Host:
    root = os.path.realpath(base_dir)
    registry = build_registry([get_standard_provider()])

    def workspace_context():
        value = discover_project(root)
        return value if isinstance(value, dict) else None

    def context_factory() -> Dict[str, Any]:
        standard = workspace_context() or {}
        observed_root = os.path.realpath(str(standard.get("caminho_origem") or root))
        return {
            "standard": standard,
            "core_memory": {
                "storage_dir": os.path.join(root, "memory"),
                # Host-defined opaque identity. Core never interprets the prefix.
                "world_scope_id": f"workspace:{observed_root}",
            },
        }

    def describe_factory() -> Dict[str, Any]:
        return {"workspace": workspace_context()}

    return Host(
        registry=registry,
        context_factory=context_factory,
        describe_factory=describe_factory,
    )
