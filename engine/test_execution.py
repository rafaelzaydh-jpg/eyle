#!/usr/bin/env python3
"""Fonte unica de verdade para execucoes ``run_tests`` da tarefa atual."""
from __future__ import annotations


def latest_test_execution(actions):
    """Resume somente a execucao ``run_tests`` mais recente realmente executada.

    Uma aprovacao antiga nunca pode mascarar uma tentativa posterior reprovada.
    """
    executions = [
        item for item in actions or []
        if isinstance(item, dict)
        and item.get("tool") == "run_tests"
        and item.get("executed") is True
    ]
    latest = executions[-1] if executions else None
    return {
        "executed": latest is not None,
        "passed": bool(latest and latest.get("ok") is True),
        "attempts": len(executions),
        "latest_ok": None if latest is None else latest.get("ok") is True,
        "latest_error_code": None if latest is None else latest.get("error_code"),
    }


def successful_test_run(actions):
    """True somente quando a ultima execucao real de testes passou."""
    return latest_test_execution(actions)["passed"]
