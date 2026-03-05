from datetime import date, datetime
import logging
from dateutil.relativedelta import relativedelta
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Max
from django.db.utils import OperationalError, ProgrammingError
from typing import Optional

from authorizations.models import BranchMarshal, Authorization, WeaponStyle, User, Branch, AuthorizationStatus, \
    Person, Discipline, AuthorizationNote, AuthorizationPortalSetting, Sanction

logger = logging.getLogger(__name__)

def authorization_officer_sign_off_enabled() -> bool:
    """Return whether Kingdom AO sign-off is currently required for applicable approvals."""
    try:
        setting_value = AuthorizationPortalSetting.objects.order_by('id').values_list(
            'require_kao_verification',
            flat=True,
        ).first()
    except (OperationalError, ProgrammingError):
        # Migration not applied yet; default to disabled.
        return False
    return bool(setting_value) if setting_value is not None else False

def membership_is_current(user):
    if not user.membership:
        return False
    if not user.membership_expiration:
        return False
    if user.membership_expiration < date.today():
        return False
    return True


def _marshal_status_expiration_for_office(person: Person, discipline: Discipline, today: Optional[date] = None):
    """Return the latest marshal-status expiration that satisfies an office requirement."""
    if today is None:
        today = date.today()
    if not person or not discipline:
        return None

    query = Authorization.objects.effectively_active(today=today).filter(
        person=person,
    )

    if discipline.name == 'Authorization Officer':
        return today + relativedelta(years=100)
    if discipline.name == 'Earl Marshal':
        query = query.filter(style__name='Senior Marshal')
    else:
        query = query.filter(
            style__discipline=discipline,
            style__name__in=['Junior Marshal', 'Senior Marshal'],
        )

    return query.aggregate(Max('effective_expiration_date'))['effective_expiration_date__max']


def marshal_office_effective_expiration(office: BranchMarshal, today: Optional[date] = None):
    """
    Effective officer capability expiration:
    min(warrant end, membership expiration, marshal status expiration requirement).
    """
    if today is None:
        today = date.today()
    if not office or not office.person or not office.discipline:
        return None

    person = office.person
    user = getattr(person, 'user', None)
    if not user:
        return None
    if not user.membership or not user.membership_expiration:
        return None

    marshal_status_expiration = _marshal_status_expiration_for_office(person, office.discipline, today=today)
    if not marshal_status_expiration:
        return None

    return min(office.end_date, user.membership_expiration, marshal_status_expiration)


def _has_active_office(query):
    today = date.today()
    offices = query.select_related('person__user', 'discipline')
    for office in offices:
        effective = marshal_office_effective_expiration(office, today=today)
        if effective and effective >= today:
            return True
    return False

def is_senior_marshal(user, discipline=None):
    """
    Checks if the user has an active Senior Marshal status for the given discipline.
    """
    query = Authorization.objects.effectively_active().filter(
        person__user=user,
        style__name='Senior Marshal',
    )

    if not membership_is_current(user):
        return False

    if discipline:
        query = query.filter(style__discipline__name=discipline)

    return query.exists()


def active_sanctions(person: Person, today: Optional[date] = None):
    if today is None:
        today = date.today()
    if not person:
        return Sanction.objects.none()
    return Sanction.objects.filter(
        person=person,
        start_date__lte=today,
        end_date__gte=today,
        lifted_at__isnull=True,
    )


def active_sanction_for_style(person: Person, style: WeaponStyle, today: Optional[date] = None):
    if today is None:
        today = date.today()
    if not person or not style:
        return None
    sanctions = active_sanctions(person, today=today).filter(
        Q(style=style) | (Q(style__isnull=True) & Q(discipline=style.discipline))
    ).select_related('discipline', 'style')
    exact_style = sanctions.filter(style=style).order_by('-end_date', '-id').first()
    if exact_style:
        return exact_style
    return sanctions.filter(style__isnull=True).order_by('-end_date', '-id').first()


def authorization_is_sanctioned(authorization: Authorization, today: Optional[date] = None) -> bool:
    if not authorization or not authorization.person or not authorization.style:
        return False
    return active_sanction_for_style(authorization.person, authorization.style, today=today) is not None

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

    return _has_active_office(query)


def is_regional_marshal(user, discipline=None, region=None):
    """
    Checks if the user is a current Regional Marshal for the given discipline or is the Earl Marshal.
    """
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

    return _has_active_office(query)

def is_kingdom_marshal(user, discipline=None):
    """
    Checks if the user is a current Kingdom Marshal for the given discipline or is the Earl Marshal.
    """

    if is_kingdom_earl_marshal(user):
        return True

    query = BranchMarshal.objects.filter(
        person__user=user,
        branch__name='An Tir',
        end_date__gte=date.today(),
    )

    if discipline:
        query = query.filter(discipline__name=discipline)

    return _has_active_office(query)


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

    return _has_active_office(query)


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

    return _has_active_office(query)


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
    active_authorizations = Authorization.objects.effectively_active().filter(person=existing_fighter)
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
            if not active_authorizations.filter(style__name='Single Sword', style__discipline__name='Rapier Combat').exists():
                return False, 'A fighter must be authorized with single sword as their first rapier authorization.'
        if style.discipline.name == 'Youth Rapier':
            if not active_authorizations.filter(style__name='Single Sword', style__discipline__name='Youth Rapier').exists():
                return False, 'A fighter must be authorized with single sword as their first youth rapier authorization.'
    
    # Rule 5: A Cut & Thrust fighter cannot have spear as their first authorization.
    if style.discipline.name == 'Cut & Thrust' and style.name == 'Spear':
        if not active_authorizations.filter(style__discipline__name='Cut & Thrust').exists():
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
        if not (active_authorizations.filter(style__name='Senior Ground Crew').exists() and active_authorizations.filter(style__name='General Riding').exists()):
            return False, 'Junior Equestrian marshal must have Senior Ground Crew and General Riding authorization.'

    # Rule 15: An Equestrian Senior marshal must already have Junior Marshal and Mounted Gaming Authorizations.
    if style.discipline.name == 'Equestrian' and style.name == 'Senior Marshal':
        if not (active_authorizations.filter(style__name='Junior Marshal', style__discipline__name='Equestrian').exists() and active_authorizations.filter(style__name='Mounted Gaming').exists()):
            return False, 'Senior Equestrian marshal must have Junior Equestrian marshal and Mounted Gaming authorization.'

    # Rule 16: In order to authorize someone in Mounted Archery, Crest Combat, Mounted Heavy Combat, Driving, or Foam-tipped Jousting,
    # the Senior Marshal must have the same Authorizations.
    if style.name in ['Mounted Archery', 'Crest Combat', 'Mounted Heavy Combat', 'Driving', 'Foam-Tipped Jousting']:
        if not Authorization.objects.effectively_active().filter(person=marshal.person, style__name=style.name, style__discipline__name="Equestrian").exists():
            return False, f'Must be authorized in {style.name} to authorize other participants.'

    # Rule 17: Junior and Senior marshals must be current members.
    if style.name in ['Junior Marshal', 'Senior Marshal']:
        if not membership_is_current(existing_fighter.user):
            return False, 'Must be a current member to be authorized as a marshal.'

    # Rule 18: You cannot authorize while an active sanction covers the style or discipline.
    if active_sanction_for_style(existing_fighter, style):
        return False, 'Cannot issue an authorization while a sanction is active for this style or discipline.'

    # Rule 19: Cannot duplicate/renew a pending authorization.
    if all_authorizations.filter(style__name=style.name, style__discipline__name=style.discipline.name, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval', 'Needs Concurrence']).exists():
        return False, 'Cannot renew a pending authorization.'

    # Rule 20: Cannot make someone a junior marshal if they are already a senior marshal.
    if style.name == 'Junior Marshal':
        # If they already have an active senior marshal, they cannot be a junior marshal.
        if is_senior_marshal(existing_fighter.user, style.discipline.name):
            return False, 'Cannot make someone a junior marshal if they are already a senior marshal.'

        # Do they already have an active junior marshal?
        if not active_authorizations.filter(style__name='Junior Marshal', style__discipline__name=style.discipline.name).exists():
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
    return Authorization.objects.effectively_active().filter(
        person__user=user,
        style__discipline__name=discipline_name,
    ).exists()

def authorization_requires_concurrence(person: Person, style: WeaponStyle, today: Optional[date] = None) -> bool:
    if today is None:
        today = date.today()
    if style.name in ['Junior Marshal', 'Senior Marshal']:
        return False
    cutoff = today - relativedelta(years=1)
    max_effective = Authorization.objects.with_effective_expiration().with_sanction_flag(today=today).filter(
        person=person,
        style__discipline=style.discipline,
        status__name='Active',
        has_active_sanction=False,
    ).aggregate(Max('effective_expiration_date'))['effective_expiration_date__max']
    if not max_effective:
        return True
    return max_effective < cutoff


def _authorization_region_name(authorization: Authorization) -> Optional[str]:
    """Resolve the fighter's region name for region-scoped authorization actions."""
    person = getattr(authorization, 'person', None)
    branch = getattr(person, 'branch', None)
    if not branch:
        return None
    if branch.is_region():
        return branch.name
    if branch.region:
        return branch.region.name
    return None


def _log_unresolved_authorization_region(action: str, authorization: Authorization, acting_user: Optional[User] = None) -> None:
    """Log region-resolution failures to support follow-up data correction."""
    person = getattr(authorization, 'person', None)
    branch = getattr(person, 'branch', None)
    parent_region = getattr(branch, 'region', None) if branch else None
    logger.error(
        'Could not resolve fighter region during %s: authorization_id=%s acting_user_id=%s fighter_user_id=%s '
        'branch_id=%s branch_name=%s branch_type=%s parent_region_id=%s parent_region_name=%s',
        action,
        getattr(authorization, 'id', None),
        getattr(acting_user, 'id', None),
        getattr(person, 'user_id', None),
        getattr(branch, 'id', None),
        getattr(branch, 'name', None),
        getattr(branch, 'type', None),
        getattr(parent_region, 'id', None),
        getattr(parent_region, 'name', None),
    )


def _active_note_offices(user: Optional[User], today: Optional[date] = None):
    if today is None:
        today = date.today()
    if not user or not getattr(user, 'is_authenticated', False) or not hasattr(user, 'person'):
        return []

    offices = []
    query = BranchMarshal.objects.filter(
        person=user.person,
        end_date__gte=today,
    ).select_related('branch', 'discipline', 'person__user')
    for office in query:
        effective_expiration = marshal_office_effective_expiration(office, today=today)
        if effective_expiration and effective_expiration >= today:
            offices.append(office)
    return offices


def _format_authorization_note_office(office: BranchMarshal) -> str:
    branch = getattr(office, 'branch', None)
    discipline = getattr(office, 'discipline', None)
    if not branch or not discipline:
        return ''
    discipline_label = discipline.name
    if discipline_label.endswith(' Combat'):
        discipline_label = discipline_label[:-7]
    if discipline.name == 'Authorization Officer' and branch.name == 'An Tir':
        return 'Kingdom Authorization Officer'
    if discipline.name == 'Earl Marshal' and branch.name == 'An Tir':
        return 'Kingdom Earl Marshal'
    if branch.name == 'An Tir':
        return f'Kingdom {discipline_label} Marshal'
    if branch.is_region():
        return f'Regional {discipline_label} Marshal'
    return f'{branch.name} {discipline_label} Marshal'


def _authorization_note_office_score(
    office: BranchMarshal,
    *,
    authorization: Authorization,
    action: str,
    region_name: Optional[str],
) -> int:
    branch = getattr(office, 'branch', None)
    discipline = getattr(office, 'discipline', None)
    authorization_discipline = getattr(getattr(getattr(authorization, 'style', None), 'discipline', None), 'name', None)
    status_name = getattr(getattr(authorization, 'status', None), 'name', '')

    if not branch or not discipline or not authorization_discipline:
        return 0

    if discipline.name == authorization_discipline:
        score = 100
        if status_name == 'Needs Regional Approval' or action == 'marshal_rejected':
            if branch.is_region() and branch.name == region_name:
                score += 60
            elif branch.name == 'An Tir':
                score += 50
            elif branch.region and branch.region.name == region_name:
                score += 20
            else:
                score += 10
        elif status_name == 'Needs Kingdom Approval' or action in ['sanction_issued', 'sanction_lifted']:
            if branch.name == 'An Tir':
                score += 60
            elif branch.is_region():
                score += 20
            else:
                score += 10
        else:
            if branch.name == 'An Tir':
                score += 40
            elif branch.is_region():
                score += 30
            else:
                score += 20
        return score

    if discipline.name == 'Authorization Officer':
        if status_name == 'Needs Kingdom Approval' or action in ['sanction_issued', 'sanction_lifted']:
            return 200
        return 0

    if discipline.name == 'Earl Marshal':
        if status_name == 'Needs Regional Approval' or action in ['marshal_rejected', 'sanction_issued', 'sanction_lifted']:
            return 150 if branch.name == 'An Tir' else 140
        if action in ['marshal_proposed', 'marshal_concurred', 'marshal_approved']:
            return 80 if branch.name == 'An Tir' else 70
        return 0

    return 0


def authorization_note_office_label(
    user: Optional[User],
    authorization: Authorization,
    action: str,
    *,
    today: Optional[date] = None,
) -> str:
    if today is None:
        today = date.today()

    active_offices = _active_note_offices(user, today=today)
    if len(active_offices) > 1:
        logger.error(
            'User has multiple active marshal offices while creating authorization note: '
            'user_id=%s authorization_id=%s action=%s office_ids=%s',
            getattr(user, 'id', None),
            getattr(authorization, 'id', None),
            action,
            [office.id for office in active_offices],
        )

    region_name = _authorization_region_name(authorization)
    scored_offices = [
        (
            _authorization_note_office_score(
                office,
                authorization=authorization,
                action=action,
                region_name=region_name,
            ),
            office,
        )
        for office in active_offices
    ]
    relevant_offices = [(score, office) for score, office in scored_offices if score > 0]
    if relevant_offices:
        relevant_offices.sort(
            key=lambda item: (
                item[0],
                1 if item[1].branch and item[1].branch.name == 'An Tir' else 0,
                getattr(item[1], 'id', 0),
            ),
            reverse=True,
        )
        top_score = relevant_offices[0][0]
        top_offices = [office for score, office in relevant_offices if score == top_score]
        if len(top_offices) > 1:
            logger.error(
                'Multiple equally relevant marshal offices while creating authorization note: '
                'user_id=%s authorization_id=%s action=%s office_ids=%s',
                getattr(user, 'id', None),
                getattr(authorization, 'id', None),
                action,
                [office.id for office in top_offices],
            )
        return _format_authorization_note_office(top_offices[0])

    discipline_name = getattr(getattr(getattr(authorization, 'style', None), 'discipline', None), 'name', None)
    if discipline_name and is_senior_marshal(user, discipline_name):
        return 'Senior Marshal'
    return ''


def create_authorization_note(
    *,
    authorization: Authorization,
    created_by: Optional[User],
    action: str,
    note: str,
) -> AuthorizationNote:
    return AuthorizationNote.objects.create(
        authorization=authorization,
        created_by=created_by,
        action=action,
        office=authorization_note_office_label(created_by, authorization, action),
        note=note,
    )

def approve_authorization(request):
    """Add a concurance to a pending marshal authorization."""
    def _display_name_for_user(user: User) -> str:
        if hasattr(user, 'person') and user.person and user.person.sca_name:
            return user.person.sca_name
        return user.get_full_name() or user.username

    def _resolve_submit_as_user():
        submit_as = request.user
        if not is_kingdom_authorization_officer(request.user):
            return submit_as, None
        submit_as_raw = (request.POST.get('submit_as_user_id') or '').strip()
        if not submit_as_raw:
            return submit_as, None
        try:
            submit_as_id = int(submit_as_raw)
        except (TypeError, ValueError):
            return None, 'Selected submitting marshal was not found.'
        try:
            submit_as = User.objects.select_related('person').get(id=submit_as_id)
        except User.DoesNotExist:
            return None, 'Selected submitting marshal was not found.'
        if not hasattr(submit_as, 'person') or submit_as.person is None:
            return None, 'Selected submitting marshal has no fighter record.'
        return submit_as, None

    def get_action_note():
        return (request.POST.get('action_note') or '').strip()

    def record_note(authorization, action, note):
        if not requires_note:
            return
        create_authorization_note(
            authorization=authorization,
            created_by=marshal,
            action=action,
            note=note,
        )

    def save_authorization(authorization):
        authorization.updated_by = marshal
        authorization.save()

    def remove_junior_marshal(authorization):
        discipline = authorization.style.discipline.name
        try:
            junior_marshal = Authorization.objects.get(person=authorization.person, style__name='Junior Marshal', style__discipline__name=discipline)
            junior_marshal.delete()
            return True
        except Authorization.DoesNotExist:
            return True

    request_user = request.user
    marshal, submit_as_error = _resolve_submit_as_user()
    if submit_as_error:
        return False, submit_as_error
    authorization = Authorization.objects.get(id=request.POST['authorization_id'])
    auth_region = _authorization_region_name(authorization)
    discipline = authorization.style.discipline.name
    requires_note = authorization.style.name in ['Junior Marshal', 'Senior Marshal']
    note = get_action_note() if requires_note else ''
    if requires_note and not note:
        return False, 'A note is required for marshal promotion actions.'
    if requires_note and request_user.id != marshal.id:
        note = (
            f'{note}\n\n'
            f'Submitted as {_display_name_for_user(marshal)} by {_display_name_for_user(request_user)}.'
        )

    active_status = AuthorizationStatus.objects.get(name='Active')
    regional_status = AuthorizationStatus.objects.get(name='Needs Regional Approval')
    kingdom_status = AuthorizationStatus.objects.get(name='Needs Kingdom Approval')
    pending_waiver_status = AuthorizationStatus.objects.get(name='Pending Waiver')

    # Helper: determine if waiver is current strictly by waiver_expiration
    def waiver_current(u: User):
        return bool(u.waiver_expiration and u.waiver_expiration > date.today())

    sign_off_required = authorization_officer_sign_off_enabled()

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
            save_authorization(authorization)
            record_note(authorization, 'marshal_concurred', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for regional approval!'
        
        # Rule 4a: If a junior marshal is approved it becomes active (or pending waiver).
        if authorization.style.name == 'Junior Marshal':
            if sign_off_required:
                authorization.status = kingdom_status
                save_authorization(authorization)
                record_note(authorization, 'marshal_concurred', note)
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom approval.'
            else:
                if not membership_is_current(authorization.person.user):
                    return False, 'Marshal authorizations require a current membership.'
                authorization.status = active_status
                save_authorization(authorization)
                record_note(authorization, 'marshal_concurred', note)
                return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'

    # Rule 5: If the authorization is out for regional approval, you need to be the correct regional marshal to approve it (exception that Armored can approve Missile).
    elif authorization.status.name == 'Needs Regional Approval':
        if not auth_region:
            _log_unresolved_authorization_region('regional approval', authorization, request_user)
            return False, 'Could not determine the fighter region for regional approval.'
        if not is_regional_marshal(marshal, region=auth_region):
            return False, 'You must be a regional marshal in the same region as the fighter to approve this authorization.'
        if authorization.style.discipline.name == 'Missile':
            if not is_regional_marshal(marshal, 'Missile', auth_region) and not is_regional_marshal(marshal, 'Armored', auth_region):
                return False, 'You must be a regional missile marshal or the regional armored marshal to approve this authorization.'
        else:
            if not is_regional_marshal(marshal, discipline, auth_region):
                return False, 'You must be a regional marshal in this discipline to approve this authorization.'
        # Rule 5: If the regional marshal approves a senior marshal, it becomes active (or pending waiver).
        if sign_off_required:
            authorization.status = kingdom_status
            save_authorization(authorization)
            record_note(authorization, 'marshal_approved', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization ready for kingdom approval.'
        else:
            if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
                if not membership_is_current(authorization.person.user):
                    return False, 'Marshal authorizations require a current membership.'
                authorization.status = active_status
                authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
                save_authorization(authorization)
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
                    save_authorization(authorization)
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
                    save_authorization(authorization)
                    return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization pending waiver.'

    elif authorization.status.name == 'Needs Kingdom Approval':
        if not is_kingdom_authorization_officer(request_user):
            return False, 'Only the Kingdom Authorization Officer can approve this authorization.'

        # Marshal authorizations require current membership; never Pending Waiver for marshal styles
        if authorization.style.name in ['Junior Marshal', 'Senior Marshal']:
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
            authorization.status = active_status
        else:
            authorization.status = active_status if waiver_current(authorization.person.user) else pending_waiver_status

        authorization.expiration = calculate_authorization_expiration(authorization.person, authorization.style)
        save_authorization(authorization)

        # Ensure waiver does not trail authorization expiration for active approvals.
        if authorization.status == active_status:
            user = authorization.person.user
            if (not user.waiver_expiration) or (user.waiver_expiration < authorization.expiration):
                user.waiver_expiration = authorization.expiration
                user.save()

        if authorization.style.name == 'Senior Marshal':
            remove_junior_marshal(authorization)

        if authorization.status == active_status:
            record_note(authorization, 'marshal_approved', note)
            return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization approved!'
        return True, f'{authorization.style.discipline.name} {authorization.style.name} authorization pending waiver.'

    else:
        return False, 'Authorization status not valid for confirmation.'


def validate_approve_authorization(request_user: User, marshal: User, authorization: Authorization):
    """Validate approval rules without persisting changes."""
    auth_region = _authorization_region_name(authorization)
    discipline = authorization.style.discipline.name

    def waiver_current(u: User):
        return bool(u.waiver_expiration and u.waiver_expiration > date.today())

    sign_off_required = authorization_officer_sign_off_enabled()

    if authorization.status.name == 'Needs Kingdom Approval':
        if not is_kingdom_authorization_officer(request_user):
            return False, 'Only the Kingdom Authorization Officer can approve this authorization.'
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
            if sign_off_required:
                return True, 'OK'
            if not membership_is_current(authorization.person.user):
                return False, 'Marshal authorizations require a current membership.'
        return True, 'OK'

    if authorization.status.name == 'Needs Regional Approval':
        if not auth_region:
            _log_unresolved_authorization_region('regional approval validation', authorization, request_user)
            return False, 'Could not determine the fighter region for regional approval.'
        if not is_regional_marshal(marshal, region=auth_region):
            return False, 'You must be a regional marshal in the same region as the fighter to approve this authorization.'
        if authorization.style.discipline.name == 'Missile':
            if not is_regional_marshal(marshal, 'Missile', auth_region) and not is_regional_marshal(marshal, 'Armored', auth_region):
                return False, 'You must be a regional missile marshal or the regional armored marshal to approve this authorization.'
        else:
            if not is_regional_marshal(marshal, discipline, auth_region):
                return False, 'You must be a regional marshal in this discipline to approve this authorization.'
        if sign_off_required:
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
    auth_region = _authorization_region_name(authorization)

    if not auth_region:
        _log_unresolved_authorization_region('regional rejection validation', authorization, marshal)
        return False, 'Could not determine the fighter region for regional rejection.'
    if not is_regional_marshal(marshal, region=auth_region):
        return False, 'You must be a regional marshal in the same region as the fighter to reject this authorization.'
    if auth_discipline == 'Missile':
        if not is_regional_marshal(marshal, 'Missile', auth_region) and not is_regional_marshal(marshal, 'Armored', auth_region):
            return False, 'You must be a regional missile marshal or the regional armored marshal to reject this authorization.'
    elif not is_regional_marshal(marshal, auth_discipline, auth_region):
        return False, 'You must be a regional marshal in this discipline to reject this authorization.'
    return True, 'OK'


def can_manage_branch_marshal_office(user: User, branch: Branch, discipline: Discipline) -> bool:
    """Return whether the user can appoint/remove the specified marshal office."""
    # Permission matrix for marshal-office management (appoint/remove):
    # - Kingdom Authorization Officer:
    #   - May manage any marshal office.
    #   - Is the only role that may manage Kingdom Earl Marshal (An Tir + Earl Marshal).
    #   - Is the only role that may manage Authorization Officer offices (An Tir only).
    # - Kingdom Earl Marshal:
    #   - May manage all marshal offices except:
    #     - Kingdom Earl Marshal office.
    #     - Authorization Officer office.
    # - Kingdom Discipline Marshal:
    #   - May manage only same-discipline offices below kingdom level
    #     (not An Tir, not Earl Marshal, not Authorization Officer).
    # - Regional/Branch marshals:
    #   - No office-management authority in this helper.
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not branch or not discipline:
        return False

    branch_name = branch.name
    discipline_name = discipline.name

    # Only the Kingdom Authorization Officer can manage Authorization Officer offices,
    # and those offices may only exist at Kingdom level.
    if discipline_name == 'Authorization Officer':
        return branch_name == 'An Tir' and is_kingdom_authorization_officer(user)

    # Only the Kingdom Authorization Officer can manage the Kingdom Earl Marshal office.
    if discipline_name == 'Earl Marshal' and branch_name == 'An Tir':
        return is_kingdom_authorization_officer(user)

    if is_kingdom_authorization_officer(user):
        return True

    if is_kingdom_earl_marshal(user):
        return True

    # Kingdom discipline marshals can manage lower offices in their own discipline only.
    if discipline_name in ['Earl Marshal', 'Authorization Officer']:
        return False
    if branch_name == 'An Tir':
        return False
    return is_kingdom_marshal(user, discipline_name)


def can_manage_any_branch_marshal_office(user: User) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return (
        is_kingdom_authorization_officer(user)
        or is_kingdom_earl_marshal(user)
        or is_kingdom_marshal(user)
    )

@login_required
def appoint_branch_marshal(request):
    """Adds a new marshal office appointment."""

    # Get the data from the request.
    try:
        person = Person.objects.get(sca_name=request.POST['person'])
        branch = Branch.objects.get(name=request.POST['branch'])
        discipline = Discipline.objects.get(name=request.POST['discipline'])
        start_date_raw = request.POST['start_date']
    except Exception:
        return False, 'Missing data'
    try:
        start_date = datetime.strptime(start_date_raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False, 'Invalid start date'

    if branch.type == 'Other':
        return False, 'Selected branch type is not eligible for marshal appointments.'

    # Must be a current member.
    if not membership_is_current(person.user):
        return False, 'Must be a current member to be a branch marshal.'

    # Rule 0: Must have appointment authority for this office.
    if not can_manage_branch_marshal_office(request.user, branch, discipline):
        return False, 'You do not have authority to appoint this marshal office.'

    # Rule 1: Prevent duplicate active appointments for the exact same office/person.
    active_same_office = BranchMarshal.objects.filter(
        person=person,
        branch=branch,
        discipline=discipline,
        end_date__gte=date.today(),
    ).exists()
    if active_same_office:
        return False, 'This fighter already holds this active marshal office.'

    # Rule 1b: One person may only hold one active marshal office at a time.
    # Multiple different people may still hold the same office concurrently.
    has_other_active_office = BranchMarshal.objects.filter(
        person=person,
        end_date__gte=date.today(),
    ).exclude(
        branch=branch,
        discipline=discipline,
    ).exists()
    if has_other_active_office:
        return False, 'Can only serve as one branch marshal position at a time.'

    # Rule 2: Authorization Officer offices are only at Kingdom level.
    if discipline.name == 'Authorization Officer':
        if branch.name != 'An Tir':
            return False, 'An Tir is the only branch that can have the authorization officer.'

    # Rule 2b: Earl Marshal offices are only allowed at region/kingdom level.
    if discipline.name == 'Earl Marshal' and not branch.is_region():
        return False, 'Earl Marshal offices may only be appointed at regional or kingdom level.'

    # Rule 3: Authorization Officer appointment does not require marshal authorization.
    if discipline.name == 'Authorization Officer':
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
    marshal_status = Authorization.objects.with_effective_expiration().with_sanction_flag().filter(
        person=person,
        style__discipline=discipline,
        effective_expiration_date__gte=date.today(),
        status__name='Active',
        has_active_sanction=False,
        style__name__in=['Senior Marshal', 'Junior Marshal']
    ).exists()

    # Rule 5: Must be a marshal in the appropriate discipline to be a branch marshal. Can be a Junior marshal.
    if not marshal_status:
        if not discipline.name == 'Earl Marshal':
            return False, 'Must be a marshal in the discipline to be a branch marshal.'

    BranchMarshal.objects.create(
        person=person,
        branch=branch,
        discipline=discipline,
        start_date=start_date,
        end_date=start_date + relativedelta(years=2),
    )

    return True, 'Branch marshal appointed.'


