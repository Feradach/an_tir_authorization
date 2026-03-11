from dateutil.relativedelta import relativedelta
import csv
import uuid
from django.core.mail import send_mail
from django.conf import settings
from datetime import date, datetime
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse, Http404, FileResponse
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
from django.utils import timezone
from django.contrib.staticfiles import finders
from django.core.cache import cache
from .models import User, Authorization, Branch, Discipline, WeaponStyle, AuthorizationStatus, Person, BranchMarshal, Title, TITLE_RANK_CHOICES, AuthorizationNote, UserNote, AuthorizationPortalSetting, ReportingPeriod, ReportValue, Sanction, MembershipRosterImport, MembershipRosterEntry, SupportingDocument, SupportingDocumentPerson, SupportingDocumentAuthorization
from .permissions import is_senior_marshal, is_branch_marshal, is_regional_marshal, is_kingdom_marshal, is_kingdom_authorization_officer, is_kingdom_earl_marshal, authorization_follows_rules, calculate_age, approve_authorization, appoint_branch_marshal, waiver_signed, authorization_officer_sign_off_enabled, membership_is_current, calculate_authorization_expiration, validate_approve_authorization, validate_reject_authorization, authorization_requires_concurrence, is_authorized_in_discipline, can_manage_branch_marshal_office, can_manage_any_branch_marshal_office, marshal_office_effective_expiration, create_authorization_note, kingdom_review_status_name_for_style, is_kingdom_review_status_name, KINGDOM_APPROVAL_STATUS, KINGDOM_EQUESTRIAN_WAIVER_STATUS
from itertools import groupby
from collections import defaultdict
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
from types import SimpleNamespace
from authorizations.security.events import log_security_event
from authorizations.reporting import build_current_report_snapshot, ReportingConfigurationError


def _get_action_note(request, field_name='action_note'):
    return (request.POST.get(field_name) or '').strip()


def not_found_redirect_view(request, exception):
    """Redirect unknown routes to the appropriate homepage shell."""
    if request.path.startswith('/authorizations/'):
        messages.warning(request, 'That page was not found. Redirected to the Authorizations Homepage.')
        return redirect('index')
    messages.warning(request, 'That page was not found. Redirected to Home.')
    return redirect('homepage')


def _marshal_promotion_note_required_for_approval(authorization: Authorization) -> bool:
    return (
        authorization.style.name in ['Junior Marshal', 'Senior Marshal']
        and not is_kingdom_review_status_name(authorization.status.name)
    )


def _note_required_for_rejection(authorization: Authorization) -> bool:
    return (
        authorization.style.name in ['Junior Marshal', 'Senior Marshal']
        or authorization.status.name == 'Pending Background Check'
        or is_kingdom_review_status_name(authorization.status.name)
    )


def _approve_all_needs_kingdom(request, action_note=''):
    pending_ids = list(
        Authorization.objects.filter(status__name='Needs Kingdom Approval')
        .order_by('id')
        .values_list('id', flat=True)
    )
    approved = 0
    failures = []
    for authorization_id in pending_ids:
        payload = {'authorization_id': str(authorization_id)}
        if action_note:
            payload['action_note'] = action_note
        synthetic_request = SimpleNamespace(user=request.user, POST=payload)
        ok, msg = approve_authorization(synthetic_request)
        if ok:
            approved += 1
        else:
            failures.append((authorization_id, msg))
    return len(pending_ids), approved, failures


def _report_bulk_kingdom_approval_results(request, total, approved, failures, *, automatic=False):
    if total == 0:
        if automatic:
            messages.info(request, 'No authorizations were waiting for Kingdom approval.')
        else:
            messages.info(request, 'No authorizations are waiting for Kingdom approval.')
        return

    prefix = 'Automatically approved' if automatic else 'Approved'
    if failures:
        messages.warning(
            request,
            f'{prefix} {approved} of {total} authorizations waiting for Kingdom approval.'
        )
        for authorization_id, error_message in failures[:3]:
            messages.warning(request, f'Authorization {authorization_id}: {error_message}')
        if len(failures) > 3:
            messages.warning(request, f'{len(failures) - 3} additional authorization(s) failed approval.')
    else:
        messages.success(request, f'{prefix} all {total} authorizations waiting for Kingdom approval.')


def _get_pending_session(request, key, max_age_seconds=3600):
    pending = request.session.get(key)
    if not pending:
        return None
    created_at = pending.get('created_at')
    if created_at:
        try:
            created_time = datetime.fromisoformat(created_at)
            if datetime.utcnow() - created_time > timedelta(seconds=max_age_seconds):
                del request.session[key]
                request.session.modified = True
                return None
        except ValueError:
            del request.session[key]
            request.session.modified = True
            return None
    return pending


def _can_concur_authorization(user, authorization: Authorization) -> bool:
    if not user or not user.is_authenticated:
        return False
    if not hasattr(user, 'person'):
        return False
    if authorization.person.user_id == user.id:
        return False
    if authorization.marshal and authorization.marshal.user_id == user.id:
        return False
    return is_authorized_in_discipline(user, authorization.style.discipline)


def _resolve_submit_as_user(request, field_name='submit_as_user_id'):
    submit_as = request.user
    if not is_kingdom_authorization_officer(request.user):
        return submit_as, None
    submit_as_raw = (request.POST.get(field_name) or '').strip()
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


MEMBERSHIP_INVALID_MESSAGE = (
    'Invalid membership information. '
    'If you believe this is an error, please contact the Kingdom Authorization Officer.'
)


def _normalize_membership_name(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip()).casefold()


def _membership_matches_current_roster(membership: str, first_name: str, last_name: str, membership_expiration: date) -> bool:
    try:
        roster_entry = MembershipRosterEntry.objects.get(membership_number=membership)
    except MembershipRosterEntry.DoesNotExist:
        return False

    return (
        _normalize_membership_name(roster_entry.first_name) == _normalize_membership_name(first_name)
        and _normalize_membership_name(roster_entry.last_name) == _normalize_membership_name(last_name)
        and roster_entry.membership_expiration == membership_expiration
    )


def _parse_membership_expiration(value: str, row_number: int) -> date:
    if not value:
        raise ValueError(f'Row {row_number}: Membership expiration date is required.')
    raw = value.strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Row {row_number}: Invalid membership expiration date "{raw}".')


def _csv_value(row: dict, row_number: int, field_names: list[str], *, required: bool = True, digits_only: bool = False) -> str:
    value = ''
    for field_name in field_names:
        if field_name in row:
            value = (row.get(field_name) or '').strip()
            break
    if required and not value:
        raise ValueError(f'Row {row_number}: Missing required field ({field_names[0]}).')
    if digits_only and value and not value.isdigit():
        raise ValueError(f'Row {row_number}: Membership number must contain only digits.')
    return value


def _load_membership_rows_from_upload(uploaded_file) -> tuple[list[MembershipRosterEntry], int]:
    decoded = uploaded_file.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(decoded.splitlines())
    if not reader.fieldnames:
        raise ValueError('The uploaded file does not contain a header row.')

    entries = []
    seen_memberships = set()
    skipped_rows = 0
    for row_number, row in enumerate(reader, start=2):
        membership_number = _csv_value(
            row,
            row_number,
            ['Legacy ID (C)', 'Membership Number', 'Membership #'],
            required=False,
            digits_only=False,
        )
        first_name = _csv_value(row, row_number, ['First Name'], required=False)
        last_name = _csv_value(row, row_number, ['Last Name'], required=False)
        expiration_value = _csv_value(
            row,
            row_number,
            ['Membership Expiration Date', 'Expiration'],
            required=False,
        )
        waiver_value = _csv_value(
            row,
            row_number,
            ['Waiver (C)', 'Waiver'],
            required=False,
        )
        has_society_waiver = waiver_value.strip().casefold() == 'yes'

        if not membership_number or not first_name or not last_name or not expiration_value:
            skipped_rows += 1
            continue
        if not membership_number.isdigit():
            skipped_rows += 1
            continue
        try:
            membership_expiration = _parse_membership_expiration(expiration_value, row_number)
        except ValueError:
            skipped_rows += 1
            continue

        if membership_number in seen_memberships:
            skipped_rows += 1
            continue
        seen_memberships.add(membership_number)

        entries.append(
            MembershipRosterEntry(
                membership_number=membership_number,
                first_name=first_name,
                last_name=last_name,
                membership_expiration=membership_expiration,
                has_society_waiver=has_society_waiver,
            )
        )

    if not entries:
        raise ValueError('The uploaded file has no member rows.')
    return entries, skipped_rows


class MembershipRosterUploadForm(forms.Form):
    membership_csv = forms.FileField(required=True)


SUPPORTED_DOCUMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_SUPPORTING_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024
EQUESTRIAN_WAIVER_LINKABLE_STATUSES = [
    'Pending',
    'Needs Concurrence',
    'Needs Regional Approval',
    'Needs Kingdom Approval',
    'Pending Background Check',
    'Pending Waiver',
    'Needs Kingdom Equestrian Waiver',
]


def _validate_supporting_document_file(uploaded_file):
    if not uploaded_file:
        raise ValueError('Please choose a file to upload.')

    filename = (uploaded_file.name or '').strip()
    _, extension = os.path.splitext(filename.lower())
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError('Supported file types are PDF, JPG, and PNG.')

    if uploaded_file.size > MAX_SUPPORTING_DOCUMENT_SIZE_BYTES:
        raise ValueError('File is too large. Maximum size is 10 MB.')


def _assign_unique_supporting_document_filename(uploaded_file, document_type):
    original_name = (uploaded_file.name or '').strip()
    extension = os.path.splitext(original_name)[1].lower()
    prefix = 'bg' if document_type == SupportingDocument.DocumentType.BACKGROUND_CHECK else 'eq'
    stamp = timezone.now().strftime('%Y%m%d%H%M%S')
    suffix = uuid.uuid4().hex[:8]
    uploaded_file.name = f'{prefix}_{stamp}_{suffix}{extension}'


def _equestrian_authorizations_for_people(person_ids):
    return Authorization.objects.select_related(
        'person',
        'style__discipline',
        'status',
    ).filter(
        person_id__in=person_ids,
        style__discipline__name='Equestrian',
        status__name__in=EQUESTRIAN_WAIVER_LINKABLE_STATUSES,
    ).order_by(
        'person__sca_name',
        'style__name',
        'id',
    )


def _parse_int_list(raw_values):
    parsed = []
    for raw in raw_values:
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            parsed.append(int(raw))
        except (TypeError, ValueError):
            continue
    return parsed


def _handle_supporting_document_upload(request, *, default_person=None, next_url='index'):
    document_type = (request.POST.get('document_type') or '').strip()
    uploaded_file = request.FILES.get('document_file')
    jurisdiction = (request.POST.get('jurisdiction') or '').strip().upper()

    try:
        _validate_supporting_document_file(uploaded_file)
    except ValueError as exc:
        return False, str(exc)

    if document_type == SupportingDocument.DocumentType.BACKGROUND_CHECK:
        if jurisdiction:
            return False, 'Jurisdiction is only used for Equestrian Event Waiver uploads.'
        if not default_person:
            return False, 'Background check uploads require an account owner.'
        _assign_unique_supporting_document_filename(uploaded_file, document_type)
        with transaction.atomic():
            document = SupportingDocument.objects.create(
                document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
                file=uploaded_file,
                uploaded_by=request.user,
            )
            SupportingDocumentPerson.objects.create(
                document=document,
                person=default_person,
            )
        return (
            True,
            f'Background check proof uploaded for {default_person.sca_name}. '
            'It is now available for Kingdom review.',
        )

    if document_type == SupportingDocument.DocumentType.EQUESTRIAN_WAIVER:
        valid_jurisdictions = {code for code, _label in SupportingDocument.Jurisdiction.choices}
        if jurisdiction not in valid_jurisdictions:
            return False, 'Please choose a valid equestrian waiver jurisdiction.'

        selected_person_ids = _parse_int_list(request.POST.getlist('eq_person_ids'))
        selected_authorization_ids = _parse_int_list(request.POST.getlist('eq_authorization_ids'))
        if not selected_person_ids:
            return False, 'Please select at least one fighter for this equestrian waiver.'
        if not selected_authorization_ids:
            return False, 'Please select at least one equestrian authorization.'

        has_global_upload_scope = (
            is_kingdom_authorization_officer(request.user)
            or is_senior_marshal(request.user, 'Equestrian')
        )
        if not has_global_upload_scope:
            allowed_person_ids = set()
            if default_person:
                allowed_person_ids.add(default_person.user_id)
            if hasattr(request.user, 'person'):
                allowed_person_ids.add(request.user.person.user_id)
                allowed_person_ids.update(
                    request.user.person.children.values_list('user_id', flat=True)
                )
            unauthorized = sorted(set(selected_person_ids) - allowed_person_ids)
            if unauthorized:
                return (
                    False,
                    'You can only upload equestrian waivers for your own account or linked child accounts.',
                )

        selected_people = list(
            Person.objects.filter(
                user_id__in=selected_person_ids,
                user__merged_into__isnull=True,
            ).order_by('sca_name')
        )
        found_person_ids = {p.user_id for p in selected_people}
        if found_person_ids != set(selected_person_ids):
            return False, 'One or more selected fighters were not found.'

        selected_authorizations = list(
            _equestrian_authorizations_for_people(selected_person_ids).filter(
                id__in=selected_authorization_ids
            )
        )
        found_authorization_ids = {a.id for a in selected_authorizations}
        if found_authorization_ids != set(selected_authorization_ids):
            return False, 'One or more selected equestrian authorizations were invalid.'

        covered_person_ids = {a.person_id for a in selected_authorizations}
        missing_people = sorted(set(selected_person_ids) - covered_person_ids)
        if missing_people:
            return (
                False,
                'Each selected fighter must have at least one selected equestrian authorization.',
            )

        _assign_unique_supporting_document_filename(uploaded_file, document_type)
        with transaction.atomic():
            document = SupportingDocument.objects.create(
                document_type=SupportingDocument.DocumentType.EQUESTRIAN_WAIVER,
                jurisdiction=jurisdiction,
                file=uploaded_file,
                uploaded_by=request.user,
            )
            SupportingDocumentPerson.objects.bulk_create(
                [
                    SupportingDocumentPerson(document=document, person=selected_person)
                    for selected_person in selected_people
                ],
            )
            SupportingDocumentAuthorization.objects.bulk_create(
                [
                    SupportingDocumentAuthorization(document=document, authorization=authorization)
                    for authorization in selected_authorizations
                ],
            )

        return (
            True,
            f'Equestrian waiver uploaded for {len(selected_people)} fighter(s) and '
            f'{len(selected_authorizations)} authorization(s).',
        )

    return False, 'Please choose a valid document type.'


def _user_can_view_note(user, note) -> bool:
    if not user or not user.is_authenticated:
        return False
    if is_kingdom_authorization_officer(user) or is_kingdom_earl_marshal(user):
        return True

    authorization = getattr(note, 'authorization', None)
    if not authorization or not authorization.style or not authorization.person:
        return False
    discipline_name = authorization.style.discipline.name
    if is_kingdom_marshal(user, discipline_name):
        return True

    branch = authorization.person.branch
    region_name = None
    if branch:
        if branch.is_region():
            region_name = branch.name
        elif branch.region:
            region_name = branch.region.name
    if region_name:
        if is_regional_marshal(user, discipline_name, region_name):
            return True
        if is_regional_marshal(user, 'Earl Marshal', region_name):
            return True

    return False


def _office_region_name(office: BranchMarshal):
    branch = getattr(office, 'branch', None)
    if not branch:
        return None
    if branch.is_region():
        return branch.name
    if branch.region:
        return branch.region.name
    return None


def _viewer_is_superior_for_office(viewer: User, office: BranchMarshal) -> bool:
    """Chain-of-command check used for limited officer-expiration visibility."""
    if not viewer or not getattr(viewer, 'is_authenticated', False):
        return False
    if not office or not office.branch or not office.discipline:
        return False

    if can_manage_branch_marshal_office(viewer, office.branch, office.discipline):
        return True

    # Regional discipline marshals are superiors for branch discipline marshals in their own region.
    if office.branch.is_region():
        return False
    if office.discipline.name in ['Earl Marshal', 'Authorization Officer']:
        return False

    region_name = _office_region_name(office)
    if not region_name:
        return False

    regional_offices = BranchMarshal.objects.filter(
        person__user=viewer,
        branch__name=region_name,
        discipline=office.discipline,
        end_date__gte=date.today(),
    ).select_related('person__user', 'discipline')
    for regional_office in regional_offices:
        effective = marshal_office_effective_expiration(regional_office)
        if effective and effective >= date.today():
            return True
    return False


def _is_sanctions_supervisor(user) -> bool:
    return is_kingdom_authorization_officer(user) or is_kingdom_earl_marshal(user)


def _can_access_sanctions(user) -> bool:
    return _is_sanctions_supervisor(user) or is_kingdom_marshal(user)


def _can_view_supporting_documents(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False))


def _can_view_all_supporting_documents(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if (
        is_kingdom_authorization_officer(user)
        or is_kingdom_earl_marshal(user)
        or is_kingdom_marshal(user, 'Equestrian')
    ):
        return True
    if not hasattr(user, 'person'):
        return False
    kingdom_offices = BranchMarshal.objects.filter(
        person=user.person,
        branch__name='An Tir',
        discipline__name__in=['Earl Marshal', 'Equestrian'],
        end_date__gte=date.today(),
    ).select_related('discipline', 'branch')
    for office in kingdom_offices:
        effective = marshal_office_effective_expiration(office)
        if effective and effective >= date.today():
            return True
    return False


def _supporting_documents_queryset_for_viewer(user):
    queryset = SupportingDocument.objects.select_related(
        'uploaded_by__person',
        'reviewed_by__person',
    ).prefetch_related(
        'person_links__person',
        'authorization_links__authorization__person',
        'authorization_links__authorization__style__discipline',
        'authorization_links__authorization__status',
    ).order_by('-uploaded_at')
    if not user or not getattr(user, 'is_authenticated', False):
        return queryset.none()
    if _can_view_all_supporting_documents(user):
        return queryset
    return queryset.filter(
        Q(uploaded_by=user)
        | Q(person_links__person__user=user)
        | Q(authorization_links__authorization__person__user=user)
    )


def _can_view_supporting_document(user, document: SupportingDocument) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if _can_view_all_supporting_documents(user):
        return True
    if document.uploaded_by_id == user.id:
        return True
    return (
        document.person_links.filter(person__user=user).exists()
        or document.authorization_links.filter(authorization__person__user=user).exists()
    )


def _supporting_document_file_exists(document: SupportingDocument) -> bool:
    document_file = getattr(document, 'file', None)
    if not document_file or not document_file.name:
        return False
    try:
        return document_file.storage.exists(document_file.name)
    except Exception:
        return False


def _annotate_homepage_document_alerts(authorizations):
    rows = list(authorizations)
    if not rows:
        return rows

    bg_person_ids = {
        auth.person_id
        for auth in rows
        if auth.status and auth.status.name == 'Pending Background Check'
    }
    eq_auth_ids = {
        auth.id
        for auth in rows
        if auth.status and auth.status.name == KINGDOM_EQUESTRIAN_WAIVER_STATUS
    }

    latest_bg_uploads = {}
    latest_bg_document_ids = {}
    if bg_person_ids:
        for link in (
            SupportingDocumentPerson.objects.filter(
                person_id__in=bg_person_ids,
                document__document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            )
            .select_related('document')
            .order_by('person_id', '-document__uploaded_at', '-document_id')
        ):
            if link.person_id in latest_bg_uploads:
                continue
            if not _supporting_document_file_exists(link.document):
                continue
            latest_bg_uploads[link.person_id] = link.document.uploaded_at
            latest_bg_document_ids[link.person_id] = link.document_id

    latest_eq_uploads = {}
    latest_eq_document_ids = {}
    if eq_auth_ids:
        for link in (
            SupportingDocumentAuthorization.objects.filter(
                authorization_id__in=eq_auth_ids,
                document__document_type=SupportingDocument.DocumentType.EQUESTRIAN_WAIVER,
            )
            .select_related('document')
            .order_by('authorization_id', '-document__uploaded_at', '-document_id')
        ):
            if link.authorization_id in latest_eq_uploads:
                continue
            if not _supporting_document_file_exists(link.document):
                continue
            latest_eq_uploads[link.authorization_id] = link.document.uploaded_at
            latest_eq_document_ids[link.authorization_id] = link.document_id

    for auth in rows:
        auth.document_alert_state = ''
        auth.document_alert_text = '-'
        auth.document_alert_uploaded_at = None
        auth.document_alert_url = ''
        if not auth.status:
            continue

        status_name = auth.status.name
        if status_name == 'Pending Background Check':
            latest_upload = latest_bg_uploads.get(auth.person_id)
            latest_document_id = latest_bg_document_ids.get(auth.person_id)
        elif status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS:
            latest_upload = latest_eq_uploads.get(auth.id)
            latest_document_id = latest_eq_document_ids.get(auth.id)
        else:
            latest_upload = None
            latest_document_id = None

        if status_name not in ['Pending Background Check', KINGDOM_EQUESTRIAN_WAIVER_STATUS]:
            continue

        if not latest_upload:
            auth.document_alert_state = 'missing'
            auth.document_alert_text = 'No file'
            continue

        auth.document_alert_uploaded_at = latest_upload
        if latest_document_id:
            auth.document_alert_url = reverse('supporting_document_file', kwargs={'document_id': latest_document_id})
        changed_at = auth.updated_at or auth.created_at
        if changed_at and latest_upload > changed_at:
            auth.document_alert_state = 'new'
            auth.document_alert_text = 'New upload'
        else:
            auth.document_alert_state = 'on_file'
            auth.document_alert_text = 'On file'

    return rows

def _sanctionable_disciplines_for_user(user):
    base = Discipline.objects.exclude(name__in=['Earl Marshal', 'Authorization Officer'])
    if not user or not user.is_authenticated:
        return base.none()
    if _is_sanctions_supervisor(user):
        return base.order_by('name')
    if not hasattr(user, 'person'):
        return base.none()
    discipline_ids = BranchMarshal.objects.filter(
        person=user.person,
        branch__name='An Tir',
        end_date__gte=date.today(),
    ).values_list('discipline_id', flat=True)
    return base.filter(id__in=discipline_ids).distinct().order_by('name')


def _can_manage_sanctions_for_discipline(user, discipline) -> bool:
    if not discipline:
        return False
    discipline_name = discipline.name if hasattr(discipline, 'name') else discipline
    if discipline_name in ['Earl Marshal', 'Authorization Officer']:
        return False
    if _is_sanctions_supervisor(user):
        return True
    return is_kingdom_marshal(user, discipline_name)


def _active_sanction_issuing_office(user, discipline, today=None):
    if today is None:
        today = date.today()
    if not user or not getattr(user, 'is_authenticated', False) or not hasattr(user, 'person'):
        return None
    if not discipline:
        return None

    target_discipline_names = [discipline.name]
    if is_kingdom_authorization_officer(user):
        target_discipline_names = ['Authorization Officer']
    elif is_kingdom_earl_marshal(user):
        target_discipline_names = ['Earl Marshal']

    offices = BranchMarshal.objects.filter(
        person=user.person,
        branch__name='An Tir',
        discipline__name__in=target_discipline_names,
        end_date__gte=today,
    ).select_related('person__user', 'discipline')

    eligible_offices = []
    for office in offices:
        effective_expiration = marshal_office_effective_expiration(office, today=today)
        if effective_expiration and effective_expiration >= today:
            eligible_offices.append(office)

    if not eligible_offices:
        return None
    return max(eligible_offices, key=lambda office: (office.end_date, office.id))


def _normalize_sanction_end_date(user, discipline, sanction_end_date_raw):
    sanction_end_date_raw = (sanction_end_date_raw or '').strip()
    if not sanction_end_date_raw:
        return False, 'Please select a sanction end date.', None, None

    try:
        sanction_end_date = datetime.strptime(sanction_end_date_raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False, 'Please enter a valid sanction end date.', None, None

    today = date.today()
    if sanction_end_date < today:
        return False, 'Sanction end date cannot be in the past.', None, None

    issuing_office = _active_sanction_issuing_office(user, discipline, today=today)
    if not issuing_office:
        return False, 'You do not have an active marshal office that can issue this sanction.', None, None

    warning_message = None
    if sanction_end_date > issuing_office.end_date:
        sanction_end_date = issuing_office.end_date
        warning_message = (
            'Sanction end date cannot exceed the marshal office expiration date. '
            f'It was set to {sanction_end_date.isoformat()}.'
        )

    return True, '', sanction_end_date, warning_message


def _prepare_sanction_request(user, post_data):
    sanction_type = (post_data.get('sanction_type') or '').strip()
    discipline_id = str(post_data.get('discipline_id') or '').strip()
    style_id = str(post_data.get('style_id') or '').strip()
    sanction_end_date_raw = post_data.get('sanction_end_date')

    if sanction_type == 'discipline':
        if not discipline_id:
            return None, 'Please select a discipline before sanctioning.'
        discipline = Discipline.objects.filter(id=discipline_id).first()
        if not discipline:
            return None, 'Invalid discipline.'
        if not _can_manage_sanctions_for_discipline(user, discipline):
            return None, 'You do not have permission to sanction this discipline.'
        ok, message, sanction_end_date, warning_message = _normalize_sanction_end_date(
            user,
            discipline,
            sanction_end_date_raw,
        )
        if not ok:
            return None, message
        return {
            'sanction_type': sanction_type,
            'discipline': discipline,
            'style': None,
            'sanction_end_date': sanction_end_date,
            'warning_message': warning_message,
        }, ''

    if sanction_type == 'style':
        if not style_id:
            return None, 'Please select a style before sanctioning.'
        style = WeaponStyle.objects.select_related('discipline').filter(id=style_id).first()
        if not style:
            return None, 'Invalid style.'
        if not _can_manage_sanctions_for_discipline(user, style.discipline):
            return None, 'You do not have permission to sanction this discipline.'
        ok, message, sanction_end_date, warning_message = _normalize_sanction_end_date(
            user,
            style.discipline,
            sanction_end_date_raw,
        )
        if not ok:
            return None, message
        return {
            'sanction_type': sanction_type,
            'discipline': style.discipline,
            'style': style,
            'sanction_end_date': sanction_end_date,
            'warning_message': warning_message,
        }, ''

    return None, 'Invalid sanction type.'


def _active_sanctions_queryset(today=None):
    if today is None:
        today = date.today()
    return Sanction.objects.select_related(
        'person__branch__region',
        'discipline',
        'style__discipline',
        'issued_by__person',
        'lifted_by__person',
    ).filter(
        start_date__lte=today,
        end_date__gte=today,
        lifted_at__isnull=True,
    )


def _sanction_note_with_end_date(note, end_date):
    note = (note or '').strip()
    suffix = f'Sanction end date: {end_date.isoformat()}'
    return f'{note}\n\n{suffix}' if note else suffix


def _sanction_extension_note(existing_note, note, end_date, updated_by):
    note = (note or '').strip()
    actor_name = getattr(getattr(updated_by, 'person', None), 'sca_name', '') or updated_by.username
    entry = (
        f'Extension by {actor_name} on {date.today().isoformat()}: {note}\n'
        f'Sanction end date: {end_date.isoformat()}'
    )
    existing_note = (existing_note or '').strip()
    return f'{existing_note}\n\n{entry}' if existing_note else entry


@login_required
def validate_authorization_rules(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Invalid request method.'}, status=405)

    person_id = request.POST.get('person_id')
    style_ids = request.POST.getlist('style_ids')
    if not person_id or not style_ids:
        return JsonResponse({'ok': False, 'message': 'Missing person or styles.'}, status=400)

    authorizing_marshal = request.user
    marshal_id = request.POST.get('marshal_id')
    if marshal_id and is_kingdom_authorization_officer(request.user):
        try:
            authorizing_marshal = User.objects.get(id=marshal_id)
        except User.DoesNotExist:
            return JsonResponse({'ok': False, 'message': 'Selected authorizing marshal not found.'}, status=400)

    person = get_object_or_404(Person, user_id=person_id)

    for style_id in style_ids:
        is_valid, mssg = authorization_follows_rules(
            marshal=authorizing_marshal,
            existing_fighter=person,
            style_id=style_id,
        )
        if not is_valid:
            return JsonResponse({'ok': False, 'message': mssg}, status=200)

    return JsonResponse({'ok': True})


@login_required
def validate_authorization_action(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Invalid request method.'}, status=405)

    action = request.POST.get('action')
    authorization_id = request.POST.get('authorization_id') or request.POST.get('bad_authorization_id')
    if not action or not authorization_id:
        return JsonResponse({'ok': False, 'message': 'Missing action or authorization.'}, status=400)

    try:
        authorization = Authorization.objects.get(id=authorization_id)
    except Authorization.DoesNotExist:
        return JsonResponse({'ok': False, 'message': 'Authorization not found.'}, status=404)

    if action == 'approve_authorization':
        submit_as_user, submit_as_error = _resolve_submit_as_user(request)
        if submit_as_error:
            return JsonResponse({'ok': False, 'message': submit_as_error}, status=400)
        ok, msg = validate_approve_authorization(request.user, submit_as_user, authorization)
        return JsonResponse({'ok': ok, 'message': msg})
    if action == 'reject_authorization':
        if is_kingdom_review_status_name(authorization.status.name):
            submit_as_user = request.user
        else:
            submit_as_user, submit_as_error = _resolve_submit_as_user(request)
            if submit_as_error:
                return JsonResponse({'ok': False, 'message': submit_as_error}, status=400)
        ok, msg = validate_reject_authorization(submit_as_user, authorization)
        return JsonResponse({'ok': ok, 'message': msg})

    return JsonResponse({'ok': False, 'message': 'Invalid action.'}, status=400)


@login_required
def get_equestrian_authorizations(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Invalid request method.'}, status=405)

    person_ids = _parse_int_list(
        request.POST.getlist('person_ids[]') + request.POST.getlist('person_ids')
    )
    if not person_ids:
        return JsonResponse({'ok': True, 'authorizations': []})

    valid_person_ids = list(
        Person.objects.filter(
            user_id__in=person_ids,
            user__merged_into__isnull=True,
        ).values_list('user_id', flat=True)
    )
    if not valid_person_ids:
        return JsonResponse({'ok': True, 'authorizations': []})

    authorizations = _equestrian_authorizations_for_people(valid_person_ids)
    payload = [
        {
            'id': auth.id,
            'label': (
                f'{auth.person.sca_name}: {auth.style.name} '
                f'({auth.status.name}, expires {auth.effective_expiration.isoformat()})'
            ),
            'person_id': auth.person_id,
            'person_name': auth.person.sca_name,
            'style_name': auth.style.name,
            'status_name': auth.status.name,
            'expiration': auth.effective_expiration.isoformat(),
        }
        for auth in authorizations
    ]
    return JsonResponse({'ok': True, 'authorizations': payload})


@login_required
def validate_sanction_action(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Invalid request method.'}, status=405)

    action = request.POST.get('action', 'issue_sanction')
    if not _can_access_sanctions(request.user):
        return JsonResponse({'ok': False, 'message': 'You do not have permission to perform this action.'}, status=403)

    if action == 'lift_sanction':
        sanction_id = request.POST.get('sanction_id')
        if not sanction_id:
            return JsonResponse({'ok': False, 'message': 'Missing sanction.'}, status=400)
        sanction = _active_sanctions_queryset().filter(
            id=sanction_id,
        ).first()
        if not sanction:
            return JsonResponse({'ok': False, 'message': 'Could not find the specified sanction to lift.'})
        if not _can_manage_sanctions_for_discipline(request.user, sanction.discipline):
            return JsonResponse({'ok': False, 'message': 'You do not have permission to manage this sanction.'}, status=403)
        return JsonResponse({'ok': True})

    person_id = request.POST.get('person_id')
    if not person_id:
        return JsonResponse({'ok': False, 'message': 'Missing person.'}, status=400)
    sanction_request, message = _prepare_sanction_request(request.user, request.POST)
    if not sanction_request:
        status_code = 403 if message == 'You do not have permission to sanction this discipline.' else 400
        return JsonResponse({'ok': False, 'message': message}, status=status_code)
    payload = {'ok': True}
    if sanction_request['warning_message']:
        payload['warning'] = sanction_request['warning_message']
    return JsonResponse(payload)

logger = logging.getLogger(__name__)
FIGHTER_CARD_WATERMARK = ''
PDF_FONT_NAME = 'DejaVuSans'
_PDF_FONT_REGISTERED = False
_PASSWORD_TOKEN_GENERATOR = PasswordResetTokenGenerator()

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

    all_people_qs = Person.objects.filter(user__merged_into__isnull=True).order_by('sca_name')
    all_people = all_people_qs.values_list('sca_name', flat=True).distinct()
    sign_off_required = authorization_officer_sign_off_enabled()
    fighter_name = request.GET.get('sca_name')

    # If a fighter name is selected, handle potential duplicates gracefully
    if fighter_name:
        matches = Person.objects.select_related('branch__region').filter(
            sca_name=fighter_name,
            user__merged_into__isnull=True,
        )
        match_count = matches.count()

        # Single match: go straight to fighter card
        if match_count == 1:
            fighter_id = matches.first().user_id
            return redirect('fighter', person_id=fighter_id)

        # Multiple matches: render index with a results table under the dropdown
        if match_count > 1:
            context = {
                'all_people': all_people,
                'all_people_people': all_people_qs,
                'name_matches': matches.order_by('user_id'),
                'authorization_officer_sign_off_required': sign_off_required,
                'can_set_authorization_officer_sign_off': False,
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
        anon_context = {
            'all_people': all_people,
            'all_people_people': all_people_qs,
            'authorization_officer_sign_off_required': sign_off_required,
            'can_set_authorization_officer_sign_off': False,
        }
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
    can_manage_sanctions = _can_access_sanctions(request.user)
    can_view_supporting_documents = _can_view_supporting_documents(request.user)

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
                pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                    person__branch=branch,
                    style__discipline=discipline,
                    status__name='Pending'
                ).order_by('effective_expiration_date')
        if regional_marshal:
            discipline = marshal.discipline
            pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                person__branch__region=marshal.branch,
                style__discipline=discipline,
                status__name='Needs Regional Approval'
            ).order_by('effective_expiration_date')
        if kingdom_marshal:
            discipline = marshal.discipline
            pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                style__discipline=discipline,
                status__name='Needs Regional Approval'
            ).order_by('effective_expiration_date')
        if kingdom_earl_marshal:
            pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                status__name='Needs Regional Approval'
            ).order_by('effective_expiration_date')
        if auth_officer:
            pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                status__name__in=[
                    KINGDOM_APPROVAL_STATUS,
                    'Pending Background Check',
                    KINGDOM_EQUESTRIAN_WAIVER_STATUS,
                ]
            ).order_by('effective_expiration_date')
        elif is_kingdom_marshal(request.user, 'Equestrian'):
            pending_authorizations = Authorization.objects.with_effective_expiration().filter(
                style__discipline__name='Equestrian',
                status__name=KINGDOM_EQUESTRIAN_WAIVER_STATUS,
            ).order_by('effective_expiration_date')

    pending_authorizations = _annotate_homepage_document_alerts(pending_authorizations)

    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to perform this action.')
            return redirect('login')

        action = request.POST.get('action')
        if action == 'set_authorization_officer_sign_off':
            if not auth_officer:
                messages.error(request, 'Only the Kingdom Authorization Officer can change this setting.')
                return redirect('index')
            value = (request.POST.get('authorization_officer_sign_off') or '').strip().lower()
            if value not in {'on', 'off'}:
                messages.error(request, 'Invalid setting value.')
                return redirect('index')
            new_value = value == 'on'
            previous_value = sign_off_required
            AuthorizationPortalSetting.objects.update_or_create(
                pk=1,
                defaults={
                    'require_kao_verification': new_value,
                    'updated_by': request.user,
                },
            )
            messages.success(
                request,
                f'Require Kingdom Authorization Officer Verification is now {"On" if new_value else "Off"}.',
            )
            if previous_value and not new_value:
                auto_note = 'Automatic bulk approval after disabling Kingdom Authorization Officer Verification.'
                total, approved, failures = _approve_all_needs_kingdom(request, action_note=auto_note)
                _report_bulk_kingdom_approval_results(
                    request,
                    total,
                    approved,
                    failures,
                    automatic=True,
                )
            return redirect('index')

        if action == 'approve_all_kingdom_authorizations':
            if not auth_officer:
                messages.error(request, 'Only the Kingdom Authorization Officer can approve all pending kingdom authorizations.')
                return redirect('index')
            if not sign_off_required:
                messages.error(request, 'Bulk Kingdom approval is only available while Kingdom verification is enabled.')
                return redirect('index')

            pending_key = 'pending_authorization_action'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and pending_action.get('action') == 'approve_all_kingdom_authorizations'
            )

            requires_note = False
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required for marshal promotion actions.')
                    return redirect('index')
                request.session[pending_key] = {
                    'action': 'approve_all_kingdom_authorizations',
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the marshal promotion approvals.')
                return redirect('index')

            total, approved, failures = _approve_all_needs_kingdom(request, action_note=action_note)
            _report_bulk_kingdom_approval_results(request, total, approved, failures, automatic=False)
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            return redirect('index')

        if action == 'clear_pending_authorization_action':
            pending_key = 'pending_authorization_action'
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending marshal promotion cleared.')
            return redirect('index')
        if action == 'approve_authorization':
            authorization_id = request.POST.get('authorization_id')
            authorization = Authorization.objects.get(id=authorization_id)
            pending_key = 'pending_authorization_action'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and pending_action.get('action') == 'approve_authorization'
                and pending_action.get('authorization_id') == authorization.id
            )
            requires_note = _marshal_promotion_note_required_for_approval(authorization)
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required for marshal promotion actions.')
                    return redirect('index')
                ok, msg = validate_approve_authorization(request.user, request.user, authorization)
                if not ok:
                    messages.error(request, msg)
                    return redirect('index')
                request.session[pending_key] = {
                    'action': 'approve_authorization',
                    'authorization_id': authorization.id,
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the marshal promotion.')
                return redirect('index')
            is_valid, mssg = approve_authorization(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            return redirect('index')
        elif action == 'reject_authorization':
            authorization = Authorization.objects.get(id=request.POST['bad_authorization_id'])
            pending_key = 'pending_authorization_action'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and pending_action.get('action') == 'reject_authorization'
                and pending_action.get('authorization_id') == authorization.id
            )
            pending_submit_as_user_id = pending_action.get('submit_as_user_id') if is_pending_submit else None
            submit_as_user_id_raw = (request.POST.get('submit_as_user_id') or '').strip()
            if pending_submit_as_user_id and not submit_as_user_id_raw:
                mutable_post = request.POST.copy()
                mutable_post['submit_as_user_id'] = str(pending_submit_as_user_id)
                request.POST = mutable_post
            requires_note = _note_required_for_rejection(authorization)
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required for this rejection.')
                    return redirect('index')
                if is_kingdom_review_status_name(authorization.status.name):
                    submit_as_user = request.user
                else:
                    submit_as_user, submit_as_error = _resolve_submit_as_user(request)
                    if submit_as_error:
                        messages.error(request, submit_as_error)
                        return redirect('index')
                ok, msg = validate_reject_authorization(submit_as_user, authorization)
                if not ok:
                    messages.error(request, msg)
                    return redirect('index')
                request.session[pending_key] = {
                    'action': 'reject_authorization',
                    'authorization_id': authorization.id,
                    'submit_as_user_id': (
                        submit_as_user.id
                        if submit_as_user and not is_kingdom_review_status_name(authorization.status.name)
                        else None
                    ),
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the rejection.')
                return redirect('index')
            is_valid, mssg = reject_authorization(request, authorization)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            return redirect('index')


    pending_authorization_action = _get_pending_session(request, 'pending_authorization_action')

    base_context = {
        'welcome_name': person.sca_name,
        'senior_marshal': senior_marshal,
        'branch_marshal': branch_marshal,
        'regional_marshal': regional_marshal,
        'kingdom_marshal': kingdom_marshal,
        'kingdom_earl_marshal': kingdom_earl_marshal,
        'auth_officer': auth_officer,
        'can_manage_sanctions': can_manage_sanctions,
        'can_view_supporting_documents': can_view_supporting_documents,
        'pending_authorizations': pending_authorizations,
        'all_people': all_people,
        'all_people_people': all_people_qs,
        'authorization_officer_sign_off_required': sign_off_required,
        'can_set_authorization_officer_sign_off': auth_officer,
        'membership_roster_import': MembershipRosterImport.objects.first() if auth_officer else None,
        'pending_authorization_action': pending_authorization_action,
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
            register_window_seconds = _throttle_setting(
                'AUTHZ_REGISTER_WINDOW_SECONDS',
                prod_default=15 * 60,
                test_default=5 * 60,
            )
            register_email_limit = _throttle_setting(
                'AUTHZ_REGISTER_EMAIL_LIMIT',
                prod_default=3,
                test_default=50,
            )
            register_ip_limit = _throttle_setting(
                'AUTHZ_REGISTER_IP_LIMIT',
                prod_default=5,
                test_default=100,
            )
            if _throttle_request(ip_key, register_ip_limit, register_window_seconds) or \
               (email and _throttle_request(email_key, register_email_limit, register_window_seconds)):
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
        messages.error(request, 'Please correct the errors below.')
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


def _throttle_setting(setting_name: str, prod_default: int, test_default: int) -> int:
    raw = getattr(settings, setting_name, None)
    if raw is not None:
        try:
            parsed = int(raw)
            return max(1, parsed)
        except (TypeError, ValueError):
            logger.warning('Invalid throttle setting %s=%r. Using defaults.', setting_name, raw)
    if getattr(settings, 'AUTHZ_TEST_FEATURES', False):
        return test_default
    return prod_default

def password_reset_token(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if (not user) or user.merged_into_id or (not _PASSWORD_TOKEN_GENERATOR.check_token(user, token)):
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
            password_reset_window_seconds = _throttle_setting(
                'AUTHZ_PASSWORD_RESET_WINDOW_SECONDS',
                prod_default=15 * 60,
                test_default=5 * 60,
            )
            password_reset_username_limit = _throttle_setting(
                'AUTHZ_PASSWORD_RESET_USERNAME_LIMIT',
                prod_default=3,
                test_default=50,
            )
            password_reset_ip_limit = _throttle_setting(
                'AUTHZ_PASSWORD_RESET_IP_LIMIT',
                prod_default=5,
                test_default=100,
            )
            if _throttle_request(username_key, password_reset_username_limit, password_reset_window_seconds) or \
               _throttle_request(ip_key, password_reset_ip_limit, password_reset_window_seconds):
                logger.warning('Password reset throttled for username=%s ip=%s', username, ip_address)
                messages.success(request, 'If an account exists for that username, a password reset link has been sent to the email on file.')
                return redirect('login')
                
            try:
                user = User.objects.get(
                    username=username,
                    merged_into__isnull=True,
                )
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
            username_recovery_window_seconds = _throttle_setting(
                'AUTHZ_USERNAME_RECOVERY_WINDOW_SECONDS',
                prod_default=15 * 60,
                test_default=5 * 60,
            )
            username_recovery_email_limit = _throttle_setting(
                'AUTHZ_USERNAME_RECOVERY_EMAIL_LIMIT',
                prod_default=3,
                test_default=50,
            )
            username_recovery_ip_limit = _throttle_setting(
                'AUTHZ_USERNAME_RECOVERY_IP_LIMIT',
                prod_default=5,
                test_default=100,
            )
            if _throttle_request(email_key, username_recovery_email_limit, username_recovery_window_seconds) or \
               _throttle_request(ip_key, username_recovery_ip_limit, username_recovery_window_seconds):
                logger.warning('Username recovery throttled for email=%s ip=%s', email, ip_address)
                messages.error(request, 'Too many recovery attempts. Please wait a bit and try again.')
                return render(request, 'authorizations/recover_account.html')

            logger.info('Username recovery requested for email=%s ip=%s', email, ip_address)
            users = User.objects.filter(
                email=email,
                merged_into__isnull=True,
            )
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


def _get_or_create_status_by_name(name: str) -> AuthorizationStatus:
    status = AuthorizationStatus.objects.filter(name=name).order_by('id').first()
    if status:
        return status
    return AuthorizationStatus.objects.create(name=name)


def _activate_pending_background_check_authorizations(target_user: User) -> int:
    """
    Promote any 'Pending Background Check' authorizations for target_user when the
    user's background check is currently valid.
    - If KAO sign-off is enabled: move to 'Needs Kingdom Approval'
    - Otherwise: move to 'Active'
    Returns the number of records updated.
    """
    if not target_user.background_check_expiration or target_user.background_check_expiration < date.today():
        return 0

    pending_qs = Authorization.objects.filter(
        person__user=target_user,
        status__name='Pending Background Check',
    )
    count = pending_qs.count()
    if count == 0:
        return 0

    next_status = _get_or_create_status_by_name(
        'Needs Kingdom Approval' if authorization_officer_sign_off_enabled() else 'Active'
    )
    pending_qs.update(status=next_status)
    return count


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


def _build_search_csv_response(authorizations):
    """Export search table rows as CSV using current filters without pagination."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="authorizations_search.csv"'
    # UTF-8 BOM helps Excel detect Unicode correctly on Windows.
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([
        'SCA Name',
        'Region',
        'Branch',
        'Discipline',
        'Weapon Style',
        'Marshal',
        'Expiration',
        'Minor',
    ])
    for auth in authorizations:
        region_name = ''
        if auth.person.branch and auth.person.branch.region:
            region_name = auth.person.branch.region.name
        writer.writerow([
            auth.person.sca_name or '',
            region_name,
            auth.person.branch.name if auth.person.branch else '',
            auth.style.discipline.name if auth.style and auth.style.discipline else '',
            auth.style.name if auth.style else '',
            auth.marshal.sca_name if auth.marshal else '',
            auth.effective_expiration.isoformat() if auth.effective_expiration else '',
            auth.person.minor_status,
        ])
    return response


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
    is_current = request.GET.get('is_current')
    if is_current:
        dynamic_filter &= Q(effective_expiration_date__gte=date.today())
    else:
        start_date_raw = request.GET.get('start_date')
        start_date, start_invalid = _parse_search_date(start_date_raw)
        if start_invalid:
            invalid_query_params.add('start_date')
            messages.error(request, 'Start date must be in YYYY-MM-DD format.')
            logger.warning('Invalid start_date provided to search: %s', start_date_raw)
        if start_date:
            dynamic_filter &= Q(effective_expiration_date__gte=start_date)

        end_date_raw = request.GET.get('end_date')
        end_date, end_invalid = _parse_search_date(end_date_raw)
        if end_invalid:
            invalid_query_params.add('end_date')
            messages.error(request, 'End date must be in YYYY-MM-DD format.')
            logger.warning('Invalid end_date provided to search: %s', end_date_raw)
        if end_date:
            dynamic_filter &= Q(effective_expiration_date__lte=end_date)
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
    matching_authorizations = Authorization.objects.with_effective_expiration().with_sanction_flag().filter(
        dynamic_filter,
        has_active_sanction=False,
    ).exclude(person__user_id=11968)
    download_format = (request.GET.get('download') or '').strip().lower()

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
        if user_sort in ['expiration', '-expiration']:
            user_sort = user_sort.replace('expiration', 'effective_expiration_date')
        
        authorization_list = matching_authorizations.select_related(
            'person__branch__region',
            'person__title',
            'style__discipline',
            'status',
            'marshal'
        ).order_by(user_sort)

        if download_format == 'csv':
            return _build_search_csv_response(authorization_list)

        items_per_page = int(request.GET.get('items_per_page', 25))
        paginator = Paginator(authorization_list, items_per_page)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    # === STEP 4: RENDER THE TEMPLATE ===
    query_params = request.GET.copy()
    if 'page' in query_params:
        query_params.pop('page')
    if 'download' in query_params:
        query_params.pop('download')
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

    merged_user = User.objects.select_related('merged_into').filter(
        id=person_id,
        merged_into__isnull=False,
    ).first()
    if merged_user and merged_user.merged_into_id:
        messages.info(request, 'That fighter record was merged. Redirected to the current record.')
        return redirect('fighter', person_id=merged_user.merged_into_id)

    # Get the person who's card is being requested
    try:
        person = Person.objects.get(user_id=person_id)
    except Person.DoesNotExist:
        messages.error(request, 'Person not found.')
        return redirect('search')
    user = person.user
    auth_officer = is_kingdom_authorization_officer(request.user) if request.user.is_authenticated else False
    earl_marshal = is_kingdom_earl_marshal(request.user) if request.user.is_authenticated else False
    can_manage_sanctions = _can_access_sanctions(request.user) if request.user.is_authenticated else False
    has_active_marshal_office = BranchMarshal.objects.filter(
        person=person,
        end_date__gte=date.today(),
    ).exists()
    can_manage_marshal_offices = (
        can_manage_any_branch_marshal_office(request.user)
        and not has_active_marshal_office
    ) if request.user.is_authenticated else False
    can_manage_officer_comments = auth_officer or earl_marshal

    # If there is a post, confirm that they are authenticated.
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'send_login_instructions':
            ip_address = _get_client_ip(request)
            person_key = f"fighter-login-instructions:person:{person_id}"
            ip_key = f"fighter-login-instructions:ip:{ip_address}"
            sender_email = settings.DEFAULT_FROM_EMAIL
            fighter_login_window_seconds = _throttle_setting(
                'AUTHZ_FIGHTER_LOGIN_INSTRUCTIONS_WINDOW_SECONDS',
                prod_default=15 * 60,
                test_default=5 * 60,
            )
            fighter_login_person_limit = _throttle_setting(
                'AUTHZ_FIGHTER_LOGIN_INSTRUCTIONS_PERSON_LIMIT',
                prod_default=3,
                test_default=50,
            )
            fighter_login_ip_limit = _throttle_setting(
                'AUTHZ_FIGHTER_LOGIN_INSTRUCTIONS_IP_LIMIT',
                prod_default=5,
                test_default=100,
            )
            throttled = _throttle_request(person_key, fighter_login_person_limit, fighter_login_window_seconds) or \
                _throttle_request(ip_key, fighter_login_ip_limit, fighter_login_window_seconds)

            if not throttled:
                login_path = reverse('login')
                login_url = f"{settings.SITE_URL}{login_path}"
                reset_link = _build_password_reset_link(user)
                try:
                    send_mail(
                        'An Tir Authorization: Login Instructions',
                        f'Login instructions were requested for your fighter record.\n\n'
                        f'Username: {user.username}\n'
                        f'Password Reset Link: {reset_link}\n'
                        f'Login URL: {login_url}\n\n'
                        f'If you did not request this, you can ignore this email.',
                        sender_email,
                        [user.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception('Error sending fighter login instructions for user_id=%s', user.id)

            messages.success(
                request,
                f'Login instructions have been sent to the email on file. '
                f'Please check your spam folder for an email from {sender_email}. '
                f'If you did not receive the email please contact the Database Officer at {sender_email} to update your email.',
            )
            return redirect('fighter', person_id=person_id)

        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to perform this action.')
            return redirect('login')

        if action == 'add_authorization':
            return add_authorization(request, person_id)
        elif action == 'clear_pending_authorization':
            pending_key = f'pending_authorization_{person_id}'
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending marshal promotion cleared.')
            return redirect('fighter', person_id=person_id)
        elif action == 'clear_pending_authorization_action':
            pending_key = 'pending_authorization_action'
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending marshal promotion cleared.')
            return redirect('fighter', person_id=person_id)
        elif action == 'update_comments':
            if not can_manage_officer_comments:
                messages.error(request, 'You do not have permission to update comments.')
            else:
                note_text = (request.POST.get('comments') or '').strip()
                if not note_text:
                    messages.error(request, 'Please enter a note before submitting.')
                    return redirect('fighter', person_id=person_id)
                UserNote.objects.create(
                    person=person,
                    created_by=request.user,
                    note_type='officer_note',
                    note=note_text,
                )
                messages.success(request, 'Officer note added successfully.')
                return redirect('fighter', person_id=person_id)
        elif action == 'approve_authorization':
            authorization_id = request.POST.get('authorization_id')
            authorization = Authorization.objects.get(id=authorization_id)
            pending_key = 'pending_authorization_action'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and pending_action.get('action') == 'approve_authorization'
                and pending_action.get('authorization_id') == authorization.id
            )
            pending_submit_as_user_id = pending_action.get('submit_as_user_id') if is_pending_submit else None
            submit_as_user_id_raw = (request.POST.get('submit_as_user_id') or '').strip()
            if pending_submit_as_user_id and not submit_as_user_id_raw:
                mutable_post = request.POST.copy()
                mutable_post['submit_as_user_id'] = str(pending_submit_as_user_id)
                request.POST = mutable_post
            requires_note = _marshal_promotion_note_required_for_approval(authorization)
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required for marshal promotion actions.')
                    return redirect('fighter', person_id=person_id)
                submit_as_user, submit_as_error = _resolve_submit_as_user(request)
                if submit_as_error:
                    messages.error(request, submit_as_error)
                    return redirect('fighter', person_id=person_id)
                ok, msg = validate_approve_authorization(request.user, submit_as_user, authorization)
                if not ok:
                    messages.error(request, msg)
                    return redirect('fighter', person_id=person_id)
                request.session[pending_key] = {
                    'action': 'approve_authorization',
                    'authorization_id': authorization.id,
                    'submit_as_user_id': submit_as_user.id if submit_as_user else None,
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the marshal promotion.')
                return redirect('fighter', person_id=person_id)
            is_valid, mssg = approve_authorization(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
        elif action == 'appoint_branch_marshal':
            if has_active_marshal_office:
                messages.error(request, 'This fighter already has an active marshal officer appointment.')
                return redirect('fighter', person_id=person_id)
            is_valid, mssg = appoint_branch_marshal(request)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
        elif action == 'reject_authorization':
            # Check if the user has the authorization over this discipline
            auth_id = request.POST['bad_authorization_id']
            authorization = Authorization.objects.get(id=request.POST['bad_authorization_id'])
            pending_key = 'pending_authorization_action'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and pending_action.get('action') == 'reject_authorization'
                and pending_action.get('authorization_id') == authorization.id
            )
            pending_submit_as_user_id = pending_action.get('submit_as_user_id') if is_pending_submit else None
            submit_as_user_id_raw = (request.POST.get('submit_as_user_id') or '').strip()
            if pending_submit_as_user_id and not submit_as_user_id_raw:
                mutable_post = request.POST.copy()
                mutable_post['submit_as_user_id'] = str(pending_submit_as_user_id)
                request.POST = mutable_post
            requires_note = _note_required_for_rejection(authorization)
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required for this rejection.')
                    return redirect('fighter', person_id=person_id)
                if is_kingdom_review_status_name(authorization.status.name):
                    submit_as_user = request.user
                else:
                    submit_as_user, submit_as_error = _resolve_submit_as_user(request)
                    if submit_as_error:
                        messages.error(request, submit_as_error)
                        return redirect('fighter', person_id=person_id)
                ok, msg = validate_reject_authorization(submit_as_user, authorization)
                if not ok:
                    messages.error(request, msg)
                    return redirect('fighter', person_id=person_id)
                request.session[pending_key] = {
                    'action': 'reject_authorization',
                    'authorization_id': authorization.id,
                    'submit_as_user_id': (
                        submit_as_user.id
                        if submit_as_user and not is_kingdom_review_status_name(authorization.status.name)
                        else None
                    ),
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the rejection.')
                return redirect('fighter', person_id=person_id)
            is_valid, mssg = reject_authorization(request, authorization)
            if not is_valid:
                messages.error(request, mssg)
            else:
                messages.success(request, mssg)
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
        elif action == 'concur_authorization':
            authorization_id = request.POST.get('authorization_id')
            if not authorization_id:
                messages.error(request, 'Missing authorization to concur.')
                return redirect('fighter', person_id=person_id)
            try:
                authorization = Authorization.objects.select_related(
                    'person__user',
                    'style__discipline',
                    'marshal__user',
                ).get(id=authorization_id)
            except Authorization.DoesNotExist:
                messages.error(request, 'Authorization not found.')
                return redirect('fighter', person_id=person_id)

            if authorization.status.name != 'Needs Concurrence':
                messages.error(request, 'Authorization is not awaiting concurrence.')
                return redirect('fighter', person_id=person_id)

            submit_as_user, submit_as_error = _resolve_submit_as_user(request)
            if submit_as_error:
                messages.error(request, submit_as_error)
                return redirect('fighter', person_id=person_id)

            if not _can_concur_authorization(submit_as_user, authorization):
                messages.error(request, 'You do not have permission to concur with this authorization.')
                return redirect('fighter', person_id=person_id)

            pending_auths = Authorization.objects.select_related(
                'style__discipline',
            ).filter(
                person=authorization.person,
                style__discipline=authorization.style.discipline,
                status__name='Needs Concurrence',
            )

            if pending_auths.filter(marshal__user=submit_as_user).exists():
                messages.error(request, 'You cannot concur with an authorization you proposed.')
                return redirect('fighter', person_id=person_id)

            if pending_auths.filter(style__name__in=['Junior Marshal', 'Senior Marshal']).exists():
                messages.error(request, 'Marshal promotions do not use concurrence.')
                return redirect('fighter', person_id=person_id)

            try:
                concurring_person = Person.objects.get(user=submit_as_user)
            except Person.DoesNotExist:
                messages.error(request, 'Unable to find your fighter record.')
                return redirect('fighter', person_id=person_id)

            active_status = AuthorizationStatus.objects.get(name='Active')
            pending_waiver_status = AuthorizationStatus.objects.get(name='Pending Waiver')
            needs_kingdom_status = AuthorizationStatus.objects.get(name=KINGDOM_APPROVAL_STATUS)
            needs_kingdom_equestrian_waiver_status = AuthorizationStatus.objects.filter(
                name=KINGDOM_EQUESTRIAN_WAIVER_STATUS
            ).order_by('id').first()
            if not needs_kingdom_equestrian_waiver_status:
                needs_kingdom_equestrian_waiver_status = AuthorizationStatus.objects.create(
                    name=KINGDOM_EQUESTRIAN_WAIVER_STATUS
                )
            sign_off_required = authorization_officer_sign_off_enabled()

            def waiver_current(u):
                return bool(u.waiver_expiration and u.waiver_expiration > date.today())

            review_status_name = kingdom_review_status_name_for_style(authorization.style)
            if sign_off_required or review_status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS:
                status_to_set = (
                    needs_kingdom_equestrian_waiver_status
                    if review_status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS
                    else needs_kingdom_status
                )
            else:
                status_to_set = active_status if waiver_current(authorization.person.user) else pending_waiver_status

            new_expiration = calculate_authorization_expiration(authorization.person, authorization.style)
            for pending in pending_auths:
                pending.concurring_fighter = concurring_person
                pending.expiration = calculate_authorization_expiration(pending.person, pending.style)
                pending.status = status_to_set
                pending.updated_by = submit_as_user
                pending.save()

            if not sign_off_required and status_to_set == active_status:
                if (not authorization.person.user.waiver_expiration) or (authorization.person.user.waiver_expiration < new_expiration):
                    authorization.person.user.waiver_expiration = new_expiration
                    authorization.person.user.save()

            if status_to_set == needs_kingdom_equestrian_waiver_status:
                messages.success(request, 'Concurrence recorded. Authorization submitted for Kingdom equestrian waiver review.')
            elif sign_off_required:
                messages.success(request, 'Concurrence recorded. Authorization submitted to Kingdom for approval.')
            elif status_to_set == pending_waiver_status:
                messages.success(request, 'Concurrence recorded. Authorization pending waiver.')
            else:
                messages.success(request, 'Concurrence recorded. Authorization approved.')

    # Get the lists of authorizations
    authorization_list = Authorization.objects.effectively_active().select_related(
        'person__branch__region',
        'style__discipline',
        'concurring_fighter',
    ).filter(person_id=person_id).order_by('style__discipline__name', 'effective_expiration_date', 'style__name')

    pending_authorization_list = Authorization.objects.with_effective_expiration().select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(
        person_id=person_id,
        status__name__in=[
            'Pending',
            'Needs Regional Approval',
            KINGDOM_APPROVAL_STATUS,
            KINGDOM_EQUESTRIAN_WAIVER_STATUS,
            'Needs Concurrence',
            'Pending Background Check',
        ],
    ).order_by(
        'style__discipline__name', 'effective_expiration_date', 'style__name')

    pending_waiver_list = Authorization.objects.with_effective_expiration().select_related(
        'person__branch__region',
        'style__discipline',
    ).filter(person_id=person_id, status__name='Pending Waiver').order_by(
        'style__discipline__name', 'effective_expiration_date', 'style__name')

    can_view_notes = False
    visible_notes = []
    if request.user.is_authenticated:
        can_view_notes = (
            is_kingdom_authorization_officer(request.user)
            or is_kingdom_earl_marshal(request.user)
            or is_kingdom_marshal(request.user)
            or is_regional_marshal(request.user)
        )
        if can_view_notes:
            notes_qs = AuthorizationNote.objects.select_related(
                'authorization__style__discipline',
                'authorization__person__branch__region',
                'created_by__person',
            ).filter(authorization__person_id=person_id)
            for note in notes_qs:
                if _user_can_view_note(request.user, note):
                    visible_notes.append(note)

    sanctions_list = _active_sanctions_queryset().filter(person_id=person_id).order_by(
        'discipline__name', 'end_date', 'style__name'
    )

    # Group by discipline

    equestrian = False
    youth = False
    fighter = False

    grouped_authorizations = {}
    for auth in authorization_list:
        discipline_name = auth.style.discipline.name
        marshal_renewal = None
        marshal_renewal_requires_bg = False
        if auth.style.name in ['Junior Marshal', 'Senior Marshal'] and auth.effective_expiration < auth.expiration:
            marshal_renewal = auth.expiration
            if auth.style.discipline.name in ['Youth Armored', 'Youth Rapier']:
                marshal_renewal_requires_bg = True
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
                'marshal_name': auth.marshal.sca_name if auth.marshal else '',
                'concurring_fighter_name': auth.concurring_fighter.sca_name if auth.concurring_fighter else '',
                'earliest_expiration': auth.effective_expiration,
                'marshal_renewal': marshal_renewal,
                'marshal_renewal_requires_bg': marshal_renewal_requires_bg,
                'styles': [auth.style.name],
            }
        else:
            if auth.effective_expiration < grouped_authorizations[discipline_name]['earliest_expiration']:
                grouped_authorizations[discipline_name]['earliest_expiration'] = auth.effective_expiration
                grouped_authorizations[discipline_name]['marshal_name'] = auth.marshal.sca_name if auth.marshal else ''
                grouped_authorizations[discipline_name]['concurring_fighter_name'] = auth.concurring_fighter.sca_name if auth.concurring_fighter else ''
            if marshal_renewal:
                current_renewal = grouped_authorizations[discipline_name].get('marshal_renewal')
                if not current_renewal or marshal_renewal > current_renewal:
                    grouped_authorizations[discipline_name]['marshal_renewal'] = marshal_renewal
                if marshal_renewal_requires_bg:
                    grouped_authorizations[discipline_name]['marshal_renewal_requires_bg'] = True
            style_name = auth.style.name
            if style_name not in grouped_authorizations[discipline_name]['styles']:
                grouped_authorizations[discipline_name]['styles'].append(style_name)

    pending_authorizations = {}
    for auth in pending_authorization_list:
        discipline_name = auth.style.discipline.name
        can_concur = auth.status.name == 'Needs Concurrence' and (
            auth_officer or _can_concur_authorization(request.user, auth)
        )
        can_approve = False
        can_reject = False
        if request.user.is_authenticated:
            can_approve, _ = validate_approve_authorization(request.user, request.user, auth)
            if auth_officer:
                can_approve = True
            can_reject_ok, _ = validate_reject_authorization(request.user, auth)
            can_reject = auth.status.name in [
                'Pending',
                'Needs Regional Approval',
                KINGDOM_APPROVAL_STATUS,
                KINGDOM_EQUESTRIAN_WAIVER_STATUS,
            ] and can_reject_ok
            if auth_officer and auth.status.name in [
                'Pending',
                'Needs Regional Approval',
                KINGDOM_APPROVAL_STATUS,
                KINGDOM_EQUESTRIAN_WAIVER_STATUS,
            ]:
                can_reject = True

        if discipline_name not in pending_authorizations:
            pending_authorizations[discipline_name] = {
                'auth_id': auth.id,
                'marshal_name': auth.marshal.sca_name if auth.marshal else '',
                'earliest_expiration': auth.effective_expiration,
                'styles': [auth.style.name],
                'status': auth.status.name,
                'can_concur': can_concur,
                'can_approve': can_approve,
                'can_reject': can_reject,
            }
        else:
            if auth.effective_expiration < pending_authorizations[discipline_name]['earliest_expiration']:
                pending_authorizations[discipline_name]['earliest_expiration'] = auth.effective_expiration
                pending_authorizations[discipline_name]['marshal_name'] = auth.marshal.sca_name if auth.marshal else ''
            style_name = auth.style.name
            if style_name not in pending_authorizations[discipline_name]['styles']:
                pending_authorizations[discipline_name]['styles'].append(style_name)
            if auth.status.name == 'Needs Concurrence':
                pending_authorizations[discipline_name]['can_concur'] = (
                    pending_authorizations[discipline_name]['can_concur']
                    or auth_officer
                    or _can_concur_authorization(request.user, auth)
                )
            pending_authorizations[discipline_name]['can_approve'] = (
                pending_authorizations[discipline_name]['can_approve']
                or can_approve
            )
            pending_authorizations[discipline_name]['can_reject'] = (
                pending_authorizations[discipline_name]['can_reject']
                or can_reject
            )

    pending_waivers = {}
    for auth in pending_waiver_list:
        discipline_name = auth.style.discipline.name
        if discipline_name not in pending_waivers:
            pending_waivers[discipline_name] = {
                'auth_id': auth.id,
                'marshal_name': auth.marshal.sca_name if auth.marshal else '',
                'earliest_expiration': auth.effective_expiration,
                'styles': [auth.style.name],
                'status': auth.status.name
            }
        else:
            if auth.effective_expiration < pending_waivers[discipline_name]['earliest_expiration']:
                pending_waivers[discipline_name]['earliest_expiration'] = auth.effective_expiration
                pending_waivers[discipline_name]['marshal_name'] = auth.marshal.sca_name if auth.marshal else ''
            style_name = auth.style.name
            if style_name not in pending_waivers[discipline_name]['styles']:
                pending_waivers[discipline_name]['styles'].append(style_name)

    sanctions = {}
    for sanction in sanctions_list:
        discipline_name = sanction.discipline.name
        style_label = sanction.style.name if sanction.style else f'All {discipline_name} styles'
        if discipline_name not in sanctions:
            sanctions[discipline_name] = {
                'sanction_id': sanction.id,
                'start_date': sanction.start_date,
                'latest_sanction_end_date': sanction.end_date,
                'styles': [style_label],
            }
        else:
            if sanction.start_date < sanctions[discipline_name]['start_date']:
                sanctions[discipline_name]['start_date'] = sanction.start_date
            current_end_date = sanctions[discipline_name]['latest_sanction_end_date']
            if sanction.end_date and (current_end_date is None or sanction.end_date > current_end_date):
                sanctions[discipline_name]['latest_sanction_end_date'] = sanction.end_date
            if style_label not in sanctions[discipline_name]['styles']:
                sanctions[discipline_name]['styles'].append(style_label)

    if 'pdf' in request.GET:
        template_id = request.GET.get('template_id')
        try:
            return generate_fighter_card(request, person_id, template_id)
        except Exception as e:
            messages.error(request, 'You don\'t have the required authorizations to view this card.')
            return redirect('fighter', person_id=person_id)

    branch_officers = list(
        BranchMarshal.objects.filter(person=person, end_date__gte=date.today())
        .select_related('branch', 'discipline')
        .order_by('branch__name', 'discipline__name', 'end_date')
    )
    for office in branch_officers:
        effective_expiration = marshal_office_effective_expiration(office)
        office.effective_expiration = effective_expiration
        lower_than_warrant = bool(effective_expiration and effective_expiration < office.end_date)
        viewer_is_self = (
            request.user.is_authenticated
            and office.person
            and office.person.user_id == request.user.id
        )
        viewer_is_superior = _viewer_is_superior_for_office(request.user, office)
        office.show_effective_expiration = lower_than_warrant and (viewer_is_self or viewer_is_superior)

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
                'branch_officers': branch_officers,
                'sanctions': sanctions,
                'can_manage_sanctions': False,
                'can_manage_marshal_offices': False,
                'pending_waivers': pending_waivers,
                'today': date.today(),
            },
        )



    # All branches except for type = other
    branch_choices = Branch.objects.exclude(type='Other').order_by('name')
    discipline_choices = Discipline.objects.all().order_by('name')
    regional_marshal = is_regional_marshal(request.user)
    pending_note_required = False
    pending_concurring_fighter_user_id = None
    pending_key = f'pending_authorization_{person_id}'
    pending = _get_pending_session(request, pending_key)
    if pending and pending.get('person_id') == person_id:
        pending_style_ids = pending.get('style_ids', [])
        pending_concurring_fighter_user_id = pending.get('concurring_fighter_user_id')
        created_at = pending.get('created_at')
        if created_at:
            try:
                created_time = datetime.fromisoformat(created_at)
                if datetime.utcnow() - created_time > timedelta(hours=1):
                    del request.session[pending_key]
                    request.session.modified = True
                    pending = None
            except ValueError:
                del request.session[pending_key]
                request.session.modified = True
                pending = None
        if pending and pending_style_ids:
            marshal_style_ids = set(
                WeaponStyle.objects.filter(name__in=['Junior Marshal', 'Senior Marshal']).values_list('id', flat=True)
            )
            pending_note_required = any(int(style_id) in marshal_style_ids for style_id in pending_style_ids)
    pending_authorization_action = _get_pending_session(request, 'pending_authorization_action')
    selected_submit_as_user_id = None
    if pending_authorization_action and pending_authorization_action.get('action') in ['approve_authorization', 'reject_authorization']:
        selected_submit_as_user_id = pending_authorization_action.get('submit_as_user_id')

    officer_notes = []
    if can_manage_officer_comments:
        officer_notes = list(
            UserNote.objects.select_related('created_by__person')
            .filter(person=person, note_type='officer_note')
            .order_by('-created_at')
        )

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
            'is_marshal': is_senior_marshal(request.user) or auth_officer,
            'auth_form': CreateAuthorizationForm(user=request.user, show_all=auth_officer),
            'auth_officer': auth_officer,
            'can_manage_sanctions': can_manage_sanctions,
            'can_manage_marshal_offices': can_manage_marshal_offices,
            'can_manage_officer_comments': can_manage_officer_comments,
            'officer_notes': officer_notes,
            'branch_officers': branch_officers,
            'branch_choices': branch_choices,
            'discipline_choices': discipline_choices,
            'sanctions': sanctions,
            'regional_marshal': regional_marshal,
            'all_people': Person.objects.filter(user__merged_into__isnull=True).order_by('sca_name'),
            'pending_waivers': pending_waivers,
            'pending_note_required': pending_note_required,
            'pending_authorization_action': pending_authorization_action,
            'selected_submit_as_user_id': selected_submit_as_user_id,
            'can_view_notes': can_view_notes,
            'visible_notes': visible_notes,
            'today': date.today(),
            'pending_concurring_fighter_user_id': pending_concurring_fighter_user_id,
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
    authorization_list = Authorization.objects.effectively_active().select_related(
        'person__branch',
        'style__discipline',
    ).filter(
        person_id=person_id,
    ).order_by(
        'style__discipline__name',
        'effective_expiration_date', 'style__name')

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
    earliest_auth = authorization_list.order_by('effective_expiration_date').first()
    expiration = earliest_auth.effective_expiration if earliest_auth else None

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
            needs_kingdom_status = AuthorizationStatus.objects.get(name=KINGDOM_APPROVAL_STATUS)
            needs_kingdom_equestrian_waiver_status = AuthorizationStatus.objects.filter(
                name=KINGDOM_EQUESTRIAN_WAIVER_STATUS
            ).order_by('id').first()
            if not needs_kingdom_equestrian_waiver_status:
                needs_kingdom_equestrian_waiver_status = AuthorizationStatus.objects.create(
                    name=KINGDOM_EQUESTRIAN_WAIVER_STATUS
                )
            needs_concurrence_status = AuthorizationStatus.objects.filter(
                name='Needs Concurrence'
            ).order_by('id').first()
            if not needs_concurrence_status:
                needs_concurrence_status = AuthorizationStatus.objects.create(name='Needs Concurrence')
            sign_off_required = authorization_officer_sign_off_enabled()

            def kingdom_review_status_for_style(style: WeaponStyle):
                status_name = kingdom_review_status_name_for_style(style)
                if status_name == KINGDOM_EQUESTRIAN_WAIVER_STATUS:
                    return needs_kingdom_equestrian_waiver_status
                return needs_kingdom_status

            def routes_to_kingdom_review(style: WeaponStyle) -> bool:
                return sign_off_required or kingdom_review_status_name_for_style(style) == KINGDOM_EQUESTRIAN_WAIVER_STATUS

            pending_key = f'pending_authorization_{person_id}'
            is_pending_submit = request.POST.get('pending_authorization') == '1'
            pending_concurring_fighter_user_id = None
            if is_pending_submit:
                pending = _get_pending_session(request, pending_key)
                if not pending:
                    messages.error(request, 'Pending authorization not found. Please try again.')
                    return redirect('fighter', person_id=person_id)
                selected_styles = pending.get('style_ids', [])
                pending_concurring_fighter_user_id = pending.get('concurring_fighter_user_id')
                if not selected_styles:
                    messages.error(request, 'Pending authorization missing styles. Please try again.')
                    return redirect('fighter', person_id=person_id)
            else:
                sent_styles = request.POST.getlist('weapon_styles')
                selected_styles = sorted(set(sent_styles))

            # Optional KAO-only explicit concurrence when rule 25 concurrence is required.
            concurring_fighter_user_id_raw = (request.POST.get('concurring_fighter_id') or '').strip()
            if is_pending_submit and pending_concurring_fighter_user_id and not concurring_fighter_user_id_raw:
                concurring_fighter_user_id_raw = str(pending_concurring_fighter_user_id)
            if concurring_fighter_user_id_raw and not is_kingdom_authorization_officer(request.user):
                messages.error(request, 'You are not allowed to specify a concurring fighter.')
                return redirect('fighter', person_id=person_id)
            concurring_fighter = None
            if concurring_fighter_user_id_raw:
                try:
                    concurring_fighter_user_id = int(concurring_fighter_user_id_raw)
                except (TypeError, ValueError):
                    messages.error(request, 'Selected concurring fighter not found.')
                    return redirect('fighter', person_id=person_id)
                try:
                    concurring_user = User.objects.select_related('person').get(id=concurring_fighter_user_id)
                except User.DoesNotExist:
                    messages.error(request, 'Selected concurring fighter not found.')
                    return redirect('fighter', person_id=person_id)
                if not hasattr(concurring_user, 'person') or concurring_user.person is None:
                    messages.error(request, 'Selected concurring fighter has no fighter record.')
                    return redirect('fighter', person_id=person_id)
                concurring_fighter = concurring_user.person
            existing_authorizations = Authorization.objects.with_effective_expiration().filter(person=person)
            existing_by_style = {int(auth.style_id): auth for auth in existing_authorizations}
            marshal_style_ids = set(
                WeaponStyle.objects.filter(name__in=['Junior Marshal', 'Senior Marshal']).values_list('id', flat=True)
            )
            def marshal_note_required(style_id):
                style_id_int = int(style_id)
                if style_id_int not in marshal_style_ids:
                    return False
                auth = existing_by_style.get(style_id_int)
                if not auth:
                    return True
                status_name = auth.status.name if auth.status else None
                if status_name and status_name != 'Active':
                    return True
                effective_expiration = getattr(auth, 'effective_expiration_date', None) or auth.effective_expiration
                days_expired = (date.today() - effective_expiration).days
                return days_expired > 365

            concurrence_cache = {}

            def requires_concurrence(style: WeaponStyle) -> bool:
                if style.name in ['Junior Marshal', 'Senior Marshal']:
                    return False
                discipline_id = style.discipline_id
                if discipline_id not in concurrence_cache:
                    concurrence_cache[discipline_id] = authorization_requires_concurrence(person, style)
                return concurrence_cache[discipline_id]

            selected_style_ids = [int(style_id) for style_id in selected_styles]
            selected_style_map = WeaponStyle.objects.select_related('discipline').in_bulk(selected_style_ids)

            if concurring_fighter:
                if concurring_fighter.user_id == person.user_id:
                    messages.error(request, 'Concurring fighter must be different from the fighter receiving the authorization.')
                    return redirect('fighter', person_id=person_id)
                if concurring_fighter.user_id == authorizing_marshal.id:
                    messages.error(request, 'Concurring fighter must be different from the authorizing marshal.')
                    return redirect('fighter', person_id=person_id)
                for style_id in selected_style_ids:
                    style = selected_style_map.get(style_id)
                    if style and requires_concurrence(style):
                        if not is_authorized_in_discipline(concurring_fighter.user, style.discipline):
                            messages.error(
                                request,
                                f"{concurring_fighter.sca_name} is not currently authorized in {style.discipline.name} and cannot concur."
                            )
                            return redirect('fighter', person_id=person_id)

            requires_note = any(marshal_note_required(style_id) for style_id in selected_styles)
            action_note = _get_action_note(request) if requires_note else ''
            if requires_note and not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required when proposing a marshal promotion.')
                    return redirect('fighter', person_id=person_id)
                for style_id in selected_styles:
                    is_valid, mssg = authorization_follows_rules(
                        marshal=authorizing_marshal,
                        existing_fighter=person,
                        style_id=style_id,
                    )
                    if not is_valid:
                        messages.error(request, mssg)
                        return redirect('fighter', person_id=person_id)
                request.session[pending_key] = {
                    'person_id': person_id,
                    'style_ids': selected_styles,
                    'authorizing_marshal_id': authorizing_marshal.id,
                    'concurring_fighter_user_id': concurring_fighter.user_id if concurring_fighter else None,
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize the marshal promotion.')
                return redirect('fighter', person_id=person_id)

            def record_note(authorization, action):
                create_authorization_note(
                    authorization=authorization,
                    created_by=authorizing_marshal,
                    action=action,
                    note=action_note,
                )

            clear_pending_on_exit = bool(is_pending_submit and action_note)
            try:
                with transaction.atomic():
                    print(f"Debug: Starting authorization process for person {person_id}")
                    print(f"Debug: Selected styles: {selected_styles}")
                    print(f"Debug: Authorizing marshal: {authorizing_marshal.person.sca_name}")

                    # Create or update authorizations
                    current_styles = [int(auth.style_id) for auth in existing_authorizations]
                    print(f"Debug: Current authorizations: {current_styles}")
                    if is_pending_submit:
                        pending = _get_pending_session(request, pending_key)
                        if not pending:
                            messages.error(request, 'Pending authorization not found. Please try again.')
                            return redirect('fighter', person_id=person_id)
                        pending_marshal_id = pending.get('authorizing_marshal_id')
                        if pending_marshal_id:
                            try:
                                authorizing_marshal = User.objects.get(id=pending_marshal_id)
                            except User.DoesNotExist:
                                messages.error(request, 'Selected authorizing marshal not found.')
                                return redirect('fighter', person_id=person_id)
                        pending_concurring_fighter_user_id = pending.get('concurring_fighter_user_id')
                        if pending_concurring_fighter_user_id and not concurring_fighter:
                            try:
                                pending_concurring_user = User.objects.select_related('person').get(
                                    id=int(pending_concurring_fighter_user_id)
                                )
                            except (User.DoesNotExist, TypeError, ValueError):
                                messages.error(request, 'Selected concurring fighter not found.')
                                return redirect('fighter', person_id=person_id)
                            if not hasattr(pending_concurring_user, 'person') or pending_concurring_user.person is None:
                                messages.error(request, 'Selected concurring fighter has no fighter record.')
                                return redirect('fighter', person_id=person_id)
                            concurring_fighter = pending_concurring_user.person
                        if concurring_fighter and concurring_fighter.user_id == authorizing_marshal.id:
                            messages.error(request, 'Concurring fighter must be different from the authorizing marshal.')
                            return redirect('fighter', person_id=person_id)
                    
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
                                    days_expired = (date.today() - update_auth.effective_expiration).days
                                    is_rejected = update_auth.status and update_auth.status.name == 'Rejected'
                                    if is_rejected or days_expired > 365:  # Treat rejected like long-expired
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
                                    if requires_concurrence(update_auth.style):
                                        if concurring_fighter:
                                            update_auth.concurring_fighter = concurring_fighter
                                            if routes_to_kingdom_review(update_auth.style):
                                                update_auth.status = kingdom_review_status_for_style(update_auth.style)
                                                if update_auth.status == needs_kingdom_equestrian_waiver_status:
                                                    messages.success(
                                                        request,
                                                        f'Concurrence recorded for {update_auth.style.name}. Authorization submitted for Kingdom equestrian waiver review.'
                                                    )
                                                else:
                                                    messages.success(
                                                        request,
                                                        f'Concurrence recorded for {update_auth.style.name}. Authorization submitted to Kingdom for approval.'
                                                    )
                                            else:
                                                update_auth.status = active_status if waiver_current(person.user) else pending_waiver_status
                                                if update_auth.status == active_status:
                                                    messages.success(
                                                        request,
                                                        f'Existing authorization for {update_auth.style.name} updated successfully with concurrence.'
                                                    )
                                                else:
                                                    messages.success(
                                                        request,
                                                        f'Existing authorization for {update_auth.style.name} pending waiver after concurrence.'
                                                    )
                                        else:
                                            update_auth.status = needs_concurrence_status
                                            update_auth.concurring_fighter = None
                                            messages.success(request, f'Authorization for {update_auth.style.name} requires concurrence from another authorized fighter.')
                                    else:
                                        update_auth.concurring_fighter = None
                                        if routes_to_kingdom_review(update_auth.style):
                                            update_auth.status = kingdom_review_status_for_style(update_auth.style)
                                            if update_auth.status == needs_kingdom_equestrian_waiver_status:
                                                messages.success(
                                                    request,
                                                    f'Authorization for {update_auth.style.name} submitted for Kingdom equestrian waiver review.'
                                                )
                                            else:
                                                messages.success(
                                                    request,
                                                    f'Authorization for {update_auth.style.name} submitted to Kingdom for approval.'
                                                )
                                        else:
                                            update_auth.status = active_status if waiver_current(person.user) else pending_waiver_status
                                            if update_auth.status == active_status:
                                                messages.success(request, f'Existing authorization for {update_auth.style.name} updated successfully!')
                                            else:
                                                messages.success(request, f'Existing authorization for {update_auth.style.name} pending waiver.')
                                
                                update_auth.expiration = calculate_authorization_expiration(person, update_auth.style)
                                update_auth.updated_by = authorizing_marshal
                                update_auth.save()
                                if update_auth.style.name in ['Junior Marshal', 'Senior Marshal']:
                                    record_note(update_auth, 'marshal_proposed')
                                selected_styles.remove(style_id)

                            else:
                                style = WeaponStyle.objects.get(id=style_id)
                                if style.name in ['Senior Marshal', 'Junior Marshal']:
                                    expiration = calculate_authorization_expiration(person, style)
                                    new_auth = Authorization.objects.create(
                                        person=person,
                                        style=style,
                                        expiration=expiration,
                                        marshal=Person.objects.get(user=authorizing_marshal),
                                        status=AuthorizationStatus.objects.get(name='Pending'),
                                        created_by=authorizing_marshal,
                                        updated_by=authorizing_marshal,
                                    )
                                    record_note(new_auth, 'marshal_proposed')
                                    messages.success(request, f'Authorization for {style.name} pending confirmation.')
                                else:
                                    expiration = calculate_authorization_expiration(person, style)
                                    if requires_concurrence(style):
                                        if concurring_fighter:
                                            if routes_to_kingdom_review(style):
                                                status_to_set = kingdom_review_status_for_style(style)
                                                if status_to_set == needs_kingdom_equestrian_waiver_status:
                                                    success_message = (
                                                        f'Concurrence recorded for {style.name}. '
                                                        'Authorization submitted for Kingdom equestrian waiver review.'
                                                    )
                                                else:
                                                    success_message = (
                                                        f'Concurrence recorded for {style.name}. '
                                                        'Authorization submitted to Kingdom for approval.'
                                                    )
                                            else:
                                                status_to_set = active_status if waiver_current(person.user) else pending_waiver_status
                                                if status_to_set == active_status:
                                                    success_message = f'Authorization for {style.name} created successfully with concurrence.'
                                                else:
                                                    success_message = f'Authorization for {style.name} pending waiver after concurrence.'
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=status_to_set,
                                                concurring_fighter=concurring_fighter,
                                                created_by=authorizing_marshal,
                                                updated_by=authorizing_marshal,
                                            )
                                            if status_to_set == active_status:
                                                if (not person.user.waiver_expiration) or (person.user.waiver_expiration < expiration):
                                                    person.user.waiver_expiration = expiration
                                                    person.user.save()
                                            messages.success(request, success_message)
                                        else:
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=needs_concurrence_status,
                                                concurring_fighter=None,
                                                created_by=authorizing_marshal,
                                                updated_by=authorizing_marshal,
                                            )
                                            messages.success(request, f'Authorization for {style.name} requires concurrence from another authorized fighter.')
                                    else:
                                        if routes_to_kingdom_review(style):
                                            status_to_set = kingdom_review_status_for_style(style)
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=status_to_set,
                                                created_by=authorizing_marshal,
                                                updated_by=authorizing_marshal,
                                            )
                                            if status_to_set == needs_kingdom_equestrian_waiver_status:
                                                messages.success(
                                                    request,
                                                    f'Authorization for {style.name} submitted for Kingdom equestrian waiver review.'
                                                )
                                            else:
                                                messages.success(request, f'Authorization for {style.name} submitted to Kingdom for approval.')
                                        else:
                                            status_to_set = active_status if waiver_current(person.user) else pending_waiver_status
                                            new_auth = Authorization.objects.create(
                                                person=person,
                                                style=style,
                                                expiration=expiration,
                                                marshal=Person.objects.get(user=authorizing_marshal),
                                                status=status_to_set,
                                                created_by=authorizing_marshal,
                                                updated_by=authorizing_marshal,
                                            )
                                            if status_to_set == active_status:
                                                messages.success(request, f'Authorization for {style.name} created successfully!')
                                            else:
                                                messages.success(request, f'Authorization for {style.name} pending waiver.')
                                        # Only push waiver when the new auth is Active
                                        if status_to_set == active_status:
                                            if (not person.user.waiver_expiration) or (person.user.waiver_expiration < expiration):
                                                person.user.waiver_expiration = expiration
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
            finally:
                if clear_pending_on_exit and pending_key in request.session:
                    del request.session[pending_key]
                    request.session.modified = True
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

        if action == 'upload_supporting_document':
            next_url = reverse('user_account', kwargs={'user_id': user_id})
            ok, message = _handle_supporting_document_upload(
                request,
                default_person=person,
                next_url=next_url,
            )
            if ok:
                messages.success(request, message)
            else:
                messages.error(request, message)
            return redirect(next_url)

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
                        expiration = calculate_authorization_expiration(person, style)
                        if not membership_is_current(person.user):
                            messages.error(request, 'Marshal authorizations require a current membership.')
                            return redirect('user_account', user_id=user.id)
                        status = active_status
                    else:
                        expiration = calculate_authorization_expiration(person, style)

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

            if discipline.name == 'Earl Marshal' and not branch.is_region():
                messages.error(request, 'Earl Marshal offices may only be appointed at regional or kingdom level.')
                return redirect('user_account', user_id=user_id)

            if action == 'self_set_regional':
                # Validate requirements only for setting (not removing)
                # Skip Senior Marshal requirement for Authorization Officer discipline
                if discipline.name != 'Authorization Officer':
                    if branch.type in ['Kingdom', 'Principality', 'Region']:
                        # Regional/kingdom appointment requires Senior Marshal
                        has_required = Authorization.objects.effectively_active().filter(
                            person=person,
                            style__name='Senior Marshal',
                            style__discipline=discipline,
                        ).exists()
                        if not has_required:
                            messages.error(request, f'You must hold an active Senior Marshal in {discipline.name}.')
                            return redirect('user_account', user_id=user_id)
                    else:
                        # Local branch appointment allows Junior or Senior Marshal
                        has_required = Authorization.objects.effectively_active().filter(
                            person=person,
                            style__name__in=['Junior Marshal', 'Senior Marshal'],
                            style__discipline=discipline,
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
        allow_membership_mismatch_bypass = (
            is_kingdom_authorization_officer(requestor)
            and (request.POST.get('membership_validation_bypass') == 'on')
        )
        membership_validation_note = (request.POST.get('membership_validation_note') or '').strip()
        form = CreatePersonForm(
            request.POST,
            user_instance=user,
            request=request,
            allow_membership_mismatch_bypass=allow_membership_mismatch_bypass,
        )
        form_valid = form.is_valid()
        if form_valid and form.membership_mismatch_bypass_used and not membership_validation_note:
            form.add_error(None, 'A bypass note is required when overriding membership validation.')
            form_valid = False
        if form_valid:
            previous_membership = user.membership
            previous_membership_expiration = user.membership_expiration
            previous_background_check_expiration = user.background_check_expiration
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
            if (
                is_kingdom_authorization_officer(request.user)
                and user.background_check_expiration
                and user.background_check_expiration != previous_background_check_expiration
            ):
                _activate_pending_background_check_authorizations(user)

            person.sca_name = form.cleaned_data['sca_name']
            person.title = form.cleaned_data['title']
            person.branch = form.cleaned_data['branch']
            person.is_minor = form.cleaned_data['is_minor']
            person.parent = form.cleaned_data.get('parent_id')
            person.save()

            if form.membership_mismatch_bypass_used:
                UserNote.objects.create(
                    person=person,
                    created_by=request.user,
                    note_type='officer_note',
                    note=(
                        'Membership validation bypass applied by Kingdom Authorization Officer.\n'
                        f'Reason: {membership_validation_note}\n'
                        f'Previous membership: {previous_membership or "-"}\n'
                        f'Previous expiration: {previous_membership_expiration or "-"}\n'
                        f'New membership: {user.membership or "-"}\n'
                        f'New expiration: {user.membership_expiration or "-"}'
                    ),
                )

            messages.success(request, 'Your information has been updated successfully.')
            return redirect('index')
        else:
            messages.error(request, 'Please correct the errors with the form.')
            for field, errors in form.errors.items():
                for error in errors:
                    if field == '__all__':
                        messages.error(request, error)
                    else:
                        field_label = form.fields[field].label if field in form.fields else field
                        messages.error(request, f"{field_label}: {error}")
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

    membership_roster_import = MembershipRosterImport.objects.first()
    recent_supporting_documents = list(
        SupportingDocument.objects.filter(
            person_links__person=person,
        ).select_related(
            'uploaded_by__person',
        ).prefetch_related(
            'person_links__person',
            'authorization_links__authorization__style__discipline',
            'authorization_links__authorization__status',
        ).order_by('-uploaded_at').distinct()[:10]
    )
    can_upload_equestrian_for_anyone = (
        is_kingdom_authorization_officer(request.user)
        or is_senior_marshal(request.user, 'Equestrian')
    )

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
        'all_people': Person.objects.filter(user__merged_into__isnull=True).order_by('sca_name'),
        # Branch choices for self-appointment (exclude Other type)
        'branch_choices': Branch.objects.exclude(type='Other').order_by('name'),
        'discipline_choices': Discipline.objects.order_by('name'),
        'testing': testing,
        'membership_roster_import': membership_roster_import,
        'supporting_document_type_choices': SupportingDocument.DocumentType.choices,
        'supporting_document_jurisdiction_choices': SupportingDocument.Jurisdiction.choices,
        'recent_supporting_documents': recent_supporting_documents,
        'can_upload_equestrian_for_anyone': can_upload_equestrian_for_anyone,
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
    if is_kingdom_review_status_name(authorization.status.name):
        submit_as_user = request.user
    else:
        submit_as_user, submit_as_error = _resolve_submit_as_user(request)
        if submit_as_error:
            return False, submit_as_error
    requires_note = _note_required_for_rejection(authorization)
    action_note = _get_action_note(request) if requires_note else ''
    if requires_note and not action_note:
        return False, 'A note is required for this rejection.'
    if requires_note and request.user.id != submit_as_user.id:
        actor_name = submit_as_user.person.sca_name if hasattr(submit_as_user, 'person') and submit_as_user.person else submit_as_user.username
        requester_name = request.user.person.sca_name if hasattr(request.user, 'person') and request.user.person else request.user.username
        action_note = (
            f'{action_note}\n\n'
            f'Submitted as {actor_name} by {requester_name}.'
        )
    ok, msg = validate_reject_authorization(submit_as_user, authorization)
    if not ok:
        return False, msg
    rejected_status = AuthorizationStatus.objects.filter(name='Rejected').order_by('id').first()
    if not rejected_status:
        rejected_status = AuthorizationStatus.objects.create(name='Rejected')
    authorization.status = rejected_status
    authorization.updated_by = submit_as_user
    authorization.save()
    if requires_note:
        create_authorization_note(
            authorization=authorization,
            created_by=submit_as_user,
            action='marshal_rejected',
            note=action_note,
        )
    return True, 'Authorization rejected.'


@login_required
def upload_membership_roster(request):
    if not is_kingdom_authorization_officer(request.user):
        raise PermissionDenied
    if request.method != 'POST':
        return redirect('index')

    next_url = request.POST.get('next') or reverse('index')
    form = MembershipRosterUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, 'Please choose a CSV file to upload.')
        return redirect(next_url)

    uploaded_file = form.cleaned_data['membership_csv']
    try:
        rows, skipped_rows = _load_membership_rows_from_upload(uploaded_file)
    except ValueError as exc:
        messages.error(request, f'Roster upload failed: {exc}')
        return redirect(next_url)

    with transaction.atomic():
        MembershipRosterEntry.objects.all().delete()
        MembershipRosterEntry.objects.bulk_create(rows, batch_size=1000)
        MembershipRosterImport.objects.update_or_create(
            pk=1,
            defaults={
                'source_filename': uploaded_file.name,
                'imported_by': request.user,
                'row_count': len(rows),
            },
        )

    messages.success(request, f'Membership roster updated successfully ({len(rows)} rows).')
    if skipped_rows:
        messages.warning(
            request,
            f'{skipped_rows} row(s) were skipped because required membership fields were missing or invalid.',
        )
    return redirect(next_url)


def supporting_documents(request):
    if not request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        if not hasattr(request.user, 'person'):
            messages.error(request, 'Your account is missing a fighter profile. Please contact support.')
            return redirect('supporting_documents')
        if request.POST.get('action') == 'upload_supporting_document':
            ok, message = _handle_supporting_document_upload(
                request,
                default_person=request.user.person,
                next_url=reverse('supporting_documents'),
            )
            if ok:
                messages.success(request, message)
            else:
                messages.error(request, message)
            return redirect('supporting_documents')
        return redirect('supporting_documents')

    can_view_all_documents = _can_view_all_supporting_documents(request.user)
    documents = _supporting_documents_queryset_for_viewer(request.user)

    selected_sca_name = (request.GET.get('sca_name') or '').strip()
    selected_review_status = (request.GET.get('review_status') or '').strip()
    requested_document_type = (request.GET.get('document_type') or '').strip()

    allowed_document_types = {value for value, _label in SupportingDocument.DocumentType.choices}
    allowed_review_statuses = {value for value, _label in SupportingDocument.ReviewStatus.choices}
    selected_document_type = requested_document_type if requested_document_type in allowed_document_types else ''

    if selected_sca_name:
        documents = documents.filter(person_links__person__sca_name=selected_sca_name)
    if selected_review_status in allowed_review_statuses:
        documents = documents.filter(review_status=selected_review_status)
    if selected_document_type:
        documents = documents.filter(document_type=selected_document_type)

    documents = documents.distinct()
    documents = list(documents)
    for document in documents:
        document.file_available = _supporting_document_file_exists(document)
    document_people = (
        Person.objects.filter(
            user__merged_into__isnull=True,
            supporting_document_links__document__in=documents,
        )
        .order_by('sca_name')
        .values_list('sca_name', flat=True)
        .distinct()
    )

    context = {
        'documents': documents,
        'document_people': document_people,
        'document_type_choices': SupportingDocument.DocumentType.choices,
        'review_status_choices': SupportingDocument.ReviewStatus.choices,
        'selected_sca_name': selected_sca_name,
        'selected_document_type': selected_document_type,
        'selected_review_status': selected_review_status,
        'can_view_all_documents': can_view_all_documents,
        'is_authenticated': request.user.is_authenticated,
        'can_upload_supporting_documents': request.user.is_authenticated and hasattr(request.user, 'person'),
        'can_upload_equestrian_for_anyone': (
            request.user.is_authenticated and (
                is_kingdom_authorization_officer(request.user)
                or is_senior_marshal(request.user, 'Equestrian')
            )
        ),
        'supporting_document_type_choices': SupportingDocument.DocumentType.choices,
        'supporting_document_jurisdiction_choices': SupportingDocument.Jurisdiction.choices,
        'upload_person': request.user.person if request.user.is_authenticated and hasattr(request.user, 'person') else None,
        'all_people': Person.objects.filter(user__merged_into__isnull=True).order_by('sca_name')
        if request.user.is_authenticated
        else [],
    }
    return render(request, 'authorizations/supporting_documents.html', context)


@login_required
def supporting_document_file(request, document_id):
    document = get_object_or_404(
        SupportingDocument.objects.select_related('uploaded_by__person'),
        id=document_id,
    )
    if not _can_view_supporting_document(request.user, document):
        messages.warning(request, 'You do not have authority to view that document.')
        return redirect('index')
    if not _supporting_document_file_exists(document):
        messages.warning(request, 'That supporting document file was not found.')
        return redirect('index')

    try:
        file_handle = document.file.open('rb')
    except FileNotFoundError:
        messages.warning(request, 'That supporting document file was not found.')
        return redirect('index')

    filename = os.path.basename(document.file.name)
    return FileResponse(file_handle, as_attachment=False, filename=filename)


@login_required
def manage_sanctions(request):
    """
    Handles displaying the sanctions search form, and showing the results
    in either a table or a card view grouped by person.
    """
    if not _can_access_sanctions(request.user):
        raise PermissionDenied
    sanctions_supervisor = _is_sanctions_supervisor(request.user)
    allowed_disciplines = _sanctionable_disciplines_for_user(request.user)
    allowed_discipline_ids = list(allowed_disciplines.values_list('id', flat=True))

    # Handle POST requests first to lift sanctions.
    if request.method == 'POST':
        return_url = request.POST.get('return_url')
        if request.POST.get('action') == 'clear_pending_sanction_lift':
            pending_key = 'pending_sanction_lift'
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending sanction action cleared.')
            return redirect(return_url or 'manage_sanctions')
        if request.POST.get('action') == 'clear_pending_sanction_extend':
            pending_key = 'pending_sanction_extend'
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending sanction action cleared.')
            return redirect(return_url or 'manage_sanctions')

        # We'll use the 'action' to make sure we're lifting a sanction.
        if request.POST.get('action') == 'lift_sanction':
            sanction_id = request.POST.get('sanction_id')
            pending_key = 'pending_sanction_lift'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and str(pending_action.get('sanction_id')) == str(sanction_id)
            )
            action_note = _get_action_note(request)
            if not action_note:
                if is_pending_submit:
                    messages.error(request, 'A note is required to lift a sanction.')
                    return redirect('manage_sanctions')
                sanction = _active_sanctions_queryset().filter(id=sanction_id).first()
                if not sanction:
                    messages.error(request, "Could not find the specified sanction to lift.")
                    return redirect('manage_sanctions')
                if not _can_manage_sanctions_for_discipline(request.user, sanction.discipline):
                    messages.error(request, 'You do not have permission to manage this sanction.')
                    return redirect('manage_sanctions')
                request.session[pending_key] = {
                    'sanction_id': sanction_id,
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note to finalize lifting the sanction.')
                return redirect(return_url or 'manage_sanctions')
            try:
                sanction = _active_sanctions_queryset().get(id=sanction_id)
                if not _can_manage_sanctions_for_discipline(request.user, sanction.discipline):
                    messages.error(request, 'You do not have permission to manage this sanction.')
                    return redirect('manage_sanctions')
                sanction.lifted_at = timezone.now()
                sanction.lifted_by = request.user
                sanction.lift_note = action_note
                sanction.updated_by = request.user
                sanction.save(update_fields=['lifted_at', 'lifted_by', 'lift_note', 'updated_by', 'updated_at'])
                messages.success(request, f"Sanction for {sanction.person.sca_name} has been lifted.")
            except Sanction.DoesNotExist:
                messages.error(request, "Could not find the specified sanction to lift.")
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
        if request.POST.get('action') == 'extend_sanction':
            sanction_id = request.POST.get('sanction_id')
            pending_key = 'pending_sanction_extend'
            pending_action = _get_pending_session(request, pending_key)
            is_pending_submit = bool(
                pending_action
                and str(pending_action.get('sanction_id')) == str(sanction_id)
            )
            action_note = _get_action_note(request)
            sanction_end_date_raw = request.POST.get('sanction_end_date')
            if not action_note or not (sanction_end_date_raw or '').strip():
                if is_pending_submit:
                    if not action_note:
                        messages.error(request, 'A note is required to extend a sanction.')
                    else:
                        messages.error(request, 'Please select a sanction end date.')
                    return redirect(return_url or 'manage_sanctions')
                sanction = _active_sanctions_queryset().filter(id=sanction_id).first()
                if not sanction:
                    messages.error(request, "Could not find the specified sanction to extend.")
                    return redirect(return_url or 'manage_sanctions')
                if not _can_manage_sanctions_for_discipline(request.user, sanction.discipline):
                    messages.error(request, 'You do not have permission to manage this sanction.')
                    return redirect(return_url or 'manage_sanctions')
                request.session[pending_key] = {
                    'sanction_id': sanction_id,
                    'sanction_end_date': sanction.end_date.isoformat(),
                    'created_at': datetime.utcnow().isoformat(),
                }
                request.session.modified = True
                messages.info(request, 'Eligibility verified. Please add a note and end date to finalize extending the sanction.')
                return redirect(return_url or 'manage_sanctions')
            try:
                sanction = _active_sanctions_queryset().get(id=sanction_id)
                if not _can_manage_sanctions_for_discipline(request.user, sanction.discipline):
                    messages.error(request, 'You do not have permission to manage this sanction.')
                    return redirect(return_url or 'manage_sanctions')
                ok, message, sanction_end_date, warning_message = _normalize_sanction_end_date(
                    request.user,
                    sanction.discipline,
                    sanction_end_date_raw,
                )
                if not ok:
                    messages.error(request, message)
                    return redirect(return_url or 'manage_sanctions')
                sanction.end_date = sanction_end_date
                sanction.issue_note = _sanction_extension_note(
                    sanction.issue_note,
                    action_note,
                    sanction_end_date,
                    request.user,
                )
                sanction.issued_by = request.user
                sanction.updated_by = request.user
                sanction.save(update_fields=['end_date', 'issue_note', 'issued_by', 'updated_by', 'updated_at'])
                messages.success(request, f"Sanction for {sanction.person.sca_name} has been extended.")
                if warning_message:
                    messages.warning(request, warning_message)
            except Sanction.DoesNotExist:
                messages.error(request, "Could not find the specified sanction to extend.")
            if is_pending_submit and pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
        
        # Redirect after POST to prevent re-submission on refresh
        return redirect(return_url or 'manage_sanctions')

    # --- Display Logic (for GET requests) ---

    # Get dropdown options for the search form
    active_sanctions = _active_sanctions_queryset()
    if not sanctions_supervisor:
        if not allowed_discipline_ids:
            active_sanctions = active_sanctions.none()
        else:
            active_sanctions = active_sanctions.filter(discipline_id__in=allowed_discipline_ids)
    sca_name_options = Person.objects.filter(sanctions__in=active_sanctions).distinct().order_by('sca_name').values_list('sca_name', flat=True)
    discipline_options = Discipline.objects.filter(sanction__in=active_sanctions).distinct().order_by('name').values_list('name', flat=True)
    style_options = WeaponStyle.objects.filter(sanction__in=active_sanctions, sanction__style__isnull=False).distinct().order_by('name').values_list('name', flat=True)

    # Check if the user is requesting the search form page
    if request.GET.get('goal') == 'search':
        context = {
            'sca_name_options': sca_name_options,
            'discipline_options': discipline_options,
            'style_options': style_options,
        }
        return render(request, 'authorizations/sanctions_search_form.html', context)

    # --- If not the search goal, proceed with showing results ---
    
    dynamic_filter = Q()
    if sca_name := request.GET.get('sca_name'):
        dynamic_filter &= Q(person__sca_name=sca_name)
    if discipline := request.GET.get('discipline'):
        dynamic_filter &= Q(discipline__name=discipline)
    if style := request.GET.get('style'):
        dynamic_filter &= Q(style__name=style)

    matching_sanctions = active_sanctions.filter(dynamic_filter).exclude(person__user_id=11968)
    if not sanctions_supervisor:
        if not allowed_discipline_ids:
            matching_sanctions = matching_sanctions.none()
        else:
            matching_sanctions = matching_sanctions.filter(discipline_id__in=allowed_discipline_ids)
    view_mode = request.GET.get('view', 'table')
    page_obj = None

    if view_mode == 'card':
        # CARD VIEW: Paginate by Person
        person_ids = matching_sanctions.values_list('person_id', flat=True).distinct()
        sanctions_prefetch = Prefetch(
            'sanctions',
            queryset=matching_sanctions.order_by('discipline__name', 'style__name', 'end_date'),
            to_attr='active_sanctions'
        )
        people_list = Person.objects.filter(user_id__in=person_ids).prefetch_related(sanctions_prefetch).order_by('sca_name')
        
        paginator = Paginator(people_list, 10) # Fewer items per page for cards
        page_obj = paginator.get_page(request.GET.get('page', 1))

    else: # 'table' view is the default
        # TABLE VIEW: Paginate by Sanction
        sanctions_list = matching_sanctions.order_by('person__sca_name', 'discipline__name', 'style__name')
        
        paginator = Paginator(sanctions_list, 25)
        page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_obj': page_obj,
        'view_mode': view_mode,
        'pending_sanction_lift': _get_pending_session(request, 'pending_sanction_lift'),
        'pending_sanction_extend': _get_pending_session(request, 'pending_sanction_extend'),
        'today': date.today(),
    }
    return render(request, 'authorizations/manage_sanctions.html', context)

@login_required()
def issue_sanctions(request, person_id):
    """Allows eligible kingdom marshals to issue sanctions by discipline scope."""
    if not _can_access_sanctions(request.user):
        raise PermissionDenied

    person = get_object_or_404(Person, user_id=person_id)

    all_disciplines = _sanctionable_disciplines_for_user(request.user)

    discipline = None
    styles = []
    discipline_name = request.GET.get('discipline')
    if discipline_name:
        selected_discipline = Discipline.objects.filter(id=discipline_name).first() or Discipline.objects.filter(name=discipline_name).first()
        if selected_discipline and _can_manage_sanctions_for_discipline(request.user, selected_discipline):
            discipline = selected_discipline
            styles = WeaponStyle.objects.filter(discipline=discipline)

    pending_key = f'pending_sanction_issue_{person_id}'
    pending_sanction_issue = _get_pending_session(request, pending_key)
    pending_style_id = None
    pending_sanction_end_date = None
    if pending_sanction_issue:
        discipline_id = pending_sanction_issue.get('discipline_id')
        if discipline_id:
            pending_discipline = Discipline.objects.filter(id=discipline_id).first()
            if pending_discipline and _can_manage_sanctions_for_discipline(request.user, pending_discipline):
                discipline = pending_discipline
                styles = WeaponStyle.objects.filter(discipline=discipline)
        pending_style_id = pending_sanction_issue.get('style_id')
        pending_sanction_end_date = pending_sanction_issue.get('sanction_end_date')

    if request.method == 'POST':
        return_url = request.POST.get('return_url')
        if request.POST.get('action') == 'clear_pending_sanction_issue':
            if pending_key in request.session:
                del request.session[pending_key]
                request.session.modified = True
            messages.info(request, 'Pending sanction action cleared.')
            if return_url:
                return redirect(return_url)
            return redirect('issue_sanctions', person_id=person_id)

        is_pending_submit = request.POST.get('pending_sanction_issue') == '1'
        action_note = _get_action_note(request)
        if not action_note:
            if is_pending_submit:
                messages.error(request, 'A note is required to issue a sanction.')
                return redirect('issue_sanctions', person_id=person_id)
            sanction_request, message = _prepare_sanction_request(request.user, request.POST)
            if not sanction_request:
                messages.error(request, message)
                if return_url:
                    return redirect(return_url)
                return redirect('issue_sanctions', person_id=person_id)
            request.session[pending_key] = {
                'person_id': person_id,
                'sanction_type': sanction_request['sanction_type'],
                'discipline_id': sanction_request['discipline'].id if sanction_request['discipline'] else '',
                'style_id': sanction_request['style'].id if sanction_request['style'] else '',
                'sanction_end_date': sanction_request['sanction_end_date'].isoformat(),
                'warning_message': sanction_request['warning_message'] or '',
                'created_at': datetime.utcnow().isoformat(),
            }
            request.session.modified = True
            if sanction_request['warning_message']:
                messages.warning(request, sanction_request['warning_message'])
            messages.info(request, 'Eligibility verified. Please add a note to finalize the sanction.')
            if return_url:
                return redirect(return_url)
            return redirect('issue_sanctions', person_id=person_id)

        if is_pending_submit and pending_sanction_issue:
            post_data = request.POST.copy()
            for field in ['sanction_type', 'discipline_id', 'style_id', 'sanction_end_date']:
                if not post_data.get(field):
                    post_data[field] = pending_sanction_issue.get(field) or ''
            request.POST = post_data

        is_valid, mssg, warning_message = create_sanction(request, person)
        if not is_valid:
            messages.error(request, mssg)
        else:
            messages.success(request, mssg)
            if warning_message:
                messages.warning(request, warning_message)
        if is_pending_submit and pending_key in request.session:
            del request.session[pending_key]
            request.session.modified = True
        if return_url:
            return redirect(return_url)
        return redirect('issue_sanctions', person_id=person_id)


    return render(request, 'authorizations/issue_sanctions.html', {
        'person': person,
        'all_disciplines': all_disciplines,
        'discipline': discipline,
        'styles': styles,
        'pending_sanction_issue': pending_sanction_issue,
        'pending_style_id': pending_style_id,
        'pending_sanction_end_date': pending_sanction_end_date,
        'today': date.today(),
    })


def create_sanction(request, person):
    """Creates sanctions for a person."""
    action_note = _get_action_note(request)
    if not action_note:
        return False, 'A note is required to issue a sanction.', None

    sanction_request, message = _prepare_sanction_request(request.user, request.POST)
    if not sanction_request:
        return False, message, None

    sanction_type = sanction_request['sanction_type']
    sanction_end_date = sanction_request['sanction_end_date']
    sanction_note = _sanction_note_with_end_date(action_note, sanction_end_date)

    if sanction_type == 'discipline':
        discipline = sanction_request['discipline']
        sanction = Sanction.objects.filter(
            person=person,
            discipline=discipline,
            style__isnull=True,
            lifted_at__isnull=True,
        ).first()
        if sanction:
            sanction.end_date = sanction_end_date
            sanction.issue_note = sanction_note
            sanction.issued_by = request.user
            sanction.updated_by = request.user
            sanction.save()
        else:
            Sanction.objects.create(
                person=person,
                discipline=discipline,
                start_date=date.today(),
                end_date=sanction_end_date,
                issue_note=sanction_note,
                issued_by=request.user,
                created_by=request.user,
                updated_by=request.user,
            )
        return True, f'Sanction issued for discipline {discipline.name}', sanction_request['warning_message']

    elif sanction_type == 'style':
        style = sanction_request['style']
        sanction = Sanction.objects.filter(
            person=person,
            style=style,
            lifted_at__isnull=True,
        ).first()
        if sanction:
            sanction.end_date = sanction_end_date
            sanction.issue_note = sanction_note
            sanction.issued_by = request.user
            sanction.updated_by = request.user
            sanction.save()
        else:
            Sanction.objects.create(
                person=person,
                discipline=style.discipline,
                style=style,
                start_date=date.today(),
                end_date=sanction_end_date,
                issue_note=sanction_note,
                issued_by=request.user,
                created_by=request.user,
                updated_by=request.user,
            )
        return True, f'Sanction issued for style {style.name}', sanction_request['warning_message']

    else:
        return False, 'Invalid sanction type.', None


def _first_non_empty(*values):
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value
        elif value is not None:
            return value
    return ''


def _updated_sort_key(record):
    return (record.updated_at, record.id)


def _newer_user(first_user: User, second_user: User) -> User:
    return max([first_user, second_user], key=lambda u: (u.updated_at, u.id))


def _build_merge_profile_initial(survivor_user: User, source_user: User, survivor_person: Person, source_person: Person):
    newer_user = _newer_user(survivor_user, source_user)
    newer_person = survivor_person if newer_user.id == survivor_user.id else source_person

    is_minor = newer_person.is_minor
    parent_id = newer_person.parent_id if is_minor else None
    if parent_id is None and is_minor:
        fallback_parent = survivor_person.parent_id if survivor_person.is_minor else None
        parent_id = fallback_parent

    return {
        'honeypot': '',
        'email': _first_non_empty(newer_user.email, survivor_user.email),
        'username': _first_non_empty(newer_user.username, survivor_user.username),
        'first_name': _first_non_empty(newer_user.first_name, survivor_user.first_name),
        'last_name': _first_non_empty(newer_user.last_name, survivor_user.last_name),
        'membership': _first_non_empty(newer_user.membership, survivor_user.membership),
        'membership_expiration': _first_non_empty(newer_user.membership_expiration, survivor_user.membership_expiration),
        'address': _first_non_empty(newer_user.address, survivor_user.address),
        'address2': _first_non_empty(newer_user.address2, survivor_user.address2),
        'city': _first_non_empty(newer_user.city, survivor_user.city),
        'state_province': _first_non_empty(newer_user.state_province, survivor_user.state_province),
        'postal_code': _first_non_empty(newer_user.postal_code, survivor_user.postal_code),
        'country': _first_non_empty(newer_user.country, survivor_user.country),
        'phone_number': _first_non_empty(newer_user.phone_number, survivor_user.phone_number),
        'birthday': _first_non_empty(newer_user.birthday, survivor_user.birthday),
        'sca_name': _first_non_empty(newer_person.sca_name, survivor_person.sca_name),
        'title': newer_person.title_id or survivor_person.title_id or '',
        'new_title': '',
        'new_title_rank': '',
        'branch': newer_person.branch_id or survivor_person.branch_id or '',
        'is_minor': is_minor,
        'parent_id': parent_id or '',
        'background_check_expiration': _first_non_empty(
            newer_user.background_check_expiration,
            survivor_user.background_check_expiration
        ),
    }


def _build_authorization_merge_preview(survivor_person: Person, source_person: Person):
    auths = list(
        Authorization.objects.select_related('style__discipline', 'status', 'person')
        .filter(person__in=[survivor_person, source_person], style__isnull=False)
        .order_by('style__discipline__name', 'style__name', 'id')
    )

    auths_by_style = defaultdict(list)
    for auth in auths:
        auths_by_style[auth.style_id].append(auth)

    active_sanction_keys = set(
        Sanction.objects.filter(
            person__in=[survivor_person, source_person],
            lifted_at__isnull=True,
            end_date__gte=date.today(),
        ).values_list('discipline_id', 'style_id')
    )

    preview_rows = []
    for style_id, candidates in auths_by_style.items():
        winner = max(candidates, key=_updated_sort_key)
        style_discipline_id = winner.style.discipline_id if winner.style else None
        preview_rows.append({
            'style_id': style_id,
            'discipline_name': winner.style.discipline.name if winner.style and winner.style.discipline else '',
            'style_name': winner.style.name if winner.style else '',
            'winner_id': winner.id,
            'winner_user_id': winner.person_id,
            'winner_sca_name': winner.person.sca_name if winner.person else '',
            'winner_status': winner.status.name if winner.status else 'Unknown',
            'sanction_kept': (
                (style_discipline_id, style_id) in active_sanction_keys
                or (style_discipline_id, None) in active_sanction_keys
            ),
            'candidates': sorted(candidates, key=_updated_sort_key, reverse=True),
        })

    preview_rows.sort(key=lambda row: (row['discipline_name'], row['style_name']))
    return preview_rows


def _build_branch_office_merge_preview(survivor_person: Person, source_person: Person):
    offices = list(
        BranchMarshal.objects.select_related('branch', 'discipline', 'person')
        .filter(person__in=[survivor_person, source_person])
        .order_by('-end_date', '-updated_at', '-id')
    )

    active_offices = []
    expired_offices = []
    by_branch_and_discipline = defaultdict(list)
    today_value = date.today()

    for office in offices:
        if office.end_date >= today_value:
            active_offices.append(office)
            by_branch_and_discipline[(office.branch_id, office.discipline_id)].append(office)
        else:
            expired_offices.append(office)

    default_keep_active_office_ids = set()
    for grouped_offices in by_branch_and_discipline.values():
        newest = max(grouped_offices, key=_updated_sort_key)
        default_keep_active_office_ids.add(newest.id)

    return {
        'active_offices': active_offices,
        'expired_offices': expired_offices,
        'default_keep_active_office_ids': sorted(default_keep_active_office_ids),
    }


def _apply_profile_form_to_user_and_person(profile_form, user: User, person: Person, acting_user: User):
    cleaned = profile_form.cleaned_data

    previous_background_check_expiration = user.background_check_expiration
    user.email = cleaned['email']
    user.username = cleaned['username']
    user.first_name = cleaned['first_name']
    user.last_name = cleaned['last_name']
    user.membership = cleaned.get('membership')
    user.membership_expiration = cleaned.get('membership_expiration')
    user.address = cleaned['address']
    user.address2 = cleaned.get('address2')
    user.city = cleaned['city']
    user.state_province = cleaned['state_province']
    user.postal_code = cleaned['postal_code']
    user.country = cleaned['country']
    user.phone_number = cleaned['phone_number']
    user.birthday = cleaned.get('birthday')
    user.background_check_expiration = cleaned.get('background_check_expiration')
    user.updated_by = acting_user
    user.save()
    if (
        user.background_check_expiration
        and user.background_check_expiration != previous_background_check_expiration
    ):
        _activate_pending_background_check_authorizations(user)

    person.sca_name = cleaned.get('sca_name') or f"{user.first_name} {user.last_name}".strip()
    person.title = cleaned.get('title')
    person.branch = cleaned.get('branch')
    person.is_minor = cleaned.get('is_minor', False)
    person.parent = cleaned.get('parent_id')
    person.updated_by = acting_user
    person.save()


def _tombstone_user_for_merge(user: User, survivor_user: User, acting_user: User):
    stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    user.username = f'merged_{user.id}_{stamp}'
    user.email = f'merged_{user.id}_{stamp}@invalid.local'
    user.membership = None
    user.membership_expiration = None
    user.is_active = False
    user.merged_into = survivor_user
    user.merged_at = timezone.now()
    user.updated_by = acting_user
    user.set_unusable_password()
    user.save()


def _execute_account_merge(
    request,
    survivor_user: User,
    source_user: User,
    profile_form,
    keep_active_office_ids,
    action_note: str,
):
    survivor_person = survivor_user.person
    source_person = source_user.person
    today_value = date.today()
    yesterday = today_value - timedelta(days=1)

    source_username_before = source_user.username
    source_email_before = source_user.email

    _tombstone_user_for_merge(source_user, survivor_user, request.user)
    _apply_profile_form_to_user_and_person(profile_form, survivor_user, survivor_person, request.user)

    Authorization.objects.filter(marshal=source_person).update(marshal=survivor_person)
    Authorization.objects.filter(concurring_fighter=source_person).update(concurring_fighter=survivor_person)
    Person.objects.filter(parent=source_person).update(parent=survivor_person)
    UserNote.objects.filter(person=source_person).update(person=survivor_person)

    all_auths = list(
        Authorization.objects.select_related('status', 'style')
        .filter(person__in=[survivor_person, source_person])
    )
    auths_with_style = [auth for auth in all_auths if auth.style_id]
    auths_without_style = [auth for auth in all_auths if not auth.style_id]

    by_style = defaultdict(list)
    for auth in auths_with_style:
        by_style[auth.style_id].append(auth)

    merged_style_count = 0
    removed_duplicate_authorizations = 0

    for style_id, candidates in by_style.items():
        winner = max(candidates, key=_updated_sort_key)
        losers = [candidate for candidate in candidates if candidate.id != winner.id]

        for loser in losers:
            AuthorizationNote.objects.filter(authorization=loser).update(authorization=winner)

        for loser in losers:
            loser.delete()
            removed_duplicate_authorizations += 1

        if winner.person_id != survivor_person.user_id:
            winner.person = survivor_person
            winner.updated_by = request.user
            winner.save()

        merged_style_count += 1

    for auth in auths_without_style:
        if auth.person_id == source_person.user_id:
            auth.person = survivor_person
            auth.updated_by = request.user
            auth.save()

    source_sanctions = list(
        Sanction.objects.filter(person=source_person).order_by('-updated_at', '-id')
    )
    for sanction in source_sanctions:
        existing = Sanction.objects.filter(
            person=survivor_person,
            discipline=sanction.discipline,
            style=sanction.style,
            lifted_at=sanction.lifted_at,
        ).first()
        if existing and sanction.lifted_at is None:
            if sanction.end_date > existing.end_date:
                existing.end_date = sanction.end_date
                existing.issue_note = sanction.issue_note
                existing.issued_by = sanction.issued_by
                existing.updated_by = request.user
                existing.save()
            sanction.delete()
            continue
        sanction.person = survivor_person
        sanction.updated_by = request.user
        sanction.save()

    all_offices = list(
        BranchMarshal.objects.filter(person__in=[survivor_person, source_person]).order_by('id')
    )
    keep_active_office_ids = set(keep_active_office_ids)
    active_kept = 0
    active_ended = 0

    for office in all_offices:
        if office.person_id != survivor_person.user_id:
            office.person = survivor_person

        if office.end_date >= today_value:
            if office.id in keep_active_office_ids:
                active_kept += 1
            else:
                office.end_date = yesterday
                active_ended += 1

        office.updated_by = request.user
        office.save()

    source_person.updated_by = request.user
    source_person.save()

    UserNote.objects.create(
        person=survivor_person,
        created_by=request.user,
        note_type='officer_note',
        note=(
            f'Account merge completed by {request.user.username}. '
            f'Merged source user #{source_user.id} ({source_username_before}, {source_email_before}) '
            f'into survivor user #{survivor_user.id}. '
            f'Action note: {action_note}'
        ),
    )

    return {
        'merged_style_count': merged_style_count,
        'removed_duplicate_authorizations': removed_duplicate_authorizations,
        'active_kept': active_kept,
        'active_ended': active_ended,
    }


@login_required
def merge_accounts(request):
    if not is_kingdom_authorization_officer(request.user):
        raise PermissionDenied

    all_people = (
        Person.objects.filter(user__merged_into__isnull=True)
        .exclude(sca_name__isnull=True)
        .exclude(sca_name='')
        .order_by('sca_name')
        .values_list('sca_name', flat=True)
        .distinct()
    )

    old_sca_name = (request.GET.get('old_sca_name') or request.POST.get('old_sca_name') or '').strip()
    new_sca_name = (request.GET.get('new_sca_name') or request.POST.get('new_sca_name') or '').strip()
    old_matches = []
    new_matches = []
    preview_data = None
    profile_form = None
    selected_survivor_user_id = None
    selected_source_user_id = None
    selected_keep_active_office_ids = []
    merge_action_note = ''
    selected_source_person = None
    selected_survivor_person = None

    def _parse_optional_int(value):
        if value in [None, '']:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    if request.method == 'GET':
        action = (request.GET.get('action') or '').strip().lower()
        selected_source_user_id = _parse_optional_int(request.GET.get('selected_source_user_id'))
        selected_survivor_user_id = _parse_optional_int(request.GET.get('selected_survivor_user_id'))

        if action == 'search_old':
            selected_source_user_id = None
            selected_survivor_user_id = None
            new_sca_name = ''
        elif action == 'select_source':
            selected_candidates = request.GET.getlist('source_candidate')
            if len(selected_candidates) != 1:
                messages.error(request, 'Please check exactly one old identity account.')
                selected_source_user_id = None
            else:
                selected_source_user_id = _parse_optional_int(selected_candidates[0])
            selected_survivor_user_id = None
            new_sca_name = ''
        elif action == 'search_new':
            selected_survivor_user_id = None
        elif action == 'select_survivor':
            selected_candidates = request.GET.getlist('survivor_candidate')
            if len(selected_candidates) != 1:
                messages.error(request, 'Please check exactly one new identity account.')
                selected_survivor_user_id = None
            else:
                selected_survivor_user_id = _parse_optional_int(selected_candidates[0])

        if old_sca_name:
            old_matches = list(
                Person.objects.select_related('user', 'branch')
                .filter(sca_name=old_sca_name, user__merged_into__isnull=True)
                .order_by('user_id')
            )

        if selected_source_user_id:
            selected_source_person = Person.objects.select_related('user', 'branch').filter(
                user_id=selected_source_user_id,
                user__merged_into__isnull=True,
            ).first()
            if not selected_source_person:
                messages.error(request, 'Selected old identity account was not found.')
                selected_source_user_id = None

        if selected_source_user_id and new_sca_name:
            new_matches = list(
                Person.objects.select_related('user', 'branch')
                .filter(sca_name=new_sca_name, user__merged_into__isnull=True)
                .order_by('user_id')
            )

        if selected_survivor_user_id:
            selected_survivor_person = Person.objects.select_related('user', 'branch').filter(
                user_id=selected_survivor_user_id,
                user__merged_into__isnull=True,
            ).first()
            if not selected_survivor_person:
                messages.error(request, 'Selected new identity account was not found.')
                selected_survivor_user_id = None

        if selected_source_user_id and selected_survivor_user_id and selected_source_user_id == selected_survivor_user_id:
            messages.error(request, 'Please choose two different accounts.')
            selected_survivor_user_id = None
            selected_survivor_person = None

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip().lower()
        selected_survivor_user_id = _parse_optional_int(request.POST.get('survivor_user_id'))
        selected_source_user_id = _parse_optional_int(request.POST.get('source_user_id'))

        if old_sca_name:
            old_matches = list(
                Person.objects.select_related('user', 'branch')
                .filter(sca_name=old_sca_name, user__merged_into__isnull=True)
                .order_by('user_id')
            )
        if new_sca_name:
            new_matches = list(
                Person.objects.select_related('user', 'branch')
                .filter(sca_name=new_sca_name, user__merged_into__isnull=True)
                .order_by('user_id')
            )

        if action in ['preview', 'execute']:
            if not selected_survivor_user_id or not selected_source_user_id:
                messages.error(request, 'Please select two valid accounts by ID.')
            elif selected_survivor_user_id == selected_source_user_id:
                messages.error(request, 'Please choose two different accounts.')
            elif selected_source_user_id == request.user.id:
                messages.error(request, 'You cannot tombstone the account you are currently logged in with.')
            else:
                selected_people = Person.objects.select_related('user', 'branch', 'title', 'parent').filter(
                    user_id__in=[selected_survivor_user_id, selected_source_user_id],
                    user__merged_into__isnull=True,
                )
                people_by_id = {person.user_id: person for person in selected_people}
                survivor_person = people_by_id.get(selected_survivor_user_id)
                source_person = people_by_id.get(selected_source_user_id)

                selected_source_person = source_person
                selected_survivor_person = survivor_person

                if not survivor_person or not source_person:
                    messages.error(request, 'One or both selected accounts could not be found.')
                else:
                    survivor_user = survivor_person.user
                    source_user = source_person.user

                    preview_data = {
                        'survivor_user': survivor_user,
                        'source_user': source_user,
                        'newer_user': _newer_user(survivor_user, source_user),
                        'authorization_rows': _build_authorization_merge_preview(survivor_person, source_person),
                    }
                    office_preview = _build_branch_office_merge_preview(survivor_person, source_person)
                    preview_data.update(office_preview)

                    if action == 'preview':
                        initial_data = _build_merge_profile_initial(
                            survivor_user,
                            source_user,
                            survivor_person,
                            source_person,
                        )
                        profile_form = CreatePersonForm(
                            initial=initial_data,
                            user_instance=survivor_user,
                            exclude_user_ids=[source_user.id],
                            request=request,
                            show_all=True,
                        )
                        selected_keep_active_office_ids = preview_data['default_keep_active_office_ids']
                    else:
                        profile_form = CreatePersonForm(
                            request.POST,
                            user_instance=survivor_user,
                            exclude_user_ids=[source_user.id],
                            request=request,
                            show_all=True,
                        )
                        posted_keep_ids = request.POST.getlist('keep_active_office_ids')
                        active_office_ids = {office.id for office in preview_data['active_offices']}
                        selected_keep_active_office_ids = sorted({
                            int(office_id) for office_id in posted_keep_ids
                            if office_id.isdigit() and int(office_id) in active_office_ids
                        })
                        merge_action_note = _get_action_note(request, 'merge_action_note')

                        if not merge_action_note:
                            messages.error(request, 'A merge action note is required.')
                        elif profile_form.is_valid():
                            try:
                                with transaction.atomic():
                                    merge_summary = _execute_account_merge(
                                        request,
                                        survivor_user,
                                        source_user,
                                        profile_form,
                                        selected_keep_active_office_ids,
                                        merge_action_note,
                                    )
                            except Exception:
                                logger.exception(
                                    'Error during account merge survivor_id=%s source_id=%s',
                                    survivor_user.id,
                                    source_user.id,
                                )
                                messages.error(
                                    request,
                                    'We could not complete the merge right now. No changes were saved.',
                                )
                            else:
                                messages.success(
                                    request,
                                    'Accounts merged successfully. '
                                    f'Merged styles: {merge_summary["merged_style_count"]}. '
                                    f'Removed duplicate authorizations: {merge_summary["removed_duplicate_authorizations"]}. '
                                    f'Active offices kept: {merge_summary["active_kept"]}. '
                                    f'Active offices ended: {merge_summary["active_ended"]}.'
                                )
                                return redirect('index')
                        else:
                            messages.error(request, 'Please correct the errors in the merged profile before continuing.')

    context = {
        'all_people': all_people,
        'old_sca_name': old_sca_name,
        'new_sca_name': new_sca_name,
        'old_matches': old_matches,
        'new_matches': new_matches,
        'preview_data': preview_data,
        'profile_form': profile_form,
        'selected_survivor_user_id': selected_survivor_user_id,
        'selected_source_user_id': selected_source_user_id,
        'selected_source_person': selected_source_person,
        'selected_survivor_person': selected_survivor_person,
        'selected_keep_active_office_ids': selected_keep_active_office_ids,
        'merge_action_note': merge_action_note,
    }
    return render(request, 'authorizations/merge_accounts.html', context)


def branch_marshals(request):
    """
    Handles displaying the branch marshal search form, and showing the results
    in either a table or a card view grouped by person.
    """
    # --- POST Logic: Handle appointment changes first ---
    if request.user.is_authenticated:
        auth_officer = is_kingdom_authorization_officer(request.user)
        can_manage_marshal_offices = can_manage_any_branch_marshal_office(request.user)
    else:
        auth_officer = False
        can_manage_marshal_offices = False
    if request.method == 'POST':
        if not can_manage_marshal_offices:
            raise PermissionDenied

        action = request.POST.get('action')
        branch_officer_id = request.POST.get('branch_officer_id')
        try:
            branch_officer = BranchMarshal.objects.select_related('branch', 'discipline', 'person').get(id=branch_officer_id)
            if not can_manage_branch_marshal_office(request.user, branch_officer.branch, branch_officer.discipline):
                raise PermissionDenied
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
    show_manage_actions = False

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
        for marshal_person in page_obj.object_list:
            for appointment in getattr(marshal_person, 'current_appointments', []):
                appointment.can_manage = (
                    request.user.is_authenticated
                    and can_manage_branch_marshal_office(request.user, appointment.branch, appointment.discipline)
                )
                if appointment.can_manage:
                    show_manage_actions = True

    else: # 'table' view is the default
       # UPDATED: Added 'branch__region' to efficiently fetch the region name
        marshals_list = matching_appointments.select_related(
            'person', 'branch__region', 'discipline'
        ).order_by('person__sca_name', 'branch__name')
        paginator = Paginator(marshals_list, 25)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        for appointment in page_obj.object_list:
            appointment.can_manage = (
                request.user.is_authenticated
                and can_manage_branch_marshal_office(request.user, appointment.branch, appointment.discipline)
            )
            if appointment.can_manage:
                show_manage_actions = True

    context = {
        'page_obj': page_obj,
        'view_mode': view_mode,
        'auth_officer': auth_officer,
        'can_manage_marshal_offices': can_manage_marshal_offices,
        'show_manage_actions': show_manage_actions,
    }
    return render(request, 'authorizations/branch_marshals.html', context)


def _period_label(period):
    if not period:
        return 'N/A'
    return f'Q{period.quarter} {period.year}'


def _previous_period_for(selected_period, periods_desc):
    if not selected_period:
        return None
    for idx, period in enumerate(periods_desc):
        if period.id == selected_period.id:
            if idx + 1 < len(periods_desc):
                return periods_desc[idx + 1]
            return None
    return None


def _build_report_rows(report_family, current_period, compare_period):
    current_qs = ReportValue.objects.filter(
        reporting_period=current_period,
        report_family=report_family,
    ).order_by('display_order', 'region_name', 'subject_name', 'metric_name')
    compare_qs = ReportValue.objects.filter(
        reporting_period=compare_period,
        report_family=report_family,
    ).order_by('display_order', 'region_name', 'subject_name', 'metric_name') if compare_period else ReportValue.objects.none()

    def row_key(item):
        return (
            item.region_name or '',
            item.subject_name,
            item.metric_name,
        )

    compare_map = {row_key(item): item.value for item in compare_qs}
    current_map = {row_key(item): item.value for item in current_qs}
    seen_keys = set()
    ordered_keys = []

    for item in current_qs:
        key = row_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)

    for item in compare_qs:
        key = row_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)

    rows = []
    for region_name, subject_name, metric_name in ordered_keys:
        current_value = current_map.get((region_name, subject_name, metric_name))
        compare_value = compare_map.get((region_name, subject_name, metric_name))
        change = None
        if current_value is not None and compare_value is not None:
            change = current_value - compare_value
        rows.append(
            {
                'region_name': region_name,
                'subject_name': subject_name,
                'metric_name': metric_name,
                'current_value': current_value,
                'compare_value': compare_value,
                'change': change,
            }
        )
    return rows


def _build_rows_from_values(current_values, compare_values):
    def row_key(item):
        return (item['region_name'] or '', item['subject_name'], item['metric_name'])

    compare_map = {row_key(item): item['value'] for item in compare_values}
    current_map = {row_key(item): item['value'] for item in current_values}

    current_sorted = sorted(
        current_values,
        key=lambda item: (item.get('display_order', 0), item['region_name'], item['subject_name'], item['metric_name']),
    )
    compare_sorted = sorted(
        compare_values,
        key=lambda item: (item.get('display_order', 0), item['region_name'], item['subject_name'], item['metric_name']),
    )

    seen_keys = set()
    ordered_keys = []
    for item in current_sorted:
        key = row_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)
    for item in compare_sorted:
        key = row_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered_keys.append(key)

    rows = []
    for region_name, subject_name, metric_name in ordered_keys:
        current_value = current_map.get((region_name, subject_name, metric_name))
        compare_value = compare_map.get((region_name, subject_name, metric_name))
        change = None
        if current_value is not None and compare_value is not None:
            change = current_value - compare_value
        rows.append(
            {
                'region_name': region_name,
                'subject_name': subject_name,
                'metric_name': metric_name,
                'current_value': current_value,
                'compare_value': compare_value,
                'change': change,
            }
        )
    return rows


def _build_reports_csv_response(download_key, marshal_rows, regional_rows, equestrian_rows, show_compare_columns, current_label, compare_label):
    export_specs = {
        'quarterly_marshal': {
            'rows': marshal_rows,
            'base_headers': ['Discipline', 'Authorization Detail'],
            'row_mapper': lambda row: [row['subject_name'], row['metric_name']],
        },
        'regional_breakdown': {
            'rows': regional_rows,
            'base_headers': ['Region', 'Description', 'Metric'],
            'row_mapper': lambda row: [row['region_name'], row['subject_name'], row['metric_name']],
        },
        'equestrian': {
            'rows': equestrian_rows,
            'base_headers': ['Region', 'Authorization Type'],
            'row_mapper': lambda row: [row['region_name'], row['subject_name']],
        },
    }
    if download_key not in export_specs:
        return None

    spec = export_specs[download_key]
    headers = list(spec['base_headers']) + [current_label]
    if show_compare_columns:
        headers.extend([compare_label, 'Change'])

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{download_key}_report.csv"'
    # UTF-8 BOM helps Excel detect Unicode correctly on Windows.
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(headers)

    for row in spec['rows']:
        csv_row = spec['row_mapper'](row)
        csv_row.append('' if row['current_value'] is None else row['current_value'])
        if show_compare_columns:
            csv_row.append('' if row['compare_value'] is None else row['compare_value'])
            csv_row.append('' if row['change'] is None else row['change'])
        writer.writerow(csv_row)

    return response


def reports_view(request):
    periods = list(ReportingPeriod.objects.order_by('-year', '-quarter'))
    if not periods:
        return render(
            request,
            'reports.html',
            {
                'available_periods': [],
                'selected_current_period': None,
                'selected_compare_period': None,
                'selected_current_period_label': 'N/A',
                'selected_compare_period_label': 'N/A',
                'marshal_rows': [],
                'regional_rows': [],
                'equestrian_rows': [],
            },
        )

    latest_period = periods[0]
    current_period_id = request.GET.get('current_period')
    compare_period_id = request.GET.get('compare_period')

    current_is_dynamic = current_period_id == 'current'
    selected_current_period = None if current_is_dynamic else latest_period
    if current_period_id and current_period_id != 'current':
        selected_current_period = next((p for p in periods if str(p.id) == current_period_id), latest_period)

    if current_is_dynamic:
        default_compare = latest_period
    else:
        default_compare = _previous_period_for(selected_current_period, periods)

    if 'compare_period' in request.GET:
        if compare_period_id:
            selected_compare_period = next((p for p in periods if str(p.id) == compare_period_id), None)
        else:
            selected_compare_period = None
    else:
        selected_compare_period = default_compare

    if current_is_dynamic:
        current_reporting_error = None
        try:
            current_snapshot = build_current_report_snapshot(as_of=date.today())
        except ReportingConfigurationError as exc:
            current_snapshot = {
                ReportValue.ReportFamily.QUARTERLY_MARSHAL: [],
                ReportValue.ReportFamily.REGIONAL_BREAKDOWN: [],
                ReportValue.ReportFamily.EQUESTRIAN: [],
            }
            current_reporting_error = '; '.join(exc.messages)

        compare_rows_by_family = defaultdict(list)
        if selected_compare_period:
            compare_qs = ReportValue.objects.filter(reporting_period=selected_compare_period)
            for item in compare_qs:
                compare_rows_by_family[item.report_family].append(
                    {
                        'region_name': item.region_name,
                        'subject_name': item.subject_name,
                        'metric_name': item.metric_name,
                        'value': item.value,
                        'display_order': item.display_order,
                    }
                )
        marshal_rows = _build_rows_from_values(
            current_snapshot[ReportValue.ReportFamily.QUARTERLY_MARSHAL],
            compare_rows_by_family[ReportValue.ReportFamily.QUARTERLY_MARSHAL],
        )
        regional_rows = _build_rows_from_values(
            current_snapshot[ReportValue.ReportFamily.REGIONAL_BREAKDOWN],
            compare_rows_by_family[ReportValue.ReportFamily.REGIONAL_BREAKDOWN],
        )
        equestrian_rows = _build_rows_from_values(
            current_snapshot[ReportValue.ReportFamily.EQUESTRIAN],
            compare_rows_by_family[ReportValue.ReportFamily.EQUESTRIAN],
        )
        selected_current_period_label = f'Current ({date.today().isoformat()})'
        selected_current_officer_name = 'Computed from current database state'
    else:
        current_reporting_error = None
        marshal_rows = _build_report_rows(
            ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            selected_current_period,
            selected_compare_period,
        )
        regional_rows = _build_report_rows(
            ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
            selected_current_period,
            selected_compare_period,
        )
        equestrian_rows = _build_report_rows(
            ReportValue.ReportFamily.EQUESTRIAN,
            selected_current_period,
            selected_compare_period,
        )
        selected_current_period_label = _period_label(selected_current_period)
        selected_current_officer_name = selected_current_period.authorization_officer_name if selected_current_period else 'N/A'

    show_compare_columns = selected_compare_period is not None
    selected_compare_period_label = _period_label(selected_compare_period)

    download_key = (request.GET.get('download') or '').strip()
    if download_key:
        download_response = _build_reports_csv_response(
            download_key,
            marshal_rows,
            regional_rows,
            equestrian_rows,
            show_compare_columns,
            selected_current_period_label,
            selected_compare_period_label,
        )
        if download_response is not None:
            return download_response

    context = {
        'available_periods': periods,
        'current_is_dynamic': current_is_dynamic,
        'selected_current_period': selected_current_period,
        'selected_compare_period': selected_compare_period,
        'show_compare_columns': show_compare_columns,
        'selected_current_period_label': selected_current_period_label,
        'selected_compare_period_label': selected_compare_period_label,
        'selected_current_officer_name': selected_current_officer_name,
        'current_reporting_error': current_reporting_error,
        'marshal_rows': marshal_rows,
        'regional_rows': regional_rows,
        'equestrian_rows': equestrian_rows,
    }
    return render(request, 'reports.html', context)


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
        # show "Duke (Duchy)" etc.
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
        label='Website',
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
        required=True,
        error_messages={
            'required': 'State/Province is required.',
            'invalid_choice': 'Select a valid state/province from the list.',
        }
    )
    postal_code = forms.CharField(
        label='Postal Code',
        required=True,
        error_messages={'required': 'Postal code is required.'},
    )
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
        self.exclude_user_ids = set(kwargs.pop('exclude_user_ids', []))
        self.request = kwargs.pop('request', None)
        self.allow_membership_mismatch_bypass = bool(kwargs.pop('allow_membership_mismatch_bypass', False))
        self.membership_fields_changed = False
        self.membership_mismatch_bypass_used = False
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
                'State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).'
            )
        
        return state_province

    def clean(self):
        cleaned_data = super().clean()
        new_title = cleaned_data.get('new_title')
        new_title_rank = cleaned_data.get('new_title_rank')

        excluded_user_ids = set(self.exclude_user_ids)
        if self.user_instance:
            excluded_user_ids.add(self.user_instance.id)

        if cleaned_data.get('honeypot'):
            raise forms.ValidationError('Unable to process submission.')

        if not cleaned_data.get('is_minor') and cleaned_data.get('parent_id'):
            raise forms.ValidationError('A non-minor must not have a parent ID.')

        username = cleaned_data.get('username')
        if username and User.objects.filter(
            username=username,
            merged_into__isnull=True,
        ).exclude(id__in=excluded_user_ids).exists():
            raise forms.ValidationError('A user with this username already exists.')

        membership = cleaned_data.get('membership')
        if isinstance(membership, str):
            membership = membership.strip()
            cleaned_data['membership'] = membership or None
        membership_expiration = cleaned_data.get('membership_expiration')
        existing_membership = self.user_instance.membership if self.user_instance else None
        existing_membership_expiration = self.user_instance.membership_expiration if self.user_instance else None
        self.membership_fields_changed = (
            membership != existing_membership
            or membership_expiration != existing_membership_expiration
        )
        if membership and User.objects.filter(
            membership=membership,
            merged_into__isnull=True,
        ).exclude(id__in=excluded_user_ids).exists():
            raise forms.ValidationError(
                'A user with this membership number already exists. '
                'If this is your existing account, use the account recovery options on the login page '
                'or contact the Kingdom Authorization Officer for help.'
            )

        first_name = cleaned_data.get('first_name')
        last_name = cleaned_data.get('last_name')
        email = cleaned_data.get('email')
        if first_name and last_name and email:
            first_name = first_name.strip()
            last_name = last_name.strip()
            email = email.strip()
            cleaned_data['first_name'] = first_name
            cleaned_data['last_name'] = last_name
            cleaned_data['email'] = email
            if User.objects.filter(
                first_name__iexact=first_name,
                last_name__iexact=last_name,
                email__iexact=email,
                merged_into__isnull=True,
            ).exclude(id__in=excluded_user_ids).exists():
                raise forms.ValidationError(
                    'An account with this first name, last name, and email already exists. '
                    'If this is your account, recover your credentials from the login page '
                    'or contact the Kingdom Authorization Officer to merge duplicate accounts.'
                )

        if bool(cleaned_data.get('membership')) != bool(cleaned_data.get('membership_expiration')):
            raise forms.ValidationError('Must have both a membership number and expiration or neither.')

        if membership and membership_expiration and self.membership_fields_changed:
            testing_enabled = getattr(settings, 'AUTHZ_TEST_FEATURES', False)
            if not testing_enabled:
                if not _membership_matches_current_roster(
                    membership=membership,
                    first_name=cleaned_data.get('first_name', ''),
                    last_name=cleaned_data.get('last_name', ''),
                    membership_expiration=membership_expiration,
                ):
                    if self.allow_membership_mismatch_bypass:
                        self.membership_mismatch_bypass_used = True
                    else:
                        raise forms.ValidationError(MEMBERSHIP_INVALID_MESSAGE)

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
            if BranchMarshal.objects.filter(person=user.person, end_date__gte=date.today(), branch__name='An Tir', discipline__name='Authorization Officer').exists():
                self.fields['discipline'].queryset = Discipline.objects.all().exclude(name__in=['Authorization Officer', 'Earl Marshal'])
            else:
                senior_authorizations = Authorization.objects.effectively_active().filter(
                    person__user=user,
                    style__name='Senior Marshal',  # Assuming 'Senior Marshal' is the style name
                ).values_list('style__discipline', flat=True)

                # Update the discipline queryset with the filtered disciplines
                self.fields['discipline'].queryset = Discipline.objects.filter(id__in=senior_authorizations)
