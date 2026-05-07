# An Tir Authorizations Production Operations Handoff

Last updated: 2026-05-07

This document is the working source of truth for the production authorization portal after the emergency launch migration. Do not store passwords, tokens, private keys, or full database dumps in this file.

## Current Production State

- Application: Django app, `authorizations`
- Production host SSH: `antir@138.68.242.105`
- Application directory on droplet: `/home/antir/apps/an_tir_authorizations`
- Production env file: `/home/antir/apps/an_tir_authorizations/.env`
- Current public URL: `https://authorizations.thebusinessduck.com/`
- Current official legacy website: `http://antirlists.org`
- Legacy authorization portal path: `http://antirlists.org/auth/index.php`
- Planned launch redirect date: `2026-05-11`
- Redirect plan: the webminister will redirect the legacy authorization path to the new portal after launch communication.
- Long-term hosting/domain plan: at an undetermined future date, the webminister may clean up an AWS host for official deployment. If/when that happens, the authorization portal is expected to move under the official `http://antirlists.org` domain.
- Web app service: `an_tir_authorizations.service`
- App server: Gunicorn
- Front end proxy: Nginx, behind Cloudflare
- Python environment: repo-local virtualenv at `/home/antir/apps/an_tir_authorizations/venv`

Nginx site configuration:

- Current production site config: `/etc/nginx/sites-available/authorizations`
- Enabled symlink: `/etc/nginx/sites-enabled/authorizations`
- Current server name: `authorizations.thebusinessduck.com`
- Proxies to Gunicorn socket: `http://unix:/run/an_tir_authorizations/gunicorn.sock`
- Access log: `/var/log/nginx/an_tir_authorizations_access.log`
- Error log: `/var/log/nginx/an_tir_authorizations_error.log`

Older/staging Nginx site:

- Config: `/etc/nginx/sites-available/an_tir_authorizations`
- Enabled symlink: `/etc/nginx/sites-enabled/an_tir_authorizations`
- Server name: `apps.authorizations.thebusinessduck.com`

## DigitalOcean Infrastructure

Droplet:

- Name: `antir-authorizations`
- IP address: `138.68.242.105`
- Region: `SFO2`
- OS: Ubuntu 24.04 LTS x64
- Size: 4 GB RAM / 80 GB disk

DigitalOcean Spaces backup bucket:

- Space name: `an-tir-authorization-backup`
- Endpoint URL: `https://an-tir-authorization-backup.sfo3.digitaloceanspaces.com`
- Recorded usage: 30.05 MiB
- Backup cadence: daily backups are stored in this Space

## Production Database

- Engine: MySQL
- Production database name: `an_tir_authorizations`
- Production database user: `antir_app`
- Production database host: `localhost`
- Production database port: `3306`
- Production database password: stored only in the server `.env`

Useful verification command on the droplet:

```bash
python manage.py shell -c "from django.conf import settings; db=settings.DATABASES['default']; print(db['NAME']); print(db['USER']); print(db['HOST']); print(db['PORT'])"
```

Expected:

```text
an_tir_authorizations
antir_app
localhost
3306
```

## Launch Migration State

The legacy migration was completed on 2026-05-07.

Post-import production counts:

- Users: `4252`
- People: `4252`
- Authorizations: `14000`
- Active authorizations: `7989`
- Duplicate authorization statuses: none

Seed system/admin account:

- User ID: `15050`
- Username: `antir.authorization.database@gmail.com`
- Email: `antir.authorization.database@gmail.com`
- Legal name: `Database Administrator`
- SCA name: `Administrator`
- Staff: yes
- Superuser: yes
- Membership expiration: `2100-12-31`
- Background check expiration: `2100-12-31`

The system/admin account is intentionally hidden from normal searches and dropdowns, but direct access to `/authorizations/fighter/15050` is allowed.

## Minor Cleanup State

After the migration, legacy `MinorExpDate` values were used to correct minor status and birthdays.

Final production state:

- Current minors: `40`
- Minors missing birthday: `0`
- Non-minors with birthday: `0`
- User `19060`: non-minor, birthday cleared

Temporary one-time cleanup files/commands should not be treated as long-term operational tools. Future minor status handling should infer minor status from birthday and jurisdiction rather than relying on stored `Person.is_minor`.

## Important Backups And Transfer Files

Keep these until production has been stable and rollback is no longer needed:

- Production pre-import backup on droplet:
  `/home/antir/db_backups/prod_before_launch_import_2026-05-07.sql`
- Launch import dump uploaded to droplet:
  `/home/antir/test_antir_auth_local_launch.sql`
- Local database backups folder:
  `C:\Users\Don Room\CascadeProjects\an_tir_authorization\backups`
- Offline backup cadence: monthly backup downloaded locally to the backups folder above
- First post-migration offline monthly backup has been taken
- Legacy database backup: retained for lookup/audit; exact file name/location should be recorded if separate from the backups folder above
- Local reviewed trial database: `test_antir_auth_local`
- Local legacy source database: `antir_auth_legacy`

Do not write to or overwrite the legacy database.

## Local Database Aliases

Local development configuration uses:

- `default`: `antir_auth_local`
- `trial`: `test_antir_auth_local`
- `legacy`: `antir_auth_legacy`

Django test databases have explicit scratch names so tests do not collide with the reviewed trial import database.

## Standard Production Commands

From `/home/antir/apps/an_tir_authorizations` with the virtualenv active:

```bash
git pull
python manage.py migrate
python manage.py check
sudo systemctl restart an_tir_authorizations.service
sudo systemctl status an_tir_authorizations.service
```

Recent logs:

```bash
sudo journalctl -u an_tir_authorizations.service -n 50 --no-pager
```

Stop app before risky database work:

```bash
sudo systemctl stop an_tir_authorizations.service
```

Start app:

```bash
sudo systemctl start an_tir_authorizations.service
```

## Backup Command

Use `--no-tablespaces` because the production app user does not have the MySQL `PROCESS` privilege.

```bash
mkdir -p ~/db_backups
mysqldump -u antir_app -p --single-transaction --quick --no-tablespaces an_tir_authorizations > ~/db_backups/prod_backup_YYYY-MM-DD.sql
ls -lh ~/db_backups/prod_backup_YYYY-MM-DD.sql
```

## Security And Reliability Features

Configured or implemented:

- `DEBUG=False` in production through environment configuration
- `ALLOWED_HOSTS` controlled by `.env`
- HTTPS through Nginx/Cloudflare
- HSTS enabled in production settings
- Secure session and CSRF cookies in production settings
- CSRF trusted origins controlled by environment/settings
- Security event log path: `/var/log/an_tir_authorizations/security_events.log`
- Login, password reset, username recovery, and email-change flows include throttling/rate limits
- Account recovery avoids account/email enumeration
- Gmail API email backend supported for production email
- Operational sender: `antir.authorization.database@gmail.com`
- System/admin user hidden from normal search/dropdown discovery

TLS certificates:

- Active certificate name: `authorizations.thebusinessduck.com`
- Active certificate domains: `authorizations.thebusinessduck.com`
- Active certificate expiry: `2026-06-28 22:25:06+00:00`
- Active certificate path: `/etc/letsencrypt/live/authorizations.thebusinessduck.com/fullchain.pem`
- Active private key path: `/etc/letsencrypt/live/authorizations.thebusinessduck.com/privkey.pem`
- Expired/staging certificate name: `apps.authorizations.thebusinessduck.com`
- Expired/staging certificate expiry: `2026-04-29 07:49:33+00:00`

Cloudflare:

- Account owner/contact: Don Reynolds, `don.k.a.reynolds@outlook.com`
- Current proxied production domain: `authorizations.thebusinessduck.com`
- Cloudflare zone: `thebusinessduck.com`
- Cloudflare account members: Don Reynolds only
- SSL/TLS encryption mode: Full
- DNS setup: Full
- Production DNS record:
  - Type: `A`
  - Name: `authorizations`
  - Content: `138.68.242.105`
  - Proxy status: Proxied
  - TTL: Auto

## Known Operational Gaps

- Maintenance mode switch is not yet implemented.
- Kingdom-owned legacy-site redirect is planned for `2026-05-11`.
- Long-term AWS/official-domain migration timing is unknown and depends on webminister availability.
- Long-term official email provisioning is still pending.
- Missing-year authorization data still needs to be imported/backfilled.
- Explicit minor status flag needs to be removed in future migration as it has already been replaced by inferred status.
- Temporary legacy cleanup/import commands should be removed or clearly quarantined after launch cleanup is complete.
- The homepage/search payloads are large with production-scale data and should be optimized.

## Smoke Test Checklist

After deployment or data work:

1. Homepage returns `200`.
2. `/authorizations/` returns `200`.
3. `/authorizations/search` returns `200`.
4. `/authorizations/fighter/15050` returns `200`.
5. Admin login works.
6. `Administrator` does not appear in normal fighter search/dropdowns.
7. Wide-open authorization search loads and paginates.
8. A few real fighter pages load.
9. Password reset/account setup email can be sent.
10. `python manage.py check` passes.

## Unknowns To Fill In

- Final AWS/official-domain deployment plan:
- Exact file name/location of the most recent legacy DB backup, if separate from the standard backups folder:
