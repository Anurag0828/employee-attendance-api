from datetime import timedelta

import pytest
from conftest import employee, ms
from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure

from app import main


@pytest.mark.parametrize(
    "time,expected",
    [
        ("09:29:59", 0),
        ("09:40:00", 0),
        ("09:40:00.999", 0),
        ("09:40:01", 10),
        ("10:15:59", 45),
    ],
)
def test_late_seconds_boundary(time, expected):
    instant = main.from_ms(ms("2026-07-06T" + time))
    result = main.derived_fields({"date": "2026-07-06", "status": "PRESENT", "punch_in": instant}, employee())
    assert result["late_minutes"] == expected
    assert result["punch_in"].microsecond == 0


@pytest.mark.parametrize(
    "seconds,hours,half",
    [(16181, 4.49, True), (16182, 4.50, False), (16218, 4.51, False), (1, 0.0, True), (86400, 24.0, False)],
)
def test_decimal_rounding_and_rounded_half_day(seconds, hours, half):
    instant = main.from_ms(ms("2026-07-06T09:30:00"))
    result = main.derived_fields(
        {
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": instant,
            "punch_out": instant + timedelta(seconds=seconds),
        },
        employee(),
    )
    assert result["work_hours"] == hours
    assert result["half_day"] is half


@pytest.mark.parametrize(
    "time,expected", [("18:35:00", 0), ("18:59:59", 0), ("19:00:00", 30), ("19:10:59", 40)]
)
def test_overtime_threshold(time, expected):
    _, end = main.shift_bounds("2026-07-06", employee())
    assert main.compute_overtime(main.from_ms(ms("2026-07-06T" + time)), end) == expected


@pytest.mark.parametrize(
    "local,day",
    [
        ("2026-07-06T21:55:00", "2026-07-06"),
        ("2026-07-07T00:15:00", "2026-07-06"),
        ("2026-07-07T05:59:59", "2026-07-06"),
        ("2026-07-07T06:00:00", "2026-07-07"),
    ],
)
def test_overnight_attendance_date(local, day):
    assert (
        main.attendance_date(main.from_ms(ms(local)), employee(shift_start="22:00", shift_end="06:00")) == day
    )


@pytest.mark.parametrize(
    "bad", [None, True, False, 1783310400, 1783310400000.0, "1783310400000", 99999999999, 4102444800001, -1]
)
@pytest.mark.parametrize("path", ["punch-in", "punch-out"])
def test_bad_timestamps_rejected_before_database_access(bad, path):
    response = TestClient(main.app).post(
        "/attendance/" + path, json={"emp_code": "EMP0001", "punched_at": bad}
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize(
    "field,value",
    [
        ("emp_code", "EMP1"),
        ("emp_code", "1234"),
        ("name", ""),
        ("name", "x" * 101),
        ("email", "bad"),
        ("department", ""),
        ("department", "x" * 51),
        ("shift_start", "24:00"),
        ("shift_end", "09:30"),
        ("joined_on", "2026-02-30"),
        ("joined_on", "2026-7-01"),
        ("joined_on", "0000-01-01"),
    ],
)
def test_employee_validation(field, value):
    response = TestClient(main.app).post("/employees", json=employee(**{field: value}))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "/employees?page=0",
        "/employees?page_size=101",
        "/attendance?page_size=0",
        "/attendance?status=OTHER",
        "/attendance?date_from=2026-02-30",
        "/attendance?date_from=2026-07-02&date_to=2026-07-01",
        "/analytics/employees/EMP0001/monthly?month=2026-13",
        "/analytics/departments/summary?month=2026-1",
        "/analytics/leaderboard/late?month=2026-07&limit=51",
        "/analytics/leaderboard/late?month=2026-07&limit=0",
        "/analytics/departments/A/trend?from=2026-07-02&to=2026-07-01",
        "/analytics/departments/A/trend?from=2026-01-01&to=2026-04-03",
        "/admin/explain/employee_monthly?month=2026-07",
        "/admin/explain/department_summary",
        "/admin/explain/late_leaderboard",
        "/admin/explain/department_trend?department=A&from=2026-01-01",
        "/admin/explain/not_real",
        "/admin/explain/attendance_list?date_from=2026-07-02&date_to=2026-07-01",
    ],
)
def test_query_validation(url):
    assert TestClient(main.app).get(url).status_code == 422


@pytest.mark.parametrize("field", ["punch_in", "punch_out", "status"])
def test_explicit_null_correction_invalid(field):
    response = TestClient(main.app).patch(
        "/attendance/EMP0001/2026-07-06",
        json={
            "reason": "Device failed",
            "regularized_by": "HR",
            field: None,
        },
    )
    assert response.status_code == 422


def test_health_probes_database_and_hides_connection_details(monkeypatch):
    class BrokenDatabase:
        def command(self, *_args):
            raise ConnectionFailure("private connection details")

    monkeypatch.setattr(main, "db", BrokenDatabase())
    response = TestClient(main.app).get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "MongoDB unavailable"}
