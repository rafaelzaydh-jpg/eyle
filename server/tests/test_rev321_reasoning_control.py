import asyncio
from starlette.requests import Request
from server import server


def _request(path: str = "/health") -> Request:
    return Request({"type":"http","method":"GET","path":path,"headers":[],"client":("127.0.0.1",1),"server":("127.0.0.1",8080),"scheme":"http"})


def test_reasoning_off_is_direct_deepseek_translation():
    body, _, _ = server.prepare_upstream({"messages": [], "reasoning_mode": "off"})
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_mode" not in body


def test_reasoning_on_is_direct_deepseek_translation():
    body, _, _ = server.prepare_upstream({"messages": [], "reasoning_mode": "on"})
    assert body["thinking"] == {"type": "enabled"}


def test_provider_default_is_pass_through_behavior():
    body, _, _ = server.prepare_upstream({"messages": [], "reasoning_mode": "provider_default"})
    assert "thinking" not in body


def test_health_exposes_transport_identity_not_semantic_capabilities():
    body = asyncio.run(server.health(_request()))
    assert body["status"] == "ok"
    assert body["adapter_protocol"] == server.ADAPTER_TRANSPORT_PROTOCOL
    assert "capabilities" not in body
