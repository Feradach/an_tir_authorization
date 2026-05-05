# Backup and Restore Runbook

## Purpose

This document describes how backups are created, verified, restored, and archived for the An Tir Authorizations system.

The current production setup uses:
- a DigitalOcean Droplet for the Django/MySQL application;
- encrypted local database backups on the droplet;
- DigitalOcean Spaces for remote backup storage;
- optional manual offline backup copies for ransomware resilience.

The goals of this system are:
- protect against accidental data modification;
- protect against host loss or droplet deletion;
- preserve at least one recoverable copy outside the application server's control;
- minimize operational complexity;
- keep costs predictable.

This system intentionally favors simplicity and reliability over fine-grained point-in-time recovery.

## System Summary

### What Is Backed Up

- MySQL database: `an_tir_authorizations`
- Full logical dump, including schema and data
- Compressed and encrypted

### Where Backups Live

Local short-term backups:
- Path: `/var/backups/an-tir-authorizations`
- Retention: 7 days
- Purpose: fast rollback for recent mistakes

Remote long-term backups:
- DigitalOcean Spaces bucket: `an-tir-authorization-backup`
- Retention: 30 days
- Purpose: disaster recovery if the droplet is lost

Offline manual archive:
- Location: operator-controlled local/offline storage
- Retention: launch baselines plus monthly copies
- Purpose: recovery if the droplet and Spaces bucket are both compromised or deleted

## Encryption Model

- Encryption: AES-256
- Key type: symmetric key file
- Key location on droplet: `/etc/an-tir-auth-backup.key`
- Owner: `root`
- Permissions: `0400`

The encryption key:
- is never uploaded to Spaces;
- must be stored securely outside the droplet by authorized operators;
- is required to restore any encrypted backup.

Loss of this key makes all encrypted backups unreadable.

Anyone with both the encrypted backup file and this key can decrypt the database backup.

## Backup Schedule

Backups are handled by systemd timers.

| Task | Time (UTC) |
| --- | --- |
| Local backup | 03:30 |
| Upload to Spaces | 04:00 |

Timers are persistent. If the server is offline at the scheduled time, the job should run once the system is back up.

## Backup Implementation

Backup logic lives in:

```bash
/usr/local/sbin/an-tir-auth-backup-local.sh
/usr/local/sbin/an-tir-auth-backup-upload.sh
```

Scheduling is via:

```bash
an-tir-auth-backup-local.timer
an-tir-auth-backup-upload.timer
```

Remote retention cleanup must delete the full object path returned by `s3cmd ls`. The delete command should look like:

```bash
sudo s3cmd del "${FILE}"
```

It should not prepend the bucket to a value that already starts with `s3://`.

## Verifying Backups

### 1. Verify Timers Are Active

```bash
systemctl list-timers | grep an-tir-auth
```

Expected:
- `an-tir-auth-backup-local.timer`
- `an-tir-auth-backup-upload.timer`

### 2. Verify Recent Local Backups

```bash
sudo ls -lh /var/backups/an-tir-authorizations
```

Expected:
- encrypted backup files are present;
- filenames include date and hostname;
- files are non-zero size;
- local retention is roughly 7 days.

### 3. Verify Remote Backups

```bash
sudo s3cmd ls s3://an-tir-authorization-backup
```

Expected:
- recent encrypted files exist in Spaces;
- files are dated daily;
- remote retention is roughly 30 days.

### 4. Verify Recent Job Success

```bash
sudo journalctl -u an-tir-auth-backup-local.service -n 80 --no-pager
sudo journalctl -u an-tir-auth-backup-upload.service -n 80 --no-pager
```

Look for:
- successful completion messages;
- no encryption or upload failures;
- no malformed remote delete paths.

## Restore Procedures

### Scenario A: Recent Mistake, Local Restore

Use this when:
- the server is healthy;
- the mistake happened within the local retention window.

Identify the correct backup:

```bash
sudo ls /var/backups/an-tir-authorizations
```

Restore the database:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in /var/backups/an-tir-authorizations/an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sudo mysql
```

This recreates the database exactly as of that backup.

### Scenario B: Server Loss Or Rebuild, Remote Restore

Use this when:
- the droplet was destroyed or rebuilt;
- local backups are unavailable.

Preconditions:
- MySQL is installed and running;
- `/etc/an-tir-auth-backup.key` is present;
- `s3cmd` is configured.

Download the desired backup:

```bash
sudo s3cmd get s3://an-tir-authorization-backup/an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc
```

Restore the database:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sudo mysql
```

## Restore Testing

Restore tests should be performed:
- before production launch;
- after major backup script changes;
- quarterly after launch.

### Safe Restore Test Procedure

Create a temporary database:

```bash
sudo mysql -e "CREATE DATABASE an_tir_authorizations_restore_test;"
```

Restore while rewriting the database name:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in /var/backups/an-tir-authorizations/an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sed -e 's/`an_tir_authorizations`/`an_tir_authorizations_restore_test`/g' \
      -e 's/CREATE DATABASE .*an_tir_authorizations.*/CREATE DATABASE IF NOT EXISTS `an_tir_authorizations_restore_test`;/' \
      -e 's/USE `an_tir_authorizations`;/USE `an_tir_authorizations_restore_test`;/' \
| sudo mysql
```

Verify tables exist:

```bash
sudo mysql -e "SHOW TABLES;" an_tir_authorizations_restore_test
```

Verify key table counts:

```bash
sudo mysql -e "SELECT COUNT(*) AS users FROM authorizations_user;" an_tir_authorizations_restore_test
sudo mysql -e "SELECT COUNT(*) AS people FROM authorizations_person;" an_tir_authorizations_restore_test
sudo mysql -e "SELECT COUNT(*) AS authorizations FROM authorizations_authorization;" an_tir_authorizations_restore_test
```

Drop the test database:

```bash
sudo mysql -e "DROP DATABASE an_tir_authorizations_restore_test;"
```

## Offline Backup Procedure

Use this after major production milestones and then monthly. The backup file remains encrypted; do not decrypt it for routine storage.

Recommended milestones:
- immediately after production data import and validation;
- monthly after launch;
- before major infrastructure changes.

### 1. Choose The Backup File

Replace `YYYYMMDD` with the actual backup date, for example `20260505`.

Example backup filename:

```text
an-tir-authorizations-db-20260505-antir-authorizations.sql.gz.enc
```

### 2. Copy The Encrypted Backup To A Temporary Readable Path

Run on the droplet:

```bash
BACKUP_FILE="an-tir-authorizations-db-YYYYMMDD-antir-authorizations.sql.gz.enc"
sudo cp "/var/backups/an-tir-authorizations/${BACKUP_FILE}" "/tmp/${BACKUP_FILE}"
sudo chown antir:antir "/tmp/${BACKUP_FILE}"
sudo chmod 600 "/tmp/${BACKUP_FILE}"
```

### 3. Download From Local PowerShell

Run from the operator's local computer:

```powershell
scp antir@YOUR_DROPLET_IP:/tmp/an-tir-authorizations-db-YYYYMMDD-antir-authorizations.sql.gz.enc "D:\AI and Technology\Programming\An_Tir_Authorizations_project\offline_backups\"
```

### 4. Remove The Temporary Droplet Copy

Run on the droplet:

```bash
rm "/tmp/${BACKUP_FILE}"
```

### 5. Store The Backup Key Separately

The required key is:

```text
/etc/an-tir-auth-backup.key
```

To copy it for secure offline storage, run on the droplet:

```bash
sudo cp /etc/an-tir-auth-backup.key /tmp/an-tir-auth-backup.key
sudo chown antir:antir /tmp/an-tir-auth-backup.key
sudo chmod 600 /tmp/an-tir-auth-backup.key
```

Download from local PowerShell:

```powershell
scp antir@YOUR_DROPLET_IP:/tmp/an-tir-auth-backup.key "D:\AI and Technology\Programming\An_Tir_Authorizations_project\offline_backups\an-tir-auth-backup.key"
```

Remove the temporary droplet copy:

```bash
rm /tmp/an-tir-auth-backup.key
```

## Failure Modes And Responses

### Local Backup Fails

- Upload will not have a fresh backup to upload.
- Check logs:

```bash
sudo journalctl -u an-tir-auth-backup-local.service -n 80 --no-pager
```

### Upload Fails

- Local backups remain intact.
- Check logs:

```bash
sudo journalctl -u an-tir-auth-backup-upload.service -n 80 --no-pager
```

- Rerun upload manually:

```bash
sudo /usr/local/sbin/an-tir-auth-backup-upload.sh
```

### Remote Retention Fails

- Symptom: old files remain in Spaces beyond the documented retention window.
- Check upload logs:

```bash
sudo journalctl -u an-tir-auth-backup-upload.service -n 80 --no-pager
```

- Confirm delete commands target paths like:

```text
s3://an-tir-authorization-backup/an-tir-authorizations-db-YYYYMMDD-antir-authorizations.sql.gz.enc
```

- They should not target paths like:

```text
s3://an-tir-authorization-backup/s3://an-tir-authorization-backup/...
```

### Disk Pressure

- Risk: local disk fills.
- Mitigation:
  - local retention is enforced;
  - disk usage must be monitored;
  - droplet currently has expanded disk capacity after the 2026-05 resize.

## Monitoring Requirements

At minimum, monitor:

- root filesystem usage (`/`);
- presence of recent backup files;
- systemd timer failures;
- remote backup count/date range;
- periodic restore test completion.

These checks are required for long-term reliability.

## Production Launch Backup Checklist

Before the testing database is wiped and replaced with production records:

- Confirm both systemd timers are active.
- Confirm the most recent local backup exists and is non-zero size.
- Confirm the most recent remote backup exists in DigitalOcean Spaces.
- Perform a restore test into a temporary database.
- Confirm the backup encryption key is recoverable by authorized operators.
- Take a final pre-wipe backup of the testing database.

After production records are imported and validated:

- Take a new baseline production backup immediately.
- Verify the baseline backup exists locally and remotely.
- Record the baseline backup filename in the launch notes.
- Download a local/offline encrypted baseline backup.
- Consider preserving this baseline outside normal 30-day remote retention.

For ransomware resilience:

- Maintain at least one backup copy that the application server cannot overwrite or delete.
- Keep backup credentials separate from application and database credentials.
- Keep the backup encryption key recoverable outside the droplet.
- Review whether monthly archive backups should be retained longer than normal remote backups.

## Verified Status

As of 2026-05-05:

- Local encrypted backup creation was verified.
- Remote upload to DigitalOcean Spaces was verified.
- Restore into `an_tir_authorizations_restore_test` was verified.
- Key table counts were checked during restore testing.
- Remote retention cleanup was corrected and verified.
- The backup encryption key was copied for offline storage.

## Ownership And Handoff Notes

- Backup logic lives in `/usr/local/sbin/`.
- Scheduling is via systemd timers.
- Server-local credentials and keys must remain protected.
- Offline monthly archives should be kept outside the droplet and Spaces bucket.
- The system is designed to be transferred to Kingdom ownership with minimal changes.
