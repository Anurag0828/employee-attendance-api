from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import employee, log, ms, stored_employee

from app import main

pytestmark = pytest.mark.integration


def test_employee_creation_pagination_and_no_internal_ids(api, database):
    for code, dept in [("EMP0003", "Sales"), ("EMP0002", "Engineering"), ("EMP0001", "Engineering")]:
        response = api.post("/employees", json=employee(code, department=dept, _id="injected", created_at=1))
        assert response.status_code == 201
        body = response.json()
        assert set(body) == set(main.EMPLOYEE_FIELDS)
        assert isinstance(body["created_at"], int) and body["created_at"] % 1000 == 0
    page = api.get("/employees?department=Engineering&page=1&page_size=1").json()
    assert page["total"] == 2 and page["items"][0]["emp_code"] == "EMP0001"
    assert (
        api.get("/employees?department=Engineering&page=2&page_size=1").json()["items"][0]["emp_code"]
        == "EMP0002"
    )
    assert api.get("/employees?department=engineering").json()["total"] == 0
    assert api.get("/employees?department=").json()["total"] == 0
    assert api.post("/employees", json=employee()).status_code == 409
    assert api.get("/health").status_code == 200
    main.create_indexes(database)  # Idempotent, with data already present.
    assert database.employees.count_documents({}) == 3


def test_overnight_punching_and_duplicate_rules(api, database):
    database.employees.insert_one(stored_employee(shift_start="22:00", shift_end="06:00"))
    body = {"emp_code": "EMP0001", "punched_at": ms("2026-07-06T21:55:00.987"), "work_hours": 999}
    response = api.post("/attendance/punch-in", json=body)
    assert response.status_code == 201
    record = response.json()
    assert record["date"] == "2026-07-06" and record["punch_in"] == ms("2026-07-06T21:55:00")
    assert record["work_hours"] is None and record["history"] == []
    assert "id" not in record and "_id" not in record
    assert api.post("/attendance/punch-in", json=body).status_code == 409
    out = api.post(
        "/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-07T06:40:00")}
    )
    assert out.status_code == 200
    assert out.json()["work_hours"] == 8.75 and out.json()["overtime_minutes"] == 40
    assert out.json()["history"] == []
    assert (
        api.post(
            "/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-07T06:41:00")}
        ).status_code
        == 409
    )


def test_punch_out_chooses_latest_even_when_closed(api, database):
    database.employees.insert_one(stored_employee())
    database.attendance_logs.insert_many(
        [log(day="2026-07-05"), log(punch_out=main.from_ms(ms("2026-07-06T18:30:00")))]
    )
    assert (
        api.post(
            "/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-06T19:30:00")}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "time,status",
    [
        ("2026-07-06T09:29:59", 404),
        ("2026-07-06T09:30:00", 422),
        ("2026-07-07T09:30:01", 422),
        ("2026-07-07T09:30:00", 200),
    ],
)
def test_punch_out_interval_bounds(api, database, time, status):
    database.employees.insert_one(stored_employee())
    database.attendance_logs.insert_one(log())
    assert (
        api.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms(time)}).status_code
        == status
    )


def test_unknown_resources(api, database):
    for action in ("punch-in", "punch-out"):
        assert api.post("/attendance/" + action, json={"emp_code": "EMP0001"}).status_code == 404
    patch = {"status": "LEAVE", "reason": "Approved leave", "regularized_by": "HR"}
    assert api.patch("/attendance/EMP0001/2026-07-06", json=patch).status_code == 404
    database.employees.insert_one(stored_employee())
    assert api.post("/attendance/punch-out", json={"emp_code": "EMP0001"}).status_code == 404
    assert api.patch("/attendance/EMP0001/2026-07-06", json=patch).status_code == 404


def test_correction_history_recompute_clear_and_restore(api, database):
    database.employees.insert_one(stored_employee())
    original_in = ms("2026-07-06T10:05:00")
    api.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": original_in})
    api.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-06T18:30:00")})
    url = "/attendance/EMP0001/2026-07-06"
    base = {"reason": "Biometric correction", "regularized_by": "HR"}
    corrected = api.patch(url, json={**base, "punch_in": ms("2026-07-06T09:28:00.500"), "late_minutes": 900})
    assert corrected.status_code == 200
    result = corrected.json()
    assert result["late_minutes"] == 0 and result["work_hours"] == 9.03
    entry = result["history"][0]
    assert entry["changes"]["punch_in"] == {"from": original_in, "to": ms("2026-07-06T09:28:00")}
    assert set(entry["changes"]) == {"punch_in", "late_minutes", "work_hours"}
    assert isinstance(entry["at"], int) and entry["at"] % 1000 == 0
    assert api.patch(url, json={**base, "punch_in": ms("2026-07-06T09:28:00")}).status_code == 422
    assert api.patch(url, json={**base, "status": "LEAVE", "punch_in": original_in}).status_code == 422
    cleared = api.patch(url, json={**base, "status": "LEAVE"}).json()
    assert cleared["punch_in"] is None and cleared["punch_out"] is None and cleared["work_hours"] is None
    assert len(cleared["history"]) == 2 and cleared["history"][0] == entry
    assert api.patch(url, json={**base, "status": "PRESENT"}).status_code == 422
    assert (
        api.patch(url, json={**base, "status": "PRESENT", "punch_in": ms("2026-07-07T09:30:00")}).status_code
        == 422
    )
    restored = api.patch(url, json={**base, "status": "WFH", "punch_in": original_in}).json()
    assert restored["late_minutes"] == 35 and len(restored["history"]) == 3
    stored = database.attendance_logs.find_one({})
    assert stored["history"][0]["changes"]["punch_in"]["to"].tzinfo is not None


def test_list_filters_sort_and_legacy_defaults(api, database):
    database.attendance_logs.insert_many(
        [log("EMP0002"), log("EMP0001"), log("EMP0003", "2026-07-07", "LEAVE")]
    )
    page = api.get("/attendance?page_size=2").json()
    assert [(r["date"], r["emp_code"]) for r in page["items"]] == [
        ("2026-07-07", "EMP0003"),
        ("2026-07-06", "EMP0001"),
    ]
    assert page["total"] == 3
    filtered = api.get(
        "/attendance?date_from=2026-07-06&date_to=2026-07-06&status=PRESENT&page=2&page_size=1"
    ).json()
    assert filtered["total"] == 2 and filtered["items"][0]["emp_code"] == "EMP0002"
    assert filtered["items"][0]["history"] == [] and filtered["items"][0]["half_day"] is False
    assert filtered["items"][0]["late_minutes"] == 0


def run_parallel(call, workers=8):
    barrier = Barrier(workers)

    def synchronized(_):
        barrier.wait(timeout=15)
        return call().status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(synchronized, range(workers)))


def test_duplicate_create_and_punch_in_races(api, database):
    results = run_parallel(lambda: api.post("/employees", json=employee()))
    assert sorted(results) == [201] + [409] * 7
    results = run_parallel(
        lambda: api.post(
            "/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-06T09:30:00")}
        )
    )
    assert sorted(results) == [201] + [409] * 7
    assert database.attendance_logs.count_documents({}) == 1


@pytest.mark.parametrize("action", ["punch-out", "correction"])
def test_overlapping_writes_are_atomic(api, database, monkeypatch, action):
    database.employees.insert_one(stored_employee())
    database.attendance_logs.insert_one(log())
    original_update = main.update_record
    barrier = Barrier(8)

    def overlapping_update(*args, **kwargs):
        barrier.wait(timeout=20)  # Force all eight requests to read the same snapshot.
        return original_update(*args, **kwargs)

    monkeypatch.setattr(main, "update_record", overlapping_update)

    def call():
        if action == "punch-out":
            return api.post(
                "/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-06T18:30:00")}
            )
        return api.patch(
            "/attendance/EMP0001/2026-07-06",
            json={"status": "WFH", "reason": "Working remotely", "regularized_by": "HR"},
        )

    assert sorted(run_parallel(call)) == [200] + [409] * 7
    assert len(database.attendance_logs.find_one({}).get("history", [])) == (
        1 if action == "correction" else 0
    )
