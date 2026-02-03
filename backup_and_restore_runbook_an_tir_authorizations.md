# Backup and Restore Runbook

## Purpose

This document describes how backups are created, verified, and restored for the **An Tir Authorizations** system. It is written to support long‑term maintenance and eventual Kingdom handoff. This is for the version stored on DigitalOcean using a Droplet to maintain the core and Spaces Object Storage for extended backups.

The goals of this system are:
- Protect against accidental data modification
- Protect against host loss or droplet deletion
- Minimize operational complexity
- Keep costs predictable

This system intentionally favors **simplicity and reliability** over fine‑grained recovery.

---

## System Summary

### What is backed up

- MySQL database: `an_tir_authorizations`
- Full logical dump (schema + data)
- Encrypted and compressed

### Where backups live

**Local (short‑term)**
- Path: `/var/backups/an-tir-authorizations`
- Retention: 7 days
- Purpose: fast rollback for recent mistakes

**Remote (long‑term)**
- DigitalOcean Spaces bucket: `an-tir-authorization-backup`
- Retention: 30 days
- Purpose: disaster recovery if the droplet is lost

---

## Encryption Model

- Encryption: AES‑256
- Key type: symmetric key file
- Key location: `/etc/an-tir-auth-backup.key`
- Owner: `root`
- Permissions: `0400`

The encryption key:
- Is never uploaded
- Is not stored in a password manager
- Is required to restore any backup

Loss of this key makes all backups unreadable.

---

## Backup Schedule

Backups are handled by systemd timers.

| Task | Time (UTC) |
|----|----|
| Local backup | 03:30 |
| Upload to Spaces | 04:00 |

Timers are persistent. If the server is offline at the scheduled time, the job will run once the system is back up.

---

## Verifying Backups

### 1. Verify timers are active

```bash
systemctl list-timers | grep an-tir-auth
```

You should see:
- `an-tir-auth-backup-local.timer`
- `an-tir-auth-backup-upload.timer`

### 2. Verify recent local backups

```bash
sudo ls -lh /var/backups/an-tir-authorizations
```

Expected:
- One encrypted file per day
- Filenames include date and hostname
- Files are non‑zero in size

### 3. Verify remote backups

```bash
sudo s3cmd ls s3://an-tir-authorization-backup
```

Expected:
- Same files as local backups
- Dates consistent with the last 30 days

### 4. Verify recent job success

```bash
journalctl -u an-tir-auth-backup-local.service
journalctl -u an-tir-auth-backup-upload.service
```

Look for:
- No errors
- Successful completion messages

---

## Restore Procedures

### Restore scenario A: Recent mistake (local restore)

Use this when:
- The server is healthy
- The mistake happened within the last 7 days

#### Steps

1. Identify the correct backup file:

```bash
sudo ls /var/backups/an-tir-authorizations
```

2. Restore the database:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sudo mysql
```

This recreates the database exactly as of that backup.

---

### Restore scenario B: Server loss or rebuild (remote restore)

Use this when:
- The droplet was destroyed or rebuilt
- Local backups are unavailable

#### Preconditions

- MySQL is installed and running
- `/etc/an-tir-auth-backup.key` is present
- `s3cmd` is configured

#### Steps

1. Download the desired backup:

```bash
sudo s3cmd get s3://an-tir-authorization-backup/an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc
```

2. Restore the database:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sudo mysql
```

---

## Restore Testing (Periodic Verification)

Restore tests should be performed **quarterly**.

### Safe restore test procedure

1. Create a temporary database:

```bash
sudo mysql -e "CREATE DATABASE an_tir_authorizations_restore_test;"
```

2. Restore while rewriting the database name:

```bash
sudo openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in an-tir-authorizations-db-YYYYMMDD-HOST.sql.gz.enc \
| gunzip \
| sed -e 's/`an_tir_authorizations`/`an_tir_authorizations_restore_test`/g' \
      -e 's/CREATE DATABASE an_tir_authorizations/CREATE DATABASE an_tir_authorizations_restore_test/g' \
      -e 's/USE an_tir_authorizations/USE an_tir_authorizations_restore_test/g' \
| sudo mysql
```

3. Verify tables exist:

```bash
sudo mysql -e "SHOW TABLES;" an_tir_authorizations_restore_test
```

4. Drop the test database:

```bash
sudo mysql -e "DROP DATABASE an_tir_authorizations_restore_test;"
```

---

## Failure Modes and Responses

### Local backup fails

- Upload will not run
- Check logs:

```bash
journalctl -u an-tir-auth-backup-local.service
```

### Upload fails

- Local backups remain intact
- Re‑run upload manually:

```bash
sudo /usr/local/sbin/an-tir-auth-backup-upload.sh
```

### Disk pressure

- Risk: local disk fills
- Mitigation:
  - Local retention enforced
  - Disk usage must be monitored

---

## Monitoring Requirements

At minimum, monitor:

- Root filesystem usage (`/`)
- Presence of recent backup files
- systemd timer failures

These checks are required for long‑term reliability.

---

## Ownership and Handoff Notes

- Backup logic lives in `/usr/local/sbin/`
- Scheduling is via systemd timers
- All credentials and keys are server‑local
- No personal cloud accounts are required

This system is designed to be transferred to Kingdom ownership with minimal changes.

