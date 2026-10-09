# Verification results

Run on 9 October 2026, Windows, Python 3.12.14.

- MongoDB Community 7.0.41: the final complete suite passed all 100 tests in
  21.53 seconds, including the opt-in scale test and a real uvicorn startup check.
- MongoDB Atlas 8.0.34: 98 functional tests passed and the separate startup test
  passed before the final trend optimization. Atlas's intermittent network
  availability prevented its scale run; scale verification used MongoDB 7 locally.
  The computer's public IP changed during the session, invalidating its Atlas
  access-list entry. Update that entry when your network address changes.
- Ruff lint and formatting checks passed. `pip check` found no broken requirements.
- Every explain target showed `IXSCAN`, no `COLLSCAN`, and zero reported lookup
  collection scans on the 100,000-record dataset.

The local scale fixture contains 1,000 employees and 100 attendance dates each.
These are observed single-request timings, not throughput claims or service guarantees:

| Check | Observed time |
|---|---:|
| Create all indexes on pre-existing 100,000 records | 1.632 s |
| Date-filtered attendance page | 0.041 s |
| Employee-filtered attendance page | 0.012 s |
| Status-filtered attendance page | 0.098 s |
| Employee monthly summary | 0.014 s |
| All-department summary | 1.232 s |
| One-department summary | 0.603 s |
| Late leaderboard, including 1,000 tied results | 0.466 s |
| Department trend, 31 calendar days | 0.599 s |
| Separate real uvicorn readiness check on local MongoDB | 2.880 s |

The first trend implementation took 7.444 seconds because it joined employee
information for each attendance row. The final implementation reads bounded logs
once per department employee, groups into daily totals, then fills the calendar.
It avoids that repeated join and keeps the intermediate daily array bounded by
the 92-day request limit (plus one empty group).

The installed Starlette test client emits one deprecation warning for its httpx
adapter. It does not affect application runtime or the passing assertions.

All databases created by the successful automated test sessions were removed by
the test fixture. Those tests did not seed, clear, or replace application records.

## Folder organization verification

After organizing the workspace on 9 October 2026:

- Application code and the assignment's required entry point were unchanged.
- 71 rule and real-server startup tests passed in 1.47 seconds on local MongoDB.
  The existing Starlette deprecation warning remained the only warning.
- Ruff lint and formatting passed; all 15 relative documentation links resolved.
- The running Atlas-backed API returned 200 for health, employee and attendance
  listing, and all four analytics reports.
- The fictional manual demo still contained four employees and five attendance
  records. The separate local demo data was also verified after its runtime moved
  into the ignored `.local/` directory.

These checks verify the folder reorganization. The full-suite and scale timings
above are from the earlier implementation verification, not a new scale run.
