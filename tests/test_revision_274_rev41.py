from types import SimpleNamespace

import engine.agent as agent_mod
from engine.agent_state import AgentState
from engine.compiler import montar_prompt_agente
from engine.task_contract import build_task_contract, evaluate_intent_coverage
from engine.work_summary import construir_resumo_trabalho


def test_explanation_with_criacao_never_becomes_project_write():
    objective = "Explique como a criação e a inicialização da aplicação funcionam em app.py"
    project = {"caminho_origem": "/tmp/project"}
    assert agent_mod.classificar_tarefa_agente(objective, projeto=project, modo="analyze") == "project_read"
    contract = build_task_contract(objective, "project_read")
    assert contract["intent"] == "explain"
    assert contract["write_allowed"] is False
    assert contract["explicit_symbols"] == []
    assert contract["requested_outputs"] == ["explanation"]


def test_short_code_symbol_list_is_preserved_without_natural_language_false_positives():
    contract = build_task_contract(
        "Analise audio.py inteiro e explique tocar e limitar_volume com citacoes.",
        "project_read",
    )
    assert contract["explicit_symbols"] == ["limitar_volume", "tocar"]


def test_analysis_plus_improvements_does_not_require_unrequested_problem_section():
    contract = build_task_contract(
        "Analise o projeto e indique exatamente 5 melhorias", "project_audit"
    )
    assert contract["intent"] == "review"
    assert contract["requested_outputs"] == ["analysis", "recommendations"]
    assert contract["recommendation_count"] == 5

    claims = [{
        "type": "fact",
        "text": "O projeto cria uma aplicação Flask.",
        "evidence_ids": ["ev-1"],
        "basis": "",
        "output": "analysis",
    }]
    claims.extend({
        "type": "recommendation",
        "text": f"Melhoria {index}.",
        "evidence_ids": ["ev-1"],
        "basis": "Estrutura observada no arquivo analisado.",
        "output": "recommendations",
    } for index in range(1, 6))
    coverage = evaluate_intent_coverage(contract, claims, limitations=["Análise estática."])
    assert coverage["ok"] is True
    assert coverage["recommendation_count_actual"] == 5


def test_problem_output_only_when_user_requests_problem_detection():
    contract = build_task_contract(
        "Analise o projeto, encontre problemas e indique 2 melhorias", "project_audit"
    )
    assert contract["requested_outputs"] == ["analysis", "problems", "recommendations"]


def test_compound_write_contract_exposes_all_requested_outputs():
    contract = build_task_contract(
        "Analise o projeto, encontre problemas, corrija os dois mais importantes e depois explique as alterações",
        "project_write",
    )
    assert contract["write_allowed"] is True
    assert contract["requested_outputs"] == [
        "analysis", "problems", "implemented_change", "verification_result",
        "final_state", "explanation",
    ]


def test_no_suite_receipt_is_success_with_partial_verification():
    state = SimpleNamespace(edit_state={
        "status": "applied_without_suite",
        "arquivo": "app.py",
        "linha_inicio": 1,
        "linha_fim_final": 12,
        "codigo_novo_preview": "def health():\n    return {'status': 'ok'}\n",
        "test": {"executed": False, "ok": True, "detail": "nenhuma suite encontrada"},
        "post_write_evidence_id": "ev-2",
    })
    text = agent_mod._conclusao_deterministica_edicao(state)
    assert "Alteração aplicada em app.py" in text
    assert "nenhuma suíte de testes disponível" in text
    assert "testes executados e aprovados" not in text
    assert "verificação parcial" in text


def test_tool_failure_report_exposes_real_tool_error_and_policy():
    state = SimpleNamespace(
        erros_consecutivos=2,
        actions=[
            {"tool": "test_patch_dry_run", "ok": False, "error_code": "INVALID_ARGUMENT", "error_detail": "linha_fim ausente"},
            {"tool": "test_patch_dry_run", "ok": False, "error_code": "DRY_RUN_FAILED", "error_detail": "SyntaxError na linha 8"},
        ],
    )
    text = agent_mod._circuit_breaker_message(state)
    assert "test_patch_dry_run" in text
    assert "INVALID_ARGUMENT" in text
    assert "SyntaxError" in text
    assert "retryable=sim" in text


def test_prompt_tells_model_to_repair_exact_tool_failure():
    config = {
        "llm": {"context_window_tokens": 8192, "max_tokens": 512},
        "context_engine": {"safety_margin_tokens": 256, "chars_per_token_fallback": 3},
    }
    prompt = montar_prompt_agente(
        "Adicione uma rota /health",
        goal_state={"objective": "Adicione uma rota /health", "task_type": "project_write"},
        actions=[{
            "tool": "test_patch_dry_run", "ok": False,
            "error_code": "DRY_RUN_FAILED", "error_detail": "SyntaxError",
        }],
        config=config,
    )
    assert "TOOL FAILURE REPAIR" in prompt
    assert "Never repeat the same invalid tool call unchanged" in prompt
    assert "SyntaxError" in prompt


def test_waiting_confirmation_is_not_presented_as_validation_failure():
    event = {"tipo": "pergunta", "texto": "Adicione uma rota /health"}
    result = {
        "agente_status": "needs_user",
        "agente_conclusao": {
            "completion_gate": {"passed": False, "requires_user": True},
            "task_intent": {
                "intent": "edit", "response_profile": "code_change",
                "write_allowed": True, "recommendations_requested": False,
                "requested_outputs": ["implemented_change", "verification_result", "final_state"],
            },
        },
    }
    summary = construir_resumo_trabalho(event, result, 0.1)
    conclusion = summary["steps"][3]
    fields = {item["label"]: item["value"] for item in conclusion["fields"]}
    assert fields["Validação"] == "proposta aprovada; aguardando confirmação"


def test_new_agent_state_does_not_inherit_previous_edit_state():
    first = AgentState()
    first.edit_state = {"status": "tests_passed", "arquivo": "old.py"}
    second = AgentState()
    assert second.edit_state == {}
    assert second.evidence == []
    assert second.erros_consecutivos == 0


def test_explanation_request_runs_as_read_only_project_read(tmp_path, monkeypatch):
    import json
    from tests.test_revision_274_rev2 import _config

    (tmp_path / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n\nif __name__ == '__main__':\n    app.run()\n",
        encoding="utf-8",
    )
    calls = []

    def agent(prompt, config):
        calls.append(prompt)
        return json.dumps({"tool": "read_file", "arguments": {"caminho_relativo": "app.py"}})

    def finalizer(prompt, config):
        assert '"response_profile":"code_explanation"' in prompt
        return json.dumps({
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "app.py cria a instância Flask no escopo do módulo e chama app.run somente na execução direta.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                    "output": "explanation",
                }],
                "verification": "arquivo completo lido",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_agente_llm", agent)
    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", finalizer)
    cfg = _config()
    cfg["agent"].update({
        "project_read_fast_path_enabled": True,
        "target_coverage_enabled": True,
        "project_read_single_repair_enabled": True,
        "intent_output_gate_enabled": True,
    })
    status, text, _, details = agent_mod.executar_agente(
        "Explique como a criação e a inicialização da aplicação funcionam em app.py",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
        modo="analyze",
    )
    assert status == "success"
    assert details["task_type"] == "project_read"
    assert details["task_intent"]["write_allowed"] is False
    assert "app.run" in text
    assert len(calls) == 1


def test_project_audit_accepts_exactly_five_requested_improvements(tmp_path, monkeypatch):
    import json
    from tests.test_project_audit_55_17 import _config

    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", lambda *args: json.dumps({
        "final": {
            "answer": "plano",
            "selected_paths": ["app.py"] if "SCOUT PHASE: initial" in args[0] else [],
            "risk_hypotheses": [],
            "gaps": [],
            "rationale": "ler o único arquivo",
        }
    }))

    def finalizer(prompt, config):
        assert "Return exactly 5 recommendation claims" in prompt
        claims = [{
            "type": "fact",
            "text": "O projeto cria uma aplicação Flask em app.py.",
            "evidence_ids": ["ev-0001"],
            "basis": "",
            "output": "analysis",
        }]
        claims.extend({
            "type": "recommendation",
            "text": f"Melhoria {index}: evolução proposta para a aplicação.",
            "evidence_ids": ["ev-0001"],
            "basis": "A aplicação observada é mínima.",
            "output": "recommendations",
        } for index in range(1, 6))
        return json.dumps({"final": {"claims": claims, "verification": "app.py lido", "limitations": []}})

    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("project_audit não deve usar o agente para redigir")),
    )
    cfg = _config()
    cfg["agent"]["intent_output_gate_enabled"] = True
    status, text, _, details = agent_mod.executar_agente(
        "Analise o projeto e indique exatamente 5 melhorias",
        cfg,
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert details["intent_coverage"]["recommendation_count_actual"] == 5
    assert details["intent_coverage"]["ok"] is True
    assert text.count("Melhoria") == 5


def test_expandable_summary_exposes_tool_error_details():
    event = {"tipo": "pergunta", "texto": "Adicione uma rota /health"}
    result = {
        "agente_status": "failed",
        "agente_conclusao": {
            "completion_gate": {"passed": False, "requires_user": False},
            "tool_errors": [{
                "tool": "test_patch_dry_run",
                "error_code": "DRY_RUN_FAILED",
                "error_detail": "SyntaxError na linha 8",
                "retryable": True,
            }],
        },
    }
    summary = construir_resumo_trabalho(event, result, 0.1)
    analysis_step = summary["steps"][2]
    fields = {item["label"]: item["value"] for item in analysis_step["fields"]}
    assert "test_patch_dry_run" in fields["Erros de ferramentas"]
    assert "DRY_RUN_FAILED" in fields["Erros de ferramentas"]
    assert "retryable=sim" in fields["Erros de ferramentas"]
