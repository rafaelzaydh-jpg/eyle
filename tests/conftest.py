"""Shared pytest configuration for the canonical Eyle runtime."""
import pytest

from llm import capabilities as structured_capabilities


@pytest.fixture(autouse=True)
def _isolated_structured_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "EYLE_LLM_CAPABILITIES_PATH",
        str(tmp_path / "llm_capabilities.json"),
    )
    structured_capabilities.reset_process_cache()
    yield
    structured_capabilities.reset_process_cache()
