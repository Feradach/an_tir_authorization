from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Max
from typing import Optional

from authorizations.models import BranchMarshal, Authorization, WeaponStyle, User, Branch, AuthorizationStatus, \
    Person, Discipline, AuthorizationNote

# Variable to determine whether we need to have the Authorization officer sign off on all authorizations. Uses status "Needs Kingdom Approval.
AUTHORIZATION_OFFICER_SIGN_OFF = False

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

    query = Authorization.objects.with_effective_expiration().filter(
        person__user=user,
        style__name='Senior Marshal',
        effective_expiration_date__gte=date.today(),
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
    # Anonymous users cannot hold marshal offices; also avoids ORM type errors
    # when this helper is called from public views.
    if not user or not getattr(user, 'is_authenticated', False):
        return False

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


def waiver_signed(user):
    """Checks if the user has signed a waiver."""
    print("checking waiver inside the function")
    waiver_signed = False
    print("user: ", user)
    print("user waiver expiration: ", user.waiver_expiration)
    print("user membership expiration: ", user.membership_expiration)
    if user.waiver_expiration and user.waiver_expiration > date.today():
        waiver_signed = True
    elif user.membership_expiration and user.membership_expiration > date.today():
        waiver_signed = True
    return waiver_signed

def authorization_follows_rules(marshal, existing_fighter, style_id, concurring_fighter: Optional[User] = None):
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
        if style.discipline.name == 'Rapier Combat':
            if not all_authorizations.filter(style__name='Single Sword', style__discipline__name='Rapier Combat', status__name='Active').exists():
                return False, 'A fighter must be authorized with single sword as their first rapier authorization.'
        if style.discipline.name == 'Youth Rapier':
            if not all_authorizations.filter(style__name='Single Sword', style__discipline__name='Youth Rapier', status__name='Active').exists():
                return False, 'A fighter must be authorized with single sword as their first youth rapier authorization.'
    
    # Rule 5: A Cut & Thrust fighter cannot have spear as their first authorization.
    if style.discipline.name == 'Cut & Thrust' and style.name == 'Spear':
        if not all_authorizations.filter(style__discipline__name='Cut & Thrust', status__name='Active').exists():
            return False, 'A fighter cannot be authorized with spear as their first cut and thrust authorization.'

    # Rule 6: Rapier fighters must be at lest 14 years old
    if style.discipline.name == 'Rapier Combat':
        if age < 14:
            return False, 'Must be at least 14 years old to become a rapier fighter.'

    # Rule 7: Armored and Cut & Thrust fighters must be at least 16 years old
    if style.discipline.name in ['Armored', 'Cut & Thrust']:
        if age < 16:
            return False, f'Must be at least 16 years old to become authorized in {style.discipline.name} combat.'

    # Rule 8: Senior Equestrian Ground Crew must be at least 16 years old
    if style.name == 'Senior Ground Crew':
        if age < 16:
            return False, f'Must be at least 16 years old to become authorized as Senior Ground Crew.'

    # Rule 9: Youth combatants must be at least 6 years old and minors.
    if style.discipline.name in ['Youth Armored', 'Youth Rapier']:
        # Rule 9a: The exception is that marshals can be adults.
        if not style.name in ['Junior Marshal', 'Senior Marshal']:
            if age < 6:
                return False, f'Must be at least 6 years old to become authorized in {style.discipline.name} combat.'
            if not existing_fighter.is_minor:
                return False, f'Must be a minor to become authorized in {style.discipline.name} combat.'

    # Rule 10: Youth marshals must have a valid background check
    if style.name in ['Junior Marshal', 'Senior Marshal'] and style.discipline.name in ['Youth Armored', 'Youth Rapier']:
        if not existing_fighter.user.background_check_expiration or existing_fighter.user.background_check_expiration < date.today():
            return False, f'Must have a valid background check to become authorized as a youth marshal in {style.discipline.name} combat.'

    # Rule 11: For equestrian, a person must be at least 5 years old to engage in general riding, mounted gaming, mounted archery, or junior ground crew.
    if style.name in ['General Riding', 'Mounted Gaming', 'Mounted Archery', 'Junior Ground Crew']:
        if age < 5:
            return False, f'Must be at least 5 years old to become authorized in {style.name}.'

    # Rule 12: For equestrian, a person must be an adult to participate in Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting.
    if style.name in ['Crest Combat', 'Mounted Heavy Combat', 'Driving', 'Foam-Tipped Jousting']:
        if existing_fighter.is_minor:
            return False, f'Must be an adult to become authorized in {style.name}.'

    # Rule 13: Youth rapier marshals must already be Senior Rapier marshals
    if style.discipline.name == 'Youth Rapier' and not is_senior_marshal(existing_fighter.user, 'Rapier Combat'):
        if style.name == 'Junior Marshal' or style.name == 'Senior Marshal':
            return False, 'Must be a senior rapier marshal to become a youth rapier marshal.'

    # Rule 14: An Equestrian Junior marshal must already have Senior Ground Crew and General Riding Authorizations.
    if style.discipline.name == 'Equestrian' and style.name == 'Junior Marshal':
        if not (all_authorizations.filter(style__name='Senior Ground Crew', status__name='Active').exists() and all_authorizations.filter(style__name='General Riding', status__name='Active').exists()):
            return False, 'Junior Equestrian marshal must have Senior Ground Crew and General Riding authorization.'

    # Rule 15: An Equestrian Senior marshal must already have Junior Marshal and Mounted Gaming Authorizations.
    if style.discipline.name == 'Equestrian' and style.name == 'Senior Marshal':
        if not (all_authorizations.filter(style__name='Junior Marshal', style__discipline__name='Equestrian', status__name='Active').exists() and all_authorizations.filter(style__name='Mounted Gaming', status__name='Active').exists()):
            return False, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.'

    # Rule 16: In order to authorize someone in Mounted Archery, Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting,
    # the Senior Marshal must have the same Authorizations.
    if style.name in ['Mounted Archery', 'Crest Combat', 'Mounted Heavy Combat', 'Driving', 'Foam-Tipped Jousting']:
        if not Authorization.objects.filter(person=marshal.person, style__name=style.name, style__discipline__name="Equestrian", status__name='Active').exists():
            return False, f'Must be authorized in {style.name} to authorize other participants.'

    # Rule 17: Junior and Senior marshals must be current members.
    if style.name in ['Junior Marshal', 'Senior Marshal']:
        if not membership_is_current(existing_fighter.user):
            return False, 'Must be a current member to be authorized as a marshal.'

    # Rule 18: You cannot renew a revoked authorization.
    if all_authorizations.filter(style__name=style.name, style__discipline__name=style.discipline.name, status__name='Revoked').exists():
        return False, 'Cannot renew a revoked authorization.'

    # Rule 19: Cannot duplicate/renew a pending authorization.
    if all_authorizations.filter(style__name=style.name, style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval', 'Needs Concurrence']).exists():
        return False, 'Cannot renew a pending authorization.'

    # Rule 20: Cannot make someone a junior marshal if they are already a senior marshal.
    if style.name == 'Junior Marshal':
        # If they already have an active senior marshal, they cannot be a junior marshal.
        if is_senior_marshal(existing_fighter.user, style.discipline.name):
            return False, 'Cannot make someone a junior marshal if they are already a senior marshal.'

        # Do they already have an active junior marshal?
        if not all_authorizations.filter(style__name='Junior Marshal', style__discipline__name=style.discipline.name, status__name='Active').exists():
            # We now know this is a new junior marshal. They cannot get a new junior marshal if there is a pending senior marshal.
            if all_authorizations.filter(style__name='Senior Marshal', style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).exists():
                return False, 'Cannot have a new junior marshal if a senior marshal is pending.'

    # Rule 21: Cannot add a new senior marshal if there is a pending junior marshal.
    if style.name == 'Senior Marshal':
        if all_authorizations.filter(style__name='Junior Marshal', style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).exists():
            return False, 'Cannot have a new senior marshal if a junior marshal is pending.'

    # Rule 22: Cannot make an authorization for yourself.
    if existing_fighter.user == marshal:
        return False, 'Cannot make an authorization for yourself.'

    # Rule 23: If the fighter is a minor, and authorizing in Rapier, Cut & Thrust, or Armored combat, they can only be authorized by a regional marshal.
    if existing_fighter.is_minor and style.discipline.name in ['Rapier Combat', 'Cut & Thrust', 'Armored']:
        if not is_regional_marshal(marshal):
            return False, 'Cannot authorize a minor in Rapier, Cut & Thrust, or Armored combat unless you are a regional marshal.'

    # Rule 24: Adults cannot be authorized as youth armored or youth rapier fighters. They can be authorized as youth marshals.
    if not existing_fighter.is_minor and style.discipline.name in ['Youth Armored', 'Youth Rapier']:
        if style.name != 'Junior Marshal' and style.name != 'Senior Marshal':
            return False, 'Adults cannot be authorized as youth armored or youth rapier fighters.'

    # Rule 25: New or significantly lapsed authorizations require a second concurrence.
    # This rule is enforced by setting the authorization status outside this validator.

    return True, 'Authorization follows all rules.'

def calculate_age(birthday):
    today = date.today()
    age = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return age

_CANADIAN_ABBREVIATIONS = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT',
}
_CANADIAN_NAMES = {
    'Alberta', 'British Columbia', 'Manitoba', 'New Brunswick', 'Newfoundland and Labrador',
    'Nova Scotia', 'Northwest Territories', 'Nunavut', 'Ontario', 'Prince Edward Island',
    'Quebec', 'Saskatchewan', 'Yukon',
}

def _is_canadian(user: User) -> bool:
    if user.country:
        country = user.country.strip().lower()
        if country in ['canada', 'ca']:
            return True
    if user.state_province:
        province = user.state_province.strip()
        if province in _CANADIAN_ABBREVIATIONS or province in _CANADIAN_NAMES:
            return True
        if province.upper() in _CANADIAN_ABBREVIATIONS:
            return True
        if province.title() in _CANADIAN_NAMES:
            return True
    return False

def _adult_age_for_user(user: User) -> int:
    return 19 if _is_canadian(user) else 18

def calculate_authorization_expiration(person: Person, style: WeaponStyle, today: Optional[date] = None) -> date:
    if today is None:
        today = date.today()
    base_years = 2 if style.discipline.name in ['Youth Armored', 'Youth Rapier'] else 4
    base_expiration = today + relativedelta(years=base_years)
    if person.is_minor and person.user.birthday:
        adult_date = person.user.birthday + relativedelta(years=_adult_age_for_user(person.user))
        return min(base_expiration, adult_date)
    return base_expiration

def is_authorized_in_discipline(user: User, discipline) -> bool:
    if not user or not hasattr(user, 'person'):
        return False
    discipline_name = discipline.name if hasattr(discipline, 'name') else discipline
    return Authorization.objects.with_effective_expiration().filter(
        person__user=user,
        style__discipline__name=discipline_name,
        effective_expiration_date__gte=date.today(),
        status__name='Active',
    ).exists()

def authorization_requires_concurrence(person: Person, style: WeaponStyle, today: Optional[date] = None) -> bool:
    if today is None:
        today = date.today()
    if style.name in ['Junior Marshal', 'Senior Marshal']:
        return False
    cutoff = today - relativedelta(years=1)
    max_effective = Authorization.objects.with_effective_expiration().filter(
        person=person,
        style__discipline=style.discipline,
        status__name='Active',
    ).aggregate(Max('effective_expiration_date'))['effective_expiration_date__max']
    if not max_effective:
        return True
    return max_effective < cutoff

def approve_authorization(request):
    """Add a concurance to a pending marshal authorization."""
    def get_action_note():
        return (request.POST.get('action_note') or '').strip()

    def record_note(authorization, action, note):
        if not requires_note:
            return
        AuthorizationNote.objects.create(
            authorization=authorization,
            created_by=request.user,
            action=action,
            note=note,
        )

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
    requires_note = authorization.style.name in ['Junior Marshal', 'Senior Marshal']
    note = get_action_note() if requires_note else ''
    if requires_note and not note:
        return False, 'A note is required for marshal promotion actions.'

    active_status = AuthorizationStatus.objects.get(name='Active')
    regional_status = AuthorizationStatus.objects.get(name='Needs Regional Approval')
    kingdom_status = AuthorizationStatus.objects.get(name='Needs Kingdom Approval')
    pending_waiver_status = AuthorizationStatus.objects.get(name='Pending Waiver')

    # Helper: determine if waiver is current strictly by waiver_expiration
    def waiver_current(u: User):
        return bool(u.waiver_expiration and u.waiver_expiration > date.today())

    # Rule 1: Kingdom authorization officer can approve any marshal by themselves.
    if is_kingdom_authorization_officer(marshal):
        # Marshal authorizations require current membership; never Pending Waiver for marshal styles
        if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
            authorization.status = active_status
            
            # Rule 1a/1b: Set base expiration based on discipline and minor status.
            authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
            authorization.save()
            # Ensure waiver does not trail authorization expiration
            user = authorization.person.user
            if (not user.waiver_expiration) or (user.waiver_expiration < authorization.expiration):
                user.waiver_expiration = authorization.expiration
                user.save()
            
            # Rule 1c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
            if authorization.style.name == 'Senior Marshal':
                remove_junior_marshal(authorization)
            record_note(authorization, 'marshal_approved', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'
        else:
            if waiver_current(authorization.person.user):
                authorization.status = active_status
                
                # Rule 1a/1b: Set base expiration based on discipline and minor status.
                authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
                authorization.save()
                # Ensure waiver does not trail authorization expiration
                user = authorization.person.user
                if (not user.waiver_expiration) or (user.waiver_expiration < authorization.expiration):
                    user.waiver_expiration = authorization.expiration
                    user.save()
                
                # Rule 1c: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
                if authorization.style.name == 'Senior Marshal':
                    remove_junior_marshal(authorization)
                record_note(authorization, 'marshal_approved', note)
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'
            else:
                authorization.status = pending_waiver_status
                authorization.save()
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization pending waiver.'


    if authorization.status.name == 'Pending':
        # Rule 2: must be a senior marshal in the discipline to approve (exception for missile marshal concurence).
        if not is_senior_marshal(marshal, discipline):
            return False, 'You must be a senior marshal in this discipline to approve this authorization.'
        
        # Rule 3: Cannot concur with an authorization you proposed.
        if authorization.marshal.user == marshal:
            return False, 'You cannot concur with your own authorization.'
        
        # Rule 4: If a pending authorization for Senior marshal is approved, it then goes to the region for approval.
        if authorization.style.name == 'Senior Marshal':
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
            authorization.status = regional_status
            authorization.save()
            record_note(authorization, 'marshal_concurred', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for regional approval!'
        
        # Rule 4a: If a junior marshal is approved it becomes active (or pending waiver).
        if authorization.style.name == 'Junior Marshal':
            if AUTHORIZATION_OFFICER_SIGN_OFF:
                authorization.status = kingdom_status
                authorization.save()
                record_note(authorization, 'marshal_concurred', note)
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom approval.'
            else:
                if not membership_is_current(authorization.person.user):
                    return False, 'Marshal authorizations require a current membership.'
                authorization.status = active_status
                authorization.save()
                record_note(authorization, 'marshal_concurred', note)
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
        # Rule 5: If the regional marshal approves a senior marshal, it becomes active (or pending waiver).
        if AUTHORIZATION_OFFICER_SIGN_OFF:
            authorization.status = kingdom_status
            authorization.save()
            record_note(authorization, 'marshal_approved', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom approval.'
        else:
            if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
                if not membership_is_current(authorization.person.user):
                    return False, 'Marshal authorizations require a current membership.'
                authorization.status = active_status
                authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
                authorization.save()
                # Rule 5a: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
                remove_junior_marshal(authorization)
                # Ensure waiver does not trail authorization expiration
                user = authorization.person.user
                if (not user.waiver_expiration) or (user.waiver_expiration < authorization.expiration):
                    user.waiver_expiration = authorization.expiration
                    user.save()
                record_note(authorization, 'marshal_approved', note)
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'
            else:
                if waiver_current(authorization.person.user):
                    authorization.status = active_status
                    authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
                    authorization.save()
                    # Rule 5a: If Senior marshal gets full approval, delete no longer relevant Junior marshal.
                    remove_junior_marshal(authorization)
                    # Ensure waiver does not trail authorization expiration
                    user = authorization.person.user
                    if (not user.waiver_expiration) or (user.waiver_expiration < authorization.expiration):
                        user.waiver_expiration = authorization.expiration
                        user.save()
                    return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'
                else:
                    authorization.status = pending_waiver_status
                    authorization.save()
                    return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization pending waiver.'

    else:
        return False, 'Authorization status not valid for confirmation.'


def validate_approve_authorization(marshal: User, authorization: Authorization):
    """Validate approval rules without persisting changes."""
    auth_region = authorization.person.branch.name if authorization.person.branch.is_region() else None
    discipline = authorization.style.discipline.name

    def waiver_current(u: User):
        return bool(u.waiver_expiration and u.waiver_expiration > date.today())

    if is_kingdom_authorization_officer(marshal):
        if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
        return True, 'OK'

    if authorization.status.name == 'Pending':
        if not is_senior_marshal(marshal, discipline):
            return False, 'You must be a senior marshal in this discipline to approve this authorization.'
        if authorization.marshal.user == marshal:
            return False, 'You cannot concur with your own authorization.'
        if authorization.style.name == 'Senior Marshal':
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
        if authorization.style.name == 'Junior Marshal':
            if AUTHORIZATION_OFFICER_SIGN_OFF:
                return True, 'OK'
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
        return True, 'OK'

    if authorization.status.name == 'Needs Regional Approval':
        if not is_regional_marshal(marshal, region=auth_region):
            return False, 'You must be a regional marshal in the same region as the fighter to approve this authorization.'
        if authorization.style.discipline.name == 'Missile':
            if not is_regional_marshal(marshal, 'Missile', auth_region) and not is_regional_marshal(marshal, 'Armored', auth_region):
                return False, 'You must be a regional missile marshal or the regional armored marshal to approve this authorization.'
        else:
            if not is_regional_marshal(marshal, discipline, auth_region):
                return False, 'You must be a regional marshal in this discipline to approve this authorization.'
        if AUTHORIZATION_OFFICER_SIGN_OFF:
            return True, 'OK'
        if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
            return True, 'OK'
        if waiver_current(authorization.person.user):
            return True, 'OK'
        return True, 'OK'

    return False, 'Authorization status not valid for confirmation.'


def validate_reject_authorization(marshal: User, authorization: Authorization):
    auth_discipline = authorization.style.discipline.name
    if is_regional_marshal(marshal, auth_discipline):
        return True, 'OK'
    return False, 'You do not have authority to reject this authorization.'

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
    marshal_status = Authorization.objects.with_effective_expiration().filter(
        person=person,
        style__discipline=discipline,
        effective_expiration_date__gte=date.today(),
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


