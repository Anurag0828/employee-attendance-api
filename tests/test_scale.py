import json
from datetime import date, timedelta
from time import perf_counter

import pytest
from conftest import log, stored_employee
from test_analytics import assert_indexed

from app import main

pytestmark = [pytest.mark.integration, pytest.mark.scale]


def test_100k_records_startup_preservation_and_index_plans(api, database):
    employees = [
        stored_employee(f"EMP{i:04d}", department="A" if i % 2 else "B", joined_on="2025-01-01")
        for i in range(1, 1001)
    ]
    database.employees.insert_many(employees)
    batch = []
    for offset in range(100):
        day = (date(2026, 5, 1) + timedelta(days=offset)).isoformat()
        for emp in employees:
            batch.append(log(emp["emp_code"], day, work_hours=8.0, late_minutes=10, half_day=False))
        if len(batch) == 5000:
            database.attendance_logs.insert_many(batch)
            batch = []
    assert database.attendance_logs.count_documents({}) == 100000
    # Mimic the grader: existing data, only default _id indexes at startup.
    database.employees.drop_indexes()
    database.attendance_logs.drop_indexes()
    before = perf_counter()
    main.create_indexes(database)
    index_seconds = perf_counter() - before
    assert index_seconds < 20
    assert database.attendance_logs.count_documents({}) == 100000
    cases = [
        ("attendance_list", "date_from=2026-07-01&date_to=2026-07-31&page=2&page_size=50", "/attendance"),
        ("attendance_list", "emp_code=EMP0001&date_from=2026-07-01&page_size=100", "/attendance"),
        ("attendance_list", "status=PRESENT&page_size=50", "/attendance"),
        ("employee_monthly", "emp_code=EMP0001&month=2026-07", "/analytics/employees/EMP0001/monthly"),
        ("department_summary", "month=2026-07", "/analytics/departments/summary"),
        ("department_summary", "month=2026-07&department=A", "/analytics/departments/summary"),
        ("late_leaderboard", "month=2026-07&limit=1", "/analytics/leaderboard/late"),
        ("department_trend", "department=A&from=2026-07-01&to=2026-07-31", "/analytics/departments/A/trend"),
    ]
    timings = []
    for endpoint, query, path in cases:
        before = perf_counter()
        response = api.get(f"{path}?{query}")
        elapsed = perf_counter() - before
        assert response.status_code == 200, response.text
        explained = api.get(f"/admin/explain/{endpoint}?{query}")
        assert explained.status_code == 200, explained.text
        assert_indexed(explained.json()["explain"])
        timings.append({"endpoint": endpoint, "seconds": round(elapsed, 3)})
        if endpoint == "employee_monthly":
            assert response.json()["present_days"] == 23 and response.json()["attendance_pct"] == 100
        elif endpoint == "department_summary":
            assert response.json()["items"][0]["headcount"] == 500
            assert response.json()["items"][0]["avg_work_hours"] == 8
        elif endpoint == "late_leaderboard":
            assert len(response.json()["items"]) == 1000
            assert all(row["rank"] == 1 for row in response.json()["items"])
    print(
        json.dumps(
            {
                "mongo_version": database.client.server_info()["version"],
                "records": 100000,
                "index_build_seconds": round(index_seconds, 3),
                "requests": timings,
            }
        )
    )
