# Roadmap (Ideas, Not Commitments)

This document tracks future ideas and exploratory concepts.
These are not approved plans, timelines, or guaranteed features.
Items may change, be deferred, or be removed as priorities evolve.

## Reporting

- Add a synthetic `Total` row to the **Current** Quarterly Marshal Statistics report.
  - Treat non-fighting marshal status at a cross-discipline level for this row.
  - Keep this row out of legacy-to-legacy comparisons since legacy data does not include it.

- Add quarter-end automation to generate and save a new report snapshot (`ReportingPeriod` + `ReportValue`).
  - Trigger near quarter end.
  - Include safeguards so it cannot create duplicate quarter entries.

- Expand report options and views for additional officer use cases.
  - Candidate examples: trend views, discipline/region deltas, custom metric groupings.

## Natural Language Reporting

- Explore a constrained natural-language reporting assistant.
  - User asks plain-English reporting questions.
  - System translates request into safe, read-only SQL.

- Security model for NL reporting:
  - Read-only DB role.
  - No write operations.
  - Query only against a curated reporting schema/views.
  - SQL validation/allowlist gate before execution.
  - Query limits, timeouts, and audit logging.

## Operational Ideas

- Decide whether to retain periodic database snapshots for historical analysis.
  - Evaluate storage/retention needs and restore procedures.

## Mobile + Event Operations Ideas

### 1) Mobile app integrated with the website

- Goal: Give marshals/fighters a faster on-the-ground interface while keeping the website as the source of truth.
- Implementation approach: Expose a versioned API from the web app and build a mobile client that uses the same permissions model.
- Guidelines: Keep business rules server-side first; mobile should call validated endpoints rather than re-implement complex authorization logic.
- Concerns: Account/session security on lost devices, API version drift, and role/permission mismatches between web and app.
- Suggested first milestone: Read-only fighter lookup + login/session flow.

### 2) Offline local copy of non-PII data + queued actions

- Goal: Allow offline lookup and field workflows when internet is unavailable.
- Implementation approach: Distribute a signed, reduced dataset to device-local storage (only reporting/authorization fields needed for field use).
- Guidelines: Treat `User` table and direct identifiers as excluded by default; explicitly allowlist fields instead of blocklisting.
- Concerns: Data staleness, data leakage from device storage, conflict resolution when reconnecting, and replay/duplicate submissions.
- Suggested method: Queue actions locally as pending commands, then submit to server for full validation and final acceptance on reconnect.

### 3) Practice check-ins with QR code

- Goal: Replace paper attendance forms with structured digital check-in.
- Implementation approach: Branch marshal creates a practice event record; system generates time-limited QR token; participants scan and confirm attendance.
- Guidelines: Event token should expire and be scoped to one event; require authenticated identity confirmation at check-in.
- Concerns: Token sharing, duplicate sign-ins, and ensuring adult/youth supervision records are captured where needed.
- Suggested first milestone: QR check-in with audit trail (`who`, `when`, `event`, `method`) and exportable attendance list.

### 4) Tournament pre-registration workflow

- Goal: Replace Google Forms + manual matching with direct database-linked preregistration.
- Implementation approach: Organizer creates an event + registration link with fields/rules; fighters self-register through web/app.
- Guidelines: Validate eligibility at submit time and again at event start (membership/waiver/authorization may change).
- Concerns: Name collisions, withdrawn entries, payment/fee integration (if needed later), and organizer override paths.
- Suggested first milestone: Single-event preregistration list with automatic fighter matching and organizer review queue for ambiguous matches.

### 5) Tournament pairing and bout result entry

- Goal: Run tournament brackets in the system with list volunteers entering outcomes.
- Implementation approach: Support configurable formats (start with one), generate pairings, record winners, and advance brackets automatically.
- Guidelines: Keep a full audit log of result edits; require role-based controls for who can post/modify bout results.
- Concerns: Rules variation by tournament type, tie/dispute handling, accidental result edits, and offline resiliency.
- Suggested first milestone: One bracket format (for example, single elimination) with manual seeding, result entry, and printable/exportable standings.

## Sequencing Thought

- Practical order with lowest risk:
- 1) API hardening and auth groundwork.
- 2) Offline-capable read-only lookup (highest immediate user value).
- 3) Practice check-ins (contained workflow, high value).
- 4) Tournament preregistration.
- 5) Tournament pairing engine.
- 6) Offline queued writes/reconnect conflict handling (highest complexity).

## Delivery Options: PWA vs Native App

### PWA (Progressive Web App) with offline storage

- Good fit for early implementation and smaller teams.
- One codebase for web + mobile-style experience.
- Can cache pages/data locally and support offline lookup.
- Easier deployment/updates (no app store review required for every change).
- Tradeoffs: weaker deep device integration and less predictable background sync behavior on some mobile platforms.

### Native mobile app (iOS/Android)

- Best control over device features, background behavior, and polished mobile UX.
- Better long-term option if workflows become deeply mobile-first.
- Tradeoffs: higher development and maintenance cost, usually separate platform considerations, app store release overhead.

### Suggested path for this project

- Phase 1: Build a PWA-style offline lookup first (read-only, non-PII dataset, clear \"last sync\" timestamp).
- Phase 2: Add event workflows (check-ins, preregistration) once lookup reliability is proven.
- Phase 3: Evaluate native app migration only if PWA limitations materially block field usage.

## Notes for a First Major Project

- Favor small, testable increments over big all-at-once launches.
- Keep core authorization/business rules on the server, even when clients work offline.
- Treat offline data model and sync strategy as first-class design concerns from day one.
