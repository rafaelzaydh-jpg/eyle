#!/usr/bin/env python3
"""
routes.py
---------
O navegador SO fala com estes endpoints. Nunca com a LLM diretamente.

    GET    /               -> painel de chat (templates/index.html), so HTML/CSS/JS
    POST   /enviar          -> entra na fila, responde na hora ({"status": "ok"})
    GET    /conversa        -> conversa persistida (memory/conversa.json)
    DELETE /mensagem/<id>   -> remove mensagem (fila + memoria)
    GET    /status          -> estatisticas do projeto indexado + tamanho da fila
    GET    /jobs/<id>       -> estado persistido exato de uma tarefa

O painel em "/" e so um cliente: ele nunca chama a LLM nem o Engine
direto, so faz polling de /conversa e /status e manda texto para
/enviar -- exatamente o mesmo contrato que qualquer outro cliente
(curl, app mobile futuro) usaria. Os endpoints de dados exigem um token
Bearer e tem rate limit por IP; o HTML/CSS/JS publico nao carrega o token
nem qualquer dado da memoria.

Fechar a aba nao interrompe nada: quem processa a fila e o
engine/worker.py, que continua rodando no mesmo processo do Flask
(veja main.py -> comando "serve").

Unica dependencia externa de todo o projeto: Flask (`pip install flask`).
"""
import json
import math
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from flask import Flask, jsonify, render_template, request

from engine import queue
from engine import telemetry
from engine import engine as eyle_engine
from engine.config_schema import carregar_config_validada

app = Flask(__name__)

CONTEXT_DIR = os.path.join(BASE_DIR, "context")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TOKEN_PATH = os.path.join(CONTEXT_DIR, "web_api_token.txt")

_ROTAS_PUBLICAS = {"painel", "static"}
_CHAVES_PUBLICAS_PROJETO = (
    "projeto",
    "arquivos",
    "chunks",
    "tokens_estimados_totais",
    "criado_ou_atualizado_em",
    "version",
)
_CHAVES_PUBLICAS_JOB = (
    "id", "tipo", "status", "tentativas", "criado_em", "atualizado_em",
    "iniciado_em", "concluido_em", "erro",
)


class _LimitadorJanela:
    """Rate limit simples, em memoria, por IP e por tipo de tentativa."""

    def __init__(self):
        self._eventos = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, chave, limite, janela_segundos):
        limite = max(1, int(limite))
        janela_segundos = max(1.0, float(janela_segundos))
        agora = time.monotonic()
        inicio = agora - janela_segundos
        with self._lock:
            eventos = self._eventos[chave]
            while eventos and eventos[0] <= inicio:
                eventos.popleft()
            if len(eventos) >= limite:
                espera = max(1, math.ceil(janela_segundos - (agora - eventos[0])))
                return False, espera
            eventos.append(agora)
        return True, 0

    def limpar(self):
        with self._lock:
            self._eventos.clear()


_limitador = _LimitadorJanela()


@app.after_request
def cabecalhos_seguros(resposta):
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    if request.endpoint not in _ROTAS_PUBLICAS:
        resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _carregar_config_web():
    config = carregar_config_validada(CONFIG_PATH)
    web = config.get("web", {}) if isinstance(config, dict) else {}
    return web if isinstance(web, dict) else {}


def _ler_token_arquivo(caminho):
    try:
        os.chmod(caminho, 0o600)
    except OSError as erro:
        telemetry.record(
            "internal", "web_token_permissions", "failed",
            metadata={
                "path": os.path.basename(caminho),
                "exception": type(erro).__name__,
                "detail": str(erro)[:300],
            },
        )
    with open(caminho, "r", encoding="utf-8") as arquivo:
        token = arquivo.read().strip()
    if len(token) < 32:
        raise RuntimeError(
            "token da API invalido; defina EYLE_API_TOKEN ou remova "
            "context/web_api_token.txt para gerar outro"
        )
    return token


def obter_api_token():
    """Resolve o segredo sem inclui-lo no codigo, HTML ou resposta da API.

    Ordem: variavel de ambiente, valor explicito no config e arquivo local
    persistente. Na primeira execucao, o arquivo e criado com permissao 0600.
    """
    token_ambiente = os.environ.get("EYLE_API_TOKEN", "").strip()
    if token_ambiente:
        if len(token_ambiente) < 32:
            raise RuntimeError("EYLE_API_TOKEN precisa ter pelo menos 32 caracteres")
        return token_ambiente

    token_config = str(_carregar_config_web().get("api_token") or "").strip()
    if token_config:
        if len(token_config) < 32:
            raise RuntimeError("web.api_token precisa ter pelo menos 32 caracteres")
        return token_config

    try:
        return _ler_token_arquivo(TOKEN_PATH)
    except FileNotFoundError:
        pass

    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    token_novo = secrets.token_urlsafe(32)
    try:
        descritor = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _ler_token_arquivo(TOKEN_PATH)
    with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
        arquivo.write(token_novo + "\n")
        arquivo.flush()
        os.fsync(arquivo.fileno())
    return token_novo


def origem_api_token():
    """Informa onde o operador deve consultar o token ativo, sem expo-lo."""
    if os.environ.get("EYLE_API_TOKEN", "").strip():
        return "variavel de ambiente EYLE_API_TOKEN"
    if str(_carregar_config_web().get("api_token") or "").strip():
        return "config.json -> web.api_token"
    return TOKEN_PATH


def _valor_limite(nome_app, nome_config, padrao):
    valor_app = app.config.get(nome_app)
    if valor_app is not None:
        valor = valor_app
    else:
        rate = _carregar_config_web().get("rate_limit", {})
        if not isinstance(rate, dict):
            return padrao
        valor = rate.get(nome_config, padrao)
    try:
        return max(1, int(valor))
    except (TypeError, ValueError):
        return padrao


def _token_recebido():
    autorizacao = request.headers.get("Authorization", "")
    esquema, separador, credencial = autorizacao.partition(" ")
    if separador and esquema.lower() == "bearer":
        return credencial.strip()
    return request.headers.get("X-API-Token", "").strip()


def _resposta_rate_limit(espera):
    resposta = jsonify({
        "status": "erro",
        "error_code": "RATE_LIMITED",
        "motivo": "limite de requisicoes excedido",
    })
    resposta.status_code = 429
    resposta.headers["Retry-After"] = str(espera)
    return resposta


@app.before_request
def proteger_api():
    # O shell visual e publico para poder pedir o token ao usuario. Todo
    # endpoint atual ou futuro fica protegido por padrao.
    if request.endpoint in _ROTAS_PUBLICAS:
        return None

    ip = request.remote_addr or "desconhecido"
    janela = _valor_limite("EYLE_RATE_LIMIT_WINDOW_SECONDS", "window_seconds", 60)
    limite = _valor_limite("EYLE_RATE_LIMIT_REQUESTS", "requests", 180)
    permitido, espera = _limitador.permitir(("api", ip), limite, janela)
    if not permitido:
        return _resposta_rate_limit(espera)

    recebido = _token_recebido()
    esperado = app.config.get("EYLE_API_TOKEN")
    if esperado is None:
        esperado = obter_api_token()
    valido = bool(recebido) and secrets.compare_digest(str(recebido), str(esperado))
    if valido:
        return None

    limite_falhas = _valor_limite(
        "EYLE_RATE_LIMIT_AUTH_FAILURES", "auth_failures", 10,
    )
    permitido, espera = _limitador.permitir(("auth", ip), limite_falhas, janela)
    if not permitido:
        return _resposta_rate_limit(espera)

    resposta = jsonify({
        "status": "erro",
        "error_code": "UNAUTHORIZED",
        "motivo": "token de API ausente ou invalido",
    })
    resposta.status_code = 401
    resposta.headers["WWW-Authenticate"] = "Bearer"
    return resposta


def _projeto_publico(projeto):
    if not isinstance(projeto, dict):
        return None
    return {
        chave: projeto[chave]
        for chave in _CHAVES_PUBLICAS_PROJETO
        if chave in projeto
    }


def _redigir_caminhos_internos(valor, caminhos):
    """Remove caminhos conhecidos sem apagar o diagnostico da fila."""
    caminhos = tuple(
        caminho for caminho in caminhos
        if isinstance(caminho, str) and caminho
    )
    if isinstance(valor, dict):
        return {
            chave: _redigir_caminhos_internos(item, caminhos)
            for chave, item in valor.items()
        }
    if isinstance(valor, list):
        return [_redigir_caminhos_internos(item, caminhos) for item in valor]
    if isinstance(valor, str):
        for caminho in caminhos:
            valor = valor.replace(caminho, "<caminho_oculto>")
        return valor
    return valor


@app.route("/", methods=["GET"])
def painel():
    # so entrega o HTML/CSS/JS estatico -- o painel busca os dados dele
    # mesmo via fetch() para /conversa e /status, igual qualquer outro
    # cliente. Nenhum dado de memoria/LLM passa pelo template.
    return render_template("index.html")


@app.route("/enviar", methods=["POST"])
def enviar():
    dados = request.get_json(silent=True) or {}
    texto = (dados.get("texto") or "").strip()
    if not texto:
        return jsonify({"status": "erro", "motivo": "campo 'texto' vazio"}), 400

    # Registra e captura o historico no mesmo lock. Se outra mensagem entrar
    # enquanto esta espera/processa, ela nao contamina o contexto deste job.
    mensagem_id, historico_snapshot = eyle_engine.registrar_mensagem_com_snapshot(
        "user", texto,
    )
    job_id = queue.adicionar({
        "tipo": "pergunta",
        "texto": texto,
        "mensagem_id": mensagem_id,
        "historico_snapshot": historico_snapshot,
    })

    return jsonify({"status": "ok", "job_id": job_id, "mensagem_id": mensagem_id})


@app.route("/conversa", methods=["GET"])
def conversa():
    return jsonify(eyle_engine.carregar_conversa())


@app.route("/mensagem/<int:mensagem_id>", methods=["DELETE"])
def apagar_mensagem(mensagem_id):
    # assim como /enviar, so entra na fila -- quem remove de verdade e o
    # Engine (via worker), nunca o Flask direto. O navegador confirma
    # olhando GET /conversa de novo em seguida.
    job_id = queue.adicionar({"tipo": "remover", "mensagem_id": mensagem_id})
    return jsonify({"status": "ok", "job_id": job_id})


@app.route("/status", methods=["GET"])
def status():
    projeto = eyle_engine.carregar_projeto()
    caminho_projeto = projeto.get("caminho_origem") if isinstance(projeto, dict) else None
    caminhos_internos = (caminho_projeto, BASE_DIR)
    config = carregar_config_validada(CONFIG_PATH)
    worker_cfg = config.get("worker", {})
    telemetry_cfg = config.get("telemetry", {})
    fila = _redigir_caminhos_internos(
        queue.estatisticas(
            stale_after_seconds=worker_cfg.get("stale_worker_seconds", 30),
            blocked_after_seconds=worker_cfg.get("head_of_line_blocked_seconds", 60),
        ),
        caminhos_internos,
    )
    projeto_publico = _redigir_caminhos_internos(
        _projeto_publico(projeto), caminhos_internos,
    )
    metricas = (
        telemetry.summary(telemetry_cfg.get("window_seconds", 3600))
        if telemetry_cfg.get("enabled", True) else {"enabled": False}
    )
    return jsonify({
        "projeto": projeto_publico,
        "eventos_na_fila": queue.tamanho(),
        "fila": fila,
        "metricas": metricas,
        "avisos_config": config.get("_config_warnings", []),
    })


@app.route("/health", methods=["GET"])
def health():
    config = carregar_config_validada(CONFIG_PATH)
    worker_cfg = config.get("worker", {})
    fila = queue.estatisticas(
        stale_after_seconds=worker_cfg.get("stale_worker_seconds", 30),
        blocked_after_seconds=worker_cfg.get("head_of_line_blocked_seconds", 60),
    )
    healthy = fila.get("live_workers", 0) > 0 and not fila.get("head_of_line_blocked")
    code = 200 if healthy else 503
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "live_workers": fila.get("live_workers", 0),
        "head_of_line_blocked": fila.get("head_of_line_blocked", False),
        "oldest_pending_seconds": fila.get("oldest_pending_seconds"),
        "oldest_processing_seconds": fila.get("oldest_processing_seconds"),
    }), code


@app.route("/jobs/<int:job_id>", methods=["GET"])
def job(job_id):
    registro = queue.obter(job_id)
    if registro is None:
        return jsonify({
            "status": "erro", "error_code": "JOB_NOT_FOUND",
            "motivo": "tarefa nao encontrada",
        }), 404
    projeto = eyle_engine.carregar_projeto()
    caminho_projeto = projeto.get("caminho_origem") if isinstance(projeto, dict) else None
    publico = {
        chave: registro.get(chave)
        for chave in _CHAVES_PUBLICAS_JOB
        if chave in registro
    }
    return jsonify(_redigir_caminhos_internos(publico, (caminho_projeto, BASE_DIR)))


if __name__ == "__main__":
    # Mantem o atalho direto funcional e com a mesma orientacao de `main.py serve`.
    from engine.worker import iniciar_em_thread

    carregar_config_validada(CONFIG_PATH)
    token_api = obter_api_token()
    print("[web] Iniciando Worker permanente...")
    iniciar_em_thread()
    print(f"[web] Token da API: {token_api}")
    print(f"[web] Origem do token: {origem_api_token()}")
    print("[web] Painel: http://127.0.0.1:5000/")
    app.run(host="127.0.0.1", port=5000, debug=False)
