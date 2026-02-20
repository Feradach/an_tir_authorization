
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
