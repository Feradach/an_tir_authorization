import zipfile
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from reportlab.pdfbase import pdfmetrics
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse

from authorizations.models import (
    Authorization,
    AuthorizationAuditEntry,
    AuthorizationNote,
    AuthorizationStatus,
    AuthorizationValidityInterval,
    AuthorizationPortalSetting,
    Branch,
    BranchMarshal,
    Discipline,
    Person,
    ReportValue,
    ReportingPeriod,
    Sanction,
    MembershipRosterEntry,
    MembershipRosterImport,
    WaiverRecord,
    SupportingDocument,
    SupportingDocumentAuthorization,
    SupportingDocumentPerson,
    UserNote,
    User,
    WeaponStyle,
    LegacyAuthorizationRecoveryEntry,
    SYSTEM_USER_IDS,
)
from authorizations.reporting import EQUESTRIAN_TYPE_ORDER, QUARTERLY_DISCIPLINE_MAP, REGION_ORDER
from authorizations.views import _fit_pdf_text_for_field, PDF_NAME_MIN_FONT_SIZE


class ViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status_active = AuthorizationStatus.objects.create(name='Active')
        cls.status_pending = AuthorizationStatus.objects.create(name='Awaiting Second Marshal Concurrence')
        cls.status_regional = AuthorizationStatus.objects.create(name='Awaiting Regional Marshal Approval')
        cls.status_kingdom = AuthorizationStatus.objects.create(name='Awaiting Kingdom Authorization Officer Review')
        cls.status_needs_kingdom_equestrian_waiver = AuthorizationStatus.objects.filter(
            name='Awaiting Equestrian Authorization Officer Review'
        ).order_by('id').first()
        if not cls.status_needs_kingdom_equestrian_waiver:
            cls.status_needs_kingdom_equestrian_waiver = AuthorizationStatus.objects.create(
                name='Awaiting Equestrian Authorization Officer Review'
            )
        cls.status_pending_background_check = AuthorizationStatus.objects.create(name='Awaiting Background Check')
        cls.status_pending_waiver = AuthorizationStatus.objects.create(name='Awaiting Waiver')
        cls.status_needs_concurrence = AuthorizationStatus.objects.create(name='Awaiting Fighter Concurrence')
        cls.status_revoked = AuthorizationStatus.objects.create(name='Revoked')
        cls.status_rejected = AuthorizationStatus.objects.create(name='Rejected')
        cls.status_inactive = AuthorizationStatus.objects.create(name='Inactive')

        cls.branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        cls.region_summits = Branch.objects.create(name='Summits', type='Region', region=cls.branch_an_tir)
        cls.region_tir_righ = Branch.objects.create(name='Tir Righ', type='Region', region=cls.branch_an_tir)
        cls.branch_gd = Branch.objects.create(name='Barony of Glyn Dwfn', type='Barony', region=cls.region_summits)
        cls.branch_lg = Branch.objects.create(name='Barony of Lions Gate', type='Barony', region=cls.region_tir_righ)
        cls.branch_other = Branch.objects.create(name='Special Other', type='Other', region=cls.region_summits)

        cls.discipline_armored = Discipline.objects.create(name='Armored Combat')
        cls.discipline_rapier = Discipline.objects.create(name='Rapier Combat')
        cls.discipline_youth_armored = Discipline.objects.create(name='Youth Armored')
        cls.discipline_auth_officer = Discipline.objects.create(name='Authorization Officer')
        cls.discipline_equestrian_auth_officer = Discipline.objects.create(name='Equestrian Authorization Officer')
        cls.discipline_earl_marshal = Discipline.objects.create(name='Earl Marshal')
        cls.discipline_seneschal, _ = Discipline.objects.get_or_create(name='Seneschal')

        cls.style_sm_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_armored)
        cls.style_jm_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_armored)
        cls.style_weapon_armored = WeaponStyle.objects.create(name='Weapon & Shield', discipline=cls.discipline_armored)
        cls.style_single_rapier = WeaponStyle.objects.create(name='Single Sword', discipline=cls.discipline_rapier)
        cls.style_sm_youth_armored = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_youth_armored)
        cls.style_jm_youth_armored = WeaponStyle.objects.create(name='Junior Marshal', discipline=cls.discipline_youth_armored)

    def setUp(self):
        cache.clear()
        self._membership_seed = 200000

    def _next_membership(self):
        self._membership_seed += 1
        return str(self._membership_seed)

    def seed_membership_roster(self, membership_number, first_name, last_name, expiration, *, has_society_waiver=False):
        return MembershipRosterEntry.objects.create(
            membership_number=membership_number,
            first_name=first_name,
            last_name=last_name,
            membership_expiration=expiration,
            has_society_waiver=has_society_waiver,
        )

    def make_person(
        self,
        username,
        sca_name,
        *,
        branch=None,
        membership='auto',
        membership_expiration='auto',
        is_minor=False,
        birthday=None,
        parent=None,
        parent_sca_name='',
        parent_first_name='',
        parent_last_name='',
        email=None,
        background_check_expiration=None,
        waiver_expiration=None,
        password='StrongPass!123',
        user_id=None,
    ):
        if membership == 'auto':
            membership = self._next_membership()
        if membership_expiration == 'auto':
            membership_expiration = date.today() + relativedelta(years=1)

        user_kwargs = {
            'username': username,
            'email': email or f'{username}@example.com',
            'first_name': sca_name.split()[0],
            'last_name': 'Tester',
            'membership': membership,
            'membership_expiration': membership_expiration,
            'birthday': birthday,
            'state_province': 'Oregon',
            'country': 'United States',
            'background_check_expiration': background_check_expiration,
            'waiver_expiration': waiver_expiration,
        }
        if user_id is None:
            user = User.objects.create_user(password=password, **user_kwargs)
        else:
            User.objects.filter(pk=user_id).delete()
            user = User(id=user_id, **user_kwargs)
            user.set_password(password)
            user.save(force_insert=True)
        person = Person.objects.create(
            user=user,
            sca_name=sca_name,
            branch=branch or self.branch_gd,
            parent=parent,
            parent_sca_name=parent_sca_name,
            parent_first_name=parent_first_name,
            parent_last_name=parent_last_name,
        )
        return user, person

    def grant_authorization(self, person, style, *, status=None, expiration=None, marshal=None):
        return Authorization.objects.create(
            person=person,
            style=style,
            status=status or self.status_active,
            expiration=expiration or (date.today() + relativedelta(years=1)),
            marshal=marshal or person,
        )

    def appoint(self, person, branch, discipline, *, end_date=None):
        return BranchMarshal.objects.create(
            person=person,
            branch=branch,
            discipline=discipline,
            start_date=date.today() - timedelta(days=1),
            end_date=end_date or (date.today() + relativedelta(years=1)),
        )

    def messages_for(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def date_value(self, maybe_date):
        return maybe_date.isoformat() if maybe_date else ''

    def registration_payload(self, username='new_user', email='new_user@example.com', **overrides):
        payload = {
            'honeypot': '',
            'email': email,
            'username': username,
            'first_name': 'New',
            'last_name': 'User',
            'membership': self._next_membership(),
            'membership_expiration': self.date_value(date.today() + relativedelta(years=1)),
            'address': '123 Main St',
            'address2': '',
            'city': 'Portland',
            'state_province': 'Oregon',
            'postal_code': '97201',
            'country': 'United States',
            'phone_number': '5035551212',
            'birthday': '',
            'sca_name': 'New User of An Tir',
            'title': '',
            'new_title': '',
            'new_title_rank': '',
            'branch': str(self.branch_gd.id),
            'is_minor': '',
            'parent_id': '',
            'parent_sca_name': '',
            'parent_first_name': '',
            'parent_last_name': '',
            'background_check_expiration': '',
        }
        payload.update(overrides)
        return payload

    def account_update_payload(self, user, person, **overrides):
        payload = {
            'honeypot': '',
            'email': user.email,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'membership': user.membership or '',
            'membership_expiration': self.date_value(user.membership_expiration),
            'address': user.address or '123 Main St',
            'address2': user.address2 or '',
            'city': user.city or 'Portland',
            'state_province': user.state_province or 'Oregon',
            'postal_code': user.postal_code or '97201',
            'country': user.country or 'United States',
            'phone_number': user.phone_number or '5035551212',
            'birthday': self.date_value(user.birthday),
            'sca_name': person.sca_name,
            'title': str(person.title_id) if person.title_id else '',
            'new_title': '',
            'new_title_rank': '',
            'branch': str(person.branch_id),
            'is_minor': 'on' if person.is_current_minor else '',
            'parent_id': str(person.parent_id) if person.parent_id else '',
            'parent_sca_name': person.parent_sca_name,
            'parent_first_name': person.parent_first_name,
            'parent_last_name': person.parent_last_name,
            'background_check_expiration': self.date_value(user.background_check_expiration),
        }
        payload.update(overrides)
        return payload


class LoginViewTests(ViewTestBase):
    @override_settings(AUTHZ_TEST_FEATURES=False)
    @patch('authorizations.views._throttle_request')
    @patch('authorizations.views._throttle_limit_reached', return_value=False)
    def test_failed_login_uses_production_throttle_defaults(self, mock_limit_reached, mock_throttle):
        response = self.client.post(
            reverse('login'),
            {
                'username': 'missing_user',
                'password': 'bad-password',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_limit_reached.call_count, 2)
        self.assertEqual(mock_throttle.call_count, 2)
        self.assertEqual(mock_throttle.call_args_list[0].args[1:], (5, 15 * 60))
        self.assertEqual(mock_throttle.call_args_list[1].args[1:], (20, 15 * 60))

    @override_settings(AUTHZ_TEST_FEATURES=True)
    @patch('authorizations.views._throttle_request')
    @patch('authorizations.views._throttle_limit_reached', return_value=False)
    def test_failed_login_uses_relaxed_test_throttle_defaults(self, mock_limit_reached, mock_throttle):
        response = self.client.post(
            reverse('login'),
            {
                'username': 'missing_user',
                'password': 'bad-password',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_limit_reached.call_count, 2)
        self.assertEqual(mock_throttle.call_count, 2)
        self.assertEqual(mock_throttle.call_args_list[0].args[1:], (100, 5 * 60))
        self.assertEqual(mock_throttle.call_args_list[1].args[1:], (200, 5 * 60))

    @patch('authorizations.views._throttle_request')
    @patch('authorizations.views._throttle_limit_reached', return_value=False)
    def test_successful_login_does_not_increment_login_throttle(self, mock_limit_reached, mock_throttle):
        user, _ = self.make_person('login_success_user', 'Login Success User')
        user.has_logged_in = True
        user.save()

        response = self.client.post(
            reverse('login'),
            {
                'username': user.username,
                'password': 'StrongPass!123',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('index'))
        self.assertEqual(mock_limit_reached.call_count, 2)
        self.assertEqual(mock_throttle.call_count, 0)

    @patch('authorizations.views.authenticate')
    @patch('authorizations.views._throttle_limit_reached', return_value=True)
    def test_throttled_login_does_not_authenticate(self, mock_limit_reached, mock_authenticate):
        response = self.client.post(
            reverse('login'),
            {
                'username': 'throttled_user',
                'password': 'StrongPass!123',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_authenticate.call_count, 0)
        response_messages = self.messages_for(response)
        self.assertTrue(any('Too many login attempts' in message for message in response_messages))


class ContactViewTests(ViewTestBase):
    def email_change_payload(self, **overrides):
        payload = {
            'sca_name': 'Example Fighter',
            'legal_name': 'Example Legalname',
            'new_email': 'new-email@example.com',
            'membership_number': '123456',
            'honeypot': '',
        }
        payload.update(overrides)
        return payload

    def test_contact_page_shows_email_change_request_form(self):
        response = self.client.get(reverse('contact'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Request Email Change')
        self.assertContains(response, 'Please try your fighter page first')
        self.assertContains(response, 'name="sca_name"')
        self.assertContains(response, 'name="legal_name"')
        self.assertContains(response, 'name="new_email"')
        self.assertContains(response, 'name="membership_number"')

    @override_settings(AUTHZ_EMAIL_CHANGE_MIN_SECONDS=0)
    @patch('authorizations.views.send_mail')
    def test_email_change_request_sends_admin_email(self, mock_send_mail):
        self.client.get(reverse('contact'))

        response = self.client.post(reverse('contact'), self.email_change_payload(), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        call_args = mock_send_mail.call_args.args
        self.assertEqual(call_args[0], 'An Tir Authorization Portal email change request')
        self.assertIn('Example Fighter', call_args[1])
        self.assertIn('new-email@example.com', call_args[1])
        self.assertIn('Do not make this change without verifying', call_args[1])
        self.assertEqual(call_args[3], ['antir.authorization.database@gmail.com'])
        self.assertIn(
            'Your email change request has been sent to the database officer for review.',
            self.messages_for(response),
        )

    @override_settings(AUTHZ_EMAIL_CHANGE_MIN_SECONDS=0)
    @patch('authorizations.views.send_mail')
    def test_email_change_honeypot_does_not_send_email(self, mock_send_mail):
        self.client.get(reverse('contact'))

        response = self.client.post(
            reverse('contact'),
            self.email_change_payload(honeypot='spam-site'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 0)

    @override_settings(AUTHZ_EMAIL_CHANGE_MIN_SECONDS=5)
    @patch('authorizations.views.send_mail')
    def test_email_change_direct_post_without_session_does_not_send_email(self, mock_send_mail):
        response = self.client.post(reverse('contact'), self.email_change_payload(), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 0)
        self.assertIn(
            'Your email change request has been sent to the database officer for review.',
            self.messages_for(response),
        )


class FighterCardPdfTextTests(TestCase):
    def test_name_field_shrinks_to_fit_before_truncating(self):
        text, font_size = _fit_pdf_text_for_field(
            'sca_name',
            'Long Society Name',
            'Helvetica',
            12,
            90,
        )

        self.assertEqual(text, 'Long Society Name')
        self.assertLess(font_size, 12)
        self.assertGreaterEqual(font_size, PDF_NAME_MIN_FONT_SIZE)

    def test_name_field_truncates_at_latest_fitting_space_after_minimum_size(self):
        max_width = pdfmetrics.stringWidth('Long Society Name With', 'Helvetica', PDF_NAME_MIN_FONT_SIZE) - 1
        text, font_size = _fit_pdf_text_for_field(
            'sca_name',
            'Long Society Name With Too Many Words',
            'Helvetica',
            12,
            max_width,
        )

        self.assertEqual(font_size, PDF_NAME_MIN_FONT_SIZE)
        self.assertEqual(text, 'Long Society Name')

    def test_marshal_name_field_uses_same_fitting_rules(self):
        max_width = pdfmetrics.stringWidth('Long Marshal Name With', 'Helvetica', PDF_NAME_MIN_FONT_SIZE) - 1
        text, font_size = _fit_pdf_text_for_field(
            'Armored Combat marshal',
            'Long Marshal Name With Too Many Words',
            'Helvetica',
            12,
            max_width,
        )

        self.assertEqual(font_size, PDF_NAME_MIN_FONT_SIZE)
        self.assertEqual(text, 'Long Marshal Name')

    def test_non_name_field_is_not_modified(self):
        text, font_size = _fit_pdf_text_for_field(
            'expiration',
            '01/01/2027',
            'Helvetica',
            12,
            10,
        )

        self.assertEqual(text, '01/01/2027')
        self.assertEqual(font_size, 12)


class IndexViewTests(ViewTestBase):
    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_header_uses_standard_logo_when_test_features_disabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("index")}"')
        self.assertContains(response, '/static/AnTirWebLogo.png')
        self.assertNotContains(response, '/static/AnTirWebLogo_Proto.png')

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_header_uses_proto_logo_when_test_features_enabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("index")}"')
        self.assertContains(response, '/static/AnTirWebLogo_Proto.png')
        self.assertNotContains(response, '/static/AnTirWebLogo.png')

    def test_index_hides_authorization_officer_sign_off_for_non_kao_when_disabled(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Require Kingdom Authorization Officer Verification')
        self.assertNotContains(response, 'Kingdom Authorization Officer Verification Is Enabled')

    def test_index_launch_notice_defaults_expanded_for_logged_out_users(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-bs-target="#launchNotice"')
        self.assertContains(response, 'aria-expanded="true"')
        self.assertContains(response, '<div id="launchNotice" class="collapse show">')

    def test_index_launch_notice_defaults_collapsed_for_logged_in_users(self):
        user, person = self.make_person('index_notice_user', 'Index Notice User')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-bs-target="#launchNotice"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, '<div id="launchNotice" class="collapse">')

    def test_index_shows_enabled_notice_for_non_kao_when_enabled(self):
        AuthorizationPortalSetting.objects.create(require_kao_verification=True)
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kingdom Authorization Officer Verification Is Enabled')
        self.assertNotContains(response, 'name="authorization_officer_sign_off"')

    def test_index_kao_sees_authorization_officer_sign_off_dropdown_with_current_state(self):
        kao_user, kao_person = self.make_person('index_setting_kao_view', 'Index Setting KAO View')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Require Kingdom Authorization Officer Verification')
        self.assertContains(response, 'name="authorization_officer_sign_off"')
        self.assertContains(response, '<option value="off" selected>Off</option>', html=True)

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        response_on = self.client.get(reverse('index'))
        self.assertContains(response_on, '<option value="on" selected>On</option>', html=True)

    def test_index_kao_sees_membership_upload_controls(self):
        kao_user, kao_person = self.make_person('index_membership_kao_view', 'Index Membership KAO View')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Society Membership CSV or Excel File')
        self.assertContains(response, 'name="membership_csv"')
        self.assertContains(response, 'Last upload:')

    def test_index_kao_sees_delete_authorizations_button(self):
        kao_user, kao_person = self.make_person('index_delete_kao_view', 'Index Delete KAO View')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('delete_authorizations'))
        self.assertContains(response, 'Delete Authorizations')
        self.assertContains(response, 'Paper Authorization Entry')

    def test_index_kingdom_seneschal_sees_membership_upload_controls(self):
        seneschal_user, seneschal_person = self.make_person(
            'index_membership_seneschal_view',
            'Index Membership Seneschal View',
        )
        self.appoint(seneschal_person, self.branch_an_tir, self.discipline_seneschal)
        self.client.login(username=seneschal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Society Membership CSV or Excel File')
        self.assertContains(response, 'name="membership_csv"')
        self.assertContains(response, 'Last upload:')
        self.assertNotContains(response, 'Require Kingdom Authorization Officer Verification')

    def test_index_senior_marshal_sees_create_account_link_without_merge_accounts(self):
        marshal_user, marshal_person = self.make_person('index_senior_marshal', 'Index Senior Marshal')
        self.grant_authorization(marshal_person, self.style_sm_armored)
        self.client.login(username=marshal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("register")}"')
        self.assertContains(response, 'Create an Account')
        self.assertNotContains(response, 'Merge Accounts')

    def test_index_staff_sees_kingdom_authorization_notifications_without_office(self):
        staff_user, _ = self.make_person('index_staff_kao_view', 'Index Staff KAO View')
        staff_user.is_staff = True
        staff_user.save()
        _, proposer_person = self.make_person('index_staff_kao_prop', 'Index Staff KAO Proposer')
        _, target_person = self.make_person('index_staff_kao_target', 'Index Staff KAO Target')
        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        self.client.login(username=staff_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Authorizations Needing Approval')
        self.assertContains(response, 'Index Staff KAO Target')
        self.assertContains(response, 'Awaiting Kingdom Authorization Officer Review')

    @patch('authorizations.views.send_mail')
    def test_register_minor_requires_parent_id_or_parent_first_and_last_name(self, mock_send_mail):
        response = self.client.post(
            reverse('register'),
            self.registration_payload(
                username='minor_without_parent',
                email='minor_without_parent@example.com',
                birthday=self.date_value(date.today() - relativedelta(years=12)),
                is_minor='on',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A minor must have either a parent ID or parent first and last name.')
        self.assertFalse(User.objects.filter(username='minor_without_parent').exists())

    @patch('authorizations.views.send_mail')
    def test_register_minor_can_store_parent_name_without_parent_id(self, mock_send_mail):
        payload = self.registration_payload(
            username='minor_with_parent_name',
            email='minor_with_parent_name@example.com',
            birthday=self.date_value(date.today() - relativedelta(years=12)),
            is_minor='on',
            parent_first_name='Pat',
            parent_last_name='Parent',
            parent_sca_name='Parent of An Tir',
        )
        self.seed_membership_roster(
            payload['membership'],
            payload['first_name'],
            payload['last_name'],
            date.fromisoformat(payload['membership_expiration']),
        )

        response = self.client.post(
            reverse('register'),
            payload,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = User.objects.get(username='minor_with_parent_name').person
        self.assertIsNone(person.parent)
        self.assertEqual(person.parent_first_name, 'Pat')
        self.assertEqual(person.parent_last_name, 'Parent')
        self.assertEqual(person.parent_sca_name, 'Parent of An Tir')

    @patch('authorizations.views.send_mail')
    def test_register_minor_parent_id_discards_parent_name_fields(self, mock_send_mail):
        parent_user, parent = self.make_person('minor_parent_id_parent', 'Minor Parent')
        payload = self.registration_payload(
            username='minor_with_parent_id',
            email='minor_with_parent_id@example.com',
            birthday=self.date_value(date.today() - relativedelta(years=12)),
            is_minor='on',
            parent_id=str(parent.user_id),
            parent_first_name='Should',
            parent_last_name='Clear',
            parent_sca_name='Should Clear',
        )
        self.seed_membership_roster(
            payload['membership'],
            payload['first_name'],
            payload['last_name'],
            date.fromisoformat(payload['membership_expiration']),
        )

        response = self.client.post(
            reverse('register'),
            payload,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = User.objects.get(username='minor_with_parent_id').person
        self.assertEqual(person.parent, parent)
        self.assertEqual(person.parent_first_name, '')
        self.assertEqual(person.parent_last_name, '')
        self.assertEqual(person.parent_sca_name, '')

    def test_index_non_kao_does_not_see_membership_upload_controls(self):
        user, _ = self.make_person('index_membership_non_kao', 'Index Membership Non KAO')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Upload Society Membership CSV or Excel File')
        self.assertNotContains(response, 'name="membership_csv"')

    def test_index_excludes_staff_from_people_dropdown_and_name_lookup(self):
        staff_user, _ = self.make_person(
            'index_staff_admin',
            'Index Staff Admin',
        )
        staff_user.is_staff = True
        staff_user.save(update_fields=['is_staff'])
        legacy_id_user, _ = self.make_person(
            'index_legacy_id_visible',
            'Index Legacy Id Visible',
            user_id=SYSTEM_USER_IDS[0],
            membership='150500',
        )
        self.make_person('index_visible_fighter', 'Index Visible Fighter')

        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Index Staff Admin', list(response.context['all_people']))
        self.assertIn('Index Legacy Id Visible', list(response.context['all_people']))

        response = self.client.get(reverse('index'), {'sca_name': 'Index Staff Admin'})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(
            getattr(response, 'url', None),
            reverse('fighter', kwargs={'person_id': staff_user.id}),
        )
        self.assertNotIn('name_matches', response.context)

        response = self.client.get(reverse('index'), {'sca_name': 'Index Legacy Id Visible'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': legacy_id_user.id}))

    def test_non_kao_cannot_change_authorization_officer_sign_off_setting(self):
        user, _ = self.make_person('index_setting_non_kao', 'Index Setting Non KAO')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Authorization Officer can change this setting.')
        self.assertFalse(AuthorizationPortalSetting.objects.exists())

    def test_kao_can_change_authorization_officer_sign_off_setting(self):
        kao_user, kao_person = self.make_person('index_setting_kao', 'Index Setting KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        self.client.login(username=kao_user.username, password='StrongPass!123')

        on_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'on',
            },
            follow=True,
        )

        self.assertEqual(on_response.status_code, 200)
        self.assertContains(on_response, 'Require Kingdom Authorization Officer Verification is now On.')
        setting = AuthorizationPortalSetting.objects.get(pk=1)
        self.assertTrue(setting.require_kao_verification)
        self.assertEqual(setting.updated_by, kao_user)

        off_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'off',
            },
            follow=True,
        )

        self.assertEqual(off_response.status_code, 200)
        self.assertContains(off_response, 'Require Kingdom Authorization Officer Verification is now Off.')
        setting.refresh_from_db()
        self.assertFalse(setting.require_kao_verification)

    def test_index_superuser_can_manage_maintenance_lock_and_see_active_sessions(self):
        admin_user, _ = self.make_person('index_maintenance_admin', 'Index Maintenance Admin')
        admin_user.is_superuser = True
        admin_user.save()
        self.client.login(username=admin_user.username, password='StrongPass!123')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Database Maintenance Lock')
        self.assertContains(response, 'Active logged-in sessions:')
        self.assertContains(response, 'Show active session users')
        self.assertContains(response, 'Index Maintenance Admin')

    def test_non_superuser_cannot_change_maintenance_lock(self):
        user, _ = self.make_person('index_maintenance_non_admin', 'Index Maintenance Non Admin')
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_maintenance_lock',
                'maintenance_lock': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only a site administrator can change the maintenance lock.')
        self.assertFalse(AuthorizationPortalSetting.objects.exists())

    def test_staff_user_cannot_change_maintenance_lock(self):
        user, _ = self.make_person('index_maintenance_staff', 'Index Maintenance Staff')
        user.is_staff = True
        user.save()
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_maintenance_lock',
                'maintenance_lock': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only a site administrator can change the maintenance lock.')
        self.assertFalse(AuthorizationPortalSetting.objects.exists())

    def test_superuser_can_turn_maintenance_lock_on_and_off(self):
        admin_user, _ = self.make_person('index_maintenance_toggle_admin', 'Index Maintenance Toggle Admin')
        admin_user.is_superuser = True
        admin_user.save()
        self.client.login(username=admin_user.username, password='StrongPass!123')

        on_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_maintenance_lock',
                'maintenance_lock': 'on',
                'maintenance_lock_message': 'Maintenance is in progress.',
            },
            follow=True,
        )

        self.assertEqual(on_response.status_code, 200)
        self.assertContains(on_response, 'Database maintenance lock is now On.')
        setting = AuthorizationPortalSetting.objects.get(pk=1)
        self.assertTrue(setting.maintenance_lock_enabled)
        self.assertEqual(setting.maintenance_lock_message, 'Maintenance is in progress.')

        off_response = self.client.post(
            reverse('index'),
            {
                'action': 'set_maintenance_lock',
                'maintenance_lock': 'off',
                'maintenance_lock_message': 'Maintenance is in progress.',
            },
            follow=True,
        )

        self.assertEqual(off_response.status_code, 200)
        self.assertContains(off_response, 'Database maintenance lock is now Off.')
        setting.refresh_from_db()
        self.assertFalse(setting.maintenance_lock_enabled)

    def test_maintenance_lock_blocks_write_requests(self):
        AuthorizationPortalSetting.objects.update_or_create(
            pk=1,
            defaults={
                'maintenance_lock_enabled': True,
                'maintenance_lock_message': 'Maintenance is in progress.',
            },
        )

        response = self.client.post(reverse('register'), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('index'))
        self.assertContains(response, 'Maintenance is in progress.')

    def test_bulk_approve_button_only_shows_for_kao_when_sign_off_enabled(self):
        kao_user, kao_person = self.make_person('index_bulk_btn_kao', 'Index Bulk Button KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_btn_prop', 'Index Bulk Button Prop')
        target_user, target_person = self.make_person(
            'index_bulk_btn_target',
            'Index Bulk Button Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        enabled_response = self.client.get(reverse('index'))
        self.assertContains(enabled_response, 'Approve All Awaiting Kingdom Authorization Officer Review')

        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        disabled_response = self.client.get(reverse('index'))
        self.assertNotContains(disabled_response, 'Approve All Awaiting Kingdom Authorization Officer Review')

    def test_kao_can_bulk_approve_needs_kingdom_without_note_flow(self):
        kao_user, kao_person = self.make_person('index_bulk_flow_kao', 'Index Bulk Flow KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_flow_prop', 'Index Bulk Flow Prop')
        target_user, target_person = self.make_person(
            'index_bulk_flow_target',
            'Index Bulk Flow Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )

        pending_jm = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        pending_weapon = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('index'),
            {'action': 'approve_all_kingdom_authorizations'},
            follow=True,
        )

        pending_jm.refresh_from_db()
        pending_weapon.refresh_from_db()
        self.assertEqual(pending_jm.status, self.status_active)
        self.assertEqual(pending_weapon.status, self.status_active)
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertContains(response, 'Approved all 2 authorizations waiting for Kingdom approval.')

    def test_kao_can_reject_needs_kingdom_from_homepage_with_required_note(self):
        kao_user, kao_person = self.make_person('index_reject_kao', 'Index Reject KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        _, proposer_person = self.make_person('index_reject_prop', 'Index Reject Proposer')
        _, target_person = self.make_person('index_reject_target', 'Index Reject Target')

        pending_auth = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        get_response = self.client.get(reverse('index'))
        self.assertContains(get_response, f'name="bad_authorization_id" value="{pending_auth.id}"')

        first = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_kingdom)
        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize the rejection.',
            self.messages_for(first),
        )

        second = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_auth.id),
                'action_note': 'Rejected after kingdom review.',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.redirect_chain)
        self.assertEqual(pending_auth.status.name, 'Rejected')
        self.assertNotIn('pending_authorization_action', self.client.session)

    def test_homepage_marshal_approve_with_note_redirects_and_refreshes(self):
        proposer_user, proposer_person = self.make_person('index_approve_prop', 'Index Approve Proposer')
        approver_user, approver_person = self.make_person('index_approve_approver', 'Index Approve Approver')
        target_user, target_person = self.make_person('index_approve_target', 'Index Approve Target')

        self.grant_authorization(proposer_person, self.style_sm_armored)
        self.grant_authorization(approver_person, self.style_sm_armored)
        pending_auth = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_pending,
            marshal=proposer_person,
        )

        self.client.login(username=approver_user.username, password='StrongPass!123')
        first = self.client.post(
            reverse('index'),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_pending)
        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize the marshal promotion.',
            self.messages_for(first),
        )

        second = self.client.post(
            reverse('index'),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'action_note': 'Concurred from homepage.',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.redirect_chain)
        self.assertEqual(pending_auth.status.name, 'Active')
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertNotContains(second, f'name="authorization_id" value="{pending_auth.id}"')

    def test_turning_sign_off_off_auto_approves_needs_kingdom(self):
        kao_user, kao_person = self.make_person('index_bulk_auto_kao', 'Index Bulk Auto KAO')
        self.appoint(kao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bulk_auto_prop', 'Index Bulk Auto Prop')
        target_user, target_person = self.make_person(
            'index_bulk_auto_target',
            'Index Bulk Auto Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )

        pending_jm = self.grant_authorization(
            target_person,
            self.style_jm_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        pending_weapon = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('index'),
            {
                'action': 'set_authorization_officer_sign_off',
                'authorization_officer_sign_off': 'off',
            },
            follow=True,
        )

        pending_jm.refresh_from_db()
        pending_weapon.refresh_from_db()
        self.assertEqual(pending_jm.status, self.status_active)
        self.assertEqual(pending_weapon.status, self.status_active)
        self.assertContains(response, 'Require Kingdom Authorization Officer Verification is now Off.')
        self.assertContains(response, 'Automatically approved all 2 authorizations waiting for Kingdom approval.')

    def test_authorization_officer_queue_shows_kingdom_and_background_not_equestrian_waiver(self):
        ao_user, ao_person = self.make_person('index_queue_ao', 'Index Queue AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_queue_prop', 'Index Queue Prop')
        target_user, target_person = self.make_person(
            'index_queue_target',
            'Index Queue Target',
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_general_riding = WeaponStyle.objects.create(
            name='General Riding',
            discipline=discipline_equestrian,
        )

        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_single_rapier,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_sm_armored,
            status=self.status_regional,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=proposer_person,
        )

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Awaiting Kingdom Authorization Officer Review')
        self.assertContains(response, 'Awaiting Background Check')
        self.assertNotContains(response, 'Awaiting Equestrian Authorization Officer Review')
        self.assertNotContains(response, 'Awaiting Regional Marshal Approval')
        self.assertNotContains(response, 'Approve As (optional):')
        self.assertContains(response, 'No file')

    def test_pending_background_check_row_uses_go_to_page_action(self):
        ao_user, ao_person = self.make_person('index_bg_ao', 'Index BG AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_prop', 'Index BG Prop')
        target_user, target_person = self.make_person('index_bg_target', 'Index BG Target')

        pending_bg = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )
        self.grant_authorization(
            target_person,
            self.style_single_rapier,
            status=self.status_kingdom,
            marshal=proposer_person,
        )

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Awaiting Background Check')
        self.assertContains(response, 'Awaiting Kingdom Authorization Officer Review')
        self.assertContains(response, 'Go To Page')
        self.assertContains(response, f'href="{reverse("user_account", kwargs={"user_id": target_user.id})}"')
        self.assertContains(response, 'class="btn btn-primary">Go To Page')
        self.assertContains(response, f'name="bad_authorization_id" value="{pending_bg.id}"')

    def test_kao_can_reject_pending_background_check_with_required_note(self):
        ao_user, ao_person = self.make_person('index_bg_reject_ao', 'Index BG Reject AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_reject_prop', 'Index BG Reject Prop')
        target_user, target_person = self.make_person('index_bg_reject_target', 'Index BG Reject Target')

        pending_bg = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )

        self.client.login(username=ao_user.username, password='StrongPass!123')
        first = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_bg.id),
            },
            follow=True,
        )

        pending_bg.refresh_from_db()
        self.assertEqual(pending_bg.status, self.status_pending_background_check)
        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize the rejection.',
            self.messages_for(first),
        )

        second = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_bg.id),
                'action_note': 'Background check was not accepted.',
            },
            follow=True,
        )

        pending_bg.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(pending_bg.status.name, 'Rejected')
        self.assertNotIn('pending_authorization_action', self.client.session)

    def test_pending_background_check_row_shows_new_upload_badge(self):
        ao_user, ao_person = self.make_person('index_bg_new_ao', 'Index BG New AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_new_prop', 'Index BG New Prop')
        target_user, target_person = self.make_person('index_bg_new_target', 'Index BG New Target')

        pending_bg = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )
        Authorization.objects.filter(id=pending_bg.id).update(
            updated_at=timezone.now() - timedelta(days=1)
        )

        bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=target_user,
        )
        bg_document.file.save('bg-new.pdf', ContentFile(b'bg-new'), save=True)
        SupportingDocumentPerson.objects.create(document=bg_document, person=target_person)

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Awaiting Background Check')
        self.assertContains(response, 'New upload')
        self.assertContains(
            response,
            f'href="{reverse("supporting_document_file", kwargs={"document_id": bg_document.id})}"',
        )
        self.assertContains(response, 'target="_blank"')

    def test_pending_background_check_row_on_file_badge_links_to_document(self):
        ao_user, ao_person = self.make_person('index_bg_link_ao', 'Index BG Link AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_link_prop', 'Index BG Link Prop')
        target_user, target_person = self.make_person('index_bg_link_target', 'Index BG Link Target')

        pending_bg = self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )

        bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=target_user,
        )
        bg_document.file.save('bg-link.pdf', ContentFile(b'bg-link'), save=True)
        SupportingDocumentPerson.objects.create(document=bg_document, person=target_person)
        Authorization.objects.filter(id=pending_bg.id).update(updated_at=timezone.now() + timedelta(minutes=1))

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'On file')
        self.assertContains(
            response,
            f'href="{reverse("supporting_document_file", kwargs={"document_id": bg_document.id})}"',
        )
        self.assertContains(response, 'target="_blank"')

    def test_pending_background_check_document_shows_kao_review_alert(self):
        ao_user, ao_person = self.make_person('index_bg_doc_ao', 'Index BG Doc AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        target_user, target_person = self.make_person('index_bg_doc_target', 'Index BG Doc Target')

        bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=target_user,
        )
        bg_document.file.save('bg-document-alert.pdf', ContentFile(b'bg-document-alert'), save=True)
        SupportingDocumentPerson.objects.create(document=bg_document, person=target_person)

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Background Checks Needing Review')
        self.assertContains(response, 'Index BG Doc Target')
        self.assertContains(response, f'href="{reverse("user_account", kwargs={"user_id": target_user.id})}"')
        self.assertContains(
            response,
            f'href="{reverse("supporting_document_file", kwargs={"document_id": bg_document.id})}"',
        )

    def test_pending_background_check_document_alert_suppressed_when_pending_authorization_exists(self):
        ao_user, ao_person = self.make_person('index_bg_doc_dupe_ao', 'Index BG Doc Dupe AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        proposer_user, proposer_person = self.make_person('index_bg_doc_dupe_prop', 'Index BG Doc Dupe Prop')
        target_user, target_person = self.make_person('index_bg_doc_dupe_target', 'Index BG Doc Dupe Target')
        self.grant_authorization(
            target_person,
            self.style_weapon_armored,
            status=self.status_pending_background_check,
            marshal=proposer_person,
        )

        bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=target_user,
        )
        bg_document.file.save('bg-document-dupe.pdf', ContentFile(b'bg-document-dupe'), save=True)
        SupportingDocumentPerson.objects.create(document=bg_document, person=target_person)

        self.client.login(username=ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Awaiting Background Check')
        self.assertNotContains(response, 'Background Checks Needing Review')
        self.assertContains(response, 'New upload')

    def test_setting_background_check_expiration_clears_document_review_alert(self):
        ao_user, ao_person = self.make_person('index_bg_doc_clear_ao', 'Index BG Doc Clear AO')
        self.appoint(ao_person, self.branch_an_tir, self.discipline_auth_officer)
        target_user, target_person = self.make_person('index_bg_doc_clear_target', 'Index BG Doc Clear Target')
        bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=target_user,
        )
        bg_document.file.save('bg-document-clear.pdf', ContentFile(b'bg-document-clear'), save=True)
        SupportingDocumentPerson.objects.create(document=bg_document, person=target_person)

        self.client.login(username=ao_user.username, password='StrongPass!123')
        bg_date = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            target_user,
            target_person,
            background_check_expiration=bg_date.isoformat(),
        )
        self.client.post(
            reverse('user_account', kwargs={'user_id': target_user.id}),
            payload,
            follow=True,
        )
        bg_document.refresh_from_db()
        response = self.client.get(reverse('index'))

        self.assertEqual(bg_document.review_status, SupportingDocument.ReviewStatus.ACCEPTED)
        self.assertEqual(bg_document.reviewed_by, ao_user)
        self.assertIsNotNone(bg_document.reviewed_at)
        self.assertNotContains(response, 'Background Checks Needing Review')

    def test_kingdom_equestrian_authorization_officer_queue_shows_needs_kingdom_equestrian_waiver(self):
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_general_riding = WeaponStyle.objects.create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person('index_eq_officer', 'Index EQ Officer')
        target_user, target_person = self.make_person('index_eq_target', 'Index EQ Target')
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        pending_eq = self.grant_authorization(
            target_person,
            style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=eq_officer_person,
        )

        self.client.login(username=eq_officer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Awaiting Equestrian Authorization Officer Review')
        self.assertContains(response, 'No file')
        self.assertContains(response, f'name="bad_authorization_id" value="{pending_eq.id}"')

    def test_kingdom_equestrian_authorization_officer_can_reject_needs_kingdom_equestrian_waiver(self):
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        style_general_riding = WeaponStyle.objects.create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person('index_eq_reject_officer', 'Index EQ Reject Officer')
        target_user, target_person = self.make_person('index_eq_reject_target', 'Index EQ Reject Target')
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        pending_eq = self.grant_authorization(
            target_person,
            style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=eq_officer_person,
        )

        self.client.login(username=eq_officer_user.username, password='StrongPass!123')
        first = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_eq.id),
            },
            follow=True,
        )

        pending_eq.refresh_from_db()
        self.assertEqual(pending_eq.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize the rejection.',
            self.messages_for(first),
        )

        second = self.client.post(
            reverse('index'),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(pending_eq.id),
                'action_note': 'Equestrian waiver was not accepted.',
            },
            follow=True,
        )

        pending_eq.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(pending_eq.status.name, 'Rejected')
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Authorization rejected.',
            self.messages_for(second),
        )

    def test_unique_name_redirects_to_fighter_page(self):
        unique_user, _ = self.make_person('unique_index_user', 'Unique Fighter')

        response = self.client.get(reverse('index'), {'sca_name': 'Unique Fighter'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': unique_user.id}))

    def test_duplicate_name_renders_match_table_for_anonymous_user(self):
        self.make_person('dup_index_1', 'Duplicate Fighter')
        self.make_person('dup_index_2', 'Duplicate Fighter')

        response = self.client.get(reverse('index'), {'sca_name': 'Duplicate Fighter'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertEqual(response.context['name_matches'].count(), 2)

    def test_duplicate_name_renders_match_table_for_authenticated_user(self):
        viewer_user, _ = self.make_person('index_viewer', 'Index Viewer')
        self.make_person('dup_index_auth_1', 'Duplicate Auth Fighter')
        self.make_person('dup_index_auth_2', 'Duplicate Auth Fighter')

        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('index'), {'sca_name': 'Duplicate Auth Fighter'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/index.html')
        self.assertEqual(response.context['name_matches'].count(), 2)


class SearchViewTests(ViewTestBase):
    def test_goal_search_renders_search_form(self):
        response = self.client.get(reverse('search'), {'goal': 'search'})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/search_form.html')

    def test_membership_filter_in_table_view(self):
        _, fighter_a = self.make_person('search_table_a', 'Search Table A', membership='1111111111')
        _, fighter_b = self.make_person('search_table_b', 'Search Table B', membership='2222222222')
        auth_a = self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'membership': '1111111111'})

        self.assertEqual(response.status_code, 200)
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertEqual(page_ids, [auth_a.id])

    def test_membership_filter_in_card_view(self):
        _, fighter_a = self.make_person('search_card_a', 'Search Card A', membership='3333333333')
        _, fighter_b = self.make_person('search_card_b', 'Search Card B', membership='4444444444')
        self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'membership': '3333333333', 'view': 'card'})

        self.assertEqual(response.status_code, 200)
        people = list(response.context['page_obj'].object_list)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].sca_name, 'Search Card A')
        self.assertTrue(hasattr(people[0], 'filtered_authorizations'))
        self.assertEqual(len(people[0].filtered_authorizations), 1)

    def test_search_excludes_staff_user_from_options_and_results(self):
        admin_user, system_person = self.make_person(
            'search_staff_admin',
            'Administrator',
            membership='150501',
        )
        admin_user.is_staff = True
        admin_user.save()
        _, visible_person = self.make_person('search_visible_fighter', 'Search Visible Fighter')
        system_auth = self.grant_authorization(system_person, self.style_weapon_armored, status=self.status_active)
        visible_auth = self.grant_authorization(visible_person, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(reverse('search'), {'goal': 'search'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Administrator', list(response.context['sca_name_options']))

        response = self.client.get(reverse('search'), {'membership': visible_person.user.membership})
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertIn(visible_auth.id, page_ids)

        response = self.client.get(reverse('search'), {'membership': system_person.user.membership})
        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertNotIn(system_auth.id, page_ids)

    def test_start_date_filter_uses_effective_expiration_for_youth_marshal(self):
        _, marshal_person = self.make_person('search_date_marshal', 'Search Date Marshal')
        _, youth_person = self.make_person(
            'search_date_youth',
            'Search Date Youth',
            background_check_expiration=date.today() + timedelta(days=15),
            membership_expiration=date.today() + relativedelta(years=2),
        )
        _, normal_person = self.make_person('search_date_normal', 'Search Date Normal')

        youth_auth = self.grant_authorization(
            youth_person,
            self.style_sm_youth_armored,
            status=self.status_active,
            expiration=date.today() + relativedelta(years=2),
            marshal=marshal_person,
        )
        normal_auth = self.grant_authorization(
            normal_person,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() + timedelta(days=120),
            marshal=marshal_person,
        )

        response = self.client.get(
            reverse('search'),
            {'start_date': (date.today() + timedelta(days=60)).isoformat()},
        )

        page_ids = [auth.id for auth in response.context['page_obj'].object_list]
        self.assertIn(normal_auth.id, page_ids)
        self.assertNotIn(youth_auth.id, page_ids)

    def test_sort_expiration_uses_effective_expiration_annotation(self):
        _, marshal_person = self.make_person('search_sort_marshal', 'Search Sort Marshal')
        _, youth_person = self.make_person(
            'search_sort_youth',
            'Search Sort Youth',
            background_check_expiration=date.today() + timedelta(days=10),
            membership_expiration=date.today() + relativedelta(years=2),
        )
        _, normal_person = self.make_person('search_sort_normal', 'Search Sort Normal')

        youth_auth = self.grant_authorization(
            youth_person,
            self.style_sm_youth_armored,
            status=self.status_active,
            expiration=date.today() + relativedelta(years=2),
            marshal=marshal_person,
        )
        self.grant_authorization(
            normal_person,
            self.style_weapon_armored,
            status=self.status_active,
            expiration=date.today() + timedelta(days=20),
            marshal=marshal_person,
        )

        response = self.client.get(reverse('search'), {'sort': 'expiration'})

        first_auth = response.context['page_obj'].object_list[0]
        self.assertEqual(first_auth.id, youth_auth.id)

    def test_invalid_start_date_reports_error_message(self):
        response = self.client.get(reverse('search'), {'start_date': 'not-a-date'}, follow=True)

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('Start date must be in YYYY-MM-DD format.', messages)

    def test_search_form_membership_input_attributes(self):
        response = self.client.get(reverse('search'), {'goal': 'search'})
        content = response.content.decode('utf-8')

        self.assertIn('id="membership"', content)
        self.assertIn('type="text"', content)
        self.assertIn('maxlength="20"', content)

    def test_table_view_download_csv_respects_filters_and_includes_all_pages(self):
        _, fighter_a = self.make_person('search_csv_a', 'Search CSV A', branch=self.branch_lg)
        _, fighter_b = self.make_person('search_csv_b', 'Search CSV B', branch=self.branch_lg)
        _, fighter_c = self.make_person('search_csv_c', 'Search CSV C', branch=self.branch_gd)
        self.grant_authorization(fighter_a, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_b, self.style_weapon_armored, status=self.status_active)
        self.grant_authorization(fighter_c, self.style_weapon_armored, status=self.status_active)

        response = self.client.get(
            reverse('search'),
            {
                'branch': self.branch_lg.name,
                'items_per_page': '1',
                'download': 'csv',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename=\"authorizations_search.csv\"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig')
        self.assertIn('SCA Name,Region,Branch,Discipline,Weapon Style,Marshal,Expiration,Minor', content)
        self.assertIn('Search CSV A', content)
        self.assertIn('Search CSV B', content)
        self.assertNotIn('Search CSV C', content)
        self.assertNotIn('<a ', content)


class DeleteAuthorizationsViewTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.style_general_riding = WeaponStyle.objects.create(name='General Riding', discipline=cls.discipline_equestrian)
        cls.style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=cls.discipline_rapier)

    def make_kao(self, username='delete_kao', sca_name='Delete KAO'):
        user, person = self.make_person(username, sca_name)
        self.appoint(person, self.branch_an_tir, self.discipline_auth_officer)
        return user, person

    def make_keao(self, username='delete_keao', sca_name='Delete KEAO'):
        user, person = self.make_person(username, sca_name)
        self.appoint(person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        return user, person

    def test_kao_can_delete_non_equestrian_authorization_and_close_current_interval(self):
        kao_user, _ = self.make_kao()
        _, fighter = self.make_person('delete_target', 'Delete Target')
        authorization = self.grant_authorization(
            fighter,
            self.style_weapon_armored,
            expiration=date.today() + relativedelta(years=1),
        )
        self.assertTrue(
            AuthorizationValidityInterval.objects.filter(
                authorization=authorization,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(authorization.id),
                'action_note': 'Entered under the wrong style.',
            },
        )

        authorization.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}))
        self.assertEqual(authorization.status.name, 'Inactive')
        self.assertEqual(authorization.updated_by, kao_user)
        self.assertFalse(
            AuthorizationValidityInterval.objects.filter(
                authorization=authorization,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )
        note = AuthorizationNote.objects.get(authorization=authorization)
        self.assertEqual(note.action, 'officer_deleted')
        self.assertIn('Entered under the wrong style.', note.note)

    def test_delete_authorization_requires_note(self):
        kao_user, _ = self.make_kao('delete_note_kao', 'Delete Note KAO')
        _, fighter = self.make_person('delete_note_target', 'Delete Note Target')
        authorization = self.grant_authorization(fighter, self.style_weapon_armored)

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(authorization.id),
            },
            follow=True,
        )

        authorization.refresh_from_db()
        self.assertEqual(authorization.status.name, 'Active')
        self.assertFalse(AuthorizationNote.objects.filter(authorization=authorization).exists())
        self.assertIn('A note is required to delete an authorization.', self.messages_for(response))

    def test_delete_prerequisite_closes_dependent_current_interval(self):
        kao_user, _ = self.make_kao('delete_prereq_kao', 'Delete Prereq KAO')
        _, fighter = self.make_person('delete_prereq_target', 'Delete Prereq Target')
        prerequisite = self.grant_authorization(
            fighter,
            self.style_single_rapier,
            expiration=date.today() + relativedelta(years=1),
        )
        dependent = self.grant_authorization(
            fighter,
            self.style_rapier_dagger,
            expiration=date.today() + relativedelta(years=1),
        )
        self.assertTrue(
            AuthorizationValidityInterval.objects.filter(
                authorization=dependent,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )

        self.client.login(username=kao_user.username, password='StrongPass!123')
        self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(prerequisite.id),
                'action_note': 'Single Sword entered for the wrong fighter.',
            },
        )

        prerequisite.refresh_from_db()
        dependent.refresh_from_db()
        self.assertEqual(prerequisite.status.name, 'Inactive')
        self.assertEqual(dependent.status.name, 'Active')
        self.assertFalse(
            AuthorizationValidityInterval.objects.filter(
                authorization=dependent,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kao_cannot_delete_equestrian_authorization(self):
        kao_user, _ = self.make_kao('delete_scope_kao', 'Delete Scope KAO')
        _, fighter = self.make_person('delete_scope_target', 'Delete Scope Target')
        authorization = self.grant_authorization(fighter, self.style_general_riding)

        self.client.login(username=kao_user.username, password='StrongPass!123')
        page = self.client.get(reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}))
        self.assertNotContains(page, 'General Riding')

        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(authorization.id),
                'action_note': 'Attempting out-of-scope delete.',
            },
            follow=True,
        )

        authorization.refresh_from_db()
        self.assertEqual(authorization.status.name, 'Active')
        self.assertIn('You do not have permission to delete that authorization.', self.messages_for(response))

    def test_keao_can_delete_equestrian_authorization(self):
        keao_user, _ = self.make_keao()
        _, fighter = self.make_person('delete_eq_target', 'Delete Eq Target')
        authorization = self.grant_authorization(fighter, self.style_general_riding)

        self.client.login(username=keao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(authorization.id),
                'action_note': 'Wrong equestrian row.',
            },
        )

        authorization.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(authorization.status.name, 'Inactive')

    def test_non_officer_cannot_access_delete_authorizations(self):
        user, _ = self.make_person('delete_regular', 'Delete Regular')

        self.client.login(username=user.username, password='StrongPass!123')
        response = self.client.get(reverse('delete_authorizations'))

        self.assertEqual(response.status_code, 403)

    def test_fighter_page_links_kao_to_delete_authorizations(self):
        kao_user, _ = self.make_kao('delete_link_kao', 'Delete Link KAO')
        _, fighter = self.make_person('delete_link_target', 'Delete Link Target')

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': fighter.user_id}))

        self.assertContains(response, reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}))
        self.assertContains(response, 'Delete Authorizations')

    def test_delete_page_uses_history_and_actions_modal_copy(self):
        kao_user, _ = self.make_kao('delete_copy_kao', 'Delete Copy KAO')
        _, fighter = self.make_person('delete_copy_target', 'Delete Copy Target')
        self.grant_authorization(fighter, self.style_weapon_armored)

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('delete_authorizations_for_person', kwargs={'person_id': fighter.user_id}))

        self.assertContains(
            response,
            'This marks the authorization inactive. It does not erase the notes, history, or any actions performed by the fighter.',
        )

    def test_delete_lookup_includes_fighter_with_pending_authorization(self):
        kao_user, _ = self.make_kao('delete_lookup_kao', 'Delete Lookup KAO')
        _, fighter = self.make_person('delete_lookup_target', 'Delete Lookup Target')
        self.grant_authorization(
            fighter,
            self.style_weapon_armored,
            status=self.status_kingdom,
        )

        self.client.login(username=kao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('officer_person_lookup'), {
            'q': 'Delete Lookup Target',
            'purpose': 'delete_authorizations',
        })

        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['results'][0]['user_id'], fighter.user_id)


@override_settings(AUTHZ_TEST_FEATURES=False)
class RegisterViewTests(ViewTestBase):
    def test_faq_includes_membership_update_guidance(self):
        response = self.client.get(reverse('faq'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="membership-update"')
        self.assertContains(response, 'Why was my membership update rejected?')
        self.assertContains(response, 'middle initial')

    def test_faq_includes_authorization_status_flowchart(self):
        response = self.client.get(reverse('faq'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="authorization-status-flow"')
        self.assertContains(response, 'id="authorization-status-flow-chart"')
        self.assertContains(response, 'data-drag-scroll')
        self.assertContains(response, 'setupSemanticFlowChart')
        self.assertContains(response, 'parseFlowRoute')
        self.assertContains(response, 'status-flow-object-highlight')
        self.assertContains(response, 'highlightOutgoingFlowRoutes')
        self.assertContains(response, 'data-flow-zoom-action="in"')
        self.assertContains(response, 'applyFlowZoom')
        self.assertContains(response, 'let flowZoom = 0.3')
        self.assertContains(response, 'data-flow-start')
        self.assertContains(response, 'setFlowInfoObject')
        self.assertContains(response, 'animatePaneScrollTo')
        self.assertContains(response, 'centerSvgBoxHorizontallyInPane')
        self.assertContains(response, 'Follow route to')
        self.assertContains(response, 'status-flow-route-path-highlight')
        self.assertContains(response, 'status-flow-route-label-highlight')
        self.assertContains(response, 'centerSvgBoxInPane')
        self.assertContains(response, 'flowObjectDetails')
        self.assertContains(response, 'Cleared by')
        self.assertContains(response, 'Appears on')
        self.assertContains(response, 'Awaiting Background Check')
        self.assertContains(response, 'authorization_status_workflow.svg')

    def test_register_get_renders_template(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/register.html')
        self.assertContains(
            response,
            'letters, digits, and the symbols @, ., +, -, and _.'
        )

    @patch('authorizations.views.send_mail')
    def test_register_post_creates_user_and_person(self, mock_send_mail):
        payload = self.registration_payload(username='register_ok', email='register_ok@example.com')
        self.seed_membership_roster(
            payload['membership'],
            payload['first_name'],
            payload['last_name'],
            date.fromisoformat(payload['membership_expiration']),
        )

        response = self.client.post(reverse('register'), payload)

        created_user = User.objects.get(username='register_ok')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': created_user.id}))
        self.assertFalse(created_user.is_active)
        self.assertTrue(Person.objects.filter(user=created_user).exists())
        mock_send_mail.assert_called_once()

    def test_register_rejects_membership_not_found_in_roster(self):
        payload = self.registration_payload(
            username='register_membership_not_found',
            email='register_membership_not_found@example.com',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Invalid membership information. Please review the',
        )
        self.assertContains(response, '<a href="/faq/#membership-update">membership FAQ</a>', html=True)
        self.assertContains(response, 'for information on how membership validation works.')
        self.assertFalse(User.objects.filter(username='register_membership_not_found').exists())

    @override_settings(AUTHZ_TEST_FEATURES=True)
    @patch('authorizations.views.send_mail')
    def test_register_skips_membership_roster_validation_in_test_mode(self, mock_send_mail):
        payload = self.registration_payload(
            username='register_test_mode_skip',
            email='register_test_mode_skip@example.com',
        )

        response = self.client.post(reverse('register'), payload)

        created_user = User.objects.get(username='register_test_mode_skip')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': created_user.id}))
        self.assertEqual(
            created_user.waiver_expiration,
            date.fromisoformat(payload['membership_expiration']),
        )
        mock_send_mail.assert_called_once()

    def test_register_minor_requires_birthday(self):
        payload = self.registration_payload(
            username='register_minor',
            email='register_minor@example.com',
            is_minor='on',
            birthday='',
            membership='',
            membership_expiration='',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A birthday must be provided for minors.')
        self.assertFalse(User.objects.filter(username='register_minor').exists())

    def test_register_requires_membership_and_expiration_together(self):
        payload = self.registration_payload(
            username='register_membership_pair',
            email='register_membership_pair@example.com',
            membership='1234567',
            membership_expiration='',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please correct the errors below.')
        self.assertContains(response, 'Please correct the following errors:')
        self.assertContains(response, 'Must have both a membership number and expiration or neither.')

    def test_register_shows_explicit_state_and_postal_errors(self):
        payload = self.registration_payload(
            username='register_bad_location',
            email='register_bad_location@example.com',
            state_province='California',
            postal_code='12345',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
        )
        self.assertContains(
            response,
            'Postal code must be within An Tir.',
        )

    def test_register_rejects_duplicate_membership(self):
        self.make_person('register_existing_member', 'Register Existing Member', membership='9999999999')
        payload = self.registration_payload(
            username='register_duplicate_member',
            email='register_duplicate_member@example.com',
            membership='9999999999',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A user with this membership number already exists.')
        self.assertContains(response, 'account recovery options on the login page')
        self.assertContains(response, 'Kingdom Authorization Officer')

    def test_register_rejects_duplicate_first_last_email(self):
        User.objects.create_user(
            username='register_existing_identity',
            password='StrongPass!123',
            email='dup.identity@example.com',
            first_name='Dup',
            last_name='Identity',
        )
        payload = self.registration_payload(
            username='register_duplicate_identity',
            email='dup.identity@example.com',
            first_name='Dup',
            last_name='Identity',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this first name, last name, and email already exists.')
        self.assertContains(response, 'recover your credentials from the login page')
        self.assertContains(response, 'merge duplicate accounts')

    def test_register_rejects_duplicate_first_last_email_case_insensitive(self):
        User.objects.create_user(
            username='register_existing_identity_case',
            password='StrongPass!123',
            email='Case.Match@Example.com',
            first_name='Case',
            last_name='Match',
        )
        payload = self.registration_payload(
            username='register_duplicate_identity_case',
            email='case.match@example.com',
            first_name='case',
            last_name='match',
        )

        response = self.client.post(reverse('register'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'An account with this first name, last name, and email already exists.')

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_background_check_field_renders_when_feature_enabled(self):
        response = self.client.get(reverse('register'))

        self.assertContains(response, 'name="background_check_expiration"')
        self.assertContains(response, 'Membership number must be between 1 and 20 digits.')
        self.assertContains(
            response,
            'Postal code must be within An Tir:'
        )
        self.assertContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver. After that you will be able to add authorizations to yourself.'
        )

    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_background_check_field_hidden_when_feature_disabled(self):
        response = self.client.get(reverse('register'))

        self.assertNotContains(response, 'name="background_check_expiration"')
        self.assertContains(
            response,
            'If entering a membership number, your First and Last name must match what is listed in your SCA membership account'
        )
        self.assertContains(response, 'Postal code must be within An Tir.')
        self.assertContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver.'
        )
        self.assertNotContains(response, 'Membership number must be between 1 and 20 digits.')
        self.assertNotContains(
            response,
            'After you set your password, log in and go to "My Account" to sign your waiver. After that you will be able to add authorizations to yourself.'
        )


class TombstoneBehaviorTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ao_user = User.objects.create_user(
            username='merge_ao',
            password='StrongPass!123',
            email='merge_ao@example.com',
            first_name='Merge',
            last_name='AO',
            membership='9191919191',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Merge AO',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

    def _create_merged_user(self, username, sca_name, *, merged_into, email):
        user = User.objects.create_user(
            username=username,
            password='StrongPass!123',
            email=email,
            first_name=sca_name.split()[0],
            last_name='Merged',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        person = Person.objects.create(
            user=user,
            sca_name=sca_name,
            branch=self.branch_gd,
        )
        user.merged_into = merged_into
        user.merged_at = timezone.now()
        user.is_active = False
        user.save()
        return user, person

    def test_fighter_redirects_merged_person_to_survivor(self):
        survivor_user, _ = self.make_person('fighter_survivor', 'Fighter Survivor')
        source_user, _ = self._create_merged_user(
            'fighter_source_merged',
            'Fighter Source',
            merged_into=survivor_user,
            email='fighter_source_merged@example.com',
        )

        response = self.client.get(reverse('fighter', kwargs={'person_id': source_user.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': survivor_user.id}))

    def test_fighter_page_allows_direct_system_admin_lookup(self):
        system_user, _ = self.make_person(
            'fighter_system_admin',
            'Administrator',
            user_id=SYSTEM_USER_IDS[0],
            membership='150502',
        )

        response = self.client.get(reverse('fighter', kwargs={'person_id': system_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Administrator')

    def test_fighter_page_lists_staff_as_database_administrator(self):
        staff_user, _ = self.make_person('fighter_staff_admin', 'Fighter Staff Admin')
        staff_user.is_staff = True
        staff_user.save(update_fields=['is_staff'])

        response = self.client.get(reverse('fighter', kwargs={'person_id': staff_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Officer Positions')
        self.assertContains(response, 'Database Administrator')

    @patch('authorizations.views.send_mail')
    def test_fighter_login_instructions_can_be_requested_anonymously(self, mock_send_mail):
        target_user, _ = self.make_person('fighter_login_target', 'Fighter Login Target')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'send_login_instructions',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        email_body = mock_send_mail.call_args[0][1]
        self.assertIn(target_user.username, email_body)
        self.assertIn('Password Reset Link:', email_body)
        self.assertNotIn('Login URL:', email_body)
        self.assertNotIn(reverse('login'), email_body)
        response_messages = self.messages_for(response)
        self.assertTrue(any('Login instructions have been sent to the email on file.' in message for message in response_messages))
        self.assertTrue(any(settings.DEFAULT_FROM_EMAIL in message for message in response_messages))

    @patch('authorizations.views.send_mail')
    def test_fighter_login_instructions_does_not_send_for_merged_record(self, mock_send_mail):
        survivor_user, _ = self.make_person('fighter_login_survivor', 'Fighter Login Survivor')
        source_user, _ = self._create_merged_user(
            'fighter_login_source_merged',
            'Fighter Login Source',
            merged_into=survivor_user,
            email='fighter_login_source_merged@example.com',
        )

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': source_user.id}),
            {
                'action': 'send_login_instructions',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('fighter', kwargs={'person_id': survivor_user.id}))
        self.assertEqual(mock_send_mail.call_count, 0)

    @patch('authorizations.views.send_mail')
    def test_username_recovery_excludes_merged_accounts(self, mock_send_mail):
        survivor_user, _ = self.make_person('recover_survivor', 'Recover Survivor', email='survivor-recover@example.com')
        active_user, _ = self.make_person('recover_active', 'Recover Active', email='shared-recover@example.com')
        merged_user, _ = self._create_merged_user(
            'recover_merged',
            'Recover Merged',
            merged_into=survivor_user,
            email='shared-recover@example.com',
        )

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'get_username',
                'email': 'shared-recover@example.com',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        email_body = mock_send_mail.call_args[0][1]
        self.assertIn(active_user.username, email_body)
        self.assertNotIn(merged_user.username, email_body)

    @patch('authorizations.views.send_mail')
    def test_password_reset_by_username_ignores_merged_accounts(self, mock_send_mail):
        survivor_user, _ = self.make_person('reset_survivor', 'Reset Survivor')
        merged_user, _ = self._create_merged_user(
            'reset_merged',
            'Reset Merged',
            merged_into=survivor_user,
            email='reset_merged@example.com',
        )

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'reset_password',
                'username': merged_user.username,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 0)
        response_messages = self.messages_for(response)
        self.assertTrue(
            any(
                'If an account exists for that username, a password reset link has been sent to the email on file.'
                in message
                for message in response_messages
            )
        )
        self.assertTrue(
            any('Please check your spam or junk folder' in message for message in response_messages)
        )

    @override_settings(AUTHZ_TEST_FEATURES=False)
    @patch('authorizations.views.send_mail')
    @patch('authorizations.views._throttle_request', return_value=False)
    def test_password_reset_uses_production_throttle_defaults(self, mock_throttle, mock_send_mail):
        user, _ = self.make_person('reset_prod_limit_user', 'Reset Prod Limit User')

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'reset_password',
                'username': user.username,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        self.assertEqual(mock_throttle.call_count, 2)
        self.assertEqual(mock_throttle.call_args_list[0].args[1:], (3, 15 * 60))
        self.assertEqual(mock_throttle.call_args_list[1].args[1:], (5, 15 * 60))

    @override_settings(AUTHZ_TEST_FEATURES=True)
    @patch('authorizations.views.send_mail')
    @patch('authorizations.views._throttle_request', return_value=False)
    def test_password_reset_uses_relaxed_test_throttle_defaults(self, mock_throttle, mock_send_mail):
        user, _ = self.make_person('reset_test_limit_user', 'Reset Test Limit User')

        response = self.client.post(
            reverse('recover_account'),
            {
                'action': 'reset_password',
                'username': user.username,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send_mail.call_count, 1)
        self.assertEqual(mock_throttle.call_count, 2)
        self.assertEqual(mock_throttle.call_args_list[0].args[1:], (50, 5 * 60))
        self.assertEqual(mock_throttle.call_args_list[1].args[1:], (100, 5 * 60))

    def test_password_reset_token_rejects_merged_account(self):
        survivor_user, _ = self.make_person('token_survivor', 'Token Survivor')
        merged_user, _ = self._create_merged_user(
            'token_merged',
            'Token Merged',
            merged_into=survivor_user,
            email='token_merged@example.com',
        )
        uidb64 = urlsafe_base64_encode(force_bytes(merged_user.pk))
        token = PasswordResetTokenGenerator().make_token(merged_user)

        response = self.client.get(reverse('password_reset_token', kwargs={'uidb64': uidb64, 'token': token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or has expired')

    @patch('authorizations.views.send_mail')
    def test_logged_in_password_change_sends_security_notice(self, mock_send_mail):
        user, _ = self.make_person(
            'password_change_user',
            'Password Change User',
            email='password-change@example.com',
        )
        self.client.login(username=user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('password_reset', kwargs={'user_id': user.id}),
            {
                'password': 'NewStrongPass!456',
                'confirmation': 'NewStrongPass!456',
            },
            follow=True,
        )

        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(user.check_password('NewStrongPass!456'))
        self.assertEqual(mock_send_mail.call_count, 1)
        call_args = mock_send_mail.call_args.args
        self.assertEqual(call_args[0], 'Password changed for an An Tir Authorization account')
        self.assertEqual(call_args[3], ['password-change@example.com'])
        self.assertIn('Society name: Password Change User', call_args[1])
        self.assertIn('Mundane name: Password Tester', call_args[1])
        self.assertIn(reverse('fighter', kwargs={'person_id': user.id}), call_args[1])
        self.assertIn('Pacific Time', call_args[1])
        self.assertIn('antir.authorization.database@gmail.com', call_args[1])

    @patch('authorizations.views.send_mail')
    def test_password_reset_token_sends_security_notice_after_password_set(self, mock_send_mail):
        user, _ = self.make_person(
            'password_token_user',
            'Password Token User',
            email='password-token@example.com',
        )
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = PasswordResetTokenGenerator().make_token(user)

        response = self.client.post(
            reverse('password_reset_token', kwargs={'uidb64': uidb64, 'token': token}),
            {
                'password': 'NewStrongPass!456',
                'confirmation': 'NewStrongPass!456',
            },
            follow=True,
        )

        user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(user.check_password('NewStrongPass!456'))
        self.assertEqual(mock_send_mail.call_count, 1)
        call_args = mock_send_mail.call_args.args
        self.assertEqual(call_args[0], 'Password changed for an An Tir Authorization account')
        self.assertEqual(call_args[3], ['password-token@example.com'])
        self.assertIn('Society name: Password Token User', call_args[1])
        self.assertIn(reverse('fighter', kwargs={'person_id': user.id}), call_args[1])

    def test_merge_page_search_excludes_already_merged_identities(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        survivor_user, _ = self.make_person('merge_page_survivor', 'Merge Page Survivor')
        active_user, _ = self.make_person('merge_page_active', 'Duplicate Merge Name')
        merged_user, _ = self._create_merged_user(
            'merge_page_merged',
            'Duplicate Merge Name',
            merged_into=survivor_user,
            email='merge_page_merged@example.com',
        )

        response = self.client.get(
            reverse('merge_accounts'),
            {
                'action': 'search_old',
                'old_sca_name': 'Duplicate Merge Name',
            },
        )

        self.assertEqual(response.status_code, 200)
        returned_ids = [person.user_id for person in response.context['old_matches']]
        self.assertIn(active_user.id, returned_ids)
        self.assertNotIn(merged_user.id, returned_ids)


@override_settings(AUTHZ_TEST_FEATURES=False)
class UserAccountViewTests(ViewTestBase):
    def _build_xlsx(self, rows):
        def column_name(index):
            name = ''
            while index:
                index, remainder = divmod(index - 1, 26)
                name = chr(65 + remainder) + name
            return name

        def escape_xml(value):
            return (
                str(value)
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
            )

        sheet_rows = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for column_index, value in enumerate(row, start=1):
                reference = f'{column_name(column_index)}{row_index}'
                if isinstance(value, (int, float)):
                    cells.append(f'<c r="{reference}"><v>{value}</v></c>')
                else:
                    cells.append(
                        f'<c r="{reference}" t="inlineStr"><is><t>{escape_xml(value)}</t></is></c>'
                    )
            sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

        workbook = BytesIO()
        with zipfile.ZipFile(workbook, 'w') as archive:
            archive.writestr(
                '[Content_Types].xml',
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '</Types>',
            )
            archive.writestr(
                '_rels/.rels',
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>',
            )
            archive.writestr(
                'xl/workbook.xml',
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>',
            )
            archive.writestr(
                'xl/_rels/workbook.xml.rels',
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                '</Relationships>',
            )
            archive.writestr(
                'xl/worksheets/sheet1.xml',
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(sheet_rows)}</sheetData>'
                '</worksheet>',
            )
        return workbook.getvalue()

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.owner_user = User.objects.create_user(
            username='account_owner',
            password='StrongPass!123',
            email='owner@example.com',
            first_name='Owner',
            last_name='User',
            membership='5555555555',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.owner_person = Person.objects.create(
            user=cls.owner_user,
            sca_name='Owner of Account',
            branch=cls.branch_gd,
        )

        cls.child_user = User.objects.create_user(
            username='account_child',
            password='StrongPass!123',
            email='child@example.com',
            first_name='Child',
            last_name='User',
            membership='6666666666',
            membership_expiration=date.today() + relativedelta(years=1),
            birthday=date.today() - relativedelta(years=15),
            state_province='Oregon',
            country='United States',
        )
        cls.child_person = Person.objects.create(
            user=cls.child_user,
            sca_name='Child of Owner',
            branch=cls.branch_gd,
            parent=cls.owner_person,
        )

        cls.other_user = User.objects.create_user(
            username='account_other',
            password='StrongPass!123',
            email='other@example.com',
            first_name='Other',
            last_name='User',
            membership='7777777777',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.other_person = Person.objects.create(
            user=cls.other_user,
            sca_name='Other User',
            branch=cls.branch_gd,
        )

        cls.ao_user = User.objects.create_user(
            username='account_ao',
            password='StrongPass!123',
            email='ao@example.com',
            first_name='Auth',
            last_name='Officer',
            membership='8888888888',
            membership_expiration=date.today() + relativedelta(years=1),
            background_check_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Authorization Officer',
            branch=cls.branch_gd,
        )
        cls.paper_concurrer_user = User.objects.create_user(
            username='paper_concurrer',
            password='StrongPass!123',
            email='paper_concurrer@example.com',
            first_name='Paper',
            last_name='Concurrer',
            membership='',
            membership_expiration=date.today() + relativedelta(years=1),
            background_check_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.paper_concurrer_person = Person.objects.create(
            user=cls.paper_concurrer_user,
            sca_name='Paper Concurrer',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        for marshal_person in (cls.ao_person, cls.other_person, cls.paper_concurrer_person):
            if marshal_person.user.background_check_expiration is None:
                marshal_person.user.background_check_expiration = date.today() + relativedelta(years=1)
                marshal_person.user.save(update_fields=['background_check_expiration'])
            Authorization.objects.create(
                person=marshal_person,
                style=cls.style_sm_armored,
                status=cls.status_active,
                marshal=marshal_person,
                expiration=date(2029, 5, 9),
            )
            Authorization.objects.create(
                person=marshal_person,
                style=cls.style_sm_youth_armored,
                status=cls.status_active,
                marshal=marshal_person,
                expiration=date(2027, 5, 10),
            )
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.style_eq_general_riding = WeaponStyle.objects.create(
            name='General Riding',
            discipline=cls.discipline_equestrian,
        )

    def paper_concurrer_fields(self, person=None, count=1):
        person = person or self.paper_concurrer_person
        return {
            'concurring_officer_sca_name': [person.sca_name] * count,
            'concurring_officer_first_name': [person.user.first_name] * count,
            'concurring_officer_last_name': [person.user.last_name] * count,
        }

    def test_owner_can_view_own_account(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_parent_can_view_minor_child_account(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.child_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_parent_account_lists_child_account_links(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Child Accounts')
        self.assertContains(response, 'Child of Owner (account_child)')
        self.assertContains(
            response,
            reverse('user_account', kwargs={'user_id': self.child_user.id}),
        )

    def test_non_owner_non_parent_non_ao_is_forbidden(self):
        self.client.login(username=self.other_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 403)

    def test_authorization_officer_can_view_any_account(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/user_account.html')

    def test_user_account_page_uses_searchable_compact_selects_for_profile_fields(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="user_account_form"')
        self.assertContains(response, 'class="form-group register-inline-field"')
        self.assertContains(response, "initUserAccountSearchableSelect('id_state_province');")
        self.assertContains(response, "initUserAccountSearchableSelect('id_title');")
        self.assertContains(response, "initUserAccountSearchableSelect('id_branch');")
        self.assertContains(response, "initUserAccountSearchableSelect('id_parent_id');")
        self.assertContains(response, '#user_account_form .choices.choices-compact')

    def test_user_account_page_normalizes_legacy_jurisdiction_values_for_dropdowns(self):
        self.owner_user.state_province = 'WA'
        self.owner_user.country = 'USA'
        self.owner_user.save(update_fields=['state_province', 'country'])
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['state_province'], 'Washington')
        self.assertEqual(response.context['form'].initial['country'], 'United States')

    def test_user_account_page_shows_supporting_document_modal(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Supporting Document')
        self.assertContains(response, 'id="supportingDocumentModal"')
        self.assertContains(response, 'name="document_type"')
        self.assertContains(response, 'name="document_file"')
        self.assertContains(response, 'name="eq_person_ids"')
        self.assertContains(response, 'name="eq_authorization_ids"')

    def test_owner_can_upload_background_check_document_for_current_account(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'background-proof.pdf',
            b'%PDF-1.4 fake',
            content_type='application/pdf',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.BACKGROUND_CHECK,
                'document_file': upload,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        document = SupportingDocument.objects.get()
        self.assertEqual(document.document_type, SupportingDocument.DocumentType.BACKGROUND_CHECK)
        self.assertEqual(document.uploaded_by, self.owner_user)
        self.assertEqual(document.jurisdiction, '')
        self.assertTrue(
            SupportingDocumentPerson.objects.filter(document=document, person=self.owner_person).exists()
        )
        self.assertFalse(
            SupportingDocumentAuthorization.objects.filter(document=document).exists()
        )

    def test_equestrian_authorization_api_returns_selected_fighter_rows(self):
        target_user, target_person = self.make_person('eq_api_target', 'EQ API Target')
        target_auth = self.grant_authorization(
            target_person,
            self.style_eq_general_riding,
            status=self.status_pending,
            marshal=self.ao_person,
        )
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('get_equestrian_authorizations'),
            {'person_ids': [str(target_user.id)]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(len(payload['authorizations']), 1)
        self.assertEqual(payload['authorizations'][0]['id'], target_auth.id)

    def test_equestrian_authorization_api_excludes_active_rows(self):
        target_user, target_person = self.make_person('eq_api_active_target', 'EQ API Active Target')
        self.grant_authorization(
            target_person,
            self.style_eq_general_riding,
            status=self.status_active,
            marshal=self.ao_person,
        )
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('get_equestrian_authorizations'),
            {'person_ids': [str(target_user.id)]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['authorizations'], [])

    def test_equestrian_senior_marshal_can_upload_for_different_fighter(self):
        uploader_user, uploader_person = self.make_person('eq_uploader', 'EQ Uploader')
        eq_senior_style, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=self.discipline_equestrian,
        )
        self.grant_authorization(
            uploader_person,
            eq_senior_style,
            status=self.status_active,
            marshal=self.ao_person,
        )
        target_user, target_person = self.make_person('eq_target', 'EQ Target')
        target_auth = self.grant_authorization(
            target_person,
            self.style_eq_general_riding,
            status=self.status_pending,
            marshal=uploader_person,
        )
        upload = SimpleUploadedFile(
            'eq-waiver.pdf',
            b'%PDF-1.4 fake',
            content_type='application/pdf',
        )

        self.client.login(username=uploader_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': uploader_user.id}),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.EQUESTRIAN_WAIVER,
                'jurisdiction': 'WA',
                'eq_person_ids': [str(target_user.id)],
                'eq_authorization_ids': [str(target_auth.id)],
                'document_file': upload,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        document = SupportingDocument.objects.get(document_type=SupportingDocument.DocumentType.EQUESTRIAN_WAIVER)
        self.assertEqual(document.uploaded_by, uploader_user)
        self.assertEqual(document.jurisdiction, 'WA')
        self.assertTrue(
            SupportingDocumentPerson.objects.filter(document=document, person=target_person).exists()
        )
        self.assertTrue(
            SupportingDocumentAuthorization.objects.filter(document=document, authorization=target_auth).exists()
        )

    def test_non_marshal_cannot_upload_equestrian_waiver_for_unrelated_fighter(self):
        target_user, target_person = self.make_person('eq_unrelated_target', 'EQ Unrelated Target')
        target_auth = self.grant_authorization(
            target_person,
            self.style_eq_general_riding,
            status=self.status_pending,
            marshal=self.ao_person,
        )
        upload = SimpleUploadedFile(
            'eq-waiver.pdf',
            b'%PDF-1.4 fake',
            content_type='application/pdf',
        )

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.EQUESTRIAN_WAIVER,
                'jurisdiction': 'WA',
                'eq_person_ids': [str(target_user.id)],
                'eq_authorization_ids': [str(target_auth.id)],
                'document_file': upload,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SupportingDocument.objects.exists())
        self.assertIn(
            'You can only upload equestrian waivers for your own account or linked child accounts.',
            self.messages_for(response),
        )

    def test_background_check_upload_uses_unique_stored_filename(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        first = SimpleUploadedFile(
            'background_check.pdf',
            b'%PDF-1.4 first',
            content_type='application/pdf',
        )
        second = SimpleUploadedFile(
            'background_check.pdf',
            b'%PDF-1.4 second',
            content_type='application/pdf',
        )

        first_response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.BACKGROUND_CHECK,
                'document_file': first,
            },
            follow=True,
        )
        second_response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.BACKGROUND_CHECK,
                'document_file': second,
            },
            follow=True,
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        stored_names = list(
            SupportingDocument.objects.filter(
                document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
                uploaded_by=self.owner_user,
            ).values_list('file', flat=True)
        )
        self.assertEqual(len(stored_names), 2)
        self.assertNotEqual(stored_names[0], stored_names[1])

    def test_non_ao_cannot_modify_background_check_expiration(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=(date.today() + relativedelta(years=2)).isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.owner_user.background_check_expiration)

    @patch('authorizations.views.send_mail')
    def test_account_update_sends_notice_to_previous_email_when_email_changes(self, mock_send_mail):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        previous_email = self.owner_user.email
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            email='owner.updated@example.com',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.email, 'owner.updated@example.com')
        self.assertEqual(mock_send_mail.call_count, 1)
        call_args = mock_send_mail.call_args.args
        self.assertEqual(call_args[0], 'Email address changed for an An Tir Authorization account')
        self.assertEqual(call_args[3], [previous_email])
        self.assertIn('Society name: Owner of Account', call_args[1])
        self.assertIn('Mundane name: Owner User', call_args[1])
        self.assertIn(reverse('fighter', kwargs={'person_id': self.owner_user.id}), call_args[1])
        self.assertIn('antir.authorization.database@gmail.com', call_args[1])
        self.assertNotIn('owner.updated@example.com', call_args[1])

    @patch('authorizations.views.send_mail')
    def test_account_update_does_not_send_previous_email_notice_when_email_unchanged(self, mock_send_mail):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            city='Eugene',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.city, 'Eugene')
        mock_send_mail.assert_not_called()

    def test_ao_can_modify_background_check_expiration(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        bg_date = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=bg_date.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.background_check_expiration, bg_date)

    def test_setting_background_check_promotes_pending_background_check_to_active_when_sign_off_off(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        pending_auth = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_sm_youth_armored,
            status=self.status_pending_background_check,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        bg_date = date.today() + relativedelta(years=1)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=bg_date.isoformat(),
        )
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)

    def test_setting_background_check_promotes_pending_background_check_to_needs_kingdom_when_sign_off_on(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        pending_auth = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_sm_youth_armored,
            status=self.status_pending_background_check,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        bg_date = date.today() + relativedelta(years=1)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            background_check_expiration=bg_date.isoformat(),
        )
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_kingdom)

    def test_account_update_skips_roster_check_when_membership_unchanged(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            city='Seattle',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.city, 'Seattle')

    def test_account_update_rejects_changed_membership_not_in_roster(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership='1231231231',
            membership_expiration=new_expiration.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        messages = self.messages_for(response)
        self.assertIn(
            'Invalid membership information. Please review the <a href="/faq/#membership-update">membership FAQ</a> for information on how membership validation works.',
            messages,
        )
        self.assertNotEqual(self.owner_user.membership, '1231231231')

    def test_account_update_accepts_changed_membership_when_roster_matches(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '9239239239'
        self.seed_membership_roster(new_membership, 'Owner', 'User', new_expiration)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)

    def test_account_update_membership_extends_waiver_when_roster_waiver_is_yes(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        self.owner_user.waiver_expiration = None
        self.owner_user.save(update_fields=['waiver_expiration'])
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '7337337337'
        self.seed_membership_roster(
            new_membership,
            'Owner',
            'User',
            new_expiration,
            has_society_waiver=True,
        )
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.waiver_expiration, new_expiration)

    def test_account_update_roster_waiver_activates_pending_waiver_authorizations(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        self.owner_user.waiver_expiration = None
        self.owner_user.save(update_fields=['waiver_expiration'])
        authorization = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_pending_waiver,
            expiration=date.today() + relativedelta(years=1),
            marshal=self.ao_person,
        )
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '7337337340'
        self.seed_membership_roster(
            new_membership,
            'Owner',
            'User',
            new_expiration,
            has_society_waiver=True,
        )
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        authorization.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.waiver_expiration, new_expiration)
        self.assertEqual(authorization.status, self.status_active)

    def test_account_update_membership_does_not_extend_waiver_when_roster_waiver_not_yes(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        self.owner_user.waiver_expiration = None
        self.owner_user.save(update_fields=['waiver_expiration'])
        authorization = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_pending_waiver,
            expiration=date.today() + relativedelta(years=1),
            marshal=self.ao_person,
        )
        new_expiration = date.today() + relativedelta(years=2)
        new_membership = '7447447447'
        self.seed_membership_roster(
            new_membership,
            'Owner',
            'User',
            new_expiration,
            has_society_waiver=False,
        )
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            first_name='Owner',
            last_name='User',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        authorization.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.owner_user.waiver_expiration)
        self.assertEqual(authorization.status, self.status_pending_waiver)

        account_response = self.client.get(reverse('user_account', kwargs={'user_id': self.owner_user.id}))
        self.assertContains(account_response, reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}))

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_account_update_skips_roster_validation_in_test_mode(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        new_membership = '4242424242'
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)
        self.assertEqual(self.owner_user.waiver_expiration, new_expiration)

    def test_ao_bypass_requires_note_for_unmatched_membership_change(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership='2232232232',
            membership_expiration=(date.today() + relativedelta(years=2)).isoformat(),
            membership_validation_bypass='on',
            membership_validation_note='',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        messages = self.messages_for(response)
        self.assertIn('A bypass note is required when overriding membership validation.', messages)
        self.assertNotEqual(self.owner_user.membership, '2232232232')

    def test_ao_bypass_with_note_saves_and_records_officer_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        new_membership = '3233233233'
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            membership_validation_bypass='on',
            membership_validation_note='Manual verification from Society spreadsheet correction.',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)
        bypass_note = UserNote.objects.filter(person=self.owner_person, note__icontains='Membership validation bypass applied').first()
        self.assertIsNotNone(bypass_note)

    def test_staff_user_bypass_with_note_saves_and_records_officer_note(self):
        staff_user, _ = self.make_person('account_staff_admin', 'Account Staff Admin')
        staff_user.is_staff = True
        staff_user.save()
        self.client.login(username=staff_user.username, password='StrongPass!123')
        new_membership = '4234234234'
        new_expiration = date.today() + relativedelta(years=2)
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            membership=new_membership,
            membership_expiration=new_expiration.isoformat(),
            membership_validation_bypass='on',
            membership_validation_note='Manual verification by administrator.',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.membership, new_membership)
        self.assertEqual(self.owner_user.membership_expiration, new_expiration)
        bypass_note = UserNote.objects.filter(
            person=self.owner_person,
            created_by=staff_user,
            note__icontains='Membership validation bypass applied',
        ).first()
        self.assertIsNotNone(bypass_note)

    def test_account_update_surfaces_explicit_state_and_postal_errors(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        payload = self.account_update_payload(
            self.owner_user,
            self.owner_person,
            state_province='California',
            postal_code='12345',
            city='Seattle',
        )

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            payload,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('Please correct the errors with the form.', messages)
        self.assertIn(
            'State/Province: State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
            messages,
        )
        self.assertIn(
            'Postal Code: Postal code must be within An Tir.',
            messages,
        )
        self.assertContains(
            response,
            'State/Province must be within An Tir (Oregon, Washington, Idaho, or British Columbia).',
        )
        self.assertContains(
            response,
            'Postal code must be within An Tir.',
        )

    def test_non_ao_cannot_upload_membership_roster(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members.csv',
            b'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n12345,Test,User,1/1/2030\n',
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
        )

        self.assertEqual(response.status_code, 403)

    def test_ao_upload_membership_roster_preserves_existing_rows_and_adds_new_rows(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        MembershipRosterEntry.objects.create(
            membership_number='111111',
            first_name='Old',
            last_name='Member',
            membership_expiration=date(2030, 1, 1),
        )
        upload = SimpleUploadedFile(
            'members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Fresh,Member,2/2/2031\n'
                '333333,New,Person,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='111111').exists())
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='222222').exists())
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='333333').exists())
        metadata = MembershipRosterImport.objects.get(pk=1)
        self.assertEqual(metadata.row_count, 2)
        self.assertEqual(metadata.source_filename, 'members.csv')

    def test_ao_upload_membership_roster_does_not_shorten_existing_roster_entry(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        MembershipRosterEntry.objects.create(
            membership_number='111111',
            first_name='Current',
            last_name='Member',
            membership_expiration=date(2035, 1, 1),
            has_society_waiver=True,
        )
        upload = SimpleUploadedFile(
            'old_members.csv',
            (
                'Legacy ID (C),Waiver (C),First Name,Last Name,Membership Expiration Date\n'
                '111111,,Old,Member,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='111111')
        self.assertEqual(entry.first_name, 'Current')
        self.assertEqual(entry.last_name, 'Member')
        self.assertEqual(entry.membership_expiration, date(2035, 1, 1))
        self.assertTrue(entry.has_society_waiver)

    def test_ao_upload_membership_roster_extends_existing_roster_entry(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        MembershipRosterEntry.objects.create(
            membership_number='111111',
            first_name='Old',
            last_name='Member',
            membership_expiration=date(2030, 1, 1),
            has_society_waiver=False,
        )
        upload = SimpleUploadedFile(
            'new_members.csv',
            (
                'Legacy ID (C),Waiver (C),First Name,Last Name,Membership Expiration Date\n'
                '111111,Yes,Current,Member,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='111111')
        self.assertEqual(entry.first_name, 'Current')
        self.assertEqual(entry.last_name, 'Member')
        self.assertEqual(entry.membership_expiration, date(2031, 2, 2))
        self.assertTrue(entry.has_society_waiver)

    def test_kingdom_seneschal_can_upload_membership_roster(self):
        seneschal_user, seneschal_person = self.make_person(
            'membership_roster_seneschal',
            'Membership Roster Seneschal',
        )
        self.appoint(seneschal_person, self.branch_an_tir, self.discipline_seneschal)
        self.client.login(username=seneschal_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Fresh,Member,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='222222').exists())
        metadata = MembershipRosterImport.objects.get(pk=1)
        self.assertEqual(metadata.row_count, 1)
        self.assertEqual(metadata.imported_by, seneschal_user)

    def test_ao_upload_membership_roster_extends_matching_user_expiration_and_records_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        self.owner_user.membership = '222222'
        self.owner_user.membership_expiration = date(2030, 1, 1)
        self.owner_user.save()
        upload = SimpleUploadedFile(
            'members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Owner,User,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.membership_expiration, date(2031, 2, 2))
        self.assertEqual(self.owner_user.updated_by, self.ao_user)
        note = UserNote.objects.get(person=self.owner_person)
        self.assertIn('Membership expiration refreshed from Society membership roster upload.', note.note)
        self.assertIn('Previous expiration: 2030-01-01', note.note)
        self.assertIn('New expiration: 2031-02-02', note.note)
        messages = self.messages_for(response)
        self.assertTrue(any('1 user membership expiration(s) were extended' in message for message in messages))

    def test_ao_upload_membership_roster_does_not_shorten_matching_user_expiration(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        self.owner_user.membership = '222222'
        self.owner_user.membership_expiration = date(2035, 1, 1)
        self.owner_user.save()
        upload = SimpleUploadedFile(
            'members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Owner,User,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.membership_expiration, date(2035, 1, 1))
        self.assertFalse(UserNote.objects.filter(person=self.owner_person).exists())
        messages = self.messages_for(response)
        self.assertFalse(any('user membership expiration(s) were extended' in message for message in messages))

    def test_ao_upload_membership_roster_refreshes_user_from_preserved_later_roster_entry(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        self.owner_user.membership = '222222'
        self.owner_user.membership_expiration = date(2030, 1, 1)
        self.owner_user.save()
        MembershipRosterEntry.objects.create(
            membership_number='222222',
            first_name='Owner',
            last_name='User',
            membership_expiration=date(2035, 1, 1),
        )
        upload = SimpleUploadedFile(
            'old_members.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '222222,Owner,User,2/2/2031\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.membership_expiration, date(2035, 1, 1))
        entry = MembershipRosterEntry.objects.get(membership_number='222222')
        self.assertEqual(entry.membership_expiration, date(2035, 1, 1))

    def test_ao_upload_membership_roster_accepts_current_society_headers(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'current_society_members.csv',
            (
                'Legacy ID (C),Waiver (C),Membership Level,Society Name (C),First Name,Last Name,Zip Code,'
                'Membership Expiration Date,Exp Date - Custom (C),Auto Renew? (C),Kingdom ID (C)\n'
                '777777,Yes,Associate,Current Society,Current,Member,97201,,4/4/2033,No,An Tir\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='777777')
        self.assertEqual(entry.first_name, 'Current')
        self.assertEqual(entry.last_name, 'Member')
        self.assertEqual(entry.membership_expiration, date(2033, 4, 4))
        self.assertTrue(entry.has_society_waiver)

    def test_ao_upload_membership_roster_uses_later_standard_expiration_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_standard_later.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date,Exp Date - Custom (C)\n'
                '121212,Standard,Later,5/5/2035,4/4/2034\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='121212')
        self.assertEqual(entry.membership_expiration, date(2035, 5, 5))

    def test_ao_upload_membership_roster_uses_later_custom_expiration_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_custom_later.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date,Exp Date - Custom (C)\n'
                '343434,Custom,Later,5/5/2035,6/6/2036\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='343434')
        self.assertEqual(entry.membership_expiration, date(2036, 6, 6))

    def test_ao_upload_membership_roster_accepts_xlsx(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        rows = [
            [
                'Legacy ID (C)',
                'Waiver (C)',
                'Membership Level',
                'Society Name (C)',
                'First Name',
                'Last Name',
                'Zip Code',
                'Membership Expiration Date',
                'Exp Date - Custom (C)',
                'Auto Renew? (C)',
                'Kingdom ID (C)',
            ],
            [
                '888888',
                'Yes',
                'Associate',
                'Excel Society',
                'Excel',
                'Member',
                '97201',
                '',
                (date(2034, 5, 5) - date(1899, 12, 30)).days,
                'No',
                'An Tir',
            ],
        ]
        upload = SimpleUploadedFile(
            'society_members.xlsx',
            self._build_xlsx(rows),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='888888')
        self.assertEqual(entry.first_name, 'Excel')
        self.assertEqual(entry.last_name, 'Member')
        self.assertEqual(entry.membership_expiration, date(2034, 5, 5))
        self.assertTrue(entry.has_society_waiver)

    def test_ao_upload_membership_roster_accepts_xlsx_decimal_membership_number(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        rows = [
            [
                'Legacy ID (C)',
                'Waiver (C)',
                'Membership Level',
                'Society Name (C)',
                'First Name',
                'Last Name',
                'Zip Code',
                'County',
                'Membership Expiration Date',
                'Kingdom ID (C)',
            ],
            [
                308001.0,
                '',
                'Associate',
                '',
                'Frances',
                'Mass',
                '98922',
                '',
                (date(2026, 4, 30) - date(1899, 12, 30)).days,
                'An Tir',
            ],
        ]
        upload = SimpleUploadedFile(
            'society_members_decimal_ids.xlsx',
            self._build_xlsx(rows),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        entry = MembershipRosterEntry.objects.get(membership_number='308001')
        self.assertEqual(entry.first_name, 'Frances')
        self.assertEqual(entry.last_name, 'Mass')
        self.assertEqual(entry.membership_expiration, date(2026, 4, 30))

    def test_ao_upload_membership_roster_skips_rows_with_blank_membership_number(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_with_blank_row.csv',
            (
                'Legacy ID (C),First Name,Last Name,Membership Expiration Date\n'
                '444444,Valid,Member,2/2/2031\n'
                ',Blank,LegacyId,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MembershipRosterEntry.objects.filter(membership_number='444444').exists())
        self.assertFalse(MembershipRosterEntry.objects.filter(first_name='Blank', last_name='LegacyId').exists())
        messages = self.messages_for(response)
        self.assertTrue(any('row(s) were skipped' in message for message in messages))

    def test_ao_upload_membership_roster_parses_waiver_yes_flag(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        upload = SimpleUploadedFile(
            'members_waiver.csv',
            (
                'Legacy ID (C),Waiver (C),First Name,Last Name,Membership Expiration Date\n'
                '555555,Yes,Waiver,Yes,2/2/2031\n'
                '666666,,Waiver,No,3/3/2032\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_membership_roster'),
            {'membership_csv': upload, 'next': reverse('user_account', kwargs={'user_id': self.owner_user.id})},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        yes_entry = MembershipRosterEntry.objects.get(membership_number='555555')
        no_entry = MembershipRosterEntry.objects.get(membership_number='666666')
        self.assertTrue(yes_entry.has_society_waiver)
        self.assertFalse(no_entry.has_society_waiver)

    def test_ao_can_import_legacy_authorization_for_existing_person(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations.csv',
            (
                'Person ID,Discipline,Weapon Style,Start Date,Marshal SCA Name\n'
                f'{self.owner_person.user_id},Armored Combat,Weapon & Shield,2025-01-15,{self.ao_person.sca_name}\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(authorization.status.name, 'Active')
        self.assertEqual(authorization.expiration, date(2029, 1, 15))
        self.assertEqual(authorization.marshal, self.ao_person)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.waiver_expiration, date(2029, 1, 15))
        record = WaiverRecord.objects.get(
            covered_user=self.owner_user,
            source=WaiverRecord.Source.LEGACY_DATABASE_IMPORT,
        )
        self.assertEqual(record.resulting_waiver_expiration, date(2029, 1, 15))
        self.assertEqual(record.recorded_by, self.ao_user)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=authorization,
                note__contains='Historical start date: 2025-01-15.',
            ).exists()
        )

    def test_ao_can_import_legacy_authorization_with_unambiguous_style_only(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations_style_only.csv',
            (
                'Person ID,Weapon Style,Start Date,Marshal SCA Name\n'
                f'{self.owner_person.user_id},Weapon & Shield,2025-01-15,{self.ao_person.sca_name}\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(authorization.status.name, 'Active')
        self.assertEqual(authorization.marshal, self.ao_person)

    def test_ao_can_import_legacy_authorization_with_combined_discipline_style(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations_combined_style.csv',
            (
                'Person ID,Weapon Style,Start Date,Marshal SCA Name\n'
                f'{self.owner_person.user_id},Armored Combat - Weapon & Shield,2025-01-15,{self.ao_person.sca_name}\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(authorization.status.name, 'Active')
        self.assertEqual(authorization.marshal, self.ao_person)

    def test_ao_can_import_legacy_authorization_with_discipline_abbreviation(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations_combined_style_abbrev.csv',
            (
                'Person ID,Weapon Style,Start Date,Marshal SCA Name\n'
                f'{self.owner_person.user_id},Heavy - Weapon & Shield,2025-01-15,{self.ao_person.sca_name}\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(authorization.status.name, 'Active')
        self.assertEqual(authorization.marshal, self.ao_person)

    def test_legacy_authorization_import_requires_authorizing_marshal(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations_missing_marshal.csv',
            (
                'Person ID,Weapon Style,Start Date\n'
                f'{self.owner_person.user_id},Weapon & Shield,2025-01-15\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Authorizing marshal is required')
        self.assertFalse(
            Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists()
        )

    def test_ao_can_import_legacy_authorization_and_create_placeholder_person(self):
        self.client.force_login(self.ao_user)
        upload = SimpleUploadedFile(
            'legacy_new_person.csv',
            (
                'SCA Name,First Name,Last Name,Email,Branch,City,State,Country,Phone,Waiver Expiration,'
                'Discipline,Weapon Style,Start Date,Marshal SCA Name\n'
                'New Legacy Fighter,New,Legacy,new.legacy@example.com,Barony of Glyn Dwfn,Portland,Oregon,'
                'United States,555-0100,2028-02-01,Armored Combat,Weapon & Shield,2025-02-01,Authorization Officer\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = Person.objects.get(sca_name='New Legacy Fighter')
        self.assertEqual(person.branch, self.branch_gd)
        self.assertEqual(person.user.first_name, 'New')
        self.assertEqual(person.user.last_name, 'Legacy')
        self.assertEqual(person.user.email, 'new.legacy@example.com')
        self.assertEqual(person.user.city, 'Portland')
        self.assertEqual(person.user.state_province, 'Oregon')
        self.assertEqual(person.user.country, 'United States')
        self.assertEqual(person.user.phone_number, '555-0100')
        self.assertEqual(person.user.waiver_expiration, date(2029, 2, 1))
        self.assertFalse(person.user.has_usable_password())
        authorization = Authorization.objects.get(person=person, style=self.style_weapon_armored)
        self.assertEqual(authorization.status.name, 'Active')
        self.assertEqual(authorization.expiration, date(2029, 2, 1))
        self.assertEqual(authorization.marshal, self.ao_person)
        self.assertTrue(UserNote.objects.filter(person=person, note__contains='Placeholder account').exists())

    def test_non_ao_cannot_import_legacy_authorizations(self):
        self.client.force_login(self.owner_user)
        upload = SimpleUploadedFile(
            'legacy_authorizations.csv',
            b'SCA Name,Discipline,Weapon Style,Start Date\nTest,Armored Combat,Weapon & Shield,2025-01-01\n',
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('upload_legacy_authorizations'),
            {'authorization_csv': upload, 'next': reverse('index')},
        )

        self.assertEqual(response.status_code, 403)

    def test_ao_can_process_legacy_recovery_batch(self):
        self.client.force_login(self.ao_user)
        other_style = WeaponStyle.objects.create(name='Two-Handed', discipline=self.discipline_armored)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name, self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name, self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name, self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Armored Combat - Two-Handed'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-11'],
                **self.paper_concurrer_fields(count=2),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 2 paper authorization row(s).')
        first_auth = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        second_auth = Authorization.objects.get(person=self.owner_person, style=other_style)
        self.assertEqual(first_auth.expiration, date(2029, 5, 10))
        self.assertEqual(second_auth.expiration, date(2029, 5, 11))
        self.assertIsNone(first_auth.concurring_fighter)
        self.assertIsNone(second_auth.concurring_fighter)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.waiver_expiration, date(2029, 5, 11))
        self.assertEqual(
            WaiverRecord.objects.filter(
                covered_user=self.owner_user,
                source=WaiverRecord.Source.LEGACY_DATABASE_IMPORT,
            ).count(),
            2,
        )
        self.assertEqual(LegacyAuthorizationRecoveryEntry.objects.count(), 2)
        first_entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=first_auth)
        self.assertFalse(first_entry.minor_on_paperwork)
        self.assertEqual(first_entry.expiration, date(2029, 5, 10))
        note_text = 'Authorization Added through Paper Authorization Entry Tool'
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=first_auth,
                created_by=self.ao_user,
                note__contains=note_text,
            ).exists()
        )
        self.assertTrue(
            UserNote.objects.filter(
                person=self.owner_person,
                created_by=self.ao_user,
                note__contains=note_text,
            ).exists()
        )
        self.assertTrue(
            UserNote.objects.filter(
                person=self.ao_person,
                created_by=self.ao_user,
                note__contains=note_text,
            ).exists()
        )

    def test_legacy_recovery_batch_blocks_duplicate_person_style(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name, self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name, self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name, self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-10'],
                **self.paper_concurrer_fields(count=2),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Armored Combat - Weapon &amp; Shield')
        self.assertContains(response, 'This authorization is already in the batch.')
        self.assertEqual(
            Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).count(),
            0,
        )
        self.assertEqual(
            LegacyAuthorizationRecoveryEntry.objects.filter(
                person=self.owner_person,
                style=self.style_weapon_armored,
            ).count(),
            0,
        )

    def test_paper_entry_rolls_back_new_fighter_when_later_row_fails(self):
        self.client.force_login(self.ao_user)
        new_sca_name = 'Atomic Paper Fighter'
        style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        self.grant_authorization(
            self.ao_person,
            style_sm_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [new_sca_name, new_sca_name],
                'person_email': ['atomic.paper@example.com', 'atomic.paper@example.com'],
                'person_first_name': ['Atomic', 'Atomic'],
                'person_last_name': ['Fighter', 'Fighter'],
                'person_address': ['123 Atomic Way', '123 Atomic Way'],
                'person_city': ['Portland', 'Portland'],
                'person_state_province': ['Oregon', 'Oregon'],
                'person_postal_code': ['97201', '97201'],
                'person_country': ['United States', 'United States'],
                'person_phone_number': ['5035550199', '5035550199'],
                'person_branch': [str(self.branch_gd.id), str(self.branch_gd.id)],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Rapier Combat - Dagger'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-10'],
                **self.paper_concurrer_fields(count=2),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rapier Combat - Dagger')
        self.assertContains(response, 'did not have Rapier Single Sword on 2025-05-10')
        self.assertFalse(Person.objects.filter(sca_name=new_sca_name).exists())
        self.assertFalse(Authorization.objects.filter(person__sca_name=new_sca_name).exists())
        self.assertFalse(LegacyAuthorizationRecoveryEntry.objects.filter(person__sca_name=new_sca_name).exists())

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_paper_entry_reports_multiple_batch_rule_errors(self):
        self.client.force_login(self.ao_user)
        style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        self.grant_authorization(
            self.ao_person,
            style_sm_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name, self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name, self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name, self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Rapier Combat - Dagger'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Armored Combat - Weapon &amp; Shield')
        self.assertContains(response, 'Concurring fighter is required for this paper authorization.')
        self.assertContains(response, 'Rapier Combat - Dagger')
        self.assertContains(response, 'did not have Rapier Single Sword on 2025-05-10')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=style_rapier_dagger).exists())

    def test_paper_entry_requires_marshal_senior_marshal_history_on_auth_date(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.owner_person.sca_name],
                'marshal_first_name': [self.owner_user.first_name],
                'marshal_last_name': [self.owner_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'was not a Senior Marshal in Armored Combat on 2025-05-10')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_does_not_accept_marshal_history_that_starts_after_auth_date(self):
        self.client.force_login(self.ao_user)
        future_marshal_user, future_marshal = self.make_person('future_marshal', 'Future Marshal')
        self.grant_authorization(
            future_marshal,
            self.style_sm_armored,
            expiration=date(2030, 5, 10),
            marshal=future_marshal,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [future_marshal.sca_name],
                'marshal_first_name': [future_marshal_user.first_name],
                'marshal_last_name': [future_marshal_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'was not a Senior Marshal in Armored Combat on 2025-05-10')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_paper_entry_requires_concurring_fighter_when_concurrence_required(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Concurring fighter is required for this paper authorization.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_allows_first_authorization_without_concurrence_when_disabled(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertIsNone(authorization.concurring_fighter)

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_paper_entry_rejects_concurring_fighter_without_history_on_auth_date(self):
        self.client.force_login(self.ao_user)
        novice_user, novice_person = self.make_person('paper_novice_concurrer', 'Paper Novice Concurrer')

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'concurring_officer_sca_name': [novice_person.sca_name],
                'concurring_officer_first_name': [novice_user.first_name],
                'concurring_officer_last_name': [novice_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'was not authorized in Armored Combat on 2025-05-10 and cannot concur.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_requires_prerequisite_history_on_auth_date(self):
        self.client.force_login(self.ao_user)
        style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        self.grant_authorization(
            self.ao_person,
            style_sm_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Rapier Combat - Dagger'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'did not have Rapier Single Sword on 2025-05-10')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=style_rapier_dagger).exists())

    def test_paper_entry_accepts_prerequisite_history_on_auth_date(self):
        self.client.force_login(self.ao_user)
        style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        self.grant_authorization(
            self.ao_person,
            style_sm_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )
        self.grant_authorization(
            self.owner_person,
            self.style_single_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Rapier Combat - Dagger'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        self.assertTrue(Authorization.objects.filter(person=self.owner_person, style=style_rapier_dagger).exists())

    def test_paper_entry_blocks_kao_from_equestrian_authorizations(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Equestrian - General Riding'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Equestrian Authorization Officer can enter equestrian paper authorizations.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_eq_general_riding).exists())

    def test_paper_entry_blocks_keao_from_non_equestrian_authorizations(self):
        keao_user, keao_person = self.make_person('paper_entry_keao', 'Paper Entry KEAO')
        self.appoint(keao_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.client.force_login(keao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Authorization Officer can enter non-equestrian paper authorizations.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_rejects_youth_combat_for_adult_on_auth_date(self):
        self.client.force_login(self.ao_user)
        youth_style = WeaponStyle.objects.create(name='Gryphon - Weapon & Shield', discipline=self.discipline_youth_armored)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Youth Armored - Gryphon - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Must be a minor to become authorized in Youth Armored combat.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=youth_style).exists())

    def test_paper_entry_accepts_youth_combat_when_minor_on_auth_date(self):
        self.client.force_login(self.ao_user)
        youth_user, youth_person = self.make_person(
            'paper_entry_youth',
            'Paper Entry Youth',
            birthday=date(2013, 5, 10),
        )
        youth_style = WeaponStyle.objects.create(name='Gryphon - Weapon & Shield', discipline=self.discipline_youth_armored)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [youth_person.sca_name],
                'person_first_name': [youth_user.first_name],
                'person_last_name': [youth_user.last_name],
                'weapon_style': ['Youth Armored - Gryphon - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=youth_person, style=youth_style)
        self.assertEqual(authorization.expiration, date(2027, 5, 10))

    def test_paper_entry_blocks_kao_from_equestrian_even_with_equestrian_marshal_auth(self):
        style_eq_sm = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_equestrian)
        self.grant_authorization(
            self.ao_person,
            style_eq_sm,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Equestrian - General Riding'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Equestrian Authorization Officer can enter equestrian paper authorizations.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_eq_general_riding).exists())

    def test_paper_entry_blocks_keao_from_non_equestrian_even_with_armored_marshal_auth(self):
        keao_user, keao_person = self.make_person('paper_entry_keao_with_armored', 'Paper Entry KEAO Armored')
        self.appoint(keao_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.grant_authorization(
            keao_person,
            self.style_sm_armored,
            expiration=date(2029, 5, 10),
            marshal=keao_person,
        )
        self.client.force_login(keao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [keao_person.sca_name],
                'marshal_first_name': [keao_user.first_name],
                'marshal_last_name': [keao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Only the Kingdom Authorization Officer can enter non-equestrian paper authorizations.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_staff_can_enter_paper_authorizations_across_kao_and_keao_scopes(self):
        staff_user, staff_person = self.make_person('paper_entry_staff', 'Paper Entry Staff')
        staff_user.is_staff = True
        staff_user.save(update_fields=['is_staff'])
        style_eq_sm = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_equestrian)
        self.grant_authorization(
            self.ao_person,
            style_eq_sm,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name, self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name, self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name, self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Equestrian - General Riding'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-10'],
                **self.paper_concurrer_fields(count=2),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 2 paper authorization row(s).')
        self.assertTrue(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())
        self.assertTrue(Authorization.objects.filter(person=self.owner_person, style=self.style_eq_general_riding).exists())

    def test_paper_entry_rejects_self_authorization(self):
        self.grant_authorization(
            self.owner_person,
            self.style_sm_armored,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.owner_person.sca_name],
                'marshal_first_name': [self.owner_user.first_name],
                'marshal_last_name': [self.owner_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cannot make an authorization for yourself.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_rejects_pending_authorization(self):
        Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_needs_concurrence,
            marshal=self.ao_person,
            expiration=date(2029, 5, 10),
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cannot renew a pending authorization.')
        pending_authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(pending_authorization.status, self.status_needs_concurrence)
        self.assertFalse(LegacyAuthorizationRecoveryEntry.objects.exists())

    def test_paper_entry_rejects_style_sanction_active_on_auth_date(self):
        Sanction.objects.create(
            person=self.owner_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date(2025, 5, 1),
            end_date=date(2025, 5, 31),
            issue_note='Paper entry test sanction.',
            issued_by=self.ao_user,
            created_by=self.ao_user,
            updated_by=self.ao_user,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cannot issue an authorization while a sanction is active for this style or discipline.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_ignores_sanction_outside_auth_date(self):
        Sanction.objects.create(
            person=self.owner_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30),
            issue_note='Expired before the paper date.',
            issued_by=self.ao_user,
            created_by=self.ao_user,
            updated_by=self.ao_user,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        self.assertTrue(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_uses_submitted_membership_expiration_for_marshal_rule(self):
        self.owner_user.membership_expiration = date(2025, 5, 9)
        self.owner_user.save(update_fields=['membership_expiration'])
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'person_membership': [self.owner_user.membership],
                'person_membership_expiration': ['2025-05-10'],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        self.assertEqual(authorization.expiration, date(2029, 5, 10))
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.membership_expiration, date(2025, 5, 10))

    def test_paper_entry_rejects_marshal_authorization_when_membership_expired_on_auth_date(self):
        self.owner_user.membership_expiration = date(2025, 5, 9)
        self.owner_user.save(update_fields=['membership_expiration'])
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Must be a current member to be authorized as a marshal.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_sm_armored).exists())

    def test_paper_entry_rejects_youth_marshal_when_background_check_expired_on_auth_date(self):
        self.owner_user.background_check_expiration = date(2025, 5, 9)
        self.owner_user.save(update_fields=['background_check_expiration'])
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Youth Armored - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Youth marshal authorizations require a current background check.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_sm_youth_armored).exists())

    def test_paper_entry_rejects_minor_adult_combat_without_regional_marshal_on_auth_date(self):
        minor_user, minor_person = self.make_person(
            'paper_entry_minor_armored',
            'Paper Entry Minor Armored',
            birthday=date(2008, 5, 10),
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [minor_person.sca_name],
                'person_first_name': [minor_user.first_name],
                'person_last_name': [minor_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cannot authorize a minor in Rapier, Cut &amp; Thrust, or Armored Combat unless you are a regional marshal.')
        self.assertFalse(Authorization.objects.filter(person=minor_person, style=self.style_weapon_armored).exists())

    def test_paper_entry_rolls_back_existing_fighter_updates_when_later_row_fails(self):
        original_email = self.owner_user.email
        style_rapier_dagger = WeaponStyle.objects.create(name='Dagger', discipline=self.discipline_rapier)
        style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=self.discipline_rapier)
        self.grant_authorization(
            self.ao_person,
            style_sm_rapier,
            expiration=date(2029, 5, 10),
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name, self.owner_person.sca_name],
                'person_email': ['paper-update@example.com', 'paper-update@example.com'],
                'person_first_name': [self.owner_user.first_name, self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name, self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield', 'Rapier Combat - Dagger'],
                'marshal_sca_name': [self.ao_person.sca_name, self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name, self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name, self.ao_user.last_name],
                'auth_date': ['2025-05-10', '2025-05-10'],
                **self.paper_concurrer_fields(count=2),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rapier Combat - Dagger')
        self.assertContains(response, 'did not have Rapier Single Sword on 2025-05-10')
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.email, original_email)
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_weapon_armored).exists())
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=style_rapier_dagger).exists())
        self.assertFalse(LegacyAuthorizationRecoveryEntry.objects.exists())

    def test_legacy_recovery_updates_existing_authorization_and_records_previous_state(self):
        self.client.force_login(self.ao_user)
        existing_authorization = self.grant_authorization(
            self.owner_person,
            self.style_weapon_armored,
            expiration=date(2028, 5, 10),
            marshal=self.other_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        existing_authorization.refresh_from_db()
        self.assertEqual(existing_authorization.expiration, date(2029, 5, 10))
        self.assertEqual(existing_authorization.marshal, self.ao_person)
        self.assertEqual(existing_authorization.updated_by, self.ao_user)
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=existing_authorization)
        self.assertEqual(entry.previous_status, self.status_active)
        self.assertEqual(entry.previous_marshal, self.other_person)
        self.assertIsNone(entry.previous_concurring_fighter)
        self.assertEqual(entry.previous_expiration, date(2028, 5, 10))

    def test_legacy_recovery_blocks_existing_authorization_from_moving_backward(self):
        self.client.force_login(self.ao_user)
        existing_authorization = self.grant_authorization(
            self.owner_person,
            self.style_weapon_armored,
            expiration=date(2029, 5, 10),
            marshal=self.other_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-09'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This row would not move the authorization forward.')
        existing_authorization.refresh_from_db()
        self.assertEqual(existing_authorization.expiration, date(2029, 5, 10))
        self.assertEqual(existing_authorization.marshal, self.other_person)
        self.assertFalse(LegacyAuthorizationRecoveryEntry.objects.exists())

    def test_legacy_recovery_minor_checkbox_uses_two_year_expiration(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                'is_minor': ['1'],
                **self.paper_concurrer_fields(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_weapon_armored)
        self.assertEqual(authorization.expiration, date(2027, 5, 10))
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=authorization)
        self.assertTrue(entry.minor_on_paperwork)
        self.assertEqual(entry.expiration, date(2027, 5, 10))

    def test_legacy_recovery_youth_marshal_uses_two_year_expiration(self):
        self.client.force_login(self.ao_user)
        self.owner_user.background_check_expiration = date(2027, 5, 10)
        self.owner_user.save(update_fields=['background_check_expiration'])

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Youth Armored - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'second_marshal_sca_name': [self.other_person.sca_name],
                'second_marshal_first_name': [self.other_user.first_name],
                'second_marshal_last_name': [self.other_user.last_name],
                'concurring_officer_sca_name': [self.ao_person.sca_name],
                'concurring_officer_first_name': [self.ao_user.first_name],
                'concurring_officer_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
                'is_minor': [''],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_youth_armored)
        self.assertEqual(authorization.expiration, date(2027, 5, 10))

    def test_legacy_recovery_junior_marshal_renewal_does_not_require_second_marshal(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Junior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_jm_armored)
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=authorization)
        self.assertFalse(entry.marshal_promotion)
        self.assertIsNone(entry.second_marshal)

    def test_legacy_recovery_junior_marshal_promotion_requires_second_marshal(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Junior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'marshal_promotion': ['1'],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Second Marshal is required for this marshal authorization.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_jm_armored).exists())

    def test_legacy_recovery_junior_marshal_records_second_marshal(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Junior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'second_marshal_sca_name': [self.other_person.sca_name],
                'second_marshal_first_name': [self.other_user.first_name],
                'second_marshal_last_name': [self.other_user.last_name],
                'marshal_promotion': ['1'],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_jm_armored)
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=authorization)
        self.assertTrue(entry.marshal_promotion)
        self.assertEqual(entry.second_marshal, self.other_person)
        self.assertIsNone(entry.concurring_officer)

    def test_legacy_recovery_senior_marshal_renewal_does_not_require_promotion_signoffs(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=authorization)
        self.assertFalse(entry.marshal_promotion)
        self.assertIsNone(entry.second_marshal)
        self.assertIsNone(entry.concurring_officer)

    def test_legacy_recovery_senior_marshal_deactivates_same_discipline_junior_marshal(self):
        junior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_jm_armored,
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        junior_authorization.refresh_from_db()
        self.assertEqual(junior_authorization.status.name, 'Inactive')
        senior_authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        audit_entry = AuthorizationAuditEntry.objects.filter(
            authorization=junior_authorization,
            before_status=self.status_active,
            after_status=self.status_inactive,
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=junior_authorization,
                action='marshal_approved',
                note__contains=f'Superseded by Senior Marshal authorization {senior_authorization.id}.',
            ).exists()
        )
        self.assertFalse(
            AuthorizationValidityInterval.objects.filter(
                authorization=junior_authorization,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )

    def test_delete_senior_marshal_restores_junior_marshal_superseded_by_paper_entry(self):
        junior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_jm_armored,
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)
        self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )
        senior_authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        junior_authorization.refresh_from_db()
        self.assertEqual(junior_authorization.status.name, 'Inactive')

        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': self.owner_person.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(senior_authorization.id),
                'action_note': 'Senior Marshal was entered for the wrong fighter.',
            },
            follow=True,
        )

        senior_authorization.refresh_from_db()
        junior_authorization.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(senior_authorization.status.name, 'Inactive')
        self.assertEqual(junior_authorization.status.name, 'Active')
        self.assertTrue(
            AuthorizationValidityInterval.objects.filter(
                authorization=junior_authorization,
                start_date__lte=date.today(),
                end_date__gte=date.today(),
            ).exists()
        )
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=junior_authorization,
                note__contains=f'Restored to Active because Senior Marshal authorization {senior_authorization.id} was deleted.',
            ).exists()
        )

    def test_delete_senior_marshal_restores_junior_marshal_superseded_before_restore_notes_existed(self):
        junior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_jm_armored,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        senior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_sm_armored,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        promotion_time = timezone.now() - timedelta(days=1)
        Authorization.objects.filter(pk=senior_authorization.pk).update(
            created_at=promotion_time,
            updated_at=promotion_time,
        )
        old_junior_update_time = promotion_time - timedelta(days=30)
        Authorization.objects.filter(pk=junior_authorization.pk).update(
            status=self.status_inactive,
            created_at=old_junior_update_time,
            updated_at=old_junior_update_time,
        )
        AuthorizationAuditEntry.objects.filter(authorization=junior_authorization).delete()
        AuthorizationNote.objects.filter(authorization=junior_authorization).delete()

        self.client.force_login(self.ao_user)
        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': self.owner_person.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(senior_authorization.id),
                'action_note': 'Senior Marshal was entered for the wrong fighter.',
            },
            follow=True,
        )

        senior_authorization.refresh_from_db()
        junior_authorization.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(senior_authorization.status.name, 'Inactive')
        self.assertEqual(junior_authorization.status.name, 'Active')
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=junior_authorization,
                note__contains='before explicit Senior/Junior restore notes were recorded',
            ).exists()
        )

    def test_delete_senior_marshal_does_not_restore_junior_when_another_senior_remains(self):
        junior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_jm_armored,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        senior_authorization = self.grant_authorization(
            self.owner_person,
            self.style_sm_armored,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        duplicate_senior_style = WeaponStyle.objects.create(
            name='Senior Marshal',
            discipline=self.discipline_armored,
        )
        remaining_senior = self.grant_authorization(
            self.owner_person,
            duplicate_senior_style,
            marshal=self.ao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        promotion_time = timezone.now() - timedelta(days=1)
        Authorization.objects.filter(pk=senior_authorization.pk).update(
            created_at=promotion_time,
            updated_at=promotion_time,
        )
        old_junior_update_time = promotion_time - timedelta(days=30)
        Authorization.objects.filter(pk=junior_authorization.pk).update(
            status=self.status_inactive,
            created_at=old_junior_update_time,
            updated_at=old_junior_update_time,
        )
        AuthorizationNote.objects.filter(authorization=junior_authorization).delete()

        self.client.force_login(self.ao_user)
        response = self.client.post(
            reverse('delete_authorizations_for_person', kwargs={'person_id': self.owner_person.user_id}),
            {
                'action': 'delete_authorization',
                'authorization_id': str(senior_authorization.id),
                'action_note': 'Duplicate Senior Marshal was entered.',
            },
            follow=True,
        )

        junior_authorization.refresh_from_db()
        senior_authorization.refresh_from_db()
        remaining_senior.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(senior_authorization.status.name, 'Inactive')
        self.assertEqual(remaining_senior.status.name, 'Active')
        self.assertEqual(junior_authorization.status.name, 'Inactive')

    def test_legacy_recovery_refuses_junior_marshal_when_senior_marshal_is_active(self):
        self.grant_authorization(
            self.owner_person,
            self.style_sm_armored,
            marshal=self.ao_person,
        )
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Junior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'already had a Senior Marshal authorization in Armored Combat on 2025-05-10',
        )
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_jm_armored).exists())

    def test_legacy_recovery_resolves_selected_marshal_by_id_before_names(self):
        self.client.force_login(self.ao_user)
        marshal_user, marshal_person = self.make_person(
            'legacy_marshal_id_lookup',
            'Connal MacLagmayn',
            user_id=16054,
        )
        self.grant_authorization(
            marshal_person,
            self.style_sm_armored,
            expiration=date(2029, 5, 10),
            marshal=marshal_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_id': [str(self.owner_person.user_id)],
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_id': [str(marshal_person.user_id)],
                'marshal_sca_name': ['Connal'],
                'marshal_first_name': ['Nathan'],
                'marshal_last_name': ['Brown'],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        self.assertEqual(authorization.marshal, marshal_person)
        self.assertFalse(
            any('Marshal was not found' in message for message in self.messages_for(response))
        )

    def test_legacy_recovery_senior_marshal_promotion_requires_concurring_officer(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'second_marshal_sca_name': [self.other_person.sca_name],
                'second_marshal_first_name': [self.other_user.first_name],
                'second_marshal_last_name': [self.other_user.last_name],
                'marshal_promotion': ['1'],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Concurring Officer is required for this marshal authorization.')
        self.assertFalse(Authorization.objects.filter(person=self.owner_person, style=self.style_sm_armored).exists())

    def test_legacy_recovery_senior_marshal_records_signoffs(self):
        self.client.force_login(self.ao_user)
        concurring_user, concurring_person = self.make_person('legacy_concurrer', 'Legacy Concurrer')
        self.grant_authorization(
            concurring_person,
            self.style_sm_armored,
            expiration=date(2029, 5, 10),
            marshal=concurring_person,
        )

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'weapon_style': ['Armored Combat - Senior Marshal'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'second_marshal_sca_name': [self.other_person.sca_name],
                'second_marshal_first_name': [self.other_user.first_name],
                'second_marshal_last_name': [self.other_user.last_name],
                'concurring_officer_sca_name': [concurring_person.sca_name],
                'concurring_officer_first_name': [concurring_user.first_name],
                'concurring_officer_last_name': [concurring_user.last_name],
                'marshal_promotion': ['1'],
                'auth_date': ['2025-05-10'],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Processed 1 paper authorization row(s).')
        authorization = Authorization.objects.get(person=self.owner_person, style=self.style_sm_armored)
        entry = LegacyAuthorizationRecoveryEntry.objects.get(authorization=authorization)
        self.assertTrue(entry.marshal_promotion)
        self.assertEqual(entry.second_marshal, self.other_person)
        self.assertEqual(entry.concurring_officer, concurring_person)
        self.assertEqual(authorization.concurring_fighter, self.ao_person)
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=authorization,
                note__contains=f'Second Marshal: {self.other_person.sca_name}.',
            ).exists()
        )
        self.assertTrue(
            AuthorizationNote.objects.filter(
                authorization=authorization,
                note__contains=f'Senior Marshal Concurrence: {concurring_person.sca_name}.',
            ).exists()
        )
        note_text = 'Authorization Added through Paper Authorization Entry Tool'
        self.assertTrue(
            UserNote.objects.filter(
                person=self.other_person,
                created_by=self.ao_user,
                note__contains=note_text,
            ).exists()
        )
        self.assertEqual(
            UserNote.objects.filter(
                person=self.ao_person,
                created_by=self.ao_user,
                note__contains=note_text,
            ).filter(note__contains='Armored Combat - Senior Marshal').count(),
            1,
        )
        self.assertTrue(
            UserNote.objects.filter(
                person=concurring_person,
                created_by=self.ao_user,
                note__contains=note_text,
            ).exists()
        )

    def test_ao_can_add_new_fighter_from_legacy_recovery_page(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'action': 'add_legacy_recovery_fighter',
                'sca_name': 'New Recovery Fighter',
                'email': 'new.recovery@example.com',
                'first_name': 'New',
                'last_name': 'Recovery',
                'membership': '',
                'membership_expiration': '',
                'address': '123 Recovery Way',
                'address2': '',
                'city': 'Portland',
                'state_province': 'Oregon',
                'postal_code': '97201',
                'country': 'United States',
                'phone_number': '5035550100',
                'birthday': '',
                'branch': str(self.branch_gd.id),
                'background_check_expiration': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Added fighter New Recovery Fighter.')
        person = Person.objects.get(sca_name='New Recovery Fighter')
        self.assertEqual(person.user.first_name, 'New')
        self.assertEqual(person.user.last_name, 'Recovery')
        self.assertEqual(person.user.username, 'new.recovery.fighter')
        self.assertEqual(person.user.email, 'new.recovery@example.com')
        self.assertEqual(person.user.address, '123 Recovery Way')
        self.assertEqual(person.user.city, 'Portland')
        self.assertEqual(person.user.state_province, 'Oregon')
        self.assertEqual(person.user.postal_code, '97201')
        self.assertEqual(person.user.country, 'United States')
        self.assertEqual(person.user.phone_number, '(503) 555-0100')
        self.assertEqual(person.branch, self.branch_gd)
        self.assertFalse(person.user.has_usable_password())
        self.assertTrue(UserNote.objects.filter(person=person, note__contains='Account created').exists())
        self.assertContains(response, 'New Recovery Fighter | New Recovery')

    def test_legacy_recovery_new_minor_fighter_requires_parent_names(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'action': 'add_legacy_recovery_fighter',
                'sca_name': 'Minor Recovery No Parent',
                'email': 'minor.recovery.no.parent@example.com',
                'first_name': 'Minor',
                'last_name': 'Recovery',
                'membership': '',
                'membership_expiration': '',
                'address': '123 Recovery Way',
                'address2': '',
                'city': 'Portland',
                'state_province': 'Oregon',
                'postal_code': '97201',
                'country': 'United States',
                'phone_number': '5035550102',
                'birthday': self.date_value(date.today() - relativedelta(years=12)),
                'branch': str(self.branch_gd.id),
                'is_minor': 'on',
                'parent_sca_name': '',
                'parent_first_name': '',
                'parent_last_name': '',
                'background_check_expiration': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A minor must have either a parent ID or parent first and last name.')
        self.assertFalse(Person.objects.filter(sca_name='Minor Recovery No Parent').exists())

    def test_legacy_recovery_new_minor_fighter_stores_parent_names(self):
        self.client.force_login(self.ao_user)

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'action': 'add_legacy_recovery_fighter',
                'sca_name': 'Minor Recovery Fighter',
                'email': 'minor.recovery@example.com',
                'first_name': 'Minor',
                'last_name': 'Recovery',
                'membership': '',
                'membership_expiration': '',
                'address': '123 Recovery Way',
                'address2': '',
                'city': 'Portland',
                'state_province': 'Oregon',
                'postal_code': '97201',
                'country': 'United States',
                'phone_number': '5035550103',
                'birthday': self.date_value(date.today() - relativedelta(years=12)),
                'branch': str(self.branch_gd.id),
                'is_minor': 'on',
                'parent_sca_name': 'Parent of Recovery',
                'parent_first_name': 'Pat',
                'parent_last_name': 'Recovery',
                'background_check_expiration': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = Person.objects.get(sca_name='Minor Recovery Fighter')
        self.assertEqual(person.parent_sca_name, 'Parent of Recovery')
        self.assertEqual(person.parent_first_name, 'Pat')
        self.assertEqual(person.parent_last_name, 'Recovery')

    def test_legacy_recovery_new_minor_fighter_parent_id_discards_parent_names(self):
        self.client.force_login(self.ao_user)
        parent_user, parent = self.make_person('legacy_recovery_parent', 'Legacy Recovery Parent')

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'action': 'add_legacy_recovery_fighter',
                'sca_name': 'Minor Recovery With Parent ID',
                'email': 'minor.recovery.parent.id@example.com',
                'first_name': 'Minor',
                'last_name': 'Parented',
                'membership': '',
                'membership_expiration': '',
                'address': '123 Recovery Way',
                'address2': '',
                'city': 'Portland',
                'state_province': 'Oregon',
                'postal_code': '97201',
                'country': 'United States',
                'phone_number': '5035550104',
                'birthday': self.date_value(date.today() - relativedelta(years=12)),
                'branch': str(self.branch_gd.id),
                'is_minor': 'on',
                'parent_id': str(parent.user_id),
                'parent_sca_name': 'Should Clear',
                'parent_first_name': 'Should',
                'parent_last_name': 'Clear',
                'background_check_expiration': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = Person.objects.get(sca_name='Minor Recovery With Parent ID')
        self.assertEqual(person.parent, parent)
        self.assertEqual(person.parent_sca_name, '')
        self.assertEqual(person.parent_first_name, '')
        self.assertEqual(person.parent_last_name, '')

    def test_legacy_recovery_updates_person_membership_from_batch_row(self):
        self.client.force_login(self.ao_user)
        self.owner_user.membership = '111111'
        self.owner_user.membership_expiration = date(2026, 1, 1)
        self.owner_user.save()

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'person_sca_name': [self.owner_person.sca_name],
                'person_first_name': [self.owner_user.first_name],
                'person_last_name': [self.owner_user.last_name],
                'person_membership': ['222222'],
                'person_membership_expiration': ['2030-06-15'],
                'weapon_style': ['Armored Combat - Weapon & Shield'],
                'marshal_sca_name': [self.ao_person.sca_name],
                'marshal_first_name': [self.ao_user.first_name],
                'marshal_last_name': [self.ao_user.last_name],
                'second_marshal_sca_name': [''],
                'second_marshal_first_name': [''],
                'second_marshal_last_name': [''],
                **self.paper_concurrer_fields(),
                'marshal_promotion': [''],
                'auth_date': ['2025-05-10'],
                'is_minor': [''],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.owner_user.refresh_from_db()
        self.assertEqual(self.owner_user.membership, '222222')
        self.assertEqual(self.owner_user.membership_expiration, date(2030, 6, 15))

    def test_legacy_recovery_new_fighter_duplicate_sca_name_gets_four_digit_username_suffix(self):
        self.client.force_login(self.ao_user)
        self.make_person('existing_same_sca', 'Shared Recovery Name')

        response = self.client.post(
            reverse('paper_authorization_entry'),
            {
                'action': 'add_legacy_recovery_fighter',
                'sca_name': 'Shared Recovery Name',
                'email': 'shared.recovery@example.com',
                'first_name': 'Shared',
                'last_name': 'Recovery',
                'membership': '',
                'membership_expiration': '',
                'address': '123 Recovery Way',
                'address2': '',
                'city': 'Portland',
                'state_province': 'Oregon',
                'postal_code': '97201',
                'country': 'United States',
                'phone_number': '5035550101',
                'birthday': '',
                'branch': str(self.branch_gd.id),
                'background_check_expiration': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        person = Person.objects.get(user__email='shared.recovery@example.com')
        self.assertRegex(person.user.username, r'^shared\.recovery\.name\.\d{4}$')

    def test_ao_can_download_legacy_recovery_audit_csv(self):
        self.client.force_login(self.ao_user)
        authorization = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_active,
            marshal=self.ao_person,
            expiration=date(2029, 5, 10),
        )
        LegacyAuthorizationRecoveryEntry.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            marshal=self.ao_person,
            second_marshal=self.other_person,
            concurring_officer=self.ao_person,
            auth_date=date(2025, 5, 10),
            minor_on_paperwork=True,
            marshal_promotion=True,
            expiration=date(2027, 5, 10),
            authorization=authorization,
            created_by=self.ao_user,
        )

        response = self.client.get(
            reverse('paper_authorization_entry'),
            {'download': 'audit_csv'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8-sig')
        self.assertIn('Processed At,Processed By,Person SCA Name', content)
        self.assertIn('Second Marshal SCA Name,Second Marshal First Name,Second Marshal Last Name', content)
        self.assertIn('Concurring Officer SCA Name,Concurring Officer First Name,Concurring Officer Last Name', content)
        self.assertIn('Marshal Promotion', content)
        self.assertIn('Owner of Account,Owner,User,Armored Combat - Weapon & Shield', content)
        self.assertIn(f'{self.other_person.sca_name},{self.other_user.first_name},{self.other_user.last_name}', content)
        self.assertIn('Yes,2025-05-10,2027-05-10,Yes', content)

    def test_legacy_recovery_promotion_checkbox_hidden_until_marshal_style_selected(self):
        self.client.force_login(self.ao_user)

        response = self.client.get(reverse('paper_authorization_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="marshal_promotion_field"')
        self.assertContains(response, 'd-none" id="marshal_promotion_field"')

    def test_paper_entry_signoff_lookups_show_member_numbers_and_use_person_search(self):
        self.client.force_login(self.ao_user)

        response = self.client.get(reverse('paper_authorization_entry'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="marshal_lookup" class="choices-dropdown form-control" data-editor-choice="1" data-person-search="1"')
        self.assertContains(response, 'id="second_marshal_lookup" class="choices-dropdown form-control" data-editor-choice="1" data-person-search="1"')
        self.assertContains(response, 'id="concurring_officer_lookup" class="choices-dropdown form-control" data-editor-choice="1" data-person-search="1"')
        self.assertContains(response, f'value="{self.ao_person.user_id}"')
        self.assertContains(response, 'data-sca-name="Authorization Officer"')
        self.assertContains(response, 'data-first-name="Auth"')
        self.assertContains(response, 'data-last-name="Officer"')
        self.assertContains(response, '8888888888 | Authorization Officer | Auth Officer')

    def test_legacy_recovery_url_redirects_to_paper_authorization_entry(self):
        self.client.force_login(self.ao_user)

        response = self.client.get(reverse('legacy_authorization_recovery'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('paper_authorization_entry'))

    @override_settings(AUTHZ_TEST_FEATURES=False)
    def test_self_set_regional_is_blocked_when_testing_disabled(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('Testing is not enabled; cannot set self as marshal officer.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_self_set_regional_requires_current_membership(self):
        self.owner_user.membership = None
        self.owner_user.membership_expiration = None
        self.owner_user.save()
        self.grant_authorization(self.owner_person, self.style_sm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('A current SCA membership (with valid expiration) is required.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_self_set_regional_local_branch_allows_junior_or_senior(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.branch_gd.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.branch_gd,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )

    @override_settings(AUTHZ_TEST_FEATURES=True)
    def test_self_set_regional_region_branch_requires_senior(self):
        self.grant_authorization(self.owner_person, self.style_jm_armored, status=self.status_active)

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'self_set_regional',
                'branch_id': str(self.region_summits.id),
                'discipline_id': str(self.discipline_armored.id),
            },
            follow=True,
        )

        messages = self.messages_for(response)
        self.assertIn('You must hold an active Senior Marshal in Armored Combat.', messages)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.owner_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )


class SupportingDocumentsViewTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.discipline_equestrian = Discipline.objects.create(name='Equestrian')
        cls.style_sm_equestrian = WeaponStyle.objects.create(
            name='Senior Marshal',
            discipline=cls.discipline_equestrian,
        )
        cls.style_general_riding = WeaponStyle.objects.create(
            name='General Riding',
            discipline=cls.discipline_equestrian,
        )

        cls.kao_user = User.objects.create_user(
            username='docs_kao',
            password='StrongPass!123',
            email='docs_kao@example.com',
            first_name='Docs',
            last_name='KAO',
            membership='9001000001',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kao_person = Person.objects.create(
            user=cls.kao_user,
            sca_name='Docs KAO',
            branch=cls.branch_gd,
        )

        cls.eq_officer_user = User.objects.create_user(
            username='docs_eq_officer',
            password='StrongPass!123',
            email='docs_eq_officer@example.com',
            first_name='Docs',
            last_name='EQ',
            membership='9001000002',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.eq_officer_person = Person.objects.create(
            user=cls.eq_officer_user,
            sca_name='Docs EQ Officer',
            branch=cls.branch_gd,
        )
        cls.earl_user = User.objects.create_user(
            username='docs_earl',
            password='StrongPass!123',
            email='docs_earl@example.com',
            first_name='Docs',
            last_name='Earl',
            membership='9001000005',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.earl_person = Person.objects.create(
            user=cls.earl_user,
            sca_name='Docs Earl Marshal',
            branch=cls.branch_gd,
        )
        cls.seneschal_user = User.objects.create_user(
            username='docs_seneschal',
            password='StrongPass!123',
            email='docs_seneschal@example.com',
            first_name='Docs',
            last_name='Seneschal',
            membership='9001000006',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.seneschal_person = Person.objects.create(
            user=cls.seneschal_user,
            sca_name='Docs Seneschal',
            branch=cls.branch_gd,
        )

        cls.viewer_user = User.objects.create_user(
            username='docs_viewer',
            password='StrongPass!123',
            email='docs_viewer@example.com',
            first_name='Docs',
            last_name='Viewer',
            membership='9001000003',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.viewer_person = Person.objects.create(
            user=cls.viewer_user,
            sca_name='Docs Viewer',
            branch=cls.branch_gd,
        )

        cls.fighter_user = User.objects.create_user(
            username='docs_fighter',
            password='StrongPass!123',
            email='docs_fighter@example.com',
            first_name='Docs',
            last_name='Fighter',
            membership='9001000004',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.fighter_person = Person.objects.create(
            user=cls.fighter_user,
            sca_name='Docs Fighter',
            branch=cls.branch_gd,
        )

        BranchMarshal.objects.create(
            person=cls.kao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        BranchMarshal.objects.create(
            person=cls.eq_officer_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_equestrian,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        BranchMarshal.objects.create(
            person=cls.earl_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        BranchMarshal.objects.create(
            person=cls.seneschal_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_seneschal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        Authorization.objects.create(
            person=cls.eq_officer_person,
            style=cls.style_sm_equestrian,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.kao_person,
        )
        Authorization.objects.create(
            person=cls.earl_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.kao_person,
        )

        cls.eq_pending_auth = Authorization.objects.create(
            person=cls.fighter_person,
            style=cls.style_general_riding,
            status=cls.status_pending,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.eq_officer_person,
        )

        cls.bg_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.BACKGROUND_CHECK,
            uploaded_by=cls.fighter_user,
        )
        cls.bg_document.file.save('bg-proof.pdf', ContentFile(b'bg-proof-content'), save=True)
        SupportingDocumentPerson.objects.create(document=cls.bg_document, person=cls.fighter_person)

        cls.eq_document = SupportingDocument.objects.create(
            document_type=SupportingDocument.DocumentType.EQUESTRIAN_WAIVER,
            jurisdiction='WA',
            uploaded_by=cls.eq_officer_user,
        )
        cls.eq_document.file.save('eq-waiver.pdf', ContentFile(b'eq-waiver-content'), save=True)
        SupportingDocumentPerson.objects.create(document=cls.eq_document, person=cls.fighter_person)
        SupportingDocumentAuthorization.objects.create(
            document=cls.eq_document,
            authorization=cls.eq_pending_auth,
        )

    def test_anonymous_user_is_redirected_to_authorizations_homepage(self):
        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('index'))

    def test_regular_user_sees_only_associated_documents(self):
        self.client.login(username=self.viewer_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No supporting documents matched your filters.')

    def test_fighter_sees_documents_attached_to_their_account(self):
        self.client.login(username=self.fighter_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
        )
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.eq_document.id}),
        )

    def test_kao_can_view_background_and_equestrian_documents(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Background Check')
        self.assertContains(response, 'Equestrian Event Waiver')
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
        )
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.eq_document.id}),
        )

    def test_kingdom_equestrian_officer_sees_only_associated_documents(self):
        self.client.login(username=self.eq_officer_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.eq_document.id}),
        )
        self.assertNotContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
        )

    def test_kingdom_earl_marshal_sees_all_documents(self):
        self.client.login(username=self.earl_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.eq_document.id}),
        )
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
        )

    def test_kingdom_seneschal_sees_all_documents(self):
        self.client.login(username=self.seneschal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.eq_document.id}),
        )
        self.assertContains(
            response,
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
        )
        self.assertContains(response, 'id="id_bg_person_id"')

    def test_kingdom_seneschal_can_upload_background_check_for_fighter(self):
        self.client.login(username=self.seneschal_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('supporting_documents'),
            {
                'action': 'upload_supporting_document',
                'document_type': SupportingDocument.DocumentType.BACKGROUND_CHECK,
                'bg_person_id': str(self.fighter_person.user_id),
                'document_file': SimpleUploadedFile(
                    'seneschal-bg.pdf',
                    b'seneschal-bg-content',
                    content_type='application/pdf',
                ),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                f'Background check proof uploaded for {self.fighter_person.sca_name}.' in message
                for message in self.messages_for(response)
            )
        )
        uploaded = SupportingDocument.objects.filter(uploaded_by=self.seneschal_user).latest('uploaded_at')
        self.assertEqual(uploaded.document_type, SupportingDocument.DocumentType.BACKGROUND_CHECK)
        self.assertTrue(
            SupportingDocumentPerson.objects.filter(
                document=uploaded,
                person=self.fighter_person,
            ).exists()
        )

    def test_kingdom_seneschal_can_read_but_not_write_fighter_notes(self):
        UserNote.objects.create(
            person=self.fighter_person,
            created_by=self.kao_user,
            note_type='officer_note',
            note='Officer-only fighter note.',
        )
        AuthorizationNote.objects.create(
            authorization=self.eq_pending_auth,
            created_by=self.kao_user,
            action='marshal_rejected',
            office='Kingdom Authorization Officer',
            note='Kingdom review action note.',
        )
        self.client.login(username=self.seneschal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.fighter_person.user_id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Officer-only fighter note.')
        self.assertContains(response, 'Kingdom review action note.')
        self.assertNotContains(response, 'Update Comments')

    def test_kao_can_open_supporting_document_file(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('Content-Type'), 'application/pdf')

    def test_viewer_cannot_open_unassociated_supporting_document_file(self):
        self.client.login(username=self.viewer_user.username, password='StrongPass!123')

        response = self.client.get(
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('index'))
        self.assertIn(
            'You do not have authority to view that document.',
            self.messages_for(response),
        )

    def test_supporting_documents_page_shows_upload_modal_for_logged_in_user(self):
        self.client.login(username=self.viewer_user.username, password='StrongPass!123')

        response = self.client.get(reverse('supporting_documents'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Document')
        self.assertContains(response, 'id="supportingDocumentModal"')

    def test_supporting_document_file_missing_redirects_to_authorizations_homepage(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        self.bg_document.file.delete(save=False)

        response = self.client.get(
            reverse('supporting_document_file', kwargs={'document_id': self.bg_document.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('index'))
        self.assertIn(
            'That supporting document file was not found.',
            self.messages_for(response),
        )


class RoadmapViewTests(ViewTestBase):
    def test_roadmap_includes_changelog_grouped_by_major_version(self):
        response = self.client.get(reverse('roadmap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>Current Roadmap:</h2>', html=True)
        self.assertContains(response, '<h2>Future Roadmap:</h2>', html=True)
        self.assertContains(response, '<h2>Changelog</h2>', html=True)
        self.assertContains(response, 'Version 1')
        self.assertContains(response, 'Version 0')

    @override_settings(RELEASE_ENV='')
    def test_roadmap_shows_unreleased_changelog_outside_production(self):
        response = self.client.get(reverse('roadmap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Version Unreleased')

    @override_settings(RELEASE_ENV='production')
    def test_roadmap_hides_unreleased_changelog_in_production(self):
        response = self.client.get(reverse('roadmap'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Version Unreleased')
        self.assertNotContains(response, 'Added an authorization deletion tool')

    def test_changelog_standalone_route_is_removed(self):
        response = self.client.get('/changelog/')

        self.assertRedirects(response, reverse('homepage'))


class NotFoundRedirectTests(ViewTestBase):
    def test_unknown_inner_route_redirects_to_authorizations_homepage(self):
        response = self.client.get('/authorizations/does-not-exist', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('index'))
        self.assertIn(
            'That page was not found. Redirected to the Authorizations Homepage.',
            self.messages_for(response),
        )

    def test_unknown_outer_route_redirects_to_homepage(self):
        response = self.client.get('/does-not-exist', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('homepage'))
        self.assertIn(
            'That page was not found. Redirected to Home.',
            self.messages_for(response),
        )


class MarshalOfficerAppointmentPermissionTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.style_sm_rapier = WeaponStyle.objects.create(name='Senior Marshal', discipline=cls.discipline_rapier)

        cls.kao_user = User.objects.create_user(
            username='office_kao',
            password='StrongPass!123',
            email='office_kao@example.com',
            first_name='Office',
            last_name='KAO',
            membership='5656565656',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kao_person = Person.objects.create(
            user=cls.kao_user,
            sca_name='Office KAO',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.kao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        cls.kem_user = User.objects.create_user(
            username='office_kem',
            password='StrongPass!123',
            email='office_kem@example.com',
            first_name='Office',
            last_name='KEM',
            membership='5757575757',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kem_person = Person.objects.create(
            user=cls.kem_user,
            sca_name='Office KEM',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.kem_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.kem_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.kem_person,
        )

        cls.krapier_user = User.objects.create_user(
            username='office_krapier',
            password='StrongPass!123',
            email='office_krapier@example.com',
            first_name='Office',
            last_name='KRapier',
            membership='5858585858',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.krapier_person = Person.objects.create(
            user=cls.krapier_user,
            sca_name='Office Kingdom Rapier',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.krapier_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.krapier_person,
            style=cls.style_sm_rapier,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_rapier_user = User.objects.create_user(
            username='office_candidate_rapier',
            password='StrongPass!123',
            email='office_candidate_rapier@example.com',
            first_name='Candidate',
            last_name='Rapier',
            membership='5959595959',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_rapier_person = Person.objects.create(
            user=cls.candidate_rapier_user,
            sca_name='Office Candidate Rapier',
            branch=cls.branch_gd,
        )
        Authorization.objects.create(
            person=cls.candidate_rapier_person,
            style=cls.style_sm_rapier,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_armored_user = User.objects.create_user(
            username='office_candidate_armored',
            password='StrongPass!123',
            email='office_candidate_armored@example.com',
            first_name='Candidate',
            last_name='Armored Combat',
            membership='6060606060',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_armored_person = Person.objects.create(
            user=cls.candidate_armored_user,
            sca_name='Office Candidate Armored',
            branch=cls.branch_gd,
        )
        Authorization.objects.create(
            person=cls.candidate_armored_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_earl_user = User.objects.create_user(
            username='office_candidate_earl',
            password='StrongPass!123',
            email='office_candidate_earl@example.com',
            first_name='Candidate',
            last_name='Earl',
            membership='6161616161',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_earl_person = Person.objects.create(
            user=cls.candidate_earl_user,
            sca_name='Office Candidate Earl',
            branch=cls.branch_gd,
        )
        Authorization.objects.create(
            person=cls.candidate_earl_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.krapier_person,
        )

        cls.candidate_ao_user = User.objects.create_user(
            username='office_candidate_ao',
            password='StrongPass!123',
            email='office_candidate_ao@example.com',
            first_name='Candidate',
            last_name='AO',
            membership='6262626262',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.candidate_ao_person = Person.objects.create(
            user=cls.candidate_ao_user,
            sca_name='Office Candidate AO',
            branch=cls.branch_gd,
        )

    def _appointment_payload(self, person, branch, discipline):
        return {
            'action': 'appoint_branch_marshal',
            'person': person.sca_name,
            'branch': branch.name,
            'discipline': discipline.name,
            'start_date': date.today().isoformat(),
        }

    def test_replacement_youth_authorization_inactivates_previous_age_category_same_style(self):
        lion_weapon = WeaponStyle.objects.create(name='Lion - Weapon & Shield', discipline=self.discipline_youth_armored)
        gryphon_weapon = WeaponStyle.objects.create(name='Gryphon - Weapon & Shield', discipline=self.discipline_youth_armored)
        dragon_weapon = WeaponStyle.objects.create(name='Dragon - Weapon & Shield', discipline=self.discipline_youth_armored)
        lion_two_handed = WeaponStyle.objects.create(name='Lion - Two-Handed', discipline=self.discipline_youth_armored)
        youth_marshal_user, youth_marshal = self.make_person(
            'replacement_youth_marshal',
            'Replacement Youth Marshal',
            background_check_expiration=date.today() + relativedelta(years=1),
        )
        self.grant_authorization(youth_marshal, self.style_sm_youth_armored)
        target_user, target = self.make_person(
            'replacement_youth_target',
            'Replacement Youth Target',
            birthday=date.today() - relativedelta(years=10),
            waiver_expiration=date.today() + relativedelta(years=1),
        )
        previous_same_style = self.grant_authorization(target, lion_weapon, marshal=youth_marshal)
        previous_other_style = self.grant_authorization(target, lion_two_handed, marshal=youth_marshal)
        previous_future_category = self.grant_authorization(target, dragon_weapon, marshal=youth_marshal)

        self.client.login(username=youth_marshal_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_youth_armored.id),
                'weapon_styles': [str(gryphon_weapon.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        previous_same_style.refresh_from_db()
        previous_other_style.refresh_from_db()
        previous_future_category.refresh_from_db()
        self.assertEqual(previous_same_style.status.name, 'Inactive')
        self.assertEqual(previous_other_style.status.name, 'Active')
        self.assertEqual(previous_future_category.status.name, 'Inactive')
        self.assertTrue(
            Authorization.objects.filter(
                person=target,
                style=gryphon_weapon,
                status=self.status_active,
            ).exists()
        )

    def _setup_equestrian_submission_context(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_junior_ground_crew, _ = WeaponStyle.objects.get_or_create(
            name='Ground Crew - Junior',
            discipline=discipline_equestrian,
        )
        style_senior_ground_crew, _ = WeaponStyle.objects.get_or_create(
            name='Ground Crew - Senior',
            discipline=discipline_equestrian,
        )
        proposer_user, proposer_person = self.make_person(
            'office_eq_proposer',
            'Office EQ Proposer',
        )
        self.grant_authorization(
            proposer_person,
            style_sm_equestrian,
            status=self.status_active,
            marshal=self.kao_person,
        )
        # Prevent initial EQ style submissions from requiring concurrence.
        self.grant_authorization(
            self.candidate_armored_person,
            style_general_riding,
            status=self.status_active,
            marshal=proposer_person,
        )
        # Ground Crew - Senior requires an active Ground Crew - Junior authorization.
        self.grant_authorization(
            self.candidate_armored_person,
            style_junior_ground_crew,
            status=self.status_active,
            marshal=proposer_person,
        )
        return proposer_user, discipline_equestrian, style_senior_ground_crew

    def test_fighter_page_hides_junior_ground_crew_when_senior_ground_crew_is_active(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_junior_ground_crew, _ = WeaponStyle.objects.get_or_create(
            name='Ground Crew - Junior',
            discipline=discipline_equestrian,
        )
        style_senior_ground_crew, _ = WeaponStyle.objects.get_or_create(
            name='Ground Crew - Senior',
            discipline=discipline_equestrian,
        )
        self.grant_authorization(
            self.candidate_armored_person,
            style_junior_ground_crew,
            status=self.status_active,
            marshal=self.kao_person,
        )
        self.grant_authorization(
            self.candidate_armored_person,
            style_senior_ground_crew,
            status=self.status_active,
            marshal=self.kao_person,
        )

        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        styles = response.context['authorization_list']['Equestrian']['styles']
        self.assertIn('Ground Crew - Senior', styles)
        self.assertNotIn('Ground Crew - Junior', styles)

    def test_fighter_owner_can_view_limited_actual_expiration(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_expiration = date.today() + timedelta(days=30)
        case_expiration = date.today() + relativedelta(years=1)
        formatted_case_expiration = f'{case_expiration.strftime("%B")} {case_expiration.day}, {case_expiration.year}'
        self.grant_authorization(
            self.candidate_ao_person,
            self.style_single_rapier,
            expiration=single_sword_expiration,
            marshal=self.krapier_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_case,
            expiration=case_expiration,
            marshal=self.krapier_person,
        )

        self.client.login(username=self.candidate_ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Case Renewal:')
        self.assertContains(response, formatted_case_expiration)

    def test_unrelated_viewer_cannot_view_limited_actual_expiration(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_expiration = date.today() + timedelta(days=30)
        case_expiration = date.today() + relativedelta(years=1)
        self.grant_authorization(
            self.candidate_ao_person,
            self.style_single_rapier,
            expiration=single_sword_expiration,
            marshal=self.krapier_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_case,
            expiration=case_expiration,
            marshal=self.krapier_person,
        )
        viewer_user, _ = self.make_person('actual_exp_unrelated', 'Actual Exp Unrelated')

        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Case Renewal:')

    def test_fighter_owner_can_view_dependency_limited_authorization_not_currently_valid(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_expiration = date.today() - timedelta(days=1)
        case_expiration = date.today() + relativedelta(years=1)
        formatted_case_expiration = f'{case_expiration.strftime("%B")} {case_expiration.day}, {case_expiration.year}'
        self.grant_authorization(
            self.candidate_ao_person,
            self.style_single_rapier,
            expiration=single_sword_expiration,
            marshal=self.krapier_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_case,
            expiration=case_expiration,
            marshal=self.krapier_person,
        )

        self.client.login(username=self.candidate_ao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Rapier Combat', response.context['authorization_list'])
        self.assertIn('Rapier Combat', response.context['limited_authorization_list'])
        self.assertContains(response, 'Authorizations Not Currently Valid')
        self.assertContains(response, 'Case Renewal:')
        self.assertContains(response, formatted_case_expiration)

    def test_auth_officer_can_view_dependency_limited_authorization_not_currently_valid(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_expiration = date.today() - timedelta(days=1)
        case_expiration = date.today() + relativedelta(years=1)
        formatted_case_expiration = f'{case_expiration.strftime("%B")} {case_expiration.day}, {case_expiration.year}'
        self.grant_authorization(
            self.candidate_ao_person,
            self.style_single_rapier,
            expiration=single_sword_expiration,
            marshal=self.krapier_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_case,
            expiration=case_expiration,
            marshal=self.krapier_person,
        )

        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertIn('Rapier Combat', response.context['limited_authorization_list'])
        self.assertContains(response, 'Authorizations Not Currently Valid')
        self.assertContains(response, 'Case Renewal:')
        self.assertContains(response, formatted_case_expiration)

    def test_unrelated_viewer_cannot_view_dependency_limited_authorization_not_currently_valid(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        single_sword_expiration = date.today() - timedelta(days=1)
        case_expiration = date.today() + relativedelta(years=1)
        self.grant_authorization(
            self.candidate_ao_person,
            self.style_single_rapier,
            expiration=single_sword_expiration,
            marshal=self.krapier_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_case,
            expiration=case_expiration,
            marshal=self.krapier_person,
        )
        viewer_user, _ = self.make_person('dependency_limited_unrelated', 'Dependency Limited Unrelated')

        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['limited_authorization_list'], {})
        self.assertNotContains(response, 'Authorizations Not Currently Valid')
        self.assertNotContains(response, 'Case Renewal:')

    def test_equestrian_actual_expiration_visibility_is_scoped_to_keao(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_mounted_gaming, _ = WeaponStyle.objects.get_or_create(
            name='Mounted Gaming',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person(
            'actual_exp_eq_officer',
            'Actual Exp EQ Officer',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        general_riding_expiration = date.today() + timedelta(days=30)
        mounted_gaming_expiration = date.today() + relativedelta(years=1)
        formatted_mounted_gaming_expiration = (
            f'{mounted_gaming_expiration.strftime("%B")} '
            f'{mounted_gaming_expiration.day}, {mounted_gaming_expiration.year}'
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_general_riding,
            expiration=general_riding_expiration,
            marshal=eq_officer_person,
        )
        self.grant_authorization(
            self.candidate_ao_person,
            style_mounted_gaming,
            expiration=mounted_gaming_expiration,
            marshal=eq_officer_person,
        )

        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        kao_response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')
        keao_response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))

        self.assertEqual(kao_response.status_code, 200)
        self.assertEqual(keao_response.status_code, 200)
        self.assertNotContains(kao_response, 'Mounted Gaming Renewal:')
        self.assertContains(keao_response, 'Mounted Gaming Renewal:')
        self.assertContains(keao_response, formatted_mounted_gaming_expiration)

    def test_kingdom_discipline_marshal_can_appoint_regional_same_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.region_summits, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.region_summits,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_discipline_marshal_can_appoint_branch_same_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_lg, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_lg,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_discipline_marshal_cannot_appoint_other_discipline(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            self._appointment_payload(self.candidate_armored_person, self.branch_lg, self.discipline_armored),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_armored_person,
                branch=self.branch_lg,
                discipline=self.discipline_armored,
                end_date__gte=date.today(),
            ).exists()
        )
        self.assertIn('You do not have authority to appoint this marshal office.', self.messages_for(response))

    def test_kingdom_discipline_marshal_cannot_appoint_kingdom_level_office(self):
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_an_tir, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_earl_marshal_can_appoint_kingdom_discipline_marshal(self):
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.branch_an_tir, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_rapier,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_earl_marshal_cannot_appoint_kingdom_earl_marshal(self):
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_an_tir, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_authorization_officer_can_appoint_kingdom_earl_marshal(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_an_tir, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_kingdom_authorization_officer_can_appoint_second_authorization_officer(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            self._appointment_payload(self.candidate_ao_person, self.branch_an_tir, self.discipline_auth_officer),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BranchMarshal.objects.filter(
                person=self.candidate_ao_person,
                branch=self.branch_an_tir,
                discipline=self.discipline_auth_officer,
                end_date__gte=date.today(),
            ).exists()
        )

    def test_appointment_form_hidden_when_target_has_active_marshal_office(self):
        self.appoint(self.candidate_rapier_person, self.branch_lg, self.discipline_rapier)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Appoint Marshal Officer')

    def test_cannot_appoint_when_target_has_active_marshal_office(self):
        self.appoint(self.candidate_rapier_person, self.branch_lg, self.discipline_rapier)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}),
            self._appointment_payload(self.candidate_rapier_person, self.region_summits, self.discipline_rapier),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            BranchMarshal.objects.filter(
                person=self.candidate_rapier_person,
                end_date__gte=date.today(),
            ).count(),
            1,
        )
        self.assertIn(
            'This fighter already has an active marshal officer appointment.',
            self.messages_for(response),
        )

    def test_fighter_hides_pending_buttons_for_wrong_discipline_kingdom_marshal(self):
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_jm_armored,
            status=self.status_pending,
            marshal=self.candidate_armored_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Armored Combat']
        self.assertFalse(pending['can_approve'])
        self.assertFalse(pending['can_reject'])

    def test_fighter_hides_pending_approve_for_user_who_created_it(self):
        Authorization.objects.create(
            person=self.candidate_rapier_person,
            style=self.style_jm_armored,
            status=self.status_pending,
            marshal=self.krapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Armored Combat']
        self.assertFalse(pending['can_approve'])

    def test_kingdom_earl_marshal_pending_buttons_require_discipline_credentials(self):
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_jm_armored,
            status=self.status_pending,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_single_rapier,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_sm_rapier,
            status=self.status_kingdom,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kem_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']
        self.assertTrue(pending['Armored Combat']['can_approve'])
        self.assertFalse(pending['Rapier Combat']['can_approve'])

    def test_kingdom_earl_marshal_does_not_see_approve_for_needs_kingdom(self):
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_single_rapier,
            status=self.status_kingdom,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kem_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Rapier Combat']
        self.assertFalse(pending['can_approve'])

    def test_kao_sees_pending_approve_but_needs_marshal_credentials_or_submit_as(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        Authorization.objects.create(
            person=self.candidate_ao_person,
            style=self.style_jm_armored,
            status=self.status_pending,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        get_response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}))
        self.assertEqual(get_response.status_code, 200)
        pending = get_response.context['pending_authorization_list']['Armored Combat']
        self.assertTrue(pending['can_approve'])

        pending_auth = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=self.style_jm_armored,
            status=self.status_pending,
        )
        blocked = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'action_note': 'KAO self approval attempt',
            },
            follow=True,
        )
        self.assertIn(
            'You must be a senior marshal in this discipline to approve this authorization.',
            self.messages_for(blocked),
        )
        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_pending)

        approved = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'submit_as_user_id': str(self.candidate_armored_user.id),
                'action_note': 'Approved as armored senior marshal',
            },
            follow=True,
        )
        pending_auth.refresh_from_db()
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertEqual(approved.status_code, 200)

    def test_kao_who_is_senior_marshal_can_approve_pending_as_self(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        Authorization.objects.create(
            person=self.kao_person,
            style=self.style_sm_armored,
            status=self.status_active,
            marshal=self.kem_person,
            expiration=date.today() + relativedelta(years=1),
        )
        pending_auth = Authorization.objects.create(
            person=self.candidate_ao_person,
            style=self.style_jm_armored,
            status=self.status_pending,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'action_note': 'KAO is also an armored senior marshal',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertNotIn(
            'Kingdom Authorization Officer must use "Approve As" to approve this authorization.',
            self.messages_for(response),
        )

    def test_kao_can_approve_needs_regional_using_submit_as(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        pending_auth = Authorization.objects.create(
            person=self.candidate_ao_person,
            style=self.style_sm_armored,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'submit_as_user_id': str(self.kem_user.id),
                'action_note': 'Approved by KAO as Earl Marshal',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)

    def test_officer_person_lookup_includes_member_number_and_mundane_name(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {
            'q': '6060606060',
            'purpose': 'active',
        })

        self.assertEqual(response.status_code, 200)
        labels = [row['label'] for row in response.json()['results']]
        self.assertTrue(any('Office Candidate Armored' in label for label in labels))
        self.assertTrue(any('Member #6060606060' in label for label in labels))
        self.assertTrue(any('Candidate Armored Combat' in label for label in labels))

    def test_authorizing_marshal_lookup_requires_active_senior_marshal_authorization(self):
        active_weapon_user, active_weapon_person = self.make_person(
            'lookup_active_weapon_only',
            'Lookup Active Weapon Only',
        )
        self.grant_authorization(active_weapon_person, self.style_weapon_armored)
        senior_user, senior_person = self.make_person(
            'lookup_active_senior',
            'Lookup Active Senior',
        )
        self.grant_authorization(senior_person, self.style_sm_armored)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {
            'q': 'Lookup Active',
            'purpose': 'senior_marshal',
        })

        self.assertEqual(response.status_code, 200)
        labels = [row['label'] for row in response.json()['results']]
        self.assertTrue(any('Lookup Active Senior' in label for label in labels))
        self.assertFalse(any('Lookup Active Weapon Only' in label for label in labels))

    def test_active_lookup_allows_any_active_authorized_person(self):
        active_weapon_user, active_weapon_person = self.make_person(
            'lookup_active_any_weapon',
            'Lookup Active Any Weapon',
        )
        self.grant_authorization(active_weapon_person, self.style_weapon_armored)
        no_auth_user, no_auth_person = self.make_person(
            'lookup_active_any_noauth',
            'Lookup Active Any Noauth',
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {
            'q': 'Lookup Active Any',
            'purpose': 'active',
        })

        self.assertEqual(response.status_code, 200)
        labels = [row['label'] for row in response.json()['results']]
        self.assertTrue(any('Lookup Active Any Weapon' in label for label in labels))
        self.assertFalse(any('Lookup Active Any Noauth' in label for label in labels))

    def test_officer_person_lookup_fuzzy_matches_name_fields(self):
        senior_user, senior_person = self.make_person(
            'lookup_fuzzy_robert',
            'Robert Fuzzy Marshal',
        )
        self.grant_authorization(senior_person, self.style_sm_armored)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {
            'q': 'Robret Fuzzy',
            'purpose': 'senior_marshal',
        })

        self.assertEqual(response.status_code, 200)
        labels = [row['label'] for row in response.json()['results']]
        self.assertTrue(any('Robert Fuzzy Marshal' in label for label in labels))

    def test_officer_person_lookup_does_not_fuzzy_match_membership_numbers(self):
        active_user, active_person = self.make_person(
            'lookup_no_fuzzy_member',
            'Lookup Numeric Exact',
            membership='9191919191',
        )
        self.grant_authorization(active_person, self.style_weapon_armored)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {
            'q': '9191919192',
            'purpose': 'active',
        })

        self.assertEqual(response.status_code, 200)
        labels = [row['label'] for row in response.json()['results']]
        self.assertFalse(any('Lookup Numeric Exact' in label for label in labels))

    def test_officer_person_lookup_rejects_non_officer(self):
        self.client.login(username=self.candidate_armored_user.username, password='StrongPass!123')

        response = self.client.get(reverse('officer_person_lookup'), {'q': 'Office'})

        self.assertEqual(response.status_code, 403)

    def test_kao_fighter_page_uses_person_lookup_instead_of_full_people_selects(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('all_people', response.context)
        self.assertContains(response, 'data-person-lookup="1"')
        self.assertContains(response, 'id="authorizing_marshal"')
        self.assertNotContains(response, 'data-user-id="')

    def test_kao_approval_preserves_existing_kingdom_review_expiration(self):
        proposed_expiration = date.today() + relativedelta(years=3)
        self.candidate_ao_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_ao_user.save()
        pending_auth = Authorization.objects.create(
            person=self.candidate_ao_person,
            style=self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=self.candidate_armored_person,
            expiration=proposed_expiration,
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'approval_date': (date.today() - timedelta(days=30)).isoformat(),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertEqual(pending_auth.expiration, proposed_expiration)

    def test_kingdom_equestrian_authorization_officer_approval_preserves_existing_expiration(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person(
            'office_eq_date_officer',
            'Office EQ Date Officer',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.candidate_ao_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_ao_user.save()
        proposed_expiration = date.today() + relativedelta(years=3)
        pending_auth = Authorization.objects.create(
            person=self.candidate_ao_person,
            style=style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=self.candidate_armored_person,
            expiration=proposed_expiration,
        )
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'approval_date': (date.today() - timedelta(days=45)).isoformat(),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertEqual(pending_auth.expiration, proposed_expiration)

    def test_concurrence_then_kingdom_approval_preserves_proposed_expiration(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        proposed_expiration = date.today() + relativedelta(years=3)
        self.candidate_ao_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_ao_user.save()
        pending_auth = Authorization.objects.create(
            person=self.candidate_ao_person,
            style=self.style_weapon_armored,
            status=self.status_needs_concurrence,
            marshal=self.candidate_rapier_person,
            expiration=proposed_expiration,
        )
        self.client.login(username=self.candidate_armored_user.username, password='StrongPass!123')

        concur_response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'concur_authorization',
                'authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(concur_response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_kingdom)
        self.assertEqual(pending_auth.concurring_fighter, self.candidate_armored_person)
        self.assertEqual(pending_auth.expiration, proposed_expiration)

        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        approve_response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertEqual(pending_auth.expiration, proposed_expiration)

    def test_kao_can_set_authorization_date_when_adding_authorization(self):
        authorization_date = date.today() - timedelta(days=20)
        self.candidate_armored_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_armored_user.save()
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'marshal_id': str(self.kem_user.id),
                'authorization_date': authorization_date.isoformat(),
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_armored_person,
            style=self.style_weapon_armored,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.expiration, authorization_date + relativedelta(years=4))
        self.assertEqual(created_auth.status, self.status_active)

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_rapier_single_sword_and_secondary_can_be_submitted_together_for_concurrence(self):
        style_case = WeaponStyle.objects.create(name='Case', discipline=self.discipline_rapier)
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_rapier.id),
                'weapon_styles': [str(self.style_single_rapier.id), str(style_case.id)],
            },
            follow=True,
        )

        single_sword = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=self.style_single_rapier,
        )
        case = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_case,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(single_sword.status.name, 'Awaiting Fighter Concurrence')
        self.assertEqual(case.status.name, 'Awaiting Fighter Concurrence')
        self.assertEqual(case.effective_expiration, date.today() - relativedelta(years=1))

    def test_keao_can_set_authorization_date_when_adding_equestrian_authorization(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person(
            'office_eq_add_date_officer',
            'Office EQ Add Date Officer',
        )
        eq_marshal_user, eq_marshal_person = self.make_person(
            'office_eq_add_date_marshal',
            'Office EQ Add Date Marshal',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.grant_authorization(eq_marshal_person, style_sm_equestrian)
        self.candidate_ao_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_ao_user.save()
        authorization_date = date.today() - timedelta(days=25)
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_general_riding.id)],
                'marshal_id': str(eq_marshal_user.id),
                'authorization_date': authorization_date.isoformat(),
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_general_riding,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.expiration, authorization_date + relativedelta(years=4))

    def test_kao_cannot_add_equestrian_authorization(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        eq_marshal_user, eq_marshal_person = self.make_person(
            'office_kao_scope_eq_marshal',
            'Office KAO Scope EQ Marshal',
        )
        self.grant_authorization(eq_marshal_person, style_sm_equestrian)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_general_riding.id)],
                'marshal_id': str(eq_marshal_user.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Authorization.objects.filter(
                person=self.candidate_ao_person,
                style=style_general_riding,
            ).exists()
        )
        self.assertIn(
            'Only the Kingdom Equestrian Authorization Officer can add equestrian authorizations.',
            self.messages_for(response),
        )

    def test_keao_cannot_add_non_equestrian_authorization(self):
        eq_officer_user, eq_officer_person = self.make_person(
            'office_keao_scope_officer',
            'Office KEAO Scope Officer',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'marshal_id': str(self.kem_user.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Authorization.objects.filter(
                person=self.candidate_ao_person,
                style=self.style_weapon_armored,
            ).exists()
        )
        self.assertIn(
            'Only the Kingdom Authorization Officer can add non-equestrian authorizations.',
            self.messages_for(response),
        )

    def test_kao_with_equestrian_senior_marshal_auth_can_add_equestrian_as_self(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        self.grant_authorization(self.kao_person, style_sm_equestrian)
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_general_riding.id)],
                'authorization_date': date.today().isoformat(),
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_general_riding,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.marshal, self.kao_person)
        self.assertEqual(created_auth.status, self.status_needs_kingdom_equestrian_waiver)

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_keao_with_non_equestrian_senior_marshal_auth_can_add_non_equestrian_as_self(self):
        eq_officer_user, eq_officer_person = self.make_person(
            'office_keao_armored_self_officer',
            'Office KEAO Armored Self Officer',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.grant_authorization(eq_officer_person, self.style_sm_armored)
        self.candidate_ao_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_ao_user.save()
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [str(self.style_weapon_armored.id)],
                'authorization_date': date.today().isoformat(),
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=self.style_weapon_armored,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.marshal, eq_officer_person)
        self.assertEqual(created_auth.status.name, 'Awaiting Fighter Concurrence')

    def test_kao_cannot_add_authorization_outside_their_marshal_discipline_as_self(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_longsword = WeaponStyle.objects.create(
            name='Longsword',
            discipline=discipline_cut_and_thrust,
        )
        Authorization.objects.create(
            person=self.kao_person,
            style=self.style_sm_rapier,
            status=self.status_active,
            marshal=self.krapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_cut_and_thrust.id),
                'weapon_styles': [str(style_longsword.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Authorization.objects.filter(
                person=self.candidate_armored_person,
                style=style_longsword,
            ).exists()
        )
        self.assertIn(
            'Error: Office KAO is not a senior marshal in Cut & Thrust and cannot authorize authorizations.',
            self.messages_for(response),
        )

    def test_kao_can_add_authorization_outside_their_marshal_discipline_using_eligible_marshal(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_longsword = WeaponStyle.objects.create(
            name='Longsword',
            discipline=discipline_cut_and_thrust,
        )
        style_sm_cut_and_thrust = WeaponStyle.objects.create(
            name='Senior Marshal',
            discipline=discipline_cut_and_thrust,
        )
        ct_marshal_user, ct_marshal_person = self.make_person(
            'office_ct_senior_marshal',
            'Office C&T Senior Marshal',
        )
        Authorization.objects.create(
            person=ct_marshal_person,
            style=style_sm_cut_and_thrust,
            status=self.status_active,
            marshal=self.kem_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_cut_and_thrust.id),
                'weapon_styles': [str(style_longsword.id)],
                'marshal_id': str(ct_marshal_user.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        created_auth = Authorization.objects.get(
            person=self.candidate_armored_person,
            style=style_longsword,
        )
        self.assertEqual(created_auth.marshal, ct_marshal_person)

    def test_equestrian_general_riding_and_mounted_gaming_can_be_submitted_together(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_mounted_gaming, _ = WeaponStyle.objects.get_or_create(
            name='Mounted Gaming',
            discipline=discipline_equestrian,
        )
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person(
            'office_eq_mg_officer',
            'Office EQ MG Officer',
        )
        eq_marshal_user, eq_marshal_person = self.make_person(
            'office_eq_mg_marshal',
            'Office EQ MG Marshal',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, self.discipline_equestrian_auth_officer)
        self.grant_authorization(eq_marshal_person, style_sm_equestrian)
        self.client.login(username=eq_officer_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_mounted_gaming.id), str(style_general_riding.id)],
                'marshal_id': str(eq_marshal_user.id),
            },
            follow=True,
        )

        general_riding = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_general_riding,
        )
        mounted_gaming = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_mounted_gaming,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(general_riding.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertEqual(mounted_gaming.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertEqual(mounted_gaming.effective_expiration, date.today() - relativedelta(years=1))

    @override_settings(AUTHZ_REQUIRE_FIGHTER_CONCURRENCE=True)
    def test_cut_and_thrust_foundation_and_spear_can_be_submitted_together_for_concurrence(self):
        discipline_cut_and_thrust = Discipline.objects.create(name='Cut & Thrust')
        style_spear = WeaponStyle.objects.create(
            name='Spear',
            discipline=discipline_cut_and_thrust,
        )
        style_longsword = WeaponStyle.objects.create(
            name='Longsword',
            discipline=discipline_cut_and_thrust,
        )
        style_sm_cut_and_thrust = WeaponStyle.objects.create(
            name='Senior Marshal',
            discipline=discipline_cut_and_thrust,
        )
        ct_marshal_user, ct_marshal_person = self.make_person(
            'office_ct_spear_senior_marshal',
            'Office C&T Spear Senior Marshal',
        )
        Authorization.objects.create(
            person=ct_marshal_person,
            style=style_sm_cut_and_thrust,
            status=self.status_active,
            marshal=self.kem_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_ao_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_cut_and_thrust.id),
                'weapon_styles': [str(style_spear.id), str(style_longsword.id)],
                'marshal_id': str(ct_marshal_user.id),
            },
            follow=True,
        )

        longsword = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_longsword,
        )
        spear = Authorization.objects.get(
            person=self.candidate_ao_person,
            style=style_spear,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(longsword.status.name, 'Awaiting Fighter Concurrence')
        self.assertEqual(spear.status.name, 'Awaiting Fighter Concurrence')
        self.assertEqual(spear.effective_expiration, date.today() - relativedelta(years=1))

    def test_basket_processes_new_style_after_existing_style_renewal(self):
        style_two_handed = WeaponStyle.objects.create(
            name='Two-Handed',
            discipline=self.discipline_armored,
        )
        self.grant_authorization(
            self.candidate_armored_person,
            self.style_weapon_armored,
            status=self.status_active,
            marshal=self.kem_person,
        )
        self.candidate_armored_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_armored_user.save()
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(self.discipline_armored.id),
                'weapon_styles': [
                    str(self.style_weapon_armored.id),
                    str(style_two_handed.id),
                ],
                'marshal_id': str(self.kem_user.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Authorization.objects.filter(
                person=self.candidate_armored_person,
                style=self.style_weapon_armored,
                status=self.status_active,
            ).exists()
        )
        self.assertTrue(
            Authorization.objects.filter(
                person=self.candidate_armored_person,
                style=style_two_handed,
                status=self.status_active,
            ).exists()
        )

    def test_kao_cannot_be_appointed_to_second_regional_office(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.kao_user.id}),
            self._appointment_payload(
                self.kao_person,
                self.region_summits,
                self.discipline_armored,
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'This fighter already has an active marshal officer appointment.',
            self.messages_for(response),
        )
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.kao_person,
                branch=self.region_summits,
                discipline=self.discipline_armored,
            ).exists()
        )

    def test_kao_can_approve_needs_kingdom_marshal_without_note(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        _, proposer_person = self.make_person('needs_kingdom_note_prop', 'Needs Kingdom Note Proposer')
        target_user, target_person = self.make_person('needs_kingdom_note_target', 'Needs Kingdom Note Target')
        pending_auth = Authorization.objects.create(
            person=target_person,
            style=self.style_sm_armored,
            status=self.status_kingdom,
            marshal=proposer_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': target_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)
        self.assertNotIn('pending_authorization_action', self.client.session)
        self.assertNotIn('A note is required for marshal promotion actions.', self.messages_for(response))

    def test_fighter_page_shows_approve_for_needs_regional_when_same_officer_proposed(self):
        pending = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_single_rapier,
            status=self.status_regional,
            marshal=self.krapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending_card = response.context['pending_authorization_list']['Rapier Combat']
        self.assertTrue(pending_card['can_approve'])
        self.assertContains(response, f'name="authorization_id" value="{pending.id}"')
        self.assertContains(response, 'name="action" value="approve_authorization"')

    def test_fighter_page_shows_approve_for_needs_kingdom_when_same_officer_proposed(self):
        pending = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=self.kao_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending_card = response.context['pending_authorization_list']['Armored Combat']
        self.assertTrue(pending_card['can_approve'])
        self.assertContains(response, f'name="authorization_id" value="{pending.id}"')
        self.assertContains(response, 'name="action" value="approve_authorization"')

    def test_fighter_page_hides_approve_for_needs_kingdom_equestrian_waiver_for_eq_officer(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_sm_equestrian, _ = WeaponStyle.objects.get_or_create(
            name='Senior Marshal',
            discipline=discipline_equestrian,
        )
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        eq_officer_user, eq_officer_person = self.make_person(
            'fighter_eq_approve_officer',
            'Fighter EQ Approve Officer',
        )
        self.appoint(eq_officer_person, self.branch_an_tir, discipline_equestrian)
        self.grant_authorization(
            eq_officer_person,
            style_sm_equestrian,
            status=self.status_active,
            marshal=self.kao_person,
        )
        pending = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=eq_officer_person,
            expiration=date.today() + relativedelta(years=1),
        )

        self.client.login(username=eq_officer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending_card = response.context['pending_authorization_list']['Equestrian']
        self.assertFalse(pending_card['can_approve'])

    def test_kingdom_earl_marshal_pending_requires_matching_senior_discipline(self):
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_sm_rapier,
            status=self.status_pending,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kem_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Rapier Combat']
        self.assertFalse(pending['can_approve'])

    def test_regional_earl_marshal_sees_needs_regional_buttons_any_discipline_in_region(self):
        regional_earl_user, regional_earl_person = self.make_person(
            'regional_earl_btn_ok',
            'Regional Earl Button Ok',
            branch=self.branch_gd,
        )
        self.grant_authorization(regional_earl_person, self.style_sm_armored)
        self.appoint(regional_earl_person, self.region_summits, self.discipline_earl_marshal)
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_single_rapier,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=regional_earl_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Rapier Combat']
        self.assertTrue(pending['can_approve'])
        self.assertTrue(pending['can_reject'])

    def test_regional_earl_marshal_sees_needs_regional_buttons_outside_region(self):
        regional_earl_user, regional_earl_person = self.make_person(
            'regional_earl_btn_out',
            'Regional Earl Button Outside',
            branch=self.branch_gd,
        )
        self.grant_authorization(regional_earl_person, self.style_sm_armored)
        self.appoint(regional_earl_person, self.region_summits, self.discipline_earl_marshal)
        outside_user, outside_person = self.make_person(
            'regional_earl_out_target',
            'Regional Earl Outside Target',
            branch=self.branch_lg,
        )
        Authorization.objects.create(
            person=outside_person,
            style=self.style_single_rapier,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=regional_earl_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': outside_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Rapier Combat']
        self.assertTrue(pending['can_approve'])
        self.assertFalse(pending['can_reject'])

    def test_regional_earl_marshal_can_approve_senior_marshal_they_proposed_outside_region(self):
        regional_earl_user, regional_earl_person = self.make_person(
            'regional_earl_approve_out',
            'Regional Earl Approve Outside',
            branch=self.branch_gd,
        )
        self.grant_authorization(regional_earl_person, self.style_sm_armored)
        self.appoint(regional_earl_person, self.region_summits, self.discipline_earl_marshal)
        outside_user, outside_person = self.make_person(
            'regional_earl_approve_target',
            'Regional Earl Approve Target',
            branch=self.branch_lg,
        )
        pending_auth = Authorization.objects.create(
            person=outside_person,
            style=self.style_sm_armored,
            status=self.status_regional,
            marshal=regional_earl_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=regional_earl_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': outside_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_auth.id),
                'action_note': 'Final concurrence as Earl Marshal at large',
            },
            follow=True,
        )

        pending_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_auth.status, self.status_active)

    def test_kao_can_reject_needs_regional_using_submit_as(self):
        needs_regional = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_sm_rapier,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        get_response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))
        self.assertEqual(get_response.status_code, 200)
        pending = get_response.context['pending_authorization_list']['Rapier Combat']
        self.assertTrue(pending['can_reject'])

        post_response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(needs_regional.id),
                'submit_as_user_id': str(self.kem_user.id),
                'action_note': 'Rejected by KAO submit-as',
            },
            follow=True,
        )

        needs_regional.refresh_from_db()
        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(needs_regional.status.name, 'Rejected')

    def test_kao_can_reject_needs_kingdom_from_fighter_with_required_note(self):
        needs_kingdom = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_weapon_armored,
            status=self.status_kingdom,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        get_response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))
        self.assertEqual(get_response.status_code, 200)
        pending = get_response.context['pending_authorization_list']['Armored Combat']
        self.assertTrue(pending['can_reject'])

        first = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(needs_kingdom.id),
            },
            follow=True,
        )

        needs_kingdom.refresh_from_db()
        self.assertEqual(needs_kingdom.status, self.status_kingdom)
        self.assertIn('pending_authorization_action', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize the rejection.',
            self.messages_for(first),
        )

        second = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'reject_authorization',
                'bad_authorization_id': str(needs_kingdom.id),
                'action_note': 'Rejected after kingdom review.',
            },
            follow=True,
        )

        needs_kingdom.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(needs_kingdom.status.name, 'Rejected')
        self.assertNotIn('pending_authorization_action', self.client.session)

    def test_pending_background_check_shows_in_pending_authorizations(self):
        pending_bg = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_sm_youth_armored,
            status=self.status_pending_background_check,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Youth Armored']
        self.assertEqual(pending['status'], 'Awaiting Background Check')
        self.assertFalse(pending['can_reject'])
        self.assertContains(response, 'Awaiting Background Check')
        self.assertContains(response, 'Youth Armored')

    def test_add_authorization_routes_equestrian_to_kingdom_waiver_when_sign_off_disabled(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': False})
        proposer_user, discipline_equestrian, style_senior_ground_crew = self._setup_equestrian_submission_context()

        self.client.login(username=proposer_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_senior_ground_crew.id)],
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_armored_person,
            style=style_senior_ground_crew,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertNotEqual(created_auth.status, self.status_kingdom)
        self.assertContains(response, 'Kingdom equestrian waiver review')

    def test_add_authorization_routes_equestrian_to_kingdom_waiver_when_sign_off_enabled(self):
        AuthorizationPortalSetting.objects.update_or_create(pk=1, defaults={'require_kao_verification': True})
        proposer_user, discipline_equestrian, style_senior_ground_crew = self._setup_equestrian_submission_context()

        self.client.login(username=proposer_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'add_authorization',
                'discipline': str(discipline_equestrian.id),
                'weapon_styles': [str(style_senior_ground_crew.id)],
            },
            follow=True,
        )

        created_auth = Authorization.objects.get(
            person=self.candidate_armored_person,
            style=style_senior_ground_crew,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(created_auth.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertNotEqual(created_auth.status, self.status_kingdom)
        self.assertContains(response, 'Kingdom equestrian waiver review')

    def test_kingdom_equestrian_authorization_officer_can_approve_needs_kingdom_equestrian_waiver(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )

        eq_officer_user, eq_officer_person = self.make_person(
            'office_eq_officer',
            'Office EQ Officer',
        )
        BranchMarshal.objects.create(
            person=eq_officer_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_equestrian_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        self.candidate_armored_user.waiver_expiration = date.today() + relativedelta(years=1)
        self.candidate_armored_user.save(update_fields=['waiver_expiration'])
        pending_eq = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )

        self.client.login(username=eq_officer_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_eq.id),
            },
            follow=True,
        )

        pending_eq.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_eq.status, self.status_active)
        self.assertContains(response, 'Equestrian General Riding authorization approved!')

    def test_kao_cannot_approve_needs_kingdom_equestrian_waiver(self):
        discipline_equestrian, _ = Discipline.objects.get_or_create(name='Equestrian')
        style_general_riding, _ = WeaponStyle.objects.get_or_create(
            name='General Riding',
            discipline=discipline_equestrian,
        )
        pending_eq = Authorization.objects.create(
            person=self.candidate_armored_person,
            style=style_general_riding,
            status=self.status_needs_kingdom_equestrian_waiver,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}),
            {
                'action': 'approve_authorization',
                'authorization_id': str(pending_eq.id),
            },
            follow=True,
        )

        pending_eq.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending_eq.status, self.status_needs_kingdom_equestrian_waiver)
        self.assertContains(response, 'Only the Kingdom Equestrian Authorization Officer can approve this authorization.')

    def test_non_marshal_does_not_get_reject_button_for_needs_regional(self):
        viewer_user, _ = self.make_person('office_pending_viewer', 'Office Awaiting Second Marshal Concurrence Viewer')
        Authorization.objects.create(
            person=self.candidate_armored_person,
            style=self.style_jm_armored,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=viewer_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_armored_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Armored Combat']
        self.assertFalse(pending['can_reject'])

    def test_out_of_region_regional_marshal_gets_approve_button_for_needs_regional_same_discipline(self):
        regional_user, regional_person = self.make_person(
            'office_out_region_armored',
            'Office Out Region Armored',
            branch=self.branch_lg,
        )
        BranchMarshal.objects.create(
            person=regional_person,
            branch=self.region_tir_righ,
            discipline=self.discipline_armored,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=regional_person,
            style=self.style_sm_armored,
            status=self.status_active,
            marshal=regional_person,
            expiration=date.today() + relativedelta(years=1),
        )
        target_user, target_person = self.make_person(
            'office_out_region_target',
            'Office Out Region Target',
            branch=self.branch_gd,
        )
        Authorization.objects.create(
            person=target_person,
            style=self.style_sm_armored,
            status=self.status_regional,
            marshal=self.candidate_rapier_person,
            expiration=date.today() + relativedelta(years=1),
        )
        self.client.login(username=regional_user.username, password='StrongPass!123')

        response = self.client.get(reverse('fighter', kwargs={'person_id': target_user.id}))

        self.assertEqual(response.status_code, 200)
        pending = response.context['pending_authorization_list']['Armored Combat']
        self.assertTrue(pending['can_approve'])

    def test_kingdom_discipline_marshal_can_end_lower_same_discipline_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.region_summits,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertLess(appointment.end_date, date.today())

    def test_kingdom_discipline_marshal_cannot_end_kingdom_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertGreaterEqual(appointment.end_date, date.today())

    def test_kingdom_earl_marshal_cannot_end_kingdom_earl_marshal_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_earl_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kem_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertGreaterEqual(appointment.end_date, date.today())

    def test_kingdom_authorization_officer_can_end_kingdom_earl_marshal_office(self):
        appointment = BranchMarshal.objects.create(
            person=self.candidate_earl_person,
            branch=self.branch_an_tir,
            discipline=self.discipline_earl_marshal,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('branch_marshals'),
            {'action': 'end_appointment', 'branch_officer_id': str(appointment.id)},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertLess(appointment.end_date, date.today())

    def test_cannot_appoint_branch_level_earl_marshal(self):
        self.client.login(username=self.kao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('fighter', kwargs={'person_id': self.candidate_earl_user.id}),
            self._appointment_payload(self.candidate_earl_person, self.branch_lg, self.discipline_earl_marshal),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BranchMarshal.objects.filter(
                person=self.candidate_earl_person,
                branch=self.branch_lg,
                discipline=self.discipline_earl_marshal,
                end_date__gte=date.today(),
            ).exists()
        )
        self.assertIn(
            'Earl Marshal offices may only be appointed at regional or kingdom level.',
            self.messages_for(response),
        )

    def test_fighter_shows_limited_office_expiration_for_officer_self(self):
        limited_date = date.today() + timedelta(days=20)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.candidate_rapier_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertContains(response, formatted_limited_date)

    def test_fighter_shows_limited_office_expiration_for_superior(self):
        limited_date = date.today() + timedelta(days=25)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        self.client.login(username=self.krapier_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertContains(response, formatted_limited_date)

    def test_fighter_hides_limited_office_expiration_for_non_superior(self):
        limited_date = date.today() + timedelta(days=30)
        self.candidate_rapier_user.membership_expiration = limited_date
        self.candidate_rapier_user.save(update_fields=['membership_expiration'])
        BranchMarshal.objects.create(
            person=self.candidate_rapier_person,
            branch=self.branch_lg,
            discipline=self.discipline_rapier,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        viewer_user, _ = self.make_person('office_unrelated_viewer', 'Office Unrelated Viewer')
        self.client.login(username=viewer_user.username, password='StrongPass!123')
        response = self.client.get(reverse('fighter', kwargs={'person_id': self.candidate_rapier_user.id}))

        self.assertEqual(response.status_code, 200)
        formatted_limited_date = f'({limited_date.strftime("%B")} {limited_date.day}, {limited_date.year})'
        self.assertNotContains(response, formatted_limited_date)


class WaiverWorkflowTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.owner_user = User.objects.create_user(
            username='waiver_owner',
            password='StrongPass!123',
            email='waiver_owner@example.com',
            first_name='Waiver',
            last_name='Owner',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        cls.owner_person = Person.objects.create(
            user=cls.owner_user,
            sca_name='Waiver Owner',
            branch=cls.branch_gd,
        )

        cls.other_user = User.objects.create_user(
            username='waiver_other',
            password='StrongPass!123',
            email='waiver_other@example.com',
            first_name='Waiver',
            last_name='Other',
            membership=None,
            membership_expiration=None,
            state_province='Oregon',
            country='United States',
        )
        cls.other_person = Person.objects.create(
            user=cls.other_user,
            sca_name='Waiver Other',
            branch=cls.branch_gd,
        )

        cls.ao_user = User.objects.create_user(
            username='waiver_ao',
            password='StrongPass!123',
            email='waiver_ao@example.com',
            first_name='Waiver',
            last_name='AO',
            membership='9090909090',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Waiver AO',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )

        cls.minor_user = User.objects.create_user(
            username='waiver_minor',
            password='StrongPass!123',
            email='waiver_minor@example.com',
            first_name='Waiver',
            last_name='Minor',
            membership=None,
            membership_expiration=None,
            birthday=date.today() - relativedelta(years=12),
            state_province='Oregon',
            country='United States',
        )
        cls.minor_person = Person.objects.create(
            user=cls.minor_user,
            sca_name='Waiver Minor',
            branch=cls.branch_gd,
            parent_first_name='Parent',
            parent_last_name='Guardian',
        )

    def test_owner_can_view_waiver_page(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.get(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authorizations/waiver.html')

    def test_minor_waiver_page_shows_minor_text_and_parent_name_fields(self):
        self.client.login(username=self.minor_user.username, password='StrongPass!123')

        response = self.client.get(reverse('sign_waiver', kwargs={'user_id': self.minor_user.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'I hereby state that Waiver Minor')
        self.assertContains(response, 'Waiver Minor')
        self.assertContains(response, 'name="parent_first_name"')
        self.assertContains(response, 'name="parent_last_name"')

    def test_minor_waiver_rejects_parent_name_mismatch(self):
        self.client.login(username=self.minor_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('sign_waiver', kwargs={'user_id': self.minor_user.id}),
            {
                'parent_first_name': 'Wrong',
                'parent_last_name': 'Guardian',
            },
            follow=True,
        )

        self.minor_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.minor_user.waiver_expiration)
        self.assertIn("The first and last name must match the parent's name.", self.messages_for(response))

    def test_minor_waiver_accepts_matching_parent_name(self):
        self.client.login(username=self.minor_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('sign_waiver', kwargs={'user_id': self.minor_user.id}),
            {
                'parent_first_name': 'Parent',
                'parent_last_name': 'Guardian',
            },
            follow=True,
        )

        self.minor_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.minor_user.waiver_expiration, date.today() + relativedelta(years=1))
        record = WaiverRecord.objects.get(covered_user=self.minor_user)
        self.assertEqual(record.source, WaiverRecord.Source.PORTAL_MINOR_SIGNATURE)
        self.assertEqual(record.signer_first_name, 'Parent')
        self.assertEqual(record.signer_last_name, 'Guardian')

    def test_authorization_officer_cannot_view_other_users_waiver_page(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.get(
            reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('You can only sign a waiver for your own account or linked child account.', messages)

    def test_non_owner_non_ao_cannot_sign_waiver_for_other_user(self):
        self.client.login(username=self.other_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self.messages_for(response)
        self.assertIn('You can only sign a waiver for your own account or linked child account.', messages)

    def test_ao_recording_paper_waiver_activates_authorizations_and_sets_latest_expiration(self):
        exp_one = date.today() + timedelta(days=30)
        exp_two = date.today() + timedelta(days=90)
        auth_one = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_weapon_armored,
            status=self.status_pending_waiver,
            expiration=exp_one,
            marshal=self.ao_person,
        )
        auth_two = Authorization.objects.create(
            person=self.owner_person,
            style=self.style_single_rapier,
            status=self.status_pending_waiver,
            expiration=exp_two,
            marshal=self.ao_person,
        )

        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'record_paper_waiver',
                'paper_signed_date': date.today().isoformat(),
                'paper_signer_first_name': 'Waiver',
                'paper_signer_last_name': 'Owner',
                'paper_signer_relationship': 'self',
                'paper_document_url': 'https://example.com/paper-waiver.pdf',
            },
            follow=True,
        )

        self.owner_user.refresh_from_db()
        auth_one.refresh_from_db()
        auth_two.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(auth_one.status, self.status_active)
        self.assertEqual(auth_two.status, self.status_active)
        self.assertEqual(self.owner_user.waiver_expiration, exp_two)
        self.assertTrue(WaiverRecord.objects.filter(covered_user=self.owner_user, source=WaiverRecord.Source.PAPER_WAIVER).exists())

    def test_signing_senior_ground_crew_pending_waiver_marks_junior_ground_crew_inactive(self):
        discipline_equestrian = Discipline.objects.create(name='Equestrian')
        junior_ground_crew = WeaponStyle.objects.create(
            name='Ground Crew - Junior',
            discipline=discipline_equestrian,
        )
        senior_ground_crew = WeaponStyle.objects.create(
            name='Ground Crew - Senior',
            discipline=discipline_equestrian,
        )
        junior_auth = Authorization.objects.create(
            person=self.owner_person,
            style=junior_ground_crew,
            status=self.status_active,
            expiration=date.today() + timedelta(days=30),
            marshal=self.ao_person,
        )
        senior_auth = Authorization.objects.create(
            person=self.owner_person,
            style=senior_ground_crew,
            status=self.status_pending_waiver,
            expiration=date.today() + timedelta(days=90),
            marshal=self.ao_person,
        )

        self.client.login(username=self.owner_user.username, password='StrongPass!123')
        response = self.client.post(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}), follow=True)

        junior_auth.refresh_from_db()
        senior_auth.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(senior_auth.status, self.status_active)
        self.assertEqual(junior_auth.status.name, 'Inactive')
        self.assertFalse(Authorization.objects.effectively_active().filter(id=junior_auth.id).exists())

    def test_owner_signing_without_pending_sets_one_year_waiver(self):
        self.client.login(username=self.owner_user.username, password='StrongPass!123')

        response = self.client.post(reverse('sign_waiver', kwargs={'user_id': self.owner_user.id}), follow=True)

        self.owner_user.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.owner_user.waiver_expiration, date.today() + relativedelta(years=1))
        record = WaiverRecord.objects.get(covered_user=self.owner_user, source=WaiverRecord.Source.PORTAL_ADULT_SIGNATURE)
        self.assertEqual(record.signer_first_name, self.owner_user.first_name)
        self.assertEqual(record.resulting_waiver_expiration, self.owner_user.waiver_expiration)

    def test_authorization_officer_can_record_paper_waiver_even_when_current(self):
        self.owner_user.waiver_expiration = date.today() + relativedelta(days=30)
        self.owner_user.save(update_fields=['waiver_expiration'])
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('user_account', kwargs={'user_id': self.owner_user.id}),
            {
                'action': 'record_paper_waiver',
                'paper_signed_date': date.today().isoformat(),
                'paper_signer_first_name': 'Waiver',
                'paper_signer_last_name': 'Owner',
                'paper_signer_relationship': 'self',
                'paper_document_url': 'https://example.com/paper-waiver.pdf',
                'paper_note': 'SharePoint record',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.owner_user.refresh_from_db()
        record = WaiverRecord.objects.get(covered_user=self.owner_user, source=WaiverRecord.Source.PAPER_WAIVER)
        self.assertEqual(record.recorded_by, self.ao_user)
        self.assertEqual(record.document_url, 'https://example.com/paper-waiver.pdf')
        self.assertEqual(record.resulting_waiver_expiration, self.owner_user.waiver_expiration)


class SanctionsWorkflowTests(ViewTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.target_user = User.objects.create_user(
            username='sanction_target',
            password='StrongPass!123',
            email='sanction_target@example.com',
            first_name='Sanction',
            last_name='Target',
            membership='5151515151',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.target_person = Person.objects.create(
            user=cls.target_user,
            sca_name='Sanction Target',
            branch=cls.branch_gd,
        )

        cls.normal_user = User.objects.create_user(
            username='sanction_normal',
            password='StrongPass!123',
            email='sanction_normal@example.com',
            first_name='Normal',
            last_name='User',
            membership='5252525252',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.normal_person = Person.objects.create(
            user=cls.normal_user,
            sca_name='Sanction Normal',
            branch=cls.branch_gd,
        )

        cls.ao_user = User.objects.create_user(
            username='sanction_ao',
            password='StrongPass!123',
            email='sanction_ao@example.com',
            first_name='AO',
            last_name='User',
            membership='5353535353',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.ao_person = Person.objects.create(
            user=cls.ao_user,
            sca_name='Sanction AO',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.ao_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_auth_officer,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        cls.kingdom_armored_user = User.objects.create_user(
            username='sanction_kingdom_armored',
            password='StrongPass!123',
            email='sanction_kingdom_armored@example.com',
            first_name='Kingdom',
            last_name='Armored Combat',
            membership='5454545454',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        cls.kingdom_armored_person = Person.objects.create(
            user=cls.kingdom_armored_user,
            sca_name='Sanction Kingdom Armored',
            branch=cls.branch_gd,
        )
        BranchMarshal.objects.create(
            person=cls.kingdom_armored_person,
            branch=cls.branch_an_tir,
            discipline=cls.discipline_armored,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + relativedelta(years=1),
        )
        Authorization.objects.create(
            person=cls.kingdom_armored_person,
            style=cls.style_sm_armored,
            status=cls.status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=cls.ao_person,
        )

    def test_issue_sanctions_requires_kingdom_sanctions_role(self):
        self.client.login(username=self.normal_user.username, password='StrongPass!123')

        response = self.client.get(reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}))

        self.assertEqual(response.status_code, 403)

    def test_kingdom_marshal_can_issue_sanction_in_own_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() + relativedelta(days=30)).isoformat(),
                'action_note': 'Issued by kingdom armored marshal',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, date.today() + relativedelta(days=30))
        self.assertIn('Sanction end date: ', sanction.issue_note)

    def test_kingdom_marshal_cannot_issue_sanction_outside_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_single_rapier.id),
                'sanction_end_date': (date.today() + relativedelta(days=30)).isoformat(),
                'action_note': 'Attempted cross-discipline sanction',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_single_rapier,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn(
            'You do not have permission to sanction this discipline.',
            self.messages_for(response),
        )

    def test_issue_style_sanction_creates_revoked_authorization_and_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() + relativedelta(days=60)).isoformat(),
                'action_note': 'Issued at kingdom event',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, date.today() + relativedelta(days=60))
        self.assertIn((date.today() + relativedelta(days=60)).isoformat(), sanction.issue_note)

    def test_issue_sanction_requires_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'action_note': 'Missing end date',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_weapon_armored,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn('Please select a sanction end date.', self.messages_for(response))

    def test_issue_sanction_rejects_past_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': (date.today() - relativedelta(days=1)).isoformat(),
                'action_note': 'Past end date',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Sanction.objects.filter(
                person=self.target_person,
                style=self.style_weapon_armored,
                lifted_at__isnull=True,
            ).exists()
        )
        self.assertIn('Sanction end date cannot be in the past.', self.messages_for(response))

    def test_issue_sanction_caps_end_date_at_office_term_and_warns(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        requested_end_date = date.today() + relativedelta(years=2)
        expected_end_date = date.today() + relativedelta(years=1)

        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': requested_end_date.isoformat(),
                'action_note': 'Too long',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        sanction = Sanction.objects.get(person=self.target_person, style=self.style_weapon_armored, lifted_at__isnull=True)
        self.assertEqual(sanction.end_date, expected_end_date)
        self.assertIn(
            f'Sanction end date cannot exceed the marshal office expiration date. It was set to {expected_end_date.isoformat()}.',
            self.messages_for(response),
        )

    def test_resanctioned_style_overwrites_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        existing = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today() - relativedelta(days=10),
            end_date=date.today() + relativedelta(days=10),
            issue_note='Original note',
            issued_by=self.ao_user,
        )

        new_end_date = date.today() + relativedelta(days=90)
        response = self.client.post(
            reverse('issue_sanctions', kwargs={'person_id': self.target_user.id}),
            {
                'sanction_type': 'style',
                'style_id': str(self.style_weapon_armored.id),
                'sanction_end_date': new_end_date.isoformat(),
                'action_note': 'Extended sanction',
            },
            follow=True,
        )

        existing.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(existing.end_date, new_end_date)
        self.assertEqual(existing.start_date, date.today() - relativedelta(days=10))

    def test_manage_sanctions_lift_two_step_flow_requires_note(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
            },
            follow=True,
        )

        self.assertIn('pending_sanction_lift', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note to finalize lifting the sanction.',
            self.messages_for(first_response),
        )

        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
                'action_note': 'Sanction lifted after review',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertIsNotNone(sanction.lifted_at)
        self.assertNotIn('pending_sanction_lift', self.client.session)
        self.assertEqual(sanction.lift_note, 'Sanction lifted after review')

    def test_manage_sanctions_extend_two_step_flow_requires_note_and_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
            },
            follow=True,
        )

        self.assertIn('pending_sanction_extend', self.client.session)
        self.assertIn(
            'Eligibility verified. Please add a note and end date to finalize extending the sanction.',
            self.messages_for(first_response),
        )

        new_end_date = date.today() + relativedelta(days=90)
        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': new_end_date.isoformat(),
                'action_note': 'Extension approved after follow-up review',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(sanction.end_date, new_end_date)
        self.assertNotIn('pending_sanction_extend', self.client.session)
        self.assertIn('Extension approved after follow-up review', sanction.issue_note)
        self.assertIn(f'Sanction end date: {new_end_date.isoformat()}', sanction.issue_note)

    def test_manage_sanctions_extend_rejects_past_end_date(self):
        self.client.login(username=self.ao_user.username, password='StrongPass!123')
        original_end_date = date.today() + relativedelta(days=30)
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=original_end_date,
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': (date.today() - relativedelta(days=1)).isoformat(),
                'action_note': 'Invalid extension attempt',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sanction.end_date, original_end_date)
        self.assertIn('Sanction end date cannot be in the past.', self.messages_for(response))

    def test_manage_sanctions_extend_caps_end_date_at_office_term_and_warns(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        original_end_date = date.today() + relativedelta(days=30)
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=original_end_date,
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )
        requested_end_date = date.today() + relativedelta(years=2)
        expected_end_date = date.today() + relativedelta(years=1)

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'extend_sanction',
                'sanction_id': str(sanction.id),
                'sanction_end_date': requested_end_date.isoformat(),
                'action_note': 'Requested long extension',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sanction.end_date, expected_end_date)
        self.assertIn(
            f'Sanction end date cannot exceed the marshal office expiration date. It was set to {expected_end_date.isoformat()}.',
            self.messages_for(response),
        )

    def test_kingdom_marshal_can_lift_sanction_in_own_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_armored,
            style=self.style_weapon_armored,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing sanction',
            issued_by=self.ao_user,
        )

        first_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
            },
            follow=True,
        )
        self.assertIn(
            'Eligibility verified. Please add a note to finalize lifting the sanction.',
            self.messages_for(first_response),
        )

        second_response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
                'action_note': 'Lifted by kingdom armored marshal',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(second_response.status_code, 200)
        self.assertIsNotNone(sanction.lifted_at)
        self.assertEqual(sanction.lift_note, 'Lifted by kingdom armored marshal')

    def test_kingdom_marshal_cannot_lift_sanction_outside_discipline(self):
        self.client.login(username=self.kingdom_armored_user.username, password='StrongPass!123')
        sanction = Sanction.objects.create(
            person=self.target_person,
            discipline=self.discipline_rapier,
            style=self.style_single_rapier,
            start_date=date.today(),
            end_date=date.today() + relativedelta(days=30),
            issue_note='Existing rapier sanction',
            issued_by=self.ao_user,
        )

        response = self.client.post(
            reverse('manage_sanctions'),
            {
                'action': 'lift_sanction',
                'sanction_id': str(sanction.id),
                'action_note': 'Attempted out-of-discipline lift',
            },
            follow=True,
        )

        sanction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(sanction.lifted_at)
        self.assertIn(
            'You do not have permission to manage this sanction.',
            self.messages_for(response),
        )


class ApiStylesViewTests(ViewTestBase):
    def test_get_weapon_styles_returns_only_matching_discipline(self):
        extra_discipline = Discipline.objects.create(name='Extra Discipline')
        extra_style = WeaponStyle.objects.create(name='Extra Style', discipline=extra_discipline)

        response = self.client.get(reverse('get_weapon_styles', kwargs={'discipline_id': self.discipline_armored.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        style_names = {item['name'] for item in payload['styles']}
        self.assertIn(self.style_weapon_armored.name, style_names)
        self.assertNotIn(extra_style.name, style_names)

    def test_get_weapon_styles_with_invalid_discipline_returns_empty_list(self):
        response = self.client.get(reverse('get_weapon_styles', kwargs={'discipline_id': 999999}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'styles': []})


class ReportsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.q2_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=2,
            authorization_officer_name='Officer Q2',
        )
        cls.q3_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=3,
            authorization_officer_name='Officer Q3',
        )
        cls.q4_2025 = ReportingPeriod.objects.create(
            year=2025,
            quarter=4,
            authorization_officer_name='Officer Q4',
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=595,
            display_order=1,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=608,
            display_order=1,
        )
        ReportValue.objects.create(
            reporting_period=cls.q2_2025,
            report_family=ReportValue.ReportFamily.QUARTERLY_MARSHAL,
            region_name='',
            subject_name='Armored Combat',
            metric_name='Total Participants',
            value=643,
            display_order=1,
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
            region_name='Central',
            subject_name='Armored Combat',
            metric_name='Combatants',
            value=220,
            display_order=10,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.REGIONAL_BREAKDOWN,
            region_name='Central',
            subject_name='Armored Combat',
            metric_name='Combatants',
            value=230,
            display_order=10,
        )

        ReportValue.objects.create(
            reporting_period=cls.q4_2025,
            report_family=ReportValue.ReportFamily.EQUESTRIAN,
            region_name='An Tir',
            subject_name='General Riding',
            metric_name='Reporting Quarter',
            value=45,
            display_order=10,
        )
        ReportValue.objects.create(
            reporting_period=cls.q3_2025,
            report_family=ReportValue.ReportFamily.EQUESTRIAN,
            region_name='An Tir',
            subject_name='General Riding',
            metric_name='Reporting Quarter',
            value=49,
            display_order=10,
        )

    def test_reports_defaults_to_latest_and_previous_quarters(self):
        response = self.client.get(reverse('reports'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q4_2025)
        self.assertEqual(response.context['selected_compare_period'], self.q3_2025)
        self.assertContains(response, 'Q4 2025')
        self.assertContains(response, 'Q3 2025')
        self.assertContains(response, 'Armored Combat')
        self.assertContains(response, '-13')

    def test_reports_allows_explicit_quarter_selection(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': str(self.q3_2025.id), 'compare_period': str(self.q2_2025.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q3_2025)
        self.assertEqual(response.context['selected_compare_period'], self.q2_2025)
        self.assertContains(response, 'Q3 2025')
        self.assertContains(response, 'Q2 2025')
        self.assertContains(response, '-35')

    def test_reports_compare_none_disables_compare_columns(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': str(self.q4_2025.id), 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_current_period'], self.q4_2025)
        self.assertIsNone(response.context['selected_compare_period'])
        self.assertFalse(response.context['show_compare_columns'])
        self.assertNotContains(response, '<th>Change</th>', html=True)

    def test_reports_supports_current_dynamic_period_without_persisting_rows(self):
        status_active, _ = AuthorizationStatus.objects.get_or_create(name='Active')
        branch_an_tir = Branch.objects.create(name='An Tir', type='Kingdom')
        for region_name in REGION_ORDER:
            Branch.objects.create(name=region_name, type='Region', region=branch_an_tir)
        region_central = Branch.objects.get(name='Central')
        local_branch = Branch.objects.create(name='Central Local', type='Barony', region=region_central)

        discipline_map = {}
        for discipline_name, _ in QUARTERLY_DISCIPLINE_MAP:
            discipline_map[discipline_name] = Discipline.objects.create(name=discipline_name)
        equestrian = discipline_map['Equestrian']
        for style_name in EQUESTRIAN_TYPE_ORDER:
            WeaponStyle.objects.create(name=style_name, discipline=equestrian)

        discipline = discipline_map['Armored Combat']
        style = WeaponStyle.objects.create(name='Weapon & Shield', discipline=discipline)
        marshal_style = WeaponStyle.objects.create(name='Junior Marshal', discipline=discipline)
        user = User.objects.create_user(
            username='current_report_user',
            password='StrongPass!123',
            email='current_report_user@example.com',
            first_name='Current',
            last_name='Report',
            membership='999000111',
            membership_expiration=date.today() + relativedelta(years=1),
            state_province='Oregon',
            country='United States',
        )
        person = Person.objects.create(user=user, sca_name='Current Report User', branch=local_branch)
        Authorization.objects.create(
            person=person,
            style=style,
            status=status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=person,
        )
        Authorization.objects.create(
            person=person,
            style=marshal_style,
            status=status_active,
            expiration=date.today() + relativedelta(years=1),
            marshal=person,
        )

        response = self.client.get(
            reverse('reports'),
            {'current_period': 'current', 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['current_is_dynamic'])
        self.assertIsNone(response.context['selected_compare_period'])
        self.assertFalse(response.context['show_compare_columns'])
        self.assertContains(response, 'Current')
        participants_row = next(
            row for row in response.context['marshal_rows']
            if row['subject_name'] == 'Armored Combat' and row['metric_name'] == 'Total Participants'
        )
        self.assertEqual(participants_row['current_value'], 1)

    def test_reports_current_mode_handles_configuration_mismatch_without_crash(self):
        response = self.client.get(
            reverse('reports'),
            {'current_period': 'current', 'compare_period': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['current_is_dynamic'])
        self.assertContains(response, 'Current report could not be generated safely')

    def test_reports_download_quarterly_csv_includes_compare_columns(self):
        response = self.client.get(
            reverse('reports'),
            {'download': 'quarterly_marshal'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename="quarterly_marshal_report.csv"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig')
        self.assertIn('Discipline,Authorization Detail,Q4 2025,Q3 2025,Change', content)
        self.assertIn('Armored Combat,Total Participants,595,608,-13', content)

    def test_reports_download_quarterly_csv_omits_compare_columns_when_none_selected(self):
        response = self.client.get(
            reverse('reports'),
            {
                'current_period': str(self.q4_2025.id),
                'compare_period': '',
                'download': 'quarterly_marshal',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        content = response.content.decode('utf-8-sig').splitlines()
        self.assertEqual(content[0], 'Discipline,Authorization Detail,Q4 2025')
        self.assertIn('Armored Combat,Total Participants,595', content)

