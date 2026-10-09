"""Exercise the actual uvicorn command, environment precedence, and readiness."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import employee

pytestmark = pytest.mark.integration


def test_uvicorn_readiness_and_environment_override(database, request):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["MONGO_URI"] = (
        os.environ["MONGO_URI"]
        if request.config.getoption("--use-app-mongo")
        else os.getenv("TEST_MONGO_URI", "mongodb://127.0.0.1:27017")
    )
    environment["MONGO_DB"] = database.name  # Overrides any developer .env setting.
    started = time.perf_counter()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1, trust_env=False) as http:
            while time.perf_counter() - started < 20:
                assert process.poll() is None, "uvicorn exited before readiness"
                try:
                    if http.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Application did not become ready within 20 seconds")
            elapsed = time.perf_counter() - started
            response = http.post("/employees", json=employee())
            assert response.status_code == 201
            assert database.employees.count_documents({"emp_code": "EMP0001"}) == 1
            print(f"uvicorn readiness: {elapsed:.3f}s")
    finally:
        process.terminate()
        process.wait(timeout=10)
