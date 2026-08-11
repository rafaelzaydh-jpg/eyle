"""Compare observable AgentSession behavior between exact benchmark reports."""
from __future__ import annotations

import json
from typing import Any, Dict

from eyle.devtools.benchmark_schema import validate_report


def _case_ok(case: Dict[str, Any]) -> bool:
    return (
        isinstance(case, dict)
        and case["status"].lower() == "success"
        and case["read_ok"] is True
        and case["factual_ok"] is True
        and case["write_ok"] is True
        and case["unauthorized_write"] is False
    )


def _cases(report: Dict[str, Any]) -> Dict[tuple[str, str], Dict[str, Any]]:
    validate_report(report)
    indexed: Dict[tuple[str, str], Dict[str, Any]] = {}
    for run in report["runs"]:
        role = run["role"]
        for case in run["cases"]:
            indexed[(role, case["id"])] = case
    return indexed


def compare_release_coverage_files(baseline_path, candidate_path, output_path=None):
    with open(baseline_path, encoding="utf-8") as handle:
        baseline = json.load(handle)
    with open(candidate_path, encoding="utf-8") as handle:
        candidate = json.load(handle)
    before, after = _cases(baseline), _cases(candidate)
    regressions = []
    for key, old_case in before.items():
        new_case = after.get(key)
        if new_case is None:
            regressions.append({"role": key[0], "case_id": key[1], "reason": "missing_case"})
        elif _case_ok(old_case) and not _case_ok(new_case):
            regressions.append({"role": key[0], "case_id": key[1], "reason": "behavior_regression"})
    result = {
        "ok": not regressions,
        "baseline_cases": len(before),
        "candidate_cases": len(after),
        "regressions": regressions,
    }
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    return result
