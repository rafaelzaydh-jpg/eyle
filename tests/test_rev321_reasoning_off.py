import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import executar as llm_mod
from tests.canonical import base_config
from eyle.runtime.config import validar_config
from tests.canonical import standard_registry


class _CaptureHandler(BaseHTTPRequestHandler):
    body = None
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0") or 0)
        type(self).body = json.loads(self.rfile.read(size))
        response = json.dumps({"choices":[{"message":{"content":"ok"}}], "usage":{"prompt_tokens":1,"completion_tokens":1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
    def log_message(self, format, *args):
        return


def test_rev321_core_sends_reasoning_off_to_adapter_by_default():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert llm_mod._chamar_openai_compatible(
            f"http://127.0.0.1:{server.server_port}", "deepseek-v4-flash", "s", "u", 0.2,
            timeout=1.0, read_timeout=1.0,
        ) == "ok"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=1.0)
    assert _CaptureHandler.body["reasoning_mode"] == "off"


def test_rev321_config_defaults_reasoning_off_and_allows_provider_default():
    cfg = base_config()
    cfg["llm"].pop("reasoning_mode", None)
    out = validar_config(cfg, standard_registry())
    assert (out.get("llm") or {}).get("reasoning_mode") is None
    cfg2 = base_config()
    cfg2["llm"]["reasoning_mode"] = "provider_default"
    validar_config(cfg2, standard_registry())
