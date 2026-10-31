# Society two-person integrity: integrated release candidate

Prepared: 2026-10-18. Branch: `agent/society-two-person-integrity`.
Base: `a9c538cc0d3c610a8f6f241955ecb9aa155436fd` (fetched `origin/main`).

On 2026-10-31, the owner authorized combining this feature with the history fixes
and the approved FAQ/home-page updates, validating it, and merging it into local
`main` for review. Integration branch: `agent/society-release-integration`.
Hosted publication, staging, and production deployment remain separate steps.
The code enforces the rule permanently when deployed; there is no feature switch.
An old `AUTHZ_REQUIRE_FIGHTER_CONCURRENCE` environment value has no effect.

## Rule and scope

- First authorization in a discipline, or no fully valid authorization in that
  discipline within the preceding calendar year: a separate eligible witness is
  required. Exactly one year since expiry remains within the renewal window.
- The witness can hold any valid authorization in that discipline. The witness
  cannot be the recipient or the proposing marshal. IDs identify all three.
- Equestrian is included. Siege, Youth Armored, and Youth Rapier remain exempt.
- Junior/Senior Marshal authorizations retain their existing marshal approvals.
- Normal officer review and Equestrian waiver review still follow concurrence.
- Paper entry enforces the same rule on the form's authorization date. There is
  no historical cutoff exception, per the owner's confirmation that old forms
  have already been processed.

The Society text says “new or significantly lapsed”; the one-year and per-discipline
interpretation above is the owner's existing kingdom policy retained by this work.

Sources checked on 2026-10-18:

- [Global authorization](https://society-rules.fullmesh.co.nz/index.php/Global:Authorization#Two_person_integrity_rule), revision 1541.
- [Equestrian authorization](https://society-rules.fullmesh.co.nz/index.php/Equestrian:Authorization), revision 4285, includes the two-person rule.
- [Siege authorization](https://society-rules.fullmesh.co.nz/index.php/Siege:Marshaling_And_Authorization), revision 2038.
- [Youth authorization](https://society-rules.fullmesh.co.nz/index.php/Youth_Combat:Youth_Combat_Authorizations), revision 1157.

## History and data

Electronic and paper eligibility use the same recorded validity intervals.
Dependent styles must overlap the prerequisite's valid periods, so a Rapier
secondary does not conceal a Single Sword lapse. Membership/background-check
gaps recorded for marshals remain gaps after later updates. Sanction dates are
excluded, including dates before a sanction was lifted. Synthetic expired dates
used for display are no longer written as validity history.

No database migration or bulk repair is part of this change. Reviewed existing
history is retained. Missing history is not guessed: entry requires concurrence
or rejects an unverified historical signer. The branch cannot reconstruct gaps
that were already lost or data written directly to the database without history.
This work does not certify production data. Feature validation uses local test
fixtures; the separately authorized production backup is not a data-quality audit.

## Retirement and release

The pause-only `advance_fighter_concurrence_authorizations` command and the legacy
CSV authorization importer are removed. Their code remains in Git history for a
future explicitly reviewed recovery operation. Normal paper-form entry and its
audit CSV download remain supported. The membership-roster importer is unrelated
and remains available.

The owner authorized the local merge on 2026-10-31 after integration review and
validation. Obtain publication and staging authorization before hosted release.
Stage the exact release commit, then wait for new explicit production approval
under `deployment_workflow.md`. Passing local checks does not authorize deployment.

## Local validation

On 2026-10-18, all 658 Django tests passed against the isolated SQLite in-memory
database in `authorizations.tests.test_settings`; email used Django's in-memory
backend. `makemigrations --check --dry-run` reported no changes, and
`git diff --check` passed. No production or staging data was read or modified.

Run with `AUTHZ_TEST_FEATURES=0`, `EMAIL_DELIVERY_MODE=file`,
`DEFAULT_FROM_EMAIL=tests@example.test`, and `RELEASE_ENV=local` in the test process:

```powershell
venv\Scripts\python.exe manage.py test --settings=authorizations.tests.test_settings --noinput
venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=authorizations.tests.test_settings
```

On 2026-10-31, the combined history, Society rule, FAQ, and home-page changes
passed all 679 tests using the same isolated settings. Migration detection found
no changes, and `git diff --check` passed. The isolated worktree used the existing
repository virtual environment without copying credentials. The owner authorized
merging this verified result into local `main`. No push or hosted deployment was
performed; hosted acceptance remains a release step.
