from tests.canonical import run_agent
import json

import eyle.core.agent as core_agent
from eyle.runtime import service
from tests.canonical import base_config


def test_service_projects_generic_execution_failure_from_operation_history():
    details={"operation_history":[{"capability":"petbot.dispense","result":{"ok":False,"error_code":"MOTOR_JAM","detail":"motor blocked","retryable":False}}]}
    metadata=service._metadata_resposta_agente("failed",details)
    assert metadata["agent_status"] == "failed"
    assert metadata["execution_failure"] == {"capability":"petbot.dispense","error_code":"MOTOR_JAM","detail":"motor blocked","retryable":False}


def test_service_carries_execution_failure_into_agent_conversation_context(monkeypatch):
    failure={"capability":"router.restart","error_code":"OFFLINE","detail":"router did not answer"}
    captured={}
    monkeypatch.setattr(service,"carregar_config",lambda:{})
    monkeypatch.setattr(service,"carregar_provider_context",lambda:{"standard":{"caminho_origem":"/tmp/project"},"memory":{"storage_dir":"/tmp/memory","world_scope_id":"workspace:/tmp/project"}})
    monkeypatch.setattr(service,"carregar_agent_pendente",lambda *args, **kwargs:None)
    def fake_process(question,config,project,**kwargs):
        captured.update(kwargs.get("conversation_context") or {})
        return {"status":"success","resposta":"ok","avisos":[],"details":{}}
    monkeypatch.setattr(service,"_processar_agente",fake_process)
    history=[{"id":1,"role":"assistant","text":"falhou","agent_status":"failed","execution_failure":failure},{"id":2,"role":"user","text":"Por que?"}]
    service.processar("Por que?",registrar_pergunta=False,historico_snapshot=history)
    assert captured["recent_messages"] == [{"role":"assistant","content":"falhou","execution_failure":failure}]


def test_follow_up_receives_prior_runtime_failure_as_current_observation(monkeypatch,tmp_path):
    prompts=[]
    def fake(prompt,_cfg):
        payload=json.loads(str(prompt)); prompts.append(payload)
        observation=payload["latest_observations"][0]
        assert observation["detail"]["source_type"] == "runtime_failure"
        return {"type":"concluir","response":"O roteador não respondeu.","memory_delta":[]}
    monkeypatch.setattr(core_agent,"executar_ecc_llm",fake)
    context={"recent_messages":[{"role":"assistant","content":"falhou","execution_failure":{"capability":"router.restart","error_code":"OFFLINE","detail":"router did not answer"}}]}
    provider_context={"standard":{"caminho_origem":str(tmp_path)},"core_memory":{"storage_dir":str(tmp_path.parent/(tmp_path.name+"_memory")),"world_scope_id":f"workspace:{tmp_path.resolve()}"}}
    status,text,_,details=run_agent(core_agent,"Por que falhou?",base_config(),provider_context=provider_context,retornar_detalhes=True,conversation_context=context)
    assert status == "completed" and "roteador" in text.lower()
    assert details["grounding_count_total"] == 1
