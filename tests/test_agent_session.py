import hashlib
import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.tools as tools


def config(tmp_path):
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
            "final_validation_retries": 1,
            "chat_history_token_budget": 1200,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
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


def test_greeting_is_written_by_the_same_agent(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: calls.append(json.loads(prompt)) or '{"final":"Oiii! Bora mexer em código? 😄"}')
    status, text, pending, details = core_agent.executar_agente(
        "Oiii Eyle", config(tmp_path), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Oiii")
    assert pending is None
    assert len(calls) == 1
    assert calls[0]["available_tools"] == []


def test_analysis_is_one_agent_loop_without_mission_interpreter(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    outputs = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"},"plan":["ler o arquivo","explicar"]}',
        '{"final":{"answer":"A função soma retorna a adição dos dois argumentos.","evidence_ids":["ev-0001"]}}',
    ])
    prompts = []
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    status, text, _, details = core_agent.executar_agente(
        "Analise o projeto", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "adição" in text
    assert len(prompts) == 2
    assert "mission" not in prompts[0]
    assert prompts[1]["latest_tool_results"][0]["detail"]["conteudo"].startswith("def soma")
    assert details["plan"] == ["ler o arquivo", "explicar"]


def test_edit_produces_confirmation_and_resume_is_deterministic(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "def soma(a, b):\n    return a + b\n"
    app.write_text(original, encoding="utf-8")
    outputs = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        outputs.append(payload)
        if len(outputs) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        detail = payload["latest_tool_results"][0]["detail"]
        return json.dumps({
            "tool": "test_patch_dry_run",
            "arguments": {
                "caminho_relativo": "app.py",
                "linha_inicio": 1,
                "linha_fim": 2,
                "codigo_novo": "def soma(a, b):\n    return a + b + 1",
                "file_hash_esperado": detail["file_hash"],
                "range_hash_esperado": detail["content_hash"],
            },
        })

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "Altere soma para adicionar um", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert app.read_text(encoding="utf-8") == original
    assert pending["tool_pendente"]["tool"] == "apply_patch"
    assert "id" not in pending and "objetivo" not in pending and "task_id" not in pending

    status, text, _, details = core_agent.executar_agente(
        pending["estado"]["request"], config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "success"
    assert "releitura" in text
    assert "+ 1" in app.read_text(encoding="utf-8")
    assert details["llm_usage"].get("llm_calls", 0) == 0


def test_external_memory_is_only_loaded_by_tool(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    file_hash = hashlib.sha256(app.read_bytes()).hexdigest()
    evidence = {
        "ev-0001": {"arquivo": "app.py", "file_hash": file_hash},
    }
    context = {"config": config(tmp_path), "projeto": {"caminho_origem": str(tmp_path)}, "evidence": evidence}
    monkeypatch.setattr(tools, "MEMORY_DIR", str(tmp_path / "memory"))
    stored = tools.executar_tool("memory_store", {
        "text": "app.py define VALUE", "kind": "fact", "evidence_ids": ["ev-0001"],
    }, context)
    assert stored["ok"] is True
    found = tools.executar_tool("memory_search", {"query": "VALUE"}, context)
    assert found["detail"]["count"] == 1


def test_exact_identical_loop_has_one_simple_guard(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda p, c: '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}')
    cfg = config(tmp_path)
    cfg["agent"]["max_identical_tool_repeats"] = 2
    status, text, _, details = core_agent.executar_agente(
        "analise", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "IDENTICAL_TOOL_LOOP"
    assert "mesma ferramenta" in text


def test_reported_phrase_uses_one_session_and_can_finish(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    outputs = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}',
        '{"final":{"answer":"O projeto define x com valor 1.","evidence_ids":["ev-0001"]}}',
    ])
    prompts = []
    monkeypatch.setattr(
        core_agent, "executar_agente_llm",
        lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs),
    )
    status, text, _, _ = core_agent.executar_agente(
        "Oi Eyle, faça uma analize do projeto", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "valor 1" in text
    assert len(prompts) == 2
    assert all("mission" not in prompt and "project_memory" not in prompt for prompt in prompts)


def test_disabled_tests_are_not_advertised(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    prompts = []
    monkeypatch.setattr(
        core_agent, "executar_agente_llm",
        lambda prompt, cfg: prompts.append(json.loads(prompt)) or '{"final":"ok"}',
    )
    status, *_ = core_agent.executar_agente(
        "olhe o projeto", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    names = {item["name"] for item in prompts[0]["available_tools"]}
    assert "run_tests" not in names


def test_removed_reasoning_modules_are_physically_absent():
    root = Path(__file__).resolve().parents[1]
    removed = [
        "eyle/core/mission.py",
        "eyle/core/state.py",
        "eyle/core/project_memory.py",
        "eyle/core/context_compiler.py",
        "ingest.py",
        "engine",
        "llm/cache.py",
    ]
    assert all(not (root / relative).exists() for relative in removed)


def test_pending_state_does_not_duplicate_raw_source(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("def f():\n    return 1\n", encoding="utf-8")
    outputs = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        outputs.append(payload)
        if len(outputs) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        detail = payload["latest_tool_results"][0]["detail"]
        return json.dumps({
            "tool": "test_patch_dry_run",
            "arguments": {
                "caminho_relativo": "app.py", "linha_inicio": 1, "linha_fim": 2,
                "codigo_novo": "def f():\n    return 2",
                "file_hash_esperado": detail["file_hash"],
                "range_hash_esperado": detail["content_hash"],
            },
        })

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "altere a função f no arquivo app.py", config(tmp_path), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"
    serialized = json.dumps(pending["estado"], ensure_ascii=False)
    assert "def f" not in serialized
    assert "return 1" not in serialized


def test_common_full_file_patch_shape_reaches_confirmation_without_hashes(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "from flask import Flask\n\napp = Flask(__name__)\n"
    updated = (
        "from flask import Flask\n\n"
        "app = Flask(__name__)\n\n"
        "@app.get('/amor')\n"
        "def amor():\n"
        "    return '<h1 style=\"text-align:center\">Amor</h1>'\n"
    )
    app.write_text(original, encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        return json.dumps({"patches": [{"path": "app.py", "content": updated}]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "Crie uma rota /amor com HTML responsivo", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
        conversation_context={"recent_messages": [{"role": "user", "content": "contexto antigo"}]},
    )
    assert status == "needs_user"
    assert pending["tool_pendente"]["tool"] == "apply_patch_set"
    patch = pending["tool_pendente"]["arguments"]["patches"][0]
    assert patch["operation"] == "replace"
    assert patch["path"] == "app.py"
    assert patch["content"] == updated
    assert len(prompts) == 2
    assert prompts[0]["recent_context"]["recent_messages"]
    assert prompts[1]["recent_context"]["recent_messages"] == []
    assert details["turns"] == 2


def test_multi_file_replace_and_create_reaches_confirmation(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    outputs = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        outputs.append(payload)
        if len(outputs) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        return json.dumps({
            "patches": [
                {"operation": "update", "path": "app.py", "content": "from flask import Flask\nfrom routes import register_routes\napp = Flask(__name__)\nregister_routes(app)\n"},
                {"path": "routes.py", "new_code": "def register_routes(app):\n    @app.get('/')\n    def index():\n        return 'ok'\n"},
                {"operation": "create", "path": "tests/test_routes.py", "content": "def test_placeholder():\n    assert True\n"},
            ]
        })

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "Separe as rotas em outro arquivo, preserve o comportamento atual e crie testes.",
        config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    patches = pending["tool_pendente"]["arguments"]["patches"]
    assert [patch["operation"] for patch in patches] == ["replace", "create", "create"]
    assert [patch["path"] for patch in patches] == ["app.py", "routes.py", "tests/test_routes.py"]


def test_failed_patch_keeps_source_for_one_correction(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        if len(prompts) == 2:
            return json.dumps({"patches": [{"path": "app.py", "content": "def broken(:\n"}]})
        assert any(item.get("tool") == "read_file" for item in payload["latest_tool_results"])
        assert any(item.get("tool") == "test_patch_set_dry_run" and item.get("ok") is False for item in payload["latest_tool_results"])
        return json.dumps({"patches": [{"path": "app.py", "content": "x = 2\n"}]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, details = core_agent.executar_agente(
        "mude x para 2", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert pending["tool_pendente"]["tool"] == "apply_patch_set"
    assert details["turns"] == 3


def test_single_range_patch_accepts_english_keys_and_fills_hashes(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("x = 1\ny = 2\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        return json.dumps({
            "tool": "test_patch_dry_run",
            "arguments": {"path": "app.py", "line_start": 1, "line_end": 1, "new_code": "x = 3"},
        })

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, _, pending, _ = core_agent.executar_agente(
        "mude x no arquivo app.py", config(tmp_path), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"
    args = pending["tool_pendente"]["arguments"]
    assert args["file_hash_esperado"]
    assert args["range_hash_esperado"]


def test_confirmed_full_file_replace_is_applied_without_another_llm_call(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "x = 1\n"
    updated = "x = 2\n"
    app.write_text(original, encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        return json.dumps({"patches": [{"path": "app.py", "content": updated}]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    cfg = config(tmp_path)
    status, _, pending, _ = core_agent.executar_agente(
        "mude x para 2", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert app.read_text(encoding="utf-8") == original

    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda *_: (_ for _ in ()).throw(AssertionError("LLM must not run on confirmation")))
    status, text, _, _ = core_agent.executar_agente(
        pending["estado"]["request"], cfg,
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "success"
    assert "Transação aplicada" in text
    assert app.read_text(encoding="utf-8") == updated


def test_patch_aliases_still_require_fresh_evidence(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    calls = []

    def fake(prompt, cfg):
        calls.append(json.loads(prompt))
        return json.dumps({"patches": [{"operation": "remove", "path": "app.py"}]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    cfg = config(tmp_path)
    cfg["agent"]["max_patch_dry_run_failures"] = 1
    status, text, pending, details = core_agent.executar_agente(
        "apague app.py", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "failed"
    assert pending is None
    assert details["failure_code"] == "PATCH_SCHEMA_INVALID"
    assert "read the existing file" in text
    assert app.exists()


def test_identical_read_is_blocked_without_second_disk_access(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) <= 2:
            return '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}'
        results = payload["latest_tool_results"]
        assert any(item.get("tool") == "read_file" and item.get("ok") is True for item in results)
        assert any(item.get("error_code") == "IDENTICAL_READ_BLOCKED" for item in results)
        return '{"final":{"answer":"x vale 1.","evidence_ids":["ev-0001"]}}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    cfg = config(tmp_path)
    cfg["agent"]["max_identical_tool_repeats"] = 2
    status, text, _, details = core_agent.executar_agente(
        "analise", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert text == "x vale 1."
    assert details["tools_used"] == ["read_file"]
    assert details["turns"] == 3


def test_failed_single_post_write_reread_rolls_back(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "x = 1\n"
    app.write_text(original, encoding="utf-8")
    outputs = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}',
        json.dumps({"patches": [{"path": "app.py", "content": "x = 2\n"}]}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda *_: next(outputs))
    cfg = config(tmp_path)
    status, _, pending, _ = core_agent.executar_agente(
        "mude x no arquivo app.py", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"

    real_execute = core_agent.executar_tool

    def fail_reread(tool, arguments, context):
        if tool == "read_file":
            return {"status": "failed", "ok": False, "executed": True, "changed": False,
                    "error_code": "READ_FAILED", "detail": "forced reread failure"}
        return real_execute(tool, arguments, context)

    monkeypatch.setattr(core_agent, "executar_tool", fail_reread)
    status, text, _, details = core_agent.executar_agente(
        pending["estado"]["request"], cfg,
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "failed"
    assert "restaurados" in text.lower() or "restaurado" in text.lower()
    assert details["failure_code"] == "POST_WRITE_READ_FAILED_ROLLED_BACK"
    assert app.read_text(encoding="utf-8") == original


def test_failed_multi_file_post_write_reread_rolls_back(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "x = 1\n"
    app.write_text(original, encoding="utf-8")
    outputs = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"app.py"}}',
        json.dumps({"patches": [
            {"path": "app.py", "content": "x = 2\n"},
            {"operation": "create", "path": "routes.py", "content": "VALUE = 1\n"},
        ]}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda *_: next(outputs))
    cfg = config(tmp_path)
    status, _, pending, _ = core_agent.executar_agente(
        "refatore o arquivo app.py", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "needs_user"

    real_execute = core_agent.executar_tool

    def fail_reread(tool, arguments, context):
        if tool == "read_file":
            return {"status": "failed", "ok": False, "executed": True, "changed": False,
                    "error_code": "READ_FAILED", "detail": "forced reread failure"}
        return real_execute(tool, arguments, context)

    monkeypatch.setattr(core_agent, "executar_tool", fail_reread)
    status, _, _, details = core_agent.executar_agente(
        pending["estado"]["request"], cfg,
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "POST_WRITE_READ_FAILED_ROLLED_BACK"
    assert app.read_text(encoding="utf-8") == original
    assert not (tmp_path / "routes.py").exists()


def test_patch_path_is_rejected_before_workspace_escape(monkeypatch, tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("KEEP = True\n", encoding="utf-8")
    monkeypatch.setattr(
        core_agent, "executar_agente_llm",
        lambda *_: json.dumps({"patches": [{"operation": "replace", "path": "../outside.py", "content": "KEEP = False\n"}]}),
    )
    cfg = config(tmp_path)
    cfg["agent"]["max_patch_dry_run_failures"] = 1
    status, text, _, details = core_agent.executar_agente(
        "mude o arquivo externo", cfg,
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] == "PATCH_SCHEMA_INVALID"
    assert "unsafe patch path" in text
    assert outside.read_text(encoding="utf-8") == "KEEP = True\n"


def test_write_request_cannot_escape_as_unsupported_factual_final(monkeypatch, tmp_path):
    routes = tmp_path / "routes.py"
    original = "def amor():\n    return '<h1>Amor</h1>'\n"
    updated = "from flask import render_template\n\ndef amor():\n    return render_template('amor.html')\n"
    template = "<h1>Amor</h1>\n"
    routes.write_text(original, encoding="utf-8")
    prompts = []

    def fake(prompt, cfg):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            assert payload["response_quality"]["write_action_required"] is True
            return json.dumps({"final": {
                "answer": "O HTML foi extraído para templates/amor.html.",
                "claims": [{
                    "kind": "fact",
                    "text": "O HTML foi extraído para templates/amor.html.",
                    "evidence_ids": [],
                }],
            }})
        if len(prompts) == 2:
            assert "FINAL_WRITE_ACTION_REQUIRED" in payload["runtime_feedback"]
            return '{"tool":"read_file","arguments":{"caminho_relativo":"routes.py"}}'
        assert payload["latest_tool_results"][0]["detail"]["conteudo"] == original
        return json.dumps({"patches": [
            {"operation": "replace", "path": "routes.py", "content": updated},
            {"operation": "create", "path": "templates/amor.html", "content": template},
        ]})

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, pending, details = core_agent.executar_agente(
        "Extraia o html para templates/amor.html", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert "Proposta transacional pronta" in text
    assert pending["tool_pendente"]["tool"] == "apply_patch_set"
    patches = pending["tool_pendente"]["arguments"]["patches"]
    assert [item["path"] for item in patches] == ["routes.py", "templates/amor.html"]
    assert details["failure_code"] is None


def test_write_intent_gate_is_not_armed_when_editing_is_disabled(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    cfg = config(tmp_path)
    cfg["codar"]["ativado"] = False
    prompts = []

    def fake(prompt, config_value):
        payload = json.loads(prompt)
        prompts.append(payload)
        assert payload["response_quality"]["write_action_required"] is False
        return '{"final":"Não posso alterar arquivos porque a escrita está desativada."}'

    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status, text, pending, _ = core_agent.executar_agente(
        "Altere app.py", cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )
    assert status == "success"
    assert "desativada" in text
    assert pending is None
