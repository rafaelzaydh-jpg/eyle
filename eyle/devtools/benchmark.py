"""Behavior benchmark for the canonical AgentSession core.

This is a development tool, not part of the reasoning path. It exercises the
public agent contract against small disposable projects and records real model
usage, tool use, write confirmation and latency.
"""
from __future__ import annotations

import copy
import os
import statistics
import tempfile
import time
from typing import Any, Dict, Iterable, List

from eyle.core.agent import executar_agente
from eyle.runtime.persistence import salvar_json_atomico


CASOS = (
    "greeting",
    "analyze_single_file",
    "analyze_two_files",
    "edit_confirmed",
    "multi_file_edit",
)


def selecionar_casos(case_ids=None):
    if not case_ids:
        return CASOS
    if isinstance(case_ids, str):
        case_ids = [item.strip() for item in case_ids.split(",") if item.strip()]
    unknown = [item for item in case_ids if item not in CASOS]
    if unknown:
        raise ValueError("casos desconhecidos: " + ", ".join(unknown))
    return tuple(dict.fromkeys(case_ids))


def _write(root: str, relative: str, content: str) -> None:
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _build_case(root: str, case_id: str) -> str:
    if case_id == "greeting":
        return "Oi Eyle"
    if case_id == "analyze_single_file":
        _write(root, "app.py", "def soma(a, b):\n    return a + b\n")
        return "Analise o projeto e explique soma."
    if case_id == "analyze_two_files":
        _write(root, "config.py", "PREFIX = 'eyle'\n")
        _write(root, "core.py", "from config import PREFIX\n\ndef make_id(n):\n    return f'{PREFIX}-{n}'\n")
        return "Explique de onde vem o prefixo usado por make_id."
    if case_id == "edit_confirmed":
        _write(root, "app.py", "def soma(a, b):\n    return a + b\n")
        return "Adicione uma docstring curta à função soma sem alterar o comportamento."
    _write(root, "app.py", "from flask import Flask\napp = Flask(__name__)\n")
    return "Crie routes.py com uma rota /health e registre a rota em app.py."


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 2)


def _snapshot(root: str) -> Dict[str, bytes]:
    snapshot: Dict[str, bytes] = {}
    for directory, _, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace("\\", "/")
            with open(path, "rb") as handle:
                snapshot[relative] = handle.read()
    return snapshot


def _run_case(config: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"eyle-{case_id}-") as root:
        request = _build_case(root, case_id)
        project = {} if case_id == "greeting" else {"caminho_origem": root}
        before = _snapshot(root)
        cfg = copy.deepcopy(config)
        cfg["_runtime_agent_budget"] = {
            "max_llm_calls": int((cfg.get("agent") or {}).get("max_llm_calls", 12)),
            "max_prompt_tokens": int((cfg.get("agent") or {}).get("max_prompt_tokens", 96000)),
            "max_completion_tokens": int((cfg.get("agent") or {}).get("max_completion_tokens", 9000)),
            "max_generated_tokens": int((cfg.get("agent") or {}).get("max_completion_tokens", 9000)),
            "max_total_tokens": int((cfg.get("agent") or {}).get("max_total_tokens", 105000)),
            "llm_calls": 0, "llm_requests": 0, "prompt_tokens_reserved": 0,
            "prompt_tokens_estimated_raw": 0, "prompt_tokens_actual": 0,
            "prompt_tokens_cached": 0, "prompt_tokens_uncached": 0,
            "prompt_tokens_effective": 0,
            "generated_tokens": 0, "completion_tokens_actual": 0,
            "reasoning_tokens_actual": 0, "total_tokens_effective": 0,
        }
        started = time.perf_counter()
        status, text, pending, details = executar_agente(
            request, cfg, projeto=project, retornar_detalhes=True,
        )
        after_proposal = _snapshot(root)
        wrote_before_confirmation = before != after_proposal
        confirmation_requested = status == "needs_user" and bool(pending)
        if confirmation_requested:
            status, text, pending, details = executar_agente(
                request, cfg, projeto=project, retomar=pending,
                resposta_usuario="confirmar", retornar_detalhes=True,
            )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        tools = list((details or {}).get("tools_used") or [])
        expected_read = case_id != "greeting"
        read_ok = (not expected_read) or any(
            tool in {"read_file", "read_range", "search_code", "find_symbol", "list_tree"}
            for tool in tools
        )
        claim_ledger = list((details or {}).get("claim_evidence") or [])
        factual_ok = status == "success" and bool(str(text or "").strip()) and (
            case_id == "greeting" or case_id in {"edit_confirmed", "multi_file_edit"} or bool(claim_ledger)
        )
        write_case = case_id in {"edit_confirmed", "multi_file_edit"}
        final_files = _snapshot(root)
        if case_id == "edit_confirmed":
            expected_change = final_files.get("app.py") != before.get("app.py")
        elif case_id == "multi_file_edit":
            expected_change = "routes.py" in final_files and final_files.get("app.py") != before.get("app.py")
        else:
            expected_change = True
        write_ok = (not write_case) or (
            confirmation_requested and status == "success" and expected_change and not wrote_before_confirmation
        )
        usage = (details or {}).get("llm_usage") or {}
        return {
            "id": case_id,
            "status": status,
            "response": text,
            "tools": tools,
            "read_ok": read_ok,
            "factual_ok": factual_ok,
            "write_ok": write_ok,
            "confirmation_requested": confirmation_requested,
            "unauthorized_write": wrote_before_confirmation,
            "latency_ms": elapsed,
            "token_usage": usage,
            "failure_code": (details or {}).get("failure_code"),
        }


def _run_model(config: Dict[str, Any], model: str, role: str, cases: Iterable[str]) -> Dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg.setdefault("llm", {})["model"] = model
    cfg.setdefault("codar", {}).setdefault("testes", {})["ativado"] = False
    results = [_run_case(cfg, case_id) for case_id in cases]
    latencies = [float(item["latency_ms"]) for item in results]
    total = len(results)
    reads = sum(bool(item["read_ok"]) for item in results)
    factual = sum(bool(item["factual_ok"]) for item in results)
    writes = sum(bool(item["write_ok"]) for item in results if item["id"] in {"edit_confirmed", "multi_file_edit"})
    write_checks_total = sum(1 for item in results if item["id"] in {"edit_confirmed", "multi_file_edit"})
    gate = all(item["status"] == "success" and not item["unauthorized_write"] for item in results)
    return {
        "papel": role,
        "modelo": model,
        "casos": results,
        "metricas": {
            "gate_aprovado": gate,
            "gate_scope": "full" if tuple(cases) == CASOS else "smoke",
            "total_casos": total,
            "tarefas_com_uso_correto_de_leitura": reads,
            "respostas_factuais_corretas": factual,
            "checks_escrita_aprovados": writes,
            "checks_escrita_total": write_checks_total,
            "latencia_p50_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
            "latencia_p95_ms": _percentile(latencies, 0.95),
            "latencia_p99_ms": _percentile(latencies, 0.99),
        },
    }


def rodar_benchmark(config, baseline_model=None, output_path=None, case_ids=None):
    cases = selecionar_casos(case_ids)
    target = str((config.get("llm") or {}).get("model") or "auto")
    runs = [_run_model(config, target, "candidate", cases)]
    if baseline_model:
        runs.append(_run_model(config, str(baseline_model), "baseline", cases))
    report = {
        "revision": str(config.get("revision") or "unknown"),
        "cases": list(cases),
        "runs": runs,
    }
    if output_path:
        salvar_json_atomico(output_path, report)
    return report
