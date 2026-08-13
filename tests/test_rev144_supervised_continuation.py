from tests.canonical import run_agent
import json
from pathlib import Path

import eyle.core.agent as agent
import eyle.runtime.service as service
from eyle.core.continuation import PENDING_SCHEMA_VERSION, validate_pending_continuation
from eyle.providers.standard_impl.workspace import discover_project
from llm.structured import StructuredResponseError, parse_agent_response
from tests.canonical import (
    agent_await_user,
    agent_complete,
    agent_tools,
    base_config,
    investigation_target,
    task_item,
    tool_call,
)


def test_provider_environment_exposes_standard_resources_without_core_project_surface(monkeypatch, tmp_path):
    root = tmp_path / "install"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".gitkeep").write_text("", encoding="utf-8")
    (root / "main.py").write_text("SELF_MARKER = 'eyle-root'\n", encoding="utf-8")
    project = discover_project(str(root))

    prompts = []
    outputs = iter([
        agent_tools(tool_call("read_file", {"source": "eyle", "path": "main.py"})),
        agent_complete({"answer": "A fonte da Eyle está acessível.", "grounding_ids": ["mat-0001"]}),
    ])
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or next(outputs))
    status, text, _, details = run_agent(agent, 
        "Inspecione sua implementação atual.", base_config(), provider_context={"standard": project}, retornar_detalhes=True,
    )
    assert status == "success" and "acessível" in text
    assert "project" not in prompts[0]
    standard = prompts[0]["environment"]["providers"]["standard"]
    assert standard["resources"]["workspace"]["kind"] == "user_workspace"
    assert standard["resources"]["eyle_source"]["kind"] == "running_eyle_source"
    assert "SELF_MARKER" in prompts[1]["latest_capability_results"][0]["detail"]["numbered_content"]
    assert details["capability_calls"] == 1

def test_await_user_persists_open_cognitive_state_and_main_authored_options(monkeypatch, tmp_path):
    investigation = investigation_target(
        target_id="source-choice", goal="Determine which Eyle tree is authoritative", status="open",
    )
    task = task_item(
        task_id="audit", description="Audit the selected Eyle source",
        completion_criteria=["Use the user-selected source"], status="open",
    )
    monkeypatch.setattr(
        agent, "executar_agente_llm",
        lambda prompt, cfg: agent_await_user(
            "Encontrei duas árvores. Qual deve ser a referência?",
            reason="The authority choice belongs to the user.",
            options=[
                {"id": "root", "label": "Eyle Root"},
                {"id": "workspace", "label": "Eyle Workspace"},
            ],
            investigation=[investigation], tasks=[task],
        ),
    )
    status, _, pending, details = run_agent(agent, 
        "Audite a Eyle.", base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "await_user"
    assert pending["pending_schema_version"] == PENDING_SCHEMA_VERSION == "4"
    assert pending["continuation_kind"] == "await_user"
    assert pending["options"][0]["id"] == "root"
    assert pending["session"]["investigation"][0]["status"] == "open"
    assert pending["session"]["tasks"][0]["status"] == "open"
    assert details["investigation"][0]["id"] == "source-choice"
    validate_pending_continuation(pending)


def test_repeated_human_gates_resume_without_request_inflation(monkeypatch, tmp_path):
    original = "Faça uma operação supervisionada em duas etapas."
    first = agent_await_user(
        "Qual fonte?", reason="Source authority belongs to the user.",
        options=[{"id": "root", "label": "Eyle Root"}],
    )
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: first)
    status, _, pending1, _ = run_agent(agent, 
        original, base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retornar_detalhes=True,
    )
    assert status == "await_user"

    resume_prompts = []
    monkeypatch.setattr(
        agent, "executar_agente_llm",
        lambda prompt, cfg: resume_prompts.append(json.loads(prompt)) or agent_await_user(
            "Autoriza continuar?", reason="The next supervised choice belongs to the user.",
            options=[{"id": "continue", "label": "Continuar"}],
        ),
    )
    status, _, pending2, _ = run_agent(agent, 
        original, base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retomar=pending1,
        resposta_usuario="Eyle Root", retornar_detalhes=True,
    )
    assert status == "await_user"
    assert pending2["session"]["request"] == original
    assert resume_prompts[0]["request"] == original

    final_prompts = []
    monkeypatch.setattr(
        agent, "executar_agente_llm",
        lambda prompt, cfg: final_prompts.append(json.loads(prompt)) or agent_complete("Concluído."),
    )
    status, text, pending3, _ = run_agent(agent, 
        original, base_config(), provider_context={"standard": {"caminho_origem": str(tmp_path)}}, retomar=pending2,
        resposta_usuario="Continuar", retornar_detalhes=True,
    )
    assert status == "success" and text == "Concluído." and pending3 is None
    assert final_prompts[0]["request"] == original
    resolutions = final_prompts[0]["request_context"]
    assert any(item.get("answer") == "Eyle Root" for item in resolutions)
    assert any(item.get("answer") == "Continuar" for item in resolutions)
    assert len(final_prompts[0]["request"]) == len(original)


def test_runtime_persists_await_user_without_expiry_and_exposes_only_safe_ui_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "AGENT_PENDENTE_PATH", str(tmp_path / "pending.json"))
    core_pending = {
        "pending_schema_version": "4",
        "continuation_kind": "await_user",
        "question": "Qual fonte?",
        "session": {"request": "audit", "secret_state": "not public"},
        "reason": "User choice required.",
        "options": [{"id": "root", "label": "Eyle Root"}],
    }
    persisted = service.salvar_agent_pendente(core_pending, provider_context={}, config=base_config())
    assert persisted["expires_at"] is None
    assert persisted["provider_context_hash"] is None
    assert "Pending ID" not in persisted["question"]
    public = service._public_await_user(persisted)
    assert public == {
        "id": persisted["id"], "question": "Qual fonte?", "reason": "User choice required.",
        "options": [{"id": "root", "label": "Eyle Root"}],
    }
    assert "session" not in public

def test_await_user_allows_no_suggested_options_because_custom_response_is_universal():
    payload = agent_await_user(
        "What should I use?", reason="The decision belongs to the user.", options=[],
    )
    parsed = parse_agent_response(payload)
    assert parsed["action"] == {
        "kind": "await_user", "question": "What should I use?",
        "reason": "The decision belongs to the user.", "options": [],
    }



def test_web_client_renders_model_options_custom_response_and_cancel_controls():
    source = Path("web/static/app.js").read_text(encoding="utf-8")
    css = Path("web/static/style.css").read_text(encoding="utf-8")
    assert "buildAwaitUserPanel" in source
    assert "await-user-option" in source
    assert "Outra instrução…" in source
    assert "Cancelar tarefa" in source
    assert "await-user-panel" in css

def test_release_manifest_declares_rev15_pending_and_action_contract():
    manifest = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_schema_version"] == "2.7.5-r1.5.1"
    assert manifest["compatibility"]["pending_continuation_schema"] == "4"
    assert manifest["compatibility"]["queue_schema"] == "2.7.5-r1.4.3"
    assert manifest["agent_action_kinds"] == ["capability_calls", "await_user", "complete"]

