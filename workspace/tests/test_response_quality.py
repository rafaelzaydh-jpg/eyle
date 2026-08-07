import json

import eyle.core.agent as core_agent
from eyle.core.response_quality import (
    requested_finding_constraints, requested_finding_limit,
    request_needs_project_evidence, request_requires_write,
)
from eyle.core.validation import validate_final


def _config():
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1400,
            "agent_patch_max_tokens": 4200,
        },
        "context_engine": {"safety_margin_tokens": 500, "chars_per_token_fallback": 3},
        "agent": {
            "max_llm_turns": 8,
            "max_tool_calls": 16,
            "max_identical_tool_repeats": 2,
            "protocol_parse_retries": 1,
            "final_validation_retries": 2,
            "chat_history_token_budget": 1200,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "response_quality": {
                "enabled": True,
                "max_relevant_sources": 4,
                "max_relevant_source_chars": 8000,
                "reject_mid_list_corrections": True,
            },
        },
        "codar": {"ativado": True, "fazer_backup": False, "testes": {"ativado": False}},
        "_runtime_agent_budget": {
            "max_llm_calls": 20,
            "max_prompt_tokens": 12000,
            "max_completion_tokens": 6000,
            "max_total_tokens": 18000,
            "llm_calls": 0,
            "llm_requests": 0,
        },
    }


def test_explicit_finding_limits_are_extracted_multilingual():
    assert requested_finding_limit("Liste até 3 bugs") == 3
    assert requested_finding_limit("Show up to 5 risks") == 5
    assert requested_finding_limit("Muestra como máximo 2 problemas") == 2
    assert requested_finding_limit("Liste bugs") is None
    constraints = requested_finding_constraints("Liste até 3 bugs e até 5 recomendações")
    assert constraints == {"overall": 8, "by_kind": {"bug": 3, "recommendation": 5}}
    generic = requested_finding_constraints("Liste até 3 pontos, incluindo bugs")
    assert generic == {"overall": 3, "by_kind": {}}
    assert request_needs_project_evidence("Verifique app.py", True) is True
    assert request_needs_project_evidence("Explique recursão", True) is False
    assert request_needs_project_evidence("O que é uma função?", True) is False
    assert request_needs_project_evidence("Analise a função soma", True) is True


def test_direct_write_intent_is_distinguished_from_questions_and_analysis():
    assert request_requires_write("Extraia o html para templates/amor.html", True) is True
    assert request_requires_write("No routes.py, separe o HTML em um template", True) is True
    assert request_requires_write("Como extraio o HTML para um template?", True) is False
    assert request_requires_write("Faça uma análise do projeto", True) is False
    assert request_requires_write("Extraia o HTML", False) is False


def test_project_fact_requires_read_then_registers_claim_ledger(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"final":{"answer":"app.py define VALUE como 1.","claims":[{"kind":"fact","text":"app.py define VALUE como 1.","evidence_ids":[]}]}}'
        if len(prompts) == 2:
            assert "FINAL_PROJECT_FACTS_REQUIRE_READ" in payload["runtime_feedback"]
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        assert payload["latest_tool_results"][0]["detail"]["conteudo"] == "VALUE = 1\n"
        assert payload["relevant_sources"] == []
        return '{"final":{"answer":"app.py define VALUE como 1.","claims":[{"kind":"fact","text":"app.py define VALUE como 1.","evidence_ids":["ev-0001"]}]}}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Analise o código do projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "app.py define VALUE como 1."
    assert details["claim_evidence"] == [{
        "kind": "fact",
        "text": "app.py define VALUE como 1.",
        "sources": [{
            "evidence_id": "ev-0001",
            "file": "app.py",
            "lines": [1, 1],
            "file_hash": details["evidence"][0]["file_hash"],
            "content_hash": details["evidence"][0]["content_hash"],
        }],
    }]


def test_runtime_enforces_up_to_limit_and_keeps_categories_distinct(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert payload["response_quality"]["requested_finding_limit"] == 2
            assert payload["response_quality"]["requested_kind_limits"] == {
                "bug": 1, "recommendation": 1,
            }
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        if len(prompts) == 2:
            return json.dumps({"final": {
                "answer": "Bug A. Bug B.",
                "claims": [
                    {"kind": "bug", "text": "Bug A.", "evidence_ids": ["ev-0001"]},
                    {"kind": "bug", "text": "Bug B.", "evidence_ids": ["ev-0001"]},
                ],
            }})
        assert "FINAL_KIND_LIMIT_EXCEEDED:bug:2>1" in payload["runtime_feedback"]
        return json.dumps({"final": {
            "answer": "Bug A. Recomendação C.",
            "claims": [
                {"kind": "bug", "text": "Bug A.", "evidence_ids": ["ev-0001"]},
                {"kind": "recommendation", "text": "Recomendação C.", "evidence_ids": []},
            ],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, _, details = core_agent.executar_agente(
        "Liste até 1 bug e até 1 recomendação no código", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert [item["kind"] for item in details["claim_evidence"]] == ["bug", "recommendation"]


def test_relevant_source_survives_a_later_non_read_result(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        if len(prompts) == 2:
            return '{"tool":"list_tree","arguments":{}}'
        assert payload["latest_tool_results"][0]["tool"] == "list_tree"
        assert payload["relevant_sources"][0]["conteudo"] == "VALUE = 1\n"
        return '{"final":{"answer":"app.py define VALUE como 1.","claims":[{"kind":"fact","text":"app.py define VALUE como 1.","evidence_ids":["ev-0001"]}]}}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, *_ = core_agent.executar_agente(
        "Analise a estrutura e o código do projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"


def test_mid_list_self_correction_is_rejected():
    evidence = {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}}
    final = {
        "answer": "- Há um bug.\n- Na verdade, não é bug.",
        "claims": [
            {"kind": "bug", "text": "Há um bug.", "evidence_ids": ["ev-0001"]},
            {"kind": "risk", "text": "Na verdade, não é bug.", "evidence_ids": ["ev-0001"]},
        ],
    }
    ok, reason, *_ = validate_final(
        final, evidence,
        request="Analise o código", project_available=True,
        quality_enabled=True, reject_mid_list_corrections=True,
    )
    assert ok is False
    assert reason == "FINAL_MID_LIST_CORRECTION"


def test_verified_bug_cannot_exist_without_evidence():
    final = {
        "answer": "Há um bug confirmado.",
        "claims": [{"kind": "bug", "text": "Há um bug confirmado.", "evidence_ids": []}],
    }
    ok, reason, *_ = validate_final(
        final, {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}},
        request="Analise o código", project_available=True, quality_enabled=True,
    )
    assert ok is False
    assert reason == "FINAL_CLAIM_REQUIRES_EVIDENCE:1:bug"


def test_response_quality_config_is_type_checked():
    import pytest
    from eyle.runtime.config import ConfigError, validar_config

    valid = validar_config({"agent": {"response_quality": {
        "enabled": True,
        "max_relevant_sources": 4,
        "max_relevant_source_chars": 8000,
        "reject_mid_list_corrections": True,
    }}})
    assert valid["agent"]["response_quality"]["enabled"] is True
    with pytest.raises(ConfigError, match="max_relevant_sources"):
        validar_config({"agent": {"response_quality": {"max_relevant_sources": 0}}})


def test_close_claim_paraphrase_aligns_to_visible_answer_sentence():
    answer = "O projeto analisado é uma aplicação Flask simples."
    final = {
        "answer": answer,
        "claims": [{
            "kind": "fact",
            "text": "O projeto é uma aplicação Flask simples.",
            "evidence_ids": ["ev-0001"],
        }],
    }
    ok, reason, rendered, _, claims, _ = validate_final(
        final, {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}},
        request="Faça uma análise do projeto", project_available=True,
        quality_enabled=True,
    )
    assert ok is True
    assert reason == "ok"
    assert rendered == answer
    assert claims[0]["text"] == answer
    assert claims[0]["original_text"] == "O projeto é uma aplicação Flask simples."
    assert claims[0]["alignment_method"] == "deterministic_answer_segment"


def test_claim_alignment_rejects_changed_framework():
    final = {
        "answer": "O projeto usa Flask.",
        "claims": [{
            "kind": "fact",
            "text": "O projeto usa Django.",
            "evidence_ids": ["ev-0001"],
        }],
    }
    ok, reason, *_ = validate_final(
        final, {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}},
        request="Faça uma análise do projeto", project_available=True,
        quality_enabled=True,
    )
    assert ok is False
    assert reason == "FINAL_CLAIM_NOT_IN_ANSWER:1"


def test_claim_alignment_rejects_reversed_polarity():
    final = {
        "answer": "O projeto não possui testes.",
        "claims": [{
            "kind": "fact",
            "text": "O projeto possui testes.",
            "evidence_ids": ["ev-0001"],
        }],
    }
    ok, reason, *_ = validate_final(
        final, {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}},
        request="Faça uma análise do projeto", project_available=True,
        quality_enabled=True,
    )
    assert ok is False
    assert reason == "FINAL_CLAIM_NOT_IN_ANSWER:1"


def test_agent_accepts_minor_claim_wording_drift_without_retry(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        return json.dumps({"final": {
            "answer": "O projeto analisado é uma aplicação Flask simples.",
            "claims": [{
                "kind": "fact",
                "text": "O projeto é uma aplicação Flask simples.",
                "evidence_ids": ["ev-0001"],
            }],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Faça uma análise do projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert len(prompts) == 2
    assert text == "O projeto analisado é uma aplicação Flask simples."
    assert details["claim_evidence"][0]["original_text"] == (
        "O projeto é uma aplicação Flask simples."
    )


def test_invalid_claim_feedback_requests_sentence_index(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        if len(prompts) == 2:
            return json.dumps({"final": {
                "answer": "O projeto usa Flask.",
                "claims": [{
                    "kind": "fact",
                    "sentence": 2,
                    "evidence_ids": ["ev-0001"],
                }],
            }})
        assert "1-based index" in payload["runtime_feedback"]
        return json.dumps({"final": {
            "answer": "O projeto usa Flask.",
            "claims": [{
                "kind": "fact",
                "sentence": 1,
                "evidence_ids": ["ev-0001"],
            }],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, _ = core_agent.executar_agente(
        "Faça uma análise do projeto", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert len(prompts) == 3
    assert text == "O projeto usa Flask."


def test_claim_alignment_rejects_changed_named_code_symbol():
    final = {
        "answer": "A função subtrai retorna o resultado corretamente.",
        "claims": [{
            "kind": "fact",
            "text": "A função soma retorna o resultado corretamente.",
            "evidence_ids": ["ev-0001"],
        }],
    }
    ok, reason, *_ = validate_final(
        final, {"ev-0001": {"arquivo": "app.py", "file_hash": "abc"}},
        request="Analise a função soma", project_available=True,
        quality_enabled=True,
    )
    assert ok is False
    assert reason == "FINAL_CLAIM_NOT_IN_ANSWER:1"
