# Bugs


## Features

### Urgent
* Authorize as needs to show member number and mundane name.
* Add the ability for KAO and KEAO to set a date when they "approve as" an authorization.
* Create person search page. Open up to those with an active senior marshal authorization.
* Add optional middle name and suffix to person form.
* Remove "country" from the address and infer it from state/province. (make sure that minor calculates correctly, do a check for bad data.)
* Allow single sword rapier to be authorized at the same time as other rapier auths.
* Create Rivers Region
* Needs Kingdom Approval chart should show who the (first) approving marshal is.

### Near Term

* Messaging system. Let people choose to recieve email notifications and put the ability for them to get alerts on their dashboard, even if they aren't a marshal. Give the ability for marshal officers to send messages which will be delivered according the the settings for the individual recipient.
* Make sure quarterly reports are working and emailing to appropriate people (did I already do this?)
* Add the ability for KAO and KEAO to delete an authorization (?).
* Add 2FA to the system. Make it optional for now, possibly mandatory for high access users.
* Notification when authorization expiration is nearing (with ability to turn them off).
* Create chart/report functionality
* Improve fighter page performance for Kingdom Authorization Officer users. The public and normal marshal views are fast, but the auth officer view is slow because it renders full-database person dropdowns for "authorize as", "approve as", and concurrence workflows. This affects only one or two officer accounts and is not a launch blocker. Potential fix: replace those full `<select>` controls with server-backed typeahead/autocomplete lookups that return a small number of matching people as the officer types.
* Ability for users to submit reports in the system.



### Long Term

* Pre-register for tournaments through the system
* Offline access
* Dedicated app
* Run tournaments through the system
* Fighter practice check in
* Integrate with a full officer database for non-marshal officers.
