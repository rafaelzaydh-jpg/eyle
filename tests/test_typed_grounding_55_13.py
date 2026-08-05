#!/usr/bin/env python3
"""Revision 55.13: typed grounding without suppressing agent autonomy."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
from engine.grounding import (  # noqa: E402
    build_safe_grounded_answer,
    classify_claim,
    format_grounding_feedback,
    verify_conclusion,
)


def _evidence():
    return [{
        "id": "ev-1",
        "arquivo": "config.py",
        "linha_inicio": 1,
        "linha_fim": 2,
        "conteudo": "TIMEOUT = 315\nMAX_RETRIES = 1\n",
    }]


def _grounding_config():
    return {
        "enabled": True,
        "block_unsupported_anchors": True,
        "require_inline_citations": False,
        "require_inference_evidence": True,
        "warn_hypothesis_without_evidence": True,
        "min_claim_token_overlap": 0.12,
        "min_claim_tokens": 5,
    }


def _agent_config(max_steps=3):
    return {
        "llm": {"context_window_tokens": 4096, "max_tokens": 700},
        "context_engine": {
            "safety_margin_tokens": 400,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 6,
        },
        "agent": {
            "max_steps": max_steps,
            "max_tentativas_parse": 1,
            "max_no_progress_decisions": 3,
            "cycle_min_repetitions": 3,
            "max_chars_por_observacao": 2200,
            "max_erros_consecutivos": 3,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
            "semantic_grounding": _grounding_config(),
        },
    }


def test_classificador_distingue_liberdade_epistemica():
    assert classify_claim("O timeout observado é 315 segundos.") == "fact"
    assert classify_claim("Isso sugere uma contenção no worker.") == "inference"
    assert classify_claim("Pode existir contenção entre os workers.") == "hypothesis"
    assert classify_claim("Vou dividir a leitura em etapas menores.") == "decision"
    assert classify_claim("Recomendo criar retry.py com 3 tentativas.") == "recommendation"


def test_fato_objetivo_inventado_continua_bloqueado():
    result = verify_conclusion(
        "O `TIMEOUT` está configurado como `600` segundos (config.py:1-2).",
        _evidence(),
        _grounding_config(),
    )

    assert result["ok"] is False
    report = result["errors"][0]
    assert report["claim_type"] == "fact"
    assert "unsupported_objective_anchor" in report["errors"]


def test_recomendacao_pode_introduzir_arquivo_e_valor_novos():
    answer = "Recomendo criar `retry.py` com `3` tentativas."
    result = verify_conclusion(answer, _evidence(), _grounding_config())

    assert result["ok"] is True
    assert result["claims"][0]["claim_type"] == "recommendation"
    assert "novel_proposed_anchor" in result["claims"][0]["warnings"]


def test_decisao_explicita_nao_precisa_existir_no_projeto():
    answer = "A estratégia escolhida será dividir a análise em blocos de `200` linhas."
    annotations = [{
        "claim": answer,
        "type": "decision",
        "basis": "Escolha operacional reversível para controlar contexto.",
    }]
    result = verify_conclusion(
        answer, _evidence(), _grounding_config(), claim_annotations=annotations,
    )

    assert result["ok"] is True
    assert result["claims"][0]["claim_type"] == "decision"
    assert result["claims"][0]["classification_source"] == "explicit"


def test_inferencia_exige_base_mas_aceita_valor_derivado():
    answer = "Isso indica que elevar o limite para `630` dobraria o valor atual."
    annotations = [{
        "claim": answer,
        "type": "inference",
        "evidence_ids": ["ev-1"],
        "basis": "TIMEOUT observado em 315; 630 é o dobro aritmético.",
    }]
    result = verify_conclusion(
        answer, _evidence(), _grounding_config(), claim_annotations=annotations,
    )

    assert result["ok"] is True
    report = result["claims"][0]
    assert report["claim_type"] == "inference"
    assert report["evidence_ids"] == ["ev-1"]
    assert "derived_or_unverified_anchor" in report["warnings"]


def test_inferencia_com_evidencia_inexistente_e_rejeitada():
    answer = "Isso indica contenção entre os workers."
    annotations = [{
        "claim": answer,
        "type": "inference",
        "evidence_ids": ["ev-fantasma"],
    }]
    result = verify_conclusion(
        answer, _evidence(), _grounding_config(), claim_annotations=annotations,
    )

    assert result["ok"] is False
    assert "annotation_evidence_not_available" in result["errors"][0]["errors"]


def test_hipotese_nao_vira_fato_so_por_conter_identificador_novo():
    answer = "Pode existir uma corrida em `worker_pool` durante o polling."
    result = verify_conclusion(answer, _evidence(), _grounding_config())

    assert result["ok"] is True
    report = result["claims"][0]
    assert report["claim_type"] == "hypothesis"
    assert "hypothesis_contains_unverified_anchor" in report["warnings"]


def test_fallback_remove_fato_ruim_e_preserva_recomendacao():
    fact = "O `TIMEOUT` já está configurado como `600` segundos (config.py:1-2)."
    recommendation = "Recomendo testar `600` segundos em uma execução controlada."
    answer = fact + " " + recommendation
    annotations = [{"claim": recommendation, "type": "recommendation"}]
    result = verify_conclusion(
        answer, _evidence(), _grounding_config(), claim_annotations=annotations,
    )

    fallback = build_safe_grounded_answer(answer, result, _evidence())
    fallback_check = verify_conclusion(
        fallback, _evidence(), _grounding_config(), claim_annotations=annotations,
    )

    assert "já está configurado" not in fallback
    assert "Recomendo testar" in fallback
    assert fallback_check["ok"] is True


def test_feedback_explica_regras_sem_castrar_decisoes():
    result = verify_conclusion(
        "O `TIMEOUT` é `600` (config.py:1-2).",
        _evidence(),
        _grounding_config(),
    )
    feedback = format_grounding_feedback(result)

    assert "Rejected claim (type=fact)" in feedback
    assert "Decisions and recommendations may introduce new choices" in feedback
    assert "Preserve valid inferences" in feedback
    assert "Do not ask the user" in feedback


def test_agente_esgotado_preserva_recomendacao_sem_needs_user(tmp_path, monkeypatch):
    (tmp_path / "config.py").write_text(
        "TIMEOUT = 315\nMAX_RETRIES = 1\n", encoding="utf-8",
    )
    fact = "O `TIMEOUT` já está configurado como `600` segundos (config.py:1-2)."
    recommendation = "Recomendo testar `600` segundos em uma execução controlada."
    final = json.dumps({
        "final": {
            "answer": fact + " " + recommendation,
            "evidence_ids": ["ev-0001"],
            "verification": "faixa lida",
            "limitations": [],
            "claim_annotations": [{
                "claim": recommendation,
                "type": "recommendation",
            }],
        }
    }, ensure_ascii=False)
    responses = iter([
        json.dumps({
            "tool": "read_range",
            "arguments": {
                "caminho_relativo": "config.py",
                "linha_inicio": 1,
                "linha_fim": 2,
            },
        }),
        final,
        final,
        final,
    ])

    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(responses))
    status, text, pending, details = agent_mod.executar_agente(
        "analise config.py",
        _agent_config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert pending is None
    assert "já está configurado" not in text
    assert "Recomendo testar" in text
    assert details["grounding_fallback_applied"] is True
    assert details["semantic_grounding"]["typed"] is True
