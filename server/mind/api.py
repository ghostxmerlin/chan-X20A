"""Read-only HTTP API of mind, on the server's loopback only (the Mac reaches it over ssh).

GET /state · /timeline?from=&to= (≤ 24 h) · /minute?t=&version=   — all need "Authorization: Bearer <token>".
Sentence text only leaves through this API; nothing is logged.
"""
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .store import LANE

MAX_RANGE_MS = 24 * 3600_000


class BadRequest(Exception):
    pass


def _int(q, name, required=True):
    values = q.get(name)
    if not values:
        if required:
            raise BadRequest(f"{name} required")
        return None
    try:
        return int(values[0])
    except ValueError:
        raise BadRequest(f"{name} must be an integer") from None


def make_server(store, token, host="127.0.0.1", port=52740):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            supplied = self.headers.get("Authorization", "")
            supplied = supplied[7:] if supplied.startswith("Bearer ") else ""
            return bool(token) and hmac.compare_digest(supplied.encode(), token.encode())

        def do_POST(self):
            self._send(405, {"error": "read only"})

        do_PUT = do_DELETE = do_PATCH = do_POST

        def do_GET(self):
            if not self._authorized():
                self._send(401, {"error": "bad token"})
                return
            url = urlsplit(self.path)
            q = parse_qs(url.query)
            try:
                if url.path == "/state":
                    self._send(200, {**store.state(), "lane": LANE, "now_ms": int(time.time() * 1000)})
                elif url.path == "/timeline":
                    lo, hi = _int(q, "from"), _int(q, "to")
                    if hi <= lo or hi - lo > MAX_RANGE_MS:
                        raise BadRequest("range must be positive and at most 24 h")
                    self._send(200, store.timeline(LANE, lo, hi))
                elif url.path == "/minute":
                    t, version = _int(q, "t"), _int(q, "version", required=False)
                    doc = store.get(LANE, t, version) if version is not None else store.latest(LANE, t)
                    if not doc:
                        self._send(404, {"error": "no such minute"})
                        return
                    self._send(200, {**doc, "versions": store.versions(LANE, t)})
                else:
                    self._send(404, {"error": "not found"})
            except BadRequest as e:
                self._send(400, {"error": str(e)})
            except Exception as e:               # noqa: BLE001 - database trouble shows as 503
                self._send(503, {"error": f"{type(e).__name__}"})

    return ThreadingHTTPServer((host, port), Handler)


def serve(store, token, host="127.0.0.1", port=52740):
    make_server(store, token, host, port).serve_forever()
