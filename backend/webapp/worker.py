import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from rq import Worker

from .config import settings
from .logging_config import configure_logging
from .migrate import run_migrations
from .queueing import get_redis_connection


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz"}:
            payload = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        # Keep probe noise out of worker logs.
        return


def _start_health_server(logger: logging.Logger) -> None:
    raw_port = os.getenv("PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError:
        port = 8080

    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        logger.warning("Worker health server failed to bind port %s: %s", port, exc)
        return

    thread = threading.Thread(target=server.serve_forever, name="worker-healthz", daemon=True)
    thread.start()
    logger.info("Worker health server listening on port %s", port)


def main() -> None:
    configure_logging(logging.INFO)
    logger = logging.getLogger(__name__)
    _start_health_server(logger)
    run_migrations()
    conn = get_redis_connection()
    worker = Worker([settings.queue_name], connection=conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
