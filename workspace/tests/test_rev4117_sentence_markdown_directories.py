import json
from pathlib import Path

import eyle.core.agent as core_agent
from eyle.core.response_quality import (
    request_needs_project_evidence,
    request_requires_write,
)
from eyle.core.validation import validate_final


def _config():
    return {
        "llm": {
            "context_window_tokens": 10000,
            "agent_decision_max_tokens": 1100,
            "agent_patch_max_tokens": 3600,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "cached_prompt_weight": 0.2,
        },
        "agent": {
            "max_llm_turns": 6,
            "max_tool_calls": 12,
            "max_identical_tool_repeats": 2,
            "protocol_parse_retries": 1,
            "final_validation_retries": 1,
            "max_patch_dry_run_failures": 2,
            "max_write_investigation_turns": 2,
            "max_no_progress_turns": 2,
            "max_phase_violations": 1,
            "chat_history_token_budget": 700,
            "task_context_token_budget": 500,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "response_quality": {"enabled": True},
        },
        "codar": {"ativado": True, "fazer_backup": False, "testes": {"ativado": False}},
        "_runtime_agent_budget": {
            "max_llm_calls": 8,
            "max_prompt_tokens": 12000,
            "max_completion_tokens": 6000,
            "max_total_tokens": 18000,
            "llm_calls": 0,
            "llm_requests": 0,
            "prompt_tokens_reserved": 0,
            "prompt_tokens_actual": 0,
            "prompt_tokens_effective": 0,
            "generated_tokens": 0,
        },
    }


def test_sentence_index_claim_does_not_duplicate_answer_text():
    final = {
        "answer": "### Estrutura\nO projeto usa Flask.\nA rota principal é `/amor`.",
        "claims": [
            {"kind": "fact", "sentence": 1, "evidence_ids": ["ev-1"]},
            {"kind": "fact", "sentence": 2, "evidence_ids": ["ev-2"]},
        ],
    }
    ok, reason, answer, _, claims, _ = validate_final(
        final,
        {
            "ev-1": {"arquivo": "app.py", "file_hash": "a"},
            "ev-2": {"arquivo": "app.py", "file_hash": "a"},
        },
        request="Faça uma análise do projeto",
        project_available=True,
        quality_enabled=True,
    )
    assert ok is True
    assert reason == "ok"
    assert answer == final["answer"]
    assert claims[0]["text"] == "O projeto usa Flask."
    assert claims[0]["sentence"] == 1
    assert claims[1]["text"] == "A rota principal é `/amor`."


def test_sentence_index_out_of_range_is_rejected():
    ok, reason, *_ = validate_final(
        {
            "answer": "O projeto usa Flask.",
            "claims": [{"kind": "fact", "sentence": 2, "evidence_ids": ["ev-1"]}],
        },
        {"ev-1": {"arquivo": "app.py", "file_hash": "a"}},
        request="Analise o projeto",
        project_available=True,
        quality_enabled=True,
    )
    assert ok is False
    assert reason == "FINAL_CLAIM_SENTENCE_OUT_OF_RANGE:1:2>1"


def test_portuguese_bring_command_is_a_write_request():
    assert request_requires_write("Traga o HTML também para o app.py", True) is True
    assert request_requires_write("Embuta o HTML no app.py", True) is True


def test_directory_state_question_requires_real_project_evidence():
    assert request_needs_project_evidence("Deletou a pasta templates?", True) is True
    assert request_needs_project_evidence("A pasta templates ainda existe?", True) is True


def test_list_tree_becomes_citable_structural_evidence(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "amor.html").write_text("<h1>Amor</h1>\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert payload["runtime_phase"] == "analysis_investigate"
            return '{"tool":"list_tree","arguments":{"filtro":"templates"}}'
        assert payload["evidence_index"][0]["source_type"] == "workspace_tree"
        return json.dumps({"final": {
            "answer": "A pasta templates existe e contém amor.html.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "A pasta templates ainda existe?",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "existe" in text
    assert details["tool_calls"] == 1
    assert details["evidence"][0]["source_type"] == "workspace_tree"


def test_delete_directory_request_deletes_files_and_prunes_empty_folder(monkeypatch, tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "amor.html").write_text("<h1>Amor</h1>\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"list_tree","arguments":{"filtro":"templates"}}'
        if len(prompts) == 2:
            assert payload["runtime_phase"] == "write_prepare"
            return '{"tool":"read_file","arguments":{"caminho_relativo":"templates/amor.html"}}'
        assert payload["runtime_phase"] == "write_patch_only"
        return json.dumps({"patches": [
            {"operation": "delete", "path": "templates/amor.html"},
        ]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "Delete a pasta de templates",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending is not None

    status, text, _, _ = core_agent.executar_agente(
        "",
        _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retomar=pending,
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "Transação aplicada" in text
    assert not templates.exists()


def test_frontend_uses_safe_markdown_dom_renderer():
    script = Path("web/static/app.js").read_text(encoding="utf-8")
    assert "function renderMarkdownSafe" in script
    assert 'document.createElement("strong")' in script
    assert 'renderMarkdownSafe(bubble, msg.text)' in script
    assert "bubble.textContent = msg.text" not in script
    renderer = script.split("function renderMarkdownSafe", 1)[1].split("function syncDeleteState", 1)[0]
    assert ".innerHTML" not in renderer


def test_directory_pruning_is_reversed_when_later_transaction_step_fails(monkeypatch, tmp_path):
    import eyle.core.transactions as transactions

    templates = tmp_path / "templates"
    templates.mkdir()
    original_html = "<h1>Amor</h1>\n"
    (templates / "amor.html").write_text(original_html, encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    dry = transactions.dry_run_patch_set(str(tmp_path), [
        {"operation": "delete", "path": "templates/amor.html"},
        {"operation": "replace", "path": "app.py", "content": "value = 2\n"},
    ])
    assert dry["ok"] is True

    real_write = transactions._escrever_arquivo_atomico
    calls = {"count": 0}

    def fail_once(path, content):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated write failure")
        return real_write(path, content)

    monkeypatch.setattr(transactions, "_escrever_arquivo_atomico", fail_once)
    result = transactions.apply_patch_set(str(tmp_path), dry["prepared_patches"])

    assert result["ok"] is False
    assert (templates / "amor.html").read_text(encoding="utf-8") == original_html
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
