import json

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from eyle.core.response_quality import validate_response_quality


def _config():
    return {
        "app_version": "2.7.4",
        "revision": "4.11.8-project-intelligence-tools",
        "llm": {
            "model": "auto",
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
            "max_project_scan_entries": 20000,
            "max_project_scan_depth": 32,
            "max_project_file_bytes": 4 * 1024 * 1024,
            "max_inspect_relation_edges": 60,
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


def _ctx(root):
    return {"projeto": {"caminho_origem": str(root)}, "config": _config()}


def test_calculator_is_safe_and_exact_for_decimal_arithmetic(tmp_path):
    result = tools.executar_tool("calculate", {"expression": "16 + 16 * 2"}, _ctx(tmp_path))
    assert result["ok"] is True
    assert result["detail"]["result"] == "48"
    assert result["detail"]["exact"] is True

    blocked = tools.executar_tool(
        "calculate", {"expression": "__import__('os').system('echo nope')"}, _ctx(tmp_path),
    )
    assert blocked["ok"] is False
    assert blocked["error_code"] == "INVALID_EXPRESSION"


def test_project_stats_measures_safe_text_project(tmp_path):
    (tmp_path / "app.py").write_text("print('oi')\n", encoding="utf-8")
    (tmp_path / "page.html").write_text("<h1>Oi</h1>\n", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\x00\x01")
    result = tools.executar_tool("project_stats", {}, _ctx(tmp_path))
    detail = result["detail"]
    assert result["ok"] is True
    assert detail["files"] == 2
    assert detail["measured_files"] == 2
    assert detail["by_language"] == {"HTML": 1, "Python": 1}
    assert detail["lines"] == 2
    assert detail["scan_hash"]


def test_count_tokens_is_truthful_about_heuristic(tmp_path):
    (tmp_path / "app.py").write_text("abcdef" * 10, encoding="utf-8")
    result = tools.executar_tool(
        "count_tokens", {"tokenizer": "qwen"}, _ctx(tmp_path),
    )
    detail = result["detail"]
    assert result["ok"] is True
    assert detail["tokenizer_requested"] == "qwen"
    assert detail["exact"] is False
    assert detail["characters"] == 60
    assert detail["estimated_tokens"] == 20
    assert detail["tokenizer_used"] == "heuristic:characters_per_token"


def test_inspect_project_returns_signals_not_importance(tmp_path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\nfrom routes import bp\n"
        "app = Flask(__name__)\n"
        "if __name__ == '__main__':\n    app.run()\n",
        encoding="utf-8",
    )
    (tmp_path / "routes.py").write_text(
        "from flask import Blueprint\nbp = Blueprint('x', __name__)\n"
        "@bp.route('/x')\ndef x():\n    return 'x'\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    result = tools.executar_tool("inspect_project", {}, _ctx(tmp_path))
    detail = result["detail"]
    assert result["ok"] is True
    assert detail["test_signals"]["has_tests"] is True
    assert any(item["name"] == "Flask" for item in detail["framework_signals"])
    edges = detail["relation_signals"]["local_import_edges"]
    assert {"from": "app.py", "to": "routes.py"} in edges
    routes = next(item for item in detail["relation_signals"]["route_files"] if item["path"] == "routes.py")
    assert routes["route_decorator_count"] == 1
    imported = next(item for item in detail["relation_signals"]["most_imported_files"] if item["path"] == "routes.py")
    assert imported["imported_by_count"] == 1
    serialized = json.dumps(detail).lower()
    assert '"important"' not in serialized
    assert '"importance"' not in serialized
    assert "objective signals only" in detail["policy"].lower()


def test_greeting_stays_tool_free_but_self_question_gets_agent_info(monkeypatch, tmp_path):
    payloads = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        payloads.append(payload)
        return '{"final":"Oi!"}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, *_ = core_agent.executar_agente("Oi Eyle", _config(), projeto={}, retornar_detalhes=True)
    assert status == "success"
    assert payloads[0]["available_tools"] == []

    payloads.clear()
    status, *_ = core_agent.executar_agente(
        "quais ferramentas tem?", _config(), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert [item["name"] for item in payloads[0]["available_tools"]] == ["agent_info"]
    assert "agent_info" in payloads[0]["tool_guidance"]


def test_agent_can_answer_its_tool_list_from_runtime_evidence(monkeypatch, tmp_path):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"agent_info","arguments":{}}'
        assert payload["latest_tool_results"][0]["tool"] == "agent_info"
        assert payload["latest_tool_results"][0]["evidence_ids"] == ["ev-0001"]
        return json.dumps({"final": {
            "answer": "Tenho ferramentas de leitura, medição, cálculo e edição supervisionada.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "quais ferramentas tem?", _config(), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "ferramentas" in text
    assert details["evidence"][0]["source_type"] == "agent_runtime"


def test_token_question_exposes_count_tokens_and_cites_measurement(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n" * 10, encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            names = {item["name"] for item in payload["available_tools"]}
            assert {"project_stats", "count_tokens", "inspect_project"} <= names
            assert "count_tokens" in payload["tool_guidance"]
            return '{"tool":"count_tokens","arguments":{"tokenizer":"qwen"}}'
        detail = payload["latest_tool_results"][0]["detail"]
        assert detail["exact"] is False
        return json.dumps({"final": {
            "answer": f"A estimativa atual é de {detail['estimated_tokens']} tokens; ela não é uma contagem exata do tokenizer Qwen.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": ["ev-0001"]}],
        }})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Quantos tokens tem esse projeto?", _config(),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "não é uma contagem exata" in text
    assert details["evidence"][0]["source_type"] == "count_tokens"


def test_general_structured_claim_without_project_evidence_no_longer_fails():
    ok, reason, claims, _ = validate_response_quality(
        {
            "answer": "Sou Eyle, um agente de código.",
            "claims": [{"kind": "fact", "sentence": 1, "evidence_ids": []}],
        },
        "Sou Eyle, um agente de código.",
        {},
        request="quem é você?",
        project_available=True,
        enabled=True,
    )
    assert ok is True
    assert reason == "ok"
    assert claims == []


def test_agent_uses_calculator_without_project(monkeypatch):
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert [item["name"] for item in payload["available_tools"]] == ["calculate"]
            return '{"tool":"calculate","arguments":{"expression":"16+16"}}'
        assert payload["latest_tool_results"][0]["detail"]["result"] == "32"
        return '{"final":"16 + 16 = 32."}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, _, details = core_agent.executar_agente(
        "Quanto é 16+16?", _config(), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "16 + 16 = 32."
    assert details["tool_calls"] == 1
