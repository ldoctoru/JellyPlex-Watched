"""Local web GUI: read-only status, live logs, and run/preview controls.

Uses only the standard library so the default install gains no dependencies.
The server is opt-in (``gui_enabled``) and binds to loopback by default.
"""

import hmac
import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from src.connection import connect_server
from src.settings import AppSettings
from src.watched import WatchedUpdate

STATIC_DIR = Path(__file__).parent / "web_static"
MAX_PLAN_ROWS = 500
LOG_BUFFER_LINES = 1000
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def summarize_plan(plan: dict[Any, dict[str, list[WatchedUpdate]]]) -> dict[str, Any]:
    """Flatten a watched plan into JSON-safe rows for display."""
    rows: list[dict[str, Any]] = []
    total = 0
    for destination, batches in plan.items():
        destination_name = destination.server_settings.name
        for source_name, updates in batches.items():
            for update in updates:
                data = update.library_data
                items: list[tuple[str, Any]] = [
                    (item.identifiers.title or "[untitled]", item.status)
                    for item in data.movies
                ]
                for series in data.series:
                    series_title = series.identifiers.title or "[untitled]"
                    items.extend(
                        (f"{series_title} · {ep.identifiers.title or '[episode]'}", ep.status)
                        for ep in series.episodes
                    )
                for title, status in items:
                    total += 1
                    if len(rows) < MAX_PLAN_ROWS:
                        rows.append(
                            {
                                "title": title,
                                "user": update.target_user,
                                "library": update.target_library,
                                "source": source_name,
                                "destination": destination_name,
                                "change": "watched" if status.completed else "in progress",
                            }
                        )
    return {"total": total, "truncated": total > len(rows), "rows": rows}


@dataclass
class Controller:
    """Shared state between the run loop and the web server."""

    settings: AppSettings
    trigger: threading.Event = field(default_factory=threading.Event)
    run_lock: threading.Lock = field(default_factory=threading.Lock)
    logs: deque[tuple[int, str]] = field(
        default_factory=lambda: deque(maxlen=LOG_BUFFER_LINES)
    )
    _log_seq: int = 0
    _log_sink_id: int | None = None
    _log_guard: threading.Lock = field(default_factory=threading.Lock)
    state: Any = None
    next_run: datetime | None = None
    preview: dict[str, Any] | None = None
    preview_at: datetime | None = None
    preview_error: str | None = None
    previewing: bool = False

    # -- logs ---------------------------------------------------------------
    def attach_log_sink(self) -> None:
        """(Re)attach the ring-buffer sink; configure_logger removes all sinks."""
        with self._log_guard:
            self._log_sink_id = logger.add(
                self._on_log, level=self.settings.debug_level, format="{message}"
            )

    def _on_log(self, message: Any) -> None:
        record = message.record
        line = (
            f"{record['time']:%H:%M:%S} | {record['level'].name:<7} | "
            f"{record['message']}"
        )
        with self._log_guard:
            self._log_seq += 1
            self.logs.append((self._log_seq, line))

    def logs_after(self, after: int) -> dict[str, Any]:
        with self._log_guard:
            lines = [{"id": i, "line": text} for i, text in self.logs if i > after]
            return {"lines": lines, "last": self._log_seq}

    # -- status -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        state = self.state
        return {
            "dryrun": self.settings.dryrun,
            "run_only_once": self.settings.run_only_once,
            "sleep_duration": self.settings.sleep_duration,
            "running": bool(state and state.running) or self.previewing,
            "last_started": _iso(state.last_started) if state else None,
            "last_duration": state.last_duration if state else None,
            "last_error": state.last_error if state else None,
            "last_planned_servers": state.last_planned_servers if state else 0,
            "next_run": _iso(self.next_run),
            "servers": [
                {"name": s.name, "type": type(s).__name__.removesuffix("Settings").lower()}
                for s in self.settings.all_servers
            ],
        }

    def check_health(self) -> list[dict[str, Any]]:
        results = []
        for server in self.settings.all_servers:
            start = perf_counter()
            try:
                connection = connect_server(self.settings, server)
                info = str(connection.info())
                results.append(
                    {"name": server.name, "ok": True, "info": info,
                     "ms": round((perf_counter() - start) * 1000)}
                )
            except Exception as error:  # noqa: BLE001 - report any failure to the UI
                results.append(
                    {"name": server.name, "ok": False, "info": _short(error),
                     "ms": round((perf_counter() - start) * 1000)}
                )
        return results

    # -- actions ------------------------------------------------------------
    def request_run(self) -> bool:
        if self.run_lock.locked():
            return False
        self.trigger.set()
        return True

    def start_preview(self) -> bool:
        """Build a plan in the background without writing anything."""
        from src.main import build_plan  # local import: avoids a cycle at load

        if not self.run_lock.acquire(blocking=False):
            return False
        self.previewing = True

        def work() -> None:
            try:
                average = self.state.average_time if self.state else 100.0
                self.preview = summarize_plan(build_plan(self.settings, average))
                self.preview_error = None
            except Exception as error:  # noqa: BLE001
                self.preview_error = _short(error)
            finally:
                self.preview_at = datetime.now(timezone.utc)
                self.previewing = False
                self.run_lock.release()

        threading.Thread(target=work, name="jpw-preview", daemon=True).start()
        return True


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _short(error: Exception) -> str:
    return str(error).splitlines()[0][:300] if str(error) else type(error).__name__


def _make_handler(controller: Controller) -> type[BaseHTTPRequestHandler]:
    token = controller.settings.gui_token
    loopback_only = not token

    class Handler(BaseHTTPRequestHandler):
        server_version = "jpw-gui"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.trace("gui: " + format % args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _authorized(self) -> bool:
            if loopback_only:
                # Blocks DNS-rebinding: only accept loopback Host headers.
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                return host in _LOOPBACK_HOSTS
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
            return hmac.compare_digest(supplied.encode(), (token or "").encode())

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                if loopback_only and not self._authorized():
                    return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                page = (STATIC_DIR / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")
            assets = {"/app.js": "text/javascript", "/app.css": "text/css"}
            if url.path in assets:
                body = (STATIC_DIR / url.path.lstrip("/")).read_bytes()
                return self._send(200, body, assets[url.path] + "; charset=utf-8")
            if not self._authorized():
                return self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            if url.path == "/api/status":
                return self._json(controller.status())
            if url.path == "/api/health":
                return self._json({"servers": controller.check_health()})
            if url.path == "/api/logs":
                after = int(parse_qs(url.query).get("after", ["0"])[0] or 0)
                return self._json(controller.logs_after(after))
            if url.path == "/api/plan":
                return self._json(
                    {
                        "running": controller.previewing,
                        "at": _iso(controller.preview_at),
                        "error": controller.preview_error,
                        "plan": controller.preview,
                    }
                )
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            # A custom header forces a CORS preflight, which we never grant.
            if self.headers.get("X-Requested-With") != "jpw":
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            path = urlparse(self.path).path
            if path == "/api/run":
                accepted = controller.request_run()
            elif path == "/api/preview":
                accepted = controller.start_preview()
            else:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            self._json({"accepted": accepted}, 202 if accepted else HTTPStatus.CONFLICT)

    return Handler


def start_web_server(controller: Controller) -> ThreadingHTTPServer:
    settings = controller.settings
    httpd = ThreadingHTTPServer(
        (settings.gui_host, settings.gui_port), _make_handler(controller)
    )
    threading.Thread(target=httpd.serve_forever, name="jpw-gui", daemon=True).start()
    logger.info("Web GUI listening on {}:{}", settings.gui_host, settings.gui_port)
    return httpd
