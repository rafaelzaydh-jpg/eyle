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


def _merge(items: Iterable[NormalizedModelResponse], *, streaming=False) -> NormalizedModelResponse:
    items = list(items)
    return NormalizedModelResponse(
        content="".join(item.content for item in items),
        reasoning_content="".join(item.reasoning_content for item in items),
        raw_text="".join(item.raw_text for item in items),
        streaming=bool(streaming or any(item.streaming for item in items)),
        partial_json=any(item.partial_json for item in items),
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


def _from_mapping(payload: dict[str, Any], *, streaming=False) -> NormalizedModelResponse:
    # Envelopes OpenAI.
    choices = payload.get("choices")
    if isinstance(choices, list):
        parts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            block = choice.get("delta") or choice.get("message") or choice
            parts.append(_from_mapping(block, streaming=streaming or "delta" in choice))
        return _merge(parts, streaming=streaming)

    # Envelope Ollama e blocos OpenAI ja internos.
    message = payload.get("message")
    if isinstance(message, dict):
        nested = _from_mapping(message, streaming=streaming)
        if nested.content or nested.reasoning_content:
            return nested

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

    return NormalizedModelResponse(
        content=content,
        reasoning_content=reasoning,
        raw_text="",
        streaming=streaming,
        partial_json=False,
    )


def normalize_model_response(raw: Any, *, streaming: bool = False) -> NormalizedModelResponse:
    """Normaliza string, bytes, dict, lista ou sequencia SSE/chunked.

    Texto JSON que representa a decisao do agente continua texto; apenas
    envelopes conhecidos de servidor sao desembrulhados.
    """
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
