import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from tests.canonical import agent_final, agent_patches, agent_tools, base_config, investigation_target, tool_call, workspace_scope


def config(tmp_path, *, claims_mode="off"):
    return base_config(claims_mode=claims_mode, tests_enabled=False)


def test_greeting_is_written_by_same_agent(monkeypatch, tmp_path):
    prompts = []
    monkeypatch.setattr(
        core_agent, "executar_agente_llm",
        lambda prompt, cfg: prompts.append(json.loads(prompt)) or agent_final("Oiii! Bora mexer em código? 😄"),
    )
    status, text, pending, details = core_agent.executar_agente(
        "Oiii Eyle", config(tmp_path), projeto={}, retornar_detalhes=True,
    )
    assert status == "success"
    assert text.startswith("Oiii")
    assert pending is None
    assert len(prompts) == 1
    assert prompts[0]["available_tools"] == []
    assert details["tool_calls"] == 0


def test_analysis_uses_one_agent_loop_and_retained_evidence(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"caminho_relativo": "app.py"}), investigation=[investigation_target(goal="Establish what app.py does")]),
        agent_final(
            {"answer": "A função soma retorna a adição dos argumentos.", "evidence_ids": ["ev-0001"]},
            investigation=[investigation_target(goal="Establish what app.py does", status="established", evidence_ids=["ev-0001"], reason="app.py was read")],
        ),
    ])
    prompts = []
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    status, text, _, details = core_agent.executar_agente(
        "Analise o projeto", config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "adição" in text
    assert len(prompts) == 2
    assert "def soma" in prompts[1]["latest_tool_results"][0]["detail"]["trecho_numerado"]
    assert "conteudo" not in prompts[1]["latest_tool_results"][0]["detail"]
    assert details["investigation"][0]["goal"] == "Establish what app.py does"


def test_transactional_write_requires_confirmation_and_resume_is_deterministic(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "def soma(a, b):\n    return a + b\n"
    updated = "def soma(a, b):\n    return a + b + 1\n"
    app.write_text(original, encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"caminho_relativo": "app.py"}), scope=workspace_scope("write")),
        agent_patches([{"operation": "replace", "path": "app.py", "content": updated}]),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: next(outputs))

    status, _, pending, _ = core_agent.executar_agente(
        "Altere soma para adicionar um", config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    assert app.read_text(encoding="utf-8") == original
    assert pending["continuation_kind"] == "write_confirmation"
    assert pending["write_transaction"]["patches"][0]["path"] == "app.py"

    status, text, pending2, details = core_agent.executar_agente(
        pending["estado"]["request"], config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "success"
    assert pending2 is None
    assert "+ 1" in app.read_text(encoding="utf-8")
    assert "releitura" in text.lower()
    assert details["llm_usage"].get("llm_calls", 0) == 0


def test_pending_transaction_does_not_duplicate_full_source(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    source = "TOKEN_DO_ARQUIVO = 'segredo-local'\n"
    app.write_text(source, encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"caminho_relativo": "app.py"}), scope=workspace_scope("write")),
        agent_patches([{"operation": "replace", "path": "app.py", "content": "TOKEN_DO_ARQUIVO = 'novo'\n"}]),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: next(outputs))
    status, _, pending, _ = core_agent.executar_agente(
        "altere app.py", config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    serialized = json.dumps(pending, ensure_ascii=False)
    assert source.strip() not in serialized
    assert pending["write_transaction"]["patches"][0]["content"] == "TOKEN_DO_ARQUIVO = 'novo'\n"


def test_identical_read_loop_is_bounded(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        core_agent, "executar_agente_llm",
        lambda p, c: agent_tools(tool_call("read_file", {"caminho_relativo": "app.py"})),
    )
    cfg = config(tmp_path)
    cfg["agent"]["max_identical_tool_repeats"] = 2
    status, _, _, details = core_agent.executar_agente(
        "analise", cfg, projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "failed"
    assert details["failure_code"] in {"IDENTICAL_TOOL_LOOP", "AGENT_NO_PROGRESS", "FINAL_PHASE_REQUIRES_ANSWER"}


def test_disabled_tests_are_not_advertised(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x=1\n", encoding="utf-8")
    prompts = []
    outputs = iter([
        agent_tools(tool_call("read_file", {"caminho_relativo": "app.py"})),
        agent_final({"answer": "app.py foi lido.", "evidence_ids": ["ev-0001"]}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    status, *_ = core_agent.executar_agente(
        "olhe o projeto", config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "run_tests" not in {item["name"] for item in prompts[0]["available_tools"]}


def test_external_memory_only_moves_through_tools(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    import hashlib
    file_hash = hashlib.sha256(app.read_bytes()).hexdigest()
    evidence = {"ev-0001": {"arquivo": "app.py", "file_hash": file_hash}}
    context = {"config": config(tmp_path), "projeto": {"caminho_origem": str(tmp_path)}, "evidence": evidence}
    monkeypatch.setattr(tools, "MEMORY_DIR", str(tmp_path / "memory"))
    stored = tools.executar_tool("memory_store", {"text": "app.py define VALUE", "kind": "fact", "evidence_ids": ["ev-0001"]}, context)
    assert stored["ok"] is True
    found = tools.executar_tool("memory_search", {"query": "VALUE"}, context)
    assert found["detail"]["count"] == 1


def test_removed_reasoning_modules_are_physically_absent():
    root = Path(__file__).resolve().parents[1]
    removed = [
        "eyle/core/mission.py", "eyle/core/state.py", "eyle/core/project_memory.py",
        "eyle/core/context_compiler.py", "eyle/core/retention.py", "ingest.py", "engine", "llm/cache.py",
    ]
    assert all(not (root / relative).exists() for relative in removed)
