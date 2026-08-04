#!/usr/bin/env python3
"""Atualizacao 27: autenticacao, rate limit e status sem caminho absoluto."""
import os
import stat
import pytest

pytest.importorskip("flask")
import web.routes as routes


def _cabecalho(token="segredo-de-teste"):
    return {"Authorization": f"Bearer {token}"}


def _cliente(monkeypatch):
    routes.app.config.update(
        TESTING=True,
        EYLE_API_TOKEN="segredo-de-teste",
        EYLE_RATE_LIMIT_REQUESTS=100,
        EYLE_RATE_LIMIT_AUTH_FAILURES=100,
        EYLE_RATE_LIMIT_WINDOW_SECONDS=60,
    )
    routes._limitador.limpar()
    monkeypatch.setattr(
        routes.eyle_engine,
        "carregar_projeto",
        lambda: {
            "projeto": "Teste",
            "caminho_origem": "/tmp/segredo/projeto",
            "arquivos": 3,
            "chunks": 5,
            "tokens_estimados_totais": 100,
            "source_hash": "nao-publico",
        },
    )
    monkeypatch.setattr(routes.eyle_engine, "carregar_conversa", lambda: [])
    monkeypatch.setattr(
        routes.queue,
        "estatisticas",
        lambda **kwargs: {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 1,
            "live_workers": 1,
            "head_of_line_blocked": False,
            "ultima_falha": {
                "erro": f"falha em /tmp/segredo/projeto e {routes.BASE_DIR}/context",
            },
        },
    )
    monkeypatch.setattr(routes.queue, "tamanho", lambda: 0)
    monkeypatch.setattr(routes.telemetry, "summary", lambda *args, **kwargs: {"total": 0, "groups": {}})
    return routes.app.test_client()


def test_shell_visual_e_publico_mas_api_exige_bearer(monkeypatch):
    cliente = _cliente(monkeypatch)

    assert cliente.get("/").status_code == 200
    sem_token = cliente.get("/status")
    assert sem_token.status_code == 401
    assert sem_token.get_json()["error_code"] == "UNAUTHORIZED"
    assert sem_token.headers["WWW-Authenticate"] == "Bearer"

    autorizado = cliente.get("/status", headers=_cabecalho())
    assert autorizado.status_code == 200
    assert autorizado.get_json()["projeto"]["projeto"] == "Teste"
    assert autorizado.headers["Cache-Control"] == "no-store"
    assert autorizado.headers["X-Frame-Options"] == "DENY"


def test_status_nao_expoe_caminho_absoluto_nem_hash_interno(monkeypatch):
    cliente = _cliente(monkeypatch)

    resposta = cliente.get("/status", headers=_cabecalho())
    corpo = resposta.get_data(as_text=True)
    projeto = resposta.get_json()["projeto"]

    assert resposta.status_code == 200
    assert "caminho_origem" not in projeto
    assert "source_hash" not in projeto
    assert "/tmp/segredo/projeto" not in corpo
    assert routes.BASE_DIR not in corpo


def test_requisicao_sem_token_nao_chega_a_mutacao(monkeypatch):
    cliente = _cliente(monkeypatch)
    chamadas = []
    monkeypatch.setattr(
        routes.eyle_engine,
        "registrar_mensagem_com_snapshot",
        lambda *args: chamadas.append(args) or (7, []),
    )
    monkeypatch.setattr(routes.queue, "adicionar", lambda evento: 11)

    resposta = cliente.post("/enviar", json={"texto": "nao executar"})

    assert resposta.status_code == 401
    assert chamadas == []


def test_rate_limit_devolve_429_e_retry_after(monkeypatch):
    cliente = _cliente(monkeypatch)
    routes.app.config["EYLE_RATE_LIMIT_REQUESTS"] = 2
    routes._limitador.limpar()

    assert cliente.get("/status", headers=_cabecalho()).status_code == 200
    assert cliente.get("/status", headers=_cabecalho()).status_code == 200
    bloqueada = cliente.get("/status", headers=_cabecalho())

    assert bloqueada.status_code == 429
    assert bloqueada.get_json()["error_code"] == "RATE_LIMITED"
    assert int(bloqueada.headers["Retry-After"]) >= 1


def test_tentativas_de_token_invalido_tem_limite_separado(monkeypatch):
    cliente = _cliente(monkeypatch)
    routes.app.config["EYLE_RATE_LIMIT_AUTH_FAILURES"] = 2
    routes._limitador.limpar()

    assert cliente.get("/status", headers=_cabecalho("errado")).status_code == 401
    assert cliente.get("/status", headers=_cabecalho("errado")).status_code == 401
    assert cliente.get("/status", headers=_cabecalho("errado")).status_code == 429


def test_token_aleatorio_e_persistente_com_permissao_restrita(monkeypatch, tmp_path):
    caminho = tmp_path / "web_api_token.txt"
    monkeypatch.delenv("EYLE_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "TOKEN_PATH", str(caminho))
    monkeypatch.setattr(routes, "_carregar_config_web", lambda: {})

    primeiro = routes.obter_api_token()
    segundo = routes.obter_api_token()

    assert primeiro == segundo
    assert len(primeiro) >= 32
    assert stat.S_IMODE(os.stat(caminho).st_mode) == 0o600


def test_job_expoe_estado_real_sem_payload_nem_resultado(monkeypatch):
    cliente = _cliente(monkeypatch)
    monkeypatch.setattr(routes.queue, "obter", lambda job_id: {
        "id": job_id, "tipo": "pergunta", "status": "processing",
        "tentativas": 1, "payload": {"texto": "segredo", "mensagem_id": 77},
        "resultado": {"resposta": "ainda privada"}, "erro": None,
        "criado_em": "2026-08-01T00:00:00Z",
    })

    resposta = cliente.get("/jobs/42", headers=_cabecalho())
    dados = resposta.get_json()

    assert resposta.status_code == 200
    assert dados["id"] == 42
    assert dados["status"] == "processing"
    assert "payload" not in dados
    assert "resultado" not in dados
    assert dados["mensagem_id"] == 77
    assert dados["texto_resumo"] == "segredo"


def test_health_reflete_worker_e_head_of_line(monkeypatch):
    cliente = _cliente(monkeypatch)
    resposta = cliente.get("/health", headers=_cabecalho())
    assert resposta.status_code == 200
    assert resposta.get_json()["status"] == "ok"

    monkeypatch.setattr(
        routes.queue, "estatisticas",
        lambda **kwargs: {
            "live_workers": 0,
            "head_of_line_blocked": True,
            "oldest_pending_seconds": 70,
            "oldest_processing_seconds": 80,
        },
    )
    degradado = cliente.get("/health", headers=_cabecalho())
    assert degradado.status_code == 503
    assert degradado.get_json()["status"] == "degraded"


def test_job_falho_expoe_so_diagnostico_seguro(monkeypatch):
    cliente = _cliente(monkeypatch)
    monkeypatch.setattr(routes.queue, "obter", lambda job_id: {
        "id": job_id, "tipo": "pergunta", "status": "failed",
        "tentativas": 1, "payload": {"texto": "segredo"},
        "resultado": {
            "status": "failed",
            "error_code": "TRANSPORT_ERROR",
            "transient": True,
            "resposta": f"falha em {routes.BASE_DIR}/llm",
            "roteador": {"motivo": "interno"},
        },
        "erro": f"detalhe em {routes.BASE_DIR}/context",
    })

    resposta = cliente.get("/jobs/9", headers=_cabecalho())
    dados = resposta.get_json()
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert dados["status"] == "failed"
    assert dados["error_code"] == "TRANSPORT_ERROR"
    assert dados["transient"] is True
    assert "mensagem" in dados
    assert "payload" not in dados
    assert "resultado" not in dados
    assert "roteador" not in corpo
    assert routes.BASE_DIR not in corpo
