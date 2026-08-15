#!/usr/bin/env python3
"""Deterministic token-efficiency comparison for exact benchmark reports."""
from __future__ import annotations

import json

from eyle.devtools.benchmark_schema import TOKEN_USAGE_FIELDS, validate_report
from eyle.runtime.storage import salvar_json_atomico


def _case_index(report):
    validate_report(report)
    indexed = {}
    for run in report["runs"]:
        role = run["role"]
        for result in run["cases"]:
            indexed[(role, result["id"])] = {
                "usage": dict(result["token_usage"]),
                "status": result["status"],
            }
    return indexed


def _growth_exceeds(baseline, candidate, tolerance):
    if candidate <= baseline:
        return False
    if baseline == 0:
        return candidate > 0
    return candidate > baseline * (1.0 + float(tolerance))


def compare_token_efficiency_reports(baseline, candidate, *, tolerance=0.10):
    tolerance = max(0.0, float(tolerance))
    baseline_cases = _case_index(baseline)
    candidate_cases = _case_index(candidate)
    regressions = []
    comparisons = []

    for key, baseline_record in sorted(baseline_cases.items()):
        role, case_id = key
        candidate_record = candidate_cases.get(key)
        if candidate_record is None:
            regressions.append({
                "role": role,
                "case_id": case_id,
                "reason": "candidate_case_missing",
                "reasons": ["candidate_case_missing"],
            })
            continue
        before = baseline_record["usage"]
        after = candidate_record["usage"]
        reasons = []
        if after["llm_calls"] > before["llm_calls"]:
            reasons.append(f"llm_calls:{before['llm_calls']}->{after['llm_calls']}")
        if after["llm_requests"] > before["llm_requests"]:
            reasons.append(f"llm_requests:{before['llm_requests']}->{after['llm_requests']}")
        for field in ("prompt_tokens_effective", "completion_tokens_actual", "total_tokens_effective"):
            if _growth_exceeds(before[field], after[field], tolerance):
                reasons.append(f"{field}:{before[field]}->{after[field]}")
        comparison = {
            "role": role,
            "case_id": case_id,
            "baseline": before,
            "candidate": after,
            "ok": not reasons,
            "reasons": reasons,
        }
        comparisons.append(comparison)
        if reasons:
            regressions.append(comparison)

    extra_cases = sorted(set(candidate_cases) - set(baseline_cases))
    aggregate_before = {field: 0 for field in TOKEN_USAGE_FIELDS}
    aggregate_after = {field: 0 for field in TOKEN_USAGE_FIELDS}
    for record in baseline_cases.values():
        for field in TOKEN_USAGE_FIELDS:
            aggregate_before[field] += record["usage"][field]
    for key in baseline_cases:
        if key not in candidate_cases:
            continue
        for field in TOKEN_USAGE_FIELDS:
            aggregate_after[field] += candidate_cases[key]["usage"][field]

    return {
        "ok": not regressions,
        "tolerance": tolerance,
        "baseline_cases": len(baseline_cases),
        "candidate_cases": len(candidate_cases),
        "compared_cases": len(comparisons),
        "extra_candidate_cases": [f"{role}:{case_id}" for role, case_id in extra_cases],
        "baseline_totals": aggregate_before,
        "candidate_totals_for_baseline_cases": aggregate_after,
        "comparisons": comparisons,
        "regressions": regressions,
    }


def compare_token_efficiency_files(baseline_path, candidate_path, *, output_path=None, tolerance=0.10):
    with open(baseline_path, "r", encoding="utf-8") as file:
        baseline = json.load(file)
    with open(candidate_path, "r", encoding="utf-8") as file:
        candidate = json.load(file)
    result = compare_token_efficiency_reports(baseline, candidate, tolerance=tolerance)
    if output_path:
        salvar_json_atomico(output_path, result)
    return result
