import json

import engine.agent as agent_mod
from engine.task_contract import build_task_contract, evaluate_target_coverage
from tests.test_revision_274_rev2 import _config


def _project(tmp_path):
    (tmp_path / "config.py").write_text("PREFIXO = 'eyle'\n", encoding="utf-8")
    (tmp_path / "core.py").write_text(
        "from config import PREFIXO\n\ndef montar_id(numero):\n    return f'{PREFIXO}-{numero}'\n",
        encoding="utf-8",
    )


def test_task_contract_exige_origem_e_valor_literal():
    contract = build_task_contract(
        "Leia config.py e core.py e explique de onde vem o prefixo de montar_id."
    )
    evidence = [
        {
            "id": "ev-1", "estado": "fresh", "arquivo": "config.py",
            "conteudo": "PREFIXO = 'eyle'\n", "leitura_completa": True,
        },
        {
            "id": "ev-2", "estado": "fresh", "arquivo": "core.py",
            "conteudo": "from config import PREFIXO\n\ndef montar_id(numero):\n    return f'{PREFIXO}-{numero}'\n",
            "leitura_completa": True,
        },
    ]
    incomplete = evaluate_target_coverage(
        contract,
        [{"text": "core.py importa PREFIXO e o usa em montar_id."}],
        evidence,
    )
    assert incomplete["ok"] is False
    assert any(item["kind"] == "origin_and_literal_value" for item in incomplete["missing"])

    complete = evaluate_target_coverage(
        contract,
        [
            {"text": "config.py define PREFIXO como eyle."},
            {"text": "core.py importa PREFIXO e o usa em montar_id."},
        ],
        evidence,
    )
    assert complete["ok"] is True


def test_fast_path_finaliza_sem_chamada_ready_to_finalize(tmp_path, monkeypatch):
    _project(tmp_path)
    agent_calls = []
    decisions = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"config.py"}}',
        '{"tool":"read_file","arguments":{"caminho_relativo":"core.py"}}',
    ])

    def agent(*args):
        agent_calls.append(args[0])
        return next(decisions)

    finalizer_calls = []

    def finalizer(prompt, config):
        finalizer_calls.append(prompt)
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
                ],
                "verification": "Leitura fresca dos dois arquivos.",
                "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_agente_llm", agent)
    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", finalizer)
    cfg = _config()
    cfg["agent"]["project_read_fast_path_enabled"] = True
    cfg["agent"]["target_coverage_enabled"] = True
    cfg["agent"]["project_read_single_repair_enabled"] = True

    status, text, _, details = agent_mod.executar_agente(
        "Leia config.py e core.py e explique de onde vem o prefixo de montar_id.",
        cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert "eyle" in text
    assert len(agent_calls) == 2
    assert len(finalizer_calls) == 1
    assert details["target_coverage"]["ok"] is True
    assert details["project_read_finalizer_calls"] == 1


def test_target_coverage_faz_um_unico_reparo_direcionado(tmp_path, monkeypatch):
    _project(tmp_path)
    decisions = iter([
        '{"tool":"read_file","arguments":{"caminho_relativo":"config.py"}}',
        '{"tool":"read_file","arguments":{"caminho_relativo":"core.py"}}',
    ])
    monkeypatch.setattr(agent_mod, "executar_agente_llm", lambda *args: next(decisions))

    finalizer_prompts = []
    finals = iter([
        {
            "final": {
                "claims": [{
                    "type": "fact",
                    "text": "core.py importa `PREFIXO` e o usa em `montar_id`.",
                    "evidence_ids": ["ev-0002"],
                    "basis": "",
                }],
                "verification": "Primeira tentativa.",
                "limitations": [],
            }
        },
        {
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
                ],
                "verification": "Reparo direcionado.",
                "limitations": [],
            }
        },
    ])

    def finalizer(prompt, config):
        finalizer_prompts.append(prompt)
        return json.dumps(next(finals))

    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", finalizer)
    cfg = _config()
    cfg["agent"]["project_read_fast_path_enabled"] = True
    cfg["agent"]["target_coverage_enabled"] = True
    cfg["agent"]["project_read_single_repair_enabled"] = True

    status, text, _, details = agent_mod.executar_agente(
        "Leia config.py e core.py e explique de onde vem o prefixo de montar_id.",
        cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert "eyle" in text
    assert len(finalizer_prompts) == 2
    assert "TARGET COVERAGE REPAIR" in finalizer_prompts[1]
    assert details["target_coverage"]["ok"] is True
    assert details["recovery_layer"] == "directed_project_read_finalizer"


def test_single_file_fast_path_economiza_ready_call(tmp_path, monkeypatch):
    (tmp_path / "audio.py").write_text(
        "def limitar_volume(valor):\n    return max(0, min(100, valor))\n\n"
        "def tocar(nome, volume=50):\n    volume = limitar_volume(volume)\n    return f'{nome}:{volume}'\n",
        encoding="utf-8",
    )
    agent_calls = []

    def agent(*args):
        agent_calls.append(args[0])
        return '{"tool":"read_file","arguments":{"caminho_relativo":"audio.py"}}'

    finalizer_calls = []

    def finalizer(prompt, config):
        finalizer_calls.append(prompt)
        return json.dumps({
            "final": {
                "claims": [
                    {
                        "type": "fact",
                        "text": "`limitar_volume` restringe o volume entre 0 e 100.",
                        "evidence_ids": ["ev-0001"], "basis": "",
                    },
                    {
                        "type": "fact",
                        "text": "`tocar` chama `limitar_volume` antes de montar o retorno.",
                        "evidence_ids": ["ev-0001"], "basis": "",
                    },
                ],
                "verification": "Arquivo completo.", "limitations": [],
            }
        })

    monkeypatch.setattr(agent_mod, "executar_agente_llm", agent)
    monkeypatch.setattr(agent_mod, "executar_project_read_finalizer_llm", finalizer)
    cfg = _config()
    cfg["agent"]["project_read_fast_path_enabled"] = True
    cfg["agent"]["target_coverage_enabled"] = True
    cfg["agent"]["project_read_single_repair_enabled"] = True

    status, _, _, details = agent_mod.executar_agente(
        "Analise audio.py inteiro e explique tocar e limitar_volume.",
        cfg, projeto={"caminho_origem": str(tmp_path)},
        retornar_detalhes=True, modo="analyze",
    )

    assert status == "success"
    assert len(agent_calls) == 1
    assert len(finalizer_calls) == 1
    assert details["target_coverage"]["ok"] is True


def test_benchmark_smoke_seleciona_somente_ids_pedidos():
    from engine.benchmark import selecionar_casos

    cases = selecionar_casos("01_audio_14_linhas,03_dois_arquivos,06_edicao_confirmada")
    assert [item["id"] for item in cases] == [
        "01_audio_14_linhas", "03_dois_arquivos", "06_edicao_confirmada",
    ]
