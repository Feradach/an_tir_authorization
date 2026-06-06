# Authorization Validity Interval Population Plan

## Goal

Populate `AuthorizationValidityInterval` rows so the KAO/KEAO paper-entry tool can answer:

> Was this authorization valid on the paperwork date?

The interval table is validity-window history. The existing `Authorization` table remains the current/latest authorization record.

## Policy Rules

- A validity check passes only when `start_date <= paperwork_date <= end_date`.
- A lapse of any length creates a new interval when validity resumes.
- No override should permit an illegal paperwork date. If paperwork falls in a gap, the officer must enter a defensible corrected date or send the paperwork back.
- Membership and background-check updates resume lapsed validity on the date the data is entered into the system.
- Officer-entered corrections should be traceable with `source`, `created_by`, and `note`.
- For initial population, use the real stored `authorization.expiration` to calculate the interval start date, then use the calculated/effective expiration as the interval end date. This preserves the estimated authorization start while still reflecting membership/background-check limits.

## Duration Rule

For back-calculating interval starts:

- Adult/standard authorizations: `start_date = expiration - 4 years`
- Minor/youth authorizations and youth combat marshal authorizations: `start_date = expiration - 2 years`

The test-data population uses this same duration rule so local and staging history behaves like the production backfill.

## Part 1: Test Data Population

Use this for local development and staging servers.

There is no legacy history for test/staging data, so create one interval per existing authorization.

Input:

- current `authorizations_authorization` rows in the target local/staging database

Process:

1. Apply migration `0037_authorizationvalidityinterval`.
2. Delete any existing test/staging interval rows if this is a rebuild.
3. For each authorization:
   - set `start_date = authorization.expiration - 4 years`, except youth/minor/youth-combat-marshal duration rows use `authorization.expiration - 2 years`
   - set `end_date = authorization.effective_expiration`
   - set `source='manual_repair'` or a dedicated test-data source if one is added later
   - set `note='Generated for local/staging test data; no legacy history available.'`
4. Skip rows where expiration is missing, where a start/end date cannot be calculated, or where effective expiration is before the calculated start date.
5. Report skipped rows.

Validation:

- interval count equals count of authorizations with usable expiration
- no interval has `start_date > end_date`
- spot-check adult authorization, youth authorization, and youth marshal authorization rows

Implementation:

- Command: `python manage.py populate_test_validity_intervals`
- Dry run is the default.
- Write mode: `python manage.py populate_test_validity_intervals --write`
- Rebuild mode for local/staging only: `python manage.py populate_test_validity_intervals --write --replace`

Corrected local result on 2026-06-05:

- authorizations considered: 14,299
- intervals created: 13,723
- two-year intervals: 373
- four-year intervals: 13,350
- skipped rows: 576 because effective expiration was before the calculated start date
- post-write verification: 576 authorizations without an interval, 0 authorizations with multiple intervals

## Part 2: Legacy Data Population

Use the legacy snapshot database `test_antir_auth_local`, taken on `2025-05-09`.

Input:

- real authorization rows from `test_antir_auth_local`

Process:

1. Read each real legacy authorization.
2. Match it to the corresponding restored/current `Authorization` row when possible.
3. Compute:
   - `end_date` from the legacy authorization expiration date
   - `start_date = end_date - 4 years`, except minors and youth combat marshal authorizations use `end_date - 2 years`
4. Create intervals with:
   - `source='legacy_import'`
   - note identifying the legacy snapshot database and whether the duration was four-year or two-year
5. Do not silently write ambiguous matches.
6. Put ambiguous or unmatched rows into a review report.

Validation:

- total legacy authorization rows considered
- total intervals created
- unmatched rows
- ambiguous matches
- two-year-duration rows
- four-year-duration rows
- no interval has `start_date > end_date`

Implementation:

- Source database remains read-only: `test_antir_auth_local`
- Write target is the restored production database: `an_tir_authorizations_restore_test`
- Command: `python manage.py populate_restore_validity_intervals`
- Dry run is the default.
- Write mode: `python manage.py populate_restore_validity_intervals --write`
- Manually reviewed person/style drift rows can be included with `--include-reviewed-drift`.
- The command must be run with `DB_NAME=an_tir_authorizations_restore_test`.
- Legacy rows are matched to restored rows by authorization ID, but are skipped for review if that ID no longer has the same person/style in the restored database.

## Part 3: Current Data Population and Merge

Use a fresh production backup restored to `an_tir_authorizations_restore_test`.

Before this step, production should be locked or put into maintenance mode so no writes happen between backup and final restore.

Input:

- restored current production database: `an_tir_authorizations_restore_test`
- legacy intervals generated from `test_antir_auth_local`

Process:

1. Lock production or put the site into maintenance mode.
2. Take and verify a fresh production backup.
3. Restore the backup into `an_tir_authorizations_restore_test`.
4. Apply migration `0037_authorizationvalidityinterval` to `an_tir_authorizations_restore_test`.
5. Generate current-data intervals from every current authorization:
   - `end_date = authorization.expiration`
   - `start_date = end_date - 4 years`, except youth/minor/youth-combat-marshal duration rows use `end_date - 2 years`
   - `source='portal_authorization'`
6. Merge current-data intervals into the legacy intervals:
   - if a current interval starts on or before an existing legacy interval's end date for the same authorization, extend the legacy interval end date when the current end date is later
   - if the current interval starts after the legacy interval's end date, create a new interval to preserve the lapse
   - if no legacy interval exists, create the current interval
7. Write the merged legacy + current interval set into `an_tir_authorizations_restore_test`.
8. Run validation reports.
9. After review, restore production from the updated `an_tir_authorizations_restore_test` database.
10. Unlock production.

Validation:

- total current authorization rows considered
- total legacy intervals loaded
- total merged intervals written
- intervals extended
- new intervals created because of gaps
- current rows with no matching legacy interval
- rows skipped
- no interval has `start_date > end_date`
- spot-check known marshal, concurring fighter, youth marshal, and recent renewal cases

Restore-test result on 2026-06-05 after manual drift review:

- current candidates: 14,669
- current rows skipped because effective expiration was before calculated start: 23
- legacy candidates: 13,931
- legacy rows skipped because effective expiration was before calculated start: 69
- manually reviewed person/style drift rows included: 62
  - 52 were expected youth combat style split rows
  - 10 were expected merged-account rows
- merged intervals written: 15,067
- overlapping intervals contained by another interval: 12,401
- overlapping intervals that extended another interval: 1,132
- post-write verification: 14,692 authorizations, 15,067 intervals, 18 authorizations without an interval, 393 authorizations with multiple intervals, 0 invalid start/end intervals
- reports written under `tmp/validity_interval_population/`

## Migration and Backup Ordering

For production, take the backup before applying the migration to production.

Recommended order:

1. Lock production or enter maintenance mode.
2. Take and verify the production backup.
3. Restore the backup to `an_tir_authorizations_restore_test`.
4. Apply the migration and run the population/merge process on `an_tir_authorizations_restore_test`.
5. Validate the restored/populated database.
6. Replace production from the validated restored/populated database.

This keeps the untouched production backup as the recovery point and avoids doing a risky schema/data backfill directly on live production.

## Future Sync Points

After the initial backfill, interval sync needs to run when these events happen:

- new authorization is created from paper entry or normal workflow with a known authorization date
- active/unexpired renewal extends an authorization
- lapsed authorization is reinstated
- authorization is revoked, rejected, inactive, or otherwise loses validity
- membership expiration is entered or updated
- background-check expiration is entered or updated

Membership/background-check restoration after a lapse should resume validity on the entry date unless a documented officer correction path explicitly supplies a different effective date.

## Final Catch-Up Command

After the vetted interval rows are imported into production, run a scoped catch-up for rows changed since the production backup cutoff.

Command:

```powershell
$env:DB_NAME='production_database_name'
venv\Scripts\python.exe manage.py catch_up_validity_intervals --since YYYY-MM-DDTHH:MM:SS
```

Behavior:

- dry-run is the default
- requires either `--since` or `--all`
- refuses to run against an empty interval table unless `--allow-empty` is explicit
- considers authorizations whose `Authorization.updated_at` or `User.updated_at` changed after the cutoff
- merges the current authorization-derived interval into existing intervals
- preserves gaps by creating a new interval when the current interval does not overlap
- writes review output to `tmp/validity_interval_population/validity_interval_catch_up_review.csv`

Write mode:

```powershell
venv\Scripts\python.exe manage.py catch_up_validity_intervals --since YYYY-MM-DDTHH:MM:SS --write
```

Restore-test dry run with `--since 2026-06-05`:

- existing intervals before catch-up: 15,067
- authorizations considered: 12
- unchanged: 12
- no writes performed

## Open Implementation Work

- Add interval sync helper.
- Add dry-run test/staging population command. Done as `populate_test_validity_intervals`.
- Add dry-run legacy population command against `test_antir_auth_local`. Done as `populate_restore_validity_intervals`.
- Add dry-run current-data merge command against `an_tir_authorizations_restore_test`. Done as `populate_restore_validity_intervals`.
- Add production-ready write mode after dry-run reports are reviewed.
- Add KAO/KEAO paper-entry validation using interval coverage.
- Add final scoped catch-up command. Done as `catch_up_validity_intervals`.
- Add tests for continuous renewal, lapsed renewal, membership lapse/resume, background-check lapse/resume, and as-of-date marshal/concurrer validation.
