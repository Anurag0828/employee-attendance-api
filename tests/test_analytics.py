import json

import pytest
from conftest import log, stored_employee

pytestmark = pytest.mark.integration


@pytest.fixture
def analytics_data(database):
    database.employees.insert_many(
        [
            stored_employee("EMP0001", department="A"),
            stored_employee("EMP0002", department="A"),  # No logs.
            stored_employee("EMP0003", department="A", joined_on="2026-07-08"),
            stored_employee("EMP0004", department="B"),
            stored_employee("EMP0005", department="A", joined_on="2026-08-01"),
            stored_employee("EMP0006", department="Future", joined_on="2026-08-01"),
            stored_employee("EMP0007", department="Empty"),
            stored_employee("EMP0008", department="B"),
            stored_employee("EMP0009", department="B"),
        ]
    )
    database.attendance_logs.insert_many(
        [
            log("EMP0001", "2026-07-06", work_hours=8.0, late_minutes=10, overtime_minutes=30),
            log("EMP0001", "2026-07-07", "WFH", work_hours=3.0, half_day=True, late_minutes=20),
            log("EMP0001", "2026-07-09", "LEAVE"),
            log("EMP0001", "2026-07-11", "ON_DUTY", work_hours=10.0, late_minutes=20, overtime_minutes=45),
            log("EMP0003", "2026-07-08", work_hours=4.51, late_minutes=50),
            log("EMP0004", "2026-07-06", work_hours=9.0, late_minutes=50),
            log("EMP0008", "2026-07-06", late_minutes=10),
            log("EMP0009", "2026-07-06", late_minutes=100),
            log("EMP9999", "2026-07-06", late_minutes=999),  # Orphan, excluded from employee joins.
            log("EMP0001", "2026-06-30", late_minutes=900),  # Outside month/range.
        ]
    )


def test_monthly_uses_stored_metrics_weekdays_and_join_date(api, analytics_data):
    response = api.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "emp_code": "EMP0001",
        "month": "2026-07",
        "working_days": 23,
        "present_days": 1.5,
        "leave_days": 1,
        "late_count": 3,
        "total_late_minutes": 50,
        "total_overtime_minutes": 75,
        "attendance_pct": 6.52,
    }
    mid = api.get("/analytics/employees/EMP0003/monthly?month=2026-07").json()
    assert mid["working_days"] == 18 and mid["attendance_pct"] == 5.56
    empty = api.get("/analytics/employees/EMP0002/monthly?month=2026-07").json()
    assert empty["present_days"] == 0 and empty["working_days"] == 23 and empty["attendance_pct"] == 0
    future = api.get("/analytics/employees/EMP0005/monthly?month=2026-07").json()
    assert future["working_days"] == 0 and future["attendance_pct"] is None
    assert api.get("/analytics/employees/UNKNOWN/monthly?month=2026-07").status_code == 404


def test_department_headcount_and_record_weighted_average(api, analytics_data):
    response = api.get("/analytics/departments/summary?month=2026-07")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [r["department"] for r in items] == ["A", "B", "Empty"]
    assert items[0] == {
        "department": "A",
        "headcount": 3,
        "present_days": 2.5,
        "avg_work_hours": 6.38,
        "late_count": 4,
        "total_late_minutes": 100,
        "leave_count": 1,
        "on_duty_count": 1,
    }
    assert items[2]["headcount"] == 1 and items[2]["avg_work_hours"] is None
    assert api.get("/analytics/departments/summary?month=2026-07&department=A").json()["items"] == items[:1]
    assert api.get("/analytics/departments/summary?month=2026-07&department=a").json()["items"] == []


def test_decimal_half_up_in_database_average(api, database):
    database.employees.insert_one(stored_employee())
    database.attendance_logs.insert_many([log(work_hours=4.50), log(day="2026-07-07", work_hours=4.51)])
    assert (
        api.get("/analytics/departments/summary?month=2026-07").json()["items"][0]["avg_work_hours"] == 4.51
    )


def test_competition_ranks_ties_cutoff_and_department_filter(api, analytics_data):
    rows = api.get("/analytics/leaderboard/late?month=2026-07&limit=2").json()["items"]
    assert [(r["emp_code"], r["rank"], r["total_late_minutes"]) for r in rows] == [
        ("EMP0009", 1, 100),
        ("EMP0001", 2, 50),
        ("EMP0003", 2, 50),
        ("EMP0004", 2, 50),
    ]
    assert api.get("/analytics/leaderboard/late?month=2026-07&limit=4").json()["items"] == rows
    assert api.get("/analytics/leaderboard/late?month=2026-07&limit=5").json()["items"][-1]["rank"] == 5
    filtered = api.get("/analytics/leaderboard/late?month=2026-07&department=A&limit=1").json()["items"]
    assert [(r["emp_code"], r["rank"]) for r in filtered] == [("EMP0001", 1), ("EMP0003", 1)]
    assert api.get("/analytics/leaderboard/late?month=2026-05").json()["items"] == []


def test_trend_fills_gaps_counts_joiners_and_uses_calendar_window(api, analytics_data):
    response = api.get("/analytics/departments/A/trend?from=2026-07-06&to=2026-07-13")
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [r["date"] for r in rows] == [f"2026-07-{day:02d}" for day in range(6, 14)]
    assert [r["headcount"] for r in rows] == [2, 2, 3, 3, 3, 3, 3, 3]
    assert [r["present_count"] for r in rows] == [1, 0.5, 1, 0, 0, 1, 0, 0]
    assert [r["attendance_rate"] for r in rows] == [0.5, 0.25, 0.3333, 0, 0, None, None, 0]
    assert rows[0]["moving_avg_7d"] == 0.5  # No records before requested start.
    assert rows[2]["moving_avg_7d"] == 0.3611
    assert rows[-1]["moving_avg_7d"] == 0.1167  # Monday 6th has left the seven-day window.
    assert rows[5]["late_count"] == 1 and rows[5]["is_working_day"] is False


def test_trend_empty_future_weekend_and_range_bounds(api, analytics_data):
    future = api.get("/analytics/departments/Future/trend?from=2026-07-06&to=2026-07-07").json()["items"]
    assert len(future) == 2
    assert all(
        r["headcount"] == 0 and r["attendance_rate"] is None and r["moving_avg_7d"] is None for r in future
    )
    weekend = api.get("/analytics/departments/Empty/trend?from=2026-07-11&to=2026-07-12").json()["items"]
    assert all(r["attendance_rate"] is None and r["moving_avg_7d"] is None for r in weekend)
    empty = api.get("/analytics/departments/Empty/trend?from=2026-07-06&to=2026-07-06").json()["items"]
    assert empty[0]["attendance_rate"] == 0 and empty[0]["moving_avg_7d"] == 0
    assert api.get("/analytics/departments/UNKNOWN/trend?from=2026-07-06&to=2026-07-06").status_code == 404
    assert (
        len(api.get("/analytics/departments/Empty/trend?from=2026-01-01&to=2026-04-02").json()["items"]) == 92
    )


EXPLAINS = [
    ("attendance_list", "date_from=2026-07-01&date_to=2026-07-31&page=2&page_size=2"),
    ("attendance_list", "emp_code=EMP0001&status=PRESENT&page_size=1"),
    ("attendance_list", "status=PRESENT&page_size=1"),
    ("employee_monthly", "emp_code=EMP0001&month=2026-07"),
    ("department_summary", "month=2026-07"),
    ("department_summary", "month=2026-07&department=A"),
    ("late_leaderboard", "month=2026-07&department=A&limit=1"),
    ("department_trend", "department=A&from=2026-07-06&to=2026-07-13"),
]


def assert_indexed(explain):
    text = json.dumps(explain)
    assert "IXSCAN" in text
    assert "COLLSCAN" not in text

    def inspect(value):
        if isinstance(value, dict):
            if "collectionScans" in value:
                assert value["collectionScans"] == 0
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(explain)


@pytest.mark.parametrize("endpoint,query", EXPLAINS)
def test_explain_executes_indexed_real_query(api, analytics_data, endpoint, query):
    response = api.get(f"/admin/explain/{endpoint}?{query}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["endpoint"] == endpoint
    assert_indexed(body["explain"])
