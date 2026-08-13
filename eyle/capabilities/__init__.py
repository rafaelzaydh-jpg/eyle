"""Universal capability boundary used by Eyle Core.

This package never chooses providers. A host assembles a CapabilityRegistry and
injects it into Runtime/Core for each run.
"""
from __future__ import annotations

from collections.abc import Iterable

from eyle.contracts.capability import RESULT_FIELDS, physical_effect
from .registry import CapabilityRegistry, Provider


def build_registry(providers: Iterable[Provider] = ()) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


__all__ = ["CapabilityRegistry", "Provider", "RESULT_FIELDS", "physical_effect", "build_registry"]
