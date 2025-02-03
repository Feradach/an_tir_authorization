# An Tir Authorization Database

## Video Demo:
[https://youtu.be/jYRtl7xnbRw](https://youtu.be/jYRtl7xnbRw)

## Description:
The Society for Creative Anachronism (SCA) is a historical society that attempts to recreate pre-1600's life.
One of the activities we engage in is martial combat sports. In order to participate in these activities, 
a person must be authorized to ensure that they know the rules and are capable of participating safely.

An Tir is a kingdom of the SCA that covers Oregon, Washington, and British Columbia. They maintain an online
database of authorized fighters and marshals (safety officers) for the various combat sports. That database 
can be viewed [here](https://antirlists.org/).

The current database is outdated and has many errors. Its functionality is limited to only a lookup feature.
In order to add new authorizations a marshal must fill out a paper form and email it in to the kingdom officer
who must enter the values into the database by hand.

The goal of this project is to create a database that is more accurate and up to date. It also includes additional
functionality that will allow other users to manage the authorizations. This will be a significant benefit to the
community and will reduce the administrative burden for the authorization officer.

The project is made using Django with one app called authorizations.

## Features:
The primary purpose of this project is to create a database of authorized fighters and marshals.

The system has the following capabilities:
* Search for authorizations.
* View authorizations for a person.
* Add new people to the system.
* Create new authorizations for existing people.
* Renew authorizations.
* Create new marshal authorizations.
* Suspend the authorizations of people.
* Remove those suspensions.
* Permissions system based on marshal status and branch marshal status.

The program has two layers. This was done to reflect the current system.

The outer layer consists of:
* homepage
* contact page
* forms page

The inner layer is the authorizations app. This app has the following pages:
* Home
* Search
* Browse
* My Account
* Login
* Password reset
* Add new fighter
* View fighter
* View branch marshals
* Manage sanctions
* Issue sanctions

### Header
The first item to discuss is the header. In the outer ring the header consists of all three pages plus a link to reach
the inner ring.

In the inner ring the header contains links to:
* Home (this leads to the outer ring homepage)
* Authorizations Homepage (this leads to the inner ring homepage)
* Search
* Browse
* My Account
* Login/Logout

### Homepage
The outer ring homepage has a brief description of the website. The intention is that, after this has been submitted
for CS50w, I will work with the kingdom to deploy this for real. The introduction speaks to this.

### Contact
The contact page right now has my contact information. This is needed so that people can reach out to me with
issues they find in testing. When deployed for real, this will have the contact information for the various
kingdom officers and a contact to report bugs.

### Forms
The forms are the paper documents that are currently used to request new authorizations. This page has
a table with all of these forms and instructions on where to email completed copies to.

### Authorization Homepage
The authorization homepage is the page that users will first go to when they navigate from the outer layer to the inner layer.
This has a number of links for common actions. These links vary depending on the user's role.
All users see:
* Search for authorizations
* View branch marshals

Users who have a senior marshal authorization see:
* Add a New Fighter to the Database

Users who have the Kingdom Earl Marshal or Kingdom Authorization Officer role also see:
* Manage Sanctions

### Search
When clicking this, users will be taken to a page where they can search by a variety of fields. These are:
* SCA Name
* Region
* Branch
* Discipline
* Weapon Style
* Authorizing Marshal
* Expiration After date
* Expiration Before date
* Minor Status

The user can enter any, all, or none of these fields. Each field is dynamically filtered so that if they choose
a region, the rest of the fields will only contain values for authorizations within that region.

Users can search the authorizations from here, or they can clear the search to enter new values.

### Browse
This page can be accessed either directly from the nav bar or by clicking the link from the search page.
If clicked from the search page, it will retain all search parameters entered there.

This page provides a filtered list of all authorizations. If users are on mobile, they will be presented
with a card view. This view lumps all of the authorizations for a person together. There are two floating
buttons to go back to the search page or to clear the current search parameters. The sort order is by sca name
and cannot be altered.

If the user is on desktop they will see a table view. In this view they can add or modify the search terms.
The results are paginated and the default sorting is by sca name. Users can modify the sort order.

The search results also provide a link to each persons fighter page.

### My Account
This page is only available to the specific user and to the authorization officer. It allows users to view their account information such as
username, email, membership information, and birthday. Users can change this information here. There is a
link to their fighter page and a link to the password change page.

### Login
This is a simple login page that takes username and password. If the user is already logged in then the 
button in the nav bar will be logout and will log them out.

### Password Reset
User creation is controlled by marshals. Only a senior marshal can create a new account. When the account 
is created the new user is sent an email with their username and a temporary password. When they log in for
the first time they are directed to this page and prompted to change their password.
This page can also be accessed from the My Account page.
It requires the user to enter their current password, a new password, and their new password confirmation.

### Add New Fighter
This page is only available to senior marshals. It allows the user to add a new person to the database.
The form allows the creation of the user, the person, and multiple authorizations at the same time.

The new user will be sent an email with their username and a temporary password. They will need to reset
their password after they log in. If they bypass the password reset they will be reprompted until they 
reset from the temporary password.

When creating the new authorizations, the system will run them through the authorization_follows_rules function
which ensures that the authorizations follow the rules laid out by the kingdom covering who can receive authorizations.
These rules include things like "your first rapier authorization must be in single sword" and "you must be
at least 16 to be a junior marshal".

### View Fighters
This page is accessible to anyone. It allows the user to see the public information about a fighter.
It includes their sca name, their branch, any branch marshal role they hold, and their authorizations.

The user can click to download a fighter card. There are three versions, and they exactly replicate the 
paper cards that the kingdom currently uses. This gives the user a way to print out their own cards and have
a physical copy to present at tournaments.

The authorizations are grouped into active, pending, and suspended.

On the fighter page, those with additional permissions can take a variety of actions.

* Go to the account page
* Approve a pending marshal authorization
* Add a new authorization
* Renew an authorization
* Suspend an authorization
* Remove a suspension
* Promote to branch marshal

Pending authorizations are used for marshal authorizations. Per the rules, when one user authorizes someone
as a junior marshal a second marshal must concur with this. If it is for a senior marshal then a second marshal
must concur and then the kingdom or regional marshal must also concur. The button only appears if the user
has permission to approve this authorization. This is also enforced on the server side.

Marshals can add a new authorization, or renew an existing authorization for the user. The field to add
these authorizations is limited, via JavaScript, by the marshal status of the current user (not the page they are viewing).

The Kingdom Earl marshal or authorization officer have a button to manage sanctions (which takes them to a pre-filtered
list of sanctions) or issue a new sanction to the individual.

The authorization officer can appoint the fighter as a branch marshal so long as they have the appropriate
marshal authorizations and don't already have a job.

### View Branch Marshals
This page is accessible to anyone. It allows the user to see the public information about branch marshals.
It includes their sca name, their branch, and any branch marshal role they currently hold. It also provides a link
to their fighter page.

### Manage Sanctions
This page is only accessible to the Earl Marshal and the Kingdom Authorization officer. It allows the user
to view all current sanctions and to lift any of them. Lifting a sanction deletes the authorization from
the database, which allows the fighter to get a new authorization in that style.

### Issue Sanctions
This page is only accessible to the Earl Marshal and the Kingdom Authorization officer and can only be linked
to from the fighter page. It allows the user to create new sanctions on the fighter. The user can choose to 
sanction an entire discipline, which will create sanctions for all styles in that discipline, or to sanction
a specific style.

The expiration date is set to the issue date and displayed elsewhere as the issue date.

### Internal Architecture
The program has one application called authorizations.

The contact, forms, and homepage are managed only by html files and stored in a templates folder at the top level.
The nav bar is managed through a layout file that contains the overall structure of the navbar (this is in
that outer layer folder). There is then an outer_layout file that contains the links used in the outer ring navbar.

The inner ring navbar links are contained in an inner_layout file which is in the authorizations template folder.

In the authorizations template there is a models.py for managing the database, a permissions.py which manages rule
based functions, and a views.py which contains the logic for each page.

There is a tests folder that contains the unit tests. It has two files which contain 48 tests. These tests
ensure that the SCA authorization rules are being correctly enforced and that the permission structure is
being respected.

The program also has a SQLite database called db.sqlite3, a README.md file, and a requirements.txt file.

## Setup Instructions
The system comes with a database already pre-filled with sample data. This will allow the end user
to run and test the system.

The requirements.txt file contains the packages needed to run the system.

The program can be run using the command python manage.py runserver. There aren't any additional dependencies.

## Distinctiveness and Complexity:
The system is significantly more complex than the projects in the class, predominantly because it has a 
role based system. This system means that the various pages need to adapt based on the user's role and so
have to include significantly more Django code.

Additionally, the large amount of rules contained in permissions.py add additional complexity because it
requires that they all be configured to work with each other and make sure that there aren't gaps in the 
system where illegal actions can be performed.

For distinctiveness, the systems in the class were a single page mailbox, a shopping application, and a social
media site. The project shares some similarities with all of these, such as how the search/browse page is a single
page with multiple views and how the system uses a database. However, the specific use of these features
shares almost nothing in common with the class projects.

Finally, this is a distinct and useful system because it is building on a real world tool. It introduces
functionality that the real world site lacks and so it takes inspiration from this tool but required me
to build new functionality where there was no analog.

#   a n _ t i r _ a u t h o r i z a t i o n 
 
 
