#!/usr/bin/env python3
"""Revisao 55.4: orcamento do Agente e transicoes deterministicas."""
import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod
from engine.agent_state import AgentState
import llm.executar as llm_mod


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _config_llm(**updates):
    llm = {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:8080",
        "model": "teste",
        "openai_compatible": False,
        "temperature": 0.2,
        "timeout_seconds": 10,
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 10,
        "agent_timeout_seconds": 10,
        "max_tokens": 1500,
        "agent_max_tokens": 512,
        "retry_max_attempts": 3,
        "retry_base_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0,
        "retry_read_timeouts": False,
        "max_concurrent_requests": 1,
        "cache": {"ativado": False},
    }
    llm.update(updates)
    return {"llm": llm}


def test_analise_geral_comeca_por_list_tree_sem_consultar_llm():
    estado = AgentState(config={})
    estado.definir_objetivo("faca a analise do projeto", "project_read", modo="analyze")

    assert agent_mod._acao_obrigatoria_goal_state(estado) == {
        "tool": "list_tree", "arguments": {},
    }

    estado.acoes_executadas = 1
    estado.goal_state["actions_executed"] = 1
    assert agent_mod._acao_obrigatoria_goal_state(estado) is None


def test_alvo_explicito_nao_recebe_list_tree_forcado():
    estado = AgentState(config={})
    estado.definir_objetivo("analise Audio.py", "project_read", modo="analyze")
    assert agent_mod._acao_obrigatoria_goal_state(estado) is None


def test_perfil_agent_usa_teto_de_tokens_proprio(monkeypatch):
    payloads = []

    def responder(req, timeout=None):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _Response({"message": {"content": "ok"}})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", responder)
    assert llm_mod._chamar_llm("s", "u", _config_llm(), perfil="agent") == "ok"
    assert payloads[0]["options"]["num_predict"] == 512


def test_timeout_de_leitura_nao_repete_geracao_inteira_por_padrao(monkeypatch):
    chamadas = []

    def falhar(req, timeout=None):
        chamadas.append(req.full_url)
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", falhar)
    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("s", "u", _config_llm(), perfil="agent")

    assert erro.value.error_code == "READ_TIMEOUT"
    assert erro.value.transient is False
    assert len(chamadas) == 1


def test_timeout_de_leitura_pode_ser_repetido_explicitamente(monkeypatch):
    chamadas = []

    def falhar(req, timeout=None):
        chamadas.append(req.full_url)
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", falhar)
    cfg = _config_llm(retry_read_timeouts=True)
    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("s", "u", cfg, perfil="agent")

    assert erro.value.error_code == "READ_TIMEOUT"
    assert len(chamadas) == 3


def test_fluxo_de_analise_geral_economiza_primeira_chamada_llm(monkeypatch, tmp_path):
    (tmp_path / "Audio.py").write_text(
        'import subprocess\n\nlink = "x"\n'
        'subprocess.run(["python", "-m", "spotdl", link, "--output", "audios"])\n',
        encoding="utf-8",
    )
    scouts = []
    finalizers = []

    def scout(prompt, config):
        scouts.append(prompt)
        return json.dumps({
            "final": {
                "answer": "plano",
                "selected_paths": ["Audio.py"],
                "risk_hypotheses": [],
                "gaps": [],
            }
        })

    def finalizer(prompt, config):
        finalizers.append(prompt)
        return json.dumps({
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "O arquivo Audio.py chama subprocess.run para executar spotdl e salvar a saída em audios.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                }],
                "verification": "codigo fresco",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_audit_scout_llm", scout)
    monkeypatch.setattr(agent_mod, "executar_audit_finalizer_llm", finalizer)
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        lambda *args: (_ for _ in ()).throw(AssertionError("project_audit nao usa o agente monolitico")),
    )
    config = {
        "agent": {
            "max_steps": 8,
            "max_tentativas_parse": 3,
            "max_no_progress_decisions": 3,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "max_erros_consecutivos": 3,
            "exigir_run_tests_apos_escrita": True,
            "enabled_modes": ["analyze", "suggest", "edit"],
            "rollout_mode": "read_only",
            "task_deadline_seconds": 30,
            "max_llm_calls": 12,
            "max_total_generated_tokens": 12000,
            "semantic_grounding": {"enabled": False},
            "audit_candidate_limit": 48,
            "audit_initial_read_limit": 6,
            "audit_gap_read_limit": 1,
        },
        "context_engine": {
            "chars_per_token_fallback": 3,
            "safety_margin_tokens": 100,
            "max_recent_observations": 4,
        },
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 1500,
            "agent_max_tokens": 512,
            "audit_scout_max_tokens": 700,
            "audit_finalizer_max_tokens": 1600,
        },
    }

    status, texto, _, detalhes = agent_mod.executar_agente(
        "faça a análise do projeto",
        config,
        entendimento={},
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True,
    )

    assert status == "success"
    assert "subprocess.run" in texto
    assert detalhes["tools_called"] == ["list_tree", "read_file"]
    assert len(scouts) == 0
    assert len(finalizers) == 1
