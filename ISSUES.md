# ISSUES

Need to launch the program. The current database has been destroyed by a ransomware attack.



## Features

### For Immediate Launch

* (External) Get membership dump from Seneschal
* (External) Add post backup authorizations into the system.
* Create welcome email template.
* (External) Roll out plan including social media information campaign.



### After Launch

* ! Add maintenance mode to lock changes to the database.
* ! Add Parent names to User. Require either parent ID or parent name for youth. (We could make parent ID mandatory and force them to create the parent first)
* ! Birthday cron checker should remove parent name and parent ID.
* Remove is_minor status instead relying on birthday.
* ISSUE-001: Improve fighter page performance for Kingdom Authorization Officer users. The public and normal marshal views are fast, but the auth officer view is slow because it renders full-database person dropdowns for "authorize as", "approve as", and concurrence workflows. This affects only one or two officer accounts and is not a launch blocker. Potential fix: replace those full `<select>` controls with server-backed typeahead/autocomplete lookups that return a small number of matching people as the officer types.
* Notification when authorization expiration is nearing (with ability to turn them off).
* Make sure quarterly reports are working and emailing to appropriate people
* Create chart/report functionality
* Ability for users to submit reports in the system.
* Allow the Authorization officer to change an auth start or end date.



### Long Term

* Pre-register for tournaments through the system
* Offline access
* Dedicated app
* Run tournaments through the system
* Fighter practice check in



## Bugs

None currently identified
