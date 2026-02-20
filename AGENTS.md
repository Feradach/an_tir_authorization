# AGENTS.md

## Purpose
Instructions for coding agents working in this repository. Keep changes focused, safe, and easy to review.

## Project Context
- Stack: Django app (`authorizations`) with templates and static assets.
- Public-facing auth workflows exist (login, account recovery, fighter page actions).
- Roles/permissions are central to behavior (marshal, regional, kingdom officer roles).

## High-Impact Rules
- Preserve permission boundaries. Do not loosen auth checks unless explicitly requested.
- Avoid account/email enumeration in recovery flows unless explicitly approved by the owner.
- Keep rate limiting/throttling in place for account recovery and login-instruction flows.
- Prefer server-side enforcement for any action that appears conditionally in UI.

## Where Things Live
- Main app logic: `authorizations/views.py`, `authorizations/permissions.py`, `authorizations/models.py`
- Templates: `authorizations/templates/authorizations/`
- Tests: `authorizations/tests/`
- Changelog: `CHANGELOG.md`

## Editing Guidelines
- Make minimal, surgical changes that solve the requested issue.
- Match existing coding and template style.
- Do not refactor unrelated code in the same change.
- Do not remove existing logging/messages unless necessary for the requested change.
- Keep user-facing text clear and non-ambiguous.

## Testing Expectations
- Add/update targeted tests when behavior changes.
- Prefer focused test execution first, then broader tests when feasible.
- If local DB/services are unavailable, report what was attempted and what blocked validation.

## Common Commands
- Run app: `venv\Scripts\python.exe manage.py runserver`
- Run all tests: `venv\Scripts\python.exe manage.py test`
- Run one test: `venv\Scripts\python.exe manage.py test authorizations.tests.test_views.SomeTestClass.test_name`

## Release Hygiene
- For user-visible behavior changes, add an entry to `CHANGELOG.md`. Entry should be added as if it is talking to an end user, not a developer. For instance, it should not reference any internal implementation details.
- For security-sensitive changes (auth, recovery, permissions), include tests in the same change.

## Avoid
- Destructive git operations (`reset --hard`, force-checkouts) unless explicitly requested.
- Silent behavior changes in authentication/recovery flows.
- Introducing new dependencies unless clearly justified.
