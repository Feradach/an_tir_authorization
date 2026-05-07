# Maintenance Runbook

Operational notes for recurring maintenance tasks on the An Tir authorization portal.

## Minor Transition Cleanup

The application infers current minor status from birthday and jurisdiction:

- United States and other non-Canadian jurisdictions: minor until age 18.
- Canada or Canadian province: minor until age 19.
- Missing birthday: treated as non-minor.

The `Person.is_minor` database column is retained temporarily as transition compatibility data. Live behavior should not rely on it. The cleanup command keeps that transitional column aligned and removes stale private data when appropriate.

### Command

From the deployed application directory:

```bash
python manage.py cleanup_minor_transition_data --dry-run --email-to antir.authorization.database@gmail.com
```

Dry run mode sends and prints a report without changing the database.

To apply changes:

```bash
python manage.py cleanup_minor_transition_data --email-to antir.authorization.database@gmail.com
```

### What It Changes

The command may:

- Clear birthdays for people age 20 or older.
- Clear parent links for people who are no longer inferred minors.
- Resync the transitional `Person.is_minor` column to the inferred value.

The command is idempotent and safe to run repeatedly. A clean production run should normally report zero records.

### Scheduled Jobs

Installed in the `antir` user's crontab on the production server.

Check installed jobs:

```bash
crontab -l
```

Expected entries:

```cron
15 3 1 * * cd /home/antir/apps/an_tir_authorizations && /home/antir/apps/an_tir_authorizations/venv/bin/python manage.py cleanup_minor_transition_data --dry-run --email-to antir.authorization.database@gmail.com >> /home/antir/apps/an_tir_authorizations/logs/minor_cleanup.log 2>&1
15 3 15 * * cd /home/antir/apps/an_tir_authorizations && /home/antir/apps/an_tir_authorizations/venv/bin/python manage.py cleanup_minor_transition_data --email-to antir.authorization.database@gmail.com >> /home/antir/apps/an_tir_authorizations/logs/minor_cleanup.log 2>&1
```

Schedule:

- 1st of each month at 03:15 server time: dry-run email report.
- 15th of each month at 03:15 server time: apply cleanup and email report.

Logs are written to:

```text
/home/antir/apps/an_tir_authorizations/logs/minor_cleanup.log
```

### Expected Report

The report includes summary counts and one line per affected person:

```text
People to inspect/change: 0
Birthdays to clear for people age 20+: 0
Adult parent links to clear: 0
Stored is_minor false -> true: 0
Stored is_minor true -> false: 0
```

If records are found, review the listed `user_id`, SCA name, birthday, country/state, and planned changes before allowing the apply job to run.

### Pausing The Job

Edit the crontab:

```bash
crontab -e
```

Comment out the two cleanup lines by prefixing each with `#`, then verify:

```bash
crontab -l
```

### When To Remove `Person.is_minor`

After the site has been live for a while with no `is_minor` errors and no unexpected cleanup drift, remove the transitional field in a later schema migration. At that point, remove compatibility writes and cleanup logic that only exist to keep `Person.is_minor` synced.
