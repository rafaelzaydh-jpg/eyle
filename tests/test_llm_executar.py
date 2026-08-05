#!/usr/bin/env python3
"""
tests/test_llm_executar.py
---------------------------
Atualizacao 15 -- primeiro teste automatizado de llm/executar.py. Cobre
o teto de tokens de saida (max_tokens/num_predict): confere que o
payload mandado pro backend local carrega o teto configurado, nos dois
formatos de backend (Ollama nativo e OpenAI-compatible), e que
max_tokens=0/None desliga o teto sem quebrar a chamada (comportamento
anterior a esta atualizacao, preservado pra quem preferir sem limite).

O socket de rede (urllib.request.urlopen) e' SEMPRE mockado -- nenhum
teste aqui precisa de um servidor local rodando.

Rodar com:
    pip install pytest --break-system-packages   # ou: pip install -r requirements-dev.txt
    pytest tests/test_llm_executar.py -v
"""
import io
import json
import os
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm.executar as llm_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _limpar_deteccao_llm():
    llm_mod._CAPACIDADES_OPENAI.clear()
    llm_mod._MODELOS_OPENAI.clear()
    yield
    llm_mod._CAPACIDADES_OPENAI.clear()
    llm_mod._MODELOS_OPENAI.clear()


class _RespostaFalsa:
    """Simula o objeto devolvido por urllib.request.urlopen(...) dentro
    de um 'with' -- so o suficiente pra .read() devolver um corpo JSON
    fixo, igual um backend real responderia."""

    def __init__(self, corpo_dict):
        self._bytes = json.dumps(corpo_dict).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes


def _config(**overrides_llm):
    cfg = {
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
        "temperature": 0.2,
        "timeout_seconds": 180,
        "cache": {"ativado": False},  # sem cache -- cada teste quer chamar o mock de verdade
    }
    cfg.update(overrides_llm)
    return {"llm": cfg}


def _capturar_payload(monkeypatch, corpo_resposta):
    """Monkeypatcha urllib.request.urlopen pra devolver corpo_resposta e
    devolve uma lista onde o payload (dict) de cada chamada e' guardado,
    na ordem em que aconteceram."""
    payloads = []

    def fake_urlopen(req, timeout=None):
        payloads.append(json.loads(req.data.decode("utf-8")))
        return _RespostaFalsa(corpo_resposta)

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    return payloads


# ---------------------------------------------------------------------------
# 1) Ollama nativo -- max_tokens vira "num_predict" dentro de "options"
# ---------------------------------------------------------------------------

def test_chamar_ollama_manda_num_predict_quando_max_tokens_configurado(monkeypatch):
    payloads = _capturar_payload(monkeypatch, {"message": {"content": "ok"}})

    resposta = llm_mod._chamar_llm(
        "prompt sistema", "prompt usuario",
        _config(openai_compatible=False, max_tokens=700),
    )

    assert resposta == "ok"
    assert payloads[0]["options"]["num_predict"] == 700


def test_chamar_ollama_sem_max_tokens_nao_manda_num_predict(monkeypatch):
    """max_tokens=0/None desliga o teto -- comportamento anterior a esta
    atualizacao precisa continuar disponivel pra quem preferir."""
    payloads = _capturar_payload(monkeypatch, {"message": {"content": "ok"}})

    llm_mod._chamar_llm("prompt sistema", "prompt usuario", _config(openai_compatible=False, max_tokens=0))

    assert "num_predict" not in payloads[0]["options"]


# ---------------------------------------------------------------------------
# 2) Backend OpenAI-compatible -- max_tokens vira "max_tokens" na raiz
# ---------------------------------------------------------------------------

def test_chamar_openai_compatible_manda_max_tokens_quando_configurado(monkeypatch):
    payloads = _capturar_payload(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    resposta = llm_mod._chamar_llm(
        "prompt sistema", "prompt usuario",
        _config(openai_compatible=True, max_tokens=512),
    )

    assert resposta == "ok"
    assert payloads[0]["max_tokens"] == 512


def test_chamar_openai_compatible_sem_max_tokens_nao_manda_o_campo(monkeypatch):
    payloads = _capturar_payload(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    llm_mod._chamar_llm("prompt sistema", "prompt usuario", _config(openai_compatible=True, max_tokens=None))

    assert "max_tokens" not in payloads[0]


# ---------------------------------------------------------------------------
# 3) Default de config.json (700) e' usado quando a chave nem existe
# ---------------------------------------------------------------------------

def test_max_tokens_usa_default_700_quando_chave_ausente(monkeypatch):
    cfg = _config(openai_compatible=True)  # _config() nao inclui max_tokens por padrao
    assert "max_tokens" not in cfg["llm"]
    payloads = _capturar_payload(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})

    llm_mod._chamar_llm("prompt sistema", "prompt usuario", cfg)

    assert payloads[0]["max_tokens"] == 700


# ---------------------------------------------------------------------------
# 4) Atualizacao 20 -- erro de backend/transporte nao e' resposta valida
# ---------------------------------------------------------------------------

def test_http_error_levanta_erro_llm_com_detalhe_do_backend(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":"modelo inexistente"}'),
        )

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_mod.ErroLLM) as capturado:
        llm_mod._chamar_llm(
            "prompt sistema", "prompt usuario",
            _config(openai_compatible=False),
        )

    assert "HTTP 400" in str(capturado.value)
    assert "modelo inexistente" in str(capturado.value)


def test_url_error_levanta_erro_llm_em_vez_de_retornar_string(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("conexao recusada")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_mod.ErroLLM) as capturado:
        llm_mod._chamar_llm(
            "prompt sistema", "prompt usuario",
            _config(openai_compatible=False),
        )

    assert "Nao foi possivel conectar" in str(capturado.value)
    assert not str(capturado.value).startswith("[erro]")


def test_erro_legado_no_cache_tambem_nao_vira_resposta(monkeypatch):
    cfg = _config(openai_compatible=False)
    cfg["llm"]["cache"]["ativado"] = True
    monkeypatch.setattr(
        llm_mod._cache, "obter",
        lambda *args, **kwargs: "[erro] falha antiga guardada indevidamente",
    )

    with pytest.raises(llm_mod.ErroLLM, match="falha antiga"):
        llm_mod._chamar_llm("prompt sistema", "prompt usuario", cfg)


def test_cache_separa_provider_e_base_url_mesmo_com_modelo_igual(monkeypatch):
    vistos = []

    def fake_obter(base_dir, fingerprint, *args, **kwargs):
        vistos.append(fingerprint)
        return "resposta do primeiro" if len(vistos) == 1 else None

    monkeypatch.setattr(llm_mod._cache, "obter", fake_obter)
    monkeypatch.setattr(llm_mod._cache, "definir", lambda *a, **k: None)
    monkeypatch.setattr(
        llm_mod, "_chamar_ollama", lambda *a, **k: "resposta do segundo",
    )

    config_a = _config(
        provider="ollama", base_url="http://servidor-a:11434",
        model="mesmo-modelo", cache={"ativado": True},
    )
    config_b = _config(
        provider="llama.cpp", base_url="http://servidor-b:8080",
        model="mesmo-modelo", cache={"ativado": True},
    )

    assert llm_mod._chamar_llm("s", "u", config_a) == "resposta do primeiro"
    assert llm_mod._chamar_llm("s", "u", config_b) == "resposta do segundo"
    assert vistos[0] != vistos[1]
    assert "servidor-a" in vistos[0]
    assert "llama.cpp" in vistos[1]

# ---------------------------------------------------------------------------
# 5) Compatibilidade basica com llama-server / modelos variados
# ---------------------------------------------------------------------------

def test_openai_detecta_modelo_unico_carregado_no_llama_server(monkeypatch):
    chamadas = []

    def fake_urlopen(req, timeout=None):
        chamadas.append((req.full_url, req.data))
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "qwen-carregado.gguf"}]})
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["model"] == "qwen-carregado.gguf"
        return _RespostaFalsa({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    resposta = llm_mod._chamar_llm(
        "s", "u",
        _config(openai_compatible=True, model="modelo-antigo.gguf"),
    )

    assert resposta == "ok"
    assert chamadas[0][0].endswith("/v1/models")
    assert chamadas[1][0].endswith("/v1/chat/completions")


def test_openai_json_mode_cai_para_prompt_quando_response_format_e_rejeitado(monkeypatch):
    payloads_chat = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        payload = json.loads(req.data.decode("utf-8"))
        payloads_chat.append(payload)
        if len(payloads_chat) <= 2:
            assert "response_format" in payload
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"response_format unsupported"}'),
            )
        assert "response_format" not in payload
        return _RespostaFalsa({
            "choices": [{"message": {"content": '{"final":"ok"}'}}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    resposta = llm_mod._chamar_llm(
        "s", "u", _config(openai_compatible=True), forcar_json=True,
    )

    assert resposta == '{"final":"ok"}'
    assert len(payloads_chat) == 3


def test_openai_lembra_que_json_mode_nativo_nao_e_suportado(monkeypatch):
    payloads_chat = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        payload = json.loads(req.data.decode("utf-8"))
        payloads_chat.append(payload)
        if len(payloads_chat) <= 2:
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"response_format unsupported"}'),
            )
        return _RespostaFalsa({
            "choices": [{"message": {"content": '{"final":"ok"}'}}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = _config(openai_compatible=True)

    llm_mod._chamar_llm("s1", "u1", cfg, forcar_json=True)
    llm_mod._chamar_llm("s2", "u2", cfg, forcar_json=True)

    assert "response_format" in payloads_chat[0]
    assert "response_format" in payloads_chat[1]
    assert "response_format" not in payloads_chat[2]
    assert "response_format" not in payloads_chat[3]


def test_openai_cai_para_system_incorporado_ao_user(monkeypatch):
    payloads_chat = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        payload = json.loads(req.data.decode("utf-8"))
        payloads_chat.append(payload)
        if len(payloads_chat) == 1:
            assert [m["role"] for m in payload["messages"]] == ["system", "user"]
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"system role unsupported by template"}'),
            )
        assert [m["role"] for m in payload["messages"]] == ["user"]
        assert "SYSTEM INSTRUCTIONS" in payload["messages"][0]["content"]
        return _RespostaFalsa({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    assert llm_mod._chamar_llm(
        "sistema", "usuario", _config(openai_compatible=True),
    ) == "ok"
    assert len(payloads_chat) == 2


def test_resposta_json_remove_bloco_think_e_cerca_markdown(monkeypatch):
    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        return _RespostaFalsa({
            "choices": [{"message": {
                "content": '<think>vou decidir</think>\n```json\n{"final":"ok"}\n```',
            }}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    resposta = llm_mod._chamar_llm(
        "s", "u", _config(openai_compatible=True), forcar_json=True,
    )
    assert resposta == '{"final":"ok"}'


def test_json_mode_envia_schema_e_desativa_thinking(monkeypatch):
    payloads = _capturar_payload(
        monkeypatch,
        {"choices": [{"message": {"content": '{"final":"ok"}'}}]},
    )

    resposta = llm_mod._chamar_llm(
        "s", "u", _config(openai_compatible=True), forcar_json=True,
    )

    assert resposta == '{"final":"ok"}'
    formato = payloads[0]["response_format"]
    assert formato["type"] == "json_object"
    assert formato["schema"]["type"] == "object"
    assert payloads[0]["reasoning_effort"] == "none"
    assert payloads[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_openai_usa_reasoning_content_se_content_vier_vazio(monkeypatch):
    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        return _RespostaFalsa({
            "choices": [{"message": {
                "content": "",
                "reasoning_content": '{"tool":"list_tree","arguments":{}}',
            }}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    resposta = llm_mod._chamar_llm(
        "s", "u", _config(openai_compatible=True), forcar_json=True,
    )
    assert resposta == '{"tool":"list_tree","arguments":{}}'



def test_openai_json_fallback_recupera_reasoning_content_sem_response_format(monkeypatch):
    payloads_chat = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        payload = json.loads(req.data.decode("utf-8"))
        payloads_chat.append(payload)
        if len(payloads_chat) <= 2:
            assert "response_format" in payload
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"response_format unsupported"}'),
            )
        assert "response_format" not in payload
        return _RespostaFalsa({
            "choices": [{"message": {
                "content": "",
                "reasoning_content": '{"tool":"list_tree","arguments":{}}',
            }}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    resposta = llm_mod._chamar_llm(
        "s", "u", _config(openai_compatible=True), forcar_json=True,
    )

    assert resposta == '{"tool":"list_tree","arguments":{}}'
    assert len(payloads_chat) == 3


def test_openai_json_mode_incompativel_lembrado_preserva_reasoning_content(monkeypatch):
    payloads_chat = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        payload = json.loads(req.data.decode("utf-8"))
        payloads_chat.append(payload)
        if len(payloads_chat) <= 2:
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":"response_format unsupported"}'),
            )
        numero = len(payloads_chat) - 2
        return _RespostaFalsa({
            "choices": [{"message": {
                "content": "",
                "reasoning_content": f'{{"final":"ok-{numero}"}}',
            }}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = _config(openai_compatible=True)

    primeira = llm_mod._chamar_llm("s1", "u1", cfg, forcar_json=True)
    segunda = llm_mod._chamar_llm("s2", "u2", cfg, forcar_json=True)

    assert primeira == '{"final":"ok-1"}'
    assert segunda == '{"final":"ok-2"}'
    assert "response_format" not in payloads_chat[2]
    assert "response_format" not in payloads_chat[3]


def test_openai_textual_nao_expoe_reasoning_content(monkeypatch):
    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            return _RespostaFalsa({"data": [{"id": "modelo-teste"}]})
        return _RespostaFalsa({
            "choices": [{"message": {
                "content": "",
                "reasoning_content": "raciocinio privado",
            }}],
        })

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(llm_mod.ErroLLM) as erro:
        llm_mod._chamar_llm("s", "u", _config(openai_compatible=True))

    assert erro.value.error_code == "EMPTY_RESPONSE"


def test_chamada_estruturada_ignora_cache_envenenado(monkeypatch):
    cfg = _config(openai_compatible=False, cache={"ativado": True})
    consultas_cache = []
    gravacoes_cache = []

    monkeypatch.setattr(
        llm_mod._cache, "obter",
        lambda *a, **k: consultas_cache.append((a, k)) or "texto invalido antigo",
    )
    monkeypatch.setattr(
        llm_mod._cache, "definir",
        lambda *a, **k: gravacoes_cache.append((a, k)),
    )
    monkeypatch.setattr(
        llm_mod, "_chamar_ollama",
        lambda *a, **k: '{"final":"novo"}',
    )

    resposta = llm_mod._chamar_llm("s", "u", cfg, forcar_json=True)

    assert resposta == '{"final":"novo"}'
    assert consultas_cache == []
    assert gravacoes_cache == []


def test_diagnosticar_backend_openai_sem_gerar_tokens(monkeypatch):
    import contextlib
    import io

    @contextlib.contextmanager
    def fake_abrir(req, connect_timeout, read_timeout=None):
        assert req.full_url == "http://localhost:8080/v1/models"
        yield io.BytesIO(b'{"data":[{"id":"modelo-local"}]}')

    monkeypatch.setattr(llm_mod, "_abrir_url", fake_abrir)
    resultado = llm_mod.diagnosticar_backend({
        "llm": {
            "base_url": "http://localhost:8080",
            "openai_compatible": True,
        }
    })

    assert resultado["ok"] is True
    assert resultado["reachable"] is True
    assert resultado["models"] == ["modelo-local"]
    assert resultado["model_count"] == 1


def test_diagnosticar_backend_reporta_servidor_inacessivel(monkeypatch):
    import contextlib
    import urllib.error

    @contextlib.contextmanager
    def fake_abrir(req, connect_timeout, read_timeout=None):
        raise urllib.error.URLError("conexao recusada")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_mod, "_abrir_url", fake_abrir)
    resultado = llm_mod.diagnosticar_backend({
        "llm": {
            "base_url": "http://localhost:8080",
            "openai_compatible": True,
        }
    })

    assert resultado["ok"] is False
    assert resultado["reachable"] is False
    assert resultado["error_code"] == "BACKEND_UNREACHABLE"
    assert "conexao recusada" in resultado["detail"]


class _DelayedOpenAIHandler(BaseHTTPRequestHandler):
    response_delay = 0.25

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(tamanho)
        time.sleep(self.response_delay)
        corpo = json.dumps({
            "choices": [{"message": {"content": "resposta depois do connect timeout"}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, format, *args):  # noqa: A003
        return


def test_connect_timeout_nao_cancela_geracao_antes_do_read_timeout():
    """Regressao 55.3: llama-server envia cabecalhos depois da geracao.

    Antes, _abrir_url passava connect_timeout=5s ao urlopen. Como urlopen
    tambem usa esse valor para esperar a linha de status HTTP, toda resposta
    nao-streaming acima de cinco segundos era cancelada pelo cliente.
    """
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _DelayedOpenAIHandler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{servidor.server_port}"
    inicio = time.monotonic()
    try:
        resposta = llm_mod._chamar_openai_compatible(
            base_url, "modelo-teste", "sistema", "usuario", 0.2,
            timeout=0.05, read_timeout=1.0,
        )
    finally:
        servidor.shutdown()
        servidor.server_close()
        thread.join(timeout=1.0)

    assert resposta == "resposta depois do connect timeout"
    assert time.monotonic() - inicio >= _DelayedOpenAIHandler.response_delay
