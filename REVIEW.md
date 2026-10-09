# Starter review

Locations below refer to the original starter's function names. Reproductions use
a 09:30–18:30 IST shift unless stated otherwise.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How it is fixed |
|---|---|---|---|---|
| 1 | MongoClient setup | Default reads return naive UTC datetimes; helpers assume local wall time. | A stored 04:10 UTC punch can be compared with 09:30 in the wrong zone. | Enable `tz_aware`, store UTC, and construct explicit IST shift boundaries. |
| 2 | Application setup | No indexes are created. | A seeded database has only `_id`; list/report queries scan data and duplicates are possible. | Idempotent startup indexes, including both business-key uniqueness guarantees. |
| 3 | `compute_late_minutes` | Replacing the punch's hour does not anchor an IST shift correctly. | A UTC timestamp is compared against 09:30 UTC instead of IST. | Pass the aware shift start calculated from the attendance date. |
| 4 | `compute_late_minutes` | Flooring before checking grace loses seconds at the boundary. | 09:40:01 returns 0 instead of 10 minutes late. | Compare seconds with 600 first, then floor elapsed minutes. |
| 5 | `compute_late_minutes` | An overnight post-midnight punch uses the wrong shift-start day. | 01:00 for a 22:00–06:00 shift should be 180 minutes late for the previous date. | Compute attendance date first and build the shift on that date. |
| 6 | `compute_work_hours` | Binary float and Python `round` do not implement decimal half-up. | 4h 30m 18s should be 4.51 hours, not 4.50. | Divide integer seconds using `Decimal` and `ROUND_HALF_UP`. |
| 7 | `compute_overtime` | Counts overtime below the 30-minute minimum. | 18:35 returns 5 instead of 0. | Floor minutes and require at least 30. |
| 8 | `compute_overtime` | Always puts shift end on the attendance date. | Overnight punch-out at 06:40 gives 1480 instead of 40 minutes. | Move the end to the next day for overnight shifts. |
| 9 | `EmployeeIn` | Missing code, length, email, date, and shift validation. | `EMP1`, empty names, invalid email, `24:00`, or February 30 are accepted. | Constrained fields plus real calendar validation. |
| 10 | `EmployeeIn` | Allows identical start/end for newly created employees. | Posting 09:30–09:30 succeeds. | Model validator rejects equality; legacy equal-time overnight records remain readable. |
| 11 | `PunchInIn` | Timestamp allows coercion, explicit null, seconds-looking values, and out-of-range values. | A numeric string or Unix seconds is accepted. | Strict bounded integer epoch milliseconds; omission alone defaults to now. |
| 12 | `PunchInIn` | Status is unconstrained. | `ABSENT`, `LEAVE`, or an arbitrary string can create a punched-in record. | Limit punch-in to the three presence statuses. |
| 13 | `health` | Reports success without contacting MongoDB. | Stop MongoDB and health still returns 200. | Ping and return 503 on database failure. |
| 14 | `create_employee` | Check-then-insert is racy. | Concurrent requests can create duplicate employee codes. | Unique index; map `DuplicateKeyError` to 409. |
| 15 | `create_employee` | `datetime.now()` is naive local time and retains microseconds. | Non-UTC hosts store the wrong instant; created time is not second-truncated. | Use aware UTC server time truncated to seconds. |
| 16 | Employee and attendance responses | Datetimes are returned as ISO strings rather than integer milliseconds. | Response timestamps have the wrong JSON type, including nested audit changes. | Explicit recursive conversion at the response boundary. |
| 17 | `list_employees` | Uses `page * page_size`. | Page 1 skips the first 20 employees. | Use `(page - 1) * page_size`. |
| 18 | `list_employees` | Counts the whole collection even with a department filter. | A department with two employees reports the global total. | Apply the same filter to the count. |
| 19 | `list_employees` | No stable employee-code ordering. | Insertion order produces incorrect pages. | Indexed ascending `emp_code` sort before pagination. |
| 20 | Both list handlers | Pagination bounds are unchecked. | Page 0, negative sizes, or size 1000 are accepted. | Validate page >= 1 and size 1–100. |
| 21 | Filter construction | Truthiness treats an empty supplied filter as absent. | `department=` unexpectedly lists every employee. | Test `is not None` for exact supplied values. |
| 22 | `punch_in` | Unknown employee is dereferenced. | An unknown code causes a 500. | Return 404 before building the attendance record. |
| 23 | `punch_in` | `fromtimestamp` uses host-local time and keeps fractional milliseconds. | A punch near IST midnight gets the wrong date on a UTC host; .900 seconds affects rules. | Convert epoch to UTC and truncate before every calculation. |
| 24 | `punch_in` | Overnight attendance-date assignment is missing. | A 01:00 punch for 22:00–06:00 is stored against the following date. | Pre-end overnight punches belong to the previous attendance day. |
| 25 | `punch_in` | Daily duplicate check races. | Simultaneous identical requests can both return 201. | Unique `(emp_code, date)` index; duplicate insertion returns 409. |
| 26 | `punch_in`, `list_attendance` | Exposes a derived `id` from MongoDB `_id`. | Attendance responses contain an uncontracted identity field. | Serialize only public fields and use the natural key throughout. |
| 27 | `list_attendance` | Reads every matching document and sorts/slices in Python. | Request memory and latency grow with 100,000 records. | Indexed database sort, skip, limit, and separate filtered count. |
| 28 | `list_attendance` | Sorts only by date. | Same-day records arrive in an unstable employee order. | Sort by date descending, then employee code ascending. |
| 29 | `list_attendance` | Status/date/range validation is absent. | Invalid dates or reversed ranges return empty results rather than 422. | Validate status, calendar strings, and inclusive range order. |
| 30 | `list_attendance` | Legacy fields are not normalized. | A seeded record without history or half-day omits required response fields. | Defaults: empty history, false half-day, zero minutes, null optional times/hours. |

The seven unimplemented routes were also added: punch-out, corrections, four
analytics endpoints, and explain. These were explicit TODOs rather than hidden
defects. Correction changes and their audit entry are one conditional atomic write;
stale writes return 409. Punch-out does not add manual-correction history.

Items reviewed and retained:

- `load_dotenv()` respects existing environment variables; the implementation makes
  `override=False` explicit. Environment-based configuration was already appropriate.
- Customer-generated `emp_code`, string calendar dates, and string shift times are
  intentional model choices. MongoDB `_id` remains internal.
- The initial punch-in defaults (open punch-out, null hours, zero overtime, false
  half-day, empty history) were correct.
- Derived input fields are ignored by Pydantic's default extra-field behavior, as required.
- `PRESENT` as the default punch-in status and the default shift hours were correct.
- Synchronous PyMongo is appropriate with synchronous FastAPI handlers, which run
  in worker threads. The connection pool can be shared across those threads.
- Email uniqueness is not required, so no additional business restriction is introduced.
