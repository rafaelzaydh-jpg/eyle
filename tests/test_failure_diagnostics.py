from tests.canonical import run_agent
import json

import eyle.core.agent as core_agent
from eyle.runtime import service
from tests.canonical import agent_complete, base_config


def test_service_projects_generic_execution_failure_from_capability_history():
    details={"capability_history":[{"capability":"petbot.dispense","result":{"ok":False,"error_code":"MOTOR_JAM","detail":"motor blocked","retryable":False}}]}
    metadata=service._metadata_resposta_agente("failed",details)
    assert metadata["agent_status"] == "failed"
    assert metadata["execution_failure"] == {"capability":"petbot.dispense","error_code":"MOTOR_JAM","detail":"motor blocked","retryable":False}


def test_service_carries_execution_failure_into_agent_conversation_context(monkeypatch):
    failure={"capability":"router.restart","error_code":"OFFLINE","detail":"router did not answer"}
    captured={}
    monkeypatch.setattr(service,"carregar_config",lambda:{})
    monkeypatch.setattr(service,"carregar_provider_context",lambda:{"standard":{"caminho_origem":"/tmp/project"},"memory":{"storage_dir":"/tmp/memory","scope_root":"/tmp/project"}})
    monkeypatch.setattr(service,"carregar_agent_pendente",lambda:None)
    def fake_process(question,config,project,**kwargs):
        captured.update(kwargs.get("conversation_context") or {})
        return {"status":"success","resposta":"ok","avisos":[],"details":{}}
    monkeypatch.setattr(service,"_processar_agente",fake_process)
    history=[{"id":1,"role":"assistant","text":"falhou","agent_status":"failed","execution_failure":failure},{"id":2,"role":"user","text":"Por que?"}]
    service.processar("Por que?",registrar_pergunta=False,historico_snapshot=history)
    assert captured["recent_messages"] == [{"role":"assistant","content":"falhou","execution_failure":failure}]


def test_follow_up_can_ground_prior_runtime_failure(monkeypatch,tmp_path):
    prompts=[]
    def fake(prompt,_cfg):
        payload=json.loads(prompt); prompts.append(payload)
        assert payload["latest_capability_results"][0]["detail"]["source_type"] == "runtime_failure"
        return agent_complete({"answer":"O roteador não respondeu.","grounding_ids":["mat-0001"]})
    monkeypatch.setattr(core_agent,"executar_agente_llm",fake)
    context={"recent_messages":[{"role":"assistant","content":"falhou","execution_failure":{"capability":"router.restart","error_code":"OFFLINE","detail":"router did not answer"}}]}
    status,text,_,details=run_agent(core_agent, "Por que falhou?",base_config(),provider_context={"standard":{"caminho_origem":str(tmp_path)}},retornar_detalhes=True,conversation_context=context)
    assert status == "success" and "roteador" in text.lower()
    assert details["grounding"][0]["source_type"] == "runtime_failure"
