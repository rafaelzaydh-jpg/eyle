"""Exact benchmark artifact contract shared by producer and comparators."""
from __future__ import annotations

from typing import Any, Dict


BENCHMARK_SCHEMA_VERSION = "1"
TOKEN_USAGE_FIELDS = (
    "llm_calls",
    "llm_requests",
    "prompt_tokens_effective",
    "completion_tokens_actual",
    "total_tokens_effective",
)
CASE_FIELDS = {
    "id", "status", "response", "tools", "read_ok", "factual_ok", "write_ok",
    "confirmation_requested", "unauthorized_write", "latency_ms", "token_usage",
    "failure_code",
}
METRIC_FIELDS = {
    "gate_passed", "gate_scope", "total_cases", "correct_read_tasks",
    "factual_answers_correct", "write_checks_passed", "write_checks_total",
    "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
}
RUN_FIELDS = {"role", "model", "cases", "metrics"}
REPORT_FIELDS = {"benchmark_schema_version", "revision", "cases", "runs"}


class BenchmarkSchemaError(ValueError):
    pass


def _exact_keys(value: Dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}:" + ";".join(detail))


def canonical_token_usage(usage: Any) -> Dict[str, int]:
    source = usage if isinstance(usage, dict) else {}
    out: Dict[str, int] = {}
    for field in TOKEN_USAGE_FIELDS:
        value = source.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            try:
                value = max(0, int(value or 0))
            except (TypeError, ValueError):
                value = 0
        out[field] = int(value)
    return out


def validate_report(report: Any) -> Dict[str, Any]:
    if not isinstance(report, dict):
        raise BenchmarkSchemaError("BENCHMARK_SCHEMA_INVALID:root:not_object")
    _exact_keys(report, REPORT_FIELDS, "root")
    if report["benchmark_schema_version"] != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkSchemaError("BENCHMARK_SCHEMA_INCOMPATIBLE")
    if not isinstance(report["revision"], str) or not report["revision"].strip():
        raise BenchmarkSchemaError("BENCHMARK_SCHEMA_INVALID:revision")
    if not isinstance(report["cases"], list) or not all(isinstance(item, str) and item for item in report["cases"]):
        raise BenchmarkSchemaError("BENCHMARK_SCHEMA_INVALID:cases")
    if not isinstance(report["runs"], list):
        raise BenchmarkSchemaError("BENCHMARK_SCHEMA_INVALID:runs")

    for run_index, run in enumerate(report["runs"]):
        label = f"runs[{run_index}]"
        if not isinstance(run, dict):
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}:not_object")
        _exact_keys(run, RUN_FIELDS, label)
        if run["role"] not in {"candidate", "baseline"}:
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.role")
        if not isinstance(run["model"], str) or not run["model"].strip():
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.model")
        if not isinstance(run["cases"], list):
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.cases")
        metrics = run["metrics"]
        if not isinstance(metrics, dict):
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.metrics")
        _exact_keys(metrics, METRIC_FIELDS, f"{label}.metrics")
        if not isinstance(metrics["gate_passed"], bool) or metrics["gate_scope"] not in {"full", "smoke"}:
            raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.metrics")
        for field in ("total_cases", "correct_read_tasks", "factual_answers_correct", "write_checks_passed", "write_checks_total"):
            if not isinstance(metrics[field], int) or isinstance(metrics[field], bool) or metrics[field] < 0:
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.metrics.{field}")
        for field in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms"):
            if not isinstance(metrics[field], (int, float)) or isinstance(metrics[field], bool) or metrics[field] < 0:
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{label}.metrics.{field}")

        for case_index, case in enumerate(run["cases"]):
            case_label = f"{label}.cases[{case_index}]"
            if not isinstance(case, dict):
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}:not_object")
            _exact_keys(case, CASE_FIELDS, case_label)
            if not isinstance(case["id"], str) or not case["id"]:
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.id")
            if not isinstance(case["status"], str) or not isinstance(case["response"], str):
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.status_or_response")
            if not isinstance(case["tools"], list) or not all(isinstance(tool, str) for tool in case["tools"]):
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.tools")
            for field in ("read_ok", "factual_ok", "write_ok", "confirmation_requested", "unauthorized_write"):
                if not isinstance(case[field], bool):
                    raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.{field}")
            if not isinstance(case["latency_ms"], (int, float)) or isinstance(case["latency_ms"], bool) or case["latency_ms"] < 0:
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.latency_ms")
            usage = case["token_usage"]
            if not isinstance(usage, dict) or set(usage) != set(TOKEN_USAGE_FIELDS):
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.token_usage")
            for field in TOKEN_USAGE_FIELDS:
                if not isinstance(usage[field], int) or isinstance(usage[field], bool) or usage[field] < 0:
                    raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.token_usage.{field}")
            if case["failure_code"] is not None and not isinstance(case["failure_code"], str):
                raise BenchmarkSchemaError(f"BENCHMARK_SCHEMA_INVALID:{case_label}.failure_code")
    return report
