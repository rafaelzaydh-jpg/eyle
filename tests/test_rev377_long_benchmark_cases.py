from __future__ import annotations

from eyle.devtools import benchmark


def test_rev377_long_benchmark_cases_are_selectable():
    selected = benchmark.select_cases(["long_file_2k", "long_file_10k", "multi_file_long"])
    assert selected == ("long_file_2k", "long_file_10k", "multi_file_long")


def test_rev377_long_benchmark_fixtures_place_targets_beyond_first_page(tmp_path):
    request = benchmark._build_case(str(tmp_path), "long_file_2k")
    lines = (tmp_path / "long_2k.py").read_text().splitlines()
    assert len(lines) == 2000
    assert lines[1799] == "TARGET_LONG_2K = 'found-near-end'"
    assert "TARGET_LONG_2K" in request

    other = tmp_path / "ten"
    other.mkdir()
    benchmark._build_case(str(other), "long_file_10k")
    lines10 = (other / "long_10k.py").read_text().splitlines()
    assert len(lines10) == 10000
    assert lines10[9599] == "TARGET_LONG_10K = 'found-near-end'"
