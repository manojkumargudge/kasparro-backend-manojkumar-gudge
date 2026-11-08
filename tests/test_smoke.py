"""Runs scripts/smoke_test.py against a real uvicorn server (live HTTP), including the
crash → restart → resume check with real ETL subprocesses."""

import shlex
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from scripts import smoke_test
from tests.conftest import API_KEY

ROOT = Path(__file__).resolve().parents[1]
ROWS = [[f"s{i}", f"Smoke {i}", str(i + 1), str((i + 1) * 10)] for i in range(6)]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_server() -> Iterator[str]:
    from api.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _etl_cmd(**env: str) -> str:
    code = (
        "import os, runpy, sys; "
        f"os.environ.update({env!r}); sys.argv = ['run_etl.py']; "
        "runpy.run_path('scripts/run_etl.py', run_name='__main__')"
    )
    return (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
        if sys.platform != "win32"
        else (f'"{sys.executable}" -c "{code.replace(chr(34), chr(39))}"')
    )


def test_smoke_against_live_server(live_server, write_csv, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("ETL_SOURCES", "csv")
    monkeypatch.setenv("CSV_PATH", str(write_csv("smoke.csv", ROWS)))
    monkeypatch.setenv("ETL_BATCH_SIZE", "2")
    monkeypatch.setenv("API_RATE_LIMIT", "15")
    from scripts import run_etl

    assert run_etl.run() == 0  # initial ingest so /health has a successful run

    code = smoke_test.main(
        [
            "--base-url",
            live_server,
            "--api-key",
            API_KEY,
            "--crash-cmd",
            _etl_cmd(FAIL_AFTER_N="3", FAIL_MODE="exit"),
            "--resume-cmd",
            _etl_cmd(),
            "--resume-source",
            "csv",
            "--max-requests",
            "30",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0, out
    for check in ("ingest", "data", "stats", "metrics", "resume", "ratelimit"):
        assert f"PASS {check}" in out, out
    assert "abandoned" in out


def test_smoke_reports_failures(live_server, capsys):
    code = smoke_test.main(["--base-url", live_server, "--api-key", "wrong-key", "--skip-rate-limit"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL ingest" in out  # no runs yet
    assert "FAIL data" in out and "HTTP 401" in out
    assert "SKIP resume" in out and "SKIP ratelimit" in out


def test_smoke_requires_key(monkeypatch):
    monkeypatch.delenv("APP_API_KEY")
    assert smoke_test.main(["--base-url", "http://127.0.0.1:9"]) == 2
