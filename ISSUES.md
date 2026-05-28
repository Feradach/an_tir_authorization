# Bugs


## Features

### Urgent


### Near Term

* Search by legal name (Maybe, does this expose people's information too much?)
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

None currently identified.
