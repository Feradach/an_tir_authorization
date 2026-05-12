# Deployment Workflow

## Purpose
This document records the working deployment decisions for this live solo project. Use it as the default release policy unless the owner gives different instructions.

## Cadence
- Production deploys normally happen on Wednesday morning.
- Skip the Wednesday deploy if there is nothing new and validated to release.
- Deploy outside the normal cadence only for critical production issues, such as broken login, security/permission failures, data corruption, production crashes, or blocked core authorization workflows.

## Environments
The normal progression is:

1. Local development
2. PythonAnywhere staging/test site
3. Production

Local validation should happen before code is pushed for normal release work. Staging validation should happen before a change is considered ready for the Wednesday production release.

Staging should use its own configuration and should not share production secrets or write to production systems. For email testing, prefer the file email backend first so staging does not accidentally contact real users. Switch to Gmail/API email only when intentionally testing outbound email behavior.

## Branch Policy
Do not use a permanent `develop` branch by default. It is likely to become a second messy `main` in this solo workflow.

Use this model:

- `main`: the current release path and expected deployable state.
- Short-lived feature branches: temporary isolation for larger work.
- Hotfix branches: narrow urgent fixes from `main`.

Branches are for isolation, not parallel routine development.

## Default Small-Change Flow
For small, bounded changes that can be completed and validated locally in one sitting:

1. Work locally on `main`.
2. Make the code work locally.
3. Run targeted checks/tests for touched behavior.
4. Push `main`.
5. Deploy `main` to PythonAnywhere staging.
6. Smoke test staging.
7. If staging passes, leave the change queued for the Wednesday production deploy.
8. If staging fails, fix `main` before any production deploy.

This flow is acceptable because `main` is still treated as "should be deployable", not as scratch space.

## Larger-Change Flow
Use a branch when the work:

- will likely span multiple sessions;
- has architectural uncertainty;
- may need to be abandoned or postponed;
- touches auth, permissions, account recovery, email, imports, migrations, deployment settings, or other high-risk workflows;
- could block regular production deploys if kept on `main`.

Examples include offline database support, major permission model changes, import/recovery overhauls, email backend changes, and substantial schema work.

When a feature branch is active, normal development on `main` pauses. The only expected exceptions are urgent hotfixes, release-blocking fixes, or trivial docs/admin cleanup that cannot reasonably wait.

If `main` changes while a feature branch is active, bring it back into the branch promptly:

```bash
git switch feature/some-change
git merge main
```

After the branch is complete:

1. Validate locally.
2. Deploy/test the branch on staging if needed.
3. Merge to `main` only when it is ready for the release queue.
4. Delete the branch after merge.

## Hotfix Flow
For urgent production fixes:

1. Start from current `main`.
2. Create a narrow `hotfix/...` branch if useful.
3. Fix only the production issue.
4. Run targeted tests/checks.
5. Merge to `main`.
6. Deploy outside the Wednesday cadence if the issue is critical.
7. If another feature branch exists, merge `main` back into that branch immediately to prevent drift.

## Validation Expectations
Before production deployment, run checks appropriate to the change. For mature or risky changes, prefer:

```bash
venv\Scripts\python.exe manage.py test
venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

On PythonAnywhere staging, typical deployment validation is:

```bash
python -m dotenv run -- python manage.py check
python -m dotenv run -- python manage.py migrate
python -m dotenv run -- python manage.py collectstatic --noinput
```

Smoke test the hosted site after reload. At minimum, check that the site loads, login works, key authorization workflows behave correctly, permission-gated pages still enforce access, and account recovery/login-instruction behavior remains safe.

## Release Hygiene
- User-visible behavior changes should have a user-facing `CHANGELOG.md` entry.
- Security-sensitive changes must include targeted tests.
- Do not deploy late if there is no time to monitor and fix issues.
- If a production deploy breaks a critical workflow and the fix is not obvious quickly, roll back rather than debugging live for an extended period.
