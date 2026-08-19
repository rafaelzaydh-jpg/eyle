import io
import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import llm.executar as llm_mod


@pytest.fixture(autouse=True)
def _adapter_handshake_already_verified(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "_ensure_adapter_ready",
        lambda config: {"adapter_protocol": llm_mod.ADAPTER_TRANSPORT_PROTOCOL},
    )


class _FakeResponse:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes

    def close(self):
        pass


def _config(**overrides):
    llm = {
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
        "temperature": 0.2,
        "connect_timeout_seconds": 5,
        "read_timeout_seconds": None,
        "retry_max_attempts": 1,
        "max_concurrent_requests": 1,
    }
    llm.update(overrides)
    return {"llm": llm}


def _capture(monkeypatch, response):
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((req.full_url, payload))
        return _FakeResponse(response)

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    return calls


def _agent_json(answer="ok"):
    return json.dumps({"type":"concluir","response":answer,"memory_delta":[]})


def test_rev282_has_no_ollama_transport_or_local_model_fallback():
    assert not hasattr(llm_mod, "_chamar_ollama")
    source = Path(llm_mod.__file__).read_text(encoding="utf-8").lower()
    assert "localhost:11434" not in source
    assert "/api/chat" not in source


def test_rev282_adapter_request_has_no_arbitrary_per_call_max_tokens(monkeypatch):
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    assert llm_mod._chamar_llm("s", "u", _config()) == "ok"
    assert "max_tokens" not in calls[0][1]


def test_structured_openai_uses_current_wire_schema(monkeypatch):
    body = _agent_json()
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": body}}]})
    parsed = llm_mod._chamar_llm("s", "u", _config(), perfil="navigation")
    assert parsed["type"] == "concluir"
    assert parsed["response"] == "ok"
    fmt = calls[0][1]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    wire = fmt["json_schema"]["schema"]
    assert "oneOf" in wire
    assert all(branch.get("additionalProperties") is False for branch in wire["oneOf"])


def test_structured_json_schema_is_required_and_fails_closed(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":"json schema unsupported"}')
        )

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(retry_max_attempts=1), perfil="navigation")
    assert exc.value.error_code == "LLM_STRUCTURED_OUTPUT_UNAVAILABLE"
    assert len(calls) == 1
    assert calls[0]["response_format"]["type"] == "json_schema"


def test_reasoning_content_is_never_executable(monkeypatch):
    _capture(monkeypatch, {
        "choices": [{"message": {"content": "", "reasoning_content": _agent_json()}}]
    })
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="navigation")
    assert exc.value.error_code == "STRUCTURED_RESPONSE_INVALID:navigation:STRUCTURED_EMPTY"


def test_explicit_openai_model_is_not_silently_replaced(monkeypatch):
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    assert llm_mod._chamar_llm("s", "u", _config(model="explicit-model")) == "ok"
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/chat/completions")
    assert calls[0][1]["model"] == "explicit-model"


def test_rev282_auto_model_is_forwarded_to_adapter_without_discovery(monkeypatch):
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    assert llm_mod._chamar_llm("s", "u", _config(model="auto")) == "ok"
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/chat/completions")
    assert calls[0][1]["model"] == "auto"


def test_rev282_empty_model_fails_before_adapter_call(monkeypatch):
    called = {"n": 0}
    def fake_urlopen(req, timeout=None):
        called["n"] += 1
        raise AssertionError("HTTP must not start")
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(model=""))
    assert exc.value.error_code == "MODEL_REQUIRED"
    assert called["n"] == 0


def test_invalid_openai_envelope_fails_at_transport_boundary(monkeypatch):
    _capture(monkeypatch, {"text": "not chat completions"})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config())
    assert exc.value.error_code == "BACKEND_RESPONSE_INVALID"


def test_core_does_not_recover_provider_markdown_or_prose(monkeypatch):
    content = '```json\n' + _agent_json() + '\n```'
    _capture(monkeypatch, {"choices": [{"message": {"content": content}}]})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="navigation")
    assert exc.value.error_code.startswith("STRUCTURED_RESPONSE_INVALID:navigation:")


def test_http_error_keeps_backend_detail(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"modelo inexistente"}')
        )

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config())
    assert "modelo inexistente" in str(exc.value)


class _DelayedOpenAIHandler(BaseHTTPRequestHandler):
    response_delay = 0.25

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(size)
        time.sleep(self.response_delay)
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


def test_connect_timeout_does_not_replace_read_timeout():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DelayedOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    start = time.monotonic()
    try:
        response = llm_mod._chamar_openai_compatible(
            f"http://127.0.0.1:{server.server_port}", "modelo", "s", "u", 0.2,
            timeout=0.05, read_timeout=1.0,
        )
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=1.0)
    assert response == "ok"
    assert time.monotonic() - start >= _DelayedOpenAIHandler.response_delay






def test_transport_timeout_is_recorded_as_started_physical_attempt_not_preflight(monkeypatch):
    import socket
    from eyle.runtime.execution_context import ExecutionContext
    from eyle.runtime.history import build_execution_trace
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
                "connect_timeout_seconds": 1,
        "read_timeout_seconds": 1,
        "retry_max_attempts": 1,
        "retry_read_timeouts": False,
        "max_concurrent_requests": 1,
    })
    execution = ExecutionContext.from_config(cfg)

    def timeout_urlopen(req, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", timeout_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "READ_TIMEOUT"
    attempt = execution.llm_calls[-1]["attempts"][0]
    assert attempt["request_status"] == "read_timeout"
    assert attempt["error_code"] == "READ_TIMEOUT"
    trace = build_execution_trace({"llm_calls": execution.llm_calls})
    assert trace["llm_calls"][0]["request_status"] == "read_timeout"


def test_true_preflight_rejection_has_no_physical_attempt(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext
    from eyle.runtime.history import build_execution_trace
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
                "context_window_tokens": 8,
        "retry_max_attempts": 1,
        "max_concurrent_requests": 1,
    })
    execution = ExecutionContext.from_config(cfg)
    calls = []

    def should_not_send(req, timeout=None):
        calls.append(req)
        return _FakeResponse({"choices": [{"message": {"content": _agent_json()}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", should_not_send)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("system contract", "user request", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "PROMPT_CONTEXT_BUDGET_EXCEEDED"
    assert calls == []
    assert execution.llm_calls[-1]["attempts"] == []
    trace = build_execution_trace({"llm_calls": execution.llm_calls})
    assert trace["llm_calls"][0]["request_status"] == "preflight_blocked"


def test_unstructured_job_streaming_uses_execution_context_not_config_dict(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
                "stream_responses": True,
        "retry_max_attempts": 1,
        "max_concurrent_requests": 1,
    })
    execution = ExecutionContext.from_config(cfg, execution_id="stream-test", source_job_id=77)
    observed = {}

    def fake_backend(base_url, model, prompt_sistema, prompt_usuario, temperature, timeout, **kwargs):
        observed["on_chunk"] = kwargs.get("on_chunk")
        on_request = kwargs.get("on_request")
        if on_request is not None:
            on_request()
        llm_mod._LLM_RESPONSE_LOCAL.metadata = {
            "provider_model": model,
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "finish_reason": "stop",
            "streaming": bool(kwargs.get("on_chunk")),
        }
        return "ok"

    monkeypatch.setattr(llm_mod, "_chamar_openai_compatible", fake_backend)
    assert llm_mod._chamar_llm("s", "u", cfg, execution) == "ok"
    assert callable(observed["on_chunk"])


def test_transient_openai_http_error_retries_after_backend_translation(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable", {},
                io.BytesIO(b'{"error":"temporary overload"}'),
            )
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    cfg = _config(
        retry_max_attempts=2,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter_seconds=0,
        cooldown_seconds=0,
    )
    assert llm_mod._chamar_llm("s", "u", cfg) == "ok"
    assert calls["n"] == 2


def test_rev22_adapter_structured_502_is_not_retried_and_error_usage_is_accounted(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080", "model": "modelo-teste",
        "retry_max_attempts": 3, "retry_base_delay_seconds": 0, "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0, "cooldown_seconds": 0,
    })
    execution = ExecutionContext.from_config(cfg)
    calls = {"n": 0}
    body = json.dumps({
        "error": {"type": "structured_contract_unsatisfied", "message": "bad schema"},
        "usage": {"prompt_tokens": 5000, "completion_tokens": 120, "total_tokens": 5120},
    }).encode()

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "LLM_STRUCTURED_RESPONSE_UNSATISFIED"
    assert exc.value.transient is False
    assert calls["n"] == 1
    usage = execution.usage_view()
    assert usage["prompt_tokens_actual"] == 5000
    assert usage["completion_tokens_actual"] == 120
    attempt = execution.llm_calls[-1]["attempts"][0]
    assert attempt["provider_usage_from_error"] is True


def test_rev251_adapter_structured_failure_surfaces_repair_diagnostics(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080", "model": "modelo-teste",
        "retry_max_attempts": 3, "retry_base_delay_seconds": 0, "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0, "cooldown_seconds": 0,
    })
    execution = ExecutionContext.from_config(cfg)
    calls = {"n": 0}
    body = json.dumps({
        "error": {
            "type": "structured_contract_unsatisfied",
            "message": "bad memory operation",
            "validation_errors": [
                "$.memory.operations[0].scope: required property missing",
                "$.memory.operations[0].kind: required property missing",
            ],
            "repairs": 1,
            "upstream_attempts": 2,
        },
        "usage": {"prompt_tokens": 8000, "completion_tokens": 700, "total_tokens": 8700},
    }).encode()

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "LLM_STRUCTURED_RESPONSE_UNSATISFIED"
    assert "$.memory.operations[0].scope" in str(exc.value)
    assert calls["n"] == 1
    attempt = execution.llm_calls[-1]["attempts"][0]
    assert attempt["adapter_upstream_attempts"] == 2
    assert attempt["adapter_structured_repairs"] == 1
    assert attempt["adapter_validation_errors"][0].startswith("$.memory.operations[0].scope")
    assert attempt["prompt_tokens"] == 8000
    assert attempt["completion_tokens"] == 700


def test_rev22_adapter_upstream_timeout_http_504_is_not_blindly_retried(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext
    from tests.canonical import base_config

    cfg = base_config()
    cfg["llm"].update({
        "base_url": "http://localhost:8080", "model": "modelo-teste",
        "retry_max_attempts": 3, "retry_read_timeouts": False,
        "retry_base_delay_seconds": 0, "retry_max_delay_seconds": 0,
        "retry_jitter_seconds": 0, "cooldown_seconds": 0,
    })
    execution = ExecutionContext.from_config(cfg)
    calls = {"n": 0}
    body = json.dumps({"error": {"type": "upstream_timeout", "message": "Timeout no upstream."}}).encode()

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 504, "Gateway Timeout", {}, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "READ_TIMEOUT"
    assert exc.value.transient is False
    assert calls["n"] == 1


def test_structured_call_records_real_system_prompt_size(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext

    body = _agent_json()
    _capture(monkeypatch, {"choices": [{"message": {"content": body}}]})
    cfg = _config()
    execution = ExecutionContext.from_config(cfg)
    execution.begin_call(mode="ecc", turn=1, prompt={"characters": 2, "estimated_tokens": 1})

    parsed = llm_mod._chamar_llm("short system", "{}", cfg, execution=execution, perfil="navigation")
    assert parsed["type"] == "concluir"
    prompt = execution.latest_call()["prompt"]
    assert prompt["system_prompt_characters"] == len("short system")
    assert prompt["system_prompt_estimated_tokens"] > 0


def test_rev286_eyle_always_sends_wire_schema_and_adapter_owns_upstream_mode(monkeypatch):
    body=_agent_json()
    for legacy_override in ("json_object", "prompt_json"):
        calls=_capture(monkeypatch,{"choices":[{"message":{"content":body}}]})
        parsed=llm_mod._chamar_llm("s","u",_config(structured_output_mode=legacy_override),perfil="navigation")
        assert parsed["response"]=="ok"
        fmt=calls[-1][1]["response_format"]
        assert fmt["type"]=="json_schema"
        assert fmt["json_schema"]["name"]=="eyle_navigation_wire"
        assert fmt["json_schema"]["strict"] is True


def test_rev28_canonical_prompt_sends_stable_message_before_dynamic(monkeypatch):
    from llm.protocol import CanonicalPrompt
    body=_agent_json()
    calls=_capture(monkeypatch,{"choices":[{"message":{"content":body}}]})
    prompt=CanonicalPrompt(stable={"ecc_operations":{"x":1}},dynamic={"current_request":"hello","runtime_feedback":[]})
    parsed=llm_mod._chamar_llm("system",prompt,_config(),perfil="navigation")
    assert parsed["response"]=="ok"
    messages=calls[0][1]["messages"]
    assert [m["role"] for m in messages]==["system","user","user","user"]
    assert "ecc_operations" in messages[1]["content"]
    assert "runtime_feedback" in messages[2]["content"]
    assert messages[-1] == {"role": "user", "content": "hello"}


def test_rev281_success_adapter_headers_are_observable(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext

    class Response(_FakeResponse):
        def __init__(self, payload):
            super().__init__(payload)
            self.headers = {
                "X-Eyle-Adapter-Profile": "eyle-rev281-provider-neutral-v1",
                "X-Eyle-Structured-Upstream-Mode": "json_object",
                "X-Eyle-Structured-Configured-Mode": "json_object",
                "X-Eyle-Cache-Mode": "implicit",
                "X-Eyle-Schema-Enforcement": "adapter_valid",
                "X-Eyle-Upstream-Attempts": "1",
                "X-Eyle-Max-Upstream-Attempts": "2",
                "X-Eyle-Structured-Repairs": "0",
                "X-Eyle-Local-Normalized": "0",
            }

    body = _agent_json()
    payload = {
        "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "model": "modelo-real",
    }
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", lambda req, timeout=None: Response(payload))
    cfg = _config(retry_max_attempts=3)
    execution = ExecutionContext.from_config(cfg)
    parsed = llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert parsed["type"] == "concluir"
    attempt = execution.llm_calls[-1]["attempts"][0]
    assert attempt["adapter_upstream_attempts"] == 1
    assert attempt["adapter_structured_repairs"] == 0
    assert attempt["adapter_structured_upstream_mode"] == "json_object"
    assert attempt["adapter_cache_mode"] == "implicit"
    assert attempt["canonical_contract_mode"] == "wire_json+local_canonical"


def test_rev281_billed_adapter_transport_failure_is_not_retried(monkeypatch):
    from eyle.runtime.execution_context import ExecutionContext

    cfg = _config(
        retry_max_attempts=3,
        retry_base_delay_seconds=0,
        retry_max_delay_seconds=0,
        retry_jitter_seconds=0,
        cooldown_seconds=0,
    )
    execution = ExecutionContext.from_config(cfg)
    calls = {"n": 0}
    body = json.dumps({
        "error": {
            "type": "upstream_connection_error",
            "detail": "connection reset after repair started",
            "upstream_attempts": 2,
            "billing_may_have_occurred": True,
            "retry_cost_risk": True,
        },
        "usage": {"prompt_tokens": 4200, "completion_tokens": 180},
    }).encode()
    headers = {
        "X-Eyle-Upstream-Attempts": "2",
        "X-Eyle-Usage-Prompt-Tokens": "4200",
        "X-Eyle-Usage-Completion-Tokens": "180",
        "X-Eyle-Billing-May-Have-Occurred": "1",
        "X-Eyle-Retry-Cost-Risk": "1",
    }

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", headers, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, execution, perfil="navigation")
    assert exc.value.error_code == "TRANSPORT_ERROR"
    assert exc.value.transient is False
    assert calls["n"] == 1
    attempt = execution.llm_calls[-1]["attempts"][0]
    assert attempt["retry_cost_risk"] is True
    assert attempt["prompt_tokens"] == 4200
    assert attempt["completion_tokens"] == 180


def test_rev281_zero_usage_adapter_transport_failure_keeps_bounded_outer_retry(monkeypatch):
    cfg = _config(
        retry_max_attempts=3,
        retry_base_delay_seconds=0,
        retry_max_delay_seconds=0,
        retry_jitter_seconds=0,
        cooldown_seconds=0,
    )
    calls = {"n": 0}
    body = json.dumps({"error": {"type": "upstream_connection_error", "upstream_attempts": 1}, "usage": {}}).encode()

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {"X-Eyle-Upstream-Attempts": "1"}, io.BytesIO(body))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", cfg, perfil="navigation")
    assert exc.value.error_code == "TRANSPORT_ERROR"
    assert calls["n"] == 3


def test_rev375_diagnosticar_backend_uses_health_then_ready(monkeypatch):
    calls = []
    health = {
        "status": "ok",
        "adapter_protocol": llm_mod.ADAPTER_TRANSPORT_PROTOCOL,
        "adapter_profile": "simple",
        "adapter_version": "2.7.5-rev3.7.5",
        "model": "provider-model",
    }

    def fake_get(endpoint, timeout, *, protocol=False):
        calls.append((endpoint, protocol))
        if endpoint.endswith("/health"):
            return health, {}
        if endpoint.endswith("/ready"):
            return {"status": "ready_configured", "model": "provider-model"}, {}
        raise AssertionError(endpoint)

    monkeypatch.setattr(llm_mod, "_get_json", fake_get)
    result = llm_mod.diagnosticar_backend(_config())
    assert result["ok"] is True
    assert result["adapter_protocol"] == llm_mod.ADAPTER_TRANSPORT_PROTOCOL
    assert result["models"] == ["provider-model"]
    assert [item[0] for item in calls] == [
        "http://localhost:8080/health",
        "http://localhost:8080/ready",
    ]
    assert all(item[1] is True for item in calls)


def test_rev375_protocol_incompatibility_blocks_before_paid_generation(monkeypatch):
    monkeypatch.undo()  # remove autouse readiness stub for this test only
    llm_mod._ADAPTER_STATUS_CACHE.clear()
    calls = []
    bad = {"status": "ok", "adapter_protocol": "old-protocol"}

    def fake_get(endpoint, timeout, *, protocol=False):
        calls.append(endpoint)
        return bad, {}

    monkeypatch.setattr(llm_mod, "_get_json", fake_get)
    paid = {"n": 0}
    monkeypatch.setattr(llm_mod, "_chamar_openai_compatible", lambda *a, **k: paid.__setitem__("n", paid["n"] + 1))
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm_impl("s", "u", _config(), execution=None)
    assert exc.value.error_code == "ADAPTER_PROTOCOL_INCOMPATIBLE"
    assert paid["n"] == 0
    assert calls == ["http://localhost:8080/health"]




def test_rev3751_adapter_boundary_metadata_is_observable():
    meta = llm_mod._adapter_response_metadata({
        "X-Eyle-Structured-Contract-Characters": "1234",
        "X-Eyle-Repair-Context-Mode": "isolated",
        "X-Eyle-Structured-Repairs": "1",
    })
    assert meta["adapter_structured_contract_characters"] == 1234
    assert meta["adapter_repair_context_mode"] == "isolated"
    assert meta["adapter_structured_repairs"] == 1
