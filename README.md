# Employee Attendance & Analytics API

FastAPI + MongoDB backend for employee attendance, audited corrections, and monthly,
department, lateness, and daily trend reports. All application logic is in `app/main.py`.

Requires Python 3.11+ and MongoDB 6.0+. Create a virtual environment, activate it, then:

```shell
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Set `MONGO_URI` and `MONGO_DB` in your environment, or copy `.env.example` to a local
`.env` and fill them in. Environment variables take precedence. Defaults are
`mongodb://localhost:27017` and `attendance_db`. For Atlas, allow your current IP
and use a database user with permission to read/write the database and create indexes.
Percent-encode special characters in connection-string credentials.

Open [interactive API docs](http://127.0.0.1:8000/docs).
`GET /health` checks MongoDB. Indexes are created automatically at startup;
existing data is preserved and no seed script is required.

Instants are integer epoch milliseconds; calendar days and shifts use IST.
Duplicate writes are protected by unique indexes and conditional atomic updates.
Analytics and daily gap filling run in MongoDB; explain endpoints reuse the real queries.

All 12 required endpoints are implemented. No authentication is included, as scoped
by the assignment; `regularized_by` is a supplied audit label. Setup secrets are ignored
by Git. See [REVIEW.md](REVIEW.md), [DECISIONS.md](DECISIONS.md), and the
[walkthrough](docs/WALKTHROUGH.md) for design and demonstration notes.

Optional test code and dependencies are entirely under `tests/`;
see the [testing guide](docs/TESTING.md) and [recorded results](docs/TEST_RESULTS.md).
The app runs without that folder. Tests use a newly generated disposable database;
the 100,000-record check requires `--run-scale`.

## Project layout

```text
candidate_kit/             # Repository root; run commands here
  app/                    # Application package
    main.py               # All application logic, as required by the assignment
  docs/                   # Walkthrough, testing guide, and verification results
  tests/                  # Optional automated tests and manual demo helper
  .env.example            # Configuration template without credentials
  .gitignore              # Excludes secrets, local tools, and generated files
  pyproject.toml          # Pytest and Ruff configuration
  requirements.txt        # Application dependencies
  README.md               # Setup and navigation
  REVIEW.md               # Required starter-code review
  DECISIONS.md            # Required design answers
```

The assignment requires `app/main.py` and the three submission documents above
in these locations. Supporting documentation is collected in [docs/](docs/README.md).
Local `.env`, `.venv/`, and `.local/` are excluded from Git.
