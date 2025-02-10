from dateutil.relativedelta import relativedelta
from django.core.mail import send_mail
from django.conf import settings
import random
import string
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse
from datetime import date, timedelta, datetime
from django.db.models import Q
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from .models import User, Authorization, Region, Branch, Discipline,  WeaponStyle, AuthorizationStatus, Person, BranchMarshal
from .permissions import is_senior_marshal, is_branch_marshal, is_regional_marshal, is_kingdom_marshal, is_kingdom_authorization_officer, authorization_follows_rules, calculate_age, approve_authorization, appoint_branch_marshal
from itertools import groupby
from operator import attrgetter
from pdfrw import PdfReader, PdfWriter, PdfName
from django.contrib import messages
from django import forms

all_region_names = Region.objects.exclude(name='An Tir').values_list('name', flat=True)
all_branch_names = Branch.objects.exclude(Q(name__in=all_region_names) | Q(name='An Tir')).values_list('name', flat=True)
all_states = [
    'Alabama',
    'Alaska',
    'Arizona',
    'Arkansas',
    'California',
    'Colorado',
    'Connecticut',
    'Delaware',
    'Florida',
    'Georgia',
    'Hawaii',
    'Idaho',
    'Illinois',
    'Indiana',
    'Iowa',
    'Kansas',
    'Kentucky',
    'Louisiana',
    'Maine',
    'Maryland',
    'Massachusetts',
    'Michigan',
    'Minnesota',
    'Mississippi',
    'Missouri',
    'Montana',
    'Nebraska',
    'Nevada',
    'New Hampshire',
    'New Jersey',
    'New Mexico',
    'New York',
    'North Carolina',
    'North Dakota',
    'Ohio',
    'Oklahoma',
    'Oregon',
    'Pennsylvania',
    'Rhode Island',
    'South Carolina',
    'South Dakota',
    'Tennessee',
    'Texas',
    'Utah',
    'Vermont',
    'Virginia',
    'Washington',
    'West Virginia',
    'Wisconsin',
    'Wyoming',
]

all_provinces = [
    'Alberta',
    'British Columbia',
    'Manitoba',
    'New Brunswick',
    'Newfoundland and Labrador',
    'Nova Scotia',
    'Ontario',
    'Prince Edward Island',
    'Quebec',
    'Saskatchewan',
    'Northwest Territories',
    'Nunavut',
    'Yukon',
]
state_choices = [(state, state) for state in all_states]
province_choices = [(province, province) for province in all_provinces]
state_province_choices = state_choices + province_choices

# Create your views here.
def index(request):
    """This is the page they land on for the """

    all_people = Person.objects.all().order_by('sca_name').values_list('sca_name',                                                                            flat=True).distinct()
    fighter_name = request.GET.get('sca_name')

    if fighter_name:
        fighter_id = Person.objects.get(sca_name=fighter_name).user_id
        return redirect('fighter', person_id=fighter_id)

    if request.user.is_anonymous:
        return render(request, 'authorizations/index.html' , {'all_people': all_people})

    pending_authorizations = []
    person = Person.objects.get(user_id=request.user.id) # Who is the person accessing the page
    senior_marshal = is_senior_marshal(request.user)
    branch_marshal = is_branch_marshal(request.user)
    regional_marshal = is_regional_marshal(request.user)
    kingdom_marshal = is_kingdom_marshal(request.user)
    kingdom_earl_marshal = is_kingdom_marshal(request.user, 'Earl Marshal')
    auth_officer = is_kingdom_authorization_officer(request.user)

    # Are they in the branch marshal table at all?
    try:
        marshal = BranchMarshal.objects.get(person=person, end_date__gte=date.today())
    except BranchMarshal.DoesNotExist:
        marshal = None

    # If they are in the branch marshal table, are they a branch marshal, regional marshal, or authorization officer
    if marshal:
        if branch_marshal:
            if senior_marshal:
                branch = marshal.branch
                discipline = marshal.discipline
                pending_authorizations = Authorization.objects.filter(person__branch=branch, style__discipline=discipline, status__name='Pending').order_by('expiration')
        if regional_marshal:
            region = Region.objects.get(name=marshal.branch.name)
            discipline = marshal.discipline
            pending_authorizations = Authorization.objects.filter(person__branch__region__name=region, style__discipline=discipline, status__name='Needs Regional Approval').order_by('expiration')
        if kingdom_marshal:
            discipline = marshal.discipline
            pending_authorizations = Authorization.objects.filter(style__discipline=discipline,
                                                          status__name='Needs Regional Approval').order_by('expiration')
        if kingdom_earl_marshal:
            pending_authorizations = Authorization.objects.filter(status__name='Needs Regional Approval').order_by('expiration')
        if auth_officer:
            pending_authorizations = Authorization.objects.filter(status__name='Needs Kingdom Approval').order_by('expiration')

    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to perform this action.')
            return redirect('login')

        action = request.POST.get('action')
        if action == 'approve_authorization':
            is_valid, mssg = approve_authorization(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
        elif action == 'reject_authorization':
            # Check if the user has the authorization over this discipline
            authorization = Authorization.objects.get(id=request.POST['bad_authorization_id'])
            is_valid, mssg = reject_authorization(request, authorization)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)


    return render(request, 'authorizations/index.html', {
        'senior_marshal': senior_marshal,
        'branch_marshal': branch_marshal,
        'regional_marshal': regional_marshal,
        'kingdom_marshal': kingdom_marshal,
        'kingdom_earl_marshal': kingdom_earl_marshal,
        'auth_officer': auth_officer,
        'pending_authorizations': pending_authorizations,
        'all_people': all_people
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':

        # Attempt to sign user in
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)

        # Check if authentication successful
        if user is not None:
            login(request, user)
            if not user.has_logged_in:
                messages.error(request, 'You must change your password when you first log into the system.')
                return redirect('password_reset', user_id=user.id)
            return HttpResponseRedirect(reverse('index'))
        else:
            return render(request, 'authorizations/login.html', {
                'message': 'Invalid email and/or password.'
            })
    else:
        return render(request, 'authorizations/login.html')


def logout_view(request):
    logout(request)
    return HttpResponseRedirect(reverse('index'))


@login_required
def password_reset(request, user_id):
    # Make sure it is the right user.
    if request.user.id != user_id:
        messages.error(request, "You don't have permission to reset this password.")
        return redirect('index')


    # Get the old password
    if request.method == 'POST':
        old_password = request.POST['old_password']
        username = request.user.username
        user = authenticate(request, username=username, password=old_password)
        if user is None:
            return render(request, 'authorizations/password_reset.html', {
                'message': 'Invalid old password.'
            })

        # Create new password
        password = request.POST['password']
        confirmation = request.POST['confirmation']
        if password != confirmation:
            return render(request, 'authorizations/password_reset.html', {
                'message': 'Passwords must match.'
            })

        # Validate the new password
        try:
            validate_password(password, user=request.user)
        except ValidationError as e:
            return render(request, 'authorizations/password_reset.html', {
                'message': ' '.join(e.messages),
            })

        # Set the new password
        user = User.objects.get(id=user_id)
        user.set_password(password)
        user.has_logged_in = True
        user.save()
        login(request, user)
        messages.success(request, 'Your password has been reset successfully.')
        return redirect('index')
    else:
        return render(request, 'authorizations/password_reset.html')


def recover_account(request):
    if request.method == 'POST':
        email = request.POST['email']
        if not email:
            messages.error(request, 'Please enter an email.')
            return render(request, 'authorizations/recover_account.html')
        action = request.POST.get('action')
        new_password = generate_random_password()
        login_path = reverse('login')
        login_url = f"{settings.SITE_URL}{login_path}"
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, 'No account with that email was found.')
            return render(request, 'authorizations/recover_account.html')
        username = user.username
        if action == 'reset_password':
            user.set_password(new_password)
            user.has_logged_in = False
            user.save()
            # Send the password via email
            send_mail(
                'An Tir Authorization: Password Reset',
                f'Your password has been reset. Your credentials are:\nTemporary Password: {new_password}\n'
                f'Please reset your password after logging in.',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            messages.success(request,
                             'Temporary Password sent')
            return redirect('login')
        elif action == 'get_username':
            send_mail(
                'An Tir Authorization: Username Request',
                f'Your username is: {username}\n'
                f'You can click this link to log in: {login_url}',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            messages.success(request,
                             'Username sent')
            return redirect('login')
        else:
            messages.error(request, 'Invalid action.')
            return render(request, 'authorizations/recover_account.html')

    return render(request, 'authorizations/recover_account.html')


def generate_random_password(length=12):
    characters = string.ascii_letters + string.digits + string.punctuation
    password = ''.join(random.choice(characters) for _ in range(length))
    return password


def search(request):
    """Returns a table of all the authorizations in the database."""

    # Retrieve Get parameters, create dynamic filter, and filter the returns
    dynamic_filter = Q(status__name='Active')
    sca_name = request.GET.get('sca_name')
    if sca_name:
        dynamic_filter &= Q(person__sca_name=sca_name)
    region = request.GET.get('region')
    if region:
        dynamic_filter &= Q(person__branch__region__name=region)
    branch = request.GET.get('branch')
    if branch:
        dynamic_filter &= Q(person__branch__name=branch)
    discipline = request.GET.get('discipline')
    if discipline:
        dynamic_filter &= Q(style__discipline__name=discipline)
    style = request.GET.get('style')
    if style:
        dynamic_filter &= Q(style__name=style)
    marshal = request.GET.get('marshal')
    if marshal:
        dynamic_filter &= Q(marshal__sca_name=marshal)
    start_date = request.GET.get('start_date')
    if start_date:
        dynamic_filter &= Q(expiration__gte=start_date)
    end_date = request.GET.get('end_date')
    if end_date:
        dynamic_filter &= Q(expiration__lte=end_date)
    is_minor = request.GET.get('is_minor')
    if is_minor:
        is_minor = True if is_minor == 'True' else False
        dynamic_filter &= Q(person__is_minor=is_minor)

    # Get all authorizations
    sort = request.GET.get('sort', 'person__sca_name')  # Default to SCA Name

    authorization_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(dynamic_filter).order_by(sort)

    # Create table drop down options based on dynamic filters
    sca_name_options = authorization_list.order_by('person__sca_name').values_list('person__sca_name', flat=True).distinct()
    region_options = authorization_list.order_by('person__branch__region__name').values_list('person__branch__region__name', flat=True).distinct()
    branch_options = authorization_list.order_by('person__branch__name').values_list('person__branch__name', flat=True).distinct()
    discipline_options = authorization_list.order_by('style__discipline__name').values_list('style__discipline__name', flat=True).distinct()
    style_options = authorization_list.order_by('style__name').values_list('style__name', flat=True).distinct()
    marshal_options = authorization_list.order_by('marshal__sca_name').values_list('marshal__sca_name', flat=True).distinct()

    # Pagination
    items_per_page = int(request.GET.get('items_per_page', 10))
    current_page = request.GET.get('page', 1)
    paginator = Paginator(authorization_list, items_per_page)
    page_obj = paginator.get_page(current_page)

    # Group by person for card view
    grouped_authorizations = [
        (person, list(authorizations))
        for person, authorizations in groupby(authorization_list, key=attrgetter('person'))
    ]

    # Control whether they go to search or results
    goal = request.GET.get('goal', None)

    return render(
        request,
        'authorizations/search.html',
        {
            'page_obj': page_obj,
            'items_per_page': items_per_page,
            'sca_name_options': sca_name_options,
            'region_options': region_options,
            'branch_options': branch_options,
            'discipline_options': discipline_options,
            'style_options': style_options,
            'marshal_options': marshal_options,
            'today': date.today(),
            'goal': goal,
            'grouped_authorizations': grouped_authorizations
        },
    )


def fighter(request, person_id):
    """Pass in a single person id. Return all of their current authorizations in a card view.
    Give a link to download or print a pdf or image of their card.
    This should ideally look like the official card.
    Give a link to add a new authorization if the user is a senior marshal.

    Create a link on the authorization search to go to this page."""

    person = Person.objects.get(user_id=person_id)

    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to perform this action.')
            return redirect('login')

        action = request.POST.get('action')
        if action == 'add_authorization':
            add_authorization(request, person_id)
        elif action == 'approve_authorization':
            is_valid, mssg = approve_authorization(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
        elif action == 'appoint_branch_marshal':
            is_valid, mssg = appoint_branch_marshal(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
        elif action == 'reject_authorization':
            # Check if the user has the authorization over this discipline
            auth_id = request.POST['bad_authorization_id']
            authorization = Authorization.objects.get(id=request.POST['bad_authorization_id'])
            is_valid, mssg = reject_authorization(request, authorization)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)




    # Get the lists of authorizations
    authorization_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(person_id=person_id, status__name='Active', expiration__gte=date.today()).order_by('style__discipline__name', 'expiration', 'style__name')

    pending_authorization_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(person_id=person_id, status__name__in=['Pending', 'Needs Regional Approval', 'Needs Kingdom Approval']).order_by(
        'style__discipline__name', 'expiration', 'style__name')

    sanctions_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(person_id=person_id, status__name='Revoked').order_by(
        'style__discipline__name', 'expiration', 'style__name'
    )

    # Group by discipline

    equestrian = False
    youth = False
    fighter = False

    grouped_authorizations = {}
    for auth in authorization_list:
        discipline_name = auth.style.discipline.name
        if discipline_name not in grouped_authorizations:
            if discipline_name == 'Equestrian':
                equestrian = True
            elif discipline_name == 'Youth Armored':
                youth = True
            elif discipline_name == 'Youth Rapier':
                youth = True
            else:
                fighter = True
            grouped_authorizations[discipline_name] = {
                'marshal_name': auth.marshal.sca_name,
                'earliest_expiration': auth.expiration,
                'styles': [auth.style.name],
            }
        else:
            if auth.expiration < grouped_authorizations[discipline_name]['earliest_expiration']:
                grouped_authorizations[discipline_name]['earliest_expiration'] = auth.expiration
                grouped_authorizations[discipline_name]['marshal_name'] = auth.marshal.sca_name
            style_name = auth.style.name
            if style_name not in grouped_authorizations[discipline_name]['styles']:
                grouped_authorizations[discipline_name]['styles'].append(style_name)

    pending_authorizations = {}
    for auth in pending_authorization_list:
        discipline_name = auth.style.discipline.name
        if discipline_name not in pending_authorizations:
            pending_authorizations[discipline_name] = {
                'auth_id': auth.id,
                'marshal_name': auth.marshal.sca_name,
                'earliest_expiration': auth.expiration,
                'styles': [auth.style.name],
                'status': auth.status.name
            }
        else:
            if auth.expiration < pending_authorizations[discipline_name]['earliest_expiration']:
                pending_authorizations[discipline_name]['earliest_expiration'] = auth.expiration
                pending_authorizations[discipline_name]['marshal_name'] = auth.marshal.sca_name
            style_name = auth.style.name
            if style_name not in pending_authorizations[discipline_name]['styles']:
                pending_authorizations[discipline_name]['styles'].append(style_name)

    sanctions = {}
    for auth in sanctions_list:
        discipline_name = auth.style.discipline.name
        if discipline_name not in sanctions:
            sanctions[discipline_name] = {
                'auth_id': auth.id,
                'earliest_expiration': auth.expiration,
                'styles': [auth.style.name],
                'status': auth.status.name
            }
        else:
            if auth.expiration < sanctions[discipline_name]['earliest_expiration']:
                sanctions[discipline_name]['earliest_expiration'] = auth.expiration
            style_name = auth.style.name
            if style_name not in sanctions[discipline_name]['styles']:
                sanctions[discipline_name]['styles'].append(style_name)

    if 'pdf' in request.GET:
        template_id = request.GET.get('template_id')
        try:
            return generate_fighter_card(request, person_id, template_id)
        except Exception as e:
            messages.error(request, 'You don\'t have the required authorizations to view this card.')
            return redirect('fighter', person_id=person_id)

    try:
        branch_officer = BranchMarshal.objects.get(person=person, end_date__gte=date.today())
    except BranchMarshal.DoesNotExist:
        branch_officer = None

    if request.user.is_anonymous:
        return render(
            request,
            'authorizations/fighter.html',
            {
                'person': person,
                'authorization_list': grouped_authorizations,
                'pending_authorization_list': pending_authorizations,
                'equestrian': equestrian,
                'youth': youth,
                'fighter': fighter,
                'is_marshal': False,
                'branch_officer': branch_officer,
                'sanctions': sanctions
            },
        )



    branch_choices = Branch.objects.all()
    discipline_choices = Discipline.objects.all()
    auth_officer = is_kingdom_authorization_officer(request.user)
    regional_marshal = is_regional_marshal(request.user)

    return render(
        request,
        'authorizations/fighter.html',
        {
            'person': person,
            'authorization_list': grouped_authorizations,
            'pending_authorization_list': pending_authorizations,
            'equestrian': equestrian,
            'youth': youth,
            'fighter': fighter,
            'is_marshal': is_senior_marshal(request.user),
            'auth_form': CreateAuthorizationForm(user=request.user),
            'auth_officer': auth_officer,
            'branch_officer': branch_officer,
            'branch_choices': branch_choices,
            'discipline_choices': discipline_choices,
            'sanctions': sanctions,
            'regional_marshal': regional_marshal
        },
    )


def generate_fighter_card(request, person_id, template_id):

    # Get core information
    authorization_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(
        person_id=person_id,
        expiration__gte=date.today(),
    ).exclude(
        status__name='Revoked'
    ).order_by(
        'style__discipline__name',
        'expiration', 'style__name')

    person = Person.objects.get(user_id=person_id)

    # Determine the type of card to generate
    if template_id == '1':
        template_path = 'authorizations/static/pdf_forms/fighter_auth.pdf'
        authorization_list = authorization_list.exclude(
            style__discipline__name__in=['Equestrian', 'Youth Armored', 'Youth Rapier']
        )
        if authorization_list.count() == 0:
            raise Exception('No fighter authorizations found')
    elif template_id == '2':
        template_path = 'authorizations/static/pdf_forms/youth_auth.pdf'
        authorization_list = authorization_list.filter(
            style__discipline__name__in=['Youth Armored', 'Youth Rapier']
        )
        if authorization_list.count() == 0:
            raise Exception('No youth authorizations found')
    elif template_id == '3':
        template_path = 'authorizations/static/pdf_forms/equestrian_auth.pdf'
        authorization_list = authorization_list.filter(
            style__discipline__name='Equestrian'
        )
        if authorization_list.count() == 0:
            raise Exception('No equestrian authorizations found')
    else:
        raise Exception('Invalid template id')

    # Get the data for the card
    expiration = authorization_list.earliest('expiration').expiration

    weapon_styles = WeaponStyle.objects.select_related('discipline').all()
    status_list = []
    for style in weapon_styles:
        is_authorized = authorization_list.filter(style=style).exists()
        status_list.append({
            'style': f'{style.discipline.name} - {style.name}',
            'is_authorized': 'X' if is_authorized else ''
        })

    marshal_list = []
    seen_disciplines = set()
    for auth in authorization_list:
        if auth.style.discipline.name not in seen_disciplines:
            seen_disciplines.add(auth.style.discipline.name)
            marshal_list.append({
                'discipline': f'{auth.style.discipline.name} marshal',  # Use the discipline's name for display
                'marshal': auth.marshal.sca_name  # Use the marshal's SCA name for display
            })

    data = {
        'sca_name': person.sca_name,
        'modern_name': person.user.first_name + ' ' + person.user.last_name,
        'expiration': expiration.strftime('%m/%d/%Y'),
        'minor': 'X' if person.minor_status == 'Yes' else ''
    }
    for status in status_list:
        data[status['style']] = status['is_authorized']

    if template_id == '2':
        data['Youth Marshal'] = marshal_list[0]['marshal']
        birthday = person.user.birthday
        if birthday:
            user_age = calculate_age(birthday)
            if 6 <= user_age <= 9:
                data['Lion'] = 'X'
            elif 10 <= user_age <= 13:
                data['Gryphon'] = 'X'
            elif 14 <= user_age <= 17:
                data['Dragon'] = 'X'
            else:
                # Add Background Check exp
                data['Background_expiration'] = expiration.strftime('%m/%d/%Y')
        else:
            # Add Background Check exp
            data['Background_expiration'] = expiration.strftime('%m/%d/%Y')

    else:
        for marshal in marshal_list:
            data[marshal['discipline']] = marshal['marshal']


    # Build the card
    template = PdfReader(template_path)
    for page in template.pages:
        annotations = page.Annots
        if annotations:
            for annotation in annotations:
                if annotation.Subtype == '/Widget' and annotation.T:
                    field_name = annotation.T[1:-1].strip()
                    if field_name in data:
                        annotation.V = data[field_name]
                        annotation.AP = None
                    flags = annotation[PdfName.Ff] if PdfName.Ff in annotation else 0
                    annotation[PdfName.Ff] = flags | 1

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="fighter_card.pdf"'
    PdfWriter(response, trailer=template).write()
    return response


def get_weapon_styles(request, discipline_id):
    styles = WeaponStyle.objects.filter(discipline_id=discipline_id).values('id', 'name')
    return JsonResponse({'styles': list(styles)})


@login_required
def add_fighter(request):
    """This will create a new fighter and add them to the database.
    Only available to senior marshals."""
    if not is_senior_marshal(request.user):
        messages.error(request, "You don't have the required permission to add a new fighter.")
        raise PermissionDenied

    if request.method == 'POST':
        person_form = CreatePersonForm(request.POST)
        auth_form = CreateAuthorizationForm(request.POST, user=request.user)

        if person_form.is_valid() and auth_form.is_valid():

            sent_styles = request.POST.getlist('weapon_styles')
            selected_styles = sorted(set(sent_styles))

            # Generate a random password
            random_password = generate_random_password()

            try:
                with transaction.atomic():

                    # Create User
                    user = User.objects.create_user(
                        email=person_form.cleaned_data['email'],
                        username=person_form.cleaned_data['username'],
                        password=random_password,
                        first_name=person_form.cleaned_data['first_name'],
                        last_name=person_form.cleaned_data['last_name'],
                        membership=person_form.cleaned_data.get('membership', None),
                        membership_expiration=person_form.cleaned_data.get('membership_expiration', None),
                        address=person_form.cleaned_data['address'],
                        address2=person_form.cleaned_data.get('address2', None),
                        city=person_form.cleaned_data['city'],
                        state_province=person_form.cleaned_data['state_province'],
                        postal_code=person_form.cleaned_data['postal_code'],
                        country=person_form.cleaned_data['country'],
                        birthday=person_form.cleaned_data.get('birthday', None),
                    )

                    # Create Person
                    person = Person.objects.create(
                        user=user,
                        sca_name=person_form.cleaned_data['sca_name'],
                        branch=person_form.cleaned_data['branch'],
                        is_minor=person_form.cleaned_data['is_minor'],
                        parent=person_form.cleaned_data.get('parent', None),
                    )

                    # Create Authorizations
                    for style_id in selected_styles:
                        is_valid, mssg = authorization_follows_rules(marshal=request.user, existing_fighter=person,
                                                                     style_id=style_id)
                        if not is_valid:
                            raise ValueError(mssg)
                        style = WeaponStyle.objects.get(id=style_id)
                        if style.name in ['Senior Marshal', 'Junior Marshal']:
                            Authorization.objects.create(
                                person=person,
                                style=style,
                                expiration=date.today() + relativedelta(years=4),
                                marshal=Person.objects.get(user=request.user),
                                status=AuthorizationStatus.objects.get(name='Pending'),
                            )
                            messages.success(request, f'Authorization for {style.name} pending confirmation.')
                        else:
                            Authorization.objects.create(
                                person=person,
                                style=style,
                                expiration=date.today() + relativedelta(years=4),
                                marshal=Person.objects.get(user=request.user),
                                status=AuthorizationStatus.objects.get(name='Active'),
                            )
                            messages.success(request, f'Authorization for {style.name} created successfully!')
            except Exception as e:
                messages.error(request, f'Error during creation: {e}')
                return render(request, 'authorizations/new_authorization.html', {
                    'person_form': person_form,
                    'auth_form': auth_form,
                })
            login_path = reverse('login')
            login_url = f"{settings.SITE_URL}{login_path}"
            send_mail(
                'An Tir Authorization: New Account',
                f'Your account has been created. Your credentials are:\nURL: {login_url}\nUsername: {person_form.cleaned_data["username"]}\nPassword: {random_password}\n'
                f'Please reset your password after logging in.',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            messages.success(request,
                             'User and person created successfully! Login credentials have been sent to the user.')

            person_form = CreatePersonForm()
            auth_form = CreateAuthorizationForm()
            return render(request, 'authorizations/new_authorization.html',
                          {'person_form': person_form, 'auth_form': auth_form})

        else:
            messages.error(request, 'Please fix the errors below.')
            return render(request, 'authorizations/new_authorization.html', {
                'person_form': person_form,
                'auth_form': auth_form,
            })
    else:
        person_form = CreatePersonForm()
        auth_form = CreateAuthorizationForm(user=request.user)

    return render(request, 'authorizations/new_authorization.html', {'person_form': person_form, 'auth_form': auth_form})


@login_required
def add_authorization(request, person_id):
    """This will add a new authorization to a fighter or update an existing authorization."""
    if not is_senior_marshal(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        person = Person.objects.get(user_id=person_id)
        auth_form = CreateAuthorizationForm(request.POST, user=request.user)

        if auth_form.is_valid():
            sent_styles = request.POST.getlist('weapon_styles')
            selected_styles = sorted(set(sent_styles))

            try:
                with transaction.atomic():

                    # Create or update authorizations
                    existing_authorizations = Authorization.objects.filter(person=person)
                    current_styles = [int(auth.style_id) for auth in existing_authorizations]
                    for style_id in selected_styles:
                        is_valid, mssg = authorization_follows_rules(marshal=request.user, existing_fighter=person,
                                                                     style_id=style_id)
                        if not is_valid:

                            raise ValueError(mssg)

                        if int(style_id) in current_styles:
                            update_auth = Authorization.objects.get(person=person, style_id=style_id)
                            update_auth.expiration = date.today() + relativedelta(years=4)
                            update_auth.marshal = Person.objects.get(user=request.user)
                            update_auth.status = AuthorizationStatus.objects.get(name='Active')
                            update_auth.save()
                            selected_styles.remove(style_id)
                            messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')

                        else:
                            style = WeaponStyle.objects.get(id=style_id)
                            if style.name in ['Senior Marshal', 'Junior Marshal']:
                                Authorization.objects.create(
                                    person=person,
                                    style=style,
                                    expiration=date.today() + relativedelta(years=4),
                                    marshal=Person.objects.get(user=request.user),
                                    status=AuthorizationStatus.objects.get(name='Pending'),
                                )
                                messages.success(request,f'Authorization for {style.name} pending confirmation.')
                            else:
                                Authorization.objects.create(
                                    person=person,
                                    style=style,
                                    expiration=date.today() + relativedelta(years=4),
                                    marshal=Person.objects.get(user=request.user),
                                    status=AuthorizationStatus.objects.get(name='Active'),
                                )
                                messages.success(request,f'Authorization for {style.name} created successfully!')

                return True

            except Exception as e:
                messages.error(request, f'Error during creation: {e}')
                return False
        else:
            messages.error(request, 'Please fix the errors below.')
            return False
    else:
        messages.error(request, f'Incorrect method passed.')
        return False


@login_required
def user_account(request, user_id):
    """Allows the user, their parent, or the Kingdom Authorization officer to view and edit the user's account."""
    requestor = request.user
    user = User.objects.get(id=user_id)
    person = user.person
    if requestor != user and (not hasattr(user, 'person') or user.person.parent_id != requestor.id):
        if not is_kingdom_authorization_officer(requestor):
            raise PermissionDenied

    children = user.person.children.all()

    try:
        branch_officer = BranchMarshal.objects.get(person__user=user, end_date__gte=date.today())
    except BranchMarshal.DoesNotExist:
        branch_officer = None

    # Pre-fill the form with the user's and person's information
    initial_data = {
        # User fields
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username,
        'membership': user.membership,
        'membership_expiration': user.membership_expiration,
        'address': user.address,
        'address2': user.address2,
        'city': user.city,
        'state_province': user.state_province,
        'postal_code': user.postal_code,
        'country': user.country,
        'birthday': user.birthday,

        # Person fields
        'sca_name': person.sca_name,
        'branch': person.branch,
        'is_minor': person.is_minor,
        'parent_id': person.parent,
    }

    if request.method == 'POST':
        requestor = request.user
        user = User.objects.get(id=user_id)
        person = user.person
        if requestor != user and (not hasattr(user, 'person') or user.person.parent_id != requestor.id):
            if not is_kingdom_authorization_officer(requestor):
                raise PermissionDenied
        form = CreatePersonForm(request.POST, user_instance=user)
        if form.is_valid():
            # Update User fields
            user.email = form.cleaned_data['email']
            user.username = form.cleaned_data['username']
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.membership = form.cleaned_data.get('membership')
            user.membership_expiration = form.cleaned_data.get('membership_expiration')
            user.address = form.cleaned_data['address']
            user.address2 = form.cleaned_data.get('address2')
            user.city = form.cleaned_data['city']
            user.state_province = form.cleaned_data['state_province']
            user.postal_code = form.cleaned_data['postal_code']
            user.country = form.cleaned_data['country']
            user.birthday = form.cleaned_data.get('birthday')
            user.save()

            # Update Person fields
            person.sca_name = form.cleaned_data['sca_name']
            person.branch = form.cleaned_data['branch']
            person.is_minor = form.cleaned_data['is_minor']
            person.parent = form.cleaned_data.get('parent_id')
            person.save()

            messages.success(request, 'Your information has been updated successfully.')
            return redirect('index')
        else:
            messages.error(request, 'Please correct the errors with the form.')
    else:
        form = CreatePersonForm(initial=initial_data, user_instance=user)

    return render(request, 'authorizations/user_account.html', {
        'user': user,
        'person': person,
        'form': form,
        'children': children,
        'branch_officer': branch_officer,
    })


def reject_authorization(request, authorization):
    auth_discipline = authorization.style.discipline.name
    if is_regional_marshal(request.user, auth_discipline):
        authorization.delete()
        return True, 'Authorization rejected.'
    else:
        return False, 'You do not have authority to reject this authorization.'

@login_required
def manage_sanctions(request):
    """Shows all of the current revoked authorizations."""
    # Ensure that the user is the authorization officer

    # Allow the user to filter the current branch marshals table
    dynamic_filter = Q(status__name='Revoked')
    sca_name = request.GET.get('sca_name')
    if sca_name:
        dynamic_filter &= Q(person__sca_name=sca_name)
    discipline = request.GET.get('discipline')
    if discipline:
        dynamic_filter &= Q(style__discipline__name=discipline)
    style = request.GET.get('style')
    if style:
        dynamic_filter &= Q(style__name=style)

    # Get a list of current branch marshals
    sanctions = Authorization.objects.select_related(
        'style__discipline',
    ).filter(dynamic_filter).order_by('person__sca_name')

    # Create table drop down options based on dynamic filters
    sca_name_options = sanctions.order_by('person__sca_name').values_list('person__sca_name', flat=True).distinct()
    discipline_options = sanctions.order_by('style__discipline__name').values_list('style__discipline__name',
                                                                                            flat=True).distinct()
    style_options = sanctions.order_by('style__name').values_list('style__name', flat=True).distinct()

    # Pagination
    items_per_page = int(request.GET.get('items_per_page', 10))
    current_page = request.GET.get('page', 1)
    paginator = Paginator(sanctions, items_per_page)
    page_obj = paginator.get_page(current_page)


    if request.method == 'POST':
        if not is_kingdom_authorization_officer(request.user) and not is_kingdom_marshal(request.user, 'Earl Marshal'):
            raise PermissionDenied

        authorization_id = request.POST.get('authorization_id')
        authorization = Authorization.objects.get(id=authorization_id)
        authorization.delete()
        messages.success(request, 'Authorization suspension has been lifted.')


    return render(request, 'authorizations/manage_sanctions.html', {
        'page_obj': page_obj,
        'items_per_page': items_per_page,
        'sanctions': sanctions,
        'sca_name_options': sca_name_options,
        'style_options': style_options,
        'discipline_options': discipline_options,
    })

@login_required()
def issue_sanctions(request, person_id):
    """Allows the authorization officer or Earl Marshal to issue a sanction."""
    if not is_kingdom_authorization_officer(request.user) and not is_kingdom_marshal(request.user, 'Earl Marshal'):
        raise PermissionDenied

    person = get_object_or_404(Person, user_id=person_id)

    all_disciplines = Discipline.objects.all().exclude(name__in=['Earl Marshal', 'Authorization Officer'])

    discipline = None
    discipline_name = request.GET.get('discipline')

    if discipline_name:
        discipline = Discipline.objects.filter(name=discipline_name).first()

    if request.method == 'POST':
        is_valid, mssg = create_sanction(request, person)
        if not is_valid:
            messages.error(request, mssg)
        else:
            messages.success(request, mssg)


    return render(request, 'authorizations/issue_sanctions.html', {
        'person': person,
        'all_disciplines': all_disciplines,
        'discipline': discipline
    })


def create_sanction(request, person):
    """Creates sanctions for a person."""
    sanction_type = request.POST.get('sanction_type')
    discipline_id = request.POST.get('discipline_id')
    style_id = request.POST.get('style_id')

    if sanction_type == 'discipline':
        if not discipline_id:
            return False, 'No discipline provided'

        # Create sanctions for all styles in the discipline
        discipline = Discipline.objects.get(id=discipline_id)
        styles = WeaponStyle.objects.filter(discipline_id=discipline_id)
        for style in styles:
            # Check if the authorization already exists.
            if Authorization.objects.filter(person=person, style=style).exists():
                # If it does, change the expiration date to the current date and the status to Revoked.
                authorization = Authorization.objects.get(person=person, style=style)
                authorization.expiration = date.today()
                authorization.status = AuthorizationStatus.objects.get(name='Revoked')
                authorization.save()
            else:
                # If it doesn't, create a new authorization.
                Authorization.objects.create(person=person, style=style, expiration=date.today(),
                                             status=AuthorizationStatus.objects.get(name='Revoked'))

        return True, f'Sanction issued for discipline {discipline.name}'

    elif sanction_type == 'style':
        if not style_id:
            return False, 'No style provided'

        # Create sanction for one discipline
        style = WeaponStyle.objects.get(id=style_id)
        # Check if the authorization already exists.
        if Authorization.objects.filter(person=person, style=style).exists():
            # If it does, change the expiration date to the current date and the status to Revoked.
            authorization = Authorization.objects.get(person=person, style=style)
            authorization.expiration = date.today()
            authorization.status = AuthorizationStatus.objects.get(name='Revoked')
            authorization.save()
        else:
            # If it doesn't, create a new authorization.
            Authorization.objects.create(person=person, style=style, expiration=date.today(),
                                         status=AuthorizationStatus.objects.get(name='Revoked'))
        return True, f'Sanction issued for style {style.name}'

    else:
        return False, 'Invalid sanction type'


def branch_marshals(request):
    """Shows all of the branch marshals."""
    # Ensure that the user is the authorization officer

    # Allow the user to filter the current branch marshals table
    dynamic_filter = Q(end_date__gte=date.today())
    sca_name = request.GET.get('sca_name')
    if sca_name:
        dynamic_filter &= Q(person__sca_name=sca_name)
    branch = request.GET.get('branch')
    if branch:
        dynamic_filter &= Q(branch__name=branch)
    discipline = request.GET.get('discipline')
    if discipline:
        dynamic_filter &= Q(discipline__name=discipline)

    # Get a list of current branch marshals
    current_branch_marshals = BranchMarshal.objects.filter(dynamic_filter).order_by('end_date')

    # Create table drop down options based on dynamic filters
    sca_name_options = current_branch_marshals.order_by('person__sca_name').values_list('person__sca_name',
                                                                                   flat=True).distinct()
    branch_options = current_branch_marshals.order_by('branch__name').values_list('branch__name',
                                                                                     flat=True).distinct()
    discipline_options = current_branch_marshals.order_by('discipline__name').values_list('discipline__name',
                                                                                            flat=True).distinct()

    # Pagination
    items_per_page = int(request.GET.get('items_per_page', 10))
    current_page = request.GET.get('page', 1)
    paginator = Paginator(current_branch_marshals, items_per_page)
    page_obj = paginator.get_page(current_page)

    try:
        auth_officer = is_kingdom_authorization_officer(request.user)
    except:
        auth_officer = False

    if request.method == 'POST':
        if not request.user.is_authenticated:
            raise PermissionDenied

        if not auth_officer:
            raise PermissionDenied

        action = request.POST.get('action')
        branch_officer_id = request.POST.get('branch_officer_id')
        branch_officer = BranchMarshal.objects.get(id=branch_officer_id)
        if action == 'extend_appointment':
            branch_officer.end_date = branch_officer.end_date + relativedelta(years=1)
            messages.success(request, f'{branch_officer.discipline.name} {branch_officer.branch.name} marshal appointment for {branch_officer.person.sca_name} has been extended one year.')
        elif action == 'end_appointment':
            branch_officer.end_date = date.today() - timedelta(days=1)
            messages.success(request, f'{branch_officer.discipline.name} {branch_officer.branch.name} marshal appointment for {branch_officer.person.sca_name} has been ended.')
        branch_officer.save()

    return render(request, 'authorizations/branch_marshals.html', {
        'page_obj': page_obj,
        'items_per_page': items_per_page,
        'current_branch_marshals': current_branch_marshals,
        'sca_name_options': sca_name_options,
        'branch_options': branch_options,
        'discipline_options': discipline_options,
        'auth_officer': auth_officer
    })



class CreatePersonForm(forms.Form):
    """Creates the initial person and user."""
    # User fields
    email = forms.EmailField(label='Email', required=True)
    username = forms.CharField(label='Username', required=True)
    first_name = forms.CharField(label='First Name', required=True)
    last_name = forms.CharField(label='Last Name', required=True)
    membership = forms.IntegerField(label='Membership Number', required=False)
    membership_expiration = forms.DateField(label='Membership Expiration', required=False,
                                            widget=forms.DateInput(attrs={'type': 'date'}))
    address = forms.CharField(label='Address', required=True)
    address2 = forms.CharField(label='Address Line 2', required=False)
    city = forms.CharField(label='City', required=True)
    state_province = forms.ChoiceField(label='State/Province', choices=state_province_choices, required=True)
    postal_code = forms.CharField(label='Postal Code', required=True)
    country = forms.ChoiceField(label='Country', choices=[('Canada', 'Canada'), ('United States', 'United States')], required=True)
    birthday = forms.DateField(label='Birthday', required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    # Person fields
    discipline_names = Discipline.objects.values_list('name', flat=True)
    sca_name = forms.CharField(label='SCA Name', required=False)
    branch = forms.ModelChoiceField(label='Branch', queryset = Branch.objects.filter(name__in=all_branch_names), required=True)
    is_minor = forms.BooleanField(label='Is Minor', required=False)
    parent_id = forms.ModelChoiceField(label='Parent ID', queryset=Person.objects.all().exclude(sca_name='admin'),required=False)

    def __init__(self, *args, **kwargs):
        """Allow passing a user instance when updating."""
        self.user_instance = kwargs.pop('user_instance', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        user_id = self.user_instance.id if self.user_instance else None

        if not cleaned_data.get('is_minor') and cleaned_data.get('parent_id'):
            raise forms.ValidationError('A non-minor must not have a parent ID.')

        username = cleaned_data.get('username')
        if username and User.objects.filter(username=username).exclude(id=user_id).exists():
            raise forms.ValidationError('A user with this username already exists.')

        membership = cleaned_data.get('membership')
        if membership and User.objects.filter(membership=membership).exclude(id=user_id).exists():
            raise forms.ValidationError('A user with this membership number already exists.')

        if bool(cleaned_data.get('membership')) != bool(cleaned_data.get('membership_expiration')):
            raise forms.ValidationError('Must have both a membership number and expiration or neither.')

        if cleaned_data.get('is_minor') and not cleaned_data.get('birthday'):
            raise forms.ValidationError('A birthday must be provided for minors.')

        return cleaned_data


class CreateAuthorizationForm(forms.Form):
    """Get the authorizations that the user would like to create."""

    discipline = forms.ModelChoiceField(
        queryset=Discipline.objects.all().exclude(name__in=['Earl Marshal', 'Authorization Officer']),
        required=False,
        empty_label='Select Discipline',
        widget=forms.Select(attrs={'id': 'discipline-select'})
    )
    weapon_styles = forms.ModelMultipleChoiceField(
        queryset=WeaponStyle.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'id': 'weapon-styles-select'})
    )

    def __init__(self, *args, **kwargs):
        # Expecting 'user' to be passed during form initialization
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if user:
            # Filter disciplines based on the user's senior marshal authorizations
            if BranchMarshal.objects.filter(person=user.person, end_date__gte=date.today(), branch__name='An Tir', discipline__name__in=['Authorization Officer', 'Earl Marshal']).exists():
                self.fields['discipline'].queryset = Discipline.objects.all().exclude(name__in=['Authorization Officer', 'Earl Marshal'])
            else:
                senior_authorizations = Authorization.objects.filter(
                    person__user=user,
                    style__name='Senior Marshal',  # Assuming 'Senior Marshal' is the style name
                    expiration__gte=date.today()  # Ensure the authorization is still valid
                ).values_list('style__discipline', flat=True)

                # Update the discipline queryset with the filtered disciplines
                self.fields['discipline'].queryset = Discipline.objects.filter(id__in=senior_authorizations)
