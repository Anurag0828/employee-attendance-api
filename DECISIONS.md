# Design decisions

1. **Indexes.** Employees: unique `emp_code` prevents duplicates; `(department, emp_code)` serves filtered listing; `(joined_on, department)` serves monthly headcount; `(department, joined_on)` serves filtered headcount and trends. Attendance: unique `(emp_code, date descending)` protects daily identity and serves employee reports; `(date descending, emp_code)` serves ordered lists and date ranges; `(emp_code, punch_in descending)` finds the latest eligible punch; `(status, date descending, emp_code)` serves status-filtered lists. I rejected a unique email index because the contract does not require email uniqueness.

2. **Punch-in race.** Both requests read the employee and calculate the attendance day. Both attempt insertion. MongoDB's unique daily index accepts one; the other raises `DuplicateKeyError`, converted to 409. The winner returns 201. A preliminary attendance lookup cannot provide this guarantee.

3. **Ties.** `$rank` uses only total late minutes. Filtering `rank <= limit` preserves every tied employee at the cutoff. A separate final sort orders ties by employee code.

4. **Headcount.** The summary starts from eligible employees, then looks up aggregated logs. An empty lookup contributes zero metrics but still one employee.

5. **At 100x.** Introduce daily department rollups, with correction-driven invalidation and rebuilding, to reduce repeated joins while preserving the original records and audit history.
