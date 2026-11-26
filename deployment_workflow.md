# Deployment Workflow

## Purpose
This document is the authoritative branch, staging, production, and rollback workflow, whether one person or several volunteers contribute. Use it unless the owner explicitly approves a different plan. Last updated 2026-11-25.

Committing saves a local snapshot. Pushing publishes Git history to GitHub. Merging combines reviewed changes into `main`. Deploying changes the running site. These are separate actions; completing one does not authorize the next.

## Agent-operated releases

The owner approves the release decisions in chat or on the pull request. The agent executes the authorized Git, staging, production, verification, and tagging steps. A single message can authorize multiple named actions, such as publishing the review branch and deploying that branch to staging. Production approval applies to the exact merged commit verified on final staging and can cover the deployment, service restart, live verification, and release tag. Include compatible rollback authority in that approval if the agent should be able to recover without another handoff.

When the release version is known before branch review, include the numbered changelog in the release PR before its staging round. This lets one PR cover both the behavior and its release notes. If the notes change after final staging, test the resulting merged commit again.

### Authenticated access

- Use the authorized GitHub connector or Git credential helper for GitHub. Use the designated PythonAnywhere account or its existing authenticated browser session for staging. Use the configured SSH identity and pinned host key for production.
- The agent may sign in with an approved account and its password when a supported connector, credential helper, or secret store can supply the credential directly to the intended service. The owner provisions access once and can rotate or revoke it. Keep passwords, tokens, and keys out of chat, repositories, command arguments, and logs. If the authentication provider requires direct user participation, request it at that step.
- Before changing the production checkout, test access to GitHub, the production host, and the exact non-interactive restart permission. Resolve access issues before deploying.

### One-time production restart setup

The production service and the existing deployment SSH account both run as `antir`. A server administrator installs [the restart rule](scripts/deploy/production-restart.sudoers) as `/etc/sudoers.d/an-tir-deploy` using `sudo visudo -f /etc/sudoers.d/an-tir-deploy`. Confirm the drop-in is owned by `root:root`, has mode `0440`, and passes `sudo visudo -c`. This access change needs its own owner approval. Keep the sudoers file and the systemd unit root-controlled.

From a new SSH connection as `antir`, check the delegated permission without restarting the service.

```bash
sudo -n -l -- /usr/bin/systemctl restart an_tir_authorizations.service
```

The rule delegates this exact restart command to the deployment account. Its authority applies to anyone who can authenticate as `antir`. Revoke it by removing the single drop-in through the administrator's approved access change and validating sudoers again.

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

### Test email inbox and delivery gates

- File delivery requires `AUTHZ_TEST_FEATURES=1` and a non-production environment.
  Non-test sites must select `EMAIL_DELIVERY_MODE=gmail` or `smtp` with the required
  provider configuration. A conflicting file configuration blocks startup.
- Browser previews additionally require `AUTHZ_EMAIL_PREVIEW_ENABLED=1`,
  `RELEASE_ENV=local` or `staging`, and file delivery. Defaults are disabled and
  allowlist access. See README for the full setup and tester ID examples.
- `AUTHZ_EMAIL_PREVIEW_ACCESS=allowlist` uses the server-configured
  `AUTHZ_EMAIL_PREVIEW_USER_IDS`. `public` deliberately exposes all saved test mail
  and recovery links to everyone, including anonymous visitors. Use public access
  only with fake data and disposable accounts; never use production passwords,
  signing secrets, database copies containing real information, or mail folders.
- Configure these settings through the server environment and restart. They cannot
  be changed through a request, profile, officer role, or the preview page.
- Keep `EMAIL_FILE_PATH` private and outside all web-server static/media aliases.
  The inbox checks Django's public paths, but cannot inspect external hosting aliases.
  Page responses are private/no-store; do not override that with proxy caching.
- For production, set `AUTHZ_TEST_FEATURES=0`, `AUTHZ_EMAIL_PREVIEW_ENABLED=0`,
  `RELEASE_ENV=production`, and the chosen real-mail provider. The production release
  check rejects preview opt-in. Preview endpoints also deny production requests.
- Changing delivery to a real provider never sends previously saved files. Retire
  old test mail through normal private-folder housekeeping, not through this page.

## Mandatory Review and Production Approval Gates

Staging and production are two separate deployment phases. Completing a staging deploy never authorizes a production deploy.

There are two staging rounds: the feature/hotfix branch before merge, and the resulting `main` release commit after merge. After each round, stop and report:

- the staging URL;
- the exact commit deployed;
- the branch and whether this is pre-merge or final release testing;
- the checks and smoke tests that passed or failed; and
- any known caveats or items that still need the owner's review.

After branch testing, wait for the owner's merge approval. After final `main` testing, wait for the owner to perform acceptance testing and give a new, explicit instruction to deploy that exact commit to production. Approval to merge does not authorize production deployment. A stated intention to launch later or "launch today if everything looks good" does not replace this approval gate.

The commit approved on staging must be the commit deployed to production. If code, configuration, migrations, or release documentation changes after staging approval, deploy the new commit to staging and repeat this gate before touching production.

## Branch Policy
- `main` is the human-reviewed integration and release branch. This repository uses `main`, not a separate `master`. The currently deployed production commit may be older than `main`; record it independently.
- Create a dedicated branch before any edits, including small fixes, configuration changes, and documentation. There is no direct-to-`main` small-change exception.
- Use `agent/<work-item-slug>` for agent work, including `agent/hotfix-<slug>` for urgent fixes. Keep each branch limited to one bounded work item.
- Start from the latest fetched `origin/main` after checking that it is the appropriate base. Record the base SHA. Using a release tag or another feature branch as the base requires the owner's agreement, and the dependency must be stated in the review handoff.
- Preserve dirty checkouts. Use a separate worktree for concurrent work rather than switching branches, stashing someone else's changes, or copying unfinished work into a hotfix. Set up its local environment deliberately; worktrees do not automatically copy ignored `.env` files or virtual environments. Use separate ports and test databases when concurrent tests or schema changes could interfere.
- Agents may commit locally when review-ready. Push only the authorized branch when the owner authorizes publication; do not push directly to `main`, force-push, merge, or deploy autonomously. Owner approval is required for merging and for each deployment scope.
- No permanent `develop` branch is required. Feature branches can coexist, but a shared staging site must have a clearly identified branch and exact SHA. Coordinate its use before replacing another tester's deployment.

## Standard Change Flow

1. **Branch and stabilize locally.** Create the branch before editing. Implement the bounded change, update applicable documentation and `CHANGELOG.md`, run targeted checks and the appropriate broader suite, and review the diff. Documentation-only changes need consistency, command, and link checks rather than application tests unless runtime behavior also changes.
2. **Publish the branch for review.** With owner authorization, push the branch and open a pull request into `main`. Report its base SHA, head SHA, validation, and any dependent work. A local commit is not a GitHub backup until it is pushed.
3. **Test the branch on staging.** With staging authorization, deploy the identified branch commit to PythonAnywhere and perform smoke/acceptance tests. Fix problems on the branch, validate locally, push authorized updates, and repeat branch staging testing. Stop for owner review and merge approval.
4. **Merge the reviewed result.** Check whether `main` has advanced. If so, bring those changes into the feature branch, resolve conflicts, retest, and renew staging acceptance and approval for the changed result. After approval, merge the pull request into `main`. The merge updates GitHub's `main`; it does not deploy production. Prepare the intended numbered release/changelog before final staging, through the same reviewed branch process if a separate release-preparation change is needed.
5. **Test the final release commit on staging.** Deploy the exact resulting `main` commit, including version/release documentation, to staging and repeat stability and acceptance tests. Record the full SHA. A passing pre-merge branch test does not replace this round. If `main` later advances, the approved SHA remains pinned; newer commits need their own staging validation and approval.
6. **Request production approval and deploy.** Stop and obtain a new, explicit owner approval for that exact final staging SHA. Deploy only it to production, verify the live site, then create and push the matching version tag when authorized. Never replace the approved SHA with whatever happens to be newest on `main`.
7. **Finish the handoff.** Report the branch/base SHA, merged/release SHA, staging and production results separately, release tag, and outstanding issues. Retire the feature branch/worktree when its work is merged and no longer needed; preserve any uncommitted or ignored files still needed.

## Keeping Unfinished Branches Current

Changes merged into `main` do not automatically appear in a feature branch. After a hotfix or other relevant merge, promptly synchronize each unfinished branch before further dependent work, and always check again before final review.

In the feature branch's clean worktree, fetch the latest remote state and merge it:

```bash
git status --short
git fetch origin
git merge origin/main
```

Confirm the checkout is the intended feature branch and is clean before running the merge. If it is dirty, preserve that work and arrange a clean checkpoint with its owner; do not force a switch or discard changes. Review conflicts and the combined behavior even when Git reports an automatic merge. Run relevant tests and repeat staging validation for the changed commit. Coordinate with a collaborator before modifying their branch or fork.

## Hotfix Flow
For urgent production fixes:

1. Record the current production SHA/tag and check current `origin/main`. Create a separate `agent/hotfix-<slug>` branch/worktree from the appropriate reviewed `main` base, leaving unfinished features untouched. If `main` contains unapproved release changes or is failing staging, ask the owner to approve a base at the deployed release commit instead; do not silently include unreleased work in an emergency deployment.
2. Fix only the emergency and run local checks. Push the hotfix branch and open a pull request when authorized.
3. Test the hotfix branch on staging, obtain merge approval, merge into `main`, and test the final merged release commit on staging. If `main` contains other changes, all changes in the final candidate need acceptance; an emergency does not authorize releasing them implicitly.
4. Obtain explicit production approval for the final tested SHA, deploy and verify it, then tag the release. Critical fixes may bypass the Wednesday schedule, but not the branch, testing, or approval gates.
5. Promptly merge updated `origin/main` into unfinished feature branches, resolve conflicts, and retest. Their work can then continue with the emergency fix included.

## If Final Staging Fails After Merge

Do not deploy production or create a successful release tag. Either fix the problem on a new branch through the same review/staging sequence, or open a revert pull request that reverses the faulty merge. The owner approves the remedy and its merge. Test the corrected/reverted `main` commit on staging before requesting any production deployment.

Use a revert commit to preserve shared Git history; do not erase the merge with `reset --hard` or force-push `main`. GitHub's merged pull request Revert action can prepare this change, though conflicts may need manual resolution. Reintroducing a reverted feature requires a new reviewed change; do not assume merging the unchanged old branch will reapply it.

Reverting code does not reverse database migrations, file changes, or data edits. Check compatibility and plan the required migration/restore work separately. If necessary, restore staging to its previous compatible tested state while preparing the fix. Production remains on its existing version throughout a staging-only failure.

## Volunteer Contributions and Forks

A fork is a separate repository; a contributor can develop an offshoot without changing this repository. Contributions returning here go through a pull request and the same local, branch-staging, merge, final-staging, and production-approval gates. Review the actual diff and tests regardless of where the branch originated. Coordinate shared staging explicitly, keep secrets out of Git, and do not assume code collaboration grants production or database access.

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

1. Coordinate use of the shared staging site, then open a console from the [web app page](https://www.pythonanywhere.com/user/feradach/webapps/#tab_id_feradach_pythonanywhere_com). Do not close another contributor's active console. In the application directory, inspect the checkout and fetch published code:

```bash
git status --short
git fetch origin --tags
```

Stop for unexpected local changes; preserve and resolve them before checkout. For a fork contribution whose commit is not present in the fetched branches, explicitly fetch the reviewed pull request ref before proceeding. Record whether this is a branch test or final merged `main` test. Replace `STAGING_FULL_COMMIT_SHA` below with the full SHA selected for that round:

```bash
git checkout --detach STAGING_FULL_COMMIT_SHA || exit 1
git rev-parse HEAD
```

Confirm the printed SHA matches the intended commit. The detached checkout keeps the test pinned while branches move. For final release testing, also verify this commit is in the fetched `main` history:

```bash
git merge-base --is-ancestor STAGING_FULL_COMMIT_SHA origin/main || exit 1
```

2. Activate the staging virtual environment. Use staging-only configuration and data. To edit its environment file, use `nano .env`. Review any migrations, then deploy the changes:

```bash
python -m pip install -r requirements.txt
python -m dotenv run -- python manage.py check
python -m dotenv run -- python manage.py migrate --plan
python -m dotenv run -- python manage.py migrate
python -m dotenv run -- python manage.py collectstatic --noinput
```

3. Reload the staging web app and perform smoke and acceptance tests. Record results against the exact SHA. Changed code requires a new deployment and repeated testing.

4. Stop and report the staging URL, source branch, testing round, SHA, and results. Branch testing requests owner merge approval; final `main` testing requests a new production approval. Do not continue into production during the same deployment phase.

## Production Deploy to DigitalOcean

Begin only after the owner explicitly approves the exact merged release commit tested on final staging for production deployment.

Before requesting approval, move the release's `CHANGELOG.md` entries out of `## [UNRELEASED]` into its numbered section through the reviewed branch process. Merge that preparation and test the resulting exact `main` commit on staging. Production must have `RELEASE_ENV=production` so the release check enforces production settings and blocks unreleased changelog entries. Do not edit release notes or code after approval without repeating final staging and approval.

1. SSH into the server and enter the application directory:

```bash
ssh antir@138.68.242.105
cd /home/antir/apps/an_tir_authorizations
source venv/bin/activate
```

The production environment file is `/home/antir/apps/an_tir_authorizations/.env`. Environment changes must be included in the deployment's validation and approval; do not improvise them to bypass a failed check.

2. Record the currently deployed SHA and inspect local changes. Confirm the rollback target and backup/migration plan within the approved deployment scope:

```bash
git status -sb
git rev-parse HEAD
sudo -n -l -- /usr/bin/systemctl restart an_tir_authorizations.service
```

Stop for unexpected modifications. Do not overwrite server changes or force a checkout. Replace `APPROVED_FULL_COMMIT_SHA` below with the full SHA approved after final staging:

```bash
git fetch --no-tags origin main
git merge-base --is-ancestor APPROVED_FULL_COMMIT_SHA origin/main || exit 1
git checkout --detach APPROVED_FULL_COMMIT_SHA || exit 1
git rev-parse HEAD
```

Confirm the printed SHA matches the owner's approval before continuing. Do not pull a moving branch or deploy a newer commit. The detached checkout is the intended production state.

3. Install requirements for that approved commit if needed, then run pre-deploy checks:

```bash
pip install -r requirements.txt
python manage.py check
python manage.py check_release_ready
python manage.py migrate --plan
```

If a check fails or the migration plan differs from the reviewed plan, stop. Correct code or release documentation on a branch, repeat staging validation and approval, and deploy the newly approved SHA. Configuration corrections also require renewed staging validation and approval for the deployment. Do not patch production to bypass the gate.

4. Confirm the required backup is available, then apply the approved migrations and collect static files:

```bash
python manage.py migrate
python manage.py migrate --check
python manage.py collectstatic --noinput
```

5. Restart and verify the service:

```bash
sudo -n -- /usr/bin/systemctl restart an_tir_authorizations.service
systemctl status an_tir_authorizations.service --no-pager
git rev-parse HEAD
```

Confirm `active`, that the service started after checkout, the approved SHA, and the live smoke-test results before tagging the release. Use read-only production smoke tests for account workflows. Keep a failed restart or smoke check untagged and follow the authorized rollback scope.

6. After production is verified, create and push the release tag when authorized. Replace `vX.X.X` with the actual version from `CHANGELOG.md` and use the same approved full SHA:

```bash
git tag -a vX.X.X APPROVED_FULL_COMMIT_SHA -m "Release vX.X.X"
git rev-list -n 1 vX.X.X
```

Confirm the tag's printed SHA matches the deployed SHA, then push it:

```bash
git push origin vX.X.X
```

A GitHub Release, if used, references this same tag; never tag a newer `main` tip merely because it exists.

## Production Rollback

Obtain rollback authorization, which may be included explicitly in the deployment approval. Record the previous stable tag before deploying. A code-only rollback is appropriate only after checking database, dependency, configuration, and static-file compatibility. If migrations or data changes have run, a forward fix or an explicitly approved restore may be required; reverting code does not undo them.

For an approved compatible rollback, replace `PREVIOUS_STABLE_TAG` with the recorded tag. Inspect and preserve unexpected server changes before checkout:

```bash
git status --short
git fetch origin --tags
git checkout --detach PREVIOUS_STABLE_TAG || exit 1
git rev-parse HEAD
pip install -r requirements.txt
python manage.py check
python manage.py collectstatic --noinput
sudo -n -- /usr/bin/systemctl restart an_tir_authorizations.service
systemctl status an_tir_authorizations.service --no-pager
```

Confirm the expected SHA and smoke-test the site. Detached HEAD is intentional. Review generated `staticfiles/...` modifications separately from source changes; never discard unexpected changes automatically.

A server rollback does not remove a merge from GitHub. Repair shared history with a reviewed fix or revert pull request. To roll forward again, follow the full final-staging and production-approval process and deploy the exact newly approved SHA. Do not switch production to the latest `main` as a shortcut.

## Release Hygiene

- User-visible changes go under `## [UNRELEASED]` until release preparation. Include numbered release notes before final staging so approval covers the entire release commit.
- Production versions use immutable tags such as `v1.1.2`, created after the deployed site is verified. Never move or reuse a pushed tag; choose the next version for a corrected release or hotfix.
- Security-sensitive changes include targeted tests in the same branch.
- Do not deploy without time to monitor and verify the result.
- If production breaks and a quick fix is unclear, use the approved rollback plan rather than extending unreviewed production changes.
