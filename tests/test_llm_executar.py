import io
import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import llm.executar as llm_mod


@pytest.fixture(autouse=True)
def _clear_model_discovery():
    llm_mod._MODELOS_OPENAI.clear()
    yield
    llm_mod._MODELOS_OPENAI.clear()


class _FakeResponse:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes


def _config(**overrides):
    llm = {
        "base_url": "http://localhost:8080",
        "model": "modelo-teste",
        "openai_compatible": True,
        "temperature": 0.2,
        "connect_timeout_seconds": 5,
        "read_timeout_seconds": 120,
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
    return json.dumps({
        "tool_calls": None, "patches": None,
        "needs_user": None, "final": {"answer": answer, "limitations": []},
        "investigation_updates": [],
    })


def test_ollama_uses_num_predict(monkeypatch):
    calls = _capture(monkeypatch, {"message": {"content": "ok"}})
    assert llm_mod._chamar_llm("s", "u", _config(openai_compatible=False, max_tokens=700)) == "ok"
    assert calls[0][1]["options"]["num_predict"] == 700


def test_openai_uses_max_tokens(monkeypatch):
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    assert llm_mod._chamar_llm("s", "u", _config(max_tokens=512)) == "ok"
    assert calls[0][1]["max_tokens"] == 512


def test_structured_openai_uses_strict_profile_schema(monkeypatch):
    body = _agent_json()
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": body}}]})
    parsed = llm_mod._chamar_llm("s", "u", _config(), perfil="agent")
    assert parsed["final"]["answer"] == "ok"
    fmt = calls[0][1]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert set(fmt["json_schema"]["schema"]["required"]) == {
        "tool_calls", "patches", "needs_user", "final", "investigation_updates",
    }


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
        llm_mod._chamar_llm("s", "u", _config(retry_max_attempts=1), perfil="agent")
    assert exc.value.error_code == "LLM_STRUCTURED_OUTPUT_UNAVAILABLE"
    assert len(calls) == 1
    assert calls[0]["response_format"]["type"] == "json_schema"


def test_reasoning_content_is_never_executable(monkeypatch):
    _capture(monkeypatch, {
        "choices": [{"message": {"content": "", "reasoning_content": _agent_json()}}]
    })
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="agent")
    assert exc.value.error_code == "EMPTY_MODEL_RESPONSE"


def test_explicit_openai_model_is_not_silently_replaced(monkeypatch):
    calls = _capture(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    assert llm_mod._chamar_llm("s", "u", _config(model="explicit-model")) == "ok"
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/chat/completions")
    assert calls[0][1]["model"] == "explicit-model"


def test_auto_model_uses_model_discovery(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/v1/models"):
            calls.append((req.full_url, None))
            return _FakeResponse({"data": [{"id": "loaded-model"}]})
        payload = json.loads(req.data.decode("utf-8"))
        calls.append((req.full_url, payload))
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert llm_mod._chamar_llm("s", "u", _config(model="auto")) == "ok"
    assert calls[0][0].endswith("/v1/models")
    assert calls[1][1]["model"] == "loaded-model"


def test_auto_model_fails_if_discovery_is_unavailable(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(model="auto"))
    assert exc.value.error_code == "MODEL_DISCOVERY_REQUIRED"


def test_invalid_openai_envelope_fails_at_transport_boundary(monkeypatch):
    _capture(monkeypatch, {"text": "not chat completions"})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config())
    assert exc.value.error_code == "BACKEND_RESPONSE_INVALID"


def test_structured_parser_rejects_markdown_or_multiple_json_objects(monkeypatch):
    content = '```json\n' + _agent_json() + '\n```\n' + _agent_json("second")
    _capture(monkeypatch, {"choices": [{"message": {"content": content}}]})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="agent")
    assert str(exc.value.error_code).startswith("STRUCTURED_RESPONSE_INVALID:agent:")


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


def test_claim_verifier_missing_claims_is_rejected_at_structured_boundary(monkeypatch):
    content = json.dumps({"semantic_gaps": []})
    _capture(monkeypatch, {"choices": [{"message": {"content": content}}]})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="claim_verifier")
    assert exc.value.error_code == "STRUCTURED_RESPONSE_INVALID:claim_verifier:CLAIM_REVIEW_MISSING_KEYS"


def test_claim_verifier_never_selects_an_earlier_partial_json_object(monkeypatch):
    complete = json.dumps({
        "material_satisfaction": {"status": "satisfied", "reason": "Fixture delivers the requested material result."},
        "answer_consistency": {"status": "consistent", "reason": "Fixture answer is internally consistent."},
        "claims": [], "semantic_gaps": [],
    })
    content = json.dumps({"semantic_gaps": []}) + "\n" + complete
    _capture(monkeypatch, {"choices": [{"message": {"content": content}}]})
    with pytest.raises(llm_mod.ErroLLM) as exc:
        llm_mod._chamar_llm("s", "u", _config(), perfil="claim_verifier")
    assert str(exc.value.error_code).startswith("STRUCTURED_RESPONSE_INVALID:claim_verifier:")
