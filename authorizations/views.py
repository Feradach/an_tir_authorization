from dateutil.relativedelta import relativedelta
from django.core.mail import send_mail
from django.conf import settings
import random
import string
from datetime import date
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse
from datetime import date, timedelta
from django.db.models import Q, Prefetch
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib.staticfiles import finders
from .models import User, Authorization, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, BranchMarshal, Title, TITLE_RANK_CHOICES
from .permissions import is_senior_marshal, is_branch_marshal, is_regional_marshal, is_kingdom_marshal, is_kingdom_authorization_officer, authorization_follows_rules, calculate_age, approve_authorization, appoint_branch_marshal, waiver_signed
from itertools import groupby
from operator import attrgetter
from pdfrw import PdfReader, PdfWriter, PdfName
from django.contrib import messages
from django import forms
import re

# Removed all_branch_names since we can now use Branch.is_region() to filter branches
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
            discipline = marshal.discipline
            pending_authorizations = Authorization.objects.filter(person__branch__region=marshal.branch, style__discipline=discipline, status__name='Needs Regional Approval').order_by('expiration')
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
        'all_people': all_people,
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
        action = request.POST.get('action')
        login_path = reverse('login')
        login_url = f"{settings.SITE_URL}{login_path}"
        
        if action == 'reset_password':
            username = request.POST.get('username', '').strip()
            if not username:
                messages.error(request, 'Please enter a username.')
                return render(request, 'authorizations/recover_account.html')
                
            try:
                user = User.objects.get(username=username)
                new_password = generate_random_password()
                user.set_password(new_password)
                user.has_logged_in = False
                user.save()
                
                # Send the password via email
                send_mail(
                    'An Tir Authorization: Password Reset',
                    f'Your password has been reset.\n\n'
                    f'Username: {user.username}\n'
                    f'Temporary Password: {new_password}\n\n'
                    f'Please log in and change your password.\n'
                    f'Login URL: {login_url}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                messages.success(request, 'A temporary password has been sent to the email on file for this username.')
                return redirect('login')
                
            except User.DoesNotExist:
                messages.error(request, 'No account with that username was found.')
                return render(request, 'authorizations/recover_account.html')
                
        elif action == 'get_username':
            email = request.POST.get('email', '').strip()
            if not email:
                messages.error(request, 'Please enter an email address.')
                return render(request, 'authorizations/recover_account.html')
                
            users = User.objects.filter(email=email)
            if not users.exists():
                messages.error(request, 'No accounts were found with that email address.')
                return render(request, 'authorizations/recover_account.html')
                
            usernames = [user.username for user in users]
            username_list = '\n'.join([f'- {username}' for username in usernames])
            
            send_mail(
                'An Tir Authorization: Username Recovery',
                f'We found the following usernames associated with this email address:\n\n'
                f'{username_list}\n\n'
                f'You can use any of these usernames to log in. If you need to reset your password, please use the "Forgot Password" option.\n\n'
                f'Login URL: {login_url}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            messages.success(request, f'A list of usernames has been sent to {email}.')
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
    """
    Handles both the search form display and the search results display.
    """

    # === Step 1: Get dropdown options (we need these for the search form too) ===
    sca_name_options = Person.objects.order_by('sca_name').values_list('sca_name', flat=True).distinct()
    region_options = Branch.objects.regions().order_by('name').values_list('name', flat=True)
    branch_options = Branch.objects.non_regions().order_by('name').values_list('name', flat=True)
    discipline_options = Discipline.objects.order_by('name').values_list('name', flat=True)
    style_options = WeaponStyle.objects.order_by('name').values_list('name', flat=True).distinct()
    marshal_options = Person.objects.filter(marshal__isnull=False).order_by('sca_name').values_list('sca_name', flat=True).distinct()

    # === Step 2: Check if the user is requesting the search form page ===
    if request.GET.get('goal') == 'search':
        # The user wants to see the search form.
        context = {
            'sca_name_options': sca_name_options,
            'region_options': region_options,
            'branch_options': branch_options,
            'discipline_options': discipline_options,
            'style_options': style_options,
            'marshal_options': marshal_options,
        }
        return render(request, 'authorizations/search_form.html', context)

    # === Step 3: If not goal=search, proceed with showing results (this is our existing logic) ===

    try:
        active_status_id = AuthorizationStatus.objects.get(name='Active').id
        dynamic_filter = Q(status_id=active_status_id)
    except AuthorizationStatus.DoesNotExist:
        dynamic_filter = Q(pk__in=[])

    # ... (all the `if sca_name:`, `if region:`, etc. blocks remain the same) ...
    sca_name = request.GET.get('sca_name')
    if sca_name: dynamic_filter &= Q(person__sca_name=sca_name)
    region = request.GET.get('region')
    if region: dynamic_filter &= Q(person__branch__region__name=region)
    branch = request.GET.get('branch')
    if branch: dynamic_filter &= Q(person__branch__name=branch)
    discipline = request.GET.get('discipline')
    if discipline: dynamic_filter &= Q(style__discipline__name=discipline)
    style = request.GET.get('style')
    if style: dynamic_filter &= Q(style__name=style)
    marshal = request.GET.get('marshal')
    if marshal: dynamic_filter &= Q(marshal__sca_name=marshal)
    start_date = request.GET.get('start_date')
    if start_date: dynamic_filter &= Q(expiration__gte=start_date)
    end_date = request.GET.get('end_date')
    if end_date: dynamic_filter &= Q(expiration__lte=end_date)
    is_minor = request.GET.get('is_minor')
    if is_minor: dynamic_filter &= Q(person__is_minor=(is_minor == 'True'))
    if membership_num := request.GET.get('membership'):
        # The path is Authorization -> person -> user -> membership
        dynamic_filter &= Q(person__user__membership=membership_num)
    if email_addr := request.GET.get('email'):
        # Using 'iexact' makes the email search case-insensitive
        dynamic_filter &= Q(person__user__email__iexact=email_addr)
    
    
    # === STEP 4: LOGIC FOR DIFFERENT VIEW MODES ===
    
    view_mode = request.GET.get('view', 'table') # Default to 'table' view
    page_obj = None # Initialize page_obj

    # First, get all authorizations that match the filter.
    # We use this as a base for both views.
    matching_authorizations = Authorization.objects.filter(dynamic_filter).exclude(person__user_id=11968)

    if view_mode == 'card':
        # --- CARD VIEW LOGIC (Corrected) ---

        # 1. Get the unique IDs of people who have matching authorizations. (No change)
        person_ids = matching_authorizations.values_list('person_id', flat=True).distinct()

        # 2. Create the Prefetch object. This defines the data we want to "attach"
        #    to each person. (No change in the Prefetch object itself)
        authorizations_prefetch = Prefetch(
            'authorization_set',  # Default related_name from Person to Authorization
            queryset=matching_authorizations.select_related('style__discipline', 'marshal').order_by('style__discipline__name', 'style__name'),
            to_attr='filtered_authorizations'  # Store results in this new attribute
        )

        # 3. Build the main people queryset.
        #    THE KEY CHANGE IS HERE: We chain .prefetch_related() to the QuerySet *before* pagination.
        people_list = Person.objects.filter(user_id__in=person_ids).select_related(
            'branch__region'
        ).prefetch_related(authorizations_prefetch).order_by('sca_name')

        # 4. NOW, we paginate the fully prepared queryset. The paginator will handle
        #    it efficiently.
        items_per_page = int(request.GET.get('items_per_page', 10))
        paginator = Paginator(people_list, items_per_page)
        page_obj = paginator.get_page(request.GET.get('page', 1))
    
    else: # 'table' view is the default
        # --- TABLE VIEW LOGIC ---
        # This is the same logic as before, but simplified.
        user_sort = request.GET.get('sort', 'person__sca_name')
        
        authorization_list = matching_authorizations.select_related(
            'person__branch__region',
            'person__title',
            'style__discipline',
            'status',
            'marshal'
        ).order_by(user_sort)

        items_per_page = int(request.GET.get('items_per_page', 25))
        paginator = Paginator(authorization_list, items_per_page)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    # === STEP 4: RENDER THE TEMPLATE ===
    return render(
        request,
        'authorizations/search.html',
        {
            'page_obj': page_obj,
            'items_per_page': items_per_page,
            'view_mode': view_mode,
            'today': date.today(),
            
            # Add these back in for the table header filters
            'sca_name_options': sca_name_options,
            'region_options': region_options,
            'branch_options': branch_options,
            'discipline_options': discipline_options,
            'style_options': style_options,
            'marshal_options': marshal_options,
        },
    )


def fighter(request, person_id):
    """Pass in a single person id. Return all of their current authorizations in a card view.
    Give a link to download or print a pdf or image of their card.
    This should ideally look like the official card.
    Give a link to add a new authorization if the user is a senior marshal.

    Create a link on the authorization search to go to this page."""

    # Get the person who's card is being requested
    person = Person.objects.get(user_id=person_id)
    user = person.user

    # If there is a post, confirm that they are authenticated.
    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to perform this action.')
            return redirect('login')

        action = request.POST.get('action')
        if action == 'add_authorization':
            add_authorization(request, person_id)
        elif action == 'update_comments':
            # Only allow auth officers to update comments
            if not is_kingdom_authorization_officer(request.user):
                messages.error(request, 'You do not have permission to update comments.')
            else:
                user.comment = request.POST.get('comments', '')
                user.save()
                messages.success(request, 'Comments updated successfully.')
                return redirect('fighter', person_id=person_id)
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
                'sanctions': sanctions,
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
            'regional_marshal': regional_marshal,
            'all_people': Person.objects.all().order_by('sca_name'),
        },
    )


def generate_fighter_card(request, person_id, template_id):

    # Get core information
    authorization_list = Authorization.objects.select_related(
        'person__branch',
        'style__discipline',
    ).filter(
        person_id=person_id,
        expiration__gte=date.today(),
        status__name='Active'
    ).order_by(
        'style__discipline__name',
        'expiration', 'style__name')

    person = Person.objects.get(user_id=person_id)

    # Determine the type of card to generate
    if template_id == '1':
        template_path = 'pdf_forms/fighter_auth.pdf'
        authorization_list = authorization_list.exclude(
            style__discipline__name__in=['Equestrian', 'Youth Armored', 'Youth Rapier']
        )
        if authorization_list.count() == 0:
            raise Exception('No fighter authorizations found')
    elif template_id == '2':
        template_path = 'pdf_forms/youth_auth.pdf'
        authorization_list = authorization_list.filter(
            style__discipline__name__in=['Youth Armored', 'Youth Rapier']
        )
        if authorization_list.count() == 0:
            raise Exception('No youth authorizations found')
    elif template_id == '3':
        template_path = 'pdf_forms/equestrian_auth.pdf'
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
    # Find the absolute path to the template using Django's static files finder
    absolute_template_path = finders.find(template_path)
    if not absolute_template_path:
        # Try to find the file in the static directory
        absolute_template_path = finders.find(f'authorizations/static/{template_path}')
    if not absolute_template_path:
        raise Exception(f'Could not find PDF template file: {template_path}. Looked in: {finders.searched_locations}')
    
    try:
        template = PdfReader(absolute_template_path)
    except Exception as e:
        raise Exception(f'Error reading PDF template file {absolute_template_path}: {str(e)}')
    
    print(f'Successfully loaded template from: {absolute_template_path}')
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

        if person_form.is_valid():

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
                        phone_number=person_form.cleaned_data.get('phone_number', None),
                        birthday=person_form.cleaned_data.get('birthday', None),
                    )

                    # Create Person
                    person = Person.objects.create(
                        user=user,
                        sca_name=person_form.cleaned_data['sca_name'],
                        title=person_form.cleaned_data['title'],
                        branch=person_form.cleaned_data['branch'],
                        is_minor=person_form.cleaned_data['is_minor'],
                        parent=person_form.cleaned_data.get('parent', None),
                    )

            except Exception as e:
                messages.error(request, f'Error during creation: {e}')
                return render(request, 'authorizations/new_fighter.html', {
                    'person_form': person_form
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

            return redirect('fighter', person_id=user.id)

        else:
            for field, errors in person_form.errors.items():
                for error in errors:
                    if field == '__all__':
                        messages.error(request, error)
                    else:
                        field_label = person_form.fields[field].label if field in person_form.fields else field
                        messages.error(request, f"{field_label}: {error}")
            return render(request, 'authorizations/new_fighter.html', {
                'person_form': person_form
            })
    else:
        person_form = CreatePersonForm()

    return render(request, 'authorizations/new_fighter.html', {'person_form': person_form})


@login_required
def add_authorization(request, person_id):
    """This will add a new authorization to a fighter or update an existing authorization."""

    marshal_id = request.POST.get('marshal_id')
    if marshal_id:
        authorizing_marshal = User.objects.get(id=marshal_id)
    else:
        authorizing_marshal = request.user
    
    # Get the selected discipline from the form
    discipline_id = request.POST.get('discipline')
    if discipline_id:
        discipline = Discipline.objects.get(id=discipline_id)
        if not is_senior_marshal(authorizing_marshal, discipline.name):
            messages.error(request, f"Error: {authorizing_marshal.person.sca_name} is not a senior marshal in {discipline.name} and cannot authorize authorizations.")
            return redirect('fighter', person_id=person_id)

    if request.method == 'POST':
        person = Person.objects.get(user_id=person_id)
        auth_form = CreateAuthorizationForm(request.POST, user=authorizing_marshal)

        if auth_form.is_valid():
            sent_styles = request.POST.getlist('weapon_styles')
            selected_styles = sorted(set(sent_styles))

            try:
                with transaction.atomic():
                    print(f"Debug: Starting authorization process for person {person_id}")
                    print(f"Debug: Selected styles: {selected_styles}")
                    print(f"Debug: Authorizing marshal: {authorizing_marshal.person.sca_name}")

                    # Create or update authorizations
                    existing_authorizations = Authorization.objects.filter(person=person)
                    current_styles = [int(auth.style_id) for auth in existing_authorizations]
                    print(f"Debug: Current authorizations: {current_styles}")
                    
                    for style_id in selected_styles:
                        print(f"\nDebug: Processing style {style_id}")
                        try:
                            is_valid, mssg = authorization_follows_rules(marshal=authorizing_marshal, existing_fighter=person,
                                                                         style_id=style_id)
                            if not is_valid:
                                messages.error(request, mssg)
                                return redirect('fighter', person_id=person_id)

                            print(f"Debug: Authorization rules passed for style {style_id}")

                            if int(style_id) in current_styles:
                                update_auth = Authorization.objects.get(person=person, style_id=style_id)
                                update_auth.marshal = Person.objects.get(user=authorizing_marshal)
                                
                                # Check if this is a marshal authorization and if it has been expired for more than a year
                                if update_auth.style.name in ['Senior Marshal', 'Junior Marshal']:
                                    days_expired = (date.today() - update_auth.expiration).days
                                    if days_expired > 365:  # More than one year expired
                                        update_auth.status = AuthorizationStatus.objects.get(name='Pending')
                                        messages.success(request, f'Authorization for {update_auth.style.name} pending confirmation.')
                                    else:
                                        update_auth.status = AuthorizationStatus.objects.get(name='Active')
                                        messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')
                                else:
                                    update_auth.status = AuthorizationStatus.objects.get(name='Active')
                                    messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')
                                
                                # Set expiration based on youth marshal rules
                                if update_auth.style.discipline.name in ['Youth Armored', 'Youth Rapier'] and update_auth.style.name in ['Junior Marshal', 'Senior Marshal']:
                                    two_years = date.today() + relativedelta(years=2)
                                    if person.user.background_check_expiration:
                                        update_auth.expiration = min(two_years, person.user.background_check_expiration)
                                    else:
                                        update_auth.expiration = two_years
                                else:
                                    update_auth.expiration = date.today() + relativedelta(years=4)
                                update_auth.save()
                                selected_styles.remove(style_id)

                            else:
                                style = WeaponStyle.objects.get(id=style_id)
                                if style.name in ['Senior Marshal', 'Junior Marshal']:
                                    new_auth = Authorization.objects.create(
                                        person=person,
                                        style=style,
                                        expiration=date.today() + relativedelta(years=4),
                                        marshal=Person.objects.get(user=authorizing_marshal),
                                        status=AuthorizationStatus.objects.get(name='Pending'),
                                    )
                                    messages.success(request, f'Authorization for {style.name} pending confirmation.')
                                else:
                                    # Set expiration based on youth marshal rules
                                    if style.discipline.name in ['Youth Armored', 'Youth Rapier'] and style.name in ['Junior Marshal', 'Senior Marshal']:
                                        two_years = date.today() + relativedelta(years=2)
                                        if person.user.background_check_expiration:
                                            expiration = min(two_years, person.user.background_check_expiration)
                                        else:
                                            expiration = two_years
                                        new_auth = Authorization.objects.create(
                                            person=person,
                                            style=style,
                                            expiration=expiration,
                                            marshal=Person.objects.get(user=authorizing_marshal),
                                            status=AuthorizationStatus.objects.get(name='Active'),
                                        )
                                        messages.success(request, f'Authorization for {style.name} created successfully!')
                                        # Update the waiver expiration date if it is greater than today to the authorization expiration date
                                        if person.user.waiver_expiration and person.user.waiver_expiration < expiration:
                                            person.user.waiver_expiration = expiration
                                            person.user.save()
                                    else:
                                        new_auth = Authorization.objects.create(
                                            person=person,
                                            style=style,
                                            expiration=date.today() + relativedelta(years=4),
                                            marshal=Person.objects.get(user=authorizing_marshal),
                                            status=AuthorizationStatus.objects.get(name='Active'),
                                        )
                                        messages.success(request, f'Authorization for {style.name} created successfully!')
                                        # Update the waiver expiration date if it is greater than today to the authorization expiration date
                                        if person.user.waiver_expiration and person.user.waiver_expiration < new_auth.expiration:
                                            person.user.waiver_expiration = new_auth.expiration
                                            person.user.save()

                        except Exception as e:
                            print(f"Error processing style {style_id}: {e}")
                            messages.error(request, f'Error processing style {style_id}: {str(e)}')
                            transaction.set_rollback(True)
                            return redirect('fighter', person_id=person_id)

                return redirect('fighter', person_id=person_id)

            except Exception as e:
                print(f"Transaction error: {e}")
                messages.error(request, f'Error during authorization process: {str(e)}')
                return redirect('fighter', person_id=person_id)
        else:
            messages.error(request, 'Please fix the errors below.')
            return redirect('fighter', person_id=person_id)
    else:
        messages.error(request, f'Incorrect method passed.')
        return redirect('fighter', person_id=person_id)

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
        'phone_number': user.phone_number,
        'birthday': user.birthday,
        'background_check_expiration': user.background_check_expiration,

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
        form = CreatePersonForm(request.POST, user_instance=user, request=request)
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
            user.phone_number = form.cleaned_data.get('phone_number')
            user.birthday = form.cleaned_data.get('birthday')
            
            # Only allow authorization officers to modify background_check_expiration
            if is_kingdom_authorization_officer(request.user):
                user.background_check_expiration = form.cleaned_data.get('background_check_expiration')
            
            user.save()

            # Update Person fields
            person.sca_name = form.cleaned_data['sca_name']
            person.title = form.cleaned_data['title']
            person.branch = form.cleaned_data['branch']
            person.is_minor = form.cleaned_data['is_minor']
            person.parent = form.cleaned_data.get('parent_id')
            person.save()

            messages.success(request, 'Your information has been updated successfully.')
            return redirect('index')
        else:
            messages.error(request, 'Please correct the errors with the form.')
    else:
        form = CreatePersonForm(initial=initial_data, user_instance=user, request=request)

    # Calculate the maximum expiration date
    waiver_signed = False
    max_expiration = None
    if user.waiver_expiration and user.waiver_expiration > date.today():
        waiver_signed = True
    elif user.membership_expiration and user.membership_expiration > date.today():
        waiver_signed = True
    if waiver_signed:
        if user.waiver_expiration and user.membership_expiration:
            max_expiration = max(user.waiver_expiration, user.membership_expiration)
        elif user.waiver_expiration:
            max_expiration = user.waiver_expiration
        elif user.membership_expiration:
            max_expiration = user.membership_expiration

    context = {
        'person': person,
        'user': user,
        'form': form,
        'children': children,
        'branch_officer': branch_officer,
        'waiver_signed': waiver_signed,
        'max_expiration': max_expiration,
        'is_authorization_officer': is_kingdom_authorization_officer(request.user),
    }

    return render(request, 'authorizations/user_account.html', context)

@login_required
def sign_waiver(request, user_id):
    if request.method == 'POST':
        user = User.objects.get(id=user_id)
        user.waiver_expiration = date.today() + relativedelta(years=1)
        user.save()
        return redirect('user_account', user_id=user.id)
    else:
        return render(request, 'authorizations/waiver.html')

def reject_authorization(request, authorization):
    auth_discipline = authorization.style.discipline.name
    if is_regional_marshal(request.user, auth_discipline):
        authorization.delete()
        return True, 'Authorization rejected.'
    else:
        return False, 'You do not have authority to reject this authorization.'

@login_required
def manage_sanctions(request):
    """
    Handles displaying the sanctions search form, and showing the results
    in either a table or a card view grouped by person.
    """
    # Handle POST requests first to lift sanctions.
    if request.method == 'POST':
        if not is_kingdom_authorization_officer(request.user) and not is_kingdom_marshal(request.user, 'Earl Marshal'):
            raise PermissionDenied
        
        # We'll use the 'action' to make sure we're lifting a sanction.
        if request.POST.get('action') == 'lift_sanction':
            authorization_id = request.POST.get('authorization_id')
            try:
                authorization = Authorization.objects.get(id=authorization_id, status__name='Revoked')
                # Instead of deleting, it's often better to change the status.
                # If you truly want to delete, you can keep authorization.delete().
                # For this example, let's assume lifting a sanction means making it 'Active' again.
                active_status = AuthorizationStatus.objects.get(name='Active')
                authorization.status = active_status
                authorization.save()
                messages.success(request, f"Sanction for {authorization.person.sca_name} has been lifted.")
            except Authorization.DoesNotExist:
                messages.error(request, "Could not find the specified sanction to lift.")
        
        # Redirect after POST to prevent re-submission on refresh
        return redirect('manage_sanctions')

    # --- Display Logic (for GET requests) ---

    # Get dropdown options for the search form
    revoked_auths = Authorization.objects.filter(status__name='Revoked')
    sca_name_options = Person.objects.filter(authorization__in=revoked_auths).distinct().order_by('sca_name').values_list('sca_name', flat=True)
    discipline_options = Discipline.objects.filter(weaponstyle__authorization__in=revoked_auths).distinct().order_by('name').values_list('name', flat=True)
    style_options = WeaponStyle.objects.filter(authorization__in=revoked_auths).distinct().order_by('name').values_list('name', flat=True)

    # Check if the user is requesting the search form page
    if request.GET.get('goal') == 'search':
        context = {
            'sca_name_options': sca_name_options,
            'discipline_options': discipline_options,
            'style_options': style_options,
        }
        return render(request, 'authorizations/sanctions_search_form.html', context)

    # --- If not the search goal, proceed with showing results ---
    
    # Build the dynamic filter
    try:
        revoked_status = AuthorizationStatus.objects.get(name='Revoked')
        dynamic_filter = Q(status=revoked_status)
    except AuthorizationStatus.DoesNotExist:
        return render(request, 'authorizations/error.html', {'message': 'System error: "Revoked" status not found.'})

    if sca_name := request.GET.get('sca_name'):
        dynamic_filter &= Q(person__sca_name=sca_name)
    if discipline := request.GET.get('discipline'):
        dynamic_filter &= Q(style__discipline__name=discipline)
    if style := request.GET.get('style'):
        dynamic_filter &= Q(style__name=style)

    matching_authorizations = Authorization.objects.filter(dynamic_filter).exclude(person__user_id=11968)
    view_mode = request.GET.get('view', 'table')
    page_obj = None

    if view_mode == 'card':
        # CARD VIEW: Paginate by Person
        person_ids = matching_authorizations.values_list('person_id', flat=True).distinct()
        authorizations_prefetch = Prefetch(
            'authorization_set',
            queryset=matching_authorizations.select_related('style__discipline').order_by('style__discipline__name'),
            to_attr='revoked_authorizations'
        )
        people_list = Person.objects.filter(user_id__in=person_ids).prefetch_related(authorizations_prefetch).order_by('sca_name')
        
        paginator = Paginator(people_list, 10) # Fewer items per page for cards
        page_obj = paginator.get_page(request.GET.get('page', 1))

    else: # 'table' view is the default
        # TABLE VIEW: Paginate by Sanction
        sanctions_list = matching_authorizations.select_related('person', 'style__discipline').order_by('person__sca_name')
        
        paginator = Paginator(sanctions_list, 25)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj': page_obj,
        'view_mode': view_mode,
    }
    return render(request, 'authorizations/manage_sanctions.html', context)

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
    """
    Handles displaying the branch marshal search form, and showing the results
    in either a table or a card view grouped by person.
    """
    # --- POST Logic: Handle appointment changes first ---
    if request.user.is_authenticated:
        auth_officer = is_kingdom_authorization_officer(request.user)
    else:
        auth_officer = False
    if request.method == 'POST':
        if not auth_officer:
            raise PermissionDenied

        action = request.POST.get('action')
        branch_officer_id = request.POST.get('branch_officer_id')
        try:
            branch_officer = BranchMarshal.objects.get(id=branch_officer_id)
            if action == 'extend_appointment':
                branch_officer.end_date += relativedelta(years=1)
                messages.success(request, f'Appointment for {branch_officer.person.sca_name} has been extended one year.')
            elif action == 'end_appointment':
                branch_officer.end_date = date.today() - timedelta(days=1)
                messages.success(request, f'Appointment for {branch_officer.person.sca_name} has been ended.')
            branch_officer.save()
        except BranchMarshal.DoesNotExist:
            messages.error(request, "The specified branch marshal appointment could not be found.")
        
        return redirect('branch_marshals')

    # --- GET Logic: Display pages ---

    # Get dropdown options for the search form
    current_marshal_offices = BranchMarshal.objects.filter(end_date__gte=date.today())
    sca_name_options = Person.objects.filter(branchmarshal__in=current_marshal_offices).distinct().order_by('sca_name').values_list('sca_name', flat=True)
    branch_options = Branch.objects.filter(branchmarshal__in=current_marshal_offices).distinct().order_by('name').values_list('name', flat=True)
    discipline_options = Discipline.objects.filter(branchmarshal__in=current_marshal_offices).distinct().order_by('name').values_list('name', flat=True)
    region_options = Branch.objects.regions().order_by('name').values_list('name', flat=True)

    # Handle request for the dedicated search form
    if request.GET.get('goal') == 'search':
        context = {
            'sca_name_options': sca_name_options,
            'branch_options': branch_options,
            'discipline_options': discipline_options,
            'region_options': region_options,
        }
        return render(request, 'authorizations/marshals_search_form.html', context)

    # --- If not the search goal, proceed with showing results ---
    
    # Build the dynamic filter
    dynamic_filter = Q(end_date__gte=date.today())
    if sca_name := request.GET.get('sca_name'):
        dynamic_filter &= Q(person__sca_name=sca_name)
    if branch := request.GET.get('branch'):
        dynamic_filter &= Q(branch__name=branch)
    if discipline := request.GET.get('discipline'):
        dynamic_filter &= Q(discipline__name=discipline)
    if region := request.GET.get('region'):
        dynamic_filter &= Q(region__name=region)
    
    matching_appointments = BranchMarshal.objects.filter(dynamic_filter).exclude(person__user_id=11968)
    view_mode = request.GET.get('view', 'table')
    page_obj = None

    if view_mode == 'card':
        # CARD VIEW: Paginate by Person
        person_ids = matching_appointments.values_list('person_id', flat=True).distinct()
        appointments_prefetch = Prefetch(
            'branchmarshal_set',
            # UPDATED: Added 'branch__region' to efficiently fetch the region name
            queryset=matching_appointments.select_related('branch__region', 'discipline').order_by('branch__name'),
            to_attr='current_appointments'
        )
        people_list = Person.objects.filter(user_id__in=person_ids).prefetch_related(appointments_prefetch).order_by('sca_name')
        
        paginator = Paginator(people_list, 10)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    else: # 'table' view is the default
       # UPDATED: Added 'branch__region' to efficiently fetch the region name
        marshals_list = matching_appointments.select_related(
            'person', 'branch__region', 'discipline'
        ).order_by('person__sca_name', 'branch__name')
        paginator = Paginator(marshals_list, 25)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj': page_obj,
        'view_mode': view_mode,
        'auth_officer': auth_officer,
    }
    return render(request, 'authorizations/branch_marshals.html', context)

# This is where the Forms are kept

class TitleModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        # show “Duke (Ducal)” etc.
        return f"{obj.name} ({obj.rank})"

class CreatePersonForm(forms.Form):
    """Creates the initial person and user."""
    ALLOWED_TITLES = [
        "Duke", "Duchess",
        "Count", "Countess",
        "Viscount", "Viscountess",
        "Sir", "Dame",
        "Master", "Maestra",
        "Baron", "Baroness",
        "Don", "Dona",
        "Honorable Lord", "Honorable Lady", "The Honorable",
        "Lord", "Lady", "Gentle",
    ]
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
    phone_number = forms.CharField(label='Phone Number', required=True, help_text='Enter a 10 digit phone number')
    birthday = forms.DateField(label='Birthday', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    discipline_names = Discipline.objects.values_list('name', flat=True)
    sca_name = forms.CharField(label='SCA Name', required=False)
    title = TitleModelChoiceField(
        label='Title',
        queryset=Title.objects.none(),
        required=False,
        empty_label='— choose one —'
    )
    new_title = forms.CharField(
        label='Or enter a new title',
        required=False,
        help_text='Type a custom title'
    )
    new_title_rank = forms.ChoiceField(
        label='Rank for new title',
        choices=[('', '— select a rank —')] + list(TITLE_RANK_CHOICES),
        required=False
    )
    branch = forms.ModelChoiceField(label='Branch', queryset=Branch.objects.non_regions(), required=True)
    is_minor = forms.BooleanField(label='Is Minor', required=False)
    parent_id = forms.ModelChoiceField(label='Parent ID', queryset=Person.objects.all().exclude(sca_name='admin'),required=False)
    background_check_expiration = forms.DateField(
        label='Background Check Expiration',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    
    def __init__(self, *args, **kwargs):
        """Allow passing a user instance when updating."""
        self.user_instance = kwargs.pop('user_instance', None)
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        qs = Title.objects.filter(name__in=self.ALLOWED_TITLES)
        qs = qs.order_by('pk')
        self.fields['title'].queryset = qs

    def clean_phone_number(self):
        raw = self.cleaned_data['phone_number']
        # strip out everything but digits
        digits = re.sub(r'\D', '', raw)
        if len(digits) != 10:
            raise ValidationError("Enter a 10-digit U.S. phone number.")
        # format as (###) ###-####
        formatted = f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
        return formatted

    def clean(self):
        cleaned_data = super().clean()
        new_title = cleaned_data.get('new_title')
        new_title_rank = cleaned_data.get('new_title_rank')

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

        if new_title:
            if not new_title_rank:
                self.add_error('new_title_rank', 'Please select one of the existing ranks.')
                raise forms.ValidationError('Creating a new title requires choosing a rank.')
            title_obj, _ = Title.objects.get_or_create(name=new_title, rank=new_title_rank)
            cleaned_data['title'] = title_obj

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
