# Optional tests

All test code and test-only dependencies live here. The application does not import
anything from this directory; you can remove it after reviewing it.

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

The scale test is opt-in because it creates 100,000 temporary records and builds
indexes on them. No sample-data dump is checked into this repository.
