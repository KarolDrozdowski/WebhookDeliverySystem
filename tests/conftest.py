from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_file_release(path: Path, attempts: int = 20, delay_seconds: float = 0.1) -> None:
    for _ in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(delay_seconds)


@dataclass
class RecordedRequest:
    body: str


@dataclass
class HttpbinStub:
    statuses: list[int] = field(default_factory=lambda: [204])
    requests: list[RecordedRequest] = field(default_factory=list)
    response_bodies: dict[int, str] = field(default_factory=dict)
    server: HTTPServer | None = None
    thread: threading.Thread | None = None
    port: int | None = None

    @property
    def url(self) -> str:
        assert self.port is not None
        return f"http://127.0.0.1:{self.port}/webhook"

    def start(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                stub.requests.append(RecordedRequest(body=body))

                status = stub.statuses.pop(0) if len(stub.statuses) > 1 else stub.statuses[0]
                response_body = stub.response_bodies.get(status, "")

                self.send_response(status)
                self.end_headers()
                if response_body:
                    self.wfile.write(response_body.encode("utf-8"))

            def log_message(self, format: str, *args: object) -> None:
                return

        self.port = _find_free_port()
        self.server = HTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


@pytest.fixture
def webhook_target() -> Generator[HttpbinStub, None, None]:
    stub = HttpbinStub()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def app_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "test_webhooks.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "0.1"
    os.environ["RETRY_DELAY_SECONDS"] = "0"
    os.environ["DELIVERY_TIMEOUT_SECONDS"] = "1"
    os.environ["WORKER_MAX_CONCURRENCY"] = "3"

    _purge_app_modules()
    app_module = import_module("app.main")
    db_module = import_module("app.db")

    with TestClient(app_module.app) as client:
        yield client

    db_module.engine.dispose()
    _purge_app_modules()
    for variable in (
        "DATABASE_URL",
        "WORKER_POLL_INTERVAL_SECONDS",
        "RETRY_DELAY_SECONDS",
        "DELIVERY_TIMEOUT_SECONDS",
        "WORKER_MAX_CONCURRENCY",
    ):
        os.environ.pop(variable, None)
    _wait_for_file_release(db_path)
