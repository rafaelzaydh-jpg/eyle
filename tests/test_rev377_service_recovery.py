from __future__ import annotations

import copy

import eyle.runtime.service as service
from eyle.core.session import AgentSession
from eyle.runtime.execution_context import ExecutionContext
from eyle.runtime.continuation import PENDING_SCHEMA_VERSION
from tests.canonical import base_config


def _checkpoint(config, execution_id="job-55"):
    session = AgentSession("original request", execution_id=execution_id)
    execution = ExecutionContext.from_config(config, execution_id=execution_id, source_job_id=55)
    return {
        "pending_schema_version": PENDING_SCHEMA_VERSION,
        "continuation_kind": "recoverable_execution",
        "question": "Recoverable execution checkpoint.",
        "session": session.to_checkpoint_dict(),
        "execution_state": execution.continuation_state(),
        "checkpoint_reason": "stalled_recoverable",
        "resume_hint": "Resume the same logical execution.",
    }


def test_rev377_service_persists_and_auto_resumes_recoverable_checkpoint(monkeypatch, tmp_path):
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(pending_dir))
    monkeypatch.setattr(service, "registrar_mensagem", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "carregar_provider_context", lambda: {"standard": {"caminho_origem": str(tmp_path)}})

    config = base_config()
    core_checkpoint = _checkpoint(config)
    calls = []

    def fake_agent(*args, **kwargs):
        calls.append(copy.deepcopy(kwargs.get("retomar")))
        if kwargs.get("retomar") is None:
            return (
                "recoverable_checkpoint",
                "checkpoint",
                copy.deepcopy(core_checkpoint),
                {"status": "recoverable_checkpoint"},
            )
        return (
            "completed",
            "resumed",
            None,
            {
                "status": "completed",
                "llm_usage": {"execution_resume_count": 1},
            },
        )

    monkeypatch.setattr(service, "executar_agente", fake_agent)

    result = service._processar_agente(
        "original request", config, {"standard": {"caminho_origem": str(tmp_path)}},
        execution_id="job-55", source_job_id=55,
    )
    assert result["status"] == "completed"
    assert result["resposta"] == "resumed"
    assert len(calls) == 2
    assert calls[1]["continuation_kind"] == "recoverable_execution"
    assert service.listar_agent_pendentes() == []


def test_rev377_restarted_execution_id_prefers_recoverable_checkpoint(monkeypatch, tmp_path):
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr(service, "AGENT_PENDENTE_DIR", str(pending_dir))
    monkeypatch.setattr(service, "registrar_mensagem", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "carregar_provider_context", lambda: {"standard": {"caminho_origem": str(tmp_path)}})
    monkeypatch.setattr(service, "carregar_config", lambda: base_config())

    config = base_config()
    persisted = service.salvar_agent_pendente(
        _checkpoint(config, execution_id="job-77"),
        provider_context={"standard": {"caminho_origem": str(tmp_path)}},
        config=config,
    )
    seen = {}

    def fake_agent(*args, **kwargs):
        seen["retomar"] = kwargs.get("retomar")
        return (
            "completed", "continued after restart", None,
            {"status": "completed", "llm_usage": {"execution_resume_count": 1}},
        )

    monkeypatch.setattr(service, "executar_agente", fake_agent)
    result = service.processar(
        "original request", registrar_pergunta=False,
        historico_snapshot=[], execution_id="job-77", source_job_id=77,
    )
    assert result["status"] == "completed"
    assert seen["retomar"]["id"] == persisted["id"]
    assert service.listar_agent_pendentes() == []
