import os
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app import main


def pytest_addoption(parser):
    parser.addoption("--run-scale", action="store_true", help="Also test 100,000 records")
    parser.addoption(
        "--use-app-mongo", action="store_true", help="Use MONGO_URI's server, always in a new test database"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-scale"):
        for item in items:
            if "scale" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Use --run-scale"))


@pytest.fixture(scope="session")
def mongo_database(request):
    # Never use MONGO_DB. Only this freshly generated database can be removed.
    uri = (
        os.environ["MONGO_URI"]
        if request.config.getoption("--use-app-mongo")
        else os.getenv("TEST_MONGO_URI", "mongodb://127.0.0.1:27017")
    )
    connection = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    connection.admin.command("ping")
    name = "attendance_test_" + uuid4().hex[:20]
    database = connection[name]
    try:
        main.create_indexes(database)
        yield database
    finally:
        assert name.startswith("attendance_test_") and len(name) == 36
        connection.drop_database(name)
        connection.close()


@pytest.fixture
def database(mongo_database, monkeypatch):
    mongo_database.employees.delete_many({})
    mongo_database.attendance_logs.delete_many({})
    monkeypatch.setattr(main, "db", mongo_database)
    return mongo_database


@pytest.fixture
def api(database):
    # Index lifecycle is exercised separately; don't close the shared test pool.
    return TestClient(main.app)


def ms(local: str) -> int:
    return int(datetime.fromisoformat(local).replace(tzinfo=main.IST).timestamp() * 1000)


def employee(code="EMP0001", **extra):
    return {
        "emp_code": code,
        "name": "Test Employee",
        "email": f"{code}@example.test",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
        **extra,
    }


def stored_employee(code="EMP0001", **extra):
    return {**employee(code, **extra), "created_at": main.from_ms(ms("2026-01-01T09:00:00"))}


def log(code="EMP0001", day="2026-07-06", status="PRESENT", **extra):
    # Intentionally permit authoritative metrics differing from the punch times.
    return {
        "emp_code": code,
        "date": day,
        "status": status,
        "punch_in": main.from_ms(ms(day + "T09:30:00")) if status in main.PRESENCE else None,
        "punch_out": None,
        "work_hours": None,
        **extra,
    }
