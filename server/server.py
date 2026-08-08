from __future__ import annotations

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("eyle-qwen-proxy")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    upstream_base_url: str
    upstream_api_key: str
    default_model: str
    model_override: str | None
    default_enable_thinking: bool
    structured_enable_thinking: bool
    force_enable_thinking: bool
    proxy_api_key: str | None
    request_timeout_seconds: float
    max_request_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8080")),
            upstream_base_url=os.getenv(
                "UPSTREAM_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ).rstrip("/"),
            upstream_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            default_model=os.getenv("DEFAULT_MODEL", "qwen3.8-max").strip(),
            model_override=os.getenv("MODEL_OVERRIDE", "").strip() or None,
            default_enable_thinking=env_bool("DEFAULT_ENABLE_THINKING", True),
            structured_enable_thinking=env_bool("STRUCTURED_ENABLE_THINKING", False),
            force_enable_thinking=env_bool("FORCE_ENABLE_THINKING", False),
            proxy_api_key=os.getenv("PROXY_API_KEY", "").strip() or None,
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "600")),
            max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
        )


settings = Settings.from_env()


def require_configuration() -> None:
    if not settings.upstream_api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY não configurada. Copie .env.example para .env e informe a chave."
        )
    if not settings.default_model and not settings.model_override:
        raise RuntimeError("Configure DEFAULT_MODEL ou MODEL_OVERRIDE.")


def validate_client_auth(request: Request) -> None:
    """Protege o proxy quando PROXY_API_KEY estiver configurada."""
    if not settings.proxy_api_key:
        return

    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    x_api_key = request.headers.get("x-api-key", "").strip()
    supplied = bearer or x_api_key

    if not supplied or not hmac.compare_digest(supplied, settings.proxy_api_key):
        raise HTTPException(status_code=401, detail="Chave do proxy inválida.")


def upstream_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.upstream_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "Eyle-Qwen-Proxy/1.1",
    }


def copied_headers(response: httpx.Response, *, streaming: bool) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in (
        "x-request-id",
        "x-dashscope-request-id",
        "request-id",
        "retry-after",
    ):
        value = response.headers.get(name)
        if value:
            headers[name] = value

    if streaming:
        headers["Cache-Control"] = "no-cache"
        headers["X-Accel-Buffering"] = "no"
    return headers


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_configuration()
    timeout = httpx.Timeout(
        connect=20.0,
        read=settings.request_timeout_seconds,
        write=60.0,
        pool=20.0,
    )
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    app.state.http = httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False)
    logger.info(
        "Proxy iniciado em %s:%s -> %s | modelo=%s",
        settings.host,
        settings.port,
        settings.upstream_base_url,
        settings.model_override or settings.default_model,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="Eyle Qwen Proxy",
    version="1.1.0",
    description="Proxy OpenAI-compatible para DashScope/Qwen.",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "Eyle Qwen Proxy",
        "status": "ok",
        "openai_base_url": f"http://{settings.host}:{settings.port}/v1",
        "model": settings.model_override or settings.default_model,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "upstream": settings.upstream_base_url,
        "model": settings.model_override or settings.default_model,
        "thinking_default": settings.default_enable_thinking,
        "thinking_structured": settings.structured_enable_thinking,
        "thinking_forced": settings.force_enable_thinking,
    }


@app.get("/v1/models")
@app.get("/models", include_in_schema=False)
async def models(request: Request) -> dict[str, Any]:
    validate_client_auth(request)
    model = settings.model_override or settings.default_model
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "alibaba-cloud",
            }
        ],
    }


def prepare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Prepare one transparent DashScope Chat Completions request.

    The proxy may select/override the model and define ``enable_thinking`` policy,
    but it never weakens or rewrites Eyle's structured-output contract.
    ``response_format`` is forwarded unchanged.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="O campo messages deve ser uma lista não vazia.")

    outgoing = dict(payload)
    outgoing["model"] = settings.model_override or outgoing.get("model") or settings.default_model

    # Rev1.1 removes legacy Eyle dialect translation.  The current Eyle sends
    # native Chat Completions fields; old compatibility fields fail explicitly
    # instead of being silently reinterpreted by the proxy.
    legacy = sorted({"chat_template_kwargs", "reasoning_effort"} & set(outgoing))
    if legacy:
        raise HTTPException(
            status_code=400,
            detail="Campos legado não suportados pelo proxy 1.1: " + ", ".join(legacy),
        )

    response_format = outgoing.get("response_format")
    structured = False
    if response_format is not None:
        if not isinstance(response_format, dict):
            raise HTTPException(status_code=400, detail="response_format deve ser um objeto.")
        tipo = str(response_format.get("type") or "").strip().lower()
        if tipo not in {"json_object", "json_schema"}:
            raise HTTPException(status_code=400, detail=f"response_format.type não suportado: {tipo or 'vazio'}.")
        if tipo == "json_schema":
            block = response_format.get("json_schema")
            if not isinstance(block, dict) or not isinstance(block.get("schema"), dict):
                raise HTTPException(status_code=400, detail="json_schema requer um objeto schema.")
        structured = True
        # Critical invariant: do not mutate response_format.  Provider
        # capability is negotiated and locally validated by Eyle.

    explicit_thinking = outgoing.get("enable_thinking")
    if settings.force_enable_thinking:
        outgoing["enable_thinking"] = True
    elif isinstance(explicit_thinking, bool):
        outgoing["enable_thinking"] = explicit_thinking
    elif structured:
        outgoing["enable_thinking"] = settings.structured_enable_thinking
    else:
        outgoing["enable_thinking"] = settings.default_enable_thinking

    return outgoing


async def read_json_body(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > settings.max_request_bytes:
        raise HTTPException(status_code=413, detail="Corpo da requisição excede o limite configurado.")
    try:
        parsed = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="O corpo deve ser um objeto JSON.")
    return parsed


@app.post("/v1/chat/completions")
@app.post("/chat/completions", include_in_schema=False)
async def chat_completions(request: Request) -> Response:
    validate_client_auth(request)
    payload = prepare_payload(await read_json_body(request))
    stream = bool(payload.get("stream", False))
    request_id = str(uuid.uuid4())
    url = f"{settings.upstream_base_url}/chat/completions"

    response_format = payload.get("response_format") if isinstance(payload.get("response_format"), dict) else {}
    format_type = response_format.get("type")
    schema_block = response_format.get("json_schema") if isinstance(response_format.get("json_schema"), dict) else {}
    logger.info(
        "request=%s model=%s stream=%s thinking=%s messages=%s structured=%s schema_name=%s strict=%s",
        request_id,
        payload.get("model"),
        stream,
        payload.get("enable_thinking"),
        len(payload.get("messages", [])),
        format_type or "none",
        schema_block.get("name"),
        schema_block.get("strict"),
    )

    client: httpx.AsyncClient = request.app.state.http

    try:
        if stream:
            upstream_request = client.build_request(
                "POST",
                url,
                headers=upstream_headers(),
                json=payload,
            )
            upstream = await client.send(upstream_request, stream=True)

            if upstream.status_code >= 400:
                error_body = await upstream.aread()
                status_code = upstream.status_code
                media_type = upstream.headers.get("content-type", "application/json")
                headers = copied_headers(upstream, streaming=False)
                await upstream.aclose()
                logger.warning("request=%s upstream_status=%s", request_id, status_code)
                return Response(
                    content=error_body,
                    status_code=status_code,
                    media_type=media_type,
                    headers=headers,
                )

            media_type = upstream.headers.get("content-type", "text/event-stream")
            return StreamingResponse(
                upstream.aiter_raw(),
                status_code=upstream.status_code,
                media_type=media_type,
                headers=copied_headers(upstream, streaming=True),
                background=BackgroundTask(upstream.aclose),
            )

        upstream = await client.post(url, headers=upstream_headers(), json=payload)
        content_chars = reasoning_chars = 0
        finish_reason = None
        try:
            parsed = upstream.json()
            choices = parsed.get("choices") or [] if isinstance(parsed, dict) else []
            if choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
                message = choices[0].get("message") or {}
                content = message.get("content")
                reasoning = message.get("reasoning_content")
                content_chars = len(content) if isinstance(content, str) else 0
                reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
        except Exception:
            pass
        logger.info(
            "request=%s upstream_status=%s finish=%s content_chars=%s reasoning_chars=%s",
            request_id, upstream.status_code, finish_reason, content_chars, reasoning_chars,
        )
        if upstream.status_code < 400 and content_chars == 0 and reasoning_chars == 0:
            logger.warning("request=%s resposta upstream sem conteúdo utilizável", request_id)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers=copied_headers(upstream, streaming=False),
        )

    except httpx.TimeoutException:
        logger.exception("request=%s timeout no upstream", request_id)
        return JSONResponse(
            status_code=504,
            content={"error": {"message": "Timeout ao consultar o Qwen.", "type": "upstream_timeout"}},
        )
    except httpx.HTTPError as exc:
        logger.exception("request=%s falha de conexão: %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": "Falha ao conectar ao serviço do Qwen.",
                    "type": "upstream_connection_error",
                }
            },
        )


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
