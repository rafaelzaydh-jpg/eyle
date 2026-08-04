import json
from pathlib import Path

from engine.config_schema import validar_config
from engine.engine import _rollout_agente_efetivo


ROOT = Path(__file__).resolve().parents[1]


def _release_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_release_defaults_are_safe_and_read_only(tmp_path, monkeypatch):
    config = validar_config(_release_config())
    monkeypatch.chdir(tmp_path)

    assert config["agent"]["rollout_mode"] == "read_only"
    assert config["agent"]["trusted_project_paths"] == []
    assert config["agent"]["require_confirmation_for_write"] is True
    assert config["codar"]["testes"]["ativado"] is False

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(ROOT / "workspace" / "demo")},
    )
    assert configured == "read_only"
    assert effective == "read_only"
    assert cause is None


def test_release_defaults_do_not_trust_external_projects(tmp_path):
    config = validar_config(_release_config())

    configured, effective, cause = _rollout_agente_efetivo(
        config,
        {"caminho_origem": str(tmp_path / "external-project")},
    )
    assert configured == "read_only"
    assert effective == "read_only"
    assert cause is None
