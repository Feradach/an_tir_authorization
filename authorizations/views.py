from dateutil.relativedelta import relativedelta
from django.core.mail import send_mail
from django.conf import settings
from datetime import date, datetime
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse
from datetime import timedelta
import logging
from io import BytesIO
from django.db.models import Q, Prefetch, Max
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib.staticfiles import finders
from django.core.cache import cache
from .models import User, Authorization, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, BranchMarshal, Title, TITLE_RANK_CHOICES
from .permissions import is_senior_marshal, is_branch_marshal, is_regional_marshal, is_kingdom_marshal, is_kingdom_authorization_officer, authorization_follows_rules, calculate_age, approve_authorization, appoint_branch_marshal, waiver_signed, AUTHORIZATION_OFFICER_SIGN_OFF, membership_is_current
from itertools import groupby
from operator import attrgetter
from pdfrw import PdfReader, PdfWriter, PdfName, PageMerge
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
from django.contrib import messages
from django import forms
from django.core.validators import RegexValidator
import re
import mistune
import bleach
from authorizations.security.events import log_security_event
from authorizations.security.signals import security_event

logger = logging.getLogger(__name__)
FIGHTER_CARD_WATERMARK = ''
PDF_FONT_NAME = 'DejaVuSans'
_PDF_FONT_REGISTERED = False
_PASSWORD_TOKEN_GENERATOR = PasswordResetTokenGenerator()

USERNAME_RECOVERY_IP_LIMIT = 5
USERNAME_RECOVERY_EMAIL_LIMIT = 3
USERNAME_RECOVERY_WINDOW_SECONDS = 15 * 60
PASSWORD_RESET_IP_LIMIT = 5
PASSWORD_RESET_USERNAME_LIMIT = 3
PASSWORD_RESET_WINDOW_SECONDS = 15 * 60
REGISTER_IP_LIMIT = 5
REGISTER_EMAIL_LIMIT = 3
REGISTER_WINDOW_SECONDS = 15 * 60

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
    """This is the page they land on for the authorization system."""

    all_people = Person.objects.all().order_by('sca_name').values_list('sca_name',                                                                            flat=True).distinct()
    fighter_name = request.GET.get('sca_name')

    # If a fighter name is selected, handle potential duplicates gracefully
    if fighter_name:
        matches = Person.objects.select_related('branch__region').filter(sca_name=fighter_name)
        match_count = matches.count()

        # Single match: go straight to fighter card
        if match_count == 1:
            fighter_id = matches.first().user_id
            return redirect('fighter', person_id=fighter_id)

        # Multiple matches: render index with a results table under the dropdown
        if match_count > 1:
            context = {
                'all_people': all_people,
                'name_matches': matches.order_by('user_id'),
            }
            # If anonymous, we don't populate marshal-related context
            if request.user.is_anonymous:
                return render(request, 'authorizations/index.html', context)

            # Otherwise, fall through to include role-based context below
            # by storing matches in a temp var we can merge later
            name_matches = matches.order_by('user_id')
        else:
            # No matches found; just continue to render the page normally
            name_matches = None

    if request.user.is_anonymous:
        # If the fighter name hasn't been chosen and the user is anonymous, load the page.
        anon_context = {'all_people': all_people}
        if 'name_matches' in locals() and name_matches:
            anon_context['name_matches'] = name_matches
        return render(request, 'authorizations/index.html' , anon_context)

    # If the user is authenticated, load the page with their marshal context
    pending_authorizations = []
    person = Person.objects.get(user_id=request.user.id)
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


    base_context = {
        'senior_marshal': senior_marshal,
        'branch_marshal': branch_marshal,
        'regional_marshal': regional_marshal,
        'kingdom_marshal': kingdom_marshal,
        'kingdom_earl_marshal': kingdom_earl_marshal,
        'auth_officer': auth_officer,
        'pending_authorizations': pending_authorizations,
        'all_people': all_people,
    }

    # Include potential duplicate results for logged-in users
    if 'name_matches' in locals() and name_matches:
        base_context['name_matches'] = name_matches

    return render(request, 'authorizations/index.html', base_context)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':

        # Attempt to sign user in
        username = request.POST['username']
        password = request.POST['password']
        ip_address = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT")

        user = authenticate(request, username=username, password=password)

        # Check if authentication successful
        if user is not None:
            login(request, user)

            log_security_event(
                "login_success",
                username=user.username,
                user_id=user.id,
                ip=ip_address,
                user_agent=user_agent
            )

            if not user.has_logged_in:
                
                log_security_event(
                    "forced_password_reset_required",
                    username=user.username,
                    user_id=user.id,
                    ip=ip_address
                )
                
                messages.warning(request, 'You must change your password when you first log into the system.')
                return redirect('password_reset', user_id=user.id)
            return HttpResponseRedirect(reverse('index'))
        else:

            log_security_event(
                "login_failed",
                attempted_username=username,
                ip=ip_address,
                user_agent=user_agent
            )
            
            messages.error(request, 'Invalid email and/or password.')
            return render(request, 'authorizations/login.html')
    else:
        return render(request, 'authorizations/login.html')


def logout_view(request):
    logout(request)
    return HttpResponseRedirect(reverse('index'))


def register(request):
    """Public registration: allow users to create their own account.
    Mirrors add_fighter flow but without marshal permission requirements."""
    from django.conf import settings
    # If the request is a POST, process the registration
    if request.method == 'POST':
        if not is_kingdom_authorization_officer(request.user):
            ip_address = _get_client_ip(request)
            email = request.POST.get('email', '').strip().lower()
            ip_key = f"register:ip:{ip_address}"
            email_key = f"register:email:{email}"
            if _throttle_request(ip_key, REGISTER_IP_LIMIT, REGISTER_WINDOW_SECONDS) or \
               (email and _throttle_request(email_key, REGISTER_EMAIL_LIMIT, REGISTER_WINDOW_SECONDS)):
                logger.warning('Registration throttled for email=%s ip=%s', email, ip_address)
                messages.error(request, 'Too many registration attempts. Please wait a bit and try again.')
                return render(request, 'authorizations/register.html', {
                    'person_form': CreatePersonForm(request.POST)
                })

        person_form = CreatePersonForm(request.POST)

        if person_form.is_valid():
            # Create the user
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=person_form.cleaned_data['email'],
                        username=person_form.cleaned_data['username'],
                        password=None,
                        is_active=False,
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
                        background_check_expiration=person_form.cleaned_data.get('background_check_expiration', None),
                    )

                    Person.objects.create(
                        user=user,
                        sca_name=person_form.cleaned_data.get('sca_name') or f"{person_form.cleaned_data['first_name']} {person_form.cleaned_data['last_name']}",
                        title=person_form.cleaned_data.get('title'),
                        branch=person_form.cleaned_data['branch'],
                        is_minor=person_form.cleaned_data['is_minor'],
                        parent=person_form.cleaned_data.get('parent', None),
                    )

            except Exception:
                logger.exception('Error during account creation')
                messages.error(request, 'We could not create the account right now. Please try again later. If this continues, contact the web team.')
                return render(request, 'authorizations/register.html', {
                    'person_form': person_form
                })

            # Send the login credentials to the user
            reset_link = _build_password_reset_link(user)
            try:
                send_mail(
                    'An Tir Authorization: New Account',
                    f'Your account has been created.\n\n'
                    f'Username: {person_form.cleaned_data["username"]}\n'
                    f'Set your password here: {reset_link}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                messages.success(request, 'Account created! A password setup link has been emailed to you.')
            except Exception:
                logger.exception('Error sending new account email for user_id=%s', user.id)
                messages.warning(
                    request,
                    'Account created, but the email failed to send. '
                    'Please contact the web team for assistance.'
                )
            return redirect('fighter', person_id=user.id)

        # invalid form
        for field, errors in person_form.errors.items():
            for error in errors:
                if field == '__all__':
                    messages.error(request, error)
                else:
                    field_label = person_form.fields[field].label if field in person_form.fields else field
                    messages.error(request, f"{field_label}: {error}")
        return render(request, 'authorizations/register.html', {'person_form': person_form})

    # GET
    person_form = CreatePersonForm()
    return render(request, 'authorizations/register.html', {'person_form': person_form})


@login_required
def password_reset(request, user_id):
    # Make sure it is the right user.
    if request.user.id != user_id:
        messages.error(request, "You don't have permission to reset this password.")
        return redirect('index')


    # Get the old password
    if request.method == 'POST':
        
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

def _build_password_reset_link(user: User) -> str:
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = _PASSWORD_TOKEN_GENERATOR.make_token(user)
    reset_path = reverse('password_reset_token', kwargs={'uidb64': uidb64, 'token': token})
    return f"{settings.SITE_URL}{reset_path}"

def _get_client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

def _throttle_request(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if throttled."""
    count = cache.get(key)
    if count is None:
        cache.set(key, 1, timeout=window_seconds)
        return False
    if count >= limit:
        return True
    cache.incr(key)
    return False

def password_reset_token(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if not user or not _PASSWORD_TOKEN_GENERATOR.check_token(user, token):
        messages.error(request, 'This password reset link is invalid or has expired.')
        return render(request, 'authorizations/password_reset_token.html', {'valid_link': False})

    if request.method == 'POST':
        password = request.POST.get('password', '')
        confirmation = request.POST.get('confirmation', '')
        if password != confirmation:
            return render(request, 'authorizations/password_reset_token.html', {
                'valid_link': True,
                'message': 'Passwords must match.'
            })

        try:
            validate_password(password, user=user)
        except ValidationError as e:
            return render(request, 'authorizations/password_reset_token.html', {
                'valid_link': True,
                'message': ' '.join(e.messages),
            })

        user.set_password(password)
        user.is_active = True
        user.has_logged_in = True
        user.save()
        messages.success(request, 'Your password has been set. You can now log in.')
        return redirect('login')

    return render(request, 'authorizations/password_reset_token.html', {'valid_link': True})


def recover_account(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # Resetting password
        if action == 'reset_password':
            username = request.POST.get('username', '').strip()
            if not username:
                messages.error(request, 'Please enter a username.')
                return render(request, 'authorizations/recover_account.html')

            ip_address = _get_client_ip(request)
            username_key = f"password-reset:username:{username.lower()}"
            ip_key = f"password-reset:ip:{ip_address}"
            if _throttle_request(username_key, PASSWORD_RESET_USERNAME_LIMIT, PASSWORD_RESET_WINDOW_SECONDS) or \
               _throttle_request(ip_key, PASSWORD_RESET_IP_LIMIT, PASSWORD_RESET_WINDOW_SECONDS):
                logger.warning('Password reset throttled for username=%s ip=%s', username, ip_address)
                messages.success(request, 'If an account exists for that username, a password reset link has been sent to the email on file.')
                return redirect('login')
                
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                # Avoid user enumeration: behave as if a reset was requested.
                messages.success(request, 'If an account exists for that username, a password reset link has been sent to the email on file.')
                return redirect('login')

            reset_link = _build_password_reset_link(user)
            
            # Send the password reset link via email
            try:
                send_mail(
                    'An Tir Authorization: Password Reset',
                    f'We received a request to reset your password.\n\n'
                    f'Username: {user.username}\n'
                    f'Password Reset Link: {reset_link}\n\n'
                    f'If you did not request this, you can ignore this email.',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception('Error sending password reset email for user_id=%s', user.id)
                messages.error(request, 'We could not send a reset email right now. Please try again later.')
                return render(request, 'authorizations/recover_account.html')

            messages.success(request, 'If an account exists for that username, a password reset link has been sent to the email on file.')
            return redirect('login')
                
        # Getting username
        elif action == 'get_username':
            email = request.POST.get('email', '').strip()
            if not email:
                messages.error(request, 'Please enter an email address.')
                return render(request, 'authorizations/recover_account.html')

            ip_address = _get_client_ip(request)
            email_key = f"username-recovery:email:{email.lower()}"
            ip_key = f"username-recovery:ip:{ip_address}"
            if _throttle_request(email_key, USERNAME_RECOVERY_EMAIL_LIMIT, USERNAME_RECOVERY_WINDOW_SECONDS) or \
               _throttle_request(ip_key, USERNAME_RECOVERY_IP_LIMIT, USERNAME_RECOVERY_WINDOW_SECONDS):
                logger.warning('Username recovery throttled for email=%s ip=%s', email, ip_address)
                messages.error(request, 'Too many recovery attempts. Please wait a bit and try again.')
                return render(request, 'authorizations/recover_account.html')

            logger.info('Username recovery requested for email=%s ip=%s', email, ip_address)
            users = User.objects.filter(email=email)
            login_path = reverse('login')
            login_url = f"{settings.SITE_URL}{login_path}"
                
            usernames = [user.username for user in users]
            username_list = '\n'.join([f'- {username}' for username in usernames])
            
            if users.exists():
                try:
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
                except Exception:
                    logger.exception('Error sending username recovery email for %s', email)
                    messages.error(request, 'We could not send an email right now. Please try again later.')
                    return render(request, 'authorizations/recover_account.html')

            # Avoid user enumeration: same response whether or not accounts exist.
            messages.success(request, 'If any accounts exist for that email address, the usernames have been sent.')
            return redirect('login')
            
        else:
            messages.error(request, 'Invalid action.')
            return render(request, 'authorizations/recover_account.html')

    return render(request, 'authorizations/recover_account.html')


def _finalize_waiver_signed(request_user: User, target_user: User):
    """Finalize waiver signing for target_user.
    - Permitted if request_user == target_user OR request_user is Authorization Officer.
    - If target_user has any 'Pending Waiver' authorizations, mark them Active and set
      waiver_expiration to the latest of their expirations.
    - Otherwise, set waiver_expiration to one year from today.
    Returns (ok: bool, message: str).
    """
    if request_user.id != target_user.id and not is_kingdom_authorization_officer(request_user):
        return False, 'You can only sign a waiver for your own account.'

    pending_qs = Authorization.objects.filter(person__user=target_user, status__name='Pending Waiver')
    if pending_qs.exists():
        max_exp = pending_qs.aggregate(latest=Max('expiration'))['latest']
        try:
            active_status = AuthorizationStatus.objects.get(name='Active')
        except AuthorizationStatus.DoesNotExist:
            return False, 'System error: Active status not found.'
        pending_qs.update(status=active_status)
        target_user.waiver_expiration = max_exp
        target_user.save()
        return True, 'Waiver signed and authorizations activated.'
    else:
        target_user.waiver_expiration = date.today() + relativedelta(years=1)
        target_user.save()
        return True, 'Waiver signed for one year.'

def _parse_search_date(value: str):
    """
    Validate date filter inputs so malformed values do not crash the view.
    Returns a tuple of (parsed_date_or_none, invalid_flag).
    """
    if not value:
        return None, False
    candidate = value.strip()
    if not candidate:
        return None, False
    try:
        return datetime.strptime(candidate, '%Y-%m-%d').date(), False
    except ValueError:
        return None, True


def _ensure_pdf_font_registered():
    global _PDF_FONT_REGISTERED
    if _PDF_FONT_REGISTERED:
        return
    font_path = finders.find('fonts/DejaVuSans.ttf') or finders.find('authorizations/static/fonts/DejaVuSans.ttf')
    if not font_path:
        raise Exception('DejaVuSans.ttf font file not found for PDF generation.')
    pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, font_path))
    _PDF_FONT_REGISTERED = True


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return 0.0


def _get_page_size(page):
    media_box = getattr(page, 'MediaBox', None)
    if not media_box:
        return (612, 792)  # Default to letter
    lower_left_x, lower_left_y, upper_right_x, upper_right_y = [_to_float(coord) for coord in media_box]
    return upper_right_x - lower_left_x, upper_right_y - lower_left_y


def _extract_font_size(annotation, default=10):
    default_appearance = getattr(annotation, 'DA', None)
    if not default_appearance:
        return default
    tokens = default_appearance.strip().split()
    if 'Tf' in tokens:
        idx = tokens.index('Tf')
        if idx >= 2:
            try:
                return float(tokens[idx - 1])
            except (TypeError, ValueError):
                return default
    return default


def _draw_watermark(
    can,
    width,
    height,
    text,
    image_path=None,
    image_opacity=0.15,
    scale=0.2,
    x_ratio=0.5,
    y_ratio=0.5,
):
    if not text and not image_path:
        return
    can.saveState()
    if hasattr(can, 'setFillAlpha'):
        can.setFillAlpha(image_opacity)
    if image_path and os.path.exists(image_path):
        target_width = width * scale
        target_height = height * scale
        x = (width * x_ratio) - (target_width / 2)
        y = (height * y_ratio) - (target_height / 2)
        can.drawImage(
            image_path,
            x,
            y,
            width=target_width,
            height=target_height,
            mask='auto',
            preserveAspectRatio=True,
        )
    elif text:
        can.setFont('Helvetica-Bold', 42)
        can.setFillColorRGB(0.85, 0.85, 0.85)
        can.translate(width / 2, height / 2)
        can.rotate(45)
        can.drawCentredString(0, 0, text)
    can.restoreState()


def _build_overlay_page(page, data, watermark_text, watermark_overlays):
    page_width, page_height = _get_page_size(page)
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_width, page_height))
    _ensure_pdf_font_registered()
    for overlay in watermark_overlays:
        _draw_watermark(
            can,
            page_width,
            page_height,
            watermark_text if overlay.get('use_text') else '',
            image_path=overlay.get('image_path'),
            image_opacity=overlay.get('opacity', 0.15),
            scale=overlay.get('scale', 0.2),
            x_ratio=overlay.get('x_ratio', 0.5),
            y_ratio=overlay.get('y_ratio', 0.5),
        )

    annotations = getattr(page, 'Annots', []) or []
    for annotation in annotations:
        if getattr(annotation, 'Subtype', None) != '/Widget' or not getattr(annotation, 'T', None):
            continue

        field_name = annotation.T[1:-1].strip()
        value = data.get(field_name)
        if not value:
            continue

        rect = getattr(annotation, 'Rect', None)
        if not rect:
            continue
        left, bottom, right, top = [_to_float(coord) for coord in rect]
        font_size = _extract_font_size(annotation)
        can.setFont(PDF_FONT_NAME, font_size)
        can.setFillColorRGB(0, 0, 0)

        rotation = 0
        mk = getattr(annotation, 'MK', None)
        if mk is not None:
            rot = None
            if hasattr(mk, 'R'):
                rot = mk.R
            elif isinstance(mk, dict):
                rot = mk.get('/R')
            if rot is not None:
                try:
                    rotation = float(str(rot))
                except (TypeError, ValueError):
                    rotation = 0

        if rotation:
            center_x = (left + right) / 2
            center_y = (bottom + top) / 2
            can.saveState()
            can.translate(center_x, center_y)
            can.rotate(rotation)
            can.drawCentredString(0, -font_size / 2, str(value))
            can.restoreState()
        else:
            text_x = left
            text_y = top - font_size
            can.drawString(text_x, text_y, str(value))

    can.save()
    packet.seek(0)
    overlay_pdf = PdfReader(packet)
    overlay_page = overlay_pdf.pages[0]
    if getattr(page, 'Rotate', None):
        overlay_page.Rotate = page.Rotate
    return overlay_page


def _flatten_pdf_template(template, data, watermark_text=FIGHTER_CARD_WATERMARK, watermark_overlays=None):
    watermark_overlays = watermark_overlays or []
    for page in template.pages:
        overlay_page = _build_overlay_page(page, data, watermark_text, watermark_overlays)
        PageMerge(page).add(overlay_page).render()
        if getattr(page, 'Annots', None) is not None:
            page.Annots = []

    if hasattr(template.Root, 'AcroForm'):
        try:
            del template.Root.AcroForm
        except AttributeError:
            template.Root.AcroForm = None

    return template


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
    invalid_query_params = set()

    discipline = request.GET.get('discipline')
    if discipline: dynamic_filter &= Q(style__discipline__name=discipline)
    style = request.GET.get('style')
    if style: dynamic_filter &= Q(style__name=style)
    marshal = request.GET.get('marshal')
    if marshal: dynamic_filter &= Q(marshal__sca_name=marshal)
    start_date_raw = request.GET.get('start_date')
    start_date, start_invalid = _parse_search_date(start_date_raw)
    if start_invalid:
        invalid_query_params.add('start_date')
        messages.error(request, 'Start date must be in YYYY-MM-DD format.')
        logger.warning('Invalid start_date provided to search: %s', start_date_raw)
    if start_date:
        dynamic_filter &= Q(expiration__gte=start_date)

    end_date_raw = request.GET.get('end_date')
    end_date, end_invalid = _parse_search_date(end_date_raw)
    if end_invalid:
        invalid_query_params.add('end_date')
        messages.error(request, 'End date must be in YYYY-MM-DD format.')
        logger.warning('Invalid end_date provided to search: %s', end_date_raw)
    if end_date:
        dynamic_filter &= Q(expiration__lte=end_date)
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
        # --- CARD VIEW LOGIC ---

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
    
    # 'table' view is the default
    # --- TABLE VIEW LOGIC ---
    # This is the same logic as before, but simplified.
    else: 
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
    query_params = request.GET.copy()
    if 'page' in query_params:
        query_params.pop('page')
    for param in invalid_query_params:
        if param in query_params:
            query_params.pop(param)

    return render(
        request,
        'authorizations/search.html',
        {
            'page_obj': page_obj,
            'items_per_page': items_per_page,
            'view_mode': view_mode,
            'today': date.today(),
            'querystring': query_params.urlencode(),
            
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
    try:
        person = Person.objects.get(user_id=person_id)
    except Person.DoesNotExist:
        messages.error(request, 'Person not found.')
        return redirect('search')
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

    pending_waiver_list = Authorization.objects.select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(person_id=person_id, status__name='Pending Waiver').order_by(
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

    pending_waivers = {}
    for auth in pending_waiver_list:
        discipline_name = auth.style.discipline.name
        if discipline_name not in pending_waivers:
            pending_waivers[discipline_name] = {
                'auth_id': auth.id,
                'marshal_name': auth.marshal.sca_name if auth.marshal else '',
                'earliest_expiration': auth.expiration,
                'styles': [auth.style.name],
                'status': auth.status.name
            }
        else:
            if auth.expiration < pending_waivers[discipline_name]['earliest_expiration']:
                pending_waivers[discipline_name]['earliest_expiration'] = auth.expiration
                pending_waivers[discipline_name]['marshal_name'] = auth.marshal.sca_name if auth.marshal else ''
            style_name = auth.style.name
            if style_name not in pending_waivers[discipline_name]['styles']:
                pending_waivers[discipline_name]['styles'].append(style_name)

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
                'pending_waivers': pending_waivers,
            },
        )



    # All branches except for type = other
    branch_choices = Branch.objects.exclude(type='Other').order_by('name')
    discipline_choices = Discipline.objects.all().order_by('name')
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
            'pending_waivers': pending_waivers,
        },
    )


def generate_fighter_card(request, person_id, template_id):

    add_watermark = False
    watermark_overlays = []
    if add_watermark:
        watermark_image_path = finders.find('pdf_forms/Fighter_Card_Watermark.png') or finders.find('authorizations/static/pdf_forms/Fighter_Card_Watermark.png')
        if watermark_image_path:
            if template_id == '1':
                watermark_overlays = [
                    {'image_path': watermark_image_path, 'scale': .20, 'x_ratio': 0.33, 'y_ratio': 0.13},
                    {'image_path': watermark_image_path, 'scale': .22, 'x_ratio': 0.70, 'y_ratio': 0.13},
                ]
            elif template_id == '2':
                watermark_overlays = [
                    {'image_path': watermark_image_path, 'scale': .20, 'x_ratio': 0.70, 'y_ratio': 0.16},
                ]
            else:
                watermark_overlays = [
                    {'image_path': watermark_image_path, 'scale': .2, 'x_ratio': 0.5, 'y_ratio': 0.13},
                ]

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

    print(f'PDF data payload for person {person_id}: {data}')


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
    template = _flatten_pdf_template(template, data, watermark_overlays=watermark_overlays)

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
    Only available to the kingdom authorization officer."""
    if not is_kingdom_authorization_officer(request.user):
        messages.error(request, "You don't have the required permission to add a new fighter.")
        raise PermissionDenied

    if request.method == 'POST':
        person_form = CreatePersonForm(request.POST)

        if person_form.is_valid():

            try:
                with transaction.atomic():

                    # Create User
                    user = User.objects.create_user(
                        email=person_form.cleaned_data['email'],
                        username=person_form.cleaned_data['username'],
                        password=None,
                        is_active=False,
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

            except Exception:
                logger.exception('Error during marshal account creation')
                messages.error(request, 'We could not create the account right now. Please try again later. If this continues, contact the web team.')
                return render(request, 'authorizations/new_fighter.html', {
                    'person_form': person_form
                })
            
            reset_link = _build_password_reset_link(user)
            try:
                send_mail(
                    'An Tir Authorization: New Account',
                    f'Your account has been created.\n\n'
                    f'Username: {person_form.cleaned_data["username"]}\n'
                    f'Set your password here: {reset_link}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                messages.success(request,
                                 'User and person created successfully! A password setup link has been sent to the user.')
            except Exception:
                logger.exception('Error sending new account email for user_id=%s', user.id)
                messages.warning(
                    request,
                    'User and person created successfully, but the email failed to send. '
                    'Please contact the web team for assistance.'
                )

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

    # Determine the authorizing marshal.
    # Only the Authorization Officer may specify a different marshal via marshal_id.
    authorizing_marshal = request.user
    marshal_id = request.POST.get('marshal_id')
    if marshal_id:
        if is_kingdom_authorization_officer(request.user):
            try:
                authorizing_marshal = User.objects.get(id=marshal_id)
            except User.DoesNotExist:
                messages.error(request, 'Selected authorizing marshal not found.')
                return redirect('fighter', person_id=person_id)
        else:
            messages.error(request, 'You are not allowed to specify an authorizing marshal.')
            return redirect('fighter', person_id=person_id)
    
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
            # Helpers for waiver and statuses
            def waiver_current(u):
                return bool(u.waiver_expiration and u.waiver_expiration > date.today())

            active_status = AuthorizationStatus.objects.get(name='Active')
            pending_waiver_status = AuthorizationStatus.objects.get(name='Pending Waiver')
            needs_kingdom_status = AuthorizationStatus.objects.get(name='Needs Kingdom Approval')
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
                                        # Marshal authorizations require current membership; never Pending Waiver here
                                        if not membership_is_current(person.user):
                                            messages.error(request, 'Marshal authorizations require a current membership.')
                                            return redirect('fighter', person_id=person_id)
                                        update_auth.status = active_status
                                        messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')
                                else:
                                    update_auth.status = active_status if waiver_current(person.user) else pending_waiver_status
                                    if update_auth.status == active_status:
                                        messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')
                                    else:
                                        messages.success(request, f'Existing authorization for {update_auth.style.name} pending waiver.')
                                
                                # Set expiration based on youth marshal rules
                                if update_auth.style.discipline.name in ['Youth Armored', 'Youth Rapier']:
                                    two_years = date.today() + relativedelta(years=2)
                                    if update_auth.style.name in ['Junior Marshal', 'Senior Marshal']:
                                        if person.user.background_check_expiration:
                                            update_auth.expiration = min(two_years, person.user.background_check_expiration)
                                        else:
                                            update_auth.expiration = two_years
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
                                        if AUTHORIZATION_OFFICER_SIGN_OFF:
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=needs_kingdom_status,
                                            )
                                            messages.success(request, f'Authorization for {style.name} submitted to Kingdom for approval.')
                                        else:
                                            status_to_set = active_status if waiver_current(person.user) else pending_waiver_status
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=status_to_set,
                                            )
                                            if status_to_set == active_status:
                                                messages.success(request, f'Authorization for {style.name} created successfully!')
                                            else:
                                                messages.success(request, f'Authorization for {style.name} pending waiver.')
                                        # Only push waiver when the new auth is Active
                                        if (not AUTHORIZATION_OFFICER_SIGN_OFF) and status_to_set == active_status:
                                            if (not person.user.waiver_expiration) or (person.user.waiver_expiration < expiration):
                                                person.user.waiver_expiration = expiration
                                                person.user.save()
                                    else:
                                        if AUTHORIZATION_OFFICER_SIGN_OFF:
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=date.today() + relativedelta(years=4),
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=needs_kingdom_status,
                                            )
                                            messages.success(request, f'Authorization for {style.name} submitted to Kingdom for approval.')
                                        else:
                                            status_to_set = active_status if waiver_current(person.user) else pending_waiver_status
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=date.today() + relativedelta(years=4),
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=status_to_set,
                                            )
                                            if status_to_set == active_status:
                                                messages.success(request, f'Authorization for {style.name} created successfully!')
                                            else:
                                                messages.success(request, f'Authorization for {style.name} pending waiver.')
                                        # Only push waiver when the new auth is Active
                                        if (not AUTHORIZATION_OFFICER_SIGN_OFF) and status_to_set == active_status:
                                            if (not person.user.waiver_expiration) or (person.user.waiver_expiration < new_auth.expiration):
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
    from django.conf import settings
    testing = getattr(settings, 'AUTHZ_TEST_FEATURES')
    print(testing)
    requestor = request.user
    user = User.objects.get(id=user_id)
    person = user.person
    if requestor != user and (not hasattr(user, 'person') or user.person.parent_id != requestor.id):
        if not is_kingdom_authorization_officer(requestor):
            raise PermissionDenied

    children = user.person.children.all()

    # Active marshal appointment (any branch type)
    branch_officer = (
        BranchMarshal.objects.filter(
            person__user=user,
            end_date__gte=date.today(),
        )
        .select_related('branch', 'discipline')
        .order_by('-end_date')
        .first()
    )

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
        action = request.POST.get('action')
        requestor = request.user
        user = User.objects.get(id=user_id)
        person = user.person

        if action == 'add_authorization_self':
            if not testing:
                messages.error(request, 'Testing is not enabled; cannot submit authorization.')
                return redirect('user_account', user_id=user.id)
            discipline_id = request.POST.get('discipline')
            style_ids = request.POST.getlist('weapon_styles')
            selected_styles = sorted(set(style_ids))
            if not discipline_id or not selected_styles:
                messages.error(request, 'Please select a discipline and at least one style.')
                return redirect('user_account', user_id=user.id)

            # Find an administrative user to record as the authorizing marshal for testing.
            admin_user = User.objects.filter(is_superuser=True).order_by('id').first()
            if not admin_user:
                admin_user = User.objects.filter(username__iexact='admin').order_by('id').first()
            if not admin_user:
                messages.error(request, 'Admin user not found; cannot submit authorization.')
                return redirect('user_account', user_id=user.id)
            try:
                admin_person = Person.objects.get(user=admin_user)
            except Person.DoesNotExist:
                # Create a minimal Person record for the superuser if missing (testing only)
                admin_person = Person.objects.create(
                    user=admin_user,
                    sca_name=admin_user.get_full_name() or admin_user.username or 'Admin',
                    branch=person.branch,
                    is_minor=False,
                )

            created = 0
            try:
                for sid in selected_styles:
                    style = WeaponStyle.objects.get(id=sid)

                    # Skip if an authorization already exists for this person/style
                    if Authorization.objects.filter(person=person, style=style).exists():
                        continue

                    if style.name in ['Senior Marshal', 'Junior Marshal']:
                        expiration = date.today() + relativedelta(years=4)
                        if not membership_is_current(person.user):
                            messages.error(request, 'Marshal authorizations require a current membership.')
                            return redirect('user_account', user_id=user.id)
                        status = active_status
                    else:
                        if style.discipline.name in ['Youth Armored', 'Youth Rapier'] and style.name in ['Junior Marshal', 'Senior Marshal']:
                            two_years = date.today() + relativedelta(years=2)
                            if person.user.background_check_expiration:
                                expiration = min(two_years, person.user.background_check_expiration)
                            else:
                                expiration = two_years
                        else:
                            expiration = date.today() + relativedelta(years=4)

                        status = active_status if waiver_current(person.user) else pending_waiver_status

                    Authorization.objects.create(
                        person=person,
                        style=style,
                        expiration=expiration,
                        marshal=admin_person,
                        status=status,
                    )
                    created += 1

                if created:
                    messages.success(request, f'{created} authorization(s) submitted.')
                else:
                    messages.info(request, 'No new authorizations were created (duplicates skipped).')
                return redirect('user_account', user_id=user.id)
            except Exception:
                logger.exception('Error creating authorization(s) for person_id=%s', person_id)
                messages.error(request, 'We could not create the authorization(s) right now. Please try again later. If this continues, contact the web team.')
                return redirect('user_account', user_id=user.id)

        elif action in ('self_set_regional', 'self_remove_regional'):
            # Only the owner can change their own appointment
            if not testing:
                messages.error(request, 'Testing is not enabled; cannot set self as marshal officer.')
                return redirect('user_account', user_id=user.id)
            if request.user.id != user_id:
                messages.error(request, "You can only change your own marshal appointment.")
                return redirect('user_account', user_id=user_id)

            # Accept either 'region_id' (legacy form field) or 'branch_id'
            branch_id = request.POST.get('region_id') or request.POST.get('branch_id')
            discipline_id = request.POST.get('discipline_id')
            try:
                branch = Branch.objects.get(id=branch_id)
            except Branch.DoesNotExist:
                messages.error(request, 'Invalid branch selected.')
                return redirect('user_account', user_id=user_id)
            # Exclude branches of type 'Other'
            if branch.type == 'Other':
                messages.error(request, 'Selected branch type is not eligible for marshal appointments.')
                return redirect('user_account', user_id=user_id)
            try:
                discipline = Discipline.objects.get(id=discipline_id)
            except Discipline.DoesNotExist:
                messages.error(request, 'Invalid discipline selected.')
                return redirect('user_account', user_id=user_id)

            if action == 'self_set_regional':
                # Validate requirements only for setting (not removing)
                # Skip Senior Marshal requirement for Authorization Officer discipline
                if discipline.name != 'Authorization Officer':
                    if branch.type in ['Kingdom', 'Principality', 'Region']:
                        # Regional/kingdom appointment requires Senior Marshal
                        has_required = Authorization.objects.filter(
                            person=person,
                            style__name='Senior Marshal',
                            style__discipline=discipline,
                            status__name='Active',
                            expiration__gte=date.today(),
                        ).exists()
                        if not has_required:
                            messages.error(request, f'You must hold an active Senior Marshal in {discipline.name}.')
                            return redirect('user_account', user_id=user_id)
                    else:
                        # Local branch appointment allows Junior or Senior Marshal
                        has_required = Authorization.objects.filter(
                            person=person,
                            style__name__in=['Junior Marshal', 'Senior Marshal'],
                            style__discipline=discipline,
                            status__name='Active',
                            expiration__gte=date.today(),
                        ).exists()
                        if not has_required:
                            messages.error(request, f'You must hold an active Junior or Senior Marshal in {discipline.name}.')
                            return redirect('user_account', user_id=user_id)

                if not user.membership or not user.membership_expiration or user.membership_expiration < date.today():
                    messages.error(request, 'A current SCA membership (with valid expiration) is required.')
                    return redirect('user_account', user_id=user_id)

                # Authorization Officer may only select the Kingdom (An Tir)
                if discipline.name == 'Authorization Officer' and branch.name != 'An Tir':
                    messages.error(request, 'Authorization Officers must be appointed at the Kingdom level (An Tir).')
                    return redirect('user_account', user_id=user_id)

                # Enforce single active officer position at a time
                has_other_active_office = BranchMarshal.objects.filter(
                    person=person,
                    end_date__gte=date.today(),
                ).exclude(branch=branch, discipline=discipline).exists()
                if has_other_active_office:
                    messages.error(
                        request,
                        'You already hold an active officer position. Please end it before setting a new one.'
                    )
                    return redirect('user_account', user_id=user_id)
                try:
                    bm = BranchMarshal.objects.get(person=person, branch=branch, discipline=discipline, end_date__gte=date.today())
                    bm.end_date = date.today() + relativedelta(years=1)
                    bm.save()
                    messages.success(request, f'Your marshal appointment for {discipline.name} in {branch.name} has been refreshed.')
                except BranchMarshal.DoesNotExist:
                    BranchMarshal.objects.create(
                        branch=branch,
                        person=person,
                        discipline=discipline,
                        start_date=date.today(),
                        end_date=date.today() + relativedelta(years=1),
                    )
                    messages.success(request, f'You are now set as a marshal for {discipline.name} in {branch.name}.')
                return redirect('user_account', user_id=user_id)

            if action == 'self_remove_regional':
                qs = BranchMarshal.objects.filter(person=person, branch=branch, discipline=discipline, end_date__gte=date.today())
                if qs.exists():
                    # Set end date to yesterday so it no longer counts as active (we check end_date__gte=today)
                    qs.update(end_date=date.today() - relativedelta(days=1))
                    messages.success(request, f'Marshal appointment for {discipline.name} in {branch.name} has been ended.')
                else:
                    messages.info(request, 'No active regional marshal appointment found to remove.')
                return redirect('user_account', user_id=user_id)

        # Default: update account information
        if requestor != user and (not hasattr(user, 'person') or user.person.parent_id != requestor.id):
            if not is_kingdom_authorization_officer(requestor):
                raise PermissionDenied
        form = CreatePersonForm(request.POST, user_instance=user, request=request)
        if form.is_valid():
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

            if is_kingdom_authorization_officer(request.user):
                user.background_check_expiration = form.cleaned_data.get('background_check_expiration')

            user.save()

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

    # Calculate waiver status strictly by waiver_expiration (not membership)
    waiver_signed = bool(user.waiver_expiration and user.waiver_expiration > date.today())
    # Preserve display of a maximum relevant date for UI (waiver or membership)
    max_expiration = None
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
        # Prep authorization UI (initially just show the form; logic wired later)
        'auth_form': CreateAuthorizationForm(user=request.user, show_all=True),
        'auth_officer': is_kingdom_authorization_officer(request.user),
        'all_people': Person.objects.all().order_by('sca_name'),
        # Branch choices for self-appointment (exclude Other type)
        'branch_choices': Branch.objects.exclude(type='Other').order_by('name'),
        'discipline_choices': Discipline.objects.order_by('name'),
        'testing': testing,
    }

    return render(request, 'authorizations/user_account.html', context)

@login_required
def sign_waiver(request, user_id):
    user = User.objects.get(id=user_id)
    if request.method == 'POST':
        ok, msg = _finalize_waiver_signed(request.user, user)
        if ok:
            messages.success(request, msg)
            return redirect('user_account', user_id=user.id)
        else:
            messages.error(request, msg)
            return redirect('index')
    else:
        # Only the account owner may view the waiver page (AO cannot view others' waiver page)
        if request.user.id != user_id:
            messages.error(request, 'You can only sign a waiver for your own account.')
            return redirect('index')
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
        # Region is a property of the related Branch (self-referential FK)
        dynamic_filter &= Q(branch__region__name=region)
    
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

def changelog_view(request):
    """Render the local CHANGELOG.md as HTML on the changelog page.

    Reads from settings.BASE_DIR so it uses the project root regardless of module location.
    """
    base_dir = settings.BASE_DIR  # Path object
    candidates = ['CHANGELOG.md', 'Changelog.md', 'changelog.md']

    md = mistune.create_markdown()
    allowed_tags = [
        'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'em', 'i', 'li', 'ol',
        'p', 'pre', 'strong', 'ul', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
    ]
    allowed_attrs = {'a': ['href', 'title', 'rel', 'target']}

    changelog_html = None
    for name in candidates:
        path = base_dir / name
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    text = f.read()
                html = md(text)
                changelog_html = bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs, strip=True)
            except Exception:
                changelog_html = None
            break

    return render(request, 'changelog.html', {'changelog_html': changelog_html})

def get_client_ip(request):
    return request.META.get(
        "HTTP_CF_CONNECTING_IP",
        request.META.get("REMOTE_ADDR")
    )


# This is where the Forms are kept

class TitleModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        # show "Duke (Ducal)" etc.
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
    honeypot = forms.CharField(
        label='Leave this field blank',
        required=False,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'tabindex': '-1',
        })
    )
    username = forms.CharField(label='Username', required=True)
    first_name = forms.CharField(label='First Name', required=True)
    last_name = forms.CharField(label='Last Name', required=True)
    membership = forms.CharField(
        label='Membership Number',
        required=False,
        max_length=20,
        validators=[RegexValidator(r'^\d{1,20}$', 'Enter 1-20 digits.')]
    )
    membership_expiration = forms.DateField(label='Membership Expiration', required=False,
                                            widget=forms.DateInput(attrs={'type': 'date'}))
    address = forms.CharField(label='Address', required=True)
    address2 = forms.CharField(label='Address Line 2', required=False)
    city = forms.CharField(label='City', required=True)
    state_province = forms.ChoiceField(
        label='State/Province',
        choices=[('', '-- select one --')] + state_province_choices,
        required=True
    )
    postal_code = forms.CharField(label='Postal Code', required=True)
    country = forms.ChoiceField(
        label='Country',
        choices=[('', '-- select one --'), ('Canada', 'Canada'), ('United States', 'United States')],
        required=True
    )
    phone_number = forms.CharField(label='Phone Number', required=True, help_text='Enter a 10 digit phone number')
    birthday = forms.DateField(label='Birthday', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    discipline_names = Discipline.objects.values_list('name', flat=True)
    sca_name = forms.CharField(label='SCA Name', required=False)
    title = TitleModelChoiceField(
        label='Title',
        queryset=Title.objects.none(),
        required=False,
        empty_label='-- choose one --'
    )
    new_title = forms.CharField(
        label='Or enter a new title',
        required=False,
        help_text='Type a custom title'
    )
    new_title_rank = forms.ChoiceField(
        label='Rank for new title',
        choices=[('', '-- select a rank --')] + list(TITLE_RANK_CHOICES),
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
        show_all = kwargs.pop('show_all', False)
        super().__init__(*args, **kwargs)
        qs = Title.objects.filter(name__in=self.ALLOWED_TITLES)
        qs = qs.order_by('pk')
        self.fields['title'].queryset = qs
        # Order branches alphabetically and exclude region-level types (Kingdom/Principality/Region)
        self.fields['branch'].queryset = Branch.objects.non_regions().order_by('name')

    def clean_phone_number(self):
        raw = self.cleaned_data['phone_number']
        # strip out everything but digits
        digits = re.sub(r'\D', '', raw)
        if len(digits) != 10:
            raise ValidationError("Enter a 10-digit U.S. phone number.")
        # format as (###) ###-####
        formatted = f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
        return formatted
        
    def clean_postal_code(self):
        postal_code = self.cleaned_data.get('postal_code', '').strip().upper()
        if not postal_code:
            raise forms.ValidationError('Postal code is required.')
            
        # Check if the postal code matches any of the valid patterns
        valid = (
            postal_code.startswith('V') or  # Starts with V
            postal_code.startswith('97') or  # Starts with 97
            postal_code.startswith('98') or  # Starts with 98
            any(postal_code.startswith(prefix) for prefix in ['991', '992', '993', '994']) or  # Starts with 991-994
            any(postal_code.startswith(prefix) for prefix in ['838', '835'])  # Starts with 838 or 835
        )
        
        if not valid:
            raise forms.ValidationError(
                'Postal code must be within An Tir.'
            )
            
        return postal_code

    def clean_state_province(self):
        state_province = self.cleaned_data.get('state_province', '').strip().title()
        if not state_province:
            raise forms.ValidationError('State/Province is required.')
        
        # Check if the state/province matches any of the valid patterns
        valid = (
            state_province == 'Oregon' or
            state_province == 'Washington' or
            state_province == 'Idaho' or
            state_province == 'British Columbia'
        )
        
        if not valid:
            raise forms.ValidationError(
                'State/Province must be within An Tir.'
            )
        
        return state_province

    def clean(self):
        cleaned_data = super().clean()
        new_title = cleaned_data.get('new_title')
        new_title_rank = cleaned_data.get('new_title_rank')

        user_id = self.user_instance.id if self.user_instance else None

        if cleaned_data.get('honeypot'):
            raise forms.ValidationError('Unable to process submission.')

        if not cleaned_data.get('is_minor') and cleaned_data.get('parent_id'):
            raise forms.ValidationError('A non-minor must not have a parent ID.')

        username = cleaned_data.get('username')
        if username and User.objects.filter(username=username).exclude(id=user_id).exists():
            raise forms.ValidationError('A user with this username already exists.')

        membership = cleaned_data.get('membership')
        if isinstance(membership, str):
            membership = membership.strip()
            cleaned_data['membership'] = membership or None
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
        show_all = kwargs.pop('show_all', False)
        super().__init__(*args, **kwargs)

        if show_all:
            # Public testing: expose all disciplines (except AO/EM which aren't user auths)
            self.fields['discipline'].queryset = Discipline.objects.all().exclude(name__in=['Authorization Officer', 'Earl Marshal'])
        elif user:
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
