"""Universal contracts shared by Eyle Core, Runtime and providers.

This package is deliberately below semantic Core and domain providers. It owns
only domain-neutral physical/data contracts.
"""
from .capability import RESULT_FIELDS, physical_effect, normalize_physical_effect, result, failure
from .observation import (
    CoverageContractError, normalize_coverage, normalize_effect, result_observation_fields,
    materialize_snapshot_handle, register_snapshot_handle, persisted_handles,
    persisted_snapshots, release_snapshot_handle,
)

__all__ = [
    "RESULT_FIELDS", "physical_effect", "normalize_physical_effect", "result", "failure",
    "CoverageContractError", "normalize_coverage", "normalize_effect", "result_observation_fields",
    "materialize_snapshot_handle", "register_snapshot_handle", "persisted_handles",
    "persisted_snapshots", "release_snapshot_handle",
]
