## [Unreleased]

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
