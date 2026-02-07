## [0.1.5] - 2026-02-07
### Added
- Changed name search on the search page to match the behavior on the authorizations home page.
- Changed date search behavior to be more smooth.

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
