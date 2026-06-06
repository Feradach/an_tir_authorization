# Backup and Restore Runbook

## Purpose

This document describes how backups are created, verified, restored, and archived for the An Tir Authorizations system.

The current production setup uses:
- a DigitalOcean Droplet for the Django/MySQL application;
- DigitalOcean Spaces for remote backup storage;
- manual encrypted offline backup copies for monthly archives and major milestones.

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

Manual droplet backups:
- Path: `/home/antir/db_backups`
- Retention: keep encrypted milestone/monthly files until they are downloaded and restore-tested
- Purpose: operator-controlled monthly archives and pre-change recovery points

Do not assume `/var/backups/an-tir-authorizations` or `an-tir-auth-backup-*` systemd units exist on the current droplet unless they have been revalidated.

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

DigitalOcean Spaces shows daily remote backups. The exact automation that creates those remote objects should be revalidated before relying on local timer commands.

Monthly offline backups are manual and should follow the procedure below so the retained file is encrypted and restore-tested.

## Backup Implementation

The confirmed manual backup path is:

```bash
mysqldump -u antir_app -p --single-transaction --quick --no-tablespaces an_tir_authorizations
gzip
sudo openssl enc -aes-256-cbc -salt -pbkdf2 -pass file:/etc/an-tir-auth-backup.key
```

If systemd backup units are reintroduced later, update this runbook with the validated unit names, script paths, and local backup directory.

## Verifying Backups

### 1. Verify Remote Backups

Use the DigitalOcean Spaces UI or a configured Spaces CLI to confirm recent backup objects exist in `an-tir-authorization-backup`.

Expected:
- recent backup files exist in Spaces;
- files are dated daily;
- remote retention is roughly 30 days.

### 2. Verify Manual Offline Backup

After taking a manual monthly backup, verify:

- the retained file ends in `.sql.gz.enc`;
- the local downloaded file is non-zero size;
- the backup restores into `an_tir_authorizations_restore_test`;
- temporary unencrypted `.sql` and `.sql.gz` working files were removed.

## Restore Procedures

Production restores are incident operations. Do not restore over production from this runbook casually.

For routine verification, use the **Local Offline Backup Viewing Procedure** below and restore into:

```text
an_tir_authorizations_restore_test
```

For an actual production restore, first identify the desired encrypted `.sql.gz.enc` backup, confirm the matching key is available, stop the application service, and write out the exact restore command for review before running it. If the incident allows time, restore into a scratch database first and verify row counts before replacing production.

Restore tests should be performed:
- after taking a monthly offline backup;
- before major production changes;
- after backup process changes;
- quarterly after launch.

## Monthly Offline Backup Procedure

Use this after major production milestones and then monthly. The retained backup file should be encrypted. Do not keep unencrypted `.sql` or `.sql.gz` files longer than needed to create and verify the encrypted backup.

This procedure creates a fresh encrypted backup on the droplet and downloads that encrypted file to the local backups folder.

### 1. Create A Fresh Database Dump

Run on the droplet:

```bash
cd /home/antir/apps/an_tir_authorizations
source venv/bin/activate

mkdir -p "$HOME/db_backups"
BACKUP_BASE="$HOME/db_backups/prod_backup_$(date +%Y-%m-%d_%H%M)"
SQL_FILE="${BACKUP_BASE}.sql"
GZ_FILE="${SQL_FILE}.gz"
ENC_FILE="${GZ_FILE}.enc"

mysqldump -u antir_app -p --single-transaction --quick --no-tablespaces an_tir_authorizations > "$SQL_FILE"
gzip -k "$SQL_FILE"
ls -lh "$SQL_FILE" "$GZ_FILE"
```

The `mysqldump` command prompts for the `antir_app` database password. If the first password attempt fails with `ERROR 1045`, rerun the `mysqldump` command and enter the correct database password.

### 2. Encrypt The Compressed Dump

Run on the droplet:

```bash
sudo openssl enc -aes-256-cbc -salt -pbkdf2 \
  -pass file:/etc/an-tir-auth-backup.key \
  -in "$GZ_FILE" \
  -out "$ENC_FILE"

sudo chown antir:antir "$ENC_FILE"
chmod 600 "$ENC_FILE"
ls -lh "$ENC_FILE"
basename "$ENC_FILE"
```

Use the `basename` output as the exact filename in the local `scp` command.

### 3. Download The Encrypted Backup

Run from the operator's local PowerShell, not from the SSH session:

```powershell
scp antir@138.68.242.105:/home/antir/db_backups/PASTE_EXACT_FILENAME.sql.gz.enc "C:\Users\Don Room\CascadeProjects\an_tir_authorization\backups\PASTE_EXACT_FILENAME.sql.gz.enc"
```

Verify the local encrypted file exists and is non-zero size:

```powershell
Get-Item "C:\Users\Don Room\CascadeProjects\an_tir_authorization\backups\PASTE_EXACT_FILENAME.sql.gz.enc"
```

### 4. Clean Up Temporary Unencrypted Files On The Droplet

After the encrypted file exists and has been downloaded, remove the unencrypted working files from the droplet:

```bash
rm -f "$SQL_FILE" "$GZ_FILE"
ls -lh "$ENC_FILE"
```

Keep the `.sql.gz.enc` file until the offline restore test succeeds.

### 5. Backup Key Reminder

The required encryption key is:

```text
/etc/an-tir-auth-backup.key
```

The key is root-readable on the droplet and should also be stored securely offline. Do not upload the key to DigitalOcean Spaces or commit it to the repository.

Only copy the key again if the offline key copy is missing or being rotated.

## Local Offline Backup Viewing Procedure

Use this when you have an encrypted offline backup saved on your local computer and want to inspect it without touching production.

This procedure restores the selected backup into the local scratch database:

```text
an_tir_authorizations_restore_test
```

Do not restore an offline backup directly into the local development database unless you intentionally want to replace your local working data.

### Preconditions

- MySQL is installed and running locally.
- The scratch database `an_tir_authorizations_restore_test` exists locally, or you have permission to create it.
- Git for Windows is installed, or you know the local paths to `openssl.exe` and `gzip.exe`.
- You have the encrypted backup file, for example:

```text
prod_backup_YYYY-MM-DD_HHMM.sql.gz.enc
```

- You have the matching backup key file:

```text
an-tir-auth-backup.key
```

### 1. Set Local Paths

Run from PowerShell on the local computer. Update `$BackupFile` for the backup you want to inspect.

```powershell
$BackupFile = "C:\Users\Don Room\CascadeProjects\an_tir_authorization\backups\PASTE_EXACT_BACKUP_FILENAME.sql.gz.enc"
$KeyFile = "D:\AI and Technology\Programming\An_Tir_Authorizations_project\offline_backups\an-tir-auth-backup.key"
$RestoreDb = "an_tir_authorizations_restore_test"
$MysqlUser = "root"
$OpenSslExe = "C:\Program Files\Git\usr\bin\openssl.exe"
$GzipExe = "C:\Program Files\Git\usr\bin\gzip.exe"
$TempDir = "$env:TEMP\an_tir_authorizations_restore_test"
$EncryptedSqlGz = Join-Path $TempDir "restore.sql.gz"
$SqlFile = Join-Path $TempDir "restore.sql"
$RewrittenSqlFile = Join-Path $TempDir "restore-to-test-db.sql"
New-Item -ItemType Directory -Force -Path $TempDir
```

Replace `root` with the local MySQL user you normally use if needed. The `-p` flag in later commands makes MySQL prompt for that user's password.

### 2. Prepare The Scratch Database

This wipes any prior restore-test contents before loading the selected backup.

```powershell
mysql -u $MysqlUser -p -e "DROP DATABASE IF EXISTS $RestoreDb; CREATE DATABASE $RestoreDb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

When prompted, enter the password for the local MySQL user.

### 3. Decrypt And Decompress The Backup Temporarily

This creates temporary decrypted SQL files under your Windows temp directory. They are removed at the end of this procedure.

```powershell
& $OpenSslExe enc -d -aes-256-cbc -pbkdf2 -pass file:"$KeyFile" -in "$BackupFile" -out "$EncryptedSqlGz"
& $GzipExe -dc "$EncryptedSqlGz" > "$SqlFile"
```

### 4. Rewrite Any Production Database Statements

Some dumps include `CREATE DATABASE` or `USE` statements for `an_tir_authorizations`; others contain only table statements. This rewrite is safe for both shapes.

```powershell
$reader = [System.IO.StreamReader]::new($SqlFile)
$writer = [System.IO.StreamWriter]::new($RewrittenSqlFile, $false, [System.Text.UTF8Encoding]::new($false))
try {
    while (($line = $reader.ReadLine()) -ne $null) {
        $line = $line -replace 'CREATE DATABASE .*`an_tir_authorizations`.*', "CREATE DATABASE IF NOT EXISTS ``$RestoreDb``;"
        $line = $line -replace 'USE `an_tir_authorizations`;', "USE ``$RestoreDb``;"
        $writer.WriteLine($line)
    }
}
finally {
    $reader.Close()
    $writer.Close()
}
```

### 5. Import The Backup Into The Scratch Database

Always include `$RestoreDb` in the import command. Some backups do not contain a `USE` statement, and importing without a database selected fails with `ERROR 1046: No database selected`.

```powershell
$ImportCommand = 'mysql -u ' + $MysqlUser + ' -p ' + $RestoreDb + ' < "' + $RewrittenSqlFile + '"'
cmd /c $ImportCommand
```

### 6. Verify And Inspect The Restored Backup

Check that tables were restored:

```powershell
mysql -u $MysqlUser -p -e "SHOW TABLES;" $RestoreDb
```

Check key row counts and recent users:

```powershell
mysql -u $MysqlUser -p -e "SELECT COUNT(*) AS users FROM authorizations_user;" $RestoreDb
mysql -u $MysqlUser -p -e "SELECT COUNT(*) AS people FROM authorizations_person;" $RestoreDb
mysql -u $MysqlUser -p -e "SELECT COUNT(*) AS authorizations FROM authorizations_authorization;" $RestoreDb
mysql -u $MysqlUser -p -e "SELECT id, username, email, date_joined FROM authorizations_user ORDER BY id DESC LIMIT 10;" $RestoreDb
```

To browse the data, point your local MySQL client at:

```text
database: an_tir_authorizations_restore_test
```

Keep this database read-only in practice. It is for inspection and restore verification, not for application use.

STOP: this is where you can inspect the database and verify the backup is correct. The next steps remove decrypted working files and, optionally, wipe the scratch database.

### 7. Remove Temporary Decrypted Files

Delete the temporary decrypted working files:

```powershell
Remove-Item -LiteralPath $EncryptedSqlGz, $SqlFile, $RewrittenSqlFile -Force -ErrorAction SilentlyContinue
Get-ChildItem -Force $TempDir
```

Only the original encrypted offline backup file and separately stored key should remain.

### 8. Wipe The Restored Data When Done

When you are done inspecting the backup, drop and recreate the scratch database so the backup contents are no longer present in an open database.

```powershell
mysql -u $MysqlUser -p -e "DROP DATABASE IF EXISTS $RestoreDb; CREATE DATABASE $RestoreDb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u $MysqlUser -p -e "SHOW TABLES;" $RestoreDb
```

Expected result:
- no tables are listed.

## Failure Modes And Responses

### Manual Dump Fails

- Symptom: `mysqldump` exits with `ERROR 1045`.
- Cause: wrong MySQL password for `antir_app`.
- Response: rerun the `mysqldump` command and enter the correct database password.

### Encryption Fails

- Symptom: `openssl` cannot read `/etc/an-tir-auth-backup.key`.
- Cause: the key is root-readable.
- Response: run the OpenSSL encryption command with `sudo`, as shown in the monthly backup procedure.

### Restore Import Fails With No Database Selected

- Symptom: `ERROR 1046 (3D000): No database selected`.
- Cause: the dump does not contain a `USE` statement.
- Response: include the scratch database name in the import command:

```powershell
$ImportCommand = 'mysql -u ' + $MysqlUser + ' -p ' + $RestoreDb + ' < "' + $RewrittenSqlFile + '"'
cmd /c $ImportCommand
```

### Remote Backup Cadence Looks Wrong

- Symptom: DigitalOcean Spaces does not show a recent dated backup object.
- Response: take a manual encrypted monthly backup using this runbook, then separately investigate the remote backup automation.

### Disk Pressure

- Risk: local disk fills.
- Mitigation:
  - remove temporary unencrypted `.sql` and `.sql.gz` files after the `.sql.gz.enc` file is created and downloaded;
  - monitor `/home/antir/db_backups`;
  - prune old encrypted manual files after they are safely retained offline.

## Monitoring Requirements

At minimum, monitor:

- root filesystem usage (`/`);
- presence of recent encrypted files in `/home/antir/db_backups`;
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

As of 2026-06-05:

- DigitalOcean Spaces contains recent dated backup objects.
- Manual `mysqldump` to `/home/antir/db_backups` was verified.
- Manual encryption with `/etc/an-tir-auth-backup.key` was verified.
- Download of an encrypted `.sql.gz.enc` backup to the local backups folder was verified.
- Local restore flow requires importing with the scratch database name because the manual dump may not include a `USE` statement.
- The older `/var/backups/an-tir-authorizations` and `an-tir-auth-backup-*` systemd workflow was not present in the observed shell session and should not be treated as current without revalidation.

## Ownership And Handoff Notes

- Confirmed monthly backup logic is the manual encrypted procedure in this runbook.
- Remote daily backup automation exists because DigitalOcean Spaces has current files, but its implementation path still needs to be revalidated and documented.
- Server-local credentials and keys must remain protected.
- Offline monthly archives should be kept outside the droplet and Spaces bucket.
- The system is designed to be transferred to Kingdom ownership with minimal changes.
