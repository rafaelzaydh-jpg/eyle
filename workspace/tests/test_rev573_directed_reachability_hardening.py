from __future__ import annotations

from pathlib import Path

import eyle.core.tools as tools
from eyle.core.observation import observation_signature
from tests.canonical import base_config


def _ctx(root: Path, *, handles=None):
    return {
        "projeto": {"caminho_origem": str(root)},
        "config": base_config(),
        "observation_handles": handles if handles is not None else {},
        "workspace_epoch": 0,
    }


def test_reachability_auto_depth_ignores_llm_tuning_and_finds_long_path(tmp_path):
    funcs = []
    for index in range(40):
        nxt = f"f{index + 1}()" if index < 39 else "target()"
        funcs.append(f"def f{index}():\n    {nxt}\n")
    (tmp_path / "main.py").write_text(
        "from target import target\n\n" + "\n".join(funcs) +
        "\ndef main():\n    f0()\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")

    result = tools.executar_tool(
        "symbol_relations",
        {"symbol": "target", "query": "reachability", "max_depth": 3, "max_edges": 20},
        _ctx(tmp_path),
    )
    assert result["ok"] is True
    assert result["coverage"]["objective_complete"] is True
    assert result["coverage"]["objective_result"] == "reachable"
    assert result["coverage"]["depth_mode"] == "auto_exhaustive"
    assert result["coverage"]["shortest_path_hops"] > 32
    assert not any(item.get("kind") == "depth_boundary" for item in result["frontiers"])


def test_reachability_signature_collapses_depth_and_edge_tuning():
    low = observation_signature("symbol_relations", {
        "symbol": "target", "query": "reachability", "max_depth": 3, "max_edges": 20,
    })
    high = observation_signature("symbol_relations", {
        "symbol": "target", "query": "reachability", "max_depth": 32, "max_edges": 500,
    })
    assert low == high


def test_reachability_validation_canonicalizes_depth_and_edges():
    normalized, error = tools.validar_chamada_tool("symbol_relations", {
        "symbol": "target", "query": "reachability", "max_depth": 5, "max_edges": 20,
    })
    assert error is None
    assert "max_depth" not in normalized
    assert "max_edges" not in normalized


def test_expand_observation_rejects_non_handle_namespace_before_execution(tmp_path):
    result = tools.executar_tool("expand_observation", {"handle": "ev-0004"}, _ctx(tmp_path))
    assert result["ok"] is False
    assert result["executed"] is False
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_missing_current_handle_does_not_make_capability_terminal(tmp_path):
    result = tools.executar_tool(
        "expand_observation", {"handle": "handle:missing:deadbeef"}, _ctx(tmp_path, handles={}),
    )
    assert result["ok"] is False
    assert result["error_code"] == "HANDLE_NOT_FOUND"
    assert result["retryable"] is True
