# Authorization history integrity follow-up

Prepared 2026-10-18 on `agent/authorization-history-integrity`.
Base: fetched `origin/main`, `a9c538cc0d3c610a8f6f241955ecb9aa155436fd`.

## Reproduced defects

- A second membership update after a lapse could extend an older validity period across the gap. An ordinary authorization save or renewal could do the same.
- Missing membership or prerequisite authorization could turn a display-only "expired" placeholder into false historical evidence. Removing membership could also erase legitimate recent history by shortening it to that placeholder.
- Model status changes could leave validity open. Correcting a prerequisite to an already-expired date skipped updates to dependent history.
- Partial saves could write validity from an unsaved in-memory expiration, while unrelated authorization updates could create history for other styles.
- Background-check approval used a bulk status update without creating validity history. Replacement workflows for youth categories and ground crew also used bulk updates that did not close the replaced period.
- Historical paper-form checks trusted a matching interval without intersecting prerequisite history or excluding sanctions on the requested date.

Ten of the initial eleven regression cases failed against the unchanged base. These are local reproductions, not evidence that particular production records were affected.

## Changes

Updates extend only the latest recorded validity period and preserve earlier gaps. Missing prerequisites close current eligibility at the change date without inventing past eligibility. Ordinary saves use persisted values; membership and prerequisite updates touch only relevant authorizations. Approval and replacement paths now maintain validity history, including dependent styles.

Historical checks use recorded intervals, prerequisite validity, and sanction dates. They preserve earlier valid periods on inactive authorizations and transferred history on survivor accounts, while rejecting merged source accounts. Missing history fails closed.

The original history-only branch left the Society concurrence switch unchanged. On 2026-10-31, the owner authorized combining these fixes with `agent/society-two-person-integrity` and the FAQ/home-page updates for a local `main` merge. The integration retains the newer history handling, permanently enables the Society rule, and retires the two obsolete backlog tools. Hosted publication and deployment remain separate steps.

## Verification and limits

- Full Django suite: 665 tests passed. `makemigrations --check --dry-run` reported no changes, and `git diff --check` passed.
- Regression coverage includes membership and background-check lapses, removal and restoration, partial saves, status changes, waiver/background-check approval, youth replacement, Equestrian prerequisite chains, alternative Cut & Thrust prerequisites, sanctions, duplicate names, and merged-account history.
- Tests use isolated in-memory SQLite and in-memory email through `authorizations.tests.test_settings`; no production data or external mail was used.
- No schema migration or automatic historical data repair is included.
- Already-lost gaps and direct SQL/bulk edits outside the maintained application paths cannot be reconstructed from today's expiration dates. Any repair requires separate evidence and a reviewed data-maintenance plan.
- Validity is recorded by calendar date; it does not establish the time of day an approval or suspension occurred.
- Hosted staging, production behavior, and database-specific concurrency have not been tested here. Publication, staging, merge, and production approval remain separate steps under `deployment_workflow.md`.
