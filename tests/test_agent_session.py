import json
from pathlib import Path

import eyle.core.agent as core_agent
import eyle.core.tools as tools
from tests.canonical import agent_final, agent_patches, agent_tools, base_config, investigation_target, tool_call


def config(tmp_path):
    return base_config(tests_enabled=False)


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
    index = prompts[0]["available_capabilities"]
    assert any(item.startswith("calculate(") for item in index)
    assert not any(item.startswith("agent_info(") for item in index)
    assert not any(item.startswith("execution_trace(") for item in index)
    assert prompts[0]["active_tools"] == []
    assert details["tool_calls"] == 0


def test_analysis_uses_one_agent_loop_and_retained_evidence(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("def soma(a, b):\n    return a + b\n", encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"}), investigation=[investigation_target(goal="Establish what app.py does")]),
        agent_final(
            {"answer": "A função soma retorna a adição dos argumentos.", "grounding_ids": ["mat-0001"]},
            investigation=[investigation_target(goal="Establish what app.py does", status="established", grounding_ids=["mat-0001"], reason="app.py was read")],
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
    assert "def soma" in prompts[1]["latest_capability_results"][0]["detail"]["numbered_content"]
    assert "content" not in prompts[1]["latest_capability_results"][0]["detail"]
    assert details["investigation"][0]["goal"] == "Establish what app.py does"


def test_transactional_write_requires_confirmation_and_resume_is_deterministic(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    original = "def soma(a, b):\n    return a + b\n"
    updated = "def soma(a, b):\n    return a + b + 1\n"
    app.write_text(original, encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"})),
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
    assert pending["transaction_id"] == pending["session"]["write_transaction"]["transaction_id"]
    assert pending["session"]["write_transaction"]["patches"][0]["path"] == "app.py"

    status, text, pending2, details = core_agent.executar_agente(
        pending["session"]["request"], config(tmp_path),
        projeto={"caminho_origem": str(tmp_path)}, retomar=pending,
        resposta_usuario="confirmar", retornar_detalhes=True,
    )
    assert status == "success"
    assert pending2 is None
    assert "+ 1" in app.read_text(encoding="utf-8")
    assert "relidos integralmente" in text.lower()
    assert details["write_transaction"]["validation"]["full_reread"]["ok"] is True
    assert details["llm_usage"].get("llm_calls", 0) == 0


def test_pending_transaction_does_not_duplicate_full_source(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    source = "TOKEN_DO_ARQUIVO = 'segredo-local'\n"
    app.write_text(source, encoding="utf-8")
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"})),
        agent_patches([{"operation": "replace", "path": "app.py", "content": "TOKEN_DO_ARQUIVO = 'novo'\n"}]),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: next(outputs))
    status, _, pending, _ = core_agent.executar_agente(
        "altere app.py", config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "needs_user"
    serialized = json.dumps(pending, ensure_ascii=False)
    assert source.strip() not in serialized
    assert pending["session"]["write_transaction"]["patches"][0]["content"] == "TOKEN_DO_ARQUIVO = 'novo'\n"


def test_identical_reads_are_memoized_without_duplicate_observations_or_semantic_fatal(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    calls=[]
    def fake(prompt,cfg):
        calls.append(json.loads(prompt))
        if len(calls) <= 5:
            return agent_tools(tool_call("read_file", {"path":"app.py"}))
        return agent_final({"answer":"app.py foi observado.","grounding_ids":["mat-0001"]})
    monkeypatch.setattr(core_agent, "executar_agente_llm", fake)
    status,_,_,details=core_agent.executar_agente("analise",config(tmp_path),projeto={"caminho_origem":str(tmp_path)},retornar_detalhes=True)
    assert status=="success"
    assert len(calls)==6
    assert details["observation_replays"] >= 4
    assert details["observation_ledger_size"] == 1
    assert details["failure_code"] is None

def test_disabled_tests_are_not_advertised(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("x=1\n", encoding="utf-8")
    prompts = []
    outputs = iter([
        agent_tools(tool_call("read_file", {"path": "app.py"})),
        agent_final({"answer": "app.py foi lido.", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(core_agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    status, *_ = core_agent.executar_agente(
        "olhe o projeto", config(tmp_path), projeto={"caminho_origem": str(tmp_path)}, retornar_detalhes=True,
    )
    assert status == "success"
    assert not any(item.startswith("run_tests(") for item in prompts[0]["available_capabilities"])


def test_external_memory_only_moves_through_tools(monkeypatch, tmp_path):
    app = tmp_path / "app.py"
    app.write_text("VALUE = 1\n", encoding="utf-8")
    import hashlib
    file_hash = hashlib.sha256(app.read_bytes()).hexdigest()
    grounding = {"mat-0001": {"file": "app.py", "file_hash": file_hash}}
    context = {"config": config(tmp_path), "projeto": {"caminho_origem": str(tmp_path)}, "grounding": grounding}
    monkeypatch.setattr(tools, "MEMORY_DIR", str(tmp_path / "memory"))
    stored = tools.executar_tool("memory_store", {"text": "app.py define VALUE", "meta": {"tags": ["code"], "grounding_ids": ["mat-0001"]}}, context)
    assert stored["ok"] is True
    found = tools.executar_tool("memory_search", {"query": "VALUE"}, context)
    assert found["detail"]["count"] == 1
    assert found["detail"]["view"]["memories"][0]["content"] == "app.py define VALUE"
    assert found["detail"]["view"]["memory_coverage"]["kind"] == "memory_navigation"


def test_removed_reasoning_modules_are_physically_absent():
    root = Path(__file__).resolve().parents[1]
    removed = [
        "eyle/core/mission.py", "eyle/core/state.py", "eyle/core/project_memory.py",
        "eyle/core/context_compiler.py", "eyle/core/retention.py", "ingest.py", "engine", "llm/cache.py",
    ]
    assert all(not (root / relative).exists() for relative in removed)
