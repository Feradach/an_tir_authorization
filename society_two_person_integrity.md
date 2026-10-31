# Society two-person integrity: parked activation branch

Prepared: 2026-10-18. Branch: `agent/society-two-person-integrity`.
Base: `a9c538cc0d3c610a8f6f241955ecb9aa155436fd` (fetched `origin/main`).

This is a local activation candidate awaiting the owner's instruction following
the Earl Marshal's go-ahead. It is not deployed or authorized for deployment.
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
This work does not certify the production data; no production data was accessed.

## Retirement and release

The pause-only `advance_fighter_concurrence_authorizations` command and the legacy
CSV authorization importer are removed. Their code remains in Git history for a
future explicitly reviewed recovery operation. Normal paper-form entry and its
audit CSV download remain supported. The membership-roster importer is unrelated
and remains available.

Before release, fetch current `origin/main`, integrate intervening approved work,
review conflicts, and rerun the full isolated test suite. Obtain publication and
staging authorization. Follow the owner's branch review and two-stage staging
workflow: branch staging and merge approval, then staging of the exact merged
release commit and new explicit production approval. Do not deploy this branch
merely because its local checks pass.

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

The isolated worktree used the existing repository virtual environment's Python;
it did not copy environment files or credentials. Hosted acceptance and browser
smoke testing remain release steps. No branch push, merge, or deployment has
been performed as part of this work.
