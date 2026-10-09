# Project documentation

Start with the [project README](../README.md) for installation and the run command.

| Document | What it explains |
|---|---|
| [Walkthrough](WALKTHROUGH.md) | Request flow, demonstration, and interview discussion points |
| [Testing guide](TESTING.md) | Automated checks, test database isolation, and the optional manual demo |
| [Verification results](TEST_RESULTS.md) | Recorded test outcomes, measured timings, and their limits |
| [Starter review](../REVIEW.md) | Defects found in the provided code and their fixes |
| [Design decisions](../DECISIONS.md) | Indexes, concurrency, ranking, headcount, and future scaling |

## Where to read the code

All application code stays in [app/main.py](../app/main.py), as required by the
assignment. Its internal sections separate the responsibilities:

1. Configuration, shared MongoDB client, indexes, and application lifespan.
2. Pydantic request models and validation helpers.
3. IST attendance dates, shift boundaries, and calculated attendance fields.
4. Response conversion and conditional atomic updates with correction history.
5. Shared query plans and MongoDB aggregation builders.
6. HTTP endpoints that connect requests to those helpers.

For a first code walkthrough, follow `EmployeeIn` to `create_employee`, then
`PunchInIn` to `punch_in` and `derived_fields`. After that, read `punch_out`,
`regularize`, and the four analytics query builders. Tests are separate in
[tests/](../tests/) and are not imported by the application.

## Required submission layout

`README.md`, `REVIEW.md`, `DECISIONS.md`, and `requirements.txt` remain at the
repository root so the submitted project matches the assignment. Supporting
guides live in this directory. The app still starts from the repository root with
`uvicorn app.main:app --port 8000`.

## Local reference and runtime folders

The development workspace also contains the following ignored folders. They are
not part of a Git clone and are not needed to run the submitted application:

- `docs/assignment/`: original problem statement, OpenAPI contract, data model,
  sample files, sample loader, and an untouched copy of the starter application.
  The supplied sample loader replaces collection contents; it is retained only
  as reference and is not part of application startup or the test suite.
- `docs/planning/`: the earlier implementation plan PDF, retained as planning history.
  The walkthrough and current source describe the implemented system.
- `.local/`: local development runtime, database files, and service logs.
- `.venv/`: installed Python environment; application dependencies are declared
  in the root requirements file and test dependencies in `tests/requirements.txt`.

MongoDB credentials stay in the local `.env` file or environment variables.
