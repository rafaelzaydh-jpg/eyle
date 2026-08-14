from tests.canonical import run_agent
import json
from pathlib import Path

import eyle.core.agent as agent
from eyle.core.token_budget import estimate_tokens
from eyle.providers.standard_impl.workspace import discover_project
from llm.executar import PROMPT_AGENTE
from tests.canonical import agent_complete, base_config


def test_standard_provider_environment_binds_self_source_without_exposing_paths(monkeypatch, tmp_path):
    root = tmp_path / "install"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".gitkeep").write_text("", encoding="utf-8")
    (root / "main.py").write_text("ROOT_IDENTITY = 'running-eyle'\n", encoding="utf-8")
    project = discover_project(str(root))
    prompts = []
    monkeypatch.setattr(agent, "executar_agente_llm", lambda prompt, cfg: prompts.append(json.loads(prompt)) or agent_complete("ok"))
    status, _, _, _ = run_agent(agent, 
        "Quem é sua instância atual?", base_config(), provider_context={"standard": project}, retornar_detalhes=True,
    )
    assert status == "success"
    assert "project" not in prompts[0]
    view = prompts[0]["environment"]["providers"]["standard"]
    assert view["resources"]["workspace"]["kind"] == "user_workspace"
    assert view["resources"]["eyle_source"]["kind"] == "running_eyle_source"
    assert str(root) not in json.dumps(view)

def test_self_source_mapping_is_provider_owned(tmp_path):
    from eyle.providers import standard as standard_provider
    root = tmp_path / "install"
    (root / "workspace").mkdir(parents=True)
    project = discover_project(str(root))
    ctx = {"provider_context": {"standard": project}}
    assert Path(standard_provider._caminho_fonte(ctx, {"source": "eyle"})).resolve() == root.resolve()
    assert Path(standard_provider._caminho_fonte(ctx, {"source": "workspace"})).resolve() == (root / "workspace").resolve()

def test_core_prompt_does_not_own_standard_provider_source_identity():
    lower = PROMPT_AGENTE.lower()
    assert "project.identity" not in lower
    assert "source=eyle" not in lower
    assert "environment contains provider/runtime facts" in lower

def test_release_manifest_declares_rev15_provider_identity_boundary():
    manifest = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_schema_version"] == "2.7.5-r1.5.3"
    assert manifest["revision"] == "rev1.5.3-cognitive-task-memory"
    assert manifest["compatibility"]["session_schema"] == "2.7.5-r1.5.3"
    assert manifest["compatibility"]["queue_schema"] == "2.7.5-r1.4.3"
    assert manifest["compatibility"]["pending_continuation_schema"] == "4"
    assert "provider_context" in manifest["architecture"]

