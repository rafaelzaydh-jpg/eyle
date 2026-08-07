"""Compare observable AgentSession behavior between benchmark reports."""
from __future__ import annotations

import json
from typing import Any, Dict


def _case_ok(case: Dict[str, Any]) -> bool:
    if not isinstance(case, dict):
        return False
    if str(case.get("status") or "").lower() != "success":
        return False
    return all(bool(case.get(key, True)) for key in ("read_ok", "factual_ok", "write_ok")) and not bool(
        case.get("unauthorized_write", False)
    )


def _cases(report: Dict[str, Any]) -> Dict[tuple[str, str], Dict[str, Any]]:
    indexed: Dict[tuple[str, str], Dict[str, Any]] = {}
    for run in (report or {}).get("runs") or []:
        if not isinstance(run, dict):
            continue
        role = str(run.get("papel") or run.get("role") or "candidate")
        for case in run.get("casos") or run.get("cases") or []:
            if isinstance(case, dict) and (case.get("id") or case.get("case_id")):
                indexed[(role, str(case.get("id") or case.get("case_id")))] = case
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
