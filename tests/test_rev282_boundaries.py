from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import llm.executar as llm_exec
from eyle.core.memory import apply_memory_sidecar
from eyle.core.session import AgentSession
from eyle.runtime.config import ConfigError, validar_config
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.memory_graph import graph_counts
from eyle.runtime.token_budget import available_user_prompt_tokens
from llm.protocol import provider_policy
from tests.canonical import standard_registry


def _release_config() -> dict:
    root = Path(__file__).resolve().parent.parent
    return json.loads((root / "config.json").read_text(encoding="utf-8"))


def _memory_context(tmp_path: Path) -> dict:
    return {
        "standard": {"caminho_origem": str(tmp_path), "eyle_root": str(tmp_path)},
        "core_memory": {
            "storage_dir": str(tmp_path / "memory-store"),
            "world_scope_id": f"workspace:{tmp_path.resolve()}",
        },
    }


def test_rev282_default_has_only_execution_generated_token_fuse():
    cfg = _release_config()
    llm = cfg["llm"]
    assert llm["generated_token_fuse"] == 120_000
    assert llm["context_window_tokens"] is None
    assert llm["read_timeout_seconds"] is None
    for removed in ("max_tokens", "agent_max_tokens", "openai_compatible"):
        assert removed not in llm


def test_rev282_legacy_llm_config_fields_fail_closed():
    base = _release_config()
    registry = standard_registry()
    for field, value in (
        ("max_tokens", 1500),
        ("agent_max_tokens", 3600),
        ("openai_compatible", False),
    ):
        cfg = copy.deepcopy(base)
        cfg["llm"][field] = value
        with pytest.raises(ConfigError) as exc:
            validar_config(cfg, registry)
        assert "UNKNOWN_CONFIG_FIELD:llm" in str(exc.value)


def test_rev282_no_local_context_crop_by_default_but_test_window_still_available():
    cfg = _release_config()
    assert available_user_prompt_tokens(cfg, "system") is None
    cfg["llm"]["context_window_tokens"] = 1000
    budget = available_user_prompt_tokens(cfg, "system")
    assert isinstance(budget, int)
    assert 0 <= budget < 1000


def test_rev282_read_timeout_defaults_to_remaining_task_deadline_not_120_seconds():
    cfg = _release_config()
    execution = ExecutionContext.from_config(cfg)
    _connect, read, remaining = llm_exec._timeouts_da_chamada(cfg["llm"], "ecc", cfg, execution)
    assert remaining is not None
    assert read > 120
    assert read <= remaining


def test_rev282_eyle_boundary_is_adapter_openai_only():
    cfg = _release_config()
    assert provider_policy(cfg)["transport"] == "adapter_openai_chat"
    source = Path(llm_exec.__file__).read_text(encoding="utf-8").lower()
    for legacy in ("localhost:11434", "/api/chat", "def _chamar_ollama"):
        assert legacy not in source


def test_rev283_prompt_tells_main_to_search_memory_and_not_treat_it_as_truth():
    prompt = llm_exec.PROMPT_ECC.lower()
    assert "memory_view is a working view" in prompt
    assert "never the boundary of memory" in prompt
    assert "memory_overview" in prompt and "memory_activate" in prompt
    assert "not universal truth" in prompt
    assert "do not guess" in prompt
    assert "frontier is not a limit" in prompt


def test_rev282_temporary_memory_is_not_auto_archived_at_48_nodes(tmp_path):
    registry = standard_registry()
    context = _memory_context(tmp_path)
    session = AgentSession("seed temporary memory")
    for batch in range(4):
        delta = [
            {
                "op": "remember",
                "scope": "world",
                "retention": "temporary",
                "kind": "clue",
                "content": f"temporary clue {batch * 15 + i}",
                "supports": [{"kind": "request"}],
            }
            for i in range(15)
        ]
        result = apply_memory_sidecar(session, delta, registry=registry, provider_context=context)
        assert result["ok"] is True
    counts = graph_counts(context["core_memory"]["storage_dir"])
    assert counts["temporary_nodes"] == 60
