#!/usr/bin/env python3
"""Criterios executaveis das Atualizacoes 48 e 49."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.agent as agent_mod  # noqa: E402
from engine import engine as engine_mod  # noqa: E402
from engine import queue  # noqa: E402
from engine.config_schema import ConfigError, validar_config  # noqa: E402
from engine.project_reader import ler_faixa_projeto  # noqa: E402


def _config(**agent_overrides):
    agente = {
        "rollout_mode": "full",
        "trusted_project_paths": ["/tmp"],
        "enabled_modes": ["analyze", "suggest", "edit"],
        "max_steps": 8,
        "max_no_progress_decisions": 2,
        "max_tentativas_parse": 1,
        "max_erros_consecutivos": 3,
        "max_chars_por_observacao": 500,
        "max_fatos_importantes": 10,
        "max_read_range_lines": 400,
        "require_confirmation_for_write": True,
        "require_confirmation_for_exec": False,
        "exigir_run_tests_apos_escrita": True,
    }
    agente.update(agent_overrides)
    return {"agent": agente}


def _llm(*decisoes):
    respostas = iter(decisoes)
    return lambda *args, **kwargs: next(respostas)


def test_rollout_tem_tres_modos_e_full_exige_projeto_confiavel():
    validar_config({"agent": {"rollout_mode": "off", "trusted_project_paths": []}})
    validar_config({"agent": {"rollout_mode": "read_only", "trusted_project_paths": []}})
    validar_config({"agent": {"rollout_mode": "full", "trusted_project_paths": ["/tmp/projeto"]}})
    with pytest.raises(ConfigError, match="rollout_mode"):
        validar_config({"agent": {"rollout_mode": "talvez"}})
    with pytest.raises(ConfigError, match="trusted_project_paths"):
        validar_config({"agent": {"rollout_mode": "full", "trusted_project_paths": []}})


def test_read_only_bloqueia_write_antes_da_execucao(tmp_path, monkeypatch):
    executadas = []
    monkeypatch.setattr(agent_mod, "TOOLS", {"apply_patch": {"permission": "WRITE"}})
    monkeypatch.setattr(
        agent_mod, "validar_chamada_tool",
        lambda nome, arguments, registro=None: (dict(arguments), None),
    )
    monkeypatch.setattr(
        agent_mod, "executar_tool",
        lambda *args, **kwargs: executadas.append(args[0]),
    )
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm(json.dumps({
            "tool": "apply_patch",
            "arguments": {"caminho_relativo": "a.py"},
        })),
    )

    status, texto, pendente = agent_mod.executar_agente(
        "corrija a.py",
        _config(rollout_mode="read_only", max_no_progress_decisions=1),
        projeto={"caminho_origem": str(tmp_path)},
        modo="edit",
    )

    assert status == "needs_user"
    assert "read_only" in texto
    assert executadas == []
    assert pendente["continuation_kind"] == "user_input"


def test_needs_user_livre_retoma_o_mesmo_objetivo_e_orcamento(monkeypatch):
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm('{"needs_user":"qual arquivo devo analisar?"}'),
    )
    status, _, pendente, detalhes = agent_mod.executar_agente(
        "analise o arquivo indicado", _config(), retornar_detalhes=True,
        task_id="task-livre",
    )

    assert status == "needs_user"
    assert pendente["task_id"] == "task-livre"
    assert pendente["continuation_kind"] == "user_input"
    assert pendente["orcamento_restante"] == 8

    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm('{"final":"vou analisar a.py"}'),
    )
    status2, texto2, _, detalhes2 = agent_mod.executar_agente(
        "analise o arquivo indicado", _config(), retomar=pendente,
        resposta_usuario="a.py", retornar_detalhes=True,
    )

    assert status2 == "success"
    assert texto2 == "vou analisar a.py"
    assert detalhes2["task_id"] == detalhes["task_id"] == "task-livre"
    assert detalhes2["goal_state"]["actions_executed"] == 0


def test_sqlite_guarda_snapshot_cancelamento_e_auditoria(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    tarefa = queue.criar_tarefa_agente("objetivo", "edit", task_id="task-sql")
    assert tarefa["status"] == "running"

    estado = {"goal_state": {"objective": "objetivo"}, "evidence": [{"id": "ev-1"}]}
    acao = {"tool": "apply_patch", "permission": "WRITE", "idempotent": False}
    queue.atualizar_tarefa_agente(
        "task-sql", status="waiting_user", estado=estado,
        continuacao={"task_id": "task-sql", "estado": estado, "tool_pendente": acao},
        acao_pendente=acao, orcamento_restante=4, pergunta="confirma?",
        evento={"tipo": "waiting_user"},
    )
    salvo = queue.obter_tarefa_agente("task-sql")
    assert salvo["estado"]["evidence"][0]["id"] == "ev-1"
    assert salvo["orcamento_restante"] == 4

    assert queue.cancelar_tarefa_agente("task-sql") is True
    cancelada = queue.obter_tarefa_agente("task-sql")
    assert cancelada["status"] == "blocked"
    assert cancelada["acao_pendente"] is None
    assert cancelada["estado"] == estado
    assert any(item["tipo"] == "task_cancelled" for item in cancelada["auditoria"])


def test_reinicio_nao_recoloca_write_e_mantem_read_idempotente(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "fila.sqlite3"))
    queue.criar_tarefa_agente("ler", "analyze", task_id="task-read")
    queue.atualizar_tarefa_agente(
        "task-read",
        continuacao={"task_id": "task-read", "tool_pendente": {"tool": "read_range"}},
        acao_pendente={"tool": "read_range", "permission": "READ", "idempotent": True},
    )
    queue.criar_tarefa_agente("editar", "edit", task_id="task-write")
    queue.atualizar_tarefa_agente(
        "task-write",
        continuacao={"task_id": "task-write", "tool_pendente": {"tool": "apply_patch"}},
        acao_pendente={"tool": "apply_patch", "permission": "WRITE", "idempotent": False},
    )

    recuperadas = queue.recuperar_tarefas_agente_interrompidas()

    assert recuperadas["idempotentes"] == ["task-read"]
    assert recuperadas["writes_protegidas"] == ["task-write"]
    assert queue.obter_tarefa_agente("task-read")["status"] == "running"
    write = queue.obter_tarefa_agente("task-write")
    assert write["status"] == "waiting_user"
    assert write["continuacao"]["recovery_required"] is True


def test_write_ja_aplicada_apos_reinicio_nao_executa_duas_vezes(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    leitura = ler_faixa_projeto(str(tmp_path), "a.py", 1, 1)
    argumentos = {
        "caminho_relativo": "a.py",
        "linha_inicio": 1,
        "linha_fim": 1,
        "codigo_original_esperado": "valor = 1",
        "codigo_novo": "valor = 2",
        "file_hash_esperado": leitura["file_hash"],
        "range_hash_esperado": leitura["content_hash"],
    }
    monkeypatch.setattr(
        agent_mod.AgentState, "validar_precondicoes_patch",
        lambda self, args: (True, "ev-1"),
    )
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm(json.dumps({"tool": "apply_patch", "arguments": argumentos})),
    )
    status, _, pendente = agent_mod.executar_agente(
        "corrija a.py", _config(), projeto={"caminho_origem": str(tmp_path)}, modo="edit",
    )
    assert status == "needs_user"

    # Simula o processo morrendo depois da troca atomica, mas antes do
    # checkpoint de conclusao da tool.
    arquivo.write_text("valor = 2\n", encoding="utf-8")
    pendente["recovery_required"] = True
    chamadas = []
    monkeypatch.setattr(
        agent_mod, "executar_tool",
        lambda nome, *args, **kwargs: chamadas.append(nome),
    )
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm('{"needs_user":"continuar verificacao depois"}'),
    )
    status2, _, pendente2 = agent_mod.executar_agente(
        "corrija a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        modo="edit", retomar=pendente,
    )

    assert status2 == "needs_user"
    assert chamadas == []
    assert arquivo.read_text(encoding="utf-8") == "valor = 2\n"
    assert pendente2["estado"]["edit_state"]["recovered_already_applied"] is True


def test_hash_de_evidencia_mudado_e_invalidado_na_retomada(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm(
            json.dumps({
                "tool": "read_range",
                "arguments": {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1},
            }),
            '{"needs_user":"qual proximo passo?"}',
        ),
    )
    status, _, pendente = agent_mod.executar_agente(
        "analise a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        modo="analyze",
    )
    assert status == "needs_user"
    assert pendente["estado"]["evidence"][0]["estado"] == "fresh"

    arquivo.write_text("valor = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm('{"needs_user":"preciso reler antes de concluir"}'),
    )
    status2, _, pendente2 = agent_mod.executar_agente(
        "analise a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        modo="analyze", retomar=pendente, resposta_usuario="continue",
    )

    assert status2 == "needs_user"
    evidencias = pendente2["estado"]["evidence"]
    assert evidencias[0]["estado"] == "stale"
    assert any(item["estado"] == "fresh" for item in evidencias[1:])
    assert "codigo_fresco_relevante" not in pendente2["estado"]["goal_state"]["evidence_needed"]


def test_full_expresso_cai_para_read_only_fora_da_allowlist(tmp_path):
    config = _config(trusted_project_paths=[str(tmp_path / "outro")])
    configurado, efetivo, causa = engine_mod._rollout_agente_efetivo(
        config, {"caminho_origem": str(tmp_path)},
    )
    assert configurado == "full"
    assert efetivo == "read_only"
    assert causa == "project_not_in_trusted_paths"


def test_checkpoint_pos_acao_retoma_sem_repetir_a_leitura(tmp_path, monkeypatch):
    arquivo = tmp_path / "a.py"
    arquivo.write_text("valor = 1\n", encoding="utf-8")
    checkpoints = []
    chamadas = []
    executar_real = agent_mod.executar_tool

    def executar_espiao(nome, arguments, ctx):
        chamadas.append(nome)
        return executar_real(nome, arguments, ctx)

    contador = {"valor": 0}

    def llm_antes_crash(*args, **kwargs):
        contador["valor"] += 1
        if contador["valor"] == 1:
            return json.dumps({
                "tool": "read_range",
                "arguments": {"caminho_relativo": "a.py", "linha_inicio": 1, "linha_fim": 1},
            })
        raise RuntimeError("queda simulada depois do checkpoint")

    monkeypatch.setattr(agent_mod, "executar_tool", executar_espiao)
    monkeypatch.setattr(agent_mod, "executar_agente_llm", llm_antes_crash)
    with pytest.raises(RuntimeError, match="queda simulada"):
        agent_mod.executar_agente(
            "analise a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
            modo="analyze", task_id="task-checkpoint", checkpoint=checkpoints.append,
        )

    ultimo = checkpoints[-1]
    assert ultimo["evento"]["tipo"] == "action_completed"
    assert ultimo["continuacao"]["tool_pendente"]["tool"] == "__resume__"

    monkeypatch.setattr(
        agent_mod, "executar_agente_llm",
        _llm(json.dumps({
            "final": {
                "resposta": "a.py:1 foi relido",
                "evidence_ids": ["ev-0001"],
                "verificacao": "hash fresco",
                "limitacoes": [],
            }
        })),
    )
    status, _, _, detalhes = agent_mod.executar_agente(
        "analise a.py", _config(), projeto={"caminho_origem": str(tmp_path)},
        modo="analyze", task_id="task-checkpoint",
        retomar=ultimo["continuacao"], retornar_detalhes=True,
    )

    assert status == "success"
    assert chamadas == ["read_range"]
    assert detalhes["tools_called"] == ["read_range"]
