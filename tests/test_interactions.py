from __future__ import annotations

import copy
from pathlib import Path

import eyle.core.agent as agent
from eyle.runtime.execution_context import (
    ExecutionContext,
    current_execution,
)
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION, confirmation_control
from eyle.runtime import service
from llm.structured import parse_ecc_response
from tests.canonical import base_config, run_agent
from tests.test_ecc_rev21_audit import provider_context


def test_confirmation_contract_is_button_ready_and_id_stays_internal():
    ctx = ExecutionContext.from_config(base_config(), execution_id="logical")
    pending = {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "capability_confirmation",
        "question": "A Eyle vai modificar calc.py e aplicar a correção verificada.",
        "session": {"pending_operation": {"operation": "transaction"}},
        "execution_state": ctx.continuation_state(),
        "capability": "standard.workspace_transaction",
        "provider": "standard",
        "confirmation_id": "ecc-cap-0001",
        "id": "93F3",
    }
    public = service._public_interaction(pending)
    assert public["kind"] == "confirmation"
    assert [item["label"] for item in public["options"]] == ["Aceitar", "Recusar"]
    assert public["options"][0]["submit_text"] == "confirmar 93F3"
    assert public["options"][1]["submit_text"] == "cancelar 93F3"
    assert "93F3" not in public["title"]
    assert confirmation_control("aceitar") == "aplicar"
    assert confirmation_control("recusar") == "cancelar"


def test_semantic_choices_pause_and_resume_same_logical_execution(monkeypatch, tmp_path):
    raw = {
        "type": "concluir",
        "response": "Há dois caminhos seguros. Escolha o que prefere.",
        "choices": ["Corrigir apenas o bug", "Refazer e adicionar testes"],
        "allow_free_text": True,
        "memory_delta": [],
    }
    parsed = parse_ecc_response(raw)
    cfg = base_config()
    cfg["llm"]["provider_token_budget_per_message"] = 100
    seen = []

    def fake(prompt, config):
        execution = current_execution()
        assert execution is not None
        seen.append((execution.completion_tokens_actual, execution.resume_count, copy.deepcopy(prompt.dynamic.get("runtime_feedback") or [])))
        if len(seen) == 1:
            execution.completion_tokens_actual += 10
            return parsed
        assert execution.provider_token_limit == 100
        assert execution.completion_tokens_actual == 10
        assert execution.resume_count == 1
        assert any(item.get("code") == "USER_CHOICE" and item.get("selected") == "Corrigir apenas o bug" for item in seen[-1][2])
        return {"type": "concluir", "response": "caminho escolhido", "memory_delta": []}

    monkeypatch.setattr(agent, "executar_ecc_llm", fake)
    status, text, pending, _ = run_agent(
        agent, "qual caminho?", cfg, provider_context=provider_context(tmp_path),
        retornar_detalhes=True, execution_id="choice", source_job_id=1,
    )
    assert status == "choice_required" and pending is not None
    assert pending["continuation_kind"] == "semantic_choice"
    assert pending["options"] == parsed["choices"]
    assert pending["execution_state"]["completion_tokens_actual"] == 10

    changed = copy.deepcopy(cfg)
    changed["llm"]["provider_token_budget_per_message"] = 999
    pending = copy.deepcopy(pending)
    pending["execution_state"]["started_wall_time"] -= 3600
    status2, text2, pending2, details2 = run_agent(
        agent, "qual caminho?", changed, provider_context=provider_context(tmp_path),
        retomar=pending, resposta_usuario="Corrigir apenas o bug", retornar_detalhes=True,
        execution_id="other-job", source_job_id=2,
    )
    assert (status2, text2, pending2) == ("completed", "caminho escolhido", None)
    assert details2["execution_id"] == "choice"
    assert details2["llm_usage"]["provider_token_limit"] == 100
    assert details2["llm_usage"]["execution_resume_count"] == 1


def test_memory_wire_uses_current_canonical_fields_without_second_llm():
    parsed = parse_ecc_response({
        "type": "concluir",
        "response": "ok",
        "memory_delta": [{
            "op": "remember",
            "arguments": {
                "scope": "user",
                "retention": "temporary",
                "kind": "preference",
                "content": "O usuário prefere respostas compactas.",
                "epistemic": {"nature": "preference", "confidence": 0.8},
                "tags": ["communication"],
                "supports": [{"kind": "request"}],
                "recall": {"aliases": ["respostas curtas"]},
            },
        }],
    })
    item = parsed["memory_delta"][0]
    assert item["op"] == "remember"
    assert item["scope"] == "user"
    assert item["retention"] == "temporary"
    assert item["kind"] == "preference"
    assert item["epistemic"]["confidence"] == 0.8
    assert item["tags"] == ["communication"]
    assert item["supports"] == [{"kind": "request"}]
    assert item["recall"]["aliases"] == ["respostas curtas"]

def test_web_panel_uses_hidden_submit_value_and_choice_resolution_code_exists():
    root = Path(__file__).resolve().parents[1]
    js = (root / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "option && option.submit_text || label" in js
    assert "panel.remove()" in js
    assert "data-pending-id" in js
    service_source = (root / "eyle" / "runtime" / "service.py").read_text(encoding="utf-8")
    assert 'interaction["resolved"] = True' in service_source
    assert "Pending ID:" not in service_source


def test_service_persists_choice_gate_and_resolves_on_next_user_message(monkeypatch, tmp_path):
    memory_dir = tmp_path / "memory"
    context_dir = tmp_path / "context"
    memory_dir.mkdir(); context_dir.mkdir()
    monkeypatch.setattr(service, "MEMORY_DIR", str(memory_dir))
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(context_dir / "pending"))

    interaction = {
        "id": "ABCD",
        "kind": "choice",
        "title": "Escolha como continuar",
        "options": [
            {"id": "choice-1", "label": "Opção A", "submit_text": "Opção A"},
            {"id": "choice-2", "label": "Opção B", "submit_text": "Opção B"},
        ],
        "allow_free_text": True,
        "resolved": False,
    }
    service.registrar_mensagem("assistant", "Escolha A ou B", metadata={"interaction": interaction})
    service.registrar_mensagem("user", "Opção B")
    messages = service.carregar_conversa()
    assert messages[0]["interaction"]["resolved"] is True
    assert messages[0]["interaction"]["selected_text"] == "Opção B"
