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
3. DigitalOcean production

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

## Staging Deploy to PythonAnywhere

To edit the .env file
```bash
nano .env
```

1. Close any existing consoles and start up a new one from the [web app page](https://www.pythonanywhere.com/user/feradach/webapps/#tab_id_feradach_pythonanywhere_com). In the console pull the code:

```bash
git pull
```

2. If neccessary, deploy core changes:

```bash
python -m pip install -r requirements.txt
python -m dotenv run -- python manage.py check
python -m dotenv run -- python manage.py migrate
python -m dotenv run -- python manage.py collectstatic --noinput
```

3. Restart the server and try to load the page.

## Production Deploy to DigitalOcean
Before production deployment, promote shipped `CHANGELOG.md` entries out of `## [UNRELEASED]` and into the numbered release section. Production should have `RELEASE_ENV=production` in its environment so the release check blocks unreleased changelog entries.

To edit the .env file
```bash
nano /home/antir/apps/an_tir_authorizations/.env
```

1. SSH into the server and enter the app directory:

```bash
ssh antir@138.68.242.105
cd /home/antir/apps/an_tir_authorizations
```

2. Activate the virtual environment:

```bash
source venv/bin/activate
```

3. Confirm the current branch:

```bash
git status -sb
```

If the server is on the wrong branch:

```bash
git branch -r
git checkout main
git status -sb
```

4. Pull the code that already passed local and staging validation:

```bash
git pull origin main
```

5. Run pre-deploy checks:

```bash
python manage.py check
python manage.py migrate --check
python manage.py check_release_ready
```

If `check_release_ready` fails, do not continue the production deploy. Fix the changelog/release issue locally, push again, then pull again on the server.

6. Install requirements, apply migrations, and collect static files as needed:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

7. Restart and verify the service:

```bash
sudo systemctl restart an_tir_authorizations.service
systemctl status an_tir_authorizations.service --no-pager
```

The service should report `active`. Also smoke test the public site before tagging the release.

8. After production is verified, tag the exact deployed commit and push the tag:

```bash
git tag -a vX.X.X -m "Release vX.X.X"
git push origin vX.X.X
```

Use the actual release version from `CHANGELOG.md`.

## Production Rollback
If the deploy fails before migrations are applied, return to the previous stable release tag and restart:

```bash
git fetch --tags
git checkout vX.X.X
sudo systemctl restart an_tir_authorizations.service
systemctl status an_tir_authorizations.service --no-pager
```

Use the actual previous stable tag. If migrations already ran, rollback may require a forward fix or restoring from backup; do not assume a code checkout alone is enough.

## Release Hygiene
- User-visible behavior changes should have a user-facing `CHANGELOG.md` entry under `## [UNRELEASED]` until release time.
- Production release versions are marked by Git tags such as `v1.1.2` after the deployed site is verified.
- Do not move or reuse pushed release tags. Use the next patch version for hotfixes.
- Security-sensitive changes must include targeted tests.
- Do not deploy late if there is no time to monitor and fix issues.
- If a production deploy breaks a critical workflow and the fix is not obvious quickly, roll back rather than debugging live for an extended period.
