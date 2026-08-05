import json
from pathlib import Path

from engine.config_schema import validar_config
from engine.engine import _rollout_agente_efetivo


ROOT = Path(__file__).resolve().parents[1]


def _release_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_release_defaults_are_full_but_write_supervised(tmp_path, monkeypatch):
    config = validar_config(_release_config())
    monkeypatch.chdir(tmp_path)

    assert config["agent"]["rollout_mode"] == "full"
    assert "trusted_project_paths" not in config["agent"]
    assert config["agent"]["require_confirmation_for_write"] is True
    assert config["codar"]["testes"]["ativado"] is False

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(ROOT / "workspace" / "demo")},
    )
    assert configured == "full"
    assert effective == "full"
    assert cause is None


def test_release_default_backend_matches_ipv4_llama_server():
    config = validar_config(_release_config())
    assert config["llm"]["base_url"] == "http://127.0.0.1:8080"
    assert config["llm"]["read_timeout_seconds"] > config["llm"]["connect_timeout_seconds"]


def test_release_defaults_use_same_agent_for_external_projects(tmp_path):
    config = validar_config(_release_config())

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(tmp_path / "external-project")},
    )
    assert configured == "full"
    assert effective == "full"
    assert cause is None
