import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import spike_readonly


class _Recorder(BaseHTTPRequestHandler):
    seen: list[str] = []

    def _any(self) -> None:
        _Recorder.seen.append(self.command)
        body = json.dumps({"items": [{"host": "10.1.2.3", "name": "x"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _any

    def log_message(self, *args: object) -> None:
        pass


def _serve() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_spike_uses_only_get(tmp_path):
    _Recorder.seen.clear()
    srv, url = _serve()
    try:
        spike_readonly.run(url, "token", verify_ssl=True, out_dir=tmp_path)
    finally:
        srv.shutdown()
    assert _Recorder.seen, "es wurde keine Anfrage gestellt"
    assert set(_Recorder.seen) == {"GET"}


def test_spike_masks_ip_addresses_in_saved_files(tmp_path):
    srv, url = _serve()
    try:
        spike_readonly.run(url, "token", verify_ssl=True, out_dir=tmp_path)
    finally:
        srv.shutdown()
    saved = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "10.1.2.3" not in saved
    assert "token" not in saved
