# Bugs

* KAO has a bug where they cannot confirm an authorization as themselves.


## Features

### Urgent

* Authorizations on the fighter page need to be separated by status first and discipline second. Each authorization that is in a status other than "Active" should be separated from all other authorizations so that they can be approved or denied separately.
* Add a confirmation email to the old address for email changes and password resets with instructions on how to dispute if it is fraudulent.
* Need to create a separation between Kingdom Authorization Officer and Kingdom Equestrian Authorization Officer Roles.
* Names on the PDF cards exceed the field width and need to be truncated.


### Near Term

* Remove is_minor status instead relying on birthday.
* Add 2FA to the system. Make it optional for now, possibly mandatory for high access users.
* Notification when authorization expiration is nearing (with ability to turn them off).
* Make sure quarterly reports are working and emailing to appropriate people
* Create chart/report functionality
* Improve fighter page performance for Kingdom Authorization Officer users. The public and normal marshal views are fast, but the auth officer view is slow because it renders full-database person dropdowns for "authorize as", "approve as", and concurrence workflows. This affects only one or two officer accounts and is not a launch blocker. Potential fix: replace those full `<select>` controls with server-backed typeahead/autocomplete lookups that return a small number of matching people as the officer types.
* Ability for users to submit reports in the system.



### Long Term

* Pre-register for tournaments through the system
* Offline access
* Dedicated app
* Run tournaments through the system
* Fighter practice check in


## Bugs

None currently identified
