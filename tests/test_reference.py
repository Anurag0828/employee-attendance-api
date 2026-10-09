"""Independent Python oracle for randomized stored data; application uses MongoDB."""

import calendar
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from conftest import log, stored_employee

pytestmark = pytest.mark.integration
PRESENT = {"PRESENT", "WFH", "ON_DUTY"}


def rounded(value, places=2):
    return float(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def weight(record):
    return (
        Decimal("0.5" if record.get("half_day", False) else "1")
        if record["status"] in PRESENT
        else Decimal(0)
    )


def weekday(day):
    return date.fromisoformat(day).weekday() < 5


def test_randomized_analytics_against_independent_reference(api, database):
    rng = random.Random(91837)
    employees = [
        stored_employee(
            f"EMP{i:04d}",
            department="A" if i % 2 else "B",
            joined_on=rng.choice(["2026-01-01", "2026-07-08", "2026-07-15", "2026-08-01"]),
        )
        for i in range(1, 13)
    ]
    records = []
    for emp in employees:
        for day in range(1, 32):
            day_text = f"2026-07-{day:02d}"
            if day_text < emp["joined_on"] or rng.random() < 0.35:
                continue
            status = rng.choice(["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"])
            metrics = {}
            if status in PRESENT:
                metrics = {
                    "half_day": rng.choice([True, False]),
                    "work_hours": rng.choice([None, 3.5, 4.51, 8.25, 9.03]),
                    "late_minutes": rng.choice([0, 10, 35, 90]),
                    "overtime_minutes": rng.choice([0, 30, 45]),
                }
            records.append(log(emp["emp_code"], day_text, status, **metrics))
    database.employees.insert_many([dict(e) for e in employees])
    database.attendance_logs.insert_many([dict(r) for r in records])

    def stats(rows):
        return {
            "present_days": float(sum((weight(r) for r in rows if weekday(r["date"])), Decimal(0))),
            "leave_days": sum(r["status"] == "LEAVE" for r in rows),
            "late_count": sum(r.get("late_minutes", 0) > 0 for r in rows),
            "total_late_minutes": sum(r.get("late_minutes", 0) for r in rows),
            "total_overtime_minutes": sum(r.get("overtime_minutes", 0) for r in rows),
        }

    for emp in employees:
        rows = [r for r in records if r["emp_code"] == emp["emp_code"]]
        first = max(date(2026, 7, 1), date.fromisoformat(emp["joined_on"]))
        working = sum(
            (first + timedelta(days=i)).weekday() < 5
            for i in range(max(0, (date(2026, 7, 31) - first).days + 1))
        )
        expected = stats(rows)
        expected.update(
            emp_code=emp["emp_code"],
            month="2026-07",
            working_days=working,
            attendance_pct=rounded(Decimal(str(expected["present_days"])) * 100 / working)
            if working
            else None,
        )
        assert api.get(f"/analytics/employees/{emp['emp_code']}/monthly?month=2026-07").json() == expected

    expected_departments = []
    for department in ("A", "B"):
        eligible = [e for e in employees if e["department"] == department and e["joined_on"] <= "2026-07-31"]
        codes = {e["emp_code"] for e in eligible}
        rows = [r for r in records if r["emp_code"] in codes]
        values = stats(rows)
        hours = [
            Decimal(str(r["work_hours"]))
            for r in rows
            if r["status"] in PRESENT and r.get("work_hours") is not None
        ]
        expected_departments.append(
            {
                "department": department,
                "headcount": len(eligible),
                "present_days": values["present_days"],
                "avg_work_hours": rounded(sum(hours) / len(hours)) if hours else None,
                "late_count": values["late_count"],
                "total_late_minutes": values["total_late_minutes"],
                "leave_count": values["leave_days"],
                "on_duty_count": sum(r["status"] == "ON_DUTY" for r in rows),
            }
        )

        daily = []
        for day in range(1, calendar.monthrange(2026, 7)[1] + 1):
            day_text = f"2026-07-{day:02d}"
            headcount = sum(e["joined_on"] <= day_text for e in eligible)
            on_day = [r for r in rows if r["date"] == day_text]
            present = sum((weight(r) for r in on_day), Decimal(0))
            rate = rounded(present / headcount, 4) if headcount and weekday(day_text) else None
            previous = [r["attendance_rate"] for r in daily[-6:]] + [rate]
            rates = [Decimal(str(v)) for v in previous if v is not None]
            daily.append(
                {
                    "date": day_text,
                    "headcount": headcount,
                    "is_working_day": weekday(day_text),
                    "present_count": float(present),
                    "late_count": sum(r.get("late_minutes", 0) > 0 for r in on_day),
                    "attendance_rate": rate,
                    "moving_avg_7d": rounded(sum(rates) / len(rates), 4) if rates else None,
                }
            )
        actual = api.get(f"/analytics/departments/{department}/trend?from=2026-07-01&to=2026-07-31").json()
        assert actual["items"] == daily
    assert api.get("/analytics/departments/summary?month=2026-07").json()["items"] == expected_departments
