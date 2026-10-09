# Optional tests

All test code and test-only dependencies live in `tests/`. The application does not
import anything from that directory; you can remove it after reviewing it.
This guide and the [recorded results](TEST_RESULTS.md) live in `docs/`.

From the repository root, using Python 3.11+:

```shell
python -m pip install -r tests/requirements.txt
python -m pytest tests -m "not integration" -q
```

For real database tests, set `TEST_MONGO_URI` to a MongoDB 6.0+ connection string.
It defaults to `mongodb://127.0.0.1:27017`. To deliberately reuse the server from
your application's `MONGO_URI` or local `.env`, pass `--use-app-mongo`:

```shell
python -m pytest tests --use-app-mongo -q
python -m pytest tests --use-app-mongo --run-scale -q -s
python -m ruff check app tests
python -m ruff format --check app tests
```

The suite creates a fresh `attendance_test_<random>` database and drops only that
database afterward. It never uses `MONGO_DB` for its test data. Credentials need
permission to create and drop this disposable database. An unavailable database
fails integration tests instead of silently pretending that they passed.

Files:

- `test_rules.py`: timestamps, validation, rounding, overnight shifts, health errors.
- `test_api.py`: CRUD, paging, audit entries, conflicts, and forced concurrent writes.
- `test_analytics.py`: hand-calculated report cases and real explain plans.
- `test_reference.py`: randomized stored data compared with an independent Python oracle.
- `test_scale.py`: 100,000 generated records, index build timing, and indexed queries.
- `test_startup.py`: starts real uvicorn, checks readiness and environment precedence.
- `conftest.py`: shared fixtures, disposable database setup, and test options.
- `manual_demo.py`: optional HTTP demonstration using fictional employees.

The scale test is opt-in because it creates 100,000 temporary records and builds
indexes on them. No sample-data dump is checked into this repository.

## Manual demonstration

With the application already running on `http://127.0.0.1:8000`, the following
command adds four fictional employees and five attendance records through the API:

```shell
python tests/manual_demo.py
```

This helper writes to the database used by the running application. Unlike the
automated tests, it does not create a disposable database or remove its records.
Use a demo database with unused employee codes `EMP9001` through `EMP9004`.
The scenarios cover lateness, a half-day, an overnight shift, and an audited
correction, followed by report requests. It is not run by pytest.
