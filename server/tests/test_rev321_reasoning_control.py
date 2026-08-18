import json
import asyncio
from starlette.requests import Request
from server import server


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


def test_handshake_advertises_client_reasoning_control():
    request = Request({"type":"http","method":"GET","path":"/v1/eyle/handshake","headers":[],"client":("127.0.0.1",1),"server":("127.0.0.1",8080),"scheme":"http"})
    body = json.loads(asyncio.run(server.handshake(request)).body)
    assert body["capabilities"]["client_reasoning_control"] is True
