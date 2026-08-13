"""Public, view-only dashboard hosted entirely on the Windows VPS.

- /api/dashboard: sanitized GET-only payload from storage/trades.json
- /health: non-sensitive liveness
- / and static assets: professional dashboard UI served from this repository
"""
from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import hmac
import json
import logging
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.dashboard_local_api import build_dashboard_payload  # noqa: E402
from utils.single_instance import acquire_single_instance  # noqa: E402

HOST = os.environ.get("DASHBOARD_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("DASHBOARD_API_PORT", "8787"))
TOKEN = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
PUBLIC_READONLY = os.environ.get("DASHBOARD_PUBLIC_READONLY", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
STATIC_ROOT = ROOT / "dashboard"
logger = logging.getLogger("dashboard_server")


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartSignalDashboard/1.0"

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.client_address[0], fmt % args)

    def _json(self, status: int, body) -> None:
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        if PUBLIC_READONLY:
            return True  # GET-only sanitized public endpoint requested by operator
        supplied = self.headers.get("X-Dashboard-Token", "")
        return bool(TOKEN) and hmac.compare_digest(supplied, TOKEN)

    def _api(self, query) -> None:
        if not PUBLIC_READONLY and not TOKEN:
            self._json(503, {"ok": False, "error": "DASHBOARD_API_TOKEN is not configured"})
            return
        if not self._authorized():
            self._json(401, {"ok": False, "error": "Unauthorized"})
            return
        try:
            limit = max(20, min(int((query.get("limit") or ["200"])[0]), 500))
            self._json(200, build_dashboard_payload(ROOT, limit=limit))
        except Exception as exc:  # noqa: BLE001
            logger.exception("dashboard payload failed")
            self._json(500, {"ok": False, "error": str(exc)[:300]})

    def _static(self, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        target = (STATIC_ROOT / rel).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "source": "vps-local-json"})
        elif parsed.path == "/api/dashboard":
            self._api(parse_qs(parsed.query))
        else:
            self._static(parsed.path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if not acquire_single_instance("dashboard_api.pid", "run_dashboard_server.py"):
        logger.info("dashboard API already running; exiting duplicate instance")
        return
    if not PUBLIC_READONLY and not TOKEN:
        raise RuntimeError("DASHBOARD_API_TOKEN is empty while public read-only mode is disabled")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info("dashboard API serving %s:%s from %s", HOST, PORT, ROOT / "storage")
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
