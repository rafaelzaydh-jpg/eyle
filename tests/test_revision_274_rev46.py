import json
from pathlib import Path

import pytest

import engine.agent as agent_mod
from engine.agent_state import AgentState
from engine.agent_tools import TOOLS, gerar_catalogo_tools
from engine.audit_pipeline import (
    ambiguous_gap_candidates,
    build_audit_candidate_catalog,
    build_deterministic_audit_plan,
)
from engine.compiler import montar_prompt_agente
from engine.benchmark import CASOS, calcular_metricas
from engine.config_schema import ConfigError, validar_config
from engine.engine import _selecionar_historico_por_tokens
from engine.response_recovery import recover_useful_response
from engine.token_efficiency import compare_token_efficiency_reports
from llm.executar import (
    ErroLLM,
    _finalizar_requisicao_llm,
    _registrar_tokens_gerados,
    _reservar_requisicao_llm,
    executar_recuperacao_textual,
)


def _inventory(paths):
    return {
        "inventory_hash": "a" * 64,
        "varredura_completa": True,
        "truncado": False,
        "total_retornado": len(paths),
        "total_arquivos": len(paths),
        "total_diretorios": 0,
        "arquivos_raiz": [p for p in paths if "/" not in p],
        "diretorios_raiz": sorted({p.split("/", 1)[0] for p in paths if "/" in p}),
        "extensoes": {".py": sum(p.endswith(".py") for p in paths)},
        "entradas": [
            {"caminho": path, "tipo": "arquivo", "profundidade": path.count("/") + 1}
            for path in paths
        ],
    }


def _runtime_config(**runtime_overrides):
    runtime = {
        "max_llm_calls": 12,
        "max_prompt_tokens": 14000,
        "max_completion_tokens": 5000,
        "max_total_tokens": 18000,
        "llm_calls": 0,
        "llm_requests": 0,
        "prompt_tokens_reserved": 0,
        "prompt_tokens_actual": 0,
        "prompt_tokens_effective": 0,
        "generated_tokens": 0,
    }
    runtime.update(runtime_overrides)
    return {
        "llm": {"context_window_tokens": 8192},
        "context_engine": {"chars_per_token_fallback": 3, "safety_margin_tokens": 200},
        "_runtime_agent_budget": runtime,
    }


def test_entendimento_json_never_enters_agent_prompt():
    entendimento = {
        "arquivos": {
            "secret_legacy.py": {
                "responsabilidade": "LEGACY_SENTINEL_SHOULD_NEVER_REACH_MODEL",
                "hash": "deadbeef",
            }
        }
    }
    prompt = montar_prompt_agente(
        "Analise o projeto",
        entendimento=entendimento,
        project_inventory=_inventory(["app.py"]),
        config={
            "llm": {"context_window_tokens": 8192, "max_tokens": 1000},
            "context_engine": {"chars_per_token_fallback": 3, "safety_margin_tokens": 200},
        },
    )
    assert "LEGACY_SENTINEL_SHOULD_NEVER_REACH_MODEL" not in prompt
    assert "entendimento.json" not in prompt
    assert "F app.py" not in prompt
    assert "PROJECT INVENTORY SUMMARY" in prompt


def test_deterministic_audit_plan_handles_single_file_without_scout():
    catalog = build_audit_candidate_catalog(_inventory(["app.py"]))
    plan = build_deterministic_audit_plan(catalog, limit=6)
    assert plan["selected_paths"] == ["app.py"]
    assert plan["planner"] == "deterministic"


def test_common_project_audit_uses_finalizer_only(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    scouts = []
    finalizers = []
    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda *args: scouts.append(args) or "{}")

    def finalizer(prompt, config):
        finalizers.append(prompt)
        return json.dumps({
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "app.py define VALUE com o valor 1.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                }],
                "verification": "fresh code",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    cfg = {
        "agent": {
            "max_steps": 8, "max_tentativas_parse": 2,
            "max_no_progress_decisions": 3, "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False, "max_erros_consecutivos": 3,
            "exigir_run_tests_apos_escrita": True,
            "enabled_modes": ["analyze", "suggest", "edit"], "rollout_mode": "read_only",
            "task_deadline_seconds": 30, "max_llm_calls": 12,
            "max_total_generated_tokens": 5000, "max_completion_tokens": 5000,
            "max_prompt_tokens": 14000, "max_total_tokens": 18000,
            "semantic_grounding": {"enabled": False},
            "audit_candidate_limit": 48, "audit_initial_read_limit": 6,
            "audit_gap_read_limit": 1,
        },
        "context_engine": {"chars_per_token_fallback": 3, "safety_margin_tokens": 100},
        "llm": {"context_window_tokens": 8192, "max_tokens": 1500, "audit_finalizer_max_tokens": 1600},
    }
    status, text, _, details = agent_mod.executar_agente(
        "Faça a análise do projeto", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "VALUE" in text
    assert scouts == []
    assert len(finalizers) == 1
    assert details["audit_pipeline"]["initial_scout"]["planner"] == "deterministic"


def test_ambiguous_expansion_only_exists_for_real_tie_without_next_path():
    catalog = {
        "candidates": [
            {"path": "a.py", "score": 10},
            {"path": "b.py", "score": 10},
            {"path": "c.py", "score": 9},
        ]
    }
    assert [item["path"] for item in ambiguous_gap_candidates(
        catalog, {"missing": ["core_logic_read"], "next_read_candidates": []}, already_read=[]
    )] == ["a.py", "b.py"]
    assert ambiguous_gap_candidates(
        catalog, {"missing": ["core_logic_read"], "next_read_candidates": ["a.py"]}, already_read=[]
    ) == []


def test_tool_catalog_is_filtered_by_current_state():
    names = {item["name"] for item in gerar_catalogo_tools(
        TOOLS, config={}, allowed_names={"read_file", "read_range"},
    )}
    assert names == {"read_file", "read_range"}
    assert "apply_patch" not in names
    assert "read_metadata" not in names


def test_prompt_budget_blocks_before_backend_request():
    cfg = _runtime_config()
    cfg["llm"]["context_window_tokens"] = 100
    with pytest.raises(ErroLLM) as exc:
        _reservar_requisicao_llm(cfg, "S" * 120, "U" * 120, 50)
    assert exc.value.error_code == "PROMPT_CONTEXT_BUDGET_EXCEEDED"
    assert cfg["_runtime_agent_budget"]["llm_requests"] == 0


def test_retries_count_prompt_cost_and_actual_usage_replaces_estimate():
    cfg = _runtime_config()
    first = _reservar_requisicao_llm(cfg, "system", "first prompt", 100)
    second = _reservar_requisicao_llm(cfg, "system", "second prompt", 100)
    assert cfg["_runtime_agent_budget"]["llm_requests"] == 2
    estimated_before = cfg["_runtime_agent_budget"]["prompt_tokens_effective"]
    _finalizar_requisicao_llm(cfg, second, {"prompt_tokens": 3})
    assert cfg["_runtime_agent_budget"]["prompt_tokens_actual"] == 3
    assert cfg["_runtime_agent_budget"]["prompt_tokens_effective"] <= estimated_before
    assert first["finalized"] is False


def test_completion_and_total_budgets_are_enforced():
    cfg = _runtime_config(max_completion_tokens=2, max_total_tokens=100)
    with pytest.raises(ErroLLM) as exc:
        _registrar_tokens_gerados(cfg, "x" * 30, [])
    assert exc.value.error_code == "MAX_COMPLETION_TOKENS_EXCEEDED"


def test_chat_history_uses_token_budget_without_slicing_messages():
    cfg = {
        "agent": {"chat_history_token_budget": 10},
        "context_engine": {"chars_per_token_fallback": 1},
        "_runtime_agent_budget": {},
    }
    history = [
        {"role": "user", "text": "old-message-too-long"},
        {"role": "assistant", "text": "ok"},
        {"role": "user", "text": "new"},
    ]
    selected = _selecionar_historico_por_tokens(history, cfg)
    assert selected == history[-1:]
    assert cfg["_runtime_agent_budget"]["chat_history_messages_omitted"] == 2


def test_legacy_llm_recovery_cannot_reach_backend():
    with pytest.raises(ErroLLM) as exc:
        executar_recuperacao_textual("prompt", {})
    assert exc.value.error_code == "LEGACY_LLM_RECOVERY_DISABLED"
    result = recover_useful_response(
        "Analise", [], {"agent": {"response_recovery": {"llm_enabled": True}}},
        cause="test", allow_llm=True,
    )
    assert result["attempts"][0]["error_code"] == "LEGACY_LLM_RECOVERY_DISABLED"


def test_config_rejects_reactivation_of_legacy_recovery():
    base = json.loads(Path("config.json").read_text(encoding="utf-8"))
    base["agent"]["response_recovery"] = {"llm_enabled": True}
    with pytest.raises(ConfigError):
        validar_config(base)


def _benchmark_result(*, calls=1, requests=1, prompt=100, completion=20, task_type="project_read"):
    return {
        "leu": True, "factual_ok": True, "completion_ok": True,
        "grounded_ok": True, "workflow_ok": True, "safety_ok": True,
        "tools": ["read_file"], "json_failures": 0, "case_elapsed_ms": 1,
        "llm_responses": [{"latency_ms": 1}], "inventadas": [],
        "false_success": False, "unauthorized_write": False,
        "information_preservation": {
            "gate": {"ok": True}, "summary": {"silent_discards": 0},
        },
        "write": {
            "confirmacao_barrou_escrita": True, "hashes_na_pendencia": True,
            "dry_run_antes_write": True, "rollback": True, "retomada_releitura": True,
        },
        "task_type": task_type,
        "token_usage": {
            "llm_calls": calls, "llm_requests": requests,
            "prompt_tokens_effective": prompt, "completion_tokens": completion,
            "total_tokens_effective": prompt + completion,
        },
    }


def test_benchmark_includes_end_to_end_token_metrics():
    metrics = calcular_metricas([_benchmark_result()], casos=[CASOS[0]])
    assert metrics["llm_calls"] == 1
    assert metrics["llm_requests"] == 1
    assert metrics["prompt_tokens_total"] == 100
    assert metrics["completion_tokens_total"] == 20
    assert metrics["total_tokens_effective"] == 120
    assert metrics["token_metrics_available"] is True


def _efficiency_report(*, calls=1, requests=1, prompt=100, completion=20):
    return {
        "runs": [{
            "papel": "principal",
            "resultados": [{
                "id": "case-1", "status": "success",
                "token_usage": {
                    "llm_calls": calls, "llm_requests": requests,
                    "prompt_tokens_effective": prompt,
                    "completion_tokens": completion,
                    "total_tokens_effective": prompt + completion,
                },
            }],
        }],
    }


def test_release_efficiency_comparison_accepts_equal_or_lower_usage():
    result = compare_token_efficiency_reports(
        _efficiency_report(prompt=100), _efficiency_report(prompt=90), tolerance=0.10,
    )
    assert result["ok"] is True
    assert result["regressions"] == []


def test_release_efficiency_comparison_rejects_call_or_token_regression():
    result = compare_token_efficiency_reports(
        _efficiency_report(calls=1, requests=1, prompt=100),
        _efficiency_report(calls=2, requests=2, prompt=130),
        tolerance=0.10,
    )
    assert result["ok"] is False
    reasons = result["regressions"][0]["reasons"]
    assert "llm_calls:1->2" in reasons
    assert "llm_requests:1->2" in reasons
    assert "prompt_tokens_effective:100->130" in reasons


def test_public_summary_exposes_logical_calls_backend_requests_and_tokens():
    from engine.work_summary import construir_resumo_trabalho

    summary = construir_resumo_trabalho(
        {"tipo": "pergunta", "texto": "Analise"},
        {"agente_status": "success", "agente_conclusao": {
            "task_type": "project_read",
            "token_usage": {
                "llm_calls": 1, "llm_requests": 2,
                "prompt_tokens_effective": 100, "completion_tokens": 20,
                "total_tokens_effective": 120,
                "chat_history_messages_omitted": 3,
            },
        }},
        0.1,
    )
    fields = summary["steps"][2]["fields"]
    usage = next(item["value"] for item in fields if item["label"] == "Uso de tokens")
    assert "chamadas lógicas=1" in usage
    assert "requests backend=2" in usage
    assert "total=120" in usage
