#!/usr/bin/env python3
"""Normalizacao unica de respostas vindas de backends LLM.

A fronteira HTTP pode devolver texto puro, envelopes OpenAI/Ollama, chunks SSE,
listas de blocos, ``content`` ou ``reasoning_content``. Este modulo converte
essas variacoes em um contrato pequeno sem fingir conteudo quando nada chegou.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class NormalizedModelResponse:
    content: str = ""
    reasoning_content: str = ""
    raw_text: str = ""
    streaming: bool = False
    partial_json: bool = False
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    response_id: str | None = None

    def usable_text(self, *, allow_reasoning: bool = False) -> str:
        # Preserve espacos dos chunks: em streaming, ``"Oi"`` + ``" mundo"``
        # precisa continuar ``"Oi mundo"``. O strip serve apenas para testar
        # vazio; a borda final decide quando remover espacos externos.
        if self.content.strip():
            return self.content
        if allow_reasoning and self.reasoning_content.strip():
            return self.reasoning_content
        return ""


def _join_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _last_not_none(items, field):
    for item in reversed(items):
        value = getattr(item, field, None)
        if value is not None:
            return value
    return None


def _merge(items: Iterable[NormalizedModelResponse], *, streaming=False) -> NormalizedModelResponse:
    items = list(items)
    return NormalizedModelResponse(
        content="".join(item.content for item in items),
        reasoning_content="".join(item.reasoning_content for item in items),
        raw_text="".join(item.raw_text for item in items),
        streaming=bool(streaming or any(item.streaming for item in items)),
        partial_json=any(item.partial_json for item in items),
        finish_reason=_last_not_none(items, "finish_reason"),
        prompt_tokens=_last_not_none(items, "prompt_tokens"),
        completion_tokens=_last_not_none(items, "completion_tokens"),
        reasoning_tokens=_last_not_none(items, "reasoning_tokens"),
        model=_last_not_none(items, "model"),
        response_id=_last_not_none(items, "response_id"),
    )


def _decode_partial_string(raw: str, field: str) -> str:
    """Recupera uma string mesmo quando o JSON terminou no meio do campo."""
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"', raw)
    if not match:
        return ""
    tail = raw[match.end():]
    escaped = False
    chars = []
    for char in tail:
        if char == '"' and not escaped:
            break
        chars.append(char)
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    fragment = "".join(chars)
    try:
        return json.loads('"' + fragment + '"')
    except Exception:
        return fragment.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')


def _usage_metadata(payload: dict[str, Any]):
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    reasoning = usage.get("reasoning_tokens")
    details = usage.get("completion_tokens_details")
    if reasoning is None and isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
    def as_int(value):
        return int(value) if isinstance(value, (int, float)) else None
    return as_int(prompt), as_int(completion), as_int(reasoning)


def _from_mapping(payload: dict[str, Any], *, streaming=False) -> NormalizedModelResponse:
    # Envelopes OpenAI.
    choices = payload.get("choices")
    if isinstance(choices, list):
        parts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            block = choice.get("delta") or choice.get("message") or choice
            nested = _from_mapping(block, streaming=streaming or "delta" in choice)
            parts.append(NormalizedModelResponse(
                content=nested.content,
                reasoning_content=nested.reasoning_content,
                raw_text=nested.raw_text,
                streaming=nested.streaming,
                partial_json=nested.partial_json,
                finish_reason=choice.get("finish_reason") or nested.finish_reason,
                prompt_tokens=nested.prompt_tokens,
                completion_tokens=nested.completion_tokens,
                reasoning_tokens=nested.reasoning_tokens,
                model=nested.model,
                response_id=nested.response_id,
            ))
        merged = _merge(parts, streaming=streaming)
        prompt_tokens, completion_tokens, reasoning_tokens = _usage_metadata(payload)
        return NormalizedModelResponse(
            content=merged.content,
            reasoning_content=merged.reasoning_content,
            raw_text=merged.raw_text,
            streaming=merged.streaming,
            partial_json=merged.partial_json,
            finish_reason=merged.finish_reason,
            prompt_tokens=prompt_tokens if prompt_tokens is not None else merged.prompt_tokens,
            completion_tokens=completion_tokens if completion_tokens is not None else merged.completion_tokens,
            reasoning_tokens=reasoning_tokens if reasoning_tokens is not None else merged.reasoning_tokens,
            model=str(payload.get("model")) if payload.get("model") is not None else merged.model,
            response_id=str(payload.get("id")) if payload.get("id") is not None else merged.response_id,
        )

    # Envelope Ollama e blocos OpenAI ja internos.
    message = payload.get("message")
    if isinstance(message, dict):
        nested = _from_mapping(message, streaming=streaming)
        if nested.content or nested.reasoning_content:
            prompt_tokens, completion_tokens, reasoning_tokens = _usage_metadata(payload)
            if completion_tokens is None and isinstance(payload.get("eval_count"), (int, float)):
                completion_tokens = int(payload.get("eval_count"))
            return NormalizedModelResponse(
                content=nested.content,
                reasoning_content=nested.reasoning_content,
                raw_text=nested.raw_text,
                streaming=nested.streaming,
                partial_json=nested.partial_json,
                finish_reason=payload.get("done_reason") or payload.get("finish_reason") or nested.finish_reason,
                prompt_tokens=prompt_tokens if prompt_tokens is not None else nested.prompt_tokens,
                completion_tokens=completion_tokens if completion_tokens is not None else nested.completion_tokens,
                reasoning_tokens=reasoning_tokens if reasoning_tokens is not None else nested.reasoning_tokens,
                model=str(payload.get("model")) if payload.get("model") is not None else nested.model,
                response_id=str(payload.get("id")) if payload.get("id") is not None else nested.response_id,
            )

    content = _join_content(payload.get("content"))
    reasoning = _join_content(
        payload.get("reasoning_content", payload.get("reasoning"))
    )

    # Alguns servidores usam response/text/output_text fora de message.
    if not content:
        for key in ("response", "text", "output_text", "generated_text"):
            content = _join_content(payload.get(key))
            if content:
                break

    # Responses API / blocos de output.
    output = payload.get("output")
    if not content and isinstance(output, list):
        nested_parts = []
        for item in output:
            if isinstance(item, dict):
                nested_parts.append(_from_mapping(item, streaming=streaming))
        nested = _merge(nested_parts, streaming=streaming)
        content = nested.content
        reasoning = reasoning or nested.reasoning_content

    prompt_tokens, completion_tokens, reasoning_tokens = _usage_metadata(payload)
    return NormalizedModelResponse(
        content=content,
        reasoning_content=reasoning,
        raw_text="",
        streaming=streaming,
        partial_json=False,
        finish_reason=payload.get("finish_reason"),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        response_id=str(payload.get("id")) if payload.get("id") is not None else None,
    )


def normalize_model_response(raw: Any, *, streaming: bool = False) -> NormalizedModelResponse:
    """Normaliza string, bytes, dict, lista ou sequencia SSE/chunked.

    Texto JSON que representa a decisao do agente continua texto; apenas
    envelopes conhecidos de servidor sao desembrulhados.
    """
    if isinstance(raw, NormalizedModelResponse):
        return raw
    if raw is None:
        return NormalizedModelResponse(streaming=streaming)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, dict):
        return _from_mapping(raw, streaming=streaming)
    if isinstance(raw, list):
        return _merge(
            [normalize_model_response(item, streaming=streaming) for item in raw],
            streaming=streaming,
        )
    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip()
    if not text:
        return NormalizedModelResponse(raw_text=raw, streaming=streaming)

    # Fluxos SSE completos ou uma colecao de JSON lines.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(line.startswith("data:") for line in lines):
        parts = []
        partial = False
        for line in lines:
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                partial = True
                payload = line
            parts.append(normalize_model_response(payload, streaming=True))
        merged = _merge(parts, streaming=True)
        return NormalizedModelResponse(
            content=merged.content,
            reasoning_content=merged.reasoning_content,
            raw_text=raw,
            streaming=True,
            partial_json=partial or merged.partial_json,
        )

    # Um envelope JSON completo do servidor deve ser desembrulhado. Uma decisao
    # JSON do agente nao possui estas chaves e permanece como texto puro.
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and any(
        key in payload for key in (
            "choices", "message", "response", "generated_text", "output_text"
        )
    ):
        normalized = _from_mapping(payload, streaming=streaming)
        return NormalizedModelResponse(
            content=normalized.content,
            reasoning_content=normalized.reasoning_content,
            raw_text=raw,
            streaming=streaming or normalized.streaming,
            partial_json=False,
            finish_reason=normalized.finish_reason,
            prompt_tokens=normalized.prompt_tokens,
            completion_tokens=normalized.completion_tokens,
            reasoning_tokens=normalized.reasoning_tokens,
            model=normalized.model,
            response_id=normalized.response_id,
        )

    # JSON parcial de envelope: extrai somente campos reconhecidos. Se nao for
    # envelope, conserva como texto puro para o parser do agente tentar reparar.
    looks_like_envelope = bool(re.search(
        r'"(?:choices|message|content|reasoning_content|response|generated_text)"\s*:',
        text,
    ))
    if payload is None and looks_like_envelope:
        content = _decode_partial_string(text, "content")
        if not content:
            content = _decode_partial_string(text, "response")
        reasoning = _decode_partial_string(text, "reasoning_content")
        return NormalizedModelResponse(
            content=content,
            reasoning_content=reasoning,
            raw_text=raw,
            streaming=streaming,
            partial_json=True,
        )

    return NormalizedModelResponse(
        content=raw,
        reasoning_content="",
        raw_text=raw,
        streaming=streaming,
        partial_json=payload is None and text.startswith("{"),
    )
