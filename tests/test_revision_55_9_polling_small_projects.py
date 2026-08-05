#!/usr/bin/env python3
"""Regressoes 55.9: polling sem 429 e fallback com codigo real."""
import re
from pathlib import Path

from engine import engine as engine_mod
from engine.compiler import montar_prompt_visao_geral


def _config_pequeno():
    return {
        "context": {
            "token_budget": 1500,
            "chars_per_token": 4,
            "small_project_full_read_max_files": 8,
            "small_project_full_read_max_lines": 600,
            "small_project_full_read_max_chars": 16000,
        }
    }


def test_projeto_pequeno_entra_na_visao_geral_com_codigo_real(tmp_path):
    (tmp_path / "app.py").write_text(
        "def dobro(valor):\n    return valor * 2\n",
        encoding="utf-8",
    )
    estrutura = {
        "app.py": {"linhas": 2, "funcoes_classes": ["dobro"]},
    }
    projeto = {"caminho_origem": str(tmp_path), "projeto": "mini", "arquivos": 1}

    codigos = engine_mod._codigos_reais_projeto_pequeno(
        _config_pequeno(), projeto, estrutura,
    )
    prompt = montar_prompt_visao_geral(
        "Faça a analise do projeto",
        projeto=projeto,
        estrutura=estrutura,
        codigos_reais=codigos,
    )

    assert "CODIGO REAL FRESCO DO PROJETO PEQUENO" in prompt
    assert "def dobro(valor)" in prompt
    assert "return valor * 2" in prompt
    assert "nao diga que ele esta indisponivel" in prompt
    assert "Nao reduza a resposta a contagem de arquivos ou linhas" in prompt


def test_projeto_acima_do_limite_nao_forca_leitura_integral(tmp_path):
    for indice in range(3):
        (tmp_path / f"f{indice}.py").write_text("x = 1\n", encoding="utf-8")
    estrutura = {
        f"f{indice}.py": {"linhas": 1, "funcoes_classes": []}
        for indice in range(3)
    }
    config = _config_pequeno()
    config["context"]["small_project_full_read_max_files"] = 2

    assert engine_mod._codigos_reais_projeto_pequeno(
        config,
        {"caminho_origem": str(tmp_path)},
        estrutura,
    ) == {}


def test_fallback_de_analise_pequena_envia_codigo_ao_executor(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text(
        "def soma(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    estrutura = {"app.py": {"linhas": 2, "funcoes_classes": ["soma"]}}
    projeto = {
        "caminho_origem": str(tmp_path),
        "projeto": "mini",
        "arquivos": 1,
        "tokens_estimados_totais": 20,
    }
    capturado = {}

    monkeypatch.setattr(engine_mod, "carregar_estrutura", lambda: estrutura)
    monkeypatch.setattr(engine_mod, "carregar_decisoes", lambda: [])
    monkeypatch.setattr(
        engine_mod,
        "classificar_pergunta",
        lambda *args, **kwargs: ("visao_geral", "analise geral"),
    )

    def executar(prompt, config):
        capturado["prompt"] = prompt
        return "O projeto soma dois valores usando app.py:1-2."

    monkeypatch.setattr(engine_mod, "executar_executor", executar)
    monkeypatch.setattr(
        engine_mod,
        "validar_resposta",
        lambda *args, **kwargs: {
            "verificacao_aprovada": True,
            "confianca": 1.0,
            "avisos": [],
        },
    )
    monkeypatch.setattr(engine_mod, "salvar_texto_atomico", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine_mod, "registrar_historico", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine_mod, "registrar_mensagem", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        engine_mod.fila_persistente,
        "atualizar_tarefa_agente",
        lambda *args, **kwargs: True,
    )

    resultado = engine_mod._fallback_leitura_legado(
        "Faça a analise do projeto",
        _config_pequeno(),
        projeto,
        {},
        "agente falhou",
        "task-55-9",
        "invalid_agent_json",
    )

    assert "def soma(a, b)" in capturado["prompt"]
    assert "return a + b" in capturado["prompt"]
    assert resultado["agente_status"] == "success"
    assert resultado["roteador"]["fallback_pipeline"] == "visao_geral"


def test_polling_do_painel_fica_abaixo_do_rate_limit_padrao():
    javascript = Path("web/static/app.js").read_text(encoding="utf-8")

    pending = int(re.search(r"CONVERSA_POLL_PENDING = (\d+)", javascript).group(1))
    max_jobs = int(re.search(r"MAX_JOBS_PER_POLL = (\d+)", javascript).group(1))
    status_poll = int(re.search(r"STATUS_POLL = (\d+)", javascript).group(1))

    requisicoes_minuto = ((max_jobs + 1) * 60000 / pending) + (60000 / status_poll)
    assert requisicoes_minuto < 180
    assert 'res.headers.get("Retry-After")' in javascript
    assert "rateLimitUntil" in javascript
    assert "setInterval(fetchStatus" not in javascript
