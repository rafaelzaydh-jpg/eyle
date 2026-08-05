import importlib.util
import json
from pathlib import Path

import engine.engine as engine_mod
from engine import compiler, roteador
import llm.executar as llm_mod


BASE = Path(__file__).resolve().parents[1]


def test_core_reset_remove_modulos_legados():
    assert importlib.util.find_spec("engine.dicas") is None
    assert importlib.util.find_spec("engine.entender") is None
    assert importlib.util.find_spec("verify") is None


def test_engine_expoe_so_chat_e_agente_publicos():
    for nome in (
        "_processar_consulta", "_processar_dicas", "_processar_visao_geral",
        "_processar_engenharia", "_fallback_leitura_legado", "ciclo_analista",
    ):
        assert not hasattr(engine_mod, nome)


def test_compiler_nao_tem_prompts_do_pipeline_antigo():
    for nome in (
        "montar_prompt_analista", "montar_prompt_executor", "montar_prompt_dicas",
        "montar_prompt_visao_geral", "montar_prompt_engenheiro", "montar_prompt_entendedor",
    ):
        assert not hasattr(compiler, nome)


def test_llm_nao_tem_personalidades_legadas():
    for nome in (
        "executar_analista", "executar_executor", "executar_sugestor",
        "executar_engenheiro", "executar_entendedor",
    ):
        assert not hasattr(llm_mod, nome)


def test_roteador_so_retorna_chat_ou_agente():
    assert roteador.classificar_pergunta("oi")[0] == "chat"
    assert roteador.classificar_pergunta("analise o projeto")[0] == "agente"
    assert roteador.classificar_pergunta("edite app.py")[0] == "agente"
    assert roteador.classificar_pergunta("dê dicas para o projeto")[0] == "agente"


def test_config_base_nao_carrega_secoes_legadas():
    config = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    assert config["app_version"] == "2.7.4"
    assert config["agent"]["rollout_mode"] == "full"
    for secao in ("engine", "dicas", "entendimento"):
        assert secao not in config


def test_ingest_nao_depende_de_llm_para_entendimento():
    texto = (BASE / "ingest.py").read_text(encoding="utf-8")
    assert "engine.entender" not in texto
    assert "gerar_entendimento_arquivos" not in texto


def test_engine_falha_sem_projeto_em_vez_de_cair_em_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_mod, "MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *a, **k: 1)
    monkeypatch.setattr(engine_mod, "carregar_config", lambda: {
        "agent": {"rollout_mode": "full", "task_deadline_seconds": 60,
                  "max_llm_calls": 2, "max_total_generated_tokens": 1000}
    })
    resultado = engine_mod.processar("analise o projeto", registrar_pergunta=False)
    assert resultado["status"] == "failed"
    assert resultado["error_code"] == "PROJECT_NOT_INDEXED"
    assert resultado["roteador"]["tipo"] == "agente"
