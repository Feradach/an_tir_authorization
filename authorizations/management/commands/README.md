# Project Management Commands

This directory contains the An Tir authorization portal's project-specific Django management commands. Run them from the deployed application directory with the application's virtual environment active:

```bash
python manage.py <command>
```

For any command that can change data, make a current database backup and run its read-only or dry-run form first. Review the per-record output before applying changes.

## Safety conventions

- **Read-only** commands do not change database rows. Some create report files or send email when explicitly requested.
- **Dry-run by default** commands only write when `--apply` or `--write` is supplied.
- **Writes by default** commands change data unless an explicit `--dry-run` flag is supplied.
- `cleanup_minor_transition_data` and `import_legacy_reports` are important exceptions: they **write by default**.
- `restoredata` clears and replaces database tables by default. Treat it as destructive.
- Commands described as local, staging, trial, migration, or anonymization tools should not be applied to production unless their documented workflow explicitly calls for it.

Use Django's built-in help to see every supported option:

```bash
python manage.py help <command>
```

## Address and minor-jurisdiction checks

### `audit_address_jurisdictions` — read-only

Checks every user row, including inactive and tombstoned merged accounts, before removal of the `country` field. It reports:

- missing or unsupported state/province;
- missing or malformed postal codes;
- postal codes outside the accepted An Tir ranges;
- state/province and postal-code jurisdiction mismatches;
- unrecognized stored country values;
- stored country values that disagree with state/province; and
- people whose current minor status would change when jurisdiction is inferred only from state/province.

Run the production check:

```bash
python manage.py audit_address_jurisdictions
```

Also save every flagged account to CSV:

```bash
python manage.py audit_address_jurisdictions --output-csv address_jurisdiction_audit.csv
```

Return a nonzero exit status when issues exist, for automated deployment checks:

```bash
python manage.py audit_address_jurisdictions --fail-on-issues
```

This command never changes the database.

### Postal-code cleanup workflow

Use these commands in order to repair the legacy postal-code data. None of them contacts an address service automatically. The export files contain street addresses, so keep them private and remove working copies when the repair is complete.

First confirm that the normal production backup system has a current recoverable backup. The postal-code workflow does not require the project's optional `backupdata` command. Then run the baseline audit:

```bash
python manage.py audit_address_jurisdictions --output-csv address_jurisdiction_before.csv
```

#### `normalize_postal_codes` — dry-run by default

Normalizes existing, structurally valid postal codes without guessing missing characters. It uppercases Canadian codes and standardizes them as `A1A 1A1`; it also standardizes a supplied nine-digit U.S. ZIP as `12345-6789`. A five-digit U.S. ZIP remains valid and ZIP+4 is never required.

Review every proposed change:

```bash
python manage.py normalize_postal_codes
```

Apply only those deterministic formatting changes:

```bash
python manage.py normalize_postal_codes --apply
```

Malformed values are reported and left unchanged.

#### `export_missing_postal_codes` — read-only database operation

Exports every account whose postal code is null, empty, or whitespace:

```bash
python manage.py export_missing_postal_codes --output-dir postal_code_lookup
```

It creates:

- `missing_postal_us_review.csv`, for reviewing and manually entering U.S. results;
- `missing_postal_us_census.csv`, a headerless file ready for the U.S. Census batch geocoder;
- `missing_postal_canada_review.csv`, for Canada Post or manual Canadian lookup; and
- `missing_postal_unknown_review.csv`, for records whose state/province must be resolved manually.

The files intentionally contain user IDs and address fields, but no names, email addresses, birthdays, membership numbers, or phone numbers.

Submit the U.S. Census file from a machine allowed to send these addresses to the Census service:

```bash
curl --form addressFile=@postal_code_lookup/missing_postal_us_census.csv --form benchmark=4 https://geocoding.geo.census.gov/geocoder/locations/addressbatch --output postal_code_lookup/us_census_results.csv
```

The Census service handles only the U.S. rows. Enter verified Canadian postal codes into the `postal_code` column of `missing_postal_canada_review.csv`. Resolve the unknown-jurisdiction file manually before entering its postal codes.

#### `import_postal_codes` — dry-run by default

Imports only validated postal codes into accounts that are still blank. It never overwrites an existing postal code.

Review the U.S. Census matches:

```bash
python manage.py import_postal_codes postal_code_lookup/us_census_results.csv --format census
```

Apply the validated U.S. matches:

```bash
python manage.py import_postal_codes postal_code_lookup/us_census_results.csv --format census --apply
```

Review and then apply a manually completed review file:

```bash
python manage.py import_postal_codes postal_code_lookup/missing_postal_canada_review.csv
python manage.py import_postal_codes postal_code_lookup/missing_postal_canada_review.csv --apply
```

Use the same review/apply pair for `missing_postal_unknown_review.csv` after its jurisdictions and postal codes have been resolved.

For review-format files, the importer verifies that the address and state/province still match the exported snapshot. For both formats it validates postal-code structure and state/province jurisdiction, locks each row before writing, and aborts the entire import if any error is found. Blank results and Census non-matches are skipped for later manual work.

Finish by rerunning the audit:

```bash
python manage.py audit_address_jurisdictions --output-csv address_jurisdiction_after.csv
python manage.py audit_address_jurisdictions --fail-on-issues
```

The second command should return success only after all remaining malformed, missing, and jurisdiction-mismatched records have been resolved.

### `audit_youth_combat_birthdays` — read-only

Reports active Youth Armored and Youth Rapier combatant authorizations whose fighters have no birthday:

```bash
python manage.py audit_youth_combat_birthdays
```

### `report_minors_missing_parent` — read-only database report

Reports current minors who have neither a selected parent account nor stored parent name information. Use `--no-email` for a console-only production check:

```bash
python manage.py report_minors_missing_parent --no-email
```

To print and email the report with its CSV attachment:

```bash
python manage.py report_minors_missing_parent --email-to recipient@example.com
```

Without `--no-email`, the command sends to its configured default recipient.

### `cleanup_minor_transition_data` — **writes by default**

Finds birthdays that should be cleared for people age 20 or older and stale parent links or parent names belonging to people who are no longer minors.

Always inspect the dry run first:

```bash
python manage.py cleanup_minor_transition_data --dry-run
```

Apply the reported cleanup:

```bash
python manage.py cleanup_minor_transition_data
```

Optionally email the report:

```bash
python manage.py cleanup_minor_transition_data --dry-run --email-to recipient@example.com
```

See the repository's `maintenance_runbook.md` for the scheduled-job details.

## Production data repair commands

### `activate_pending_waiver_authorizations` — dry-run by default

Finds waiver-blocked authorizations whose fighters already have a current waiver expiration:

```bash
python manage.py activate_pending_waiver_authorizations
python manage.py activate_pending_waiver_authorizations --apply
```

The apply form marks the reported authorizations Active and records repair notes.

### `advance_fighter_concurrence_authorizations` — dry-run by default

Finds non-marshal authorizations stuck in `Awaiting Fighter Concurrence` after fighter concurrence has been disabled. It calculates the next status using the current authorization-officer sign-off setting:

```bash
python manage.py advance_fighter_concurrence_authorizations
python manage.py advance_fighter_concurrence_authorizations --write
```

Review the reported sign-off setting and every proposed destination status before using `--write`.

### `deactivate_superseded_junior_marshals` — dry-run by default

Finds active Junior Marshal authorizations when the same person has an active Senior Marshal authorization in the same discipline:

```bash
python manage.py deactivate_superseded_junior_marshals
python manage.py deactivate_superseded_junior_marshals --apply
```

### `repair_merged_account_history` — dry-run by default

Reattaches surviving history records from tombstoned source accounts to their merged survivor accounts:

```bash
python manage.py repair_merged_account_history
python manage.py repair_merged_account_history --apply
```

Limit the review or repair to one or more tombstoned source user IDs:

```bash
python manage.py repair_merged_account_history --source-user-id 17272
python manage.py repair_merged_account_history --source-user-id 17272 --apply
```

The command cannot reconstruct rows that were already deleted; recover those from backup first.

## Authorization validity-interval commands

### `catch_up_validity_intervals` — dry-run by default

Reconciles validity intervals for authorizations changed after a reviewed cutoff:

```bash
python manage.py catch_up_validity_intervals --since 2026-06-05
python manage.py catch_up_validity_intervals --since 2026-06-05 --write
```

An intentional full reconciliation uses `--all`:

```bash
python manage.py catch_up_validity_intervals --all
```

The command normally refuses to run against an empty interval table. `--allow-empty` overrides that protection and should only be used intentionally.

### `populate_restore_validity_intervals` — dry-run by default

Builds merged legacy/current validity intervals for a restored production database while treating the legacy database as read-only:

```bash
python manage.py populate_restore_validity_intervals
python manage.py populate_restore_validity_intervals --write
```

`--replace` deletes existing intervals before recreating them and requires `--write`:

```bash
python manage.py populate_restore_validity_intervals --write --replace
```

Use `--include-reviewed-drift` only after manually reviewing legacy rows whose person or style changed.

### `populate_test_validity_intervals` — local/staging only

Creates validity intervals for local or staging test data where no legacy history exists:

```bash
python manage.py populate_test_validity_intervals
python manage.py populate_test_validity_intervals --write
```

`--write --replace` deletes existing test intervals before recreating them. Do not use this command to substitute generated test history for vetted production history.

## Import and migration audit commands

### `check_legacy_migration_databases` — read-only

Confirms that the configured legacy source and trial target aliases resolve to distinct databases:

```bash
python manage.py check_legacy_migration_databases
```

Override aliases when needed:

```bash
python manage.py check_legacy_migration_databases --source-db legacy --target-db trial
```

### `plan_legacy_migration` — read-only databases

Analyzes the legacy database and writes planning CSV files:

```bash
python manage.py plan_legacy_migration
```

Use `--legacy-db` and `--output-dir` to override the defaults.

### `validate_legacy_migration_decisions` — read-only databases

Validates the hand-edited duplicate-person and reference-mapping decision CSVs and writes validation reports:

```bash
python manage.py validate_legacy_migration_decisions
```

The main file options are `--duplicate-person-file`, `--reference-mapping-file`, `--legacy-db`, and `--output-dir`.

### `import_legacy_database` — dry-run by default; trial target only

Plans or applies the legacy MySQL import into the isolated trial database:

```bash
python manage.py import_legacy_database
python manage.py import_legacy_database --apply
```

Reset and repopulate the trial target only after reviewing the dry run:

```bash
python manage.py import_legacy_database --apply --reset-target
```

The command enforces a trial target. Its other inputs include the validated mapping file, duplicate-actions file, source/target aliases, and report output directory.

### `audit_user_profile_import` — read-only

Compares imported user profile fields in a target database with a reviewed source database or CSV:

```bash
python manage.py audit_user_profile_import
```

Common alternatives:

```bash
python manage.py audit_user_profile_import --source-csv reviewed_profiles.csv
python manage.py audit_user_profile_import --export-source-csv reviewed_profiles.csv
```

Use `--include-system-users` only when seed/system rows belong in the comparison.

### `export_legacy_trial_auth_sample` — read-only databases

Writes paired random legacy-source and trial-target authorization samples for manual review:

```bash
python manage.py export_legacy_trial_auth_sample --count 100
```

Use `--seed` for a repeatable sample and `--output-dir` to choose the report directory.

### `sample_database_rows` — read-only database

Prints or exports a small repeatable sample from every table:

```bash
python manage.py sample_database_rows --database default --rows 3
python manage.py sample_database_rows --database default --rows 3 --output-csv database_sample.csv
```

Sensitive columns are excluded by default. Do not use `--include-sensitive` for files that may be shared.

## Reporting commands

### `import_legacy_reports` — **writes by default**

Imports legacy quarterly XLSX data into stored reporting periods and values.

Always parse and validate first:

```bash
python manage.py import_legacy_reports --dry-run
```

Apply the import by omitting `--dry-run`:

```bash
python manage.py import_legacy_reports
```

Use `--base-dir` to select the XLSX directory. `--include-current-quarter` also imports in-progress quarter sheets when present.

### `generate_quarterly_report` — writes stored report data

Generates a stored quarterly snapshot. With no period arguments it only runs on the last day of a quarter:

```bash
python manage.py generate_quarterly_report
```

Generate a specific completed quarter:

```bash
python manage.py generate_quarterly_report --year 2026 --quarter 2
```

Existing quarterly reports are immutable unless `--force` is supplied. `--force` deletes and replaces that quarter's stored values, so use it only for an intentional correction.

## Release, backup, and restore commands

### `check_release_ready` — read-only

Checks production release configuration and changelog readiness:

```bash
python manage.py check_release_ready
```

Run this during the production pre-deploy checks described in `deployment_workflow.md`.

### `backupdata` — writes a backup file

Creates a MySQL dump using the configured default database:

```bash
python manage.py backupdata
python manage.py backupdata --output backups/pre_change.sql --no-timestamp
```

`--no-data` creates a schema-only dump. The command requires `sql_details.env` and the `mysqldump` client. See the backup and restore runbook before relying on the result.

### `restoredata` — **destructive**

Clears the configured database tables and restores a MySQL dump:

```bash
python manage.py restoredata backups/db_backup.sql
```

The command prompts before proceeding. `--no-confirm` bypasses that protection. `--no-clear` imports without first dropping tables and is explicitly not recommended. Confirm the target database and follow the backup and restore runbook before running this command.

## Environment preparation and non-production tools

### `normalize_local_weapon_styles` — local database only; dry-run by default

Reports or applies known weapon-style name normalization in the protected local database:

```bash
python manage.py normalize_local_weapon_styles
python manage.py normalize_local_weapon_styles --apply
```

The command refuses to apply to a database whose resolved name is not `antir_auth_local`.

### `anonymize_db` — copied/non-production database only; dry-run by default

Anonymizes sensitive data deterministically for safe testing or sharing:

```bash
python manage.py anonymize_db
python manage.py anonymize_db --apply
```

Additional options can pseudonymize names, shift expirations, fake memberships, randomize branches, and clear comments. Never apply this to the live production database.

### `seed_kingdom_authorization_officer` — writes immediately

Creates or updates the seed Kingdom Authorization Officer account and related access:

```bash
python manage.py seed_kingdom_authorization_officer --prompt-password
```

The command supports explicit account identity, membership, user ID, expiration, and password options. Avoid `--password` in shell history; prefer `--prompt-password`.

## Updating this catalog

When adding, renaming, or changing a project management command:

1. Add or update its entry in this file.
2. State clearly whether it is read-only, dry-run by default, or writes by default.
3. Include the safest production command first.
4. Document the explicit flag that applies changes.
5. Add targeted tests for data-changing behavior and dry-run protection.
