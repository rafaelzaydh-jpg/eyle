#!/usr/bin/env python3
"""Comparação determinística de eficiência de tokens entre releases.

A comparação trabalha por papel + caso de benchmark. Ela não substitui os gates
factuais e de preservação: apenas impede que uma versão nova aumente chamadas ou
consumo de contexto sem uma decisão explícita.
"""
from __future__ import annotations

import json

from eyle.runtime.persistence import salvar_json_atomico


_COUNTER_FIELDS = (
    "llm_calls",
    "llm_requests",
    "prompt_tokens_effective",
    "completion_tokens_actual",
    "total_tokens_effective",
)


def _integer(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_from_result(result):
    result = result or {}
    usage = result.get("token_usage") if isinstance(result.get("token_usage"), dict) else {}
    responses = result.get("llm_responses") or []
    prompt_from_responses = sum(_integer(item.get("prompt_tokens")) for item in responses if isinstance(item, dict))
    completion_from_responses = sum(
        _integer(item.get("completion_tokens")) for item in responses if isinstance(item, dict)
    )
    calls = _integer(usage.get("llm_calls", result.get("llm_calls", len(responses))))
    requests = _integer(usage.get("llm_requests", calls))
    prompt = _integer(usage.get("prompt_tokens_effective", prompt_from_responses))
    completion = _integer(usage.get("completion_tokens_actual", completion_from_responses))
    total = _integer(usage.get("total_tokens_effective", prompt + completion))
    return {
        "llm_calls": calls,
        "llm_requests": requests,
        "prompt_tokens_effective": prompt,
        "completion_tokens_actual": completion,
        "total_tokens_effective": total,
    }


def _case_index(report):
    indexed = {}
    for run in (report or {}).get("runs") or []:
        if not isinstance(run, dict):
            continue
        role = str(run.get("papel") or run.get("role") or "principal")
        for result in run.get("casos") or run.get("cases") or run.get("resultados") or []:
            if not isinstance(result, dict) or not result.get("id"):
                continue
            indexed[(role, str(result["id"]))] = {
                "usage": _usage_from_result(result),
                "status": result.get("status"),
            }
    return indexed


def _growth_exceeds(baseline, candidate, tolerance):
    baseline = _integer(baseline)
    candidate = _integer(candidate)
    if candidate <= baseline:
        return False
    if baseline == 0:
        return candidate > 0
    return candidate > baseline * (1.0 + float(tolerance))


def compare_token_efficiency_reports(baseline, candidate, *, tolerance=0.10):
    """Compara relatórios e lista regressões por caso.

    Chamadas lógicas e requests de backend não possuem tolerância: qualquer
    aumento é regressão. Tokens recebem tolerância relativa para acomodar
    pequenas variações de tokenização/provedor.
    """
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
    aggregate_before = {field: 0 for field in _COUNTER_FIELDS}
    aggregate_after = {field: 0 for field in _COUNTER_FIELDS}
    for record in baseline_cases.values():
        for field in _COUNTER_FIELDS:
            aggregate_before[field] += record["usage"][field]
    for key in baseline_cases:
        if key not in candidate_cases:
            continue
        for field in _COUNTER_FIELDS:
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
