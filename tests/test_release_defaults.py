import json
from pathlib import Path

from engine.config_schema import validar_config
from engine.engine import _rollout_agente_efetivo


ROOT = Path(__file__).resolve().parents[1]


def _release_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_release_defaults_enable_supervised_agent_for_workspace(tmp_path, monkeypatch):
    config = validar_config(_release_config())
    monkeypatch.chdir(tmp_path)

    assert config["agent"]["rollout_mode"] == "full"
    assert config["agent"]["trusted_project_paths"] == ["workspace"]
    assert config["agent"]["require_confirmation_for_write"] is True
    assert config["codar"]["testes"]["ativado"] is True

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(ROOT / "workspace" / "demo")},
    )
    assert (configured, effective, cause) == ("full", "full", None)


def test_release_defaults_keep_external_projects_read_only(tmp_path):
    config = validar_config(_release_config())

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(tmp_path / "external-project")},
    )
    assert configured == "full"
    assert effective == "read_only"
    assert cause == "project_not_in_trusted_paths"
