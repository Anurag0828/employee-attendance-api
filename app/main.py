"""Employee attendance API. Run: uvicorn app.main:app --port 8000."""

import calendar
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date as CalendarDate
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from bson import Decimal128, json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import AfterValidator, BaseModel, Field, model_validator
from pymongo import IndexModel, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(override=False)
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]
Status = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
ExplainEndpoint = Literal[
    "attendance_list",
    "employee_monthly",
    "department_summary",
    "late_leaderboard",
    "department_trend",
]

# A single thread-safe pool is shared by FastAPI's synchronous worker threads.
client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    connect=False,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]


def create_indexes(database):
    database.employees.create_indexes(
        [
            IndexModel([("emp_code", 1)], unique=True, name="employee_code"),
            IndexModel([("department", 1), ("emp_code", 1)], name="department_code"),
            IndexModel([("joined_on", 1), ("department", 1)], name="joined_department"),
            IndexModel([("department", 1), ("joined_on", 1)], name="department_joined"),
        ]
    )
    database.attendance_logs.create_indexes(
        [
            IndexModel([("emp_code", 1), ("date", -1)], unique=True, name="employee_date"),
            IndexModel([("date", -1), ("emp_code", 1)], name="date_employee"),
            IndexModel([("emp_code", 1), ("punch_in", -1)], name="employee_punch"),
            IndexModel([("status", 1), ("date", -1), ("emp_code", 1)], name="status_date_employee"),
        ]
    )


@asynccontextmanager
async def lifespan(_app):
    try:
        create_indexes(db)
        yield
    finally:
        client.close()


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# Validation and conversion at the HTTP boundary.
def valid_date(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use YYYY-MM-DD")
    CalendarDate.fromisoformat(value)
    return value


def valid_month(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Use YYYY-MM")
    CalendarDate.fromisoformat(value + "-01")
    return value


DateText = Annotated[str, AfterValidator(valid_date)]
MonthText = Annotated[str, AfterValidator(valid_month)]
ShiftTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
EpochMillis = Annotated[int, Field(strict=True, ge=100000000000, le=4102444800000)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Limit = Annotated[int, Query(ge=1, le=50)]


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def epoch_ms(value: datetime) -> int:
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000


def from_ms(value: int) -> datetime:
    return EPOCH + timedelta(seconds=value // 1000)


class EmployeeIn(BaseModel):
    emp_code: Annotated[str, Field(pattern=r"^EMP[0-9]{4,6}$")]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    email: Annotated[str, Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    department: Annotated[str, Field(min_length=1, max_length=50)]
    shift_start: ShiftTime = "09:30"
    shift_end: ShiftTime = "18:30"
    joined_on: DateText

    @model_validator(mode="after")
    def distinct_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: EpochMillis = Field(default_factory=lambda: epoch_ms(now()))


class PunchInIn(PunchOutIn):
    status: PresenceStatus = "PRESENT"


class CorrectionIn(BaseModel):
    # Omitted fields retain the default; explicit null is invalid per the contract.
    status: Status = Field(default=None)
    punch_in: EpochMillis = Field(default=None)
    punch_out: EpochMillis = Field(default=None)
    reason: Annotated[str, Field(min_length=5, max_length=200)]
    regularized_by: Annotated[str, Field(min_length=1, max_length=50)]


def invalid(message: str, field: str, location: str = "body"):
    raise HTTPException(
        422,
        detail=[
            {
                "loc": [location, field],
                "msg": message,
                "type": "value_error",
            }
        ],
    )


def check_range(start: str | None, end: str | None, max_days: int | None = None):
    if start is not None and end is not None:
        if start > end:
            invalid("End date must be on or after start date", "to" if max_days else "date_to", "query")
        if (
            max_days
            and (CalendarDate.fromisoformat(end) - CalendarDate.fromisoformat(start)).days + 1 > max_days
        ):
            invalid(f"Range must not exceed {max_days} days", "to", "query")


def require_employee(emp_code: str):
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


# Business rules use aware UTC instants and IST calendar boundaries.
def attendance_date(punch_in: datetime, employee: dict) -> str:
    local = punch_in.astimezone(IST).replace(microsecond=0)
    day = local.date()
    if employee["shift_end"] <= employee["shift_start"] and local.strftime("%H:%M") < employee["shift_end"]:
        day -= timedelta(days=1)
    return day.isoformat()


def shift_bounds(day: str, employee: dict) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(f"{day}T{employee['shift_start']}:00").replace(tzinfo=IST)
    end = datetime.fromisoformat(f"{day}T{employee['shift_end']}:00").replace(tzinfo=IST)
    if employee["shift_end"] <= employee["shift_start"]:
        end += timedelta(days=1)
    return start, end


def compute_late_minutes(punch_in: datetime, shift_start: datetime) -> int:
    seconds = int((punch_in - shift_start).total_seconds())
    return seconds // 60 if seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    return float((Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: datetime) -> int:
    minutes = int((punch_out - shift_end).total_seconds()) // 60
    return minutes if minutes >= 30 else 0


DEFAULTS = {
    "punch_in": None,
    "punch_out": None,
    "work_hours": None,
    "late_minutes": 0,
    "overtime_minutes": 0,
    "half_day": False,
}
MUTABLE = ("status", *DEFAULTS)
EMPLOYEE_FIELDS = (
    "emp_code",
    "name",
    "email",
    "department",
    "shift_start",
    "shift_end",
    "joined_on",
    "created_at",
)


def derived_fields(record: dict, employee: dict) -> dict:
    result = dict(DEFAULTS)
    if record["status"] not in PRESENCE:
        return result
    punch_in, punch_out = record.get("punch_in"), record.get("punch_out")
    if punch_in is None:
        invalid("Presence requires punch_in", "punch_in")
    punch_in = punch_in.replace(microsecond=0)
    if attendance_date(punch_in, employee) != record["date"]:
        invalid("punch_in must belong to the addressed attendance date", "punch_in")
    start, end = shift_bounds(record["date"], employee)
    result.update(punch_in=punch_in, late_minutes=compute_late_minutes(punch_in, start))
    if punch_out is not None:
        punch_out = punch_out.replace(microsecond=0)
        if not 0 < (punch_out - punch_in).total_seconds() <= 86400:
            invalid("Punch-out must be after punch-in and within 24 hours", "punch_out")
        hours = compute_work_hours(punch_in, punch_out)
        result.update(
            punch_out=punch_out,
            work_hours=hours,
            half_day=hours < 4.50,
            overtime_minutes=compute_overtime(punch_out, end),
        )
    return result


def api_values(value: Any):
    """Convert nested audit dates too; never modify the database document."""
    if isinstance(value, datetime):
        return epoch_ms(value)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, dict):
        return {key: api_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [api_values(item) for item in value]
    return value


def employee_response(doc: dict):
    return api_values({key: doc[key] for key in EMPLOYEE_FIELDS})


def attendance_response(doc: dict):
    result = {key: doc[key] for key in ("emp_code", "date", "status")}
    result.update({key: doc.get(key, default) for key, default in DEFAULTS.items()})
    result["history"] = doc.get("history", [])
    return api_values(result)


def snapshot_filter(record: dict) -> dict:
    """Compare-and-set: reject stale writes without adding fields to seeded data."""
    query = {"emp_code": record["emp_code"], "date": record["date"]}
    for key in MUTABLE:
        query[key] = {"$eq": record[key], "$exists": True} if key in record else {"$exists": False}
    # History length detects a correction that changed fields and changed them back.
    query["$expr"] = {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, len(record.get("history", []))]}
    return query


def update_record(original: dict, values: dict, history: dict | None = None):
    update = {"$set": values}
    if history is not None:
        update["$push"] = {"history": history}
    result = db.attendance_logs.find_one_and_update(
        snapshot_filter(original),
        update,
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(409, "Record changed concurrently; read it again before retrying")
    return attendance_response(result)


# Normal endpoints and executionStats explanations share these query builders.
@dataclass
class QueryPlan:
    collection: str
    hint: str
    pipeline: list | None = None
    filter: dict | None = None
    sort: dict | None = None
    skip: int = 0
    limit: int = 20

    def run(self):
        collection = db[self.collection]
        if self.pipeline is not None:
            return list(collection.aggregate(self.pipeline, hint=self.hint))
        return list(
            collection.find(self.filter, {"_id": 0})
            .hint(self.hint)
            .sort(list(self.sort.items()))
            .skip(self.skip)
            .limit(self.limit)
        )

    def explain(self):
        if self.pipeline is not None:
            command = {
                "aggregate": self.collection,
                "pipeline": self.pipeline,
                "cursor": {},
                "hint": self.hint,
            }
        else:
            command = {
                "find": self.collection,
                "filter": self.filter,
                "projection": {"_id": 0},
                "sort": self.sort,
                "skip": self.skip,
                "limit": self.limit,
                "hint": self.hint,
            }
        return json.loads(json_util.dumps(db.command("explain", command, verbosity="executionStats")))


def attendance_plan(emp_code, date_from, date_to, status, page, page_size):
    check_range(date_from, date_to)
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        query["date"] = {}
        if date_from is not None:
            query["date"]["$gte"] = date_from
        if date_to is not None:
            query["date"]["$lte"] = date_to
    if status is not None:
        query["status"] = status
    hint = (
        "employee_date"
        if emp_code is not None
        else "status_date_employee"
        if status is not None
        else "date_employee"
    )
    return QueryPlan(
        "attendance_logs",
        hint,
        filter=query,
        sort={"date": -1, "emp_code": 1},
        skip=(page - 1) * page_size,
        limit=page_size,
    )


def month_bounds(month: str) -> tuple[str, str]:
    year, number = map(int, month.split("-"))
    return month + "-01", f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def mongo_date(value):
    return {"$dateFromString": {"dateString": value, "timezone": "+05:30"}}


def working_day(value):
    return {"$in": [{"$dayOfWeek": {"date": value, "timezone": "+05:30"}}, [2, 3, 4, 5, 6]]}


def half_up(value, places=2):
    # Nonnegative metrics: decimal arithmetic avoids binary half-tie drift.
    # MongoDB $round uses ties-to-even, not the required half-up.
    scale = Decimal128(str(10**places))
    return {
        "$divide": [
            {"$floor": {"$add": [{"$multiply": [{"$toDecimal": value}, scale]}, Decimal128("0.5")]}},
            scale,
        ]
    }


def count_if(condition):
    return {"$sum": {"$cond": [condition, 1, 0]}}


IS_PRESENT = {"$in": ["$status", PRESENCE]}
PRESENT_WEIGHT = {"$cond": [IS_PRESENT, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}
IS_LATE = {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}
STATS_FIELDS = (
    "present_days",
    "leave_days",
    "late_count",
    "total_late_minutes",
    "total_overtime_minutes",
    "on_duty_count",
    "hours_sum",
    "hours_count",
)


def stats_lookup(start, end):
    """Aggregate each employee's logs into one row inside MongoDB."""
    has_hours = {"$and": [IS_PRESENT, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}
    return {
        "$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "as": "stats",
            "pipeline": [
                {"$match": {"date": {"$gte": start, "$lte": end}, "$expr": {"$eq": ["$emp_code", "$$code"]}}},
                {
                    "$group": {
                        "_id": None,
                        "present_days": {
                            "$sum": {"$cond": [working_day(mongo_date("$date")), PRESENT_WEIGHT, 0]}
                        },
                        "leave_days": count_if({"$eq": ["$status", "LEAVE"]}),
                        "late_count": count_if(IS_LATE),
                        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
                        "on_duty_count": count_if({"$eq": ["$status", "ON_DUTY"]}),
                        "hours_sum": {
                            "$sum": {"$cond": [has_hours, {"$toDecimal": "$work_hours"}, Decimal128("0")]}
                        },
                        "hours_count": count_if(has_hours),
                    }
                },
            ],
        }
    }


def flatten_stats():
    return {
        "$set": {name: {"$ifNull": [{"$arrayElemAt": [f"$stats.{name}", 0]}, 0]} for name in STATS_FIELDS}
    }


def monthly_plan(emp_code, month):
    start, end = month_bounds(month)
    first = {"$max": ["$joined_on", start]}
    # At most 31 days, generated in MongoDB even for employees with no logs.
    days = {
        "$range": [
            0,
            {
                "$max": [
                    0,
                    {
                        "$add": [
                            1,
                            {
                                "$dateDiff": {
                                    "startDate": mongo_date(first),
                                    "endDate": mongo_date(end),
                                    "unit": "day",
                                }
                            },
                        ]
                    },
                ]
            },
        ]
    }
    workdays = {
        "$size": {
            "$filter": {
                "input": days,
                "as": "offset",
                "cond": working_day(
                    {
                        "$dateAdd": {
                            "startDate": mongo_date(first),
                            "unit": "day",
                            "amount": "$$offset",
                        }
                    }
                ),
            }
        }
    }
    return QueryPlan(
        "employees",
        "employee_code",
        pipeline=[
            {"$match": {"emp_code": emp_code}},
            stats_lookup(start, end),
            flatten_stats(),
            {"$set": {"working_days": workdays}},
            {
                "$project": {
                    "_id": 0,
                    "emp_code": 1,
                    "month": {"$literal": month},
                    "working_days": 1,
                    "present_days": 1,
                    "leave_days": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "total_overtime_minutes": 1,
                    "attendance_pct": {
                        "$cond": [
                            {"$gt": ["$working_days", 0]},
                            half_up(
                                {
                                    "$divide": [
                                        {"$multiply": ["$present_days", Decimal128("100")]},
                                        "$working_days",
                                    ]
                                }
                            ),
                            None,
                        ]
                    },
                }
            },
        ],
    )


def department_plan(month, department):
    start, end = month_bounds(month)
    query = {"joined_on": {"$lte": end}}
    if department is not None:
        query["department"] = department
    return QueryPlan(
        "employees",
        "department_joined" if department is not None else "joined_department",
        pipeline=[
            {"$match": query},
            stats_lookup(start, end),
            flatten_stats(),
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$sum": 1},
                    **{name: {"$sum": f"${name}"} for name in STATS_FIELDS},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$hours_count", 0]},
                            half_up({"$divide": ["$hours_sum", "$hours_count"]}),
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": "$leave_days",
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ],
    )


def leaderboard_plan(month, department, limit):
    start, end = month_bounds(month)
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "late_count": count_if(IS_LATE),
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
                "rank": 1,
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ]
    return QueryPlan("attendance_logs", "date_employee", pipeline=pipeline)


def trend_plan(department, start, end):
    check_range(start, end, 92)
    count = (CalendarDate.fromisoformat(end) - CalendarDate.fromisoformat(start)).days + 1
    # Read each department employee's bounded date range once, then reduce to
    # at most 92 daily totals. This avoids joining an employee for every log.
    # Preserve empty lookups so known departments with no logs still get days.
    return QueryPlan(
        "employees",
        "department_joined",
        pipeline=[
            {"$match": {"department": department}},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "as": "daily",
                    "pipeline": [
                        {
                            "$match": {
                                "date": {"$gte": start, "$lte": end},
                                "$expr": {"$eq": ["$emp_code", "$$code"]},
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "date": 1,
                                "present_count": PRESENT_WEIGHT,
                                "late_count": {"$cond": [IS_LATE, 1, 0]},
                            }
                        },
                    ],
                }
            },
            {"$unwind": {"path": "$daily", "preserveNullAndEmptyArrays": True}},
            {
                "$group": {
                    "_id": "$daily.date",
                    "present_count": {"$sum": "$daily.present_count"},
                    "late_count": {"$sum": "$daily.late_count"},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "stats": {
                        "$push": {
                            "date": "$_id",
                            "present_count": "$present_count",
                            "late_count": "$late_count",
                        }
                    },
                }
            },
            {"$project": {"_id": 0, "stats": 1, "offset": {"$range": [0, count]}}},
            {"$unwind": "$offset"},
            {
                "$set": {
                    "day": {"$dateAdd": {"startDate": mongo_date(start), "unit": "day", "amount": "$offset"}}
                }
            },
            {
                "$set": {
                    "date": {"$dateToString": {"date": "$day", "format": "%Y-%m-%d", "timezone": "+05:30"}},
                    "is_working_day": working_day("$day"),
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$date"},
                    "as": "people",
                    "pipeline": [
                        {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},
                        {"$count": "count"},
                    ],
                }
            },
            {
                "$set": {
                    "daily": {
                        "$arrayElemAt": [
                            {
                                "$filter": {
                                    "input": "$stats",
                                    "as": "row",
                                    "cond": {"$eq": ["$$row.date", "$date"]},
                                }
                            },
                            0,
                        ]
                    }
                }
            },
            {
                "$set": {
                    "headcount": {"$ifNull": [{"$arrayElemAt": ["$people.count", 0]}, 0]},
                    "present_count": {"$ifNull": ["$daily.present_count", 0]},
                    "late_count": {"$ifNull": ["$daily.late_count", 0]},
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                            half_up({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4),
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}},
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": 1,
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": {
                        "$cond": [{"$ne": ["$moving_avg_7d", None]}, half_up("$moving_avg_7d", 4), None]
                    },
                }
            },
            {"$sort": {"date": 1}},
        ],
    )


# HTTP handlers: transport, validation, and response formatting.
@app.get("/health")
def health():
    try:
        db.command("ping")
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable") from None
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = now()
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists") from None
    return employee_response(doc)


@app.get("/employees")
def list_employees(department: str | None = None, page: Page = 1, page_size: PageSize = 20):
    query = {"department": department} if department is not None else {}
    hint = "department_code" if department is not None else "employee_code"
    items = (
        db.employees.find(query, {"_id": 0})
        .hint(hint)
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [employee_response(doc) for doc in items],
        "total": db.employees.count_documents(query, hint=hint),
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = require_employee(body.emp_code)
    instant = from_ms(body.punched_at)
    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date(instant, employee),
        "status": body.status,
        "punch_in": instant,
        "history": [],
    }
    doc.update(derived_fields(doc, employee))
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Attendance already exists for this date") from None
    return attendance_response(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = require_employee(body.emp_code)
    instant = from_ms(body.punched_at)
    original = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$type": "date", "$lte": instant}},
        sort=[("punch_in", -1)],
        hint="employee_punch",
    )
    if original is None:
        raise HTTPException(404, "No punch-in found at or before this time")
    if original.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    values = derived_fields({**original, "punch_out": instant}, employee)
    return update_record(original, values)


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: DateText | None = None,
    date_to: DateText | None = None,
    status: Status | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    plan = attendance_plan(emp_code, date_from, date_to, status, page, page_size)
    return {
        "items": [attendance_response(doc) for doc in plan.run()],
        "total": db.attendance_logs.count_documents(plan.filter, hint=plan.hint),
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: DateText, body: CorrectionIn):
    employee = require_employee(emp_code)
    original = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if original is None:
        raise HTTPException(404, "Attendance record not found")
    changed = body.model_fields_set
    final = {**DEFAULTS, **original}
    if "status" in changed:
        final["status"] = body.status
    if final["status"] not in PRESENCE and changed & {"punch_in", "punch_out"}:
        invalid("ABSENT and LEAVE cannot include punch times", "status")
    for key in ("punch_in", "punch_out"):
        if key in changed:
            final[key] = from_ms(getattr(body, key))
    values = {"status": final["status"], **derived_fields(final, employee)}
    changes = {
        key: {"from": original.get(key, DEFAULTS.get(key)), "to": value}
        for key, value in values.items()
        if original.get(key, DEFAULTS.get(key)) != value
    }
    if not changes:
        invalid("Correction must change at least one stored value", "reason")
    entry = {"at": now(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    return update_record(original, values, entry)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: MonthText):
    rows = monthly_plan(emp_code, month).run()
    if not rows:
        raise HTTPException(404, "Employee not found")
    return api_values(rows[0])


@app.get("/analytics/departments/summary")
def department_summary(month: MonthText, department: str | None = None):
    return {"month": month, "items": api_values(department_plan(month, department).run())}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: MonthText, department: str | None = None, limit: Limit = 10):
    return {"month": month, "items": api_values(leaderboard_plan(month, department, limit).run())}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    start: Annotated[DateText, Query(alias="from")],
    end: Annotated[DateText, Query(alias="to")],
):
    rows = trend_plan(department, start, end).run()
    if not rows:
        raise HTTPException(404, "Department not found")
    return {"department": department, "items": api_values(rows)}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: ExplainEndpoint,
    emp_code: str | None = None,
    month: MonthText | None = None,
    department: str | None = None,
    limit: Limit = 10,
    date_from: DateText | None = None,
    date_to: DateText | None = None,
    status: Status | None = None,
    start: Annotated[DateText | None, Query(alias="from")] = None,
    end: Annotated[DateText | None, Query(alias="to")] = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    required = {
        "attendance_list": {},
        "employee_monthly": {"emp_code": emp_code, "month": month},
        "department_summary": {"month": month},
        "late_leaderboard": {"month": month},
        "department_trend": {"department": department, "from": start, "to": end},
    }
    for name, value in required[endpoint].items():
        if value is None:
            invalid("Field required", name, "query")
    if endpoint == "attendance_list":
        plan = attendance_plan(emp_code, date_from, date_to, status, page, page_size)
    elif endpoint == "employee_monthly":
        plan = monthly_plan(emp_code, month)
    elif endpoint == "department_summary":
        plan = department_plan(month, department)
    elif endpoint == "late_leaderboard":
        plan = leaderboard_plan(month, department, limit)
    else:
        plan = trend_plan(department, start, end)
    return {"endpoint": endpoint, "collection": plan.collection, "explain": plan.explain()}
