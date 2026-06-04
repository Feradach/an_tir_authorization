
## [UNRELEASED]
### Major Changes


### Added


### Changed


### Fixed


### Removed


## [1.1.3] - 2026-06-04
### Added
- Added a form for requestiong new features.
- Added seneschal roles.
- Added a Kingdom Seneschal role with the ability to upload mmebership documents and read notes.

### Fixed
- Regional marshals can now approve senior marshal promotions in their discipline even when the fighter is from another region.
- Fixed bug where submitting multiple authorization styles at once would sometimes skip some of them.
- Legacy authorization recovery now inactivates same-discipline Junior Marshal authorizations when adding a Senior Marshal, and blocks adding Junior Marshal when an active Senior Marshal already exists.


## [1.1.2] - 2026-05-29
### Fixed
- Fixed a bug where pending waiver authorizations were not being cleared when the Membership information was entered.
- Renewing an active, unexpired authorization no longer moves it into Kingdom Authorization Officer review when verification is enabled.


## [1.1.1] - 2026-05-28
### Added
- Added a scrollable, zoomable FAQ flow chart with clickable object highlights showing how authorization statuses move through review.
- Automated the generation of quarterly reports.
- Added an authorization audit history that records tracked authorization row changes for future troubleshooting.

### Fixed
- Fixed legacy authorization recovery so selected people are matched by their account ID instead of re-checking name text from the batch row.
- Fixed the fighter-page authorization basket so removed styles are no longer submitted.
- Marshal authorizations now show as expired when required membership or background-check expiration dates are missing.


## [1.1.0] - 2026-05-27
### Major Changes
- Waiver handling has been overhauled. You will now be able to see a record of previous waivers you have signed.
- Youth authorizations have been restructured to be tied to specific age categories and must be renewed when moving to a new age category.
- Changed the names of the different authorization statuses to be more descriptive.

### Added
- When an account email address or password is changed, a security notice is sent to the (previous) email address.
- Password fields now include a show/hide control so users can check what they typed.
- Tightened authorization officer interface.
- Added a Kingdom Equestrian Authorization Officer role for kingdom-level review of equestrian authorizations.

### Changed
- Kingdom Authorization Officer review is now limited to non-equestrian authorizations; equestrian kingdom waiver review belongs to the Kingdom Equestrian Authorization Officer.
- Kingdom and equestrian authorization officers can record paper waivers from an account page whenever a paper form has been received.
- Fighter pages now show authorizations in workflow order, with pending authorizations separated so each one can be approved, concurred with, or rejected individually.
- Senior marshals can now add new accounts.
- Long names on fighter card PDFs now shrink down to 8 point text and then truncate at a word boundary when needed.


## [1.0.1] - 2026-05-13
### Changed
- When a roster is uploaded, all membership expiration dates are automatically updated to the latest date found in the roster.
- Society membership uploads now use the later expiration date when the roster provides more than one date, extend matching account membership expiration dates when the roster has a later date, and record an officer note for each updated account.
- Tweaked how admin accounts display information.
- Added FAQ guidance for membership update problems and linked membership validation errors to that help section.

### Fixed
- Modified how names and junior marshal/ground crew authorizations are handled to prevent conflicts.
- Updated rules on zip code validation.


## [1.0.0] - 2026-05-11
### Added
- Official launch of the new authorization portal!
- Added contact page to the inner loop navigation.
- Added the ability to temporarily lock the site for maintenance or updates.
- Accounts for children now require either an attached parent account or the name of the parent/guardian.

### Changed
- Moved the changelog onto the Roadmap page and grouped it into collapsible sections by major version.
- Updated the test-mode administrator contact email.
- Staff accounts are hidden from authorization search results.

### Fixed
- The welcome message on the authorization portal homepage now starts collapsed for logged-in users and expanded for visitors who are not logged in.


## [0.1.13] - 2026-05-07
### Fixed
- Fixed account edit dropdowns so imported state/province and country abbreviations show the correct selected values.
- Removed the extra login link from account setup emails so recipients are directed only to the password setup link.
- Updated the Society Membership upload to accept the current Society CSV and Excel export formats.
- Changed the header logo link to return to the authorization portal home page.


## [0.1.12] - 2026-05-06
### Added
- Added a launch notice with training videos and account setup guidance to the home pages and FAQ.
- Added a temporary contact form for requesting an email address update during account rollout.
- Migrated legacy data to new database structure.
- Changed the registration form so that ParentID and Birthday fields are not visible unless the user is a minor.
- Updated how minor status is determined in the back end.
- Legacy marshal authorization recovery now treats marshal entries as renewals by default and only asks for promotion concurrence when the Promotion checkbox is selected.


## [0.1.11] - 2026-05-05
### Added
- Changed the email address used for authorization notifications to a dedicated address.
- Increased the size of the server the system is running on.
- Added additional security measures to protect the system.
- Added additional protection against repeated failed login attempts.

### Fixed
- Updated how database backups are performed to use a more reliable method.


## [0.1.10] - 2026-03-11
### Fixed
- The account page now shows separate First Name and Last Name fields instead of a blank Legal Name field.
- Equestrian-waiver approvals and rejections are now handled only by Kingdom Authorization Officers, and Kingdom Equestrian officers no longer have global access to supporting documents.
- New non-marshal authorization requests in Equestrian, Siege, Youth Armored, and Youth Rapier no longer require a second concurrence.


## [0.1.9] - 2026-03-10
### Added
- Added a new document uploader to account pages for two file types: Background Check proof and Equestrian Event Waiver.
- Added a Supporting Documents page where officers can review uploaded files.
  - Not logged in: no access allowed.
  - Logged in users: see documents related to their account.
  - Kingdom Authorization Officer, Kingdom Earl Marshal, and Kingdom Equestrian Officer: can see all documents.
- Added a new authorization status: `Needs Kingdom Equestrian Waiver`.

### Fixed
- Equestrian waiver selection now ignores already active authorizations and only shows records that still need review.
- Equestrian authorizations now move to `Needs Kingdom Equestrian Waiver` at the kingdom-review step.
- Pending queue actions were expanded:
  - `Needs Kingdom Equestrian Waiver` can now be rejected (with a required note).
  - `Pending Background Check` now shows both `Go To Page` and `Reject` (reject also requires a note).
- Updated the FAQ page so it reflects current account, document upload, approval queue, sanctions, and reporting workflows.
- Equestrian authorization checks now enforce key prerequisites more clearly: Ground Crew - Senior requires Ground Crew - Junior, Mounted Gaming requires General Riding, and mounted weapon-game special authorizations require Mounted Gaming (with Mounted Heavy Combat also requiring General Riding).
- Added a dedicated FAQ quick-reference section summarizing An Tir equestrian authorization rules and age limits.
- Rearranged the menu and improved behavior when switching window size.
- Users who are not logged in are now redirected to the Authorizations Homepage when they open Supporting Documents.
- If you try to open a supporting document you are not authorized to view, you are now redirected to the Authorizations Homepage with a warning message.
- Unknown URLs now redirect to the correct home shell instead of showing a raw 404: Authorizations routes go to the Authorizations Homepage, and outer-site routes go to Home.
- Missing supporting document files now redirect to the Authorizations Homepage with a warning instead of showing a 404 page.


## [0.1.8] - 2026-03-08
### Fixed
- On the user account edit form, State/Province, Title, Branch, and Parent ID now use the same type-to-filter dropdown behavior and compact formatting as the register page, and the account layout no longer forces early text wrapping from narrow columns.
- Approving a `Needs Kingdom Approval` authorization no longer prompts for a marshal promotion note; that final Kingdom approval step now proceeds without requiring a note for any style.
- Kingdom Authorization Officers now have a Reject action for `Needs Kingdom Approval` authorizations on both the fighter page and the homepage queue, and rejecting those records now requires a note.


## [0.1.8] - 2026-03-07
### Fixed
- The fighter page no longer shows marshal-officer appointment controls for fighters who already hold an active marshal officer position, and direct appointment attempts are blocked with a clear error.
- Pending-authorization cards on the fighter page now show approve/reject actions only when the logged-in user is allowed to perform that action, with Earl Marshal authority limited to final regional-step approvals/rejections and Kingdom-step approval remaining Kingdom Authorization Officer only.
- Kingdom Authorization Officers still see pending-approval actions, but must use "Approve As" for Pending and Needs Regional Approval actions instead of approving those directly as themselves.
- Youth Armored and Youth Rapier marshal proposals no longer fail immediately when a background check is missing; final approvals now move those marshal authorizations to Pending Background Check until a current background check is on file.
- On fighter cards, Kingdom Authorization Officers can now use "Approve As" for reject actions as well, and Pending Background Check authorizations now appear in the pending-authorization section as read-only entries.
- Updating a user's background check expiration now automatically advances any Pending Background Check authorizations: to Active when Kingdom verification is off, or to Needs Kingdom Approval when Kingdom verification is on.


## [0.1.7] - 2026-03-04
### Added
- Authorization notes now record the office or marshal status that justified the action when a note is saved, and show that information on the fighter page.
- Logged-in users now see a personalized welcome message on the Authorization Portal homepage.
- Sanctions now have their own records with start and end dates, and sanction notes automatically include the selected end date.
- Kingdom Authorization Officers can now upload the Society membership CSV directly from the account page, replacing the entire membership roster in one step.
- Kingdom Authorization Officers can now apply a documented membership-validation bypass during account updates when Society data needs manual correction.
- The register page now shows production-specific account creation guidance for membership name matching, simplified An Tir postal wording, and the waiver-signing next step, while test mode keeps its existing detailed guidance.

### Fixed
- Note attribution now prefers the currently relevant office for the action, falls back to the acting marshal status when no office applies, and logs data problems if someone incorrectly has multiple active offices.
- Sanctions now expire based on their end date without permanently overwriting authorization records, and authorization displays and marshal-status checks now treat active sanctions as an effective overlay.
- Sanction issuance now requires an end date, rejects past dates, caps overly long sanctions to the issuing officer's term end with a warning, updates existing sanctions in the same scope with the newly selected end date, and uses normal page messages instead of pop-up alerts when discipline or style is missing.
- Sanctions can be extended by issuing a new sanction with a later end date.
- Membership number updates now verify against the uploaded Society roster by matching number, first name, last name, and expiration date, while test mode skips this validation for fake data.
- Membership-based waiver auto-extension now only happens when the uploaded Society roster marks that member as having a waiver on file (`Waiver (C) = Yes`).


## [0.1.6] - 2026-02-20
### Added
- Added ability to request a username from the fighter page.
- Header logo now indicates whether the site is in test mode or production mode.
- Clarified register page guidance to list allowed username characters.
- Parent account page now lists linked child accounts with direct links.
- Added a homepage control for Kingdom Authorization Officers to turn "Require Kingdom Authorization Officer Verification" on or off.
- Added a Kingdom Authorization Officer bulk action on the authorizations homepage to approve all "Needs Kingdom Approval" records at once.
- Modified the way that reports are displayed so that they pull from data on the new system rather than from legacy reports.
- Added the ability to create a report for the current data.
- Added a safety check for Current reports so if expected report categories/regions have changed, the page shows a warning instead of crashing.
- Updated Current report region handling so Principalities are treated like Regions and additional An Tir region-level groups are included automatically.
- Added CSV download buttons to each report table on the reports page.
- Added CSV download for the authorizations search table view using the active filters across all result pages.
- Updated CSV downloads to use UTF-8 BOM so special characters display correctly in Excel more reliably.

### Fixed
- Enforced region matching for regional marshal approvals when the fighter belongs to a local branch (using the branch's parent region).
- Enforced the same region-scoped checks for marshal-promotion rejection actions.
- Added error logging when fighter region data cannot be resolved during regional marshal approve/reject checks.
- Removed Kingdom Earl Marshal blanket elevation for issuing authorizations; issuance now requires normal Senior Marshal qualification in the discipline.
- Updated sanctions permissions: kingdom discipline marshals can issue/lift only within their discipline, while Kingdom Earl Marshal and Kingdom Authorization Officer can issue/lift across all disciplines.
- Updated marshal-office appointment/removal permissions so kingdom discipline marshals can manage lower same-discipline offices, kingdom earl marshal can manage all marshal offices except kingdom earl/auth officer, and kingdom authorization officer can also manage kingdom earl marshal and additional kingdom authorization officers.
- Marshal-officer capability checks now use the effective minimum of membership, marshal-status validity, and warrant end date; fighter page shows the calculated limiting date (in red, parenthetical) to the officer or their chain-of-command superiors when it shortens the warrant.
- Improved account-edit and registration form feedback so state/province and postal-code validation errors are explicit and shown inline on the form.
- Enforced Authorization Officer sign-off behavior for existing non-marshal authorization renewals when sign-off is enabled, and added coverage tests for sign-off enabled/disabled flows.
- Turning Kingdom Authorization Officer verification from On to Off now automatically processes all "Needs Kingdom Approval" records through the normal approval flow.
- Authorization Officer queue on the authorizations homepage now shows only "Needs Kingdom Approval" and "Pending Background Check", with a "Go To Page" action for background-check cases.


## [0.1.5] - 2026-02-07
### Added
- Changed the way some of the drop downs behave to be more user friendly.
- Set up protections against making duplicate accounts.
- Set up the ability for the Kingdom Authorization Officer to merge duplicate accounts.
- Added unit tests.
- Refined how the Kingdom Authorization Officer ability to submit as someone else works.


## [0.1.4] - 2026-02-05
### Added
- Changed how marshal expirations are calculated. They will now track the actual marshal expiration, the membership expiration, and the background check (for youth marshals) and dynamically display the earliest number. The public page will now show the earliest expiration date. This allows marshals to better manage their various expiration dates.
- Youth combat expirations cannot be valid past their age of majority (18 for US, 19 for Canada).
- Added ability to limit search by "is current". This overrides the date range filter.
- Added requirement to enter a note when promoting a marshal or sanctioning a fighter.
- Added requirement to have a second authorized person concur with the first authorization someone receives in a discipline.
- Added ability for marshal officers to see notes on fighters.

## [0.1.3] - 2026-02-04
### Added
- Added security logging.
- Added server side stability features.

## [0.1.2] - 2026-02-02
### Fixed
- Changed dropdowns to be more readable.

### Added
- Moved system from PythonAnywhere to DigitalOcean.
- Changed email from SMTP to HTTPS.
- Opened up self registration for production version.
- Added security features for password reset and registration.

## [0.1.1] - 2025-11-28
### Fixed
- Fixed bug where the page would crash if user put in incorrect date format into URL.
- Fixed fighter cards so that they appear correctly in Firefox by flattening the PDF.
- Added ability to place watermark on fighter cards.
- Updated logging for better debugging.

## [0.1.0] - 2025-10-30
### Fixed
- Fixed issue with user lookup when two people have the same SCA name.
- Fixed membership field so it allows up to 20 digits and handles errors better.
- Shored up security for authorizations.
- Fixed branch dropdown in account creation to exclude region-level types (Kingdom/Principality/Region).
- Fixed branch dropdown to be alphabetically ordered.
- Changed testing branch marshal to now allow any branch to be selected.
- Made errors more visible.
- Setting a marshal authorization in testing will make it active.
- Set rule so that adults cannot be authorized as youth fighters.
- Set so that waiver can only be signed by the account owner or the authorization officer.
- Removed requirement to enter old password on password reset page.
- Fixed issue where dropdowns were cut off on the search pages with few values returned.

### Added
- Added a "testing" flag to the settings so that test and production code can be unified. Flag set to false will turn off all testing features.
- Added background check expiration to the self-registration form.
- Added a note saying that fighter cards do not work in Firefox.
- Added pending waiver status. If a fighter does not have a current waiver, authorizations assigned to them will be set to pending waiver. When they sign the waiver all pending waiver authorizations will be set to active.
- Added go to page feature on search pages.

## [0.0.3] - 2025-10-27
### Added
- Added the changelog to the Portal Test Info drop down and the roadmap page.

## [0.0.2] - 2025-10-23
### Fixed
- Fixed bug where search authorizations page would not go past page 2
- Fixed bug where search branch marshals page would error if search by region
- Changed weapons style "case" to "Case"
- Fixed bug where youth rapier fighters could not be authorized
- Fixed authorization search so that membership field only allows numbers

### Added
- Added "My Account" and "Login/Logout" to the outer menu
- Added a changelog page

## [0.0.1] - 2025-10-23
### Added
- Initial test deployment
