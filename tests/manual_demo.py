"""Seed fictional records through HTTP into the explicitly chosen demo database."""

from datetime import datetime

import httpx


def ms(local):
    return int(datetime.fromisoformat(local + "+05:30").timestamp() * 1000)


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=10) as api:
        assert api.get("/health").json() == {"status": "ok"}
        employees = [
            ("EMP9001", "Riya Demo", "Engineering Demo", "09:30", "18:30"),
            ("EMP9002", "Arjun Demo", "Engineering Demo", "09:30", "18:30"),
            ("EMP9003", "Maya Demo", "Sales Demo", "09:30", "18:30"),
            ("EMP9004", "Kabir Demo", "Support Demo", "22:00", "06:00"),
        ]
        for code, name, department, start, end in employees:
            response = api.post(
                "/employees",
                json={
                    "emp_code": code,
                    "name": name,
                    "email": f"{code}@example.test",
                    "department": department,
                    "joined_on": "2026-10-01",
                    "shift_start": start,
                    "shift_end": end,
                },
            )
            assert response.status_code in (201, 409), response.text
        punches = [
            ("EMP9001", "2026-10-05T09:40:01", "2026-10-05T18:30:00", "PRESENT"),
            ("EMP9001", "2026-10-06T09:30:00", "2026-10-06T13:00:00", "WFH"),
            ("EMP9001", "2026-10-07T10:05:00", "2026-10-07T18:30:00", "PRESENT"),
            ("EMP9003", "2026-10-05T10:15:00", "2026-10-05T19:15:00", "ON_DUTY"),
            ("EMP9004", "2026-10-08T21:55:00", "2026-10-09T06:40:00", "PRESENT"),
        ]
        for code, start, end, status in punches:
            response = api.post(
                "/attendance/punch-in", json={"emp_code": code, "punched_at": ms(start), "status": status}
            )
            assert response.status_code in (201, 409), response.text
            response = api.post("/attendance/punch-out", json={"emp_code": code, "punched_at": ms(end)})
            assert response.status_code in (200, 409), response.text
        response = api.patch(
            "/attendance/EMP9001/2026-10-07",
            json={
                "punch_in": ms("2026-10-07T09:28:00"),
                "reason": "Corrected biometric device time",
                "regularized_by": "demo.hr",
            },
        )
        assert response.status_code in (200, 422), response.text  # Rerunning may be a no-op.
        checks = [
            "/employees",
            "/attendance",
            "/analytics/employees/EMP9001/monthly?month=2026-10",
            "/analytics/departments/summary?month=2026-10",
            "/analytics/leaderboard/late?month=2026-10",
            "/analytics/departments/Engineering%20Demo/trend?from=2026-10-05&to=2026-10-11",
            "/admin/explain/employee_monthly?emp_code=EMP9001&month=2026-10",
        ]
        for path in checks:
            response = api.get(path)
            assert response.status_code == 200, response.text
            print("200", path)
        records = api.get("/attendance?emp_code=EMP9001").json()["items"]
        corrected = next(r for r in records if r["date"] == "2026-10-07")
        assert corrected["late_minutes"] == 0 and len(corrected["history"]) == 1
        print("Demo ready: 4 employees, 5 records, half-day, overnight shift, and audited correction.")


if __name__ == "__main__":
    main()
