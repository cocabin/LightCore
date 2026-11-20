"""Sahte Hue Bridge (REST API v1): eşleştirme, lamba listesi, renk komutları."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LIGHTS = {
    "1": {"name": "Lamba 1", "productname": "Hue color lamp", "state": {"on": True, "xy": [0.3, 0.3]}},
    "2": {"name": "Lamba 2", "productname": "Hue color lamp", "state": {"on": True, "xy": [0.3, 0.3]}},
    "3": {"name": "Beyaz ampul", "productname": "Hue white lamp", "state": {"on": True, "bri": 254}},
}


class MockHue:
    def __init__(self, button_pressed=True):
        self.button_pressed = button_pressed
        self.states = []  # (lamba, gövde)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # kalıcı bağlantı (istemci gibi)

            def log_message(self, *_args):
                pass

            def _reply(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(n) or b"null")

            def do_GET(self):
                if self.path == "/api/config":
                    self._reply({"name": "Sahte köprü", "bridgeid": "001788FFFE000000"})
                elif self.path == "/api/kullanici/lights":
                    self._reply(LIGHTS)
                else:
                    self._reply([{"error": {"type": 1, "description": "unauthorized user"}}])

            def do_POST(self):
                self._body()
                if bridge.button_pressed:
                    self._reply([{"success": {"username": "kullanici"}}])
                else:
                    self._reply([{"error": {"type": 101, "description": "link button not pressed"}}])

            def do_PUT(self):
                body = self._body()
                light = self.path.split("/")[4]
                bridge.states.append((light, body))
                self._reply([{"success": {f"/lights/{light}/state/on": True}}])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
