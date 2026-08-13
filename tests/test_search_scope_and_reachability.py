from __future__ import annotations
from tests.canonical import standard_registry

import eyle.providers.standard as tools
from eyle.providers.standard_impl.code_relations import analyze_symbol_relations
from eyle.providers.standard import capability_observation_signature as observation_signature
from tests.canonical import base_config


def _ctx(root):
    return {"provider_context": {"standard": {"caminho_origem": str(root)}}, "config": base_config(), "reality_epoch": 0}


def test_literal_directory_scope_is_recursive_and_coverage_is_physical(tmp_path):
    (tmp_path / "eyle" / "core" / "nested").mkdir(parents=True)
    (tmp_path / "eyle" / "runtime").mkdir(parents=True)
    (tmp_path / "eyle" / "core" / "a.py").write_text("needle = 1\n", encoding="utf-8")
    (tmp_path / "eyle" / "core" / "nested" / "b.py").write_text("needle = 2\n", encoding="utf-8")
    (tmp_path / "eyle" / "runtime" / "c.py").write_text("needle = 3\n", encoding="utf-8")

    result = standard_registry().execute("search_code", {"query": "needle", "include_paths": ["eyle/core"]}, _ctx(tmp_path))
    assert result["ok"] is True
    detail = result["detail"]
    assert detail["matches_observed"] == 2
    assert set(detail["materialized_files"]) == {"eyle/core/a.py", "eyle/core/nested/b.py"}
    assert detail["search_scope"]["files_resolved"] == 2
    assert detail["search_scope"]["files_scanned"] == 2
    assert detail["coverage_complete"] is True


def test_literal_file_exact_directory_exclusion_recursive_and_glob_explicit(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "src" / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "tests" / "nested" / "test_b.py").write_text("needle\n", encoding="utf-8")

    exact = standard_registry().execute("search_code", {"query": "needle", "include_paths": ["src/a.py"]}, _ctx(tmp_path))["detail"]
    assert exact["materialized_files"] == ["src/a.py"]
    excluded = standard_registry().execute("search_code", {"query": "needle", "exclude_paths": ["tests"]}, _ctx(tmp_path))["detail"]
    assert set(excluded["materialized_files"]) == {"src/a.py", "src/b.py", "src/a.txt"}
    globbed = standard_registry().execute("search_code", {"query": "needle", "include_paths": ["src/*.py"]}, _ctx(tmp_path))["detail"]
    assert set(globbed["materialized_files"]) == {"src/a.py", "src/b.py"}
    assert globbed["search_scope"]["include_resolution"][0]["kind"] == "glob"


def test_missing_unsafe_and_capability_excluded_scope_fail_closed(tmp_path):
    (tmp_path / "app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("needle\n", encoding="utf-8")
    cases = [
        ({"query": "needle", "include_paths": ["does/not/exist"]}, "SEARCH_SCOPE_PATH_NOT_FOUND"),
        ({"query": "needle", "include_paths": ["../outside"]}, "SEARCH_SCOPE_PATH_UNSAFE"),
        ({"query": "needle", "include_paths": ["node_modules"]}, "SEARCH_SCOPE_OUTSIDE_CAPABILITY_BOUNDARY"),
    ]
    for args, code in cases:
        result = standard_registry().execute("search_code", args, _ctx(tmp_path))
        assert result["ok"] is False
        assert result["executed"] is False
        assert result["error_code"] == code


def test_scope_counts_protected_files_before_read_boundary(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "pkg" / ".env").write_text("needle=secret\n", encoding="utf-8")
    detail = standard_registry().execute("search_code", {"query": "needle", "include_paths": ["pkg"]}, _ctx(tmp_path))["detail"]
    assert detail["search_scope"]["files_resolved"] == 2
    assert detail["search_scope"]["files_scanned"] == 1
    assert detail["search_scope"]["protected_files"] == 1
    assert detail["coverage_complete"] is False


def test_search_identity_canonicalizes_scope_spelling():
    a = observation_signature("search_code", {"query": "x", "include_paths": ["./eyle/core/"]})
    b = observation_signature("search_code", {"query": "x", "include_paths": ["eyle/core"]})
    c = observation_signature("search_code", {"query": "x", "include_paths": ["eyle/runtime"]})
    assert a == b
    assert a != c


def test_reachability_resolves_import_binding_before_duplicate_global_name(tmp_path):
    (tmp_path / "left.py").write_text("def bridge():\n    return 'left'\n", encoding="utf-8")
    (tmp_path / "right.py").write_text("def bridge():\n    return 'right'\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from left import bridge\n\ndef main():\n    bridge()\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8",
    )
    result = analyze_symbol_relations(str(tmp_path), "bridge", query="reachability", max_depth=6, max_edges=20)
    assert result["coverage"]["objective_complete"] is True
    path = result["observations"][0]["path"]
    assert path[-1] == "left.py::bridge"
    assert "right.py::bridge" not in path


def test_reachability_is_exhaustive_not_llm_depth_tuned(tmp_path):
    funcs = []
    for index in range(40):
        nxt = f"f{index + 1}()" if index < 39 else "target()"
        funcs.append(f"def f{index}():\n    {nxt}\n")
    (tmp_path / "main.py").write_text(
        "from target import target\n\n" + "\n".join(funcs) +
        "\ndef main():\n    f0()\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8",
    )
    (tmp_path / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    result = standard_registry().execute("symbol_relations", {"symbol": "target", "query": "reachability", "max_depth": 3, "max_edges": 20}, _ctx(tmp_path))
    assert result["ok"] is True
    assert result["coverage"]["facts"]["objective_result"] == "reachable"
    assert result["coverage"]["facts"]["depth_mode"] == "auto_exhaustive"
    assert result["coverage"]["facts"]["shortest_path_hops"] > 32


def test_reachability_identity_and_validation_drop_tuning_knobs():
    low = observation_signature("symbol_relations", {"symbol": "target", "query": "reachability", "max_depth": 3, "max_edges": 20})
    high = observation_signature("symbol_relations", {"symbol": "target", "query": "reachability", "max_depth": 32, "max_edges": 500})
    assert low == high
    normalized, error = standard_registry().validate("symbol_relations", {"symbol": "target", "query": "reachability", "max_depth": 5, "max_edges": 20})
    assert error is None
    assert "max_depth" not in normalized and "max_edges" not in normalized
