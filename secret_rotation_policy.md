# Secret Rotation Policy

## Purpose

This document defines how to review and rotate sensitive credentials for the An Tir Authorization Portal.

It is intended to be safe for a public repository. It describes categories, procedures, and responsibilities, but must not contain real secret values, private keys, access tokens, recovery codes, passwords, or exact break-glass credentials.

## Public Repository Guidance

This document may be committed to GitHub because it contains policy and operational guidance only.

Do commit:
- which types of secrets exist;
- when they should be rotated;
- the general order of operations;
- how to validate that rotation worked;
- who or what role is responsible.

Do not commit:
- `.env` files with production values;
- SSH private keys;
- API keys or access tokens;
- OAuth client secrets or refresh tokens;
- backup encryption keys;
- database passwords;
- Cloudflare, DigitalOcean, Gmail, or GitHub recovery codes;
- exact offline storage locations;
- private incident details that would help an attacker.

Environment-specific values should live in a private operator runbook or password manager controlled by the responsible organization.

## Secret Categories

### Infrastructure Access

Includes:
- DigitalOcean account credentials;
- Cloudflare account credentials;
- GitHub owner/admin credentials;
- SSH keys for server access;
- sudo-capable Linux accounts;
- domain registrar credentials, if separate from Cloudflare.

Rotation/review triggers:
- after any suspected compromise;
- when an operator leaves the project;
- when an SSH key may have been copied to an unsafe machine;
- annually, at minimum, for access review.

Required controls:
- MFA enabled for all infrastructure accounts;
- no shared personal passwords;
- root SSH login disabled;
- SSH password login disabled;
- only expected public keys present on the server.

### Django Application Secrets

Includes:
- `DJANGO_SECRET_KEY`;
- production `.env` values;
- any application signing or encryption secrets.

Rotation/review triggers:
- if the `.env` file may have been exposed;
- after host compromise;
- when moving to a new production environment;
- when an operator with secret access leaves.

Expected impact:
- active sessions may be invalidated;
- password reset links and other signed links may stop working;
- users may need to log in again.

### Database Credentials

Includes:
- runtime database user password;
- migration/admin database user password, if separate;
- MySQL root/admin credentials;
- backup database credentials.

Rotation/review triggers:
- after suspected server or database compromise;
- after accidental exposure in logs, shell history, screenshots, or repository files;
- when changing hosting providers;
- when an operator with database access leaves.

Required controls:
- runtime app user should not be a MySQL superuser;
- database should not be publicly exposed;
- backups should use only the privileges needed to create reliable dumps;
- migration credentials, if more privileged, should not be used by the running web app.

### Email Credentials

Includes:
- Gmail API token file;
- OAuth client secret;
- SMTP username/password, if SMTP is used;
- sender mailbox password, recovery methods, and MFA settings.

Rotation/review triggers:
- after server compromise if the token file was present;
- after email account compromise;
- if suspicious email activity appears;
- when moving from a personal sender account to an organization-owned sender account.

Required controls:
- sender account should have MFA;
- token file should be readable only by the app user or operational group that needs it;
- sender account should be owned by the organization where feasible.

### Backup Secrets

Includes:
- backup encryption key;
- DigitalOcean Spaces access keys;
- `s3cmd` configuration;
- credentials used by backup jobs.

Rotation/review triggers:
- after any server compromise;
- if backup credentials may have been exposed;
- if encrypted backups and the encryption key may both have been accessible to an attacker;
- when changing backup storage providers.

Required controls:
- backup encryption key must not be uploaded to remote backup storage;
- at least one backup copy must be outside the production server's ability to overwrite or delete;
- backup storage credentials should be separate from application and database credentials;
- old backup keys must be retained securely until old encrypted backups are no longer needed.

## Rotation Schedule

Routine schedule:
- Quarterly: review superusers, staff users, SSH keys, Cloudflare/DigitalOcean/GitHub account access, and backup health.
- Quarterly: perform or confirm a restore test.
- Annually: rotate infrastructure and database credentials unless a stronger organizational policy exists.
- After every major production milestone: create and verify a fresh baseline backup.

Immediate rotation is required after:
- ransomware or malware incident;
- suspected server compromise;
- accidental commit of a secret;
- lost laptop or workstation that had production access;
- unexpected admin/superuser account;
- operator departure where access cannot be confidently confirmed removed;
- unknown access in Cloudflare, DigitalOcean, GitHub, Gmail, or the server.

## Standard Rotation Procedure

Use this order for planned broad rotation:

1. Confirm a recent backup exists.
2. Confirm the backup encryption key is recoverable by authorized operators.
3. If rotating live production secrets, turn on the maintenance lock.
4. Rotate infrastructure account passwords and confirm MFA.
5. Review and remove unexpected account members in Cloudflare, DigitalOcean, GitHub, Gmail, and the server.
6. Rotate SSH keys and remove stale keys from `authorized_keys`.
7. Rotate database credentials and update production environment configuration.
8. Rotate email credentials or regenerate the Gmail API token if needed.
9. Rotate backup storage keys.
10. Create a new backup encryption key if the old key may have been exposed.
11. Take a fresh encrypted backup using current credentials.
12. Restart affected services.
13. Run smoke tests.
14. Confirm backups still run and upload.
15. Turn off the maintenance lock.
16. Record the rotation date and outcome in the private operator log.

## Django `SECRET_KEY` Rotation

Rotate `DJANGO_SECRET_KEY` if it may have been exposed.

Expected effects:
- current user sessions may become invalid;
- password reset links generated before rotation may fail;
- users may need to request new login/reset links.

Recommended process:

1. Turn on maintenance lock.
2. Set a new strong random `DJANGO_SECRET_KEY` in the production environment.
3. Restart the application service.
4. Test homepage, login, admin login, and password reset email.
5. Turn off maintenance lock.

Do not commit the new key to GitHub.

## Backup Key Rotation

Rotate the backup encryption key if the old key may have been exposed.

Important:
- old backups encrypted with the old key still require the old key;
- keep the old key securely offline until old backups age out or are intentionally retired;
- do not store the only copy of the new key on the production server.

Recommended process:

1. Confirm at least one known-good backup can be restored with the old key.
2. Generate a new backup encryption key on a trusted system or directly on the server.
3. Install the new key with root ownership and restrictive permissions.
4. Run a manual backup.
5. Verify the new backup exists locally and remotely.
6. Perform a restore test using the new key.
7. Store the new key offline with authorized operators.
8. Retain the old key offline until old backups are no longer needed.

## Access Review Checklist

Review these after incidents and at least quarterly:

- Django superusers;
- Django staff users;
- Cloudflare account members;
- DigitalOcean account members and API tokens;
- GitHub repository/admin access;
- SSH users and `authorized_keys`;
- sudo-capable Linux users;
- Gmail sender account recovery methods;
- Spaces access keys;
- production `.env` file permissions;
- Gmail token file permissions;
- backup encryption key permissions;
- backup directory permissions.

## Validation Checklist

After rotating secrets, verify:

- homepage loads;
- login works;
- Django admin login works;
- password reset or account setup email sends;
- protected officer workflows still enforce permissions;
- database connection works;
- uploaded/supporting documents remain accessible;
- manual backup succeeds;
- backup upload succeeds;
- security logs and application logs show no new errors.

## Incident Notes

For suspected compromise:
- preserve logs before overwriting evidence;
- prefer rebuilding from a clean server image over attempting to clean a compromised host;
- restore only from backups believed to predate the compromise;
- rotate all secrets that existed on the compromised host;
- review all admin, staff, officer, and superuser accounts after restore.

Detailed incident timelines, private account names, and exact recovery materials should be recorded in a private operator log, not in this public repository.
