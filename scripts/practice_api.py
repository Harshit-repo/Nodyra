"""Deterministic loopback-only practice API for Nodyra academy exercises."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

CUSTOMERS = [
    {"id": 1, "name": "Ada Lovelace", "region": "west", "active": True},
    {"id": 2, "name": "Grace Hopper", "region": "east", "active": True},
    {"id": 3, "name": "Katherine Johnson", "region": "west", "active": False},
]


class PracticeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    attempts: ClassVar[dict[str, int]] = {}

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        request = urlsplit(self.path)
        if request.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if request.path == "/customers":
            region = parse_qs(request.query).get("region", [""])[0]
            values = [item for item in CUSTOMERS if not region or item["region"] == region]
            self._json(HTTPStatus.OK, {"items": values, "count": len(values)})
            return
        if request.path == "/unstable":
            key = parse_qs(request.query).get("key", ["default"])[0][:80]
            attempt = self.attempts.get(key, 0) + 1
            self.attempts[key] = attempt
            if attempt <= 2:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"attempt": attempt, "retryable": True})
            else:
                self._json(HTTPStatus.OK, {"attempt": attempt, "result": "recovered"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if urlsplit(self.path).path != "/events":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return
        if size < 1 or size > 64 * 1024:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return
        self._json(HTTPStatus.ACCEPTED, {"accepted": True, "event": payload})

    def log_message(self, format: str, *args: object) -> None:
        print(f"practice-api {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("practice API is intentionally loopback-only")
    server = ThreadingHTTPServer((args.host, args.port), PracticeHandler)
    print(f"Practice API listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
