"""Local API. No Cognito, no AWS, and by default no calls to regulations.gov.

    python -m api.local_server

The React dev server proxies /api here. Set REGS_FIXTURE=0 and REGS_API_KEY
to use the real API instead of the built-in sample document.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from api.fixture import FixtureClient
from api.handler import route
from api.repo import DiskRepo
from regs_pull.preview import preview_document

ROOT = Path(os.environ.get("REGS_LOCAL_DIR", "var/local"))
REPO = DiskRepo(ROOT)


def _preview(document_id: str) -> dict:
    if os.environ.get("REGS_FIXTURE", "1") != "0":
        return preview_document(FixtureClient(), document_id)
    from regs_pull.engine import DEFAULT_BASE_URL, RegulationsClient

    key = os.environ["REGS_API_KEY"]
    return preview_document(RegulationsClient(os.environ.get("REGS_BASE_URL", DEFAULT_BASE_URL), key), document_id)


def _start(job: dict) -> None:
    def work() -> None:
        from worker.main import _client
        from worker.run_job import execute_job

        try:
            execute_job(job, _client(), REPO, ROOT / "work" / job["jobId"])
        except Exception as exc:  # noqa: BLE001
            REPO.update_job(job["userId"], job["jobId"], {"status": "failed", "error": str(exc)})

    threading.Thread(target=work, daemon=True).start()


REPO.start_job = _start  # type: ignore[method-assign]


class Handler(BaseHTTPRequestHandler):
    server_version = "regs-local/1.0"

    def _user(self) -> str:
        return self.headers.get("X-Local-User", "local")

    def _send(self, status: int, payload: dict | bytes, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type, x-local-user")
        self.send_header("access-control-allow-methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/local-file":
            key = parse_qs(parsed.query).get("key", [""])[0]
            data = REPO.get_object(key)
            if data is None:
                self._send(404, {"error": "File not found."})
                return
            content_type = "application/octet-stream"
            if key.endswith(".csv"):
                content_type = "text/csv; charset=utf-8"
            elif key.endswith(".json"):
                content_type = "application/json"
            elif key.endswith(".zip"):
                content_type = "application/zip"
            self._send(200, data, content_type)
            return
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        status, body = route("GET", parsed.path, query, None, self._user(), REPO, previewer=_preview)
        self._send(status, body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "Request body must be JSON."})
            return
        parsed = urlparse(self.path)
        status, body = route("POST", parsed.path, {}, payload, self._user(), REPO, previewer=_preview)
        self._send(status, body)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        status, body = route("DELETE", parsed.path, {}, None, self._user(), REPO, previewer=_preview)
        self._send(status, body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[local] {self.address_string()} {fmt % args}")


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Local API on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
