from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.contrib.auth.decorators import login_required
from django.db.models import Q

from authorizations.models import BranchMarshal, Authorization, WeaponStyle, User, Branch, AuthorizationStatus, \
    Person, Discipline


def membership_is_current(user):
    if not user.membership:
        return False
    if not user.membership_expiration:
        return False
    if user.membership_expiration < date.today():
        return False
    return True

def is_senior_marshal(user, discipline=None):
    """
    Checks if the user has an active Senior Marshal status for the given discipline.
    """

    if is_kingdom_authorization_officer(user):
        return True

    if is_kingdom_earl_marshal(user):
        return True

    query = Authorization.objects.filter(
        person__user=user,
        style__name='Senior Marshal',
        expiration__gte=date.today(),
        status__name = 'Active'
    )

    if not membership_is_current(user):
        return False

    if discipline:
        query = query.filter(style__discipline__name=discipline)

    return query.exists()

def is_branch_marshal(user, branch=None, discipline=None):
    """
    Checks if the user is a current Branch Marshal for the given branch and discipline.
    """

    query = BranchMarshal.objects.filter(
        person__user=user,
        end_date__gte=date.today(),
        branch__in=Branch.objects.non_regions()
    )

    if discipline:
        query = query.filter(discipline__name=discipline)

    if branch:
        query = query.filter(branch__name=branch)

    if not membership_is_current(user):
        return False

    return query.exists()


def is_regional_marshal(user, discipline=None, region=None):
    """
    Checks if the user is a current Regional Marshal for the given discipline or is the Earl Marshal.
    """
    if not membership_is_current(user):
        return False

    if is_kingdom_authorization_officer(user):
        return True

    if is_kingdom_earl_marshal(user):
        return True

    if discipline:
        if is_kingdom_marshal(user, discipline):
            return True
    else:
        if is_kingdom_marshal(user):
            return True

    if region:
        # Check if the region sent by the user is in fact a region.
        # Get the region branch by name and check if they are a marshal for it
        try:
            branch = Branch.objects.get(name=region)
            if not branch in Branch.objects.regions():
                return False
            query = BranchMarshal.objects.filter(
                person__user=user,
                branch=branch,
                end_date__gte=date.today(),
            )
        except Branch.DoesNotExist:
            return False
    else:
        # Get all region branches and check if they are a marshal for any of them
        # Get all region branches
        region_branches = Branch.objects.regions()
        query = BranchMarshal.objects.filter(
            person__user=user,
            branch__in=region_branches,
            end_date__gte=date.today(),
        )
    
    if discipline:
        query = query.filter(discipline__name__in=[discipline, 'Earl Marshal'])

    return query.exists()

def is_kingdom_marshal(user, discipline=None):
    """
    Checks if the user is a current Kingdom Marshal for the given discipline or is the Earl Marshal.
    """

    if is_kingdom_authorization_officer(user):
        return True

    if is_kingdom_earl_marshal(user):
        return True

    query = BranchMarshal.objects.filter(
        person__user=user,
        branch__name='An Tir',
        end_date__gte=date.today(),
    )

    if discipline:
        query = query.filter(discipline__name=discipline)

    if not membership_is_current(user):
        return False

    return query.exists()


def is_kingdom_authorization_officer(user):
    """
    Checks if the user is a current Kingdom Authorization Officer for the given discipline.
    """

    query = BranchMarshal.objects.filter(
        person__user=user,
        branch__name='An Tir',
        discipline__name='Authorization Officer',
        end_date__gte=date.today(),
    )

    if not membership_is_current(user):
        return False

    return query.exists()


def is_kingdom_earl_marshal(user):
    """
    Checks if the user is a current Earl Marshal.
    """
    query = BranchMarshal.objects.filter(
        person__user=user,
        branch__name='An Tir',
        discipline__name='Earl Marshal',
        end_date__gte=date.today(),
    )

    if not membership_is_current(user):
        return False

    return query.exists()


def authorization_follows_rules(marshal, existing_fighter, style_id):
    """Will need marshal, fighter, style.
    marshal needs to come in as a User. Existing_fighter comes in as a Person. Style_id comes in as a number.
    All of the rules rely on the spelling of the disciplines and weapon styles in the database.
    Some of these permissions rely on the fact that the weapon styles are in the proper order in the database. Where it matters, this will be called out in the rule."""
    # Get information needed to enforce the rules
    style = WeaponStyle.objects.get(id=style_id)
    all_authorizations = Authorization.objects.filter(person=existing_fighter)
    if existing_fighter.is_minor:
        birthday = existing_fighter.user.birthday
        age = calculate_age(birthday)
    else:
        age = 30


    # Rule 1: A senior marshal in a discipline can authorize any person in a weapon style for that discipline
    if not is_senior_marshal(marshal, style.discipline.name):
        return False, f'Must have a current {style.discipline.name} senior marshal.'

    # Rule 2: A junior marshal must be at least 16 years old
    if style.name == 'Junior Marshal':
        # Rule 2a: Archery and Thrown junior marshals must be adults
        if style.discipline.name in ['Archery', 'Thrown']:
            if existing_fighter.is_minor:
                return False, 'Must be an adult to become an archery or thrown weapon junior marshal.'
        if age < 16:
            return False, 'Must be at least 16 years old to become a junior marshal.'

    # Rule 3: A senior marshal must be an adult
    if style.name == 'Senior Marshal':
        if existing_fighter.is_minor:
            return False, 'Must be an adult to become a senior marshal.'

    # Rule 4: A Rapier or Youth Rapier fighter must have single sword as their first weapon authorization
    # Since these require single sword first, they rely on single sword being before the other combat styles so that they can be added in the same form submission.
    if not style.name in ['Single Sword', 'Junior Marshal', 'Senior Marshal']:
        if style.discipline.name == 'Rapier':
            if not all_authorizations.filter(style__name='Single Sword', style__discipline__name='Rapier', status__name='Active').exists():
                return False, 'A fighter must be authorized with single sword as their first rapier authorization.'
        if style.discipline.name == 'Youth Rapier':
            if not all_authorizations.filter(style__name='Single Sword', style__discipline__name='Youth Rapier', status__name='Active').exists():
                return False, 'A fighter must be authorized with single sword as their first youth rapier authorization.'
    
    # Rule 4a: A Cut & Thrust fighter cannot have spear as their first authorization.
    if style.discipline.name == 'Cut & Thrust':
        if not all_authorizations.filter(style__name='Spear', style__discipline__name='Cut & Thrust', status__name='Active').exists():
            return False, 'A fighter cannot be authorized with spear as their first cut and thrust authorization.'
    

    # Rule 5: Rapier fighters must be at lest 14 years old
    if style.discipline.name == 'Rapier':
        if age < 14:
            return False, 'Must be at least 14 years old to become a rapier fighter.'

    # Rule 6: Armored and Cut & Thrust fighters must be at least 16 years old
    if style.discipline.name in ['Armored', 'Cut & Thrust']:
        if age < 16:
            return False, f'Must be at least 16 years old to become authorized in {style.discipline.name} combat.'

    # Rule 7: Senior Equestrian Ground Crew must be at least 16 years old
    if style.name == 'Senior Ground Crew':
        if age < 16:
            return False, f'Must be at least 16 years old to become authorized as Senior Ground Crew.'

    # Rule 8: Youth combatants must be at least 6 years old and minors.
    if style.discipline.name in ['Youth Armored', 'Youth Rapier']:
        # Rule 8a: The exception is that marshals can be adults.
        if not style.name in ['Junior Marshal', 'Senior Marshal']:
            if age < 6:
                return False, f'Must be at least 6 years old to become authorized in {style.discipline.name} combat.'
            if not existing_fighter.is_minor:
                return False, f'Must be a minor to become authorized in {style.discipline.name} combat.'

    # Rule 9: For equestrian, a person must be at least 5 years old to engage in general riding, mounted gaming, mounted archery, or junior ground crew.
    if style.name in ['General Riding', 'Mounted Gaming', 'Mounted Archery', 'Junior Ground Crew']:
        if age < 5:
            return False, f'Must be at least 5 years old to become authorized in {style.name}.'

    # Rule 10: For equestrian, a person must be an adult to participate in Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting.
    if style.name in ['Crest Combat', 'Mounted Heavy Combat', 'Driving', 'Foam-Tipped Jousting']:
        if existing_fighter.is_minor:
            return False, f'Must be an adult to become authorized in {style.name}.'

    # Rule 11: Youth rapier marshals must already be Senior Rapier marshals
    if style.discipline.name == 'Youth Rapier' and not is_senior_marshal(existing_fighter.user, 'Rapier'):
        return False, 'Must be a senior rapier marshal to become a youth rapier marshal.'

    # Rule 12: An Equestrian Junior marshal must already have Senior Ground Crew and General Riding Authorizations.
    if style.discipline.name == 'Equestrian' and style.name == 'Junior Marshal':
        if not (all_authorizations.filter(style__name='Senior Ground Crew', status__name='Active').exists() and all_authorizations.filter(style__name='General Riding', status__name='Active').exists()):
            return False, 'Junior Equestrian marshal must have Senior Ground Crew and General Riding authorization.'

    # Rule 13: An Equestrian Senior marshal must already have Junior Marshal and Mounted Gaming Authorizations.
    if style.discipline.name == 'Equestrian' and style.name == 'Senior Marshal':
        if not (all_authorizations.filter(style__name='Junior Marshal', style__discipline__name='Equestrian', status__name='Active').exists() and all_authorizations.filter(style__name='Mounted Gaming', status__name='Active').exists()):
            return False, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.'

    # Rule 14: In order to authorize someone in Mounted Archery, Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting,
    # the Senior Marshal must have the same Authorizations.
    if style.name in ['Mounted Archery', 'Crest Combat', 'Mounted Heavy Combat', 'Driving', 'Foam-Tipped Jousting']:
        if not Authorization.objects.filter(person=marshal.person, style__name=style.name, style__discipline__name="Equestrian", status__name='Active').exists():
            return False, f'Must be authorized in {style.name} to authorize other participants.'

    # Rule 15: Junior and Senior marshals must be current members.
    if style.name in ['Junior Marshal', 'Senior Marshal']:
        if not membership_is_current(existing_fighter.user):
            return False, 'Must be a current member to be authorized as a marshal.'

    # Rule 16: You cannot renew a revoked authorization.
    if all_authorizations.filter(style__name=style.name, style__discipline__name=style.discipline.name, status__name='Revoked').exists():
        return False, 'Cannot renew a revoked authorization.'

    # Rule 17: Cannot duplicate/renew a pending authorization.
    if all_authorizations.filter(style__name=style.name, style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).exists():
        return False, 'Cannot renew a pending authorization.'

    # Rule 18: Cannot make someone a junior marshal if they are already a senior marshal.
    if style.name == 'Junior Marshal':
        # If they already have an active senior marshal, they cannot be a junior marshal.
        if is_senior_marshal(existing_fighter.user, style.discipline.name):
            return False, 'Cannot make someone a junior marshal if they are already a senior marshal.'

        # Do they already have an active junior marshal?
        if not all_authorizations.filter(style__name='Junior Marshal', style__discipline__name=style.discipline.name, status__name='Active').exists():
            # We now know this is a new junior marshal. They cannot get a new junior marshal if there is a pending senior marshal.
            if all_authorizations.filter(style__name='Senior Marshal', style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).exists():
                return False, 'Cannot have a new junior marshal if a senior marshal is pending.'

    # Rule 19: Cannot add a new senior marshal if there is a pending junior marshal.
    if style.name == 'Senior Marshal':
        if all_authorizations.filter(style__name='Junior Marshal', style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).exists():
            return False, 'Cannot have a new senior marshal if a junior marshal is pending.'

    # Rule 20: Cannot make an authorization for yourself.
    if existing_fighter.user == marshal:
        return False, 'Cannot make an authorization for yourself.'

    # Rule 21: If the fighter is a minor, and authorizing in Rapier, Cut & Thrust, or Armored combat, they can only be authorized by a regional marshal.
    if existing_fighter.is_minor and style.discipline.name in ['Rapier', 'Cut & Thrust', 'Armored']:
        if not is_regional_marshal(marshal):
            return False, 'Cannot authorize a minor in Rapier, Cut & Thrust, or Armored combat unless you are a regional marshal.'

    return True, 'Authorization follows all rules.'

def calculate_age(birthday):
    today = date.today()
    age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return age

def approve_authorization(request):
    """Add a concurance to a pending marshal authorization."""
    def remove_junior_marshal(authorization):
        discipline = authorization.style.discipline.name
        try:
            junior_marshal = Authorization.objects.get(person=authorization.person, style__name='Junior Marshal', style__discipline__name=discipline)
            junior_marshal.delete()
            return True
        except Authorization.DoesNotExist:
            return True

    marshal = request.user
    authorization = Authorization.objects.get(id=request.POST['authorization_id'])
    auth_region = authorization.person.branch.name if authorization.person.branch.is_region() else None
    discipline = authorization.style.discipline.name

    active_status = AuthorizationStatus.objects.get(name='Active')
    regional_status = AuthorizationStatus.objects.get(name='Needs Regional Approval')
    kingdom_status = AuthorizationStatus.objects.get(name='Needs Kingdom Approval')

    # Rule 1: Kingdom authorization officer can approve any marshal by themselves.
    if is_kingdom_authorization_officer(marshal):
        authorization.status = active_status
        # Rule 1a: If Youth Armored or Youth Rapier, set expiration to 2 years.
        if authorization.style.discipline.name in ['Youth Armored', 'Youth Rapier']:
            authorization.expiration = date.today() + relativedelta(years=2)
        # Rule 1b: If not Youth Armored or Youth Rapier, set expiration to 4 years.
        else:
            authorization.expiration = date.today() + relativedelta(years=4)
        authorization.save()
        # Rule 1c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
        if authorization.style.name == 'Senior Marshal':
            remove_junior_marshal(authorization)
        return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'


    if authorization.status.name == 'Pending':
        # Rule 2: must be a senior marshal in the discipline to approve (exception for missile marshal concurence).
        if not is_senior_marshal(marshal, discipline):
            return False, 'You must be a senior marshal in this discipline to approve this authorization.'
        # Rule 3: Cannot concur with an authorization you proposed.
        if authorization.marshal.user == marshal:
            return False, 'You cannot concur with your own authorization.'
        # Rule 4: If a pending authorization for Senior marshal is approved, it then goes to the region for approval.
        if authorization.style.name == 'Senior Marshal':
            authorization.status = regional_status
            authorization.save()
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for regional approval!'
        # Rule 4a: If a junior marshal is approved it becomes active.
        if authorization.style.name == 'Junior Marshal':
            # Rule 4b: If a junior marshal for Youth combat is approved it goes to the Kingdom authorization officer for confirmation.
            if authorization.style.discipline.name in ['Youth Armored', 'Youth Rapier']:
                authorization.status = kingdom_status
                authorization.save()
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom to confirm background check!'
            else:
                authorization.status = active_status
                authorization.save()
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'

    # Rule 5: If the authorization is out for regional approval, you need to be the correct regional marshal to approve it (exception that Armored can approve Missile).
    elif authorization.status.name == 'Needs Regional Approval':
        if not is_regional_marshal(marshal, region=auth_region):
            return False, 'You must be a regional marshal in the same region as the fighter to approve this authorization.'
        if authorization.style.discipline.name == 'Missile':
            if not is_regional_marshal(marshal, 'Missile', auth_region) and not is_regional_marshal(marshal, 'Armored', auth_region):
                return False, 'You must be a regional missile marshal or the regional armored marshal to approve this authorization.'
        else:
            if not is_regional_marshal(marshal, discipline, auth_region):
                return False, 'You must be a regional marshal in this discipline to approve this authorization.'
        # Rule 5a: If the region approves a Youth Combat Senior marshal, it goes to the Kingdom authorization officer for confirmation.
        if authorization.style.discipline.name in ['Youth Armored', 'Youth Rapier']:
            authorization.status = kingdom_status
            authorization.save()
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom to confirm background check!'
        # Rule 5b: If the regional marshal approves a non-youth senior marshal, it becomes active.
        else:
            authorization.status = active_status
            authorization.expiration = date.today() + relativedelta(years=4)
            authorization.save()
            # Rule 5c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
            remove_junior_marshal(authorization)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'

    # Rule 6: If the authorization is out for kingdom approval, you need to be a kingdom authorization officer to approve it.
    elif authorization.status.name == 'Needs Kingdom Approval':
        if not is_kingdom_authorization_officer(marshal):
            return False, 'You must be the kingdom authorization officer to approve this authorization.'

    else:
        return False, 'Authorization status not valid for confirmation.'

@login_required
def appoint_branch_marshal(request):
    """Adds a new branch marshal to the database. Only available to branch marshals or the authorization officer.
    Each branch can have at most two branch marshals for each discipline, a deputy and an actual.
    The system does not distinguish between the two."""

    # Get the data from the request.
    try:
        person = Person.objects.get(sca_name=request.POST['person'])
        branch = Branch.objects.get(name=request.POST['branch'])
        discipline = Discipline.objects.get(name=request.POST['discipline'])
        start_date = request.POST['start_date']
    except:
        return False, 'Missing data'

    # Must be a current member.
    if not membership_is_current(person.user):
        return False, 'Must be a current member to be a branch marshal.'

    # Rule 0: Only the authorization officer can appoint branch marshals
    if not is_kingdom_authorization_officer(request.user):
        return False, 'Only the authorization officer can appoint branch marshals.'

    # Rule 1: A branch marshal can only serve as one position at a time
    # Check if they are currently a branch marshal.
    branch_marshal_status = BranchMarshal.objects.filter(
        person=person,
        end_date__gte=date.today(),
    ).exists()
    if branch_marshal_status:
        return False, 'Can only serve as one branch marshal position at a time.'

    # Rule 2: An Tir is the only branch that can have the authorization officer.
    if discipline.name == 'Authorization Officer':
        if not branch.name == 'An Tir':
            return False, 'An Tir is the only branch that can have the authorization officer.'

    # Rule 3: The Authorization Officer doesn't need to be a senior marshal.
    if discipline.name == 'Authorization Officer':
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()

        BranchMarshal.objects.create(
            person=person,
            branch=branch,
            discipline=discipline,
            start_date=start_date,
            end_date=start_date + relativedelta(years=2),
        )

        return True, 'Authorization officer appointed.'

    # Check is_region() to see if they are being made a regional marshal.
    # Rule 4: A regional marshal must be a senior marshal.
    if branch.is_region():
        if not is_senior_marshal(person.user):
            return False, 'Must be a senior marshal to be a regional marshal.'

    # This captures whether the user is a Junior or Senior marshal in the discipline.
    marshal_status = Authorization.objects.filter(
        person=person,
        style__discipline=discipline,
        expiration__gte=date.today(),
        status__name = 'Active',
        style__name__in=['Senior Marshal', 'Junior Marshal']
    ).exists()

    # Rule 5: Must be a marshal in the appropriate discipline to be a branch marshal. Can be a Junior marshal.
    if not marshal_status:
        if not discipline.name == 'Earl Marshal':
            return False, 'Must be a marshal in the discipline to be a branch marshal.'

    start_date = datetime.strptime(start_date, '%Y-%m-%d').date()

    BranchMarshal.objects.create(
        person=person,
        branch=branch,
        discipline=discipline,
        start_date=start_date,
        end_date=start_date + relativedelta(years=2),
    )

    return True, 'Branch marshal appointed.'


