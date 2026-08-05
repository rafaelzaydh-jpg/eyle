#!/usr/bin/env python3
"""Revision 55.12: autonomous grounding repair and stable pending identity."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
import engine.engine as engine_mod  # noqa: E402
from engine.agent_state import AgentState  # noqa: E402
from engine.grounding import (  # noqa: E402
    build_safe_grounded_answer,
    format_grounding_feedback,
    verify_conclusion,
)


def _config(max_steps=3):
    return {
        "llm": {
            "context_window_tokens": 4096,
            "max_tokens": 700,
        },
        "context_engine": {
            "safety_margin_tokens": 400,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 6,
        },
        "agent": {
            "max_steps": max_steps,
            "max_tentativas_parse": 1,
            "max_no_progress_decisions": 3,
            "max_chars_por_observacao": 2200,
            "max_erros_consecutivos": 3,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
            "semantic_grounding": {
                "enabled": True,
                "block_unsupported_anchors": True,
                "require_inline_citations": False,
                "min_claim_token_overlap": 0.12,
                "min_claim_tokens": 5,
            },
        },
    }


def test_grounding_feedback_identifica_claim_e_ancora_rejeitada():
    evidence = [{
        "id": "ev-1",
        "arquivo": "audio.py",
        "linha_inicio": 1,
        "linha_fim": 2,
        "conteudo": "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
    }]
    result = verify_conclusion(
        "A função `limitar_volume` chama `os.remove` (audio.py:1-2).",
        evidence,
        {"enabled": True, "block_unsupported_anchors": True},
    )

    feedback = format_grounding_feedback(result)

    assert "Rejected claim" in feedback
    assert "os.remove" in feedback
    assert "unsupported_objective_anchor" in feedback
    assert "Do not ask the user" in feedback


def test_grounding_esgotado_conclui_com_fallback_seguro_sem_pendencia(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text(
        "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
        encoding="utf-8",
    )
    bad_final = json.dumps({
        "final": {
            "answer": "A função `limitar_volume` chama `os.remove` (audio.py:1-2).",
            "evidence_ids": ["ev-0001"],
            "verification": "faixa lida",
            "limitations": [],
        }
    }, ensure_ascii=False)
    responses = iter([
        json.dumps({
            "tool": "read_range",
            "arguments": {
                "caminho_relativo": "audio.py",
                "linha_inicio": 1,
                "linha_fim": 2,
            },
        }),
        bad_final,
        bad_final,
        bad_final,
    ])
    prompts = []

    def fake_llm(prompt, config):
        del config
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(agent_mod, "executar_agente_llm", fake_llm)
    status, text, pending, details = agent_mod.executar_agente(
        "analise audio.py",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert pending is None
    assert "os.remove" not in text
    assert "Evidências verificadas: audio.py:1-2" in text
    assert details["grounding_fallback_applied"] is True
    assert details["fallback_cause"] == "semantic_grounding_safe_fallback"
    assert details["semantic_grounding"]["ok"] is True
    assert details["semantic_grounding_original"]["ok"] is False
    assert "os.remove" in prompts[2]
    assert "Do not ask the user" in prompts[2]


def test_fallback_preserva_claims_aceitas_e_remove_as_rejeitadas():
    evidence = [{
        "id": "ev-1",
        "arquivo": "audio.py",
        "linha_inicio": 1,
        "linha_fim": 2,
        "conteudo": "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
    }]
    answer = (
        "`limitar_volume` restringe o valor entre `0` e `100` (audio.py:1-2). "
        "A função chama `os.remove` (audio.py:1-2)."
    )
    result = verify_conclusion(
        answer,
        evidence,
        {"enabled": True, "block_unsupported_anchors": True},
    )

    fallback = build_safe_grounded_answer(answer, result, evidence)
    fallback_check = verify_conclusion(
        fallback,
        evidence,
        {"enabled": True, "block_unsupported_anchors": True},
    )

    assert "limitar_volume" in fallback
    assert "os.remove" not in fallback
    assert fallback_check["ok"] is True


def test_mesma_pendencia_valida_preserva_o_id(tmp_path):
    project = {"projeto": "demo", "caminho_origem": str(tmp_path)}
    first = engine_mod._preparar_pendencia(
        {"task_id": "task-1"},
        "agente",
        project,
        {"confirmacoes": {"expiracao_segundos": 3600}},
    )
    resumed = engine_mod._preparar_pendencia(
        dict(first, pergunta_ao_usuario="Escolha A ou B"),
        "agente",
        project,
        {"confirmacoes": {"expiracao_segundos": 3600}},
    )

    assert resumed["id"] == first["id"]
    assert resumed["criado_em"] == first["criado_em"]
    assert resumed["expira_em"] == first["expira_em"]


def test_retomada_que_pergunta_de_novo_herda_o_mesmo_id(monkeypatch):
    config = _config()
    state = AgentState(config)
    state.definir_objetivo("conversa", "chat", modo="chat")
    retomar = {
        "id": "A1B2",
        "tipo_pendencia": "agente",
        "criado_em": "2026-08-05T09:00:00Z",
        "expira_em": "2026-08-05T12:00:00Z",
        "projeto_hash": None,
        "task_id": "task-stable-id",
        "objetivo": "conversa",
        "modo": "chat",
        "task_type": "chat",
        "estado": state.to_dict(),
        "tool_pendente": {
            "tool": "__user_response__",
            "arguments": {},
            "permission": "READ",
            "idempotent": True,
        },
        "continuation_kind": "user_input",
        "pergunta_ao_usuario": "Primeira pergunta",
    }
    monkeypatch.setattr(
        agent_mod,
        "executar_agente_llm",
        lambda *args: json.dumps({"needs_user": "Segunda pergunta"}),
    )

    status, _, pending, _ = agent_mod.executar_agente(
        "conversa",
        config,
        projeto=None,
        retomar=retomar,
        resposta_usuario="resposta",
        retornar_detalhes=True,
    )

    assert status == "needs_user"
    assert pending["id"] == "A1B2"
    assert pending["task_id"] == "task-stable-id"
