import json
import sys

import engine.agent as agent_mod
import engine.sandbox as sandbox_mod


def _config():
    return {
        "llm": {
            "context_window_tokens": 8192,
            "max_tokens": 700,
            "project_read_finalizer_max_tokens": 1400,
        },
        "context_engine": {
            "safety_margin_tokens": 500,
            "chars_per_token_fallback": 3,
            "max_recent_observations": 4,
        },
        "codar": {"fazer_backup": False, "testes": {"ativado": False}},
        "agent": {
            "enabled": True,
            "enabled_modes": ["analyze", "suggest", "edit"],
            "max_steps": 8,
            "max_no_progress_decisions": 3,
            "max_tentativas_parse": 1,
            "max_erros_consecutivos": 3,
            "max_chars_por_observacao": 1000,
            "max_fatos_importantes": 10,
            "max_tree_entries": 200,
            "max_tree_depth": 6,
            "max_read_range_lines": 400,
            "require_confirmation_for_write": True,
            "require_confirmation_for_exec": False,
            "exigir_run_tests_apos_escrita": True,
            "project_read_finalizer_enabled": True,
            "deterministic_symbol_lookup_enabled": True,
            "deterministic_post_write_enabled": True,
            "semantic_grounding": {"enabled": True},
        },
    }


def test_project_read_aceita_claims_estruturadas_e_grounding_lexical_e_advisory(tmp_path, monkeypatch):
    (tmp_path / "config.py").write_text("PREFIXO = 'eyle'\n", encoding="utf-8")
    (tmp_path / "core.py").write_text(
        "from config import PREFIXO\n\ndef montar_id(numero):\n    return f'{PREFIXO}-{numero}'\n",
        encoding="utf-8",
    )
    respostas = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"config.py"}}',
        '{"tool":"read_file","arguments":{"caminho_relativo":"core.py"}}',
        '{"ready_to_finalize":true}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))

    def finalizer(*args):
        return json.dumps({
            "final": {
                "claims": [
                    {
                        "type": "fact",
                        "text": "config.py define `PREFIXO` como `eyle`.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "core.py importa `PREFIXO` e o usa em `montar_id`.",
                        "evidence_ids": ["ev-0002"],
                        "basis": "",
                    },
                    {
                        "type": "inference",
                        "text": "Assim, `montar_id(7)` produz um identificador com o prefixo `eyle`.",
                        "evidence_ids": ["ev-0001", "ev-0002"],
                        "basis": "A constante e definida em config.py e interpolada por core.py.",
                    },
                ],
                "verification": "Leitura fresca dos dois arquivos.",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", finalizer)
    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Explique como config.py e core.py formam o identificador",
        _config(), projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert pendente is None
    assert "PREFIXO" in texto
    assert "montar_id" in texto
    assert detalhes["semantic_grounding"]["ok"] is True
    assert detalhes["semantic_grounding"]["warnings"]


def test_project_read_recusa_claim_factual_sem_evidencia():
    estado = agent_mod.AgentState(config=_config())
    decisao = {
        "final": {
            "claims": [{
                "type": "fact", "text": "a.py define valor.",
                "evidence_ids": [], "basis": "",
            }],
            "verification": "", "limitations": [],
        }
    }

    conclusao, erro = agent_mod._normalizar_conclusao(
        decisao, estado, "project_read",
    )

    assert conclusao is None
    assert "exige evidence_ids" in erro


def test_trusted_local_executa_allowlist_em_snapshot_sem_shell(tmp_path):
    original = tmp_path / "estado.txt"
    original.write_text("original", encoding="utf-8")
    cfg = {
        "backend": "trusted_local",
        "allow_trusted_local": True,
        "comandos_permitidos": [[sys.executable]],
        "timeout_segundos": 10,
        "cpu_segundos": 10,
        "memoria_mb": 512,
        "max_processos": 32,
        "max_arquivos_abertos": 64,
        "max_saida_kb": 64,
        "copiar_projeto": True,
    }

    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path),
        [sys.executable, "-c", "from pathlib import Path; Path('estado.txt').write_text('mudou'); print('ok')"],
        cfg,
    )

    assert resultado["executado"] is True
    assert resultado["ok"] is True
    assert resultado["backend"] == "trusted_local"
    assert resultado["network_isolated"] is False
    assert "ok" in resultado["saida"]
    assert original.read_text(encoding="utf-8") == "original"


def test_trusted_local_exige_autorizacao_explicita(tmp_path):
    resultado = sandbox_mod.executar_no_sandbox(
        str(tmp_path), [sys.executable, "-c", "print('x')"], {
            "backend": "trusted_local",
            "allow_trusted_local": False,
            "comandos_permitidos": [[sys.executable]],
            "timeout_segundos": 5,
            "cpu_segundos": 5,
            "memoria_mb": 128,
            "max_processos": 16,
            "max_arquivos_abertos": 32,
            "max_saida_kb": 32,
        },
    )
    assert resultado["executado"] is False
    assert "allow_trusted_local=true" in resultado["erro"]


def test_project_read_claim_com_ancora_inventada_e_recuperada(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text(
        "def limitar_volume(valor):\n    return max(0, min(100, valor))\n",
        encoding="utf-8",
    )
    respostas = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"audio.py"}}',
        '{"ready_to_finalize":true}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(respostas))
    finalizers = iter([
        {
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "`limitar_volume` chama `os.remove`.",
                    "evidence_ids": ["ev-0001"],
                    "basis": "",
                }],
                "verification": "Leitura fresca.",
                "limitations": [],
            }
        },
        {
            "final": {
                "claims": [
                    {
                        "type": "fact",
                        "text": "`limitar_volume` limita o valor ao intervalo entre 0 e 100.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "A implementação usa `max` e `min` para aplicar os limites.",
                        "evidence_ids": ["ev-0001"],
                        "basis": "",
                    },
                ],
                "verification": "Reparo único com leitura fresca.",
                "limitations": [],
            }
        },
    ])
    monkeypatch.setattr(
        agent_mod, "executar_project_read_finalizer_llm",
        lambda *args: json.dumps(next(finalizers)),
    )

    status, texto, pendente, detalhes = agent_mod.executar_agente(
        "Analise audio.py", _config(),
        projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert pendente is None
    assert "os.remove" not in texto
    assert "limitar_volume" in texto
    assert detalhes["grounding_fallback_applied"] is True
    assert detalhes["recovery_layer"] == "deterministic_structured_claims"
